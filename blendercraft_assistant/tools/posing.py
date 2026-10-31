"""Pose a quadruped's legs: standing (legs down, soles on Z = 0) or print
pose (legs flat, pointing outward), and measure it standing.

Legs are found by their hinge:
1. a BlenderCraft pivot (BC_Pivo_*, from joint_create / add_joint) whose
   name mentions a leg (perna / leg): turned with joints.set_angle;
2. otherwise the SolidCraft convention: 04-perna-<frente|tras>-x<+|->
   with its pin 08-pino-perna-<same> (hinge axis = the pin's longest side,
   through its centre); the leg turns together with every part named for the
   same leg (06-bola, 07-furo, 11- pads and toes).

set_pose remembers the pose before its first change, so target "rest" puts
everything back. measure_standing poses standing only for the measurement
and always restores the exact previous transforms.
"""

import json
import math
import re

import bpy
from mathutils import Matrix, Vector

from . import articulate as art
from . import joints
from .base import Tool, register_tool

_LEG_WORD = re.compile(r"perna|leg", re.IGNORECASE)
_SC_LEG = re.compile(r"^04-perna-(frente|tras)-x([+-])")
_REST_KEY = "bc_pose_rest"
_DOWN = Vector((0.0, 0.0, -1.0))
# Dossier section 8, at 35 mm high.
_TARGETS_35 = {
    "height": 35.0,
    "length": 42.0,
    "back_height": 22.8,
    "belly_height": 8.1,
    "pivot_to_sole": 13.4,
    "pivot_height": 13.3,
    "left_right_gap": 3.8,
    "legs_out_to_out": 20.8,
    "rear_leg_from_rear": 10.0,
    "front_leg_from_rear": 26.5,
    "eye_diameter": 6.5,
    "eye_spacing": 9.8,
}


def _figure(parts) -> list:
    if parts:
        return [art._get_object(str(n)) for n in parts]
    return [
        o for o in bpy.context.scene.objects
        if (o.type == "MESH" and o.visible_get()) or "bc_axis" in o
    ]


def _bbox(objs):
    pts = [o.matrix_world @ Vector(c) for o in objs if o.type == "MESH" for c in o.bound_box]
    if not pts:
        return None, None
    lo = Vector([min(p[i] for p in pts) for i in range(3)])
    hi = Vector([max(p[i] for p in pts) for i in range(3)])
    return lo, hi


def _mesh_lowest(objs) -> float | None:
    low = None
    for o in objs:
        if o.type != "MESH":
            continue
        mw = o.matrix_world
        z = min((mw @ v.co).z for v in o.data.vertices) if o.data.vertices else None
        if z is not None:
            low = z if low is None else min(low, z)
    return low


def _centroid(obj) -> Vector:
    mw = obj.matrix_world
    corners = [mw @ Vector(c) for c in obj.bound_box]
    return sum(corners, Vector()) / 8


def find_legs(figure: list) -> list[dict]:
    """Every leg with its hinge: point, axis, what turns and how."""
    legs, taken = [], set()
    for pivot in figure:
        if "bc_axis" not in pivot:
            continue
        meshes = [o for o in pivot.children_recursive if o.type == "MESH"]
        if not meshes or not (_LEG_WORD.search(pivot.name) or _role(pivot) == "leg"):
            continue
        axis_i = "XYZ".index(pivot["bc_axis"])
        axis = (pivot.matrix_world.to_3x3() @ Matrix.Identity(3).col[axis_i]).normalized()
        main = max(meshes, key=lambda o: len(o.data.vertices))
        legs.append({"name": pivot.name, "mode": "pivot", "pivot": pivot,
                     "point": pivot.matrix_world.translation.copy(), "axis": axis,
                     "main": main, "objects": meshes})
        taken.update(o.name for o in meshes)
    names = {o.name: o for o in figure}
    for obj in figure:
        m = _SC_LEG.match(obj.name)
        if not m or obj.name in taken:
            continue
        end, sign = m.groups()
        key = f"{end}-x{sign}"
        pin = next((o for n, o in names.items() if n.startswith(f"08-pino-perna-{key}")), None)
        if pin is None:
            continue
        lo, hi = _bbox([pin])
        size = hi - lo
        i = max(range(3), key=lambda k: size[k])
        axis = Vector((0.0, 0.0, 0.0))
        axis[i] = 1.0
        group = [
            o for n, o in names.items()
            if o.type == "MESH" and re.match(r"^(04|06|07|11)-", n) and re.search(rf"{re.escape(key)}(?![+-])", n)
        ]
        legs.append({"name": obj.name, "mode": "objects", "pivot": None, "point": (lo + hi) / 2,
                     "axis": axis, "main": obj, "objects": group})
    return legs


def _role(pivot) -> str | None:
    try:
        return json.loads(pivot.get("bc_joint", "{}")).get("role")
    except (TypeError, ValueError):
        return None


def _state(leg: dict, centre: Vector) -> tuple[float, float, Vector]:
    """Signed angles (radians) about the hinge axis that bring the leg down
    (standing) and flat outward (print), and the outward direction."""
    a = leg["axis"]
    d = _centroid(leg["main"]) - leg["point"]
    d -= a * d.dot(a)
    out = _centroid(leg["main"]) - centre
    out.z = 0.0
    out -= a * out.dot(a)
    if out.length < 1e-9:
        out = a.cross(_DOWN)
    out.normalize()
    down = _DOWN - a * _DOWN.dot(a)

    def signed(u, v):
        if u.length < 1e-9 or v.length < 1e-9:
            return 0.0
        return math.atan2(a.dot(u.cross(v)), u.dot(v))

    return signed(d, down), signed(d, out), out


def _turn(leg: dict, angle: float) -> None:
    if abs(angle) < 1e-9:
        return
    if leg["mode"] == "pivot":
        pivot = leg["pivot"]
        current = float(pivot.get("bc_angle", 0.0))
        # The pivot's local axis may point against the world axis we used.
        joints.set_angle(pivot.name, current + math.degrees(angle))
        return
    rot = Matrix.Translation(leg["point"]) @ Matrix.Rotation(angle, 4, leg["axis"]) @ Matrix.Translation(-leg["point"])
    names = {o.name for o in leg["objects"]}
    for obj in leg["objects"]:
        if obj.parent is not None and obj.parent.name in names:
            continue
        obj.matrix_world = rot @ obj.matrix_world


def _snapshot(figure: list) -> dict:
    data = {}
    for o in figure:
        entry = {"m": [list(r) for r in o.matrix_world]}
        if "bc_axis" in o:
            entry["angle"] = float(o.get("bc_angle", 0.0))
        data[o.name] = entry
    return data


def _restore(snapshot: dict) -> None:
    # Pivot angles first (they move children), then every world matrix.
    for name, entry in snapshot.items():
        obj = bpy.data.objects.get(name)
        if obj is not None and "angle" in entry and "bc_axis" in obj:
            joints.set_angle(name, entry["angle"])
    bpy.context.view_layer.update()
    for name, entry in snapshot.items():
        obj = bpy.data.objects.get(name)
        if obj is not None:
            obj.matrix_world = Matrix(entry["m"])
            bpy.context.view_layer.update()


def _ground(figure: list) -> float:
    """Shift the figure's root objects so its lowest point is at Z = 0."""
    bpy.context.view_layer.update()
    low = _mesh_lowest(figure)
    if low is None or abs(low) < 1e-9:
        return 0.0
    names = {o.name for o in figure}
    for obj in figure:
        if obj.parent is None or obj.parent.name not in names:
            obj.matrix_world = Matrix.Translation((0.0, 0.0, -low)) @ obj.matrix_world
    bpy.context.view_layer.update()
    return -low


def apply_pose(target: str, parts=None, remember: bool = True) -> dict:
    target = str(target).lower()
    if target not in ("standing", "print", "rest"):
        raise ValueError("target must be standing, print or rest")
    figure = _figure(parts)
    scene = bpy.context.scene
    if target == "rest":
        saved = scene.get(_REST_KEY)
        if not saved:
            return {"target": "rest", "changed": False, "note": "nothing to restore: set_pose was not used"}
        _restore(json.loads(saved))
        del scene[_REST_KEY]
        return {"target": "rest", "changed": True}
    legs = find_legs(figure)
    if not legs:
        raise ValueError(
            "No legs found: they need a BC_Pivo_ pivot named for a leg, or SolidCraft "
            "names (04-perna-frente|tras-x+|- with 08-pino-perna-<same>)"
        )
    if remember and not scene.get(_REST_KEY):
        scene[_REST_KEY] = json.dumps(_snapshot(figure))
    lo, hi = _bbox(figure)
    centre = (lo + hi) / 2
    report = []
    for leg in legs:
        to_down, to_out, _ = _state(leg, centre)
        angle = to_down if target == "standing" else to_out
        before = "standing" if abs(to_down) < math.radians(10) else (
            "print" if abs(to_out) < math.radians(10) else "other")
        _turn(leg, angle)
        bpy.context.view_layer.update()
        report.append({"leg": leg["name"], "was": before, "turned_deg": round(math.degrees(angle), 2),
                       "hinge": leg["mode"], "moved": [o.name for o in leg["objects"]]})
    shift = _ground(figure)
    return {"target": target, "legs": report, "raised_mm": art._to_mm(shift),
            "note": "set_pose target 'rest' restores the pose before the first set_pose"}


def _measure(figure: list, legs: list) -> list[dict]:
    lo, hi = _bbox(figure)
    size = hi - lo
    li = 0 if size.x >= size.y else 1
    wi = 1 - li
    leg_names = {o.name for leg in legs for o in leg["objects"]}
    joint_parts = re.compile(r"^(06|07|08|09|10|11)-")
    torso = [o for o in figure if o.type == "MESH" and o.name not in leg_names and not joint_parts.match(o.name)]
    t_lo, t_hi = _bbox(torso) if torso else (lo, hi)
    floor = lo.z
    rows = {"height": size.z, "length": size[li]}
    rows["back_height"] = t_hi.z - floor
    rows["belly_height"] = t_lo.z - floor
    # The rear: the torso end farthest from the head side (head = taller end).
    ends = []
    for leg in legs:
        c = _centroid(leg["main"])
        low = _mesh_lowest(leg["objects"])
        ends.append((leg, c, low))
    if ends:
        rows["pivot_to_sole"] = sum(l["point"].z - low for l, _, low in ends) / len(ends)
        rows["pivot_height"] = sum(l["point"].z - floor for l, _, _ in ends) / len(ends)
        rear_sign = _rear_sign(figure, li, t_lo, t_hi)
        rear_end = t_hi[li] if rear_sign > 0 else t_lo[li]
        by_end = {}
        for leg, c, _ in ends:
            key = "rear" if (c[li] - (t_lo[li] + t_hi[li]) / 2) * rear_sign > 0 else "front"
            by_end.setdefault(key, []).append(leg)
        for key, group in by_end.items():
            dist = sum(abs(_centroid(l["main"])[li] - rear_end) for l in group) / len(group)
            rows[f"{key}_leg_from_rear"] = dist
            if len(group) == 2:
                boxes = [_bbox(l["objects"]) for l in group]
                boxes.sort(key=lambda b: b[0][wi])
                rows.setdefault("left_right_gap", boxes[1][0][wi] - boxes[0][1][wi])
                rows.setdefault("legs_out_to_out", boxes[1][1][wi] - boxes[0][0][wi])
    eyes = [o for o in figure if o.type == "MESH" and re.search(r"olho|eye", o.name, re.IGNORECASE)]
    if eyes:
        dims = [max(o.dimensions) for o in eyes]
        rows["eye_diameter"] = sum(dims) / len(dims)
        if len(eyes) >= 2:
            rows["eye_spacing"] = (_centroid(eyes[0]) - _centroid(eyes[1])).length
    k = size.z * art._mm() / _TARGETS_35["height"]
    out = []
    for item, value in rows.items():
        current = value * art._mm()
        target = _TARGETS_35.get(item)
        row = {"item": item, "current_mm": round(current, 2)}
        if target is not None:
            row["target_mm"] = round(target * k, 2)
            row["diff_mm"] = round(current - target * k, 2)
            row["diff_percent"] = round(100 * (current - target * k) / (target * k), 1) if target else None
        out.append(row)
    return out


def _rear_sign(figure, li, t_lo, t_hi) -> int:
    """+1 if the rear is toward +length: the head end is the taller one."""
    mid = (t_lo[li] + t_hi[li]) / 2
    top_pos, top_neg = -math.inf, -math.inf
    for o in figure:
        if o.type != "MESH":
            continue
        lo, hi = _bbox([o])
        c = (lo[li] + hi[li]) / 2
        if c > mid:
            top_pos = max(top_pos, hi.z)
        else:
            top_neg = max(top_neg, hi.z)
    return -1 if top_pos > top_neg else 1


def measure_standing(parts=None) -> dict:
    figure = _figure(parts)
    legs = find_legs(figure)
    if not legs:
        raise ValueError("No legs found to stand the figure up (see set_pose)")
    snapshot = _snapshot(figure)
    try:
        lo, hi = _bbox(figure)
        centre = (lo + hi) / 2
        for leg in legs:
            to_down, _, _ = _state(leg, centre)
            _turn(leg, to_down)
            bpy.context.view_layer.update()
        _ground(figure)
        legs = find_legs(figure)
        rows = _measure(figure, legs)
    finally:
        _restore(snapshot)
    return {
        "table": rows,
        "reference": "dossier section 8 targets scaled by height / 35 mm",
        "legs": [l["name"] for l in legs],
        "note": "measured standing; the previous pose was restored exactly",
    }


register_tool(
    Tool(
        name="set_pose",
        description=(
            "Turn a quadruped's legs about their hinges: 'standing' (legs down, "
            "soles on Z = 0), 'print' (legs flat, pointing outward, figure on "
            "the bed) or 'rest' (back to the pose before the first set_pose). "
            "Hinges come from BC_Pivo_ pivots named for legs, else from the "
            "SolidCraft pins (08-pino-perna-*, axis = longest side); each leg "
            "turns with its knuckle, hole and pads (06-, 07-, 11- of the same "
            "leg). Idempotent: reports each leg's previous state and the angle "
            "applied. parts limits it to those objects (default: visible "
            "meshes and pivots)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "target": {"type": "string", "enum": ["standing", "print", "rest"]},
                "parts": {"type": "array", "items": {"type": "string"}},
                "description": {"type": "string"},
            },
            "required": ["target"],
        },
        handler=art._wrap(lambda a: apply_pose(a["target"], a.get("parts"))),
        kind="scene",
    )
)

register_tool(
    Tool(
        name="measure_standing",
        description=(
            "Measure a quadruped standing even when its legs are in print "
            "pose: stands it up for the measurement only (the exact previous "
            "pose is always restored) and returns height, length, back and "
            "belly height, pivot height and pivot-to-sole, left/right gap, "
            "legs out to out, front/rear leg positions from the rear and eyes "
            "(if named olho/eye), each with the dossier target scaled to the "
            "height (section 8) and the difference."
        ),
        input_schema={
            "type": "object",
            "properties": {"parts": {"type": "array", "items": {"type": "string"}}},
        },
        handler=art._wrap(lambda a: measure_standing(a.get("parts"))),
        kind="read",
    )
)
