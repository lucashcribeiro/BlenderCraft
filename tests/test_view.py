import struct
import tempfile
import unittest
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix

from blendercraft_assistant import tools, units
from blendercraft_assistant.tools import view
from blendercraft_assistant.tools.dispatch import call_tool

tools.load_all()

_PNG_MAGIC = b"\x89PNG"


def _clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for cam in list(bpy.data.cameras):
        bpy.data.cameras.remove(cam)


def _make_cube(name, size=20.0, location=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0)):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=size, matrix=Matrix.Diagonal((*scale, 1.0)))
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.update()
    return obj


def _paint(obj, color):
    mat = bpy.data.materials.new(f"{obj.name}-mat")
    mat.diffuse_color = color
    obj.data.materials.append(mat)


def _pixels(png: bytes) -> list[float]:
    """Decoded RGBA pixels; PNG bytes also embed render metadata (e.g. time)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "shot.png"
        path.write_bytes(png)
        image = bpy.data.images.load(str(path))
        try:
            return list(image.pixels)
        finally:
            bpy.data.images.remove(image)


def _png_size(data: bytes) -> tuple[int, int]:
    # IHDR: 8-byte signature, 4-byte length, b"IHDR", width, height.
    return struct.unpack(">II", data[16:24])


def _snapshot(scene):
    r = scene.render
    return {
        "camera": scene.camera,
        "engine": r.engine,
        "res": (r.resolution_x, r.resolution_y, r.resolution_percentage),
        "filepath": r.filepath,
        "format": r.image_settings.file_format,
        "color_mode": r.image_settings.color_mode,
        "transparent": r.film_transparent,
        "use_border": r.use_border,
        "light": scene.display.shading.light,
        "objects": sorted(o.name for o in bpy.data.objects),
        "hide_render": {o.name: o.hide_render for o in bpy.data.objects},
        "cameras": sorted(c.name for c in bpy.data.cameras),
    }


class ViewTestBase(unittest.TestCase):
    def setUp(self):
        _clear_scene()
        units.apply_internal_mm(bpy.context.scene)
        bpy.context.scene.camera = None

    def tearDown(self):
        _clear_scene()

    def shoot(self, name="iso", size=768):
        try:
            return view.screenshot(name, size)
        except RuntimeError as exc:
            self.skipTest(f"Workbench render unavailable in background: {exc}")


class ScreenshotTest(ViewTestBase):
    def test_iso_png(self):
        _make_cube("Cube")
        data = self.shoot("iso")
        self.assertTrue(data.startswith(_PNG_MAGIC))
        self.assertEqual(_png_size(data)[0], 768)

    def test_size_argument(self):
        _make_cube("Cube")
        data = self.shoot("front", 256)
        self.assertEqual(_png_size(data), (256, 256))

    def test_every_fixed_view_renders(self):
        _make_cube("Cube", scale=(2.0, 1.0, 0.5))
        for name in ("front", "back", "left", "right", "top", "bottom", "iso"):
            with self.subTest(view=name):
                data = self.shoot(name, 128)
                self.assertTrue(data.startswith(_PNG_MAGIC))

    def test_viewport_falls_back_to_iso_in_background(self):
        _make_cube("Cube")
        data = self.shoot("viewport", 128)
        self.assertTrue(data.startswith(_PNG_MAGIC))

    def test_restores_scene(self):
        _make_cube("Cube")
        scene = bpy.context.scene
        scene.render.resolution_x = 1234
        scene.render.resolution_y = 567
        scene.render.resolution_percentage = 50
        scene.render.film_transparent = True
        scene.render.filepath = "/tmp/blendercraft-keep-"
        existing = bpy.data.cameras.new("Mine")
        cam_obj = bpy.data.objects.new("MyCam", existing)
        scene.collection.objects.link(cam_obj)
        scene.camera = cam_obj
        before = _snapshot(scene)
        self.shoot("iso", 128)
        self.assertEqual(_snapshot(scene), before)

    def test_restores_scene_on_render_failure(self):
        _make_cube("Cube")
        scene = bpy.context.scene
        before = _snapshot(scene)

        def boom(*args, **kwargs):
            raise RuntimeError("boom")

        real_render = view._render_still
        view._render_still = boom
        try:
            with self.assertRaises(RuntimeError):
                view.screenshot("iso", 128)
        finally:
            view._render_still = real_render
        self.assertEqual(_snapshot(scene), before)

    def test_restores_hide_render_on_render_failure(self):
        _make_cube("Cube")
        hidden = _make_cube("Hidden", location=(0.0, -30.0, 0.0))
        hidden.hide_set(True)
        scene = bpy.context.scene
        before = _snapshot(scene)

        def boom(*args, **kwargs):
            raise RuntimeError("boom")

        real_render = view._render_still
        view._render_still = boom
        try:
            with self.assertRaises(RuntimeError):
                view.screenshot("front", 128)
        finally:
            view._render_still = real_render
        self.assertFalse(hidden.hide_render)
        self.assertTrue(hidden.hide_get())
        self.assertEqual(_snapshot(scene), before)

    def test_eye_hidden_object_is_absent_from_render(self):
        red = (1.0, 0.0, 0.0, 1.0)

        def scene_with_blocker(hidden):
            _clear_scene()
            _make_cube("Cube", size=20)
            blocker = _make_cube("Blocker", size=10, location=(0.0, -30.0, 0.0))
            _paint(blocker, red)
            if hidden:
                blocker.hide_set(True)
            return _pixels(view.screenshot("front", 128))

        def scene_without_blocker():
            _clear_scene()
            _make_cube("Cube", size=20)
            return _pixels(view.screenshot("front", 128))

        try:
            baseline = scene_without_blocker()
        except RuntimeError as exc:
            self.skipTest(f"Workbench render unavailable in background: {exc}")
        # Control: a visible red blocker in front of the cube changes pixels.
        self.assertNotEqual(scene_with_blocker(False), baseline)
        # Hidden with the eye icon: the render must match the scene without it.
        self.assertEqual(scene_with_blocker(True), baseline)

    def test_hidden_objects_are_not_rendered_or_framed(self):
        _make_cube("Cube", size=20)
        far = _make_cube("Far", size=20, location=(5000.0, 0.0, 0.0))
        far.hide_set(True)
        before = far.hide_render
        self.shoot("front", 128)
        self.assertEqual(far.hide_render, before)
        self.assertTrue(far.hide_get())
        low, high = view._visible_mesh_bounds(bpy.context)
        self.assertAlmostEqual(high.x, 10.0, places=3)

    def test_ortho_scale_frames_bbox_with_margin(self):
        _make_cube("Cube", size=20, scale=(2.0, 1.0, 1.0))  # 40 x 20 x 20
        low, high = view._visible_mesh_bounds(bpy.context)
        scale = view._ortho_scale_for("front", low, high)
        self.assertAlmostEqual(scale, 40.0 * 1.1, places=3)
        scale = view._ortho_scale_for("top", low, high)
        self.assertAlmostEqual(scale, 40.0 * 1.1, places=3)


class ViewportTest(ViewTestBase):
    """The viewport path needs a UI; its seams are patched to run in -b."""

    def setUp(self):
        super().setUp()
        self._saved = (view._has_ui, view._run_screenshot_area)
        view._has_ui = lambda: True

    def tearDown(self):
        view._has_ui, view._run_screenshot_area = self._saved
        super().tearDown()

    def _fake_capture(self, pixels):
        """Operator stand-in that 'captures' a real PNG of pixels x pixels."""
        source = self.shoot("front", pixels)

        def run(path):
            path.write_bytes(source)
            return {"FINISHED"}

        view._run_screenshot_area = run

    def test_small_capture_is_returned_unchanged(self):
        _make_cube("Cube")
        self._fake_capture(256)
        label, data = view._screenshot("viewport", 768)
        self.assertEqual(label, "viewport")
        self.assertEqual(_png_size(data), (256, 256))

    def test_large_capture_is_downscaled(self):
        _make_cube("Cube")
        self._fake_capture(2000)
        label, data = view._screenshot("viewport", 768)
        self.assertEqual(label, "viewport")
        self.assertTrue(data.startswith(_PNG_MAGIC))
        self.assertEqual(_png_size(data), (1568, 1568))
        self.assertEqual(
            [i.name for i in bpy.data.images if i.type == "IMAGE"], []
        )

    def test_non_png_capture_is_converted(self):
        _make_cube("Cube")
        scene = bpy.context.scene
        before = scene.render.image_settings.file_format

        def run(path):
            image = bpy.data.images.new("tmp", 64, 64)
            image.file_format = "JPEG"
            image.filepath_raw = str(path)
            image.save()
            bpy.data.images.remove(image)
            return {"FINISHED"}

        view._run_screenshot_area = run
        label, data = view._screenshot("viewport", 768)
        self.assertEqual(label, "viewport")
        self.assertTrue(data.startswith(_PNG_MAGIC))
        self.assertEqual(scene.render.image_settings.file_format, before)

    def test_cancelled_operator_falls_back_to_iso(self):
        _make_cube("Cube")
        view._run_screenshot_area = lambda path: {"CANCELLED"}
        label, data = view._screenshot("viewport", 128)
        self.assertEqual(label, "iso")
        self.assertEqual(_png_size(data), (128, 128))

    def test_missing_file_falls_back_to_iso(self):
        _make_cube("Cube")
        view._run_screenshot_area = lambda path: {"FINISHED"}
        label, _data = view._screenshot("viewport", 128)
        self.assertEqual(label, "iso")

    def test_operator_error_falls_back_to_iso(self):
        _make_cube("Cube")

        def run(path):
            raise RuntimeError("context is incorrect")

        view._run_screenshot_area = run
        label, _data = view._screenshot("viewport", 128)
        self.assertEqual(label, "iso")

    def test_tool_text_reports_fallback(self):
        _make_cube("Cube")
        view._run_screenshot_area = lambda path: {"CANCELLED"}
        result = call_tool("screenshot", {"view": "viewport", "size": 128})
        self.assertEqual(result.text, "vista: iso")


class ScreenshotToolTest(ViewTestBase):
    def test_unknown_view_error(self):
        _make_cube("Cube")
        with self.assertRaises(ValueError):
            view.screenshot("sideways", 128)
        result = call_tool("screenshot", {"view": "sideways"})
        self.assertTrue(result.is_error)
        self.assertIn("sideways", result.text)
        self.assertEqual(result.images, [])

    def test_bad_size_error(self):
        _make_cube("Cube")
        result = call_tool("screenshot", {"size": 0})
        self.assertTrue(result.is_error)

    def test_size_above_cap_is_error(self):
        _make_cube("Cube")
        result = call_tool("screenshot", {"size": 4096})
        self.assertTrue(result.is_error)
        self.assertIn("2048", result.text)
        self.assertEqual(result.images, [])

    def test_max_size_renders(self):
        _make_cube("Cube")
        data = self.shoot("front", 2048)
        self.assertEqual(_png_size(data), (2048, 2048))

    def test_empty_scene_error(self):
        result = call_tool("screenshot", {"view": "iso"})
        self.assertTrue(result.is_error)
        self.assertIn("no visible mesh objects", result.text)

    def test_tool_returns_image(self):
        _make_cube("Cube")
        result = call_tool("screenshot", {"view": "top", "size": 128})
        if result.is_error and "Workbench" in result.text:
            self.skipTest(result.text)
        self.assertFalse(result.is_error, result.text)
        self.assertEqual(result.text, "vista: top")
        self.assertEqual(len(result.images), 1)
        self.assertTrue(result.images[0].startswith(_PNG_MAGIC))

    def test_tool_is_registered_read(self):
        from blendercraft_assistant.tools.base import REGISTRY

        self.assertEqual(REGISTRY["screenshot"].kind, "read")
