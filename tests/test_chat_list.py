"""Conversation flattened into scrollable list rows, and follow-bottom."""

import unittest
from unittest import mock

import bpy

from blendercraft_assistant import i18n
from blendercraft_assistant.conversations import Message, ToolCall


def _messages():
    return [
        Message("user", "crie um cubo de 20 mm"),
        Message(
            "assistant",
            "## Pronto\nCriei o **cubo**.",
            [
                ToolCall("tu1", "run_python", {"code": "x", "description": "cubo"}, "saida ok", "ok"),
                ToolCall("tu2", "screenshot", {"view": "iso"}, "", "running"),
            ],
        ),
    ]


class FlattenTest(unittest.TestCase):
    def setUp(self):
        from blendercraft_assistant.ui import chat_list

        self.chat = chat_list

    def rows(self, messages=None, error="", expanded="", width=400):
        return self.chat.flatten(messages if messages is not None else _messages(), error, expanded, width, 1.0)

    def test_kinds_in_order(self):
        rows = self.rows()
        kinds = [r[0] for r in rows]
        self.assertEqual(
            kinds,
            ["USER_HEAD", "USER", "GAP", "CLAUDE_HEAD", "HEADING", "TEXT", "TOOL", "TOOL", "END"],
        )
        self.assertEqual(rows[1][1], "crie um cubo de 20 mm")
        # Headings keep their structure; inline markup is reduced to its text.
        self.assertEqual(rows[4][1], "Pronto")
        self.assertEqual([r[1] for r in rows if r[0] == "TEXT"], ["Criei o cubo."])
        tool = rows[6]
        self.assertEqual(tool[1], "Executando Python — cubo")
        self.assertEqual((tool[2], tool[3]), ("tu1", "ok"))
        self.assertEqual(rows[7][3], "running")
        self.assertEqual(rows[7][1], "Tirando print (iso)")  # no description: label only

    def test_long_text_is_wrapped_to_width(self):
        long = "palavra " * 60
        rows = self.rows([Message("assistant", long)], width=200)
        texts = [r[1] for r in rows if r[0] == "TEXT"]
        self.assertGreater(len(texts), 5)
        self.assertTrue(all(len(t) <= 200 // 7 for t in texts))

    def test_expanded_tool_adds_detail_rows(self):
        collapsed = self.rows()
        expanded = self.rows(expanded="tu1")
        details = [r[1] for r in expanded if r[0] == "DETAIL"]
        self.assertFalse(any(r[0] == "DETAIL" for r in collapsed))
        self.assertTrue(any(d.startswith("→") for d in details))
        self.assertTrue(any("saida ok" in d for d in details))
        # Details follow their tool row.
        kinds = [r[0] for r in expanded]
        self.assertEqual(kinds[kinds.index("DETAIL") - 1], "TOOL")

    def test_error_rows(self):
        rows = self.rows(error="Claude Code exited with code 1")
        self.assertIn(("ERROR", "Claude Code exited with code 1", "", ""), rows)
        self.assertEqual(rows[-1][0], "END")

    def test_empty(self):
        self.assertEqual([r[0] for r in self.rows([])], ["END"])

    def test_visible_rows_fit_the_region(self):
        self.assertEqual(self.chat.visible_rows(772, 1.0), 772 // 20 - self.chat.RESERVED_ROWS)
        self.assertEqual(self.chat.visible_rows(100, 1.0), self.chat.MIN_ROWS)
        self.assertEqual(self.chat.visible_rows(10000, 1.0), self.chat.MAX_ROWS)
        self.assertEqual(self.chat.visible_rows(772, 2.0), self.chat.MIN_ROWS)
        self.assertEqual(self.chat.visible_rows(772, 0.0), self.chat.visible_rows(772, 1.0))
        self.assertEqual(self.chat.visible_rows(772, 1.0, 5), self.chat.visible_rows(772, 1.0) - 5)


class FakeSession:
    def __init__(self, messages, status="IDLE", error=""):
        self.conversation = mock.Mock(id="c1", messages=messages)
        self.status = status
        self.error = error


class SyncTest(unittest.TestCase):
    def setUp(self):
        from blendercraft_assistant.ui import chat_list, props

        self.chat = chat_list
        props.register()
        self.addCleanup(props.unregister)
        self.chat.reset()
        self.addCleanup(self.chat.reset)
        self.bc = bpy.context.window_manager.bc
        self.bc.chat_lines.clear()
        self.bc.chat_index = 0
        self.bc.expanded_tool = ""
        self.bc.show_older = 0

    def sync(self, session):
        return self.chat.sync(self.bc, session, 400, 1.0)

    def texts(self):
        return [(line.kind, line.text) for line in self.bc.chat_lines]

    def test_rows_written_once_per_change(self):
        session = FakeSession(_messages())
        self.assertTrue(self.sync(session))
        self.assertEqual(len(self.bc.chat_lines), 9)
        self.assertEqual(self.bc.chat_lines[6].tool_id, "tu1")
        self.assertFalse(self.sync(session))  # nothing changed: no rewrite
        session.conversation.messages[1].text += " Mais texto."
        self.assertTrue(self.sync(session))
        self.assertIn(("TEXT", "Criei o cubo. Mais texto."), self.texts())

    def test_follows_the_bottom_while_streaming(self):
        session = FakeSession(_messages(), status="WORKING")
        self.sync(session)
        self.assertEqual(self.bc.chat_index, len(self.bc.chat_lines) - 1)
        self.bc.chat_index = 2  # the user clicked an older row
        session.conversation.messages.append(Message("assistant", "nova linha"))
        self.sync(session)
        # While the agent works the newest line always shows.
        self.assertEqual(self.bc.chat_index, len(self.bc.chat_lines) - 1)

    def test_idle_keeps_a_row_the_user_picked(self):
        session = FakeSession(_messages())
        self.sync(session)
        self.bc.chat_index = 2
        session.conversation.messages.append(Message("user", "outra"))
        self.sync(session)
        self.assertEqual(self.bc.chat_index, 2)
        # At the bottom it keeps following.
        self.bc.chat_index = len(self.bc.chat_lines) - 1
        session.conversation.messages.append(Message("assistant", "resposta"))
        self.sync(session)
        self.assertEqual(self.bc.chat_index, len(self.bc.chat_lines) - 1)

    def test_other_conversation_jumps_to_bottom(self):
        session = FakeSession(_messages())
        self.sync(session)
        self.bc.chat_index = 0
        other = FakeSession([Message("user", "oi")])
        other.conversation.id = "c2"
        self.sync(other)
        self.assertEqual(self.texts(), [("USER_HEAD", i18n.t("you")), ("USER", "oi"), ("END", "")])
        self.assertEqual(self.bc.chat_index, 2)

    def test_details_toggle_and_width_rebuild(self):
        session = FakeSession(_messages())
        self.sync(session)
        self.bc.expanded_tool = "tu1"
        self.assertTrue(self.sync(session))
        self.assertTrue(any(line.kind == "DETAIL" for line in self.bc.chat_lines))
        self.assertTrue(self.chat.sync(self.bc, session, 150, 1.0))

    def test_no_session(self):
        self.sync(FakeSession(_messages()))
        self.sync(None)
        self.assertEqual(self.texts(), [("END", "")])

    def test_pages_of_older_messages(self):
        many = [Message("user", f"m{i}") for i in range(self.chat.MESSAGES_PER_PAGE + 5)]
        session = FakeSession(many)
        self.sync(session)
        users = [line.text for line in self.bc.chat_lines if line.kind == "USER"]
        self.assertEqual(len(users), self.chat.MESSAGES_PER_PAGE)
        self.assertEqual(users[-1], many[-1].text)
        self.assertTrue(self.chat.has_older(session, self.bc))
        self.bc.show_older = 1
        self.sync(session)
        users = [line.text for line in self.bc.chat_lines if line.kind == "USER"]
        self.assertEqual(len(users), len(many))
        self.assertFalse(self.chat.has_older(session, self.bc))


if __name__ == "__main__":
    unittest.main()
