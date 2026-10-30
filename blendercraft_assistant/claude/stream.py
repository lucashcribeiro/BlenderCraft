"""Parser for Claude Code's `--output-format stream-json` lines.

Turns one line of stdout into zero or more events for the UI. The shapes
follow the recorded fixtures in tests/fixtures (Claude Code 2.1.x):

- system/init                 -> SessionStarted
- stream_event content_block_start of a text block
                              -> TextBlockStarted (text blocks of separate
                                 messages, e.g. around a tool call, are
                                 separate paragraphs)
- stream_event text_delta     -> TextDelta (the only source of text; the
                                 complete `assistant` messages repeat it)
- assistant tool_use blocks   -> ToolStarted (input arrives complete here)
- user tool_result blocks     -> ToolFinished
- result                      -> TurnFinished

Anything else (hooks, status, rate limits, thinking, partial tool JSON,
malformed lines) yields no events.

This module must not import bpy.
"""

import json
from dataclasses import dataclass

MCP_PREFIX = "mcp__blendercraft__"


@dataclass
class SessionStarted:
    session_id: str


@dataclass
class TextBlockStarted:
    pass


@dataclass
class TextDelta:
    text: str


@dataclass
class ToolStarted:
    tool_use_id: str
    name: str  # without the "mcp__blendercraft__" prefix
    input: dict


@dataclass
class ToolFinished:
    tool_use_id: str
    output: str
    is_error: bool


@dataclass
class TurnFinished:
    is_error: bool
    result: str


Event = (
    SessionStarted | TextBlockStarted | TextDelta | ToolStarted | ToolFinished | TurnFinished
)


def _blocks(message) -> list[dict]:
    """Content blocks of an assistant/user message ([] for plain strings)."""
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict)]


def _tool_name(name) -> str:
    name = name if isinstance(name, str) else ""
    return name[len(MCP_PREFIX):] if name.startswith(MCP_PREFIX) else name


def _result_text(content) -> str:
    """tool_result content is a string or a list of blocks; keep the text."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [
        b["text"]
        for b in content
        if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
    ]
    return "\n".join(parts)


def parse_line(line: str) -> list[Event]:
    try:
        data = json.loads(line)
    except ValueError:
        return []
    if not isinstance(data, dict):
        return []
    kind = data.get("type")

    if kind == "system":
        if data.get("subtype") == "init" and isinstance(data.get("session_id"), str):
            return [SessionStarted(data["session_id"])]
        return []

    if kind == "stream_event":
        event = data.get("event")
        if not isinstance(event, dict):
            return []
        if event.get("type") == "content_block_start":
            block = event.get("content_block")
            if isinstance(block, dict) and block.get("type") == "text":
                return [TextBlockStarted()]
            return []
        if event.get("type") != "content_block_delta":
            return []
        delta = event.get("delta")
        if (
            isinstance(delta, dict)
            and delta.get("type") == "text_delta"
            and isinstance(delta.get("text"), str)
        ):
            return [TextDelta(delta["text"])]
        return []

    if kind == "assistant":
        out: list[Event] = []
        for block in _blocks(data.get("message")):
            if block.get("type") != "tool_use":
                continue
            tool_input = block.get("input")
            out.append(
                ToolStarted(
                    tool_use_id=str(block.get("id", "")),
                    name=_tool_name(block.get("name")),
                    input=tool_input if isinstance(tool_input, dict) else {},
                )
            )
        return out

    if kind == "user":
        out = []
        for block in _blocks(data.get("message")):
            if block.get("type") != "tool_result":
                continue
            out.append(
                ToolFinished(
                    tool_use_id=str(block.get("tool_use_id", "")),
                    output=_result_text(block.get("content")),
                    is_error=bool(block.get("is_error", False)),
                )
            )
        return out

    if kind == "result":
        result = data.get("result")
        return [
            TurnFinished(
                is_error=bool(data.get("is_error", False)),
                result=result if isinstance(result, str) else "",
            )
        ]

    return []
