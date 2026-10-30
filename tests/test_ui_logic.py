import unittest
from unittest import mock

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


if __name__ == "__main__":
    unittest.main()
