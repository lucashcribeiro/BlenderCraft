"""Minimal 3MF reader and writer (core spec, one mesh object per item).

Exports are always in millimeters: vertices are converted from Blender units
with ``mm_per_bu`` and the model declares ``unit="millimeter"``.
"""

import zipfile
from xml.etree import ElementTree as ET

import bmesh
import bpy
from mathutils import Matrix

from .tools.inspect import _evaluated_world_mesh

CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
_CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_MODEL_REL_TYPE = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
_MODEL_PATH = "3D/3dmodel.model"
_MODEL_CONTENT_TYPE = "application/vnd.ms-package.3dmanufacturing-3dmodel+xml"
_RELS_CONTENT_TYPE = "application/vnd.openxmlformats-package.relationships+xml"

DEFAULT_COLOR = "#CCCCCC"

# Millimeters per file unit, for the model's ``unit`` attribute.
UNIT_MM = {
    "micron": 0.001,
    "millimeter": 1.0,
    "centimeter": 10.0,
    "inch": 25.4,
    "foot": 304.8,
    "meter": 1000.0,
}


def _linear_to_srgb(c: float) -> float:
    c = min(max(c, 0.0), 1.0)
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def linear_to_hex(rgb) -> str:
    """Linear RGB (as Blender stores colors) to an sRGB ``#RRGGBB`` string."""
    return "#" + "".join(f"{round(_linear_to_srgb(c) * 255):02X}" for c in rgb[:3])


def hex_to_linear(value: str) -> tuple[float, float, float, float]:
    """``#RRGGBB`` or ``#RRGGBBAA`` (sRGB) to Blender linear RGBA."""
    digits = value.lstrip("#")
    if len(digits) not in (6, 8):
        raise ValueError(f"Bad 3MF color: {value!r}")
    channels = [int(digits[i : i + 2], 16) / 255 for i in range(0, len(digits), 2)]
    rgb = [_srgb_to_linear(c) for c in channels[:3]]
    return (*rgb, channels[3] if len(channels) == 4 else 1.0)


def _material_color(mat):
    """Linear RGB of a material: Principled base color, else viewport color."""
    if mat.use_nodes and mat.node_tree is not None:
        for node in mat.node_tree.nodes:
            if node.type == "BSDF_PRINCIPLED":
                base = node.inputs["Base Color"]
                # A texture or other link has no single color to export.
                if not base.is_linked:
                    return base.default_value
                break
    return mat.diffuse_color


def _object_color(obj) -> str:
    for slot in obj.material_slots:
        if slot.material is not None:
            return linear_to_hex(_material_color(slot.material))
    return DEFAULT_COLOR


def _triangulated_world_mesh(obj, mm_per_bu: float):
    """Vertices (mm) and triangles of the evaluated world-space mesh.

    Loose vertices are dropped; a mirrored transform has its winding fixed.
    """
    bm = _evaluated_world_mesh(obj)
    try:
        if obj.matrix_world.determinant() < 0:
            bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
        bmesh.ops.triangulate(bm, faces=bm.faces[:])
        remap: dict[int, int] = {}
        verts = []
        tris = []
        for face in bm.faces:
            tri = []
            for v in face.verts:
                if v.index not in remap:
                    remap[v.index] = len(verts)
                    verts.append(tuple(c * mm_per_bu for c in v.co))
                tri.append(remap[v.index])
            tris.append(tuple(tri))
        return verts, tris
    finally:
        bm.free()


def _num(value: float) -> str:
    return f"{value:.6f}"


def _xml_bytes(root: ET.Element) -> bytes:
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def export_3mf(path: str, objects: list, mm_per_bu: float) -> list[str]:
    """Write mesh objects to a 3MF file in millimeters.

    Meshes are evaluated (modifiers applied), triangulated, placed in world
    space and scaled by ``mm_per_bu``. Each object gets one base material
    whose color comes from its first material (sRGB), or ``#CCCCCC``.
    Objects without faces are skipped. Returns the names actually written.
    """
    model = ET.Element("model", {"xmlns": CORE_NS, "unit": "millimeter"})
    resources = ET.SubElement(model, "resources")
    basematerials = ET.SubElement(resources, "basematerials", id="1")
    build = ET.SubElement(model, "build")

    bpy.context.view_layer.update()
    written_names: list[str] = []
    for obj in objects:
        verts, tris = _triangulated_world_mesh(obj, mm_per_bu)
        if not tris:
            continue
        ET.SubElement(
            basematerials,
            "base",
            name=obj.name,
            displaycolor=_object_color(obj),
        )
        written = len(written_names)
        object_id = str(written + 2)  # id 1 is the basematerials group
        obj_el = ET.SubElement(
            resources,
            "object",
            id=object_id,
            type="model",
            name=obj.name,
            pid="1",
            pindex=str(written),
        )
        mesh_el = ET.SubElement(obj_el, "mesh")
        verts_el = ET.SubElement(mesh_el, "vertices")
        for x, y, z in verts:
            ET.SubElement(
                verts_el, "vertex", x=_num(x), y=_num(y), z=_num(z)
            )
        tris_el = ET.SubElement(mesh_el, "triangles")
        for a, b, c in tris:
            ET.SubElement(
                tris_el, "triangle", v1=str(a), v2=str(b), v3=str(c)
            )
        ET.SubElement(build, "item", objectid=object_id)
        written_names.append(obj.name)
    if not written_names:
        raise ValueError("No mesh with faces to export")

    content_types = ET.Element("Types", {"xmlns": _CONTENT_TYPES_NS})
    ET.SubElement(
        content_types,
        "Default",
        Extension="rels",
        ContentType=_RELS_CONTENT_TYPE,
    )
    ET.SubElement(
        content_types,
        "Default",
        Extension="model",
        ContentType=_MODEL_CONTENT_TYPE,
    )
    rels = ET.Element("Relationships", {"xmlns": _RELS_NS})
    ET.SubElement(
        rels,
        "Relationship",
        Target=f"/{_MODEL_PATH}",
        Id="rel0",
        Type=_MODEL_REL_TYPE,
    )

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _xml_bytes(content_types))
        zf.writestr("_rels/.rels", _xml_bytes(rels))
        zf.writestr(_MODEL_PATH, _xml_bytes(model))
    return written_names


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(el: ET.Element, name: str):
    return [c for c in el.iter() if _local(c.tag) == name]


def _model_path(zf: zipfile.ZipFile) -> str:
    """Model part named by the package relationships, or the usual path."""
    if "_rels/.rels" in zf.namelist():
        rels = ET.fromstring(zf.read("_rels/.rels"))
        for rel in rels:
            if rel.get("Type") == _MODEL_REL_TYPE and rel.get("Target"):
                return rel.get("Target").lstrip("/")
    return _MODEL_PATH


def _item_matrix(transform: str | None, scale: float) -> Matrix:
    """Blender matrix (BU) of a 3MF transform (row vectors, file units)."""
    if not transform:
        return Matrix.Identity(4)
    v = [float(x) for x in transform.split()]
    if len(v) != 12:
        raise ValueError(f"Bad 3MF transform: {transform!r}")
    rows = Matrix(
        (
            (v[0], v[1], v[2], 0.0),
            (v[3], v[4], v[5], 0.0),
            (v[6], v[7], v[8], 0.0),
            (v[9] * scale, v[10] * scale, v[11] * scale, 1.0),
        )
    )
    return rows.transposed()


def _material_for(color_hex: str, cache: dict):
    if color_hex not in cache:
        mat = bpy.data.materials.new(f"Color_{color_hex.lstrip('#').upper()}")
        mat.diffuse_color = hex_to_linear(color_hex)
        tree = mat.node_tree
        bsdf = tree.nodes.get("Principled BSDF") if tree else None
        if bsdf is not None:
            bsdf.inputs["Base Color"].default_value = mat.diffuse_color
        cache[color_hex] = mat
    return cache[color_hex]


def import_3mf(path: str, mm_per_bu: float) -> list:
    """Create one Blender object per mesh object placed by the build items.

    The file's ``unit`` is converted to Blender units, item transforms are
    applied, and a material is created from the base material color. Objects
    no build item references are imported at the origin.
    """
    with zipfile.ZipFile(path) as zf:
        root = ET.fromstring(zf.read(_model_path(zf)))

    unit = root.get("unit", "millimeter")
    if unit not in UNIT_MM:
        raise ValueError(f"Unknown 3MF unit: {unit!r}")
    scale = UNIT_MM[unit] / mm_per_bu

    groups: dict[str, list[str]] = {}
    for group in _children(root, "basematerials"):
        groups[group.get("id")] = [
            b.get("displaycolor") for b in group if _local(b.tag) == "base"
        ]

    meshes = {}
    for obj_el in _children(root, "object"):
        mesh_el = next((c for c in obj_el if _local(c.tag) == "mesh"), None)
        if mesh_el is None:
            continue
        verts = [
            (float(v.get("x")) * scale, float(v.get("y")) * scale, float(v.get("z")) * scale)
            for v in _children(mesh_el, "vertex")
        ]
        tri_els = _children(mesh_el, "triangle")
        tris = [(int(t.get("v1")), int(t.get("v2")), int(t.get("v3"))) for t in tri_els]
        color = _declared_color(obj_el, tri_els, groups)
        meshes[obj_el.get("id")] = (obj_el.get("name"), verts, tris, color)

    placements = [
        (item.get("objectid"), item.get("transform"))
        for item in _children(root, "item")
        if item.get("objectid") in meshes
    ]
    placed = {oid for oid, _ in placements}
    placements += [(oid, None) for oid in meshes if oid not in placed]

    materials: dict = {}
    created = []
    for index, (oid, transform) in enumerate(placements):
        name, verts, tris, color = meshes[oid]
        name = name or f"Object{oid}"
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(verts, [], tris)
        mesh.update()
        if color:
            mesh.materials.append(_material_for(color, materials))
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.collection.objects.link(obj)
        obj.matrix_world = _item_matrix(transform, scale)
        created.append(obj)
    return created


def _declared_color(obj_el, tri_els, groups) -> str | None:
    """Display color from the object's pid/pindex or its first triangle."""
    pid, index = obj_el.get("pid"), obj_el.get("pindex")
    if pid is None and tri_els:
        pid, index = tri_els[0].get("pid"), tri_els[0].get("p1")
    if pid is None or index is None:
        return None
    colors = groups.get(pid)
    if not colors or int(index) >= len(colors):
        return None
    return colors[int(index)]
