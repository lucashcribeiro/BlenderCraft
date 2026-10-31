"""Tripo (fallback provider): https://developers.tripo3d.ai/en/docs

- Base URL: https://openapi.tripo3d.ai/v3 (international keys) or
  https://openapi.tripo3d.com/v3 (keys made on the China site). A key only
  works on the site it was created on.
- Upload: POST /files, multipart/form-data field "file" (JPEG or PNG,
  20 MB) -> {"code": 0, "data": {"file_token": ...}}.
- POST /generation/text-to-model {prompt, ...},
  /generation/image-to-model {input: file_token, ...},
  /generation/multiview-to-model {inputs: [{"front": token}, ...]} (front
  required, at least 2 images) -> {"code": 0, "data": {"task_id": ...}}.
- GET /tasks/{task_id} -> data.status queued | running | success | failed |
  cancelled (legacy: banned, expired), data.progress, data.output.model_url
  (GLB), data.credits_consumed, data.error_code / error_message.
"""

import threading

from .base import (
    GeneratedModel,
    GenerationError,
    GenerationRequest,
    PollState,
    ProgressFn,
    Provider,
    build_multipart,
)

HOSTS = {
    "ai": "https://openapi.tripo3d.ai/v3",
    "com": "https://openapi.tripo3d.com/v3",
}
MODEL = "v3.1-20260211"
PROMPT_LIMIT = 1024
NEGATIVE_LIMIT = 255
VIEWS = ("front", "left", "back", "right")  # order of image_paths for multiview
UPLOAD_TYPES = ("image/png", "image/jpeg")

_RUNNING = ("queued", "running")
_FAILED = ("failed", "cancelled", "banned", "expired")


def _options(req: GenerationRequest) -> dict:
    body = {
        "model": MODEL,
        "texture": bool(req.texture),
        # pbr defaults to true and would force a texture; plain colours are
        # enough for printing.
        "pbr": False,
        "geometry_quality": "detailed" if req.quality == "high" else "standard",
    }
    if req.texture and req.quality == "high":
        body["texture_quality"] = "detailed"
    return body


def text_body(req: GenerationRequest) -> dict:
    body = {"prompt": req.prompt}
    if req.negative_prompt:
        body["negative_prompt"] = req.negative_prompt[:NEGATIVE_LIMIT]
    body.update(_options(req))
    return body


def image_body(file_token: str, req: GenerationRequest) -> dict:
    return {"input": file_token, **_options(req)}


def multiview_body(tokens: list[str], req: GenerationRequest) -> dict:
    return {
        "inputs": [{view: token} for view, token in zip(VIEWS, tokens)],
        **_options(req),
    }


def _data(reply) -> dict:
    """data of a {"code": 0, "data": {...}} reply, or raise."""
    if not isinstance(reply, dict):
        raise GenerationError("Tripo: unexpected reply", "server")
    if reply.get("code", 0) != 0:
        raise GenerationError(
            f"Tripo: error {reply.get('code')}: {reply.get('message', '')}"[:300], "failed"
        )
    data = reply.get("data")
    if not isinstance(data, dict):
        raise GenerationError("Tripo: reply without data", "server")
    return data


def interpret(reply) -> PollState:
    task = _data(reply)
    status = str(task.get("status", ""))
    progress = task.get("progress")
    progress = int(progress) if isinstance(progress, (int, float)) else None
    if status == "success":
        return PollState(status, 100, done=True, payload=task)
    if status in _FAILED:
        message = task.get("error_message") or ""
        code = task.get("error_code")
        error = f"{status} {code if code is not None else ''} {message}".strip()
        return PollState(status, progress, failed=True, error=error)
    # queued, running, or an unknown status: keep polling (bounded by max_wait).
    return PollState(status or "queued", progress)


class TripoProvider(Provider):
    name = "tripo"
    poll_interval = 2.0  # docs: poll every 1-2 s
    max_wait = 1200.0

    def __init__(self, api_key: str, transport=None, region: str = "ai", **kwargs):
        super().__init__(api_key, transport=transport, **kwargs)
        if region not in HOSTS:
            raise GenerationError(f"Unknown Tripo region: {region!r}", "input")
        self.region = region
        self.base = HOSTS[region]

    def upload(self, image, cancel: threading.Event, boundary: str | None = None) -> str:
        if image.mime not in UPLOAD_TYPES:
            raise GenerationError(
                f"Tripo uploads take PNG or JPEG only: {image.name}", "input"
            )
        body, content_type = build_multipart(
            [("file", image.name, image.mime, image.data)], boundary
        )
        reply = self._create(self.base + "/files", body, cancel, content_type)
        token = _data(reply).get("file_token")
        if not token:
            raise GenerationError("Tripo: upload reply without file_token", "server")
        return str(token)

    def generate(
        self,
        request: GenerationRequest,
        cancel: threading.Event,
        progress: ProgressFn,
    ) -> GeneratedModel:
        if request.kind == "text":
            if not request.prompt:
                raise GenerationError("A text prompt is required", "input")
            if len(request.prompt) > PROMPT_LIMIT:
                raise GenerationError(f"Prompt over {PROMPT_LIMIT} characters", "input")
            path, body = "/generation/text-to-model", text_body(request)
        else:
            if request.kind == "multiview" and not 2 <= len(request.images) <= 4:
                raise GenerationError(
                    "Tripo multiview takes 2 to 4 images (front, left, back, right)",
                    "input",
                )
            tokens = []
            for image in request.images:
                progress("upload", image.name, None)
                tokens.append(self.upload(image, cancel))
            if request.kind == "image":
                path, body = "/generation/image-to-model", image_body(tokens[0], request)
            else:
                path, body = "/generation/multiview-to-model", multiview_body(tokens, request)

        task_id = _data(self._create(self.base + path, body, cancel)).get("task_id")
        if not task_id:
            raise GenerationError("Tripo: create reply without task_id", "server")
        progress("generate", "queued", 0)
        task = self._poll(
            f"{self.base}/tasks/{task_id}",
            interpret,
            cancel,
            lambda st: progress("generate", st.status, st.progress),
        )
        url = (task.get("output") or {}).get("model_url")
        if not url:
            raise GenerationError("Tripo: the task returned no model_url", "server")
        progress("download", "downloading", None)
        glb = self._download(url, request.out_dir / "model.glb", cancel)
        meta = {
            "provider": self.name,
            "model": MODEL,
            "region": self.region,
            "task_ids": [str(task_id)],
            "credits_consumed": task.get("credits_consumed"),
        }
        return GeneratedModel(path=glb, files={"glb": glb}, meta=meta)
