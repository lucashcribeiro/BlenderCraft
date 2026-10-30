"""Scene inspection tools: inspect_scene, inspect_object and measure."""

import math

import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from ..units import (
    display_scale_and_suffix,
    format_length,
    get_display_unit,
    mm_per_bu,
)
from .base import Tool, json_result, register_tool

# Above this many edge pairs the exact edge-edge search is skipped.
_EDGE_PAIR_LIMIT = 2_000_000


def _fmt(values, scene) -> list[str]:
    return [format_length(v, scene) for v in values]


def _get_object(name: str):
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ValueError(f"Object not found: {name}")
    return obj


def _rotation_deg(obj) -> list[float]:
    # rotation_euler is stale in QUATERNION / AXIS_ANGLE mode.
    if obj.rotation_mode in {"QUATERNION", "AXIS_ANGLE"}:
        euler = obj.matrix_basis.to_euler()
    else:
        euler = obj.rotation_euler
    return [round(math.degrees(a), 3) for a in euler]


def _object_info(obj, scene) -> dict:
    return {
        "name": obj.name,
        "type": obj.type,
        "parent": obj.parent.name if obj.parent else None,
        "location": _fmt(obj.location, scene),
        "rotation_deg": _rotation_deg(obj),
        "scale": [round(s, 6) for s in obj.scale],
        "dimensions": _fmt(obj.dimensions, scene),
        "materials": [
            slot.material.name for slot in obj.material_slots if slot.material
        ],
        "modifiers": [m.name for m in obj.modifiers],
        "visible": obj.visible_get(),
        "selected": obj.select_get(),
    }


def _evaluated_world_mesh(obj) -> bmesh.types.BMesh:
    """Evaluated mesh of obj (modifiers applied) in world space."""
    if obj.type != "MESH":
        raise ValueError(f"Object is not a mesh: {obj.name}")
    depsgraph = bpy.context.evaluated_depsgraph_get()
    mesh = obj.evaluated_get(depsgraph).to_mesh()
    try:
        bm = bmesh.new()
        bm.from_mesh(mesh)
    finally:
        obj.evaluated_get(depsgraph).to_mesh_clear()
    bm.transform(obj.matrix_world)
    return bm


def _inspect_scene(args: dict):
    bpy.context.view_layer.update()
    scene = bpy.context.scene
    active = bpy.context.view_layer.objects.active
    return json_result(
        {
            "unit": get_display_unit(scene),
            # Not always 1: files made outside BlenderCraft keep their scale.
            "mm_per_bu": mm_per_bu(scene),
            "objects": [_object_info(o, scene) for o in scene.objects],
            "active": active.name if active else None,
        }
    )


def _inspect_object(args: dict):
    bpy.context.view_layer.update()
    scene = bpy.context.scene
    obj = _get_object(str(args["name"]))
    info = _object_info(obj, scene)
    if obj.type == "MESH":
        bm = _evaluated_world_mesh(obj)
        try:
            info["vertices"] = len(bm.verts)
            info["faces"] = len(bm.faces)
            coords = [v.co for v in bm.verts]
            lo = [min(c[i] for c in coords) for i in range(3)] if coords else [0.0] * 3
            hi = [max(c[i] for c in coords) for i in range(3)] if coords else [0.0] * 3
        finally:
            bm.free()
    else:
        info["vertices"] = None
        info["faces"] = None
        corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        lo = [min(c[i] for c in corners) for i in range(3)]
        hi = [max(c[i] for c in corners) for i in range(3)]
    info["bbox_min"] = _fmt(lo, scene)
    info["bbox_max"] = _fmt(hi, scene)
    return json_result(info)


def _bvh(bm) -> BVHTree:
    return BVHTree.FromBMesh(bm)


def _segment_distance(p1, q1, p2, q2) -> float:
    """Closest distance between segments p1-q1 and p2-q2 (Ericson 5.1.9)."""
    d1, d2, r = q1 - p1, q2 - p2, p1 - p2
    a, e, f = d1.dot(d1), d2.dot(d2), d2.dot(r)
    eps = 1e-12
    if a <= eps and e <= eps:
        return r.length
    if a <= eps:
        s, t = 0.0, min(max(f / e, 0.0), 1.0)
    else:
        c = d1.dot(r)
        if e <= eps:
            s, t = min(max(-c / a, 0.0), 1.0), 0.0
        else:
            b = d1.dot(d2)
            denom = a * e - b * b
            # Parallel segments (denom == 0): pick any s, t is fixed below.
            s = min(max((b * f - c * e) / denom, 0.0), 1.0) if denom > eps else 0.0
            t = (b * s + f) / e
            if t < 0.0:
                t, s = 0.0, min(max(-c / a, 0.0), 1.0)
            elif t > 1.0:
                t, s = 1.0, min(max((b - c) / a, 0.0), 1.0)
    return ((p1 + d1 * s) - (p2 + d2 * t)).length


def _edge_gap(bm_a, bm_b, best: float) -> float:
    """Smallest edge-to-edge distance, never above ``best``.

    Edge pairs whose bounding boxes are already farther apart than the best
    distance found so far are skipped.
    """
    def boxes(bm):
        out = []
        for e in bm.edges:
            p, q = e.verts[0].co.copy(), e.verts[1].co.copy()
            lo = tuple(min(p[i], q[i]) for i in range(3))
            hi = tuple(max(p[i], q[i]) for i in range(3))
            out.append((lo, hi, p, q))
        return out

    boxes_a, boxes_b = boxes(bm_a), boxes(bm_b)
    for lo_a, hi_a, p1, q1 in boxes_a:
        for lo_b, hi_b, p2, q2 in boxes_b:
            sq = 0.0
            for i in range(3):
                d = max(lo_a[i] - hi_b[i], lo_b[i] - hi_a[i], 0.0)
                sq += d * d
            if sq >= best * best:
                continue
            best = min(best, _segment_distance(p1, q1, p2, q2))
    return best


def _min_gap(bm_a, bm_b) -> tuple[float | None, bool]:
    """Smallest distance between two meshes in BU, and whether it is approximate.

    0 when the surfaces overlap. Otherwise the minimum of vertex-to-surface
    distances (BVH) both ways and edge-to-edge distances. The edge-to-edge
    search is skipped above _EDGE_PAIR_LIMIT edge pairs, which makes the
    result approximate (it can overestimate). None when there is no geometry.
    """
    tree_a, tree_b = _bvh(bm_a), _bvh(bm_b)
    if tree_a.overlap(tree_b):
        return 0.0, False
    best = math.inf
    for verts, tree in ((bm_a.verts, tree_b), (bm_b.verts, tree_a)):
        for v in verts:
            hit = tree.find_nearest(v.co)
            if hit and hit[3] is not None:
                best = min(best, hit[3])
    approximate = len(bm_a.edges) * len(bm_b.edges) > _EDGE_PAIR_LIMIT
    if not approximate and bm_a.edges and bm_b.edges:
        best = _edge_gap(bm_a, bm_b, best)
    if best == math.inf:
        return None, False
    return best, approximate


def _with_unit(value: float, suffix: str, power: str) -> str:
    return f"{value:.3f} {suffix}{power}" if suffix else f"{value:.3f}"


def _measure(args: dict):
    bpy.context.view_layer.update()
    scene = bpy.context.scene
    a = _get_object(str(args["a"]))
    b_name = args.get("b")
    if not b_name:
        bm = _evaluated_world_mesh(a)
        try:
            volume = abs(bm.calc_volume(signed=True))
            area = sum(f.calc_area() for f in bm.faces)
        finally:
            bm.free()
        scale, suffix = display_scale_and_suffix(scene)
        return json_result(
            {
                "dimensions": _fmt(a.dimensions, scene),
                "volume": _with_unit(volume * scale**3, suffix, "³"),
                "area": _with_unit(area * scale**2, suffix, "²"),
            }
        )
    b = _get_object(str(b_name))
    bm_a, bm_b = _evaluated_world_mesh(a), _evaluated_world_mesh(b)
    try:
        gap, approximate = _min_gap(bm_a, bm_b)
    finally:
        bm_a.free()
        bm_b.free()
    distance = (a.matrix_world.translation - b.matrix_world.translation).length
    result = {
        "distance_between_origins": format_length(distance, scene),
        "min_gap": None if gap is None else format_length(gap, scene),
    }
    if gap is None:
        result["note"] = "min_gap unavailable: a mesh has no geometry"
    elif approximate:
        result["approximate"] = True
        result["note"] = (
            "min_gap is approximate: the meshes are too large for the exact "
            "edge-to-edge search, so it may overestimate"
        )
    return json_result(result)


register_tool(
    Tool(
        name="inspect_scene",
        description=(
            "List every object in the scene with type, parent, location, "
            "rotation (degrees), scale, dimensions, materials, modifiers, "
            "visibility and selection. Lengths are in the displayed unit; "
            "mm_per_bu is the file's scale (millimeters per Blender unit)."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=_inspect_scene,
        kind="read",
    )
)

register_tool(
    Tool(
        name="inspect_object",
        description=(
            "Inspect one object by name: same fields as inspect_scene plus "
            "vertex and face counts and the world bounding box."
        ),
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        handler=_inspect_object,
        kind="read",
    )
)

register_tool(
    Tool(
        name="measure",
        description=(
            "Measure objects. With only a: dimensions, volume and surface "
            "area. With a and b: distance between origins and the minimum gap "
            "between the evaluated meshes (0 when they touch or overlap). The gap is "
            "exact unless the meshes have more than 2 million edge pairs; then "
            "the result carries \"approximate\": true and may overestimate."
        ),
        input_schema={
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
            "required": ["a"],
        },
        handler=_measure,
        kind="read",
    )
)
