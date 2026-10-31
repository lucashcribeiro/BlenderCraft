"""Articulation tools on a small procedural puppy (one capsule-ish shell)."""

import json
import unittest

import bmesh
import bpy
from mathutils import Matrix, Vector

from blendercraft_assistant import tools, units
from blendercraft_assistant.tools import articulate
from blendercraft_assistant.tools.dispatch import call_tool

tools.load_all()

CLEARANCE = 0.30
# Right rear leg (x = 3.5, y = 8, radius 2.8) below the belly (z = 8 at x = 0).
LEG_BOX = {"type": "box", "center": [3.5, 8, 3.25], "size": [7, 7, 8.5]}
LEG_TOP = 7.5


def _clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def _link(name, bm):
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.update()
    return obj


def _puppy(name="Puppy", s=1.0):
    """Body capsule along Y, head at -Y, four legs standing on Z = 0, tail.
    s scales the whole figure."""

    def v(x, y, z):
        return Vector((x * s, y * s, z * s))

    # The cylinder stops short of the end spheres' centres: no coincident
    # rings, which EXACT booleans handle badly.
    body = articulate._cylinder_bm(v(0, -10.5, 18), v(0, 10.5, 18), 10.0 * s)
    parts = [
        articulate._sphere_bm(v(0, -11, 18), 10.0 * s),
        articulate._sphere_bm(v(0, 11, 18), 10.0 * s),
        articulate._sphere_bm(v(0, -19, 26), 7.5 * s),
        articulate._cylinder_bm(v(0, 17, 20), v(0, 23, 24), 1.6 * s),
    ]
    for x in (-3.5, 3.5):
        for y in (-8.0, 8.0):
            parts.append(articulate._cylinder_bm(v(x, y, 0), v(x, y, 14), 2.8 * s))
    obj = _link(name, body)
    articulate._boolean(obj, "UNION", parts)
    for b in parts:
        b.free()
    return obj


def _call(name, args):
    result = call_tool(name, args)
    data = json.loads(result.text) if result.text.lstrip().startswith("{") else result.text
    return result, data


def _shells(name):
    from blendercraft_assistant.tools.topology import mesh_topology

    return mesh_topology(name)


class ArticulateBase(unittest.TestCase):
    def setUp(self):
        _clear_scene()
        units.apply_internal_mm(bpy.context.scene)

    def tearDown(self):
        _clear_scene()


class PrepareMeshTest(ArticulateBase):
    def test_fixes_open_mesh_with_duplicate_vertices(self):
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=10.0)
        # Split every face apart (duplicate vertices) and open one face.
        bmesh.ops.split_edges(bm, edges=bm.edges[:])
        bm.faces.ensure_lookup_table()
        bm.faces.remove(bm.faces[0])
        _link("Broken", bm)
        self.assertFalse(_shells("Broken")["manifold"])
        result, data = _call("prepare_mesh", {"name": "Broken"})
        self.assertFalse(result.is_error, result.text)
        self.assertFalse(data["before"]["manifold"])
        self.assertTrue(data["after"]["manifold"])
        self.assertEqual(data["after"]["components"], 1)
        self.assertEqual(data["after"]["dimensions_mm"], [10.0, 10.0, 10.0])

    def test_overlapping_shells_are_fused_by_remesh(self):
        bm = articulate._sphere_bm(Vector((0, 0, 0)), 5.0)
        other = articulate._sphere_bm(Vector((4, 0, 0)), 5.0)
        mesh = bpy.data.meshes.new("tmp")
        other.to_mesh(mesh)
        other.free()
        bm.from_mesh(mesh)
        bpy.data.meshes.remove(mesh)
        _link("Blobs", bm)
        result, data = _call("prepare_mesh", {"name": "Blobs", "voxel_mm": 0.4})
        self.assertFalse(result.is_error, result.text)
        self.assertEqual(data["before"]["components"], 2)
        self.assertTrue(data["after"]["manifold"])
        self.assertEqual(data["after"]["components"], 1)

    def test_split_shells_and_near_shells(self):
        bm = articulate._box_bm(Vector((0, 0, 0)), (10, 10, 10))
        far = articulate._box_bm(Vector((10.2, 0, 0)), (10, 10, 10))  # 0.2 mm apart
        mesh = bpy.data.meshes.new("tmp")
        far.to_mesh(mesh)
        far.free()
        bm.from_mesh(mesh)
        bpy.data.meshes.remove(mesh)
        _link("Two", bm)
        _, data = _call("prepare_mesh", {"name": "Two", "split_shells": True})
        self.assertEqual(len(data["near_shells"]), 1)
        self.assertAlmostEqual(data["near_shells"][0]["gap_mm"], 0.2, places=3)
        self.assertEqual(len(data["shells"]), 2)
        self.assertIn("Two.shell_01", bpy.data.objects)
        self.assertEqual(_shells("Two")["components"], 1)


class CutPartTest(ArticulateBase):
    def test_box_cut_gives_two_watertight_shells_with_gap(self):
        _puppy()
        cutter = LEG_BOX
        result, data = _call(
            "cut_part", {"name": "Puppy", "cutter": cutter, "new_part_name": "Leg", "gap_mm": CLEARANCE}
        )
        self.assertFalse(result.is_error, result.text)
        for part in ("Puppy", "Leg"):
            self.assertEqual(data["parts"][part]["components"], 1)
            self.assertTrue(data["parts"][part]["manifold"])
        self.assertAlmostEqual(bpy.data.objects["Leg"].dimensions.z, 7.5, places=3)
        a = articulate._world_bm(bpy.data.objects["Puppy"])
        b = articulate._world_bm(bpy.data.objects["Leg"])
        gap, _ = articulate._min_gap(a, b)
        a.free()
        b.free()
        self.assertGreaterEqual(gap, CLEARANCE - 0.02)
        self.assertIn("bc_cutter", bpy.data.objects["Leg"])

    def test_plane_through_all_legs_is_rejected_and_rolled_back(self):
        puppy = _puppy()
        faces = len(puppy.data.polygons)
        cutter = {"type": "plane", "point": [0, 0, 5], "normal": [0, 0, -1]}
        result, _ = _call("cut_part", {"name": "Puppy", "cutter": cutter, "new_part_name": "Legs"})
        self.assertTrue(result.is_error)
        self.assertIn("4 shells", result.text)
        self.assertNotIn("Legs", bpy.data.objects)
        self.assertEqual(len(puppy.data.polygons), faces)

    def test_validation(self):
        _puppy()
        for cutter in ({"type": "cone"}, {"type": "box", "center": [0, 0, 0], "size": [1, 0, 1]}):
            result, _ = _call("cut_part", {"name": "Puppy", "cutter": cutter, "new_part_name": "X"})
            self.assertTrue(result.is_error)
        result, _ = _call(
            "cut_part",
            {"name": "Puppy", "cutter": {"type": "sphere", "center": [0, 0, 100], "radius": 2}, "new_part_name": "X"},
        )
        self.assertTrue(result.is_error)
        self.assertIn("does not touch", result.text)


class AddJointTest(ArticulateBase):
    def _cut_leg(self, cutter):
        _puppy()
        result, _ = _call(
            "cut_part", {"name": "Puppy", "cutter": cutter, "new_part_name": "Leg", "gap_mm": CLEARANCE}
        )
        self.assertFalse(result.is_error, result.text)

    def _assert_joint(self, data):
        measured = data["measured"]
        self.assertTrue(measured["ok"])
        self.assertGreater(measured["min_gap_mm"], 0.0)
        self.assertGreaterEqual(measured["min_gap_mm"], CLEARANCE - 0.02)
        self.assertGreaterEqual(measured["local_min_gap_mm"], CLEARANCE - 0.02)
        for report in data["parts"].values():
            self.assertEqual(report["components"], 1)
            self.assertTrue(report["manifold"])
        sweep = data["sweep"]
        self.assertFalse(sweep["colliding_at_rest"])
        self.assertGreaterEqual(sweep["free_max"], 20.0)
        self.assertLessEqual(sweep["free_min"], -20.0)
        pivot = bpy.data.objects[data["pivot"]]
        self.assertIs(bpy.data.objects["Leg"].parent, pivot)
        self.assertEqual(pivot.get("bc_angle"), 0.0)

    def test_ball_joint(self):
        self._cut_leg(LEG_BOX)
        # Leg top at z = 7.5; ball 3 mm needs 1.5 + 3 x 0.3 + 1.2 = 3.6 mm.
        result, data = _call(
            "add_joint",
            {"parent": "Puppy", "child": "Leg", "type": "ball", "pivot": [3.5, 8, 11.5],
             "axis": [1, 0, 0], "size_mm": 3.0, "range_deg": 30},
        )
        self.assertFalse(result.is_error, result.text)
        self._assert_joint(data)
        self.assertEqual(data["built"]["ball_diameter_mm"], 3.0)
        self.assertGreaterEqual(data["built"]["lip_mm"], 1.2)
        self.assertTrue(data["retention"]["retained"], data["retention"])
        # The ball is fused to the leg: it reaches above the leg top.
        self.assertGreater(bpy.data.objects["Leg"].dimensions.z, LEG_TOP + 4.5)

    def test_pin_joint_with_tab(self):
        cutter = {
            "type": "union",
            "parts": [
                LEG_BOX,
                # Tab x 2.0..4.3 inside the body (half-width 7.6 at z 11.5).
                {"type": "box", "center": [3.15, 8, 10.5], "size": [2.3, 4.6, 7]},
            ],
        }
        self._cut_leg(cutter)
        result, data = _call(
            "add_joint",
            {"parent": "Puppy", "child": "Leg", "type": "pin", "pivot": [3.15, 8, 11.5],
             "axis": [1, 0, 0], "size_mm": 2.0, "range_deg": 30},
        )
        self.assertFalse(result.is_error, result.text)
        self._assert_joint(data)
        self.assertEqual(data["built"]["hole_diameter_mm"], 2.6)
        self.assertTrue(all(s["anchored"] for s in data["built"]["sides"].values()))
        self.assertTrue(data["retention"]["retained"], data["retention"])

    def test_pin_with_pivot_outside_child_is_refused(self):
        self._cut_leg(LEG_BOX)
        faces = len(bpy.data.objects["Puppy"].data.polygons)
        result, _ = _call(
            "add_joint",
            {"parent": "Puppy", "child": "Leg", "type": "pin", "pivot": [3.5, 8, 12], "axis": [1, 0, 0]},
        )
        self.assertTrue(result.is_error)
        self.assertIn("inside the child", result.text)
        self.assertEqual(len(bpy.data.objects["Puppy"].data.polygons), faces)
        self.assertIsNone(bpy.data.objects["Leg"].parent)

    def test_too_small_clearance_measure_rolls_back(self):
        self._cut_leg(LEG_BOX)
        # The cut left 0.30 mm; asking for 0.5 mm fails the measurement.
        faces = len(bpy.data.objects["Puppy"].data.polygons)
        result, _ = _call(
            "add_joint",
            {"parent": "Puppy", "child": "Leg", "type": "ball", "pivot": [3.5, 8, 12.5],
             "axis": [1, 0, 0], "size_mm": 3.0, "clearance_mm": 0.5, "relief": False},
        )
        self.assertTrue(result.is_error, result.text)
        self.assertIn("rolled back", result.text)
        self.assertEqual(len(bpy.data.objects["Puppy"].data.polygons), faces)
        self.assertNotIn("BC_Pivo_Leg", bpy.data.objects)

    def test_cap_joint_on_mid_body(self):
        body = _link("Body", articulate._cylinder_bm(Vector((0, -12, 17)), Vector((0, 12, 17)), 8.0))
        self.assertTrue(body)
        cutter = {"type": "plane", "point": [0, 0, 17], "normal": [0, 1, 0]}
        result, _ = _call(
            "cut_part", {"name": "Body", "cutter": cutter, "new_part_name": "Rear", "gap_mm": 1.0}
        )
        self.assertFalse(result.is_error, result.text)
        result, data = _call(
            "add_joint",
            {"parent": "Body", "child": "Rear", "type": "cap", "pivot": [0, -1.0, 17],
             "axis": [1, 0, 0], "size_mm": 9.0, "range_deg": 5},
        )
        self.assertFalse(result.is_error, result.text)
        self.assertTrue(data["measured"]["ok"])
        self.assertGreaterEqual(data["measured"]["local_min_gap_mm"], CLEARANCE - 0.02)
        self.assertAlmostEqual(data["built"]["cap_height_mm"], 3.5, places=2)


class FitClearanceTest(ArticulateBase):
    def test_pin_inside_block_gets_a_hole(self):
        _link("Block", articulate._box_bm(Vector((0, 0, 0)), (10, 10, 10)))
        _link("Pin", articulate._cylinder_bm(Vector((-8, 0, 0)), Vector((8, 0, 0)), 1.5))
        result, data = _call("fit_clearance", {"female": "Block", "male": "Pin", "clearance_mm": 0.3})
        self.assertFalse(result.is_error, result.text)
        self.assertTrue(data["measured"]["ok"])
        self.assertGreaterEqual(data["measured"]["min_gap_mm"], 0.28)
        self.assertLess(data["measured"]["min_gap_mm"], 0.4)

    def test_boolean_parts_union(self):
        _link("Block", articulate._box_bm(Vector((0, 0, 0)), (10, 10, 10)))
        _link("Ball", articulate._sphere_bm(Vector((5, 0, 0)), 3))
        result, data = _call(
            "boolean_parts", {"target": "Block", "tools": ["Ball"], "operation": "union", "delete_tools": True}
        )
        self.assertFalse(result.is_error, result.text)
        self.assertEqual(data["part"]["components"], 1)
        self.assertNotIn("Ball", bpy.data.objects)


class AnalyzeAndArticulateTest(ArticulateBase):
    def test_analyze_finds_legs_head_and_tail(self):
        _puppy()
        result, data = _call("analyze_figure", {"name": "Puppy"})
        self.assertFalse(result.is_error, result.text)
        self.assertEqual(data["length_axis"], "Y")
        self.assertEqual(data["head_direction"], [0.0, -1.0, 0.0])
        names = sorted(leg["name"] for leg in data["legs"])
        self.assertEqual(
            names, ["leg_front_left", "leg_front_right", "leg_rear_left", "leg_rear_right"]
        )
        for leg in data["legs"]:
            # The legs join the body around z = 9-11.
            self.assertGreater(leg["leg_top_z_mm"], 7.0)
            self.assertLess(leg["leg_top_z_mm"], 13.0)
            self.assertTrue(leg["pin"]["fits"])
        self.assertTrue(all(m["ok"] for m in data["mirror_check"]))
        self.assertIsNotNone(data["tail"])

    def test_articulate_figure_end_to_end(self):
        _puppy()
        result, data = _call("articulate_figure", {"name": "Puppy", "plan": {"preset": "quadruped", "legs": "pin"}})
        self.assertFalse(result.is_error, result.text)
        self.assertNotIn("failed_step", data, json.dumps(data.get("failed_step"), indent=1))
        self.assertEqual(len(data["table"]), 4)
        for row in data["table"]:
            self.assertTrue(row["ok"], row)
            self.assertGreaterEqual(row["built_min_gap_mm"], CLEARANCE - 0.02)
            self.assertGreater(row["built_range_deg"][1], 0)
            self.assertLess(row["built_range_deg"][0], 0)
        pivots = [o for o in bpy.data.objects if o.name.startswith("BC_Pivo_")]
        self.assertEqual(len(pivots), 4)

    def test_dry_run_changes_nothing(self):
        puppy = _puppy()
        faces = len(puppy.data.polygons)
        _, data = _call(
            "articulate_figure", {"name": "Puppy", "plan": {"legs": "ball"}, "dry_run": True}
        )
        self.assertEqual([s["op"] for s in data["steps"]], ["cut", "joint"] * 4)
        self.assertEqual(len(puppy.data.polygons), faces)
        self.assertEqual(len(bpy.data.objects), 1)


if __name__ == "__main__":
    unittest.main()
