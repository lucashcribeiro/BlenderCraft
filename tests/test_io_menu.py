"""File > Import/Export 3MF operators, the .3mf drop handler and STL import size."""

import contextlib
import ctypes
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import bpy

from blendercraft_assistant import i18n, io_3mf, units

TMP = Path(tempfile.gettempdir())


@contextlib.contextmanager
def _quiet():
    """Silence operator reports and importer logging (test output only)."""
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


def _clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


class IoMenuTest(unittest.TestCase):
    def setUp(self):
        from blendercraft_assistant.ui import io_ops

        self.io_ops = io_ops
        io_ops.register()
        self.addCleanup(io_ops.unregister)
        _clear_scene()
        self.addCleanup(_clear_scene)
        scene = bpy.context.scene
        units.apply_internal_mm(scene)
        self.addCleanup(units.apply_internal_mm, scene)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def add_cube(self, name, size=20.0, location=(0.0, 0.0, 0.0)):
        bpy.ops.mesh.primitive_cube_add(size=size, location=location)
        obj = bpy.context.active_object
        obj.name = name
        return obj

    def run_op(self, op, **kwargs):
        with _quiet():
            return op(**kwargs)

    def model_names(self, path):
        with zipfile.ZipFile(path) as zf:
            text = zf.read("3D/3dmodel.model").decode()
        return [n for n in ("A", "B", "C", "Lamp") if f'name="{n}"' in text]

    def test_menu_entries_registered_once(self):
        imports = bpy.types.TOPBAR_MT_file_import._dyn_ui_initialize()
        exports = bpy.types.TOPBAR_MT_file_export._dyn_ui_initialize()
        self.assertEqual(imports.count(self.io_ops.menu_import), 1)
        self.assertEqual(exports.count(self.io_ops.menu_export), 1)
        self.io_ops.unregister()
        self.assertNotIn(self.io_ops.menu_import, imports)
        self.assertNotIn(self.io_ops.menu_export, exports)
        self.io_ops.register()  # what the cleanup unregisters

    def test_operators_filter_3mf(self):
        for op in (bpy.ops.bc.import_3mf, bpy.ops.bc.export_3mf):
            props = op.get_rna_type().properties
            self.assertEqual(props["filter_glob"].default, "*.3mf")
        self.assertEqual(self.io_ops.BC_OT_export_3mf.filename_ext, ".3mf")

    def test_export_selected_in_mm(self):
        a = self.add_cube("A", size=20.0)
        self.add_cube("B", size=5.0, location=(50.0, 0.0, 0.0))
        bpy.ops.object.select_all(action="DESELECT")
        a.select_set(True)
        path = self.tmp / "sel.3mf"
        self.assertEqual(self.run_op(bpy.ops.bc.export_3mf, filepath=str(path)), {"FINISHED"})
        self.assertEqual(self.model_names(path), ["A"])
        bpy.data.objects.remove(a, do_unlink=True)
        bpy.data.objects.remove(bpy.data.objects["B"], do_unlink=True)
        with _quiet():
            (obj,) = io_3mf.import_3mf(str(path), 1.0)
        self.assertAlmostEqual(obj.dimensions.x, 20.0, places=3)

    def test_export_all_visible_meshes_when_nothing_selected(self):
        self.add_cube("A")
        b = self.add_cube("B", location=(50.0, 0.0, 0.0))
        c = self.add_cube("C", location=(-50.0, 0.0, 0.0))
        c.hide_set(True)
        lamp = bpy.data.objects.new("Lamp", bpy.data.lights.new("Lamp", "POINT"))
        bpy.context.collection.objects.link(lamp)
        bpy.ops.object.select_all(action="DESELECT")
        path = self.tmp / "all.3mf"
        self.assertEqual(self.run_op(bpy.ops.bc.export_3mf, filepath=str(path)), {"FINISHED"})
        self.assertEqual(self.model_names(path), ["A", "B"])
        self.assertTrue(b.visible_get())

    def test_export_skips_non_meshes_and_reports(self):
        a = self.add_cube("A")
        lamp = bpy.data.objects.new("Lamp", bpy.data.lights.new("Lamp", "POINT"))
        bpy.context.collection.objects.link(lamp)
        a.select_set(True)
        lamp.select_set(True)
        written, skipped = self.io_ops.export_objects(
            str(self.tmp / "x.3mf"), self.io_ops.export_candidates(bpy.context), 1.0
        )
        self.assertEqual(written, ["A"])
        self.assertEqual(skipped, ["Lamp"])
        msg = self.io_ops.export_message(written, skipped, "x.3mf")
        self.assertIn("Lamp", msg)
        self.assertIn("1", msg)

    def test_export_without_meshes_is_cancelled(self):
        lamp = bpy.data.objects.new("Lamp", bpy.data.lights.new("Lamp", "POINT"))
        bpy.context.collection.objects.link(lamp)
        lamp.select_set(True)
        path = self.tmp / "none.3mf"
        with self.assertRaises(RuntimeError):  # an ERROR report raises from Python
            self.run_op(bpy.ops.bc.export_3mf, filepath=str(path))
        self.assertFalse(path.exists())

    def test_export_meters_scene_writes_mm(self):
        bpy.context.scene.unit_settings.scale_length = 1.0
        self.add_cube("A", size=0.02)
        path = self.tmp / "m.3mf"
        self.assertEqual(self.run_op(bpy.ops.bc.export_3mf, filepath=str(path)), {"FINISHED"})
        units.apply_internal_mm(bpy.context.scene)
        with _quiet():
            (obj,) = io_3mf.import_3mf(str(path), 1.0)
        self.assertAlmostEqual(obj.dimensions.x, 20.0, places=3)

    def _write_cube_3mf(self, name="A", size=20.0):
        self.add_cube(name, size=size)
        path = self.tmp / f"{name}.3mf"
        io_3mf.export_3mf(str(path), [bpy.data.objects[name]], 1.0)
        _clear_scene()
        return path

    def test_import_in_mm_selects_and_pushes_undo(self):
        path = self._write_cube_3mf()
        self.assertIn("UNDO", self.io_ops.BC_OT_import_3mf.bl_options)
        self.assertEqual(self.run_op(bpy.ops.bc.import_3mf, filepath=str(path)), {"FINISHED"})
        obj = bpy.data.objects["A"]
        self.assertAlmostEqual(obj.dimensions.x, 20.0, places=3)
        self.assertTrue(obj.select_get())
        self.assertIs(bpy.context.view_layer.objects.active, obj)

    def test_import_into_meters_scene_converts(self):
        path = self._write_cube_3mf()
        bpy.context.scene.unit_settings.scale_length = 1.0
        self.assertEqual(self.run_op(bpy.ops.bc.import_3mf, filepath=str(path)), {"FINISHED"})
        self.assertAlmostEqual(bpy.data.objects["A"].dimensions.x, 0.02, places=5)

    def test_import_several_dropped_files(self):
        a = self._write_cube_3mf("A")
        b = self._write_cube_3mf("B", size=10.0)
        result = self.run_op(
            bpy.ops.bc.import_3mf,
            directory=str(self.tmp),
            files=[{"name": a.name}, {"name": b.name}],
        )
        self.assertEqual(result, {"FINISHED"})
        self.assertEqual(sorted(o.name for o in bpy.data.objects), ["A", "B"])

    def test_import_bad_file_is_cancelled(self):
        bad = self.tmp / "bad.3mf"
        bad.write_bytes(b"not a zip")
        with self.assertRaises(RuntimeError):
            self.run_op(bpy.ops.bc.import_3mf, filepath=str(bad))
        self.assertEqual(len(bpy.data.objects), 0)

    def test_file_handler_for_3mf(self):
        fh = bpy.types.FileHandler.bl_rna_get_subclass_py("IO_FH_bc_3mf")
        self.assertIsNotNone(fh)
        self.assertEqual(fh.bl_import_operator, "bc.import_3mf")
        self.assertEqual(fh.bl_file_extensions, ".3mf")

        class _Area:
            type = "VIEW_3D"

        class _Ctx:
            area = _Area()

        self.assertTrue(fh.poll_drop(_Ctx()))
        _Ctx.area = None
        self.assertFalse(fh.poll_drop(_Ctx()))

    def test_stock_stl_import_lands_in_mm(self):
        # Blender's own STL importer (also used by its .stl drop handler)
        # reads 1 file unit as 1 BU by default: right for BlenderCraft's
        # 1 BU = 1 mm scenes, no extra importer needed.
        props = bpy.ops.wm.stl_import.get_rna_type().properties
        self.assertEqual(props["global_scale"].default, 1.0)
        self.assertFalse(props["use_scene_unit"].default)
        self.add_cube("A", size=20.0)
        path = self.tmp / "c.stl"
        with _quiet():
            bpy.ops.wm.stl_export(filepath=str(path), export_selected_objects=True)
        _clear_scene()
        with _quiet():
            bpy.ops.wm.stl_import(filepath=str(path))
        (obj,) = bpy.context.selected_objects
        self.assertAlmostEqual(obj.dimensions.x, 20.0, places=3)

    def test_strings_translated(self):
        for key in ("menu_3mf", "imported_3mf", "exported_3mf", "export_skipped", "no_mesh_to_export"):
            self.assertIn(key, i18n.STRINGS["pt"])
            self.assertIn(key, i18n.STRINGS["en"])


if __name__ == "__main__":
    unittest.main()
