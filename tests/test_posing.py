"""set_pose and measure_standing on a procedural figure saved in print pose."""

import json
import unittest

import bpy
from mathutils import Vector

from blendercraft_assistant import units
from blendercraft_assistant.tools import articulate, joints
from blendercraft_assistant.tools.dispatch import call_tool
from test_articulate import _clear_scene, _link


def _call(name, args):
    result = call_tool(name, args)
    return result, (json.loads(result.text) if result.text.lstrip().startswith("{") else result.text)


def _print_pose_figure():
    """Torso lying on the bed; legs flat, pointing outward from pins along Y
    at (+-5, y, 3); 10 mm from the hinge to the sole."""
    box, sphere, cyl = articulate._box_bm, articulate._sphere_bm, articulate._cylinder_bm
    _link("01-cabeca-tronco-dianteiro", box(Vector((0, -8, 6)), (14, 24, 12)))
    _link("02-tronco-traseiro", box(Vector((0, 12, 5)), (14, 14, 10)))
    for end, y in (("frente", -10.0), ("tras", 12.0)):
        for sign, s in (("+", 1.0), ("-", -1.0)):
            key = f"{end}-x{sign}"
            _link(f"04-perna-{key}", box(Vector((10 * s, y, 3)), (10, 4, 4)))
            _link(f"06-bola-{key}", sphere(Vector((5 * s, y, 3)), 2.0))
            _link(f"08-pino-perna-{key}", cyl(Vector((5 * s, y - 3, 3)), Vector((5 * s, y + 3, 3)), 0.8))
            _link(f"11-almofada-grande-{key}", box(Vector((14.8 * s, y, 3)), (0.4, 3, 3)))


def _matrices():
    return {o.name: [list(r) for r in o.matrix_world] for o in bpy.data.objects}


def _z_range(name):
    obj = bpy.data.objects[name]
    zs = [(obj.matrix_world @ v.co).z for v in obj.data.vertices]
    return min(zs), max(zs)


class PoseTest(unittest.TestCase):
    def setUp(self):
        _clear_scene()
        units.apply_internal_mm(bpy.context.scene)
        _print_pose_figure()

    def tearDown(self):
        _clear_scene()

    def assertMatricesEqual(self, a, b):
        self.assertEqual(a.keys(), b.keys())
        for name in a:
            for ra, rb in zip(a[name], b[name]):
                for x, y in zip(ra, rb):
                    self.assertAlmostEqual(x, y, places=4, msg=name)

    def test_standing_print_and_rest(self):
        before = _matrices()
        result, data = _call("set_pose", {"target": "standing"})
        self.assertFalse(result.is_error, result.text)
        self.assertEqual(len(data["legs"]), 4)
        self.assertTrue(all(l["was"] == "print" for l in data["legs"]))
        self.assertTrue(all(abs(abs(l["turned_deg"]) - 90) < 0.01 for l in data["legs"]))
        self.assertAlmostEqual(data["raised_mm"], 7.0, places=3)
        lo, hi = _z_range("04-perna-frente-x+")
        self.assertAlmostEqual(lo, 0.0, places=3)
        self.assertAlmostEqual(hi, 10.0, places=3)  # the hinge end is now on top
        self.assertAlmostEqual(_z_range("01-cabeca-tronco-dianteiro")[0], 7.0, places=3)
        # The pad travels with its leg, down to the sole.
        self.assertLess(_z_range("11-almofada-grande-tras-x-")[0], 0.5)
        # Idempotent.
        _, again = _call("set_pose", {"target": "standing"})
        self.assertTrue(all(l["was"] == "standing" and abs(l["turned_deg"]) < 0.01 for l in again["legs"]))
        _, flat = _call("set_pose", {"target": "print"})
        self.assertTrue(all(l["was"] == "standing" for l in flat["legs"]))
        self.assertAlmostEqual(_z_range("01-cabeca-tronco-dianteiro")[0], 0.0, places=3)
        self.assertAlmostEqual(_z_range("04-perna-frente-x-")[1] - _z_range("04-perna-frente-x-")[0], 4.0, places=3)
        _call("set_pose", {"target": "standing"})
        result, _ = _call("set_pose", {"target": "rest"})
        self.assertFalse(result.is_error, result.text)
        self.assertMatricesEqual(before, _matrices())

    def test_measure_standing_restores_the_print_pose(self):
        before = _matrices()
        result, data = _call("measure_standing", {})
        self.assertFalse(result.is_error, result.text)
        rows = {r["item"]: r for r in data["table"]}
        self.assertAlmostEqual(rows["height"]["current_mm"], 19.0, places=2)
        self.assertAlmostEqual(rows["pivot_to_sole"]["current_mm"], 10.0, places=2)
        self.assertAlmostEqual(rows["pivot_height"]["current_mm"], 10.0, places=2)
        self.assertAlmostEqual(rows["belly_height"]["current_mm"], 7.0, places=2)
        self.assertAlmostEqual(rows["left_right_gap"]["current_mm"], 6.0, places=2)
        self.assertAlmostEqual(rows["height"]["target_mm"], 19.0, places=2)
        self.assertAlmostEqual(rows["pivot_to_sole"]["target_mm"], 13.4 * 19 / 35, places=2)
        self.assertMatricesEqual(before, _matrices())

    def test_pivots_are_used_when_present(self):
        for end, y in (("frente", -10.0), ("tras", 12.0)):
            for sign, s in (("+", 1.0), ("-", -1.0)):
                joints.create_pivot(f"04-perna-{end}-x{sign}", (5 * s, y, 3), "Y")
        _, data = _call("set_pose", {"target": "standing"})
        self.assertTrue(all(l["hinge"] == "pivot" for l in data["legs"]))
        angles = sorted(round(bpy.data.objects[f"BC_Pivo_04-perna-{k}"]["bc_angle"]) for k in
                        ("frente-x+", "frente-x-", "tras-x+", "tras-x-"))
        self.assertEqual(angles, [-90, -90, 90, 90])
        self.assertAlmostEqual(_z_range("04-perna-tras-x+")[0], 0.0, places=3)

    def test_no_legs(self):
        _clear_scene()
        _link("Cube", articulate._box_bm(Vector((0, 0, 0)), (2, 2, 2)))
        result, _ = _call("set_pose", {"target": "standing"})
        self.assertTrue(result.is_error)


if __name__ == "__main__":
    unittest.main()
