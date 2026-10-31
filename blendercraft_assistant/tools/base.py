"""Tool catalog base: result and tool types, registry, truncation.

This module must not import bpy: the MCP server imports it under the
system python3.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class ToolResult:
    text: str
    images: list[bytes] = field(default_factory=list)  # PNG
    is_error: bool = False
    # Net move on the undo stack: -n after undoing n steps, +n after redoing.
    undo_delta: int = 0


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], ToolResult]
    # read: only reads; scene: changes the scene; file: writes to disk
    kind: Literal["read", "scene", "file"]
    push_undo: bool = True  # only applies when kind == "scene"
    # False: the handler runs on the calling (MCP server) thread instead of
    # Blender's main thread, for long network waits. It must not touch bpy
    # except through mainthread.run_on_main, and it pushes its own undo step
    # (dispatch.scene_step); call_tool neither pushes one nor opens the AI
    # scope for it.
    main_thread: bool = True


REGISTRY: dict[str, Tool] = {}

_MODES = ("AUTO", "ASK", "READONLY")


def register_tool(tool: Tool) -> None:
    if tool.name in REGISTRY:
        raise ValueError(f"Tool already registered: {tool.name!r}")
    REGISTRY[tool.name] = tool


def tools_for_mode(mode: str) -> list[Tool]:
    if mode not in _MODES:
        raise ValueError(f"Unknown mode: {mode!r}")
    if mode == "READONLY":
        return [t for t in REGISTRY.values() if t.kind == "read"]
    return list(REGISTRY.values())


def truncate(text: str, limit: int = 8000) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n…[truncado {len(text) - limit} caracteres]"


def json_result(data: Any) -> ToolResult:
    """Wrap JSON-serializable data as a text result."""
    return ToolResult(text=json.dumps(data, ensure_ascii=False, indent=1))
