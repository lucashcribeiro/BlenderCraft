"""File > Open model…, Ctrl/Cmd+Shift+O, and the top bar Assistant button."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import bpy

from blendercraft_assistant import i18n, io_3mf, units

from test_io_menu import _clear_scene, _quiet


class OpenModelTest(unittest.TestCase):
    def setUp(self):
        from blendercraft_assistant import tools
        from blendercraft_assistant.ui import io_ops, keymap, operators, props

        tools.load_all()
        self.io_ops = io_ops
        self.keymap = keymap
        for module in (props, operators, io_ops, keymap):
            module.register()
            self.addCleanup(module.unregister)
        _clear_scene()
        self.addCleanup(_clear_scene)
        units.apply_internal_mm(bpy.context.scene)
        self.addCleanup(units.apply_internal_mm, bpy.context.scene)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def write_models(self):
        bpy.ops.mesh.primitive_cube_add(size=20.0)
        cube = bpy.context.active_object
        cube.name = "Cubo"
        stl = self.tmp / "peca.stl"
        mf = self.tmp / "peca.3mf"
        with _quiet():
            bpy.ops.wm.stl_export(filepath=str(stl), export_selected_objects=True)
        io_3mf.export_3mf(str(mf), [cube], 1.0)
        _clear_scene()
        return stl, mf

    def test_opens_stl_and_3mf_in_mm(self):
        stl, mf = self.write_models()
        self.assertIn("UNDO", self.io_ops.BC_OT_open_model.bl_options)
        with _quiet():
            result = bpy.ops.bc.open_model(
                directory=str(self.tmp), files=[{"name": stl.name}, {"name": mf.name}]
            )
        self.assertEqual(result, {"FINISHED"})
        objs = list(bpy.data.objects)
        self.assertEqual(len(objs), 2)
        for obj in objs:
            self.assertAlmostEqual(obj.dimensions.x, 20.0, places=3)
            self.assertTrue(obj.select_get())
        self.assertIn(bpy.context.view_layer.objects.active, objs)

    def test_single_path_and_meters_scene(self):
        stl, _ = self.write_models()
        bpy.context.scene.unit_settings.scale_length = 1.0
        with _quiet():
            self.assertEqual(bpy.ops.bc.open_model(filepath=str(stl)), {"FINISHED"})
        (obj,) = bpy.data.objects
        self.assertAlmostEqual(obj.dimensions.x, 0.02, places=5)  # 20 mm in a metres scene

    def test_filter_and_errors(self):
        glob = bpy.ops.bc.open_model.get_rna_type().properties["filter_glob"].default
        for ext in ("stl", "3mf", "obj", "ply", "glb", "gltf"):
            self.assertIn(f"*.{ext}", glob)
        bad = self.tmp / "x.blend1"
        bad.write_text("x")
        with _quiet(), self.assertRaises(RuntimeError):
            bpy.ops.bc.open_model(filepath=str(bad))
        self.assertEqual(len(bpy.data.objects), 0)

    def test_menu_item_right_under_open(self):
        funcs = bpy.types.TOPBAR_MT_file._dyn_ui_initialize()
        self.assertIs(funcs[0], self.io_ops.draw_file_menu)
        calls = []

        class Layout:
            operator_context = "EXEC_DEFAULT"

            def operator(self, idname, **kwargs):
                calls.append(idname)
                return mock.Mock()

            def __getattr__(self, name):
                return lambda *a, **k: calls.append(name) or mock.Mock()

        menu = mock.Mock(layout=Layout())
        self.io_ops.draw_file_menu(menu, bpy.context)
        index = calls.index("wm.open_mainfile")
        self.assertEqual(calls[index + 1], "bc.open_model")
        self.assertEqual(calls.count("bc.open_model"), 1)
        # Unregistering gives Blender its own draw back.
        self.io_ops.unregister()
        self.assertIsNot(funcs[0], self.io_ops.draw_file_menu)
        self.assertEqual(funcs[0].__qualname__, "TOPBAR_MT_file.draw")
        self.io_ops.register()

    def test_menu_item_falls_back_to_the_end(self):
        calls = []

        def original(self, context):
            self.layout.operator("wm.save_mainfile")

        layout = mock.Mock()
        layout.operator.side_effect = lambda idname, **k: calls.append(idname)
        with mock.patch.object(self.io_ops, "_original_file_draw", original):
            self.io_ops.draw_file_menu(mock.Mock(layout=layout), bpy.context)
        self.assertEqual(calls, ["wm.save_mainfile", "bc.open_model"])

    def test_shortcut(self):
        found = [
            (kmi.idname, kmi.type, kmi.value, kmi.ctrl, kmi.oskey, kmi.shift)
            for _km, kmi in self.keymap.items
            if kmi.idname == "bc.open_model"
        ]
        self.assertEqual(
            sorted(found),
            sorted(
                [
                    ("bc.open_model", "O", "PRESS", True, False, True),
                    ("bc.open_model", "O", "PRESS", False, True, True),
                ]
            ),
        )

    def test_strings(self):
        for key in ("open_model", "open_model_desc", "opened_models", "toggle_assistant", "toggle_assistant_desc", "no_3d_view"):
            self.assertIn(key, i18n.STRINGS["pt"], key)
            self.assertIn(key, i18n.STRINGS["en"], key)


class _Region:
    def __init__(self, type_, category=""):
        self.type = type_
        self.active_panel_category = category
        self.width = 300


class _Space:
    def __init__(self, show):
        self.show_region_ui = show


class _Area:
    def __init__(self, type_, width, height, show=False, category=""):
        self.type = type_
        self.width = width
        self.height = height
        self.regions = [_Region("WINDOW"), _Region("UI", category)]
        self.spaces = mock.Mock(active=_Space(show))


class ToggleAssistantTest(unittest.TestCase):
    def setUp(self):
        from blendercraft_assistant.ui import topbar

        self.topbar = topbar
        self.category = i18n.t("assistant")

    def screen(self, *areas):
        return mock.Mock(areas=list(areas))

    def test_opens_the_largest_3d_view(self):
        small = _Area("VIEW_3D", 200, 200)
        big = _Area("VIEW_3D", 800, 600, category="Item")
        screen = self.screen(_Area("OUTLINER", 2000, 2000), small, big)
        self.assertEqual(self.topbar.toggle(screen, self.category), "opened")
        self.assertTrue(big.spaces.active.show_region_ui)
        self.assertEqual(big.regions[1].active_panel_category, self.category)
        self.assertFalse(small.spaces.active.show_region_ui)
        self.assertTrue(self.topbar.is_showing(screen, self.category))

    def test_closes_when_showing(self):
        area = _Area("VIEW_3D", 800, 600, show=True, category=self.category)
        screen = self.screen(area)
        self.assertEqual(self.topbar.toggle(screen, self.category), "closed")
        self.assertFalse(area.spaces.active.show_region_ui)
        self.assertFalse(self.topbar.is_showing(screen, self.category))

    def test_other_tab_open_switches_to_assistant(self):
        area = _Area("VIEW_3D", 800, 600, show=True, category="Item")
        self.assertEqual(self.topbar.toggle(self.screen(area), self.category), "opened")
        self.assertTrue(area.spaces.active.show_region_ui)
        self.assertEqual(area.regions[1].active_panel_category, self.category)

    def test_no_3d_view(self):
        self.assertEqual(self.topbar.toggle(self.screen(_Area("OUTLINER", 9, 9)), self.category), "none")
        self.assertFalse(self.topbar.is_showing(self.screen(), self.category))

    def test_operator_without_3d_view_is_cancelled(self):
        from blendercraft_assistant.ui import operators, props

        for module in (props, operators):
            module.register()
            self.addCleanup(module.unregister)
        self.topbar.register()
        self.addCleanup(self.topbar.unregister)
        with mock.patch.object(self.topbar, "toggle", return_value="none"), _quiet():
            self.assertEqual(bpy.ops.bc.toggle_assistant(), {"CANCELLED"})
        with mock.patch.object(self.topbar, "toggle", return_value="opened"):
            self.assertEqual(bpy.ops.bc.toggle_assistant(), {"FINISHED"})

    def test_draw_only_on_the_right_with_status(self):
        from blendercraft_assistant import runtime
        from blendercraft_assistant.ui import icons

        icons.register()
        self.addCleanup(icons.unregister)
        self.topbar.register()
        self.addCleanup(self.topbar.unregister)
        funcs = bpy.types.TOPBAR_HT_upper_bar._dyn_ui_initialize()
        self.assertEqual(funcs.count(self.topbar.draw_topbar), 1)
        drawn = []

        class Layout:
            def row(self, **kwargs):
                return self

            def operator(self, idname, **kwargs):
                drawn.append((idname, kwargs.get("depress"), kwargs.get("icon_value") or kwargs.get("icon")))

            def separator(self, **kwargs):
                pass

        def draw(alignment, status):
            drawn.clear()
            context = mock.Mock(region=mock.Mock(alignment=alignment), screen=self.screen())
            session = mock.Mock(status=status) if status else None
            with mock.patch.object(runtime, "session", session):
                self.topbar.draw_topbar(mock.Mock(layout=Layout()), context)
            return list(drawn)

        self.assertEqual(draw("LEFT", None), [])
        # Background mode has no icon previews (icon_id 0): a stock icon stands in.
        self.assertEqual(draw("RIGHT", None), [("bc.toggle_assistant", False, "LIGHT")])
        ids = {"chat": 11, "chat_working": 12, "chat_error": 13}
        with mock.patch.object(icons, "icon_id", side_effect=lambda name: ids[name]):
            self.assertEqual(draw("RIGHT", None), [("bc.toggle_assistant", False, 11)])
            self.assertEqual(draw("RIGHT", "WORKING")[0][2], 12)
            self.assertEqual(draw("RIGHT", "ERROR")[0][2], 13)
        self.topbar.unregister()
        self.assertNotIn(self.topbar.draw_topbar, funcs)
        self.topbar.register()


    def test_status_change_redraws_the_top_bar(self):
        from blendercraft_assistant import runtime

        self.topbar.register()
        self.addCleanup(self.topbar.unregister)
        self.assertIn(self.topbar.refresh_status, runtime.redraw_hooks)
        workspace = mock.Mock(use_filter_by_owner=False)
        writes = []
        type(workspace).use_filter_by_owner = mock.PropertyMock(
            side_effect=lambda *a: writes.append(a) if a else False
        )
        wm = mock.Mock(windows=[mock.Mock(workspace=workspace)])
        session = mock.Mock(status="IDLE")
        with mock.patch.object(runtime, "session", session), \
                mock.patch.object(bpy, "context", mock.Mock(window_manager=wm)):
            self.topbar.refresh_status()
            self.assertEqual(writes, [(False,)])  # first time: draw the status
            self.topbar.refresh_status()
            self.assertEqual(len(writes), 1)  # unchanged: nothing
            session.status = "WORKING"
            self.topbar.refresh_status()
            self.assertEqual(len(writes), 2)
        self.topbar.unregister()
        self.assertNotIn(self.topbar.refresh_status, runtime.redraw_hooks)
        self.topbar.register()


class IconsTest(unittest.TestCase):
    def test_icons_load_and_release(self):
        from blendercraft_assistant.ui import icons

        icons.register()
        try:
            self.assertEqual(sorted(icons._previews.keys()), sorted(icons.NAMES))
            ids = [icons.icon_id(name) for name in icons.NAMES]
            if not bpy.app.background:  # -b has no icon system: ids are 0 there
                self.assertTrue(all(i > 0 for i in ids), ids)
            icons.register()  # idempotent
        finally:
            icons.unregister()
        self.assertIsNone(icons._previews)
        self.assertEqual(icons.icon_id("chat"), 0)
        for name in icons.NAMES:
            path = icons.ICON_DIR / f"{name}.png"
            self.assertEqual(path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            image = bpy.data.images.load(str(path))
            try:
                self.assertEqual(tuple(image.size), (64, 64))
            finally:
                bpy.data.images.remove(image)


if __name__ == "__main__":
    unittest.main()
