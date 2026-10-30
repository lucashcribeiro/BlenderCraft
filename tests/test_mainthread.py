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
        # Drain the stale item so it does not leak into other tests.
        mainthread.pump()

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
