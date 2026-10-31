"""Panel operators (bc.*) and message submission."""

import base64
import os
import time
from contextlib import nullcontext

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ImportHelper

from .. import runtime
from ..gen3d import jobs as gen3d_jobs
from ..i18n import t
from ..tools.dispatch import call_tool
from ..tools.joints import create_pivot, joint_test, reset
from ..tools.view import screenshot
from ..units import DISPLAY_UNITS, UNIT_LABELS, set_display_unit
from . import draft
from . import props as props_module

# Selected objects described in one "attach selection" block.
MAX_ATTACHED_OBJECTS = 20
# Enter's key release must follow the field's commit within this time.
ENTER_WINDOW = 0.6  # seconds
MAX_FILE_CHARS = 200_000  # text read from an attached file
# Attached images: shown to Claude up to this size (the API takes 5 MB of
# base64 per image); larger ones go by path only.
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
MAX_IMAGE_SHOWN = 3_750_000

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


def _image_blocks(item, warnings: list[str]) -> list[dict]:
    """An attached image for Claude, and its path so tools can use it (for
    example as image_paths of generate_3d_model)."""
    note = {
        "type": "text",
        "text": (
            f"Attached image: {item.name}\nPath: {item.path}\n"
            "(usable as image_paths in generate_3d_model)"
        ),
    }
    mime = IMAGE_TYPES.get(os.path.splitext(item.path)[1].lower())
    try:
        size = os.path.getsize(item.path)
        data = None
        if mime is not None and size <= MAX_IMAGE_SHOWN:
            with open(item.path, "rb") as fh:
                data = fh.read()
    except OSError as exc:
        warnings.append(t("attach_failed", err=exc))
        return []
    if data is None:
        note["text"] += "\n(not shown: too large to display)"
        return [note]
    image = {
        "type": "image",
        "source": {"type": "base64", "media_type": mime, "data": base64.b64encode(data).decode("ascii")},
    }
    return [image, note]


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
    for item in props.attached_files:
        if item.path:
            blocks.extend(_image_blocks(item, warnings))
        else:
            blocks.append({"type": "text", "text": f"Attached file: {item.name}\n\n{item.content}"})
    props.attach_view = False
    props.attach_selection = False
    props.attached_files.clear()
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
    """The message to send: the draft, then the typed line, verbatim (only
    the outer blank space is trimmed). Clears both. With nothing written
    but files attached, the message names the files."""
    props = context.window_manager.bc
    parts = [p for p in (draft.text(props).strip(), props.input.strip()) if p]
    text = "\n\n".join(parts)
    if not text and len(props.attached_files):
        text = t("attached_files_msg", names=", ".join(f.name for f in props.attached_files))
    props.input = ""
    draft.clear(props)
    return text


def _has_message(props) -> bool:
    return bool(props.input.strip() or draft.text(props).strip() or len(props.attached_files))


def _send_typed(op, context, now: bool):
    if not _runtime_ready(op):
        return {"CANCELLED"}
    text = _take_input(context)
    if not text:
        return {"CANCELLED"}
    submit(context, text, now=now)
    return {"FINISHED"}


class BC_OT_send(Operator):
    bl_idname = "bc.send"
    bl_label = t("send")
    bl_description = t("send")

    def execute(self, context):
        return _send_typed(self, context, now=False)


def _just_committed() -> bool:
    return time.monotonic() - props_module.input_committed_at < ENTER_WINDOW


class BC_OT_send_key(Operator):
    """Enter in the message field (keymap: Enter release).

    Blender commits a text field the same way on Enter and on a click
    elsewhere; only Enter is followed by its key release, right after the
    commit. So this only runs within ENTER_WINDOW of the field's commit.
    While the agent works the message is queued.
    """

    bl_idname = "bc.send_key"
    bl_label = t("send_key")
    bl_description = t("send_key")
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        props = context.window_manager.bc
        return _just_committed() and _has_message(props)

    def execute(self, context):
        props_module.input_committed_at = 0.0  # one send per commit
        return _send_typed(self, context, now=False)


class BC_OT_send_key_now(Operator):
    """Ctrl/Cmd+Enter: send now (interrupting the agent if it works).

    Runs right after the field's commit, or with the pointer over a 3D View
    (the field already committed)."""

    bl_idname = "bc.send_key_now"
    bl_label = t("send_key_now")
    bl_description = t("send_key_now")
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        props = context.window_manager.bc
        area = context.area
        over_view = area is not None and area.type == "VIEW_3D"
        return (_just_committed() or over_view) and _has_message(props)

    def execute(self, context):
        props_module.input_committed_at = 0.0
        session = runtime.session
        working = session is not None and session.status == "WORKING"
        return _send_typed(self, context, now=working)


class BC_OT_pick_context(Operator):
    bl_idname = "bc.pick_context"
    bl_label = t("pick_context")
    bl_description = t("pick_context_desc")

    def execute(self, context):
        count = len(context.selected_objects)
        if not count:
            self.report({"WARNING"}, t("pick_context_none"))
            return {"CANCELLED"}
        context.window_manager.bc.attach_selection = True
        self.report({"INFO"}, t("pick_context_done", n=count))
        return {"FINISHED"}


class BC_OT_paste_draft(Operator):
    bl_idname = "bc.paste_draft"
    bl_label = t("paste_draft")
    bl_description = t("paste_draft_desc")

    def execute(self, context):
        value = draft.read_clipboard()
        if not value.strip():
            self.report({"WARNING"}, t("clipboard_empty"))
            return {"CANCELLED"}
        draft.append(context.window_manager.bc, value.replace("\r\n", "\n"))
        return {"FINISHED"}


class BC_OT_edit_draft(Operator):
    bl_idname = "bc.edit_draft"
    bl_label = t("edit_draft")
    bl_description = t("edit_draft_desc")

    def execute(self, context):
        txt = draft.open_composer(context.window_manager.bc)
        if not draft.show_window(context, txt):
            self.report({"INFO"}, t("draft_in_text_editor", name=txt.name))
        return {"FINISHED"}


class BC_OT_clear_draft(Operator):
    bl_idname = "bc.clear_draft"
    bl_label = t("clear_draft")
    bl_description = t("clear_draft")

    def execute(self, context):
        draft.clear(context.window_manager.bc)
        return {"FINISHED"}


class BC_OT_attach_file(Operator, ImportHelper):
    bl_idname = "bc.attach_file"
    bl_label = t("attach_file")
    bl_description = t("attach_file_desc")

    filter_glob: StringProperty(
        default="*.md;*.markdown;*.txt;*.png;*.jpg;*.jpeg;*.webp", options={"HIDDEN"}
    )
    filepath: StringProperty(subtype="FILE_PATH", options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        if os.path.splitext(self.filepath)[1].lower() in IMAGE_TYPES:
            if not os.path.isfile(self.filepath):
                self.report({"ERROR"}, self.filepath)
                return {"CANCELLED"}
            item = context.window_manager.bc.attached_files.add()
            item.name = os.path.basename(self.filepath)
            item.path = os.path.abspath(self.filepath)
            return {"FINISHED"}
        try:
            with open(self.filepath, encoding="utf-8", errors="replace") as fh:
                content = fh.read(MAX_FILE_CHARS + 1)
        except OSError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if len(content) > MAX_FILE_CHARS:
            content = content[:MAX_FILE_CHARS]
            self.report({"WARNING"}, t("file_truncated", n=MAX_FILE_CHARS))
        item = context.window_manager.bc.attached_files.add()
        item.name = os.path.basename(self.filepath)
        item.content = content
        return {"FINISHED"}


class BC_OT_remove_file(Operator):
    bl_idname = "bc.remove_file"
    bl_label = t("remove_file")
    bl_description = t("remove_file")

    index: IntProperty(options={"SKIP_SAVE", "HIDDEN"})

    def execute(self, context):
        files = context.window_manager.bc.attached_files
        if not 0 <= self.index < len(files):
            return {"CANCELLED"}
        files.remove(self.index)
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
        # Nothing typed: promote the newest queued message instead.
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


class BC_OT_gen3d_cancel(Operator):
    bl_idname = "bc.gen3d_cancel"
    bl_label = t("gen3d_cancel")
    bl_description = t("gen3d_cancel")

    def execute(self, context):
        gen3d_jobs.registry.cancel_all()
        runtime.tag_redraw()
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


class BC_OT_set_unit(Operator):
    bl_idname = "bc.set_unit"
    bl_label = t("unit")
    bl_description = t("unit")
    bl_options = {"REGISTER", "UNDO"}

    unit: EnumProperty(
        name=t("unit"),
        items=[(key, UNIT_LABELS[key], "") for key in DISPLAY_UNITS],
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
    BC_OT_send_key,
    BC_OT_send_key_now,
    BC_OT_send_now,
    BC_OT_pick_context,
    BC_OT_paste_draft,
    BC_OT_edit_draft,
    BC_OT_clear_draft,
    BC_OT_attach_file,
    BC_OT_remove_file,
    BC_OT_stop,
    BC_OT_gen3d_cancel,
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
