import json
import math
import unittest

import bmesh
import bpy
from mathutils import Matrix

from blendercraft_assistant import tools, units
from blendercraft_assistant.tools import joints
from blendercraft_assistant.tools.dispatch import call_tool

tools.load_all()


def _clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def _box(name, size, center):
    """Link a box mesh of the given (x, y, z) size centered at center."""
    bm = bmesh.new()
    bmesh.ops.create_cube(
        bm, size=1.0, matrix=Matrix.Translation(center) @ Matrix.Diagonal((*size, 1.0))
    )
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.update()
    return obj


class JointTestBase(unittest.TestCase):
    def setUp(self):
        _clear_scene()
        units.apply_internal_mm(bpy.context.scene)
        # Bar 10x2x2 from x=0 to x=10, pivot at the origin turning about Z.
        self.bar = _box("Bar", (10, 2, 2), (5, 0, 0))
        self.pivot = joints.create_pivot("Bar", (0, 0, 0), "Z")

    def tearDown(self):
        _clear_scene()

    def _obstacle(self, center, name="Obstacle"):
        return _box(name, (2, 2, 2), center)

    def _angle(self):
        return math.degrees(bpy.data.objects[self.pivot].rotation_euler.z)


class CreatePivotTest(JointTestBase):
    def test_pivot_properties_and_parenting(self):
        self.assertEqual(self.pivot, "BC_Pivo_Bar")
        pivot = bpy.data.objects[self.pivot]
        self.assertEqual(pivot.type, "EMPTY")
        self.assertEqual(pivot["bc_axis"], "Z")
        self.assertEqual(list(pivot["bc_rest"]), [0.0, 0.0, 0.0])
        self.assertEqual(pivot["bc_angle"], 0.0)
        self.assertIs(self.bar.parent, pivot)

    def test_keeps_part_world_transform(self):
        _clear_scene()
        bar = _box("Bar", (10, 2, 2), (5, 0, 0))
        bar.rotation_euler = (0.0, 0.0, math.radians(30))
        bpy.context.view_layer.update()
        before = bar.matrix_world.copy()
        joints.create_pivot("Bar", (3, 4, 5), "X")
        bpy.context.view_layer.update()
        for row_a, row_b in zip(before, bar.matrix_world):
            for a, b in zip(row_a, row_b):
                self.assertAlmostEqual(a, b, places=5)
        pivot = bpy.data.objects["BC_Pivo_Bar"]
        self.assertEqual(tuple(pivot.matrix_world.translation), (3.0, 4.0, 5.0))

    def test_rejects_bad_axis_and_double_pivot(self):
        _clear_scene()
        _box("Bar", (10, 2, 2), (5, 0, 0))
        with self.assertRaises(ValueError):
            joints.create_pivot("Bar", (0, 0, 0), "W")
        joints.create_pivot("Bar", (0, 0, 0), "Y")
        with self.assertRaises(ValueError):
            joints.create_pivot("Bar", (0, 0, 0), "Y")


class SetAngleTest(JointTestBase):
    def test_rotates_part_about_axis(self):
        joints.set_angle(self.pivot, 90.0)
        bpy.context.view_layer.update()
        far = max(
            (self.bar.matrix_world @ v.co for v in self.bar.data.vertices),
            key=lambda p: p.length,
        )
        self.assertAlmostEqual(far.y, 10.0, places=4)
        self.assertAlmostEqual(abs(far.x), 1.0, places=4)
        self.assertAlmostEqual(bpy.data.objects[self.pivot]["bc_angle"], 90.0)

    def test_reset(self):
        joints.set_angle(self.pivot, 40.0)
        joints.reset(self.pivot)
        self.assertAlmostEqual(self._angle(), 0.0)
        self.assertEqual(bpy.data.objects[self.pivot]["bc_angle"], 0.0)

    def test_no_keyframes(self):
        joints.set_angle(self.pivot, 33.0)
        joints.reset(self.pivot)
        joints.set_angle(self.pivot, -12.0)
        self.assertIsNone(bpy.data.objects[self.pivot].animation_data)
        self.assertIsNone(self.bar.animation_data)

    def test_unknown_pivot(self):
        with self.assertRaises(ValueError):
            joints.set_angle("Nope", 10.0)
        with self.assertRaises(ValueError):
            joints.set_angle("Bar", 10.0)  # exists, but is not a pivot


class JointTestSweep(JointTestBase):
    def test_sweep(self):
        self._obstacle((0, 6, 0))
        result = joints.joint_test(self.pivot)
        self.assertTrue(60 <= result["free_max"] <= 70, result)
        self.assertEqual(result["free_min"], -180.0)
        self.assertIs(result["colliding_at_rest"], False)
        self.assertEqual(len(result["collisions"]), 1)
        hit = result["collisions"][0]
        self.assertEqual(hit["with"], "Obstacle")
        self.assertAlmostEqual(hit["angle"], result["free_max"] + 5.0)

    def test_parked_in_a_colliding_pose(self):
        # The rest check is about angle 0, not the pose the pivot is parked in.
        self._obstacle((0, 6, 0))
        joints.set_angle(self.pivot, 80.0)
        result = joints.joint_test(self.pivot)
        self.assertIs(result["colliding_at_rest"], False)
        self.assertTrue(60 <= result["free_max"] <= 70, result)
        self.assertAlmostEqual(self._angle(), 80.0, places=3)
        self.assertEqual(bpy.data.objects[self.pivot]["bc_angle"], 80.0)

    def test_free_when_nothing_in_the_way(self):
        result = joints.joint_test(self.pivot, start=-90.0, end=45.0, step=10.0)
        self.assertEqual(result["free_min"], -90.0)
        self.assertEqual(result["free_max"], 45.0)
        self.assertEqual(result["collisions"], [])

    def test_restores_angle(self):
        self._obstacle((0, 6, 0))
        joints.set_angle(self.pivot, 25.0)
        before = tuple(bpy.data.objects[self.pivot].rotation_euler)
        joints.joint_test(self.pivot)
        pivot = bpy.data.objects[self.pivot]
        self.assertEqual(tuple(pivot.rotation_euler), before)
        self.assertEqual(pivot["bc_angle"], 25.0)

    def test_error_leaves_angle_untouched(self):
        joints.set_angle(self.pivot, 25.0)
        with self.assertRaises(ValueError):
            joints.joint_test(self.pivot, against=["Missing"])
        self.assertAlmostEqual(self._angle(), 25.0, places=3)

    def test_rest_collision(self):
        # Overlaps the bar's top face near its far end.
        self._obstacle((7, 1.5, 0))
        result = joints.joint_test(self.pivot)
        self.assertIs(result["colliding_at_rest"], True)
        self.assertEqual(result["collisions"][0]["angle"], 0.0)
        self.assertEqual(result["free_min"], 0.0)
        self.assertEqual(result["free_max"], 0.0)

    def test_touching_obstacle_counts_as_rest_collision(self):
        # Obstacle face coplanar with the bar's top face (y = 1).
        self._obstacle((7, 2.0, 0))
        self.assertIs(joints.joint_test(self.pivot)["colliding_at_rest"], True)

    def test_against_filters_objects(self):
        self._obstacle((0, 6, 0), "Hit")
        self._obstacle((0, -6, 0), "Other")
        only_other = joints.joint_test(self.pivot, against=["Other"], start=-90.0, end=90.0)
        self.assertTrue(only_other["free_max"] == 90.0)
        self.assertTrue(only_other["free_min"] < 0.0)
        self.assertEqual(only_other["collisions"][0]["with"], "Other")

    def test_default_ignores_hidden_objects_and_own_part(self):
        hidden = self._obstacle((0, 6, 0))
        hidden.hide_set(True)
        result = joints.joint_test(self.pivot, start=-90.0, end=90.0)
        self.assertEqual(result["free_max"], 90.0)

    def test_children_of_part_move_with_it(self):
        child = _box("Child", (2, 2, 2), (9, 0, 0))
        child.parent = self.bar
        child.matrix_parent_inverse = self.bar.matrix_world.inverted()
        self._obstacle((0, 6, 0))
        result = joints.joint_test(self.pivot)
        self.assertTrue(result["free_max"] < 90.0)
        self.assertNotIn("Child", [c["with"] for c in result["collisions"]])

    def test_invalid_range(self):
        with self.assertRaises(ValueError):
            joints.joint_test(self.pivot, step=0.0)
        with self.assertRaises(ValueError):
            joints.joint_test(self.pivot, start=10.0)
        with self.assertRaises(ValueError):
            joints.joint_test(self.pivot, end=-10.0)


class JointToolsTest(JointTestBase):
    def test_tools_registered_with_kinds(self):
        from blendercraft_assistant.tools.base import REGISTRY

        self.assertEqual(REGISTRY["joint_create"].kind, "scene")
        self.assertEqual(REGISTRY["joint_set_angle"].kind, "scene")
        self.assertTrue(REGISTRY["joint_set_angle"].push_undo)
        self.assertEqual(REGISTRY["joint_test"].kind, "read")

    def test_create_set_angle_and_test_through_dispatch(self):
        _clear_scene()
        _box("Door", (10, 2, 2), (5, 0, 0))
        created = call_tool(
            "joint_create",
            {"part": "Door", "point": [0, 0, 0], "axis": "z", "description": "pivot"},
        )
        self.assertFalse(created.is_error, created.text)
        pivot = json.loads(created.text)["pivot"]
        self.assertEqual(pivot, "BC_Pivo_Door")

        moved = call_tool(
            "joint_set_angle",
            {"pivot": pivot, "degrees": 30, "description": "open"},
        )
        self.assertFalse(moved.is_error, moved.text)
        self.assertEqual(json.loads(moved.text)["angle_deg"], 30.0)

        _box("Wall", (2, 2, 2), (0, 6, 0))
        tested = call_tool("joint_test", {"pivot": pivot})
        self.assertFalse(tested.is_error, tested.text)
        data = json.loads(tested.text)
        self.assertEqual(data["colliding_at_rest"], False)
        self.assertTrue(60 <= data["free_max"] <= 70)
        # joint_test restored the 30 degree pose set before.
        self.assertAlmostEqual(bpy.data.objects[pivot]["bc_angle"], 30.0)

    def test_error_result_for_unknown_pivot(self):
        result = call_tool("joint_set_angle", {"pivot": "Nope", "degrees": 5})
        self.assertTrue(result.is_error)
