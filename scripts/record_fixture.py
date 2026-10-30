"""Record a real Claude Code turn as a stream-json fixture.

Run from the repository root with the system python3 (no Blender, no bpy):

    python3 scripts/record_fixture.py text_turn "Responda apenas: oi"
    python3 scripts/record_fixture.py tool_turn \
        "Chame a ferramenta echo com text='oi' e depois responda ok"

Starts a local MCP server exposing a fake read-only `echo` tool, runs the
installed `claude` with the same arguments the add-on uses (model haiku),
sends one user message, waits for the `result` event, closes stdin and
writes stdout to tests/fixtures/<name>.jsonl. Each run spends one tiny
Haiku call.

ClaudeProcess strips parent-session variables (CLAUDECODE, CLAUDE_CODE_*)
from the environment; this script reuses the same helper, so it also works
when run from inside a Claude Code session.

Before the file is written it is sanitized: user hook events
(system/hook_started, system/hook_response) and rate_limit_event (account
usage numbers) are dropped, system/init is cut
down to the fields the parser cares about (the full event lists the user's
personal skills, plugins and paths), and machine-specific values (home path,
temp dirs, e-mail, the throwaway token, MCP url) become placeholders.
"""

import json
import os
import queue
import re
import secrets
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from blendercraft_assistant.claude.process import (  # noqa: E402
    ClaudeProcess,
    build_args,
    find_claude,
)
from blendercraft_assistant.mcp.protocol import McpHandler  # noqa: E402
from blendercraft_assistant.mcp.server import McpServer  # noqa: E402
from blendercraft_assistant.tools.base import Tool, ToolResult  # noqa: E402

TIMEOUT_S = 120

ECHO = Tool(
    name="echo",
    description="Echoes back the given text. Use it when asked to call echo.",
    input_schema={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
    handler=lambda args: ToolResult(text=f"echo: {args.get('text', '')}"),
    kind="read",
)


_DROPPED_SYSTEM_SUBTYPES = {"hook_started", "hook_response"}
_DROPPED_TYPES = {"rate_limit_event"}  # carries account usage numbers
_INIT_KEEP = (
    "type", "subtype", "cwd", "session_id", "tools", "mcp_servers", "model",
    "permissionMode", "claude_code_version", "uuid",
)


def sanitize(line: str) -> str | None:
    """Drop or shrink events that carry personal or irrelevant data."""
    try:
        event = json.loads(line)
    except ValueError:
        return line
    if event.get("type") in _DROPPED_TYPES:
        return None
    if event.get("type") == "system":
        subtype = event.get("subtype")
        if subtype in _DROPPED_SYSTEM_SUBTYPES:
            return None
        if subtype == "init":
            event = {k: event[k] for k in _INIT_KEEP if k in event}
            return json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    return line


def redact(line: str, replacements: list[tuple[str, str]]) -> str:
    for old, new in replacements:
        if old:
            line = line.replace(old, new)
    return line


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    name, message = sys.argv[1], sys.argv[2]
    claude = find_claude()
    if claude is None:
        print("claude CLI not found", file=sys.stderr)
        return 1

    token = secrets.token_urlsafe(32)
    handler = McpHandler(lambda: [ECHO], lambda n, a: ECHO.handler(a))
    server = McpServer(handler, token)
    server.start()
    lines: queue.Queue = queue.Queue()
    exited: queue.Queue = queue.Queue()
    try:
        with tempfile.TemporaryDirectory() as plugin_dir, tempfile.TemporaryDirectory() as cwd:
            args = build_args(
                claude,
                mcp_url=server.url,
                token=token,
                mode="AUTO",
                model="haiku",
                system_prompt="You are a test assistant. Keep answers extremely short.",
                plugin_dir=plugin_dir,
                read_tools=["echo"],
                write_tools=[],
                resume=None,
            )
            proc = ClaudeProcess(args, cwd, lines.put, exited.put)
            proc.start()
            proc.send_user([{"type": "text", "text": message}])
            raw = []
            try:
                while True:
                    line = lines.get(timeout=TIMEOUT_S)
                    raw.append(line)
                    try:
                        if json.loads(line).get("type") == "result":
                            break
                    except ValueError:
                        pass
            except queue.Empty:
                print("timed out waiting for result", file=sys.stderr)
                proc.stop()
                return 1
            proc.close_stdin()  # let the CLI exit by itself
            try:
                exited.get(timeout=30)
            except queue.Empty:
                pass
            proc.stop()
            replacements = [
                (str(Path(cwd).resolve()), "/tmp/blendercraft-cwd"),
                (cwd, "/tmp/blendercraft-cwd"),
                (plugin_dir, "/tmp/blendercraft-plugin"),
                (str(Path(plugin_dir).resolve()), "/tmp/blendercraft-plugin"),
                (token, "TOKEN"),
                (server.url, "http://127.0.0.1:0/mcp"),
                (str(Path.home()), "/HOME"),
                (os.path.expanduser("~"), "/HOME"),
            ]
    finally:
        server.stop()

    out_dir = ROOT / "tests" / "fixtures"
    out_dir.mkdir(parents=True, exist_ok=True)
    kept = [line for line in map(sanitize, raw) if line is not None]
    text = "\n".join(redact(line, replacements) for line in kept) + "\n"
    # Anything that still looks like an e-mail address is machine specific.
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "user@example.com", text)
    (out_dir / f"{name}.jsonl").write_text(text, encoding="utf-8")
    print(f"wrote {out_dir / (name + '.jsonl')} ({len(kept)} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
