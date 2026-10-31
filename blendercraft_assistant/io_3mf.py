"""3MF reader and writer.

Exports are always in millimeters: vertices are converted from Blender units
with ``mm_per_bu`` and the model declares ``unit="millimeter"``. Two layouts:
``export_3mf`` follows the core spec, one mesh object per item;
``export_3mf_pieces`` writes one object per print piece the way Bambu Studio
does (components into 3D/Objects/*.model plus Metadata/model_settings.config
naming the parts and their extruders).

The reader also understands the Production extension (components that point
into other model parts of the package with ``p:path``, as Bambu Studio and
PrusaSlicer write them) and, best effort, Bambu Studio's project metadata for
part names and filament colors. Model parts are streamed, so files with
millions of triangles import without building an XML tree.
"""

import json
import posixpath
import re
import uuid
import zipfile
from array import array
from xml.etree import ElementTree as ET

import bmesh
import bpy
import numpy as np
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


def export_3mf(
    path: str, objects: list, mm_per_bu: float, meshes: dict | None = None
) -> list[str]:
    """Write mesh objects to a 3MF file in millimeters.

    Meshes are evaluated (modifiers applied), triangulated, placed in world
    space and scaled by ``mm_per_bu``. Each object gets one base material
    whose color comes from its first material (sRGB), or ``#CCCCCC``.
    Objects without faces are skipped. Returns the names actually written.
    The result is plain core-spec 3MF, which Bambu Studio and PrusaSlicer open
    directly (named objects, one color each, items marked printable).
    ``meshes`` optionally holds already evaluated meshes (name -> (vertices in
    mm, triangles)), as made by export_check.mesh_cache.
    """
    model = ET.Element(
        "model",
        {
            "xmlns": CORE_NS,
            "unit": "millimeter",
            "{http://www.w3.org/XML/1998/namespace}lang": "en-US",
        },
    )
    # Slicers (Bambu Studio, PrusaSlicer) show the producing application;
    # metadata has to come before resources in the schema.
    ET.SubElement(model, "metadata", name="Application").text = "BlenderCraft"
    resources = ET.SubElement(model, "resources")
    basematerials = ET.SubElement(resources, "basematerials", id="1")
    build = ET.SubElement(model, "build")

    bpy.context.view_layer.update()
    written_names: list[str] = []
    for obj in objects:
        if meshes is not None and obj.name in meshes:
            verts, tris = meshes[obj.name]
        else:
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
        ET.SubElement(build, "item", objectid=object_id, printable="1")
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


# --- Export, Bambu Studio layout (one object per print piece) ----------------

PROD_NS = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
BAMBU_NS = "http://schemas.bambulab.com/package/2021"
_OBJECTS_DIR = "3D/Objects"
_MODEL_RELS_PATH = "3D/_rels/3dmodel.model.rels"


def _bambu_model() -> ET.Element:
    """A ``<model>`` root with the namespaces Bambu Studio writes."""
    model = ET.Element(
        "model",
        {
            "unit": "millimeter",
            "{http://www.w3.org/XML/1998/namespace}lang": "en-US",
            "xmlns": CORE_NS,
            "xmlns:BambuStudio": BAMBU_NS,
            "xmlns:p": PROD_NS,
            "requiredextensions": "p",
        },
    )
    return model


def _uuid() -> str:
    return str(uuid.uuid4())


def _translation(offset) -> str:
    """3MF transform (row vectors) of a pure translation."""
    x, y, z = (_num(float(c)) for c in offset)
    return f"1 0 0 0 1 0 0 0 1 {x} {y} {z}"


def _bambu_matrix(offset) -> str:
    """Bambu's ``matrix`` metadata (row-major 4x4) of a pure translation."""
    x, y, z = (_num(float(c)) for c in offset)
    return f"1 0 0 {x} 0 1 0 {y} 0 0 1 {z} 0 0 0 1"


def _add_mesh(parent: ET.Element, verts, tris) -> None:
    mesh_el = ET.SubElement(parent, "mesh")
    verts_el = ET.SubElement(mesh_el, "vertices")
    for x, y, z in verts:
        ET.SubElement(verts_el, "vertex", x=_num(x), y=_num(y), z=_num(z))
    tris_el = ET.SubElement(mesh_el, "triangles")
    for a, b, c in tris:
        ET.SubElement(tris_el, "triangle", v1=str(a), v2=str(b), v3=str(c))


def _meta(parent: ET.Element, key: str, value) -> None:
    ET.SubElement(parent, "metadata", key=key, value=str(value))


def export_3mf_pieces(
    path: str, pieces: list, mm_per_bu: float, meshes: dict | None = None
) -> list[dict]:
    """Write one 3MF object per print piece, laid out like Bambu Studio does.

    ``pieces`` is ``[(name, [objects])]``. Each piece becomes one object of
    3D/3dmodel.model whose ``<components>`` point (Production extension,
    ``p:path``) to one mesh object per part in 3D/Objects/object_<n>.model;
    Metadata/model_settings.config names the object and its parts and gives
    each part an extruder. Extruders follow the distinct part colours in order
    of first appearance (all parts on extruder 1 when they share a colour).

    Geometry is the evaluated world mesh in millimeters. As in Bambu's own
    files each part mesh is centred on its bounding box, the component
    transform (also the part's ``matrix``) moves it into the piece, and the
    build item moves the piece (centred on its box) to its world position, so
    the assembled positions are kept. Parts without faces are skipped, and so
    are pieces left empty. Returns ``[{name, parts: [{name, extruder,
    color}]}]`` of what was written.
    """
    bpy.context.view_layer.update()
    planned = []
    for name, objs in pieces:
        parts = []
        for obj in objs:
            if meshes is not None and obj.name in meshes:
                verts, tris = meshes[obj.name]
            else:
                verts, tris = _triangulated_world_mesh(obj, mm_per_bu)
            if tris:
                coords = np.asarray(verts, dtype=np.float64).reshape(-1, 3)
                parts.append((obj, coords, tris, _object_color(obj)))
        if parts:
            planned.append((name, parts))
    if not planned:
        raise ValueError("No mesh with faces to export")

    extruders: dict[str, int] = {}
    for _, parts in planned:
        for *_, color in parts:
            extruders.setdefault(color, len(extruders) + 1)

    model = _bambu_model()
    # Metadata has to come before resources in the schema.
    ET.SubElement(model, "metadata", name="Application").text = "BlenderCraft"
    ET.SubElement(model, "metadata", name="BambuStudio:3mfVersion").text = "1"
    resources = ET.SubElement(model, "resources")
    build = ET.SubElement(model, "build", {"p:UUID": _uuid()})
    config = ET.Element("config")
    instances = []

    sub_models: list[tuple[str, ET.Element]] = []
    written = []
    next_id = 1
    for index, (name, parts) in enumerate(planned, start=1):
        part_path = f"{_OBJECTS_DIR}/object_{index}.model"
        lo = np.min([coords.min(axis=0) for _, coords, _, _ in parts], axis=0)
        hi = np.max([coords.max(axis=0) for _, coords, _, _ in parts], axis=0)
        center = (lo + hi) / 2

        sub = _bambu_model()
        ET.SubElement(sub, "metadata", name="BambuStudio:3mfVersion").text = "1"
        sub_resources = ET.SubElement(sub, "resources")
        group_id = next_id
        next_id += 1
        group = ET.SubElement(sub_resources, "basematerials", id=str(group_id))
        components = []
        for position, (obj, coords, tris, color) in enumerate(parts):
            part_id = next_id
            next_id += 1
            ET.SubElement(group, "base", name=obj.name, displaycolor=color)
            part_center = (coords.min(axis=0) + coords.max(axis=0)) / 2
            part_el = ET.SubElement(
                sub_resources,
                "object",
                {
                    "id": str(part_id),
                    "p:UUID": _uuid(),
                    "type": "model",
                    "name": obj.name,
                    "pid": str(group_id),
                    "pindex": str(position),
                },
            )
            _add_mesh(part_el, (coords - part_center).tolist(), tris)
            components.append((part_id, part_center - center, obj, tris, color))
        sub_models.append((part_path, sub))

        object_id = next_id
        next_id += 1
        top = ET.SubElement(
            resources,
            "object",
            {"id": str(object_id), "p:UUID": _uuid(), "type": "model", "name": name},
        )
        comps_el = ET.SubElement(top, "components")
        for part_id, offset, *_ in components:
            ET.SubElement(
                comps_el,
                "component",
                {
                    "p:path": f"/{part_path}",
                    "objectid": str(part_id),
                    "p:UUID": _uuid(),
                    "transform": _translation(offset),
                },
            )
        item_transform = _translation(center)
        ET.SubElement(
            build,
            "item",
            {
                "objectid": str(object_id),
                "p:UUID": _uuid(),
                "transform": item_transform,
                "printable": "1",
            },
        )

        first_extruder = extruders[components[0][4]]
        obj_cfg = ET.SubElement(config, "object", id=str(object_id))
        _meta(obj_cfg, "name", name)
        _meta(obj_cfg, "extruder", first_extruder)
        ET.SubElement(
            obj_cfg, "metadata", face_count=str(sum(len(c[3]) for c in components))
        )
        summary = []
        for part_id, offset, obj, tris, color in components:
            part_cfg = ET.SubElement(
                obj_cfg, "part", id=str(part_id), subtype="normal_part"
            )
            _meta(part_cfg, "name", obj.name)
            _meta(part_cfg, "matrix", _bambu_matrix(offset))
            _meta(part_cfg, "extruder", extruders[color])
            ET.SubElement(
                part_cfg,
                "mesh_stat",
                face_count=str(len(tris)),
                edges_fixed="0",
                degenerate_facets="0",
                facets_removed="0",
                facets_reversed="0",
                backwards_edges="0",
            )
            summary.append(
                {"name": obj.name, "extruder": extruders[color], "color": color}
            )
        instances.append((object_id, item_transform))
        written.append({"name": name, "parts": summary})

    plate = ET.SubElement(config, "plate")
    _meta(plate, "plater_id", 1)
    _meta(plate, "plater_name", "")
    _meta(plate, "locked", "false")
    for position, (object_id, _) in enumerate(instances):
        instance = ET.SubElement(plate, "model_instance")
        _meta(instance, "object_id", object_id)
        _meta(instance, "instance_id", 0)
        _meta(instance, "identify_id", 100 + position)
    assemble = ET.SubElement(config, "assemble")
    for object_id, transform in instances:
        ET.SubElement(
            assemble,
            "assemble_item",
            object_id=str(object_id),
            instance_id="0",
            transform=transform,
            offset="0 0 0",
        )

    content_types = ET.Element("Types", {"xmlns": _CONTENT_TYPES_NS})
    ET.SubElement(
        content_types, "Default", Extension="rels", ContentType=_RELS_CONTENT_TYPE
    )
    ET.SubElement(
        content_types, "Default", Extension="model", ContentType=_MODEL_CONTENT_TYPE
    )
    rels = ET.Element("Relationships", {"xmlns": _RELS_NS})
    ET.SubElement(
        rels, "Relationship", Target=f"/{_MODEL_PATH}", Id="rel-1", Type=_MODEL_REL_TYPE
    )
    model_rels = ET.Element("Relationships", {"xmlns": _RELS_NS})
    for position, (part_path, _) in enumerate(sub_models, start=1):
        ET.SubElement(
            model_rels,
            "Relationship",
            Target=f"/{part_path}",
            Id=f"rel-{position}",
            Type=_MODEL_REL_TYPE,
        )

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _xml_bytes(content_types))
        zf.writestr("_rels/.rels", _xml_bytes(rels))
        zf.writestr(_MODEL_PATH, _xml_bytes(model))
        zf.writestr(_MODEL_RELS_PATH, _xml_bytes(model_rels))
        for part_path, sub in sub_models:
            zf.writestr(part_path, _xml_bytes(sub))
        zf.writestr(_BAMBU_SETTINGS, _xml_bytes(config))
    return written


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


# --- Import -------------------------------------------------------------

_PROD_PATH = "{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}path"
_HEX_COLOR = re.compile(r"^#?([0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})$")
_BAMBU_SETTINGS = "Metadata/model_settings.config"
_BAMBU_PROJECT = "Metadata/project_settings.config"
_MAX_DEPTH = 64


class _Obj:
    """One ``<object>`` of a model part (a mesh, or a list of components)."""

    __slots__ = (
        "id", "name", "type", "pid", "pindex", "coords", "tris", "first_tri",
        "components",
    )

    def __init__(self, attrib):
        self.id = attrib.get("id")
        self.name = attrib.get("name")
        self.type = attrib.get("type") or "model"
        self.pid = attrib.get("pid")
        self.pindex = attrib.get("pindex")
        self.coords = None  # array('f') in BU once a <mesh> is seen
        self.tris = None  # array('I'), three indices per triangle
        self.first_tri = None  # (pid, p1) of the first triangle
        self.components = []  # [(p:path or None, objectid, transform)]


class _Part:
    """A parsed model part: objects, build items and base material groups."""

    def __init__(self, path):
        self.path = path
        self.scale = 1.0  # Blender units per file unit
        self.objects: dict[str, _Obj] = {}
        self.items: list[tuple[str, str | None]] = []
        self.groups: dict[str, list[str | None]] = {}


def _parse_part(stream, path: str, mm_per_bu: float) -> _Part:
    """Stream one model part into flat arrays (no XML tree is kept).

    Vertices are converted to Blender units as they are read; the children of
    ``<vertices>``/``<triangles>`` are dropped as soon as they are consumed so
    memory stays flat for very large meshes.
    """
    part = _Part(path)
    scale = 1.0
    local_names: dict[str, str] = {}
    obj = coords = tris = container = group = None
    try:
        for event, el in ET.iterparse(stream, events=("start", "end")):
            tag = el.tag
            name = local_names.get(tag)
            if name is None:
                name = local_names[tag] = tag.rsplit("}", 1)[-1]
            if event == "start":
                if name == "vertices" or name == "triangles":
                    container = el
                elif name == "object":
                    obj = _Obj(el.attrib)
                    part.objects[obj.id] = obj
                elif name == "mesh":
                    if obj is not None:
                        obj.coords = coords = array("f")
                        obj.tris = tris = array("I")
                elif name == "basematerials":
                    group = part.groups.setdefault(el.get("id"), [])
                elif name == "model":
                    unit = el.get("unit", "millimeter")
                    if unit not in UNIT_MM:
                        raise ValueError(f"Unknown 3MF unit: {unit!r}")
                    part.scale = scale = UNIT_MM[unit] / mm_per_bu
                continue
            if name == "vertex":
                a = el.attrib
                coords.extend(
                    (
                        float(a["x"]) * scale,
                        float(a["y"]) * scale,
                        float(a["z"]) * scale,
                    )
                )
                container.clear()
            elif name == "triangle":
                a = el.attrib
                if obj.first_tri is None:
                    obj.first_tri = (a.get("pid"), a.get("p1"))
                tris.extend((int(a["v1"]), int(a["v2"]), int(a["v3"])))
                container.clear()
            elif name == "component":
                if obj is not None:
                    a = el.attrib
                    obj.components.append(
                        (a.get(_PROD_PATH) or a.get("path"), a.get("objectid"), a.get("transform"))
                    )
            elif name == "item":
                part.items.append((el.get("objectid"), el.get("transform")))
            elif name == "base":
                if group is not None:
                    group.append(el.get("displaycolor"))
            elif name == "basematerials":
                group = None
            elif name == "mesh":
                coords = tris = None
            elif name == "object":
                obj = None
    except (KeyError, OverflowError) as exc:
        raise ValueError(f"Malformed 3MF model part {path!r}: {exc!r}") from exc
    return part


class _Package:
    """Lazy access to the model parts of a 3MF package."""

    def __init__(self, zf: zipfile.ZipFile, mm_per_bu: float):
        self.zf = zf
        self.mm_per_bu = mm_per_bu
        self._lower = {n.lower(): n for n in zf.namelist()}
        self._parts: dict[str, _Part] = {}

    def resolve(self, path: str) -> str | None:
        """Zip member name for a package part path, or None if absent."""
        name = posixpath.normpath(path.lstrip("/"))
        if name in self._lower.values():
            return name
        return self._lower.get(name.lower())

    def part(self, path: str) -> _Part:
        if path not in self._parts:
            with self.zf.open(path) as stream:
                self._parts[path] = _parse_part(stream, path, self.mm_per_bu)
        return self._parts[path]

    def related(self, part_path: str) -> list[str]:
        """Model parts the given part's relationships point to."""
        folder, base = posixpath.split(part_path)
        rels_name = self.resolve(posixpath.join(folder, "_rels", base + ".rels"))
        if rels_name is None:
            return []
        try:
            rels = ET.fromstring(self.zf.read(rels_name))
        except ET.ParseError:
            return []
        found = []
        for rel in rels:
            target = rel.get("Target")
            if rel.get("Type") != _MODEL_REL_TYPE or not target:
                continue
            if not target.startswith("/"):
                target = posixpath.join(folder, target)
            name = self.resolve(target)
            if name is not None:
                found.append(name)
        return found


class _Leaf:
    """A mesh object reached through the build, with its composed matrix."""

    __slots__ = ("part", "obj", "matrix", "top", "label", "index", "ncomp")

    def __init__(self, part, obj, matrix, top, label, index, ncomp):
        self.part = part
        self.obj = obj
        self.matrix = matrix
        self.top = top  # id of the build item's (or orphan's) object
        self.label = label  # name of the nearest named container
        self.index = index  # top-level component this leaf comes from
        self.ncomp = ncomp  # number of top-level components


def _component_target(pkg: _Package, part: _Part, comp, problems: list):
    """Part holding the object a component points to, or None."""
    path, oid, _ = comp
    if path:
        name = pkg.resolve(path)
        if name is None:
            problems.append(f"component part {path!r} is not in the package")
            return None
        return pkg.part(name)
    if oid in part.objects:
        return part
    for name in pkg.related(part.path):
        sub = pkg.part(name)
        if oid in sub.objects:
            return sub
    problems.append(f"component object {oid} not found from {part.path}")
    return None


def _walk(pkg, part, oid, matrix, ctx, leaves, problems, stack):
    """Collect mesh leaves below ``oid``; matrices compose as in 3MF.

    3MF uses row vectors, so a vertex goes through its component transform
    first and the parent's (or the build item's) transform after it; with
    Blender's column matrices that is ``item @ component @ ...``.
    """
    obj = part.objects.get(oid)
    if obj is None:
        problems.append(f"object {oid} not found in {part.path}")
        return
    if obj.type != "model" or len(stack) > _MAX_DEPTH:
        return
    key = (part.path, oid)
    if key in stack:
        problems.append(f"component cycle through object {oid} in {part.path}")
        return
    top, label, index, ncomp = ctx
    label = obj.name or label
    if obj.coords is not None:
        if obj.tris:
            leaves.append(_Leaf(part, obj, matrix, top, label, index, ncomp))
        return
    inner = stack | {key}
    for position, comp in enumerate(obj.components):
        target = _component_target(pkg, part, comp, problems)
        if target is None:
            continue
        child = (
            (top, label, position, len(obj.components))
            if not stack
            else (top, label, index, ncomp)
        )
        _walk(
            pkg,
            target,
            comp[1],
            matrix @ _item_matrix(comp[2], part.scale),
            child,
            leaves,
            problems,
            inner,
        )


class _BambuPart:
    __slots__ = ("name", "extruder", "subtype")

    def __init__(self, name, extruder, subtype):
        self.name, self.extruder, self.subtype = name, extruder, subtype


class _BambuObject:
    __slots__ = ("name", "extruder", "parts", "order")

    def __init__(self, name, extruder):
        self.name, self.extruder = name, extruder
        self.parts: dict[str, _BambuPart] = {}
        self.order: list[_BambuPart] = []


def _positive_int(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _read_bambu_objects(zf: zipfile.ZipFile) -> dict[str, _BambuObject]:
    """Object/part names and extruders from Bambu's model_settings.config."""
    if _BAMBU_SETTINGS not in zf.namelist():
        return {}
    try:
        root = ET.fromstring(zf.read(_BAMBU_SETTINGS))
    except (ET.ParseError, ValueError, KeyError):
        return {}

    def meta(el) -> dict[str, str]:
        return {
            m.get("key"): m.get("value")
            for m in el.findall("metadata")
            if m.get("key") is not None
        }

    objects = {}
    for el in root.findall("object"):
        values = meta(el)
        bobj = _BambuObject(values.get("name"), _positive_int(values.get("extruder")))
        for part_el in el.findall("part"):
            pv = meta(part_el)
            bpart = _BambuPart(
                pv.get("name"), _positive_int(pv.get("extruder")), part_el.get("subtype")
            )
            bobj.order.append(bpart)
            if part_el.get("id") is not None:
                bobj.parts[part_el.get("id")] = bpart
        if el.get("id") is not None:
            objects[el.get("id")] = bobj
    return objects


def _read_bambu_colors(zf: zipfile.ZipFile) -> list[str | None]:
    """``filament_colour`` of project_settings.config, bad entries as None."""
    if _BAMBU_PROJECT not in zf.namelist():
        return []
    try:
        data = json.loads(zf.read(_BAMBU_PROJECT))
        colors = data.get("filament_colour")
    except (ValueError, AttributeError, KeyError):
        return []
    if not isinstance(colors, list):
        return []
    return [
        "#" + c.lstrip("#").upper() if isinstance(c, str) and _HEX_COLOR.match(c) else None
        for c in colors
    ]


def _declared_color(obj: _Obj, groups) -> str | None:
    """Display color from the object's pid/pindex or its first triangle."""
    pid, index = obj.pid, obj.pindex
    if pid is None and obj.first_tri is not None:
        pid, index = obj.first_tri
    if pid is None or index is None:
        return None
    colors = groups.get(pid)
    try:
        color = colors[int(index)] if colors else None
    except (ValueError, IndexError):
        return None
    return color if color and _HEX_COLOR.match(color) else None


def _build_mesh(name: str, obj: _Obj):
    """Mesh datablock from the flat arrays, filled with ``foreach_set``."""
    if not len(obj.coords) or not len(obj.tris):
        raise ValueError(f"3MF object {obj.id} has no vertices or triangles")
    coords = np.frombuffer(obj.coords, dtype=np.float32)
    corners = np.frombuffer(obj.tris, dtype=np.dtype(f"u{obj.tris.itemsize}"))
    vert_count = coords.size // 3
    if int(corners.max()) >= vert_count:
        raise ValueError(
            f"3MF object {obj.id} has triangle indices outside its vertex list"
        )
    mesh = bpy.data.meshes.new(name)
    try:
        mesh.vertices.add(vert_count)
        mesh.loops.add(corners.size)
        mesh.polygons.add(corners.size // 3)
        mesh.vertices.foreach_set("co", coords)
        mesh.polygons.foreach_set(
            "loop_start", np.arange(0, corners.size, 3, dtype=np.int32)
        )
        mesh.polygons.foreach_set("vertices", corners.astype(np.int32))
        mesh.shade_flat()
        mesh.update(calc_edges=True)
    except Exception:
        bpy.data.meshes.remove(mesh)
        raise
    return mesh


def _leaf_name(leaf: _Leaf, bambu_obj, part_info) -> str:
    return (
        (part_info.name if part_info else None)
        or leaf.obj.name
        or leaf.label
        or (bambu_obj.name if bambu_obj else None)
        or f"Object{leaf.obj.id}"
    )


def _bambu_part(bambu_obj, leaf: _Leaf):
    """Bambu part record of a leaf: by position when counts agree, else id."""
    if bambu_obj is None:
        return None
    if leaf.index is not None and len(bambu_obj.order) == leaf.ncomp:
        return bambu_obj.order[leaf.index]
    return bambu_obj.parts.get(leaf.obj.id)


def import_3mf(path: str, mm_per_bu: float, warnings: list | None = None) -> list:
    """Create one Blender object per mesh reached from the build items.

    Handles core-spec files and the Production extension: components are
    resolved across model parts (``p:path``, or the part relationships) and
    nested; each part's ``unit`` is converted to Blender units and transforms
    compose as component then item. Only ``type="model"`` objects are
    imported; root-model objects nothing references are imported at the
    origin. Materials come from base material colors, else (Bambu Studio
    files) from the part's extruder in ``filament_colour``; Bambu part names
    are used when present. Unresolvable references are skipped and described
    in ``warnings`` (if given); an error is raised only when nothing at all
    could be imported.
    """
    problems: list[str] = []
    with zipfile.ZipFile(path) as zf:
        pkg = _Package(zf, mm_per_bu)
        root_name = pkg.resolve(_model_path(zf))
        if root_name is None:
            raise ValueError("The 3MF file has no 3D model part")
        root = pkg.part(root_name)
        bambu_objects = _read_bambu_objects(zf)
        bambu_colors = _read_bambu_colors(zf)

        leaves: list[_Leaf] = []
        referenced = set()
        for oid, transform in root.items:
            referenced.add(oid)
            matrix = _item_matrix(transform, root.scale)
            _walk(pkg, root, oid, matrix, (oid, None, None, 0), leaves, problems, frozenset())
        for obj in root.objects.values():
            for comp_path, comp_oid, _ in obj.components:
                if not comp_path or pkg.resolve(comp_path) == root_name:
                    referenced.add(comp_oid)
        for oid, obj in root.objects.items():
            if oid not in referenced:
                _walk(
                    pkg, root, oid, Matrix.Identity(4), (oid, None, None, 0),
                    leaves, problems, frozenset(),
                )
        if warnings is not None:
            warnings.extend(problems)

        wanted = []
        for leaf in leaves:
            bambu_obj = bambu_objects.get(leaf.top)
            part_info = _bambu_part(bambu_obj, leaf)
            if part_info and part_info.subtype not in (None, "normal_part"):
                continue  # negative parts, modifiers, support blockers
            color = _declared_color(leaf.obj, leaf.part.groups)
            if color is None:
                extruder = (part_info.extruder if part_info else None) or (
                    bambu_obj.extruder if bambu_obj else None
                )
                if extruder and extruder <= len(bambu_colors):
                    color = bambu_colors[extruder - 1]
            wanted.append((leaf, _leaf_name(leaf, bambu_obj, part_info), color))
        if not wanted:
            detail = "; ".join(problems[:3]) or "no model-type object with a mesh"
            raise ValueError(f"No mesh objects found in the 3MF file ({detail})")

        meshes = []
        cache: dict = {}
        try:
            for leaf, name, _ in wanted:
                key = (leaf.part.path, leaf.obj.id)
                if key in cache:
                    mesh = cache[key].copy()  # instances stay independent
                    mesh.name = name
                    mesh.materials.clear()
                else:
                    mesh = cache[key] = _build_mesh(name, leaf.obj)
                meshes.append(mesh)
        except Exception:
            for mesh in cache.values():
                bpy.data.meshes.remove(mesh)
            for mesh in meshes:
                if mesh.name in bpy.data.meshes:
                    bpy.data.meshes.remove(mesh)
            raise

    materials: dict = {}
    created = []
    for (leaf, name, color), mesh in zip(wanted, meshes):
        if color:
            mesh.materials.append(_material_for(color, materials))
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.collection.objects.link(obj)
        obj.matrix_world = leaf.matrix
        created.append(obj)
    return created
