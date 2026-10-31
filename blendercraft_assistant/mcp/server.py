"""Local MCP server: streamable HTTP transport (spec 2025-06-18).

Single endpoint POST /mcp, one JSON-RPC message per request, replies as
application/json (no SSE). Listens on 127.0.0.1 only and requires a bearer
token. This module must not import bpy: scripts/record_fixture.py imports
it under the system python3.
"""

import hmac
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .protocol import McpHandler, error_response

_MAX_BODY = 16 * 1024 * 1024
_ENDPOINT = "/mcp"


class _HttpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler_cls, mcp_handler, token):
        super().__init__(address, handler_cls)
        self.mcp_handler = mcp_handler
        self.token = token.encode("utf-8")

    @property
    def local_port(self) -> int:
        return self.server_address[1]


class _RequestHandler(BaseHTTPRequestHandler):
    server: _HttpServer
    server_version = "BlenderCraftMCP"
    # Socket reads only: a stalled client must not pin a thread forever.
    # It does not limit a handler blocked in approve.
    timeout = 30

    def log_message(self, format, *args):  # keep stderr clean
        pass

    # --- helpers -------------------------------------------------------

    def _send(self, status: int, body: bytes = b"", headers: dict | None = None):
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_json(self, status: int, payload: dict, headers: dict | None = None):
        try:
            # ASCII-only output cannot fail on lone surrogates in tool text.
            body = json.dumps(payload).encode("ascii")
        except (TypeError, ValueError) as exc:
            status = 500
            body = json.dumps(
                error_response(
                    payload.get("id"), -32603, f"Could not serialize reply: {exc}"
                )
            ).encode("ascii")
        merged = {"Content-Type": "application/json"}
        merged.update(headers or {})
        self._send(status, body, merged)

    def _allowed_hosts(self) -> set[str]:
        port = self.server.local_port
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _check_request(self) -> bool:
        """Host, Origin and token checks. Sends the error and returns False."""
        allowed = self._allowed_hosts()
        if self.headers.get("Host", "") not in allowed:
            self._send(403)
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {f"http://{h}" for h in allowed}:
            self._send(403)
            return False
        auth = self.headers.get("Authorization", "")
        scheme, _, supplied = auth.partition(" ")
        ok = scheme.lower() == "bearer" and hmac.compare_digest(
            supplied.strip().encode("utf-8"), self.server.token
        )
        if not ok:
            self._send(401, headers={"WWW-Authenticate": "Bearer"})
            return False
        return True

    # --- verbs ---------------------------------------------------------

    def do_POST(self):
        if not self._check_request():
            return
        if self.path.split("?", 1)[0] != _ENDPOINT:
            self._send(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > _MAX_BODY:
            self._send(413 if length > _MAX_BODY else 400)
            return
        raw = self.rfile.read(length)
        try:
            message = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._send_json(400, error_response(None, -32700, "Parse error"))
            return
        if not isinstance(message, dict):  # batches are not supported
            self._send_json(400, error_response(None, -32600, "Invalid request"))
            return
        reply = self.server.mcp_handler.handle(message)
        if reply is None:
            self._send(202)
            return
        headers = {}
        # Issued for clients that want one; intentionally never validated.
        if message.get("method") == "initialize" and "error" not in reply:
            headers["Mcp-Session-Id"] = secrets.token_hex(16)
        self._send_json(200, reply, headers)

    def _method_not_allowed(self):
        if self._check_request():
            self._send(405, headers={"Allow": "POST"})

    do_GET = do_PUT = do_DELETE = do_PATCH = _method_not_allowed


class McpServer:
    def __init__(self, handler: McpHandler, token: str, port: int = 0):
        if not token:
            raise ValueError("MCP server requires a non-empty token")
        self._handler = handler
        self._token = token
        self._port = port
        self._httpd: _HttpServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> int:
        if self._httpd is not None:
            raise RuntimeError("MCP server already started")
        httpd = _HttpServer(
            ("127.0.0.1", self._port), _RequestHandler, self._handler, self._token
        )
        self._httpd = httpd
        self._port = httpd.local_port
        self._thread = threading.Thread(
            target=httpd.serve_forever,
            kwargs={"poll_interval": 0.05},  # keeps stop() quick
            name="blendercraft-mcp",
            daemon=True,
        )
        self._thread.start()
        return self._port

    def stop(self) -> None:
        httpd, thread = self._httpd, self._thread
        self._httpd = self._thread = None
        if httpd is None:
            return
        httpd.shutdown()
        httpd.server_close()
        if thread is not None:
            thread.join(timeout=5)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}/mcp"
