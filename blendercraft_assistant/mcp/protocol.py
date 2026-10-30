"""MCP protocol layer (JSON-RPC 2.0, spec 2025-06-18), transport agnostic.

This module must not import bpy: the MCP server imports it under the
system python3.
"""

import base64
import json
from collections.abc import Callable
from typing import Any

from ..tools.base import Tool, ToolResult

PROTOCOL_VERSION = "2025-06-18"
APPROVAL_TOOL = "approve_action"

_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603

_APPROVAL_DEFINITION = {
    "name": APPROVAL_TOOL,
    "description": (
        "Permission prompt: asks the user whether a tool call may run. "
        "Returns the allow/deny decision as JSON."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "tool_name": {"type": "string"},
            "input": {"type": "object"},
        },
        "required": ["tool_name", "input"],
    },
}


def error_response(id_: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _text_result(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


class McpHandler:
    def __init__(
        self,
        list_tools: Callable[[], list[Tool]],
        call: Callable[[str, dict], ToolResult],
        approve: Callable[[str, dict], tuple[bool, str]] | None = None,
        name: str = "blendercraft",
        version: str = "0.1.0",
    ):
        self._list_tools = list_tools
        self._call = call
        self._approve = approve
        self._name = name
        self._version = version

    def handle(self, message: dict) -> dict | None:
        """Handle one JSON-RPC message. Returns None when no reply is due
        (notifications and responses from the client)."""
        if not isinstance(message, dict):
            return error_response(None, _INVALID_REQUEST, "Invalid request")
        has_id = "id" in message
        id_ = message.get("id")
        method = message.get("method")
        if method is None:
            if "result" in message or "error" in message:
                return None  # a response to a server request: nothing to do
            return error_response(id_, _INVALID_REQUEST, "Invalid request")
        if not isinstance(method, str):
            return error_response(id_, _INVALID_REQUEST, "Invalid request")
        if not has_id:
            return None  # notification (initialized, cancelled, ...)
        params = message.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return error_response(id_, _INVALID_PARAMS, "params must be an object")
        try:
            return self._dispatch(id_, method, params)
        except Exception as exc:  # defensive: never let a request crash the server
            return error_response(id_, _INTERNAL_ERROR, f"Internal error: {exc}")

    def _dispatch(self, id_: Any, method: str, params: dict) -> dict:
        if method == "initialize":
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": self._name, "version": self._version},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": self._tool_definitions()}
        elif method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments")
            if arguments is None:
                arguments = {}
            if not isinstance(name, str) or not isinstance(arguments, dict):
                return error_response(
                    id_, _INVALID_PARAMS, "tools/call needs a name and object arguments"
                )
            result = self._tools_call(name, arguments)
        else:
            return error_response(id_, _METHOD_NOT_FOUND, f"Method not found: {method}")
        return {"jsonrpc": "2.0", "id": id_, "result": result}

    def _tool_definitions(self) -> list[dict]:
        tools = [
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.input_schema,
            }
            for t in self._list_tools()
        ]
        if self._approve is not None:
            tools.append(_APPROVAL_DEFINITION)
        return tools

    def _tools_call(self, name: str, arguments: dict) -> dict:
        if name == APPROVAL_TOOL:
            return self._approval_call(arguments)
        # Only tools in the current listing may run (e.g. READONLY mode).
        allowed = {t.name for t in self._list_tools()}
        if name not in allowed:
            return _text_result(f"Unknown tool: {name}", is_error=True)
        try:
            result = self._call(name, arguments)
            content: list[dict] = [{"type": "text", "text": result.text}]
            for png in result.images:
                content.append(
                    {
                        "type": "image",
                        "data": base64.b64encode(png).decode("ascii"),
                        "mimeType": "image/png",
                    }
                )
            return {"content": content, "isError": bool(result.is_error)}
        except Exception as exc:
            return _text_result(f"{type(exc).__name__}: {exc}", is_error=True)

    def _approval_call(self, arguments: dict) -> dict:
        if self._approve is None:
            return _text_result(f"Unknown tool: {APPROVAL_TOOL}", is_error=True)
        tool_name = arguments.get("tool_name")
        tool_input = arguments.get("input")
        if not isinstance(tool_name, str) or not isinstance(tool_input, dict):
            return _text_result(
                "approve_action needs tool_name (string) and input (object)",
                is_error=True,
            )
        try:
            allowed, reason = self._approve(tool_name, tool_input)
        except Exception as exc:  # fail closed
            allowed, reason = False, f"Approval failed: {exc}"
        if allowed:
            decision = {"behavior": "allow", "updatedInput": tool_input}
        else:
            decision = {"behavior": "deny", "message": reason}
        return _text_result(json.dumps(decision, ensure_ascii=False))
