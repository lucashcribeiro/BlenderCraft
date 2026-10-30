"""Queue that runs callables on Blender's main thread.

Worker threads (MCP HTTP handlers, the Claude subprocess reader) never touch
bpy directly: they post callables here and a persistent timer drains the
queue on the main thread. In background mode (-b) timers never fire, so
tests call ``pump()`` by hand.
"""

import queue
import threading
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Callable, TypeVar

T = TypeVar("T")

TIMER_INTERVAL = 0.05

_queue: "queue.SimpleQueue[tuple[Callable[[], object], Future | None]]" = (
    queue.SimpleQueue()
)


def _on_main_thread() -> bool:
    return threading.current_thread() is threading.main_thread()


def run_on_main(fn: Callable[[], T], timeout: float = 120.0) -> T:
    """Run ``fn`` on the main thread and return its result.

    Called from the main thread it runs directly. Otherwise it is queued and
    this call blocks until the main thread has run it. Exceptions raised by
    ``fn`` are re-raised here; ``TimeoutError`` is raised after ``timeout``.
    """
    if _on_main_thread():
        return fn()
    future: Future = Future()
    _queue.put((fn, future))
    try:
        return future.result(timeout=timeout)
    except FutureTimeoutError:
        # Cancel so a late pump skips the work instead of running it unseen.
        future.cancel()
        raise TimeoutError(f"main thread did not run the call within {timeout}s") from None


def post(fn: Callable[[], None]) -> None:
    """Queue ``fn`` for the main thread without waiting for it."""
    _queue.put((fn, None))


def pump(max_items: int = 100) -> int:
    """Run up to ``max_items`` queued items; return how many were run."""
    ran = 0
    while ran < max_items:
        try:
            fn, future = _queue.get_nowait()
        except queue.Empty:
            break
        ran += 1
        if future is None:
            try:
                fn()
            except Exception:  # noqa: BLE001 - one bad post must not stop the queue
                import traceback

                traceback.print_exc()
            continue
        if not future.set_running_or_notify_cancel():
            continue  # caller already timed out
        try:
            future.set_result(fn())
        except BaseException as exc:  # noqa: BLE001 - handed to the waiting thread
            future.set_exception(exc)
    return ran


def _tick() -> float:
    pump()
    return TIMER_INTERVAL


def install_timer() -> None:
    """Register the persistent timer that drains the queue (idempotent)."""
    import bpy

    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=TIMER_INTERVAL, persistent=True)


def uninstall_timer() -> None:
    import bpy

    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
