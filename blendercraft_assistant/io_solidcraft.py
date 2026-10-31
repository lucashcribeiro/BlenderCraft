"""SolidCraft project (.sc) reader.

A .sc file is a ZIP holding ``container.json`` (``{"format":
"solidcraft-container/1"}``), ``document.json`` and ``blobs/<sha256>.mesh``
files. The layout below was inferred from files saved by SolidCraft; the
reader is defensive about everything it does not need.

``document.json``: ``units`` (``mm``, ``cm``...), ``components`` (id, name,
parent, identification_color_index), ``occurrences`` (id, component, parent,
4x4 ``transform``, rows of a column-vector matrix with the translation in the
last column), ``features`` (``mesh_import`` features whose ``meshes`` entries
carry ``name`` and ``mesh_blob``) and ``visibility`` (``hidden_bodies``,
``hidden_occurrences``).

SCMESH01 blob, all little-endian::

    8 bytes   ASCII "SCMESH01"
    u64       n_coords   number of f64 values (3 per vertex)
    u64       n_indices  number of u32 values (3 per triangle)
    f64[n_coords]   x, y, z of every vertex, in document units
    u32[n_indices]  triangle corner indices into the vertices

Each mesh becomes one Blender object named like the SolidCraft body, placed
with the world matrix of its component's occurrence, in a collection named
after the document and tinted by the component's identification color.
"""

import json
import sys
import zipfile
from array import array

import bpy
from mathutils import Matrix

MAGIC = b"SCMESH01"
_HEADER = 24
CONTAINER_PREFIX = "solidcraft-container/"

# Millimeters per document unit.
UNIT_MM = {
    "um": 0.001,
    "mm": 1.0,
    "cm": 10.0,
    "m": 1000.0,
    "in": 25.4,
    "ft": 304.8,
}

# sRGB identification colors, indexed by identification_color_index (wraps).
PALETTE = (
    "#4E79A7", "#F28E2B", "#E15759", "#76B7B2", "#59A14F", "#EDC948",
    "#B07AA1", "#FF9DA7", "#9C755F", "#86BCB6", "#D37295", "#8CD17D",
)


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def palette_color(index: int) -> tuple[float, float, float, float]:
    """Linear RGBA of the identification color ``index`` (wraps around)."""
    digits = PALETTE[int(index) % len(PALETTE)].lstrip("#")
    rgb = [_srgb_to_linear(int(digits[i : i + 2], 16) / 255) for i in (0, 2, 4)]
    return (*rgb, 1.0)


def parse_mesh(data) -> tuple[array, array]:
    """Decode an SCMESH01 blob into (f64 positions, u32 indices) arrays."""
    view = memoryview(data)
    if len(view) < _HEADER or bytes(view[:8]) != MAGIC:
        raise ValueError("not an SCMESH01 mesh (bad magic)")
    n_coords = int.from_bytes(view[8:16], "little")
    n_indices = int.from_bytes(view[16:24], "little")
    if n_coords % 3 or n_indices % 3:
        raise ValueError("SCMESH01 counts are not multiples of 3")
    end_coords = _HEADER + n_coords * 8
    end = end_coords + n_indices * 4
    if len(view) < end:
        raise ValueError("SCMESH01 data is truncated")
    positions = array("d")
    positions.frombytes(view[_HEADER:end_coords])
    indices = array("I")
    indices.frombytes(view[end_coords:end])
    if sys.byteorder == "big":
        positions.byteswap()
        indices.byteswap()
    if indices and max(indices) >= n_coords // 3:
        raise ValueError("SCMESH01 triangle index out of range")
    return positions, indices


def _matrix(value) -> Matrix:
    """4x4 Matrix from nested rows or 16 flat values (identity when absent)."""
    if not value:
        return Matrix.Identity(4)
    if isinstance(value[0], (list, tuple)):
        rows = [list(map(float, r)) for r in value]
    else:
        flat = list(map(float, value))
        rows = [flat[i : i + 4] for i in range(0, 16, 4)]
    if len(rows) != 4 or any(len(r) != 4 for r in rows):
        raise ValueError("SolidCraft transform is not a 4x4 matrix")
    matrix = Matrix(rows)
    # Column-vector convention (translation in the last column) is the norm;
    # a matrix that only fits the row-vector form is transposed.
    last_row_ok = list(matrix[3]) == [0.0, 0.0, 0.0, 1.0]
    last_col_ok = [matrix[i][3] for i in range(3)] == [0.0, 0.0, 0.0]
    if not last_row_ok and last_col_ok:
        matrix.transpose()
    return matrix


def _read_document(zf: zipfile.ZipFile) -> dict:
    try:
        container = json.loads(zf.read("container.json"))
    except KeyError:
        container = None
    except ValueError as exc:
        raise ValueError(f"Not a SolidCraft file: bad container.json ({exc})") from exc
    fmt = container.get("format") if isinstance(container, dict) else None
    if not isinstance(fmt, str) or not fmt.startswith(CONTAINER_PREFIX):
        raise ValueError(f"Not a SolidCraft file: container format {fmt!r}")
    try:
        document = json.loads(zf.read("document.json"))
    except KeyError:
        raise ValueError("SolidCraft file has no document.json") from None
    except ValueError as exc:
        raise ValueError(f"SolidCraft document.json is not valid JSON ({exc})") from exc
    if not isinstance(document, dict):
        raise ValueError("SolidCraft document.json is not an object")
    return document


def _inline_mesh(entry) -> tuple[array, array] | None:
    """Positions/triangles stored inline in document.json (no blob)."""
    positions, triangles = entry.get("positions"), entry.get("triangles")
    if not positions or not triangles:
        return None

    def flat(values):
        out = []
        for v in values:
            out.extend(v if isinstance(v, (list, tuple)) else [v])
        return out

    coords = array("d", map(float, flat(positions)))
    indices = array("I", map(int, flat(triangles)))
    if len(coords) % 3 or len(indices) % 3 or max(indices) >= len(coords) // 3:
        raise ValueError("inline SolidCraft mesh is malformed")
    return coords, indices


class _Body:
    __slots__ = ("group", "name", "positions", "indices", "world", "color_index", "hidden")

    def __init__(self, group, name, positions, indices, world, color_index, hidden):
        self.group = group  # occurrences of one body share their mesh data
        self.name = name
        self.positions = positions
        self.indices = indices
        self.world = world
        self.color_index = color_index
        self.hidden = hidden


def read_bodies(path: str) -> tuple[str, float, list[_Body]]:
    """Parse a .sc file: (document name, mm per unit, bodies in file order).

    Pure Python (no Blender data is created), so any error leaves the scene
    untouched.
    """
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError(f"Not a SolidCraft file (.sc is a ZIP container): {exc}") from exc
    with zf:
        document = _read_document(zf)
        unit = str(document.get("units") or "mm").lower()
        if unit not in UNIT_MM:
            raise ValueError(f"Unknown SolidCraft unit: {unit!r}")
        name = str(document.get("name") or "SolidCraft")

        components = {c["id"]: c for c in document.get("components") or [] if "id" in c}
        occurrences = [o for o in document.get("occurrences") or [] if "id" in o]
        by_component: dict = {}
        for occ in occurrences:
            by_component.setdefault(occ.get("component"), []).append(occ)

        def world(occ, depth=0):
            local = _matrix(occ.get("transform"))
            parents = by_component.get(occ.get("parent"))
            if not parents or depth > 64:
                return local
            return world(parents[0], depth + 1) @ local

        visibility = document.get("visibility") or {}
        hidden_bodies = set(visibility.get("hidden_bodies") or [])
        hidden_occ = set(visibility.get("hidden_occurrences") or [])

        cache: dict = {}
        bodies = []
        for feature in document.get("features") or []:
            if not isinstance(feature, dict) or feature.get("suppressed"):
                continue
            component = components.get(feature.get("component"), {})
            placements = by_component.get(feature.get("component")) or [None]
            for entry in feature.get("meshes") or []:
                blob = entry.get("mesh_blob")
                body_name = str(entry.get("name") or feature.get("name") or "body")
                if blob:
                    if blob not in cache:
                        try:
                            data = zf.read(blob)
                        except KeyError:
                            raise ValueError(
                                f"SolidCraft mesh for {body_name!r} is missing ({blob})"
                            ) from None
                        try:
                            cache[blob] = parse_mesh(data)
                        except ValueError as exc:
                            raise ValueError(f"{body_name}: {exc}") from exc
                        del data
                    positions, indices = cache[blob]
                else:
                    try:
                        inline = _inline_mesh(entry)
                    except (TypeError, ValueError) as exc:
                        raise ValueError(f"{body_name}: {exc}") from exc
                    if inline is None:
                        continue
                    positions, indices = inline
                group = len(bodies)
                for k, occ in enumerate(placements):
                    matrix = world(occ) if occ else Matrix.Identity(4)
                    label = body_name
                    if k and occ.get("name"):
                        label = f"{body_name} ({occ['name']})"
                    hidden = body_name in hidden_bodies or (
                        occ is not None
                        and (occ["id"] in hidden_occ or occ.get("name") in hidden_occ)
                    )
                    bodies.append(
                        _Body(
                            group, label, positions, indices, matrix,
                            component.get("identification_color_index", 0), hidden,
                        )
                    )
    if not bodies:
        raise ValueError("SolidCraft file has no mesh bodies")
    return name, UNIT_MM[unit], bodies


def _material(index: int, materials: dict):
    slot = int(index) % len(PALETTE)
    if slot not in materials:
        mat = bpy.data.materials.new(f"SolidCraft color {slot:02d}")
        mat.diffuse_color = palette_color(slot)
        materials[slot] = mat
    return materials[slot]


def _build_mesh(body: _Body, scale: float, starts: dict):
    mesh = bpy.data.meshes.new(body.name)
    n_verts = len(body.positions) // 3
    n_tris = len(body.indices) // 3
    mesh.vertices.add(n_verts)
    mesh.vertices.foreach_set("co", body.positions)
    mesh.loops.add(n_tris * 3)
    mesh.loops.foreach_set("vertex_index", body.indices)
    mesh.polygons.add(n_tris)
    if n_tris not in starts:
        starts[n_tris] = array("i", range(0, n_tris * 3, 3))
    mesh.polygons.foreach_set("loop_start", starts[n_tris])
    mesh.update(calc_edges=True)
    mesh.shade_flat()
    if scale != 1.0:
        mesh.transform(Matrix.Scale(scale, 4))
    return mesh


def import_sc(path: str, mm_per_bu: float) -> list:
    """Import a SolidCraft project: one object per body, grouped in a
    collection named after the document. Returns the new objects.

    Raises ValueError for anything that is not a readable .sc file; nothing
    is added to the scene in that case.
    """
    name, unit_mm, bodies = read_bodies(path)
    scale = unit_mm / mm_per_bu
    collection = bpy.data.collections.new(name)
    created: list = []
    meshes: dict = {}
    materials: dict = {}
    starts: dict = {}
    try:
        for body in bodies:
            mesh = meshes.get(body.group)
            if mesh is None:
                mesh = meshes[body.group] = _build_mesh(body, scale, starts)
                mesh.materials.append(_material(body.color_index, materials))
            obj = bpy.data.objects.new(body.name, mesh)
            obj.color = palette_color(body.color_index)
            matrix = body.world.copy()
            matrix.translation = matrix.translation * scale
            obj.matrix_world = matrix
            collection.objects.link(obj)
            created.append(obj)
    except Exception:
        for obj in created:
            bpy.data.objects.remove(obj, do_unlink=True)
        for mesh in meshes.values():
            bpy.data.meshes.remove(mesh)
        bpy.data.collections.remove(collection)
        raise
    bpy.context.scene.collection.children.link(collection)
    bpy.context.view_layer.update()
    for body, obj in zip(bodies, created):
        if body.hidden:
            obj.hide_render = True
            try:
                obj.hide_set(True)
            except RuntimeError:  # not in the active view layer
                obj.hide_viewport = True
    return created
