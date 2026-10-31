"""File > Open model…, File > Import/Export 3MF, File > Import SolidCraft
and drag-and-drop of .3mf and .sc.

3MF goes through io_3mf (files in millimeters, converted with mm_per_bu).
STL needs nothing here: Blender's own STL importer reads 1 file unit as
1 BU by default (right in 1 BU = 1 mm scenes) and has its own drop handler.
"""

import os

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, StringProperty
from bpy.types import FileHandler, Operator, OperatorFileListElement
from bpy_extras.io_utils import ExportHelper, ImportHelper

from .. import export_check
from ..i18n import t
from ..io_3mf import export_3mf, import_3mf
from ..io_solidcraft import import_sc
from ..units import mm_per_bu

MODEL_EXTENSIONS = (".stl", ".3mf", ".obj", ".ply", ".glb", ".gltf", ".sc")


def export_candidates(context) -> list:
    """Selected objects, or every visible object when nothing is selected."""
    selected = list(context.selected_objects)
    if selected:
        return selected
    return [o for o in context.view_layer.objects if o.visible_get()]


def export_print(
    path: str,
    objects: list,
    mm: float,
    group: str = "pieces",
    strict: bool = False,
    include_helpers: bool = False,
):
    """Check and write the visible meshes of objects as 3MF.

    Helpers (cutters, pivots, backups) are left out unless include_helpers;
    strict refuses (ExportRefused) when separate pieces overlap. Returns
    export_check.Prepared plus the pieces written (None in the flat layout).
    Raises ValueError when no mesh with faces is left to write.
    """

    def visible(objs):
        return [o for o in objs if o.visible_get()]

    prepared = export_check.prepare(objects, mm, group, include_helpers, accept=visible)
    export_check.refuse_if_strict(prepared.report, strict)
    pieces = export_check.write_3mf(path, prepared, mm)
    return prepared, pieces


def export_objects(path: str, objects: list, mm: float) -> tuple[list[str], list[str]]:
    """Flat 3MF of the visible meshes of objects; return (written, skipped).

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


class BC_OT_import_sc(Operator, ImportHelper):
    """File > Import > SolidCraft (.sc): one object per component body, in a
    collection named after the project, at the right size."""

    bl_idname = "bc.import_sc"
    bl_label = t("import_sc")
    bl_description = t("import_sc")
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".sc"
    filter_glob: StringProperty(default="*.sc", options={"HIDDEN"})
    filepath: StringProperty(subtype="FILE_PATH", options={"HIDDEN", "SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN", "SKIP_SAVE"})
    files: CollectionProperty(type=OperatorFileListElement, options={"HIDDEN", "SKIP_SAVE"})

    _paths = BC_OT_import_3mf._paths

    def invoke(self, context, event):
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
                created += import_sc(path, mm)
            except Exception as exc:  # noqa: BLE001 - bad zip, JSON or blob
                self.report({"ERROR"}, f"{os.path.basename(path)}: {exc}")
                return {"CANCELLED"}
        for obj in context.view_layer.objects:
            obj.select_set(False)
        shown = [o for o in created if o.visible_get()]
        for obj in shown:
            obj.select_set(True)
        if shown:
            context.view_layer.objects.active = shown[0]
        self.report({"INFO"}, t("imported_sc", n=len(created)))
        return {"FINISHED"}


class BC_OT_export_3mf(Operator, ExportHelper):
    bl_idname = "bc.export_3mf"
    bl_label = t("export_3mf")
    bl_description = t("export_3mf_desc")

    filename_ext = ".3mf"
    filter_glob: StringProperty(default="*.3mf", options={"HIDDEN"})
    group: EnumProperty(
        name=t("export_group"),
        items=(
            ("PIECES", t("export_group_pieces"), t("export_group_pieces_desc")),
            ("OBJECTS", t("export_group_objects"), t("export_group_objects_desc")),
        ),
        default="PIECES",
    )
    strict: BoolProperty(name=t("export_strict"), description=t("export_strict_desc"))
    include_helpers: BoolProperty(
        name=t("export_include_helpers"), description=t("export_include_helpers_desc")
    )

    def execute(self, context):
        context.view_layer.update()
        try:
            prepared, pieces = export_print(
                self.filepath,
                export_candidates(context),
                mm_per_bu(context.scene),
                group=self.group.lower(),
                strict=self.strict,
                include_helpers=self.include_helpers,
            )
        except (OSError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        filename = os.path.basename(self.filepath)
        skipped = [s["name"] for s in prepared.skipped]
        if pieces is not None:
            message = t(
                "exported_3mf_pieces",
                n=len(pieces),
                parts=sum(len(p["parts"]) for p in pieces),
                file=filename,
            )
            if skipped:
                message += " " + t("export_skipped", names=", ".join(skipped))
        else:
            message = export_message([o.name for o in prepared.meshes], skipped, filename)
        report = prepared.report
        notes = [] if report.lines() == [t("chk_ok")] else report.lines()
        level = "WARNING" if skipped or notes else "INFO"
        self.report({level}, "\n".join([message, *notes]))
        return {"FINISHED"}


def _frame_selected(context) -> None:
    """Frame the selection in the largest 3D View (no-op without a UI)."""
    screen = context.screen if context.window is not None else None
    areas = [a for a in getattr(screen, "areas", ()) if a.type == "VIEW_3D"]
    if not areas:
        return
    area = max(areas, key=lambda a: a.width * a.height)
    region = next((r for r in area.regions if r.type == "WINDOW"), None)
    if region is None:
        return
    with context.temp_override(area=area, region=region):
        bpy.ops.view3d.view_selected()


class BC_OT_open_model(Operator, ImportHelper):
    """File > Open model…: STL, 3MF, OBJ, PLY, glTF and SolidCraft into the current
    scene, at the right size (the same import as the assistant's
    import_file tool). File > Open itself only reads .blend files."""

    bl_idname = "bc.open_model"
    bl_label = t("open_model")
    bl_description = t("open_model_desc")
    bl_options = {"REGISTER", "UNDO"}

    filter_glob: StringProperty(
        default=";".join(f"*{ext}" for ext in MODEL_EXTENSIONS), options={"HIDDEN"}
    )
    filepath: StringProperty(subtype="FILE_PATH", options={"HIDDEN", "SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN", "SKIP_SAVE"})
    files: CollectionProperty(type=OperatorFileListElement, options={"HIDDEN", "SKIP_SAVE"})

    def _paths(self) -> list[str]:
        if self.directory and len(self.files):
            return [os.path.join(self.directory, f.name) for f in self.files if f.name]
        return [self.filepath] if self.filepath else []

    def execute(self, context):
        from ..tools.files import _import_file

        paths = self._paths()
        if not paths:
            return {"CANCELLED"}
        created = []
        errors = []
        for path in paths:
            before = set(bpy.data.objects)
            try:
                _import_file({"path": path})
            except Exception as exc:  # noqa: BLE001 - reported to the user
                errors.append(f"{os.path.basename(path)}: {exc}")
            created += [o for o in bpy.data.objects if o not in before]
        if not created:
            self.report({"ERROR"}, "\n".join(errors) or t("opened_models", n=0))
            return {"CANCELLED"}
        for obj in context.view_layer.objects:
            obj.select_set(False)
        for obj in created:
            if context.view_layer.objects.get(obj.name) is obj:
                obj.select_set(True)
        shown = [o for o in created if context.view_layer.objects.get(o.name) is o and o.visible_get()]
        context.view_layer.objects.active = (shown or created)[0]
        _frame_selected(context)
        if errors:
            self.report({"WARNING"}, "\n".join(errors))
        self.report({"INFO"}, t("opened_models", n=len(created)))
        return {"FINISHED"}


# Blender's own File menu draw, wrapped so "Open model…" sits right under
# "Open…" (a menu can only be appended or prepended to otherwise).
_original_file_draw = None


class _InsertAfterOpen:
    """Layout stand-in that adds our item right after wm.open_mainfile."""

    def __init__(self, layout, inserted: list):
        object.__setattr__(self, "_layout", layout)
        object.__setattr__(self, "_inserted", inserted)

    def __getattr__(self, name):
        return getattr(self._layout, name)

    def __setattr__(self, name, value):
        setattr(self._layout, name, value)

    def operator(self, idname, *args, **kwargs):
        result = self._layout.operator(idname, *args, **kwargs)
        if idname == "wm.open_mainfile" and not self._inserted:
            _draw_open_model(self._layout)
            self._inserted.append(True)
        return result


class _MenuStandIn:
    def __init__(self, menu, layout):
        object.__setattr__(self, "_menu", menu)
        object.__setattr__(self, "layout", layout)

    def __getattr__(self, name):
        return getattr(self._menu, name)


def _draw_open_model(layout) -> None:
    layout.operator("bc.open_model", text=t("open_model"), icon="IMPORT")


def draw_file_menu(self, context) -> None:
    inserted: list = []
    original = _original_file_draw
    if original is not None:
        original(_MenuStandIn(self, _InsertAfterOpen(self.layout, inserted)), context)
    if not inserted:
        _draw_open_model(self.layout)


def _wrap_file_menu() -> None:
    global _original_file_draw
    funcs = bpy.types.TOPBAR_MT_file._dyn_ui_initialize()
    if funcs and funcs[0] is not draw_file_menu:
        _original_file_draw = funcs[0]
        funcs[0] = draw_file_menu


def _unwrap_file_menu() -> None:
    global _original_file_draw
    funcs = bpy.types.TOPBAR_MT_file._dyn_ui_initialize()
    if funcs and funcs[0] is draw_file_menu and _original_file_draw is not None:
        funcs[0] = _original_file_draw
    _original_file_draw = None


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


class IO_FH_bc_sc(FileHandler):
    """Dropping .sc (SolidCraft) files on the 3D View imports them."""

    bl_idname = "IO_FH_bc_sc"
    bl_label = "SolidCraft"
    bl_import_operator = "bc.import_sc"
    bl_file_extensions = ".sc"

    @classmethod
    def poll_drop(cls, context):
        area = context.area
        return area is not None and area.type == "VIEW_3D"


def menu_import(self, context) -> None:
    self.layout.operator("bc.import_3mf", text=t("menu_3mf"))


def menu_import_sc(self, context) -> None:
    self.layout.operator("bc.import_sc", text=t("menu_sc"))


def menu_export(self, context) -> None:
    self.layout.operator("bc.export_3mf", text=t("menu_3mf"))


CLASSES = (
    BC_OT_import_3mf,
    BC_OT_import_sc,
    BC_OT_export_3mf,
    BC_OT_open_model,
    IO_FH_bc_3mf,
    IO_FH_bc_sc,
)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    for menu, fn in (
        (bpy.types.TOPBAR_MT_file_import, menu_import),
        (bpy.types.TOPBAR_MT_file_import, menu_import_sc),
        (bpy.types.TOPBAR_MT_file_export, menu_export),
    ):
        menu.remove(fn)
        menu.append(fn)
    _wrap_file_menu()


def unregister() -> None:
    _unwrap_file_menu()
    bpy.types.TOPBAR_MT_file_import.remove(menu_import)
    bpy.types.TOPBAR_MT_file_import.remove(menu_import_sc)
    bpy.types.TOPBAR_MT_file_export.remove(menu_export)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
