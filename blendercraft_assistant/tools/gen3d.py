"""AI 3D generation tools: generate_3d_model and generation_status.

Both handlers run off Blender's main thread (Tool.main_thread=False): the
generation is a background job (gen3d.jobs) that talks to the provider and
polls for minutes, while the tool call only waits for it up to WAIT_S
seconds. The bpy parts (settings, the import) go through run_on_main; the
import is one AI undo step, "IA: gerar modelo 3D".

Why a job id instead of one long call: an HTTP MCP client aborts a request
that has not started answering after its per-request timer, which Claude
Code sets to 60 s unless the server entry has a larger "timeout" (and an
external `claude mcp add` user would not have one). Generations take 1-4
minutes, so each call waits at most WAIT_S and Claude keeps waiting with
generation_status.
"""

import json
import os
import re
import threading
import time
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Matrix

from .. import mainthread
from ..gen3d import PROVIDERS, make_provider
from ..gen3d.base import (
    Cancelled,
    GenerationError,
    GenerationRequest,
    load_image,
    scrub,
)
from ..gen3d.jobs import registry
from ..gen3d.prompts import NEGATIVE, STYLES, build_prompt
from ..gen3d.meshy import PROMPT_LIMIT as MESHY_PROMPT_LIMIT
from ..gen3d.tripo import PROMPT_LIMIT as TRIPO_PROMPT_LIMIT
from ..i18n import t
from ..units import mm_per_bu
from . import paths
from .base import Tool, ToolResult, register_tool
from .dispatch import current_tracker, scene_step
from .topology import mesh_topology

WAIT_S = 40.0  # per call: under the 60 s per-request timer of HTTP MCP clients
MAX_STATUS_WAIT_S = 50.0
MAX_HEIGHT_MM = 1000.0
MAX_IMAGES = 4
UNDO_LABEL = "gerar modelo 3D"
OUTPUT_DIR = "geracoes"
NAME_PREFIX = "Gerado: "
ENV_KEYS = {"meshy": "MESHY_API_KEY", "tripo": "TRIPO_API_KEY"}
QUALITIES = ("standard", "high")

# The add-on package (blendercraft_assistant, or bl_ext.<repo>.<id> when
# installed): the preferences live under this id.
ADDON_ID = __package__.rpartition(".")[0]

# Test seams: a transport factory and extra provider kwargs (fake clock).
transport_factory = None
provider_kwargs: dict = {}


# ---- settings (main thread) -------------------------------------------


def _addon_prefs():
    addon = bpy.context.preferences.addons.get(ADDON_ID)
    return None if addon is None else addon.preferences


def _online_access() -> bool:
    return bool(bpy.app.online_access)


def api_key(provider: str) -> str:
    """Key from the add-on preferences, else the environment variable."""
    prefs = _addon_prefs()
    key = getattr(prefs, f"{provider}_api_key", "") if prefs is not None else ""
    return (key or os.environ.get(ENV_KEYS[provider], "")).strip()


def has_any_key() -> bool:
    return any(api_key(p) for p in PROVIDERS)


def _settings(image_paths: list[str]) -> dict:
    prefs = _addon_prefs()
    default = getattr(prefs, "gen3d_provider", "MESHY") if prefs is not None else "MESHY"
    region = getattr(prefs, "tripo_region", "AI") if prefs is not None else "AI"
    return {
        "online": _online_access(),
        "keys": {p: api_key(p) for p in PROVIDERS},
        "default_provider": str(default).lower(),
        "tripo_region": str(region).lower(),
        "out_base": paths.resolve(OUTPUT_DIR),
        "images": [paths.resolve(p) for p in image_paths],
        "mm_per_bu": mm_per_bu(bpy.context.scene),
    }


def _redraw() -> None:
    from .. import runtime  # lazy: runtime imports the tool catalog

    runtime.tag_redraw()


# ---- import (main thread) ---------------------------------------------


def _bake_world(obj) -> None:
    """Apply the object's world matrix to its mesh and unparent it."""
    if obj.data.users > 1:
        obj.data = obj.data.copy()
    obj.data.transform(obj.matrix_world)
    obj.parent = None
    obj.matrix_world = Matrix.Identity(4)


def _join(meshes: list):
    if len(meshes) == 1:
        return meshes[0]
    with bpy.context.temp_override(
        active_object=meshes[0],
        selected_objects=meshes,
        selected_editable_objects=meshes,
    ):
        result = bpy.ops.object.join()
    if "FINISHED" not in result:
        raise RuntimeError(f"Joining the generated parts failed: {sorted(result)}")
    return meshes[0]


def _weld(mesh) -> int:
    """Merge co-located vertices; returns how many were removed.

    glTF stores a vertex once per normal/UV combination, so an imported
    surface is cut along every UV seam and sharp edge. Welding (within a
    millionth of the size) makes it one closed surface again for the print
    checks; the colours (UVs and materials) are per face corner and stay.
    """
    lo, hi = _bounds(mesh)
    dist = max(float(np.linalg.norm(hi - lo)) * 1e-6, 1e-9)
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        before = len(bm.verts)
        bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=dist)
        removed = before - len(bm.verts)
        bm.to_mesh(mesh)
    finally:
        bm.free()
    mesh.update()
    return removed


def _bounds(mesh) -> tuple[np.ndarray, np.ndarray]:
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    return co.min(axis=0), co.max(axis=0)


def import_model(path: Path, target_height_mm: float, name: str) -> dict:
    """Import a generated GLB/STL as one mesh object, scaled so its height
    is target_height_mm, standing on Z = 0 and centred on X/Y."""
    scene = bpy.context.scene
    mm = mm_per_bu(scene)
    before = set(bpy.data.objects)
    path = Path(path)
    if path.suffix.lower() in (".glb", ".gltf"):
        result = bpy.ops.import_scene.gltf(filepath=str(path))
    elif path.suffix.lower() == ".stl":
        result = bpy.ops.wm.stl_import(
            filepath=str(path), global_scale=1.0 / mm, forward_axis="Y", up_axis="Z"
        )
    else:
        raise ValueError(f"Cannot import {path.suffix} files")
    if "FINISHED" not in result:
        raise RuntimeError(f"Import of {path.name} failed: {sorted(result)}")
    bpy.context.view_layer.update()
    created = [o for o in bpy.data.objects if o not in before]
    meshes = [o for o in created if o.type == "MESH" and len(o.data.vertices)]
    if not meshes:
        for o in created:
            bpy.data.objects.remove(o, do_unlink=True)
        raise RuntimeError(f"{path.name} has no mesh")
    for obj in meshes:
        _bake_world(obj)
    for obj in created:
        if obj not in meshes:
            bpy.data.objects.remove(obj, do_unlink=True)
    obj = _join(meshes)
    mesh = obj.data
    welded = _weld(mesh)

    lo, hi = _bounds(mesh)
    height = float(hi[2] - lo[2])
    if height <= 0.0:
        raise RuntimeError("The generated mesh is flat (zero height)")
    factor = (target_height_mm / mm) / height
    cx, cy = (lo[0] + hi[0]) / 2.0, (lo[1] + hi[1]) / 2.0
    mesh.transform(
        Matrix.Translation((-cx * factor, -cy * factor, -lo[2] * factor))
        @ Matrix.Scale(factor, 4)
    )
    mesh.update()
    obj.location = (0.0, 0.0, 0.0)
    obj.name = name
    mesh.name = name

    view_layer = bpy.context.view_layer
    for o in view_layer.objects:
        o.select_set(False)
    if view_layer.objects.get(obj.name) is obj:
        obj.select_set(True)
        view_layer.objects.active = obj
    view_layer.update()
    return {
        "object": obj.name,
        "dimensions_mm": [round(d * mm, 3) for d in obj.dimensions],
        "faces": len(mesh.polygons),
        "welded_vertices": welded,
        "materials": [m.name for m in mesh.materials if m is not None],
    }


def _topology_summary(name: str) -> dict:
    topo = mesh_topology(name)
    keys = ("components", "manifold", "boundary_edges", "non_manifold_edges", "inverted_normals")
    return {k: topo[k] for k in keys}


def _next_steps(topo: dict) -> list[str]:
    steps = []
    if not topo["manifold"] or topo["boundary_edges"] or topo["non_manifold_edges"]:
        steps.append(
            "Not watertight: repair before printing (voxel remesh with a voxel "
            "size about 0.5% of the height, or fill holes), then mesh_topology again."
        )
    if topo["components"] > 1:
        steps.append(
            f"{topo['components']} loose parts: delete the small floating ones "
            "or join them into the body."
        )
    if topo["inverted_normals"]:
        steps.append("Normals point inward: recalculate them outside.")
    steps.append(
        "Screenshot it from two angles, flatten the bottom so it sits on the "
        "bed, then run print_check."
    )
    return steps


def _object_name(subject: str, images: list[Path]) -> str:
    base = subject.strip() or (images[0].stem if images else "modelo")
    base = " ".join(base.split())
    if len(base) > 40:
        base = base[:40].rstrip() + "…"
    return NAME_PREFIX + base


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:40] or "modelo"


# ---- tools --------------------------------------------------------------


def _error(text: str, keys=()) -> ToolResult:
    return ToolResult(text=scrub(text, keys), is_error=True)


def _hint(code: str, provider: str) -> str:
    label = provider.capitalize()
    return {
        "auth": t("gen3d_bad_key", provider=label),
        "credits": t("gen3d_no_credits", provider=label),
        "rate_limit": t("gen3d_rate_limit", provider=label),
    }.get(code, "")


def _report(job, budget: float) -> ToolResult:
    if budget > 0 and threading.current_thread() is not threading.main_thread():
        job.done.wait(budget)
    data = job.summary()
    if data["state"] == "running":
        data["next"] = (
            "Still generating. Call generation_status with this job_id to keep "
            "waiting; tell the user it is in progress."
        )
    elif data["state"] == "failed":
        hint = _hint(data.get("error_code", ""), job.provider)
        if hint:
            data["user_message"] = hint
    text = scrub(json.dumps(data, ensure_ascii=False, indent=1), job.secret_values)
    return ToolResult(text=text, is_error=data["state"] == "failed")


def _parse_args(args: dict) -> dict:
    prompt = args.get("prompt") or ""
    if not isinstance(prompt, str):
        raise ValueError("'prompt' must be a string")
    images = args.get("image_paths") or []
    if not isinstance(images, list) or not all(isinstance(p, str) and p for p in images):
        raise ValueError("'image_paths' must be a list of file paths")
    if not prompt.strip() and not images:
        raise ValueError("Give a 'prompt', 'image_paths' or both")
    if len(images) > MAX_IMAGES:
        raise ValueError(f"At most {MAX_IMAGES} images")
    try:
        height = float(args["target_height_mm"])
    except KeyError:
        raise ValueError("'target_height_mm' is required") from None
    except (TypeError, ValueError):
        raise ValueError("'target_height_mm' must be a number") from None
    if not 0.0 < height <= MAX_HEIGHT_MM:
        raise ValueError(f"'target_height_mm' must be between 0 and {MAX_HEIGHT_MM:g}")
    style = args.get("style") or "figurine"
    if style not in STYLES:
        raise ValueError(f"'style' must be one of {', '.join(STYLES)}")
    quality = args.get("quality") or "standard"
    if quality not in QUALITIES:
        raise ValueError(f"'quality' must be one of {', '.join(QUALITIES)}")
    provider = args.get("provider")
    if provider is not None and provider not in PROVIDERS:
        raise ValueError(f"'provider' must be one of {', '.join(PROVIDERS)}")
    return {
        "prompt": prompt.strip(),
        "images": images,
        "height": height,
        "style": style,
        "quality": quality,
        "provider": provider,
        "texture": bool(args.get("texture", True)),
        "print_repair": bool(args.get("print_repair", False)),
    }


def _generate(args: dict) -> ToolResult:
    try:
        opts = _parse_args(args)
    except ValueError as exc:
        return _error(str(exc))
    settings = mainthread.run_on_main(lambda: _settings(opts["images"]))
    keys = tuple(settings["keys"].values())
    if not settings["online"]:
        return _error(t("gen3d_offline"))
    provider = opts["provider"] or settings["default_provider"]
    if provider not in PROVIDERS:
        provider = "meshy"
    key = settings["keys"][provider]
    if not key:
        return _error(t("gen3d_no_key", provider=provider.capitalize(), env=ENV_KEYS[provider]))
    if opts["print_repair"] and provider != "meshy":
        return _error("print_repair is only available with provider 'meshy'.")
    try:
        images = [load_image(p) for p in settings["images"]]
    except GenerationError as exc:
        return _error(str(exc), keys)

    if not images:
        kind = "text"
    elif len(images) == 1:
        kind = "image"
    else:
        kind = "multiview"
    if kind == "multiview" and provider == "tripo" and len(images) < 2:
        return _error("Tripo multiview needs at least 2 images")
    limit = MESHY_PROMPT_LIMIT if provider == "meshy" else TRIPO_PROMPT_LIMIT
    prompt_sent = build_prompt(opts["prompt"], opts["style"], limit) if kind == "text" else ""

    stamp = time.strftime("%Y%m%d-%H%M%S")
    subject = opts["prompt"] or (settings["images"][0].stem if settings["images"] else "")
    out_dir = settings["out_base"] / f"{stamp}-{_slug(subject)}"
    request = GenerationRequest(
        kind=kind,
        prompt=prompt_sent,
        images=images,
        negative_prompt=NEGATIVE,
        quality=opts["quality"],
        texture=opts["texture"],
        target_height_mm=opts["height"],
        print_repair=opts["print_repair"],
        out_dir=out_dir,
    )
    name = _object_name(opts["prompt"], settings["images"])
    tracker = current_tracker()
    kwargs = dict(provider_kwargs)
    if provider == "tripo":
        kwargs["region"] = settings["tripo_region"]
    transport = transport_factory() if transport_factory is not None else None

    def progress(job, stage, status, percent):
        job.set_progress(stage, status, percent)
        mainthread.post(_redraw)

    def run(job) -> dict:
        prov = make_provider(provider, key, transport=transport, **kwargs)
        out_dir.mkdir(parents=True, exist_ok=True)
        model = prov.generate(
            request, job.cancel, lambda s, st, p: progress(job, s, st, p)
        )
        if job.cancel.is_set():
            raise Cancelled()
        progress(job, "import", "importing", None)

        def import_step():
            if job.cancel.is_set():  # cancelled while queued for the main thread
                raise Cancelled()
            info = scene_step(
                lambda: import_model(model.path, request.target_height_mm, name),
                UNDO_LABEL,
                tracker,
            )
            info["mesh_topology"] = _topology_summary(info["object"])
            return info

        info = mainthread.run_on_main(import_step)
        return {
            **info,
            "next_steps": _next_steps(info["mesh_topology"]),
            "files": {fmt: str(p) for fmt, p in model.files.items()},
            "imported_file": str(model.path),
            "kind": kind,
            "style": opts["style"] if kind == "text" else None,
            "prompt_sent": prompt_sent or None,
            **model.meta,
        }

    job = registry.start(provider, run, on_change=lambda: mainthread.post(_redraw),
                         secret_values=keys)
    return _report(job, WAIT_S)


def _status(args: dict) -> ToolResult:
    job_id = args.get("job_id")
    job = registry.get(str(job_id)) if job_id else None
    if job is None:
        return _error(f"Unknown job_id: {job_id!r}")
    if args.get("cancel"):
        job.cancel.set()
        job.done.wait(5.0)
        return _report(job, 0.0)
    try:
        wait = float(args.get("wait_seconds", WAIT_S))
    except (TypeError, ValueError):
        return _error("'wait_seconds' must be a number")
    return _report(job, min(max(wait, 0.0), MAX_STATUS_WAIT_S))


register_tool(
    Tool(
        name="generate_3d_model",
        description=(
            "Generate an organic 3D model (animal, character, figurine) with an "
            "AI text/image-to-3D service (Meshy by default, Tripo as fallback) "
            "and import it: one mesh named 'Gerado: …', scaled so its height is "
            "target_height_mm, standing on Z = 0 and centred on X/Y. Use it for "
            "realistic or figurine requests and when the user gives a photo; "
            "model hard-surface parts yourself. prompt: the subject in English "
            "(the style adds the rest); image_paths: 1-4 photos or renders of "
            "the same subject (PNG/JPEG/WebP; the first is the front view; for "
            "Tripo the order is front, left, back, right). style: figurine "
            "(default), realistic or toy-articulated. quality: standard or high "
            "(finer geometry, more credits). texture (default true): colours in "
            "the GLB. print_repair (Meshy only, costs 10 credits): Meshy's "
            "printability repair for an FDM print at target_height_mm; removes "
            "colours. The generation takes 1-4 minutes and costs the user's "
            "credits: call it once per request and do not retry a failure "
            "without asking. Each call waits up to 40 s; while state is "
            "'running', call generation_status with the job_id. The result "
            "gives the object, dimensions in mm, face count, mesh_topology "
            "(components, manifold) and the downloaded files (in a 'geracoes' "
            "folder next to the .blend, or ~/Documents/BlenderCraft/geracoes)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "image_paths": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
                "style": {"type": "string", "enum": list(STYLES)},
                "target_height_mm": {"type": "number"},
                "provider": {"type": "string", "enum": list(PROVIDERS)},
                "quality": {"type": "string", "enum": list(QUALITIES)},
                "texture": {"type": "boolean"},
                "print_repair": {"type": "boolean"},
            },
            "required": ["target_height_mm"],
        },
        handler=_generate,
        kind="scene",
        push_undo=False,  # the import pushes "IA: gerar modelo 3D" itself
        main_thread=False,
    )
)

register_tool(
    Tool(
        name="generation_status",
        description=(
            "State of a generate_3d_model job: waits up to wait_seconds "
            "(default 40, max 50) for it to finish, then returns state "
            "(running, succeeded, failed, cancelled), stage, progress and, "
            "once succeeded, the same result as generate_3d_model (the model "
            "is already imported). cancel: true stops the job."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "wait_seconds": {"type": "number"},
                "cancel": {"type": "boolean"},
            },
            "required": ["job_id"],
        },
        handler=_status,
        kind="read",
        main_thread=False,
    )
)
