import contextlib
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import bpy

from test_ui_logic import _quiet_reports

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = ROOT / "app_template" / "blendercraft" / "__init__.py"
EXT_UNITS = "bl_ext.user_default.blendercraft_assistant.units"


def load_template():
    spec = importlib.util.spec_from_file_location("blendercraft_template_under_test", TEMPLATE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EXTENSION = "bl_ext.user_default.blendercraft_assistant"


@contextlib.contextmanager
def _without_extension_modules():
    """Hide the extension's modules, as if it were not enabled yet. The
    developer's own install may make it importable here."""
    saved = {name: mod for name, mod in sys.modules.items() if name.startswith(EXTENSION)}
    for name in saved:
        del sys.modules[name]
    try:
        yield
    finally:
        for name in [n for n in sys.modules if n.startswith(EXTENSION)]:
            del sys.modules[name]
        sys.modules.update(saved)


def fresh_startup_file():
    bpy.ops.wm.read_factory_settings(use_empty=False)


class SetupNewFileTest(unittest.TestCase):
    def setUp(self):
        fresh_startup_file()
        self.template = load_template()

    def tearDown(self):
        self.template.unregister()  # setup_new_file schedules the UI timer
        fresh_startup_file()

    def test_setup_new_file(self):
        self.assertEqual(bpy.data.filepath, "")
        self.assertIn("Animation", bpy.data.workspaces)
        self.template.setup_new_file()
        scene = bpy.context.scene
        self.assertAlmostEqual(scene.unit_settings.scale_length, 0.001, places=6)
        self.assertEqual(scene.unit_settings.system, "METRIC")
        self.assertEqual(scene.unit_settings.length_unit, "MILLIMETERS")
        self.assertNotIn("Animation", bpy.data.workspaces)
        self.assertNotIn("Animation", bpy.data.screens)
        # Other default workspaces stay.
        for name in ("Layout", "Modeling", "Sculpting", "Shading", "Scripting"):
            self.assertIn(name, bpy.data.workspaces)
            self.assertIn(name, bpy.data.screens)
        cube = bpy.data.objects["Cube"]
        for value in cube.dimensions:
            self.assertAlmostEqual(value, 20.0, places=4)
        # The cube is resized in its mesh, so the AI sees a clean scale.
        for value in cube.scale:
            self.assertAlmostEqual(value, 1.0, places=6)

    def test_setup_new_file_twice_is_stable(self):
        self.template.setup_new_file()
        self.template.setup_new_file()
        for value in bpy.data.objects["Cube"].dimensions:
            self.assertAlmostEqual(value, 20.0, places=4)

    def test_edited_cube_is_not_rescaled(self):
        bpy.data.objects["Cube"].dimensions = (5, 6, 7)
        bpy.context.view_layer.update()
        self.template.setup_new_file()
        dims = tuple(bpy.data.objects["Cube"].dimensions)
        for got, want in zip(dims, (5, 6, 7)):
            self.assertAlmostEqual(got, want, places=4)

    def test_default_camera_and_light_move_out_with_the_cube(self):
        camera, light = bpy.data.objects["Camera"], bpy.data.objects["Light"]
        before = {o.name: tuple(o.location) for o in (camera, light)}
        self.template.setup_new_file()
        for obj in (camera, light):
            for got, was in zip(obj.location, before[obj.name]):
                self.assertAlmostEqual(got, was * 10, places=3)
            for value in obj.scale:
                self.assertAlmostEqual(value, 1.0, places=6)

    def test_default_camera_clip_range_grows_with_its_distance(self):
        camera = bpy.data.objects["Camera"]
        start, end = camera.data.clip_start, camera.data.clip_end
        self.template.setup_new_file()
        self.assertAlmostEqual(camera.data.clip_start, start * 10, places=4)
        self.assertAlmostEqual(camera.data.clip_end, end * 10, places=3)
        # The cube (at the origin) is well inside the clip range.
        self.assertLess(camera.location.length + 20.0, camera.data.clip_end)
        self.template.setup_new_file()  # only once
        self.assertAlmostEqual(camera.data.clip_end, end * 10, places=3)

    def test_camera_and_light_are_moved_only_once(self):
        self.template.setup_new_file()
        once = {n: tuple(bpy.data.objects[n].location) for n in ("Camera", "Light")}
        self.template.setup_new_file()
        for name, location in once.items():
            for got, was in zip(bpy.data.objects[name].location, location):
                self.assertAlmostEqual(got, was, places=4)

    def test_edited_camera_and_light_are_left_alone(self):
        camera, light = bpy.data.objects["Camera"], bpy.data.objects["Light"]
        camera.location = (1, 2, 3)
        light.location = (4.0, 1.0, 5.9)  # close to the factory spot, but moved
        clip_end = camera.data.clip_end
        self.template.setup_new_file()
        self.assertEqual(camera.data.clip_end, clip_end)
        self.assertEqual(tuple(round(v, 4) for v in camera.location), (1.0, 2.0, 3.0))
        self.assertEqual(tuple(round(v, 4) for v in light.location), (4.0, 1.0, 5.9))

    def test_an_object_named_light_that_is_not_a_light_is_left_alone(self):
        # An object called "Light" that is not a light (e.g. an empty) is not ours.
        light = bpy.data.objects["Light"]
        empty = bpy.data.objects.new("Light", None)
        bpy.context.scene.collection.objects.link(empty)
        empty.location = light.location
        bpy.data.objects.remove(light)
        empty.name = "Light"
        location = tuple(empty.location)
        self.template.setup_new_file()
        self.assertEqual(tuple(empty.location), location)

    def test_missing_cube_is_fine(self):
        bpy.data.objects.remove(bpy.data.objects["Cube"])
        self.template.setup_new_file()
        self.assertNotIn("Animation", bpy.data.workspaces)

    def test_named_file_untouched(self):
        scene = bpy.context.scene
        scene.unit_settings.scale_length = 1.0
        tmp = Path(tempfile.mkdtemp())
        try:
            path = str(tmp / "named.blend")
            with _quiet_reports():
                bpy.ops.wm.save_as_mainfile(filepath=path)
                bpy.ops.wm.open_mainfile(filepath=path)
            self.assertEqual(os.path.realpath(bpy.data.filepath), os.path.realpath(path))
            self.template.setup_new_file()
            bpy.context.view_layer.update()
            scene = bpy.context.scene
            self.assertAlmostEqual(scene.unit_settings.scale_length, 1.0, places=6)
            self.assertIn("Animation", bpy.data.workspaces)
            for value in bpy.data.objects["Cube"].dimensions:
                self.assertAlmostEqual(value, 2.0, places=4)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_uses_the_extension_units_when_available(self):
        calls = []
        stub = types.ModuleType(EXT_UNITS)
        stub.apply_internal_mm = lambda scene: calls.append(scene)
        with mock.patch.dict(sys.modules, {EXT_UNITS: stub}):
            self.template.setup_new_file()
        self.assertEqual(len(calls), 1)

    def test_falls_back_to_direct_units_without_the_extension(self):
        # The extension is not loaded; the lookup must not raise.
        scene = bpy.context.scene
        scene.unit_settings.system = "IMPERIAL"
        scene.unit_settings.scale_length = 1.0
        with _without_extension_modules():
            self.template.apply_units(scene)
        self.assertEqual(scene.unit_settings.system, "METRIC")
        self.assertAlmostEqual(scene.unit_settings.scale_length, 0.001, places=6)
        self.assertEqual(scene.unit_settings.length_unit, "MILLIMETERS")

    def test_units_never_import_the_extension(self):
        # Importing it before addon_utils.enable() makes enable() reload the
        # package ("module changed on disk ... reloading").
        with _without_extension_modules():
            self.template.apply_units(bpy.context.scene)
            loaded = [name for name in sys.modules if name.startswith(EXTENSION)]
        self.assertEqual(loaded, [])


class RegisterTest(unittest.TestCase):
    def setUp(self):
        fresh_startup_file()
        self.template = load_template()
        self.undo_steps = bpy.context.preferences.edit.undo_steps
        self.show_splash = bpy.context.preferences.view.show_splash

    def tearDown(self):
        bpy.context.preferences.edit.undo_steps = self.undo_steps
        self.template.unregister()
        bpy.context.preferences.view.show_splash = self.show_splash
        fresh_startup_file()

    def test_register_hides_the_splash_until_the_first_tick(self):
        view = bpy.context.preferences.view
        view.show_splash = True
        with mock.patch("addon_utils.enable"):
            self.template.register()
        # Blender decides on the splash after the template registers and
        # before the first timer tick.
        self.assertFalse(view.show_splash)
        self.assertTrue(bpy.app.timers.is_registered(self.template._restore_splash))
        self.assertIsNone(self.template._restore_splash())
        # The user's own setting is back, so saving the shared preferences
        # keeps the splash for the stock Blender.
        self.assertTrue(view.show_splash)

    def test_splash_setting_survives_a_second_register(self):
        view = bpy.context.preferences.view
        view.show_splash = True
        with mock.patch("addon_utils.enable"):
            self.template.register()
            self.template.register()
        self.template._restore_splash()
        self.assertTrue(view.show_splash)

    def test_splash_off_stays_off(self):
        view = bpy.context.preferences.view
        view.show_splash = False
        with mock.patch("addon_utils.enable"):
            self.template.register()
        self.template._restore_splash()
        self.assertFalse(view.show_splash)

    def test_unregister_restores_the_splash_setting(self):
        view = bpy.context.preferences.view
        view.show_splash = True
        with mock.patch("addon_utils.enable"):
            self.template.register()
        self.template.unregister()
        self.assertTrue(view.show_splash)
        self.assertFalse(bpy.app.timers.is_registered(self.template._restore_splash))

    def test_register_unregister(self):
        with mock.patch("addon_utils.enable") as enable:
            self.template.register()
        # Enabling waits for the first tick: bl_ext does not exist yet at register().
        enable.assert_not_called()
        self.assertEqual(bpy.context.preferences.edit.undo_steps, 256)
        self.assertIn(self.template.setup_new_file, bpy.app.handlers.load_post)
        self.assertTrue(bpy.app.timers.is_registered(self.template._tweak_ui))
        self.assertTrue(bpy.app.timers.is_registered(self.template._enable_extension))
        self.template.unregister()
        self.assertNotIn(self.template.setup_new_file, bpy.app.handlers.load_post)
        self.assertFalse(bpy.app.timers.is_registered(self.template._tweak_ui))
        self.assertFalse(bpy.app.timers.is_registered(self.template._enable_extension))

    def test_enable_tick_enables_the_extension(self):
        state = {"enabled": False}

        def enable(*args, **kwargs):
            state["enabled"] = True

        self.template._enable_attempts = 0
        with mock.patch("addon_utils.enable", side_effect=enable) as enable_mock, mock.patch.object(
            self.template, "_extension_enabled", side_effect=lambda: state["enabled"]
        ):
            self.assertIsNone(self.template._enable_extension())
        args, kwargs = enable_mock.call_args
        self.assertEqual(args[0], "bl_ext.user_default.blendercraft_assistant")
        self.assertTrue(kwargs.get("default_set"))
        self.assertTrue(kwargs.get("persistent"))

    def test_enable_tick_retries_while_the_extension_is_missing(self):
        self.template._enable_attempts = 0
        with mock.patch("addon_utils.enable") as enable_mock, mock.patch.object(
            self.template, "_extension_enabled", return_value=False
        ):
            self.assertEqual(self.template._enable_extension(), self.template.ENABLE_INTERVAL)
        enable_mock.assert_called_once()

    def test_enable_tick_does_nothing_when_already_enabled(self):
        with mock.patch("addon_utils.enable") as enable_mock, mock.patch.object(
            self.template, "_extension_enabled", return_value=True
        ):
            self.assertIsNone(self.template._enable_extension())
        enable_mock.assert_not_called()

    def test_enable_tick_gives_up_with_a_clear_message(self):
        self.template._enable_attempts = self.template.MAX_ENABLE_ATTEMPTS - 1
        out = io.StringIO()
        with mock.patch("addon_utils.enable"), mock.patch.object(
            self.template, "_extension_enabled", return_value=False
        ), contextlib.redirect_stdout(out):
            self.assertIsNone(self.template._enable_extension())
        self.assertIn("could not enable", out.getvalue())
        self.assertIn("install.sh", out.getvalue())

    def test_enable_tick_survives_a_failing_enable(self):
        self.template._enable_attempts = 0
        out = io.StringIO()
        with mock.patch("addon_utils.enable", side_effect=RuntimeError("boom")), mock.patch.object(
            self.template, "_extension_enabled", return_value=False
        ), contextlib.redirect_stdout(out):
            self.assertEqual(self.template._enable_extension(), self.template.ENABLE_INTERVAL)
        self.assertIn("boom", out.getvalue())

    def test_register_twice_adds_one_handler(self):
        with mock.patch("addon_utils.enable"):
            self.template.register()
            self.template.register()
        handlers = [h for h in bpy.app.handlers.load_post if h is self.template.setup_new_file]
        self.assertEqual(len(handlers), 1)

    def test_tweak_ui_without_a_window_retries_later(self):
        self.assertEqual(self.template._windows(), [])  # background mode
        self.template._attempts = 0
        self.assertIsNotNone(self.template._tweak_ui())

    def test_tweak_ui_gives_up_after_the_retries(self):
        self.template._attempts = self.template.MAX_ATTEMPTS
        self.assertIsNone(self.template._tweak_ui())

    def test_timeline_areas_picks_only_timelines(self):
        def area(kind, ui_type):
            return types.SimpleNamespace(type=kind, ui_type=ui_type)

        timeline = area("DOPESHEET_EDITOR", "TIMELINE")
        screen = types.SimpleNamespace(
            areas=[
                area("VIEW_3D", "VIEW_3D"),
                area("DOPESHEET_EDITOR", "DOPESHEET"),
                timeline,
            ]
        )
        self.assertEqual(self.template.timeline_areas(screen), [timeline])


class _Recorder:
    """Stands in for temp_override and the screen operators and notes when they ran."""

    def __init__(self):
        self.current_area = None
        self.depth = 0
        self.closes = []  # context depth at each area_close
        self.splits = []  # (direction, factor, context depth)
        self.sets = []  # (property, value, context depth)
        self.windows = []

    @contextlib.contextmanager
    def override(self, window, screen, area, region=None):
        self.current_area = area
        self.depth += 1
        try:
            yield
        finally:
            self.depth -= 1
            self.current_area = None

    def _screen_of(self, area):
        return next(w.screen for w in self.windows if area in w.screen.areas)

    def area_close(self):
        area = self.current_area
        self.closes.append(self.depth)
        self._screen_of(area).areas.remove(area)

    def area_split(self, direction, factor):
        """Like Blender: the new area takes the left (or bottom) part."""
        area = self.current_area
        self.splits.append((direction, round(factor, 4), self.depth))
        screen = self._screen_of(area)
        new = _stub_area(self, area.type, area.ui_type)
        screen.areas.insert(screen.areas.index(area), new)


class _Recording:
    """Notes every attribute set, with the override depth at that moment."""

    def __init__(self, recorder, **attrs):
        object.__setattr__(self, "_recorder", recorder)
        for name, value in attrs.items():
            object.__setattr__(self, name, value)

    def __setattr__(self, name, value):
        self._recorder.sets.append((name, value, self._recorder.depth))
        object.__setattr__(self, name, value)
        if name == "ui_type":  # changing the editor changes the area type
            object.__setattr__(self, "type", value)


def _Space(recorder, kind):
    return _Recording(recorder, type=kind, region_3d=_Recording(recorder))


def _stub_area(recorder, kind, ui_type=None, x=0, width=100):
    ui_type = ui_type or kind
    return _Recording(
        recorder,
        type=kind,
        ui_type=ui_type,
        x=x,
        width=width,
        spaces=[_Space(recorder, kind)],
        regions=[
            _Recording(recorder, type="WINDOW"),
            _Recording(
                recorder,
                type="UI",
                active_panel_category="Item",
                is_property_readonly=lambda name: False,
            ),
        ],
    )


def _stub_window(workspace, areas, width=2560):
    return types.SimpleNamespace(
        workspace=types.SimpleNamespace(name=workspace),
        screen=types.SimpleNamespace(areas=list(areas)),
        width=width,
    )


def _factory_layout(rec):
    """Blender's Layout: Outliner over Properties on the right, timeline below."""
    view = _stub_area(rec, "VIEW_3D", x=2, width=2100)
    areas = [
        _stub_area(rec, "PROPERTIES", x=2105, width=453),
        _stub_area(rec, "OUTLINER", x=2105, width=453),
        _stub_area(rec, "DOPESHEET_EDITOR", "TIMELINE", x=2, width=2100),
        view,
    ]
    return view, areas


class TweakUiGuiBranchTest(unittest.TestCase):
    """The window branch of _tweak_ui, with stand-ins for windows and areas.

    Real windows do not exist in -b, and UI changes outside a window context
    crash Blender, so what matters is that they all run inside the override.
    """

    def setUp(self):
        self.template = load_template()
        self.rec = _Recorder()
        self.category = "Assistente"
        self.ui_scale = 1.0

    def run_tweak(self, windows, unsaved=True, tab=False):
        self.rec.windows = windows
        with mock.patch.object(self.template, "_windows", return_value=windows), mock.patch.object(
            self.template, "_override", self.rec.override
        ), mock.patch.object(self.template, "_area_close", self.rec.area_close), mock.patch.object(
            self.template, "_area_split", self.rec.area_split
        ), mock.patch.object(
            self.template, "_unsaved", return_value=unsaved
        ), mock.patch.object(
            self.template, "assistant_category", side_effect=lambda: self.category
        ), mock.patch.object(
            self.template, "_ui_scale", side_effect=lambda: self.ui_scale
        ), mock.patch.object(
            self.template, "_schedule_tab", side_effect=lambda: self.rec.sets.append(("tab", None, 0))
        ):
            if tab:
                return self.template._select_assistant_tab()
            return self.template._tweak_ui()

    def tab_scheduled(self):
        return ("tab", None, 0) in self.rec.sets

    def ui_region(self, area):
        return next(r for r in area.regions if r.type == "UI")

    def test_solidcraft_like_layout_inside_the_override(self):
        view, areas = _factory_layout(self.rec)
        window = _stub_window("Layout", areas)
        self.assertIsNone(self.run_tweak([window]))
        # Browser (Outliner) over Properties on the left, then the 3D View:
        # the first split's new area (left) became the Outliner and the
        # second's (its bottom part) the Properties.
        self.assertEqual(
            sorted(a.type for a in window.screen.areas), ["OUTLINER", "PROPERTIES", "VIEW_3D"]
        )
        self.assertEqual(
            [(n, v) for n, v, _ in self.rec.sets if n == "ui_type"],
            [("ui_type", "OUTLINER"), ("ui_type", "PROPERTIES")],
        )
        self.assertEqual([a for a in window.screen.areas if a.type == "VIEW_3D"], [view])
        # Timeline, then the right-hand Properties and Outliner.
        self.assertEqual(self.rec.closes, [1, 1, 1])
        self.assertEqual(
            self.rec.splits,
            [("VERTICAL", round(320 / 2560, 4), 1), ("HORIZONTAL", 0.4, 1)],
        )
        # The Assistant tab is chosen on a later tick, once the sidebar exists.
        self.assertTrue(self.tab_scheduled())
        self.rec.sets.remove(("tab", None, 0))
        # Every UI change ran inside the override.
        self.assertTrue(self.rec.sets)
        self.assertTrue(all(depth == 1 for _, _, depth in self.rec.sets), self.rec.sets)
        space = view.spaces[0]
        self.assertTrue(space.show_region_ui)
        self.assertEqual(space.region_3d.view_distance, self.template.VIEW_DISTANCE)
        self.assertEqual(self.template.VIEW_DISTANCE, 80.0)
        self.assertEqual(self.ui_region(view).active_panel_category, "Item")
        self.assertIsNone(self.run_tweak([window], tab=True))
        self.assertEqual(self.ui_region(view).active_panel_category, "Assistente")
        self.assertEqual(self.rec.sets[-1], ("active_panel_category", "Assistente", 1))

    def test_tab_waits_until_the_sidebar_has_its_tabs(self):
        view, areas = _factory_layout(self.rec)
        window = _stub_window("Layout", areas)
        region = self.ui_region(view)
        object.__setattr__(region, "is_property_readonly", lambda name: True)
        self.template._tab_attempts = 0
        self.assertEqual(self.run_tweak([window], tab=True), self.template.TAB_INTERVAL)
        self.assertEqual(region.active_panel_category, "Item")
        object.__setattr__(region, "is_property_readonly", lambda name: False)
        self.assertIsNone(self.run_tweak([window], tab=True))
        self.assertEqual(region.active_panel_category, "Assistente")

    def test_tab_gives_up_after_the_retries(self):
        view, areas = _factory_layout(self.rec)
        object.__setattr__(self.ui_region(view), "is_property_readonly", lambda name: True)
        self.template._tab_attempts = self.template.MAX_TAB_ATTEMPTS - 1
        self.assertIsNone(self.run_tweak([_stub_window("Layout", areas)], tab=True))

    def test_left_column_scales_with_the_interface(self):
        _, areas = _factory_layout(self.rec)
        self.ui_scale = 2.0
        self.run_tweak([_stub_window("Layout", areas, width=2880)])
        self.assertEqual(self.rec.splits[0][:2], ("VERTICAL", round(640 / 2880, 4)))

    def test_left_column_factor_is_clamped(self):
        factor = self.template.left_column_factor
        self.assertAlmostEqual(factor(10000, 1.0), 0.1)
        self.assertAlmostEqual(factor(800, 2.0), 0.4)
        self.assertAlmostEqual(factor(0, 1.0), 0.2)

    def test_a_layout_without_the_right_column_is_not_split(self):
        view = _stub_area(self.rec, "VIEW_3D", x=330, width=2228)
        browser = _stub_area(self.rec, "OUTLINER", x=2, width=320)
        window = _stub_window("Layout", [browser, view])
        self.run_tweak([window])
        self.assertEqual(self.rec.closes, [])
        self.assertEqual(self.rec.splits, [])
        self.assertEqual([a.type for a in window.screen.areas], ["OUTLINER", "VIEW_3D"])
        self.assertTrue(view.spaces[0].show_region_ui)

    def test_right_column_areas(self):
        view, areas = _factory_layout(self.rec)
        screen = types.SimpleNamespace(areas=areas)
        self.assertEqual(
            [a.type for a in self.template.right_column_areas(screen, view)],
            ["PROPERTIES", "OUTLINER"],
        )

    def test_waits_for_the_assistant_panel(self):
        _, areas = _factory_layout(self.rec)
        window = _stub_window("Layout", areas)
        self.category = None
        self.template._attempts = 0
        self.assertEqual(self.run_tweak([window]), self.template.RETRY_INTERVAL)
        self.assertEqual(self.rec.closes, [])
        self.assertEqual(self.rec.sets, [])

    def test_lays_out_without_the_panel_after_the_retries(self):
        view, areas = _factory_layout(self.rec)
        window = _stub_window("Layout", areas)
        self.category = None
        self.template._attempts = self.template.MAX_ATTEMPTS - 1
        self.assertIsNone(self.run_tweak([window]))
        self.assertEqual(
            sorted(a.type for a in window.screen.areas), ["OUTLINER", "PROPERTIES", "VIEW_3D"]
        )
        self.assertEqual(self.ui_region(view).active_panel_category, "Item")
        self.assertFalse(self.tab_scheduled())

    def test_every_timeline_is_closed(self):
        timelines = [_stub_area(self.rec, "DOPESHEET_EDITOR", "TIMELINE") for _ in range(3)]
        window = _stub_window("Layout", [*timelines, _stub_area(self.rec, "VIEW_3D")])
        self.run_tweak([window])
        self.assertEqual([a.type for a in window.screen.areas], ["VIEW_3D"])
        self.assertEqual(self.rec.closes, [1, 1, 1])

    def test_a_timeline_that_will_not_close_does_not_loop_forever(self):
        stuck = _stub_area(self.rec, "DOPESHEET_EDITOR", "TIMELINE")
        window = _stub_window("Layout", [stuck])
        self.rec.windows = [window]
        with mock.patch.object(self.template, "_windows", return_value=[window]), mock.patch.object(
            self.template, "_override", self.rec.override
        ), mock.patch.object(self.template, "_area_close", lambda: self.rec.closes.append(1)), mock.patch.object(
            self.template, "assistant_category", return_value="Assistente"
        ):
            self.template._tweak_ui()
        self.assertLessEqual(len(self.rec.closes), self.template.MAX_CLOSES)

    def test_other_workspaces_are_skipped(self):
        timeline = _stub_area(self.rec, "DOPESHEET_EDITOR", "TIMELINE")
        view = _stub_area(self.rec, "VIEW_3D")
        window = _stub_window("Modeling", [timeline, view])
        self.run_tweak([window])
        self.assertEqual(window.screen.areas, [timeline, view])
        self.assertEqual(self.rec.closes, [])
        self.assertEqual(self.rec.sets, [])

    def test_named_files_are_skipped(self):
        _, areas = _factory_layout(self.rec)
        window = _stub_window("Layout", areas)
        self.assertIsNone(self.run_tweak([window], unsaved=False))
        self.assertEqual(window.screen.areas, areas)
        self.assertEqual(self.rec.closes, [])
        self.assertEqual(self.rec.sets, [])


@unittest.skipUnless(shutil.which("bash"), "bash not available")
class InstallScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.config = self.tmp / "config"
        self.fake_blender = self.tmp / "Blender.app"
        self.fake_blender.mkdir()

    def run_install(self, *args, blender_app=None, home=None, path="/usr/bin:/bin"):
        env = dict(os.environ)
        env["BLENDERCRAFT_CONFIG_DIR"] = str(self.config)
        env["BLENDERCRAFT_BLENDER_APP"] = str(blender_app or self.fake_blender)
        env["HOME"] = str(home or self.tmp)
        env["PATH"] = path
        return subprocess.run(
            ["bash", str(ROOT / "scripts" / "install.sh"), *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def targets(self):
        return (
            self.config / "scripts" / "startup" / "bl_app_templates_user" / "blendercraft",
            self.config / "extensions" / "user_default" / "blendercraft_assistant",
        )

    def test_scripts_are_executable(self):
        for name in ("install.sh", "BlenderCraft.command"):
            self.assertTrue(os.access(ROOT / "scripts" / name, os.X_OK), name)

    def test_launcher_runs_blender_with_the_template(self):
        text = (ROOT / "scripts" / "BlenderCraft.command").read_text(encoding="utf-8")
        self.assertIn(
            'exec /Applications/Blender.app/Contents/MacOS/Blender --app-template blendercraft "$@"',
            text,
        )

    def test_copy_install(self):
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        template, extension = self.targets()
        self.assertTrue(template.is_dir() and not template.is_symlink())
        self.assertTrue((template / "__init__.py").is_file())
        self.assertTrue(extension.is_dir() and not extension.is_symlink())
        self.assertTrue((extension / "blender_manifest.toml").is_file())
        self.assertFalse(list(extension.rglob("__pycache__")))

    def test_copy_install_twice_replaces_without_nesting(self):
        self.assertEqual(self.run_install().returncode, 0)
        self.assertEqual(self.run_install().returncode, 0)
        template, extension = self.targets()
        self.assertFalse((template / "blendercraft").exists())
        self.assertFalse((extension / "blendercraft_assistant").exists())

    def test_dev_install_links(self):
        result = self.run_install("--dev")
        self.assertEqual(result.returncode, 0, result.stderr)
        template, extension = self.targets()
        self.assertEqual(template.resolve(), (ROOT / "app_template" / "blendercraft").resolve())
        self.assertEqual(extension.resolve(), (ROOT / "blendercraft_assistant").resolve())
        self.assertTrue(template.is_symlink() and extension.is_symlink())

    def test_switching_between_copy_and_dev(self):
        self.assertEqual(self.run_install("--dev").returncode, 0)
        self.assertEqual(self.run_install().returncode, 0)
        template, extension = self.targets()
        self.assertFalse(template.is_symlink() or extension.is_symlink())
        self.assertEqual(self.run_install("--dev").returncode, 0)
        self.assertTrue(template.is_symlink() and extension.is_symlink())
        # The repo itself must be intact.
        self.assertTrue((ROOT / "blendercraft_assistant" / "blender_manifest.toml").is_file())

    def test_fails_clearly_without_blender(self):
        result = self.run_install(blender_app=self.tmp / "nope.app")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Blender", result.stderr)
        self.assertFalse(self.config.exists())

    def test_warns_without_claude_but_succeeds(self):
        for path in ("/opt/homebrew/bin/claude", "/usr/local/bin/claude"):
            if os.path.exists(path):
                self.skipTest(f"claude is installed at {path}")
        result = self.run_install()
        self.assertEqual(result.returncode, 0)
        self.assertIn("claude", result.stdout + result.stderr)

    def test_reports_the_claude_it_finds(self):
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "claude"
        fake.write_text("#!/bin/sh\n", encoding="utf-8")
        fake.chmod(0o755)
        result = self.run_install(path=f"{bin_dir}:/usr/bin:/bin")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(fake), result.stdout)
        self.assertNotIn("Aviso", result.stderr)

    def test_config_dir_with_spaces(self):
        self.config = self.tmp / "My Blender Config" / "5.2 beta"
        for args in ((), ("--dev",)):
            result = self.run_install(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
            template, extension = self.targets()
            self.assertTrue((template / "__init__.py").is_file())
            self.assertTrue((extension / "blender_manifest.toml").is_file())

    def test_install_leaves_no_temporary_files(self):
        for args in ((), ("--dev",), ()):
            self.assertEqual(self.run_install(*args).returncode, 0)
        template, extension = self.targets()
        for path in (template, extension):
            self.assertEqual([p.name for p in path.parent.iterdir()], [path.name])

    def test_help_prints_only_the_header(self):
        result = self.run_install("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("--dev", result.stdout)
        self.assertIn("BLENDERCRAFT_BLENDER_APP", result.stdout)
        self.assertNotIn("set -euo", result.stdout)
        self.assertFalse(self.config.exists())

    def test_unknown_option_fails(self):
        result = self.run_install("--bogus")
        self.assertNotEqual(result.returncode, 0)
