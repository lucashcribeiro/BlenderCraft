import json
import math
import unittest

import bmesh
import bpy
from mathutils import Matrix, Vector

from blendercraft_assistant import tools, units
from blendercraft_assistant.tools import topology
from blendercraft_assistant.tools.dispatch import call_tool

tools.load_all()


def _clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def _make_object(name, build, location=(0.0, 0.0, 0.0)):
    """Link a new mesh object whose geometry is created by build(bm)."""
    bm = bmesh.new()
    build(bm)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.update()
    return obj


def _cube(bm, size=20.0, center=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0)):
    matrix = Matrix.Translation(center) @ Matrix.Diagonal((*scale, 1.0))
    bmesh.ops.create_cube(bm, size=size, matrix=matrix)


def _sphere(bm, diameter, center):
    bmesh.ops.create_uvsphere(
        bm,
        u_segments=16,
        v_segments=8,
        radius=diameter / 2,
        matrix=Matrix.Translation(center),
    )


class TopologyTestBase(unittest.TestCase):
    def setUp(self):
        _clear_scene()
        units.apply_internal_mm(bpy.context.scene)

    def tearDown(self):
        _clear_scene()
        units.apply_internal_mm(bpy.context.scene)


class MeshTopologyTest(TopologyTestBase):
    def test_closed_cube(self):
        _make_object("Cube", lambda bm: _cube(bm, 20))
        info = topology.mesh_topology("Cube")
        self.assertEqual(info["components"], 1)
        self.assertTrue(info["manifold"])
        self.assertEqual(info["boundary_edges"], 0)
        self.assertEqual(info["non_manifold_edges"], 0)
        self.assertFalse(info["inverted_normals"])
        self.assertEqual(info["vertices"], 8)
        self.assertEqual(info["faces"], 6)
        self.assertAlmostEqual(info["volume_mm3"], 8000.0, places=2)

    def test_two_components(self):
        def build(bm):
            _cube(bm, 20)
            _sphere(bm, 6, (40, 0, 0))

        _make_object("Pair", build)
        self.assertEqual(topology.mesh_topology("Pair")["components"], 2)

    def test_missing_face_is_open(self):
        def build(bm):
            _cube(bm, 20)
            bm.faces.ensure_lookup_table()
            bmesh.ops.delete(bm, geom=[bm.faces[0]], context="FACES_ONLY")

        _make_object("Open", build)
        info = topology.mesh_topology("Open")
        self.assertFalse(info["manifold"])
        self.assertEqual(info["boundary_edges"], 4)
        self.assertEqual(info["non_manifold_edges"], 0)

    def test_edge_shared_by_three_faces(self):
        def build(bm):
            _cube(bm, 20)
            # A flap sharing one cube edge makes that edge 3-faced.
            bm.edges.ensure_lookup_table()
            a, b = bm.edges[0].verts
            c = bm.verts.new(a.co + Vector((5, 5, 5)))
            d = bm.verts.new(b.co + Vector((5, 5, 5)))
            bm.faces.new((a, b, d, c))

        _make_object("Flap", build)
        info = topology.mesh_topology("Flap")
        self.assertFalse(info["manifold"])
        self.assertEqual(info["non_manifold_edges"], 1)

    def test_inverted_normals(self):
        def build(bm):
            _cube(bm, 20)
            bmesh.ops.reverse_faces(bm, faces=bm.faces[:])

        _make_object("Inside", build)
        info = topology.mesh_topology("Inside")
        self.assertTrue(info["inverted_normals"])
        self.assertAlmostEqual(info["volume_mm3"], 8000.0, places=2)

    def test_mirrored_object_is_not_inverted(self):
        obj = _make_object("Mirror", lambda bm: _cube(bm, 20))
        obj.scale = (-1.0, 1.0, 1.0)
        bpy.context.view_layer.update()
        self.assertFalse(topology.mesh_topology("Mirror")["inverted_normals"])

    def test_world_transform_and_unit_scale(self):
        # 1 BU = 10 mm: a 2 BU cube is 20 mm wide.
        bpy.context.scene.unit_settings.scale_length = 0.01
        obj = _make_object("Big", lambda bm: _cube(bm, 2))
        obj.scale = (2.0, 2.0, 2.0)
        bpy.context.view_layer.update()
        info = topology.mesh_topology("Big")
        self.assertAlmostEqual(info["volume_mm3"], 40.0**3, places=1)

    def test_modifiers_are_evaluated(self):
        obj = _make_object("Arr", lambda bm: _cube(bm, 20))
        mod = obj.modifiers.new("Array", "ARRAY")
        mod.count = 2
        mod.relative_offset_displace = (3.0, 0.0, 0.0)
        bpy.context.view_layer.update()
        info = topology.mesh_topology("Arr")
        self.assertEqual(info["components"], 2)
        self.assertEqual(info["faces"], 12)

    def test_tool_result(self):
        _make_object("Cube", lambda bm: _cube(bm, 20))
        result = call_tool("mesh_topology", {"name": "Cube"})
        self.assertFalse(result.is_error)
        self.assertEqual(json.loads(result.text)["components"], 1)

    def test_unknown_and_non_mesh(self):
        result = call_tool("mesh_topology", {"name": "Nope"})
        self.assertTrue(result.is_error)
        self.assertIn("Object not found: Nope", result.text)
        empty = bpy.data.objects.new("Empty", None)
        bpy.context.scene.collection.objects.link(empty)
        result = call_tool("mesh_topology", {"name": "Empty"})
        self.assertTrue(result.is_error)
        self.assertIn("Object is not a mesh: Empty", result.text)


class PrintCheckTest(TopologyTestBase):
    def test_thin_plate(self):
        _make_object("Plate", lambda bm: _cube(bm, 20, scale=(1, 1, 0.02)))
        info = topology.print_check("Plate")
        self.assertGreater(info["thin_faces"], 0)
        self.assertLess(abs(info["min_wall_mm"] - 0.4), 0.01)

    def test_thick_cube_has_no_thin_faces(self):
        _make_object("Cube", lambda bm: _cube(bm, 20), location=(0, 0, 10))
        info = topology.print_check("Cube")
        self.assertEqual(info["thin_faces"], 0)
        self.assertLess(abs(info["min_wall_mm"] - 20.0), 0.01)

    def test_twisted_quad_is_not_a_thin_wall(self):
        # A raised corner makes the top quad non-planar: its center is off the
        # surface, but the solid is still 20 mm thick.
        for corner in range(4):
            with self.subTest(corner=corner):
                _clear_scene()

                def build(bm):
                    _cube(bm, 20, center=(0, 0, 10))
                    top = max(bm.faces, key=lambda f: f.calc_center_median().z)
                    top.verts[corner].co.z += 0.05

                _make_object("Twist", build)
                info = topology.print_check("Twist")
                self.assertEqual(info["thin_faces"], 0)
                self.assertLess(abs(info["min_wall_mm"] - 20.0), 0.1)

    def test_min_wall_threshold_argument(self):
        _make_object("Cube", lambda bm: _cube(bm, 20), location=(0, 0, 10))
        info = topology.print_check("Cube", min_wall_mm=25.0)
        self.assertEqual(info["thin_faces"], 6)

    def test_cube_on_bed(self):
        _make_object("Cube", lambda bm: _cube(bm, 20), location=(0, 0, 10))
        info = topology.print_check("Cube")
        self.assertEqual(info["overhang_faces"], 0)
        self.assertEqual(info["overhang_area_mm2"], 0)
        self.assertEqual(info["below_bed_mm"], 0)

    def test_cube_below_bed(self):
        _make_object("Cube", lambda bm: _cube(bm, 20))
        self.assertEqual(topology.print_check("Cube")["below_bed_mm"], 10)

    def test_overhanging_slab(self):
        def build(bm):
            _cube(bm, 20, center=(0, 0, 10))
            # 20 x 20 x 2 slab sticking out of the +X side at z = 10.
            _cube(bm, 20, center=(20, 0, 10), scale=(1, 1, 0.1))

        _make_object("Shelf", build)
        info = topology.print_check("Shelf")
        self.assertGreater(info["overhang_faces"], 0)
        self.assertGreater(info["overhang_area_mm2"], 0)

    def test_overhang_angle_threshold(self):
        # overhang_deg is measured from vertical (slicer convention). A plate tilted
        # 30 degrees from horizontal leans 60 degrees from vertical: it is an
        # overhang at 45 but not at 70.
        def build(bm):
            _cube(bm, 20, center=(0, 0, 10))
            tilt = Matrix.Translation((20, 0, 10)) @ Matrix.Rotation(
                math.radians(30), 4, "Y"
            )
            bmesh.ops.create_cube(bm, size=10, matrix=tilt @ Matrix.Diagonal((1, 1, 0.1, 1)))

        _make_object("Tilt", build)
        self.assertGreater(topology.print_check("Tilt", overhang_deg=45)["overhang_faces"], 0)
        self.assertEqual(topology.print_check("Tilt", overhang_deg=70)["overhang_faces"], 0)

    def test_inverted_mesh_is_checked_as_solid(self):
        def build(bm):
            _cube(bm, 20, scale=(1, 1, 0.02), center=(0, 0, 0.2))
            bmesh.ops.reverse_faces(bm, faces=bm.faces[:])

        _make_object("InvPlate", build)
        info = topology.print_check("InvPlate")
        self.assertLess(abs(info["min_wall_mm"] - 0.4), 0.01)
        self.assertEqual(info["overhang_faces"], 0)

    def test_tool_result_and_defaults(self):
        _make_object("Plate", lambda bm: _cube(bm, 20, scale=(1, 1, 0.02)))
        result = call_tool("print_check", {"name": "Plate"})
        self.assertFalse(result.is_error)
        data = json.loads(result.text)
        self.assertGreater(data["thin_faces"], 0)
        result = call_tool(
            "print_check", {"name": "Plate", "min_wall_mm": 0.1, "overhang_deg": 30}
        )
        self.assertEqual(json.loads(result.text)["thin_faces"], 0)

    def test_unknown_and_non_mesh(self):
        result = call_tool("print_check", {"name": "Nope"})
        self.assertTrue(result.is_error)
        self.assertIn("Object not found: Nope", result.text)
        empty = bpy.data.objects.new("Empty", None)
        bpy.context.scene.collection.objects.link(empty)
        result = call_tool("print_check", {"name": "Empty"})
        self.assertTrue(result.is_error)
        self.assertIn("Object is not a mesh: Empty", result.text)

    def test_invalid_overhang_angle(self):
        _make_object("Cube", lambda bm: _cube(bm, 20))
        result = call_tool("print_check", {"name": "Cube", "overhang_deg": 120})
        self.assertTrue(result.is_error)
        self.assertIn("overhang_deg", result.text)
