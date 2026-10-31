"""Reusable articulation templates for quadrupeds.

A template is the articulation system of a toy, stored relative to the
figure's landmarks (spec: docs/projeto-executivo-articulacao-quadrupedes.md,
sections 3, 5, 6 and 7), so it can be applied to any new one-piece figure:

- every size is given at the template's reference height (35 mm) with
  min/max bounds: size = clamp(ref x H / reference_height, min, max);
- clearances, walls and pin embedding are absolute and never scale;
- positions are relative to landmarks: leg pivots at pivot_height_rel x H
  above the floor, over each foot; the waist at position_from_rear_rel of the
  torso length, measured from the rear.

Tools: extract_articulation_template (from a finished model split into parts
named by the SolidCraft convention), apply_articulation_template (maps a
template onto a figure and returns cut/joint steps for articulate_figure,
shown as helper objects), list_articulation_templates, clear_plan_helpers.
"""

import copy
import json
import os
import re
import time
from pathlib import Path

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from . import articulate as art
from .base import Tool, register_tool

FORMAT = "blendercraft-articulation-template/1"
DEFAULT_TEMPLATE = "cachorrinho-2.0"
BUILTIN_DIR = Path(__file__).resolve().parent.parent / "templates"

# The executive spec (v1.0), sections 5-7, at H = 35 mm.
SPEC_DEFAULTS = {
    "reference_height_mm": 35.0,
    "rules": {
        "clearance_mm": 0.30,
        "assembled_clearance_mm": 0.20,
        "min_wall_mm": 1.2,
        "pin_embed_mm": 1.5,
        "min_detail_mm": 0.4,
    },
    "legs": {
        "enabled": True,
        "joint": "pin",
        "axis": "length",
        "pivot_height_rel": 0.38,
        "pin_mm": {"ref": 2.5, "min": 2.0, "max": 4.0},
        "hole": "pin + 2 x clearance",
        "knuckle_radius": "half the leg width",
        "min_leg_width_mm": 5.5,
        "range_deg": {"outward": 90.0, "inward": 0.0},
        "pocket": "leg swept 0-90 deg outward + clearance, subtracted from the body",
    },
    "waist": {
        "enabled": True,
        "position_from_rear_rel": 0.52,
        "gap_mm": {"ref": 1.3, "min": 0.8, "max": 2.0},
        "joint": "ball",
        "ball_on": "front",
        "ball_mm": {"ref": 7.0, "min": 4.5, "max": 12.0},
        "neck_mm": {"ref": 4.0, "min": 3.0, "max": 8.0},
        "mouth_mm": {"ref": 5.2, "min": 3.6, "max": 10.0},
        "lateral_deg": 10.0,
        "twist_deg": 30.0,
    },
    "neck": {"enabled": False, "ball_mm": {"ref": 6.0, "min": 4.0, "max": 10.0}, "min_neck_mm": 9.0},
    "tail": {"enabled": False, "ball_mm": {"ref": 3.5, "min": 3.0, "max": 6.0}, "min_root_mm": 5.0},
}

# Part roles by name (SolidCraft convention of the user's projects).
_ROLES = [
    ("front", re.compile(r"^01-")),
    ("rear", re.compile(r"^02-")),
    ("neck", re.compile(r"^03-")),
    ("leg", re.compile(r"^04-perna-(frente|tras)-x([+-])")),
    ("tail", re.compile(r"^05-")),
    ("knuckle", re.compile(r"^06-bola-(frente|tras)-x([+-])")),
    ("hole", re.compile(r"^07-furo-(frente|tras)-x([+-])")),
    ("leg_pin", re.compile(r"^08-pino-perna-(frente|tras)-x([+-])")),
    ("mid_pin", re.compile(r"^09-")),
    ("cap", re.compile(r"^10-")),
    ("pad", re.compile(r"^11-")),
]
_END = {"frente": "front", "tras": "rear"}


def templates_dir() -> Path:
    """User templates: ~/Documents/BlenderCraft/templates (or the folder in
    BLENDERCRAFT_TEMPLATES_DIR, used by the tests)."""
    override = os.environ.get("BLENDERCRAFT_TEMPLATES_DIR")
    if override:
        return Path(override)
    return Path.home() / "Documents" / "BlenderCraft" / "templates"


def _safe_name(name) -> str:
    name = str(name).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name) or ".." in name:
        raise ValueError("template_name: letters, digits, '.', '_' or '-' (max 64), e.g. cachorrinho-2.0")
    return name


def list_templates() -> list[dict]:
    out, seen = [], set()
    for origin, root in (("user", templates_dir()), ("built-in", BUILTIN_DIR)):
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*/template.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            name = path.parent.name
            if name in seen or data.get("format") != FORMAT:
                continue
            seen.add(name)
            out.append(
                {
                    "name": name,
                    "origin": origin,
                    "default": name == DEFAULT_TEMPLATE,
                    "description": data.get("description", ""),
                    "reference_height_mm": data.get("reference_height_mm"),
                    "joints": [k for k in ("legs", "waist", "neck", "tail") if data.get(k, {}).get("enabled")],
                    "path": str(path.parent),
                }
            )
    return out


def load_template(name: str | None) -> dict:
    name = _safe_name(name or DEFAULT_TEMPLATE)
    for root in (templates_dir(), BUILTIN_DIR):
        path = root / name / "template.json"
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("format") != FORMAT:
                raise ValueError(f"{name}: not a {FORMAT} file")
            return data
    available = ", ".join(t["name"] for t in list_templates()) or "none"
    raise ValueError(f"Template not found: {name}. Available: {available}")


def _clamp(size: dict, k: float) -> float:
    """Size in mm at scale k, within its bounds."""
    value = size["ref"] * k
    if "min" in size:
        value = max(size["min"], value)
    if "max" in size:
        value = min(size["max"], value)
    return round(value, 3)


# ---------------------------------------------------------------- extract


def _bbox(objs) -> tuple[Vector, Vector]:
    pts = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    lo = Vector([min(p[i] for p in pts) for i in range(3)])
    hi = Vector([max(p[i] for p in pts) for i in range(3)])
    return lo, hi


def _pin_fit(obj) -> tuple[Vector, int, float]:
    """Centre, axis index (longest bbox side) and diameter (smallest side)."""
    lo, hi = _bbox([obj])
    size = hi - lo
    i = max(range(3), key=lambda k: size[k])
    return (lo + hi) / 2, i, min(size[k] for k in range(3) if k != i)


def extract_template(template_name: str, source_collection: str | None = None, description: str = "",
                     standing_height_mm: float | None = None, save_meshes: bool = True) -> dict:
    """Template from a model split into named parts; spec defaults fill what
    the model cannot tell, and the dossier corrections always apply."""
    name = _safe_name(template_name)
    t0 = time.perf_counter()
    if source_collection:
        coll = bpy.data.collections.get(source_collection)
        if coll is None:
            raise ValueError(f"Collection not found: {source_collection}")
        objs = [o for o in coll.all_objects if o.type == "MESH"]
    else:
        objs = [o for o in bpy.context.scene.objects if o.type == "MESH" and o.visible_get()]
    roles: dict[str, list] = {}
    keyed: dict[tuple, object] = {}
    unknown = []
    for obj in objs:
        for role, pattern in _ROLES:
            m = pattern.match(obj.name)
            if m:
                roles.setdefault(role, []).append(obj)
                if m.groups():
                    keyed[(role, _END[m.group(1)], m.group(2))] = obj
                break
        else:
            unknown.append(obj.name)
    if "front" not in roles or "leg" not in roles:
        raise ValueError(
            "Parts not found: name them by the SolidCraft convention (01- front body "
            "with head, 02- rear body, 04-perna-frente|tras-x+|-, 05- tail, 06-bola- "
            "knuckles, 07-furo- holes, 08-pino-perna- leg pins, 09-pino-meio, "
            "10-calota-meio, 11- pads)"
        )
    mm = art._mm()
    template = copy.deepcopy(SPEC_DEFAULTS)
    measured, warnings = {}, []
    all_lo, all_hi = _bbox(objs)
    legs = roles["leg"]
    leg_lo, leg_hi = _bbox(legs)
    leg_size = leg_hi - leg_lo
    # A model in print pose has the legs lying flat (wider than tall).
    standing = all(
        (_bbox([o])[1] - _bbox([o])[0]).z >= max((_bbox([o])[1] - _bbox([o])[0])[:2]) for o in legs
    )
    model_height = (all_hi.z - all_lo.z) * mm
    if standing:
        height = model_height
    else:
        height = float(standing_height_mm) if standing_height_mm else template["reference_height_mm"]
        warnings.append(
            f"model is in print pose (legs flat): standing height taken as {height} mm"
            + ("" if standing_height_mm else " (spec reference; pass standing_height_mm)")
            + "; leg pivot height keeps the spec value"
        )
    template["reference_height_mm"] = round(height, 3)
    measured["model_height_mm"] = round(model_height, 3)
    measured["standing"] = standing

    torso = roles["front"] + roles.get("rear", [])
    t_lo, t_hi = _bbox(torso)
    li = 0 if (t_hi - t_lo).x >= (t_hi - t_lo).y else 1
    front_c = (_bbox(roles["front"])[0] + _bbox(roles["front"])[1]) / 2
    rear_objs = roles.get("rear", [])
    rear_c = (_bbox(rear_objs)[0] + _bbox(rear_objs)[1]) / 2 if rear_objs else (t_lo + t_hi) / 2
    head_sign = 1 if front_c[li] > rear_c[li] else -1
    rear_end = t_lo[li] if head_sign > 0 else t_hi[li]
    front_end = t_hi[li] if head_sign > 0 else t_lo[li]

    # Leg pins and holes.
    pins, sources = [], []
    for (role, end, sign), obj in keyed.items():
        if role != "leg_pin":
            continue
        centre, axis_i, diameter = _pin_fit(obj)
        pins.append(diameter * mm)
        hole = keyed.get(("hole", end, sign))
        if hole is not None:
            h_lo, h_hi = _bbox([hole])
            across = min((h_hi - h_lo)[k] for k in range(3) if k != axis_i)
            sources.append(round((across - diameter) / 2 * mm, 3))
        if axis_i != li:
            warnings.append(f"{obj.name}: pin is not along the body length")
        if standing:
            template["legs"].setdefault("_pivot_heights", []).append(
                (centre.z - all_lo.z) * mm / height
            )
    if pins:
        k = height / SPEC_DEFAULTS["reference_height_mm"]
        ref = sum(pins) / len(pins) / k
        template["legs"]["pin_mm"]["ref"] = round(min(4.0, max(2.0, ref)), 3)
        measured["leg_pin_mm"] = [round(p, 3) for p in sorted(pins)]
    if sources:
        measured["leg_source_clearance_mm"] = sorted(sources)
        if min(sources) < template["rules"]["clearance_mm"] - 0.02:
            warnings.append(
                f"source pin clearances {sorted(sources)} mm; template uses "
                f"{template['rules']['clearance_mm']} mm everywhere"
            )
    heights = template["legs"].pop("_pivot_heights", None)
    if heights:
        template["legs"]["pivot_height_rel"] = round(sum(heights) / len(heights), 4)

    # Waist between the front and rear bodies.
    if rear_objs:
        f_lo, f_hi = _bbox(roles["front"])
        r_lo, r_hi = _bbox(rear_objs)
        f_face = f_lo[li] if head_sign > 0 else f_hi[li]
        r_face = r_hi[li] if head_sign > 0 else r_lo[li]
        gap = abs(f_face - r_face) * mm
        waist = (f_face + r_face) / 2
        rel = abs(waist - rear_end) / abs(front_end - rear_end)
        template["waist"]["position_from_rear_rel"] = round(rel, 4)
        measured["waist_gap_mm"] = round(gap, 3)
        if gap < template["waist"]["gap_mm"]["ref"]:
            warnings.append(
                f"waist gap was {gap:.2f} mm; template keeps {template['waist']['gap_mm']['ref']} mm (dossier)"
            )
    if roles.get("cap") or roles.get("mid_pin"):
        measured["waist_source_joint"] = "cap + transverse pin"
        warnings.append("waist joint replaced by the spec's ball (cap + pin only for narrow torsos)")

    # Mirror checks: legs and pads.
    for end in ("front", "rear"):
        l, r = keyed.get(("leg", end, "-")), keyed.get(("leg", end, "+"))
        if l is not None and r is not None:
            dl, dr = _bbox([l])[1] - _bbox([l])[0], _bbox([r])[1] - _bbox([r])[0]
            diff = max(abs(a - b) for a, b in zip(dl, dr)) * mm
            if diff > 0.2:
                warnings.append(f"{end} legs differ by {diff:.2f} mm left/right (mirror check)")
        pads = {"-": 0, "+": 0}
        for pad in roles.get("pad", []):
            m = re.search(rf"{'frente' if end == 'front' else 'tras'}-x([+-])", pad.name)
            if m:
                pads[m.group(1)] += 1
        if pads["-"] != pads["+"]:
            warnings.append(
                f"{end} pads differ left/right ({pads['-']} vs {pads['+']} pieces): mirror error in the source"
            )
    template.update(
        {
            "format": FORMAT,
            "name": name,
            "description": description or f"Extracted from {len(objs)} named parts",
            "spec": "projeto-executivo-articulacao-quadrupedes.md v1.0",
            "measured": measured,
            "color_regions": sorted(o.name for o in roles.get("pad", [])),
            "source": {"parts": len(objs), "unknown_parts": sorted(unknown)},
            "warnings": warnings,
        }
    )
    folder = templates_dir() / name
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "template.json"
    path.write_text(json.dumps(template, indent=1, ensure_ascii=False), encoding="utf-8")
    saved = [str(path)]
    if save_meshes:
        refs = [o for role in ("knuckle", "leg_pin", "mid_pin", "cap") for o in roles.get(role, [])]
        if refs:
            blend = folder / "joints.blend"
            bpy.data.libraries.write(str(blend), set(refs), fake_user=True)
            saved.append(str(blend))
    return {
        "template": name,
        "saved": saved,
        "reference_height_mm": template["reference_height_mm"],
        "legs": template["legs"],
        "waist": template["waist"],
        "measured": measured,
        "warnings": warnings,
        "unknown_parts": sorted(unknown),
        "timings_s": round(time.perf_counter() - t0, 2),
    }


# ---------------------------------------------------------------- apply


def _figure_tree(name: str):
    bm = art._world_bm(art._part(name))
    try:
        return BVHTree.FromBMesh(bm), [v.co.copy() for v in bm.verts]
    finally:
        bm.free()


def _vertical_run(tree: BVHTree, x: float, y: float, z_from: float, floor: float):
    """(bottom, top) of the solid crossed by a vertical line at (x, y) that
    contains z_from or starts just above it."""
    origin = Vector((x, y, floor - 1.0))
    up = Vector((0.0, 0.0, 1.0))
    hits = art._ray_hits(tree, origin, up)
    runs = [(origin.z + hits[i], origin.z + hits[i + 1]) for i in range(0, len(hits) - 1, 2)]
    for a, b in runs:
        if b >= z_from:
            return a, b
    return None


def plan_from_template(target: str, template_name: str | None = None, options: dict | None = None) -> dict:
    options = dict(options or {})
    template = load_template(template_name)
    t0 = time.perf_counter()
    mm = art._mm()
    analysis = art.analyze_figure(target, options.get("others"))
    tree, verts = _figure_tree(target)
    floor = art._bu(analysis["floor_z_mm"])
    height = analysis["dimensions_mm"][2]  # mm
    k = height / template["reference_height_mm"]
    rules = template["rules"]
    clearance = float(options.get("clearance_mm", rules["clearance_mm"]))
    if not 0.05 <= clearance <= 1.0:
        raise ValueError("clearance_mm must be between 0.05 and 1")
    c = art._bu(clearance)
    li = 0 if analysis["length_axis"] == "X" else 1
    wi = 1 - li
    head = Vector(analysis["head_direction"])
    rear_dir = -head
    length_vec = Vector((1.0, 0.0, 0.0)) if li == 0 else Vector((0.0, 1.0, 0.0))
    width_vec = Vector((0.0, 1.0, 0.0)) if li == 0 else Vector((1.0, 0.0, 0.0))
    up = Vector((0.0, 0.0, 1.0))
    belly = art._bu(analysis["belly_z_mm"])
    top = floor + art._bu(height)
    centre_w = sum(v[wi] for v in verts) / max(1, len(verts))
    steps, notes, table = [], [], []
    use_waist = bool(options.get("waist", template["waist"]["enabled"]))
    use_legs = bool(options.get("legs", template["legs"]["enabled"]))
    front, rear = target, f"{target}_traseira"

    # Torso length: a slice just above the belly (chest to rear, under the
    # head), stopping at the tail root.
    band = [v for v in verts if belly + 0.1 * (top - belly) <= v.z <= belly + 0.35 * (top - belly)]
    waist_l = None
    if use_waist and band:
        signed = [v.dot(head) for v in band]
        chest, rear_end = max(signed), min(signed)
        tail = analysis.get("tail")
        if tail:
            rear_end = max(rear_end, Vector(art._vec_mm(tail["cut"]["point"], "tail")).dot(head))
        waist_rel = template["waist"]["position_from_rear_rel"]
        waist_s = rear_end + waist_rel * (chest - rear_end)
        point = Vector((0.0, 0.0, 0.0))
        point[li] = waist_s * head[li]
        point[wi] = centre_w
        run = None
        probe = point.copy()
        run = _vertical_run(tree, probe.x, probe.y, belly, floor)
        z_mid = (run[0] + run[1]) / 2 if run else (belly + top) / 2
        point.z = z_mid
        gap = _clamp(template["waist"]["gap_mm"], k)
        ball = _clamp(template["waist"]["ball_mm"], k)
        neck = _clamp(template["waist"]["neck_mm"], k)
        mouth = max(_clamp(template["waist"]["mouth_mm"], k), neck + 2 * clearance)
        mouth = min(mouth, ball - 0.4)
        # Torso width at the waist: the ball needs walls on both sides.
        widths = [v[wi] for v in verts if abs(v.dot(head) - waist_s) < art._bu(1.0) and run and run[0] <= v.z <= run[1]]
        torso_w = (max(widths) - min(widths)) * mm if widths else 0.0
        if torso_w and torso_w < ball + 2 * rules["min_wall_mm"] + 2 * clearance:
            notes.append(
                f"waist: torso only {torso_w:.1f} mm wide for a {ball} mm ball; "
                "use the cap + transverse pin variant or a smaller ball"
            )
        cut_point = point.copy()
        steps.append(
            {
                "op": "cut", "id": "waist", "part": target, "new_part_name": rear,
                "cutter": {"type": "plane", "point": art._point_mm(cut_point), "normal": [round(x, 6) for x in rear_dir]},
                "gap_mm": gap,
            }
        )
        # Ball on the front, socket in the rear: the pivot sits in the rear,
        # a lip of min_wall past the socket.
        depth = art._bu(ball / 2 + clearance + rules["min_wall_mm"] + 0.1)
        pivot = cut_point + rear_dir * depth
        steps.append(
            {
                "op": "joint", "id": "waist", "parent": rear, "child": front, "type": "ball",
                "pivot": art._point_mm(pivot), "axis": [0.0, 0.0, 1.0], "size_mm": ball,
                "neck_mm": neck, "mouth_mm": round(mouth, 3), "clearance_mm": clearance,
                "range_deg": template["waist"]["lateral_deg"], "relief": False,
            }
        )
        table.append({"item": "waist ball / socket", "mm": [ball, round(ball + 2 * clearance, 3)], "neck_mm": neck,
                      "mouth_mm": round(mouth, 3), "gap_mm": gap})
        waist_l = waist_s

    if use_legs:
        legs_t = template["legs"]
        pivot_z = floor + legs_t["pivot_height_rel"] * art._bu(height)
        dp_ref = _clamp(legs_t["pin_mm"], k)
        span_out = float(options.get("leg_range_deg", legs_t["range_deg"]["outward"]))
        span_in = float(legs_t["range_deg"].get("inward", 0.0))
        walk = str(options.get("leg_axis", "")).lower() == "walk"
        for leg in analysis["legs"]:
            label = leg["name"]
            if leg.get("existing_part"):
                notes.append(f"{label}: separate part {leg['part']}: weld it first (prepare_mesh weld_near)")
                continue
            box = leg["ball"]["cutter"]
            z_cut = art._bu(box["center"][2] + box["size"][2] / 2)
            foot = Vector((art._bu(leg["foot_center_mm"][0]), art._bu(leg["foot_center_mm"][1]), 0.0))
            ext = leg["column_size_mm"]
            ext_l, ext_w = art._bu(ext[analysis["length_axis"]]), art._bu(ext[analysis["width_axis"]])
            axis = width_vec if walk else length_vec
            along, across = (ext_w, ext_l) if walk else (ext_l, ext_w)
            radius = across / 2
            width_mm = across * mm
            dp = dp_ref
            need = dp + 2 * rules["min_wall_mm"] + 2 * clearance
            if width_mm < need:
                dp = round(width_mm - 2 * rules["min_wall_mm"] - 2 * clearance, 3)
                if dp < legs_t["pin_mm"]["min"]:
                    notes.append(
                        f"{label}: leg {width_mm:.1f} mm wide, under {legs_t['min_leg_width_mm']} mm: "
                        "scale the model up or use a bigger inner knuckle (spec 13); skipped"
                    )
                    continue
                notes.append(f"{label}: pin reduced to {dp} mm to keep 1.2 mm walls in a {width_mm:.1f} mm leg")
            pz = max(pivot_z, z_cut + radius * 0.5)
            if pz > pivot_z + 1e-6:
                notes.append(f"{label}: pivot raised to {art._to_mm(pz - floor)} mm to stay inside the body")
            pivot = foot.copy()
            pivot.z = pz
            side = 1.0 if label.endswith("right") else -1.0
            outward = width_vec * side
            turn = axis.cross(-up)
            if walk:
                rng = [-span_out / 2, span_out / 2]
            elif turn.dot(outward) > 0:
                rng = [-span_in, span_out]
            else:
                rng = [-span_out, span_in]
            foot_box = dict(box)
            column_size = [0.0, 0.0, 0.0]
            column_size[li] = round(ext_l * mm, 3)
            column_size[wi] = round(ext_w * mm, 3)
            column_size[2] = round((pz - z_cut + art._bu(0.5)) * mm, 3)
            column_centre = foot.copy()
            column_centre.z = (pz + z_cut - art._bu(0.5)) / 2
            knuckle_a = pivot - axis * (along / 2)
            knuckle_b = pivot + axis * (along / 2)
            parent = front
            if waist_l is not None:
                parent = front if foot.dot(head) > waist_l else rear
            child = f"{target}_{label}"
            steps.append(
                {
                    "op": "cut", "id": label, "part": parent, "new_part_name": child, "gap_mm": clearance,
                    "cutter": {
                        "type": "union",
                        "parts": [
                            foot_box,
                            {"type": "box", "center": art._point_mm(column_centre), "size": column_size},
                            {"type": "cylinder", "start": art._point_mm(knuckle_a), "end": art._point_mm(knuckle_b),
                             "radius": round(radius * mm, 3)},
                        ],
                    },
                }
            )
            reach = (pz - floor) + max(art._bu(leg["column_size_mm"][analysis["length_axis"]]), across) + art._bu(3.0)
            steps.append(
                {
                    "op": "joint", "id": label, "parent": parent, "child": child, "type": "pin",
                    "pivot": art._point_mm(pivot), "axis": [round(x, 6) for x in axis], "size_mm": dp,
                    "clearance_mm": clearance, "range_deg": rng, "relief": True,
                    "relief_radius_mm": round(reach * mm, 3),
                }
            )
            table.append(
                {
                    "item": label, "pin_mm": dp, "hole_mm": round(dp + 2 * clearance, 3),
                    "knuckle_radius_mm": round(radius * mm, 3), "pivot_height_mm": art._to_mm(pz - floor),
                    "range_deg": rng, "parent": parent,
                }
            )
    return {
        "target": target,
        "template": template["name"],
        "height_mm": height,
        "scale": round(k, 4),
        "clearance_mm": clearance,
        "table": table,
        "steps": steps,
        "notes": notes,
        "timings_s": round(time.perf_counter() - t0, 2),
    }


def _show_helpers(plan: dict) -> list[str]:
    """Wire cutters and pivot arrows in the BC_Plano collection."""
    art._clear_helpers()
    coll = bpy.data.collections.new(art.HELPER_COLLECTION)
    bpy.context.scene.collection.children.link(coll)
    names = []
    target = bpy.data.objects.get(plan["target"])
    reach, around = art._reach(target) if target else (art._bu(200), Vector())
    for step in plan["steps"]:
        if step["op"] == "cut":
            spec = art._parse_cutter(step["cutter"])
            for k, prim in enumerate(art._cutter_prims(spec)):
                bm = art._prim_bm(prim, 0.0, reach / 6 if prim["type"] == "plane" else reach, around)
                mesh = bpy.data.meshes.new(f"Plano_{step['id']}_{k}")
                bm.to_mesh(mesh)
                bm.free()
                obj = bpy.data.objects.new(mesh.name, mesh)
                obj.display_type = "WIRE"
                obj.hide_render = True
                coll.objects.link(obj)
                names.append(obj.name)
        elif step["op"] == "joint":
            empty = bpy.data.objects.new(f"Plano_pivo_{step['id']}", None)
            empty.empty_display_type = "SINGLE_ARROW"
            empty.empty_display_size = art._bu(6.0)
            empty.location = art._vec_mm(step["pivot"], "pivot")
            empty.rotation_mode = "QUATERNION"
            empty.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(Vector(step["axis"]))
            coll.objects.link(empty)
            names.append(empty.name)
    bpy.context.view_layer.update()
    return names


def apply_template(target: str, template_name: str | None = None, options: dict | None = None,
                   show_helpers: bool = True) -> dict:
    plan = plan_from_template(target, template_name, options)
    if show_helpers:
        plan["helpers"] = _show_helpers(plan)
    plan["next"] = (
        "Screenshot the helpers (collection BC_Plano: wire cutters, pivot arrows) "
        "with the table and ask the user to confirm. Then run articulate_figure("
        "name=target, plan={steps: <these steps>, only: [i, i+1]}) one cut + joint "
        "per call, check each row, print_check every part, compare_surface against "
        "the STL original, pose the legs at 0 deg (print pose) and export all parts as 3MF."
    )
    return plan


# ---------------------------------------------------------------- tools

register_tool(
    Tool(
        name="extract_articulation_template",
        description=(
            "Save the articulation system of a finished model as a reusable "
            "template (template.json + joints.blend in ~/Documents/BlenderCraft/"
            "templates/<template_name>/). Reads parts named by the SolidCraft "
            "convention (01- front body with head, 02- rear body, "
            "04-perna-frente|tras-x+|-, 05- tail, 06-bola- knuckles, 07-furo- "
            "holes, 08-pino-perna- leg pins, 09-pino-meio, 10-calota-meio, 11- "
            "pads), measures pin sizes, waist position and gap, pivot height "
            "(standing models) relative to the height, and fills the rest from "
            "the executive spec. Dossier fixes always apply: 0.30 mm clearances, "
            "1.3 mm waist gap, holes = pin + 2 x clearance, mirror checks. "
            "standing_height_mm: standing height of a model saved in print pose."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "template_name": {"type": "string"},
                "source_collection": {"type": "string"},
                "description": {"type": "string"},
                "standing_height_mm": {"type": "number"},
            },
            "required": ["template_name"],
        },
        handler=art._wrap(
            lambda a: extract_template(
                a["template_name"], a.get("source_collection"), a.get("description", ""),
                a.get("standing_height_mm"),
            )
        ),
        kind="file",
    )
)

register_tool(
    Tool(
        name="apply_articulation_template",
        description=(
            "Plan the articulation of a one-piece quadruped from a template "
            f"(default {DEFAULT_TEMPLATE}, the executive spec): finds the "
            "landmarks (analyze_figure), scales sizes by height / reference "
            "height within min/max (clearances and walls never scale) and "
            "returns a measurement table plus the steps for articulate_figure: "
            "waist cut + ball (front) in socket (rear), and per leg a cut "
            "(foot, column, round knuckle on a pin axis along the body) + pin "
            "joint swept 0-90 deg outward (pocket with the inverted-U opening). "
            "Shows the plan as helper objects in BC_Plano for a screenshot; "
            "changes nothing else. options: clearance_mm, legs, waist, "
            "leg_range_deg, leg_axis ('walk' swings legs fore/aft instead), "
            "others (separate parts of the figure)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "template_name": {"type": "string"},
                "options": {"type": "object"},
                "show_helpers": {"type": "boolean"},
                "description": {"type": "string"},
            },
            "required": ["target"],
        },
        handler=art._wrap(
            lambda a: apply_template(
                str(a["target"]), a.get("template_name"), a.get("options"), bool(a.get("show_helpers", True))
            )
        ),
        kind="scene",
    )
)

register_tool(
    Tool(
        name="list_articulation_templates",
        description=(
            "List the articulation templates: the user's (~/Documents/"
            "BlenderCraft/templates) and the built-in ones. "
            f"{DEFAULT_TEMPLATE} (the executive spec) is the default."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=art._wrap(lambda a: {"templates": list_templates()}),
        kind="read",
    )
)

register_tool(
    Tool(
        name="clear_plan_helpers",
        description="Remove the plan helper objects (collection BC_Plano) shown by apply_articulation_template.",
        input_schema={"type": "object", "properties": {"description": {"type": "string"}}},
        handler=art._wrap(lambda a: {"removed": art._clear_helpers()}),
        kind="scene",
    )
)
