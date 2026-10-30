"""BlenderCraft application template.

Enables the BlenderCraft Assistant extension, tunes the preferences and sets
up every new, unsaved file: 1 BU = 1 mm, a 20 mm default cube (camera and light moved with it) and no
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
MAX_CLOSES = 8
# The default view distance (10 BU) is a 10 mm window; 80 mm frames the 20 mm cube.
VIEW_DISTANCE = 80.0
_attempts = 0

# Enabling the extension waits for bl_pkg and the extension repositories.
ENABLE_INTERVAL = 0.25
MAX_ENABLE_ATTEMPTS = 20
_enable_attempts = 0


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


# Where Blender's startup file puts them, in the original 1 BU = 1 m scene.
FACTORY_LOCATIONS = {
    "Camera": ("CAMERA", (7.358891, -6.925791, 4.958309)),
    "Light": ("LIGHT", (4.076245, 1.005454, 5.903862)),
}
LOCATION_FACTOR = DEFAULT_CUBE_SIZE_MM / 2.0


def _is_factory_object(obj, kind: str, location) -> bool:
    if obj.type != kind or obj.parent is not None:
        return False
    return all(abs(a - b) <= 1e-3 for a, b in zip(obj.location, location))


def _scale_default_camera_and_light() -> None:
    """Move the untouched default camera and light out by the cube's factor,
    so they stay around the cube once it is 20 mm. The camera's clip range
    grows by the same factor, or the cube would fall beyond clip_end."""
    for name, (kind, location) in FACTORY_LOCATIONS.items():
        obj = bpy.data.objects.get(name)
        if obj is not None and _is_factory_object(obj, kind, location):
            obj.location = [value * LOCATION_FACTOR for value in obj.location]
            if kind == "CAMERA":
                obj.data.clip_start *= LOCATION_FACTOR
                obj.data.clip_end *= LOCATION_FACTOR


def _remove_animation_workspace() -> None:
    animation = bpy.data.workspaces.get("Animation")
    if animation is None:
        return
    layout = bpy.data.workspaces.get("Layout")
    wm = bpy.context.window_manager
    for window in wm.windows if wm else ():
        if window.workspace == animation and layout is not None:
            window.workspace = layout
    # The workspace's screens stay behind with no users once it is gone.
    screens = [screen.name for screen in animation.screens]
    # bpy.data.workspaces has no remove(); batch_remove deletes any ID.
    bpy.data.batch_remove(ids=(animation,))
    for name in screens:
        screen = bpy.data.screens.get(name)
        if screen is not None and screen.users == 0:
            bpy.data.batch_remove(ids=(screen,))


@persistent
def setup_new_file(*_args) -> None:
    """Prepare a new, unsaved file. Does nothing for files with a name."""
    if not _unsaved():
        return
    scene = bpy.context.scene
    if scene is not None:
        apply_units(scene)
    _resize_default_cube()
    _scale_default_camera_and_light()
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


def _unsaved() -> bool:
    return bpy.data.filepath == ""


def _override(window, screen, area):
    """Context for UI changes. Without a window context some of them crash
    Blender 5.2 (show_region_ui goes through ED_area_init)."""
    return bpy.context.temp_override(window=window, screen=screen, area=area)


def _area_close() -> None:
    bpy.ops.screen.area_close()


def _close_timelines(window, screen) -> None:
    """Close every Timeline of the screen, scanning again after each close."""
    for _ in range(MAX_CLOSES):
        areas = timeline_areas(screen)
        if not areas:
            return
        with _override(window, screen, areas[0]):
            _area_close()


def _open_sidebar(window, screen) -> None:
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        with _override(window, screen, area):
            for space in area.spaces:
                if space.type == "VIEW_3D":
                    space.show_region_ui = True
                    space.region_3d.view_distance = VIEW_DISTANCE


def _tweak_ui():
    """Timer: close the Layout timeline, open the sidebar and frame the cube. None when done."""
    global _attempts
    if not _unsaved():
        return None
    windows = _windows()
    if not windows:
        _attempts += 1
        return RETRY_INTERVAL if _attempts < MAX_ATTEMPTS else None
    for window in windows:
        if window.workspace.name != "Layout":
            continue
        _close_timelines(window, window.screen)
        _open_sidebar(window, window.screen)
    return None


def _schedule_ui_tweaks() -> None:
    global _attempts
    _attempts = 0
    if not bpy.app.timers.is_registered(_tweak_ui):
        # Persistent: the startup file loads after the template registers and
        # would drop a plain timer.
        bpy.app.timers.register(_tweak_ui, first_interval=RETRY_INTERVAL, persistent=True)


def _extension_enabled() -> bool:
    return EXTENSION in bpy.context.preferences.addons


def _enable_extension():
    """Timer: enable the assistant extension, retrying until it is in the preferences.

    register() runs before bl_pkg and the bl_ext repositories exist, so enabling
    there silently fails (addon_utils.enable just returns None). A few ticks
    later it works. Returns None when done."""
    global _enable_attempts
    import addon_utils

    if _extension_enabled():
        return None
    try:
        addon_utils.enable(EXTENSION, default_set=True, persistent=True)
    except Exception as exc:  # the template must keep working without it
        print(f"BlenderCraft: error enabling the assistant extension: {exc}")
    if _extension_enabled():
        return None
    _enable_attempts += 1
    if _enable_attempts >= MAX_ENABLE_ATTEMPTS:
        print(
            f"BlenderCraft: could not enable the extension {EXTENSION}. "
            "Is it installed? Run scripts/install.sh."
        )
        return None
    return ENABLE_INTERVAL


def _schedule_enable() -> None:
    global _enable_attempts
    _enable_attempts = 0
    if not bpy.app.timers.is_registered(_enable_extension):
        bpy.app.timers.register(_enable_extension, first_interval=ENABLE_INTERVAL, persistent=True)


def register() -> None:
    _schedule_enable()
    bpy.context.preferences.edit.undo_steps = UNDO_STEPS
    if setup_new_file not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(setup_new_file)
    _schedule_ui_tweaks()


def unregister() -> None:
    while setup_new_file in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(setup_new_file)
    for timer in (_tweak_ui, _enable_extension):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
