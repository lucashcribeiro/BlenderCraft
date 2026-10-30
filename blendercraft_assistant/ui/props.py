"""Panel state on the WindowManager (runtime only, never saved in .blend)."""

import bpy
from bpy.app.handlers import persistent
from bpy.props import (
    BoolProperty,
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

_clearing_input = False


def _on_input(self, context):
    """Enter in the message field sends it. Clearing the field fires this
    update again; the flag and the empty-text check stop the recursion."""
    global _clearing_input
    if _clearing_input:
        return
    text = self.input.strip()
    if not text:
        return
    _clearing_input = True
    try:
        self.input = ""
    finally:
        _clearing_input = False
    from .operators import submit

    submit(context, text)


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
    if pivot is not None and pivot.get("bc_angle") != self.joint_angle:
        set_angle(pivot.name, self.joint_angle)


class BC_Props(PropertyGroup):
    input: StringProperty(name=t("message"), update=_on_input)
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
    show_older: IntProperty(min=0)  # pages of older messages shown
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
    bpy.utils.register_class(BC_Props)
    WindowManager.bc = PointerProperty(type=BC_Props)
    if _sync_after_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_sync_after_load)


def unregister() -> None:
    while _sync_after_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_sync_after_load)
    del WindowManager.bc
    bpy.utils.unregister_class(BC_Props)
