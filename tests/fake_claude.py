"""Stand-in for the `claude` CLI, used by tests/test_claude_bridge.py.

Usage: fake_claude.py FIXTURE.jsonl

Replays FIXTURE line by line on stdout each time a `user` message arrives
on stdin, and exits when stdin closes. Runs under the same interpreter as
the tests (Blender's bundled python), so it only uses the standard library.
"""

import json
import sys
from pathlib import Path


def main() -> int:
    fixture = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            message = json.loads(raw)
        except ValueError:
            continue
        if message.get("type") != "user":
            continue
        for line in fixture:
            sys.stdout.write(line + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
