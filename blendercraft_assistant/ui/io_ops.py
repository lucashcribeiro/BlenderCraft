"""File > Import/Export 3MF and drag-and-drop of .3mf files.

3MF goes through io_3mf (files in millimeters, converted with mm_per_bu).
STL needs nothing here: Blender's own STL importer reads 1 file unit as
1 BU by default (right in 1 BU = 1 mm scenes) and has its own drop handler.
"""

import os

import bpy
from bpy.props import CollectionProperty, StringProperty
from bpy.types import FileHandler, Operator, OperatorFileListElement
from bpy_extras.io_utils import ExportHelper, ImportHelper

from ..i18n import t
from ..io_3mf import export_3mf, import_3mf
from ..units import mm_per_bu


def export_candidates(context) -> list:
    """Selected objects, or every visible object when nothing is selected."""
    selected = list(context.selected_objects)
    if selected:
        return selected
    return [o for o in context.view_layer.objects if o.visible_get()]


def export_objects(path: str, objects: list, mm: float) -> tuple[list[str], list[str]]:
    """Write the visible meshes of objects; return (written, skipped) names.

    Raises ValueError when no mesh with faces is left to write.
    """
    meshes = [o for o in objects if o.type == "MESH" and o.visible_get()]
    skipped = [o.name for o in objects if o not in meshes]
    if not meshes:
        raise ValueError(t("no_mesh_to_export"))
    written = export_3mf(path, meshes, mm)
    skipped += [o.name for o in meshes if o.name not in written]
    return written, skipped


def export_message(written: list[str], skipped: list[str], filename: str) -> str:
    message = t("exported_3mf", n=len(written), file=filename)
    if skipped:
        message += " " + t("export_skipped", names=", ".join(skipped))
    return message


class BC_OT_import_3mf(Operator, ImportHelper):
    bl_idname = "bc.import_3mf"
    bl_label = t("import_3mf")
    bl_description = t("import_3mf")
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".3mf"
    filter_glob: StringProperty(default="*.3mf", options={"HIDDEN"})
    # SKIP_SAVE: the menu must open the file browser, not re-import the last file.
    filepath: StringProperty(subtype="FILE_PATH", options={"HIDDEN", "SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN", "SKIP_SAVE"})
    files: CollectionProperty(type=OperatorFileListElement, options={"HIDDEN", "SKIP_SAVE"})

    def _paths(self) -> list[str]:
        if self.directory and len(self.files):
            return [os.path.join(self.directory, f.name) for f in self.files if f.name]
        return [self.filepath] if self.filepath else []

    def invoke(self, context, event):
        # Dropped files arrive with their paths set: import right away.
        if self._paths():
            return self.execute(context)
        return ImportHelper.invoke(self, context, event)

    def execute(self, context):
        paths = self._paths()
        if not paths:
            return {"CANCELLED"}
        mm = mm_per_bu(context.scene)
        created = []
        for path in paths:
            try:
                created += import_3mf(path, mm)
            except Exception as exc:  # noqa: BLE001 - bad zip, XML or unit
                self.report({"ERROR"}, f"{os.path.basename(path)}: {exc}")
                return {"CANCELLED"}
        for obj in context.view_layer.objects:
            obj.select_set(False)
        for obj in created:
            obj.select_set(True)
        if created:
            context.view_layer.objects.active = created[0]
        self.report({"INFO"}, t("imported_3mf", n=len(created)))
        return {"FINISHED"}


class BC_OT_export_3mf(Operator, ExportHelper):
    bl_idname = "bc.export_3mf"
    bl_label = t("export_3mf")
    bl_description = t("export_3mf_desc")

    filename_ext = ".3mf"
    filter_glob: StringProperty(default="*.3mf", options={"HIDDEN"})

    def execute(self, context):
        context.view_layer.update()
        try:
            written, skipped = export_objects(
                self.filepath, export_candidates(context), mm_per_bu(context.scene)
            )
        except (OSError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        level = "WARNING" if skipped else "INFO"
        self.report({level}, export_message(written, skipped, os.path.basename(self.filepath)))
        return {"FINISHED"}


class IO_FH_bc_3mf(FileHandler):
    """Dropping .3mf files on the 3D View imports them."""

    bl_idname = "IO_FH_bc_3mf"
    bl_label = "3MF"
    bl_import_operator = "bc.import_3mf"
    bl_file_extensions = ".3mf"

    @classmethod
    def poll_drop(cls, context):
        area = context.area
        return area is not None and area.type == "VIEW_3D"


def menu_import(self, context) -> None:
    self.layout.operator("bc.import_3mf", text=t("menu_3mf"))


def menu_export(self, context) -> None:
    self.layout.operator("bc.export_3mf", text=t("menu_3mf"))


CLASSES = (BC_OT_import_3mf, BC_OT_export_3mf, IO_FH_bc_3mf)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    for menu, fn in (
        (bpy.types.TOPBAR_MT_file_import, menu_import),
        (bpy.types.TOPBAR_MT_file_export, menu_export),
    ):
        menu.remove(fn)
        menu.append(fn)


def unregister() -> None:
    bpy.types.TOPBAR_MT_file_import.remove(menu_import)
    bpy.types.TOPBAR_MT_file_export.remove(menu_export)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
