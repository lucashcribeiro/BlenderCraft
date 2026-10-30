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
from xml.etree import ElementTree as ET

import bmesh
import bpy
from mathutils import Matrix

from blendercraft_assistant import io_3mf, tools, units
from blendercraft_assistant.tools.dispatch import call_tool as _call_tool

tools.load_all()

CORE = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"


@contextlib.contextmanager
def _quiet_fds():
    """Silence Blender's exporter/importer logging (test output only)."""
    sys.stdout.flush()
    sys.stderr.flush()
    saved = [os.dup(1), os.dup(2)]
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
        yield
    finally:
        # Operator reports go through sys.stdout and the timing lines through
        # C stdio; drain both into the null fd before restoring it.
        sys.stdout.flush()
        sys.stderr.flush()
        ctypes.CDLL(None).fflush(None)
        os.dup2(saved[0], 1)
        os.dup2(saved[1], 2)
        for fd in (*saved, devnull):
            os.close(fd)


def call_tool(name, args):
    with _quiet_fds():
        return _call_tool(name, args)


def _clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mat in list(bpy.data.materials):
        bpy.data.materials.remove(mat)


def _cube(name, size, color=None, location=(0.0, 0.0, 0.0)):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=size)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    bpy.context.scene.collection.objects.link(obj)
    if color is not None:
        mat = bpy.data.materials.new(f"{name}_mat")
        mat.diffuse_color = color
        # New materials already carry a Principled node (base color 0.8 grey),
        # which is what renders; keep it in step with the viewport color.
        if mat.use_nodes:
            bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
            bsdf.inputs["Base Color"].default_value = color
        mesh.materials.append(mat)
    bpy.context.view_layer.update()
    return obj


def _select(*objs, active=None):
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = active


def _read_stl(path):
    """Vertices of a binary STL as (x, y, z) tuples."""
    data = Path(path).read_bytes()
    (count,) = struct.unpack_from("<I", data, 80)
    assert len(data) == 84 + 50 * count, "not a binary STL"
    verts = []
    for i in range(count):
        values = struct.unpack_from("<12fH", data, 84 + 50 * i)
        verts += [values[3:6], values[6:9], values[9:12]]
    return verts


def _extent(verts):
    return [max(v[i] for v in verts) - min(v[i] for v in verts) for i in range(3)]


def _read_3mf(path):
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        root = ET.fromstring(zf.read("3D/3dmodel.model"))
    return names, root


def _mesh_vertices(obj_el):
    return [
        (float(v.get("x")), float(v.get("y")), float(v.get("z")))
        for v in obj_el.iter(f"{CORE}vertex")
    ]


class IoTestBase(unittest.TestCase):
    def setUp(self):
        _clear_scene()
        units.apply_internal_mm(bpy.context.scene)
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()
        _clear_scene()
        units.apply_internal_mm(bpy.context.scene)

    def export(self, name, **args):
        result = call_tool("export_file", {"path": str(self.dir / name), **args})
        self.assertFalse(result.is_error, result.text)
        return self.dir / name

    def import_(self, path):
        result = call_tool("import_file", {"path": str(path)})
        self.assertFalse(result.is_error, result.text)
        return json.loads(result.text)


class Export3mfTest(IoTestBase):
    def test_3mf_structure(self):
        red = _cube("Red", 20.0, (1.0, 0.0, 0.0, 1.0))
        blue = _cube("Blue", 20.0, (0.0, 0.0, 1.0, 1.0), location=(50.0, 0.0, 0.0))
        path = self.export("two.3mf", objects=["Red", "Blue"])

        names, root = _read_3mf(path)
        self.assertEqual(
            set(names), {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"}
        )
        self.assertEqual(root.get("unit"), "millimeter")
        objects = root.findall(f"{CORE}resources/{CORE}object")
        self.assertEqual(len(objects), 2)
        self.assertEqual([o.get("name") for o in objects], ["Red", "Blue"])
        self.assertTrue(all(o.get("type") == "model" for o in objects))
        for obj_el in objects:
            self.assertEqual(len(obj_el.findall(f".//{CORE}triangle")), 12)

        bases = root.findall(f"{CORE}resources/{CORE}basematerials/{CORE}base")
        colors = []
        for obj_el in objects:
            self.assertEqual(obj_el.get("pid"), "1")
            colors.append(bases[int(obj_el.get("pindex"))].get("displaycolor"))
        self.assertEqual(colors, ["#FF0000", "#0000FF"])
        self.assertEqual(len(root.findall(f"{CORE}build/{CORE}item")), 2)
        # World placement is baked into the vertices (Blue sits at x = 50).
        blue_x = [v[0] for v in _mesh_vertices(objects[1])]
        self.assertAlmostEqual(min(blue_x), 40.0, places=4)
        self.assertAlmostEqual(max(blue_x), 60.0, places=4)

    def test_3mf_package_metadata(self):
        _cube("Box", 20.0)
        path = self.export("box.3mf", objects=["Box"])
        with zipfile.ZipFile(path) as zf:
            types = ET.fromstring(zf.read("[Content_Types].xml"))
            rels = ET.fromstring(zf.read("_rels/.rels"))
        ct = "{http://schemas.openxmlformats.org/package/2006/content-types}"
        defaults = {d.get("Extension"): d.get("ContentType") for d in types.iter(f"{ct}Default")}
        self.assertEqual(
            defaults,
            {
                "model": "application/vnd.ms-package.3dmanufacturing-3dmodel+xml",
                "rels": "application/vnd.openxmlformats-package.relationships+xml",
            },
        )
        (rel,) = list(rels)
        self.assertEqual(
            rel.get("Type"), "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
        )
        self.assertEqual(rel.get("Target"), "/3D/3dmodel.model")

    def test_3mf_default_color_and_sRGB_conversion(self):
        _cube("Plain", 10.0)
        # Linear 0.21586 is sRGB 0x80.
        _cube("Mid", 10.0, (0.21586, 0.21586, 0.21586, 1.0), location=(30.0, 0.0, 0.0))
        path = self.export("c.3mf", objects=["Plain", "Mid"])
        _, root = _read_3mf(path)
        bases = root.findall(f"{CORE}resources/{CORE}basematerials/{CORE}base")
        self.assertEqual(
            [b.get("displaycolor") for b in bases], ["#CCCCCC", "#808080"]
        )

    def test_3mf_uses_principled_base_color(self):
        obj = _cube("Node", 10.0, (0.8, 0.8, 0.8, 1.0))
        mat = obj.data.materials[0]
        bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        bsdf.inputs["Base Color"].default_value = (0.0, 1.0, 0.0, 1.0)
        path = self.export("n.3mf", objects=["Node"])
        _, root = _read_3mf(path)
        (base,) = root.findall(f"{CORE}resources/{CORE}basematerials/{CORE}base")
        self.assertEqual(base.get("displaycolor"), "#00FF00")

    def test_3mf_linked_base_color_falls_back_to_diffuse(self):
        obj = _cube("Linked", 10.0, (1.0, 0.0, 0.0, 1.0))
        mat = obj.data.materials[0]
        mat.use_nodes = True
        nodes, links = mat.node_tree.nodes, mat.node_tree.links
        bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
        rgb = nodes.new("ShaderNodeRGB")
        links.new(rgb.outputs["Color"], bsdf.inputs["Base Color"])
        path = self.export("l.3mf", objects=["Linked"])
        _, root = _read_3mf(path)
        (base,) = root.findall(f"{CORE}resources/{CORE}basematerials/{CORE}base")
        self.assertEqual(base.get("displaycolor"), "#FF0000")

    def test_3mf_applies_modifiers(self):
        obj = _cube("Mod", 20.0)
        obj.modifiers.new("Array", "ARRAY").count = 2
        path = self.export("m.3mf", objects=["Mod"])
        _, root = _read_3mf(path)
        (obj_el,) = root.findall(f"{CORE}resources/{CORE}object")
        self.assertEqual(len(obj_el.findall(f".//{CORE}triangle")), 24)

    def test_3mf_mirrored_object_keeps_outward_winding(self):
        obj = _cube("Mirror", 20.0)
        obj.scale.x = -1.0
        path = self.export("mir.3mf", objects=["Mirror"])
        _, root = _read_3mf(path)
        obj_el = root.find(f"{CORE}resources/{CORE}object")
        verts = _mesh_vertices(obj_el)
        volume = 0.0
        for t in obj_el.iter(f"{CORE}triangle"):
            a, b, c = (verts[int(t.get(k))] for k in ("v1", "v2", "v3"))
            volume += (
                a[0] * (b[1] * c[2] - b[2] * c[1])
                - a[1] * (b[0] * c[2] - b[2] * c[0])
                + a[2] * (b[0] * c[1] - b[1] * c[0])
            ) / 6.0
        self.assertAlmostEqual(volume, 8000.0, places=2)

    def test_3mf_meters_scene(self):
        scene = bpy.context.scene
        scene.unit_settings.system = "METRIC"
        scene.unit_settings.scale_length = 1.0
        _cube("Small", 0.02)
        path = self.export("s.3mf", objects=["Small"])
        _, root = _read_3mf(path)
        self.assertEqual(root.get("unit"), "millimeter")
        verts = _mesh_vertices(root.find(f"{CORE}resources/{CORE}object"))
        for extent in _extent(verts):
            self.assertAlmostEqual(extent, 20.0, places=3)

    def test_3mf_roundtrip(self):
        _cube("Red", 20.0, (1.0, 0.0, 0.0, 1.0))
        _cube("Blue", 20.0, (0.0, 0.0, 1.0, 1.0), location=(50.0, 0.0, 0.0))
        path = self.export("rt.3mf", objects=["Red", "Blue"])
        _clear_scene()

        info = self.import_(path)
        self.assertEqual(len(info["objects"]), 2)
        for entry in info["objects"]:
            obj = bpy.data.objects[entry["name"]]
            for dim in obj.dimensions:
                self.assertAlmostEqual(dim, 20.0, places=3)
        red = bpy.data.objects["Red"]
        self.assertEqual(
            [round(c, 3) for c in red.material_slots[0].material.diffuse_color],
            [1.0, 0.0, 0.0, 1.0],
        )
        blue = bpy.data.objects["Blue"]
        self.assertEqual(
            [round(c, 3) for c in blue.material_slots[0].material.diffuse_color],
            [0.0, 0.0, 1.0, 1.0],
        )

    def test_3mf_result_lists_written_and_skipped_objects(self):
        _cube("Solid", 20.0)
        bpy.data.objects.new("Faceless", bpy.data.meshes.new("Faceless"))
        bpy.context.scene.collection.objects.link(bpy.data.objects["Faceless"])
        bpy.ops.object.empty_add()
        empty = bpy.context.active_object
        result = call_tool(
            "export_file",
            {
                "path": str(self.dir / "w.3mf"),
                "objects": ["Solid", "Faceless", empty.name],
            },
        )
        self.assertFalse(result.is_error, result.text)
        data = json.loads(result.text)
        self.assertEqual(data["objects"], ["Solid"])
        self.assertEqual(
            sorted((e["name"], e["reason"]) for e in data["skipped"]),
            sorted([("Faceless", "no faces"), (empty.name, "not a mesh")]),
        )

    def test_export_result_has_empty_skipped_list_when_all_written(self):
        _cube("Solid", 20.0)
        for name in ("a.3mf", "a.stl"):
            data = json.loads(
                call_tool(
                    "export_file",
                    {"path": str(self.dir / name), "objects": ["Solid"]},
                ).text
            )
            self.assertEqual(data["objects"], ["Solid"])
            self.assertEqual(data["skipped"], [])

    def test_3mf_no_mesh_objects_is_error(self):
        bpy.ops.object.empty_add()
        empty = bpy.context.active_object
        result = call_tool(
            "export_file", {"path": str(self.dir / "e.3mf"), "objects": [empty.name]}
        )
        self.assertTrue(result.is_error)


def _write_model(path, unit, body):
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<model unit="{unit}" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
        f"{body}</model>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("3D/3dmodel.model", xml)


_UNIT_CUBE = (
    "<object id='1' type='model' name='Unit'><mesh><vertices>"
    "<vertex x='0' y='0' z='0'/><vertex x='1' y='0' z='0'/>"
    "<vertex x='1' y='1' z='0'/><vertex x='0' y='1' z='0'/>"
    "<vertex x='0' y='0' z='1'/><vertex x='1' y='0' z='1'/>"
    "<vertex x='1' y='1' z='1'/><vertex x='0' y='1' z='1'/></vertices><triangles>"
    "<triangle v1='0' v2='2' v3='1'/><triangle v1='0' v2='3' v3='2'/>"
    "<triangle v1='4' v2='5' v3='6'/><triangle v1='4' v2='6' v3='7'/>"
    "<triangle v1='0' v2='1' v3='5'/><triangle v1='0' v2='5' v3='4'/>"
    "<triangle v1='1' v2='2' v3='6'/><triangle v1='1' v2='6' v3='5'/>"
    "<triangle v1='2' v2='3' v3='7'/><triangle v1='2' v2='7' v3='6'/>"
    "<triangle v1='3' v2='0' v3='4'/><triangle v1='3' v2='4' v3='7'/>"
    "</triangles></mesh></object>"
)


class Import3mfTest(IoTestBase):
    def _import_unit_cube(self, unit, items="<item objectid='1'/>"):
        path = self.dir / "u.3mf"
        _write_model(
            path, unit, f"<resources>{_UNIT_CUBE}</resources><build>{items}</build>"
        )
        info = self.import_(path)
        return [bpy.data.objects[e["name"]] for e in info["objects"]]

    def test_unit_attribute_is_respected(self):
        expected = {
            "micron": 0.001,
            "millimeter": 1.0,
            "centimeter": 10.0,
            "inch": 25.4,
            "foot": 304.8,
            "meter": 1000.0,
        }
        for unit, mm in expected.items():
            with self.subTest(unit=unit):
                _clear_scene()
                (obj,) = self._import_unit_cube(unit)
                for dim in obj.dimensions:
                    self.assertAlmostEqual(dim, mm, places=3)

    def test_unit_converted_to_scene_units(self):
        scene = bpy.context.scene
        scene.unit_settings.system = "METRIC"
        scene.unit_settings.scale_length = 1.0  # 1 BU = 1 m
        (obj,) = self._import_unit_cube("centimeter")
        for dim in obj.dimensions:
            self.assertAlmostEqual(dim, 0.01, places=6)

    def test_item_transform_is_applied_in_file_units(self):
        # Row-vector 3MF matrix: translation in the last three values.
        items = "<item objectid='1' transform='1 0 0 0 1 0 0 0 1 2 3 4'/>"
        (obj,) = self._import_unit_cube("centimeter", items)
        self.assertEqual([round(c, 3) for c in obj.location], [20.0, 30.0, 40.0])
        for dim in obj.dimensions:
            self.assertAlmostEqual(dim, 10.0, places=3)

    def test_unknown_unit_is_error(self):
        path = self.dir / "bad.3mf"
        _write_model(path, "furlong", f"<resources>{_UNIT_CUBE}</resources><build/>")
        result = call_tool("import_file", {"path": str(path)})
        self.assertTrue(result.is_error)


class StlExportTest(IoTestBase):
    def test_stl_mm_any_display(self):
        _cube("Cube", 20.0)
        units.set_display_unit(bpy.context.scene, "IN")
        path = self.export("c.stl", objects=["Cube"])
        for extent in _extent(_read_stl(path)):
            self.assertAlmostEqual(extent, 20.0, places=3)

    def test_stl_meters_scene(self):
        scene = bpy.context.scene
        scene.unit_settings.system = "METRIC"
        scene.unit_settings.scale_length = 1.0
        _cube("Cube", 0.02)
        path = self.export("c.stl", objects=["Cube"])
        for extent in _extent(_read_stl(path)):
            self.assertAlmostEqual(extent, 20.0, places=3)

    def test_stl_exports_selection_by_default(self):
        a = _cube("A", 20.0)
        _cube("B", 10.0, location=(100.0, 0.0, 0.0))
        _select(a, active=a)
        path = self.export("sel.stl")
        for extent in _extent(_read_stl(path)):
            self.assertAlmostEqual(extent, 20.0, places=3)

    def test_stl_applies_modifiers(self):
        obj = _cube("Mod", 20.0)
        obj.modifiers.new("Array", "ARRAY").count = 2
        path = self.export("m.stl", objects=["Mod"])
        self.assertAlmostEqual(_extent(_read_stl(path))[0], 40.0, places=3)

    def test_stl_roundtrip_mm_scene(self):
        _cube("Cube", 20.0)
        path = self.export("c.stl", objects=["Cube"])
        _clear_scene()
        (entry,) = self.import_(path)["objects"]
        for dim in bpy.data.objects[entry["name"]].dimensions:
            self.assertAlmostEqual(dim, 20.0, places=3)

    def test_stl_import_in_meters_scene(self):
        _cube("Cube", 20.0)
        path = self.export("c.stl", objects=["Cube"])
        _clear_scene()
        scene = bpy.context.scene
        scene.unit_settings.system = "METRIC"
        scene.unit_settings.scale_length = 1.0
        (entry,) = self.import_(path)["objects"]
        for dim in bpy.data.objects[entry["name"]].dimensions:
            self.assertAlmostEqual(dim, 0.02, places=6)


class OtherFormatsTest(IoTestBase):
    def test_obj_and_ply_roundtrip_in_mm(self):
        _cube("Cube", 20.0)
        for ext in ("obj", "ply"):
            with self.subTest(ext=ext):
                path = self.export(f"c.{ext}", objects=["Cube"])
                before = set(bpy.data.objects.keys())
                info = self.import_(path)
                (entry,) = info["objects"]
                self.assertNotIn(entry["name"], before)
                obj = bpy.data.objects[entry["name"]]
                # Z-up both ways: a cube stays a 20 mm cube.
                for dim in obj.dimensions:
                    self.assertAlmostEqual(dim, 20.0, places=3)
                bpy.data.objects.remove(obj, do_unlink=True)

    def test_obj_export_scales_to_mm(self):
        scene = bpy.context.scene
        scene.unit_settings.system = "METRIC"
        scene.unit_settings.scale_length = 1.0
        _cube("Cube", 0.02)
        path = self.export("c.obj", objects=["Cube"])
        coords = [
            tuple(float(x) for x in line.split()[1:4])
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith("v ")
        ]
        for extent in _extent(coords):
            self.assertAlmostEqual(extent, 20.0, places=3)

    def test_gltf_export_and_import(self):
        _cube("Cube", 20.0)
        for ext in ("glb", "gltf"):
            with self.subTest(ext=ext):
                path = self.export(f"c.{ext}", objects=["Cube"])
                self.assertGreater(path.stat().st_size, 0)
                before = set(bpy.data.objects.keys())
                info = self.import_(path)
                self.assertTrue(info["objects"])
                for entry in info["objects"]:
                    self.assertNotIn(entry["name"], before)

    def test_format_overrides_extension(self):
        _cube("Cube", 20.0)
        path = self.export("noext", objects=["Cube"], format="stl")
        self.assertEqual(len(_read_stl(path)), 36)

    def test_unsupported_format_is_error(self):
        _cube("Cube", 20.0)
        result = call_tool(
            "export_file", {"path": str(self.dir / "c.fbx"), "objects": ["Cube"]}
        )
        self.assertTrue(result.is_error)
        result = call_tool("import_file", {"path": str(self.dir / "c.fbx")})
        self.assertTrue(result.is_error)

    def test_import_missing_file_is_error(self):
        result = call_tool("import_file", {"path": str(self.dir / "nope.stl")})
        self.assertTrue(result.is_error)

    def test_export_nothing_selected_is_error(self):
        _cube("Cube", 20.0)
        _select()
        result = call_tool("export_file", {"path": str(self.dir / "c.stl")})
        self.assertTrue(result.is_error)

    def test_export_hidden_object_is_error_not_empty_file(self):
        obj = _cube("Hidden", 20.0)
        obj.hide_set(True)
        result = call_tool(
            "export_file", {"path": str(self.dir / "h.stl"), "objects": ["Hidden"]}
        )
        self.assertTrue(result.is_error)
        self.assertIn("Hidden", result.text)

    def test_export_hidden_object_is_error_in_every_format(self):
        for how in ("hide_set", "hide_viewport"):
            for name in ("h.stl", "h.obj", "h.ply", "h.glb", "h.3mf"):
                with self.subTest(how=how, name=name):
                    _clear_scene()
                    obj = _cube("Hidden", 20.0)
                    obj.modifiers.new("Array", "ARRAY").count = 2
                    if how == "hide_set":
                        obj.hide_set(True)
                    else:
                        obj.hide_viewport = True
                    result = call_tool(
                        "export_file",
                        {"path": str(self.dir / name), "objects": ["Hidden"]},
                    )
                    self.assertTrue(result.is_error)
                    self.assertIn("Hidden", result.text)
                    self.assertFalse((self.dir / name).exists())

    def test_export_unknown_object_is_error(self):
        result = call_tool(
            "export_file", {"path": str(self.dir / "c.stl"), "objects": ["Ghost"]}
        )
        self.assertTrue(result.is_error)


class SelectionRestoreTest(IoTestBase):
    def _assert_selection(self, selected, active):
        view_layer = bpy.context.view_layer
        self.assertEqual(
            {o.name for o in view_layer.objects if o.select_get()}, set(selected)
        )
        self.assertIs(view_layer.objects.active, active)

    def test_export_restores_selection(self):
        a = _cube("A", 20.0)
        b = _cube("B", 20.0, location=(50.0, 0.0, 0.0))
        c = _cube("C", 20.0, location=(100.0, 0.0, 0.0))
        for name in ("e.stl", "e.obj", "e.ply", "e.glb", "e.3mf"):
            with self.subTest(name=name):
                _select(a, c, active=c)
                self.export(name, objects=["B"])
                self._assert_selection({"A", "C"}, c)
        self.assertIsNotNone(b)

    def test_export_restores_selection_on_error(self):
        a = _cube("A", 20.0)
        _cube("B", 20.0, location=(50.0, 0.0, 0.0))
        _select(a, active=a)
        # The target is an existing directory, so the exporter itself fails.
        result = call_tool(
            "export_file", {"path": str(self.dir), "objects": ["B"], "format": "stl"}
        )
        self.assertTrue(result.is_error)
        self._assert_selection({"A"}, a)

    def test_export_restores_no_active_object(self):
        a = _cube("A", 20.0)
        _select(active=None)
        self.export("e.stl", objects=["A"])
        self._assert_selection(set(), None)
        self.assertIsNotNone(a)


class ToolRegistrationTest(unittest.TestCase):
    def test_kinds(self):
        from blendercraft_assistant.tools.base import REGISTRY

        self.assertEqual(REGISTRY["import_file"].kind, "scene")
        self.assertEqual(REGISTRY["export_file"].kind, "file")

    def test_io_3mf_has_expected_api(self):
        self.assertTrue(callable(io_3mf.export_3mf))
        self.assertTrue(callable(io_3mf.import_3mf))
