"""The Assistant tab in the 3D View sidebar.

Laid out like SolidCraft's Assistant: a header with the status badge and
chat buttons, the provider and model, the conversation in a fixed-height
scrollable list (chat_list), a footer with the change count, and the input
card at the bottom. Joint tools and settings are collapsed subpanels.
"""

import json

import bpy
from bpy.types import Panel

from .. import runtime
from ..gen3d import jobs as gen3d_jobs
from ..tools import gen3d as gen3d_tool
from ..i18n import t
from ..units import UNIT_LABELS, get_display_unit, mm_per_bu
from . import chat_list, draft, operators
from .text_wrap import wrap

DETAIL_CHARS = 600  # tool input shown in the approval block
MAX_CONVERSATIONS = 30
_MARGIN_PX = 40  # panel padding left out of the text width
NARROW_PX = 420  # below this width the attach row takes two lines

_STATUS = {
    "IDLE": ("idle", "CHECKMARK"),
    "WORKING": ("working", "TIME"),
    "ERROR": ("error", "ERROR"),
}


def _scale(context) -> float:
    return context.preferences.system.ui_scale or 1.0


def _region_size(context) -> tuple[float, float]:
    region = context.region
    width = getattr(region, "width", None)
    height = getattr(region, "height", None)
    width = width if isinstance(width, (int, float)) else 300
    height = height if isinstance(height, (int, float)) else 600
    return width, height


def _text_width(context) -> int:
    return int(_region_size(context)[0] - _MARGIN_PX * _scale(context))


def _text_lines(layout, context, text: str, alert: bool = False) -> None:
    """Labels for text wrapped to the panel width."""
    col = layout.column(align=True)
    col.scale_y = 0.8
    col.alert = alert
    for line in wrap(text, _text_width(context), _scale(context)):
        col.label(text=line)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[:limit]}…"


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


def _draw_generations(layout, context) -> None:
    """One line per running AI 3D generation, with a cancel button."""
    for job in gen3d_jobs.registry.running():
        row = layout.row(align=True)
        percent = f"{job.percent}%" if job.percent is not None else ""
        row.label(
            text=t("gen3d_running", provider=job.provider.capitalize(), stage=job.stage, percent=percent),
            icon="TIME",
        )
        row.operator("bc.gen3d_cancel", text="", icon="CANCEL")


def _draw_header(layout, status: str) -> None:
    """Status badge on the left, chat buttons on the right."""
    row = layout.row(align=True)
    badge = row.row(align=True)
    badge.alert = status == "ERROR"
    key, icon = _STATUS.get(status, _STATUS["IDLE"])
    badge.label(text=t(key), icon=icon)
    buttons = row.row(align=True)
    buttons.alignment = "RIGHT"
    buttons.emboss = "NONE"
    if status == "WORKING":
        buttons.operator("bc.stop", text="", icon="CANCEL")
    buttons.operator("bc.new_chat", text="", icon="ADD")
    buttons.popover(panel="VIEW3D_PT_bc_conversations", text="", icon="FILE_FOLDER")


def _extra_rows(session, props) -> int:
    """Rows taken by optional blocks, so the input card stays in view."""
    extra = 0
    if draft.exists(props):
        extra += draft.PREVIEW_LINES + 2
    extra += len(props.attached_files)
    extra += len(gen3d_jobs.registry.running())
    if session is not None and session.queue:
        extra += 1
    if runtime.pending_approval is not None:
        extra += 8
    if runtime.claude_problem:
        extra += 3
    return extra


def _draw_stream(layout, context, session, props) -> None:
    """The conversation: a list with its own scrollbar."""
    width, height = _region_size(context)
    scale = _scale(context)
    try:
        chat_list.sync(props, session, width, scale)
    except AttributeError:  # writing is refused in this context: next redraw
        pass
    if chat_list.has_older(session, props):
        op = layout.operator("wm.context_set_int", text=t("show_older"), icon="TRIA_UP")
        op.data_path = "window_manager.bc.show_older"
        op.value = props.show_older + 1
    extra = _extra_rows(session, props) + (1 if width < NARROW_PX * scale else 0)
    rows = chat_list.visible_rows(height, scale, extra)
    layout.template_list(
        "BC_UL_chat", "", props, "chat_lines", props, "chat_index", rows=rows, maxrows=rows
    )


def _draw_footer(layout, context, session) -> None:
    row = layout.row(align=True)
    row.label(text=t("changes", n=session.changes if session is not None else 0))
    row.operator("bc.undo_ai_changes", text="", icon="LOOP_BACK")
    if operators.notice:
        _text_lines(layout, context, operators.notice, alert=True)


def _draw_draft(layout, context, props) -> None:
    """Preview of a multi-line draft, with edit and discard buttons."""
    value = draft.text(props)
    lines, count = draft.preview(value, _text_width(context) - 20, _scale(context))
    box = layout.box()
    col = box.column(align=True)
    col.scale_y = 0.8
    col.active = False
    for line in lines:
        col.label(text=line)
    row = box.row(align=True)
    row.label(text=t("draft_lines", n=count), icon="TEXT")
    row.operator("bc.edit_draft", text="", icon="GREASEPENCIL")
    row.operator("bc.clear_draft", text="", icon="X")


def _draw_input_card(layout, context, session, props, status: str) -> None:
    working = status == "WORKING"
    card = layout.box()
    if draft.exists(props):
        _draw_draft(card, context, props)
    for index, item in enumerate(props.attached_files):
        chip = card.row(align=True)
        chip.label(text=item.name, icon="FILE_IMAGE" if item.path else "FILE_TEXT")
        op = chip.operator("bc.remove_file", text="", icon="X")
        op.index = index
    card.prop(
        props,
        "input",
        text="",
        placeholder=t("placeholder_working" if working else "placeholder"),
    )
    row = card.row(align=True)
    row.operator("bc.new_chat", text="", icon="ADD")
    row.prop(props, "mode", text="")
    row.operator("bc.paste_draft", text="", icon="PASTEDOWN")
    row.operator("bc.edit_draft", text="", icon="TEXT")
    row.operator("bc.attach_file", text="", icon="FILE_TEXT")
    send = row.row(align=True)
    send.scale_x = 1.3
    if working:
        send.operator("bc.send", text="", icon="SORTTIME")  # queued for later
        send.operator("bc.send_now", text="", icon="FF")
        stop = send.row(align=True)
        stop.alert = True
        stop.operator("bc.stop", text="", icon="SNAP_FACE")
    else:
        send.operator("bc.send", text="", icon="PLAY")
    if session is not None and session.queue:
        card.label(text=t("queued", n=len(session.queue)), icon="SORTTIME")
    attach = card.row(align=True)
    attach.prop(props, "attach_view", text=t("attach_view"))
    attach.prop(
        props,
        "attach_selection",
        text=t("attach_selection_n", n=len(context.selected_objects or ())),
    )
    if _region_size(context)[0] < NARROW_PX * _scale(context):
        attach = card.row(align=True)  # a narrow sidebar gets a second line
    attach.operator("bc.pick_context", text=t("pick_context"), icon="RESTRICT_SELECT_OFF")


class VIEW3D_PT_bc_assistant(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Assistant"  # translated in register()
    bl_label = t("assistant")

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = False
        props = context.window_manager.bc
        session = runtime.session
        status = session.status if session is not None else "IDLE"

        _draw_approval(layout, context)
        _draw_claude_problem(layout, context)
        _draw_header(layout, status)
        _draw_generations(layout, context)

        row = layout.row(align=True)
        row.label(text=t("provider"), icon="LIGHT")
        row.prop(props, "model", text="")

        scale = mm_per_bu(context.scene)
        if scale != 1.0:
            _text_lines(layout, context, t("scale_warning", mm=f"{scale:g}"), alert=True)

        _draw_stream(layout, context, session, props)
        _draw_footer(layout, context, session)
        _draw_input_card(layout, context, session, props, status)


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


class VIEW3D_PT_bc_settings(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Assistant"  # translated in register()
    bl_parent_id = "VIEW3D_PT_bc_assistant"
    bl_label = t("settings")
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        row = layout.row(align=True)
        row.label(text=t("unit"))
        row.operator_menu_enum(
            "bc.set_unit", "unit", text=UNIT_LABELS[get_display_unit(context.scene)]
        )
        layout.operator("bc.copy_mcp_command", text=t("copy_mcp"), icon="COPYDOWN")
        if not gen3d_tool.has_any_key():
            _text_lines(layout, context, t("gen3d_key_hint"))


def draw_unit_header(self, context) -> None:
    """Display unit selector at the end of the 3D View header."""
    self.layout.operator_menu_enum(
        "bc.set_unit", "unit", text=UNIT_LABELS[get_display_unit(context.scene)]
    )


def register_header() -> None:
    """Add the unit selector to the 3D View header (once)."""
    header = bpy.types.VIEW3D_HT_header
    header.remove(draw_unit_header)
    header.append(draw_unit_header)


CLASSES = (
    chat_list.BC_UL_chat,
    VIEW3D_PT_bc_assistant,
    VIEW3D_PT_bc_conversations,
    VIEW3D_PT_bc_joint,
    VIEW3D_PT_bc_settings,
)


def register() -> None:
    chat_list.reset()
    category = t("assistant")
    VIEW3D_PT_bc_assistant.bl_category = category
    VIEW3D_PT_bc_joint.bl_category = category
    VIEW3D_PT_bc_settings.bl_category = category
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    register_header()


def unregister() -> None:
    bpy.types.VIEW3D_HT_header.remove(draw_unit_header)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
