"""3MF Production extension (components across model parts) and Bambu extras."""

import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

import bpy
from mathutils import Vector

from blendercraft_assistant import io_3mf, units

CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
PROD_NS = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
MODEL_REL = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"

ROT_Z_90 = "0 1 0 -1 0 0 0 0 1"  # row vectors: +X goes to +Y

_CUBE_MESH = (
    "<mesh><vertices>"
    "<vertex x='0' y='0' z='0'/><vertex x='1' y='0' z='0'/>"
    "<vertex x='1' y='1' z='0'/><vertex x='0' y='1' z='0'/>"
    "<vertex x='0' y='0' z='1'/><vertex x='1' y='0' z='1'/>"
    "<vertex x='1' y='1' z='1'/><vertex x='0' y='1' z='1'/></vertices><triangles>"
    "<triangle v1='0' v2='2' v3='1'/><triangle v1='0' v2='3' v3='2'/>"
    "<triangle v1='4' v2='5' v3='6'/><triangle v1='4' v2='6' v3='7'/>"
    "<triangle v1='0' v2='1' v3='5'/><triangle v1='0' v2='5' v3='4'/>"
    "<triangle v1='1' v2='2' v3='6'/><triangle v1='1' v2='6' v3='5'/>"
    "<triangle v1='2' v2='3' v3='7'/><triangle v1='2' v2='7' v3='6'/>"
    "<triangle v1='3' v2='0' v3='4'/><triangle v1='3' v2='4' v3='7'/>"
    "</triangles></mesh>"
)


def _model(body, unit="millimeter", extra_ns=True):
    ns = f' xmlns:p="{PROD_NS}" requiredextensions="p"' if extra_ns else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<model unit="{unit}" xmlns="{CORE_NS}"{ns}>{body}</model>'
    )


def _write_package(path, parts, root="3D/3dmodel.model", rels_for_root=True):
    """``parts`` maps a zip name to text; the root model gets package rels."""
    with zipfile.ZipFile(path, "w") as zf:
        if rels_for_root:
            rels = (
                '<Relationships xmlns="http://schemas.openxmlformats.org/'
                'package/2006/relationships">'
                f'<Relationship Target="/{root}" Id="rel-1" Type="{MODEL_REL}"/>'
                "</Relationships>"
            )
            zf.writestr("_rels/.rels", rels)
        for name, text in parts.items():
            zf.writestr(name, text)


def _clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)
    for mat in list(bpy.data.materials):
        bpy.data.materials.remove(mat)


class ProductionBase(unittest.TestCase):
    def setUp(self):
        _clear_scene()
        units.apply_internal_mm(bpy.context.scene)
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()
        _clear_scene()
        units.apply_internal_mm(bpy.context.scene)

    def load(self, parts, **kwargs):
        path = self.dir / "p.3mf"
        _write_package(path, parts, **kwargs)
        return io_3mf.import_3mf(str(path), units.mm_per_bu(bpy.context.scene))


def _loc(obj):
    return [round(c, 3) for c in obj.matrix_world.translation]


class ProductionExtensionTest(ProductionBase):
    def _two_part_package(self):
        # a.model: object 1 is a cube, object 2 nests object 1 shifted +10 in X.
        sub = _model(
            f"<resources><object id='1' type='model' name='Cube'>{_CUBE_MESH}</object>"
            "<object id='2' type='model'><components>"
            "<component objectid='1' transform='1 0 0 0 1 0 0 0 1 10 0 0'/>"
            "</components></object></resources><build/>"
        )
        root = _model(
            "<resources><object id='5' type='model' name='Group'><components>"
            "<component p:path='/3D/Objects/a.model' objectid='1' "
            "transform='1 0 0 0 1 0 0 0 1 0 0 3'/>"
            "<component p:path='/3D/Objects/a.model' objectid='2' "
            "transform='1 0 0 0 1 0 0 0 1 0 20 0'/>"
            "</components></object></resources><build>"
            "<item objectid='5' transform='1 0 0 0 1 0 0 0 1 100 0 0'/>"
            "<item objectid='5' transform='1 0 0 0 1 0 0 0 1 0 0 200'/>"
            "</build>"
        )
        return {"3D/3dmodel.model": root, "3D/Objects/a.model": sub}

    def test_components_resolve_across_parts_with_nested_and_multiple_items(self):
        created = self.load(self._two_part_package())
        self.assertEqual(len(created), 4)
        self.assertEqual(
            sorted(_loc(o) for o in created),
            sorted(
                [
                    [100.0, 0.0, 3.0],
                    [110.0, 20.0, 0.0],
                    [0.0, 0.0, 203.0],
                    [10.0, 20.0, 200.0],
                ]
            ),
        )
        for obj in created:
            self.assertEqual(len(obj.data.polygons), 12)
            for dim in obj.dimensions:
                self.assertAlmostEqual(dim, 1.0, places=4)
            self.assertIn(obj.name, bpy.context.collection.objects)

    def test_leaf_name_wins_then_container_name(self):
        created = self.load(self._two_part_package())
        names = sorted(o.name.split(".")[0] for o in created)
        # Object 1 is named "Cube"; object 2 (nested) has none, so its
        # leaf keeps the name of the mesh object it reaches.
        self.assertEqual(names, ["Cube"] * 4)

    def test_container_name_used_when_mesh_object_is_unnamed(self):
        sub = _model(f"<resources><object id='1' type='model'>{_CUBE_MESH}</object></resources>")
        root = _model(
            "<resources><object id='2' type='model' name='Holder'><components>"
            "<component p:path='/3D/o.model' objectid='1'/></components></object>"
            "</resources><build><item objectid='2'/></build>"
        )
        (obj,) = self.load({"3D/3dmodel.model": root, "3D/o.model": sub})
        self.assertEqual(obj.name, "Holder")

    def test_transform_composition_order_is_component_then_item(self):
        # Component moves +5 X inside the object, the item then turns 90 deg
        # about Z: the cube ends up on +Y, not +X.
        sub = _model(f"<resources><object id='1' type='model'>{_CUBE_MESH}</object></resources>")
        root = _model(
            "<resources><object id='2' type='model'><components>"
            "<component p:path='/3D/o.model' objectid='1' "
            "transform='1 0 0 0 1 0 0 0 1 5 0 0'/></components></object></resources>"
            f"<build><item objectid='2' transform='{ROT_Z_90} 100 0 0'/></build>"
        )
        (obj,) = self.load({"3D/3dmodel.model": root, "3D/o.model": sub})
        self.assertEqual(_loc(obj), [100.0, 5.0, 0.0])
        # The cube's +X corner (1, 0, 0) maps to +Y after the item rotation.
        corner = obj.matrix_world @ Vector((1.0, 0.0, 0.0))
        self.assertEqual([round(c, 3) for c in corner], [100.0, 6.0, 0.0])

    def test_part_units_are_converted_independently(self):
        sub = _model(
            f"<resources><object id='1' type='model'>{_CUBE_MESH}</object></resources>",
            unit="centimeter",
        )
        root = _model(
            "<resources><object id='2' type='model'><components>"
            "<component p:path='/3D/o.model' objectid='1' "
            "transform='1 0 0 0 1 0 0 0 1 7 0 0'/></components></object></resources>"
            "<build><item objectid='2'/></build>"
        )
        (obj,) = self.load({"3D/3dmodel.model": root, "3D/o.model": sub})
        for dim in obj.dimensions:
            self.assertAlmostEqual(dim, 10.0, places=3)
        # The component offset is in the root part's unit (millimeters).
        self.assertEqual(_loc(obj), [7.0, 0.0, 0.0])

    def test_non_model_objects_are_skipped(self):
        root = _model(
            f"<resources><object id='1' type='support'>{_CUBE_MESH}</object>"
            f"<object id='2' type='model' name='Keep'>{_CUBE_MESH}</object>"
            "<object id='3' type='other'><components>"
            "<component objectid='2'/></components></object></resources>"
            "<build><item objectid='1'/><item objectid='3'/>"
            "<item objectid='2'/></build>"
        )
        created = self.load({"3D/3dmodel.model": root})
        self.assertEqual([o.name for o in created], ["Keep"])

    def test_component_without_path_finds_object_through_rels(self):
        sub = _model(f"<resources><object id='7' type='model' name='Far'>{_CUBE_MESH}</object></resources>")
        root = _model(
            "<resources><object id='2' type='model'><components>"
            "<component objectid='7'/></components></object></resources>"
            "<build><item objectid='2'/></build>"
        )
        rels = (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Target="/3D/Objects/far.model" Id="r1" Type="{MODEL_REL}"/>'
            "</Relationships>"
        )
        (obj,) = self.load(
            {
                "3D/3dmodel.model": root,
                "3D/Objects/far.model": sub,
                "3D/_rels/3dmodel.model.rels": rels,
            }
        )
        self.assertEqual(obj.name, "Far")

    def test_repeated_mesh_instances_have_independent_mesh_data(self):
        created = self.load(self._two_part_package())
        meshes = {o.data.name for o in created}
        self.assertEqual(len(meshes), 4)
        self.assertTrue(all(o.data.users == 1 for o in created))

    def test_reference_cycle_does_not_hang_and_valid_parts_still_import(self):
        root = _model(
            f"<resources><object id='1' type='model' name='Ok'>{_CUBE_MESH}</object>"
            "<object id='2' type='model'><components><component objectid='3'/>"
            "</components></object><object id='3' type='model'><components>"
            "<component objectid='2'/></components></object></resources>"
            "<build><item objectid='1'/><item objectid='2'/></build>"
        )
        problems = []
        path = self.dir / "cyc.3mf"
        _write_package(path, {"3D/3dmodel.model": root})
        created = io_3mf.import_3mf(
            str(path), units.mm_per_bu(bpy.context.scene), warnings=problems
        )
        self.assertEqual([o.name for o in created], ["Ok"])
        self.assertTrue(any("cycle" in p for p in problems), problems)

    def test_missing_model_part_with_nothing_else_is_an_error(self):
        root = _model(
            "<resources><object id='2' type='model'><components>"
            "<component p:path='/3D/gone.model' objectid='1'/></components></object>"
            "</resources><build><item objectid='2'/></build>"
        )
        with self.assertRaisesRegex(ValueError, "mesh"):
            self.load({"3D/3dmodel.model": root})

    def test_unreferenced_root_mesh_is_still_imported_at_the_origin(self):
        root = _model(
            f"<resources><object id='1' type='model' name='A'>{_CUBE_MESH}</object>"
            f"<object id='2' type='model' name='Loose'>{_CUBE_MESH}</object></resources>"
            "<build><item objectid='1' transform='1 0 0 0 1 0 0 0 1 9 0 0'/></build>"
        )
        created = self.load({"3D/3dmodel.model": root})
        self.assertEqual(sorted(o.name for o in created), ["A", "Loose"])
        self.assertEqual(_loc(bpy.data.objects["Loose"]), [0.0, 0.0, 0.0])

    def test_core_spec_file_without_production_still_works(self):
        root = _model(
            f"<resources><object id='1' type='model' name='Plain'>{_CUBE_MESH}</object>"
            "</resources><build><item objectid='1' transform='1 0 0 0 1 0 0 0 1 1 2 3'/></build>",
            extra_ns=False,
        )
        (obj,) = self.load({"3D/3dmodel.model": root})
        self.assertEqual(obj.name, "Plain")
        self.assertEqual(_loc(obj), [1.0, 2.0, 3.0])

    def test_out_of_range_triangle_index_is_an_error(self):
        bad = (
            "<mesh><vertices><vertex x='0' y='0' z='0'/><vertex x='1' y='0' z='0'/>"
            "<vertex x='0' y='1' z='0'/></vertices><triangles>"
            "<triangle v1='0' v2='1' v3='9'/></triangles></mesh>"
        )
        root = _model(
            f"<resources><object id='1' type='model'>{bad}</object></resources>"
            "<build><item objectid='1'/></build>"
        )
        with self.assertRaises(ValueError):
            self.load({"3D/3dmodel.model": root})

    def test_large_mesh_imports_with_exact_counts_in_reasonable_time(self):
        n = 250  # (n+1)^2 vertices, 2 n^2 triangles
        verts = "".join(
            f"<vertex x='{x}' y='{y}' z='{(x * y) % 7}'/>"
            for y in range(n + 1)
            for x in range(n + 1)
        )
        tris = []
        for y in range(n):
            for x in range(n):
                a = y * (n + 1) + x
                b, c, d = a + 1, a + n + 1, a + n + 2
                tris.append(f"<triangle v1='{a}' v2='{b}' v3='{d}'/>")
                tris.append(f"<triangle v1='{a}' v2='{d}' v3='{c}'/>")
        mesh = f"<mesh><vertices>{verts}</vertices><triangles>{''.join(tris)}</triangles></mesh>"
        root = _model(
            f"<resources><object id='1' type='model' name='Grid'>{mesh}</object></resources>"
            "<build><item objectid='1'/></build>"
        )
        started = time.perf_counter()
        (obj,) = self.load({"3D/3dmodel.model": root})
        elapsed = time.perf_counter() - started
        self.assertEqual(len(obj.data.vertices), (n + 1) ** 2)
        self.assertEqual(len(obj.data.polygons), 2 * n * n)
        self.assertLess(elapsed, 20.0)
        self.assertAlmostEqual(obj.dimensions.x, n, places=3)


_SETTINGS = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <object id="5">
    <metadata key="name" value="Cachorro"/>
    <metadata key="extruder" value="1"/>
    <part id="1" subtype="normal_part">
      <metadata key="name" value="Corpo"/>
      <metadata key="extruder" value="3"/>
    </part>
    <part id="2" subtype="normal_part">
      <metadata key="name" value="Orelhas"/>
    </part>
    <part id="3" subtype="negative_part">
      <metadata key="name" value="Furo"/>
    </part>
  </object>
</config>
"""

_PROJECT = json.dumps({"filament_colour": ["#000000", "#FFFFFF", "#FF0000", "#0000FF"]})


class BambuExtrasTest(ProductionBase):
    def _package(self, settings=_SETTINGS, project=_PROJECT):
        sub = _model(
            "<resources>"
            + "".join(
                f"<object id='{i}' type='model'>{_CUBE_MESH}</object>" for i in (1, 2, 3)
            )
            + "</resources><build/>"
        )
        root = _model(
            "<resources><object id='5' type='model'><components>"
            + "".join(
                f"<component p:path='/3D/Objects/object_1.model' objectid='{i}'/>"
                for i in (1, 2, 3)
            )
            + "</components></object></resources>"
            "<build><item objectid='5' transform='1 0 0 0 1 0 0 0 1 128 128 0'/></build>"
        )
        parts = {"3D/3dmodel.model": root, "3D/Objects/object_1.model": sub}
        if settings is not None:
            parts["Metadata/model_settings.config"] = settings
        if project is not None:
            parts["Metadata/project_settings.config"] = project
        return parts

    @staticmethod
    def _color(obj):
        return [round(c, 3) for c in obj.material_slots[0].material.diffuse_color]

    def test_part_names_and_extruder_colours(self):
        created = self.load(self._package())
        # The negative part is a Bambu helper volume, not model geometry.
        self.assertEqual([o.name for o in created], ["Corpo", "Orelhas"])
        body, ears = created
        # Part extruder 3 -> red; Orelhas inherits object extruder 1 -> black.
        self.assertEqual(self._color(body), [1.0, 0.0, 0.0, 1.0])
        self.assertEqual(self._color(ears), [0.0, 0.0, 0.0, 1.0])
        self.assertEqual(_loc(body), [128.0, 128.0, 0.0])

    def test_object_name_used_when_parts_have_no_names(self):
        settings = (
            '<config><object id="5"><metadata key="name" value="Cachorro"/>'
            '<metadata key="extruder" value="2"/></object></config>'
        )
        created = self.load(self._package(settings=settings))
        self.assertEqual({o.name.split(".")[0] for o in created}, {"Cachorro"})
        self.assertEqual(self._color(created[0]), [1.0, 1.0, 1.0, 1.0])

    def test_missing_or_broken_bambu_files_never_fail_the_import(self):
        for label, settings, project in (
            ("none", None, None),
            ("broken-xml", "<config><object", _PROJECT),
            ("broken-json", _SETTINGS, "{not json"),
            ("short-colours", _SETTINGS, json.dumps({"filament_colour": ["#112233"]})),
            ("bad-colour", _SETTINGS, json.dumps({"filament_colour": ["red"] * 4})),
        ):
            with self.subTest(label):
                _clear_scene()
                created = self.load(self._package(settings=settings, project=project))
                self.assertGreaterEqual(len(created), 2)
                for obj in created:
                    self.assertEqual(len(obj.data.polygons), 12)

    def test_declared_core_colour_beats_extruder_colour(self):
        sub = _model(
            "<resources><basematerials id='9'><base name='g' displaycolor='#00FF00'/>"
            f"</basematerials><object id='1' type='model' pid='9' pindex='0'>{_CUBE_MESH}"
            "</object></resources><build/>"
        )
        root = _model(
            "<resources><object id='5' type='model'><components>"
            "<component p:path='/3D/Objects/object_1.model' objectid='1'/>"
            "</components></object></resources><build><item objectid='5'/></build>"
        )
        parts = {
            "3D/3dmodel.model": root,
            "3D/Objects/object_1.model": sub,
            "Metadata/model_settings.config": _SETTINGS,
            "Metadata/project_settings.config": _PROJECT,
        }
        (obj,) = self.load(parts)
        self.assertEqual(self._color(obj), [0.0, 1.0, 0.0, 1.0])


if __name__ == "__main__":
    unittest.main()
