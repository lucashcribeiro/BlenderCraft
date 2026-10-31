"""Articulate an existing figure: prepare the mesh, analyze it, cut it into
parts and build real print-in-place joints between the parts.

Every length in the tool arguments and results is in millimeters (converted
with units.mm_per_bu); angles are degrees. Geometry is built in world space
and applied with EXACT booleans, so a part may carry any transform.

Joint rules (the user's dossier): clearances are absolute, never scaled with
the figure: 0.30 mm per side for print-in-place, 0.20 mm for assembled parts.
Male and female parts never overlap: every hole, socket and relief is a real
subtraction, and add_joint measures the result and refuses (rolling back)
when a gap is under clearance - 0.02 mm.
"""

import json
import math
import time

import bmesh
import bpy
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

from ..units import mm_per_bu
from . import joints
from .base import Tool, json_result, register_tool
from .inspect import _evaluated_world_mesh, _get_object, _min_gap
from .topology import _count_components, mesh_topology

# A measured gap may be this much under the clearance (tessellation).
_GAP_TOLERANCE_MM = 0.02
# Thinnest wall left around sockets, holes and pin anchors.
_MIN_WALL_MM = 1.2
# A pin is embedded at least this deep in each wall, under this much skin.
_PIN_EMBED_MM = 1.5
_PIN_MIN_EMBED_MM = 1.2
_PIN_SKIN_MM = 1.2
# Segments of the round primitives: a 48-gon deviates 0.006 mm from a
# 3 mm circle, far under the gap tolerance.
_SEGMENTS = 48
# Largest angle between two rotated copies of a swept relief.
_SWEEP_STEP_DEG = 3.0
_SOLVER = "EXACT"
_JOINT_TYPES = ("ball", "pin", "cap")
_DEFAULT_RANGE = {"ball": 30.0, "pin": 30.0, "cap": 10.0}


# ---------------------------------------------------------------- units


def _mm() -> float:
    return mm_per_bu(bpy.context.scene)


def _bu(value_mm: float) -> float:
    return value_mm / _mm()


def _to_mm(value_bu: float, digits: int = 3) -> float:
    return round(value_bu * _mm(), digits)


def _point_mm(v) -> list[float]:
    return [_to_mm(c) for c in v]


def _number(value, name: str, minimum: float | None = None, allow_zero=True) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number, got {value!r}") from None
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    if minimum is not None:
        if number < minimum or (not allow_zero and number == minimum):
            relation = ">=" if allow_zero else ">"
            raise ValueError(f"{name} must be {relation} {minimum}, got {number}")
    return number


def _vec_mm(value, name: str) -> Vector:
    """A point given in mm, returned in Blender units."""
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{name} must be a list of three numbers (mm)")
    return Vector([_bu(_number(c, name)) for c in value])


def _direction(value, name: str) -> Vector:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{name} must be a list of three numbers")
    v = Vector([_number(c, name) for c in value])
    if v.length < 1e-9:
        raise ValueError(f"{name} must not be the zero vector")
    return v.normalized()


# ---------------------------------------------------------------- objects


def _part(name: str):
    obj = _get_object(str(name))
    if obj.type != "MESH":
        raise ValueError(f"Object is not a mesh: {obj.name}")
    if obj.modifiers:
        raise ValueError(
            f"{obj.name} has modifiers ({', '.join(m.name for m in obj.modifiers)}); "
            "apply them first (prepare_mesh applies them)"
        )
    if not obj.data.polygons:
        raise ValueError(f"{obj.name} has no faces")
    return obj


def _world_bm(obj) -> bmesh.types.BMesh:
    bpy.context.view_layer.update()
    bm = _evaluated_world_mesh(obj)
    if obj.matrix_world.determinant() < 0:
        bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
    bm.normal_update()
    return bm


def _world_tree(obj) -> BVHTree:
    bm = _world_bm(obj)
    try:
        return BVHTree.FromBMesh(bm)
    finally:
        bm.free()


def _new_object_like(source, name: str, mesh):
    obj = bpy.data.objects.new(name, mesh)
    for collection in source.users_collection or [bpy.context.scene.collection]:
        collection.objects.link(obj)
    obj.matrix_world = source.matrix_world.copy()
    return obj


def _apply_modifier(obj, mod) -> None:
    """Replace obj's mesh by its evaluated mesh, then drop the modifier."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    new = bpy.data.meshes.new_from_object(
        obj.evaluated_get(depsgraph), preserve_all_data_layers=True, depsgraph=depsgraph
    )
    old = obj.data
    obj.modifiers.remove(mod)
    name = old.name
    obj.data = new
    if old.users == 0:
        bpy.data.meshes.remove(old)
    new.name = name


def _mesh_ok(mesh) -> bool:
    """Watertight and manifold (edges with two faces, manifold vertices)."""
    bm = bmesh.new()
    bm.from_mesh(mesh)
    try:
        return all(len(e.link_faces) == 2 for e in bm.edges) and all(v.is_manifold for v in bm.verts)
    finally:
        bm.free()


def _boolean(target, operation: str, cutters: list) -> float:
    """Apply EXACT booleans between target and world-space bmeshes, one
    cutter at a time. When the result breaks a watertight target, it is
    redone with the MANIFOLD solver (also exact on watertight input).
    Returns the seconds spent. The cutter bmeshes are not freed.
    """
    start = time.perf_counter()
    was_ok = _mesh_ok(target.data)
    if not was_ok:
        for cutter in cutters:
            _boolean_once(target, operation, [cutter])
        return time.perf_counter() - start
    backup = target.data.copy()
    # One cutter per boolean: an EXACT boolean with a collection operand
    # also intersects the cutters with each other and is ~30x slower on a
    # 400k-face part.
    attempts = [("EXACT", [[c] for c in cutters]), ("MANIFOLD", [[c] for c in cutters])]
    try:
        for n, (solver, groups) in enumerate(attempts):
            if n:
                _swap_mesh(target, backup.copy())
            for group in groups:
                _boolean_once(target, operation, group, solver)
            if _mesh_ok(target.data):
                break
    finally:
        if backup.users == 0:
            bpy.data.meshes.remove(backup)
    return time.perf_counter() - start


def _swap_mesh(obj, mesh) -> None:
    old = obj.data
    name = old.name
    obj.data = mesh
    if old.users == 0:
        bpy.data.meshes.remove(old)
    mesh.name = name


def _boolean_once(target, operation: str, cutters: list, solver: str = _SOLVER) -> None:
    collection = bpy.data.collections.new("BC_tmp_cutters")
    bpy.context.scene.collection.children.link(collection)
    temp = []
    try:
        for i, bm in enumerate(cutters):
            mesh = bpy.data.meshes.new(f"BC_tmp_cutter_{i}")
            bm.to_mesh(mesh)
            obj = bpy.data.objects.new(mesh.name, mesh)
            collection.objects.link(obj)
            temp.append(obj)
        mod = target.modifiers.new("BC_boolean", "BOOLEAN")
        mod.operation = operation
        mod.solver = solver
        if len(temp) == 1:
            mod.operand_type = "OBJECT"
            mod.object = temp[0]
        else:
            mod.operand_type = "COLLECTION"
            mod.collection = collection
        _apply_modifier(target, mod)
        _close_cracks(target)
    finally:
        for obj in temp:
            mesh = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.meshes.remove(mesh)
        bpy.data.collections.remove(collection)


def _close_cracks(obj) -> None:
    """EXACT booleans sometimes leave seams open by up to ~0.01 mm; weld the
    vertices of the open edges."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    try:
        open_verts = list({v for e in bm.edges if e.is_boundary for v in e.verts})
        if open_verts:
            # Only the seam vertices: the rest of the surface stays untouched.
            bmesh.ops.remove_doubles(bm, verts=open_verts, dist=_bu(0.02))
            wire = [e for e in bm.edges if not e.link_faces]
            if wire:
                bmesh.ops.delete(bm, geom=wire, context="EDGES")
            loose = [v for v in bm.verts if not v.link_faces]
            if loose:
                bmesh.ops.delete(bm, geom=loose, context="VERTS")
            bm.to_mesh(obj.data)
            obj.data.update()
    finally:
        bm.free()


def _bm_boolean(bm, operation: str, cutters: list) -> bmesh.types.BMesh:
    """Boolean between small world-space bmeshes; returns a new bmesh."""
    mesh = bpy.data.meshes.new("BC_tmp_operand")
    bm.to_mesh(mesh)
    obj = bpy.data.objects.new(mesh.name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    try:
        _boolean(obj, operation, cutters)
        out = bmesh.new()
        out.from_mesh(obj.data)
        return out
    finally:
        mesh = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.meshes.remove(mesh)


class _Snapshot:
    """Mesh copies of parts, to roll a failed operation back."""

    def __init__(self, objs):
        self.saved = [(o, o.data.copy()) for o in objs]
        self.created: list = []

    def restore(self) -> None:
        for obj in self.created:
            if obj.name in bpy.data.objects:
                data = obj.data
                bpy.data.objects.remove(obj, do_unlink=True)
                if data is not None and getattr(data, "users", 1) == 0:
                    bpy.data.meshes.remove(data)
        for obj, mesh in self.saved:
            old = obj.data
            obj.data = mesh
            if old.users == 0:
                bpy.data.meshes.remove(old)
        self.saved = []

    def discard(self) -> None:
        for _, mesh in self.saved:
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        self.saved = []


# ---------------------------------------------------------------- primitives
# All in Blender units, world space. outer=True circumscribes the polygonal
# approximation (every face at or beyond the nominal radius): used for
# holes and sockets, while male parts are inscribed. Gaps are therefore
# never smaller than designed.


def _sphere_bm(center, radius: float, outer: bool = False) -> bmesh.types.BMesh:
    if outer:
        radius /= math.cos(math.sqrt(2.0) * math.pi / _SEGMENTS)
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(
        bm,
        u_segments=_SEGMENTS,
        v_segments=_SEGMENTS // 2,
        radius=radius,
        matrix=Matrix.Translation(center),
    )
    return bm


def _axis_matrix(direction: Vector) -> Matrix:
    return Vector((0.0, 0.0, 1.0)).rotation_difference(direction).to_matrix().to_4x4()


def _cylinder_bm(p0, p1, radius: float, outer: bool = False) -> bmesh.types.BMesh:
    axis = Vector(p1) - Vector(p0)
    if axis.length < 1e-9:
        raise ValueError("cylinder start and end must differ")
    if outer:
        radius /= math.cos(math.pi / _SEGMENTS)
    bm = bmesh.new()
    matrix = Matrix.Translation((Vector(p0) + Vector(p1)) / 2) @ _axis_matrix(axis.normalized())
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        cap_tris=False,
        segments=_SEGMENTS,
        radius1=radius,
        radius2=radius,
        depth=axis.length,
        matrix=matrix,
    )
    return bm


def _box_bm(center, size, rotation: Matrix | None = None) -> bmesh.types.BMesh:
    bm = bmesh.new()
    rot = (rotation or Matrix.Identity(3)).to_4x4()
    matrix = Matrix.Translation(center) @ rot @ Matrix.Diagonal((*size, 1.0))
    bmesh.ops.create_cube(bm, size=1.0, matrix=matrix)
    return bm


def _octagon(width: float, thickness: float, chamfer: float) -> list[tuple[float, float]]:
    """Rectangle width x thickness with 45 deg chamfers, counter-clockwise."""
    w, t, c = width / 2, thickness / 2, chamfer
    pts = [(w, -t + c), (w, t - c), (w - c, t), (-w + c, t), (-w, t - c), (-w, -t + c), (-w + c, -t), (w - c, -t)]
    out = []
    for p in pts:
        if not out or (abs(out[-1][0] - p[0]) > 1e-9 or abs(out[-1][1] - p[1]) > 1e-9):
            out.append(p)
    return out


def _prism_bm(p0, p1, width_axis: Vector, width: float, thickness: float, chamfer: float):
    """Octagonal prism from p0 to p1; width along width_axis."""
    d = (Vector(p1) - Vector(p0)).normalized()
    u = Vector(width_axis).normalized()
    v = d.cross(u).normalized()
    bm = bmesh.new()
    rings = []
    for base in (Vector(p0), Vector(p1)):
        rings.append([bm.verts.new(base + u * a + v * b) for a, b in _octagon(width, thickness, chamfer)])
    n = len(rings[0])
    bm.faces.new(list(reversed(rings[0])))
    bm.faces.new(rings[1])
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((rings[0][i], rings[0][j], rings[1][j], rings[1][i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return bm


def _revolution_bm(p0, p1, radius: float, chamfer: float, outer: bool = False):
    """Cylinder with 45 deg conical ends (an octagon section turned about
    its axis): what a chamfered leg top sweeps around its pin."""
    if outer:
        radius /= math.cos(math.pi / _SEGMENTS)
    axis = Vector(p1) - Vector(p0)
    length = axis.length
    rot = _axis_matrix(axis.normalized())
    base = Vector(p0)
    if chamfer > 1e-9:
        profile = [(0.0, radius - chamfer), (chamfer, radius), (length - chamfer, radius), (length, radius - chamfer)]
    else:
        profile = [(0.0, radius), (length, radius)]
    bm = bmesh.new()
    rings = []
    for z, r in profile:
        ring = []
        for k in range(_SEGMENTS):
            a = 2 * math.pi * (k + 0.5) / _SEGMENTS  # no vertex on the axis planes
            local = Vector((r * math.cos(a), r * math.sin(a), z))
            ring.append(bm.verts.new(base + rot.to_3x3() @ local))
        rings.append(ring)
    for a_ring, b_ring in zip(rings, rings[1:]):
        for k in range(_SEGMENTS):
            j = (k + 1) % _SEGMENTS
            bm.faces.new((a_ring[k], a_ring[j], b_ring[j], b_ring[k]))
    bm.faces.new(list(reversed(rings[0])))
    bm.faces.new(rings[-1])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return bm


def _pin_bm(p0, p1, radius: float, flat: float, down: Vector = Vector((0.0, 0.0, -1.0))):
    """Pin with a flat underside (cut `flat` deep): prints without a sag."""
    axis = (Vector(p1) - Vector(p0)).normalized()
    down = down - axis * down.dot(axis)
    if down.length < 1e-6:
        return _cylinder_bm(p0, p1, radius)
    down.normalize()
    limit = radius - flat
    pts = []
    for base in (Vector(p0), Vector(p1)):
        for o in _perpendicular_dirs(axis, _SEGMENTS):
            q = o * radius
            depth = q.dot(down)
            if depth > limit:
                q -= down * (depth - limit)
            pts.append(base + q)
    return _hull_bm(pts)


def _teardrop_bm(p0, p1, radius: float, tip: Vector):
    """Hole with a 45 deg roof pointing to `tip`: prints without support."""
    axis = (Vector(p1) - Vector(p0)).normalized()
    radius /= math.cos(math.pi / _SEGMENTS)
    pts = []
    for base in (Vector(p0), Vector(p1)):
        pts.extend(base + o * radius for o in _perpendicular_dirs(axis, _SEGMENTS))
        pts.append(base + tip * radius * math.sqrt(2.0))
    return _hull_bm(pts)


def _hull_bm(points) -> bmesh.types.BMesh:
    """Convex hull of points as a closed bmesh."""
    bm = bmesh.new()
    verts = [bm.verts.new(p) for p in points]
    if len(verts) < 4:
        bm.free()
        raise ValueError("not enough points for a hull")
    result = bmesh.ops.convex_hull(bm, input=verts, use_existing_faces=False)
    junk = {g for g in result["geom_interior"] + result["geom_unused"] if isinstance(g, bmesh.types.BMVert)}
    if junk:
        bmesh.ops.delete(bm, geom=list(junk), context="VERTS")
    loose = [v for v in bm.verts if not v.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return bm


def _hull_points(points) -> list[Vector]:
    bm = _hull_bm(points)
    try:
        return [v.co.copy() for v in bm.verts]
    finally:
        bm.free()


def _ball_offsets(radius: float) -> list[Vector]:
    """Points whose hull contains a ball of the given radius."""
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=2, radius=1.0)
    bm.faces.ensure_lookup_table()
    inner = min(abs(f.calc_center_median().dot(f.normal)) for f in bm.faces if f.normal.length)
    points = [v.co.copy() * (radius / inner) for v in bm.verts]
    bm.free()
    return points


# ---------------------------------------------------------------- cutters


def _parse_cutter(spec, depth: int = 0) -> dict:
    """Validate a cutter given in mm; returns it in BU (internal form)."""
    if not isinstance(spec, dict) or "type" not in spec:
        raise ValueError(
            "cutter must be an object with a type: plane, box, cylinder, sphere or union"
        )
    kind = str(spec["type"]).lower()
    if kind == "plane":
        return {
            "type": "plane",
            "point": _vec_mm(spec.get("point"), "cutter.point"),
            "normal": _direction(spec.get("normal"), "cutter.normal"),
        }
    if kind == "box":
        size = spec.get("size")
        if not isinstance(size, (list, tuple)) or len(size) != 3:
            raise ValueError("box cutter needs size: [x, y, z] in mm")
        rotation = spec.get("rotation_deg") or [0.0, 0.0, 0.0]
        if not isinstance(rotation, (list, tuple)) or len(rotation) != 3:
            raise ValueError("box rotation_deg must be [x, y, z] degrees")
        from mathutils import Euler

        return {
            "type": "box",
            "center": _vec_mm(spec.get("center"), "cutter.center"),
            "size": Vector([_bu(_number(s, "cutter.size", 0.0, allow_zero=False)) for s in size]),
            "rotation": Euler([math.radians(_number(r, "rotation_deg")) for r in rotation]).to_matrix(),
        }
    if kind == "cylinder":
        start = _vec_mm(spec.get("start"), "cutter.start")
        end = _vec_mm(spec.get("end"), "cutter.end")
        if (end - start).length < 1e-9:
            raise ValueError("cylinder cutter start and end must differ")
        return {
            "type": "cylinder",
            "start": start,
            "end": end,
            "radius": _bu(_number(spec.get("radius"), "cutter.radius", 0.0, allow_zero=False)),
        }
    if kind == "sphere":
        return {
            "type": "sphere",
            "center": _vec_mm(spec.get("center"), "cutter.center"),
            "radius": _bu(_number(spec.get("radius"), "cutter.radius", 0.0, allow_zero=False)),
        }
    if kind in ("prism", "revolution"):
        start = _vec_mm(spec.get("start"), "cutter.start")
        end = _vec_mm(spec.get("end"), "cutter.end")
        if (end - start).length < 1e-9:
            raise ValueError(f"{kind} cutter start and end must differ")
        chamfer = _bu(_number(spec.get("chamfer", 0.0), "cutter.chamfer", 0.0))
        if kind == "revolution":
            radius = _bu(_number(spec.get("radius"), "cutter.radius", 0.0, allow_zero=False))
            if chamfer >= radius or 2 * chamfer >= (end - start).length:
                raise ValueError("revolution chamfer must be smaller than the radius and half the length")
            return {"type": kind, "start": start, "end": end, "radius": radius, "chamfer": chamfer}
        width = _bu(_number(spec.get("width"), "cutter.width", 0.0, allow_zero=False))
        thickness = _bu(_number(spec.get("thickness"), "cutter.thickness", 0.0, allow_zero=False))
        if 2 * chamfer >= min(width, thickness):
            raise ValueError("prism chamfer must be under half the width and the thickness")
        d = (end - start).normalized()
        u = _direction(spec.get("width_axis"), "cutter.width_axis")
        u = u - d * u.dot(d)
        if u.length < 1e-6:
            raise ValueError("prism width_axis must not be along start -> end")
        return {"type": kind, "start": start, "end": end, "width": width, "thickness": thickness,
                "chamfer": chamfer, "width_axis": u.normalized()}
    if kind == "union":
        parts = spec.get("parts")
        if depth > 0:
            raise ValueError("a union cutter cannot contain another union")
        if not isinstance(parts, list) or not 1 <= len(parts) <= 8:
            raise ValueError("union cutter needs parts: a list of 1 to 8 cutters")
        return {"type": "union", "parts": [_with_fill(_parse_cutter(p, depth + 1), p) for p in parts]}
    raise ValueError(
        f"Unknown cutter type {kind!r}: use plane, box, cylinder, sphere, prism, revolution or union"
    )


def _with_fill(prim: dict, spec: dict) -> dict:
    if spec.get("fill"):
        prim["fill"] = True
    return prim


def _cutter_prims(cutter: dict) -> list[dict]:
    return cutter["parts"] if cutter["type"] == "union" else [cutter]


def _prim_bm(prim: dict, dilate: float, reach: float, around: Vector) -> bmesh.types.BMesh:
    """Mesh of a primitive grown by ``dilate`` (BU). Planes become a box of
    side ``reach`` around ``around``."""
    kind = prim["type"]
    if kind == "plane":
        n = prim["normal"]
        point = prim["point"] - n * dilate
        foot = around - n * (around - point).dot(n)
        center = foot + n * (reach / 2)
        rotation = _axis_matrix(n).to_3x3()
        return _box_bm(center, (reach, reach, reach), rotation)
    if kind == "box":
        size = prim["size"] + Vector((2 * dilate,) * 3)
        return _box_bm(prim["center"], size, prim["rotation"])
    if kind == "cylinder":
        axis = (prim["end"] - prim["start"]).normalized()
        return _cylinder_bm(
            prim["start"] - axis * dilate,
            prim["end"] + axis * dilate,
            prim["radius"] + dilate,
            outer=dilate > 0,
        )
    if kind == "sphere":
        return _sphere_bm(prim["center"], prim["radius"] + dilate, outer=dilate > 0)
    # Offsetting an octagon by d moves each 45 deg face by d too: the chamfer
    # legs grow by d (2 - sqrt 2).
    grow = dilate * (2.0 - math.sqrt(2.0))
    if kind == "prism":
        d = (prim["end"] - prim["start"]).normalized()
        return _prism_bm(prim["start"] - d * dilate, prim["end"] + d * dilate, prim["width_axis"],
                         prim["width"] + 2 * dilate, prim["thickness"] + 2 * dilate, prim["chamfer"] + grow)
    if kind == "revolution":
        d = (prim["end"] - prim["start"]).normalized()
        return _revolution_bm(prim["start"] - d * dilate, prim["end"] + d * dilate,
                              prim["radius"] + dilate, prim["chamfer"] + grow, outer=dilate > 0)
    raise ValueError(f"unknown primitive {kind}")


def _cutter_to_mm(cutter: dict) -> dict:
    kind = cutter["type"]
    if kind == "union":
        return {"type": "union", "parts": [_cutter_to_mm(p) for p in cutter["parts"]]}
    out = {"type": kind}
    for key, value in cutter.items():
        if key == "type":
            continue
        if key == "rotation":
            out["rotation_deg"] = [round(math.degrees(a), 4) for a in value.to_euler()]
        elif key in ("normal", "width_axis"):
            out[key] = [round(c, 6) for c in value]
        elif key == "fill":
            out[key] = bool(value)
        elif isinstance(value, Vector):
            out[key] = _point_mm(value)
        else:
            out[key] = _to_mm(value)
    return out


def _reach(obj) -> tuple[float, Vector]:
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    lo = Vector([min(c[i] for c in corners) for i in range(3)])
    hi = Vector([max(c[i] for c in corners) for i in range(3)])
    return 4.0 * (hi - lo).length + _bu(10.0), (lo + hi) / 2


# ---------------------------------------------------------------- analysis helpers


def _components(bm) -> list[list]:
    """Vertex lists of the connected components."""
    bm.verts.index_update()
    seen = [False] * len(bm.verts)
    out = []
    for start in bm.verts:
        if seen[start.index]:
            continue
        seen[start.index] = True
        stack, comp = [start], []
        while stack:
            v = stack.pop()
            comp.append(v)
            for e in v.link_edges:
                o = e.other_vert(v)
                if not seen[o.index]:
                    seen[o.index] = True
                    stack.append(o)
        out.append(comp)
    return out


def _component_volumes(bm) -> list[tuple[float, list]]:
    """(volume BU³, faces) per connected component, largest first."""
    out = []
    for comp in _components(bm):
        faces = {f for v in comp for f in v.link_faces}
        volume = 0.0
        for f in faces:
            verts = [v.co for v in f.verts]
            for i in range(1, len(verts) - 1):
                volume += verts[0].dot(verts[i].cross(verts[i + 1])) / 6.0
        out.append((abs(volume), list(faces)))
    out.sort(key=lambda item: -item[0])
    return out


def _drop_slivers(obj, min_fraction: float = 1e-3, max_mm3: float = 1.0) -> list[float]:
    """Remove boolean slivers (components under min_fraction of the part's
    volume and under max_mm3). Returns the dropped volumes in mm³."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    try:
        comps = _component_volumes(bm)
        if len(comps) <= 1:
            return []
        total = sum(v for v, _ in comps)
        mm3 = _mm() ** 3
        dropped, kill = [], set()
        for volume, faces in comps[1:]:
            if volume < total * min_fraction and volume * mm3 < max_mm3:
                dropped.append(round(volume * mm3, 4))
                kill.update(faces)
        if kill:
            verts = {v for f in kill for v in f.verts}
            bmesh.ops.delete(bm, geom=list(verts), context="VERTS")
            bm.to_mesh(obj.data)
            obj.data.update()
        return dropped
    finally:
        bm.free()


def _shell_report(obj) -> dict:
    topo = mesh_topology(obj.name)
    return {
        "components": topo["components"],
        "manifold": topo["manifold"],
        "faces": topo["faces"],
        "volume_mm3": round(topo["volume_mm3"], 2),
    }


def _ray_hits(tree: BVHTree, origin: Vector, direction: Vector, limit: float = 1e6) -> list[float]:
    """Distances of every surface crossing along a ray."""
    hits, travelled, eps = [], 0.0, _bu(1e-4)
    o = origin.copy()
    for _ in range(64):
        loc, _n, _i, dist = tree.ray_cast(o, direction, limit - travelled)
        if loc is None:
            break
        travelled += dist + eps
        hits.append(travelled - eps)
        o = loc + direction * eps
    return hits


def _inside(tree: BVHTree, point: Vector) -> bool:
    """Point-in-mesh by ray parity, voting over three directions."""
    votes = 0
    for d in (Vector((0.31, 0.52, 0.79)), Vector((-0.6, 0.71, -0.36)), Vector((0.12, -0.83, 0.54))):
        votes += len(_ray_hits(tree, point, d.normalized())) % 2
    return votes >= 2


def _perpendicular_dirs(axis: Vector, count: int = 24) -> list[Vector]:
    base = axis.orthogonal().normalized()
    other = axis.cross(base).normalized()
    return [
        (base * math.cos(2 * math.pi * k / count) + other * math.sin(2 * math.pi * k / count))
        for k in range(count)
    ]


def _sphere_dirs(count: int = 200) -> list[Vector]:
    golden = math.pi * (3 - math.sqrt(5))
    out = []
    for k in range(count):
        z = 1 - 2 * (k + 0.5) / count
        r = math.sqrt(max(0.0, 1 - z * z))
        out.append(Vector((r * math.cos(golden * k), r * math.sin(golden * k), z)))
    return out


def _rotate_about(point: Vector, axis: Vector, angle: float):
    rot = Matrix.Rotation(angle, 4, axis)
    return Matrix.Translation(point) @ rot @ Matrix.Translation(-point)


def _parse_range(value, default: float) -> tuple[float, float]:
    """range_deg as (lo, hi) about the axis (right-hand rule), lo <= 0 <= hi.
    A number r means (-r, r)."""
    if value is None:
        value = default
    if isinstance(value, (list, tuple)):
        if len(value) != 2:
            raise ValueError("range_deg must be a number or [min, max] in degrees")
        lo, hi = (_number(v, "range_deg") for v in value)
        if not lo <= 0.0 <= hi:
            raise ValueError("range_deg [min, max] must contain 0 (the current pose)")
    else:
        r = _number(value, "range_deg", 0.0)
        lo, hi = -r, r
    if lo < -180 or hi > 180:
        raise ValueError("range_deg must stay within -180..180")
    return float(lo), float(hi)


def _sweep_angles(rng) -> list[float]:
    lo, hi = rng if isinstance(rng, tuple) else (-float(rng), float(rng))
    if hi - lo <= 0:
        return [0.0]
    steps = max(1, math.ceil((hi - lo) / _SWEEP_STEP_DEG))
    angles = [math.radians(lo + (hi - lo) * k / steps) for k in range(steps + 1)]
    return angles + [0.0]


def _swept_hull(points, pivot: Vector, axis: Vector, range_deg) -> bmesh.types.BMesh:
    base = _hull_points(points)
    swept = []
    for angle in _sweep_angles(range_deg):
        m = _rotate_about(pivot, axis, angle)
        swept.extend(m @ p for p in base)
    hull = _hull_bm(swept)
    # Near-coincident points of neighbouring copies leave sliver faces.
    bmesh.ops.remove_doubles(hull, verts=hull.verts[:], dist=_bu(0.005))
    bmesh.ops.dissolve_degenerate(hull, dist=_bu(0.001), edges=hull.edges[:])
    return hull


# ---------------------------------------------------------------- prepare_mesh


def prepare_mesh(
    name: str,
    target_faces: int | None = None,
    voxel_mm: float | None = None,
    merge_mm: float = 0.001,
    split_shells: bool = False,
    near_gap_mm: float = 0.3,
    keep_original: bool = True,
) -> dict:
    obj = _get_object(name)
    if obj.type != "MESH":
        raise ValueError(f"Object is not a mesh: {obj.name}")
    if target_faces is not None and int(target_faces) < 1000:
        raise ValueError("target_faces must be at least 1000")
    if voxel_mm is not None and not 0.02 <= float(voxel_mm) <= 5.0:
        raise ValueError("voxel_mm must be between 0.02 and 5 mm")
    if not 0.0 <= float(merge_mm) <= 0.1:
        raise ValueError("merge_mm must be between 0 and 0.1 mm")
    timings, steps = {}, []
    t0 = time.perf_counter()
    original = None
    if keep_original:
        original = _keep_original(obj)
    for mod in list(obj.modifiers):
        label = mod.name
        _apply_modifier(obj, mod)
        steps.append(f"applied modifier {label}")
    before = _shell_report(obj)
    before["dimensions_mm"] = _point_mm(obj.dimensions)

    # Cleanup in object space; distances scale with the object's scale.
    scale = max(obj.matrix_world.to_scale()) or 1.0
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    try:
        # Only open seams are touched: vertices of a watertight surface keep
        # their exact positions (merging close vertices elsewhere could even
        # break a good mesh).
        loose = [v for v in bm.verts if not v.link_faces]
        if loose:
            bmesh.ops.delete(bm, geom=loose, context="VERTS")
            steps.append(f"removed {len(loose)} loose vertices")
        wire = [e for e in bm.edges if not e.link_faces]
        if wire:
            bmesh.ops.delete(bm, geom=wire, context="EDGES")
        open_verts = list({v for e in bm.edges if e.is_boundary for v in e.verts})
        if open_verts:
            n_verts = len(bm.verts)
            bmesh.ops.remove_doubles(bm, verts=open_verts, dist=_bu(merge_mm) / scale)
            merged = n_verts - len(bm.verts)
            if merged:
                steps.append(f"merged {merged} duplicate vertices on open seams")
        boundary = [e for e in bm.edges if e.is_boundary]
        if boundary:
            bmesh.ops.holes_fill(bm, edges=boundary, sides=0)
            steps.append(f"filled holes ({len(boundary)} boundary edges)")
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        if bm.calc_volume(signed=True) < 0:
            bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
        bm.to_mesh(obj.data)
        obj.data.update()
        # Overlapping shells break booleans; touching-but-separate shells
        # weld in the slicer. Both are reported.
        comps = _components(bm)
        overlapping, near = [], []
        if 1 < len(comps) <= 64:
            trees = []
            for comp in comps:
                faces = {f for v in comp for f in v.link_faces}
                sub = bmesh.new()
                vmap = {}
                for f in faces:
                    vs = []
                    for v in f.verts:
                        if v not in vmap:
                            vmap[v] = sub.verts.new(obj.matrix_world @ v.co)
                        vs.append(vmap[v])
                    try:
                        sub.faces.new(vs)
                    except ValueError:
                        pass
                trees.append((BVHTree.FromBMesh(sub), sub))
            for i in range(len(trees)):
                for j in range(i + 1, len(trees)):
                    if trees[i][0].overlap(trees[j][0]):
                        overlapping.append([i, j])
                        continue
                    gap, _ = _min_gap(trees[i][1], trees[j][1])
                    if gap is not None and gap * _mm() < near_gap_mm:
                        near.append({"shells": [i, j], "gap_mm": round(gap * _mm(), 3)})
            for _, sub in trees:
                sub.free()
    finally:
        bm.free()
    timings["cleanup_s"] = round(time.perf_counter() - t0, 2)

    dropped = _drop_islands(obj, 1.0)
    if dropped:
        steps.append(f"removed {len(dropped)} floating island(s) under 1 mm³")
    topo = mesh_topology(obj.name)
    warnings = []
    if not topo["manifold"]:
        warnings.append(
            "still not watertight after the local repair: a global remesh would change "
            "the sculpt; repair only the bad area (or pass voxel_mm knowingly)"
        )
    if overlapping:
        warnings.append(
            "overlapping shells: split_shells and join them with boolean_parts union "
            "(exact, keeps the surface) before cutting"
        )
    if voxel_mm is not None:
        t = time.perf_counter()
        warnings.append("voxel remesh rebuilt the whole surface (not the original sculpt any more)")
        mod = obj.modifiers.new("BC_remesh", "REMESH")
        mod.mode = "VOXEL"
        mod.voxel_size = _bu(float(voxel_mm)) / scale
        mod.use_smooth_shade = False
        _apply_modifier(obj, mod)
        steps.append(f"voxel remesh at {float(voxel_mm):.3f} mm (requested)")
        timings["remesh_s"] = round(time.perf_counter() - t, 2)
    if target_faces is not None and len(obj.data.polygons) > int(target_faces):
        t = time.perf_counter()
        mod = obj.modifiers.new("BC_decimate", "DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = int(target_faces) / len(obj.data.polygons)
        mod.use_collapse_triangulate = True
        _apply_modifier(obj, mod)
        steps.append(f"decimated to {len(obj.data.polygons)} faces")
        warnings.append("decimation changed the surface: use it only for previews")
        timings["decimate_s"] = round(time.perf_counter() - t, 2)

    shells = []
    if split_shells:
        shells = _split_shells(obj)
        if len(shells) > 1:
            steps.append(f"split into {len(shells)} objects, one per shell")
    after = _shell_report(obj)
    after["dimensions_mm"] = _point_mm(obj.dimensions)
    timings["total_s"] = round(time.perf_counter() - t0, 2)
    result = {
        "name": obj.name,
        "before": before,
        "after": after,
        "steps": steps or ["nothing to fix"],
        "overlapping_shells": overlapping,
        "near_shells": near,
        "original": original,
        "timings": timings,
    }
    if warnings:
        result["warnings"] = warnings
    if shells:
        result["shells"] = shells
    if near:
        result["note"] = (
            f"Separate shells closer than {near_gap_mm} mm weld in the slicer: give "
            "them a real clearance (fit_clearance or add_joint) or join them."
        )
    return result


def _keep_original(obj) -> str:
    """Hidden untouched copy '<name> (STL original)' for comparison."""
    name = f"{obj.name} (STL original)"
    existing = bpy.data.objects.get(name)
    if existing is not None:
        return existing.name
    copy = _new_object_like(obj, name, obj.data.copy())
    copy.hide_render = True
    copy.hide_set(True)
    copy["bc_original_of"] = obj.name
    return copy.name


def _drop_islands(obj, max_mm3: float) -> list[float]:
    """Delete separate shells smaller than max_mm3 (debris)."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    try:
        comps = _component_volumes(bm)
        if len(comps) <= 1:
            return []
        mm3 = (_mm() * (max(obj.matrix_world.to_scale()) or 1.0)) ** 3
        kill, dropped = set(), []
        for volume, faces in comps[1:]:
            if volume * mm3 < max_mm3:
                dropped.append(round(volume * mm3, 4))
                kill.update(faces)
        if kill:
            verts = {v for f in kill for v in f.verts}
            bmesh.ops.delete(bm, geom=list(verts), context="VERTS")
            bm.to_mesh(obj.data)
            obj.data.update()
        return dropped
    finally:
        bm.free()


def _split_shells(obj) -> list[dict]:
    """Split obj into one object per shell; the largest keeps the name."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    try:
        comps = _component_volumes(bm)
        if len(comps) <= 1:
            return [{"name": obj.name, "volume_mm3": round(comps[0][0] * _mm() ** 3, 2) if comps else 0}]
        out = []
        for index, (volume, faces) in enumerate(comps):
            keep = set(faces)
            sub = bm.copy()
            sub.faces.ensure_lookup_table()
            bm.faces.index_update()
            keep_idx = {f.index for f in keep}
            dead = [f for f in sub.faces if f.index not in keep_idx]
            bmesh.ops.delete(sub, geom=dead, context="FACES")
            loose = [v for v in sub.verts if not v.link_faces]
            if loose:
                bmesh.ops.delete(sub, geom=loose, context="VERTS")
            if index == 0:
                target = obj
                sub.to_mesh(obj.data)
                obj.data.update()
            else:
                mesh = obj.data.copy()
                sub.to_mesh(mesh)
                target = _new_object_like(obj, f"{obj.name}.shell_{index:02d}", mesh)
            sub.free()
            out.append(
                {
                    "name": target.name,
                    "volume_mm3": round(volume * _mm() ** 3, 2),
                    "dimensions_mm": _point_mm(target.dimensions),
                }
            )
        bpy.context.view_layer.update()
        return out
    finally:
        bm.free()


# ---------------------------------------------------------------- analyze_figure


def _occupancy(tree: BVHTree, lo: Vector, hi: Vector, cell: float):
    """Inside/outside grid from vertical rays (parity). occ[i][j] is a sorted
    list of (z_in, z_out) intervals for column (i, j)."""
    nx = max(1, int(math.ceil((hi.x - lo.x) / cell)))
    ny = max(1, int(math.ceil((hi.y - lo.y) / cell)))
    up = Vector((0.0, 0.0, 1.0))
    columns = []
    for i in range(nx):
        row = []
        for j in range(ny):
            origin = Vector((lo.x + (i + 0.5) * cell, lo.y + (j + 0.5) * cell, lo.z - 1.0))
            hits = _ray_hits(tree, origin, up)
            intervals = [
                (origin.z + hits[k], origin.z + hits[k + 1]) for k in range(0, len(hits) - 1, 2)
            ]
            row.append(intervals)
        columns.append(row)
    return columns, nx, ny


def _slice_mask(columns, nx, ny, z0: float, z1: float) -> set:
    """Cells occupied anywhere between z0 and z1."""
    mask = set()
    for i in range(nx):
        for j in range(ny):
            for a, b in columns[i][j]:
                if a < z1 and b > z0:
                    mask.add((i, j))
                    break
    return mask


def _label(mask: set) -> list[set]:
    out, seen = [], set()
    for cell in mask:
        if cell in seen:
            continue
        seen.add(cell)
        stack, comp = [cell], set()
        while stack:
            c = stack.pop()
            comp.add(c)
            i, j = c
            for n in ((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)):
                if n in mask and n not in seen:
                    seen.add(n)
                    stack.append(n)
        out.append(comp)
    out.sort(key=len, reverse=True)
    return out


def analyze_figure(name: str, others: list[str] | None = None, cell_mm: float | None = None) -> dict:
    """Heuristic layout of a standing figure (Z up, floor at its lowest Z)."""
    t0 = time.perf_counter()
    objs = [_get_object(name)] + [_get_object(n) for n in (others or [])]
    for o in objs:
        if o.type != "MESH":
            raise ValueError(f"Object is not a mesh: {o.name}")
    bms = [_world_bm(o) for o in objs]
    try:
        trees = [BVHTree.FromBMesh(b) for b in bms]
        all_co = [v.co for b in bms for v in b.verts]
        lo = Vector([min(c[i] for c in all_co) for i in range(3)])
        hi = Vector([max(c[i] for c in all_co) for i in range(3)])
        dims = hi - lo
        height = dims.z
        length_axis = 0 if dims.x >= dims.y else 1
        width_axis = 1 - length_axis
        cell = _bu(cell_mm) if cell_mm else max(dims.x, dims.y) / 90.0
        merged = bmesh.new()
        for b in bms:
            tmp = bpy.data.meshes.new("BC_tmp_analyze")
            b.to_mesh(tmp)
            merged.from_mesh(tmp)
            bpy.data.meshes.remove(tmp)
        tree_all = BVHTree.FromBMesh(merged)
        columns, nx, ny = _occupancy(tree_all, lo, hi, cell)
        merged.free()

        def cell_center(c):
            return Vector((lo.x + (c[0] + 0.5) * cell, lo.y + (c[1] + 0.5) * cell))

        def owner(point3):
            best = None
            for o, tree in zip(objs, trees):
                hit = tree.find_nearest(point3)
                if hit[0] is not None and (best is None or hit[3] < best[1]):
                    best = (o.name, hit[3])
            return best[0] if best else name

        # Top profile along the length axis -> head end.
        nl = nx if length_axis == 0 else ny
        top = [lo.z] * nl
        for i in range(nx):
            for j in range(ny):
                if columns[i][j]:
                    k = i if length_axis == 0 else j
                    top[k] = max(top[k], columns[i][j][-1][1])
        k_top = max(range(nl), key=lambda k: top[k])
        center_l = (nl - 1) / 2
        head_sign = 1 if k_top > center_l else -1
        length_name = "XY"[length_axis]
        width_name = "XY"[width_axis]

        # Feet: islands in a thin slab above the floor.
        foot_h = max(2.5 * cell, 0.05 * height)
        feet_mask = _slice_mask(columns, nx, ny, lo.z, lo.z + foot_h)
        footprint = len(_slice_mask(columns, nx, ny, lo.z, hi.z))
        islands = [c for c in _label(feet_mask) if len(c) >= max(3, 0.005 * footprint)]

        # Leg columns: follow each island upward until it merges.
        nz = int(math.ceil(height / cell))
        levels = []
        for k in range(nz):
            z0 = lo.z + k * cell
            levels.append(_label(_slice_mask(columns, nx, ny, z0, z0 + cell)))
        legs = []
        for index, island in enumerate(islands):
            prev = island
            min_area = len(island)
            merge_z = hi.z
            widths = None
            for k in range(nz):
                comp = next((c for c in levels[k] if c & prev), None)
                if comp is None:
                    break
                others_hit = any(comp & other for o, other in enumerate(islands) if o != index)
                if k > 0 and (others_hit or len(comp) > 2.2 * max(min_area, 1)):
                    merge_z = lo.z + k * cell
                    break
                min_area = min(min_area, len(comp))
                prev = comp
                widths = comp
            if widths is None:
                widths = island
            ci = [c[0] for c in widths]
            cj = [c[1] for c in widths]
            cx = lo.x + (sum(ci) / len(ci) + 0.5) * cell
            cy = lo.y + (sum(cj) / len(cj) + 0.5) * cell
            ext_x = (max(ci) - min(ci) + 1) * cell
            ext_y = (max(cj) - min(cj) + 1) * cell
            fi = [c[0] for c in island]
            fj = [c[1] for c in island]
            foot_x = (max(fi) - min(fi) + 1) * cell
            foot_y = (max(fj) - min(fj) + 1) * cell
            center = Vector((cx, cy, lo.z + 0.5 * (merge_z - lo.z)))
            l_val = center[length_axis]
            w_val = center[width_axis]
            mid_l = (lo[length_axis] + hi[length_axis]) / 2
            mid_w = (lo[width_axis] + hi[width_axis]) / 2
            front = (l_val - mid_l) * head_sign > 0
            side = "left" if (w_val - mid_w) < 0 else "right"
            legs.append(
                {
                    "body_run": _body_run(columns, lo, cell, nx, ny, cx, cy, width_axis),
                    "name": f"leg_{'front' if front else 'rear'}_{side}",
                    "owner": owner(Vector((cx, cy, lo.z + 0.5 * foot_h))),
                    "center": Vector((cx, cy)),
                    "merge_z": merge_z,
                    "column_ext": (ext_x, ext_y),
                    "foot_ext": (foot_x, foot_y),
                    "column_top": _column_top(columns, lo, cell, cx, cy, merge_z),
                }
            )
        # Body sections along the length axis above the leg tops.
        belly = min((leg["merge_z"] for leg in legs), default=lo.z)
        areas = []
        for k in range(nl):
            count = 0
            for i in range(nx):
                for j in range(ny):
                    if (i if length_axis == 0 else j) != k:
                        continue
                    for a, b in columns[i][j]:
                        count += max(0.0, b - max(a, belly))
            areas.append(count * cell)  # BU²
        timings = round(time.perf_counter() - t0, 2)
        return _figure_report(
            name, objs, lo, hi, cell, legs, areas, top, k_top, head_sign,
            length_axis, width_axis, belly, timings,
        )
    finally:
        for b in bms:
            b.free()


def _body_run(columns, lo, cell, nx, ny, x, y, axis):
    """f(z) -> (lo, hi) of the occupied run through (x, y) along the width
    axis at height z."""

    def occupied(i, j, z):
        return any(a <= z <= b for a, b in columns[i][j])

    def run(z):
        i = max(0, min(int((x - lo.x) / cell), nx - 1))
        j = max(0, min(int((y - lo.y) / cell), ny - 1))
        if not occupied(i, j, z):
            return ((x, y)[axis], (x, y)[axis])
        step = (1, 0) if axis == 0 else (0, 1)
        a = b = (i, j)
        while 0 <= a[0] - step[0] and 0 <= a[1] - step[1] and occupied(a[0] - step[0], a[1] - step[1], z):
            a = (a[0] - step[0], a[1] - step[1])
        while b[0] + step[0] < nx and b[1] + step[1] < ny and occupied(b[0] + step[0], b[1] + step[1], z):
            b = (b[0] + step[0], b[1] + step[1])
        origin = lo.x if axis == 0 else lo.y
        return (origin + a[axis] * cell, origin + (b[axis] + 1) * cell)

    return run


def _column_top(columns, lo, cell, x, y, z_from) -> float:
    i = int((x - lo.x) / cell)
    j = int((y - lo.y) / cell)
    i = max(0, min(i, len(columns) - 1))
    j = max(0, min(j, len(columns[0]) - 1))
    top = z_from
    for a, b in columns[i][j]:
        if b > z_from and a <= top + cell:
            top = max(top, b)
    return top


def _figure_report(name, objs, lo, hi, cell, legs, areas, top, k_top, head_sign,
                   length_axis, width_axis, belly, timings) -> dict:
    L = "XY"[length_axis]
    W = "XY"[width_axis]
    axis_l = Vector((1, 0, 0)) if length_axis == 0 else Vector((0, 1, 0))
    axis_w = Vector((0, 1, 0)) if length_axis == 0 else Vector((1, 0, 0))
    head_dir = axis_l * head_sign
    nl = len(areas)

    def l_coord(k):
        return lo[length_axis] + (k + 0.5) * cell

    clearance = _bu(0.30)
    suggestions = []
    leg_out = []
    for leg in legs:
        cx, cy = leg["center"]
        ext_l = leg["column_ext"][length_axis]
        ext_w = leg["column_ext"][width_axis]
        foot_l = leg["foot_ext"][length_axis]
        foot_w = leg["foot_ext"][width_axis]
        z_cut = leg["merge_z"] - 0.5 * cell
        space = leg["column_top"] - z_cut
        box_size = [0.0, 0.0, 0.0]
        box_size[length_axis] = max(ext_l, foot_l) + _bu(2.0)
        box_size[width_axis] = max(ext_w, foot_w) + _bu(2.0)
        box_size[2] = z_cut - (lo.z - _bu(1.0))
        box_center = Vector((cx, cy, (z_cut + lo.z - _bu(1.0)) / 2))
        # Pin hinge: a tab of the leg goes up into the body; the pin crosses
        # it along the width axis and anchors in body walls on both sides.
        pin_d = min(_bu(4.0), max(_bu(2.0), 0.3 * ext_l))
        tab_l = min(ext_l, max(0.6 * ext_l, pin_d + 2 * clearance + _bu(1.8)))
        tab_h = min(space - _bu(_MIN_WALL_MM + 1.3), max(pin_d + 2 * clearance + _bu(2.4), 0.6 * ext_l))
        z_pin = z_cut + tab_h / 2
        w_center = (cx, cy)[width_axis]
        body_lo, body_hi = leg["body_run"](z_pin)
        inset = _bu(1.0) + clearance
        side_wall = _bu(2.0) + clearance
        tab_lo = max(w_center - ext_w / 2 + inset, body_lo + side_wall)
        tab_hi = min(w_center + ext_w / 2 - inset, body_hi - side_wall)
        tab_w = tab_hi - tab_lo
        confidence = 0.8 if leg["merge_z"] < hi.z - cell else 0.3
        entry = {
            "name": leg["name"],
            "part": leg["owner"],
            "existing_part": leg["owner"] != name,
            "foot_center_mm": [_to_mm(cx), _to_mm(cy)],
            "leg_top_z_mm": _to_mm(leg["merge_z"]),
            "column_size_mm": {L: _to_mm(ext_l), W: _to_mm(ext_w)},
            "foot_size_mm": {L: _to_mm(foot_l), W: _to_mm(foot_w)},
            "body_above_mm": _to_mm(space),
            "confidence": confidence,
        }
        if leg["owner"] == name:
            tab_center = Vector((cx, cy, z_cut + tab_h / 2 - _bu(0.25)))
            tab_center[width_axis] = (tab_lo + tab_hi) / 2
            tab_size = [0.0, 0.0, 0.0]
            tab_size[length_axis] = tab_l
            tab_size[width_axis] = max(tab_w, _bu(0.1))
            tab_size[2] = tab_h + _bu(0.5)
            pivot_pin = tab_center.copy()
            pivot_pin.z = z_pin
            r0 = min(_bu(4.0), max(_bu(1.5), 0.25 * min(ext_l, ext_w)))
            pivot_ball = Vector((cx, cy, z_cut + clearance + r0 + clearance + _bu(_MIN_WALL_MM) + clearance + _bu(0.2)))
            entry["pin"] = {
                "cutter": {
                    "type": "union",
                    "parts": [
                        {"type": "box", "center": _point_mm(box_center), "size": [_to_mm(s) for s in box_size]},
                        {"type": "box", "center": _point_mm(tab_center), "size": [_to_mm(s) for s in tab_size]},
                    ],
                },
                "pivot": _point_mm(pivot_pin),
                "axis": list(axis_w),
                "size_mm": _to_mm(pin_d, 2),
                "fits": tab_w >= _bu(1.5) and tab_h > pin_d + 2 * clearance + _bu(2.0),
            }
            entry["ball"] = {
                "cutter": {"type": "box", "center": _point_mm(box_center), "size": [_to_mm(s) for s in box_size]},
                "pivot": _point_mm(pivot_ball),
                "axis": list(axis_w),
                "size_mm": _to_mm(2 * r0, 2),
                "fits": space > (pivot_ball.z - z_cut) + r0 + _bu(_MIN_WALL_MM + 0.3),
            }
        leg_out.append(entry)

    # Neck: narrowest body section between the middle and the head top.
    center_k = (nl - 1) / 2
    lo_k, hi_k = sorted((int(round(center_k)), k_top))
    neck = None
    if hi_k - lo_k >= 2:
        span = [k for k in range(lo_k, hi_k + 1) if areas[k] > 0]
        if span:
            k_neck = min(span, key=lambda k: areas[k])
            ref = max(areas)
            ratio = areas[k_neck] / ref if ref else 1.0
            point = [0.0, 0.0, 0.0]
            point[length_axis] = l_coord(k_neck)
            point[width_axis] = (lo[width_axis] + hi[width_axis]) / 2
            point[2] = (belly + top[k_neck]) / 2
            neck = {
                "cut": {"type": "plane", "point": _point_mm(Vector(point)), "normal": list(head_dir)},
                "section_ratio": round(ratio, 3),
                "confidence": round(max(0.1, min(0.9, 1.2 - ratio)), 2),
                "note": "plane cut: if it also crosses legs or ears, use a box cutter around the head",
            }
    # Tail: thin sections at the rear end.
    tail = None
    rear = range(nl) if head_sign > 0 else range(nl - 1, -1, -1)
    ref = max(areas) if areas else 0
    run = []
    for k in rear:
        if areas[k] <= 0:
            if run:
                break
            continue
        if areas[k] < 0.12 * ref:
            run.append(k)
        else:
            break
    if run:
        k_end = run[-1]
        point = [0.0, 0.0, 0.0]
        point[length_axis] = l_coord(k_end) + head_sign * 0.5 * cell
        point[width_axis] = (lo[width_axis] + hi[width_axis]) / 2
        point[2] = (belly + top[k_end]) / 2
        tail = {
            "cut": {"type": "plane", "point": _point_mm(Vector(point)), "normal": list(-head_dir)},
            "length_mm": _to_mm(len(run) * cell),
            "confidence": 0.5 if len(run) >= 2 else 0.3,
        }
    # Middle of the body between front and rear legs.
    front = [l for l in legs if (l["center"][length_axis] - (lo[length_axis] + hi[length_axis]) / 2) * head_sign > 0]
    rear_legs = [l for l in legs if l not in front]
    mid = None
    if front and rear_legs:
        f = max((l["center"][length_axis] * -head_sign for l in front)) * -head_sign
        r = max((l["center"][length_axis] * head_sign for l in rear_legs)) * head_sign
        point = [0.0, 0.0, 0.0]
        point[length_axis] = (f + r) / 2
        point[width_axis] = (lo[width_axis] + hi[width_axis]) / 2
        point[2] = belly + 0.5 * (max(top) - belly) * 0.6
        mid = {"cut": {"type": "plane", "point": _point_mm(Vector(point)), "normal": list(-head_dir)}}
    # Left/right mirror check of the leg columns.
    mirrors = []
    by_name = {l["name"]: l for l in leg_out}
    for end in ("front", "rear"):
        a, b = by_name.get(f"leg_{end}_left"), by_name.get(f"leg_{end}_right")
        if a and b:
            diff = max(abs(a["column_size_mm"][k] - b["column_size_mm"][k]) for k in (L, W))
            mirrors.append({"pair": end, "size_difference_mm": round(diff, 2), "ok": diff <= max(1.0, 1.5 * _to_mm(cell))})
    return {
        "name": name,
        "objects": [o.name for o in objs],
        "dimensions_mm": _point_mm(hi - lo),
        "floor_z_mm": _to_mm(lo.z),
        "length_axis": L,
        "width_axis": W,
        "head_direction": list(head_dir),
        "belly_z_mm": _to_mm(belly),
        "grid_cell_mm": _to_mm(cell),
        "legs": leg_out,
        "neck": neck,
        "tail": tail,
        "mid_body": mid,
        "mirror_check": mirrors,
        "timings_s": timings,
        "note": (
            "Heuristic: confirm every cut with screenshots before cutting. "
            "Pivots and cutters are in mm, world space."
        ),
    }


# ---------------------------------------------------------------- cut_part


def _fill_child(child, fill_bms: list) -> str | None:
    """Make the child solid inside the fill primitives: the sculpt's own
    grooves and seams there (hidden inside the parent) would leave thin walls
    around a pin hole. The fill is the convex hull of the child's piece in
    those primitives, clipped to them, so it never reaches past the sculpt."""
    region = fill_bms[0] if len(fill_bms) == 1 else _bm_boolean(fill_bms[0], "UNION", fill_bms[1:])
    try:
        whole = _world_bm(child)
        piece = _bm_boolean(whole, "INTERSECT", [region])
        whole.free()
        if len(piece.verts) < 4:
            piece.free()
            return None
        hull = _hull_bm([v.co for v in piece.verts])
        piece.free()
        solid = _bm_boolean(hull, "INTERSECT", [region])
        hull.free()
        _boolean(child, "UNION", [solid])
        solid.free()
        return "child made solid inside the fill primitives"
    finally:
        if region is not fill_bms[0]:
            region.free()


def cut_part(name: str, cutter: dict, new_part_name: str, gap_mm: float = 0.3,
             allow_multiple_shells: bool = False, drop_shells_under_mm3: float = 0.0,
             discard_rest: bool = False) -> dict:
    obj = _part(name)
    new_part_name = str(new_part_name).strip()
    if not new_part_name:
        raise ValueError("new_part_name must not be empty")
    if new_part_name in bpy.data.objects:
        raise ValueError(f"An object named {new_part_name!r} already exists")
    gap_mm = _number(gap_mm, "gap_mm", 0.0)
    if gap_mm > 5.0:
        raise ValueError("gap_mm must be at most 5 mm")
    spec = _parse_cutter(cutter)
    reach, around = _reach(obj)
    gap = _bu(gap_mm)
    snap = _Snapshot([obj])
    t0 = time.perf_counter()
    fill_note = None
    try:
        child = _new_object_like(obj, new_part_name, obj.data.copy())
        snap.created.append(child)
        prims = _cutter_prims(spec)
        inner = [_prim_bm(p, 0.0, reach, around) for p in prims]
        outer = [_prim_bm(p, gap, reach, around) for p in prims]
        try:
            if len(inner) > 1:
                # Intersect with the union of the primitives: union them first.
                union = _bm_boolean(inner[0], "UNION", inner[1:])
                t_child = _boolean(child, "INTERSECT", [union])
                union.free()
            else:
                t_child = _boolean(child, "INTERSECT", inner)
            fill = [b for p, b in zip(prims, inner) if p.get("fill")]
            if fill:
                filled = _fill_child(child, fill)
                if filled:
                    fill_note = filled
            t_parent = _boolean(obj, "DIFFERENCE", outer)
        finally:
            for b in inner + outer:
                b.free()
        if not child.data.polygons:
            raise ValueError("The cutter does not touch the part: the new part would be empty")
        if not obj.data.polygons:
            raise ValueError("The cutter swallows the whole part: nothing would remain")
        dropped = {"child": _drop_slivers(child), "parent": _drop_slivers(obj)}
        limit = _number(drop_shells_under_mm3, "drop_shells_under_mm3", 0.0)
        if limit > 0:
            dropped["child"] += _drop_islands(child, limit)
            dropped["parent"] += _drop_islands(obj, limit)
        report = {"parent": _shell_report(obj), "child": _shell_report(child)}
        checked = {k: v for k, v in report.items() if not (discard_rest and k == "parent")}
        problems = [
            f"{label} has {r['components']} shells" for label, r in checked.items() if r["components"] != 1
        ] + [f"{label} is not watertight" for label, r in checked.items() if not r["manifold"]]
        if problems and not allow_multiple_shells:
            volumes = {}
            for label, o in (("parent", obj), ("child", child)):
                bm = bmesh.new()
                bm.from_mesh(o.data)
                bm.transform(o.matrix_world)
                volumes[label] = [round(v * _mm() ** 3, 2) for v, _ in _component_volumes(bm)][:8]
                bm.free()
            raise ValueError(
                "Cut rejected (rolled back): "
                + "; ".join(problems)
                + f". Shell volumes (mm³): {volumes}. Each part must be one watertight "
                "shell: use a cutter that encloses only the piece you want, drop "
                "small loose pieces with drop_shells_under_mm3, or pass allow_multiple_shells."
            )
        child["bc_cutter"] = json.dumps(_cutter_to_mm(spec))
        child["bc_cut_gap_mm"] = gap_mm
        child["bc_cut_from"] = obj.name
    except BaseException:
        snap.restore()
        raise
    snap.discard()
    parent_name = obj.name
    if discard_rest:
        # Keep only the new part (e.g. a separate leg trimmed to the standard
        # knuckle): the rest of the original is removed.
        mesh = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
        report["parent"] = {"discarded": True}
        parent_name = f"{parent_name} (discarded)"
    return {
        "parent": parent_name,
        "child": child.name,
        "gap_mm": gap_mm,
        "parts": {parent_name: report["parent"], child.name: report["child"]},
        "dropped_slivers_mm3": dropped,
        "filled": fill_note,
        "timings_s": {
            "child_boolean": round(t_child, 2),
            "parent_boolean": round(t_parent, 2),
            "total": round(time.perf_counter() - t0, 2),
        },
    }


# ---------------------------------------------------------------- add_joint


def _make_pivot(child, parent, point: Vector, axis: Vector) -> str:
    """Pivot empty at point whose local Z (or matching world axis) is axis."""
    names = {(1, 0, 0): "X", (0, 1, 0): "Y", (0, 0, 1): "Z"}
    key = tuple(int(round(abs(c))) for c in axis) if max(abs(c) for c in axis) > 1 - 1e-6 else None
    pivot = bpy.data.objects.new(f"BC_Pivo_{child.name}", None)
    pivot.empty_display_type = "ARROWS"
    pivot.empty_display_size = max(max(child.dimensions) * 0.15, 1.0)
    pivot.rotation_mode = "XYZ"
    if key in names:
        axis_name = names[key]
        rotation = Matrix.Identity(3)
    else:
        axis_name = "Z"
        rotation = Vector((0, 0, 1)).rotation_difference(axis).to_matrix()
    for collection in child.users_collection or [bpy.context.scene.collection]:
        collection.objects.link(pivot)
    bpy.context.view_layer.update()
    world = child.matrix_world.copy()
    pivot.parent = parent
    pivot.matrix_world = Matrix.Translation(point) @ rotation.to_4x4()
    bpy.context.view_layer.update()
    pivot["bc_axis"] = axis_name
    pivot["bc_rest"] = list(pivot.rotation_euler)
    pivot["bc_angle"] = 0.0
    child.parent = pivot
    child.matrix_parent_inverse = pivot.matrix_world.inverted()
    child.matrix_world = world
    bpy.context.view_layer.update()
    return pivot.name


def _relief_bm(child, parent, pivot, axis, clearance, range_deg, keep, rho_override=None):
    """Swept clearance around the child near the joint, as one bmesh: the
    child's contact region (or its cutter), grown by the clearance, rotated
    over +-range about the axis. keep (bmesh or None) is spared."""
    child_bm = _world_bm(child)
    try:
        parent_tree = _world_tree(parent)
        near = _bu(2.0) + clearance
        contact = []
        step = max(1, len(child_bm.verts) // 60000)
        for v in child_bm.verts[::step]:
            hit = parent_tree.find_nearest(v.co, near)
            if hit[0] is not None:
                contact.append(v.co.copy())
        if not contact:
            return [], 0.0
        rho0 = max((p - pivot).length for p in contact)
        # Points this deep can rise into the parent while turning.
        turn = max(abs(x) for x in range_deg) if isinstance(range_deg, tuple) else range_deg
        depth = near + rho0 * math.sin(math.radians(min(turn, 90.0)))
        rho = rho0
        for v in child_bm.verts[::step]:
            hit = parent_tree.find_nearest(v.co, depth)
            if hit[0] is not None:
                rho = max(rho, (v.co - pivot).length)
        rho += clearance
        if rho_override:
            rho = rho_override
        if not child.get("bc_cutter"):
            # A part that was already separate has no cutter to describe it:
            # relieve along all of it.
            rho = max(rho, max((v.co - pivot).length for v in child_bm.verts) + clearance)
    finally:
        child_bm.free()
    ball = _sphere_bm(pivot, rho)
    pieces = []
    try:
        recorded = child.get("bc_cutter")
        if recorded:
            spec = _parse_cutter(json.loads(recorded))
            reach, around = _reach(child)
            for prim in _cutter_prims(spec):
                # Exactly the cut's own clearance: faces coplanar with the
                # cut faces are robust, nearly coplanar ones are not.
                # Turn-invariant pieces (the knuckle) are exactly the cut's own
                # clearance: coplanar with the cut faces, which EXACT handles.
                # Swept pieces get 0.05 mm more: their facets would otherwise
                # graze the cut faces, and nearly coincident faces break the
                # booleans that follow.
                invariant = _turn_invariant(prim, pivot, axis)
                grown = _prim_bm(prim, clearance + (0.0 if invariant else _bu(0.05)), reach, around)
                piece = _bm_boolean(grown, "INTERSECT", [ball])
                grown.free()
                pieces.append((piece, invariant))
            offsets = [Vector()]
        else:
            whole = _world_bm(child)
            pieces.append((_bm_boolean(whole, "INTERSECT", [ball]), False))
            whole.free()
            offsets = _ball_offsets(clearance + _bu(0.01))
        reliefs = []
        for piece, invariant in pieces:
            if len(piece.verts) < 4:
                continue
            if invariant:
                # Turning does not move it: no sweep (and no near-duplicate
                # points that make slivers).
                part = piece.copy()
            else:
                base = _hull_points([v.co for v in piece.verts])
                grown = [p + o for p in base for o in offsets]
                # 2 deg past each end: the end poses (one is the print pose)
                # keep the full clearance, not just the sampled hull.
                lo, hi = range_deg if isinstance(range_deg, tuple) else (-range_deg, range_deg)
                part = _swept_hull(grown, pivot, axis, (lo - 2.0 if lo < 0 else lo, hi + 2.0 if hi > 0 else hi))
            if keep is not None:
                spared = _bm_boolean(part, "DIFFERENCE", [keep])
                part.free()
                part = spared
            if len(part.verts) >= 4:
                reliefs.append(part)
            else:
                part.free()
        return reliefs, rho
    finally:
        ball.free()
        for piece, _ in pieces:
            piece.free()


def _turn_invariant(prim: dict, pivot: Vector, axis: Vector) -> bool:
    """A sphere centred on the pivot, or a cylinder on the joint axis."""
    eps = _bu(1e-3)
    if prim["type"] == "sphere":
        return (prim["center"] - pivot).length < eps
    if prim["type"] in ("cylinder", "revolution"):
        d = (prim["end"] - prim["start"]).normalized()
        if abs(abs(d.dot(axis)) - 1.0) > 1e-6:
            return False
        off = prim["start"] - pivot
        return (off - axis * off.dot(axis)).length < eps
    return False


def _walls_from(tree: BVHTree, point: Vector, first_max: float, dirs=None) -> float | None:
    """Thinnest wall around a cavity centred at point: per ray, distance
    between the first and second surface crossing (rays whose first hit is
    beyond first_max go out through a slot and are skipped)."""
    best = None
    for d in dirs or _sphere_dirs(160):
        hits = _ray_hits(tree, point, d)
        if len(hits) < 2 or hits[0] > first_max:
            continue
        wall = hits[1] - hits[0]
        best = wall if best is None else min(best, wall)
    return best


def _moving_set(child) -> list:
    """The child and every mesh that turns with it (its descendants)."""
    return [child] + [o for o in child.children_recursive if o.type == "MESH"]


def _cropped_world(objs, pivot: Vector, radius: float):
    """One bmesh with the faces of objs within radius of pivot (world)."""
    out = bmesh.new()
    for obj in objs:
        bm = _world_bm(obj)
        try:
            crop = _crop(bm, pivot, radius)
        finally:
            bm.free()
        if crop is None:
            continue
        mesh = bpy.data.meshes.new("BC_tmp_crop")
        crop.to_mesh(mesh)
        crop.free()
        out.from_mesh(mesh)
        bpy.data.meshes.remove(mesh)
    out.normal_update()
    return out


def _obstacles(child, parent) -> list:
    moving = {o.name for o in _moving_set(child)}
    out = [parent]
    for o in bpy.context.scene.objects:
        if o.type == "MESH" and o.visible_get() and o.name not in moving and o != parent:
            out.append(o)
    return out


def _sweep_test(parent, child, pivot, axis, start, end, step, radius) -> dict:
    """Turn the child (and what hangs from it) about the axis, without
    touching the scene, against the visible meshes within radius of the
    pivot. Same result shape as joint_test."""
    static = _cropped_world(_obstacles(child, parent), pivot, radius)
    moving = _cropped_world(_moving_set(child), pivot, radius)
    try:
        static_tree = BVHTree.FromBMesh(static)

        def hits(angle_deg):
            posed = moving.copy()
            posed.transform(_rotate_about(pivot, axis, math.radians(angle_deg)))
            tree = BVHTree.FromBMesh(posed)
            posed.free()
            return bool(tree.overlap(static_tree))

        if hits(0.0):
            return {"free_min": 0.0, "free_max": 0.0, "colliding_at_rest": True,
                    "collisions": [{"angle": 0.0}], "tested_within_mm": _to_mm(radius)}
        free, collisions = {}, []
        for key, limit in (("free_max", end), ("free_min", start)):
            free[key] = 0.0
            for angle in joints._sweep_angles(limit, step):
                if hits(angle):
                    collisions.append({"angle": angle})
                    break
                free[key] = angle
        return {"free_min": free["free_min"], "free_max": free["free_max"], "colliding_at_rest": False,
                "collisions": collisions, "tested_within_mm": _to_mm(radius)}
    finally:
        static.free()
        moving.free()


def _pull_test(parent, child, directions: dict, pivot: Vector, radius: float) -> dict:
    """How far the child slides along each direction before touching the
    parent (mm, None = more than 3 mm: not retained that way)."""
    static = _cropped_world([parent], pivot, radius)
    child_bm = _cropped_world([child], pivot, radius)
    out = {}
    try:
        parent_tree = BVHTree.FromBMesh(static)
        for label, d in directions.items():
            out[label] = None
            for dist_mm in (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0):
                moved = child_bm.copy()
                moved.transform(Matrix.Translation(d * _bu(dist_mm)))
                tree = BVHTree.FromBMesh(moved)
                moved.free()
                if tree.overlap(parent_tree):
                    out[label] = dist_mm
                    break
    finally:
        child_bm.free()
        static.free()
    return out


def _gap_check(parent, child, pivot: Vector, radius: float) -> dict:
    a, b = _world_bm(parent), _world_bm(child)
    try:
        if len(a.verts) + len(b.verts) > 200_000:
            gap, approximate = _sampled_gap(a, b, 200_000), True
        else:
            gap, approximate = _min_gap(a, b)
        local = None
        # Exact (with edges) near the joint, where the clearances are.
        crop_a = _crop(a, pivot, radius)
        crop_b = _crop(b, pivot, radius)
        if crop_a and crop_b:
            local, _ = _min_gap(crop_a, crop_b)
        for c in (crop_a, crop_b):
            if c:
                c.free()
    finally:
        a.free()
        b.free()
    return {
        "min_gap_mm": None if gap is None else _to_mm(gap, 4),
        "local_min_gap_mm": None if local is None else _to_mm(local, 4),
        "approximate": approximate,
    }


def _sampled_gap(bm_a, bm_b, budget: int) -> float | None:
    """Overlap test, then vertex-to-surface distances on a vertex sample."""
    tree_a, tree_b = BVHTree.FromBMesh(bm_a), BVHTree.FromBMesh(bm_b)
    if tree_a.overlap(tree_b):
        return 0.0
    total = len(bm_a.verts) + len(bm_b.verts)
    step = max(1, total // budget)
    best = math.inf
    for verts, tree in ((bm_a.verts, tree_b), (bm_b.verts, tree_a)):
        for v in verts[::step]:
            hit = tree.find_nearest(v.co)
            if hit[3] is not None:
                best = min(best, hit[3])
    return None if best == math.inf else best


def _crop(bm, center: Vector, radius: float):
    faces = [f for f in bm.faces if any((v.co - center).length <= radius for v in f.verts)]
    if not faces:
        return None
    out = bmesh.new()
    vmap = {}
    for f in faces:
        vs = []
        for v in f.verts:
            if v not in vmap:
                vmap[v] = out.verts.new(v.co)
            vs.append(vmap[v])
        try:
            out.faces.new(vs)
        except ValueError:
            pass
    out.normal_update()
    return out


def add_joint(
    parent: str,
    child: str,
    joint_type: str,
    pivot,
    axis,
    size_mm: float | None = None,
    clearance_mm: float = 0.30,
    range_deg: float | None = None,
    relief: bool | None = None,
    test: bool = True,
    make_pivot: bool = True,
    neck_mm: float | None = None,
    mouth_mm: float | None = None,
    relief_radius_mm: float | None = None,
    role: str | None = None,
    pin_flat_mm: float | None = None,
    hole_tip=None,
    embed_mm: float | None = None,
    search_mm: float | None = None,
    relief_also=None,
) -> dict:
    joint_type = str(joint_type).lower()
    if joint_type not in _JOINT_TYPES:
        raise ValueError(f"type must be one of {', '.join(_JOINT_TYPES)}")
    parent_obj, child_obj = _part(parent), _part(child)
    if parent_obj == child_obj:
        raise ValueError("parent and child must be different objects")
    if make_pivot and child_obj.parent is not None and "bc_axis" in child_obj.parent:
        raise ValueError(f"{child_obj.name} already has the joint {child_obj.parent.name}")
    p = _vec_mm(pivot, "pivot")
    a = _direction(axis, "axis")
    c_mm = _number(clearance_mm, "clearance_mm", 0.05)
    if c_mm > 1.0:
        raise ValueError("clearance_mm must be at most 1 mm (0.30 print-in-place, 0.20 assembled)")
    c = _bu(c_mm)
    rng = _parse_range(range_deg, _DEFAULT_RANGE[joint_type])
    if size_mm is not None:
        size_mm = _number(size_mm, "size_mm", 0.0, allow_zero=False)
    use_relief = (joint_type != "cap") if relief is None else bool(relief)
    built_rho = None
    rho_set = None
    if relief_radius_mm is not None:
        rho_set = _bu(_number(relief_radius_mm, "relief_radius_mm", 0.0, allow_zero=False))
    wall = _bu(_MIN_WALL_MM)
    t0 = time.perf_counter()
    timings: dict = {}
    built: dict = {}
    warnings: list[str] = []
    also_objs = [_part(n) for n in (relief_also or []) if n not in (parent_obj.name, child_obj.name)]
    snap = _Snapshot([parent_obj, child_obj] + also_objs)
    try:
        child_tree = _world_tree(child_obj)
        p_in_child = _inside(child_tree, p)
        near = child_tree.find_nearest(p)
        d_child = near[3]
        if d_child is None:
            raise ValueError(f"{child_obj.name} has no surface")
        u = Vector(near[0]) - p
        u = u.normalized() if u.length > 1e-9 else Vector((0, 0, -1))
        if p_in_child:
            centroid = sum((child_obj.matrix_world @ Vector(c_) for c_ in child_obj.bound_box), Vector()) / 8
            u = (centroid - p)
            u = (u - a * u.dot(a))
            u = u.normalized() if u.length > 1e-9 else a.orthogonal().normalized()

        if joint_type == "pin":
            if not p_in_child:
                raise ValueError(
                    "pin: the pivot must be inside the child (the pin crosses a "
                    "hole in the child and anchors in the parent on both sides "
                    "of it). Move the pivot into the child's knuckle/tab."
                )
            t_perp = min(
                (h[0] for h in (_ray_hits(child_tree, p, d) for d in _perpendicular_dirs(a)) if h),
                default=0.0,
            )
            if size_mm is None:
                dp = min(_bu(5.0), max(_bu(1.6), 1.5 * (t_perp - c - wall)))
            else:
                dp = _bu(size_mm)
            fit = 2 * (t_perp - c - _bu(0.8))
            if t_perp < dp / 2 + c + _bu(0.8) and fit >= _bu(2.0):
                warnings.append(
                    f"pin reduced from {_to_mm(dp)} to {_to_mm(fit)} mm: the child is only "
                    f"{_to_mm(t_perp)} mm thick around the pivot"
                )
                dp = fit
            embed_target = _bu(max(_PIN_MIN_EMBED_MM, _number(embed_mm, "embed_mm", 0.0)) if embed_mm is not None
                               else _PIN_EMBED_MM)
            flat = _bu(_number(pin_flat_mm, "pin_flat_mm", 0.0)) if pin_flat_mm else 0.0
            if flat >= dp / 2:
                raise ValueError("pin_flat_mm must be under the pin radius")
            tip = None
            if hole_tip is not None:
                tip = _direction(hole_tip, "hole_tip")
                tip = tip - a * tip.dot(a)
                if tip.length < 1e-6:
                    raise ValueError("hole_tip must not be along the axis")
                tip.normalize()
            if t_perp < dp / 2 + c + _bu(0.8):
                raise ValueError(
                    f"pin: the child is only {_to_mm(t_perp)} mm thick around the pivot "
                    f"(needs {_to_mm(dp / 2 + c + _bu(0.8))} mm for a {_to_mm(dp)} mm pin "
                    "with its hole); use a smaller size_mm or move the pivot"
                )
            if use_relief:
                t = time.perf_counter()
                # The walls that anchor the pin, beyond both ends of the
                # child along the axis, are spared by the relief.
                span = _bu(1000.0)
                keep_r = dp / 2 + c + wall
                # Spare the walls that anchor the pin: only beyond the ends of
                # the child's knuckle (its material near the axis), so the
                # child's own corners near the axis still get room to turn.
                cb = _world_bm(child_obj)
                proj = []
                for v in cb.verts:
                    r = v.co - p
                    along_a = r.dot(a)
                    if (r - a * along_a).length <= 2 * keep_r:
                        proj.append(along_a)
                cb.free()
                if not proj:
                    proj = [0.0]
                keeps = [
                    _cylinder_bm(p + a * (max(proj) + c), p + a * span, keep_r),
                    _cylinder_bm(p - a * span, p + a * (min(proj) - c), keep_r),
                ]
                keep = _bm_boolean(keeps[0], "UNION", [keeps[1]])
                for k_bm in keeps:
                    k_bm.free()
                reliefs, rho = _relief_bm(child_obj, parent_obj, p, a, c, rng, keep, rho_set)
                keep.free()
                if reliefs:
                    _boolean(parent_obj, "DIFFERENCE", reliefs)
                    for other in also_objs:
                        _boolean(other, "DIFFERENCE", reliefs)
                        _drop_slivers(other, 5e-4, 20.0)
                    for b in reliefs:
                        b.free()
                    built["relief_radius_mm"] = _to_mm(rho)
                    built_rho = rho
                timings["relief_s"] = round(time.perf_counter() - t, 2)
            parent_tree = _world_tree(parent_obj)
            sides = {}
            for sign in (1, -1):
                d = a * sign
                exit_child = _ray_hits(child_tree, p, d)
                t_out = exit_child[0] if exit_child else _bu(1.0)
                # The child may reach further along the rim of the hole.
                for o in _perpendicular_dirs(a, 12):
                    start = p + o * (dp / 2 + c)
                    rim = _ray_hits(child_tree, start, d)
                    if rim and len(rim) % 2 == 1:
                        t_out = max(t_out, rim[0])
                hits = _ray_hits(parent_tree, p, d)
                wall_len = (hits[1] - hits[0]) if len(hits) >= 2 else 0.0
                if hits and wall_len >= _bu(_PIN_MIN_EMBED_MM + _PIN_SKIN_MM):
                    embed = min(embed_target, wall_len - _bu(_PIN_SKIN_MM))
                    sides[sign] = {
                        "hole_end": t_out + _bu(1.0),
                        "pin_end": hits[0] + embed,
                        "anchored": True,
                        "embed_mm": _to_mm(embed),
                        "parent_wall_mm": _to_mm(wall_len),
                    }
                else:
                    blind = max(dp / 2, t_out - _bu(0.8))
                    sides[sign] = {
                        "hole_end": blind,
                        "pin_end": blind - c,
                        "anchored": False,
                        "parent_wall_mm": _to_mm(wall_len),
                    }
            if not any(s["anchored"] for s in sides.values()):
                raise ValueError(
                    f"pin: no parent wall of {_PIN_MIN_EMBED_MM + _PIN_SKIN_MM} mm or more on either side of the "
                    "child along the axis; the pin cannot anchor. Use a ball "
                    "joint, or cut the child with a tab that sits inside the parent."
                )
            if not all(s["anchored"] for s in sides.values()):
                warnings.append(
                    "pin anchored on one side only: the child can slide off along "
                    "the axis on the other side unless something else holds it"
                )
            t = time.perf_counter()
            p0, p1 = p - a * sides[-1]["pin_end"], p + a * sides[1]["pin_end"]
            pin = _pin_bm(p0, p1, dp / 2, flat) if flat > 0 else _cylinder_bm(p0, p1, dp / 2)
            h0, h1 = p - a * sides[-1]["hole_end"], p + a * sides[1]["hole_end"]
            hole = (_teardrop_bm(h0, h1, dp / 2 + c, tip) if tip is not None
                    else _cylinder_bm(h0, h1, dp / 2 + c, outer=True))
            try:
                _boolean(child_obj, "DIFFERENCE", [hole])
                _boolean(parent_obj, "UNION", [pin])
            finally:
                pin.free()
                hole.free()
            timings["pin_s"] = round(time.perf_counter() - t, 2)
            built.update(
                {
                    "pin_diameter_mm": _to_mm(dp),
                    "pin_flat_mm": _to_mm(flat),
                    "hole_diameter_mm": _to_mm(dp + 2 * c),
                    "hole_teardrop_tip": None if tip is None else [round(x, 4) for x in tip],
                    "sides": {("+axis" if k > 0 else "-axis"): {kk: vv for kk, vv in v.items() if kk.endswith("_mm") or kk == "anchored"} for k, v in sides.items()},
                }
            )
            check_radius = max(sides[1]["hole_end"], sides[-1]["hole_end"]) + _bu(3.0)
            child_wall = _walls_from(_world_tree(child_obj), p, dp / 2 + c + _bu(0.3), _perpendicular_dirs(a, 32))
            built["child_wall_around_hole_mm"] = None if child_wall is None else _to_mm(child_wall)
            if child_wall is not None and child_wall < _bu(0.8):
                warnings.append(f"the wall around the pin hole is only {_to_mm(child_wall)} mm")

        elif joint_type == "ball":
            if p_in_child:
                raise ValueError(
                    "ball: the pivot must be inside the parent, beyond the child "
                    "(the ball sits in a socket in the parent and a neck joins it "
                    "to the child)"
                )
            # Room around the socket away from the child (other cuts, the
            # outside): the socket keeps a min_wall everywhere.
            parent_tree0 = _world_tree(parent_obj)
            dirs = _sphere_dirs(800)

            def room_at(q):
                return min((h[0] for h in (_ray_hits(parent_tree0, q, d) for d in dirs) if h), default=None)

            room = room_at(p)
            if search_mm:
                # Move the pivot (keeping its depth) to where the socket has
                # the most room: away from pockets and the outer surface.
                # Closest spot where the wanted ball fits; else the roomiest.
                want = (_bu(size_mm) / 2 if size_mm is not None else _bu(3.0)) + c + wall + _bu(0.1)
                best = (room or 0.0, 0.0, p)
                fits = (room or 0.0) >= want
                reach = _bu(_number(search_mm, "search_mm", 0.0))
                plane = _perpendicular_dirs(u, 8)
                for ring in range(1, 7):
                    if fits:
                        break
                    for o in plane:
                        q = p + o * (reach * ring / 6)
                        if not _inside(parent_tree0, q) or _inside(child_tree, q):
                            continue
                        r_q = room_at(q)
                        if r_q is None:
                            continue
                        if r_q >= want and (not fits or r_q > best[0]):
                            best, fits = (r_q, (q - p).length, q), True
                        elif not fits and r_q > best[0] + _bu(0.05):
                            best = (r_q, (q - p).length, q)
                if best[2] is not p:
                    warnings.append(
                        f"ball pivot moved {_to_mm(best[1])} mm to give the socket "
                        f"{_to_mm(best[0])} mm of room (was {_to_mm(room or 0.0)})"
                    )
                    p = best[2]
                    room = best[0]
                    near = child_tree.find_nearest(p)
                    d_child = near[3]
                    u = (Vector(near[0]) - p).normalized()
            max_r0 = d_child - 3 * c - wall
            r0 = _bu(size_mm) / 2 if size_mm is not None else min(_bu(6.0), max_r0)
            if room is not None and r0 + c + wall + _bu(0.1) > room:
                fit = room - c - wall - _bu(0.1)
                if fit >= _bu(1.5):
                    warnings.append(
                        f"ball reduced from {_to_mm(2 * r0)} to {_to_mm(2 * fit)} mm to keep a "
                        f"{_MIN_WALL_MM} mm wall around the socket"
                    )
                    if neck_mm is not None:
                        neck_mm = min(neck_mm, _to_mm(2 * fit) * 0.57)
                    if mouth_mm is not None:
                        mouth_mm = min(mouth_mm, _to_mm(2 * fit) - 0.4)
                    r0 = fit
            if r0 < _bu(1.5) or r0 > max_r0 + 1e-9:
                need = _to_mm(max(r0, _bu(1.5)) + 3 * c + wall)
                raise ValueError(
                    f"ball: the pivot is {_to_mm(d_child)} mm from the child; a ball of "
                    f"{_to_mm(2 * max(r0, _bu(1.5)))} mm needs {need} mm (socket + "
                    f"{_MIN_WALL_MM} mm lip + clearances). Move the pivot deeper into the parent."
                )
            r1 = r0 + c
            rn = _bu(_number(neck_mm, "neck_mm", 0.5)) / 2 if neck_mm is not None else max(_bu(0.6), 0.55 * r0)
            mouth = _bu(_number(mouth_mm, "mouth_mm", 0.5)) / 2 if mouth_mm is not None else rn + c
            if mouth < rn + c - 1e-9:
                warnings.append("ball: mouth widened to neck + 2 x clearance")
                mouth = rn + c
            if mouth >= r0:
                raise ValueError(
                    f"ball: the socket mouth ({_to_mm(2 * mouth)} mm) must be smaller than the "
                    f"ball ({_to_mm(2 * r0)} mm) to hold it"
                )
            neck_end = p + u * (d_child + _bu(1.5))
            ball = _sphere_bm(p, r0)
            neck = _cylinder_bm(p, neck_end, rn)
            socket = _sphere_bm(p, r1, outer=True)
            slot_cyl = _cylinder_bm(p, p + u * (d_child + c), mouth, outer=True)
            slot = _swept_hull([v.co for v in slot_cyl.verts], p, a, rng)
            slot_cyl.free()
            cutters = [socket, slot]
            keep = _sphere_bm(p, d_child - c)
            t = time.perf_counter()
            try:
                if use_relief:
                    reliefs, rho = _relief_bm(child_obj, parent_obj, p, a, c, rng, keep, rho_set)
                    if reliefs:
                        cutters.extend(reliefs)
                        built["relief_radius_mm"] = _to_mm(rho)
                        built_rho = rho
                    built_rho = rho
                _boolean(parent_obj, "DIFFERENCE", cutters)
                _boolean(child_obj, "UNION", [ball, neck])
            finally:
                for b in cutters + [ball, neck, keep]:
                    b.free()
            timings["ball_s"] = round(time.perf_counter() - t, 2)
            built.update(
                {
                    "ball_diameter_mm": _to_mm(2 * r0),
                    "socket_diameter_mm": _to_mm(2 * r1),
                    "neck_diameter_mm": _to_mm(2 * rn),
                    "mouth_diameter_mm": _to_mm(2 * mouth),
                    "lip_mm": _to_mm(d_child - c - r1),
                }
            )
            check_radius = d_child + _bu(3.0)
            socket_wall = _walls_from(_world_tree(parent_obj), p, r1 + _bu(0.3))
            built["parent_wall_around_socket_mm"] = None if socket_wall is None else _to_mm(socket_wall)
            if socket_wall is not None and socket_wall < wall:
                warnings.append(
                    f"the socket wall is only {_to_mm(socket_wall)} mm somewhere (min {_MIN_WALL_MM}): "
                    "use a smaller ball or move the pivot deeper into the part"
                )

        else:  # cap
            parent_tree = _world_tree(parent_obj)
            if p_in_child:
                raise ValueError("cap: the pivot must be in the parent (the cap grows from it into the child)")
            if size_mm is not None:
                radius = _bu(size_mm) / 2
            else:
                inner = min(
                    (h[0] for h in (_ray_hits(parent_tree, p, d) for d in _perpendicular_dirs(u)) if h),
                    default=0.0,
                )
                radius = inner - wall
            if radius < d_child + _bu(0.5):
                raise ValueError(
                    f"cap: radius {_to_mm(radius)} mm does not reach 0.5 mm into the child "
                    f"(pivot is {_to_mm(d_child)} mm from it); move the pivot closer or give size_mm"
                )
            sphere = _sphere_bm(p, radius)
            half = _box_bm(p + u * (radius + _bu(1.0)), (2 * radius + _bu(2.0),) * 3, _axis_matrix(u).to_3x3())
            cap = _bm_boolean(sphere, "INTERSECT", [half])
            sphere.free()
            half.free()
            concave = _sphere_bm(p, radius + c, outer=True)
            t = time.perf_counter()
            try:
                if use_relief:
                    keep = _sphere_bm(p, radius + c)
                    reliefs, rho = _relief_bm(child_obj, parent_obj, p, a, c, rng, keep, rho_set)
                    keep.free()
                    if reliefs:
                        _boolean(parent_obj, "DIFFERENCE", reliefs)
                        for b in reliefs:
                            b.free()
                _boolean(parent_obj, "UNION", [cap])
                _boolean(child_obj, "DIFFERENCE", [concave])
            finally:
                cap.free()
                concave.free()
            timings["cap_s"] = round(time.perf_counter() - t, 2)
            built.update(
                {
                    "cap_diameter_mm": _to_mm(2 * radius),
                    "concave_diameter_mm": _to_mm(2 * (radius + c)),
                    "cap_height_mm": _to_mm(radius - d_child),
                }
            )
            check_radius = radius + _bu(3.0)

        for obj in (parent_obj, child_obj):
            # Joint cuts through a sculpt can free crumbs where it was thin.
            dropped = _drop_slivers(obj, 5e-4, 20.0)
            if dropped:
                warnings.append(f"removed slivers from {obj.name}: {dropped} mm³")
        test_radius = max(check_radius, rho_set or 0.0, built_rho or 0.0) + _bu(5.0)
        t = time.perf_counter()
        gaps = _gap_check(parent_obj, child_obj, p, check_radius)
        timings["measure_s"] = round(time.perf_counter() - t, 2)
        limit = c_mm - _GAP_TOLERANCE_MM
        measured = [g for g in (gaps["min_gap_mm"], gaps["local_min_gap_mm"]) if g is not None]
        gaps["ok"] = bool(measured) and min(measured) >= limit
        shells = {parent_obj.name: _shell_report(parent_obj), child_obj.name: _shell_report(child_obj)}
        bad_shells = [n for n, r in shells.items() if r["components"] != 1 or not r["manifold"]]
        if not gaps["ok"] or bad_shells:
            raise _JointRejected(
                {
                    "error": "joint rejected and rolled back",
                    "reason": (
                        f"gap {min(measured) if measured else None} mm is under "
                        f"{limit:.2f} mm" if not gaps["ok"] else f"not one watertight shell: {bad_shells}"
                    ),
                    "measured": gaps,
                    "parts": shells,
                    "built": built,
                }
            )
    except BaseException:
        snap.restore()
        raise
    snap.discard()

    pivot_name = None
    if make_pivot:
        pivot_name = _make_pivot(child_obj, parent_obj, p, a)
        bpy.data.objects[pivot_name]["bc_joint"] = json.dumps(
            {"type": joint_type, "parent": parent_obj.name, "clearance_mm": c_mm, "range_deg": list(rng),
             "role": str(role) if role else None}
        )
    result = {
        "joint": joint_type,
        "parent": parent_obj.name,
        "child": child_obj.name,
        "pivot": pivot_name,
        "pivot_mm": _point_mm(p),
        "axis": [round(x, 6) for x in a],
        "clearance_mm": c_mm,
        "range_deg": list(rng),
        "built": built,
        "measured": gaps,
        "parts": shells,
    }
    if test and pivot_name:
        t = time.perf_counter()
        start, end = max(-180.0, rng[0] - 15.0), min(180.0, rng[1] + 15.0)
        sweep = _sweep_test(parent_obj, child_obj, p, a, start, end, 2.5 if end - start <= 120 else 5.0, test_radius)
        result["sweep"] = sweep
        timings["joint_test_s"] = round(time.perf_counter() - t, 2)
        t = time.perf_counter()
        pulls = _pull_test(parent_obj, child_obj, {"out": u, "+axis": a, "-axis": -a}, p, test_radius)
        result["retention"] = {
            "slide_before_contact_mm": pulls,
            "retained": all(v is not None and v <= 1.5 for v in pulls.values()),
        }
        timings["pull_test_s"] = round(time.perf_counter() - t, 2)
        if joint_type == "cap" and not result["retention"]["retained"]:
            warnings.append("a cap only guides the turn: hold the parts with a pin or ball joint")
        if sweep["colliding_at_rest"]:
            warnings.append("the parts touch at rest: see sweep.collisions")
        elif sweep["free_min"] > rng[0] + 1e-6 or sweep["free_max"] < rng[1] - 1e-6:
            warnings.append(
                f"free range {sweep['free_min']}..{sweep['free_max']} deg does not cover the "
                f"designed {rng[0]}..{rng[1]} deg: other geometry blocks it (see sweep.collisions)"
            )
        result["range_ok"] = (
            not sweep["colliding_at_rest"]
            and sweep["free_min"] <= rng[0] + 1e-6
            and sweep["free_max"] >= rng[1] - 1e-6
        )
    timings["total_s"] = round(time.perf_counter() - t0, 2)
    result["timings"] = timings
    if warnings:
        result["warnings"] = warnings
    return result


class _JointRejected(Exception):
    def __init__(self, payload: dict):
        super().__init__(json.dumps(payload, ensure_ascii=False))
        self.payload = payload


# ---------------------------------------------------------------- fit_clearance


def fit_clearance(
    female: str,
    male: str,
    clearance_mm: float = 0.30,
    pivot=None,
    axis=None,
    range_deg: float = 0.0,
    region_radius_mm: float | None = None,
) -> dict:
    """Subtract the male part, grown by the clearance (and swept about a
    pivot/axis), from the female part: hole for a pin, socket for a ball,
    notch for a leg that turns."""
    f_obj, m_obj = _part(female), _part(male)
    if f_obj == m_obj:
        raise ValueError("female and male must be different objects")
    c_mm = _number(clearance_mm, "clearance_mm", 0.05)
    if c_mm > 1.0:
        raise ValueError("clearance_mm must be at most 1 mm")
    c = _bu(c_mm)
    rng = _parse_range(range_deg, 0.0)
    if (pivot is None) != (axis is None):
        raise ValueError("give both pivot and axis, or neither")
    if rng != (0.0, 0.0) and pivot is None:
        raise ValueError("range_deg needs a pivot and an axis")
    t0 = time.perf_counter()
    male_bm = _world_bm(m_obj)
    snap = _Snapshot([f_obj])
    try:
        if pivot is not None:
            p, a = _vec_mm(pivot, "pivot"), _direction(axis, "axis")
        else:
            p = sum((v.co for v in male_bm.verts), Vector()) / max(1, len(male_bm.verts))
            a = Vector((0, 0, 1))
        piece = male_bm
        if region_radius_mm is not None:
            radius = _bu(_number(region_radius_mm, "region_radius_mm", 0.0, allow_zero=False))
            ball = _sphere_bm(p, radius)
            piece = _bm_boolean(male_bm, "INTERSECT", [ball])
            ball.free()
        if len(piece.verts) < 4:
            raise ValueError("the region does not contain the male part")
        base = _hull_points([v.co for v in piece.verts])
        offsets = _ball_offsets(c + _bu(0.01))
        cutter = _swept_hull([q + o for q in base for o in offsets], p, a, rng)
        if piece is not male_bm:
            piece.free()
        t = time.perf_counter()
        _boolean(f_obj, "DIFFERENCE", [cutter])
        t_bool = time.perf_counter() - t
        cutter.free()
        _drop_slivers(f_obj)
        gaps = _gap_check(f_obj, m_obj, p, _bu(1e6))
        limit = c_mm - _GAP_TOLERANCE_MM
        measured = [g for g in (gaps["min_gap_mm"], gaps["local_min_gap_mm"]) if g is not None]
        gaps["ok"] = bool(measured) and min(measured) >= limit
        shells = {f_obj.name: _shell_report(f_obj)}
        if not gaps["ok"]:
            raise _JointRejected({"error": "fit rejected and rolled back", "measured": gaps})
    except BaseException:
        snap.restore()
        raise
    finally:
        male_bm.free()
    snap.discard()
    return {
        "female": f_obj.name,
        "male": m_obj.name,
        "clearance_mm": c_mm,
        "range_deg": list(rng),
        "measured": gaps,
        "parts": shells,
        "note": "the hull of the male region is subtracted: concave male shapes get extra room",
        "timings_s": {"boolean": round(t_bool, 2), "total": round(time.perf_counter() - t0, 2)},
    }


def boolean_parts(target: str, tools: list[str], operation: str, delete_tools: bool = False) -> dict:
    op = str(operation).upper()
    if op not in ("UNION", "DIFFERENCE", "INTERSECT"):
        raise ValueError("operation must be union, difference or intersect")
    obj = _part(target)
    if not tools:
        raise ValueError("tools must list at least one object")
    tool_objs = [_part(n) for n in tools]
    if obj in tool_objs:
        raise ValueError("target cannot be one of the tools")
    bms = [_world_bm(o) for o in tool_objs]
    snap = _Snapshot([obj])
    try:
        seconds = _boolean(obj, op, bms)
        if not obj.data.polygons:
            raise ValueError("the result is empty")
    except BaseException:
        snap.restore()
        raise
    finally:
        for b in bms:
            b.free()
    snap.discard()
    if delete_tools:
        for o in tool_objs:
            mesh = o.data
            bpy.data.objects.remove(o, do_unlink=True)
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
    return {
        "target": obj.name,
        "operation": op.lower(),
        "deleted_tools": [n for n in tools] if delete_tools else [],
        "part": _shell_report(obj),
        "timings_s": round(seconds, 2),
    }


# ---------------------------------------------------------------- clearance sweep


def clearance_sweep(pivot: str, start: float = 0.0, end: float = 90.0, step: float = 5.0,
                    radius_mm: float | None = None) -> dict:
    """Minimum gap between a joint's moving part and the rest, angle by angle
    (the scene is not changed)."""
    pivot_obj = joints._get_pivot(pivot)
    meshes = [o for o in pivot_obj.children_recursive if o.type == "MESH"]
    if not meshes:
        raise ValueError(f"{pivot} moves no mesh")
    if step <= 0 or end < start or (end - start) / step > 360:
        raise ValueError("need start <= end and a step > 0 (at most 360 steps)")
    info = {}
    try:
        info = json.loads(pivot_obj.get("bc_joint", "{}"))
    except (TypeError, ValueError):
        pass
    parent = bpy.data.objects.get(info.get("parent", "")) if info.get("parent") else None
    child = max(meshes, key=lambda o: len(o.data.vertices))
    p = pivot_obj.matrix_world.translation.copy()
    axis_i = "XYZ".index(pivot_obj["bc_axis"])
    axis = (pivot_obj.matrix_world.to_3x3() @ Matrix.Identity(3).col[axis_i]).normalized()
    radius = _bu(radius_mm) if radius_mm else max(child.dimensions) * 1.2
    obstacles = _obstacles(child, parent) if parent else [
        o for o in bpy.context.scene.objects
        if o.type == "MESH" and o.visible_get() and o not in meshes
    ]
    static = _cropped_world(obstacles, p, radius)
    moving = _cropped_world(meshes, p, radius)
    rows = []
    try:
        n = int(round((end - start) / step))
        for k in range(n + 1):
            angle = start + k * step
            posed = moving.copy()
            posed.transform(_rotate_about(p, axis, math.radians(angle)))
            gap = _sampled_gap(posed, static, 400_000)
            posed.free()
            rows.append({"angle": round(angle, 3), "min_gap_mm": None if gap is None else _to_mm(gap, 3)})
    finally:
        static.free()
        moving.free()
    gaps = [r["min_gap_mm"] for r in rows if r["min_gap_mm"] is not None]
    return {
        "pivot": pivot_obj.name,
        "rows": rows,
        "min_gap_mm": min(gaps) if gaps else None,
        "within_mm": _to_mm(radius),
    }


# ---------------------------------------------------------------- compare_surface


def compare_surface(original: str, parts: list[str] | None = None, tolerance_mm: float = 1e-4) -> dict:
    """How much of the original surface survives untouched in the parts."""
    from mathutils.kdtree import KDTree

    orig = _get_object(original)
    if orig.type != "MESH":
        raise ValueError(f"Object is not a mesh: {orig.name}")
    if parts:
        objs = [_get_object(n) for n in parts]
    else:
        objs = [
            o for o in bpy.context.scene.objects
            if o.type == "MESH" and o.visible_get() and o != orig
        ]
    if not objs:
        raise ValueError("no parts to compare with")
    tol = _bu(_number(tolerance_mm, "tolerance_mm", 0.0, allow_zero=False))
    t0 = time.perf_counter()
    part_bms = [_world_bm(o) for o in objs]
    try:
        total = sum(len(b.verts) for b in part_bms)
        tree = KDTree(total)
        k = 0
        for b in part_bms:
            for v in b.verts:
                tree.insert(v.co, k)
                k += 1
        tree.balance()
        lo = [min(v.co[i] for b in part_bms for v in b.verts) for i in range(3)]
        hi = [max(v.co[i] for b in part_bms for v in b.verts) for i in range(3)]
    finally:
        for b in part_bms:
            b.free()
    ob = _world_bm(orig)
    try:
        moved = []
        for v in ob.verts:
            _co, _i, dist = tree.find(v.co)
            if dist is None or dist > tol:
                moved.append(v.co.copy())
        o_lo = [min(v.co[i] for v in ob.verts) for i in range(3)]
        o_hi = [max(v.co[i] for v in ob.verts) for i in range(3)]
        count = len(ob.verts)
    finally:
        ob.free()
    # Group the changed vertices into zones (5 mm grid, flood-filled).
    cell = _bu(5.0)
    cells = {}
    for p in moved:
        cells.setdefault(tuple(int(math.floor(p[i] / cell)) for i in range(3)), []).append(p)
    zones, seen = [], set()
    for start in cells:
        if start in seen:
            continue
        seen.add(start)
        stack, members = [start], []
        while stack:
            c = stack.pop()
            members.extend(cells[c])
            for d in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
                n = (c[0] + d[0], c[1] + d[1], c[2] + d[2])
                if n in cells and n not in seen:
                    seen.add(n)
                    stack.append(n)
        zlo = [min(p[i] for p in members) for i in range(3)]
        zhi = [max(p[i] for p in members) for i in range(3)]
        zones.append({"vertices": len(members), "min_mm": _point_mm(zlo), "max_mm": _point_mm(zhi)})
    zones.sort(key=lambda z: -z["vertices"])
    size_o = [o_hi[i] - o_lo[i] for i in range(3)]
    size_p = [hi[i] - lo[i] for i in range(3)]
    return {
        "original": orig.name,
        "parts": [o.name for o in objs],
        "vertices": count,
        "identical": count - len(moved),
        "identical_percent": round(100.0 * (count - len(moved)) / max(1, count), 3),
        "changed_zones": zones[:20],
        "dimensions_original_mm": _point_mm(size_o),
        "dimensions_parts_mm": _point_mm(size_p),
        "dimension_difference_mm": [_to_mm(abs(a - b)) for a, b in zip(size_o, size_p)],
        "timings_s": round(time.perf_counter() - t0, 2),
        "note": (
            "changed vertices must lie only in the cut zones (waist, under the thighs); "
            "every other vertex of the original must be identical"
        ),
    }


# ---------------------------------------------------------------- articulate_figure

HELPER_COLLECTION = "BC_Plano"


def _clear_helpers() -> int:
    """Remove the plan helpers (cut planes, pivots) shown for confirmation."""
    coll = bpy.data.collections.get(HELPER_COLLECTION)
    if coll is None:
        return 0
    count = 0
    for obj in list(coll.objects):
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data is not None and data.users == 0 and isinstance(data, bpy.types.Mesh):
            bpy.data.meshes.remove(data)
        count += 1
    bpy.data.collections.remove(coll)
    return count


def _quadruped_plan(name: str, analysis: dict, options: dict) -> list[dict]:
    clearance = float(options.get("clearance_mm", 0.30))
    legs = str(options.get("legs", "pin")).lower()
    if legs not in ("pin", "ball", "none"):
        raise ValueError("plan.legs must be pin, ball or none")
    leg_range = float(options.get("leg_range_deg", 30.0))
    steps = []
    if legs != "none":
        for leg in analysis["legs"]:
            part_name = f"{name}_{leg['name']}"
            if leg["existing_part"]:
                steps.append(
                    {
                        "op": "note",
                        "text": f"{leg['name']} is already a separate part ({leg['part']}): "
                        "join it with add_joint (pin with the pivot inside its top) "
                        "or fit_clearance by hand",
                    }
                )
                continue
            design = leg[legs]
            steps.append(
                {"op": "cut", "part": name, "cutter": design["cutter"], "new_part_name": part_name, "gap_mm": clearance}
            )
            steps.append(
                {
                    "op": "joint", "parent": name, "child": part_name, "type": legs,
                    "pivot": design["pivot"], "axis": design["axis"], "size_mm": design["size_mm"],
                    "clearance_mm": clearance, "range_deg": leg_range,
                }
            )
    if options.get("tail") and analysis.get("tail"):
        tail = analysis["tail"]["cut"]
        steps.append({"op": "cut", "part": name, "cutter": tail, "new_part_name": f"{name}_tail", "gap_mm": clearance})
        normal = Vector(tail["normal"])
        pivot = (Vector(tail["point"]) - normal * 2.5)
        steps.append({"op": "joint", "parent": name, "child": f"{name}_tail", "type": "ball",
                      "pivot": list(pivot), "axis": [0, 0, 1], "clearance_mm": clearance, "range_deg": 30})
    if options.get("neck") and analysis.get("neck"):
        neck = analysis["neck"]["cut"]
        steps.append({"op": "cut", "part": name, "cutter": neck, "new_part_name": f"{name}_head", "gap_mm": clearance})
        normal = Vector(neck["normal"])
        depth = float(options.get("neck_ball_mm", 6.0)) / 2 + 3 * clearance + _MIN_WALL_MM + 0.3
        pivot = Vector(neck["point"]) - normal * depth
        steps.append({"op": "joint", "parent": name, "child": f"{name}_head", "type": "ball",
                      "pivot": list(pivot), "axis": [0, 0, 1], "size_mm": float(options.get("neck_ball_mm", 6.0)),
                      "clearance_mm": clearance, "range_deg": float(options.get("neck_range_deg", 40.0))})
    if options.get("mid_body") and analysis.get("mid_body"):
        mid = analysis["mid_body"]["cut"]
        gap = float(options.get("mid_gap_mm", 1.3))
        steps.append({"op": "cut", "part": name, "cutter": mid, "new_part_name": f"{name}_rear", "gap_mm": gap})
        normal = Vector(mid["normal"])
        pivot = Vector(mid["point"]) - normal * 2.0
        width_axis = [1, 0, 0] if analysis["width_axis"] == "X" else [0, 1, 0]
        steps.append({"op": "joint", "parent": name, "child": f"{name}_rear", "type": "cap",
                      "pivot": list(pivot), "axis": width_axis, "clearance_mm": clearance, "range_deg": 10})
    return steps


def articulate_figure(name: str, plan=None, dry_run: bool = False) -> dict:
    _part(name)
    plan = plan or {}
    if not isinstance(plan, dict):
        raise ValueError("plan must be an object: {preset, ...options} or {steps: [...]}")
    t0 = time.perf_counter()
    if "steps" in plan:
        steps = plan["steps"]
        if steps == "scene":
            from .articulation_templates import scene_plan

            steps = scene_plan()
        if not isinstance(steps, list) or not steps:
            raise ValueError("plan.steps must be a non-empty list")
        analysis = None
    else:
        preset = str(plan.get("preset", "quadruped")).lower()
        if preset != "quadruped":
            raise ValueError("plan.preset must be quadruped (or give plan.steps)")
        analysis = analyze_figure(name, plan.get("others"))
        steps = _quadruped_plan(name, analysis, plan)
    only = plan.get("only")
    selected = list(range(len(steps)))
    if only is not None:
        if not isinstance(only, list) or not all(isinstance(i, int) and 0 <= i < len(steps) for i in only):
            raise ValueError(f"plan.only must list step indices between 0 and {len(steps) - 1}")
        selected = sorted(set(only))
    if dry_run:
        summary = None
        if analysis is not None:
            summary = {
                "dimensions_mm": analysis["dimensions_mm"],
                "head_direction": analysis["head_direction"],
                "legs": [
                    {"name": l["name"], "part": l["part"], "pin_fits": l.get("pin", {}).get("fits"),
                     "ball_fits": l.get("ball", {}).get("fits")}
                    for l in analysis["legs"]
                ],
            }
        return {"name": name, "dry_run": True, "steps": steps, "analysis": summary}
    done, table = [], []
    _clear_helpers()
    for index in selected:
        step = steps[index]
        op = step.get("op")
        t = time.perf_counter()
        try:
            if op == "cut":
                res = cut_part(step["part"], step["cutter"], step["new_part_name"],
                               step.get("gap_mm", 0.3), step.get("allow_multiple_shells", False),
                               step.get("drop_shells_under_mm3", 0.0), bool(step.get("discard_rest", False)))
                brief = {"child": res["child"], "parts": {
                    k: ("discarded" if v.get("discarded") else f"{v['components']} shell(s), manifold={v['manifold']}")
                    for k, v in res["parts"].items()},
                    "dropped_mm3": res["dropped_slivers_mm3"], "timings": res["timings_s"]}
            elif op == "joint":
                res = add_joint(step["parent"], step["child"], step["type"], step["pivot"], step["axis"],
                                step.get("size_mm"), step.get("clearance_mm", 0.30), step.get("range_deg"),
                                step.get("relief"), bool(step.get("test", True)),
                                bool(step.get("make_pivot", True)), step.get("neck_mm"), step.get("mouth_mm"),
                                step.get("relief_radius_mm"), step.get("role"), step.get("pin_flat_mm"),
                                step.get("hole_tip"), step.get("embed_mm"), step.get("search_mm"),
                                step.get("relief_also"))
                sweep = res.get("sweep", {})
                table.append(
                    {
                        "step": index,
                        "joint": f"{res['parent']} -> {res['child']}",
                        "type": res["joint"],
                        "pivot": res["pivot"],
                        "target_clearance_mm": res["clearance_mm"],
                        "built_min_gap_mm": res["measured"]["min_gap_mm"],
                        "built_local_gap_mm": res["measured"]["local_min_gap_mm"],
                        "target_range_deg": res["range_deg"],
                        "built_range_deg": [sweep.get("free_min"), sweep.get("free_max")],
                        "retained": res.get("retention", {}).get("retained"),
                        "ok": res["measured"]["ok"],
                    }
                )
                brief = {"built": res["built"], "warnings": res.get("warnings", []), "timings": res["timings"]}
            elif op == "note":
                brief = {"note": step.get("text")}
            else:
                raise ValueError(f"unknown step op {op!r} (cut, joint or note)")
        except Exception as exc:  # noqa: BLE001 - reported with the progress
            detail = exc.payload if isinstance(exc, _JointRejected) else str(exc)
            return {
                "name": name,
                "failed_step": {"index": index, "step": step, "error": detail},
                "table": table,
                "completed_steps": done,
                "timings_s": round(time.perf_counter() - t0, 2),
                "note": "steps before the failed one stay applied (undo reverts the whole call)",
            }
        done.append({"index": index, "op": op, "seconds": round(time.perf_counter() - t, 2), **brief})
    return {
        "name": name,
        "steps_run": selected,
        "table": table,
        "completed_steps": done,
        "timings_s": round(time.perf_counter() - t0, 2),
    }


# ---------------------------------------------------------------- tool handlers


def _wrap(fn):
    def handler(args: dict):
        try:
            return json_result(fn(args))
        except _JointRejected as exc:
            from .base import ToolResult

            return ToolResult(text=json.dumps(exc.payload, ensure_ascii=False, indent=1), is_error=True)

    return handler


_POINT = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
_RANGE = {
    "description": "degrees: a number r for -r..r, or [min, max] about the axis (right-hand rule), containing 0",
    "anyOf": [{"type": "number"}, {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}],
}
_CUTTER_DOC = (
    "Cutter (mm, world): {type: plane, point, normal} keeps for the new part "
    "the side the normal points to; {type: box, center, size, rotation_deg?}; "
    "{type: cylinder, start, end, radius}; {type: sphere, center, radius}; "
    "{type: prism, start, end, width, thickness, chamfer, width_axis} (octagonal "
    "leg block); {type: revolution, start, end, radius, chamfer} (leg top turned "
    "about the pin axis); {type: union, parts: [...]}. A part with fill: true "
    "makes the new part solid inside it (closes the sculpt's grooves there)."
)

register_tool(
    Tool(
        name="prepare_mesh",
        description=(
            "Make an imported figure workable without losing the sculpt: apply "
            "modifiers, merge duplicate vertices (merge_mm), remove loose "
            "geometry and islands under 1 mm³, fill small holes and fix "
            "normals; nothing else on the surface changes. Keeps a hidden copy "
            "'<name> (STL original)' (keep_original) for compare_surface. Never "
            "remeshes or decimates by itself: voxel_mm and target_faces do, "
            "and they rebuild the sculpt (previews only). split_shells makes one object per separate "
            "shell. Reports before/after faces, manifold, shells, dimensions "
            "(mm), overlapping and near shells (closer than near_gap_mm weld "
            "when printed) and timings. A leg that is already a "
            "separate shell becomes its own part with split_shells; "
            "add_joint (and apply_articulation_template) then open the real "
            "clearance around it. Booleans stay fast on 500k faces, so "
            "keep the original resolution."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "target_faces": {"type": "integer"},
                "voxel_mm": {"type": "number"},
                "merge_mm": {"type": "number"},
                "split_shells": {"type": "boolean"},
                "near_gap_mm": {"type": "number"},
                "keep_original": {"type": "boolean"},
                "description": {"type": "string"},
            },
            "required": ["name"],
        },
        handler=_wrap(
            lambda a: prepare_mesh(
                str(a["name"]),
                a.get("target_faces"),
                a.get("voxel_mm"),
                float(a.get("merge_mm", 0.001)),
                bool(a.get("split_shells", False)),
                float(a.get("near_gap_mm", 0.3)),
                bool(a.get("keep_original", True)),
            )
        ),
        kind="scene",
    )
)

register_tool(
    Tool(
        name="analyze_figure",
        description=(
            "Read-only layout of a standing figure (Z up, floor at its lowest "
            "point) for planning articulations: dimensions, length/width axis, "
            "head direction, belly height, legs (foot islands followed up to "
            "where they join the body) with ready cut+joint suggestions for a "
            "pin hinge (leg box plus a tab inside the body, pin across the "
            "width axis) and a ball joint, neck and tail cut planes, a "
            "mid-body plane and a left/right mirror check, with confidence. "
            "others: extra objects that belong to the figure (legs already "
            "separate). Heuristic: confirm with screenshots before cutting."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "others": {"type": "array", "items": {"type": "string"}},
                "cell_mm": {"type": "number"},
            },
            "required": ["name"],
        },
        handler=_wrap(lambda a: analyze_figure(str(a["name"]), a.get("others"), a.get("cell_mm"))),
        kind="read",
    )
)

register_tool(
    Tool(
        name="cut_part",
        description=(
            "Split a part with EXACT booleans: the new part is what lies inside "
            "the cutter, the original keeps the rest minus the cutter grown by "
            "gap_mm (default 0.30, the print-in-place clearance; 0 for a "
            "glued split). Cut faces are filled; both results must be one "
            "watertight shell or the cut is rolled back. " + _CUTTER_DOC +
            " Returns both parts' topology and timings."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "cutter": {"type": "object"},
                "new_part_name": {"type": "string"},
                "gap_mm": {"type": "number"},
                "allow_multiple_shells": {"type": "boolean"},
                "drop_shells_under_mm3": {"type": "number"},
                "discard_rest": {"type": "boolean", "description": "keep only the new part (trim a separate leg)"},
                "description": {"type": "string"},
            },
            "required": ["name", "cutter", "new_part_name"],
        },
        handler=_wrap(
            lambda a: cut_part(
                str(a["name"]), a["cutter"], str(a["new_part_name"]), a.get("gap_mm", 0.3),
                bool(a.get("allow_multiple_shells", False)), a.get("drop_shells_under_mm3", 0.0),
                bool(a.get("discard_rest", False)),
            )
        ),
        kind="scene",
    )
)

register_tool(
    Tool(
        name="add_joint",
        description=(
            "Build a real print-in-place joint between two separate parts "
            "(mm, world). type pin: the pivot is INSIDE the child; a hole of "
            "pin + 2 x clearance crosses the child along axis and a pin of "
            "size_mm (diameter) is fused to the parent, anchored in its walls "
            "on both sides. type ball: the pivot is inside the PARENT, beyond "
            "the child; a ball of size_mm on a neck joins the child, the parent "
            "gets the socket (ball + clearance) with a retaining lip of 1.2 mm+ "
            "and a swing slot for +-range_deg about axis. type cap: a spherical "
            "cap of size_mm grows from the parent into a concave face in the "
            "child (guide only; hold the parts with a pin or ball). relief "
            "(default on for pin and ball) carves the parent where the child "
            "turns through range_deg, within relief_radius_mm of the pivot "
            "(default: the contact zone). Then every gap is MEASURED: under "
            "clearance - 0.02 mm, or a part not one watertight shell, rolls "
            "everything back with an error. On success the child gets the "
            "pivot BC_Pivo_<child> and the result has the measured gaps, the "
            "joint_test sweep (free range, range_ok), a pull test (retained), "
            "walls and timings. Ball options: neck_mm (stem), mouth_mm (socket "
            "opening, between neck + 0.6 and the ball). A pin anchors at least "
            "1.5 mm in each wall. clearance_mm: 0.30 print-in-place, 0.20 assembled; "
            "never scale it with the model. make_pivot false builds the "
            "geometry only (e.g. a cap that guides a pin joint)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "parent": {"type": "string"},
                "child": {"type": "string"},
                "type": {"type": "string", "enum": list(_JOINT_TYPES)},
                "pivot": _POINT,
                "axis": _POINT,
                "size_mm": {"type": "number"},
                "clearance_mm": {"type": "number"},
                "range_deg": _RANGE,
                "relief": {"type": "boolean"},
                "test": {"type": "boolean"},
                "make_pivot": {"type": "boolean"},
                "neck_mm": {"type": "number"},
                "mouth_mm": {"type": "number"},
                "relief_radius_mm": {"type": "number"},
                "role": {"type": "string", "description": "e.g. leg, waist, neck, tail (set_pose finds legs by it)"},
                "pin_flat_mm": {"type": "number", "description": "pin: flat underside this deep (0.2 at 35 mm)"},
                "hole_tip": {**_POINT, "description": "pin: teardrop hole, 45 deg tip toward this direction"},
                "embed_mm": {"type": "number", "description": "pin: depth in each wall (default 1.5, min 1.2)"},
                "search_mm": {"type": "number", "description": "ball: look this far around the pivot for the spot with most room"},
                "relief_also": {"type": "array", "items": {"type": "string"},
                                "description": "pin: other parts the turning child passes near (e.g. the other body half)"},
                "description": {"type": "string"},
            },
            "required": ["parent", "child", "type", "pivot", "axis"],
        },
        handler=_wrap(
            lambda a: add_joint(
                str(a["parent"]), str(a["child"]), a["type"], a["pivot"], a["axis"],
                a.get("size_mm"), a.get("clearance_mm", 0.30), a.get("range_deg"),
                a.get("relief"), bool(a.get("test", True)), bool(a.get("make_pivot", True)),
                a.get("neck_mm"), a.get("mouth_mm"), a.get("relief_radius_mm"), a.get("role"),
                a.get("pin_flat_mm"), a.get("hole_tip"), a.get("embed_mm"), a.get("search_mm"),
                a.get("relief_also"),
            )
        ),
        kind="scene",
    )
)

register_tool(
    Tool(
        name="fit_clearance",
        description=(
            "Give two existing parts a real clearance: subtract from the "
            "female part the male part (or the piece of it within "
            "region_radius_mm of the pivot) grown by clearance_mm and, with "
            "pivot + axis + range_deg, swept through +-range_deg so the male "
            "can turn. Use it to open holes for pins, sockets for balls and "
            "notches for legs of a model that already has separate parts. The "
            "convex hull of the male region is used. Measures the gap and "
            "rolls back if it is under clearance - 0.02 mm."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "female": {"type": "string"},
                "male": {"type": "string"},
                "clearance_mm": {"type": "number"},
                "pivot": _POINT,
                "axis": _POINT,
                "range_deg": _RANGE,
                "region_radius_mm": {"type": "number"},
                "description": {"type": "string"},
            },
            "required": ["female", "male"],
        },
        handler=_wrap(
            lambda a: fit_clearance(
                str(a["female"]), str(a["male"]), a.get("clearance_mm", 0.30), a.get("pivot"),
                a.get("axis"), a.get("range_deg", 0.0), a.get("region_radius_mm"),
            )
        ),
        kind="scene",
    )
)

register_tool(
    Tool(
        name="boolean_parts",
        description=(
            "EXACT boolean between parts: union (fuse a ball or pin into a "
            "part), difference (subtract hole solids) or intersect. tools are "
            "object names; delete_tools removes them afterwards. Rolls back if "
            "the result is empty. Reports the target's shells and timing."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "tools": {"type": "array", "items": {"type": "string"}},
                "operation": {"type": "string", "enum": ["union", "difference", "intersect"]},
                "delete_tools": {"type": "boolean"},
                "description": {"type": "string"},
            },
            "required": ["target", "tools", "operation"],
        },
        handler=_wrap(
            lambda a: boolean_parts(
                str(a["target"]), list(a["tools"]), a["operation"], bool(a.get("delete_tools", False))
            )
        ),
        kind="scene",
    )
)

register_tool(
    Tool(
        name="joint_clearance_sweep",
        description=(
            "Measure a joint's clearance at every angle: turns the part hung "
            "on the pivot from start to end (degrees, every step) without "
            "changing the scene and returns the minimum gap to the other parts "
            "at each angle (mm, 0 = touching). Use it to prove a leg keeps "
            "0.30 mm through 0-90 deg."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "pivot": {"type": "string"},
                "start": {"type": "number"},
                "end": {"type": "number"},
                "step": {"type": "number"},
                "radius_mm": {"type": "number"},
            },
            "required": ["pivot"],
        },
        handler=_wrap(
            lambda a: clearance_sweep(
                str(a["pivot"]), float(a.get("start", 0.0)), float(a.get("end", 90.0)),
                float(a.get("step", 5.0)), a.get("radius_mm"),
            )
        ),
        kind="read",
    )
)

register_tool(
    Tool(
        name="compare_surface",
        description=(
            "Acceptance check that the sculpt was preserved: compares every "
            "vertex of the original (e.g. '<name> (STL original)') with the "
            "vertices of the parts (default: all visible meshes). Returns the "
            "percentage identical (within tolerance_mm, default 0.0001), the "
            "zones where vertices changed (should be only the cut zones) and "
            "overall dimensions original vs parts."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "original": {"type": "string"},
                "parts": {"type": "array", "items": {"type": "string"}},
                "tolerance_mm": {"type": "number"},
            },
            "required": ["original"],
        },
        handler=_wrap(
            lambda a: compare_surface(str(a["original"]), a.get("parts"), a.get("tolerance_mm", 1e-4))
        ),
        kind="read",
    )
)

register_tool(
    Tool(
        name="articulate_figure",
        description=(
            "Run a whole articulation plan in one call and return a "
            "target-vs-built table with measured clearances and free ranges. "
            "plan {preset: 'quadruped', legs: 'pin'|'ball'|'none', neck: bool, "
            "tail: bool, mid_body: bool, clearance_mm: 0.30, leg_range_deg: 30, "
            "others: [separate parts]} builds the steps from analyze_figure; "
            "or plan {steps: [{op: 'cut', part, cutter, new_part_name, gap_mm}, "
            "{op: 'joint', parent, child, type, pivot, axis, size_mm, "
            "clearance_mm, range_deg, relief?, make_pivot?}] or steps: 'scene' (the plan saved by "
            "apply_articulation_template), only: [step "
            "indices to run now]}. dry_run returns the steps without "
            "changing anything. Plan helpers (BC_Plano) are removed first. One undo step. On heavy meshes (300k+ faces) "
            "prefer dry_run and then cut_part/add_joint one by one: each "
            "call must finish within 2 minutes."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "plan": {"type": "object"},
                "dry_run": {"type": "boolean"},
                "description": {"type": "string"},
            },
            "required": ["name"],
        },
        handler=_wrap(lambda a: articulate_figure(str(a["name"]), a.get("plan"), bool(a.get("dry_run", False)))),
        kind="scene",
    )
)


# Template tools build on these; imported last so they can use everything above.
from . import articulation_templates  # noqa: E402,F401
from . import posing  # noqa: E402,F401
