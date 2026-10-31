"""Panel state on the WindowManager (runtime only, never saved in .blend)."""

import time

import bpy
from bpy.app.handlers import persistent
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import PropertyGroup, WindowManager

from .. import runtime
from ..i18n import t
from ..tools.joints import set_angle

# Blender treats an empty enum identifier as a separator, so Claude Code's
# default model ("") is stored as DEFAULT.
MODEL_DEFAULT = "DEFAULT"
ANGLE_TOLERANCE = 1e-4  # degrees

# When the message field was last committed (time.monotonic()). Enter and a
# click elsewhere both commit it; only Enter is followed by a key release,
# which the bc.send_key keymap item checks against this time.
input_committed_at = 0.0


def _on_input(self, context):
    global input_committed_at
    input_committed_at = time.monotonic()


def _on_mode(self, context):
    runtime.set_mode(self.mode)


def _on_model(self, context):
    runtime.set_model("" if self.model == MODEL_DEFAULT else self.model)


def _pivot(self):
    obj = bpy.data.objects.get(self.joint_pivot) if self.joint_pivot else None
    return obj if obj is not None and "bc_axis" in obj else None


def _on_joint_pivot(self, context):
    pivot = _pivot(self)
    if pivot is not None:
        self.joint_angle = float(pivot.get("bc_angle", 0.0))


def _on_joint_angle(self, context):
    pivot = _pivot(self)
    if pivot is None:
        return
    # The slider is float32: picking a pivot shows its angle rounded, and
    # that rounding must not turn the pivot.
    current = pivot.get("bc_angle")
    if current is None or abs(float(current) - self.joint_angle) > ANGLE_TOLERANCE:
        set_angle(pivot.name, self.joint_angle)


class BC_ChatLine(PropertyGroup):
    """One display row of the conversation list (see chat_list)."""

    kind: StringProperty()
    text: StringProperty()
    tool_id: StringProperty()
    status: StringProperty()


class BC_AttachedFile(PropertyGroup):
    """A text file attached to the next message (name shown as a chip)."""

    name: StringProperty()
    content: StringProperty()


class BC_Props(PropertyGroup):
    # The update never sends: Blender commits a text field the same way on
    # Enter and on a click elsewhere. It only records the time, and the
    # Enter key release (keymap item bc.send_key) sends.
    input: StringProperty(name=t("message"), update=_on_input)
    # Multi-line text pasted from the clipboard (the field above is
    # single-line). While the composer is open its Text block holds it.
    draft: StringProperty()
    mode: EnumProperty(
        name=t("mode"),
        items=[
            ("AUTO", t("mode_auto"), t("mode_auto_desc")),
            ("ASK", t("mode_ask"), t("mode_ask_desc")),
            ("READONLY", t("mode_readonly"), t("mode_readonly_desc")),
        ],
        default="AUTO",
        update=_on_mode,
    )
    model: EnumProperty(
        name=t("model"),
        items=[
            (MODEL_DEFAULT, t("model_default"), ""),
            ("opus", "Opus", ""),
            ("sonnet", "Sonnet", ""),
            ("haiku", "Haiku", ""),
            ("fable", "Fable", ""),
        ],
        default=MODEL_DEFAULT,
        update=_on_model,
    )
    attach_view: BoolProperty(name=t("attach_view"))
    attach_selection: BoolProperty(name=t("attach_selection"))
    attached_files: CollectionProperty(type=BC_AttachedFile)
    show_older: IntProperty(min=0)  # pages of older messages shown
    chat_lines: CollectionProperty(type=BC_ChatLine)
    chat_index: IntProperty(min=0)  # active row: kept on the last row to follow the bottom
    expanded_tool: StringProperty()  # tool call id whose details are open
    joint_pivot: StringProperty(name=t("joint_pivot"), update=_on_joint_pivot)
    joint_angle: FloatProperty(
        name=t("joint_angle"),
        min=-360.0,
        max=360.0,
        soft_min=-180.0,
        soft_max=180.0,
        precision=1,
        update=_on_joint_angle,
    )


@persistent
def _sync_after_load(*_args) -> None:
    """Show the runtime's mode and model again if a file load reset them."""
    props = getattr(bpy.context.window_manager, "bc", None)
    if props is None:
        return
    if props.mode != runtime.mode:
        props.mode = runtime.mode
    model = runtime.model or MODEL_DEFAULT
    if props.model != model:
        props.model = model


def register() -> None:
    bpy.utils.register_class(BC_ChatLine)
    bpy.utils.register_class(BC_AttachedFile)
    bpy.utils.register_class(BC_Props)
    WindowManager.bc = PointerProperty(type=BC_Props)
    if _sync_after_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_sync_after_load)


def unregister() -> None:
    while _sync_after_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_sync_after_load)
    del WindowManager.bc
    bpy.utils.unregister_class(BC_Props)
    bpy.utils.unregister_class(BC_AttachedFile)
    bpy.utils.unregister_class(BC_ChatLine)
