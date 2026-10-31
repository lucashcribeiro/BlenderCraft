"""The extension's log file: location, lazy creation, redaction, and what
the runtime, the tool dispatcher and the Claude process report."""

import logging
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from blendercraft_assistant import log as bclog
from blendercraft_assistant import runtime
from blendercraft_assistant.tools import load_all
from blendercraft_assistant.tools.base import REGISTRY, Tool, ToolResult, register_tool
from blendercraft_assistant.tools.dispatch import call_tool


class LogTestBase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        patcher = mock.patch.dict(os.environ, {"BLENDERCRAFT_LOG_DIR": str(self.dir / "logs")})
        patcher.start()
        self.addCleanup(patcher.stop)
        bclog.shutdown()
        self.addCleanup(bclog.shutdown)
        self.addCleanup(bclog._secrets.clear)

    @property
    def file(self) -> Path:
        return self.dir / "logs" / bclog.LOG_FILE

    def text(self) -> str:
        for handler in logging.getLogger(bclog.__package__).handlers:
            handler.flush()
        return self.file.read_text(encoding="utf-8") if self.file.exists() else ""


class FileHandlerTest(LogTestBase):
    def test_folder_comes_from_the_environment_override(self):
        self.assertEqual(bclog.log_dir(), self.dir / "logs")
        with mock.patch.dict(os.environ, clear=False):
            os.environ.pop("BLENDERCRAFT_LOG_DIR")
            self.assertEqual(bclog.log_dir(), Path.home() / "Library" / "Logs" / "BlenderCraft")

    def test_the_handler_is_lazy_and_the_file_appears_with_the_first_record(self):
        self.assertFalse(self.file.exists())
        self.assertTrue(bclog.ensure())
        self.assertFalse(self.file.exists())  # nothing written yet
        bclog.get("blendercraft_assistant.something").warning("first %s", "record")
        text = self.text()
        self.assertIn("WARNING blendercraft_assistant.something: first record", text)
        self.assertRegex(text, r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d")

    def test_ensure_is_idempotent_and_follows_a_changed_folder(self):
        bclog.ensure()
        bclog.ensure()
        package = logging.getLogger(bclog.__package__)
        self.assertEqual(len([h for h in package.handlers if h is bclog._handler]), 1)
        other = self.dir / "other"
        with mock.patch.dict(os.environ, {"BLENDERCRAFT_LOG_DIR": str(other)}):
            bclog.get("blendercraft_assistant.x").error("elsewhere")
        self.assertIn("elsewhere", (other / bclog.LOG_FILE).read_text(encoding="utf-8"))

    def test_an_unwritable_folder_never_raises(self):
        blocker = self.dir / "file"
        blocker.write_text("x", encoding="utf-8")
        with mock.patch.dict(os.environ, {"BLENDERCRAFT_LOG_DIR": str(blocker / "sub")}):
            self.assertFalse(bclog.ensure())
            logging.getLogger("blendercraft_assistant.x").warning("goes nowhere")

    def test_the_file_rotates(self):
        bclog.ensure()
        handler = bclog._handler
        self.assertEqual(handler.maxBytes, bclog.MAX_BYTES)
        self.assertEqual(handler.backupCount, bclog.BACKUP_COUNT)

    def test_shutdown_detaches_the_handler(self):
        bclog.ensure()
        bclog.shutdown()
        self.assertIsNone(bclog._handler)
        self.assertEqual(logging.getLogger(bclog.__package__).handlers, [])


class RedactionTest(LogTestBase):
    def test_tokens_and_keys_never_reach_the_file(self):
        bclog.add_secret("s3cr3t-session-token-value")
        logger = bclog.get("blendercraft_assistant.redact")
        logger.warning("registered s3cr3t-session-token-value here")
        logger.warning("header Authorization: Bearer abcdef123456789")
        logger.warning("key sk-ant-api03-ABCDEFGHIJKLMNOP and tsk_ZZZZZZZZZZZZ")
        logger.warning("settings {'meshy_api_key': 'msy_12345678', 'token': 'abc12345'}")
        logger.warning("url?api_key=hunter2hunter2&x=1 password=hunter3")
        try:
            raise RuntimeError("failed with token=top-secret-value")
        except RuntimeError:
            logger.exception("with traceback")
        text = self.text()
        for leaked in (
            "s3cr3t-session-token-value", "abcdef123456789", "ABCDEFGHIJKLMNOP",
            "ZZZZZZZZZZZZ", "msy_12345678", "abc12345", "hunter2hunter2", "hunter3",
            "top-secret-value",
        ):
            self.assertNotIn(leaked, text)
        self.assertIn("[redacted]", text)
        self.assertIn("Traceback", text)

    def test_ordinary_text_is_left_alone(self):
        self.assertEqual(
            bclog.redact("tool run_python returned an error: NameError: name 'O' is not defined"),
            "tool run_python returned an error: NameError: name 'O' is not defined",
        )

    def test_short_secrets_are_not_registered(self):
        bclog.add_secret("ab")
        self.assertNotIn("ab", bclog._secrets)


class ToolErrorLogTest(LogTestBase):
    def setUp(self):
        super().setUp()
        load_all()

        def crash(args):
            raise ValueError("handler blew up")

        for name, handler in (
            ("log_crash", crash),
            ("log_error", lambda args: ToolResult(text="bad input: 42", is_error=True)),
            ("log_fine", lambda args: ToolResult(text="all good")),
        ):
            register_tool(
                Tool(name=name, description="x", input_schema={"type": "object", "properties": {}},
                     handler=handler, kind="read")
            )
            self.addCleanup(REGISTRY.pop, name, None)

    def test_a_crashing_tool_is_logged_with_its_traceback(self):
        result = call_tool("log_crash", {})
        self.assertTrue(result.is_error)
        text = self.text()
        self.assertIn("ERROR blendercraft_assistant.tools.dispatch: tool log_crash crashed", text)
        self.assertIn("Traceback", text)
        self.assertIn("ValueError: handler blew up", text)

    def test_an_error_result_is_logged_as_a_warning(self):
        call_tool("log_error", {})
        self.assertIn("WARNING blendercraft_assistant.tools.dispatch: tool log_error returned an error: bad input: 42", self.text())

    def test_run_python_errors_show_the_users_traceback(self):
        call_tool("run_python", {"code": "O + 1", "description": "x"})
        self.assertIn("NameError", self.text())

    def test_a_successful_tool_logs_nothing(self):
        call_tool("log_fine", {})
        self.assertEqual(self.text(), "")


class RuntimeLogTest(LogTestBase):
    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, runtime, "store", runtime.store)
        patcher = mock.patch.object(runtime, "WORKSPACE", self.dir / "workspace")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_start_and_stop_are_logged_without_the_token(self):
        runtime.start()
        token = runtime.token
        self.assertTrue(token)
        runtime.stop()
        text = self.text()
        self.assertIn("runtime started: MCP server at http://127.0.0.1:", text)
        self.assertIn("runtime stopping", text)
        self.assertNotIn(token, text)

    def test_the_token_is_masked_if_it_leaks_into_a_message(self):
        runtime.start()
        self.addCleanup(runtime.stop)
        logging.getLogger("blendercraft_assistant.runtime").warning("oops %s", runtime.token)
        self.assertNotIn(runtime.token, self.text())

    def test_claude_exit_code_and_stderr_tail_are_logged(self):
        runtime.start()
        self.addCleanup(runtime.stop)
        seen = []
        runtime._deliver_exit(lambda code, tail: seen.append(code), 2, "boom: something broke\n")
        runtime._deliver_exit(lambda code, tail: seen.append(code), 0, "")
        text = self.text()
        self.assertIn("Claude Code exited with code 2; stderr tail: boom: something broke", text)
        self.assertIn("Claude Code exited with code 0", text)
        self.assertEqual(seen, [2, 0])

    def test_the_stderr_tail_is_redacted_and_bounded(self):
        runtime.start()
        self.addCleanup(runtime.stop)
        tail = "x" * 5000 + " Authorization: Bearer abcdef123456789"
        runtime._deliver_exit(lambda *a: None, 1, tail)
        text = self.text()
        self.assertNotIn("abcdef123456789", text)
        self.assertLess(len(text), 2000)

    def test_a_missing_claude_is_a_warning(self):
        runtime.start()
        self.addCleanup(runtime.stop)
        with mock.patch.object(runtime, "find_claude", return_value=None):
            with self.assertRaises(RuntimeError):
                runtime._spawn(None, lambda l: None, lambda c, t: None)
        self.assertIn("WARNING blendercraft_assistant.runtime: Claude Code not found", self.text())


class LauncherAndRunnerTest(unittest.TestCase):
    def test_the_test_runner_keeps_logs_out_of_the_real_folder(self):
        self.assertTrue(os.environ.get("BLENDERCRAFT_LOG_DIR"))
        self.assertNotIn("Library/Logs", os.environ["BLENDERCRAFT_LOG_DIR"])


if __name__ == "__main__":
    unittest.main()
