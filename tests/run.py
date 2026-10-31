"""Test runner for BlenderCraft. Runs inside Blender in background mode.

From the repository root:

    /Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup \
        --python-exit-code 1 --python tests/run.py -- [-k PATTERN]

Discovers tests/test_*.py. PATTERN filters test names with the same
semantics as unittest's -k. Exits with status 1 if any test fails.
"""

import argparse
import os
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parent


def _name_patterns() -> list[str] | None:
    # Blender puts its own arguments first; ours come after "--".
    if "--" not in sys.argv:
        return None
    extra = sys.argv[sys.argv.index("--") + 1 :]
    parser = argparse.ArgumentParser(prog="tests/run.py")
    parser.add_argument("-k", dest="pattern", metavar="PATTERN", default=None)
    args = parser.parse_args(extra)
    if not args.pattern:
        return None
    pattern = args.pattern
    if "*" not in pattern:
        pattern = f"*{pattern}*"
    return [pattern]


def main() -> None:
    # The extension's log file must not land in the user's real log folder.
    if not os.environ.get("BLENDERCRAFT_LOG_DIR"):
        os.environ["BLENDERCRAFT_LOG_DIR"] = tempfile.mkdtemp(prefix="blendercraft-logs-")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    loader = unittest.TestLoader()
    loader.testNamePatterns = _name_patterns()
    suite = loader.discover(
        start_dir=str(TESTS_DIR),
        pattern="test_*.py",
        top_level_dir=str(TESTS_DIR),
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


main()
