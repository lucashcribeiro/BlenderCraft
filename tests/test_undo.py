import unittest

import bpy

from blendercraft_assistant import undo


class _Update:
    def __init__(self, geometry=False, transform=False):
        self.is_updated_geometry = geometry
        self.is_updated_transform = transform


class _Depsgraph:
    def __init__(self, *updates):
        self.updates = list(updates)


def _clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


class UndoTest(unittest.TestCase):
    def setUp(self):
        _clear_scene()
        # Undo needs an initial step to exist: push a baseline.
        bpy.ops.ed.undo_push(message="baseline")
        self.tracker = undo.ChangeTracker()
        # Depsgraph updates are only visible inside the handler callback
        # (empty afterwards), so drive the tracker through a real handler.
        self._handler = self.tracker.on_depsgraph_update
        bpy.app.handlers.depsgraph_update_post.append(self._handler)

    def tearDown(self):
        bpy.app.handlers.depsgraph_update_post.remove(self._handler)

    def _move_and_evaluate(self, obj):
        obj.location.x += 5.0
        bpy.context.view_layer.update()

    def _add_cube_as_ai(self):
        with self.tracker.ai_scope():
            bpy.ops.mesh.primitive_cube_add()
            undo.push_step("cubo")
            self.tracker.record_ai_step()

    def test_undo_last_turn(self):
        self.tracker.begin_turn()
        self._add_cube_as_ai()
        self.assertEqual(len(bpy.data.objects), 1)
        self.assertTrue(self.tracker.can_undo_last_turn())
        self.assertEqual(self.tracker.undo_last_turn(), 1)
        self.assertEqual(len(bpy.data.objects), 0)

    def test_undo_without_steps_is_refused(self):
        self.tracker.begin_turn()
        self.assertFalse(self.tracker.can_undo_last_turn())
        with self.assertRaises(RuntimeError):
            self.tracker.undo_last_turn()

    def test_manual_edit_blocks(self):
        self.tracker.begin_turn()
        self._add_cube_as_ai()
        bpy.context.view_layer.update()  # flush the AI add outside the scope
        self._move_and_evaluate(bpy.data.objects[0])
        self.assertFalse(self.tracker.can_undo_last_turn())
        with self.assertRaises(RuntimeError):
            self.tracker.undo_last_turn()

    def test_updates_inside_ai_scope_are_not_manual(self):
        self.tracker.begin_turn()
        with self.tracker.ai_scope():
            bpy.ops.mesh.primitive_cube_add()
            self._move_and_evaluate(bpy.data.objects[0])
            undo.push_step("cubo")
            self.tracker.record_ai_step()
        self.assertTrue(self.tracker.can_undo_last_turn())

    def test_direct_call_filters_update_kinds(self):
        self.tracker.begin_turn()
        self.tracker.record_ai_step()
        scene = bpy.context.scene
        self.tracker.on_depsgraph_update(scene, _Depsgraph(_Update()))
        self.assertTrue(self.tracker.can_undo_last_turn())
        self.tracker.on_depsgraph_update(scene, _Depsgraph(_Update(geometry=True)))
        self.assertFalse(self.tracker.can_undo_last_turn())

    def test_direct_call_transform_marks_manual(self):
        self.tracker.begin_turn()
        self.tracker.record_ai_step()
        self.tracker.on_depsgraph_update(
            bpy.context.scene, _Depsgraph(_Update(transform=True))
        )
        self.assertFalse(self.tracker.can_undo_last_turn())

    def test_begin_turn_resets(self):
        self.tracker.begin_turn()
        self._add_cube_as_ai()
        self.tracker.on_depsgraph_update(
            bpy.context.scene, _Depsgraph(_Update(transform=True))
        )
        self.assertFalse(self.tracker.can_undo_last_turn())
        self.tracker.begin_turn()
        self.assertFalse(self.tracker.can_undo_last_turn())  # no AI steps yet
        self._add_cube_as_ai()
        self.assertTrue(self.tracker.can_undo_last_turn())

    def test_ai_scope_is_reentrant_and_exits_on_error(self):
        scene = bpy.context.scene
        with self.assertRaises(ValueError):
            with self.tracker.ai_scope():
                with self.tracker.ai_scope():
                    raise ValueError("x")
        self.tracker.begin_turn()
        self.tracker.record_ai_step()
        # Scope fully exited: the update counts as manual.
        self.tracker.on_depsgraph_update(scene, _Depsgraph(_Update(transform=True)))
        self.assertFalse(self.tracker.can_undo_last_turn())


if __name__ == "__main__":
    unittest.main()
