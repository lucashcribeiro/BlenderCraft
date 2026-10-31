"""The launcher (scripts/BlenderCraft.command), BlenderCraft.app and its icon.

These run shell tools only (no bpy), so they work in -b like the rest.
"""

import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
LAUNCHER = SCRIPTS / "BlenderCraft.command"
ICON = SCRIPTS / "assets" / "BlenderCraft.png"

FAKE_BLENDER = """#!/bin/bash
for a in "$@"; do printf '%s\\n' "$a"; done > "$ARGS_OUT"
echo "blender says hello"
echo "blender complains" >&2
"""


class LauncherTest(unittest.TestCase):
    """What the launcher hands to Blender, with a stand-in Blender."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fake = self.tmp / "Blender"
        self.fake.write_text(FAKE_BLENDER, encoding="utf-8")
        self.fake.chmod(0o755)
        self.out = self.tmp / "args.txt"
        self.log_dir = self.tmp / "logs"

    def env(self, **extra):
        return dict(
            os.environ,
            BLENDERCRAFT_BLENDER=str(self.fake),
            BLENDERCRAFT_LOG_DIR=str(self.log_dir),
            ARGS_OUT=str(self.out),
            **extra,
        )

    def launch(self, *args, cwd=None):
        env = self.env()
        result = subprocess.run(
            ["bash", str(LAUNCHER), *args],
            env=env,
            cwd=cwd or self.tmp,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.out.read_text(encoding="utf-8").splitlines()

    def test_no_files(self):
        self.assertEqual(self.launch(), ["--app-template", "blendercraft"])

    def test_meshes_are_imported_after_the_double_dash(self):
        self.assertEqual(
            self.launch("/m/a b.stl", "--open", "/m/c.3mf"),
            ["--app-template", "blendercraft", "--", "--bc-open", "/m/a b.stl", "--bc-open", "/m/c.3mf"],
        )

    def test_a_blend_file_is_opened_by_blender(self):
        self.assertEqual(
            self.launch("/m/x.stl", "/m/Model.BLEND", "/m/other.blend"),
            ["--app-template", "blendercraft", "/m/Model.BLEND", "--", "--bc-open", "/m/x.stl"],
        )

    def test_relative_paths_become_absolute(self):
        args = self.launch("parts/a.stl")
        self.assertEqual(args[-1], str(self.tmp.resolve() / "parts" / "a.stl"))

    def test_open_without_a_file_fails(self):
        result = subprocess.run(
            ["bash", str(LAUNCHER), "--open"], env=self.env(), capture_output=True, text=True, timeout=30
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.out.exists())


    # ---- log file --------------------------------------------------------

    @property
    def log(self):
        return self.log_dir / "blendercraft.log"

    def test_blender_output_is_appended_to_the_log_with_a_header_per_launch(self):
        self.launch("/m/a.stl")
        self.launch()
        text = self.log.read_text(encoding="utf-8")
        self.assertEqual(text.count("blender says hello"), 2)
        self.assertEqual(text.count("blender complains"), 2)  # stderr too
        headers = [l for l in text.splitlines() if l.startswith("=== ")]
        self.assertEqual(len(headers), 2)
        self.assertRegex(headers[0], r"^=== \d{4}-\d\d-\d\d \d\d:\d\d:\d\d BlenderCraft: ")
        self.assertIn("--app-template blendercraft", headers[0])
        self.assertIn("/m/a.stl", headers[0])
        # The header comes before the output of its launch.
        self.assertLess(text.index("==="), text.index("blender says hello"))

    def test_launcher_prints_nothing_itself(self):
        env = self.env()
        result = subprocess.run(
            ["bash", str(LAUNCHER)], env=env, cwd=self.tmp, capture_output=True, text=True, timeout=30
        )
        self.assertEqual((result.stdout, result.stderr), ("", ""))

    def test_the_log_folder_is_created(self):
        self.assertFalse(self.log_dir.exists())
        self.launch()
        self.assertTrue(self.log.is_file())

    def test_a_log_above_10_mb_is_rotated_to_dot_1(self):
        self.log_dir.mkdir()
        self.log.write_bytes(b"x" * (10 * 1024 * 1024 + 1))
        (self.log_dir / "blendercraft.log.1").write_text("older", encoding="utf-8")
        self.launch()
        rotated = self.log_dir / "blendercraft.log.1"
        self.assertEqual(rotated.stat().st_size, 10 * 1024 * 1024 + 1)  # replaced
        text = self.log.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("=== "))
        self.assertIn("blender says hello", text)

    def test_a_small_log_is_not_rotated(self):
        self.log_dir.mkdir()
        self.log.write_text("previous run\n", encoding="utf-8")
        self.launch()
        self.assertFalse((self.log_dir / "blendercraft.log.1").exists())
        self.assertTrue(self.log.read_text(encoding="utf-8").startswith("previous run\n"))

    def test_blender_still_starts_when_the_log_folder_is_not_writable(self):
        blocker = self.tmp / "file"
        blocker.write_text("not a folder", encoding="utf-8")
        env = self.env()
        env["BLENDERCRAFT_LOG_DIR"] = str(blocker / "sub")
        result = subprocess.run(
            ["bash", str(LAUNCHER)], env=env, cwd=self.tmp, capture_output=True, text=True, timeout=30
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.out.exists())


class IconTest(unittest.TestCase):
    def test_icon_is_a_1024_png(self):
        data = ICON.read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        self.assertEqual((width, height), (1024, 1024))

    def test_generator_is_committed(self):
        self.assertTrue((SCRIPTS / "make_icon.py").is_file())


TOOLS = ("osacompile", "sips", "iconutil", "codesign")


@unittest.skipUnless(
    sys.platform == "darwin" and all(shutil.which(t) for t in TOOLS), "macOS build tools needed"
)
class BuildAppTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        env = dict(os.environ, BLENDERCRAFT_DIST_DIR=str(cls.tmp / "dist"))
        cls.result = subprocess.run(
            ["bash", str(SCRIPTS / "build_app.sh")],
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        cls.app = cls.tmp / "dist" / "BlenderCraft.app"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.assertEqual(self.result.returncode, 0, self.result.stderr)

    def plist(self):
        with open(self.app / "Contents" / "Info.plist", "rb") as f:
            return plistlib.load(f)

    def test_names_and_identifier(self):
        info = self.plist()
        self.assertEqual(info["CFBundleName"], "BlenderCraft")
        self.assertEqual(info["CFBundleDisplayName"], "BlenderCraft")
        self.assertEqual(info["CFBundleIdentifier"], "io.github.lucashcribeiro.blendercraft")

    def test_document_types_do_not_steal_the_default_app(self):
        types_ = {
            ext: entry
            for entry in self.plist()["CFBundleDocumentTypes"]
            for ext in entry["CFBundleTypeExtensions"]
        }
        self.assertEqual(sorted(types_), ["3mf", "blend", "stl"])
        for ext, entry in types_.items():
            self.assertEqual(entry["LSHandlerRank"], "Alternate", ext)
        self.assertEqual(types_["stl"]["CFBundleTypeRole"], "Viewer")
        self.assertEqual(types_["3mf"]["CFBundleTypeRole"], "Viewer")

    def test_icon(self):
        info = self.plist()
        self.assertEqual(info["CFBundleIconFile"], "BlenderCraft")
        self.assertNotIn("CFBundleIconName", info)  # would pick Assets.car
        resources = self.app / "Contents" / "Resources"
        self.assertTrue((resources / "BlenderCraft.icns").is_file())
        self.assertFalse((resources / "Assets.car").exists())
        self.assertFalse(list(resources.glob("applet.icns")) + list(resources.glob("droplet.icns")))

    def test_launcher_is_bundled(self):
        launcher = self.app / "Contents" / "Resources" / "BlenderCraft.command"
        self.assertTrue(os.access(launcher, os.X_OK))
        self.assertEqual(launcher.read_bytes(), LAUNCHER.read_bytes())

    def test_script_handles_run_and_open(self):
        script = self.app / "Contents" / "Resources" / "Scripts" / "main.scpt"
        source = subprocess.run(
            ["osadecompile", str(script)], capture_output=True, text=True, timeout=30
        ).stdout
        self.assertIn("on run", source)
        self.assertIn("on open", source)
        self.assertIn("BlenderCraft.command", source)
        self.assertIn("quoted form of", source)
        # Output is no longer discarded: the launcher logs it.
        self.assertNotIn("/dev/null", source)
        self.assertIn('" &"', source)

    def test_signature_is_valid(self):
        result = subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", str(self.app)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
