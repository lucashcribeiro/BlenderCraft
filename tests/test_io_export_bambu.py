"""Exported 3MF files carry what slicers like Bambu Studio expect."""

import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import bpy

from blendercraft_assistant import io_3mf, units

CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"


def _clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)


class BambuExportTest(unittest.TestCase):
    def test_export_declares_application_and_printable_items(self):
        _clear_scene()
        units.apply_internal_mm(bpy.context.scene)
        mesh = bpy.data.meshes.new("Tri")
        mesh.from_pydata([(0, 0, 0), (10, 0, 0), (0, 10, 0)], [], [(0, 1, 2)])
        obj = bpy.data.objects.new("Tri", mesh)
        bpy.context.scene.collection.objects.link(obj)
        bpy.context.view_layer.update()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.3mf"
            io_3mf.export_3mf(str(path), [obj], 1.0)
            with zipfile.ZipFile(path) as zf:
                root = ET.fromstring(zf.read("3D/3dmodel.model"))
        _clear_scene()
        core = f"{{{CORE_NS}}}"
        meta = {m.get("name"): m.text for m in root.findall(f"{core}metadata")}
        self.assertEqual(meta.get("Application"), "BlenderCraft")
        # Metadata must precede resources for strict readers.
        self.assertEqual(list(root)[0].tag, f"{core}metadata")
        (item,) = root.findall(f"{core}build/{core}item")
        self.assertEqual(item.get("printable"), "1")
        self.assertEqual(
            root.get("{http://www.w3.org/XML/1998/namespace}lang"), "en-US"
        )
