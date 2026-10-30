import json
import re
import unittest
from pathlib import Path

from blendercraft_assistant.claude.system_prompt import build_system_prompt

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "blendercraft_assistant" / "plugin"
SKILLS = ("blender-map", "design", "printing", "joints")


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
        for phrase in ("ambiguous", "description", "what you could not"):
            self.assertIn(phrase, text)

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
        for name in SKILLS:
            path = PLUGIN / "skills" / name / "SKILL.md"
            fields = frontmatter(path.read_text(encoding="utf-8"))
            self.assertEqual(fields.get("name"), name, path)
            self.assertTrue(fields.get("description"), path)

    def test_no_stray_skill_folders(self):
        found = sorted(p.parent.name for p in (PLUGIN / "skills").glob("*/SKILL.md"))
        self.assertEqual(found, sorted(SKILLS))
