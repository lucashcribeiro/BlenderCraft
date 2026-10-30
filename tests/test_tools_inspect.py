import json
import math
import unittest
from unittest import mock

import bpy
from mathutils import Euler

from blendercraft_assistant import tools, units
from blendercraft_assistant.tools import base
from blendercraft_assistant.tools.base import (
    REGISTRY,
    Tool,
    ToolResult,
    register_tool,
    tools_for_mode,
    truncate,
)
from blendercraft_assistant.tools import dispatch
from blendercraft_assistant.tools.dispatch import call_tool
from blendercraft_assistant.undo import ChangeTracker

tools.load_all()

_SCHEMA = {"type": "object", "properties": {}}


def _ok(_args):
    return ToolResult(text="ok")


def _boom(_args):
    raise KeyError("missing-thing")


def _tool(name, kind="read", handler=_ok, **kw):
    return Tool(name, f"test tool {name}", _SCHEMA, handler, kind, **kw)


def _clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def _add_cube(name, size_mm, x_mm=0.0):
    bpy.ops.mesh.primitive_cube_add(size=size_mm, location=(x_mm, 0, 0))
    obj = bpy.context.active_object
    obj.name = name
    return obj


class RegistryTestCase(unittest.TestCase):
    """Keeps test-only tools out of the global registry."""

    def setUp(self):
        self._before = set(REGISTRY)

    def tearDown(self):
        for name in list(REGISTRY):
            if name not in self._before:
                del REGISTRY[name]


class BaseTest(RegistryTestCase):
    def test_register_duplicate(self):
        register_tool(_tool("t_dup"))
        with self.assertRaises(ValueError):
            register_tool(_tool("t_dup"))

    def test_readonly_filter(self):
        register_tool(_tool("t_read", "read"))
        register_tool(_tool("t_scene", "scene"))
        register_tool(_tool("t_file", "file"))
        names = {t.name for t in tools_for_mode("READONLY")}
        self.assertIn("t_read", names)
        self.assertNotIn("t_scene", names)
        self.assertNotIn("t_file", names)
        self.assertTrue(all(t.kind == "read" for t in tools_for_mode("READONLY")))
        all_names = {t.name for t in tools_for_mode("AUTO")}
        self.assertTrue({"t_read", "t_scene", "t_file"} <= all_names)

    def test_truncate(self):
        self.assertEqual(truncate("abc", 10), "abc")
        out = truncate("x" * 20, 8)
        self.assertEqual(out, "x" * 8 + "\n…[truncado 12 caracteres]")

    def test_base_does_not_import_bpy(self):
        self.assertNotIn("bpy", vars(base))


class DispatchTest(RegistryTestCase):
    def test_unknown_tool_is_error(self):
        res = call_tool("no_such_tool", {})
        self.assertTrue(res.is_error)
        self.assertIn("no_such_tool", res.text)

    def test_exception_becomes_error(self):
        register_tool(_tool("t_boom", handler=_boom))
        res = call_tool("t_boom", {})
        self.assertTrue(res.is_error)
        self.assertIn("KeyError", res.text)

    def test_output_is_truncated(self):
        register_tool(
            _tool("t_big", handler=lambda a: ToolResult(text="y" * 9000))
        )
        res = call_tool("t_big", {})
        self.assertIn("truncado", res.text)
        self.assertLess(len(res.text), 8100)

    def test_scene_tool_records_step_and_runs_in_scope(self):
        tracker = ChangeTracker()
        seen = {}

        def handler(args):
            seen["depth"] = tracker._scope_depth
            return ToolResult(text="done")

        register_tool(_tool("t_scene2", "scene", handler))
        res = call_tool("t_scene2", {"description": "x"}, tracker)
        self.assertFalse(res.is_error)
        self.assertEqual(seen["depth"], 1)
        self.assertEqual(tracker._ai_steps, 1)

    def test_read_tool_records_no_step(self):
        tracker = ChangeTracker()
        register_tool(_tool("t_read2", "read"))
        call_tool("t_read2", {}, tracker)
        self.assertEqual(tracker._ai_steps, 0)

    def test_push_undo_false_records_no_step(self):
        tracker = ChangeTracker()
        register_tool(_tool("t_scene3", "scene", push_undo=False))
        call_tool("t_scene3", {}, tracker)
        self.assertEqual(tracker._ai_steps, 0)

    def test_undo_push_failure_is_error_and_keeps_handler_text(self):
        register_tool(
            _tool("t_scene5", "scene", lambda a: ToolResult(text="handler said hi"))
        )
        with mock.patch.object(dispatch, "push_step", side_effect=RuntimeError("no undo")):
            res = call_tool("t_scene5", {}, ChangeTracker())
        self.assertTrue(res.is_error)
        self.assertIn("handler said hi", res.text)
        self.assertIn("no undo", res.text)

    def test_scene_tool_without_tracker_works(self):
        register_tool(_tool("t_scene4", "scene"))
        self.assertFalse(call_tool("t_scene4", {}).is_error)


class IntrospectTest(unittest.TestCase):
    def test_list_operators_finds_bevel(self):
        res = call_tool("list_operators", {"query": "bevel"})
        self.assertFalse(res.is_error, res.text)
        ids = [o["id"] for o in json.loads(res.text)]
        self.assertIn("mesh.bevel", ids)

    def test_list_operators_limit(self):
        res = call_tool("list_operators", {"query": "mesh", "limit": 3})
        self.assertEqual(len(json.loads(res.text)), 3)

    def test_list_operators_nonpositive_limit_is_empty(self):
        for limit in (0, -5):
            res = call_tool("list_operators", {"query": "mesh", "limit": limit})
            self.assertEqual(json.loads(res.text), [])

    def test_describe_bevel_has_width(self):
        # Blender 5.2 renamed mesh.bevel's "width" to "offset".
        res = call_tool("describe_operator", {"id": "mesh.bevel"})
        self.assertFalse(res.is_error, res.text)
        data = json.loads(res.text)
        self.assertEqual(data["id"], "mesh.bevel")
        params = {p["name"]: p for p in data["params"]}
        self.assertIn("offset", params)
        self.assertEqual(params["offset"]["type"], "FLOAT")
        self.assertEqual(params["offset"]["default"], 0.0)
        self.assertIn("min", params["offset"])
        self.assertEqual(params["profile"]["default"], 0.5)
        self.assertIn("affect", params)
        self.assertIn("items", params["affect"])

    def test_describe_unknown_operator_is_error(self):
        self.assertTrue(call_tool("describe_operator", {"id": "mesh.nope"}).is_error)
        self.assertTrue(call_tool("describe_operator", {"id": "nodot"}).is_error)


class InspectTest(unittest.TestCase):
    def setUp(self):
        _clear_scene()
        units.apply_internal_mm(bpy.context.scene)

    def tearDown(self):
        _clear_scene()

    def test_inspect_scene_units(self):
        _add_cube("Caixa", 20.0)
        res = call_tool("inspect_scene", {})
        self.assertFalse(res.is_error, res.text)
        data = json.loads(res.text)
        self.assertEqual(data["unit"], "MM")
        self.assertEqual(data["active"], "Caixa")
        obj = data["objects"][0]
        self.assertEqual(obj["name"], "Caixa")
        self.assertEqual(obj["type"], "MESH")
        dims = " ".join(obj["dimensions"])
        self.assertIn("20", dims)
        self.assertIn("mm", dims)
        for key in ("parent", "location", "rotation_deg", "scale", "materials",
                    "modifiers", "visible", "selected"):
            self.assertIn(key, obj)

    def test_rotation_deg_in_quaternion_mode(self):
        obj = _add_cube("Q", 20.0)
        obj.rotation_mode = "QUATERNION"
        obj.rotation_quaternion = Euler((0.0, 0.0, math.pi / 2)).to_quaternion()
        data = json.loads(call_tool("inspect_object", {"name": "Q"}).text)
        x, y, z = data["rotation_deg"]
        self.assertAlmostEqual(x, 0.0, delta=0.01)
        self.assertAlmostEqual(y, 0.0, delta=0.01)
        self.assertAlmostEqual(z, 90.0, delta=0.01)

    def test_rotation_deg_in_euler_mode(self):
        obj = _add_cube("E", 20.0)
        obj.rotation_euler = Euler((0.0, 0.0, math.radians(30)))
        data = json.loads(call_tool("inspect_object", {"name": "E"}).text)
        self.assertAlmostEqual(data["rotation_deg"][2], 30.0, delta=0.01)

    def test_inspect_scene_follows_display_unit(self):
        _add_cube("Caixa", 25.4)
        units.set_display_unit(bpy.context.scene, "IN")
        data = json.loads(call_tool("inspect_scene", {}).text)
        self.assertEqual(data["unit"], "IN")
        self.assertIn("1.000 in", data["objects"][0]["dimensions"][0])

    def test_inspect_object(self):
        _add_cube("Caixa", 20.0, x_mm=100.0)
        res = call_tool("inspect_object", {"name": "Caixa"})
        self.assertFalse(res.is_error, res.text)
        data = json.loads(res.text)
        self.assertEqual(data["vertices"], 8)
        self.assertEqual(data["faces"], 6)
        self.assertIn("90.000 mm", data["bbox_min"][0])
        self.assertIn("110.000 mm", data["bbox_max"][0])

    def test_inspect_object_unknown_is_error(self):
        self.assertTrue(call_tool("inspect_object", {"name": "Nada"}).is_error)

    def test_measure_single(self):
        _add_cube("Caixa", 20.0)
        data = json.loads(call_tool("measure", {"a": "Caixa"}).text)
        self.assertIn("20.000 mm", data["dimensions"][0])
        self.assertAlmostEqual(float(data["volume"].split()[0]), 8000.0, delta=0.01)
        self.assertIn("mm³", data["volume"])
        self.assertAlmostEqual(float(data["area"].split()[0]), 2400.0, delta=0.01)
        self.assertIn("mm²", data["area"])

    def test_measure_gap(self):
        _add_cube("A", 20.0, x_mm=0.0)
        _add_cube("B", 20.0, x_mm=25.0)  # faces 10 and 15: gap of 5 mm
        res = call_tool("measure", {"a": "A", "b": "B"})
        self.assertFalse(res.is_error, res.text)
        data = json.loads(res.text)
        gap = float(data["min_gap"].split()[0])
        self.assertAlmostEqual(gap, 5.0, delta=0.01)
        self.assertIn("mm", data["min_gap"])
        dist = float(data["distance_between_origins"].split()[0])
        self.assertAlmostEqual(dist, 25.0, delta=0.01)

    def test_measure_gap_without_geometry_is_null(self):
        _add_cube("A", 20.0)
        empty = bpy.data.objects.new("Vazio", bpy.data.meshes.new("Vazio"))
        bpy.context.scene.collection.objects.link(empty)
        data = json.loads(call_tool("measure", {"a": "A", "b": "Vazio"}).text)
        self.assertIsNone(data["min_gap"])
        self.assertIn("note", data)

    def test_measure_overlap_gap_is_zero(self):
        _add_cube("A", 20.0, x_mm=0.0)
        _add_cube("B", 20.0, x_mm=10.0)
        data = json.loads(call_tool("measure", {"a": "A", "b": "B"}).text)
        self.assertAlmostEqual(float(data["min_gap"].split()[0]), 0.0, delta=0.001)


if __name__ == "__main__":
    unittest.main()
