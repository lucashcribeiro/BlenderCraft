"""Undo steps for AI changes and detection of manual edits."""

import traceback
from contextlib import contextmanager

import bpy


def push_step(label: str) -> None:
    """Push an undo step labelled as coming from the AI."""
    bpy.ops.ed.undo_push(message=f"IA: {label}")


class ChangeTracker:
    """Tracks AI undo steps in the current turn and manual edits after them.

    "Undo last turn" is only safe while the user has not edited the scene
    since the turn began; otherwise ``ed.undo`` would also drop their work.
    """

    def __init__(self) -> None:
        self._ai_steps = 0
        self._manual_edit = False
        self._scope_depth = 0

    def begin_turn(self) -> None:
        self._ai_steps = 0
        self._manual_edit = False

    def record_ai_step(self) -> None:
        self._ai_steps += 1

    def shift_ai_steps(self, delta: int) -> None:
        """The AI moved on the undo stack: -n after undoing n steps, +n after
        redoing. Never below zero."""
        self._ai_steps = max(0, self._ai_steps + delta)

    @contextmanager
    def ai_scope(self):
        """Depsgraph updates inside this scope are not manual edits.

        Interactive Blender evaluates the depsgraph later, from the event
        loop. Flush on exit, while the scope is still active, so the
        handler sees the AI's own changes inside the scope.
        """
        self._scope_depth += 1
        try:
            yield
        finally:
            try:
                bpy.context.view_layer.update()
            except Exception:  # noqa: BLE001 - must not mask the scope's own error
                traceback.print_exc()
            self._scope_depth -= 1

    def on_depsgraph_update(self, scene, depsgraph) -> None:
        if self._scope_depth > 0 or self._manual_edit:
            return
        # Any update counts, selection and Scene-only ones included: they push
        # undo steps too, and ed.undo would revert those instead of AI steps.
        for _update in depsgraph.updates:
            self._manual_edit = True
            return

    @property
    def ai_steps(self) -> int:
        """AI undo steps recorded since the current turn began."""
        return self._ai_steps

    def can_undo_last_turn(self) -> bool:
        return self._ai_steps > 0 and not self._manual_edit

    def undo_last_turn(self) -> int:
        if not self.can_undo_last_turn():
            raise RuntimeError("Cannot undo the last turn")
        count = self._ai_steps
        with self.ai_scope():
            for _ in range(count):
                bpy.ops.ed.undo()
        self._ai_steps = 0
        return count
