"""Panel parts of AI 3D generation: the missing-key hint, the running job
line with its cancel button, and image attachments with their path."""

import base64
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import bpy

from blendercraft_assistant import i18n
from blendercraft_assistant.gen3d.jobs import Job, registry
from blendercraft_assistant.tools import gen3d as gen3d_tool

from test_chat_input import _Base as InputBase
from test_ui_logic import PanelDrawTest

PNG = b"\x89PNG\r\n\x1a\nfake"


class Gen3dPanelTest(unittest.TestCase):
    setUp = PanelDrawTest.setUp
    draw = PanelDrawTest.draw
    labels = PanelDrawTest.labels
    operators_drawn = PanelDrawTest.operators_drawn

    def test_settings_hint_without_key(self):
        with mock.patch.object(gen3d_tool, "has_any_key", return_value=False):
            log = self.draw(self.panel.VIEW3D_PT_bc_settings)
        hint = i18n.t("gen3d_key_hint")[:20]
        self.assertIn(hint, " ".join(self.labels(log)))
        with mock.patch.object(gen3d_tool, "has_any_key", return_value=True):
            log = self.draw(self.panel.VIEW3D_PT_bc_settings)
        self.assertNotIn(hint, " ".join(self.labels(log)))

    def test_running_job_line_and_cancel(self):
        log = self.draw(self.panel.VIEW3D_PT_bc_assistant)
        self.assertNotIn("bc.gen3d_cancel", self.operators_drawn(log))
        job = Job(id="gen-1", provider="meshy")
        job.set_progress("generate", "IN_PROGRESS", 40)
        with mock.patch.object(registry, "running", return_value=[job]):
            log = self.draw(self.panel.VIEW3D_PT_bc_assistant)
        self.assertIn("bc.gen3d_cancel", self.operators_drawn(log))
        self.assertTrue(any("40%" in label and "Meshy" in label for label in self.labels(log)))

    def test_cancel_operator_cancels_jobs(self):
        with mock.patch.object(registry, "cancel_all", return_value=1) as cancel_all:
            self.assertEqual(bpy.ops.bc.gen3d_cancel(), {"FINISHED"})
        cancel_all.assert_called_once()


class ImageAttachmentTest(InputBase):
    def test_attach_image_sends_it_with_its_path(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "cachorro.png"
        path.write_bytes(PNG)
        self.assertEqual(bpy.ops.bc.attach_file(filepath=str(path)), {"FINISHED"})
        props = bpy.ops.bc.attach_file.get_rna_type().properties
        self.assertIn("*.png", props["filter_glob"].default)
        self.assertEqual(bpy.ops.bc.send(), {"FINISHED"})
        ((text, attachments),) = self.session.sent
        self.assertEqual(text, i18n.t("attached_files_msg", names="cachorro.png"))
        image, note = attachments
        self.assertEqual(
            image,
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.b64encode(PNG).decode("ascii"),
                },
            },
        )
        self.assertEqual(note["type"], "text")
        self.assertIn(str(path), note["text"])
        self.assertIn("generate_3d_model", note["text"])

    def test_text_files_unchanged(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "plano.md"
        path.write_text("# x", encoding="utf-8")
        bpy.ops.bc.attach_file(filepath=str(path))
        bpy.ops.bc.send()
        ((_text, attachments),) = self.session.sent
        self.assertEqual(attachments, [{"type": "text", "text": "Attached file: plano.md\n\n# x"}])


if __name__ == "__main__":
    unittest.main()
