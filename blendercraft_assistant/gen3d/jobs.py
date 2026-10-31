"""Background generation jobs.

A job runs the provider (network and polling) on its own daemon thread, so
neither Blender's main thread nor an MCP request is held for the minutes a
generation takes. Tools wait on a job for a bounded time and report its
state; a Stop cancels every job (checked between polls and downloads).
"""

import secrets
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field

from .base import Cancelled, GenerationError, scrub

MAX_KEPT = 20  # finished jobs remembered for generation_status


@dataclass
class Job:
    id: str
    provider: str
    cancel: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    state: str = "running"  # running | succeeded | failed | cancelled
    stage: str = "queued"
    status: str = ""
    percent: int | None = None
    result: dict | None = None
    error: str = ""
    error_code: str = ""
    started: float = field(default_factory=time.monotonic)
    finished: float | None = None
    secret_values: tuple[str, ...] = ()

    def set_progress(self, stage: str, status: str, percent) -> None:
        self.stage, self.status, self.percent = stage, str(status), percent

    def summary(self) -> dict:
        """State for a tool result (never holds a secret)."""
        end = self.finished if self.finished is not None else time.monotonic()
        data = {
            "job_id": self.id,
            "provider": self.provider,
            "state": self.state,
            "stage": self.stage,
            "provider_status": self.status,
            "progress_percent": self.percent,
            "elapsed_s": round(end - self.started, 1),
        }
        if self.error:
            data["error"] = scrub(self.error, self.secret_values)
            data["error_code"] = self.error_code
        if self.result is not None:
            data["result"] = self.result
        return data


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def start(
        self,
        provider: str,
        run: Callable[[Job], dict],
        on_change: Callable[[], None] | None = None,
        secret_values: tuple[str, ...] = (),
    ) -> Job:
        job = Job(id=f"gen-{secrets.token_hex(4)}", provider=provider,
                  secret_values=tuple(v for v in secret_values if v))
        with self._lock:
            self._prune()
            self._jobs[job.id] = job

        def target() -> None:
            try:
                job.result = run(job)
                job.state = "succeeded"
            except Cancelled:
                job.state = "cancelled"
            except GenerationError as exc:
                job.state, job.error, job.error_code = "failed", str(exc), exc.code
            except BaseException:  # noqa: BLE001 - reported in the job
                job.state, job.error, job.error_code = (
                    "failed", traceback.format_exc(limit=6), "internal"
                )
            finally:
                if job.cancel.is_set() and job.state == "failed":
                    job.state = "cancelled"
                job.finished = time.monotonic()
                job.done.set()
                if on_change is not None:
                    try:
                        on_change()
                    except Exception:  # noqa: BLE001 - a redraw must not break the job
                        traceback.print_exc()

        thread = threading.Thread(target=target, name=f"blendercraft-{job.id}", daemon=True)
        thread.start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def running(self) -> list[Job]:
        with self._lock:
            return [j for j in self._jobs.values() if not j.done.is_set()]

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None:
            return False
        job.cancel.set()
        return True

    def cancel_all(self) -> int:
        jobs = self.running()
        for job in jobs:
            job.cancel.set()
        return len(jobs)

    def _prune(self) -> None:
        finished = [j for j in self._jobs.values() if j.done.is_set()]
        for job in finished[: max(0, len(finished) - MAX_KEPT + 1)]:
            del self._jobs[job.id]


registry = JobRegistry()
