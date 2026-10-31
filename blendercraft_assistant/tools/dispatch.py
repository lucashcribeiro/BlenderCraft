"""Runs registered tools with undo steps, change tracking and truncation."""

import threading
import traceback
from contextlib import nullcontext
from dataclasses import replace

from ..undo import ChangeTracker, push_step
from .base import REGISTRY, ToolResult, truncate

# Tracker of the off-main-thread tool call running on this thread.
_local = threading.local()


def current_tracker() -> ChangeTracker | None:
    """Tracker handed to the running off-main-thread handler (or None)."""
    return getattr(_local, "tracker", None)


def scene_step(fn, label: str, tracker: ChangeTracker | None = None):
    """Run fn as one AI change: inside the AI scope, followed by an undo
    step "IA: <label>" (pushed even if fn raises, like call_tool does).
    Main thread only: off-main-thread handlers wrap this in run_on_main."""
    scope = tracker.ai_scope() if tracker is not None else nullcontext()
    with scope:
        try:
            return fn()
        finally:
            push_step(label)
            if tracker is not None:
                tracker.record_ai_step()


def call_tool(
    name: str, args: dict, tracker: ChangeTracker | None = None
) -> ToolResult:
    tool = REGISTRY.get(name)
    if tool is None:
        return ToolResult(text=f"Unknown tool: {name}", is_error=True)

    if not tool.main_thread:
        return _call_off_main(tool, args, tracker)

    if tracker is not None and name == "undo":
        refused = _check_undo_limit(args, tracker)
        if refused is not None:
            return refused

    scope = tracker.ai_scope() if tracker is not None else nullcontext()
    with scope:
        try:
            result = tool.handler(args)
        # SystemExit and KeyboardInterrupt are not Exceptions; they must not
        # unwind into Blender's main loop (quitting it or killing the timer).
        except BaseException:  # noqa: BLE001 - reported as an error result
            result = ToolResult(text=traceback.format_exc(), is_error=True)
        # Pushed even after a failure: the handler may have changed the scene
        # before raising, and the step keeps that change undoable.
        if tool.kind == "scene" and tool.push_undo:
            try:
                push_step(args.get("description") or name)
                if tracker is not None:
                    tracker.record_ai_step()
            except Exception:  # noqa: BLE001 - reported as an error result
                result = ToolResult(
                    text=f"{result.text}\n\nUndo step failed:\n{traceback.format_exc()}",
                    images=result.images,
                    is_error=True,
                )
        if tracker is not None and result.undo_delta:
            tracker.shift_ai_steps(result.undo_delta)
    return replace(result, text=truncate(result.text))


def _call_off_main(tool, args: dict, tracker: ChangeTracker | None) -> ToolResult:
    """Run a main_thread=False handler on this thread (no bpy here)."""
    _local.tracker = tracker
    try:
        result = tool.handler(args)
    except BaseException:  # noqa: BLE001 - reported as an error result
        result = ToolResult(text=traceback.format_exc(), is_error=True)
    finally:
        _local.tracker = None
    return replace(result, text=truncate(result.text))


def _check_undo_limit(args: dict, tracker: ChangeTracker) -> ToolResult | None:
    """The AI may only undo its own steps of the current request: past them
    lie the user's work and earlier requests."""
    try:
        steps = int(args.get("steps", 1))
    except (TypeError, ValueError):
        return None  # the handler reports the bad argument
    available = tracker.ai_steps
    if steps <= available:
        return None
    return ToolResult(
        text=(
            f"Refused: undo can only revert the steps made in this request "
            f"({available} available, {steps} requested). Earlier changes, "
            "including the user's own work, must be undone by the user."
        ),
        is_error=True,
    )
