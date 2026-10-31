"""Articulation templates: extract from a split model, apply to a new one."""

import json
import os
import shutil
import tempfile
import unittest

import bpy
from mathutils import Vector

from blendercraft_assistant import units
from blendercraft_assistant.tools import articulate, articulation_templates as tpl
from blendercraft_assistant.tools.dispatch import call_tool
from test_articulate import _clear_scene, _link, _puppy

CLEARANCE = 0.30


def _call(name, args):
    result = call_tool(name, args)
    return result, (json.loads(result.text) if result.text.lstrip().startswith("{") else result.text)


def _source_figure():
    """A standing split figure named like the user's SolidCraft projects."""
    box, sphere, cyl = articulate._box_bm, articulate._sphere_bm, articulate._cylinder_bm
    _link("01-cabeca-tronco-dianteiro", box(Vector((0, -8, 18)), (16, 24, 20)))
    _link("02-tronco-traseiro", box(Vector((0, 13.65, 16)), (16, 16, 16)))
    _link("10-calota-meio", sphere(Vector((0, 3, 15)), 4.0))
    _link("09-pino-meio", cyl(Vector((-6, 4.8, 15)), Vector((6, 4.8, 15)), 1.0))
    for end, y in (("frente", -10.0), ("tras", 14.0)):
        for sign, x in (("+", 4.0), ("-", -4.0)):
            _link(f"04-perna-{end}-x{sign}", box(Vector((x, y, 5)), (5, 5, 10)))
            _link(f"06-bola-{end}-x{sign}", sphere(Vector((x, y, 9.5)), 2.5))
            _link(f"07-furo-{end}-x{sign}", cyl(Vector((x, y - 2.5, 9.5)), Vector((x, y + 2.5, 9.5)), 0.95))
            _link(f"08-pino-perna-{end}-x{sign}", cyl(Vector((x, y - 4, 9.5)), Vector((x, y + 4, 9.5)), 0.7))
            if not (end == "tras" and sign == "-"):
                _link(f"11-almofada-grande-{end}-x{sign}", box(Vector((x, y, 0.2)), (3, 3, 0.4)))


class TemplateTest(unittest.TestCase):
    def setUp(self):
        _clear_scene()
        units.apply_internal_mm(bpy.context.scene)
        self.folder = tempfile.mkdtemp(prefix="bc-templates-")
        os.environ["BLENDERCRAFT_TEMPLATES_DIR"] = self.folder

    def tearDown(self):
        _clear_scene()
        os.environ.pop("BLENDERCRAFT_TEMPLATES_DIR", None)
        shutil.rmtree(self.folder, ignore_errors=True)

    def _extract(self):
        _source_figure()
        result, data = _call("extract_articulation_template", {"template_name": "teste"})
        self.assertFalse(result.is_error, result.text)
        return data

    def test_extract_writes_relative_template(self):
        data = self._extract()
        self.assertTrue(any("pads differ" in w for w in data["warnings"]))
        self.assertTrue(any("source pin clearances [0.25" in w for w in data["warnings"]))
        saved = json.loads((tpl.templates_dir() / "teste" / "template.json").read_text())
        self.assertEqual(saved["format"], tpl.FORMAT)
        # Standing model: its own height is the reference.
        self.assertEqual(saved["reference_height_mm"], 28.0)
        self.assertAlmostEqual(saved["legs"]["pivot_height_rel"], 9.5 / 28.0, places=3)
        # 1.4 mm pin at 28 mm is 1.75 mm at 35: clamped to the spec minimum.
        self.assertEqual(saved["legs"]["pin_mm"]["ref"], 2.0)
        self.assertAlmostEqual(saved["waist"]["position_from_rear_rel"], 16.825 / 41.65, places=3)
        self.assertEqual(saved["measured"]["waist_gap_mm"], 1.65)
        self.assertEqual(saved["rules"]["clearance_mm"], 0.3)
        self.assertEqual(saved["waist"]["joint"], "ball")
        self.assertTrue((tpl.templates_dir() / "teste" / "joints.blend").is_file())
        listed = {t["name"]: t for t in tpl.list_templates()}
        self.assertEqual(listed["teste"]["origin"], "user")
        self.assertEqual(listed["cachorrinho-2.0"]["origin"], "built-in")
        self.assertTrue(listed["cachorrinho-2.0"]["default"])

    def test_apply_to_a_bigger_figure_and_build(self):
        self._extract()
        _clear_scene()
        _puppy("Dog", s=1.5)
        result, plan = _call(
            "apply_articulation_template", {"target": "Dog", "template_name": "teste"}
        )
        self.assertFalse(result.is_error, result.text)
        self.assertAlmostEqual(plan["scale"], 50.25 / 28.0, places=2)
        ids = [(s["op"], s["id"]) for s in plan["steps"]]
        self.assertEqual(ids[:2], [("cut", "waist"), ("joint", "waist")])
        self.assertEqual(len(ids), 10, plan["notes"])
        self.assertTrue(plan["helpers"])
        self.assertIn("BC_Plano", bpy.data.collections)
        waist_ball = plan["steps"][1]
        self.assertEqual(waist_ball["size_mm"], 12.0)  # 7.0 x 1.79 clamped to 12
        self.assertEqual(plan["steps"][0]["gap_mm"], 2.0)  # 1.3 x 1.79 clamped to 2
        for step in plan["steps"]:
            if step["op"] == "cut" and step["id"].startswith("leg_"):
                self.assertEqual(step["part"], "Dog" if "front" in step["id"] else "Dog_traseira")
            if step["op"] == "joint" and step["id"].startswith("leg_"):
                x, y, z = step["pivot"]
                self.assertAlmostEqual(abs(x), 5.25, delta=0.6)
                self.assertAlmostEqual(abs(y), 12.0, delta=0.6)
                self.assertAlmostEqual(z, 9.5 / 28.0 * 50.25, delta=0.3)
                self.assertEqual(step["axis"], [0.0, 1.0, 0.0])
                # Outward is +90 about +Y for the left leg (x < 0).
                self.assertEqual(step["range_deg"], [0.0, 90.0] if x < 0 else [-90.0, 0.0])
        for first in range(0, 10, 2):
            result, built = _call(
                "articulate_figure", {"name": "Dog", "plan": {"steps": plan["steps"], "only": [first, first + 1]}}
            )
            self.assertFalse(result.is_error, result.text)
            self.assertNotIn("failed_step", built, json.dumps(built.get("failed_step"), indent=1)[:3000])
            row = built["table"][0]
            self.assertTrue(row["ok"], row)
            self.assertGreaterEqual(row["built_min_gap_mm"], CLEARANCE - 0.02)
        self.assertNotIn("BC_Plano", bpy.data.collections)
        parts = [o for o in bpy.data.objects if o.type == "MESH"]
        self.assertEqual(len(parts), 6)
        from blendercraft_assistant.tools.topology import mesh_topology

        for obj in parts:
            topo = mesh_topology(obj.name)
            self.assertEqual(topo["components"], 1, obj.name)
            self.assertTrue(topo["manifold"], obj.name)
        # Legs swing 0..90 deg outward without collision.
        for obj in bpy.data.objects:
            if obj.name.startswith("BC_Pivo_Dog_leg"):
                sweep = articulate.joints.joint_test(obj.name, start=-95, end=95, step=5)
                self.assertGreaterEqual(sweep["free_max"] - sweep["free_min"], 90.0, (obj.name, sweep))

    def test_builtin_default_template_loads(self):
        data = tpl.load_template("cachorrinho-2.0")
        self.assertEqual(data["rules"]["clearance_mm"], 0.3)
        self.assertEqual(data["legs"]["pivot_height_rel"], 0.38)
        self.assertEqual(data["waist"]["position_from_rear_rel"], 0.52)
        self.assertEqual(data["waist"]["ball_mm"]["ref"], 7.0)

    def test_bad_names(self):
        result, _ = _call("extract_articulation_template", {"template_name": "../x"})
        self.assertTrue(result.is_error)
        result, _ = _call("apply_articulation_template", {"target": "Nope", "template_name": "teste"})
        self.assertTrue(result.is_error)


if __name__ == "__main__":
    unittest.main()
