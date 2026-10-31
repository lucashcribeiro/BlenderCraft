"""AI 3D generation providers (no network): request bodies against the
official docs, the polling state machine, cancellation and errors."""

import base64
import json
import threading
import unittest
from pathlib import Path
import tempfile

from blendercraft_assistant.gen3d import make_provider
from blendercraft_assistant.gen3d import jobs as gen3d_jobs
from blendercraft_assistant.gen3d import meshy, prompts, tripo
from blendercraft_assistant.gen3d.base import (
    Cancelled,
    GenerationError,
    GenerationRequest,
    ImageInput,
    Response,
    TransportError,
    build_multipart,
    load_image,
    retry_after,
    scrub,
)

KEY = "msy-SECRET-key-123"
PNG = b"\x89PNG\r\n\x1a\nfake"


def reply(data, status=200, headers=None):
    return Response(status, {k.lower(): v for k, v in (headers or {}).items()},
                    json.dumps(data).encode("utf-8"))


class FakeTransport:
    """Scripted replies, matched in order per (method, url)."""

    def __init__(self, script=None, files=None):
        self.script = list(script or [])
        self.calls = []
        self.downloads = []
        self.files = files or {}
        self.on_request = None

    def request(self, method, url, headers, body=None, timeout=30.0):
        self.calls.append(
            {"method": method, "url": url, "headers": dict(headers), "body": body}
        )
        if self.on_request is not None:
            self.on_request(method, url)
        for i, (m, u, resp) in enumerate(self.script):
            if m == method and u == url:
                del self.script[i]
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise AssertionError(f"Unexpected request: {method} {url}")

    def download(self, url, path, cancel, timeout=120.0):
        self.downloads.append(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.files.get(url, b"model-bytes"))
        return len(self.files.get(url, b"model-bytes"))

    def bodies(self, method="POST"):
        out = []
        for c in self.calls:
            if c["method"] == method and c["body"] is not None:
                try:
                    out.append(json.loads(c["body"]))
                except ValueError:
                    out.append(c["body"])
        return out


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.waits = []
        self.cancel_after = None  # cancel the event on the n-th wait

    def clock(self):
        return self.now

    def wait(self, seconds, cancel):
        self.waits.append(seconds)
        self.now += seconds
        if self.cancel_after is not None and len(self.waits) >= self.cancel_after:
            cancel.set()
        return cancel.is_set()


def provider(name, transport, clock, **kw):
    return make_provider(name, KEY, transport=transport, wait=clock.wait,
                         clock=clock.clock, **kw)


def noop(*_args):
    pass


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.clock = FakeClock()

    def tearDown(self):
        self.tmp.cleanup()

    def request(self, **kw):
        kw.setdefault("out_dir", self.dir)
        return GenerationRequest(**kw)


M = meshy.BASE_URL


class MeshyBodiesTest(_Base):
    def test_text_preview_body(self):
        req = self.request(kind="text", prompt="a dog")
        self.assertEqual(
            meshy.preview_body(req),
            {
                "mode": "preview",
                "prompt": "a dog",
                "ai_model": "latest",
                "geometry_resolution": "standard",
                "should_remesh": False,
                "target_formats": ["glb", "stl"],
            },
        )
        req.quality = "high"
        self.assertEqual(meshy.preview_body(req)["geometry_resolution"], "4k")

    def test_refine_body(self):
        self.assertEqual(
            meshy.refine_body("t1"),
            {
                "mode": "refine",
                "preview_task_id": "t1",
                "ai_model": "latest",
                "target_formats": ["glb", "stl"],
            },
        )

    def test_image_body_is_a_data_uri(self):
        img = ImageInput("dog.png", PNG, "image/png")
        req = self.request(kind="image", images=[img], texture=False)
        body = meshy.image_body(req)
        self.assertEqual(
            body["image_url"], "data:image/png;base64," + base64.b64encode(PNG).decode()
        )
        self.assertEqual(
            {k: v for k, v in body.items() if k != "image_url"},
            {
                "ai_model": "latest",
                "geometry_resolution": "standard",
                "should_remesh": False,
                "target_formats": ["glb", "stl"],
                "should_texture": False,
            },
        )

    def test_multi_image_body_caps_high_at_2k(self):
        imgs = [ImageInput(f"{i}.jpg", PNG, "image/jpeg") for i in range(3)]
        req = self.request(kind="multiview", images=imgs, quality="high")
        body = meshy.multi_body(req)
        self.assertEqual(len(body["image_urls"]), 3)
        self.assertTrue(body["image_urls"][0].startswith("data:image/jpeg;base64,"))
        self.assertEqual(body["geometry_resolution"], "2k")
        self.assertTrue(body["should_texture"])

    def test_repair_body(self):
        self.assertEqual(
            meshy.repair_body("t9", 80),
            {"input_task_id": "t9", "printer_type": "fdm", "intended_print_height_mm": 80.0},
        )
        # Out of the documented 1-1000 range: left out.
        self.assertNotIn("intended_print_height_mm", meshy.repair_body("t9", 2000))


def _task(status, progress=0, **extra):
    return {"id": "x", "status": status, "progress": progress, **extra}


class MeshyFlowTest(_Base):
    def test_text_with_texture_runs_preview_then_refine(self):
        urls = {"glb": "https://assets.meshy.ai/r.glb", "stl": "https://assets.meshy.ai/r.stl"}
        ft = FakeTransport(
            [
                ("POST", M + meshy.TEXT_PATH, reply({"result": "p1"})),
                ("GET", f"{M}{meshy.TEXT_PATH}/p1", reply(_task("PENDING"))),
                ("GET", f"{M}{meshy.TEXT_PATH}/p1", reply(_task("IN_PROGRESS", 40))),
                ("GET", f"{M}{meshy.TEXT_PATH}/p1",
                 reply(_task("SUCCEEDED", 100, consumed_credits=20,
                             model_urls={"glb": "https://assets.meshy.ai/p.glb"}))),
                ("POST", M + meshy.TEXT_PATH, reply({"result": "r1"})),
                ("GET", f"{M}{meshy.TEXT_PATH}/r1",
                 reply(_task("SUCCEEDED", 100, consumed_credits=10, model_urls=urls))),
            ]
        )
        seen = []
        result = provider("meshy", ft, self.clock).generate(
            self.request(kind="text", prompt="a dog"),
            threading.Event(),
            lambda stage, status, pct: seen.append((stage, status, pct)),
        )
        bodies = ft.bodies()
        self.assertEqual(bodies[0]["mode"], "preview")
        self.assertEqual(bodies[1], meshy.refine_body("p1"))
        self.assertEqual(ft.downloads, [urls["glb"], urls["stl"]])
        self.assertEqual(result.path, self.dir / "model.glb")
        self.assertEqual(set(result.files), {"glb", "stl"})
        self.assertEqual(result.meta["consumed_credits"], 30)
        self.assertEqual(result.meta["task_ids"], ["p1", "r1"])
        self.assertIn(("generate", "IN_PROGRESS", 40), seen)
        self.assertIn(("texture", "SUCCEEDED", 100), seen)
        # Default poll interval: 5 s between the three preview polls.
        self.assertEqual(self.clock.waits, [5.0, 5.0])
        for call in ft.calls:
            self.assertEqual(call["headers"]["Authorization"], f"Bearer {KEY}")
        self.assertEqual(ft.calls[0]["headers"]["Content-Type"], "application/json")

    def test_untextured_text_skips_refine(self):
        ft = FakeTransport(
            [
                ("POST", M + meshy.TEXT_PATH, reply({"result": "p1"})),
                ("GET", f"{M}{meshy.TEXT_PATH}/p1",
                 reply(_task("SUCCEEDED", 100, model_urls={"stl": "https://a/x.stl"}))),
            ]
        )
        result = provider("meshy", ft, self.clock).generate(
            self.request(kind="text", prompt="a dog", texture=False), threading.Event(), noop
        )
        self.assertEqual(len(ft.bodies()), 1)
        self.assertEqual(result.path, self.dir / "model.stl")

    def test_retry_after_and_transient_errors(self):
        path = f"{M}{meshy.IMAGE_PATH}/i1"
        ft = FakeTransport(
            [
                ("POST", M + meshy.IMAGE_PATH, reply({"result": "i1"})),
                ("GET", path, reply(_task("IN_PROGRESS", 10), headers={"Retry-After": "7"})),
                ("GET", path, reply({"message": "slow down"}, 429, {"Retry-After": "12"})),
                ("GET", path, reply({"message": "oops"}, 503)),
                ("GET", path, TransportError("timed out")),
                ("GET", path, reply(_task("SUCCEEDED", 100, model_urls={"glb": "https://a/m.glb"}))),
            ]
        )
        img = ImageInput("dog.png", PNG, "image/png")
        provider("meshy", ft, self.clock).generate(
            self.request(kind="image", images=[img]), threading.Event(), noop
        )
        self.assertEqual(self.clock.waits, [7.0, 12.0, 5.0, 5.0])

    def test_failed_task_raises_with_message(self):
        ft = FakeTransport(
            [
                ("POST", M + meshy.TEXT_PATH, reply({"result": "p1"})),
                ("GET", f"{M}{meshy.TEXT_PATH}/p1",
                 reply(_task("FAILED", 0, task_error={"message": "bad prompt"}))),
            ]
        )
        with self.assertRaises(GenerationError) as ctx:
            provider("meshy", ft, self.clock).generate(
                self.request(kind="text", prompt="x"), threading.Event(), noop
            )
        self.assertEqual(ctx.exception.code, "failed")
        self.assertIn("bad prompt", str(ctx.exception))

    def test_cancel_between_polls(self):
        path = f"{M}{meshy.TEXT_PATH}/p1"
        ft = FakeTransport(
            [("POST", M + meshy.TEXT_PATH, reply({"result": "p1"}))]
            + [("GET", path, reply(_task("IN_PROGRESS", 5)))] * 5
        )
        self.clock.cancel_after = 2
        with self.assertRaises(Cancelled):
            provider("meshy", ft, self.clock).generate(
                self.request(kind="text", prompt="x"), threading.Event(), noop
            )
        self.assertEqual(sum(1 for c in ft.calls if c["method"] == "GET"), 2)
        self.assertEqual(ft.downloads, [])

    def test_gives_up_after_max_wait(self):
        path = f"{M}{meshy.TEXT_PATH}/p1"
        ft = FakeTransport(
            [("POST", M + meshy.TEXT_PATH, reply({"result": "p1"}))]
            + [("GET", path, reply(_task("IN_PROGRESS", 5)))] * 10
        )
        prov = provider("meshy", ft, self.clock, max_wait=12)
        with self.assertRaises(GenerationError) as ctx:
            prov.generate(self.request(kind="text", prompt="x"), threading.Event(), noop)
        self.assertEqual(ctx.exception.code, "timeout")

    def test_http_errors_are_classified(self):
        for status, code in ((401, "auth"), (402, "credits"), (400, "bad_request")):
            ft = FakeTransport(
                [("POST", M + meshy.TEXT_PATH, reply({"message": "nope"}, status))]
            )
            with self.assertRaises(GenerationError) as ctx:
                provider("meshy", ft, self.clock).generate(
                    self.request(kind="text", prompt="x"), threading.Event(), noop
                )
            self.assertEqual(ctx.exception.code, code)
            self.assertIn("nope", str(ctx.exception))

    def test_create_429_is_retried_but_not_timeouts(self):
        ft = FakeTransport(
            [
                ("POST", M + meshy.TEXT_PATH, reply({}, 429, {"Retry-After": "3"})),
                ("POST", M + meshy.TEXT_PATH, reply({"result": "p1"})),
                ("GET", f"{M}{meshy.TEXT_PATH}/p1",
                 reply(_task("SUCCEEDED", 100, model_urls={"glb": "https://a/m.glb"}))),
            ]
        )
        provider("meshy", ft, self.clock).generate(
            self.request(kind="text", prompt="x", texture=False), threading.Event(), noop
        )
        self.assertEqual(self.clock.waits, [3.0])
        ft = FakeTransport([("POST", M + meshy.TEXT_PATH, TransportError("timed out"))])
        with self.assertRaises(GenerationError) as ctx:
            provider("meshy", ft, self.clock).generate(
                self.request(kind="text", prompt="x"), threading.Event(), noop
            )
        self.assertEqual(ctx.exception.code, "network")
        self.assertEqual(len(ft.calls), 1)  # a create is never repeated blindly

    def test_print_repair_after_generation(self):
        ft = FakeTransport(
            [
                ("POST", M + meshy.IMAGE_PATH, reply({"result": "i1"})),
                ("GET", f"{M}{meshy.IMAGE_PATH}/i1",
                 reply(_task("SUCCEEDED", 100, consumed_credits=30,
                             model_urls={"glb": "https://a/m.glb", "stl": "https://a/m.stl"}))),
                ("POST", M + meshy.REPAIR_PATH, reply({"result": "r1"})),
                ("GET", f"{M}{meshy.REPAIR_PATH}/r1",
                 reply(_task("SUCCEEDED", 100, consumed_credits=10,
                             model_urls={"glb": "https://a/fixed.glb"},
                             print_repair_report={"route": "topology", "applied": True}))),
            ]
        )
        img = ImageInput("dog.png", PNG, "image/png")
        result = provider("meshy", ft, self.clock).generate(
            self.request(kind="image", images=[img], print_repair=True, target_height_mm=60),
            threading.Event(),
            noop,
        )
        self.assertEqual(ft.bodies()[1], meshy.repair_body("i1", 60))
        self.assertEqual(result.path, self.dir / "model_repaired.glb")
        self.assertEqual(result.meta["consumed_credits"], 40)
        self.assertEqual(result.meta["print_repair_report"]["route"], "topology")


T = tripo.HOSTS["ai"]


class TripoTest(_Base):
    def test_text_body(self):
        req = self.request(kind="text", prompt="a dog", negative_prompt="base", quality="high")
        self.assertEqual(
            tripo.text_body(req),
            {
                "prompt": "a dog",
                "negative_prompt": "base",
                "model": "v3.1-20260211",
                "texture": True,
                "pbr": False,
                "geometry_quality": "detailed",
                "texture_quality": "detailed",
            },
        )

    def test_image_and_multiview_bodies(self):
        req = self.request(kind="image", texture=False)
        self.assertEqual(
            tripo.image_body("file_1", req),
            {"input": "file_1", "model": "v3.1-20260211", "texture": False,
             "pbr": False, "geometry_quality": "standard"},
        )
        body = tripo.multiview_body(["f1", "f2", "f3"], req)
        self.assertEqual(body["inputs"], [{"front": "f1"}, {"left": "f2"}, {"back": "f3"}])

    def test_upload_multipart_body(self):
        ft = FakeTransport(
            [("POST", T + "/files", reply({"code": 0, "data": {"file_token": "file_abc"}}))]
        )
        prov = provider("tripo", ft, self.clock)
        token = prov.upload(ImageInput("dog.png", PNG, "image/png"), threading.Event(), "BND")
        self.assertEqual(token, "file_abc")
        call = ft.calls[0]
        self.assertEqual(call["headers"]["Content-Type"], "multipart/form-data; boundary=BND")
        self.assertEqual(
            call["body"],
            b"--BND\r\n"
            b'Content-Disposition: form-data; name="file"; filename="dog.png"\r\n'
            b"Content-Type: image/png\r\n\r\n" + PNG + b"\r\n--BND--\r\n",
        )

    def test_webp_is_refused_for_upload(self):
        prov = provider("tripo", FakeTransport(), self.clock)
        with self.assertRaises(GenerationError):
            prov.upload(ImageInput("a.webp", PNG, "image/webp"), threading.Event())

    def test_region_host(self):
        prov = provider("tripo", FakeTransport(), self.clock, region="com")
        self.assertEqual(prov.base, "https://openapi.tripo3d.com/v3")

    def test_image_flow(self):
        task = f"{T}/tasks/t1"
        ft = FakeTransport(
            [
                ("POST", T + "/files", reply({"code": 0, "data": {"file_token": "file_1"}})),
                ("POST", T + "/generation/image-to-model",
                 reply({"code": 0, "data": {"task_id": "t1"}})),
                ("GET", task, reply({"code": 0, "data": {"status": "queued", "progress": 0}})),
                ("GET", task, reply({"code": 0, "data": {"status": "running", "progress": 50}})),
                ("GET", task, reply({"code": 0, "data": {
                    "status": "success", "progress": 100, "credits_consumed": 30,
                    "output": {"model_url": "https://cdn.tripo3d.ai/o.glb"}}})),
            ]
        )
        result = provider("tripo", ft, self.clock).generate(
            self.request(kind="image", images=[ImageInput("d.png", PNG, "image/png")]),
            threading.Event(),
            noop,
        )
        self.assertEqual(ft.bodies()[-1], tripo.image_body("file_1", self.request(kind="image")))
        self.assertEqual(self.clock.waits, [2.0, 2.0])
        self.assertEqual(result.files, {"glb": self.dir / "model.glb"})
        self.assertEqual(result.meta["credits_consumed"], 30)

    def test_banned_and_api_error(self):
        task = f"{T}/tasks/t1"
        ft = FakeTransport(
            [
                ("POST", T + "/generation/text-to-model",
                 reply({"code": 0, "data": {"task_id": "t1"}})),
                ("GET", task, reply({"code": 0, "data": {"status": "banned"}})),
            ]
        )
        with self.assertRaises(GenerationError):
            provider("tripo", ft, self.clock).generate(
                self.request(kind="text", prompt="x"), threading.Event(), noop
            )
        ft = FakeTransport(
            [("POST", T + "/generation/text-to-model",
              reply({"code": 2010, "message": "insufficient credits"}, 403))]
        )
        with self.assertRaises(GenerationError) as ctx:
            provider("tripo", ft, self.clock).generate(
                self.request(kind="text", prompt="x"), threading.Event(), noop
            )
        self.assertIn("insufficient credits", str(ctx.exception))


class HelpersTest(_Base):
    def test_retry_after(self):
        self.assertEqual(retry_after({"retry-after": "7"}), 7.0)
        self.assertEqual(retry_after({"retry-after": "0"}), 1.0)
        self.assertEqual(retry_after({"retry-after": "999"}), 60.0)
        self.assertIsNone(retry_after({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}))
        self.assertIsNone(retry_after({}))

    def test_multipart_escapes_filename_quotes(self):
        body, ctype = build_multipart([("file", 'a"b.png', "image/png", b"x")], "B")
        self.assertIn(b'filename="a_b.png"', body)
        self.assertEqual(ctype, "multipart/form-data; boundary=B")

    def test_load_image_checks_type_and_existence(self):
        good = self.dir / "a.PNG"
        good.write_bytes(PNG)
        self.assertEqual(load_image(good).mime, "image/png")
        (self.dir / "a.gif").write_bytes(b"GIF")
        for bad in (self.dir / "a.gif", self.dir / "missing.png"):
            with self.assertRaises(GenerationError):
                load_image(bad)

    def test_scrub(self):
        self.assertEqual(scrub(f"key={KEY}!", [KEY, ""]), "key=***!")

    def test_build_prompt_keeps_suffix_within_limit(self):
        text = prompts.build_prompt("a happy corgi", "figurine", 800)
        self.assertTrue(text.startswith("a happy corgi, collectible vinyl toy figurine"))
        long = prompts.build_prompt("dog " * 400, "realistic", 800)
        self.assertLessEqual(len(long), 800)
        self.assertTrue(long.endswith("no base, no text"))
        with self.assertRaises(ValueError):
            prompts.build_prompt("x", "cartoon", 800)

    def test_provider_needs_a_key(self):
        with self.assertRaises(GenerationError) as ctx:
            make_provider("meshy", "")
        self.assertEqual(ctx.exception.code, "auth")


class JobsTest(unittest.TestCase):
    def test_job_states(self):
        reg = gen3d_jobs.JobRegistry()
        ok = reg.start("meshy", lambda job: {"x": 1})
        self.assertTrue(ok.done.wait(5))
        self.assertEqual(ok.summary()["state"], "succeeded")
        self.assertEqual(ok.summary()["result"], {"x": 1})

        def fail(job):
            raise GenerationError(f"bad key {KEY}", "auth")

        bad = reg.start("meshy", fail, secret_values=(KEY,))
        self.assertTrue(bad.done.wait(5))
        summary = bad.summary()
        self.assertEqual((summary["state"], summary["error_code"]), ("failed", "auth"))
        self.assertNotIn(KEY, json.dumps(summary))

        def slow(job):
            job.cancel.wait(5)
            raise Cancelled()

        job = reg.start("tripo", slow)
        self.assertEqual(reg.cancel_all(), 1)
        self.assertTrue(job.done.wait(5))
        self.assertEqual(job.state, "cancelled")
        self.assertEqual(reg.running(), [])


if __name__ == "__main__":
    unittest.main()
