"""AgentSession: the state machine behind the side panel.

Owns one conversation and (lazily) one Claude Code process: message queue
while the agent works, Stop, tool-call limit, change counter, saving and
resuming. All methods run on Blender's main thread. spawn() gets callbacks
bound to the process it creates (on_line / on_exit); the caller must call
them on the main thread (the runtime posts them through mainthread), and
output of a process that was replaced or stopped is dropped. handle_line /
handle_exit feed the current process's output directly (used by tests).

This module must not import bpy.
"""

import logging
import time
from collections.abc import Callable
from typing import Protocol

from ..conversations import Conversation, ConversationStore, Message, ToolCall
from .stream import (
    SessionStarted,
    TextBlockStarted,
    TextDelta,
    ToolFinished,
    ToolStarted,
    TurnFinished,
    parse_line,
)

log = logging.getLogger(__name__)

IDLE = "IDLE"
WORKING = "WORKING"
ERROR = "ERROR"

SUMMARY_MESSAGES = 10

# User-visible texts; the runtime passes translated ones. Fields are
# filled with str.format.
_DEFAULT_NOTES = {
    "step_limit": "Stopped: tool call limit reached for this request.",
    "queue_dropped": "({n} queued messages discarded)",
    "exit_code": "Claude Code exited with code {code}",
    "send_failed": "Could not talk to Claude Code: {error}",
    "save_failed": "Could not save the conversation: {error}",
    # Heads the history replayed when a --resume fails.
    "summary_header": "Summary of the previous conversation:",
}
NOTE_KEYS = tuple(_DEFAULT_NOTES)


class ProcessLike(Protocol):
    """What AgentSession needs from ClaudeProcess."""

    def send_user(self, content: list[dict]) -> None: ...

    def stop(self) -> None: ...

    @property
    def alive(self) -> bool: ...


MAX_TOOL_CALLS = 40  # per user request; also told to the model in the prompt
# Built-in Skill only reads the plugin's skills: not a step of the work.
UNCOUNTED_TOOLS = frozenset({"Skill"})
# Scene tools that move on the undo stack instead of changing the scene.
NOT_CHANGES = frozenset({"undo", "redo"})


class AgentSession:
    def __init__(
        self,
        conversation: Conversation,
        store: ConversationStore,
        # spawn(resume_id, on_line, on_exit) -> process. The two callbacks are
        # bound to this process; output from a replaced/stopped process is
        # dropped, so the runtime can wire them straight to the process (via
        # the main-thread queue).
        spawn: Callable[
            [str | None, Callable[[str], None], Callable[[int, str], None]],
            ProcessLike,
        ],
        tool_kinds: Callable[[str], str],  # tool name -> "read" | "scene" | "file"
        max_tool_calls: int = MAX_TOOL_CALLS,
        notes: dict[str, str] | None = None,
        # Called whenever a user turn is sent to Claude, queued ones included
        # (the runtime resets the undo tracker there).
        on_turn_begin: Callable[[], None] | None = None,
        # Returns a line put before each user message as Claude receives it
        # (not in the conversation shown to the user): display unit, scale
        # and language, read when the message is actually sent.
        context: Callable[[], str] | None = None,
    ):
        self.conversation = conversation
        self.store = store
        self._spawn = spawn
        self._tool_kinds = tool_kinds
        self.max_tool_calls = max_tool_calls
        self._notes = {**_DEFAULT_NOTES, **(notes or {})}
        self._on_turn_begin = on_turn_begin
        self._context = context

        self.status = IDLE
        self.queue: list[tuple[str, list[dict]]] = []
        # Successful "scene" tool calls in this conversation, undo/redo aside.
        self.changes = 0
        self.error = ""

        self._process: ProcessLike | None = None
        # Bumped whenever a process is spawned or dropped; callbacks of an
        # older generation are stale and ignored.
        self._generation = 0
        # True from a spawn with --resume until the resumed process finishes
        # a turn cleanly. SessionStarted does not count: the CLI announces
        # the session before it loads the history, so a resume can still fail.
        self._resume_pending = False
        self._tool_count = 0  # ToolStarted in the current turn, Skill aside
        self._user_index = 0  # position of the current turn's user message
        self._assistant: Message | None = None
        # A text block started: its first delta begins a new paragraph.
        self._new_paragraph = False
        # Current turn's input as Claude receives it (context line included).
        self._pending: tuple[str, list[dict]] = ("", [])

    # ---- public API -------------------------------------------------

    def send(self, text: str, attachments: list[dict] = ()) -> None:
        attachments = list(attachments)
        if self.status == WORKING:
            self.queue.append((text, attachments))
            return
        self._begin_turn(text, attachments)

    def send_now(self, text: str, attachments: list[dict] = ()) -> None:
        """Interrupt the current work and send right away; queued messages
        stay, behind this one."""
        self._halt()
        self._begin_turn(text, list(attachments))

    def stop(self) -> None:
        """User Stop: halt and drop the queued messages, which were written
        for the work that was just cancelled."""
        self.queue.clear()
        self.error = ""
        self._halt()

    def _halt(self) -> None:
        """Kill the process, mark running tools interrupted, go IDLE and
        save. The next send() respawns with --resume."""
        self._drop_process()
        self._interrupt_tools()
        self.status = IDLE
        self._save()

    def close(self) -> None:
        """New conversation / quitting: stop() (queue dropped) and save."""
        self.stop()

    def handle_line(self, line: str) -> None:
        """A stdout line of the current process."""
        self._on_line(self._generation, line)

    def handle_exit(self, code: int, stderr_tail: str = "") -> None:
        """The current process exited."""
        self._on_exit(self._generation, code, stderr_tail)

    def _on_line(self, generation: int, line: str) -> None:
        if generation != self._generation:
            return
        for event in parse_line(line):
            if generation != self._generation:  # a previous event replaced the process
                return
            self._handle_event(event)

    def _on_exit(self, generation: int, code: int, stderr_tail: str) -> None:
        # Stale generation, or no process: we stopped it ourselves.
        if generation != self._generation or self._process is None:
            return
        self._process = None
        if self.status != WORKING:
            return
        if self._resume_pending and code != 0 and not self._turn_has_output():
            self._resume_fallback()
            return
        self._interrupt_tools()
        self._fail(self._notes["exit_code"].format(code=code), stderr_tail.strip())

    # ---- turns ------------------------------------------------------

    def _begin_turn(self, text: str, attachments: list[dict]) -> None:
        if self._on_turn_begin is not None:
            self._on_turn_begin()
        conv = self.conversation
        self.error = ""
        self._tool_count = 0
        self._user_index = len(conv.messages)
        self._assistant = Message("assistant")
        conv.messages += [Message("user", text), self._assistant]
        context = self._context() if self._context is not None else ""
        sent = f"{context}\n{text}" if context else text
        self._pending = (sent, attachments)
        self.status = WORKING
        if self._deliver([{"type": "text", "text": sent}, *attachments]):
            self._save()

    def _start_next(self) -> None:
        if self.queue:
            text, attachments = self.queue.pop(0)
            self._begin_turn(text, attachments)

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
        self._fail(self._notes["send_failed"].format(error=last))
        return False

    def _ensure_process(self) -> ProcessLike:
        if self._process is not None and not self._process.alive:
            self._drop_process()
        if self._process is None:
            resume_id = self.conversation.claude_session_id
            self._generation += 1
            generation = self._generation
            self._process = self._spawn(
                resume_id,
                lambda line: self._on_line(generation, line),
                lambda code, tail: self._on_exit(generation, code, tail),
            )
            self._resume_pending = resume_id is not None
        return self._process

    def _drop_process(self) -> None:
        proc, self._process = self._process, None
        self._generation += 1  # whatever the old process still emits is stale
        self._resume_pending = False
        if proc is not None:
            try:
                proc.stop()
            except Exception:
                pass

    def _turn_has_output(self) -> bool:
        """The current turn already produced text or a tool call. A failure
        after that is not a broken --resume (the session loaded) but e.g. a
        network or usage-limit error: the fallback would drop Claude's
        memory of the conversation and replay actions already taken."""
        return self._assistant is not None and bool(
            self._assistant.text or self._assistant.tools
        )

    def _resume_fallback(self) -> None:
        """--resume failed (session gone): start fresh and replay the last
        user message preceded by a summary of the previous conversation.
        Only used while the resumed turn has no output (_turn_has_output)."""
        conv = self.conversation
        conv.claude_session_id = None
        self._drop_process()
        history = conv.messages[: self._user_index]
        lines = [
            f"{m.role}: {m.text}" for m in history[-SUMMARY_MESSAGES:] if m.text.strip()
        ]
        text, attachments = self._pending
        summary = "\n".join([self._notes["summary_header"], *lines])
        self._assistant.text = ""
        self._assistant.tools = []
        self._tool_count = 0
        self._deliver([{"type": "text", "text": f"{summary}\n\n{text}"}, *attachments])

    def _fail(self, message: str, detail: str = "") -> None:
        self.status = ERROR
        self.error = f"{message}\n{detail}" if detail else message
        if self.queue:
            note = self._notes["queue_dropped"].format(n=len(self.queue))
            self.error = f"{self.error}\n{note}"
            self.queue.clear()
        self._save()

    # ---- events -----------------------------------------------------

    def _handle_event(self, event) -> None:
        if isinstance(event, SessionStarted):
            self.conversation.claude_session_id = event.session_id
            return
        if self.status != WORKING:  # stale output after stop() / an error
            return
        if isinstance(event, TextBlockStarted):
            self._new_paragraph = True
        elif isinstance(event, TextDelta):
            if self._new_paragraph and self._assistant.text and event.text:
                self._assistant.text += "\n\n"
            if event.text:
                self._new_paragraph = False
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
        if event.name not in UNCOUNTED_TOOLS:
            self._tool_count += 1
        self._assistant.tools.append(
            ToolCall(event.tool_use_id, event.name, event.input)
        )
        if self._tool_count > self.max_tool_calls:
            note = self._notes["step_limit"]
            self._assistant.text = f"{self._assistant.text}\n\n{note}".strip()
            self._halt()  # the queue is kept: carry on with the next message
            self._start_next()

    def _tool_finished(self, event: ToolFinished) -> None:
        tool = self._find_tool(event.tool_use_id)
        if tool is None:
            return
        tool.output = event.output
        tool.status = "error" if event.is_error else "ok"
        if (
            not event.is_error
            and tool.name not in NOT_CHANGES
            and self._tool_kinds(tool.name) == "scene"
        ):
            self.changes += 1

    def _turn_finished(self, event: TurnFinished) -> None:
        if event.is_error and self._resume_pending and not self._turn_has_output():
            self._resume_fallback()
            return
        self._resume_pending = False
        if event.is_error and not self._assistant.text:
            self._assistant.text = event.result
        self.status = IDLE
        self._save()
        self._start_next()

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
        try:
            self.store.save(self.conversation)
        except OSError as exc:  # a full disk must not break stop()/the queue
            log.warning("Could not save conversation %s: %s", self.conversation.id, exc)
            note = self._notes["save_failed"].format(error=exc)
            if note not in self.error:
                self.error = f"{self.error}\n{note}".strip()
