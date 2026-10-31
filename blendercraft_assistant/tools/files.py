"""File tools: import_file and export_file.

Exports for slicers (STL, OBJ, PLY, 3MF) are always in millimeters, whatever
the scene's unit scale or display unit. Imports of the same formats assume
the file is in millimeters (3MF and SolidCraft .sc declare their own unit).
"""

import json
from contextlib import contextmanager
from pathlib import Path

import bpy
from mathutils import Matrix

from .. import export_check
from ..io_3mf import import_3mf
from ..io_solidcraft import import_sc
from ..units import mm_per_bu
from . import paths
from .base import Tool, ToolResult, json_result, register_tool
from .inspect import _get_object

IMPORT_EXTENSIONS = (".stl", ".obj", ".ply", ".glb", ".gltf", ".3mf", ".sc")
EXPORT_FORMATS = ("stl", "obj", "ply", "glb", "gltf", "3mf")
# Formats for slicers: checked (helpers left out, overlaps reported) first.
PRINT_FORMATS = ("3mf", "stl")


def _path(args: dict) -> Path:
    raw = args.get("path")
    if not raw or not isinstance(raw, str):
        raise ValueError("'path' is required")
    return paths.resolve(raw)


def _run_operator(op, **kwargs) -> None:
    result = op(**kwargs)
    if "FINISHED" not in result:
        raise RuntimeError(f"{op.idname()} did not finish: {sorted(result)}")


def _select_only(objs: list, strict: bool = True) -> None:
    """Select exactly objs and make the first one active.

    strict (exports): an object that refuses selection raises, because the
    exporter would silently skip it. Otherwise (imports) it is left alone.
    """
    view_layer = bpy.context.view_layer
    for o in view_layer.objects:
        o.select_set(False)
    selected = []
    for o in objs:
        try:
            o.select_set(True)
            ok = o.select_get()
        except RuntimeError:  # not in the view layer
            ok = False
        if ok:
            selected.append(o)
        elif strict:
            raise ValueError(f"Object cannot be selected (hidden?): {o.name}")
    if selected:
        view_layer.objects.active = selected[0]


@contextmanager
def _keep_selection():
    """Restore selection and active object afterwards, even on error."""
    view_layer = bpy.context.view_layer
    selected = [o.name for o in view_layer.objects if o.select_get()]
    active = view_layer.objects.active
    active_name = active.name if active else None
    try:
        yield
    finally:
        for o in view_layer.objects:
            o.select_set(False)
        for name in selected:
            obj = view_layer.objects.get(name)
            if obj is not None:
                obj.select_set(True)
        view_layer.objects.active = (
            view_layer.objects.get(active_name) if active_name else None
        )


@contextmanager
def _scaled_root(objs: list, scale: float):
    """Temporarily parent objs under an empty scaled by ``scale``.

    glTF has no scale option; a scaled root node turns Blender units into
    metres without touching any mesh. Objects whose parent is not exported
    become children of the root with their world matrix as local matrix, so
    the exported world is ``scale`` times the scene's. Parenting, local
    matrices and the empty itself are always restored or removed.
    """
    exported = {o.name for o in objs}
    tops = [o for o in objs if o.parent is None or o.parent.name not in exported]
    saved = [(o, o.parent, o.matrix_parent_inverse.copy(), o.matrix_basis.copy()) for o in tops]
    worlds = [o.matrix_world.copy() for o in tops]
    root = bpy.data.objects.new("BlenderCraft_gltf_root", None)
    bpy.context.scene.collection.objects.link(root)
    try:
        root.scale = (scale, scale, scale)
        bpy.context.view_layer.update()
        for o, world in zip(tops, worlds):
            o.parent = root
            o.matrix_parent_inverse = Matrix.Identity(4)
            o.matrix_basis = world
        bpy.context.view_layer.update()
        yield root
    finally:
        for o, parent, inverse, basis in saved:
            o.parent = parent
            o.matrix_parent_inverse = inverse
            o.matrix_basis = basis
        bpy.data.objects.remove(root, do_unlink=True)
        bpy.context.view_layer.update()


def _requested_objects(args: dict) -> list:
    names = args.get("objects")
    if names:
        return [_get_object(str(n)) for n in names]
    objs = [o for o in bpy.context.view_layer.objects if o.select_get()]
    if not objs:
        raise ValueError("No 'objects' given and nothing is selected")
    return objs


def _check_exportable(objs: list) -> list:
    in_layer = bpy.context.view_layer.objects
    for o in objs:
        if in_layer.get(o.name) is not o:
            raise ValueError(f"Object is not in the current view layer: {o.name}")
        # Hidden objects are skipped by the operator exporters and are not
        # evaluated by the viewport depsgraph (wrong 3MF geometry).
        if not o.visible_get():
            raise ValueError(f"Object is hidden, unhide it to export: {o.name}")
    return objs


def _format(args: dict, path: Path, allowed: tuple[str, ...]) -> str:
    fmt = str(args.get("format") or path.suffix.lstrip(".")).lower()
    if fmt not in allowed:
        raise ValueError(
            f"Unsupported format {fmt!r}; supported: {', '.join(allowed)}"
        )
    return fmt


def _export_with_operator(fmt: str, path: Path, objs: list, mm: float) -> None:
    ops = bpy.ops
    filepath = str(path)
    with _keep_selection():
        if fmt in ("glb", "gltf"):
            _export_gltf(fmt, filepath, objs, mm)
            return
        _select_only(objs)
        # Z-up, Y-forward: slicers read the file as-is, with no axis change.
        axes = {"forward_axis": "Y", "up_axis": "Z"}
        if fmt == "stl":
            _run_operator(
                ops.wm.stl_export,
                filepath=filepath,
                export_selected_objects=True,
                global_scale=mm,
                apply_modifiers=True,
                **axes,
            )
        elif fmt == "obj":
            _run_operator(
                ops.wm.obj_export,
                filepath=filepath,
                export_selected_objects=True,
                global_scale=mm,
                apply_modifiers=True,
                **axes,
            )
        elif fmt == "ply":
            _run_operator(
                ops.wm.ply_export,
                filepath=filepath,
                export_selected_objects=True,
                global_scale=mm,
                apply_modifiers=True,
                **axes,
            )
        else:
            raise ValueError(f"Unsupported format: {fmt}")


def _export_gltf(fmt: str, filepath: str, objs: list, mm: float) -> None:
    """glTF is in metres: export under a root scaled mm_per_bu / 1000."""
    with _scaled_root(objs, mm / 1000.0) as root:
        _select_only([*objs, root])
        _run_operator(
            bpy.ops.export_scene.gltf,
            filepath=filepath,
            export_format="GLB" if fmt == "glb" else "GLTF_SEPARATE",
            use_selection=True,
            export_apply=True,
        )


def _print_options(args: dict) -> tuple[str, bool, bool]:
    group = str(args.get("group") or "pieces").lower()
    if group not in export_check.GROUPS:
        raise ValueError(
            f"Unknown group {group!r}; use one of: {', '.join(export_check.GROUPS)}"
        )
    return group, bool(args.get("strict")), bool(args.get("include_helpers"))


def _pieces_summary(pieces: list[dict]) -> dict:
    """Compact pieces for the tool result: part names and extruders per
    piece, and the colour of each extruder (filaments[0] is extruder 1)."""
    filaments: dict[int, str] = {}
    out = []
    for piece in pieces:
        for part in piece["parts"]:
            filaments[part["extruder"]] = part["color"]
        out.append(
            {
                "name": piece["name"],
                "parts": [p["name"] for p in piece["parts"]],
                "extruders": [p["extruder"] for p in piece["parts"]],
            }
        )
    return {"pieces": out, "filaments": [filaments[k] for k in sorted(filaments)]}


def _export_file(args: dict):
    # Bases of just-added or just-removed objects are stale until an update.
    bpy.context.view_layer.update()
    path = _path(args)
    fmt = _format(args, path, EXPORT_FORMATS)
    objs = _requested_objects(args)
    scene = bpy.context.scene
    mm = mm_per_bu(scene)
    skipped = []
    data_extra = {}
    if fmt in PRINT_FORMATS:
        group, strict, include_helpers = _print_options(args)
        if fmt == "stl":
            group = "objects"  # one mesh soup: every overlap matters
        prepared = export_check.prepare(
            objs, mm, group, include_helpers, accept=_check_exportable
        )
        export_check.refuse_if_strict(prepared.report, strict)
        skipped = prepared.skipped
        path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "stl":
            _export_with_operator(fmt, path, prepared.meshes, mm)
        else:
            pieces = export_check.write_3mf(str(path), prepared, mm)
            if pieces is not None:
                data_extra.update(_pieces_summary(pieces))
        objs = prepared.meshes
        data_extra = {"check": prepared.report.to_dict(), **data_extra}
    else:
        _check_exportable(objs)
        path.parent.mkdir(parents=True, exist_ok=True)
        _export_with_operator(fmt, path, objs, mm)
    if not path.is_file():
        raise RuntimeError(f"Export produced no file: {path}")
    # Long check reports first: the dispatcher truncates long results, so
    # what the user must see comes before the object lists.
    data = {
        "path": str(path),
        "format": fmt,
        **data_extra,
        "objects": [o.name for o in objs],
        "size_bytes": path.stat().st_size,
        "skipped": skipped,
        # The unit of the file's coordinates.
        "unit": "meter" if fmt in ("glb", "gltf") else "millimeter",
    }
    if "check" not in data:
        return json_result(data)
    # Print exports carry a long report: compact JSON keeps it whole.
    return ToolResult(text=json.dumps(data, ensure_ascii=False))


def _import_with_operator(fmt: str, path: Path, mm: float) -> None:
    ops = bpy.ops
    filepath = str(path)
    # 1 file unit = 1 mm, which is 1 / mm_per_bu Blender units.
    common = {"filepath": filepath, "global_scale": 1.0 / mm}
    axes = {"forward_axis": "Y", "up_axis": "Z"}
    if fmt == "stl":
        _run_operator(ops.wm.stl_import, **common, **axes)
    elif fmt == "obj":
        _run_operator(ops.wm.obj_import, **common, **axes)
    elif fmt == "ply":
        _run_operator(ops.wm.ply_import, **common, **axes)
    else:
        # glTF is in metres; Blender's importer already converts them with
        # the scene's unit scale (1 m = 1 / scale_length BU).
        _run_operator(ops.import_scene.gltf, filepath=filepath)


def _import_file(args: dict):
    path = _path(args)
    suffix = path.suffix.lower()
    if suffix not in IMPORT_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type {suffix!r}; supported: {', '.join(IMPORT_EXTENSIONS)}"
        )
    if not path.is_file():
        raise ValueError(f"File not found: {path}")
    fmt = suffix.lstrip(".")
    mm = mm_per_bu(bpy.context.scene)

    before = set(bpy.data.objects.keys())
    if fmt == "3mf":
        created = import_3mf(str(path), mm)
    elif fmt == "sc":
        created = import_sc(str(path), mm)
    else:
        _import_with_operator("glb" if fmt == "gltf" else fmt, path, mm)
        created = [o for o in bpy.data.objects if o.name not in before]
    # New bases only show up in the view layer after an update.
    bpy.context.view_layer.update()
    # The importers disagree about selection; leave the new objects selected.
    _select_only(created, strict=False)
    data = {
        "path": str(path),
        "format": fmt,
        "objects": [
            {
                "name": o.name,
                "type": o.type,
                "dimensions_mm": [round(d * mm, 3) for d in o.dimensions],
            }
            for o in created
        ],
    }
    if fmt == "sc":
        # One collection per project; hidden bodies are reference geometry.
        data["collection"] = created[0].users_collection[0].name
        data["hidden"] = [o.name for o in created if o.hide_get()]
    return json_result(data)


register_tool(
    Tool(
        name="import_file",
        description=(
            "Import a mesh file into the scene: .stl, .obj, .ply, .glb, .gltf "
            "or .3mf, or a SolidCraft project (.sc: one object per component "
            "body, named like in SolidCraft, grouped in a collection, "
            "colored and placed as in the project; bodies hidden in "
            "SolidCraft are imported hidden). STL, OBJ and PLY are assumed "
            "to be in millimeters; 3MF and SolidCraft use the unit declared "
            "in the file; glTF is in meters. Sizes are converted to the "
            "scene's units. The new objects are selected. Returns their "
            "names and dimensions in mm."
            + paths.RULE
        ),
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        handler=_import_file,
        kind="scene",
    )
)

register_tool(
    Tool(
        name="export_file",
        description=(
            "Export objects to a file. The format comes from the file "
            "extension (.stl, .obj, .ply, .glb, .gltf, .3mf) unless 'format' "
            "is given. Without 'objects' the current selection is exported. "
            "Modifiers are applied. STL, OBJ, PLY and 3MF are always written "
            "in millimeters and glTF in meters (its specification), whatever "
            "the scene unit. Hidden objects are rejected. Selection and "
            "active object are restored.\n"
            "3MF and STL are checked for printing first: cutters and helpers "
            "(collections BC_cutters/BC_backup/helpers, names BC_cut*, "
            "BC_off*, BC_Pivo*, BC_plan*, *furo*, *cutter*, Boolean modifier "
            "operands) are left out unless include_helpers is true; "
            "overlapping parts (a slicer fuses them) and non-manifold meshes "
            "are reported. ALWAYS show the result's check.report lines to "
            "the user and say what to fix (e.g. boolean-union a ball or pin "
            "into its host, subtract a hole). strict: true refuses to write "
            "the file when separate pieces overlap.\n"
            "3MF group: 'pieces' (default) writes one Bambu Studio object per "
            "print piece with its parts inside (Production extension plus "
            "Metadata/model_settings.config, one extruder per distinct "
            "colour), keeping assembled positions. A piece is an object plus "
            "its colour-insert children (pads, toes, eyes, or bc_role = "
            "'insert'), or the objects sharing a 'bc_piece' custom property; "
            "joint parts (ball, pin, cap) stay separate pieces. 'objects' "
            "writes the old flat layout, one object per Blender object."
            + paths.RULE
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "objects": {"type": "array", "items": {"type": "string"}},
                "format": {"type": "string"},
                "group": {
                    "type": "string",
                    "enum": ["pieces", "objects"],
                    "description": "3MF layout (default pieces).",
                },
                "strict": {
                    "type": "boolean",
                    "description": "3MF/STL: refuse to export when pieces overlap.",
                },
                "include_helpers": {
                    "type": "boolean",
                    "description": "3MF/STL: export cutters/helpers too (default false).",
                },
            },
            "required": ["path"],
        },
        handler=_export_file,
        kind="file",
    )
)
