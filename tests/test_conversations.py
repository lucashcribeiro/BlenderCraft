import json
import tempfile
import time
import unittest
from pathlib import Path

from blendercraft_assistant.conversations import (
    Conversation,
    ConversationStore,
    Message,
    ToolCall,
)


class ConversationStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "conversations"
        self.store = ConversationStore(self.root)

    def _saved(self, blend_path, updated, text="hi"):
        conv = self.store.new(blend_path, "sonnet")
        conv.messages.append(Message("user", text))
        conv.updated = updated
        self.store.save(conv)
        return conv

    def test_roundtrip(self):
        conv = self.store.new("/tmp/a.blend", "opus")
        conv.claude_session_id = "abc"
        conv.messages.append(Message("user", "faça um cubo"))
        conv.messages.append(
            Message(
                "assistant",
                "pronto",
                [ToolCall("t1", "run_python", {"code": "x"}, "ok!", "ok")],
            )
        )
        self.store.save(conv)
        loaded = self.store.load(conv.id)
        self.assertEqual(loaded, conv)
        self.assertIsInstance(loaded.messages[1].tools[0], ToolCall)
        self.assertEqual(loaded.model, "opus")
        self.assertEqual(loaded.blend_path, "/tmp/a.blend")

    def test_new_defaults(self):
        before = time.time()
        conv = self.store.new("/tmp/a.blend", "opus")
        self.assertEqual(len(conv.id), 32)
        self.assertIsNone(conv.claude_session_id)
        self.assertEqual(conv.messages, [])
        self.assertGreaterEqual(conv.created, before)

    def test_title_is_first_user_message_cut_to_60(self):
        conv = self.store.new("", "m")
        conv.messages.append(Message("user", "x" * 100))
        conv.messages.append(Message("user", "second"))
        self.store.save(conv)
        self.assertEqual(self.store.load(conv.id).title, "x" * 60)

    def test_title_collapses_whitespace_and_is_kept_once_set(self):
        conv = self.store.new("", "m")
        conv.messages.append(Message("user", "olá\n  mundo"))
        self.store.save(conv)
        self.assertEqual(conv.title, "olá mundo")
        conv.title = "renamed"
        self.store.save(conv)
        self.assertEqual(self.store.load(conv.id).title, "renamed")

    def test_save_is_atomic_and_leaves_no_tmp(self):
        conv = self._saved("", 1.0)
        self.assertEqual([p.name for p in self.root.iterdir()], [f"{conv.id}.json"])

    def test_list_current_first(self):
        old_here = self._saved("/here.blend", 10.0)
        new_other = self._saved("/other.blend", 50.0)
        new_here = self._saved("/here.blend", 30.0)
        old_other = self._saved("/other.blend", 20.0)
        ids = [c.id for c in self.store.list("/here.blend")]
        self.assertEqual(
            ids, [new_here.id, old_here.id, new_other.id, old_other.id]
        )

    def test_list_without_current_sorts_by_updated(self):
        a = self._saved("/a.blend", 10.0)
        b = self._saved("/b.blend", 20.0)
        self.assertEqual([c.id for c in self.store.list()], [b.id, a.id])

    def test_list_empty_when_root_missing(self):
        self.assertEqual(self.store.list(), [])

    def test_corrupt_file_skipped(self):
        good = self._saved("", 1.0)
        (self.root / "broken.json").write_text("{not json", encoding="utf-8")
        (self.root / "wrongshape.json").write_text(json.dumps({"id": "x"}), encoding="utf-8")
        with self.assertLogs("blendercraft_assistant.conversations", level="WARNING"):
            listed = self.store.list()
        self.assertEqual([c.id for c in listed], [good.id])

    def test_delete(self):
        conv = self._saved("", 1.0)
        self.store.delete(conv.id)
        self.assertEqual(self.store.list(), [])
        self.store.delete(conv.id)  # already gone: no error

    def test_load_missing_raises(self):
        with self.assertRaises(FileNotFoundError):
            self.store.load("0" * 32)

    def test_ids_cannot_escape_root(self):
        with self.assertRaises(ValueError):
            self.store.load("../evil")
        with self.assertRaises(ValueError):
            self.store.delete("../evil")


if __name__ == "__main__":
    unittest.main()
