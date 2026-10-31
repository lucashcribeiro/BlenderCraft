"""Makes the AI's scene changes show up at once.

Tool calls run from a timer, not from an input event, so Blender does not
redraw by itself: the user would see the model only after moving the mouse.
After a tool that changed the scene (or wrote files / imported) the depsgraph
is updated and every area and region of every window is tagged for redraw.
"""

import traceback

_dirty = False


def refresh(window_manager=None, update=None) -> None:
    """Update the view layer and tag every area and region for redraw.

    ``window_manager`` and ``update`` exist for tests (stand-ins); by default
    the current window manager and view layer. Never raises: a redraw is a
    courtesy, not a reason to fail a tool call.
    """
    global _dirty
    _dirty = False
    try:
        if update is None:
            import bpy

            update = bpy.context.view_layer.update
        update()
    except Exception:  # noqa: BLE001 - no view layer in this context
        traceback.print_exc()
    try:
        if window_manager is None:
            import bpy

            window_manager = getattr(bpy.context, "window_manager", None)
        for window in getattr(window_manager, "windows", ()):
            screen = getattr(window, "screen", None)
            for area in getattr(screen, "areas", ()):
                area.tag_redraw()
                for region in area.regions:
                    region.tag_redraw()
    except Exception:  # noqa: BLE001
        traceback.print_exc()


def mark_dirty() -> None:
    """A tool call changed something: the timer refreshes once more after
    the batch it belongs to (see flush_if_dirty)."""
    global _dirty
    _dirty = True


def flush_if_dirty() -> bool:
    """Refresh if a tool call marked the views dirty; True if it did."""
    if not _dirty:
        return False
    refresh()
    return True
