"""Runs registered tools with undo steps, change tracking and truncation."""

import traceback
from contextlib import nullcontext
from dataclasses import replace

from ..undo import ChangeTracker, push_step
from .base import REGISTRY, ToolResult, truncate


def call_tool(
    name: str, args: dict, tracker: ChangeTracker | None = None
) -> ToolResult:
    tool = REGISTRY.get(name)
    if tool is None:
        return ToolResult(text=f"Unknown tool: {name}", is_error=True)

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
