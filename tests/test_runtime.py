import json
import shlex
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import bpy

from blendercraft_assistant import mainthread, runtime
from blendercraft_assistant.conversations import ConversationStore

TESTS_DIR = Path(__file__).resolve().parent
FIXTURES = TESTS_DIR / "fixtures"
FAKE_CLAUDE = TESTS_DIR / "fake_claude.py"

LOGIN_FIXTURE = (
    '{"type":"result","is_error":true,'
    '"result":"Invalid API key \\u00b7 Please run /login"}\n'
)


def pump_until(cond, timeout=10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        mainthread.pump()
        if cond():
            return True
        time.sleep(0.01)
    return False


def rpc(method, params=None, token=None):
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    ).encode("utf-8")
    request = urllib.request.Request(
        runtime.server.url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token or runtime.token}",
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def rpc_in_thread(method, params=None):
    """Run rpc on a worker thread (tools/call waits for the main thread)."""
    box = {}

    def worker():
        try:
            box["reply"] = rpc(method, params)
        except Exception as exc:  # noqa: BLE001 - surfaced by the test
            box["error"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread, box


class RuntimeTestBase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        patcher = mock.patch.object(runtime, "WORKSPACE", self.tmp / "workspace")
        patcher.start()
        self.addCleanup(patcher.stop)
        runtime.mode = "AUTO"
        runtime.model = ""
        runtime.claude_problem = ""
        runtime.start()
        self.addCleanup(runtime.stop)
        runtime.store = ConversationStore(self.tmp / "conversations")

    def fake_claude(self, fixture: Path) -> str:
        """A `claude` that logs its arguments and replays fixture."""
        self.args_log = self.tmp / "args.txt"
        script = self.tmp / "claude"
        script.write_text(
            "#!/bin/sh\n"
            f'for a in "$@"; do printf "%s\\n" "$a"; done >> {shlex.quote(str(self.args_log))}\n'
            f"echo ---- >> {shlex.quote(str(self.args_log))}\n"
            f"exec {shlex.quote(sys.executable)} {shlex.quote(str(FAKE_CLAUDE))} "
            f"{shlex.quote(str(fixture))}\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
        return str(script)

    def spawned_args(self) -> list[list[str]]:
        runs = self.args_log.read_text(encoding="utf-8").split("----\n")
        return [run.splitlines() for run in runs if run.strip()]


class RuntimeServerTest(RuntimeTestBase):
    def test_tools_list_requires_token(self):
        names = [t["name"] for t in rpc("tools/list")["result"]["tools"]]
        self.assertIn("run_python", names)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            rpc("tools/list", token="wrong")
        self.assertEqual(ctx.exception.code, 401)

    def test_readonly_lists_only_read_tools(self):
        runtime.set_mode("READONLY")
        names = {t["name"] for t in rpc("tools/list")["result"]["tools"]}
        self.assertIn("inspect_scene", names)
        self.assertNotIn("run_python", names)
        reply = rpc("tools/call", {"name": "run_python", "arguments": {"code": "1"}})
        self.assertTrue(reply["result"]["isError"])

    def test_tool_call_runs_on_main_thread_as_ai_step(self):
        before = len(bpy.data.objects)
        thread, box = rpc_in_thread(
            "tools/call",
            {
                "name": "run_python",
                "arguments": {
                    "code": "bpy.ops.mesh.primitive_cube_add(size=20)",
                    "description": "cubo",
                },
            },
        )
        self.assertTrue(pump_until(lambda: not thread.is_alive()))
        self.assertNotIn("error", box)
        self.assertFalse(box["reply"]["result"]["isError"], box["reply"])
        self.addCleanup(
            lambda: bpy.data.objects.remove(bpy.data.objects[-1], do_unlink=True)
        )
        self.assertEqual(len(bpy.data.objects), before + 1)
        self.assertTrue(runtime.tracker.can_undo_last_turn())

    def test_depsgraph_handler_installed(self):
        self.assertIn(runtime._on_depsgraph_update, bpy.app.handlers.depsgraph_update_post)

    def test_stop_stops_server_and_handlers(self):
        url = runtime.server.url
        runtime.stop()
        self.assertIsNone(runtime.server)
        self.assertNotIn(
            runtime._on_depsgraph_update, bpy.app.handlers.depsgraph_update_post
        )
        with self.assertRaises(urllib.error.URLError):
            urllib.request.urlopen(url, data=b"{}", timeout=2)
        runtime.stop()  # idempotent

    def test_mcp_command(self):
        command = runtime.mcp_command()
        self.assertEqual(
            command,
            f"claude mcp add --transport http blendercraft {runtime.server.url} "
            f'--header "Authorization: Bearer {runtime.token}"',
        )


CUBE_ARGS = {"code": "bpy.ops.mesh.primitive_cube_add(size=4)", "description": "cubo"}


class RuntimeAskEnforcementTest(RuntimeTestBase):
    def setUp(self):
        super().setUp()
        runtime.set_mode("ASK")
        self.before = len(bpy.data.objects)
        self.addCleanup(self.remove_new_objects)

    def remove_new_objects(self):
        while len(bpy.data.objects) > self.before:
            bpy.data.objects.remove(bpy.data.objects[-1], do_unlink=True)

    def call(self, name, args):
        thread, box = rpc_in_thread("tools/call", {"name": name, "arguments": args})
        self.assertTrue(pump_until(lambda: not thread.is_alive()))
        self.assertNotIn("error", box)
        return box["reply"]["result"]

    def grant(self, name, args, allow=True):
        box = {}

        def worker():
            box["decision"] = runtime._approve(f"mcp__blendercraft__{name}", args)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        self.assertTrue(pump_until(lambda: runtime.pending_approval is not None))
        runtime.answer_approval(allow)
        thread.join(5)
        return box["decision"]

    def test_write_call_without_permit_is_refused(self):
        result = self.call("run_python", CUBE_ARGS)
        self.assertTrue(result["isError"])
        self.assertIn("approval required", result["content"][0]["text"])
        self.assertEqual(len(bpy.data.objects), self.before)

    def test_permit_runs_once(self):
        self.assertTrue(self.grant("run_python", dict(reversed(list(CUBE_ARGS.items()))))[0])
        result = self.call("run_python", CUBE_ARGS)  # same input, other key order
        self.assertFalse(result["isError"], result)
        self.assertEqual(len(bpy.data.objects), self.before + 1)
        again = self.call("run_python", CUBE_ARGS)
        self.assertTrue(again["isError"])
        self.assertEqual(len(bpy.data.objects), self.before + 1)

    def test_permit_is_for_that_input_only(self):
        self.grant("run_python", CUBE_ARGS)
        other = dict(CUBE_ARGS, code="bpy.ops.mesh.primitive_cube_add(size=8)")
        self.assertTrue(self.call("run_python", other)["isError"])
        self.assertTrue(self.call("save", {})["isError"])  # file tools too

    def test_denied_request_gives_no_permit(self):
        self.assertFalse(self.grant("run_python", CUBE_ARGS, allow=False)[0])
        self.assertTrue(self.call("run_python", CUBE_ARGS)["isError"])

    def test_read_tools_need_no_permit(self):
        self.assertFalse(self.call("inspect_scene", {})["isError"])

    def test_permits_cleared_on_stop_and_mode_change(self):
        self.grant("run_python", CUBE_ARGS)
        runtime.stop_session()
        self.assertTrue(self.call("run_python", CUBE_ARGS)["isError"])
        self.grant("run_python", CUBE_ARGS)
        runtime.set_mode("AUTO")
        runtime.set_mode("ASK")
        self.assertTrue(self.call("run_python", CUBE_ARGS)["isError"])

    def test_auto_mode_needs_no_permit(self):
        runtime.set_mode("AUTO")
        self.assertFalse(self.call("run_python", CUBE_ARGS)["isError"])


class RuntimeApprovalTest(RuntimeTestBase):
    def ask(self):
        box = {}

        def worker():
            box["decision"] = runtime._approve(
                "mcp__blendercraft__run_python", {"code": "x = 1"}
            )

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        self.assertTrue(pump_until(lambda: runtime.pending_approval is not None))
        return thread, box

    def test_approve(self):
        thread, box = self.ask()
        name, tool_input, _event, _answer = runtime.pending_approval
        self.assertEqual(name, "run_python")
        self.assertEqual(tool_input, {"code": "x = 1"})
        runtime.answer_approval(True)
        thread.join(5)
        self.assertEqual(box["decision"][0], True)
        self.assertIsNone(runtime.pending_approval)

    def test_deny(self):
        thread, box = self.ask()
        runtime.answer_approval(False)
        thread.join(5)
        self.assertFalse(box["decision"][0])
        self.assertTrue(box["decision"][1])

    def test_timeout_denies(self):
        with mock.patch.object(runtime, "APPROVAL_TIMEOUT", 0.05):
            decision = runtime._approve("mcp__blendercraft__save", {})
        self.assertFalse(decision[0])
        self.assertIsNone(runtime.pending_approval)

    def test_stop_denies_pending(self):
        thread, box = self.ask()
        runtime.stop()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertFalse(box["decision"][0])

    def two_requests(self):
        """Two concurrent approvals: one on screen, one waiting behind it."""
        box = {}

        def worker(key):
            start = time.monotonic()
            box[key] = runtime._approve("mcp__blendercraft__run_python", {"code": key})
            box[f"{key}_seconds"] = time.monotonic() - start

        first = threading.Thread(target=worker, args=("A",), daemon=True)
        first.start()
        self.assertTrue(pump_until(lambda: runtime.pending_approval is not None))
        second = threading.Thread(target=worker, args=("B",), daemon=True)
        second.start()
        time.sleep(0.1)  # let it queue behind the first
        return (first, second), box

    def test_stop_session_denies_queued_requests_too(self):
        threads, box = self.two_requests()
        runtime.stop_session()
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertFalse(box["A"][0])
        self.assertFalse(box["B"][0])
        self.assertIsNone(runtime.pending_approval)

    def test_new_conversation_denies_queued_requests(self):
        threads, box = self.two_requests()
        runtime.new_conversation()
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertIsNone(runtime.pending_approval)
        self.assertFalse(box["B"][0])

    def test_runtime_stop_denies_queued_requests(self):
        threads, box = self.two_requests()
        runtime.stop()
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertFalse(box["B"][0])
        self.assertIsNone(runtime.pending_approval)

    def test_requests_after_stop_session_still_shown(self):
        runtime.stop_session()  # an older Stop does not deny new requests
        thread, box = self.ask()
        runtime.answer_approval(True)
        thread.join(5)
        self.assertTrue(box["decision"][0])

    def test_lock_wait_counts_against_the_timeout(self):
        # Nobody answers. Unbounded, the queued request would wait for the
        # first one (~1 s) and then its own full timeout (~1 s more).
        with mock.patch.object(runtime, "APPROVAL_TIMEOUT", 1.0):
            threads, box = self.two_requests()
            for thread in threads:
                thread.join(5)
        self.assertFalse(box["B"][0])
        self.assertLess(box["B_seconds"], 1.5)

class RuntimeSessionTest(RuntimeTestBase):
    def test_send_spawns_claude_and_streams(self):
        claude = self.fake_claude(FIXTURES / "text_turn.jsonl")
        with mock.patch.object(runtime, "find_claude", return_value=claude):
            session = runtime.ensure_session()
            self.assertIs(runtime.ensure_session(), session)
            runtime.tracker.record_ai_step()
            session.send("oi")
            # A new user turn resets the tracker.
            self.assertFalse(runtime.tracker.can_undo_last_turn())
            self.assertTrue(pump_until(lambda: session.status == "IDLE"))
        self.assertEqual(session.conversation.messages[1].text, "Oi!")
        self.assertTrue((self.tmp / "workspace").is_dir())
        (args,) = self.spawned_args()
        self.assertEqual(args[args.index("--plugin-dir") + 1], str(runtime.PLUGIN_DIR))
        self.assertEqual(args[args.index("--allowedTools") + 1], "mcp__blendercraft")
        self.assertNotIn("--model", args)
        self.assertNotIn("--resume", args)

    def test_mode_and_model_change_restart_with_resume(self):
        claude = self.fake_claude(FIXTURES / "text_turn.jsonl")
        with mock.patch.object(runtime, "find_claude", return_value=claude):
            session = runtime.ensure_session()
            session.send("oi")
            self.assertTrue(pump_until(lambda: session.status == "IDLE"))
            runtime.set_mode("ASK")
            runtime.set_model("sonnet")
            self.assertEqual(session.conversation.model, "sonnet")
            session.send("de novo")
            self.assertTrue(pump_until(lambda: session.status == "IDLE"))
        first, second = self.spawned_args()
        self.assertNotIn("--permission-prompt-tool", first)
        self.assertIn("--permission-prompt-tool", second)
        self.assertEqual(second[second.index("--model") + 1], "sonnet")
        self.assertEqual(
            second[second.index("--resume") + 1], session.conversation.claude_session_id
        )

    def test_claude_missing(self):
        with mock.patch.object(runtime, "find_claude", return_value=None):
            session = runtime.ensure_session()
            session.send("oi")
        self.assertEqual(session.status, "ERROR")
        self.assertEqual(runtime.claude_problem, "missing")

    def test_login_problem_detected(self):
        fixture = self.tmp / "login.jsonl"
        fixture.write_text(LOGIN_FIXTURE, encoding="utf-8")
        claude = self.fake_claude(fixture)
        with mock.patch.object(runtime, "find_claude", return_value=claude):
            session = runtime.ensure_session()
            session.send("oi")
            self.assertTrue(pump_until(lambda: session.status != "WORKING"))
        self.assertEqual(runtime.claude_problem, "login")

    def test_session_messages_follow_the_interface_language(self):
        from blendercraft_assistant import i18n
        from blendercraft_assistant.claude import session as session_module

        with mock.patch.object(i18n, "_locale", return_value="pt_BR"), \
                mock.patch.object(runtime, "find_claude", return_value=None):
            session = runtime.ensure_session()
            session.send("oi")
        self.assertEqual(set(session._notes), set(session_module.NOTE_KEYS))
        for key in session_module.NOTE_KEYS:
            self.assertEqual(session._notes[key], i18n.STRINGS["pt"][key])
        prefix = i18n.STRINGS["pt"]["send_failed"].split("{")[0]
        self.assertTrue(session.error.startswith(prefix), session.error)
        self.assertIn(i18n.STRINGS["pt"]["claude_missing"], session.error)

    def test_new_open_and_delete_conversation(self):
        claude = self.fake_claude(FIXTURES / "text_turn.jsonl")
        with mock.patch.object(runtime, "find_claude", return_value=claude):
            first = runtime.ensure_session()
            first.send("oi")
            self.assertTrue(pump_until(lambda: first.status == "IDLE"))
        first_id = first.conversation.id
        runtime.new_conversation()
        self.assertIsNone(runtime.session)
        self.assertIsNot(runtime.ensure_session(), first)
        runtime.open_conversation(first_id)
        self.assertEqual(runtime.session.conversation.id, first_id)
        self.assertEqual(runtime.session.conversation.messages[1].text, "Oi!")
        runtime.delete_conversation(first_id)
        self.assertIsNone(runtime.session)
        self.assertEqual(runtime.store.list(), [])


if __name__ == "__main__":
    unittest.main()
