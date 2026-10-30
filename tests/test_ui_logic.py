import unittest
from unittest import mock

import bpy

from blendercraft_assistant import i18n
from blendercraft_assistant.ui.text_wrap import strip_markdown, wrap

REQUIRED_KEYS = (
    "assistant idle working error new_chat conversations stop send send_now "
    "queued attach_view attach_selection mode_auto mode_ask mode_readonly "
    "changes undo_ai undo_ai_blocked details show_older approve deny "
    "approval_request claude_missing claude_login step_limit unit joint "
    "joint_reset copy_mcp"
).split()


class I18nTest(unittest.TestCase):
    def test_strings_complete(self):
        self.assertEqual(set(i18n.STRINGS), {"pt", "en"})
        self.assertEqual(set(i18n.STRINGS["pt"]), set(i18n.STRINGS["en"]))
        for key in REQUIRED_KEYS:
            self.assertIn(key, i18n.STRINGS["en"])
        for lang, table in i18n.STRINGS.items():
            for key, value in table.items():
                self.assertTrue(value.strip(), f"{lang}.{key} is empty")

    def test_lang_follows_locale(self):
        with mock.patch.object(i18n, "_locale", return_value="pt_BR"):
            self.assertEqual(i18n.lang(), "pt")
            self.assertEqual(i18n.t("stop"), i18n.STRINGS["pt"]["stop"])
        for locale in ("en_US", "fr_FR", ""):
            with mock.patch.object(i18n, "_locale", return_value=locale):
                self.assertEqual(i18n.lang(), "en")

    def test_t_formats_only_when_asked(self):
        with mock.patch.object(i18n, "_locale", return_value="en_US"):
            self.assertIn("3", i18n.t("changes", n=3))
            # Session notes are formatted later by AgentSession.
            self.assertIn("{n}", i18n.t("queue_dropped"))
            self.assertEqual(i18n.t("no_such_key"), "no_such_key")


LOREM = (
    "Criei um cubo de 20 mm centrado na origem e apliquei um chanfro de "
    "1 mm em todas as arestas para facilitar a impressão sem suportes."
)


class TextWrapTest(unittest.TestCase):
    def test_wrap_width(self):
        lines = wrap(LOREM * 3, 280, 1)
        self.assertGreater(len(lines), 3)
        for line in lines:
            self.assertLessEqual(len(line), 40)

    def test_wrap_scales_with_ui_scale(self):
        self.assertLessEqual(max(len(l) for l in wrap(LOREM, 280, 2)), 20)
        # Background mode reports ui_scale 0; it must not divide by zero.
        self.assertLessEqual(max(len(l) for l in wrap(LOREM, 280, 0)), 40)

    def test_wrap_minimum_width(self):
        lines = wrap(LOREM, 10, 1)
        self.assertTrue(all(len(l) <= 10 for l in lines))
        self.assertTrue(any(len(l) > 5 for l in lines))

    def test_wrap_keeps_paragraphs(self):
        lines = wrap("Primeiro parágrafo.\n\nSegundo.\nTerceira linha.", 280, 1)
        self.assertEqual(
            lines, ["Primeiro parágrafo.", "", "Segundo.", "Terceira linha."]
        )

    def test_wrap_breaks_long_words(self):
        lines = wrap("x" * 100, 280, 1)
        self.assertEqual(["x" * 40, "x" * 40, "x" * 20], lines)

    def test_wrap_empty(self):
        self.assertEqual(wrap("", 280, 1), [])

    def test_strip_markdown(self):
        text = (
            "# Título\n"
            "## Seção\n"
            "Um **cubo** de __20 mm__ com `bevel` e ```code```.\n"
            "Veja [a documentação](https://docs.blender.org) e [isto](x).\n"
            "Nota #3 fica."
        )
        self.assertEqual(
            strip_markdown(text),
            "Título\n"
            "Seção\n"
            "Um cubo de 20 mm com bevel e code.\n"
            "Veja a documentação e isto.\n"
            "Nota #3 fica.",
        )


class PropsTest(unittest.TestCase):
    def setUp(self):
        from blendercraft_assistant import runtime
        from blendercraft_assistant.ui import prefs, props

        self.runtime = runtime
        props.register()
        self.addCleanup(props.unregister)
        prefs.register()
        self.addCleanup(prefs.unregister)
        runtime.mode = "AUTO"
        runtime.model = ""
        self.addCleanup(setattr, runtime, "mode", "AUTO")
        self.addCleanup(setattr, runtime, "model", "")
        self.bc = bpy.context.window_manager.bc

    def test_prefs_id_is_the_package(self):
        from blendercraft_assistant.ui import prefs

        self.assertEqual(prefs.BC_AddonPreferences.bl_idname, "blendercraft_assistant")
        self.assertEqual(prefs.ADDON_ID, self.runtime.ADDON_ID)

    def test_defaults(self):
        self.assertEqual(self.bc.mode, "AUTO")
        self.assertEqual(self.bc.model, "DEFAULT")
        self.assertFalse(self.bc.attach_view)
        self.assertFalse(self.bc.attach_selection)
        self.assertEqual(self.bc.show_older, 0)

    def test_mode_and_model_reach_runtime(self):
        with mock.patch.object(self.runtime, "stop_session") as stop_session:
            self.bc.mode = "READONLY"
            self.assertEqual(self.runtime.mode, "READONLY")
            self.bc.model = "sonnet"
            self.assertEqual(self.runtime.model, "sonnet")
            self.bc.model = "DEFAULT"
            self.assertEqual(self.runtime.model, "")
        self.assertEqual(stop_session.call_count, 3)

    def test_load_post_restores_props_from_runtime(self):
        from blendercraft_assistant.ui import props

        self.runtime.mode = "ASK"
        self.runtime.model = "haiku"
        props._sync_after_load()
        self.assertEqual(self.bc.mode, "ASK")
        self.assertEqual(self.bc.model, "haiku")

    def test_joint_angle_turns_pivot(self):
        from blendercraft_assistant.tools.joints import create_pivot

        bpy.ops.mesh.primitive_cube_add(size=10)
        cube = bpy.context.active_object
        pivot = create_pivot(cube.name, (0.0, 0.0, 0.0), "Z")
        self.addCleanup(bpy.data.objects.remove, bpy.data.objects[pivot], do_unlink=True)
        self.addCleanup(bpy.data.objects.remove, cube, do_unlink=True)
        self.bc.joint_pivot = pivot
        self.bc.joint_angle = 30.0
        self.assertAlmostEqual(bpy.data.objects[pivot]["bc_angle"], 30.0)
        self.bc.joint_pivot = ""
        self.bc.joint_angle = 0.0  # no pivot: nothing happens
        bpy.data.objects[pivot]["bc_angle"] = 45.0
        self.bc.joint_pivot = pivot  # picking a pivot shows its angle
        self.assertAlmostEqual(self.bc.joint_angle, 45.0)


if __name__ == "__main__":
    unittest.main()
