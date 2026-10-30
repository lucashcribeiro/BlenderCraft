import unittest
from unittest import mock

from blendercraft_assistant import i18n

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


if __name__ == "__main__":
    unittest.main()
