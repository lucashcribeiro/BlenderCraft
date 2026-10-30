"""Runtime singleton: MCP server, Claude Code session and change tracker.

Lives while the add-on is enabled: start() on the first timer tick after
register(), stop() from unregister(). Everything here runs on Blender's main
thread except the MCP handler callbacks (_list_tools, _call, _approve), which
run on server threads and only touch plain Python state or go through
mainthread.
"""

import json
import logging
import secrets
import threading
import time
import traceback
from pathlib import Path

import bpy
from bpy.app.handlers import persistent

from . import mainthread
from .claude.process import TOOL_PREFIX, ClaudeProcess, build_args, find_claude
from .claude.session import AgentSession
from .conversations import ConversationStore
from .i18n import t
from .mcp.protocol import McpHandler
from .mcp.server import McpServer
from .tools import load_all
from .tools.base import REGISTRY, tools_for_mode
from .tools.dispatch import call_tool
from .undo import ChangeTracker

log = logging.getLogger(__name__)

ADDON_ID = __package__
PLUGIN_DIR = Path(__file__).resolve().parent / "plugin"
WORKSPACE = Path.home() / "Library/Application Support/BlenderCraft/workspace"
APPROVAL_TIMEOUT = 600.0  # seconds; no answer means deny
STOP_JOIN_TIMEOUT = 8.0  # seconds stop() waits for Claude processes to end

# Placeholder until the real system prompt builder exists; swap it here.
SYSTEM_PROMPT = (
    "You are an assistant embedded in Blender, working on the user's open "
    "scene through the blendercraft MCP tools. 1 Blender unit = 1 mm. "
    "Inspect before changing, and keep each change small and described."
)

_LOGIN_HINTS = ("/login", "not logged in", "invalid api key", "log in")

server: McpServer | None = None
token = ""
store: ConversationStore | None = None
session: AgentSession | None = None
tracker: ChangeTracker | None = None
# (tool name without prefix, tool input, event, answer list) while Claude
# waits for the user in ASK mode. The answer list gets one (allowed, reason).
pending_approval: tuple[str, dict, threading.Event, list] | None = None
mode = "AUTO"  # AUTO | ASK | READONLY; plain copy of the panel property
model = ""  # "" = Claude Code's default
claude_problem = ""  # "", "missing" or "login": shown as a panel warning

_approval_lock = threading.Lock()
_stoppers: list[threading.Thread] = []


# ---- lifecycle -------------------------------------------------------


def start() -> None:
    """Start the MCP server, the main-thread timer and the handlers."""
    global server, token, store, tracker
    if server is not None:
        return
    load_all()
    try:
        token = secrets.token_urlsafe(32)
        tracker = ChangeTracker()
        if store is None:
            store = ConversationStore()
        mainthread.install_timer()
        _add_handlers()
        handler = McpHandler(_list_tools, _call, _approve)
        port = _pref("external_port", 0)
        try:
            srv = McpServer(handler, token, port)
            srv.start()
        except OSError:
            if not port:
                raise
            log.warning("MCP port %s unavailable, using a random port", port)
            srv = McpServer(handler, token, 0)
            srv.start()
        server = srv
    except BaseException:
        stop()
        raise


def stop() -> None:
    """Close the session, stop the server, the timer and the handlers.

    Waits up to STOP_JOIN_TIMEOUT for Claude processes being stopped, so
    none outlives the add-on (unregister also runs when Blender quits).
    """
    global server, session, token
    _deny_pending("The Blender assistant was stopped.")
    if session is not None:
        try:
            session.close()
        except Exception:  # noqa: BLE001 - shutting down regardless
            traceback.print_exc()
        session = None
    srv, server = server, None
    if srv is not None:
        srv.stop()
    token = ""
    mainthread.uninstall_timer()
    _remove_handlers()
    _join_stoppers(STOP_JOIN_TIMEOUT)


def _add_handlers() -> None:
    handlers = bpy.app.handlers
    if _on_depsgraph_update not in handlers.depsgraph_update_post:
        handlers.depsgraph_update_post.append(_on_depsgraph_update)
    if _on_load_post not in handlers.load_post:
        handlers.load_post.append(_on_load_post)


def _remove_handlers() -> None:
    handlers = bpy.app.handlers
    for handler_list, fn in (
        (handlers.depsgraph_update_post, _on_depsgraph_update),
        (handlers.load_post, _on_load_post),
    ):
        while fn in handler_list:
            handler_list.remove(fn)


@persistent
def _on_depsgraph_update(scene, depsgraph) -> None:
    if tracker is not None:
        tracker.on_depsgraph_update(scene, depsgraph)


@persistent
def _on_load_post(*_args) -> None:
    # A loaded file has a fresh undo history: nothing of the AI to undo.
    if tracker is not None:
        tracker.begin_turn()


def _pref(name: str, default):
    addon = bpy.context.preferences.addons.get(ADDON_ID)
    if addon is None or addon.preferences is None:
        return default
    return getattr(addon.preferences, name, default)


def tag_redraw() -> None:
    """Redraw the 3D Views (the panel lives there). No-op without a UI."""
    if bpy.app.background:
        return
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return
    for window in wm.windows:
        screen = window.screen
        if screen is None:
            continue
        for area in screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def mcp_command() -> str:
    """Command that registers this server with an external Claude Code."""
    url = server.url if server is not None else ""
    return (
        f"claude mcp add --transport http blendercraft {url} "
        f'--header "Authorization: Bearer {token}"'
    )


# ---- MCP handler callbacks (server threads) --------------------------


def _list_tools():
    return tools_for_mode(mode)


def _call(name: str, args: dict):
    return mainthread.run_on_main(lambda: call_tool(name, args, tracker))


def _approve(tool_name: str, tool_input: dict) -> tuple[bool, str]:
    """Show the request in the panel and wait for Approve/Deny."""
    global pending_approval
    name = tool_name[len(TOOL_PREFIX):] if tool_name.startswith(TOOL_PREFIX) else tool_name
    event = threading.Event()
    answer: list[tuple[bool, str]] = []
    with _approval_lock:  # one request on screen at a time
        if server is None:
            return False, "The Blender assistant was stopped."
        pending_approval = (name, tool_input, event, answer)
        mainthread.post(tag_redraw)
        try:
            answered = event.wait(APPROVAL_TIMEOUT)
        finally:
            if pending_approval is not None and pending_approval[2] is event:
                pending_approval = None
            mainthread.post(tag_redraw)
    if not answered or not answer:
        return False, "The user did not answer in time."
    return answer[0]


def answer_approval(allow: bool) -> None:
    """The user's answer to the pending approval (main thread)."""
    reason = "" if allow else "The user denied this action."
    _resolve_pending(allow, reason)


def _deny_pending(reason: str) -> None:
    _resolve_pending(False, reason)


def _resolve_pending(allow: bool, reason: str) -> None:
    global pending_approval
    pending = pending_approval
    if pending is None:
        return
    pending_approval = None
    _name, _input, event, answer = pending
    if not answer:
        answer.append((allow, reason))
    event.set()
    tag_redraw()


# ---- session ---------------------------------------------------------


def ensure_session() -> AgentSession:
    """The current session, creating a new conversation if there is none."""
    global session
    if session is None:
        session = _make_session(store.new(bpy.data.filepath, model))
    return session


def _make_session(conversation) -> AgentSession:
    return AgentSession(
        conversation,
        store,
        _spawn,
        _tool_kind,
        notes={"step_limit": t("step_limit"), "queue_dropped": t("queue_dropped")},
        on_turn_begin=_on_turn_begin,
    )


def _close_session() -> None:
    global session
    _deny_pending("The user stopped the assistant.")
    if session is not None:
        session.close()
        session = None


def new_conversation() -> None:
    """Close the current conversation; the next message starts a new one."""
    _close_session()
    tag_redraw()


def open_conversation(conv_id: str) -> None:
    """Continue a saved conversation (raises OSError/ValueError if unreadable)."""
    global session
    if session is not None and session.conversation.id == conv_id:
        return
    conversation = store.load(conv_id)
    _close_session()
    session = _make_session(conversation)
    tag_redraw()


def delete_conversation(conv_id: str) -> None:
    if session is not None and session.conversation.id == conv_id:
        _close_session()  # before deleting: closing saves the file
    store.delete(conv_id)
    tag_redraw()


def stop_session() -> None:
    """Panel Stop: interrupt the agent and drop the queue."""
    _deny_pending("The user stopped the assistant.")
    if session is not None:
        session.stop()
    tag_redraw()


def set_mode(value: str) -> None:
    """New permission mode; the next message restarts Claude with --resume."""
    global mode
    if value == mode:
        return
    mode = value
    stop_session()


def set_model(value: str) -> None:
    global model
    if value == model:
        return
    model = value
    if session is not None:
        session.conversation.model = value
    stop_session()


def _on_turn_begin() -> None:
    if tracker is not None:
        tracker.begin_turn()


def _tool_kind(name: str) -> str:
    tool = REGISTRY.get(name)
    return tool.kind if tool is not None else "read"


def _spawn(resume_id, on_line, on_exit):
    global claude_problem
    claude = find_claude(_pref("claude_path", ""))
    if claude is None:
        claude_problem = "missing"
        raise RuntimeError(t("claude_missing"))
    if claude_problem == "missing":
        claude_problem = ""
    if server is None:
        raise RuntimeError(t("server_off"))
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    args = build_args(
        claude,
        mcp_url=server.url,
        token=token,
        mode=mode,
        model=model,
        system_prompt=SYSTEM_PROMPT,
        plugin_dir=str(PLUGIN_DIR),
        read_tools=[tool.name for tool in REGISTRY.values() if tool.kind == "read"],
        write_tools=[tool.name for tool in REGISTRY.values() if tool.kind != "read"],
        resume=resume_id,
    )

    # Lines and the exit go through the same FIFO, in reader-thread order.
    def line_cb(line: str) -> None:
        mainthread.post(lambda: _deliver_line(on_line, line))

    def exit_cb(code: int, tail: str) -> None:
        mainthread.post(lambda: _deliver_exit(on_exit, code, tail))

    proc = ClaudeProcess(args, str(WORKSPACE), line_cb, exit_cb)
    try:
        proc.start()
    except BaseException:
        proc.stop()  # removes the temp MCP config file
        raise
    return _BackgroundStop(proc)


def _looks_like_login(text: str) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in _LOGIN_HINTS)


def _deliver_line(on_line, line: str) -> None:
    global claude_problem
    if '"result"' in line:
        try:
            data = json.loads(line)
        except ValueError:
            data = None
        if isinstance(data, dict) and data.get("type") == "result":
            result = data.get("result")
            if data.get("is_error") and isinstance(result, str) and _looks_like_login(result):
                claude_problem = "login"
            elif not data.get("is_error") and claude_problem == "login":
                claude_problem = ""
    on_line(line)
    tag_redraw()


def _deliver_exit(on_exit, code: int, tail: str) -> None:
    global claude_problem
    if code != 0 and _looks_like_login(tail):
        claude_problem = "login"
    on_exit(code, tail)
    tag_redraw()


class _BackgroundStop:
    """ClaudeProcess whose stop() returns at once: terminating the CLI can
    take seconds, so it runs on a worker thread. The session has already
    dropped the process, so its late output is ignored."""

    def __init__(self, proc: ClaudeProcess):
        self._proc = proc

    def send_user(self, content: list[dict]) -> None:
        self._proc.send_user(content)

    @property
    def alive(self) -> bool:
        return self._proc.alive

    def stop(self) -> None:
        _stoppers[:] = [th for th in _stoppers if th.is_alive()]
        thread = threading.Thread(
            target=self._proc.stop, name="blendercraft-claude-stop", daemon=True
        )
        _stoppers.append(thread)
        thread.start()


def _join_stoppers(timeout: float) -> None:
    end = time.monotonic() + timeout
    for thread in list(_stoppers):
        thread.join(max(0.0, end - time.monotonic()))
    _stoppers[:] = [th for th in _stoppers if th.is_alive()]
