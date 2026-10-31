"""Screenshot tool: the 3D viewport or a Workbench render from a fixed angle."""

import shutil
import tempfile
import traceback
from pathlib import Path

import bpy
from mathutils import Euler, Vector

from .base import Tool, ToolResult, register_tool

VIEWS = ("viewport", "front", "back", "left", "right", "top", "bottom", "iso")

# Direction from the scene center towards the camera, per fixed view.
# Blender convention: front looks along +Y (camera at -Y), right looks along -X.
_VIEW_DIRECTIONS = {
    "front": Vector((0.0, -1.0, 0.0)),
    "back": Vector((0.0, 1.0, 0.0)),
    "left": Vector((-1.0, 0.0, 0.0)),
    "right": Vector((1.0, 0.0, 0.0)),
    "top": Vector((0.0, 0.0, 1.0)),
    "bottom": Vector((0.0, 0.0, -1.0)),
    "iso": Vector((1.0, -1.0, 1.0)).normalized(),
}

_MARGIN = 0.10
_DEFAULT_SIZE = 768
_MIN_SIZE = 16
_MAX_SIZE = 2048
# Long edge of a viewport capture, so the image stays cheap for the model.
_VIEWPORT_MAX_EDGE = 1568
_PNG_SIGNATURE = b"\x89PNG"


def _visible_mesh_bounds(context) -> tuple[Vector, Vector]:
    """World-space (min, max) of the evaluated visible mesh objects.

    Raises ValueError when there is no visible geometry.
    """
    points = _visible_mesh_points(context)
    if not points:
        raise ValueError("no visible mesh objects")
    low = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    high = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    return low, high


def _visible_mesh_points(context) -> list[Vector]:
    context.view_layer.update()
    depsgraph = context.evaluated_depsgraph_get()
    points: list[Vector] = []
    for obj in context.scene.objects:
        if obj.type != "MESH" or not obj.visible_get() or obj.hide_render:
            continue
        evaluated = obj.evaluated_get(depsgraph)
        if len(evaluated.data.vertices) == 0:
            continue
        matrix = evaluated.matrix_world
        points.extend(matrix @ Vector(corner) for corner in evaluated.bound_box)
    return points


def _camera_rotation(direction: Vector) -> Euler:
    """Euler rotation of a camera at +direction looking at the origin, Z up."""
    # Looking straight down/up has no defined "up" for a track quaternion;
    # use Blender's own top (X right, Y up) and bottom (X right, Y down).
    if abs(direction.z) > 0.999999:
        return Euler((0.0 if direction.z > 0 else 3.141592653589793, 0.0, 0.0))
    return direction.to_track_quat("Z", "Y").to_euler()


def _frame(view: str, low: Vector, high: Vector):
    """Camera rotation, location, ortho scale and clip range framing the bbox."""
    direction = _VIEW_DIRECTIONS[view]
    rotation = _camera_rotation(direction)
    basis = rotation.to_matrix()
    right, up, back = basis.col[0], basis.col[1], basis.col[2]
    corners = [
        Vector((x, y, z))
        for x in (low.x, high.x)
        for y in (low.y, high.y)
        for z in (low.z, high.z)
    ]
    r = [c.dot(right) for c in corners]
    u = [c.dot(up) for c in corners]
    b = [c.dot(back) for c in corners]
    width = max(r) - min(r)
    height = max(u) - min(u)
    depth = max(b) - min(b)
    ortho_scale = max(max(width, height) * (1.0 + _MARGIN), 1e-3)
    diagonal = (high - low).length
    distance = max(diagonal, 1.0)
    location = (
        right * ((max(r) + min(r)) / 2)
        + up * ((max(u) + min(u)) / 2)
        + back * (max(b) + distance)
    )
    clip_start = distance * 0.5
    clip_end = distance * 1.5 + depth
    return rotation, location, ortho_scale, clip_start, clip_end


def _render_still(path: Path) -> None:
    """Render the scene and save the result to path in the scene's image format.

    Saving from the Render Result (instead of write_still) avoids Blender
    logging a "Saved: ..." line for every screenshot.
    """
    result = bpy.ops.render.render()
    if "FINISHED" not in result:
        raise RuntimeError(f"Workbench render did not finish: {result}")
    bpy.data.images["Render Result"].save_render(str(path))


def _render_view(view: str, size: int) -> bytes:
    context = bpy.context
    scene = context.scene
    low, high = _visible_mesh_bounds(context)
    rotation, location, ortho_scale, clip_start, clip_end = _frame(view, low, high)

    render = scene.render
    shading = scene.display.shading
    saved = {
        "camera": scene.camera,
        "engine": render.engine,
        "resolution_x": render.resolution_x,
        "resolution_y": render.resolution_y,
        "resolution_percentage": render.resolution_percentage,
        "file_format": render.image_settings.file_format,
        "color_mode": render.image_settings.color_mode,
        "film_transparent": render.film_transparent,
        "use_border": render.use_border,
        "light": shading.light,
    }
    # The eye icon hides an object in the viewport only; a render would still
    # draw it, so hidden objects are excluded from the render and restored.
    hidden = [o for o in scene.objects if not o.visible_get() and not o.hide_render]

    cam_data = None
    cam_obj = None
    tmp_dir = tempfile.mkdtemp(prefix="blendercraft-shot-")
    try:
        for obj in hidden:
            obj.hide_render = True
        cam_data = bpy.data.cameras.new("BlenderCraftShot")
        cam_data.type = "ORTHO"
        cam_data.ortho_scale = ortho_scale
        cam_data.clip_start = clip_start
        cam_data.clip_end = clip_end
        cam_obj = bpy.data.objects.new("BlenderCraftShot", cam_data)
        scene.collection.objects.link(cam_obj)
        cam_obj.location = location
        cam_obj.rotation_euler = rotation

        scene.camera = cam_obj
        render.engine = "BLENDER_WORKBENCH"
        shading.light = "STUDIO"
        render.resolution_x = size
        render.resolution_y = size
        render.resolution_percentage = 100
        render.film_transparent = False
        render.use_border = False
        render.image_settings.file_format = "PNG"
        render.image_settings.color_mode = "RGB"
        path = Path(tmp_dir) / "shot.png"
        context.view_layer.update()

        try:
            _render_still(path)
        except RuntimeError:
            raise
        except Exception as exc:  # noqa: BLE001 - e.g. no GPU context in background
            raise RuntimeError(f"Workbench render failed: {exc}") from exc
        return path.read_bytes()
    finally:
        # Restore even on error. Each step is guarded so one failure does not
        # leave the others undone.
        for step in (
            lambda: setattr(scene, "camera", saved["camera"]),
            lambda: setattr(render, "engine", saved["engine"]),
            lambda: setattr(render, "resolution_x", saved["resolution_x"]),
            lambda: setattr(render, "resolution_y", saved["resolution_y"]),
            lambda: setattr(render, "resolution_percentage", saved["resolution_percentage"]),
            lambda: setattr(render.image_settings, "file_format", saved["file_format"]),
            lambda: setattr(render.image_settings, "color_mode", saved["color_mode"]),
            lambda: setattr(render, "film_transparent", saved["film_transparent"]),
            lambda: setattr(render, "use_border", saved["use_border"]),
            lambda: setattr(shading, "light", saved["light"]),
            lambda: [setattr(o, "hide_render", False) for o in hidden],
            lambda: bpy.data.objects.remove(cam_obj, do_unlink=True) if cam_obj else None,
            lambda: bpy.data.cameras.remove(cam_data) if cam_data else None,
            lambda: shutil.rmtree(tmp_dir, ignore_errors=True),
        ):
            try:
                step()
            except Exception:  # noqa: BLE001 - best-effort restore
                traceback.print_exc()


def _largest_view3d_area():
    """(window, area, region) of the largest 3D View, or None without a UI."""
    best = None
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != "VIEW_3D":
                continue
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            if region is None:
                continue
            if best is None or area.width * area.height > best[1].width * best[1].height:
                best = (window, area, region)
    return best


def _has_ui() -> bool:
    return not bpy.app.background


def _run_screenshot_area(path: Path):
    """Run the screenshot operator on the largest 3D View; returns its result set."""
    found = _largest_view3d_area()
    if found is None:
        return {"CANCELLED"}
    window, area, region = found
    with bpy.context.temp_override(window=window, area=area, region=region):
        return bpy.ops.screen.screenshot_area(filepath=str(path))


def _fit_png(path: Path) -> bytes:
    """PNG bytes of the image at path, long edge capped at _VIEWPORT_MAX_EDGE.

    The operator follows the scene's image format, so the file is re-encoded
    as PNG whenever it is not one already.
    """
    data = path.read_bytes()
    image = bpy.data.images.load(str(path))
    try:
        width, height = image.size
        longest = max(width, height)
        if data.startswith(_PNG_SIGNATURE) and longest <= _VIEWPORT_MAX_EDGE:
            return data
        if longest > _VIEWPORT_MAX_EDGE:
            factor = _VIEWPORT_MAX_EDGE / longest
            image.scale(max(1, round(width * factor)), max(1, round(height * factor)))
        out = path.with_name("fitted.png")
        image.file_format = "PNG"
        image.filepath_raw = str(out)
        image.save()
        return out.read_bytes()
    finally:
        bpy.data.images.remove(image)


def _viewport_screenshot() -> bytes | None:
    """PNG of the largest 3D View, or None when it cannot be captured."""
    if not _has_ui():
        return None
    tmp_dir = tempfile.mkdtemp(prefix="blendercraft-shot-")
    try:
        path = Path(tmp_dir) / "viewport.png"
        try:
            result = _run_screenshot_area(path)
        except RuntimeError:
            return None  # operator unavailable in this context
        if "FINISHED" not in result or not path.is_file():
            return None
        data = _fit_png(path)
        return data if data.startswith(_PNG_SIGNATURE) else None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def screenshot(view: str = "viewport", size: int = _DEFAULT_SIZE) -> bytes:
    """PNG bytes of the viewport or a fixed view. See _screenshot for the label."""
    return _screenshot(view, size)[1]


def _screenshot(view: str, size: int) -> tuple[str, bytes]:
    if view not in VIEWS:
        raise ValueError(f"Unknown view: {view!r}. Valid views: {', '.join(VIEWS)}")
    if not _MIN_SIZE <= size <= _MAX_SIZE:
        raise ValueError(f"size must be between {_MIN_SIZE} and {_MAX_SIZE}, got {size}")
    if view == "viewport":
        data = _viewport_screenshot()
        if data is not None:
            return "viewport", data
        view = "iso"  # no interface (background mode)
    return view, _render_view(view, size)


def _screenshot_tool(args: dict) -> ToolResult:
    view = args.get("view")
    size = args.get("size")
    try:
        try:
            size = _DEFAULT_SIZE if size is None else int(size)
        except (TypeError, ValueError):
            raise ValueError(f"size must be an integer, got {size!r}") from None
        label, data = _screenshot("viewport" if view is None else str(view), size)
    except ValueError as exc:
        return ToolResult(text=str(exc), is_error=True)
    return ToolResult(text=f"vista: {label}", images=[data])


register_tool(
    Tool(
        name="screenshot",
        description=(
            "Take a PNG screenshot to check the design. view: 'viewport' "
            "(the current 3D View exactly as the user sees it; falls back to "
            "'iso' when Blender has no interface), or a fixed orthographic "
            "view: front, back, left, right, top, bottom, iso (front-right-"
            "top). Fixed views are Workbench renders framing all visible "
            "mesh objects with a 10% margin, size x size pixels (default "
            "768, 16-2048; ignored for 'viewport', whose long edge is capped at 1568). Use several angles to "
            "judge proportions, joins and orientation. Does not change the "
            "scene."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "view": {"type": "string", "enum": list(VIEWS)},
                "size": {"type": "integer", "minimum": _MIN_SIZE, "maximum": _MAX_SIZE},
            },
        },
        handler=_screenshot_tool,
        kind="read",
    )
)
