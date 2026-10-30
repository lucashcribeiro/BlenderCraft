"""File tools: import_file and export_file.

Exports for slicers (STL, OBJ, PLY, 3MF) are always in millimeters, whatever
the scene's unit scale or display unit. Imports of the same formats assume
the file is in millimeters.
"""

from contextlib import contextmanager
from pathlib import Path

import bpy
from mathutils import Matrix

from ..io_3mf import export_3mf, import_3mf
from ..units import mm_per_bu
from . import paths
from .base import Tool, json_result, register_tool
from .inspect import _get_object

IMPORT_EXTENSIONS = (".stl", ".obj", ".ply", ".glb", ".gltf", ".3mf")
EXPORT_FORMATS = ("stl", "obj", "ply", "glb", "gltf", "3mf")


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


def _export_objects(args: dict) -> list:
    names = args.get("objects")
    if names:
        objs = [_get_object(str(n)) for n in names]
    else:
        objs = [o for o in bpy.context.view_layer.objects if o.select_get()]
        if not objs:
            raise ValueError("No 'objects' given and nothing is selected")
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


def _export_file(args: dict):
    # Bases of just-added or just-removed objects are stale until an update.
    bpy.context.view_layer.update()
    path = _path(args)
    fmt = _format(args, path, EXPORT_FORMATS)
    objs = _export_objects(args)
    scene = bpy.context.scene
    mm = mm_per_bu(scene)
    skipped = []
    if fmt == "3mf":
        skipped = [
            {"name": o.name, "reason": "not a mesh"} for o in objs if o.type != "MESH"
        ]
        meshes = [o for o in objs if o.type == "MESH"]
        if not meshes:
            raise ValueError("3MF export needs at least one mesh object")
        path.parent.mkdir(parents=True, exist_ok=True)
        written = export_3mf(str(path), meshes, mm)
        skipped += [
            {"name": o.name, "reason": "no faces"}
            for o in meshes
            if o.name not in written
        ]
        objs = [o for o in meshes if o.name in written]
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        _export_with_operator(fmt, path, objs, mm)
    if not path.is_file():
        raise RuntimeError(f"Export produced no file: {path}")
    data = {
        "path": str(path),
        "format": fmt,
        "objects": [o.name for o in objs],
        "size_bytes": path.stat().st_size,
        "skipped": skipped,
        # The unit of the file's coordinates.
        "unit": "meter" if fmt in ("glb", "gltf") else "millimeter",
    }
    return json_result(data)


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
    else:
        _import_with_operator("glb" if fmt == "gltf" else fmt, path, mm)
        created = [o for o in bpy.data.objects if o.name not in before]
    # New bases only show up in the view layer after an update.
    bpy.context.view_layer.update()
    # The importers disagree about selection; leave the new objects selected.
    _select_only(created, strict=False)
    return json_result(
        {
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
    )


register_tool(
    Tool(
        name="import_file",
        description=(
            "Import a mesh file into the scene: .stl, .obj, .ply, .glb, .gltf "
            "or .3mf. STL, OBJ and PLY are assumed to be in millimeters; 3MF "
            "uses the unit declared in the file; glTF is in meters. Sizes "
            "are converted to the scene's units. The new objects are "
            "selected. Returns their names and dimensions in mm."
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
            "active object are restored."
            + paths.RULE
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "objects": {"type": "array", "items": {"type": "string"}},
                "format": {"type": "string"},
            },
            "required": ["path"],
        },
        handler=_export_file,
        kind="file",
    )
)
