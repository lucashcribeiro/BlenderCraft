"""Panel operators (bc.*) and message submission."""

import base64
from contextlib import nullcontext

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy.types import Operator

from .. import runtime
from ..i18n import t
from ..tools.dispatch import call_tool
from ..tools.joints import create_pivot, joint_test, reset
from ..tools.view import screenshot
from ..units import DISPLAY_UNITS, set_display_unit

# Selected objects described in one "attach selection" block.
MAX_ATTACHED_OBJECTS = 20

# Shown in the panel: the last joint test and the last attachment problem.
joint_result = ""
notice = ""


# ---- sending ---------------------------------------------------------


def build_attachments(context) -> tuple[list[dict], list[str]]:
    """Content blocks for the checked attachments, and warnings for those
    that failed. The checkboxes apply to one message and are cleared.

    Runs inside the tracker's AI scope: capturing the view (a temporary
    camera without a UI) is not a manual edit of the scene.
    """
    tracker = runtime.tracker
    scope = tracker.ai_scope() if tracker is not None else nullcontext()
    with scope:
        return _build_attachments(context)


def _build_attachments(context) -> tuple[list[dict], list[str]]:
    props = context.window_manager.bc
    blocks: list[dict] = []
    warnings: list[str] = []
    if props.attach_view:
        try:
            png = screenshot("viewport")
        except Exception as exc:  # noqa: BLE001 - e.g. nothing to render
            warnings.append(t("attach_failed", err=exc))
        else:
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64.b64encode(png).decode("ascii"),
                    },
                }
            )
    if props.attach_selection:
        selected = list(context.selected_objects)
        if selected:
            parts = [
                call_tool("inspect_object", {"name": obj.name}).text
                for obj in selected[:MAX_ATTACHED_OBJECTS]
            ]
            extra = len(selected) - MAX_ATTACHED_OBJECTS
            if extra > 0:
                parts.append(f"... and {extra} more selected objects")
            blocks.append(
                {
                    "type": "text",
                    "text": "Selected objects (inspect_object):\n" + "\n".join(parts),
                }
            )
    props.attach_view = False
    props.attach_selection = False
    return blocks, warnings


def submit(context, text: str, now: bool = False) -> None:
    """Send text with the checked attachments. While the agent works a plain
    send is queued; now=True interrupts and sends at once."""
    global notice
    attachments, warnings = build_attachments(context)
    notice = "\n".join(warnings)
    session = runtime.ensure_session()
    if now:
        session.send_now(text, attachments)
    else:
        session.send(text, attachments)
    context.window_manager.bc.show_older = 0
    runtime.tag_redraw()


def _runtime_ready(op) -> bool:
    """Report and refuse while the runtime is not up (the typed text stays)."""
    if runtime.ready():
        return True
    op.report({"WARNING"}, t("server_off"))
    return False


def _take_input(context) -> str:
    props = context.window_manager.bc
    text = props.input.strip()
    props.input = ""  # empty: the update callback does not send again
    return text


class BC_OT_send(Operator):
    bl_idname = "bc.send"
    bl_label = t("send")
    bl_description = t("send")

    def execute(self, context):
        if not _runtime_ready(self):
            return {"CANCELLED"}
        text = _take_input(context)
        if not text:
            return {"CANCELLED"}
        submit(context, text)
        return {"FINISHED"}


class BC_OT_send_now(Operator):
    bl_idname = "bc.send_now"
    bl_label = t("send_now")
    bl_description = t("send_now")

    def execute(self, context):
        if not _runtime_ready(self):
            return {"CANCELLED"}
        text = _take_input(context)
        if text:
            submit(context, text, now=True)
            return {"FINISHED"}
        # Enter (or leaving the field) already queued the text: promote the
        # newest queued message instead.
        session = runtime.ensure_session()
        if not session.queue:
            return {"CANCELLED"}
        text, attachments = session.queue.pop()
        session.send_now(text, attachments)
        runtime.tag_redraw()
        return {"FINISHED"}


class BC_OT_stop(Operator):
    bl_idname = "bc.stop"
    bl_label = t("stop")
    bl_description = t("stop")

    def execute(self, context):
        runtime.stop_session()
        return {"FINISHED"}


class BC_OT_new_chat(Operator):
    bl_idname = "bc.new_chat"
    bl_label = t("new_chat")
    bl_description = t("new_chat")

    def execute(self, context):
        runtime.new_conversation()
        props = context.window_manager.bc
        props.show_older = 0
        props.expanded_tool = ""
        return {"FINISHED"}


class BC_OT_open_conversation(Operator):
    bl_idname = "bc.open_conversation"
    bl_label = t("open_conversation")
    bl_description = t("open_conversation")

    conv_id: StringProperty(options={"SKIP_SAVE", "HIDDEN"})

    def execute(self, context):
        try:
            runtime.open_conversation(self.conv_id)
        except (OSError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        props = context.window_manager.bc
        props.show_older = 0
        props.expanded_tool = ""
        return {"FINISHED"}


class BC_OT_delete_conversation(Operator):
    bl_idname = "bc.delete_conversation"
    bl_label = t("delete_conversation")
    bl_description = t("delete_conversation")

    conv_id: StringProperty(options={"SKIP_SAVE", "HIDDEN"})

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        try:
            runtime.delete_conversation(self.conv_id)
        except (OSError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class BC_OT_toggle_details(Operator):
    bl_idname = "bc.toggle_details"
    bl_label = t("details")
    bl_description = t("details")

    tool_id: StringProperty(options={"SKIP_SAVE", "HIDDEN"})

    def execute(self, context):
        props = context.window_manager.bc
        props.expanded_tool = "" if props.expanded_tool == self.tool_id else self.tool_id
        return {"FINISHED"}


class BC_OT_undo_ai_changes(Operator):
    bl_idname = "bc.undo_ai_changes"
    bl_label = t("undo_ai")
    bl_description = t("undo_ai")

    @classmethod
    def poll(cls, context):
        # Disabled while the agent works and when the AI has nothing to undo.
        session = runtime.session
        if session is not None and session.status == "WORKING":
            return False
        tracker = runtime.tracker
        return tracker is not None and tracker.ai_steps > 0

    def execute(self, context):
        tracker = runtime.tracker
        if tracker is None or not tracker.can_undo_last_turn():
            self.report({"WARNING"}, t("undo_ai_blocked"))
            try:
                bpy.ops.ed.undo_history("INVOKE_DEFAULT")
            except RuntimeError:  # no undo history UI (background mode)
                pass
            return {"CANCELLED"}
        count = tracker.undo_last_turn()
        self.report({"INFO"}, t("undo_ai_done", n=count))
        runtime.tag_redraw()
        return {"FINISHED"}


class BC_OT_approve(Operator):
    bl_idname = "bc.approve"
    bl_label = t("approve")
    bl_description = t("approve")

    allow: BoolProperty(options={"SKIP_SAVE"})

    def execute(self, context):
        runtime.answer_approval(self.allow)
        return {"FINISHED"}


_UNIT_LABELS = {"MM": "mm", "CM": "cm", "M": "m", "IN": "in", "FT": "ft"}


class BC_OT_set_unit(Operator):
    bl_idname = "bc.set_unit"
    bl_label = t("unit")
    bl_description = t("unit")
    bl_options = {"REGISTER", "UNDO"}

    unit: EnumProperty(
        name=t("unit"),
        items=[(key, _UNIT_LABELS[key], "") for key in DISPLAY_UNITS],
    )

    def execute(self, context):
        set_display_unit(context.scene, self.unit)
        return {"FINISHED"}


def _current_pivot(context):
    name = context.window_manager.bc.joint_pivot
    obj = bpy.data.objects.get(name) if name else None
    return obj if obj is not None and "bc_axis" in obj else None


class BC_OT_joint_create(Operator):
    bl_idname = "bc.joint_create"
    bl_label = t("joint_create")
    bl_description = t("joint_create_desc")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.active_object
        if obj is None:
            self.report({"WARNING"}, t("joint_no_active"))
            return {"CANCELLED"}
        if "bc_axis" in obj:
            pivot = obj.name
        elif obj.parent is not None and "bc_axis" in obj.parent:
            pivot = obj.parent.name
        else:
            try:
                pivot = create_pivot(obj.name, tuple(context.scene.cursor.location), "Z")
            except ValueError as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
        context.window_manager.bc.joint_pivot = pivot
        return {"FINISHED"}


class BC_OT_joint_reset(Operator):
    bl_idname = "bc.joint_reset"
    bl_label = t("joint_reset")
    bl_description = t("joint_reset")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _current_pivot(context) is not None

    def execute(self, context):
        reset(_current_pivot(context).name)
        context.window_manager.bc.joint_angle = 0.0
        return {"FINISHED"}


def _angle(value: float) -> str:
    return f"{value:g}"


def format_joint_result(result: dict) -> str:
    if result["colliding_at_rest"]:
        return t("joint_at_rest", obj=result["collisions"][0]["with"])
    lines = [t("joint_free", min=_angle(result["free_min"]), max=_angle(result["free_max"]))]
    for hit in result["collisions"]:
        lines.append(t("joint_hit", obj=hit["with"], angle=_angle(hit["angle"])))
    return "\n".join(lines)


class BC_OT_joint_test(Operator):
    bl_idname = "bc.joint_test"
    bl_label = t("joint_test")
    bl_description = t("joint_test")

    @classmethod
    def poll(cls, context):
        return _current_pivot(context) is not None

    def execute(self, context):
        global joint_result
        try:
            result = joint_test(_current_pivot(context).name)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        joint_result = format_joint_result(result)
        return {"FINISHED"}


class BC_OT_copy_mcp_command(Operator):
    bl_idname = "bc.copy_mcp_command"
    bl_label = t("copy_mcp")
    bl_description = t("copy_mcp")

    def execute(self, context):
        if runtime.server is None:
            self.report({"WARNING"}, t("server_off"))
            return {"CANCELLED"}
        context.window_manager.clipboard = runtime.mcp_command()
        self.report({"INFO"}, t("mcp_copied"))
        return {"FINISHED"}


CLASSES = (
    BC_OT_send,
    BC_OT_send_now,
    BC_OT_stop,
    BC_OT_new_chat,
    BC_OT_open_conversation,
    BC_OT_delete_conversation,
    BC_OT_toggle_details,
    BC_OT_undo_ai_changes,
    BC_OT_approve,
    BC_OT_set_unit,
    BC_OT_joint_create,
    BC_OT_joint_reset,
    BC_OT_joint_test,
    BC_OT_copy_mcp_command,
)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
