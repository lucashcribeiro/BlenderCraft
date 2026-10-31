"""generate_3d_model / generation_status tools with a fake provider
transport: import of a local GLB/STL, scaling, bed placement, undo step,
missing key, online access off, and the key never leaking."""

import contextlib
import io
import json
import os
import tempfile
import threading
import time
import tomllib
import unittest
from pathlib import Path
from unittest import mock

import bmesh
import bpy

from blendercraft_assistant import mainthread, runtime, tools
from blendercraft_assistant.gen3d import meshy, tripo
from blendercraft_assistant.gen3d.jobs import registry
from blendercraft_assistant.tools import dispatch, paths
from blendercraft_assistant.tools.base import REGISTRY, Tool, ToolResult
from blendercraft_assistant.undo import ChangeTracker

from test_gen3d import FakeClock, FakeTransport, _task, reply

tools.load_all()
from blendercraft_assistant.tools import gen3d as gen3d_tool  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
KEY = "msy-SECRET-key-123"
M = meshy.BASE_URL


def _quiet():
    return contextlib.redirect_stdout(io.StringIO())


def _clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)


def _make_fixture(directory: Path) -> tuple[Path, Path]:
    """A 10 x 6 x 4 box off-centre, as GLB and STL (local fixtures)."""
    _clear_scene()
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(10.0, 6.0, 4.0), verts=bm.verts)
    mesh = bpy.data.meshes.new("Fixture")
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("Fixture", mesh)
    obj.location = (30.0, -12.0, 7.0)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.update()
    for o in bpy.context.view_layer.objects:
        o.select_set(o is obj)
    glb, stl = directory / "fixture.glb", directory / "fixture.stl"
    with _quiet():
        bpy.ops.export_scene.gltf(filepath=str(glb), export_format="GLB", use_selection=True)
        bpy.ops.wm.stl_export(filepath=str(stl), export_selected_objects=True)
    _clear_scene()
    return glb, stl


def pump_until(cond, timeout=20.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        mainthread.pump()
        if cond():
            return True
        time.sleep(0.01)
    return False


def call_in_thread(name, args, tracker=None):
    box = {}

    def worker():
        box["result"] = dispatch.call_tool(name, args, tracker)

    thread = threading.Thread(target=worker)
    thread.start()
    assert pump_until(lambda: "result" in box), "tool call did not return"
    thread.join(5)
    return box["result"]


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures_tmp = tempfile.TemporaryDirectory()
        cls.glb, cls.stl = _make_fixture(Path(cls.fixtures_tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls.fixtures_tmp.cleanup()

    def setUp(self):
        _clear_scene()
        bpy.context.scene.unit_settings.system = "METRIC"
        bpy.context.scene.unit_settings.scale_length = 0.001
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.clock = FakeClock()
        self.patches = [
            mock.patch.object(gen3d_tool, "_online_access", return_value=True),
            mock.patch.object(gen3d_tool, "_addon_prefs", return_value=None),
            mock.patch.object(paths, "DEFAULT_DIR", self.dir / "docs"),
            mock.patch.object(paths, "_blend_filepath", return_value=""),
            mock.patch.dict(os.environ, {"MESHY_API_KEY": KEY}, clear=False),
            mock.patch.object(gen3d_tool, "provider_kwargs",
                              {"wait": self.clock.wait, "clock": self.clock.clock}),
        ]
        os.environ.pop("TRIPO_API_KEY", None)
        for p in self.patches:
            p.start()

    def tearDown(self):
        registry.cancel_all()
        for p in reversed(self.patches):
            p.stop()
        gen3d_tool.transport_factory = None
        _clear_scene()
        self.tmp.cleanup()

    def meshy_text_transport(self, glb_bytes=None, stl_bytes=None):
        urls = {"glb": "https://assets.meshy.ai/m.glb", "stl": "https://assets.meshy.ai/m.stl"}
        ft = FakeTransport(
            [
                ("POST", M + meshy.TEXT_PATH, reply({"result": "p1"})),
                ("GET", f"{M}{meshy.TEXT_PATH}/p1", reply(_task("IN_PROGRESS", 50))),
                ("GET", f"{M}{meshy.TEXT_PATH}/p1",
                 reply(_task("SUCCEEDED", 100, consumed_credits=20, model_urls=urls))),
            ],
            files={
                urls["glb"]: glb_bytes if glb_bytes is not None else self.glb.read_bytes(),
                urls["stl"]: stl_bytes if stl_bytes is not None else self.stl.read_bytes(),
            },
        )
        gen3d_tool.transport_factory = lambda: ft
        return ft


class ImportModelTest(_Base):
    def _check_placed(self, info, height):
        obj = bpy.data.objects[info["object"]]
        self.assertEqual(obj.type, "MESH")
        self.assertAlmostEqual(info["dimensions_mm"][2], height, places=3)
        # Proportions kept (10 x 6 x 4 box).
        self.assertAlmostEqual(info["dimensions_mm"][0], height * 10 / 4, places=2)
        world = [obj.matrix_world @ v.co for v in obj.data.vertices]
        self.assertAlmostEqual(min(v.z for v in world), 0.0, places=4)
        self.assertAlmostEqual(min(v.x for v in world) + max(v.x for v in world), 0.0, places=4)
        self.assertAlmostEqual(min(v.y for v in world) + max(v.y for v in world), 0.0, places=4)
        self.assertEqual(tuple(obj.location), (0.0, 0.0, 0.0))
        self.assertIsNone(obj.parent)
        self.assertEqual(len(bpy.data.objects), 1)  # glTF empties removed
        self.assertTrue(obj.select_get())

    def test_glb_scaled_to_height_and_on_bed(self):
        with _quiet():
            info = gen3d_tool.import_model(self.glb, 50.0, "Gerado: caixa")
        self.assertEqual(info["object"], "Gerado: caixa")
        self._check_placed(info, 50.0)

    def test_stl_and_other_unit_scale(self):
        bpy.context.scene.unit_settings.scale_length = 0.01  # 1 BU = 10 mm
        with _quiet():
            info = gen3d_tool.import_model(self.stl, 30.0, "Gerado: stl")
        self.assertAlmostEqual(bpy.data.objects[info["object"]].dimensions.z, 3.0, places=4)
        self.assertAlmostEqual(info["dimensions_mm"][2], 30.0, places=3)


class GenerateToolTest(_Base):
    def test_text_generation_end_to_end(self):
        ft = self.meshy_text_transport()
        tracker = ChangeTracker()
        with mock.patch.object(dispatch, "push_step") as push, _quiet():
            result = call_in_thread(
                "generate_3d_model",
                {"prompt": "a happy corgi", "target_height_mm": 60, "texture": False},
                tracker,
            )
        self.assertFalse(result.is_error, result.text)
        data = json.loads(result.text)
        self.assertEqual(data["state"], "succeeded")
        res = data["result"]
        self.assertEqual(res["object"], "Gerado: a happy corgi")
        self.assertAlmostEqual(res["dimensions_mm"][2], 60.0, places=3)
        self.assertEqual(res["faces"], 12)  # glTF is triangulated
        self.assertEqual(
            res["mesh_topology"],
            {"components": 1, "manifold": True, "boundary_edges": 0,
             "non_manifold_edges": 0, "inverted_normals": False},
        )
        self.assertEqual(res["provider"], "meshy")
        self.assertEqual(res["consumed_credits"], 20)
        self.assertTrue(res["prompt_sent"].startswith("a happy corgi, collectible vinyl toy"))
        # Downloads kept under <base>/geracoes/<stamp>-<slug>/.
        glb = Path(res["files"]["glb"])
        self.assertTrue(glb.is_file())
        self.assertEqual(glb.parent.parent, self.dir / "docs" / "geracoes")
        self.assertTrue(glb.parent.name.endswith("-a-happy-corgi"))
        self.assertTrue(Path(res["files"]["stl"]).is_file())
        # One undo step, named for the AI, recorded in the tracker.
        push.assert_called_once_with("gerar modelo 3D")
        self.assertEqual(tracker.ai_steps, 1)
        # The request the provider got.
        body = ft.bodies()[0]
        self.assertEqual(body["mode"], "preview")
        self.assertEqual(body["target_formats"], ["glb", "stl"])
        self.assertNotIn(KEY, result.text)

    def test_status_while_running_then_done(self):
        gate = threading.Event()
        ft = self.meshy_text_transport()
        ft.on_request = lambda method, url: gate.wait(10) if method == "GET" else None
        with mock.patch.object(gen3d_tool, "WAIT_S", 0.2), _quiet():
            first = call_in_thread(
                "generate_3d_model", {"prompt": "dog", "target_height_mm": 40, "texture": False}
            )
            data = json.loads(first.text)
            self.assertEqual(data["state"], "running")
            self.assertIn("generation_status", data["next"])
            gate.set()
            job_id = data["job_id"]
            self.assertTrue(pump_until(lambda: registry.get(job_id).done.is_set()))
            second = call_in_thread("generation_status", {"job_id": job_id, "wait_seconds": 5})
        done = json.loads(second.text)
        self.assertEqual(done["state"], "succeeded")
        self.assertAlmostEqual(done["result"]["dimensions_mm"][2], 40.0, places=3)

    def test_status_cancel_and_unknown_job(self):
        gate = threading.Event()
        ft = self.meshy_text_transport()
        ft.on_request = lambda method, url: gate.wait(0.5) if method == "GET" else None
        with mock.patch.object(gen3d_tool, "WAIT_S", 0.1), _quiet():
            data = json.loads(call_in_thread(
                "generate_3d_model", {"prompt": "dog", "target_height_mm": 40}).text)
            stopped = call_in_thread("generation_status", {"job_id": data["job_id"], "cancel": True})
        self.assertEqual(json.loads(stopped.text)["state"], "cancelled")
        self.assertEqual(len(bpy.data.objects), 0)
        unknown = call_in_thread("generation_status", {"job_id": "gen-nope"})
        self.assertTrue(unknown.is_error)

    def test_stop_cancels_running_jobs(self):
        with mock.patch.object(registry, "cancel_all") as cancel_all:
            runtime._deny_pending("stopped")
        cancel_all.assert_called_once()

    def test_missing_key_points_to_preferences(self):
        os.environ.pop("MESHY_API_KEY", None)
        result = call_in_thread("generate_3d_model", {"prompt": "dog", "target_height_mm": 40})
        self.assertTrue(result.is_error)
        self.assertIn("MESHY_API_KEY", result.text)
        self.assertIn("BlenderCraft Assistant", result.text)
        result = call_in_thread(
            "generate_3d_model", {"prompt": "dog", "target_height_mm": 40, "provider": "tripo"}
        )
        self.assertIn("TRIPO_API_KEY", result.text)

    def test_online_access_off(self):
        with mock.patch.object(gen3d_tool, "_online_access", return_value=False):
            result = call_in_thread("generate_3d_model", {"prompt": "dog", "target_height_mm": 40})
        self.assertTrue(result.is_error)
        self.assertTrue("Online Access" in result.text or "acesso online" in result.text)
        self.assertEqual(registry.running(), [])

    def test_bad_arguments(self):
        for args in (
            {"prompt": "dog"},
            {"target_height_mm": 40},
            {"prompt": "dog", "target_height_mm": -1},
            {"prompt": "dog", "target_height_mm": 40, "style": "cartoon"},
            {"prompt": "dog", "target_height_mm": 40, "provider": "rodin"},
            {"image_paths": ["a.png"] * 5, "target_height_mm": 40},
            {"prompt": "dog", "target_height_mm": 40, "provider": "tripo", "print_repair": True},
        ):
            os.environ["TRIPO_API_KEY"] = "tsk_x"
            result = call_in_thread("generate_3d_model", args)
            self.assertTrue(result.is_error, args)

    def test_key_never_in_results_or_output(self):
        ft = FakeTransport(
            [("POST", M + meshy.TEXT_PATH,
              reply({"message": f"Invalid API key {KEY}"}, 401))]
        )
        gen3d_tool.transport_factory = lambda: ft
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = call_in_thread("generate_3d_model", {"prompt": "dog", "target_height_mm": 40})
        self.assertTrue(result.is_error)
        data = json.loads(result.text)
        self.assertEqual(data["error_code"], "auth")
        self.assertIn("user_message", data)
        for text in (result.text, out.getvalue(), err.getvalue()):
            self.assertNotIn(KEY, text)
        # The key went only to the provider, in the Authorization header.
        self.assertEqual(ft.calls[0]["headers"]["Authorization"], f"Bearer {KEY}")

    def test_image_paths_resolved_and_sent(self):
        photo = self.dir / "docs" / "dog.png"
        photo.parent.mkdir(parents=True, exist_ok=True)
        photo.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        urls = {"glb": "https://assets.meshy.ai/m.glb"}
        ft = FakeTransport(
            [
                ("POST", M + meshy.IMAGE_PATH, reply({"result": "i1"})),
                ("GET", f"{M}{meshy.IMAGE_PATH}/i1",
                 reply(_task("SUCCEEDED", 100, model_urls=urls))),
            ],
            files={urls["glb"]: self.glb.read_bytes()},
        )
        gen3d_tool.transport_factory = lambda: ft
        with mock.patch.object(dispatch, "push_step"), _quiet():
            result = call_in_thread(
                "generate_3d_model", {"image_paths": ["dog.png"], "target_height_mm": 25}
            )
        data = json.loads(result.text)
        self.assertEqual(data["state"], "succeeded", result.text)
        self.assertEqual(data["result"]["object"], "Gerado: dog")
        self.assertTrue(ft.bodies()[0]["image_url"].startswith("data:image/png;base64,"))
        self.assertIsNone(data["result"]["prompt_sent"])

    def test_tripo_region_from_prefs(self):
        os.environ["TRIPO_API_KEY"] = "tsk_x"
        prefs = mock.Mock(meshy_api_key="", tripo_api_key="", gen3d_provider="TRIPO",
                          tripo_region="COM")
        host = tripo.HOSTS["com"]
        ft = FakeTransport(
            [
                ("POST", host + "/generation/text-to-model",
                 reply({"code": 0, "data": {"task_id": "t1"}})),
                ("GET", host + "/tasks/t1", reply({"code": 0, "data": {
                    "status": "success", "output": {"model_url": "https://cdn.tripo3d.ai/o.glb"}}})),
            ],
            files={"https://cdn.tripo3d.ai/o.glb": self.glb.read_bytes()},
        )
        gen3d_tool.transport_factory = lambda: ft
        with mock.patch.object(gen3d_tool, "_addon_prefs", return_value=prefs), \
                mock.patch.object(dispatch, "push_step"), _quiet():
            result = call_in_thread("generate_3d_model", {"prompt": "cat", "target_height_mm": 30})
        data = json.loads(result.text)
        self.assertEqual(data["state"], "succeeded", result.text)
        self.assertEqual(data["result"]["provider"], "tripo")
        self.assertEqual(ft.bodies()[0]["negative_prompt"][:20], "thin fragile parts, ")

    def test_tool_flags(self):
        gen = REGISTRY["generate_3d_model"]
        self.assertEqual((gen.kind, gen.push_undo, gen.main_thread), ("scene", False, False))
        status = REGISTRY["generation_status"]
        self.assertEqual((status.kind, status.main_thread), ("read", False))


class OffMainDispatchTest(unittest.TestCase):
    def test_runtime_runs_off_main_tools_on_the_server_thread(self):
        seen = {}

        def handler(args):
            seen["thread"] = threading.current_thread()
            seen["tracker"] = dispatch.current_tracker()
            return ToolResult(text="ok")

        REGISTRY["_offmain_probe"] = Tool("_offmain_probe", "", {}, handler, "scene",
                                          main_thread=False)
        box = {}
        try:
            with mock.patch.object(dispatch, "push_step") as push:
                thread = threading.Thread(
                    target=lambda: box.setdefault("r", runtime._call("_offmain_probe", {}))
                )
                thread.start()
                thread.join(5)  # no pump: the main thread is not needed
                self.assertEqual(box["r"].text, "ok")
                self.assertIs(seen["thread"], thread)
                push.assert_not_called()  # the handler pushes its own step
        finally:
            del REGISTRY["_offmain_probe"]


class ManifestAndPrefsTest(unittest.TestCase):
    def test_manifest_declares_network_permission(self):
        manifest = tomllib.loads(
            (ROOT / "blendercraft_assistant" / "blender_manifest.toml").read_text()
        )
        self.assertIn("network", manifest["permissions"])

    def test_keys_are_password_fields(self):
        from blendercraft_assistant.ui.prefs import BC_AddonPreferences

        props = BC_AddonPreferences.__annotations__
        for name in ("meshy_api_key", "tripo_api_key"):
            self.assertEqual(props[name].keywords["subtype"], "PASSWORD")


if __name__ == "__main__":
    unittest.main()
