"""run_python, undo, redo and save."""

import ast
import contextlib
import io
import linecache
import math
import os
import traceback

import bmesh
import bpy
import mathutils
from mathutils import Matrix, Vector

from .base import Tool, ToolResult, register_tool

_FILENAME = "<run_python>"


def _namespace() -> dict:
    return {
        "__name__": "__bc__",
        "bpy": bpy,
        "bmesh": bmesh,
        "mathutils": mathutils,
        "Vector": Vector,
        "Matrix": Matrix,
        "math": math,
    }


def _format_error(exc: BaseException) -> str:
    """Traceback limited to the user's code (our own frames are dropped)."""
    tb = exc.__traceback__
    # The first frame is _run_python's own exec call.
    if tb is not None and tb.tb_next is not None:
        tb = tb.tb_next
    return "".join(traceback.format_exception(type(exc), exc, tb)).rstrip()


def _split_last_expression(source: str):
    """Compile source; return (statements code, last-expression code or None)."""
    tree = ast.parse(source, _FILENAME, "exec")
    last = None
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last = ast.Expression(tree.body.pop().value)
    body = compile(tree, _FILENAME, "exec")
    value = compile(last, _FILENAME, "eval") if last is not None else None
    return body, value


def _run_python(args: dict) -> ToolResult:
    source = args["code"]
    # Lets tracebacks show the offending line of the user's code.
    linecache.cache[_FILENAME] = (
        len(source),
        None,
        source.splitlines(keepends=True),
        _FILENAME,
    )
    out = io.StringIO()
    value_repr = None
    try:
        body, value = _split_last_expression(source)
    except SyntaxError as exc:
        # Only the location, offending line and caret: the traceback would
        # show our own compile frames.
        message = "".join(traceback.format_exception_only(type(exc), exc))
        return ToolResult(text=message.rstrip(), is_error=True)
    try:
        namespace = _namespace()
        with contextlib.redirect_stdout(out):
            exec(body, namespace)  # noqa: S102 - unrestricted by design (spec)
            if value is not None:
                result = eval(value, namespace)  # noqa: S307
                if result is not None:
                    value_repr = repr(result)
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - reported as an error
        # SystemExit (sys.exit(), exit(), quit()) is not an Exception: left
        # alone it would unwind through the dispatcher and quit Blender.
        text = out.getvalue()
        if text and not text.endswith("\n"):
            text += "\n"
        error = _format_error(exc)
        if isinstance(exc, SystemExit):
            error += (
                f"\nSystemExit({exc.code!r}): run_python code must not exit Blender"
            )
        return ToolResult(text=f"{text}{error}", is_error=True)

    parts = []
    if out.getvalue():
        parts.append(out.getvalue().rstrip("\n"))
    if value_repr is not None:
        parts.append(f"=> {value_repr}")
    return ToolResult(text="\n".join(parts) or "OK (no output)")


def _steps(args: dict) -> int:
    steps = int(args.get("steps", 1))
    if steps < 1:
        raise ValueError("steps must be at least 1")
    return steps


def _repeat(op, label: str, steps: int, sign: int) -> ToolResult:
    done = 0
    try:
        for _ in range(steps):
            op()
            done += 1
    except RuntimeError as exc:
        return ToolResult(
            text=f"Nothing more to {label} after {done} of {steps} steps: {exc}",
            is_error=True,
            undo_delta=sign * done,
        )
    return ToolResult(text=f"{label} done: {done} step(s)", undo_delta=sign * done)


def _undo(args: dict) -> ToolResult:
    return _repeat(bpy.ops.ed.undo, "undo", _steps(args), -1)


def _redo(args: dict) -> ToolResult:
    return _repeat(bpy.ops.ed.redo, "redo", _steps(args), 1)


def _save(args: dict) -> ToolResult:
    path = args.get("path")
    if not path:
        if not bpy.data.filepath:
            return ToolResult(
                text="The file has no name yet: pass 'path' to save it.",
                is_error=True,
            )
        bpy.ops.wm.save_mainfile()
        return ToolResult(text=f"Saved {bpy.data.filepath}")
    path = os.path.abspath(os.path.expanduser(path))
    if not path.lower().endswith(".blend"):
        path += ".blend"
    bpy.ops.wm.save_as_mainfile(filepath=path)
    return ToolResult(text=f"Saved {path}")


_STEPS_SCHEMA = {
    "type": "object",
    "properties": {"steps": {"type": "integer", "minimum": 1, "default": 1}},
}

register_tool(
    Tool(
        name="run_python",
        description=(
            "Run Python in Blender's main thread with bpy, bmesh, mathutils, "
            "Vector, Matrix and math already available (a fresh namespace on "
            "every call). Returns what the code prints plus the repr of a "
            "final expression, or the traceback on error. This is the main "
            "modelling tool; each call is one undo step. Units are mm "
            "(1 BU = 1 mm)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "description": {
                    "type": "string",
                    "description": "Short label for the undo step.",
                },
            },
            "required": ["code", "description"],
        },
        handler=_run_python,
        kind="scene",
    )
)

register_tool(
    Tool(
        name="undo",
        description=(
            "Undo the last steps (default 1) in the scene. Only the steps of "
            "the current request can be undone; earlier changes are the "
            "user's to undo."
        ),
        input_schema=_STEPS_SCHEMA,
        handler=_undo,
        kind="scene",
        push_undo=False,
    )
)

register_tool(
    Tool(
        name="redo",
        description="Redo steps (default 1) that were undone.",
        input_schema=_STEPS_SCHEMA,
        handler=_redo,
        kind="scene",
        push_undo=False,
    )
)

register_tool(
    Tool(
        name="save",
        description=(
            "Save the .blend file. Without path, saves to the current file; "
            "an unsaved file needs a path."
        ),
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
        },
        handler=_save,
        kind="file",
    )
)
