"""The extension's own log file (no bpy: plain `logging`).

BlenderCraft.command appends Blender's stdout/stderr to blendercraft.log in
~/Library/Logs/BlenderCraft; this module writes what the extension itself
reports (runtime start/stop, tool errors with tracebacks, Claude process
exits) to extension.log in the same folder, through the standard `logging`
package: every module logs with logging.getLogger(__name__) and ensure()
attaches one rotating file handler to the package logger, the first time
something needs it (the file is only created when the first record is
written).

Tokens and API keys never reach the file: redact() masks bearer tokens,
key-looking values, `token=...` style pairs and every secret registered with
add_secret() (the MCP token of the session).

BLENDERCRAFT_LOG_DIR overrides the folder (tests).
"""

import logging
import logging.handlers
import os
import re
import threading
from pathlib import Path

LOG_FILE = "extension.log"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 2
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

_lock = threading.Lock()
_handler: logging.Handler | None = None
_secrets: set[str] = set()

_PATTERNS = (
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{6,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\btsk_[A-Za-z0-9_-]{8,}"),
    re.compile(
        r"(?i)([\"']?(?:api[_-]?key|token|authorization|password|passwd|secret)[\"']?"
        r"\s*[=:]\s*)[\"']?[^\s\"',;&}]+[\"']?"
    ),
)


def log_dir() -> Path:
    override = os.environ.get("BLENDERCRAFT_LOG_DIR")
    if override:
        return Path(override)
    return Path.home() / "Library" / "Logs" / "BlenderCraft"


def add_secret(value: str) -> None:
    """Mask this exact string from now on (ignored when too short to be one)."""
    if isinstance(value, str) and len(value) >= 6:
        _secrets.add(value)


def redact(text: str) -> str:
    for secret in _secrets:
        text = text.replace(secret, "[redacted]")
    for pattern in _PATTERNS[:3]:
        text = pattern.sub("[redacted]", text)
    return _PATTERNS[3].sub(lambda m: f"{m.group(1)}[redacted]", text)


class _RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def _package_logger() -> logging.Logger:
    return logging.getLogger(__package__)


def ensure() -> bool:
    """Attach the file handler for the current log folder (once). Returns
    False if the folder is not writable: logging then does nothing, it never
    raises."""
    global _handler
    wanted = log_dir() / LOG_FILE
    with _lock:
        current = _handler
        if current is not None and getattr(current, "baseFilename", None) == str(wanted):
            return True
        logger = _package_logger()
        if current is not None:
            logger.removeHandler(current)
            current.close()
            _handler = None
        try:
            wanted.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(
                wanted, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT,
                encoding="utf-8", delay=True,
            )
        except OSError:
            return False
        handler.setFormatter(_RedactingFormatter(FORMAT))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        _handler = handler
        return True


def shutdown() -> None:
    """Detach and close the file handler (add-on unregistered)."""
    global _handler
    with _lock:
        if _handler is not None:
            _package_logger().removeHandler(_handler)
            _handler.close()
            _handler = None


def get(name: str) -> logging.Logger:
    """Logger for a module, making sure the file handler exists."""
    ensure()
    return logging.getLogger(name)
