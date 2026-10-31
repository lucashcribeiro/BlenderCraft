"""Chat-bubble button on the right of the top bar: shows or hides the
Assistant in the largest 3D View's sidebar. Its icon carries a blue dot
while the agent works and an orange one after an error."""

import bpy
from bpy.types import Operator

from .. import runtime
from ..i18n import t
from . import icons

_ICONS = {"WORKING": "chat_working", "ERROR": "chat_error"}

# Status the top bar icon was last refreshed for.
_shown_status = None


def refresh_status() -> None:
    """Redraw the top bar when the agent's status changed.

    The top bar is a global area that Python cannot tag for redraw; it does
    redraw on workspace notifiers, which writing a workspace property sends
    (the same value: nothing changes). Done only on status changes.
    """
    global _shown_status
    session = runtime.session
    status = session.status if session is not None else "IDLE"
    if status == _shown_status:
        return
    _shown_status = status
    for window in bpy.context.window_manager.windows:
        workspace = window.workspace
        if workspace is None:
            continue
        try:
            workspace.use_filter_by_owner = workspace.use_filter_by_owner
        except AttributeError:  # writing refused in this context
            pass


def _largest_view3d(screen):
    areas = [a for a in getattr(screen, "areas", ()) if a.type == "VIEW_3D"]
    return max(areas, key=lambda a: a.width * a.height, default=None)


def _sidebar(area):
    return next((r for r in area.regions if r.type == "UI"), None)


def is_showing(screen, category: str) -> bool:
    area = _largest_view3d(screen)
    if area is None:
        return False
    region = _sidebar(area)
    return bool(
        area.spaces.active.show_region_ui
        and region is not None
        and region.active_panel_category == category
    )


def toggle(screen, category: str) -> str:
    """Show the Assistant tab in the largest 3D View, or hide the sidebar
    when it already shows. Returns "opened", "closed" or "none"."""
    area = _largest_view3d(screen)
    if area is None:
        return "none"
    if is_showing(screen, category):
        area.spaces.active.show_region_ui = False
        return "closed"
    area.spaces.active.show_region_ui = True
    region = _sidebar(area)
    if region is not None:
        region.active_panel_category = category
    return "opened"


class BC_OT_toggle_assistant(Operator):
    bl_idname = "bc.toggle_assistant"
    bl_label = t("toggle_assistant")
    bl_description = t("toggle_assistant_desc")

    def execute(self, context):
        screen = context.screen if context.window is not None else None
        if toggle(screen, t("assistant")) == "none":
            self.report({"WARNING"}, t("no_3d_view"))
            return {"CANCELLED"}
        for area in getattr(screen, "areas", ()):
            area.tag_redraw()
        return {"FINISHED"}


def draw_topbar(self, context) -> None:
    """Top bar (right side only): the chat-bubble toggle."""
    region = context.region
    if region is None or region.alignment != "RIGHT":
        return
    session = runtime.session
    status = session.status if session is not None else "IDLE"
    row = self.layout.row(align=True)
    icon = icons.icon_id(_ICONS.get(status, "chat"))
    showing = is_showing(context.screen, t("assistant"))
    if icon:
        row.operator("bc.toggle_assistant", text="", icon_value=icon, depress=showing)
    else:  # icons not loaded
        row.operator("bc.toggle_assistant", text="", icon="LIGHT", depress=showing)
    row.separator()


def register() -> None:
    global _shown_status
    _shown_status = None
    bpy.utils.register_class(BC_OT_toggle_assistant)
    if refresh_status not in runtime.redraw_hooks:
        runtime.redraw_hooks.append(refresh_status)
    header = bpy.types.TOPBAR_HT_upper_bar
    header.remove(draw_topbar)
    header.prepend(draw_topbar)


def unregister() -> None:
    while refresh_status in runtime.redraw_hooks:
        runtime.redraw_hooks.remove(refresh_status)
    bpy.types.TOPBAR_HT_upper_bar.remove(draw_topbar)
    bpy.utils.unregister_class(BC_OT_toggle_assistant)
