import contextlib
import io
import threading
import time
import unittest

from blendercraft_assistant import mainthread


def _pump_until_done(thread: threading.Thread, limit: float = 5.0) -> None:
    deadline = time.monotonic() + limit
    while thread.is_alive() and time.monotonic() < deadline:
        mainthread.pump()
        time.sleep(0.005)


class MainThreadTest(unittest.TestCase):
    def test_run_on_main_from_worker(self):
        out = {}

        def worker():
            out["value"] = mainthread.run_on_main(lambda: 42)

        t = threading.Thread(target=worker)
        t.start()
        _pump_until_done(t)
        t.join(1)
        self.assertFalse(t.is_alive())
        self.assertEqual(out.get("value"), 42)

    def test_runs_on_the_main_thread(self):
        out = {}

        def worker():
            out["thread"] = mainthread.run_on_main(threading.current_thread)

        t = threading.Thread(target=worker)
        t.start()
        _pump_until_done(t)
        t.join(1)
        self.assertIs(out.get("thread"), threading.main_thread())

    def test_direct_call_on_main_thread(self):
        # No pump needed: already on the main thread.
        self.assertEqual(mainthread.run_on_main(lambda: "direct"), "direct")

    def test_exception_propagates(self):
        out = {}

        def boom():
            raise ValueError("boom")

        def worker():
            try:
                mainthread.run_on_main(boom)
            except Exception as exc:  # noqa: BLE001 - recording the type
                out["exc"] = exc

        t = threading.Thread(target=worker)
        t.start()
        _pump_until_done(t)
        t.join(1)
        self.assertIsInstance(out.get("exc"), ValueError)

    def test_timeout(self):
        out = {}

        def worker():
            try:
                mainthread.run_on_main(lambda: 1, timeout=0.1)
            except Exception as exc:  # noqa: BLE001
                out["exc"] = exc

        t = threading.Thread(target=worker)
        t.start()
        t.join(5)
        self.assertIsInstance(out.get("exc"), TimeoutError)
        self.assertIn("not run", str(out["exc"]))
        # The cancelled item is skipped by a late pump.
        self.assertEqual(mainthread.pump(), 1)

    def test_timeout_while_running_still_applies(self):
        effects = []
        out = {}
        release = threading.Event()

        def slow():
            release.wait(5)
            effects.append("done")

        def worker():
            try:
                mainthread.run_on_main(slow, timeout=0.3)
            except Exception as exc:  # noqa: BLE001
                out["exc"] = exc
            finally:
                release.set()

        t = threading.Thread(target=worker)
        t.start()
        deadline = time.monotonic() + 2
        while mainthread._queue.empty() and time.monotonic() < deadline:
            time.sleep(0.001)
        # Starts slow() on this thread; it finishes once the worker timed out.
        self.assertEqual(mainthread.pump(), 1)
        t.join(2)
        self.assertEqual(effects, ["done"])
        self.assertIsInstance(out.get("exc"), TimeoutError)
        self.assertIn("may still have been applied", str(out["exc"]))

    def test_uninstall_timer_fails_pending_calls(self):
        out = {}

        def worker():
            try:
                mainthread.run_on_main(lambda: 1, timeout=30)
            except Exception as exc:  # noqa: BLE001
                out["exc"] = exc

        t = threading.Thread(target=worker)
        t.start()
        deadline = time.monotonic() + 2
        while mainthread._queue.empty() and time.monotonic() < deadline:
            time.sleep(0.001)
        mainthread.uninstall_timer()
        t.join(2)
        self.assertFalse(t.is_alive())
        self.assertIsInstance(out.get("exc"), RuntimeError)
        self.assertIn("queue stopped", str(out["exc"]))

    def test_post_and_pump_count(self):
        seen = []
        for i in range(3):
            mainthread.post(lambda i=i: seen.append(i))
        self.assertEqual(mainthread.pump(max_items=2), 2)
        self.assertEqual(seen, [0, 1])
        self.assertEqual(mainthread.pump(), 1)
        self.assertEqual(seen, [0, 1, 2])
        self.assertEqual(mainthread.pump(), 0)

    def test_post_exception_does_not_stop_pump(self):
        seen = []

        def boom():
            raise RuntimeError("ignored")

        mainthread.post(boom)
        mainthread.post(lambda: seen.append("after"))
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(mainthread.pump(), 2)
        self.assertIn("ignored", err.getvalue())
        self.assertEqual(seen, ["after"])


if __name__ == "__main__":
    unittest.main()
