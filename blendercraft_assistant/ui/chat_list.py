"""The conversation as a fixed-height, scrollable list (a UIList).

Blender panels grow with their content, so a long conversation would push
the input card off the sidebar. Instead the messages are flattened into
display rows (wrapped text lines, tool rows, details) kept in
WindowManager.bc.chat_lines and drawn by BC_UL_chat with its own
scrollbar. Rows are rebuilt only when what they show changes.

Follow-bottom: the UIList scrolls to the active row when the number of
rows changes, so the active row is kept on the last row (an empty END
row) while the agent works or while the user has not picked an older row.
"""

import json

from bpy.types import UIList

from .. import activity, runtime
from ..i18n import lang, t
from . import markdown
from .text_wrap import CHAR_PX, wrap

MESSAGES_PER_PAGE = 40  # messages per "show older" page
DETAIL_CHARS = 600  # tool input/output shown under Details
SUMMARY_CHARS = 60
LIST_MARGIN_PX = 70  # panel padding, row padding and the list scrollbar
ROW_PX = 20  # height of a list row at ui_scale 1
RESERVED_ROWS = 14  # header, model, footer, input card and subpanels
MIN_ROWS = 8
MAX_ROWS = 60

CELL_SEP = "\x1f"  # between the cells of a TABLE / TABLE_HEAD row

TOOL_ICONS = {
    "running": "SORTTIME",
    "ok": "CHECKMARK",
    "error": "ERROR",
    "interrupted": "CANCEL",
}


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[:limit]}…"


def tool_row_text(tool) -> str:
    """Row text of a call: a friendly label, then the model's description
    ("Executando Python — Corta o furo da perna"). A tool without a label
    keeps its raw name and shows its first argument if it has no description."""
    data = tool.input if isinstance(tool.input, dict) else {}
    description = data.get("description")
    description = " ".join(description.split()) if isinstance(description, str) else ""
    if not activity.knows(tool.name):
        summary = tool_summary(tool)
        return f"{tool.name}: {summary}" if summary else tool.name
    label = activity.tool_label(tool.name, data)
    return f"{label} — {_clip(description, SUMMARY_CHARS)}" if description else label


def tool_summary(tool) -> str:
    """One-line summary of a call: its description, else its first argument."""
    data = tool.input if isinstance(tool.input, dict) else {}
    value = data.get("description")
    if not isinstance(value, str) or not value:
        value = next((v for v in data.values() if isinstance(v, (str, int, float))), "")
    return _clip(" ".join(str(value).split()), SUMMARY_CHARS)


def _chars(width_px: int, scale: float) -> int:
    return max(10, int(width_px / (CHAR_PX * (scale if scale > 0 else 1.0))))


def _text_rows(text: str, user: bool, width_px: int, scale: float) -> list[tuple]:
    """Rows for a message's Markdown (the same for both sides)."""
    para_kind = "USER" if user else "TEXT"
    rows = []
    for block in markdown.parse(text):
        kind = block[0]
        if kind == "blank":
            rows.append((para_kind, "", "", ""))
        elif kind == "para":
            rows += [(para_kind, line, "", "") for line in wrap(block[1], width_px, scale)]
        elif kind == "heading":
            level = str(block[1])
            rows += [("HEADING", line, "", level) for line in wrap(block[2], width_px, scale)]
        elif kind == "item":
            _, depth, marker, item = block
            indent = "    " * depth
            first = f"{indent}{marker} "
            hang = " " * len(first)
            lines = wrap(item, max(0, width_px - len(first) * CHAR_PX * (scale or 1.0)), scale) or [""]
            rows.append(("BULLET", first + lines[0], "", ""))
            rows += [("BULLET", hang + line, "", "") for line in lines[1:]]
        elif kind == "code":
            line = block[1]
            size = _chars(width_px, scale)
            chunks = [line[i : i + size] for i in range(0, len(line), size)] or [""]
            rows += [("CODE", chunk, "", "") for chunk in chunks]
        elif kind == "rule":
            rows.append(("RULE", "─" * max(3, _chars(width_px, scale) // 2), "", ""))
        elif kind == "table":
            _, header, body = block
            fractions = ",".join(f"{f:.4f}" for f in markdown.column_fractions(header, body))
            rows.append(("TABLE_HEAD", CELL_SEP.join(header), "", fractions))
            rows += [("TABLE", CELL_SEP.join(cells), "", fractions) for cells in body]
    return rows


def flatten(messages, error: str, expanded_tool: str, width_px: int, scale: float):
    """Display rows (kind, text, tool_id, status) for messages.

    Kinds: USER_HEAD, CLAUDE_HEAD, USER / TEXT (a line of the user's or
    Claude's prose), HEADING, BULLET, CODE, RULE, TABLE_HEAD, TABLE, TOOL,
    DETAIL, ERROR, GAP (blank line between messages) and a final END row.
    The last field depends on the kind: a tool's status, a heading's level,
    a table's column fractions ("0.2,0.8").
    """
    text_width = max(0, int(width_px - LIST_MARGIN_PX * (scale if scale > 0 else 1.0)))
    rows = []
    for index, message in enumerate(messages):
        if index:
            rows.append(("GAP", "", "", ""))
        user = message.role == "user"
        rows.append(("USER_HEAD", t("you"), "", "") if user else ("CLAUDE_HEAD", t("claude"), "", ""))
        rows += _text_rows(message.text, user, text_width, scale)
        for tool in message.tools:
            rows.append(("TOOL", tool_row_text(tool), tool.id, tool.status))
            if tool.id == expanded_tool:
                details = "→ " + _clip(json.dumps(tool.input, ensure_ascii=False, indent=1), DETAIL_CHARS)
                if tool.output:
                    details += "\n← " + _clip(tool.output, DETAIL_CHARS)
                rows += [("DETAIL", line, tool.id, "") for line in wrap(details, text_width, scale)]
    if error:
        rows += [("ERROR", line, "", "") for line in wrap(error, text_width, scale)]
    rows.append(("END", "", "", ""))
    return rows


def visible_rows(region_height: float, scale: float, extra_rows: int = 0) -> int:
    """List height in rows: what the region fits besides the rest of the
    panel (RESERVED_ROWS, plus extra_rows for optional blocks)."""
    scale = scale if scale > 0 else 1.0
    rows = int(region_height / (ROW_PX * scale)) - RESERVED_ROWS - extra_rows
    return max(MIN_ROWS, min(MAX_ROWS, rows))


def _shown(session, props) -> list:
    if session is None:
        return []
    messages = session.conversation.messages
    return messages[-MESSAGES_PER_PAGE * (1 + props.show_older) :]


def has_older(session, props) -> bool:
    if session is None:
        return False
    return len(session.conversation.messages) > MESSAGES_PER_PAGE * (1 + props.show_older)


def _signature(session, props, width_px: int, scale: float):
    if session is None:
        return None
    shown = _shown(session, props)
    expanded = props.expanded_tool
    return (
        session.conversation.id,
        len(session.conversation.messages),
        tuple(
            (
                m.role,
                m.text,
                tuple(
                    (tc.id, tc.name, tc.status, tc.output if tc.id == expanded else "")
                    for tc in m.tools
                ),
            )
            for m in shown
        ),
        session.error,
        expanded,
        props.show_older,
        int(width_px),
        scale,
        lang(),
    )


class _State:
    signature = None
    rows: list = []
    conv_id = None


_state = _State()


def reset() -> None:
    """Forget what was built (the next sync rewrites every row)."""
    _state.signature = None
    _state.rows = []
    _state.conv_id = None


def sync(props, session, width_px: int, scale: float) -> bool:
    """Rebuild props.chat_lines if the conversation changed; True if it did.

    Only rows that differ are written. The active row follows the bottom
    while the agent works, when another conversation is shown, and when it
    already was the last row; otherwise the row the user picked stays.
    """
    lines = props.chat_lines
    signature = _signature(session, props, width_px, scale)
    if signature == _state.signature and len(lines) == len(_state.rows) and len(lines):
        return False
    shown = _shown(session, props)
    rows = flatten(
        shown,
        session.error if session is not None else "",
        props.expanded_tool,
        width_px,
        scale,
    )
    conv_id = session.conversation.id if session is not None else None
    old_len = len(lines)
    follow = (
        props.chat_index >= old_len - 1
        or conv_id != _state.conv_id
        or (session is not None and session.status == "WORKING")
    )
    previous = _state.rows if len(_state.rows) == old_len else []
    while len(lines) > len(rows):
        lines.remove(len(lines) - 1)
    for index, row in enumerate(rows):
        if index < len(previous) and previous[index] == row:
            continue
        line = lines[index] if index < len(lines) else lines.add()
        line.kind, line.text, line.tool_id, line.status = row
    _state.signature = signature
    _state.rows = rows
    _state.conv_id = conv_id
    last = len(rows) - 1
    if follow:
        props.chat_index = last
    elif props.chat_index > last:
        props.chat_index = last
    return True


class BC_UL_chat(UIList):
    """One conversation row: a header, a text line, a tool call or a detail."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0, flt_flag=0):
        kind = item.kind
        if kind == "USER_HEAD":
            layout.label(text=item.text, icon="USER")
        elif kind == "CLAUDE_HEAD":
            layout.label(text=item.text, icon="LIGHT")
        elif kind == "USER":
            layout.label(text=item.text, icon="BLANK1")  # indented under "You"
        elif kind in ("TEXT", "BULLET"):
            layout.label(text=item.text)
        elif kind == "HEADING":
            layout.label(text=item.text, icon="TRIA_RIGHT" if item.status in ("1", "2") else "DOT")
        elif kind == "CODE":
            box = layout.box()
            box.scale_y = 0.6
            box.label(text=item.text)
        elif kind == "RULE":
            row = layout.row()
            row.active = False
            row.label(text=item.text)
        elif kind in ("TABLE_HEAD", "TABLE"):
            self._draw_table_row(layout, item)
        elif kind == "TOOL":
            self._draw_tool(layout, data, item)
        elif kind == "DETAIL":
            row = layout.row()
            row.active = False
            row.label(text=item.text, icon="BLANK1")
        elif kind == "ERROR":
            row = layout.row()
            row.alert = True
            row.label(text=item.text)
        elif kind == "END":
            # The active row (highlighted): it says when the agent works.
            session = runtime.session
            if session is not None and session.status == "WORKING":
                layout.label(text=activity.working_text(session), icon="TIME")
            else:
                layout.label(text="")
        else:  # GAP
            layout.label(text="")

    @staticmethod
    def _draw_tool(layout, data, item):
        expanded = data.expanded_tool == item.tool_id
        row = layout.row(align=True)
        row.label(text=item.text, icon=TOOL_ICONS.get(item.status, "QUESTION"))
        side = row.row(align=True)
        side.alignment = "RIGHT"
        side.active = False
        side.label(text=t(f"tool_{item.status}"))
        op = side.operator(
            "bc.toggle_details",
            text=t("details"),
            icon="DISCLOSURE_TRI_DOWN" if expanded else "DISCLOSURE_TRI_RIGHT",
            emboss=False,
        )
        op.tool_id = item.tool_id

    @staticmethod
    def _draw_table_row(layout, item):
        cells = item.text.split(CELL_SEP)
        try:
            fractions = [float(f) for f in item.status.split(",")]
        except ValueError:
            fractions = []
        if len(fractions) != len(cells):
            fractions = [1.0 / len(cells)] * len(cells)
        # The header sits in a box; body cells are plain labels.
        holder = layout.box() if item.kind == "TABLE_HEAD" else layout
        if item.kind == "TABLE_HEAD":
            holder.scale_y = 0.6
        remaining = 1.0
        current = holder
        for cell, share in zip(cells[:-1], fractions[:-1]):
            # A split's factor applies to what is left of the row.
            split = current.split(factor=min(1.0, share / remaining) if remaining > 0 else 0.5)
            split.label(text=cell)
            current = split
            remaining -= share
        current.label(text=cells[-1])

    def draw_filter(self, context, layout):
        pass  # no filter or sort options for a conversation
