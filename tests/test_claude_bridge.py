import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from blendercraft_assistant.claude import process as process_module
from blendercraft_assistant.claude.process import (
    ClaudeProcess,
    build_args,
    clean_env,
    find_claude,
)
from blendercraft_assistant.claude.stream import (
    SessionStarted,
    TextDelta,
    ToolFinished,
    ToolStarted,
    TurnFinished,
    parse_line,
)

TESTS_DIR = Path(__file__).resolve().parent
FIXTURES = TESTS_DIR / "fixtures"
FAKE_CLAUDE = TESTS_DIR / "fake_claude.py"


def _events(name):
    out = []
    for line in (FIXTURES / f"{name}.jsonl").read_text(encoding="utf-8").splitlines():
        out.extend(parse_line(line))
    return out


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class StreamTest(unittest.TestCase):
    def test_text_turn(self):
        events = _events("text_turn")
        text = "".join(e.text for e in events if isinstance(e, TextDelta))
        self.assertTrue(text)
        started = [e for e in events if isinstance(e, SessionStarted)]
        self.assertEqual(len(started), 1)
        self.assertTrue(started[0].session_id)
        finished = [e for e in events if isinstance(e, TurnFinished)]
        self.assertEqual(len(finished), 1)
        self.assertFalse(finished[0].is_error)
        # Deltas appear once and add up to the final text: the complete
        # assistant message repeats that text and must not be parsed again.
        self.assertEqual(text, finished[0].result)
        self.assertIsInstance(events[0], SessionStarted)
        self.assertIsInstance(events[-1], TurnFinished)
        self.assertFalse(any(isinstance(e, (ToolStarted, ToolFinished)) for e in events))

    def test_tool_turn(self):
        events = _events("tool_turn")
        starts = [e for e in events if isinstance(e, ToolStarted)]
        ends = [e for e in events if isinstance(e, ToolFinished)]
        self.assertEqual(len(starts), 1)
        self.assertEqual(len(ends), 1)
        self.assertEqual(starts[0].name, "echo")  # prefix removed
        self.assertEqual(starts[0].input, {"text": "oi"})
        self.assertEqual(ends[0].tool_use_id, starts[0].tool_use_id)
        self.assertEqual(ends[0].output, "echo: oi")
        self.assertFalse(ends[0].is_error)
        self.assertLess(events.index(starts[0]), events.index(ends[0]))
        text = "".join(e.text for e in events if isinstance(e, TextDelta))
        self.assertTrue(text)
        self.assertEqual(text, events[-1].result)
        self.assertFalse(events[-1].is_error)

    def test_invalid_line(self):
        self.assertEqual(parse_line("lixo"), [])
        self.assertEqual(parse_line(""), [])
        self.assertEqual(parse_line("[1, 2]"), [])
        self.assertEqual(parse_line('{"type": "never_heard_of_it"}'), [])
        self.assertEqual(parse_line('{"type": "system", "subtype": "status"}'), [])
        self.assertEqual(parse_line('{"type": "stream_event", "event": 3}'), [])

    def test_error_tool_result_and_block_shapes(self):
        line = json.dumps(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "t1",
                            "is_error": True,
                            "content": "boom",
                        },
                        {
                            "type": "tool_result",
                            "tool_use_id": "t2",
                            "content": [
                                {"type": "text", "text": "a"},
                                {"type": "image", "source": {}},
                                {"type": "text", "text": "b"},
                            ],
                        },
                    ],
                },
            }
        )
        self.assertEqual(
            parse_line(line),
            [ToolFinished("t1", "boom", True), ToolFinished("t2", "a\nb", False)],
        )

    def test_user_message_with_plain_string_content(self):
        line = json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}})
        self.assertEqual(parse_line(line), [])

    def test_failed_turn(self):
        line = json.dumps(
            {"type": "result", "subtype": "error_during_execution", "is_error": True}
        )
        self.assertEqual(parse_line(line), [TurnFinished(is_error=True, result="")])

    def test_unprefixed_and_foreign_tool_names(self):
        def start(name):
            line = json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "text", "text": "ignored"},
                            {"type": "tool_use", "id": "x", "name": name, "input": {}},
                        ]
                    },
                }
            )
            return parse_line(line)

        self.assertEqual(start("mcp__blendercraft__mesh_stats")[0].name, "mesh_stats")
        self.assertEqual(start("mcp__other__thing")[0].name, "mcp__other__thing")
        self.assertEqual(len(start("echo")), 1)


class BuildArgsTest(unittest.TestCase):
    def _args(self, mode, **over):
        kw = dict(
            mcp_url="http://127.0.0.1:5555/mcp",
            token="tok",
            mode=mode,
            model="",
            system_prompt="SYS",
            plugin_dir="/plug",
            read_tools=["a", "b"],
            write_tools=["w1", "w2"],
            resume=None,
        )
        kw.update(over)
        return build_args("/bin/claude", **kw)

    def _value(self, args, flag):
        return args[args.index(flag) + 1]

    def test_base(self):
        args = self._args("AUTO")
        self.assertEqual(args[0], "/bin/claude")
        self.assertEqual(args[1], "-p")
        for flag, value in [
            ("--input-format", "stream-json"),
            ("--output-format", "stream-json"),
            ("--tools", ""),
            ("--append-system-prompt", "SYS"),
            ("--plugin-dir", "/plug"),
        ]:
            self.assertEqual(self._value(args, flag), value)
        for flag in ("--include-partial-messages", "--verbose", "--strict-mcp-config"):
            self.assertIn(flag, args)
        config = json.loads(self._value(args, "--mcp-config"))
        self.assertEqual(
            config,
            {
                "mcpServers": {
                    "blendercraft": {
                        "type": "http",
                        "url": "http://127.0.0.1:5555/mcp",
                        "headers": {"Authorization": "Bearer tok"},
                    }
                }
            },
        )
        self.assertNotIn("--model", args)
        self.assertNotIn("--resume", args)

    def test_build_args_modes(self):
        auto = self._args("AUTO")
        self.assertEqual(self._value(auto, "--allowedTools"), "mcp__blendercraft")
        for flag in ("--permission-prompts", "--permission-prompt-tool", "--disallowedTools"):
            self.assertNotIn(flag, auto)

        ask = self._args("ASK")
        self.assertEqual(
            self._value(ask, "--allowedTools"),
            "mcp__blendercraft__a,mcp__blendercraft__b",
        )
        self.assertEqual(self._value(ask, "--permission-prompts"), "host")
        self.assertEqual(
            self._value(ask, "--permission-prompt-tool"),
            "mcp__blendercraft__approve_action",
        )
        self.assertNotIn("--disallowedTools", ask)

        ro = self._args("READONLY")
        self.assertEqual(
            self._value(ro, "--allowedTools"),
            "mcp__blendercraft__a,mcp__blendercraft__b",
        )
        self.assertEqual(
            self._value(ro, "--disallowedTools"),
            "mcp__blendercraft__w1,mcp__blendercraft__w2",
        )
        self.assertEqual(self._value(ro, "--permission-prompts"), "none")
        self.assertNotIn("--permission-prompt-tool", ro)

    def test_unknown_mode(self):
        with self.assertRaises(ValueError):
            self._args("YOLO")

    def test_model_and_resume(self):
        args = self._args("AUTO", model="haiku", resume="sess-1")
        self.assertEqual(self._value(args, "--model"), "haiku")
        self.assertEqual(self._value(args, "--resume"), "sess-1")

    def test_tools_flag_value_is_a_real_empty_argument(self):
        args = self._args("AUTO")
        self.assertEqual(args[args.index("--tools") + 1], "")


class FindClaudeTest(unittest.TestCase):
    def test_find_claude_without_path(self):
        with tempfile.TemporaryDirectory() as home:
            expected = _executable(Path(home) / ".local" / "bin" / "claude")
            env = {"PATH": "", "HOME": home}
            with mock.patch.dict(os.environ, env), mock.patch.object(
                process_module, "_SYSTEM_PATHS", ()
            ):
                self.assertEqual(find_claude(), str(expected))

    def test_find_claude_none(self):
        with tempfile.TemporaryDirectory() as home:
            env = {"PATH": "", "HOME": home}
            with mock.patch.dict(os.environ, env), mock.patch.object(
                process_module, "_SYSTEM_PATHS", ()
            ):
                self.assertIsNone(find_claude())

    def test_preference_wins_and_must_be_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            _executable(home / ".local" / "bin" / "claude")
            pref = _executable(Path(tmp) / "mine" / "claude")
            plain = Path(tmp) / "plain"
            plain.write_text("x", encoding="utf-8")
            plain.chmod(0o644)
            env = {"PATH": "", "HOME": str(home)}
            with mock.patch.dict(os.environ, env), mock.patch.object(
                process_module, "_SYSTEM_PATHS", ()
            ):
                self.assertEqual(find_claude(str(pref)), str(pref))
                self.assertEqual(
                    find_claude(str(plain)), str(home / ".local" / "bin" / "claude")
                )
                self.assertEqual(
                    find_claude(str(Path(tmp) / "missing")),
                    str(home / ".local" / "bin" / "claude"),
                )

    def test_path_lookup_comes_before_fallbacks(self):
        with tempfile.TemporaryDirectory() as tmp:
            on_path = _executable(Path(tmp) / "bin" / "claude")
            home = Path(tmp) / "home"
            _executable(home / ".local" / "bin" / "claude")
            env = {"PATH": str(on_path.parent), "HOME": str(home)}
            with mock.patch.dict(os.environ, env):
                self.assertEqual(find_claude(), str(on_path))


class CleanEnvTest(unittest.TestCase):
    def test_strips_parent_session_variables_only(self):
        env = {
            "CLAUDECODE": "1",
            "CLAUDE_CODE_ENTRYPOINT": "cli",
            "CLAUDE_CODE_SESSION_ID": "abc",
            "CLAUDE_AGENT_SDK_VERSION": "1",
            "CLAUDE_CODE_OAUTH_TOKEN": "keep-me",
            "ANTHROPIC_API_KEY": "keep-me-too",
            "PATH": "/usr/bin",
            "HOME": "/h",
        }
        out = clean_env(env)
        self.assertEqual(
            out,
            {
                "CLAUDE_CODE_OAUTH_TOKEN": "keep-me",
                "ANTHROPIC_API_KEY": "keep-me-too",
                "PATH": "/usr/bin",
                "HOME": "/h",
            },
        )
        self.assertIn("CLAUDECODE", env)  # input untouched


class ProcessTest(unittest.TestCase):
    def _run_fake(self, fixture):
        lines, exits = [], []
        done = threading.Event()
        proc = ClaudeProcess(
            [sys.executable, str(FAKE_CLAUDE), str(FIXTURES / f"{fixture}.jsonl")],
            cwd=str(TESTS_DIR),
            on_line=lines.append,
            on_exit=lambda code: (exits.append(code), done.set()),
        )
        return proc, lines, exits, done

    def _wait_lines(self, lines, count, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(lines) >= count:
                return True
            time.sleep(0.05)
        return False

    def test_process_with_fake(self):
        proc, lines, exits, done = self._run_fake("tool_turn")
        expected = (FIXTURES / "tool_turn.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertFalse(proc.alive)
        proc.start()
        self.assertTrue(proc.alive)
        proc.send_user([{"type": "text", "text": "ola"}])
        self.assertTrue(self._wait_lines(lines, len(expected)))
        self.assertEqual(lines, expected)
        events = [e for line in lines for e in parse_line(line)]
        self.assertIsInstance(events[-1], TurnFinished)
        # A second message gets a second replay: the process stays up between turns.
        proc.send_user([{"type": "text", "text": "de novo"}])
        self.assertTrue(self._wait_lines(lines, 2 * len(expected)))
        self.assertEqual(lines[len(expected):], expected)
        self.assertTrue(proc.alive)
        proc.stop()
        self.assertFalse(proc.alive)
        self.assertTrue(done.wait(5))
        self.assertEqual(len(exits), 1)

    def test_closing_stdin_ends_the_process_naturally(self):
        proc, lines, exits, done = self._run_fake("text_turn")
        proc.start()
        proc.send_user([{"type": "text", "text": "x"}])
        proc.close_stdin()
        self.assertTrue(done.wait(10))
        self.assertEqual(exits, [0])
        proc.stop()
        self.assertFalse(proc.alive)

    def test_send_user_wire_format(self):
        script = (
            "import sys; sys.stdout.write(sys.stdin.readline()); sys.stdout.flush()"
        )
        lines, done = [], threading.Event()
        proc = ClaudeProcess(
            [sys.executable, "-c", script],
            cwd=str(TESTS_DIR),
            on_line=lines.append,
            on_exit=lambda code: done.set(),
        )
        proc.start()
        content = [{"type": "text", "text": "café → ok"}]
        proc.send_user(content)
        self.assertTrue(done.wait(10))
        self.assertEqual(
            json.loads(lines[0]),
            {"type": "user", "message": {"role": "user", "content": content}},
        )
        proc.stop()

    def test_send_user_after_exit_raises(self):
        proc, lines, exits, done = self._run_fake("text_turn")
        with self.assertRaises(RuntimeError):
            proc.send_user([])  # not started
        proc.start()
        proc.stop()
        with self.assertRaises(RuntimeError):
            proc.send_user([{"type": "text", "text": "x"}])

    def test_stop_kills_a_process_that_ignores_sigterm(self):
        script = (
            "import signal, sys, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "print('ready', flush=True)\n"
            "time.sleep(60)\n"
        )
        lines, done = [], threading.Event()
        proc = ClaudeProcess(
            [sys.executable, "-c", script],
            cwd=str(TESTS_DIR),
            on_line=lines.append,
            on_exit=lambda code: done.set(),
        )
        proc.start()
        self.assertTrue(self._wait_lines(lines, 1))
        proc.stop()
        self.assertFalse(proc.alive)
        self.assertTrue(done.wait(5))

    def test_child_does_not_see_parent_session_variables(self):
        script = "import os; print(os.environ.get('CLAUDECODE', 'unset'), flush=True)"
        lines, done = [], threading.Event()
        proc = ClaudeProcess(
            [sys.executable, "-c", script],
            cwd=str(TESTS_DIR),
            on_line=lines.append,
            on_exit=lambda code: done.set(),
        )
        with mock.patch.dict(os.environ, {"CLAUDECODE": "1"}):
            proc.start()
        self.assertTrue(done.wait(10))
        self.assertEqual(lines, ["unset"])
        proc.stop()


if __name__ == "__main__":
    unittest.main()
