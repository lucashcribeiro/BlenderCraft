import json
import re
import unittest
from pathlib import Path

from blendercraft_assistant.claude.system_prompt import build_system_prompt, context_line

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "blendercraft_assistant" / "plugin"
SKILLS = ("blender-map", "design", "printing", "joints")
# Every skill folder in the plugin (SKILLS are the ones the prompt names).
ALL_SKILLS = SKILLS + ("organic-modeling", "quality-workflow")
ORGANIC = PLUGIN / "skills" / "organic-modeling"


def frontmatter(text: str) -> dict:
    match = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
    if match is None:
        return {}
    fields = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


class PromptTests(unittest.TestCase):
    def test_prompt_contains_context(self):
        text = build_system_prompt("5.2.2", "in", "en")
        self.assertIn("5.2.2", text)
        self.assertIn("inches (in)", text)
        self.assertIn("screenshot", text)
        self.assertIn("at least two angles", text)
        self.assertIn("Answer in English", text)
        self.assertNotIn("Portuguese", text)

    def test_prompt_language_pt(self):
        text = build_system_prompt("5.2.2", "mm", "pt")
        self.assertIn("Answer in Brazilian Portuguese", text)
        self.assertNotIn("Answer in English", text)

    def test_prompt_names_skills_and_rules(self):
        text = build_system_prompt("5.2.2", "mm", "en")
        for skill in SKILLS:
            self.assertIn(f"blendercraft:{skill}", text)
        for phrase in (
            "ambiguous",
            "description",
            "what you could not",
            "Follow the user's instructions exactly",
            "Inspect before you change",
            "inspect_scene",
            "Prefer non-destructive",
            "list_operators",
        ):
            self.assertIn(phrase, text)

    def test_prompt_states_the_file_scale_and_the_context_line(self):
        text = build_system_prompt("5.2.2", "mm", "en", mm_per_bu=1000.0)
        self.assertIn("1 BU = 1000 mm", text)
        self.assertNotIn("1 mm, always", text)
        self.assertIn("[BlenderCraft:", text)
        self.assertIn("overrides this prompt", text)
        self.assertIn("1 BU = 1 mm", build_system_prompt("5.2.2", "mm", "en"))

    def test_context_line(self):
        self.assertEqual(
            context_line("in", 1.0, "pt"),
            "[BlenderCraft: unidade exibida = in · 1 BU = 1 mm · idioma = pt]",
        )
        self.assertEqual(
            context_line("MM", 0.001, "en"),
            "[BlenderCraft: unidade exibida = mm · 1 BU = 0.001 mm · idioma = en]",
        )

    def test_tool_call_limit_is_a_parameter(self):
        self.assertIn("(40)", build_system_prompt("5.2.2", "mm", "en"))
        self.assertIn("(12)", build_system_prompt("5.2.2", "mm", "en", 12))

    def test_unknown_language_falls_back_to_english(self):
        self.assertIn("English", build_system_prompt("5.2.2", "mm", "xx"))


class PluginTests(unittest.TestCase):
    def test_plugin_valid(self):
        manifest = json.loads(
            (PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["name"], "blendercraft")
        self.assertEqual(manifest["version"], "0.1.0")
        for name in ALL_SKILLS:
            path = PLUGIN / "skills" / name / "SKILL.md"
            fields = frontmatter(path.read_text(encoding="utf-8"))
            self.assertEqual(fields.get("name"), name, path)
            self.assertTrue(fields.get("description"), path)

    def test_no_stray_skill_folders(self):
        found = sorted(p.parent.name for p in (PLUGIN / "skills").glob("*/SKILL.md"))
        self.assertEqual(found, sorted(ALL_SKILLS))

    def test_organic_skill_ships_its_kit_and_reference(self):
        for name in ("kit.py", "articulated.py", "reference/articulated-puppy-35mm.md"):
            self.assertTrue((ORGANIC / name).is_file(), name)
        text = (ORGANIC / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("NOT", text)
        self.assertIn("remesh_smooth", text)
        workflow = (PLUGIN / "skills" / "quality-workflow" / "SKILL.md").read_text(encoding="utf-8")
        for phrase in ("Stage 0", "dossier", "gate", "generate_3d_model", "target-vs-built"):
            self.assertIn(phrase, workflow)


def _load_kit():
    """The loader snippet from organic-modeling/SKILL.md (bc_kit only)."""
    import sys
    import types

    import blendercraft_assistant  # noqa: F401 - the loader looks it up

    if "bc_kit" not in sys.modules:
        pkg = next(m for n, m in list(sys.modules.items())
                   if n.rsplit(".", 1)[-1] == "blendercraft_assistant")
        p = Path(pkg.__file__).parent / "plugin/skills/organic-modeling" / "kit.py"
        mod = types.ModuleType("bc_kit")
        mod.__file__ = str(p)
        exec(compile(p.read_text(), str(p), "exec"), mod.__dict__)
        sys.modules["bc_kit"] = mod
    return sys.modules["bc_kit"]


class OrganicKitTests(unittest.TestCase):
    def setUp(self):
        import bpy

        self.bpy = bpy
        self.kit = _load_kit()
        self.before = set(bpy.data.objects.keys())

    def tearDown(self):
        for obj in list(self.bpy.data.objects):
            if obj.name not in self.before:
                self.kit.delete(obj)

    def test_blockout_remesh_smooth_gives_one_manifold_shell(self):
        kit = self.kit
        body = kit.blob("KitTestBody", [
            ("cap", (0, 0, 6), 3.0, 3.0, (0, 0, 90)),
            ("ball", (0, -5, 10), 3.5),
            ("ell", (2.5, 3, 3), (1.5, 1.5, 3.0)),
        ], resolution=0.5)
        kit.remesh_smooth(body, voxel=0.4, iterations=6)
        self.assertEqual(kit.topology(body), (1, True))
        kit.fit(body, size=(None, None, 12.0), low=(None, None, 0.0))
        lo, hi = kit.bounds(body)
        self.assertAlmostEqual(lo.z, 0.0, places=4)
        self.assertAlmostEqual(hi.z - lo.z, 12.0, places=3)

    def test_mirror_is_a_true_mirror(self):
        kit = self.kit
        left = kit.sphere("KitTestLeft", (5, 0, 0), (2, 1, 1))
        right = kit.mirror_x(left, "KitTestRight")
        lo, hi = kit.bounds(right)
        self.assertAlmostEqual(lo.x, -7.0, places=4)
        self.assertAlmostEqual(hi.x, -3.0, places=4)
        import bmesh
        volumes = []
        for obj in (left, right):
            bm = bmesh.new()
            bm.from_mesh(obj.data)
            volumes.append(bm.calc_volume(signed=True))
            bm.free()
        self.assertGreater(volumes[1], 0)  # normals still point out
        self.assertAlmostEqual(volumes[0], volumes[1], places=4)
