"""BlenderCraft application template.

Enables the BlenderCraft Assistant extension, tunes the preferences and sets
up every new, unsaved file: 1 BU = 1 mm, a 20 mm default cube and no
Animation workspace or timeline. Files that already have a name keep their
own workspaces and units.

Nothing from the extension is imported at module level, so this module also
loads in background mode without the extension installed.
"""

import importlib

import bpy
from bpy.app.handlers import persistent

EXTENSION = "bl_ext.user_default.blendercraft_assistant"
UNDO_STEPS = 256
DEFAULT_CUBE_SIZE_MM = 20.0

# The window may take a moment to exist after startup: retry for ~10 s.
RETRY_INTERVAL = 0.5
MAX_ATTEMPTS = 20
_attempts = 0


def apply_units(scene) -> None:
    """1 BU = 1 mm shown in millimeters, through the extension when present."""
    try:
        units = importlib.import_module(f"{EXTENSION}.units")
    except ImportError:
        units = None
    if units is not None:
        units.apply_internal_mm(scene)
        return
    us = scene.unit_settings
    us.system = "METRIC"
    us.scale_length = 0.001
    us.length_unit = "MILLIMETERS"


def _is_factory_cube(cube) -> bool:
    """True for the default cube: 8 vertices at +-1 and no object scale."""
    mesh = cube.data
    if len(mesh.vertices) != 8:
        return False
    if any(abs(value - 1.0) > 1e-4 for value in cube.scale):
        return False
    return all(abs(abs(c) - 1.0) <= 1e-4 for v in mesh.vertices for c in v.co)


def _resize_default_cube() -> None:
    """Make the untouched default cube 20 mm, keeping its object scale at 1."""
    from mathutils import Matrix

    cube = bpy.data.objects.get("Cube")
    if cube is None or cube.type != "MESH" or not _is_factory_cube(cube):
        return
    cube.data.transform(Matrix.Scale(DEFAULT_CUBE_SIZE_MM / 2.0, 4))
    cube.data.update()
    # Object dimensions come from the evaluated bounds, which are stale until now.
    bpy.context.view_layer.update()


def _remove_animation_workspace() -> None:
    animation = bpy.data.workspaces.get("Animation")
    if animation is None:
        return
    layout = bpy.data.workspaces.get("Layout")
    wm = bpy.context.window_manager
    for window in wm.windows if wm else ():
        if window.workspace == animation and layout is not None:
            window.workspace = layout
    # bpy.data.workspaces has no remove(); batch_remove deletes any ID.
    bpy.data.batch_remove(ids=(animation,))


@persistent
def setup_new_file(*_args) -> None:
    """Prepare a new, unsaved file. Does nothing for files with a name."""
    if bpy.data.filepath != "":
        return
    scene = bpy.context.scene
    if scene is not None:
        apply_units(scene)
    _resize_default_cube()
    _remove_animation_workspace()
    # File > New reaches here too, and its Layout still has a timeline.
    _schedule_ui_tweaks()


def timeline_areas(screen) -> list:
    """The Timeline areas of a screen (a Dope Sheet editor in Timeline mode)."""
    return [a for a in screen.areas if a.type == "DOPESHEET_EDITOR" and a.ui_type == "TIMELINE"]


def _windows() -> list:
    """Open windows; none in background mode, where UI operators never return."""
    if bpy.app.background:
        return []
    wm = bpy.context.window_manager
    return list(wm.windows) if wm else []


def _tweak_ui():
    """Timer: close the Layout timeline and open the sidebar. None when done."""
    global _attempts
    if bpy.data.filepath != "":
        return None
    windows = _windows()
    if not windows:
        _attempts += 1
        return RETRY_INTERVAL if _attempts < MAX_ATTEMPTS else None
    for window in windows:
        screen = window.screen
        if window.workspace.name != "Layout":
            continue
        for area in timeline_areas(screen):
            with bpy.context.temp_override(window=window, screen=screen, area=area):
                bpy.ops.screen.area_close()
        for area in screen.areas:
            if area.type == "VIEW_3D":
                for space in area.spaces:
                    if space.type == "VIEW_3D":
                        space.show_region_ui = True
    return None


def _schedule_ui_tweaks() -> None:
    global _attempts
    _attempts = 0
    if not bpy.app.timers.is_registered(_tweak_ui):
        # Persistent: the startup file loads after the template registers and
        # would drop a plain timer.
        bpy.app.timers.register(_tweak_ui, first_interval=RETRY_INTERVAL, persistent=True)


def _enable_extension() -> None:
    import addon_utils

    try:
        addon_utils.enable(EXTENSION, default_set=True, persistent=True)
    except Exception as exc:  # the template must keep working without it
        print(f"BlenderCraft: could not enable the assistant extension: {exc}")


def register() -> None:
    _enable_extension()
    bpy.context.preferences.edit.undo_steps = UNDO_STEPS
    if setup_new_file not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(setup_new_file)
    _schedule_ui_tweaks()


def unregister() -> None:
    while setup_new_file in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(setup_new_file)
    if bpy.app.timers.is_registered(_tweak_ui):
        bpy.app.timers.unregister(_tweak_ui)
