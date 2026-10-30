import unittest
from unittest import mock

import bpy

from blendercraft_assistant import i18n
from blendercraft_assistant.ui.text_wrap import strip_markdown, wrap

REQUIRED_KEYS = (
    "assistant idle working error new_chat conversations stop send send_now "
    "queued attach_view attach_selection mode_auto mode_ask mode_readonly "
    "changes undo_ai undo_ai_blocked details show_older approve deny "
    "approval_request claude_missing claude_login step_limit unit joint "
    "joint_reset copy_mcp"
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

    def test_strip_markdown(self):
        text = (
            "# Título\n"
            "## Seção\n"
            "Um **cubo** de __20 mm__ com `bevel` e ```code```.\n"
            "Veja [a documentação](https://docs.blender.org) e [isto](x).\n"
            "Nota #3 fica."
        )
        self.assertEqual(
            strip_markdown(text),
            "Título\n"
            "Seção\n"
            "Um cubo de 20 mm com bevel e code.\n"
            "Veja a documentação e isto.\n"
            "Nota #3 fica.",
        )


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


class FakeSession:
    def __init__(self, queue=()):
        self.queue = list(queue)
        self.sent = []
        self.sent_now = []

    def send(self, text, attachments=()):
        self.sent.append((text, list(attachments)))

    def send_now(self, text, attachments=()):
        self.sent_now.append((text, list(attachments)))


class OperatorsTest(unittest.TestCase):
    def setUp(self):
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

    def test_input_enter_sends_once_and_clears(self):
        self.bc.input = "  crie um cubo  "
        self.assertEqual(self.session.sent, [("crie um cubo", [])])
        self.assertEqual(self.bc.input, "")
        self.bc.input = "   "
        self.assertEqual(len(self.session.sent), 1)

    def test_send_before_runtime_is_up_keeps_text(self):
        with mock.patch.object(self.runtime, "server", None):
            self.bc.input = "crie um cubo"  # Enter
            self.assertEqual(self.bc.input, "crie um cubo")
            self.assertEqual(self.operators.notice, i18n.t("server_off"))
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
        context.region = mock.Mock(width=300)
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
            ("prop", "model"),
            ("label", i18n.t("you")),
            ("prop", "input"),
            ("prop", "attach_view"),
            ("prop", "attach_selection"),
            ("prop", "mode"),
            ("label", i18n.t("changes", n=0)),
            ("operator", "bc.undo_ai_changes"),
            ("operator", "bc.set_unit"),
        ]
        positions = [kinds.index(item) for item in order]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("bc.stop", self.operators_drawn(log))
        self.assertNotIn("bc.send_now", self.operators_drawn(log))
        # Older messages are hidden behind "show older".
        self.assertIn("wm.context_set_int", self.operators_drawn(log))
        labels = self.labels(log)
        self.assertIn("Pronto", labels)
        self.assertIn("Criei o cubo com bevel.", labels)
        self.assertFalse(any("**" in label for label in labels))

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
