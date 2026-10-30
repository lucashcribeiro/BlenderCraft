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
        # Other default workspaces stay.
        for name in ("Layout", "Modeling", "Sculpting", "Shading", "Scripting"):
            self.assertIn(name, bpy.data.workspaces)
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
        # The extension is not installed here; the lookup must not raise.
        self.assertNotIn(EXT_UNITS, sys.modules)
        scene = bpy.context.scene
        scene.unit_settings.system = "IMPERIAL"
        scene.unit_settings.scale_length = 1.0
        self.template.apply_units(scene)
        self.assertEqual(scene.unit_settings.system, "METRIC")
        self.assertAlmostEqual(scene.unit_settings.scale_length, 0.001, places=6)
        self.assertEqual(scene.unit_settings.length_unit, "MILLIMETERS")


class RegisterTest(unittest.TestCase):
    def setUp(self):
        fresh_startup_file()
        self.template = load_template()
        self.undo_steps = bpy.context.preferences.edit.undo_steps

    def tearDown(self):
        bpy.context.preferences.edit.undo_steps = self.undo_steps
        self.template.unregister()
        fresh_startup_file()

    def test_register_unregister(self):
        with mock.patch("addon_utils.enable") as enable:
            self.template.register()
        enable.assert_called_once()
        args, kwargs = enable.call_args
        self.assertEqual(args[0], "bl_ext.user_default.blendercraft_assistant")
        self.assertTrue(kwargs.get("default_set"))
        self.assertTrue(kwargs.get("persistent"))
        self.assertEqual(bpy.context.preferences.edit.undo_steps, 256)
        self.assertIn(self.template.setup_new_file, bpy.app.handlers.load_post)
        self.assertTrue(bpy.app.timers.is_registered(self.template._tweak_ui))
        self.template.unregister()
        self.assertNotIn(self.template.setup_new_file, bpy.app.handlers.load_post)
        self.assertFalse(bpy.app.timers.is_registered(self.template._tweak_ui))

    def test_register_survives_a_failing_extension_enable(self):
        out = io.StringIO()
        with mock.patch("addon_utils.enable", side_effect=RuntimeError("boom")):
            with contextlib.redirect_stdout(out):
                self.template.register()
        self.assertIn("boom", out.getvalue())
        self.assertIn(self.template.setup_new_file, bpy.app.handlers.load_post)

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

    def test_unknown_option_fails(self):
        result = self.run_install("--bogus")
        self.assertNotEqual(result.returncode, 0)
