"""Print-ready export: helper exclusion, overlap/manifold check, strict mode
and the Bambu-style grouped 3MF (one object per print piece, parts inside)."""

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import bmesh
import bpy
from mathutils import Vector

from blendercraft_assistant import export_check, io_3mf, units
from blendercraft_assistant.tools import files

CORE = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
PROD = "{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}"


def _clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)
    for coll in list(bpy.data.collections):
        bpy.data.collections.remove(coll)
    for mat in list(bpy.data.materials):
        bpy.data.materials.remove(mat)


def _material(name, rgba):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.diffuse_color = rgba
    tree = mat.node_tree
    bsdf = tree.nodes.get("Principled BSDF") if tree else None
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = rgba
    return mat


def _cube(name, size=10.0, location=(0.0, 0.0, 0.0), color=None, parent=None,
          collection=None, open_top=False):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=size)
    if open_top:
        top = max(bm.faces, key=lambda f: f.calc_center_median().z)
        bmesh.ops.delete(bm, geom=[top], context="FACES_ONLY")
    bm.to_mesh(mesh)
    bm.free()
    if color is not None:
        mesh.materials.append(_material(f"M{color}", color))
    obj = bpy.data.objects.new(name, mesh)
    (collection or bpy.context.scene.collection).objects.link(obj)
    obj.location = location
    if parent is not None:
        bpy.context.view_layer.update()
        world = obj.matrix_world.copy()
        obj.parent = parent
        obj.matrix_world = world
    bpy.context.view_layer.update()
    return obj


def _collection(name):
    coll = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(coll)
    return coll


RED = (1.0, 0.0, 0.0, 1.0)
BLUE = (0.0, 0.0, 1.0, 1.0)
GREY = (0.8, 0.8, 0.8, 1.0)


class _Base(unittest.TestCase):
    def setUp(self):
        _clear_scene()
        self.addCleanup(_clear_scene)
        units.apply_internal_mm(bpy.context.scene)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def export(self, names, filename="out.3mf", **kwargs):
        args = {"path": str(self.tmp / filename), "objects": names, **kwargs}
        return json.loads(files._export_file(args).text)


class HelperExclusionTest(_Base):
    def test_helpers_are_detected_by_name_collection_and_boolean_use(self):
        body = _cube("Body", 20.0)
        furo = _cube("07-furo-frente", 4.0, parent=body)
        cut = _cube("BC_cut_furo-a", 4.0, collection=_collection("BC_cutters"))
        helper = _cube("Guide", 4.0, collection=_collection("helpers"))
        pivot = bpy.data.objects.new("BC_Pivo_leg", None)
        bpy.context.scene.collection.objects.link(pivot)
        drill = _cube("Drill", 3.0, location=(30, 0, 0))
        mod = body.modifiers.new("Hole", "BOOLEAN")
        mod.object = drill
        mod.show_viewport = False  # keep Body's geometry simple
        kept, excluded = export_check.split_helpers(
            [body, furo, cut, helper, pivot, drill]
        )
        # Pivots are empties: never written, so reported as "not a mesh".
        self.assertEqual(kept, [body, pivot])
        reasons = {e["name"]: e["reason"] for e in excluded}
        self.assertEqual(
            set(reasons), {"07-furo-frente", "BC_cut_furo-a", "Guide", "Drill"}
        )
        self.assertIn("BC_cutters", reasons["BC_cut_furo-a"])
        self.assertIn("furo", reasons["07-furo-frente"])
        self.assertIn("Body", reasons["Drill"])

    def test_padrao_and_ordinary_names_are_not_helpers(self):
        obj = _cube("Dog-Padrao-V1")
        other = _cube("04-perna-frente", location=(30, 0, 0))
        kept, excluded = export_check.split_helpers([obj, other])
        self.assertEqual(kept, [obj, other])
        self.assertEqual(excluded, [])

    def test_export_excludes_helpers_even_hidden_ones(self):
        body = _cube("Body", 20.0)
        furo = _cube("07-furo-x", 4.0, location=(0, 0, 10))
        cut = _cube("BC_cut_a", 4.0, collection=_collection("BC_cutters"))
        cut.hide_set(True)
        data = self.export(["Body", "07-furo-x", "BC_cut_a"])
        self.assertEqual(data["objects"], ["Body"])
        excluded = {e["name"] for e in data["check"]["excluded"]}
        self.assertEqual(excluded, {"07-furo-x", "BC_cut_a"})
        self.assertTrue(any("07-furo-x" in line for line in data["check"]["report"]))
        with zipfile.ZipFile(self.tmp / "out.3mf") as zf:
            text = b"".join(zf.read(n) for n in zf.namelist()).decode()
        self.assertNotIn("furo", text)
        self.assertNotIn("BC_cut", text)
        self.assertIsNotNone(furo)

    def test_include_helpers_keeps_them(self):
        _cube("Body", 20.0)
        _cube("07-furo-x", 4.0, location=(40, 0, 0))
        data = self.export(["Body", "07-furo-x"], include_helpers=True)
        self.assertEqual(sorted(data["objects"]), ["07-furo-x", "Body"])
        self.assertEqual(data["check"]["excluded"], [])

    def test_stl_export_excludes_helpers(self):
        _cube("Body", 20.0)
        _cube("BC_off_ball", 4.0, location=(40, 0, 0))
        data = self.export(["Body", "BC_off_ball"], filename="out.stl")
        self.assertEqual(data["objects"], ["Body"])
        self.assertEqual([e["name"] for e in data["check"]["excluded"]], ["BC_off_ball"])
        bpy.data.objects.remove(bpy.data.objects["BC_off_ball"], do_unlink=True)
        bpy.data.objects.remove(bpy.data.objects["Body"], do_unlink=True)
        bpy.ops.wm.stl_import(filepath=str(self.tmp / "out.stl"))
        (obj,) = bpy.context.selected_objects
        self.assertAlmostEqual(obj.dimensions.x, 20.0, places=3)


class OverlapCheckTest(_Base):
    def test_intersecting_pieces_are_reported(self):
        _cube("A", 10.0)
        _cube("B", 10.0, location=(5, 0, 0))
        _cube("C", 10.0, location=(40, 0, 0))
        data = self.export(["A", "B", "C"])
        (overlap,) = data["check"]["overlaps"]
        self.assertEqual({overlap["a"], overlap["b"]}, {"A", "B"})
        self.assertEqual(overlap["kind"], "intersect")
        report = "\n".join(data["check"]["report"])
        self.assertIn("A", report)
        self.assertTrue(
            "o fatiador vai fundir" in report or "the slicer will fuse" in report
        )

    def test_part_fully_inside_another_is_reported(self):
        _cube("Big", 20.0)
        _cube("06-bola", 4.0)
        data = self.export(["Big", "06-bola"])
        (overlap,) = data["check"]["overlaps"]
        self.assertEqual(overlap["kind"], "inside")
        # A male joint part inside its host: the fix is a boolean union.
        self.assertEqual(overlap["hint"], {"union": "06-bola", "into": "Big"})

    def test_separate_parts_have_no_overlap(self):
        _cube("A", 10.0)
        _cube("B", 10.0, location=(10.5, 0, 0))
        data = self.export(["A", "B"])
        self.assertEqual(data["check"]["overlaps"], [])

    def test_insert_inside_its_parent_is_same_piece(self):
        leg = _cube("04-perna", 20.0, color=GREY)
        _cube("11-almofada", 6.0, location=(0, 0, -8), color=RED, parent=leg)
        data = self.export(["04-perna", "11-almofada"])
        self.assertEqual(data["check"]["overlaps"], [])
        self.assertEqual(data["check"]["insert_overlaps"], 1)
        self.assertEqual(data["check"]["problems"], 0)
        self.assertTrue(any("04-perna" in line for line in data["check"]["report"]))

    def test_non_manifold_parts_are_reported(self):
        _cube("Open", 10.0, open_top=True)
        _cube("Closed", 10.0, location=(30, 0, 0))
        data = self.export(["Open", "Closed"])
        self.assertEqual(data["check"]["non_manifold"], [{"name": "Open", "edges": 4}])


class StrictTest(_Base):
    def test_strict_refuses_overlapping_pieces_and_writes_nothing(self):
        _cube("A", 10.0)
        _cube("B", 10.0, location=(5, 0, 0))
        path = self.tmp / "strict.3mf"
        with self.assertRaises(ValueError) as ctx:
            files._export_file({"path": str(path), "objects": ["A", "B"], "strict": True})
        self.assertIn("A", str(ctx.exception))
        self.assertIn("B", str(ctx.exception))
        self.assertFalse(path.exists())

    def test_strict_accepts_inserts_in_their_piece(self):
        leg = _cube("04-perna", 20.0)
        _cube("11-dedo", 4.0, location=(0, 0, -9), parent=leg)
        data = self.export(["04-perna", "11-dedo"], strict=True)
        self.assertEqual(len(data["pieces"]), 1)

    def test_strict_in_flat_mode_refuses_inserts_too(self):
        leg = _cube("04-perna", 20.0)
        _cube("11-dedo", 4.0, location=(0, 0, -9), parent=leg)
        with self.assertRaises(ValueError):
            self.export(["04-perna", "11-dedo"], strict=True, group="objects")


class PieceGroupingTest(_Base):
    def test_inserts_follow_their_parent_and_joint_parts_do_not(self):
        root = bpy.data.objects.new("Root", None)
        bpy.context.scene.collection.objects.link(root)
        leg = _cube("04-perna", 20.0, parent=root)
        toe = _cube("11-dedo-01", 4.0, location=(0, 8, -9), parent=leg)
        eye = _cube("olho", 2.0, location=(0, 0, 10), parent=leg)
        ball = _cube("06-bola", 6.0, location=(0, 0, 10), parent=leg)
        glued_a = _cube("Wing-L", 5.0, location=(40, 0, 0))
        glued_b = _cube("Wing-R", 5.0, location=(60, 0, 0))
        glued_a["bc_piece"] = "Wings"
        glued_b["bc_piece"] = "Wings"
        pieces = export_check.group_pieces([toe, leg, eye, ball, glued_a, glued_b])
        layout = {p.name: [o.name for o in p.parts] for p in pieces}
        self.assertEqual(
            layout,
            {
                "04-perna": ["04-perna", "11-dedo-01", "olho"],
                "06-bola": ["06-bola"],
                "Wings": ["Wing-L", "Wing-R"],
            },
        )

    def test_bc_role_insert_property(self):
        body = _cube("Body", 20.0)
        badge = _cube("Badge", 3.0, location=(0, 0, 10), parent=body)
        badge["bc_role"] = "insert"
        (piece,) = export_check.group_pieces([body, badge])
        self.assertEqual([o.name for o in piece.parts], ["Body", "Badge"])


class GroupedExportTest(_Base):
    def build_scene(self):
        leg = _cube("04-perna", 20.0, location=(10, 20, 10), color=GREY)
        leg.rotation_euler = (0.3, 0.0, 0.5)
        _cube("11-almofada", 6.0, location=(10, 20, 1), color=RED, parent=leg)
        _cube("11-dedo", 3.0, location=(14, 20, 1), color=GREY, parent=leg)
        _cube("Tail", 8.0, location=(-30, 5, 4), color=BLUE)
        bpy.context.view_layer.update()

    def world_boxes(self, names):
        boxes = {}
        for name in names:
            obj = bpy.data.objects[name]
            pts = [obj.matrix_world @ v.co for v in obj.data.vertices]
            boxes[name] = (
                Vector([min(p[i] for p in pts) for i in range(3)]),
                Vector([max(p[i] for p in pts) for i in range(3)]),
            )
        return boxes

    def test_round_trip_keeps_parts_positions_names_and_extruders(self):
        self.build_scene()
        names = ["04-perna", "11-almofada", "11-dedo", "Tail"]
        before = self.world_boxes(names)
        data = self.export(names)
        self.assertEqual(
            data["pieces"],
            [
                {
                    "name": "04-perna",
                    "parts": ["04-perna", "11-almofada", "11-dedo"],
                    "extruders": [1, 2, 1],
                },
                {"name": "Tail", "parts": ["Tail"], "extruders": [3]},
            ],
        )
        self.assertEqual(data["filaments"], ["#E7E7E7", "#FF0000", "#0000FF"])
        path = self.tmp / "out.3mf"
        _clear_scene()
        created = io_3mf.import_3mf(str(path), 1.0)
        bpy.context.view_layer.update()
        self.assertEqual(sorted(o.name for o in created), sorted(names))
        after = self.world_boxes(names)
        for name in names:
            for a, b in zip(before[name], after[name]):
                self.assertLess((a - b).length, 1e-3, name)
        colors = {
            o.name: io_3mf.linear_to_hex(o.data.materials[0].diffuse_color) for o in created
        }
        self.assertEqual(colors["11-almofada"], "#FF0000")
        self.assertEqual(colors["Tail"], "#0000FF")
        with zipfile.ZipFile(path) as zf:
            bambu = io_3mf._read_bambu_objects(zf)
        extruders = {
            part.name: part.extruder for obj in bambu.values() for part in obj.order
        }
        self.assertEqual(
            extruders, {"04-perna": 1, "11-almofada": 2, "11-dedo": 1, "Tail": 3}
        )

    def test_package_follows_bambu_structure(self):
        self.build_scene()
        self.export(["04-perna", "11-almofada", "11-dedo", "Tail"])
        with zipfile.ZipFile(self.tmp / "out.3mf") as zf:
            names = set(zf.namelist())
            self.assertTrue(
                {
                    "[Content_Types].xml",
                    "_rels/.rels",
                    "3D/3dmodel.model",
                    "3D/_rels/3dmodel.model.rels",
                    "3D/Objects/object_1.model",
                    "3D/Objects/object_2.model",
                    "Metadata/model_settings.config",
                }
                <= names,
                names,
            )
            root_xml = zf.read("3D/3dmodel.model").decode()
            root = ET.fromstring(root_xml)
            rels = ET.fromstring(zf.read("3D/_rels/3dmodel.model.rels"))
            config = ET.fromstring(zf.read("Metadata/model_settings.config"))
            sub = ET.fromstring(zf.read("3D/Objects/object_1.model"))
        self.assertIn('requiredextensions="p"', root_xml)
        meta = {m.get("name"): m.text for m in root.findall(f"{CORE}metadata")}
        self.assertEqual(meta["Application"], "BlenderCraft")
        self.assertEqual(meta["BambuStudio:3mfVersion"], "1")
        targets = sorted(r.get("Target") for r in rels)
        self.assertEqual(targets, ["/3D/Objects/object_1.model", "/3D/Objects/object_2.model"])

        objects = root.findall(f"{CORE}resources/{CORE}object")
        items = root.findall(f"{CORE}build/{CORE}item")
        self.assertEqual(len(objects), 2)
        self.assertEqual([i.get("objectid") for i in items], [o.get("id") for o in objects])
        for item in items:
            self.assertEqual(item.get("printable"), "1")
            self.assertTrue(item.get(f"{PROD}UUID"))
        leg = objects[0]
        comps = leg.findall(f"{CORE}components/{CORE}component")
        self.assertEqual(len(comps), 3)
        self.assertEqual({c.get(f"{PROD}path") for c in comps}, {"/3D/Objects/object_1.model"})
        self.assertIsNone(leg.find(f"{CORE}mesh"))
        sub_ids = [o.get("id") for o in sub.findall(f"{CORE}resources/{CORE}object")]
        self.assertEqual([c.get("objectid") for c in comps], sub_ids)

        (cfg_leg, cfg_tail) = config.findall("object")
        self.assertEqual(cfg_leg.get("id"), leg.get("id"))

        def meta_of(el):
            return {m.get("key"): m.get("value") for m in el.findall("metadata") if m.get("key")}

        self.assertEqual(meta_of(cfg_leg)["name"], "04-perna")
        self.assertEqual(meta_of(cfg_leg)["extruder"], "1")
        parts = cfg_leg.findall("part")
        self.assertEqual([p.get("id") for p in parts], sub_ids)
        self.assertEqual({p.get("subtype") for p in parts}, {"normal_part"})
        self.assertEqual(
            [meta_of(p)["name"] for p in parts], ["04-perna", "11-almofada", "11-dedo"]
        )
        self.assertEqual([meta_of(p)["extruder"] for p in parts], ["1", "2", "1"])
        # "matrix" (row-major 4x4) is the component transform (3MF row vectors).
        for part, comp in zip(parts, comps):
            m = [float(v) for v in meta_of(part)["matrix"].split()]
            t = [float(v) for v in comp.get("transform").split()]
            self.assertEqual(len(m), 16)
            self.assertEqual(m[12:], [0.0, 0.0, 0.0, 1.0])
            self.assertAlmostEqual(m[3], t[9], places=5)
            self.assertAlmostEqual(m[7], t[10], places=5)
            self.assertAlmostEqual(m[11], t[11], places=5)
        instances = config.findall("plate/model_instance")
        self.assertEqual(len(instances), 2)
        assemble = config.findall("assemble/assemble_item")
        self.assertEqual(
            [a.get("transform") for a in assemble], [i.get("transform") for i in items]
        )

    def test_same_colour_everywhere_uses_one_extruder(self):
        leg = _cube("04-perna", 20.0, color=GREY)
        _cube("11-almofada", 6.0, location=(0, 0, -9), color=GREY, parent=leg)
        _cube("Tail", 8.0, location=(-30, 0, 0), color=GREY)
        data = self.export(["04-perna", "11-almofada", "Tail"])
        extruders = {e for piece in data["pieces"] for e in piece["extruders"]}
        self.assertEqual(extruders, {1})
        self.assertEqual(data["filaments"], ["#E7E7E7"])


# Content hashes of the flat (one object per item) export of the scene in
# FlatModeTest, recorded with the exporter before grouping existed.
FLAT_GOLDEN = {
    "3D/3dmodel.model": "5079a4bb6134f2d38b99df3587ce94371fe336a4f750cade95c65f3638660025",
    "[Content_Types].xml": "5e0114a2fc79a5fdaa8ba7d3c48e1510e2c415784d38555eda070b1a0757be95",
    "_rels/.rels": "54b6d668a0cf0575cf59bde2723279b7facf518d3f67460227896b13a29004a7",
}


class FlatModeTest(_Base):
    def build(self):
        out = []
        for name, loc, size, color in (
            ("A", (0, 0, 0), 10, (1, 0, 0, 1)),
            ("B", (30, 0, 0), 5, (0, 0, 1, 1)),
        ):
            mesh = bpy.data.meshes.new(name)
            bm = bmesh.new()
            bmesh.ops.create_cube(bm, size=size)
            bm.to_mesh(mesh)
            bm.free()
            obj = bpy.data.objects.new(name, mesh)
            bpy.context.scene.collection.objects.link(obj)
            obj.location = loc
            mat = bpy.data.materials.new(name + "M")
            mat.diffuse_color = color
            mat.use_nodes = False
            mesh.materials.append(mat)
            out.append(obj)
        bpy.context.view_layer.update()
        return out

    def hashes(self, path):
        with zipfile.ZipFile(path) as zf:
            return {n: hashlib.sha256(zf.read(n)).hexdigest() for n in zf.namelist()}

    def test_export_3mf_is_byte_identical_to_before(self):
        objs = self.build()
        path = self.tmp / "flat.3mf"
        io_3mf.export_3mf(str(path), objs, 1.0)
        self.assertEqual(self.hashes(path), FLAT_GOLDEN)

    def test_group_objects_option_writes_the_old_layout(self):
        self.build()
        data = self.export(["A", "B"], filename="flat.3mf", group="objects")
        self.assertNotIn("pieces", data)
        self.assertEqual(self.hashes(self.tmp / "flat.3mf"), FLAT_GOLDEN)

    def test_bad_group_is_rejected(self):
        self.build()
        with self.assertRaises(ValueError):
            self.export(["A"], group="parts")


class ExportOperatorCheckTest(_Base):
    def setUp(self):
        super().setUp()
        from blendercraft_assistant.ui import io_ops

        io_ops.register()
        self.addCleanup(io_ops.unregister)

    def test_operator_groups_and_excludes_helpers_by_default(self):
        leg = _cube("04-perna", 20.0, color=GREY)
        _cube("11-dedo", 4.0, location=(0, 0, -9), color=RED, parent=leg)
        _cube("07-furo", 4.0, location=(0, 0, 9), parent=leg)
        bpy.ops.object.select_all(action="DESELECT")
        path = self.tmp / "op.3mf"
        self.assertEqual(bpy.ops.bc.export_3mf(filepath=str(path)), {"FINISHED"})
        with zipfile.ZipFile(path) as zf:
            self.assertIn("Metadata/model_settings.config", zf.namelist())
            text = b"".join(zf.read(n) for n in zf.namelist()).decode()
        self.assertNotIn("07-furo", text)
        self.assertIn("11-dedo", text)

    def test_operator_strict_refuses_overlaps(self):
        _cube("A", 10.0)
        _cube("B", 10.0, location=(5, 0, 0))
        path = self.tmp / "op.3mf"
        with self.assertRaises(RuntimeError):  # an ERROR report raises
            bpy.ops.bc.export_3mf(filepath=str(path), strict=True)
        self.assertFalse(path.exists())

    def test_operator_flat_mode(self):
        _cube("A", 10.0)
        path = self.tmp / "op.3mf"
        self.assertEqual(
            bpy.ops.bc.export_3mf(filepath=str(path), group="OBJECTS"), {"FINISHED"}
        )
        with zipfile.ZipFile(path) as zf:
            self.assertNotIn("Metadata/model_settings.config", zf.namelist())


class StringsTest(unittest.TestCase):
    def test_check_strings_in_both_languages(self):
        from blendercraft_assistant import i18n

        keys = [k for k in i18n.STRINGS["en"] if k.startswith(("chk_", "export_group", "export_strict", "export_include"))]
        keys.append("exported_3mf_pieces")
        self.assertGreater(len(keys), 10)
        for key in keys:
            self.assertIn(key, i18n.STRINGS["pt"], key)


if __name__ == "__main__":
    unittest.main()
