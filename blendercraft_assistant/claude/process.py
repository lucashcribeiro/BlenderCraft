"""Claude Code subprocess bridge: locating the CLI, building its arguments
and running it headless with stream-json on stdin/stdout.

This module must not import bpy: scripts/record_fixture.py imports it under
the system python3.
"""

import json
import os
import shutil
import subprocess
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
        "--mcp-config", json.dumps(mcp_config),
        "--strict-mcp-config",
        "--tools", "",
        "--setting-sources", "",
        "--append-system-prompt", system_prompt,
        "--plugin-dir", plugin_dir,
    ]
    reads = ",".join(TOOL_PREFIX + name for name in read_tools)
    writes = ",".join(TOOL_PREFIX + name for name in write_tools)
    if mode == "AUTO":
        args += ["--allowedTools", f"mcp__{MCP_SERVER_NAME}"]
    elif mode == "ASK":
        args += [
            "--allowedTools", reads,
            "--permission-prompts", "host",
            "--permission-prompt-tool", f"{TOOL_PREFIX}approve_action",
        ]
    elif mode == "READONLY":
        args += [
            "--allowedTools", reads,
            "--disallowedTools", writes,
            "--permission-prompts", "none",
        ]
    else:
        raise ValueError(f"Unknown mode: {mode!r}")
    if model:
        args += ["--model", model]
    if resume:
        args += ["--resume", resume]
    return args


class ClaudeProcess:
    def __init__(
        self,
        args: list[str],
        cwd: str,
        on_line: Callable[[str], None],
        on_exit: Callable[[int], None],
    ):
        self._args = args
        self._cwd = cwd
        self._on_line = on_line
        self._on_exit = on_exit
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._write_lock = threading.Lock()

    def start(self) -> None:
        if self._proc is not None:
            raise RuntimeError("process already started")
        self._proc = subprocess.Popen(
            self._args,
            cwd=self._cwd,
            env=clean_env(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self._reader = threading.Thread(
            target=self._read_loop,
            args=(self._proc,),
            name="blendercraft-claude-reader",
            daemon=True,
        )
        self._reader.start()

    def _read_loop(self, proc: subprocess.Popen) -> None:
        try:
            for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if line:
                    try:
                        self._on_line(line)
                    except Exception:  # a bad callback must not kill the reader
                        pass
        finally:
            code = proc.wait()
            try:
                self._on_exit(code)
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

    def stop(self) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except OSError:
            pass
        if self._reader is not None and self._reader is not threading.current_thread():
            self._reader.join(timeout=3)
        try:
            if proc.stdout is not None:
                proc.stdout.close()
        except OSError:
            pass

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None
