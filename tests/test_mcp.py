import base64
import json
import unittest
import urllib.error
import urllib.request

from blendercraft_assistant.mcp.protocol import (
    APPROVAL_TOOL,
    PROTOCOL_VERSION,
    McpHandler,
)
from blendercraft_assistant.mcp.server import McpServer
from blendercraft_assistant.tools.base import Tool, ToolResult

PNG = b"\x89PNG\r\n\x1a\nfake"


def _tool(name="echo"):
    return Tool(
        name=name,
        description="Echo tool",
        input_schema={"type": "object", "properties": {}},
        handler=lambda args: ToolResult(text="ok"),
        kind="read",
    )


def _call(name, args):
    if name == "echo":
        return ToolResult(text=f"echo:{args.get('x')}")
    if name == "shot":
        return ToolResult(text="shot", images=[PNG, PNG + b"2"])
    if name == "fail":
        return ToolResult(text="bad", is_error=True)
    if name == "boom":
        raise RuntimeError("kaboom")
    raise KeyError(name)


def _handler(approve=None):
    tools = [_tool(n) for n in ("echo", "shot", "fail", "boom")]
    return McpHandler(lambda: tools, _call, approve)


def _rpc(method, params=None, id=1):
    msg = {"jsonrpc": "2.0", "method": method}
    if id is not None:
        msg["id"] = id
    if params is not None:
        msg["params"] = params
    return msg


class ProtocolTest(unittest.TestCase):
    def test_initialize(self):
        out = _handler().handle(_rpc("initialize", {"protocolVersion": "2024-11-05"}))
        self.assertEqual(out["id"], 1)
        result = out["result"]
        self.assertEqual(result["protocolVersion"], PROTOCOL_VERSION)
        self.assertEqual(result["capabilities"], {"tools": {}})
        self.assertEqual(result["serverInfo"]["name"], "blendercraft")

    def test_initialized_notification_returns_none(self):
        self.assertIsNone(_handler().handle(_rpc("notifications/initialized", id=None)))

    def test_ping(self):
        self.assertEqual(_handler().handle(_rpc("ping"))["result"], {})

    def test_tools_list_without_approve(self):
        tools = _handler().handle(_rpc("tools/list"))["result"]["tools"]
        self.assertEqual(
            [t["name"] for t in tools], ["echo", "shot", "fail", "boom"]
        )
        self.assertEqual(
            set(tools[0]), {"name", "description", "inputSchema"}
        )

    def test_tools_list_with_approve(self):
        h = _handler(approve=lambda n, i: (True, ""))
        names = [t["name"] for t in h.handle(_rpc("tools/list"))["result"]["tools"]]
        self.assertEqual(names, ["echo", "shot", "fail", "boom", APPROVAL_TOOL])

    def test_tools_call_text(self):
        out = _handler().handle(
            _rpc("tools/call", {"name": "echo", "arguments": {"x": 5}})
        )["result"]
        self.assertEqual(out["content"], [{"type": "text", "text": "echo:5"}])
        self.assertFalse(out["isError"])

    def test_tools_call_image(self):
        out = _handler().handle(_rpc("tools/call", {"name": "shot"}))["result"]
        self.assertEqual(out["content"][0], {"type": "text", "text": "shot"})
        self.assertEqual(len(out["content"]), 3)
        self.assertEqual(
            out["content"][1],
            {
                "type": "image",
                "data": base64.b64encode(PNG).decode("ascii"),
                "mimeType": "image/png",
            },
        )

    def test_tools_call_error_flag(self):
        out = _handler().handle(_rpc("tools/call", {"name": "fail"}))["result"]
        self.assertTrue(out["isError"])

    def test_tools_call_exception_is_error_result(self):
        out = _handler().handle(_rpc("tools/call", {"name": "boom"}))["result"]
        self.assertTrue(out["isError"])
        self.assertIn("kaboom", out["content"][0]["text"])

    def test_unknown_method(self):
        out = _handler().handle(_rpc("nope"))
        self.assertEqual(out["error"]["code"], -32601)
        self.assertEqual(out["id"], 1)

    def test_unknown_notification_returns_none(self):
        self.assertIsNone(_handler().handle(_rpc("nope", id=None)))

    def test_invalid_request(self):
        out = _handler().handle({"jsonrpc": "2.0", "id": 3})
        self.assertEqual(out["error"]["code"], -32600)

    def test_tools_call_bad_params(self):
        out = _handler().handle(_rpc("tools/call", {"arguments": {}}))
        self.assertEqual(out["error"]["code"], -32602)

    def test_approve_allow(self):
        seen = []

        def approve(name, inp):
            seen.append((name, inp))
            return True, ""

        out = _handler(approve)
        res = out.handle(
            _rpc(
                "tools/call",
                {
                    "name": APPROVAL_TOOL,
                    "arguments": {"tool_name": "scene_x", "input": {"a": 1}},
                },
            )
        )["result"]
        self.assertEqual(seen, [("scene_x", {"a": 1})])
        self.assertFalse(res["isError"])
        self.assertEqual(
            json.loads(res["content"][0]["text"]),
            {"behavior": "allow", "updatedInput": {"a": 1}},
        )

    def test_approve_deny(self):
        h = _handler(lambda n, i: (False, "no thanks"))
        res = h.handle(
            _rpc(
                "tools/call",
                {
                    "name": APPROVAL_TOOL,
                    "arguments": {"tool_name": "t", "input": {}},
                },
            )
        )["result"]
        payload = json.loads(res["content"][0]["text"])
        self.assertEqual(payload["behavior"], "deny")
        self.assertEqual(payload["message"], "no thanks")

    def test_bad_call_return_is_error_result(self):
        h = McpHandler(lambda: [_tool("echo")], lambda n, a: None)
        res = h.handle(_rpc("tools/call", {"name": "echo", "arguments": {}}))[
            "result"
        ]
        self.assertTrue(res["isError"])
        self.assertEqual(res["content"][0]["type"], "text")

    def test_unlisted_tool_is_refused_without_calling(self):
        calls = []

        def call(name, args):
            calls.append(name)
            return ToolResult(text="ran")

        h = McpHandler(lambda: [_tool("echo")], call)
        res = h.handle(_rpc("tools/call", {"name": "secret", "arguments": {}}))[
            "result"
        ]
        self.assertTrue(res["isError"])
        self.assertIn("Unknown tool: secret", res["content"][0]["text"])
        self.assertEqual(calls, [])
        h.handle(_rpc("tools/call", {"name": "echo", "arguments": {}}))
        self.assertEqual(calls, ["echo"])

    def test_tool_list_is_evaluated_per_request(self):
        listed = [_tool("echo")]
        h = McpHandler(lambda: list(listed), _call)
        args = {"name": "shot", "arguments": {}}
        self.assertTrue(h.handle(_rpc("tools/call", args))["result"]["isError"])
        listed.append(_tool("shot"))
        self.assertFalse(h.handle(_rpc("tools/call", args))["result"]["isError"])

    def test_approve_not_available_without_callback(self):
        res = _handler().handle(
            _rpc(
                "tools/call",
                {"name": APPROVAL_TOOL, "arguments": {"tool_name": "t", "input": {}}},
            )
        )["result"]
        self.assertTrue(res["isError"])
        self.assertIn("Unknown tool", res["content"][0]["text"])


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _post(server, body, token="TOKEN", headers=None):
    hdrs = {"Content-Type": "application/json", "Accept": "application/json"}
    if token is not None:
        hdrs["Authorization"] = f"Bearer {token}"
    hdrs.update(headers or {})
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    req = urllib.request.Request(server.url, data=data, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.server = McpServer(_handler(), token="TOKEN")
        self.port = self.server.start()
        self.addCleanup(self.server.stop)

    def test_url(self):
        self.assertEqual(self.server.url, f"http://127.0.0.1:{self.port}/mcp")

    def test_no_token_401(self):
        status, _, _ = _post(self.server, _rpc("tools/list"), token=None)
        self.assertEqual(status, 401)

    def test_wrong_token_401(self):
        status, _, _ = _post(self.server, _rpc("tools/list"), token="WRONG")
        self.assertEqual(status, 401)

    def test_non_ascii_token_401(self):
        status, _, _ = _post(self.server, _rpc("tools/list"), token="TÖKEN")
        self.assertEqual(status, 401)

    def test_bad_host_403(self):
        status, _, _ = _post(
            self.server, _rpc("tools/list"), headers={"Host": "evil.com"}
        )
        self.assertEqual(status, 403)

    def test_host_with_wrong_port_403(self):
        status, _, _ = _post(
            self.server,
            _rpc("tools/list"),
            headers={"Host": f"127.0.0.1:{self.port + 1}"},
        )
        self.assertEqual(status, 403)

    def test_localhost_host_ok(self):
        status, _, _ = _post(
            self.server,
            _rpc("tools/list"),
            headers={"Host": f"localhost:{self.port}"},
        )
        self.assertEqual(status, 200)

    def test_foreign_origin_403(self):
        status, _, _ = _post(
            self.server,
            _rpc("tools/list"),
            headers={"Origin": "https://evil.com"},
        )
        self.assertEqual(status, 403)

    def test_local_origin_ok(self):
        status, _, _ = _post(
            self.server,
            _rpc("tools/list"),
            headers={"Origin": f"http://localhost:{self.port}"},
        )
        self.assertEqual(status, 200)

    def test_tools_list_200(self):
        status, headers, body = _post(self.server, _rpc("tools/list"))
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers["Content-Type"])
        names = [t["name"] for t in json.loads(body)["result"]["tools"]]
        self.assertEqual(names, ["echo", "shot", "fail", "boom"])

    def test_initialize_sets_session_id(self):
        status, headers, body = _post(
            self.server,
            _rpc("initialize", {"protocolVersion": PROTOCOL_VERSION}),
            headers={"MCP-Protocol-Version": PROTOCOL_VERSION},
        )
        self.assertEqual(status, 200)
        self.assertTrue(headers.get("Mcp-Session-Id"))
        self.assertEqual(
            json.loads(body)["result"]["protocolVersion"], PROTOCOL_VERSION
        )

    def test_notification_202(self):
        status, _, body = _post(
            self.server, _rpc("notifications/initialized", id=None)
        )
        self.assertEqual(status, 202)
        self.assertEqual(body, b"")

    def test_client_response_202(self):
        status, _, body = _post(
            self.server, {"jsonrpc": "2.0", "id": 9, "result": {}}
        )
        self.assertEqual(status, 202)
        self.assertEqual(body, b"")

    def test_malformed_json_parse_error(self):
        status, _, body = _post(self.server, b"{not json")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"]["code"], -32700)

    def test_batch_invalid_request(self):
        status, _, body = _post(self.server, [_rpc("ping")])
        self.assertEqual(json.loads(body)["error"]["code"], -32600)

    def test_unknown_tool_is_error_result(self):
        status, _, body = _post(
            self.server, _rpc("tools/call", {"name": "missing", "arguments": {}})
        )
        self.assertEqual(status, 200)
        result = json.loads(body)["result"]
        self.assertTrue(result["isError"])

    def test_get_405(self):
        req = urllib.request.Request(
            self.server.url, headers={"Authorization": "Bearer TOKEN"}
        )
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 405)
        cm.exception.close()

    def test_wrong_path_404(self):
        req = urllib.request.Request(
            self.server.url.replace("/mcp", "/other"),
            data=b"{}",
            headers={"Authorization": "Bearer TOKEN"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 404)
        cm.exception.close()

    def test_lone_surrogate_roundtrips(self):
        server = McpServer(_handler(lambda n, i: (False, "\ud800")), token="TOKEN")
        server.start()
        self.addCleanup(server.stop)
        body = _rpc(
            "tools/call",
            {"name": APPROVAL_TOOL, "arguments": {"tool_name": "t", "input": {}}},
        )
        status, _, raw = _post(server, body)
        self.assertEqual(status, 200)
        text = json.loads(raw)["result"]["content"][0]["text"]
        self.assertEqual(json.loads(text)["message"], "\ud800")

    def test_unserializable_reply_is_500_with_jsonrpc_error(self):
        bad = Tool("bad", "d", {"x": {1, 2}}, lambda a: ToolResult(""), "read")
        server = McpServer(McpHandler(lambda: [bad], _call), token="TOKEN")
        server.start()
        self.addCleanup(server.stop)
        status, _, raw = _post(server, _rpc("tools/list", id=7))
        self.assertEqual(status, 500)
        payload = json.loads(raw)
        self.assertEqual(payload["error"]["code"], -32603)
        self.assertEqual(payload["id"], 7)

    def test_stop_twice_is_safe(self):
        self.server.stop()
        self.server.stop()
        with self.assertRaises(urllib.error.URLError):
            _post(self.server, _rpc("ping"))


if __name__ == "__main__":
    unittest.main()
