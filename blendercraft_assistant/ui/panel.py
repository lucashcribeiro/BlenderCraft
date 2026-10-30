"""The Assistant tab in the 3D View sidebar."""

import json

import bpy
from bpy.types import Panel

from .. import runtime
from ..i18n import t
from ..units import get_display_unit
from . import operators
from .text_wrap import strip_markdown, wrap

RECENT_MESSAGES = 6  # messages shown per "show older" page
DETAIL_CHARS = 600  # tool input/output shown under Details
SUMMARY_CHARS = 40
MAX_CONVERSATIONS = 30
_MARGIN_PX = 40  # panel padding left out of the text width

_STATUS = {
    "IDLE": ("idle", "CHECKMARK"),
    "WORKING": ("working", "SORTTIME"),
    "ERROR": ("error", "ERROR"),
}
_TOOL_ICONS = {
    "running": "SORTTIME",
    "ok": "CHECKMARK",
    "error": "ERROR",
    "interrupted": "CANCEL",
}
_UNIT_LABELS = {"MM": "mm", "CM": "cm", "M": "m", "IN": "in", "FT": "ft"}


def _text_lines(layout, context, text: str, alert: bool = False) -> None:
    """Labels for text wrapped to the panel width."""
    scale = context.preferences.system.ui_scale or 1.0
    region = context.region
    width = (region.width if region is not None else 300) - _MARGIN_PX * scale
    col = layout.column(align=True)
    col.scale_y = 0.8
    col.alert = alert
    for line in wrap(text, int(width), scale):
        col.label(text=line)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[:limit]}…"


def _tool_summary(tool) -> str:
    """One-line summary of a call: its description, else its first argument."""
    data = tool.input if isinstance(tool.input, dict) else {}
    value = data.get("description")
    if not isinstance(value, str) or not value:
        value = next((v for v in data.values() if isinstance(v, (str, int, float))), "")
    return _clip(" ".join(str(value).split()), SUMMARY_CHARS)


def _draw_approval(layout, context) -> None:
    pending = runtime.pending_approval
    if pending is None:
        return
    name, tool_input = pending[0], pending[1]
    box = layout.box()
    box.label(text=t("approval_request", tool=name), icon="QUESTION")
    details = json.dumps(tool_input, ensure_ascii=False, indent=1)
    _text_lines(box, context, _clip(details, DETAIL_CHARS))
    row = box.row(align=True)
    row.scale_y = 1.3
    op = row.operator("bc.approve", text=t("approve"), icon="CHECKMARK")
    op.allow = True
    op = row.operator("bc.approve", text=t("deny"), icon="X")
    op.allow = False


def _draw_claude_problem(layout, context) -> None:
    problem = runtime.claude_problem
    if not problem:
        return
    box = layout.box()
    _text_lines(box, context, t("claude_login" if problem == "login" else "claude_missing"), alert=True)


def _draw_tool(layout, context, tool, props) -> None:
    expanded = props.expanded_tool == tool.id
    row = layout.row(align=True)
    summary = _tool_summary(tool)
    row.label(
        text=f"{tool.name}: {summary}" if summary else tool.name,
        icon=_TOOL_ICONS.get(tool.status, "QUESTION"),
    )
    # Icon only (tooltip "Details"): a text button would squeeze the name.
    op = row.operator(
        "bc.toggle_details",
        text="",
        icon="DISCLOSURE_TRI_DOWN" if expanded else "DISCLOSURE_TRI_RIGHT",
        emboss=False,
    )
    op.tool_id = tool.id
    if expanded:
        box = layout.box()
        tool_input = json.dumps(tool.input, ensure_ascii=False, indent=1)
        _text_lines(box, context, "→ " + _clip(tool_input, DETAIL_CHARS))
        if tool.output:
            _text_lines(box, context, "← " + _clip(tool.output, DETAIL_CHARS))


def _draw_conversation(layout, context, session, props) -> None:
    messages = session.conversation.messages if session is not None else []
    shown = RECENT_MESSAGES * (1 + props.show_older)
    if len(messages) > shown:
        op = layout.operator("wm.context_set_int", text=t("show_older"), icon="TRIA_UP")
        op.data_path = "window_manager.bc.show_older"
        op.value = props.show_older + 1
    for message in messages[-shown:]:
        box = layout.box()
        user = message.role == "user"
        box.label(text=t("you") if user else t("claude"), icon="USER" if user else "LIGHT")
        if message.text:
            _text_lines(box, context, message.text if user else strip_markdown(message.text))
        for tool in message.tools:
            _draw_tool(box, context, tool, props)
    if session is not None and session.error:
        _text_lines(layout.box(), context, session.error, alert=True)


class VIEW3D_PT_bc_assistant(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Assistant"  # translated in register()
    bl_label = t("assistant")

    def draw(self, context):
        layout = self.layout
        props = context.window_manager.bc
        session = runtime.session
        status = session.status if session is not None else "IDLE"

        _draw_approval(layout, context)
        _draw_claude_problem(layout, context)

        # Header: status badge, new chat, saved conversations.
        row = layout.row(align=True)
        key, icon = _STATUS.get(status, _STATUS["IDLE"])
        row.label(text=t(key), icon=icon)
        row.operator("bc.new_chat", text="", icon="ADD")
        row.popover(panel="VIEW3D_PT_bc_conversations", text="", icon="FILE_FOLDER")

        layout.prop(props, "model", text=t("model"))

        if status == "WORKING":
            row = layout.row()
            row.alert = True
            row.operator("bc.stop", text=t("stop"), icon="CANCEL")

        _draw_conversation(layout, context, session, props)

        row = layout.row(align=True)
        row.prop(props, "input", text="", placeholder=t("message"))
        row.operator("bc.send", text="", icon="PLAY")
        if status == "WORKING":
            layout.operator("bc.send_now", text=t("send_now"), icon="FF")
        if session is not None and session.queue:
            layout.label(text=t("queued", n=len(session.queue)), icon="SORTTIME")
        if operators.notice:
            _text_lines(layout, context, operators.notice, alert=True)

        row = layout.row()
        row.prop(props, "attach_view")
        row.prop(props, "attach_selection")

        layout.prop(props, "mode", text="")

        row = layout.row(align=True)
        row.label(text=t("changes", n=session.changes if session is not None else 0))
        row.operator("bc.undo_ai_changes", text=t("undo_ai"), icon="LOOP_BACK")

        row = layout.row(align=True)
        row.label(text=t("unit"))
        row.operator_menu_enum(
            "bc.set_unit", "unit", text=_UNIT_LABELS[get_display_unit(context.scene)]
        )


class VIEW3D_PT_bc_conversations(Panel):
    """Popover listing saved conversations: this file's first."""

    bl_space_type = "VIEW_3D"
    bl_region_type = "HEADER"
    bl_label = t("conversations")
    bl_ui_units_x = 14

    def draw(self, context):
        layout = self.layout
        layout.label(text=t("conversations"))
        store = runtime.store
        conversations = store.list(bpy.data.filepath) if store is not None else []
        if not conversations:
            layout.label(text=t("no_conversations"))
            return
        session = runtime.session
        current = session.conversation.id if session is not None else ""
        for conv in conversations[:MAX_CONVERSATIONS]:
            row = layout.row(align=True)
            op = row.operator(
                "bc.open_conversation",
                text=conv.title or t("untitled"),
                icon="RIGHTARROW_THIN" if conv.id == current else "NONE",
            )
            op.conv_id = conv.id
            op = row.operator("bc.delete_conversation", text="", icon="TRASH")
            op.conv_id = conv.id


class VIEW3D_PT_bc_joint(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Assistant"  # translated in register()
    bl_parent_id = "VIEW3D_PT_bc_assistant"
    bl_label = t("joint")
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        props = context.window_manager.bc
        layout.operator("bc.joint_create", text=t("joint_create"), icon="EMPTY_AXIS")
        layout.prop_search(props, "joint_pivot", bpy.data, "objects", text=t("joint_pivot"))
        pivot = operators._current_pivot(context)
        if pivot is None:
            return
        layout.prop(props, "joint_angle", slider=True)
        row = layout.row(align=True)
        row.operator("bc.joint_reset", text=t("joint_reset"), icon="LOOP_BACK")
        row.operator("bc.joint_test", text=t("joint_test"), icon="PLAY")
        if operators.joint_result:
            _text_lines(layout, context, operators.joint_result)


CLASSES = (VIEW3D_PT_bc_assistant, VIEW3D_PT_bc_conversations, VIEW3D_PT_bc_joint)


def register() -> None:
    category = t("assistant")
    VIEW3D_PT_bc_assistant.bl_category = category
    VIEW3D_PT_bc_joint.bl_category = category
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
