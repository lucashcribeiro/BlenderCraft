"""Claude Code subprocess bridge: locating the CLI, building its arguments
and running it headless with stream-json on stdin/stdout.

This module must not import bpy: scripts/record_fixture.py imports it under
the system python3.
"""

import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

MCP_SERVER_NAME = "blendercraft"
TOOL_PREFIX = f"mcp__{MCP_SERVER_NAME}__"

# Variables that bind a process to the *parent* Claude Code session (session
# and host ids, messaging socket/token, entrypoint, SDK transport switches).
# A nested `claude` must not inherit them, which happens when Blender is
# launched from a Claude Code terminal; Blender started from the Dock does
# not have them. Everything else passes through on purpose: credentials,
# ANTHROPIC_*, CLAUDE_CONFIG_DIR and the other CLAUDE_CODE_* settings.
_ENV_NAMES = frozenset(
    {
        "CLAUDECODE",
        "CLAUDE_CODE_ENTRYPOINT",
        "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_CODE_CHILD_SESSION",
        "CLAUDE_CODE_SESSION_ATTENDED",
        "CLAUDE_CODE_HOST_SESSION_ID",
        "CLAUDE_CODE_MESSAGING_SOCKET",
        "CLAUDE_CODE_MESSAGING_TOKEN",
        "CLAUDE_CODE_EXECPATH",
        "CLAUDE_PID",
        "CLAUDE_EFFORT",
    }
)
_ENV_PREFIXES = ("CLAUDE_AGENT_SDK", "CLAUDE_PREVIEW")


def clean_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """Copy of env (default os.environ) without parent-session variables."""
    source = os.environ if env is None else env
    out = {}
    for key, value in source.items():
        if key in _ENV_NAMES or key.startswith(_ENV_PREFIXES):
            continue
        out[key] = value
    return out


_SYSTEM_PATHS = ("/opt/homebrew/bin/claude", "/usr/local/bin/claude")


def find_claude(pref_path: str = "") -> str | None:
    """Locate the claude executable: preference, PATH, then usual installs.

    The fallbacks matter because Blender launched from the Dock has a
    minimal PATH."""
    home = Path(os.path.expanduser("~"))
    candidates = [
        os.path.expanduser(pref_path) if pref_path else "",
        shutil.which("claude"),
        str(home / ".local" / "bin" / "claude"),
        *_SYSTEM_PATHS,
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


_CONFIG_PREFIX = "blendercraft-mcp-"


def _write_mcp_config(config: dict) -> str:
    """Write the MCP config (it holds the bearer token) to a 0600 temp file.

    argv is visible to every local user via `ps`, a file with owner-only
    permissions is not. ClaudeProcess deletes the file on exit/stop."""
    fd, path = tempfile.mkstemp(prefix=_CONFIG_PREFIX, suffix=".json")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(config, handle)
    except BaseException:
        try:
            os.unlink(path)
        finally:
            raise
    return path


def mcp_config_file(args: list[str]) -> str | None:
    """Path of the temp MCP config file that build_args put in args."""
    try:
        path = args[args.index("--mcp-config") + 1]
    except (ValueError, IndexError):
        return None
    return path if os.path.basename(path).startswith(_CONFIG_PREFIX) else None


def build_args(
    claude: str,
    *,
    mcp_url: str,
    token: str,
    mode: str,
    model: str,
    system_prompt: str,
    plugin_dir: str,
    read_tools: list[str],
    write_tools: list[str],
    resume: str | None,
) -> list[str]:
    """Arguments for the headless CLI.

    Side effect: the MCP config (with the token) is written to a 0600 temp
    file whose path goes after --mcp-config. Pass the result to
    ClaudeProcess, which removes the file on exit/stop; callers that do not
    start a process should delete it with mcp_config_file(args)."""
    if mode not in ("AUTO", "ASK", "READONLY"):
        raise ValueError(f"Unknown mode: {mode!r}")  # before any file is written
    mcp_config = {
        "mcpServers": {
            MCP_SERVER_NAME: {
                "type": "http",
                "url": mcp_url,
                "headers": {"Authorization": f"Bearer {token}"},
            }
        }
    }
    args = [
        claude,
        "-p",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--include-partial-messages",
        "--verbose",
        "--mcp-config", _write_mcp_config(mcp_config),
        "--strict-mcp-config",
        # Only Skill: the designer skills must be usable; every other built-in
        # tool (Bash, Read, ...) stays off.
        "--tools", "Skill",
        "--setting-sources", "",
        "--append-system-prompt", system_prompt,
        "--plugin-dir", plugin_dir,
    ]
    reads = ",".join(["Skill"] + [TOOL_PREFIX + name for name in read_tools])
    writes = ",".join(TOOL_PREFIX + name for name in write_tools)
    if mode == "AUTO":
        args += ["--allowedTools", f"Skill,mcp__{MCP_SERVER_NAME}"]
    elif mode == "ASK":
        args += [
            "--allowedTools", reads,
            "--permission-prompts", "host",
            "--permission-prompt-tool", f"{TOOL_PREFIX}approve_action",
        ]
    else:  # READONLY
        args += [
            "--allowedTools", reads,
            "--disallowedTools", writes,
            "--permission-prompts", "none",
        ]
    if model:
        args += ["--model", model]
    if resume:
        args += ["--resume", resume]
    return args


_STDERR_TAIL_BYTES = 4096


class ClaudeProcess:
    """Runs the CLI. on_line(line) is called for every stdout line and
    on_exit(returncode, stderr_tail) once at the end, both on the reader
    thread. stderr_tail is the last ~4 KB the CLI wrote to stderr."""

    def __init__(
        self,
        args: list[str],
        cwd: str,
        on_line: Callable[[str], None],
        on_exit: Callable[[int, str], None],
    ):
        self._args = args
        self._cwd = cwd
        self._on_line = on_line
        self._on_exit = on_exit
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr_tail = b""
        self._write_lock = threading.Lock()
        self._config_file = mcp_config_file(args)

    def start(self) -> None:
        if self._proc is not None:
            raise RuntimeError("process already started")
        self._proc = subprocess.Popen(
            self._args,
            cwd=self._cwd,
            env=clean_env(),
            start_new_session=True,  # own process group: stop() reaches children
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr,
            args=(self._proc,),
            name="blendercraft-claude-stderr",
            daemon=True,
        )
        self._stderr_thread.start()
        self._reader = threading.Thread(
            target=self._read_loop,
            args=(self._proc,),
            name="blendercraft-claude-reader",
            daemon=True,
        )
        self._reader.start()

    def _drain_stderr(self, proc: subprocess.Popen) -> None:
        # Always drain, or a chatty child would block on a full pipe.
        try:
            while True:
                chunk = proc.stderr.read1(4096)
                if not chunk:
                    break
                self._stderr_tail = (self._stderr_tail + chunk)[-_STDERR_TAIL_BYTES:]
        except (OSError, ValueError):
            pass

    def _read_loop(self, proc: subprocess.Popen) -> None:
        try:
            for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if line:
                    try:
                        self._on_line(line)
                    except Exception:  # a bad callback must not kill the reader
                        pass
        except (OSError, ValueError):  # pipe closed under us (stop())
            pass
        finally:
            code = proc.wait()
            self._remove_config_file()
            if self._stderr_thread is not None:
                self._stderr_thread.join(timeout=2)
            tail = self._stderr_tail.decode("utf-8", errors="replace")
            try:
                self._on_exit(code, tail)
            except Exception:
                pass

    def send_user(self, content: list[dict]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.poll() is not None:
            raise RuntimeError("claude process is not running")
        message = {"type": "user", "message": {"role": "user", "content": content}}
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            with self._write_lock:
                proc.stdin.write(data)
                proc.stdin.flush()
        except (OSError, ValueError) as exc:  # broken pipe, closed file
            raise RuntimeError("claude process is not running") from exc

    def close_stdin(self) -> None:
        """End of input: the CLI finishes the current turn and exits."""
        proc = self._proc
        if proc is not None and proc.stdin is not None:
            try:
                proc.stdin.close()
            except OSError:
                pass

    @staticmethod
    def _signal(proc: subprocess.Popen, sig: int) -> None:
        """Signal the whole process group (the CLI may have children),
        falling back to the pid alone."""
        try:
            os.killpg(proc.pid, sig)  # new session: pgid == pid
            return
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.send_signal(sig)
        except ProcessLookupError:
            pass

    def _remove_config_file(self) -> None:
        path, self._config_file = self._config_file, None
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass

    def stop(self) -> None:
        proc = self._proc
        if proc is None:
            self._remove_config_file()
            return
        if proc.poll() is None:
            self._signal(proc, signal.SIGTERM)
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._signal(proc, signal.SIGKILL)
                proc.wait()
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except OSError:
            pass
        if self._reader is not None and self._reader is not threading.current_thread():
            self._reader.join(timeout=3)
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass
        self._remove_config_file()

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None
