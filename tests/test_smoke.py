import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class SmokeTest(unittest.TestCase):
    def test_manifest(self):
        data = tomllib.loads((ROOT / "blendercraft_assistant/blender_manifest.toml").read_text())
        self.assertEqual(data["id"], "blendercraft_assistant")
        self.assertEqual(data["version"], "0.1.0")
        self.assertEqual(data["blender_version_min"], "5.2.0")
        self.assertEqual(data["license"], ["SPDX:GPL-3.0-or-later"])
        self.assertEqual(data["type"], "add-on")

    def test_package_imports(self):
        import blendercraft_assistant
        self.assertTrue(callable(blendercraft_assistant.register))
