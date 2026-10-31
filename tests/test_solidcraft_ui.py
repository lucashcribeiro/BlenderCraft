"""SolidCraft (.sc) File menu hooks (Open model, Import, drop handler) and the
import_file tool."""

import json
import unittest

import bpy

from blendercraft_assistant import i18n, tools
from blendercraft_assistant.tools.dispatch import call_tool as _call_tool

from test_solidcraft import IDENTITY, ScTestBase, _quiet

tools.load_all()


def call_tool(name, args):
    with _quiet():
        return _call_tool(name, args)


class ScUiTest(ScTestBase):
    def setUp(self):
        super().setUp()
        from blendercraft_assistant.ui import io_ops

        self.io_ops = io_ops
        io_ops.register()
        self.addCleanup(io_ops.unregister)

    def test_import_operator_selects_and_activates_a_visible_body(self):
        vis = {"hidden_bodies": ["a"], "hidden_occurrences": [], "hidden_origin": [],
               "hidden_sketches": [], "shown_sketches": []}
        path = self.make([("a", "a", IDENTITY), ("b", "b", IDENTITY)], visibility=vis)
        with _quiet():
            self.assertEqual(bpy.ops.bc.import_sc(filepath=path), {"FINISHED"})
        self.assertIn("UNDO", self.io_ops.BC_OT_import_sc.bl_options)
        self.assertIs(bpy.context.view_layer.objects.active, bpy.data.objects["b"])
        self.assertTrue(bpy.data.objects["b"].select_get())

    def test_import_operator_bad_file_is_cancelled(self):
        bad = self.dir / "bad.sc"
        bad.write_bytes(b"nope")
        with self.assertRaises(RuntimeError):
            with _quiet():
                bpy.ops.bc.import_sc(filepath=str(bad))
        self.assertEqual(len(bpy.data.objects), 0)

    def test_menu_entry_and_filters(self):
        imports = bpy.types.TOPBAR_MT_file_import._dyn_ui_initialize()
        self.assertEqual(imports.count(self.io_ops.menu_import_sc), 1)
        props = bpy.ops.bc.import_sc.get_rna_type().properties
        self.assertEqual(props["filter_glob"].default, "*.sc")
        self.assertIn(".sc", self.io_ops.MODEL_EXTENSIONS)
        self.assertIn("*.sc", props_default("bc.open_model"))
        self.io_ops.unregister()
        self.assertNotIn(self.io_ops.menu_import_sc, imports)
        self.io_ops.register()

    def test_file_handler(self):
        fh = bpy.types.FileHandler.bl_rna_get_subclass_py("IO_FH_bc_sc")
        self.assertIsNotNone(fh)
        self.assertEqual(fh.bl_import_operator, "bc.import_sc")
        self.assertEqual(fh.bl_file_extensions, ".sc")

    def test_open_model_imports_sc(self):
        path = self.make([("a", "a", IDENTITY)])
        with _quiet():
            self.assertEqual(bpy.ops.bc.open_model(filepath=path), {"FINISHED"})
        self.assertIn("a", bpy.data.objects)

    def test_strings_translated(self):
        for key in ("menu_sc", "import_sc", "imported_sc"):
            self.assertIn(key, i18n.STRINGS["pt"])
            self.assertIn(key, i18n.STRINGS["en"])


def props_default(idname):
    op = bpy.ops
    for part in idname.split("."):
        op = getattr(op, part)
    return op.get_rna_type().properties["filter_glob"].default


class ImportFileToolTest(ScTestBase):
    def test_import_file_tool_accepts_sc(self):
        vis = {"hidden_bodies": ["b"], "hidden_occurrences": [], "hidden_origin": [],
               "hidden_sketches": [], "shown_sketches": []}
        path = self.make([("a", "a", IDENTITY), ("b", "b", IDENTITY)], visibility=vis)
        result = json.loads(call_tool("import_file", {"path": path}).text)
        self.assertEqual(result["format"], "sc")
        names = [o["name"] for o in result["objects"]]
        self.assertEqual(names, ["a", "b"])
        self.assertEqual(result["objects"][0]["dimensions_mm"], [10.0, 20.0, 30.0])
        self.assertEqual(result["collection"], "Doc")
        self.assertEqual(result["hidden"], ["b"])


if __name__ == "__main__":
    unittest.main()
