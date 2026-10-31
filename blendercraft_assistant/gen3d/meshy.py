"""Meshy (default provider): https://docs.meshy.ai

- Text to 3D: POST /openapi/v2/text-to-3d, mode "preview" (geometry), then
  mode "refine" with preview_task_id when a texture is wanted.
- Image to 3D: POST /openapi/v1/image-to-3d, image_url as a base64 data URI.
- Multi-image to 3D: POST /openapi/v1/multi-image-to-3d, image_urls (1-4,
  the first is the front view; geometry_resolution standard or 2k only).
- Print repair: POST /openapi/v1/print/repair with input_task_id,
  printer_type and intended_print_height_mm (GLB input; removes textures).
- Each create returns {"result": task_id}; GET <endpoint>/<id> returns the
  task: status PENDING | IN_PROGRESS | SUCCEEDED | FAILED | CANCELED,
  progress 0-100, model_urls {glb, stl, ...}, task_error.message,
  consumed_credits.
"""

import threading

from .base import (
    GeneratedModel,
    GenerationError,
    GenerationRequest,
    PollState,
    ProgressFn,
    Provider,
)

BASE_URL = "https://api.meshy.ai"
AI_MODEL = "latest"
TARGET_FORMATS = ["glb", "stl"]
PROMPT_LIMIT = 800
PRINTER_TYPE = "fdm"

TEXT_PATH = "/openapi/v2/text-to-3d"
IMAGE_PATH = "/openapi/v1/image-to-3d"
MULTI_PATH = "/openapi/v1/multi-image-to-3d"
REPAIR_PATH = "/openapi/v1/print/repair"

_FINAL_FAIL = ("FAILED", "CANCELED")


def geometry_resolution(kind: str, quality: str) -> str:
    """standard, or the Ultra pass for quality "high" (4k; the multi-image
    endpoint only has 2k)."""
    if quality != "high":
        return "standard"
    return "2k" if kind == "multiview" else "4k"


def _geometry_fields(kind: str, quality: str) -> dict:
    return {
        "ai_model": AI_MODEL,
        "geometry_resolution": geometry_resolution(kind, quality),
        # No remesh: the raw mesh keeps the most detail.
        "should_remesh": False,
        "target_formats": list(TARGET_FORMATS),
    }


def preview_body(req: GenerationRequest) -> dict:
    return {"mode": "preview", "prompt": req.prompt, **_geometry_fields("text", req.quality)}


def refine_body(preview_task_id: str) -> dict:
    return {
        "mode": "refine",
        "preview_task_id": preview_task_id,
        "ai_model": AI_MODEL,
        "target_formats": list(TARGET_FORMATS),
    }


def image_body(req: GenerationRequest) -> dict:
    return {
        "image_url": req.images[0].data_uri(),
        **_geometry_fields("image", req.quality),
        "should_texture": bool(req.texture),
    }


def multi_body(req: GenerationRequest) -> dict:
    return {
        "image_urls": [img.data_uri() for img in req.images],
        **_geometry_fields("multiview", req.quality),
        "should_texture": bool(req.texture),
    }


def repair_body(input_task_id: str, height_mm: float) -> dict:
    body = {"input_task_id": input_task_id, "printer_type": PRINTER_TYPE}
    if 1.0 <= height_mm <= 1000.0:
        body["intended_print_height_mm"] = round(float(height_mm), 3)
    return body


def interpret(task) -> PollState:
    if not isinstance(task, dict):
        raise GenerationError("Meshy: unexpected task reply", "server")
    status = str(task.get("status", ""))
    progress = task.get("progress")
    progress = int(progress) if isinstance(progress, (int, float)) else None
    if status == "SUCCEEDED":
        return PollState(status, 100, done=True, payload=task)
    if status in _FINAL_FAIL:
        error = task.get("task_error") or {}
        message = error.get("message", "") if isinstance(error, dict) else str(error)
        return PollState(status, progress, failed=True, error=message)
    return PollState(status or "PENDING", progress)


class MeshyProvider(Provider):
    name = "meshy"
    poll_interval = 5.0
    max_wait = 3600.0  # docs: wait at least 60 min from created_at

    def generate(
        self,
        request: GenerationRequest,
        cancel: threading.Event,
        progress: ProgressFn,
    ) -> GeneratedModel:
        credits = 0
        task_ids: list[str] = []

        def run(stage: str, path: str, body: dict) -> dict:
            nonlocal credits
            created = self._create(BASE_URL + path, body, cancel)
            task_id = created.get("result") if isinstance(created, dict) else None
            if not task_id:
                raise GenerationError("Meshy: create reply without a task id", "server")
            task_ids.append(str(task_id))
            progress(stage, "PENDING", 0)
            task = self._poll(
                f"{BASE_URL}{path}/{task_id}",
                interpret,
                cancel,
                lambda st: progress(stage, st.status, st.progress),
            )
            credits += int(task.get("consumed_credits") or 0)
            return task

        if request.kind == "text":
            if not request.prompt:
                raise GenerationError("A text prompt is required", "input")
            if len(request.prompt) > PROMPT_LIMIT:
                raise GenerationError(f"Prompt over {PROMPT_LIMIT} characters", "input")
            task = run("generate", TEXT_PATH, preview_body(request))
            if request.texture:
                task = run("texture", TEXT_PATH, refine_body(task_ids[-1]))
        elif request.kind == "image":
            task = run("generate", IMAGE_PATH, image_body(request))
        elif request.kind == "multiview":
            if not 1 <= len(request.images) <= 4:
                raise GenerationError("Meshy takes 1 to 4 images", "input")
            task = run("generate", MULTI_PATH, multi_body(request))
        else:
            raise GenerationError(f"Unknown kind: {request.kind}", "input")

        urls = task.get("model_urls") or {}
        files = {}
        progress("download", "DOWNLOADING", None)
        for fmt in TARGET_FORMATS:
            url = urls.get(fmt)
            if url:
                files[fmt] = self._download(url, request.out_dir / f"model.{fmt}", cancel)
        if not files:
            raise GenerationError("Meshy: the task returned no GLB or STL", "server")

        meta = {
            "provider": self.name,
            "ai_model": task.get("ai_model") or AI_MODEL,
            "task_ids": list(task_ids),
            "geometry_resolution": geometry_resolution(request.kind, request.quality),
        }
        if request.print_repair and "glb" in files:
            repair = run("repair", REPAIR_PATH, repair_body(task_ids[-1], request.target_height_mm))
            url = (repair.get("model_urls") or {}).get("glb")
            if url:
                files["repaired_glb"] = self._download(
                    url, request.out_dir / "model_repaired.glb", cancel
                )
            meta["print_repair_report"] = repair.get("print_repair_report")
            meta["intended_print"] = repair.get("intended_print")
        meta["consumed_credits"] = credits
        main = files.get("repaired_glb") or files.get("glb") or files["stl"]
        return GeneratedModel(path=main, files=files, meta=meta)
