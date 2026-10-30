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

    scope = tracker.ai_scope() if tracker is not None else nullcontext()
    with scope:
        try:
            result = tool.handler(args)
        except Exception:  # noqa: BLE001 - reported to the model as an error result
            result = ToolResult(text=traceback.format_exc(), is_error=True)
        # Pushed even after a failure: the handler may have changed the scene
        # before raising, and the step keeps that change undoable.
        if tool.kind == "scene" and tool.push_undo:
            push_step(args.get("description") or name)
            if tracker is not None:
                tracker.record_ai_step()
    return replace(result, text=truncate(result.text))
