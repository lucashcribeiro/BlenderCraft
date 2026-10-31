import unittest

import bpy

from blendercraft_assistant import units


class UnitsTest(unittest.TestCase):
    def setUp(self):
        self.scene = bpy.context.scene
        us = self.scene.unit_settings
        us.system = "METRIC"
        us.scale_length = 1.0
        us.length_unit = "METERS"

    def test_internal_mm(self):
        units.apply_internal_mm(self.scene)
        self.assertEqual(units.mm_per_bu(self.scene), 1.0)
        self.assertEqual(units.get_display_unit(self.scene), "MM")

    def test_display_inches_keeps_scale(self):
        units.apply_internal_mm(self.scene)
        units.set_display_unit(self.scene, "IN")
        self.assertAlmostEqual(self.scene.unit_settings.scale_length, 0.001, places=9)
        self.assertEqual(units.get_display_unit(self.scene), "IN")
        self.assertIn("0.787", units.format_length(20, self.scene))

    def test_format_length_each_unit(self):
        units.apply_internal_mm(self.scene)
        expected = {"MM": "20.000 mm", "CM": "2.000 cm", "M": "0.020 m",
                    "IN": "0.787 in", "FT": "0.066 ft"}
        for key, text in expected.items():
            units.set_display_unit(self.scene, key)
            self.assertEqual(units.format_length(20, self.scene), text)

    def test_display_scale_and_suffix(self):
        units.apply_internal_mm(self.scene)
        expected = {"MM": (1.0, "mm"), "CM": (0.1, "cm"), "M": (0.001, "m"),
                    "IN": (1 / 25.4, "in"), "FT": (1 / 304.8, "ft")}
        for key, (factor, suffix) in expected.items():
            units.set_display_unit(self.scene, key)
            got_factor, got_suffix = units.display_scale_and_suffix(self.scene)
            self.assertAlmostEqual(got_factor, factor, places=9)
            self.assertEqual(got_suffix, suffix)

    def test_display_scale_none_system(self):
        self.scene.unit_settings.system = "NONE"
        self.assertEqual(units.display_scale_and_suffix(self.scene), (1.0, ""))

    def test_meters_scene(self):
        us = self.scene.unit_settings
        us.scale_length = 1.0
        us.system = "METRIC"
        self.assertEqual(units.mm_per_bu(self.scene), 1000.0)

    def test_none_system(self):
        self.scene.unit_settings.system = "NONE"
        self.assertEqual(units.mm_per_bu(self.scene), 1.0)

    def test_unknown_unit(self):
        with self.assertRaises(ValueError):
            units.set_display_unit(self.scene, "XX")

    def test_every_display_unit_roundtrips(self):
        units.apply_internal_mm(self.scene)
        for key in units.DISPLAY_UNITS:
            units.set_display_unit(self.scene, key)
            self.assertEqual(units.get_display_unit(self.scene), key)
            self.assertAlmostEqual(self.scene.unit_settings.scale_length, 0.001, places=9)


if __name__ == "__main__":
    unittest.main()
