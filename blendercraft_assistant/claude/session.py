"""AgentSession: the state machine behind the side panel.

Owns one conversation and (lazily) one Claude Code process: message queue
while the agent works, Stop, tool-call limit, change counter, saving and
resuming. All methods run on Blender's main thread; the process callbacks
(on_line / on_exit) must be marshalled there before reaching handle_line /
handle_exit.

This module must not import bpy.
"""

import time
from collections.abc import Callable
from typing import Protocol

from ..conversations import Conversation, ConversationStore, Message, ToolCall
from .stream import (
    SessionStarted,
    TextDelta,
    ToolFinished,
    ToolStarted,
    TurnFinished,
    parse_line,
)

IDLE = "IDLE"
WORKING = "WORKING"
ERROR = "ERROR"

SUMMARY_HEADER = "Resumo da conversa anterior:"
SUMMARY_MESSAGES = 10

_DEFAULT_NOTES = {
    "step_limit": "Stopped: tool call limit reached for this request.",
}


class ProcessLike(Protocol):
    """What AgentSession needs from ClaudeProcess."""

    def send_user(self, content: list[dict]) -> None: ...

    def stop(self) -> None: ...

    @property
    def alive(self) -> bool: ...


class AgentSession:
    def __init__(
        self,
        conversation: Conversation,
        store: ConversationStore,
        spawn: Callable[[str | None], ProcessLike],  # receives the resume id
        tool_kinds: Callable[[str], str],  # tool name -> "read" | "scene" | "file"
        max_tool_calls: int = 40,
        notes: dict[str, str] | None = None,
    ):
        self.conversation = conversation
        self.store = store
        self._spawn = spawn
        self._tool_kinds = tool_kinds
        self.max_tool_calls = max_tool_calls
        self._notes = {**_DEFAULT_NOTES, **(notes or {})}

        self.status = IDLE
        self.queue: list[tuple[str, list[dict]]] = []
        self.changes = 0  # successful "scene" tool calls in this conversation
        self.error = ""

        self._process: ProcessLike | None = None
        # True from a spawn with --resume until the CLI proves the resume
        # worked (SessionStarted or a clean TurnFinished).
        self._resume_pending = False
        self._tool_count = 0  # ToolStarted in the current turn
        self._user_message: Message | None = None
        self._assistant: Message | None = None
        self._pending: tuple[str, list[dict]] = ("", [])  # current turn's input

    # ---- public API -------------------------------------------------

    def send(self, text: str, attachments: list[dict] = ()) -> None:
        attachments = list(attachments)
        if self.status == WORKING:
            self.queue.append((text, attachments))
            return
        self._begin_turn(text, attachments)

    def send_now(self, text: str, attachments: list[dict] = ()) -> None:
        """Interrupt the current work and send right away (queue dropped)."""
        self.stop()
        self.send(text, attachments)

    def stop(self) -> None:
        """Kill the process, mark running tools interrupted, go IDLE and
        save. The next send() respawns with --resume. Queued messages are
        dropped: they were written for the work that was just cancelled."""
        self._drop_process()
        self._interrupt_tools()
        self.queue.clear()
        self.status = IDLE
        self._save()

    def close(self) -> None:
        """New conversation / quitting: stop() and save."""
        self.stop()

    def handle_line(self, line: str) -> None:
        for event in parse_line(line):
            self._handle_event(event)

    def handle_exit(self, code: int, stderr_tail: str = "") -> None:
        proc = self._process
        # No process: we stopped it ourselves. Process still alive: this is
        # the late exit of an earlier process, already replaced.
        if proc is None or proc.alive:
            return
        self._process = None
        if self.status != WORKING:
            return
        if self._resume_pending and code != 0:
            self._resume_fallback()
            return
        self._interrupt_tools()
        self._fail(f"Claude Code exited with code {code}", stderr_tail.strip())

    # ---- turns ------------------------------------------------------

    def _begin_turn(self, text: str, attachments: list[dict]) -> None:
        conv = self.conversation
        self.error = ""
        self._tool_count = 0
        self._user_message = Message("user", text)
        self._assistant = Message("assistant")
        conv.messages += [self._user_message, self._assistant]
        self._pending = (text, attachments)
        self.status = WORKING
        if self._deliver([{"type": "text", "text": text}, *attachments]):
            self._save()

    def _deliver(self, content: list[dict]) -> bool:
        """Send content, (re)creating the process as needed. A dead process
        is replaced once (resuming the session); a second failure is an
        ERROR. Returns False after setting ERROR."""
        last: Exception | None = None
        for _ in range(2):
            try:
                self._ensure_process().send_user(content)
                return True
            except (RuntimeError, OSError) as exc:
                last = exc
                self._drop_process()
        self._interrupt_tools()
        self._fail(f"Could not talk to Claude Code: {last}")
        return False

    def _ensure_process(self) -> ProcessLike:
        if self._process is not None and not self._process.alive:
            self._drop_process()
        if self._process is None:
            resume_id = self.conversation.claude_session_id
            self._process = self._spawn(resume_id)
            self._resume_pending = resume_id is not None
        return self._process

    def _drop_process(self) -> None:
        proc, self._process = self._process, None
        self._resume_pending = False
        if proc is not None:
            try:
                proc.stop()
            except Exception:
                pass

    def _resume_fallback(self) -> None:
        """--resume failed (session gone): start fresh and replay the last
        user message preceded by a summary of the previous conversation."""
        conv = self.conversation
        conv.claude_session_id = None
        self._drop_process()
        history = conv.messages[: conv.messages.index(self._user_message)]
        lines = [
            f"{m.role}: {m.text}" for m in history[-SUMMARY_MESSAGES:] if m.text.strip()
        ]
        text, attachments = self._pending
        summary = "\n".join([SUMMARY_HEADER, *lines])
        self._assistant.text = ""
        self._assistant.tools = []
        self._tool_count = 0
        self._deliver([{"type": "text", "text": f"{summary}\n\n{text}"}, *attachments])

    def _fail(self, message: str, detail: str = "") -> None:
        self.status = ERROR
        self.error = f"{message}\n{detail}" if detail else message
        self._save()

    # ---- events -----------------------------------------------------

    def _handle_event(self, event) -> None:
        if isinstance(event, SessionStarted):
            self.conversation.claude_session_id = event.session_id
            self._resume_pending = False
            return
        if self.status != WORKING:  # stale output after stop() / an error
            return
        if isinstance(event, TextDelta):
            self._assistant.text += event.text
        elif isinstance(event, ToolStarted):
            self._tool_started(event)
        elif isinstance(event, ToolFinished):
            self._tool_finished(event)
        elif isinstance(event, TurnFinished):
            self._turn_finished(event)

    def _find_tool(self, tool_use_id: str) -> ToolCall | None:
        return next((t for t in self._assistant.tools if t.id == tool_use_id), None)

    def _tool_started(self, event: ToolStarted) -> None:
        existing = self._find_tool(event.tool_use_id)
        if existing is not None:  # same block seen again
            existing.input = event.input
            return
        self._tool_count += 1
        self._assistant.tools.append(
            ToolCall(event.tool_use_id, event.name, event.input)
        )
        if self._tool_count > self.max_tool_calls:
            note = self._notes["step_limit"]
            self._assistant.text = f"{self._assistant.text}\n\n{note}".strip()
            self.stop()

    def _tool_finished(self, event: ToolFinished) -> None:
        tool = self._find_tool(event.tool_use_id)
        if tool is None:
            return
        tool.output = event.output
        tool.status = "error" if event.is_error else "ok"
        if not event.is_error and self._tool_kinds(tool.name) == "scene":
            self.changes += 1

    def _turn_finished(self, event: TurnFinished) -> None:
        if event.is_error and self._resume_pending:
            self._resume_fallback()
            return
        self._resume_pending = False
        if event.is_error and not self._assistant.text:
            self._assistant.text = event.result
        self.status = IDLE
        self._save()
        if self.queue:
            text, attachments = self.queue.pop(0)
            self._begin_turn(text, attachments)

    # ---- helpers ----------------------------------------------------

    def _interrupt_tools(self) -> None:
        if self._assistant is None:
            return
        for tool in self._assistant.tools:
            if tool.status == "running":
                tool.status = "interrupted"

    def _save(self) -> None:
        if not self.conversation.messages:
            return  # nothing worth a file yet
        self.conversation.updated = time.time()
        self.store.save(self.conversation)
