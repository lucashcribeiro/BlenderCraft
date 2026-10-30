"""Mesh topology and 3D-print checks on the evaluated, world-space mesh.

Every number is reported in millimeters (areas in mm², volumes in mm³).
"""

import math

import bmesh
import bpy
from mathutils.bvhtree import BVHTree

from ..units import mm_per_bu
from .base import Tool, json_result, register_tool
from .inspect import _evaluated_world_mesh, _get_object

# Wall-thickness rays start this far (mm) behind the face, so a face never
# hits itself.
_RAY_BIAS_MM = 0.001
# Faces whose lowest point is this close to the lowest Z (mm) rest on the bed.
_BED_CONTACT_MM = 0.01


def _oriented_world_mesh(obj) -> bmesh.types.BMesh:
    """World-space evaluated mesh with consistent, up-to-date normals.

    A negative-determinant transform (mirrored scale) flips the winding, so
    the faces are reversed back to keep the signed volume meaningful.
    """
    bpy.context.view_layer.update()
    bm = _evaluated_world_mesh(obj)
    if obj.matrix_world.determinant() < 0:
        bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
    bm.normal_update()
    return bm


def _count_components(bm) -> int:
    """Connected components of vertices through edges (a loose vertex counts)."""
    seen = set()
    count = 0
    for start in bm.verts:
        if start.index in seen:
            continue
        count += 1
        seen.add(start.index)
        stack = [start]
        while stack:
            vert = stack.pop()
            for edge in vert.link_edges:
                other = edge.other_vert(vert)
                if other.index not in seen:
                    seen.add(other.index)
                    stack.append(other)
    return count


def mesh_topology(obj_name: str) -> dict:
    """Connectivity, manifoldness and volume of the evaluated world mesh."""
    obj = _get_object(obj_name)
    mm = mm_per_bu(bpy.context.scene)
    bm = _oriented_world_mesh(obj)
    try:
        bm.verts.index_update()
        boundary = sum(1 for e in bm.edges if len(e.link_faces) == 1)
        # Edges with no face (wire) or three or more faces; boundary edges are
        # counted separately.
        non_manifold = sum(
            1 for e in bm.edges if len(e.link_faces) == 0 or len(e.link_faces) > 2
        )
        # Vertex-level problems (bow-ties, loose vertices) also break printing.
        verts_ok = all(v.is_manifold for v in bm.verts)
        signed = bm.calc_volume(signed=True)
        return {
            "components": _count_components(bm),
            "manifold": bool(bm.faces) and boundary == 0 and non_manifold == 0 and verts_ok,
            "boundary_edges": boundary,
            "non_manifold_edges": non_manifold,
            "inverted_normals": signed < 0,
            "vertices": len(bm.verts),
            "faces": len(bm.faces),
            "volume_mm3": round(abs(signed) * mm**3, 4),
        }
    finally:
        bm.free()


def print_check(
    obj_name: str, min_wall_mm: float = 0.8, overhang_deg: float = 45.0
) -> dict:
    """Thin walls, overhangs and the part below the print bed."""
    if not 0.0 <= overhang_deg <= 90.0:
        raise ValueError("overhang_deg must be between 0 and 90")
    obj = _get_object(obj_name)
    mm = mm_per_bu(bpy.context.scene)
    bm = _oriented_world_mesh(obj)
    try:
        # Rays and overhangs assume outward normals; an inside-out mesh is
        # checked as the solid it describes.
        if bm.calc_volume(signed=True) < 0:
            bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
            bm.normal_update()

        # Thin walls: from the centroid of each loop triangle (a point exactly
        # on the surface, unlike the center of a non-planar quad), shoot
        # inward and measure the distance to the first surface hit. A face's
        # wall is the thinnest of its triangles.
        tree = BVHTree.FromBMesh(bm)
        bias = _RAY_BIAS_MM / mm
        bm.faces.index_update()
        face_wall: dict[int, float] = {}
        for tri in bm.calc_loop_triangles():
            a, b, c = (loop.vert.co for loop in tri)
            normal = (b - a).cross(c - a)
            if normal.length == 0.0:
                continue
            direction = -normal.normalized()
            origin = (a + b + c) / 3 + direction * bias
            hit = tree.ray_cast(origin, direction)
            if hit[0] is None:
                continue
            distance_mm = (hit[3] + bias) * mm
            index = tri[0].face.index
            face_wall[index] = min(face_wall.get(index, math.inf), distance_mm)
        wall = min(face_wall.values(), default=math.inf)
        thin_faces = sum(1 for d in face_wall.values() if d < min_wall_mm)

        # Overhangs: faces looking down, unless they sit on the bed.
        lowest = min((v.co.z for v in bm.verts), default=0.0)
        limit = -math.cos(math.radians(overhang_deg))
        contact = _BED_CONTACT_MM / mm
        overhangs = [
            f
            for f in bm.faces
            if f.normal.z < limit and min(v.co.z for v in f.verts) > lowest + contact
        ]
        area_bu = sum(f.calc_area() for f in overhangs)
        return {
            "thin_faces": thin_faces,
            "min_wall_mm": None if wall == math.inf else round(wall, 4),
            "overhang_faces": len(overhangs),
            "overhang_area_mm2": round(area_bu * mm**2, 4),
            "below_bed_mm": round(max(0.0, -lowest * mm), 4),
        }
    finally:
        bm.free()


def _topology_tool(args: dict):
    return json_result(mesh_topology(str(args["name"])))


def _print_check_tool(args: dict):
    return json_result(
        print_check(
            str(args["name"]),
            float(args.get("min_wall_mm", 0.8)),
            float(args.get("overhang_deg", 45.0)),
        )
    )


register_tool(
    Tool(
        name="mesh_topology",
        description=(
            "Check the evaluated mesh of an object (modifiers applied, world "
            "space): connected components, manifold flag, boundary edges, "
            "non-manifold edges (wire or 3+ faces), inverted normals "
            "(negative signed volume), vertex and face counts and volume in "
            "mm³. A printable part is one component, manifold, not inverted."
        ),
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        handler=_topology_tool,
        kind="read",
    )
)

register_tool(
    Tool(
        name="print_check",
        description=(
            "3D-print checks on the evaluated world-space mesh. thin_faces "
            "counts faces whose wall (distance from the face center inward "
            "to the next surface) is under min_wall_mm; min_wall_mm in the "
            "result is the thinnest wall found. overhang_faces and "
            "overhang_area_mm2 cover faces tilted past overhang_deg from "
            "vertical, ignoring faces resting on the bed (lowest Z). "
            "below_bed_mm is how far the object sinks under Z = 0. "
            "Distances are in mm; min_wall_mm defaults to 0.8 and "
            "overhang_deg to 45."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "min_wall_mm": {"type": "number"},
                "overhang_deg": {"type": "number"},
            },
            "required": ["name"],
        },
        handler=_print_check_tool,
        kind="read",
    )
)
