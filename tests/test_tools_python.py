import contextlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

import bpy

from blendercraft_assistant import tools
from blendercraft_assistant.tools.base import REGISTRY, Tool, register_tool
from blendercraft_assistant.tools.dispatch import call_tool

tools.load_all()


def _clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def _run(code, description="teste"):
    return call_tool("run_python", {"code": code, "description": description})


class RunPythonTest(unittest.TestCase):
    def setUp(self):
        _clear_scene()
        # Undo needs a baseline step in background mode.
        bpy.ops.ed.undo_push(message="baseline")

    def test_registered_with_expected_kinds(self):
        self.assertEqual(REGISTRY["run_python"].kind, "scene")
        self.assertTrue(REGISTRY["run_python"].push_undo)
        for name in ("undo", "redo"):
            self.assertEqual(REGISTRY[name].kind, "scene")
            self.assertFalse(REGISTRY[name].push_undo)
        self.assertEqual(REGISTRY["save"].kind, "file")

    def test_stdout_and_expression(self):
        res = _run("print('a')\n1+1")
        self.assertFalse(res.is_error)
        self.assertIn("a", res.text)
        self.assertIn("2", res.text)

    def test_expression_only_returns_repr(self):
        res = _run("'x' * 3")
        self.assertIn("'xxx'", res.text)

    def test_statement_last_returns_no_value(self):
        res = _run("x = 5")
        self.assertFalse(res.is_error)
        self.assertNotIn("5", res.text)

    def test_namespace_has_helpers(self):
        res = _run(
            "[bpy.__name__, bmesh.__name__, Vector((1, 0, 0)).length,"
            " Matrix.Identity(3)[0][0], math.pi > 3, __name__]"
        )
        self.assertFalse(res.is_error)
        self.assertIn("__bc__", res.text)
        self.assertIn("'bpy'", res.text)

    def test_namespace_is_fresh_per_call(self):
        _run("leaked = 1")
        res = _run("leaked")
        self.assertTrue(res.is_error)
        self.assertIn("NameError", res.text)

    def test_functions_see_their_globals(self):
        # exec with a single namespace: helper functions can call each other.
        res = _run("def f():\n    return math.floor(2.5)\nf()")
        self.assertFalse(res.is_error)
        self.assertIn("2", res.text)

    def test_error_traceback(self):
        res = _run("x = 1\n1/0")
        self.assertTrue(res.is_error)
        self.assertIn("ZeroDivisionError", res.text)
        # The failing line of the user code is shown.
        self.assertIn("<run_python>", res.text)
        self.assertIn("line 2", res.text)
        self.assertIn("1/0", res.text)

    def test_error_keeps_output_printed_before(self):
        res = _run("print('before')\nraise ValueError('boom')")
        self.assertTrue(res.is_error)
        self.assertIn("before", res.text)
        self.assertIn("ValueError", res.text)
        self.assertIn("boom", res.text)

    def test_syntax_error(self):
        res = _run("def (:")
        self.assertTrue(res.is_error)
        self.assertIn("SyntaxError", res.text)
        self.assertIn("<run_python>", res.text)
        self.assertIn("def (:", res.text)
        # Internal frames must not leak to the model.
        self.assertNotIn("python_exec.py", res.text)
        self.assertNotIn("ast.py", res.text)

    def test_runtime_syntax_error_keeps_traceback_and_output(self):
        res = _run("print('before')\ncompile('def (:', 'inner', 'exec')")
        self.assertTrue(res.is_error)
        self.assertIn("before", res.text)
        self.assertIn("File \"<run_python>\", line 2", res.text)

    def test_system_exit_is_error(self):
        for code in ("import sys\nsys.exit(3)", "exit()", "quit()"):
            res = _run(code)
            self.assertTrue(res.is_error, code)
            self.assertIn("SystemExit", res.text)
            self.assertIn("must not exit Blender", res.text)

    def test_system_exit_code_is_reported(self):
        res = _run("import sys\nsys.exit(3)")
        self.assertIn("SystemExit(3)", res.text)

    def test_dispatch_converts_system_exit_from_any_handler(self):
        def _exits(_args):
            raise SystemExit(7)

        register_tool(
            Tool("t_exits", "test", {"type": "object"}, _exits, "read")
        )
        try:
            res = call_tool("t_exits", {})
        finally:
            del REGISTRY["t_exits"]
        self.assertTrue(res.is_error)
        self.assertIn("SystemExit", res.text)

    def test_creates_and_undo(self):
        res = call_tool(
            "run_python",
            {"code": "bpy.ops.mesh.primitive_cube_add()", "description": "cubo"},
        )
        self.assertFalse(res.is_error, res.text)
        self.assertEqual(len(bpy.data.objects), 1)
        res = call_tool("undo", {})
        self.assertFalse(res.is_error, res.text)
        self.assertEqual(len(bpy.data.objects), 0)

    def test_redo_restores_object(self):
        call_tool(
            "run_python",
            {"code": "bpy.ops.mesh.primitive_cube_add()", "description": "cubo"},
        )
        call_tool("undo", {})
        self.assertEqual(len(bpy.data.objects), 0)
        res = call_tool("redo", {})
        self.assertFalse(res.is_error, res.text)
        self.assertEqual(len(bpy.data.objects), 1)

    def test_undo_multiple_steps(self):
        for name in ("A", "B"):
            call_tool(
                "run_python",
                {
                    "code": f"bpy.ops.mesh.primitive_cube_add()\n"
                    f"bpy.context.active_object.name = '{name}'",
                    "description": name,
                },
            )
        self.assertEqual(len(bpy.data.objects), 2)
        res = call_tool("undo", {"steps": 2})
        self.assertFalse(res.is_error, res.text)
        self.assertEqual(len(bpy.data.objects), 0)

    def test_undo_does_not_push_its_own_step(self):
        for name in ("A", "B"):
            call_tool(
                "run_python",
                {
                    "code": "bpy.ops.mesh.primitive_cube_add()",
                    "description": name,
                },
            )
        call_tool("undo", {})
        # If undo had pushed a step, redo would have nothing to restore.
        call_tool("redo", {})
        self.assertEqual(len(bpy.data.objects), 2)

    def test_undo_with_nothing_to_undo_is_error(self):
        # Ask for more steps than the undo stack can hold.
        too_many = bpy.context.preferences.edit.undo_steps + 10
        res = call_tool("undo", {"steps": too_many})
        self.assertTrue(res.is_error)
        self.assertIn("undo", res.text.lower())


@contextlib.contextmanager
def _quiet_fd_stdout():
    """Silence Blender's C-level "Info: Saved ..." lines (test output only)."""
    sys.stdout.flush()
    saved = os.dup(1)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 1)
        yield
    finally:
        os.dup2(saved, 1)
        os.close(saved)
        os.close(devnull)


def _save(args):
    with _quiet_fd_stdout():
        return call_tool("save", args)


class SaveTest(unittest.TestCase):
    def setUp(self):
        # Start from an unsaved file.
        bpy.ops.wm.read_factory_settings(use_empty=True)
        self.assertEqual(bpy.data.filepath, "")

    def test_save_requires_path(self):
        res = _save({})
        self.assertTrue(res.is_error)
        self.assertIn("path", res.text.lower())

    def test_save_with_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "model.blend"
            res = _save({"path": str(target)})
            self.assertFalse(res.is_error, res.text)
            self.assertTrue(target.exists())
            self.assertIn(str(target), res.text)

    def test_save_adds_blend_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = _save({"path": str(Path(tmp) / "model")})
            self.assertFalse(res.is_error, res.text)
            self.assertTrue((Path(tmp) / "model.blend").exists())

    def test_save_without_path_uses_current_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "model.blend"
            _save({"path": str(target)})
            target.unlink()
            res = _save({})
            self.assertFalse(res.is_error, res.text)
            self.assertTrue(target.exists())


if __name__ == "__main__":
    unittest.main()
