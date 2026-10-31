import contextlib
import ctypes
import os
import sys
import unittest
from unittest import mock

import bpy

from blendercraft_assistant import i18n
from blendercraft_assistant.ui.text_wrap import wrap

REQUIRED_KEYS = (
    "assistant idle working error new_chat conversations stop send send_now "
    "queued attach_view attach_selection mode_auto mode_ask mode_readonly "
    "changes undo_ai undo_ai_blocked details show_older approve deny "
    "approval_request claude_missing claude_login step_limit unit joint "
    "joint_reset copy_mcp scale_warning summary_header"
).split()


class I18nTest(unittest.TestCase):
    def test_strings_complete(self):
        self.assertEqual(set(i18n.STRINGS), {"pt", "en"})
        self.assertEqual(set(i18n.STRINGS["pt"]), set(i18n.STRINGS["en"]))
        for key in REQUIRED_KEYS:
            self.assertIn(key, i18n.STRINGS["en"])
        for lang, table in i18n.STRINGS.items():
            for key, value in table.items():
                self.assertTrue(value.strip(), f"{lang}.{key} is empty")

    def test_lang_follows_locale(self):
        with mock.patch.object(i18n, "_locale", return_value="pt_BR"):
            self.assertEqual(i18n.lang(), "pt")
            self.assertEqual(i18n.t("stop"), i18n.STRINGS["pt"]["stop"])
        for locale in ("en_US", "fr_FR", ""):
            with mock.patch.object(i18n, "_locale", return_value=locale):
                self.assertEqual(i18n.lang(), "en")

    def test_t_formats_only_when_asked(self):
        with mock.patch.object(i18n, "_locale", return_value="en_US"):
            self.assertIn("3", i18n.t("changes", n=3))
            # Session notes are formatted later by AgentSession.
            self.assertIn("{n}", i18n.t("queue_dropped"))
            self.assertEqual(i18n.t("no_such_key"), "no_such_key")


LOREM = (
    "Criei um cubo de 20 mm centrado na origem e apliquei um chanfro de "
    "1 mm em todas as arestas para facilitar a impressão sem suportes."
)


class TextWrapTest(unittest.TestCase):
    def test_wrap_width(self):
        lines = wrap(LOREM * 3, 280, 1)
        self.assertGreater(len(lines), 3)
        for line in lines:
            self.assertLessEqual(len(line), 40)

    def test_wrap_scales_with_ui_scale(self):
        self.assertLessEqual(max(len(l) for l in wrap(LOREM, 280, 2)), 20)
        # Background mode reports ui_scale 0; it must not divide by zero.
        self.assertLessEqual(max(len(l) for l in wrap(LOREM, 280, 0)), 40)

    def test_wrap_minimum_width(self):
        lines = wrap(LOREM, 10, 1)
        self.assertTrue(all(len(l) <= 10 for l in lines))
        self.assertTrue(any(len(l) > 5 for l in lines))

    def test_wrap_keeps_paragraphs(self):
        lines = wrap("Primeiro parágrafo.\n\nSegundo.\nTerceira linha.", 280, 1)
        self.assertEqual(
            lines, ["Primeiro parágrafo.", "", "Segundo.", "Terceira linha."]
        )

    def test_wrap_breaks_long_words(self):
        lines = wrap("x" * 100, 280, 1)
        self.assertEqual(["x" * 40, "x" * 40, "x" * 20], lines)

    def test_wrap_empty(self):
        self.assertEqual(wrap("", 280, 1), [])


class PropsTest(unittest.TestCase):
    def setUp(self):
        from blendercraft_assistant import runtime
        from blendercraft_assistant.ui import prefs, props

        self.runtime = runtime
        props.register()
        self.addCleanup(props.unregister)
        prefs.register()
        self.addCleanup(prefs.unregister)
        runtime.mode = "AUTO"
        runtime.model = ""
        self.addCleanup(setattr, runtime, "mode", "AUTO")
        self.addCleanup(setattr, runtime, "model", "")
        self.bc = bpy.context.window_manager.bc

    def test_prefs_id_is_the_package(self):
        from blendercraft_assistant.ui import prefs

        self.assertEqual(prefs.BC_AddonPreferences.bl_idname, "blendercraft_assistant")
        self.assertEqual(prefs.ADDON_ID, self.runtime.ADDON_ID)

    def test_defaults(self):
        self.assertEqual(self.bc.mode, "AUTO")
        self.assertEqual(self.bc.model, "DEFAULT")
        self.assertFalse(self.bc.attach_view)
        self.assertFalse(self.bc.attach_selection)
        self.assertEqual(self.bc.show_older, 0)

    def test_mode_and_model_reach_runtime(self):
        with mock.patch.object(self.runtime, "stop_session") as stop_session:
            self.bc.mode = "READONLY"
            self.assertEqual(self.runtime.mode, "READONLY")
            self.bc.model = "sonnet"
            self.assertEqual(self.runtime.model, "sonnet")
            self.bc.model = "DEFAULT"
            self.assertEqual(self.runtime.model, "")
        self.assertEqual(stop_session.call_count, 3)

    def test_load_post_restores_props_from_runtime(self):
        from blendercraft_assistant.ui import props

        self.runtime.mode = "ASK"
        self.runtime.model = "haiku"
        props._sync_after_load()
        self.assertEqual(self.bc.mode, "ASK")
        self.assertEqual(self.bc.model, "haiku")

    def test_joint_angle_turns_pivot(self):
        from blendercraft_assistant.tools.joints import create_pivot

        bpy.ops.mesh.primitive_cube_add(size=10)
        cube = bpy.context.active_object
        pivot = create_pivot(cube.name, (0.0, 0.0, 0.0), "Z")
        self.addCleanup(bpy.data.objects.remove, bpy.data.objects[pivot], do_unlink=True)
        self.addCleanup(bpy.data.objects.remove, cube, do_unlink=True)
        self.bc.joint_pivot = pivot
        self.bc.joint_angle = 30.0
        self.assertAlmostEqual(bpy.data.objects[pivot]["bc_angle"], 30.0)
        self.bc.joint_pivot = ""
        self.bc.joint_angle = 0.0  # no pivot: nothing happens
        bpy.data.objects[pivot]["bc_angle"] = 45.0
        self.bc.joint_pivot = pivot  # picking a pivot shows its angle
        self.assertAlmostEqual(self.bc.joint_angle, 45.0)


    def test_picking_a_pivot_never_turns_it(self):
        from blendercraft_assistant.tools.joints import create_pivot

        bpy.ops.mesh.primitive_cube_add(size=10)
        cube = bpy.context.active_object
        pivot = create_pivot(cube.name, (0.0, 0.0, 0.0), "Z")
        obj = bpy.data.objects[pivot]
        self.addCleanup(bpy.data.objects.remove, obj, do_unlink=True)
        self.addCleanup(bpy.data.objects.remove, cube, do_unlink=True)
        # 30.3 has no exact float32 form: the slider shows 30.2999992...
        obj["bc_angle"] = 30.3
        rotation = tuple(obj.rotation_euler)
        self.bc.joint_pivot = pivot
        self.assertAlmostEqual(self.bc.joint_angle, 30.3, places=4)
        self.assertEqual(tuple(obj.rotation_euler), rotation)
        self.assertEqual(obj["bc_angle"], 30.3)

class FakeSession:
    def __init__(self, queue=()):
        self.queue = list(queue)
        self.sent = []
        self.sent_now = []

    def send(self, text, attachments=()):
        self.sent.append((text, list(attachments)))

    def send_now(self, text, attachments=()):
        self.sent_now.append((text, list(attachments)))


@contextlib.contextmanager
def _quiet_reports():
    """Silence operator reports ("Warning: ...") in test output only.

    Blender writes them with C stdio, buffered when output is a pipe, so C
    buffers are flushed while the descriptors still point at /dev/null.
    """
    libc = ctypes.CDLL(None)
    sys.stdout.flush()
    sys.stderr.flush()
    libc.fflush(None)  # earlier output still goes where it belongs
    saved = [os.dup(1), os.dup(2)]
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
        yield
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        libc.fflush(None)
        os.dup2(saved[0], 1)
        os.dup2(saved[1], 2)
        for fd in (*saved, devnull):
            os.close(fd)


class OperatorsTest(unittest.TestCase):
    def setUp(self):
        quiet = _quiet_reports()
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)
        from blendercraft_assistant import runtime
        from blendercraft_assistant.ui import operators, props

        self.runtime = runtime
        self.operators = operators
        props.register()
        self.addCleanup(props.unregister)
        operators.register()
        self.addCleanup(operators.unregister)
        self.bc = bpy.context.window_manager.bc
        self.session = FakeSession()
        for name, value in (
            ("ensure_session", mock.Mock(return_value=self.session)),
            ("server", mock.Mock()),  # the runtime is up
            ("store", mock.Mock()),
        ):
            patcher = mock.patch.object(runtime, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # WindowManager values outlive re-registration: start from an empty field.
        self.bc.input = ""
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)

    def add_cube(self, name="Peca", size=10.0):
        bpy.ops.mesh.primitive_cube_add(size=size)
        cube = bpy.context.active_object
        cube.name = name
        self.addCleanup(lambda: bpy.data.objects.get(name) and bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True))
        return cube

    def test_committing_the_field_does_not_send(self):
        # Blender commits the field on Enter and on clicking elsewhere alike;
        # only the Send button sends.
        self.bc.input = "  crie um cubo  "
        self.assertEqual(self.session.sent, [])
        self.assertEqual(self.bc.input, "  crie um cubo  ")
        self.assertEqual(bpy.ops.bc.send(), {"FINISHED"})
        self.assertEqual(self.session.sent, [("crie um cubo", [])])
        self.assertEqual(self.bc.input, "")
        self.bc.input = "   "
        self.assertEqual(bpy.ops.bc.send(), {"CANCELLED"})
        self.assertEqual(len(self.session.sent), 1)

    def test_send_before_runtime_is_up_keeps_text(self):
        with mock.patch.object(self.runtime, "server", None):
            self.bc.input = "crie um cubo"
            self.assertEqual(bpy.ops.bc.send(), {"CANCELLED"})
            self.session.queue[:] = [("um", [])]
            self.assertEqual(bpy.ops.bc.send_now(), {"CANCELLED"})
        self.assertEqual(self.bc.input, "crie um cubo")
        self.assertEqual(self.session.sent + self.session.sent_now, [])
        self.assertEqual(self.session.queue, [("um", [])])
        self.runtime.ensure_session.assert_not_called()

    def test_send_now_promotes_last_queued(self):
        self.session.queue[:] = [("um", []), ("dois", [{"type": "text", "text": "x"}])]
        self.assertEqual(bpy.ops.bc.send_now(), {"FINISHED"})
        self.assertEqual(self.session.sent_now, [("dois", [{"type": "text", "text": "x"}])])
        self.assertEqual(self.session.queue, [("um", [])])

    def test_send_without_text_does_nothing(self):
        self.assertEqual(bpy.ops.bc.send(), {"CANCELLED"})
        self.assertEqual(bpy.ops.bc.send_now(), {"CANCELLED"})
        self.assertEqual(self.session.sent + self.session.sent_now, [])

    def test_attach_selection(self):
        self.add_cube("Peca")
        self.bc.attach_selection = True
        blocks, warnings = self.operators.build_attachments(bpy.context)
        self.assertEqual(warnings, [])
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["type"], "text")
        self.assertIn('"name": "Peca"', blocks[0]["text"])
        self.assertIn('"vertices": 8', blocks[0]["text"])
        self.assertFalse(self.bc.attach_selection)  # only for the next message

    def test_attach_view(self):
        import base64

        self.add_cube("Peca")
        self.bc.attach_view = True
        blocks, warnings = self.operators.build_attachments(bpy.context)
        self.assertEqual(warnings, [])
        (block,) = blocks
        self.assertEqual(block["type"], "image")
        self.assertEqual(block["source"]["type"], "base64")
        self.assertEqual(block["source"]["media_type"], "image/png")
        self.assertTrue(base64.b64decode(block["source"]["data"]).startswith(b"\x89PNG"))
        self.assertFalse(self.bc.attach_view)

    def test_attach_view_is_not_a_manual_edit(self):
        from blendercraft_assistant.undo import ChangeTracker

        self.add_cube("Peca")
        tracker = ChangeTracker()
        patcher = mock.patch.object(self.runtime, "tracker", tracker)
        patcher.start()
        self.addCleanup(patcher.stop)
        handlers = bpy.app.handlers.depsgraph_update_post
        handlers.append(self.runtime._on_depsgraph_update)
        self.addCleanup(handlers.remove, self.runtime._on_depsgraph_update)
        tracker.begin_turn()
        tracker.record_ai_step()  # the agent is mid-turn; the user queues a message
        self.bc.attach_view = True
        blocks, _warnings = self.operators.build_attachments(bpy.context)
        self.assertEqual(len(blocks), 1)
        # The screenshot (a temporary camera in background mode) is not the user's edit.
        self.assertTrue(tracker.can_undo_last_turn())

    def test_attach_view_failure_is_a_warning(self):
        self.bc.attach_view = True  # empty scene: nothing to render
        blocks, warnings = self.operators.build_attachments(bpy.context)
        self.assertEqual(blocks, [])
        self.assertEqual(len(warnings), 1)

    def test_submit_sends_attachments(self):
        self.add_cube("Peca")
        self.bc.attach_selection = True
        self.bc.input = "o que tem aqui?"
        bpy.ops.bc.send()
        ((text, attachments),) = self.session.sent
        self.assertEqual(text, "o que tem aqui?")
        self.assertIn("Peca", attachments[0]["text"])

    def test_undo_ai_changes(self):
        from blendercraft_assistant.undo import ChangeTracker, push_step

        bpy.ops.ed.undo_push(message="baseline")
        tracker = ChangeTracker()
        with mock.patch.object(self.runtime, "tracker", tracker):
            # Nothing of the AI to undo: the button is disabled.
            self.assertFalse(bpy.ops.bc.undo_ai_changes.poll())
            tracker.begin_turn()
            self.add_cube("DaIA")
            push_step("cubo")
            tracker.record_ai_step()
            self.assertEqual(bpy.ops.bc.undo_ai_changes(), {"FINISHED"})
        self.assertNotIn("DaIA", bpy.data.objects)

    def test_undo_ai_changes_after_manual_edit(self):
        from blendercraft_assistant.undo import ChangeTracker

        class _Depsgraph:
            updates = [object()]

        tracker = ChangeTracker()
        tracker.begin_turn()
        tracker.record_ai_step()
        tracker.on_depsgraph_update(bpy.context.scene, _Depsgraph())  # the user's edit
        with mock.patch.object(self.runtime, "tracker", tracker):
            self.assertTrue(bpy.ops.bc.undo_ai_changes.poll())
            self.assertEqual(bpy.ops.bc.undo_ai_changes(), {"CANCELLED"})
        self.assertEqual(tracker.ai_steps, 1)  # nothing was undone

    def test_set_unit(self):
        from blendercraft_assistant.units import get_display_unit, set_display_unit

        scene = bpy.context.scene
        self.addCleanup(set_display_unit, scene, get_display_unit(scene))
        self.assertEqual(bpy.ops.bc.set_unit(unit="IN"), {"FINISHED"})
        self.assertEqual(get_display_unit(scene), "IN")

    def test_joint_operators(self):
        cube = self.add_cube("Tampa", size=10.0)
        bpy.context.scene.cursor.location = (5.0, 0.0, 0.0)
        self.addCleanup(setattr, bpy.context.scene.cursor, "location", (0.0, 0.0, 0.0))
        self.assertEqual(bpy.ops.bc.joint_create(), {"FINISHED"})
        pivot = bpy.data.objects[self.bc.joint_pivot]
        self.addCleanup(bpy.data.objects.remove, pivot, do_unlink=True)
        self.assertIs(cube.parent, pivot)
        self.assertEqual(pivot["bc_axis"], "Z")
        self.assertEqual(tuple(pivot.location), (5.0, 0.0, 0.0))
        # The part (or its pivot) active again picks the same pivot.
        self.bc.joint_pivot = ""
        self.assertEqual(bpy.ops.bc.joint_create(), {"FINISHED"})
        self.assertEqual(self.bc.joint_pivot, pivot.name)
        self.bc.joint_angle = 40.0
        self.assertEqual(bpy.ops.bc.joint_test(), {"FINISHED"})
        self.assertIn("180", self.operators.joint_result)
        self.assertAlmostEqual(pivot["bc_angle"], 40.0)  # restored by joint_test
        self.assertEqual(bpy.ops.bc.joint_reset(), {"FINISHED"})
        self.assertEqual(pivot["bc_angle"], 0.0)
        self.assertEqual(self.bc.joint_angle, 0.0)

    def test_joint_create_needs_active_object(self):
        bpy.context.view_layer.objects.active = None
        self.assertEqual(bpy.ops.bc.joint_create(), {"CANCELLED"})

    def test_toggle_details(self):
        bpy.ops.bc.toggle_details(tool_id="tu1")
        self.assertEqual(self.bc.expanded_tool, "tu1")
        bpy.ops.bc.toggle_details(tool_id="tu2")
        self.assertEqual(self.bc.expanded_tool, "tu2")
        bpy.ops.bc.toggle_details(tool_id="tu2")
        self.assertEqual(self.bc.expanded_tool, "")

    def test_approve(self):
        with mock.patch.object(self.runtime, "answer_approval") as answer:
            bpy.ops.bc.approve(allow=True)
            bpy.ops.bc.approve(allow=False)
        self.assertEqual([c.args for c in answer.call_args_list], [(True,), (False,)])

    def test_conversation_operators(self):
        with mock.patch.object(self.runtime, "open_conversation") as open_conv, \
                mock.patch.object(self.runtime, "delete_conversation") as delete_conv:
            self.assertEqual(bpy.ops.bc.open_conversation(conv_id="abc"), {"FINISHED"})
            self.assertEqual(bpy.ops.bc.delete_conversation(conv_id="abc"), {"FINISHED"})
            open_conv.side_effect = ValueError("invalid conversation id")
            # An ERROR report surfaces as RuntimeError when called from Python.
            with self.assertRaisesRegex(RuntimeError, "invalid conversation id"):
                bpy.ops.bc.open_conversation(conv_id="../x")
        open_conv.assert_called_with("../x")
        delete_conv.assert_called_once_with("abc")
        # Deleting from the panel asks first.
        self.assertTrue(hasattr(self.operators.BC_OT_delete_conversation, "invoke"))

    def test_unit_labels_defined_once(self):
        from blendercraft_assistant import units
        from blendercraft_assistant.ui import panel

        for module in (panel, self.operators):
            self.assertFalse(hasattr(module, "_UNIT_LABELS"), module.__name__)
        items = bpy.ops.bc.set_unit.get_rna_type().properties["unit"].enum_items
        self.assertEqual({i.identifier: i.name for i in items}, units.UNIT_LABELS)

    def test_copy_mcp_command_needs_server(self):
        with mock.patch.object(self.runtime, "server", None):
            self.assertEqual(bpy.ops.bc.copy_mcp_command(), {"CANCELLED"})


ICONS = set(bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items.keys())


class FakeOperatorProps:
    def __init__(self, idname):
        module, name = idname.split(".")
        rna = getattr(getattr(bpy.ops, module), name).get_rna_type()
        object.__setattr__(self, "_names", set(rna.properties.keys()))

    def __setattr__(self, name, value):
        if name not in self._names:
            raise AttributeError(f"operator has no property {name!r}")
        object.__setattr__(self, name, value)


class FakeLayout:
    """Records what a panel draws and checks icons, properties and operators."""

    def __init__(self, log):
        self.log = log
        self.alert = False
        self.enabled = True
        self.active = True
        self.scale_y = 1.0
        self.use_property_split = False

    def _icon(self, icon):
        assert icon in ICONS, f"unknown icon {icon!r}"

    def _child(self, *args, **kwargs):
        return FakeLayout(self.log)

    row = column = box = split = _child

    def label(self, text="", icon="NONE", **kwargs):
        self._icon(icon)
        self.log.append(("label", text))

    def separator(self, **kwargs):
        pass

    def prop(self, data, prop, text=None, icon="NONE", **kwargs):
        self._icon(icon)
        assert prop in data.bl_rna.properties, prop
        self.log.append(("prop", prop))

    def prop_search(self, data, prop, search_data, search_prop, **kwargs):
        assert prop in data.bl_rna.properties, prop
        self.log.append(("prop", prop))

    def operator(self, idname, text="", icon="NONE", **kwargs):
        self._icon(icon)
        self.log.append(("operator", idname, text))
        return FakeOperatorProps(idname)

    def operator_menu_enum(self, idname, prop, text="", icon="NONE", **kwargs):
        self._icon(icon)
        props = FakeOperatorProps(idname)
        assert prop in props._names, prop
        self.log.append(("operator", idname, text))

    def popover(self, panel, text="", icon="NONE", **kwargs):
        self._icon(icon)
        assert hasattr(bpy.types, panel), panel
        self.log.append(("popover", panel))

    def template_list(self, listtype, list_id, dataptr, propname, active_dataptr, active_propname, rows=5, maxrows=5, **kwargs):
        assert hasattr(bpy.types, listtype), listtype
        assert propname in dataptr.bl_rna.properties, propname
        assert active_propname in active_dataptr.bl_rna.properties, active_propname
        self.log.append(("list", listtype, rows, maxrows))
        # Draw every row the way Blender would, through the UIList.
        ui_list = getattr(bpy.types, listtype)
        for index, item in enumerate(getattr(dataptr, propname)):
            ui_list.draw_item(ui_list, None, self, dataptr, item, 0, active_dataptr, active_propname, index)


class PanelDrawTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        from blendercraft_assistant import runtime
        from blendercraft_assistant.claude.session import AgentSession
        from blendercraft_assistant.conversations import (
            ConversationStore,
            Message,
            ToolCall,
        )
        from blendercraft_assistant.ui import operators, panel, prefs, props

        self.runtime = runtime
        self.panel = panel
        self.operators = operators
        for module in (props, prefs, operators, panel):
            module.register()
            self.addCleanup(module.unregister)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = ConversationStore(Path(tmp.name))
        conv = store.new("", "")
        conv.messages = [
            Message("user", "crie um cubo de 20 mm"),
            Message(
                "assistant",
                "## Pronto\nCriei o **cubo** com [bevel](https://x).",
                [
                    ToolCall("tu1", "run_python", {"code": "x", "description": "cubo"}, "ok", "ok"),
                    ToolCall("tu2", "screenshot", {"view": "iso"}, "", "running"),
                    ToolCall("tu3", "measure", {}, "falhou", "error"),
                ],
            ),
        ] * 5
        self.session = AgentSession(conv, store, lambda *a: None, lambda n: "read")
        for name, value in (
            ("store", store),
            ("session", self.session),
            ("pending_approval", None),
            ("claude_problem", ""),
        ):
            patcher = mock.patch.object(runtime, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.bc = bpy.context.window_manager.bc

    def draw(self, panel_cls):
        log = []
        fake = mock.Mock(layout=FakeLayout(log))
        context = mock.Mock(wraps=bpy.context)
        context.window_manager = bpy.context.window_manager
        context.scene = bpy.context.scene
        context.preferences = bpy.context.preferences
        context.region = mock.Mock(width=300, height=800)
        context.selected_objects = bpy.context.selected_objects
        context.active_object = bpy.context.active_object
        panel_cls.draw(fake, context)
        return log

    def labels(self, log):
        return [entry[1] for entry in log if entry[0] == "label"]

    def operators_drawn(self, log):
        return [entry[1] for entry in log if entry[0] == "operator"]

    def test_category_is_translated_at_register(self):
        self.assertEqual(
            self.panel.VIEW3D_PT_bc_assistant.bl_category, i18n.t("assistant")
        )
        self.assertTrue(hasattr(bpy.types, "VIEW3D_PT_bc_assistant"))

    def test_idle_panel_elements_in_order(self):
        log = self.draw(self.panel.VIEW3D_PT_bc_assistant)
        kinds = [(e[0], e[1]) for e in log]
        order = [
            ("label", i18n.t("idle")),
            ("operator", "bc.new_chat"),
            ("popover", "VIEW3D_PT_bc_conversations"),
            ("label", i18n.t("provider")),
            ("prop", "model"),
            ("list", "BC_UL_chat"),
            ("label", i18n.t("you")),
            ("label", i18n.t("changes", n=0)),
            ("operator", "bc.undo_ai_changes"),
            ("prop", "input"),
            ("prop", "mode"),
            ("operator", "bc.paste_draft"),
            ("operator", "bc.edit_draft"),
            ("operator", "bc.attach_file"),
            ("operator", "bc.send"),
            ("prop", "attach_view"),
            ("prop", "attach_selection"),
            ("operator", "bc.pick_context"),
        ]
        positions = [kinds.index(item) for item in order]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("bc.stop", self.operators_drawn(log))
        self.assertNotIn("bc.send_now", self.operators_drawn(log))
        # The list has a fixed height that fits the region.
        (entry,) = [e for e in log if e[0] == "list"]
        self.assertEqual(entry[2], entry[3])
        scale = bpy.context.preferences.system.ui_scale or 1.0
        # The 300 px test region is narrow: the attach row takes a second line.
        self.assertEqual(entry[2], self.panel.chat_list.visible_rows(800, scale, 1))
        # A draft preview takes room from the list.
        self.bc.draft = "a\nb"
        self.addCleanup(setattr, self.bc, "draft", "")
        (entry,) = [e for e in self.draw(self.panel.VIEW3D_PT_bc_assistant) if e[0] == "list"]
        self.assertLess(entry[2], self.panel.chat_list.visible_rows(800, scale))
        labels = self.labels(log)
        self.assertIn("Pronto", labels)
        self.assertIn("Criei o cubo com bevel.", labels)
        self.assertFalse(any("**" in label for label in labels))
        # Secondary tools live in subpanels.
        self.assertNotIn("bc.copy_mcp_command", self.operators_drawn(log))
        self.assertNotIn("bc.set_unit", self.operators_drawn(log))

    def test_show_older_for_long_conversations(self):
        from blendercraft_assistant.conversations import Message

        self.assertNotIn("wm.context_set_int", self.operators_drawn(self.draw(self.panel.VIEW3D_PT_bc_assistant)))
        self.session.conversation.messages = [Message("user", f"m{i}") for i in range(100)]
        self.assertIn("wm.context_set_int", self.operators_drawn(self.draw(self.panel.VIEW3D_PT_bc_assistant)))

    def test_attach_selection_shows_count(self):
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.active_object
        self.addCleanup(bpy.data.objects.remove, cube, do_unlink=True)
        cube.select_set(True)
        n = len(bpy.context.selected_objects)
        log = []
        fake = mock.Mock(layout=FakeLayout(log))
        context = mock.Mock(wraps=bpy.context)
        for name in ("window_manager", "scene", "preferences"):
            setattr(context, name, getattr(bpy.context, name))
        context.region = mock.Mock(width=300, height=800)
        context.selected_objects = bpy.context.selected_objects
        texts = []
        orig = FakeLayout.prop

        def prop(layout, data, name, text=None, **kwargs):
            texts.append((name, text))
            return orig(layout, data, name, text=text, **kwargs)

        with mock.patch.object(FakeLayout, "prop", prop):
            self.panel.VIEW3D_PT_bc_assistant.draw(fake, context)
        self.assertIn(("attach_selection", i18n.t("attach_selection_n", n=n)), texts)

    def test_draft_preview_and_file_chips(self):
        from blendercraft_assistant.ui import draft

        self.bc.draft = "# Plano\n| a | b |\n|---|---|\nlinha 4"
        self.addCleanup(draft.clear, self.bc)
        item = self.bc.attached_files.add()
        item.name, item.content = "plano.md", "x"
        self.addCleanup(self.bc.attached_files.clear)
        log = self.draw(self.panel.VIEW3D_PT_bc_assistant)
        labels = self.labels(log)
        self.assertIn("# Plano", labels)
        self.assertIn(i18n.t("draft_lines", n=4), labels)
        self.assertIn("plano.md", labels)
        ops = self.operators_drawn(log)
        self.assertIn("bc.clear_draft", ops)
        self.assertIn("bc.remove_file", ops)

    def test_working_shows_stop_send_now_and_queue(self):
        self.session.status = "WORKING"
        self.session.queue = [("depois", [])]
        log = self.draw(self.panel.VIEW3D_PT_bc_assistant)
        ops = self.operators_drawn(log)
        self.assertIn("bc.stop", ops)
        self.assertIn("bc.send_now", ops)
        self.assertIn(i18n.t("queued", n=1), self.labels(log))
        self.assertIn(i18n.t("working"), self.labels(log))

    def test_tool_details_expand(self):
        collapsed = self.labels(self.draw(self.panel.VIEW3D_PT_bc_assistant))
        self.bc.expanded_tool = "tu3"
        expanded = self.labels(self.draw(self.panel.VIEW3D_PT_bc_assistant))
        self.assertFalse(any("falhou" in label for label in collapsed))
        self.assertTrue(any("falhou" in label for label in expanded))

    def test_approval_block_on_top(self):
        import threading

        self.runtime.pending_approval = (
            "run_python",
            {"code": "bpy.ops.mesh.primitive_cube_add()", "description": "cubo"},
            threading.Event(),
            [],
        )
        log = self.draw(self.panel.VIEW3D_PT_bc_assistant)
        self.assertEqual(log[0], ("label", i18n.t("approval_request", tool="run_python")))
        self.assertEqual(self.operators_drawn(log)[:2], ["bc.approve", "bc.approve"])

    def test_claude_warnings(self):
        self.runtime.claude_problem = "missing"
        labels = " ".join(self.labels(self.draw(self.panel.VIEW3D_PT_bc_assistant)))
        self.assertIn(i18n.t("claude_missing"), labels)
        self.runtime.claude_problem = "login"
        labels = " ".join(self.labels(self.draw(self.panel.VIEW3D_PT_bc_assistant)))
        self.assertIn(i18n.t("claude_login"), labels)

    def test_settings_subpanel(self):
        log = self.draw(self.panel.VIEW3D_PT_bc_settings)
        self.assertIn(("operator", "bc.copy_mcp_command", i18n.t("copy_mcp")), log)
        self.assertIn("bc.set_unit", self.operators_drawn(log))
        self.assertIn("DEFAULT_CLOSED", self.panel.VIEW3D_PT_bc_settings.bl_options)
        self.assertEqual(self.panel.VIEW3D_PT_bc_settings.bl_parent_id, "VIEW3D_PT_bc_assistant")

    def test_markdown_rows_drawn(self):
        from pathlib import Path

        from blendercraft_assistant.conversations import Message

        fixture = (Path(__file__).parent / "fixtures" / "pernas.md").read_text(encoding="utf-8")
        self.session.conversation.messages = [Message("user", fixture), Message("assistant", fixture)]
        log = self.draw(self.panel.VIEW3D_PT_bc_assistant)
        labels = self.labels(log)
        # Table cells are drawn one label each.
        self.assertGreaterEqual(labels.count("−23 % ❌"), 4)
        self.assertGreaterEqual(labels.count("Margem"), 2)
        self.assertIn("4. Pernas (4 iguais)", labels)

    def test_unit_selector_in_the_3d_view_header(self):
        header = bpy.types.VIEW3D_HT_header
        funcs = header._dyn_ui_initialize()
        self.assertEqual(funcs.count(self.panel.draw_unit_header), 1)
        self.panel.unregister()
        self.assertNotIn(self.panel.draw_unit_header, funcs)
        self.panel.register()  # what the cleanup unregisters
        self.panel.register_header()  # idempotent
        self.assertEqual(funcs.count(self.panel.draw_unit_header), 1)
        log = []
        fake = mock.Mock(layout=FakeLayout(log))
        self.panel.draw_unit_header(fake, bpy.context)
        self.assertEqual([e[1] for e in log if e[0] == "operator"], ["bc.set_unit"])

    def test_scale_warning_when_file_is_not_in_mm(self):
        from blendercraft_assistant import units

        scene = bpy.context.scene
        units.apply_internal_mm(scene)
        self.addCleanup(units.apply_internal_mm, scene)
        warning = i18n.t("scale_warning", mm="1000")
        labels = " ".join(self.labels(self.draw(self.panel.VIEW3D_PT_bc_assistant)))
        self.assertNotIn(warning, labels)
        scene.unit_settings.scale_length = 1.0
        labels = " ".join(self.labels(self.draw(self.panel.VIEW3D_PT_bc_assistant)))
        self.assertIn(warning, labels)

    def test_error_shown(self):
        self.session.status = "ERROR"
        self.session.error = "Claude Code exited with code 1"
        labels = self.labels(self.draw(self.panel.VIEW3D_PT_bc_assistant))
        self.assertIn(i18n.t("error"), labels)
        self.assertIn("Claude Code exited with code 1", labels)

    def test_no_session(self):
        self.runtime.session = None
        log = self.draw(self.panel.VIEW3D_PT_bc_assistant)
        self.assertIn(("label", i18n.t("idle")), log)

    def test_conversations_popover(self):
        self.runtime.store.save(self.session.conversation)
        log = self.draw(self.panel.VIEW3D_PT_bc_conversations)
        ops = [e for e in log if e[0] == "operator"]
        self.assertEqual(
            [e[1] for e in ops], ["bc.open_conversation", "bc.delete_conversation"]
        )
        self.assertEqual(ops[0][2], "crie um cubo de 20 mm")

    def test_joint_subpanel(self):
        from blendercraft_assistant.tools.joints import create_pivot

        log = self.draw(self.panel.VIEW3D_PT_bc_joint)
        self.assertEqual(self.operators_drawn(log), ["bc.joint_create"])
        bpy.ops.mesh.primitive_cube_add(size=10)
        cube = bpy.context.active_object
        pivot = create_pivot(cube.name, (5.0, 0.0, 0.0), "Z")
        self.addCleanup(bpy.data.objects.remove, bpy.data.objects[pivot], do_unlink=True)
        self.addCleanup(bpy.data.objects.remove, cube, do_unlink=True)
        self.bc.joint_pivot = pivot
        with mock.patch.object(self.operators, "joint_result", "Gira livre"):
            log = self.draw(self.panel.VIEW3D_PT_bc_joint)
        self.assertEqual(
            self.operators_drawn(log), ["bc.joint_create", "bc.joint_reset", "bc.joint_test"]
        )
        self.assertIn(("prop", "joint_angle"), log)
        self.assertIn("Gira livre", self.labels(log))


if __name__ == "__main__":
    unittest.main()
