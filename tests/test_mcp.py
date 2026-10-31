import base64
import json
import socket
import threading
import unittest
from unittest import mock
import urllib.error
import urllib.request

from blendercraft_assistant.mcp.protocol import (
    APPROVAL_TOOL,
    PROTOCOL_VERSION,
    McpHandler,
)
from blendercraft_assistant.mcp import server as server_module
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


class ApprovalEnabledTest(unittest.TestCase):
    def _handler(self, enabled):
        state = {"on": enabled}
        handler = McpHandler(
            lambda: [_tool("echo")],
            _call,
            lambda name, args: (True, ""),
            approval_enabled=lambda: state["on"],
        )
        return handler, state

    def _names(self, handler):
        out = handler.handle(_rpc("tools/list"))
        return [t["name"] for t in out["result"]["tools"]]

    def test_listed_and_callable_only_while_enabled(self):
        handler, state = self._handler(False)
        self.assertEqual(self._names(handler), ["echo"])
        call = _rpc(
            "tools/call",
            {"name": APPROVAL_TOOL, "arguments": {"tool_name": "t", "input": {}}},
        )
        self.assertTrue(handler.handle(call)["result"]["isError"])
        state["on"] = True
        self.assertEqual(self._names(handler), ["echo", APPROVAL_TOOL])
        self.assertFalse(handler.handle(call)["result"]["isError"])


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

    def test_approve_raising_denies(self):
        def approve(name, inp):
            raise RuntimeError("dialog crashed")

        res = _handler(approve).handle(
            _rpc(
                "tools/call",
                {"name": APPROVAL_TOOL, "arguments": {"tool_name": "t", "input": {}}},
            )
        )["result"]
        self.assertFalse(res["isError"])
        payload = json.loads(res["content"][0]["text"])
        self.assertEqual(payload["behavior"], "deny")
        self.assertIn("dialog crashed", payload["message"])

    def test_approve_invalid_arguments(self):
        calls = []
        h = _handler(lambda n, i: calls.append(n) or (True, ""))
        for arguments in (
            {},
            {"tool_name": "t"},
            {"tool_name": 5, "input": {}},
            {"tool_name": "t", "input": "x"},
            {"tool_name": "t", "input": [1]},
        ):
            with self.subTest(arguments=arguments):
                res = h.handle(
                    _rpc(
                        "tools/call",
                        {"name": APPROVAL_TOOL, "arguments": arguments},
                    )
                )["result"]
                self.assertTrue(res["isError"])
        self.assertEqual(calls, [])

    def test_non_dict_params(self):
        for method in ("tools/list", "tools/call", "ping"):
            with self.subTest(method=method):
                out = _handler().handle(_rpc(method, [1, 2]))
                self.assertEqual(out["error"]["code"], -32602)

    def test_non_dict_arguments(self):
        out = _handler().handle(
            _rpc("tools/call", {"name": "echo", "arguments": [1]})
        )
        self.assertEqual(out["error"]["code"], -32602)

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

    def test_empty_token_rejected(self):
        with self.assertRaises(ValueError):
            McpServer(_handler(), token="")

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
        self.assertEqual(status, 400)
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

    def _raw_status(self, header_lines, token="TOKEN"):
        head = [
            "POST /mcp HTTP/1.1",
            f"Host: 127.0.0.1:{self.port}",
            f"Authorization: Bearer {token}",
            *header_lines,
            "",
            "",
        ]
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as c:
            c.sendall("\r\n".join(head).encode())
            return int(c.recv(1024).split(b" ", 2)[1])

    def test_oversized_body_413(self):
        self.assertEqual(self._raw_status(["Content-Length: 999999999999"]), 413)

    def test_missing_content_length_is_parse_error(self):
        self.assertEqual(self._raw_status([]), 400)

    def test_invalid_content_length_400(self):
        for value in ("abc", "-5"):
            with self.subTest(value=value):
                self.assertEqual(
                    self._raw_status([f"Content-Length: {value}"]), 400
                )

    def test_non_bearer_scheme_401(self):
        for auth in ("Basic TOKEN", "TOKEN", "Bearer", "Token TOKEN"):
            with self.subTest(auth=auth):
                status, _, _ = _post(
                    self.server,
                    _rpc("tools/list"),
                    token=None,
                    headers={"Authorization": auth},
                )
                self.assertEqual(status, 401)

    def test_put_and_delete_405(self):
        for method in ("PUT", "DELETE"):
            with self.subTest(method=method):
                req = urllib.request.Request(
                    self.server.url,
                    data=b"{}" if method == "PUT" else None,
                    headers={"Authorization": "Bearer TOKEN"},
                    method=method,
                )
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    urllib.request.urlopen(req, timeout=5)
                self.assertEqual(cm.exception.code, 405)
                cm.exception.close()

    def test_blocked_approve_does_not_block_other_requests(self):
        entered, release = threading.Event(), threading.Event()

        def approve(name, inp):
            entered.set()
            release.wait(5)
            return True, ""

        server = McpServer(_handler(approve), token="TOKEN")
        server.start()
        self.addCleanup(server.stop)
        self.addCleanup(release.set)
        slow = {}

        def slow_request():
            slow["result"] = _post(
                server,
                _rpc(
                    "tools/call",
                    {
                        "name": APPROVAL_TOOL,
                        "arguments": {"tool_name": "t", "input": {}},
                    },
                ),
            )

        t = threading.Thread(target=slow_request)
        t.start()
        self.assertTrue(entered.wait(5))
        status, _, _ = _post(server, _rpc("tools/list"))
        self.assertEqual(status, 200)
        self.assertTrue(t.is_alive())  # the approval is still pending
        release.set()
        t.join(5)
        self.assertEqual(slow["result"][0], 200)

    def test_default_socket_timeout_is_set(self):
        self.assertEqual(server_module._RequestHandler.timeout, 30)

    def test_stalled_client_is_dropped(self):
        with mock.patch.object(server_module._RequestHandler, "timeout", 0.2):
            with socket.create_connection(("127.0.0.1", self.port), timeout=5) as c:
                c.sendall(b"POST /mcp HTTP/1.1\r\nHost: x\r\n")  # never finishes
                # The server closes the connection once its read times out.
                self.assertEqual(c.recv(1024), b"")

    def test_stop_twice_is_safe(self):
        self.server.stop()
        self.server.stop()
        with self.assertRaises(urllib.error.URLError):
            _post(self.server, _rpc("ping"))


if __name__ == "__main__":
    unittest.main()
