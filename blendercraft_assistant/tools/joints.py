"""Joints: test whether a part turns about a pivot without colliding.

A joint is an EMPTY named ``BC_Pivo_<part>`` that the part is parented to.
No animation data is ever created: the angle is set directly on the pivot's
rotation. The pivot carries these custom properties:

- ``bc_axis``: ``"X"``, ``"Y"`` or ``"Z"``, the local axis it turns about.
- ``bc_rest``: the pivot's rotation (Euler XYZ, radians) at angle 0.
- ``bc_angle``: the current angle in degrees relative to the rest pose. The
  panel reads it to show the slider; ``set_angle`` keeps it up to date.

Lengths and points are in Blender units (1 BU = 1 mm); angles are degrees.
"""

import math

import bmesh
import bpy
from mathutils import Euler, Matrix
from mathutils.bvhtree import BVHTree

from .base import Tool, json_result, register_tool
from .inspect import _get_object

_AXES = ("X", "Y", "Z")
# The sweep treats a limit as reached when it is this close (degrees).
_ANGLE_EPS = 1e-9
# Largest number of angles a single joint_test may visit.
_MAX_STEPS = 1440


def _check_axis(axis: str) -> str:
    axis = str(axis).upper()
    if axis not in _AXES:
        raise ValueError(f"axis must be X, Y or Z, got {axis!r}")
    return axis


def _get_pivot(name: str):
    pivot = _get_object(name)
    if "bc_axis" not in pivot:
        raise ValueError(f"Object is not a BlenderCraft pivot: {name}")
    return pivot


def create_pivot(part: str, point: tuple[float, float, float], axis: str) -> str:
    """Make ``part`` a child of a new pivot empty at ``point`` (BU).

    The part keeps its world transform. Returns the empty's name.
    """
    axis = _check_axis(axis)
    if len(point) != 3:
        raise ValueError("point must have three coordinates")
    obj = _get_object(part)
    if obj.parent is not None and "bc_axis" in obj.parent:
        raise ValueError(
            f"{part} already has the pivot {obj.parent.name}; "
            "use joint_set_angle or joint_test on it"
        )
    bpy.context.view_layer.update()
    world = obj.matrix_world.copy()

    pivot = bpy.data.objects.new(f"BC_Pivo_{part}", None)
    pivot.empty_display_type = "ARROWS"
    pivot.empty_display_size = max(max(obj.dimensions) * 0.1, 1.0)
    pivot.rotation_mode = "XYZ"
    pivot.location = tuple(float(c) for c in point)
    pivot["bc_axis"] = axis
    pivot["bc_rest"] = list(pivot.rotation_euler)
    pivot["bc_angle"] = 0.0
    collections = obj.users_collection or [bpy.context.scene.collection]
    collections[0].objects.link(pivot)
    bpy.context.view_layer.update()

    obj.parent = pivot
    obj.matrix_parent_inverse = pivot.matrix_world.inverted()
    # Re-applied so the world transform survives a previous parent too.
    obj.matrix_world = world
    return pivot.name


def set_angle(pivot: str, degrees: float) -> None:
    """Turn the pivot to ``degrees`` from its rest pose. Never keyframes."""
    obj = _get_pivot(pivot)
    rest = Euler(obj["bc_rest"], "XYZ")
    turn = Matrix.Rotation(math.radians(degrees), 3, obj["bc_axis"])
    obj.rotation_mode = "XYZ"
    obj.rotation_euler = (rest.to_matrix() @ turn).to_euler("XYZ", rest)
    obj["bc_angle"] = float(degrees)


def reset(pivot: str) -> None:
    """Put the pivot back at its rest pose (angle 0)."""
    set_angle(pivot, 0.0)


def _moving_meshes(pivot_obj) -> list:
    meshes = [o for o in pivot_obj.children_recursive if o.type == "MESH"]
    if not meshes:
        raise ValueError(f"Pivot {pivot_obj.name} has no mesh part")
    return meshes


def _local_bmesh(obj, depsgraph) -> bmesh.types.BMesh:
    """Evaluated mesh of ``obj`` (modifiers applied) in object space.

    Extracted once per mesh per joint_test: the geometry does not change
    while the pivot turns, only the world matrix does.
    """
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        bm = bmesh.new()
        bm.from_mesh(mesh)
    finally:
        evaluated.to_mesh_clear()
    return bm


def _obstacle_tree(obj, depsgraph) -> BVHTree:
    """BVH of a static mesh in world space (built once).

    ``BVHTree.FromObject`` would be in object space, which is useless for
    comparing objects.
    """
    bm = _local_bmesh(obj, depsgraph)
    try:
        bm.transform(obj.evaluated_get(depsgraph).matrix_world)
        return BVHTree.FromBMesh(bm)
    finally:
        bm.free()


def _posed_tree(local_bm, matrix) -> BVHTree:
    """BVH of a moving mesh in world space for the given world matrix."""
    posed = local_bm.copy()
    try:
        posed.transform(matrix)
        return BVHTree.FromBMesh(posed)
    finally:
        posed.free()


def _default_obstacles(moving: list) -> list:
    skip = {o.name for o in moving}
    return [
        o
        for o in bpy.context.scene.objects
        if o.type == "MESH" and o.name not in skip and o.visible_get()
    ]


def _sweep_angles(limit: float, step: float) -> list[float]:
    """Angles after 0 up to ``limit`` (signed); ends exactly at ``limit``."""
    if abs(limit) <= _ANGLE_EPS:
        return []
    sign = 1.0 if limit > 0 else -1.0
    angles = []
    k = 1
    while k * step < abs(limit) - _ANGLE_EPS:
        angles.append(sign * k * step)
        k += 1
    angles.append(float(limit))
    return angles


def joint_test(
    pivot: str,
    against: list[str] | None = None,
    start: float = -180.0,
    end: float = 180.0,
    step: float = 5.0,
) -> dict:
    """Sweep the joint from 0 to ``end`` and from 0 to ``start``.

    Each direction stops at the first collision (BVH overlap between the
    moving meshes and the obstacles). ``free_min``/``free_max`` are the last
    angles reached without collision; when a sweep reaches its limit freely
    the value is the limit exactly. If the part already collides at rest the
    sweeps are skipped and both are 0. The pivot's previous pose is restored.

    Only surface overlap counts: a mesh lying entirely inside another is not
    detected.
    """
    if step <= 0:
        raise ValueError("step must be greater than 0")
    if start > 0:
        raise ValueError("start must be 0 or negative")
    if end < 0:
        raise ValueError("end must be 0 or positive")
    if (end - start) / step > _MAX_STEPS + _ANGLE_EPS:
        raise ValueError(
            f"too many steps: {(end - start) / step:.0f} (max {_MAX_STEPS}); "
            "use a larger step"
        )
    pivot_obj = _get_pivot(pivot)
    moving = _moving_meshes(pivot_obj)
    if against is None:
        obstacles = _default_obstacles(moving)
    else:
        moving_names = {o.name for o in moving}
        obstacles = [
            o for o in (_get_object(n) for n in against) if o.name not in moving_names
        ]
        for obstacle in obstacles:
            if obstacle.type != "MESH":
                raise ValueError(f"Object is not a mesh: {obstacle.name}")

    saved_rotation = pivot_obj.rotation_euler.copy()
    saved_mode = pivot_obj.rotation_mode
    saved_angle = pivot_obj.get("bc_angle")
    moving_meshes: list = []  # (object, local-space bmesh), freed in finally
    try:
        bpy.context.view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        static_trees = [(o.name, _obstacle_tree(o, depsgraph)) for o in obstacles]
        for part in moving:
            moving_meshes.append((part, _local_bmesh(part, depsgraph)))

        def collision_with() -> str | None:
            # Called after set_angle: refresh the depsgraph first.
            bpy.context.view_layer.update()
            graph = bpy.context.evaluated_depsgraph_get()
            for part, local_bm in moving_meshes:
                tree = _posed_tree(local_bm, part.evaluated_get(graph).matrix_world)
                for name, other in static_trees:
                    if tree.overlap(other):
                        return name
            return None

        collisions: list[dict] = []
        # The pivot may be parked at any pose: the rest check is about 0.
        set_angle(pivot, 0.0)
        at_rest = collision_with()
        if at_rest is not None:
            return {
                "free_min": 0.0,
                "free_max": 0.0,
                "colliding_at_rest": True,
                "collisions": [{"angle": 0.0, "with": at_rest}],
            }

        free = {}
        for key, limit in (("free_max", end), ("free_min", start)):
            free[key] = 0.0
            for angle in _sweep_angles(limit, step):
                set_angle(pivot, angle)
                hit = collision_with()
                if hit is not None:
                    collisions.append({"angle": angle, "with": hit})
                    break
                free[key] = angle
        return {
            "free_min": free["free_min"],
            "free_max": free["free_max"],
            "colliding_at_rest": False,
            "collisions": collisions,
        }
    finally:
        for _, local_bm in moving_meshes:
            local_bm.free()
        pivot_obj.rotation_mode = saved_mode
        pivot_obj.rotation_euler = saved_rotation
        if saved_angle is None:
            if "bc_angle" in pivot_obj:
                del pivot_obj["bc_angle"]
        else:
            pivot_obj["bc_angle"] = saved_angle
        bpy.context.view_layer.update()


def _joint_create(args: dict):
    pivot = create_pivot(str(args["part"]), tuple(args["point"]), args["axis"])
    obj = _get_object(pivot)
    return json_result({"pivot": pivot, "axis": obj["bc_axis"], "point": list(args["point"])})


def _joint_set_angle(args: dict):
    set_angle(str(args["pivot"]), float(args["degrees"]))
    return json_result({"pivot": args["pivot"], "angle_deg": float(args["degrees"])})


def _joint_test(args: dict):
    return json_result(
        joint_test(
            str(args["pivot"]),
            against=args.get("against"),
            start=float(args.get("start", -180.0)),
            end=float(args.get("end", 180.0)),
            step=float(args.get("step", 5.0)),
        )
    )


register_tool(
    Tool(
        name="joint_create",
        description=(
            "Make a part rotate about a pivot: creates an empty BC_Pivo_<part> "
            "at point (Blender units, mm) and parents the part to it, keeping "
            "its position. axis is X, Y or Z. No animation is used."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "part": {"type": "string"},
                "point": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                "axis": {"type": "string", "enum": list(_AXES)},
                "description": {"type": "string"},
            },
            "required": ["part", "point", "axis"],
        },
        handler=_joint_create,
        kind="scene",
    )
)

register_tool(
    Tool(
        name="joint_set_angle",
        description=(
            "Turn a joint pivot to an angle in degrees from its rest pose. "
            "Changes the rotation only, never creates keyframes."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "pivot": {"type": "string"},
                "degrees": {"type": "number"},
                "description": {"type": "string"},
            },
            "required": ["pivot", "degrees"],
        },
        handler=_joint_set_angle,
        kind="scene",
        push_undo=True,
    )
)

register_tool(
    Tool(
        name="joint_test",
        description=(
            "Test a joint without animating: sweep from 0 to end and from 0 "
            "to start (degrees, every step) and stop in each direction at the "
            "first collision with the against objects (default: every visible "
            "mesh except the part and its children). Returns free_min and "
            "free_max (last free angles), colliding_at_rest and the "
            "collisions found. The pivot's angle is restored afterwards."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "pivot": {"type": "string"},
                "against": {"type": "array", "items": {"type": "string"}},
                "start": {"type": "number", "description": "0 or negative, default -180"},
                "end": {"type": "number", "description": "0 or positive, default 180"},
                "step": {"type": "number", "description": "degrees, default 5"},
            },
            "required": ["pivot"],
        },
        handler=_joint_test,
        kind="read",
    )
)
