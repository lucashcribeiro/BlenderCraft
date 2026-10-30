"""Scene inspection tools: inspect_scene, inspect_object and measure."""

import math

import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from ..units import format_length, get_display_unit, mm_per_bu
from .base import Tool, json_result, register_tool

# Millimeters per display unit, with the suffix used in areas and volumes.
_UNIT_MM = {
    "MM": (1.0, "mm"),
    "CM": (10.0, "cm"),
    "M": (1000.0, "m"),
    "IN": (25.4, "in"),
    "FT": (304.8, "ft"),
}


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


def _min_gap(bm_a, bm_b) -> float:
    """Smallest distance between two meshes, in BU (0 when they overlap).

    Takes the nearest point on the other mesh's BVH for every vertex of each
    mesh. Exact for vertex-to-surface closest pairs; it can overestimate
    when the closest pair is edge against edge.
    """
    tree_a, tree_b = _bvh(bm_a), _bvh(bm_b)
    if tree_a.overlap(tree_b):
        return 0.0
    best = math.inf
    for verts, tree in ((bm_a.verts, tree_b), (bm_b.verts, tree_a)):
        for v in verts:
            hit = tree.find_nearest(v.co)
            if hit and hit[3] is not None:
                best = min(best, hit[3])
    return best


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
        scale = mm_per_bu(scene) / _UNIT_MM[get_display_unit(scene)][0]
        suffix = _UNIT_MM[get_display_unit(scene)][1]
        return json_result(
            {
                "dimensions": _fmt(a.dimensions, scene),
                "volume": f"{volume * scale**3:.3f} {suffix}³",
                "area": f"{area * scale**2:.3f} {suffix}²",
            }
        )
    b = _get_object(str(b_name))
    bm_a, bm_b = _evaluated_world_mesh(a), _evaluated_world_mesh(b)
    try:
        gap = _min_gap(bm_a, bm_b)
    finally:
        bm_a.free()
        bm_b.free()
    distance = (a.matrix_world.translation - b.matrix_world.translation).length
    return json_result(
        {
            "distance_between_origins": format_length(distance, scene),
            "min_gap": format_length(gap, scene),
        }
    )


register_tool(
    Tool(
        name="inspect_scene",
        description=(
            "List every object in the scene with type, parent, location, "
            "rotation (degrees), scale, dimensions, materials, modifiers, "
            "visibility and selection. Lengths are in the displayed unit."
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
            "between the evaluated meshes (0 when they touch or overlap)."
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
