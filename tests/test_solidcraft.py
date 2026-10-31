"""SolidCraft (.sc) import: container, SCMESH01 blobs, names, transforms,
units, colors, visibility and error paths. Also holds the helpers that build
synthetic .sc files for test_solidcraft_ui."""

import contextlib
import ctypes
import json
import os
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import bpy

from blendercraft_assistant import io_solidcraft, units

IDENTITY = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]

CUBE_CORNERS = [(x, y, z) for x in (0, 1) for y in (0, 1) for z in (0, 1)]
CUBE_TRIS = [
    (0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1),
    (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3),
]


@contextlib.contextmanager
def _quiet():
    libc = ctypes.CDLL(None)
    sys.stdout.flush()
    sys.stderr.flush()
    libc.fflush(None)
    saved = [os.dup(1), os.dup(2)]
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
        yield
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        libc.fflush(None)
        os.dup2(saved[0], 1)
        os.dup2(saved[1], 2)
        for fd in (*saved, devnull):
            os.close(fd)


def mesh_blob(size=(1.0, 1.0, 1.0), origin=(0.0, 0.0, 0.0)) -> bytes:
    """A box as an SCMESH01 blob: f64 positions, then u32 indices."""
    coords = []
    for corner in CUBE_CORNERS:
        coords += [origin[i] + corner[i] * size[i] for i in range(3)]
    indices = [i for tri in CUBE_TRIS for i in tri]
    return (
        b"SCMESH01"
        + struct.pack("<QQ", len(coords), len(indices))
        + struct.pack(f"<{len(coords)}d", *coords)
        + struct.pack(f"<{len(indices)}I", *indices)
    )


def translation(x=0.0, y=0.0, z=0.0):
    return [[1.0, 0.0, 0.0, x], [0.0, 1.0, 0.0, y], [0.0, 0.0, 1.0, z], [0.0, 0.0, 0.0, 1.0]]


def build_doc(parts, units_name="mm", visibility=None, name="Doc"):
    """parts: [(component name, blob name, transform)] under a root component."""
    components = [{"id": 1, "name": name, "parent": 0, "identification_color_index": 0}]
    occurrences = [
        {"id": 2, "component": 1, "name": f"{name}:1", "parent": 0, "grounded": True,
         "transform": IDENTITY}
    ]
    features = []
    next_id = 3
    for index, (cname, blob, transform) in enumerate(parts, start=1):
        cid = next_id
        components.append(
            {"id": cid, "name": cname, "parent": 1, "identification_color_index": index}
        )
        occurrences.append(
            {"id": cid + 1, "component": cid, "name": f"{cname}:1", "parent": 1,
             "grounded": False, "transform": transform}
        )
        features.append(
            {"id": cid + 2, "component": cid, "type": "mesh_import", "suppressed": False,
             "inserted": True, "name": cname, "file": f"{cname}.stl", "body_names": [cname],
             "meshes": [{"name": cname, "mesh_blob": f"blobs/{blob}.mesh",
                         "positions": [], "triangles": []}]}
        )
        next_id += 3
    return {
        "format": "solidcraft/44", "name": name, "units": units_name, "params": [],
        "components": components, "occurrences": occurrences, "features": features,
        "next_id": next_id, "next_component_color_index": len(parts) + 1,
        "visibility": visibility
        or {"hidden_bodies": [], "hidden_occurrences": [], "hidden_origin": [],
            "hidden_sketches": [], "shown_sketches": []},
    }


def write_sc(path, doc, blobs, container=None):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr(
            "container.json",
            json.dumps(container or {"format": "solidcraft-container/1"}),
        )
        zf.writestr("document.json", json.dumps(doc))
        for blob_name, data in blobs.items():
            zf.writestr(f"blobs/{blob_name}.mesh", data)


def _clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for coll in list(bpy.data.collections):
        bpy.data.collections.remove(coll)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)
    for mat in list(bpy.data.materials):
        bpy.data.materials.remove(mat)


class ScTestBase(unittest.TestCase):
    def setUp(self):
        _clear_scene()
        self.addCleanup(_clear_scene)
        scene = bpy.context.scene
        units.apply_internal_mm(scene)
        self.addCleanup(units.apply_internal_mm, scene)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def make(self, parts, blobs=None, **kwargs):
        blobs = blobs or {"a": mesh_blob((10, 20, 30)), "b": mesh_blob((2, 2, 2))}
        path = self.dir / "doc.sc"
        write_sc(path, build_doc(parts, **kwargs), blobs)
        return str(path)


class ImportScTest(ScTestBase):
    def test_one_object_per_body_named_like_solidcraft(self):
        path = self.make(
            [("05-rabo", "a", IDENTITY), ("04-perna-frente-x+", "b", translation(5, 6, 7))]
        )
        objs = io_solidcraft.import_sc(path, 1.0)
        self.assertEqual([o.name for o in objs], ["05-rabo", "04-perna-frente-x+"])
        self.assertTrue(all(o.type == "MESH" for o in objs))
        self.assertEqual(len(objs[0].data.polygons), 12)
        self.assertEqual(len(objs[0].data.vertices), 8)

    def test_geometry_dimensions_and_transform(self):
        path = self.make([("big", "a", IDENTITY), ("small", "b", translation(5, 6, 7))])
        big, small = io_solidcraft.import_sc(path, 1.0)
        self.assertEqual([round(d, 4) for d in big.dimensions], [10.0, 20.0, 30.0])
        self.assertEqual([round(v, 4) for v in small.matrix_world.translation], [5.0, 6.0, 7.0])
        self.assertEqual([round(d, 4) for d in small.dimensions], [2.0, 2.0, 2.0])

    def test_transform_is_inherited_through_parent_components(self):
        doc = build_doc([("child", "b", translation(1, 0, 0))])
        # Nest "child" under a part placed at x=100 (occurrence of component 3).
        doc["components"].append(
            {"id": 50, "name": "sub", "parent": 1, "identification_color_index": 9}
        )
        doc["occurrences"].append(
            {"id": 51, "component": 50, "name": "sub:1", "parent": 1, "grounded": False,
             "transform": translation(100, 0, 0)}
        )
        doc["components"][1]["parent"] = 50
        doc["occurrences"][1]["parent"] = 50
        path = self.dir / "nested.sc"
        write_sc(path, doc, {"b": mesh_blob()})
        (obj,) = io_solidcraft.import_sc(str(path), 1.0)
        self.assertAlmostEqual(obj.matrix_world.translation.x, 101.0, places=4)

    def test_rotation_matrix_in_last_row_convention_is_transposed(self):
        # Translation stored in the last row instead of the last column.
        transposed = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [3.0, 4.0, 5.0, 1.0]]
        path = self.make([("t", "b", transposed)])
        (obj,) = io_solidcraft.import_sc(path, 1.0)
        self.assertEqual([round(v, 4) for v in obj.matrix_world.translation], [3.0, 4.0, 5.0])

    def test_units_cm_and_scene_scale(self):
        path = self.make([("big", "a", translation(1, 0, 0))], units_name="cm")
        (obj,) = io_solidcraft.import_sc(path, 1.0)  # 1 BU = 1 mm
        self.assertEqual([round(d, 4) for d in obj.dimensions], [100.0, 200.0, 300.0])
        self.assertAlmostEqual(obj.matrix_world.translation.x, 10.0, places=4)
        _clear_scene()
        # 1 BU = 1 m: 1 mm is 0.001 BU, so 10 cm becomes 0.1 BU.
        (obj,) = io_solidcraft.import_sc(path, 1000.0)
        self.assertAlmostEqual(obj.dimensions.x, 0.1, places=5)

    def test_unknown_unit_is_an_error(self):
        path = self.make([("a", "a", IDENTITY)], units_name="parsec")
        with self.assertRaisesRegex(ValueError, "parsec"):
            io_solidcraft.import_sc(path, 1.0)

    def test_collection_named_after_document(self):
        path = self.make([("a", "a", IDENTITY), ("b", "b", IDENTITY)], name="2.0 CACHORRINHO")
        objs = io_solidcraft.import_sc(path, 1.0)
        coll = bpy.data.collections["2.0 CACHORRINHO"]
        self.assertIn(coll, list(bpy.context.scene.collection.children))
        self.assertEqual(sorted(o.name for o in coll.objects), ["a", "b"])
        self.assertTrue(all(o.users_collection == (coll,) for o in objs))

    def test_colors_follow_identification_color_index(self):
        path = self.make([("a", "a", IDENTITY), ("b", "b", IDENTITY)])
        a, b = io_solidcraft.import_sc(path, 1.0)
        for got, want in zip(a.color, io_solidcraft.palette_color(1)):
            self.assertAlmostEqual(got, want, places=5)
        for got, want in zip(b.color, io_solidcraft.palette_color(2)):
            self.assertAlmostEqual(got, want, places=5)
        self.assertNotEqual(tuple(a.color), tuple(b.color))
        self.assertEqual(len(a.data.materials), 1)
        mat = a.data.materials[0]
        for got, want in zip(mat.diffuse_color, io_solidcraft.palette_color(1)):
            self.assertAlmostEqual(got, want, places=5)
        # The palette wraps around for large indices.
        n = len(io_solidcraft.PALETTE)
        self.assertEqual(io_solidcraft.palette_color(n + 3), io_solidcraft.palette_color(3))

    def test_hidden_bodies_and_occurrences(self):
        vis = {"hidden_bodies": ["a"], "hidden_occurrences": [], "hidden_origin": [],
               "hidden_sketches": [], "shown_sketches": []}
        path = self.make([("a", "a", IDENTITY), ("b", "b", IDENTITY)], visibility=vis)
        a, b = io_solidcraft.import_sc(path, 1.0)
        self.assertTrue(a.hide_get())
        self.assertTrue(a.hide_render)
        self.assertFalse(b.hide_get())
        self.assertFalse(b.hide_render)

    def test_hidden_occurrence_by_id(self):
        doc = build_doc([("a", "a", IDENTITY), ("b", "b", IDENTITY)])
        doc["visibility"]["hidden_occurrences"] = [doc["occurrences"][2]["id"]]
        path = self.dir / "hid.sc"
        write_sc(path, doc, {"a": mesh_blob(), "b": mesh_blob()})
        a, b = io_solidcraft.import_sc(str(path), 1.0)
        self.assertFalse(a.hide_get())
        self.assertTrue(b.hide_get())

    def test_shared_blob_and_suppressed_feature(self):
        doc = build_doc([("a", "a", IDENTITY), ("b", "a", translation(30, 0, 0)),
                         ("c", "a", IDENTITY)])
        doc["features"][2]["suppressed"] = True
        path = self.dir / "shared.sc"
        write_sc(path, doc, {"a": mesh_blob()})
        objs = io_solidcraft.import_sc(str(path), 1.0)
        self.assertEqual([o.name for o in objs], ["a", "b"])

    def test_feature_without_blob_is_skipped(self):
        doc = build_doc([("a", "a", IDENTITY)])
        doc["features"].append({"id": 99, "component": 3, "type": "extrude", "name": "x"})
        path = self.dir / "feat.sc"
        write_sc(path, doc, {"a": mesh_blob()})
        self.assertEqual([o.name for o in io_solidcraft.import_sc(str(path), 1.0)], ["a"])

    def test_parse_mesh_layout(self):
        positions, indices = io_solidcraft.parse_mesh(mesh_blob((1, 2, 3)))
        self.assertEqual(len(positions), 24)
        self.assertEqual(len(indices), 36)
        self.assertEqual(max(positions), 3.0)
        self.assertEqual(max(indices), 7)


class ImportScErrorsTest(ScTestBase):
    def test_not_a_zip(self):
        path = self.dir / "bad.sc"
        path.write_bytes(b"definitely not a zip")
        with self.assertRaisesRegex(ValueError, "SolidCraft"):
            io_solidcraft.import_sc(str(path), 1.0)

    def test_wrong_container_format(self):
        path = self.dir / "other.sc"
        write_sc(path, build_doc([]), {}, container={"format": "something-else/1"})
        with self.assertRaisesRegex(ValueError, "something-else"):
            io_solidcraft.import_sc(str(path), 1.0)

    def test_missing_document(self):
        path = self.dir / "nodoc.sc"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("container.json", json.dumps({"format": "solidcraft-container/1"}))
        with self.assertRaisesRegex(ValueError, "document.json"):
            io_solidcraft.import_sc(str(path), 1.0)

    def test_bad_magic(self):
        path = self.make([("a", "a", IDENTITY)], blobs={"a": b"NOTMESH0" + mesh_blob()[8:]})
        with self.assertRaisesRegex(ValueError, "SCMESH01"):
            io_solidcraft.import_sc(path, 1.0)

    def test_missing_blob_names_the_body(self):
        path = self.make([("05-rabo", "gone", IDENTITY)], blobs={"a": mesh_blob()})
        with self.assertRaisesRegex(ValueError, "05-rabo"):
            io_solidcraft.import_sc(path, 1.0)
        self.assertEqual(len(bpy.data.objects), 0)

    def test_truncated_blob(self):
        path = self.make([("a", "a", IDENTITY)], blobs={"a": mesh_blob()[:-10]})
        with self.assertRaisesRegex(ValueError, "truncated"):
            io_solidcraft.import_sc(path, 1.0)

    def test_index_out_of_range(self):
        blob = bytearray(mesh_blob())
        struct.pack_into("<I", blob, len(blob) - 4, 99)
        path = self.make([("a", "a", IDENTITY)], blobs={"a": bytes(blob)})
        with self.assertRaisesRegex(ValueError, "index"):
            io_solidcraft.import_sc(path, 1.0)

    def test_no_bodies(self):
        path = self.make([])
        with self.assertRaisesRegex(ValueError, "no mesh"):
            io_solidcraft.import_sc(path, 1.0)

    def test_failed_import_leaves_no_objects_or_collection(self):
        path = self.make(
            [("ok", "a", IDENTITY), ("bad", "b", IDENTITY)],
            blobs={"a": mesh_blob(), "b": b"SCMESH01"},
        )
        with self.assertRaises(ValueError):
            io_solidcraft.import_sc(path, 1.0)
        self.assertEqual(len(bpy.data.objects), 0)
        self.assertEqual(len(bpy.data.collections), 0)


if __name__ == "__main__":
    unittest.main()
