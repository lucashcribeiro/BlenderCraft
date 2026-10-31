"""Saved conversations: dataclasses and a JSON-file store.

One file per conversation (<id>.json), written atomically. This module must
not import bpy.
"""

import json
import logging
import os
import re
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_ROOT = Path.home() / "Library/Application Support/BlenderCraft/conversations"
TITLE_MAX = 60

_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict
    output: str = ""
    status: str = "running"  # running | ok | error | interrupted


@dataclass
class Message:
    role: str  # "user" | "assistant"
    text: str = ""
    tools: list[ToolCall] = field(default_factory=list)


@dataclass
class Conversation:
    id: str
    title: str
    created: float
    updated: float
    blend_path: str
    model: str
    claude_session_id: str | None
    messages: list[Message]


def make_title(text: str) -> str:
    """First user message on one line, cut to TITLE_MAX characters."""
    return " ".join(text.split())[:TITLE_MAX]


def _conversation_from_dict(data: dict) -> Conversation:
    try:
        messages = [
            Message(
                role=m["role"],
                text=m.get("text", ""),
                tools=[ToolCall(**t) for t in m.get("tools", [])],
            )
            for m in data["messages"]
        ]
        return Conversation(
            id=data["id"],
            title=data["title"],
            created=data["created"],
            updated=data["updated"],
            blend_path=data["blend_path"],
            model=data["model"],
            claude_session_id=data.get("claude_session_id"),
            messages=messages,
        )
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"malformed conversation: {exc!r}") from exc


class ConversationStore:
    def __init__(self, root: Path = DEFAULT_ROOT):
        self.root = Path(root)

    def _path(self, conv_id: str) -> Path:
        if not isinstance(conv_id, str) or not _ID_RE.match(conv_id):
            raise ValueError(f"invalid conversation id: {conv_id!r}")
        return self.root / f"{conv_id}.json"

    def new(self, blend_path: str, model: str) -> Conversation:
        now = time.time()
        return Conversation(
            id=uuid.uuid4().hex,
            title="",
            created=now,
            updated=now,
            blend_path=blend_path,
            model=model,
            claude_session_id=None,
            messages=[],
        )

    def save(self, conv: Conversation) -> None:
        if not conv.title:
            first = next((m for m in conv.messages if m.role == "user"), None)
            if first is not None:
                conv.title = make_title(first.text)
        path = self._path(conv.id)
        self.root.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f"{conv.id}-", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(asdict(conv), handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def load(self, conv_id: str) -> Conversation:
        path = self._path(conv_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("conversation file is not a JSON object")
        return _conversation_from_dict(data)

    def list(self, current_blend: str = "") -> list[Conversation]:
        """Conversations of current_blend first, then the rest; each group
        by updated, newest first. Corrupt files are skipped (and logged)."""
        found = []
        if self.root.is_dir():
            for path in self.root.glob("*.json"):
                try:
                    found.append(self.load(path.stem))
                except (OSError, ValueError) as exc:
                    log.warning("Skipping unreadable conversation %s: %s", path, exc)
        found.sort(key=lambda c: (c.blend_path != current_blend, -c.updated))
        return found

    def delete(self, conv_id: str) -> None:
        self._path(conv_id).unlink(missing_ok=True)
