"""Undo steps for AI changes and detection of manual edits."""

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

    @contextmanager
    def ai_scope(self):
        """Depsgraph updates inside this scope are not manual edits."""
        self._scope_depth += 1
        try:
            yield
        finally:
            self._scope_depth -= 1

    def on_depsgraph_update(self, scene, depsgraph) -> None:
        if self._scope_depth > 0 or self._manual_edit:
            return
        for update in depsgraph.updates:
            if update.is_updated_geometry or update.is_updated_transform:
                self._manual_edit = True
                return

    def can_undo_last_turn(self) -> bool:
        return self._ai_steps > 0 and not self._manual_edit

    def undo_last_turn(self) -> int:
        if not self.can_undo_last_turn():
            raise RuntimeError("Cannot undo the last turn")
        count = self._ai_steps
        for _ in range(count):
            bpy.ops.ed.undo()
        self._ai_steps = 0
        return count
