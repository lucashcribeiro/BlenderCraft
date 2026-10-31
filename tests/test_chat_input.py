"""Input card: Enter / Ctrl+Enter keys, pick context, multi-line drafts."""

import time
import unittest
from unittest import mock

import bpy

from blendercraft_assistant import i18n

from test_ui_logic import FakeSession, _quiet_reports


class _Base(unittest.TestCase):
    def setUp(self):
        quiet = _quiet_reports()
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)
        from blendercraft_assistant import runtime
        from blendercraft_assistant.ui import draft, keymap, operators, props

        self.runtime = runtime
        self.props_mod = props
        self.draft = draft
        self.keymap = keymap
        for module in (props, operators, keymap):
            module.register()
            self.addCleanup(module.unregister)
        self.bc = bpy.context.window_manager.bc
        self.session = FakeSession()
        self.session.status = "IDLE"
        for name, value in (
            ("ensure_session", mock.Mock(return_value=self.session)),
            ("server", mock.Mock()),
            ("store", mock.Mock()),
            ("session", self.session),
        ):
            patcher = mock.patch.object(runtime, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.bc.input = ""
        self.bc.draft = ""
        self.bc.attached_files.clear()
        self.addCleanup(draft.clear, self.bc)
        self.props_mod.input_committed_at = 0.0
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)


class KeysTest(_Base):
    def test_field_commit_records_time(self):
        before = time.monotonic()
        self.bc.input = "crie um cubo"
        self.assertGreaterEqual(self.props_mod.input_committed_at, before)
        self.assertEqual(self.session.sent, [])  # committing alone never sends

    def test_enter_release_right_after_commit_sends(self):
        self.bc.input = "crie um cubo"
        self.assertEqual(bpy.ops.bc.send_key(), {"FINISHED"})
        self.assertEqual(self.session.sent, [("crie um cubo", [])])
        self.assertEqual(self.bc.input, "")
        # A second release (no new commit) does nothing.
        self.bc.input = "x"
        self.props_mod.input_committed_at = 0.0
        self.assertFalse(bpy.ops.bc.send_key.poll())

    def test_enter_long_after_commit_does_not_send(self):
        # A click elsewhere commits the field too, with no Enter release.
        self.bc.input = "crie um cubo"
        self.props_mod.input_committed_at = time.monotonic() - 5.0
        self.assertFalse(bpy.ops.bc.send_key.poll())

    def test_ctrl_enter_sends_now_while_working(self):
        self.session.status = "WORKING"
        self.bc.input = "pare e faça outra coisa"
        self.assertEqual(bpy.ops.bc.send_key_now(), {"FINISHED"})
        self.assertEqual(self.session.sent_now, [("pare e faça outra coisa", [])])
        self.session.status = "IDLE"
        self.bc.input = "oi"
        self.assertEqual(bpy.ops.bc.send_key_now(), {"FINISHED"})
        self.assertEqual(self.session.sent, [("oi", [])])

    def test_plain_enter_queues_while_working(self):
        self.session.status = "WORKING"
        self.bc.input = "depois"
        self.assertEqual(bpy.ops.bc.send_key(), {"FINISHED"})
        self.assertEqual(self.session.sent, [("depois", [])])  # session.send queues

    def test_keys_need_text(self):
        self.props_mod.input_committed_at = time.monotonic()
        self.assertFalse(bpy.ops.bc.send_key.poll())
        self.assertFalse(bpy.ops.bc.send_key_now.poll())

    def test_keymap_items(self):
        items = [(km.name, kmi.idname, kmi.type, kmi.value, kmi.ctrl, kmi.oskey) for km, kmi in self.keymap.items]
        for key in ("RET", "NUMPAD_ENTER"):
            self.assertIn(("Window", "bc.send_key", key, "RELEASE", False, False), items)
            self.assertIn(("Window", "bc.send_key_now", key, "RELEASE", True, False), items)
            self.assertIn(("Window", "bc.send_key_now", key, "RELEASE", False, True), items)
        km = self.keymap.items[0][0]
        count, ours = len(km.keymap_items), len(self.keymap.items)
        self.keymap.unregister()
        self.assertEqual(self.keymap.items, [])
        self.assertEqual(len(km.keymap_items), count - ours)
        self.keymap.register()  # what the cleanup unregisters


class PickContextTest(_Base):
    def test_pick_context_attaches_selection(self):
        self.assertEqual(bpy.ops.bc.pick_context(), {"CANCELLED"})
        self.assertFalse(self.bc.attach_selection)
        bpy.ops.mesh.primitive_cube_add()
        bpy.ops.mesh.primitive_cube_add(location=(5, 0, 0))
        for obj in bpy.data.objects:
            obj.select_set(True)
        self.assertEqual(bpy.ops.bc.pick_context(), {"FINISHED"})
        self.assertTrue(self.bc.attach_selection)


class DraftTest(_Base):
    MD = "# Título\n\n| a | b |\n|---|---|\n| 1 | 2 |\n" + "\n".join(f"linha {i}" for i in range(40))

    def paste(self, text):
        with mock.patch.object(self.draft, "read_clipboard", return_value=text):
            return bpy.ops.bc.paste_draft()

    def test_paste_keeps_every_line(self):
        self.assertEqual(self.paste(self.MD), {"FINISHED"})
        self.assertEqual(self.draft.text(self.bc), self.MD)
        self.assertEqual(self.bc.input, "")

    def test_paste_appends_to_the_draft(self):
        self.paste("primeiro\nbloco")
        self.paste("segundo")
        self.assertEqual(self.draft.text(self.bc), "primeiro\nbloco\nsegundo")

    def test_paste_empty_clipboard(self):
        self.assertEqual(self.paste("  \n"), {"CANCELLED"})
        self.assertEqual(self.draft.text(self.bc), "")

    def test_send_uses_draft_and_typed_text(self):
        self.paste(self.MD)
        self.bc.input = "resuma isso"
        self.assertEqual(bpy.ops.bc.send(), {"FINISHED"})
        ((text, _),) = self.session.sent
        self.assertEqual(text, self.MD.strip() + "\n\nresuma isso")
        self.assertEqual(self.draft.text(self.bc), "")
        self.assertEqual(self.bc.input, "")

    def test_send_draft_alone_and_with_enter(self):
        self.paste("a\nb")
        self.assertEqual(bpy.ops.bc.send(), {"FINISHED"})
        self.assertEqual(self.session.sent[-1][0], "a\nb")
        self.paste("c\nd")
        self.bc.input = "e"
        self.assertEqual(bpy.ops.bc.send_key(), {"FINISHED"})
        self.assertEqual(self.session.sent[-1][0], "c\nd\n\ne")

    def test_send_before_runtime_keeps_draft(self):
        self.paste("a\nb")
        with mock.patch.object(self.runtime, "server", None):
            self.assertEqual(bpy.ops.bc.send(), {"CANCELLED"})
        self.assertEqual(self.draft.text(self.bc), "a\nb")

    def test_editor_moves_draft_into_a_text_block(self):
        self.paste("a\nb")
        self.assertEqual(bpy.ops.bc.edit_draft(), {"FINISHED"})  # no window in -b: text only
        txt = self.draft.composer()
        self.assertIsNotNone(txt)
        self.assertEqual(txt.as_string(), "a\nb")
        self.assertFalse(txt.use_fake_user)  # never saved in the .blend
        self.assertEqual(self.bc.draft, "")
        self.assertEqual(self.draft.text(self.bc), "a\nb")
        # Typing in the composer is the draft; pasting appends to it.
        txt.write("\nc")
        self.paste("d")
        self.assertEqual(self.draft.text(self.bc), "a\nb\nc\nd")
        # Opening again reuses the same block.
        bpy.ops.bc.edit_draft()
        self.assertEqual(len([t for t in bpy.data.texts if t.get(self.draft.DRAFT_KEY)]), 1)
        self.assertEqual(bpy.ops.bc.send(), {"FINISHED"})
        self.assertEqual(self.session.sent[-1][0], "a\nb\nc\nd")
        self.assertIsNone(self.draft.composer())  # removed after sending

    def test_clear_draft(self):
        self.paste("a\nb")
        bpy.ops.bc.edit_draft()
        self.assertEqual(bpy.ops.bc.clear_draft(), {"FINISHED"})
        self.assertEqual(self.draft.text(self.bc), "")
        self.assertIsNone(self.draft.composer())

    def test_preview(self):
        self.paste(self.MD)
        lines, count = self.draft.preview(self.draft.text(self.bc), 400, 1.0)
        self.assertEqual(count, self.MD.count("\n") + 1)
        self.assertEqual(len(lines), self.draft.PREVIEW_LINES)
        self.assertEqual(lines[0], "# Título")

    def test_markdown_reaches_claude_verbatim(self):
        text = "## 4. Pernas\n\n| a | **b** |\n|---|---|\n| 1 | **−23 %** ❌ |\n\n- item\n```\n  code\n```"
        self.paste(text)
        bpy.ops.bc.send()
        self.assertEqual(self.session.sent[-1][0], text)

    def test_attach_file(self):
        import tempfile
        from pathlib import Path

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "plano.md"
        path.write_text("# Plano\n\n| a | b |\n|---|---|\n| ❌ | **x** |\n", encoding="utf-8")
        self.assertEqual(bpy.ops.bc.attach_file(filepath=str(path)), {"FINISHED"})
        self.assertEqual([f.name for f in self.bc.attached_files], ["plano.md"])
        props = bpy.ops.bc.attach_file.get_rna_type().properties
        self.assertEqual(props["filter_glob"].default, "*.md;*.markdown;*.txt;*.png;*.jpg;*.jpeg;*.webp")
        # Only files attached: the message names them.
        self.assertEqual(bpy.ops.bc.send(), {"FINISHED"})
        ((text, attachments),) = self.session.sent
        self.assertEqual(text, i18n.t("attached_files_msg", names="plano.md"))
        self.assertEqual(
            attachments,
            [{"type": "text", "text": "Attached file: plano.md\n\n" + path.read_text(encoding="utf-8")}],
        )
        self.assertEqual(len(self.bc.attached_files), 0)

    def test_remove_attached_file(self):
        for name in ("a.md", "b.txt"):
            item = self.bc.attached_files.add()
            item.name, item.content = name, "x"
        self.addCleanup(self.bc.attached_files.clear)
        self.assertEqual(bpy.ops.bc.remove_file(index=0), {"FINISHED"})
        self.assertEqual([f.name for f in self.bc.attached_files], ["b.txt"])
        self.assertEqual(bpy.ops.bc.remove_file(index=5), {"CANCELLED"})

    def test_strings(self):
        for key in (
            "paste_draft", "paste_draft_desc", "clipboard_empty", "edit_draft",
            "edit_draft_desc", "clear_draft", "draft_lines", "draft_name",
            "pick_context", "pick_context_desc", "pick_context_done",
            "pick_context_none", "attach_selection_n", "placeholder",
            "placeholder_working", "settings", "tool_ok", "tool_running",
            "tool_error", "tool_interrupted", "send_key", "send_key_now", "queue",
            "attach_file", "attach_file_desc", "remove_file", "attached_files_msg",
        ):
            self.assertIn(key, i18n.STRINGS["pt"], key)
            self.assertIn(key, i18n.STRINGS["en"], key)


if __name__ == "__main__":
    unittest.main()
