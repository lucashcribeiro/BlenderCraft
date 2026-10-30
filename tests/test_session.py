import json
import tempfile
import unittest
from pathlib import Path

from blendercraft_assistant.claude.session import AgentSession
from blendercraft_assistant.conversations import ConversationStore

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class FakeProcess:
    def __init__(self, resume):
        self.resume = resume
        self.sent = []
        self.stopped = False
        self.alive = True
        self.fail_send = False

    def send_user(self, content):
        if not self.alive or self.fail_send:
            raise RuntimeError("claude process is not running")
        self.sent.append(content)

    def stop(self):
        self.stopped = True
        self.alive = False


def tool_started_line(i, name="build"):
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": f"tu{i}",
                        "name": f"mcp__blendercraft__{name}",
                        "input": {"n": i},
                    }
                ]
            },
        }
    )


def tool_finished_line(i, is_error=False):
    return json.dumps(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": f"tu{i}",
                        "content": "out",
                        "is_error": is_error,
                    }
                ]
            },
        }
    )


def init_line(session_id="sid-1"):
    return json.dumps({"type": "system", "subtype": "init", "session_id": session_id})


def delta_line(text):
    return json.dumps(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": text},
            },
        }
    )


def result_line(text="", is_error=False):
    return json.dumps({"type": "result", "is_error": is_error, "result": text})


class SessionTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = ConversationStore(Path(self._tmp.name))
        self.procs = []
        self.conv = self.store.new("/x.blend", "sonnet")
        self.kinds = {}

    def spawn(self, resume):
        proc = FakeProcess(resume)
        self.procs.append(proc)
        return proc

    def make(self, **kwargs):
        kwargs.setdefault("notes", {"step_limit": "STEP LIMIT NOTE"})
        return AgentSession(
            self.conv,
            self.store,
            self.spawn,
            lambda name: self.kinds.get(name, "read"),
            **kwargs,
        )

    def feed_fixture(self, session, name):
        for line in (FIXTURES / f"{name}.jsonl").read_text(encoding="utf-8").splitlines():
            session.handle_line(line)

    def exit_process(self, session, proc, code, tail=""):
        proc.alive = False
        session.handle_exit(code, tail)


class SessionFlowTest(SessionTestBase):
    def test_text_turn_flow(self):
        session = self.make()
        self.assertEqual(session.status, "IDLE")
        session.send("oi")
        self.assertEqual(session.status, "WORKING")
        self.assertEqual(self.procs[0].sent, [[{"type": "text", "text": "oi"}]])
        self.assertEqual([m.role for m in self.conv.messages], ["user", "assistant"])
        self.feed_fixture(session, "text_turn")
        self.assertEqual(session.status, "IDLE")
        self.assertEqual(self.conv.messages[1].text, "Oi!")
        saved = self.store.load(self.conv.id)
        self.assertEqual(saved.claude_session_id, "dd3c42a4-b85d-4613-9d9b-fa9f2a2986da")
        self.assertEqual(saved.messages[1].text, "Oi!")
        self.assertEqual(saved.title, "oi")

    def test_attachments_follow_text(self):
        session = self.make()
        image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AA"}}
        session.send("olha", [image])
        self.assertEqual(self.procs[0].sent[0], [{"type": "text", "text": "olha"}, image])

    def test_process_reused_between_turns(self):
        session = self.make()
        session.send("one")
        self.feed_fixture(session, "text_turn")
        session.send("two")
        self.assertEqual(len(self.procs), 1)
        self.assertEqual(len(self.procs[0].sent), 2)

    def test_first_spawn_uses_conversation_session_id(self):
        self.conv.claude_session_id = "abc"
        session = self.make()
        session.send("oi")
        self.assertEqual(self.procs[0].resume, "abc")

    def test_session_started_is_idempotent(self):
        session = self.make()
        session.send("oi")
        session.handle_line(init_line("a"))
        session.handle_line(init_line("b"))
        self.assertEqual(self.conv.claude_session_id, "b")
        self.assertEqual(session.status, "WORKING")

    def test_tool_events_update_tool_call(self):
        self.kinds["echo"] = "read"
        session = self.make()
        session.send("echo")
        lines = (FIXTURES / "tool_turn.jsonl").read_text(encoding="utf-8").splitlines()
        for line in lines:
            session.handle_line(line)
        tools = self.conv.messages[1].tools
        self.assertEqual(len(tools), 1)
        self.assertEqual((tools[0].name, tools[0].input), ("echo", {"text": "oi"}))
        self.assertEqual((tools[0].output, tools[0].status), ("echo: oi", "ok"))
        self.assertEqual(self.conv.messages[1].text, "ok")
        self.assertEqual(session.changes, 0)

    def test_tool_error_status(self):
        session = self.make()
        session.send("x")
        session.handle_line(tool_started_line(1))
        session.handle_line(tool_finished_line(1, is_error=True))
        self.assertEqual(self.conv.messages[1].tools[0].status, "error")

    def test_duplicate_tool_started_not_duplicated(self):
        session = self.make()
        session.send("x")
        session.handle_line(tool_started_line(1))
        session.handle_line(tool_started_line(1))
        self.assertEqual(len(self.conv.messages[1].tools), 1)

    def test_error_turn_shows_result_when_no_text(self):
        session = self.make()
        session.send("x")
        session.handle_line(init_line())
        session.handle_line(result_line("API Error: boom", is_error=True))
        self.assertEqual(session.status, "IDLE")
        self.assertEqual(self.conv.messages[1].text, "API Error: boom")

    def test_error_turn_keeps_existing_text(self):
        session = self.make()
        session.send("x")
        session.handle_line(init_line())
        session.handle_line(delta_line("parcial"))
        session.handle_line(result_line("API Error: boom", is_error=True))
        self.assertEqual(self.conv.messages[1].text, "parcial")


class SessionQueueTest(SessionTestBase):
    def test_queue_while_working(self):
        session = self.make()
        session.send("um")
        session.send("dois")
        self.assertEqual(len(self.procs[0].sent), 1)
        self.assertEqual(session.queue, [("dois", [])])
        self.assertEqual(len(self.conv.messages), 2)
        self.feed_fixture(session, "text_turn")
        self.assertEqual(len(self.procs[0].sent), 2)
        self.assertEqual(self.procs[0].sent[1], [{"type": "text", "text": "dois"}])
        self.assertEqual(session.queue, [])
        self.assertEqual(session.status, "WORKING")
        self.assertEqual([m.role for m in self.conv.messages], ["user", "assistant", "user", "assistant"])

    def test_queue_keeps_attachments(self):
        session = self.make()
        image = {"type": "image"}
        session.send("um")
        session.send("dois", [image])
        self.assertEqual(session.queue, [("dois", [image])])
        self.feed_fixture(session, "text_turn")
        self.assertEqual(self.procs[0].sent[1], [{"type": "text", "text": "dois"}, image])

    def test_changes_count(self):
        self.kinds["build"] = "scene"
        self.kinds["look"] = "read"
        session = self.make()
        session.send("x")
        session.handle_line(tool_started_line(1, "build"))
        session.handle_line(tool_finished_line(1))
        self.assertEqual(session.changes, 1)
        session.handle_line(tool_started_line(2, "build"))
        session.handle_line(tool_finished_line(2, is_error=True))
        session.handle_line(tool_started_line(3, "look"))
        session.handle_line(tool_finished_line(3))
        self.assertEqual(session.changes, 1)

    def test_send_now_stops_and_sends(self):
        session = self.make()
        session.send("um")
        session.handle_line(tool_started_line(1))
        session.send("fila")
        session.send_now("agora")
        self.assertTrue(self.procs[0].stopped)
        self.assertEqual(self.conv.messages[1].tools[0].status, "interrupted")
        self.assertEqual(len(self.procs), 2)
        self.assertEqual(self.procs[1].sent, [[{"type": "text", "text": "agora"}]])
        self.assertEqual(session.queue, [])
        self.assertEqual(session.status, "WORKING")


class SessionStopTest(SessionTestBase):
    def test_stop_interrupts(self):
        session = self.make()
        session.send("x")
        session.handle_line(init_line())
        session.handle_line(tool_started_line(1))
        session.handle_line(tool_started_line(2))
        session.handle_line(tool_finished_line(2))
        session.stop()
        self.assertTrue(self.procs[0].stopped)
        self.assertEqual(session.status, "IDLE")
        tools = self.store.load(self.conv.id).messages[1].tools
        self.assertEqual([t.status for t in tools], ["interrupted", "ok"])

    def test_stop_then_send_resumes(self):
        session = self.make()
        session.send("x")
        session.handle_line(init_line("sid-9"))
        session.stop()
        session.send("y")
        self.assertEqual(len(self.procs), 2)
        self.assertEqual(self.procs[1].resume, "sid-9")

    def test_events_after_stop_are_ignored(self):
        session = self.make()
        session.send("x")
        session.stop()
        session.handle_line(delta_line("late"))
        session.handle_line(result_line("late"))
        self.assertEqual(self.conv.messages[1].text, "")
        self.assertEqual(session.status, "IDLE")

    def test_exit_after_stop_is_not_an_error(self):
        session = self.make()
        session.send("x")
        session.stop()
        session.handle_exit(-15, "")
        self.assertEqual(session.status, "IDLE")
        self.assertEqual(session.error, "")

    def test_stale_exit_with_new_process_alive_is_ignored(self):
        session = self.make()
        session.send("x")
        session.stop()
        session.send("y")
        session.handle_exit(-15, "")  # late exit of the first process
        self.assertEqual(session.status, "WORKING")

    def test_close_stops_process(self):
        session = self.make()
        session.send("x")
        session.handle_line(init_line())
        session.close()
        self.assertTrue(self.procs[0].stopped)
        self.assertEqual(session.status, "IDLE")
        self.assertEqual(self.store.load(self.conv.id).claude_session_id, "sid-1")

    def test_close_without_messages_writes_nothing(self):
        session = self.make()
        session.close()
        self.assertEqual(self.store.list(), [])

    def test_step_limit(self):
        session = self.make(max_tool_calls=40)
        session.send("x")
        for i in range(1, 41):
            session.handle_line(tool_started_line(i))
        self.assertEqual(session.status, "WORKING")
        self.assertFalse(self.procs[0].stopped)
        session.handle_line(tool_started_line(41))
        self.assertTrue(self.procs[0].stopped)
        self.assertEqual(session.status, "IDLE")
        self.assertIn("STEP LIMIT NOTE", self.conv.messages[1].text)
        self.assertEqual(len(self.conv.messages[1].tools), 40 + 1)
        self.assertEqual(self.conv.messages[1].tools[-1].status, "interrupted")

    def test_step_limit_default_note_and_counter_resets(self):
        session = self.make(max_tool_calls=2, notes=None)
        session.send("x")
        for i in range(1, 4):
            session.handle_line(tool_started_line(i))
        self.assertTrue(self.conv.messages[1].text)
        session.send("again")
        for i in range(10, 12):
            session.handle_line(tool_started_line(i))
        self.assertEqual(session.status, "WORKING")

    def test_step_limit_note_follows_existing_text(self):
        session = self.make(max_tool_calls=1)
        session.send("x")
        session.handle_line(delta_line("antes"))
        session.handle_line(tool_started_line(1))
        session.handle_line(tool_started_line(2))
        self.assertTrue(self.conv.messages[1].text.startswith("antes"))
        self.assertTrue(self.conv.messages[1].text.endswith("STEP LIMIT NOTE"))


class SessionFailureTest(SessionTestBase):
    def _history(self, n):
        from blendercraft_assistant.conversations import Message

        for i in range(n):
            self.conv.messages.append(Message("user" if i % 2 == 0 else "assistant", f"m{i}"))

    def test_resume_failure_fallback(self):
        self.conv.claude_session_id = "abc"
        self._history(2)
        session = self.make()
        session.send("nova pergunta")
        self.assertEqual(self.procs[0].resume, "abc")
        self.exit_process(session, self.procs[0], 1, "no conversation found")
        self.assertEqual(len(self.procs), 2)
        self.assertIsNone(self.procs[1].resume)
        self.assertIsNone(self.conv.claude_session_id)
        text = self.procs[1].sent[0][0]["text"]
        self.assertTrue(text.startswith("Resumo da conversa anterior:"))
        self.assertIn("m0", text)
        self.assertIn("m1", text)
        self.assertTrue(text.endswith("nova pergunta"))
        self.assertEqual(session.status, "WORKING")
        self.assertEqual(len(self.conv.messages), 4)  # no duplicated messages

    def test_resume_failure_fallback_on_error_result(self):
        self.conv.claude_session_id = "abc"
        session = self.make()
        session.send("oi")
        session.handle_line(result_line("No conversation found", is_error=True))
        self.assertEqual(len(self.procs), 2)
        self.assertTrue(self.procs[0].stopped)
        self.assertIsNone(self.procs[1].resume)
        self.assertEqual(self.conv.messages[1].text, "")
        self.assertEqual(session.status, "WORKING")

    def test_fallback_summary_has_last_10_messages_only(self):
        self.conv.claude_session_id = "abc"
        self._history(14)
        session = self.make()
        session.send("q")
        self.exit_process(session, self.procs[0], 1)
        text = self.procs[1].sent[0][0]["text"]
        expected = ["Resumo da conversa anterior:"] + [
            f"{'user' if i % 2 == 0 else 'assistant'}: m{i}" for i in range(4, 14)
        ]
        self.assertEqual(text, "\n".join(expected) + "\n\nq")

    def test_fallback_with_repeated_user_text(self):
        from blendercraft_assistant.conversations import Message

        self.conv.claude_session_id = "abc"
        self.conv.messages += [Message("user", "sim"), Message("assistant", "certo")]
        session = self.make()
        session.send("sim")  # equal (==) to the earlier user message
        self.exit_process(session, self.procs[0], 1)
        text = self.procs[1].sent[0][0]["text"]
        self.assertEqual(
            text,
            "Resumo da conversa anterior:\nuser: sim\nassistant: certo\n\nsim",
        )

    def test_no_fallback_after_session_started(self):
        self.conv.claude_session_id = "abc"
        session = self.make()
        session.send("oi")
        session.handle_line(init_line("abc"))
        self.exit_process(session, self.procs[0], 1, "boom")
        self.assertEqual(len(self.procs), 1)
        self.assertEqual(session.status, "ERROR")

    def test_fallback_happens_only_once(self):
        self.conv.claude_session_id = "abc"
        session = self.make()
        session.send("oi")
        self.exit_process(session, self.procs[0], 1)
        self.exit_process(session, self.procs[1], 1, "again")
        self.assertEqual(len(self.procs), 2)
        self.assertEqual(session.status, "ERROR")

    def test_unexpected_exit_mid_turn_is_error(self):
        session = self.make()
        session.send("x")
        session.handle_line(tool_started_line(1))
        self.exit_process(session, self.procs[0], 3, "segfault")
        self.assertEqual(session.status, "ERROR")
        self.assertIn("3", session.error)
        self.assertIn("segfault", session.error)
        self.assertEqual(self.conv.messages[1].tools[0].status, "interrupted")
        self.assertEqual(self.store.load(self.conv.id).messages[0].text, "x")

    def test_exit_while_idle_just_drops_process(self):
        session = self.make()
        session.send("x")
        self.feed_fixture(session, "text_turn")
        self.exit_process(session, self.procs[0], 0)
        self.assertEqual(session.status, "IDLE")
        session.send("y")
        self.assertEqual(len(self.procs), 2)
        self.assertEqual(self.procs[1].resume, "dd3c42a4-b85d-4613-9d9b-fa9f2a2986da")

    def test_send_after_error_recovers(self):
        session = self.make()
        session.send("x")
        self.exit_process(session, self.procs[0], 3, "boom")
        session.send("y")
        self.assertEqual(session.status, "WORKING")
        self.assertEqual(session.error, "")

    def test_send_respawns_when_process_died(self):
        session = self.make()
        session.send("x")
        self.feed_fixture(session, "text_turn")
        self.procs[0].alive = False  # died quietly; exit not delivered yet
        self.procs[0].fail_send = True
        session.send("y")
        self.assertEqual(len(self.procs), 2)
        self.assertEqual(self.procs[1].sent, [[{"type": "text", "text": "y"}]])
        self.assertEqual(session.status, "WORKING")

    def test_send_retries_once_when_send_user_raises(self):
        session = self.make()
        session.send("x")
        self.feed_fixture(session, "text_turn")
        self.procs[0].fail_send = True  # still "alive" but the pipe is broken
        session.send("y")
        self.assertEqual(len(self.procs), 2)
        self.assertEqual(self.procs[1].resume, "dd3c42a4-b85d-4613-9d9b-fa9f2a2986da")
        self.assertEqual(session.status, "WORKING")

    def test_send_fails_twice_is_error(self):
        spawned = []

        def spawn(resume):
            proc = FakeProcess(resume)
            proc.fail_send = True
            spawned.append(proc)
            return proc

        session = AgentSession(self.conv, self.store, spawn, lambda n: "read")
        session.send("x")
        self.assertEqual(len(spawned), 2)
        self.assertEqual(session.status, "ERROR")
        self.assertTrue(session.error)

    def test_spawn_exception_is_error(self):
        def spawn(resume):
            raise OSError("claude not found")

        session = AgentSession(self.conv, self.store, spawn, lambda n: "read")
        session.send("x")
        self.assertEqual(session.status, "ERROR")
        self.assertIn("claude not found", session.error)


if __name__ == "__main__":
    unittest.main()
