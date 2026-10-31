"""Common provider interface: requests, results, errors, HTTP transport and
the polling state machine.

No bpy here: everything runs on worker threads. Every wait goes through
``wait(seconds, cancel)`` so a cancel (threading.Event) stops the job
between polls, and tests can replace the clock.
"""

import base64
import json
import os
import secrets
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

# Images accepted as input, by extension.
IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
MAX_IMAGE_BYTES = 20 * 1024 * 1024  # both providers: 20 MB per image
MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024  # sanity cap for a model file
_CHUNK = 256 * 1024

# progress(stage, status, percent): stage is "upload", "generate", "texture",
# "repair" or "download"; percent is 0-100 or None when unknown.
ProgressFn = Callable[[str, str, "int | None"], None]
# wait(seconds, cancel) -> True when cancelled during the wait.
WaitFn = Callable[[float, threading.Event], bool]


class GenerationError(Exception):
    """A generation that cannot finish. ``code`` classifies it for the tool:
    auth, credits, bad_request, not_found, rate_limit, server, network,
    failed, timeout, input."""

    def __init__(self, message: str, code: str = "failed"):
        super().__init__(message)
        self.code = code


class Cancelled(Exception):
    """The job was cancelled (user Stop, new conversation, add-on off)."""


@dataclass
class ImageInput:
    name: str
    data: bytes
    mime: str

    def data_uri(self) -> str:
        encoded = base64.b64encode(self.data).decode("ascii")
        return f"data:{self.mime};base64,{encoded}"


def load_image(path: str | os.PathLike) -> ImageInput:
    """Read an input image, checking its type and size."""
    p = Path(path)
    mime = IMAGE_TYPES.get(p.suffix.lower())
    if mime is None:
        raise GenerationError(
            f"Unsupported image type {p.suffix!r} ({p.name}); use PNG, JPEG or WebP",
            "input",
        )
    if not p.is_file():
        raise GenerationError(f"Image not found: {p}", "input")
    size = p.stat().st_size
    if size > MAX_IMAGE_BYTES:
        raise GenerationError(f"Image over 20 MB: {p.name} ({size} bytes)", "input")
    return ImageInput(p.name, p.read_bytes(), mime)


@dataclass
class GenerationRequest:
    kind: str  # "text" | "image" | "multiview"
    prompt: str = ""
    images: list[ImageInput] = field(default_factory=list)
    negative_prompt: str = ""
    quality: str = "standard"  # "standard" | "high"
    texture: bool = True
    target_height_mm: float = 0.0
    print_repair: bool = False  # Meshy only
    out_dir: Path = Path(".")


@dataclass
class GeneratedModel:
    path: Path  # GLB when available (colours), else STL
    files: dict[str, Path] = field(default_factory=dict)  # format -> path
    meta: dict = field(default_factory=dict)


# ---- HTTP transport ---------------------------------------------------


class TransportError(Exception):
    """Network failure or timeout (no HTTP status)."""


@dataclass
class Response:
    status: int
    headers: dict[str, str]  # lower-case keys
    body: bytes

    def json(self):
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise GenerationError(
                f"Unreadable reply (HTTP {self.status}): {exc}", "server"
            ) from None


class UrllibTransport:
    """Real transport (stdlib urllib). Never raises on HTTP status codes."""

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None = None,
        timeout: float = 30.0,
    ) -> Response:
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return Response(resp.status, _lower(resp.headers), resp.read())
        except urllib.error.HTTPError as exc:
            try:
                data = exc.read()
            except OSError:
                data = b""
            return Response(exc.code, _lower(exc.headers or {}), data)
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise TransportError(f"{type(exc).__name__}: {reason}") from None

    def download(
        self, url: str, path: Path, cancel: threading.Event, timeout: float = 120.0
    ) -> int:
        """Stream url to path (atomically). Returns the byte count."""
        if not url.startswith("https://"):
            raise GenerationError(f"Refusing a non-HTTPS download URL: {url[:80]}", "server")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".download-")
        total = 0
        try:
            with os.fdopen(fd, "wb") as out:
                req = urllib.request.Request(url, method="GET")
                try:
                    resp = urllib.request.urlopen(req, timeout=timeout)
                except urllib.error.HTTPError as exc:
                    raise GenerationError(
                        f"Download failed: HTTP {exc.code}", "server"
                    ) from None
                except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
                    raise TransportError(f"Download failed: {exc}") from None
                with resp:
                    while True:
                        if cancel.is_set():
                            raise Cancelled()
                        chunk = resp.read(_CHUNK)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_DOWNLOAD_BYTES:
                            raise GenerationError("Download larger than 1 GB", "server")
                        out.write(chunk)
            os.replace(tmp, path)
            tmp = ""
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        return total


def _lower(headers) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in dict(headers).items()}


def build_multipart(
    fields: list[tuple[str, str, str, bytes]], boundary: str | None = None
) -> tuple[bytes, str]:
    """multipart/form-data body for file fields (name, filename, type, data).

    Returns (body, Content-Type header value)."""
    boundary = boundary or f"blendercraft-{secrets.token_hex(16)}"
    parts: list[bytes] = []
    for name, filename, content_type, data in fields:
        safe = filename.replace('"', "_").replace("\r", "_").replace("\n", "_")
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"; filename="{safe}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8")
            + data
            + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def retry_after(headers: dict[str, str]) -> float | None:
    """Seconds from a Retry-After header (numeric form), clamped to 1-60."""
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        value = float(raw.strip())
    except ValueError:
        return None
    return min(60.0, max(1.0, value))


def scrub(text: str, secret_values) -> str:
    """text with every non-empty secret replaced by ***."""
    for value in secret_values:
        if value:
            text = text.replace(value, "***")
    return text


def default_wait(seconds: float, cancel: threading.Event) -> bool:
    return cancel.wait(seconds)


# ---- provider base ----------------------------------------------------

_HTTP_CODES = {
    400: "bad_request",
    401: "auth",
    402: "credits",
    403: "auth",
    404: "not_found",
    422: "bad_request",
    429: "rate_limit",
}


def http_error(resp: Response, provider: str) -> GenerationError:
    detail = ""
    try:
        data = json.loads(resp.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        data = None
    if isinstance(data, dict):
        detail = str(data.get("message") or data.get("error") or "")
    if not detail:
        detail = resp.body[:200].decode("utf-8", "replace")
    code = _HTTP_CODES.get(resp.status, "server" if resp.status >= 500 else "failed")
    return GenerationError(f"{provider}: HTTP {resp.status}: {detail[:300]}", code)


@dataclass
class PollState:
    status: str
    progress: int | None = None
    done: bool = False
    failed: bool = False
    error: str = ""
    payload: dict = field(default_factory=dict)


class Provider:
    """Base for the providers. Subclasses implement generate()."""

    name = ""
    poll_interval = 5.0  # seconds, when the server gives no Retry-After
    max_wait = 3600.0  # seconds before a task is given up
    create_retries = 3  # retries of a create request rejected with 429

    def __init__(
        self,
        api_key: str,
        transport=None,
        wait: WaitFn | None = None,
        clock: Callable[[], float] | None = None,
        poll_interval: float | None = None,
        max_wait: float | None = None,
    ):
        if not api_key:
            raise GenerationError(f"No API key for {self.name}", "auth")
        self.api_key = api_key
        self.transport = transport or UrllibTransport()
        self.wait = wait or default_wait
        self.clock = clock or time.monotonic
        if poll_interval is not None:
            self.poll_interval = poll_interval
        if max_wait is not None:
            self.max_wait = max_wait

    def generate(
        self,
        request: GenerationRequest,
        cancel: threading.Event,
        progress: ProgressFn,
    ) -> GeneratedModel:
        raise NotImplementedError

    # -- HTTP helpers --

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def _send(self, method, url, body=None, content_type="application/json", timeout=30.0):
        headers = self._auth()
        if body is not None:
            headers["Content-Type"] = content_type
        try:
            return self.transport.request(method, url, headers, body, timeout)
        except TransportError as exc:
            raise GenerationError(f"{self.name}: network error: {exc}", "network") from None

    def _create(self, url, body, cancel, content_type="application/json", timeout=60.0):
        """POST a create request. Only 429 is retried: a timed-out or failed
        create may still have started a paid task."""
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        for attempt in range(self.create_retries + 1):
            if cancel.is_set():
                raise Cancelled()
            resp = self._send("POST", url, body, content_type, timeout)
            if resp.status == 429 and attempt < self.create_retries:
                delay = retry_after(resp.headers) or self.poll_interval
                if self.wait(delay, cancel):
                    raise Cancelled()
                continue
            if resp.status >= 400:
                raise http_error(resp, self.name)
            return resp.json()
        raise GenerationError(f"{self.name}: rate limited", "rate_limit")  # pragma: no cover

    def _poll(
        self,
        url: str,
        interpret: Callable[[object], PollState],
        cancel: threading.Event,
        on_progress: Callable[[PollState], None],
    ) -> dict:
        """Poll url until interpret() says done (returns its payload) or
        failed (raises). Timeouts, 429 and 5xx are transient: the task is
        still running. Honour Retry-After, else wait poll_interval."""
        start = self.clock()
        while True:
            if cancel.is_set():
                raise Cancelled()
            delay = self.poll_interval
            try:
                resp = self.transport.request("GET", url, self._auth(), None, 30.0)
            except TransportError:
                resp = None
            if resp is not None:
                hinted = retry_after(resp.headers)
                if hinted is not None:
                    delay = hinted
                if resp.status == 429 or resp.status >= 500:
                    pass  # transient: poll again
                elif resp.status >= 400:
                    raise http_error(resp, self.name)
                else:
                    state = interpret(resp.json())
                    on_progress(state)
                    if state.failed:
                        raise GenerationError(
                            f"{self.name}: generation failed: {state.error or state.status}",
                            "failed",
                        )
                    if state.done:
                        return state.payload
            if self.clock() - start > self.max_wait:
                raise GenerationError(
                    f"{self.name}: task not finished after {int(self.max_wait)} s",
                    "timeout",
                )
            if self.wait(delay, cancel):
                raise Cancelled()

    def _download(self, url: str, path: Path, cancel: threading.Event) -> Path:
        try:
            self.transport.download(url, path, cancel)
        except TransportError as exc:
            raise GenerationError(f"{self.name}: {exc}", "network") from None
        return path
