"""Friendly tool labels, the header status while working, the prompt asks."""

import unittest
from unittest import mock

from blendercraft_assistant import activity, i18n, mainthread, runtime
from blendercraft_assistant.claude.session import AgentSession
from blendercraft_assistant.claude.system_prompt import build_system_prompt
from blendercraft_assistant.conversations import Message, ToolCall
from blendercraft_assistant.ui import chat_list, panel


class _Session:
    """Just what working_text reads."""

    def __init__(self, tools=(), started=100.0):
        self._assistant = Message("assistant", "", list(tools))
        self.turn_started = started
        self.status = "WORKING"


class LabelTest(unittest.TestCase):
    def test_portuguese_labels(self):
        cases = [
            ("run_python", {}, "Executando Python"),
            ("screenshot", {"view": "iso"}, "Tirando print (iso)"),
            ("screenshot", {}, "Tirando print"),
            ("inspect_scene", {}, "Lendo a cena"),
            ("measure", {}, "Medindo"),
            ("mesh_topology", {}, "Checando a malha"),
            ("print_check", {}, "Checando impressão"),
            ("import_file", {"path": "/m/perna.stl"}, "Importando perna.stl"),
            ("export_file", {"path": "/m/peca.3mf"}, "Exportando peca.3mf"),
            ("import_file", {}, "Importando"),
            ("joint_create", {}, "Articulação: criando"),
            ("joint_set_angle", {}, "Articulação: ajustando o ângulo"),
            ("joint_test", {}, "Articulação: testando"),
            ("undo", {}, "Desfazendo"),
            ("redo", {}, "Refazendo"),
            ("save", {}, "Salvando"),
            ("Skill", {"skill": "blendercraft:design"}, "Consultando skill design"),
            ("Skill", {}, "Consultando skill"),
            ("articulate_figure", {}, "Articulando a figura"),
            ("analyze_figure", {}, "Analisando a figura"),
        ]
        for name, data, expected in cases:
            self.assertEqual(activity.tool_label(name, data), expected, name)

    def test_english_labels(self):
        with mock.patch.object(i18n, "_setting", return_value="EN"):
            self.assertEqual(activity.tool_label("measure"), "Measuring")
            self.assertEqual(activity.tool_label("import_file", {"path": "a.stl"}), "Importing a.stl")

    def test_unknown_tool_falls_back_to_its_raw_name(self):
        self.assertEqual(activity.tool_label("brand_new_tool", {"x": 1}), "brand_new_tool")
        self.assertFalse(activity.knows("brand_new_tool"))
        self.assertTrue(activity.knows("measure"))

    def test_every_registered_tool_has_a_label_in_both_languages(self):
        from blendercraft_assistant.tools import load_all
        from blendercraft_assistant.tools.base import REGISTRY

        load_all()
        for name in REGISTRY:
            for setting in ("PT", "EN"):
                with mock.patch.object(i18n, "_setting", return_value=setting):
                    self.assertTrue(activity.knows(name), f"{name} ({setting})")

    def test_label_keys_exist_in_both_languages(self):
        keys = [k for k in i18n.STRINGS["pt"] if k.startswith("act_")]
        self.assertTrue(keys)
        for key in keys:
            self.assertIn(key, i18n.STRINGS["en"])


class RowTextTest(unittest.TestCase):
    def row(self, name, data):
        return chat_list.tool_row_text(ToolCall("t1", name, data))

    def test_label_then_the_models_description(self):
        self.assertEqual(
            self.row("run_python", {"code": "x", "description": "Corta o furo da perna"}),
            "Executando Python — Corta o furo da perna",
        )

    def test_label_alone_without_a_description(self):
        self.assertEqual(self.row("measure", {"a": "Cube"}), "Medindo")

    def test_unknown_tool_keeps_name_and_summary(self):
        self.assertEqual(self.row("brand_new_tool", {"description": "faz coisa"}), "brand_new_tool: faz coisa")
        self.assertEqual(self.row("brand_new_tool", {}), "brand_new_tool")

    def test_long_description_is_clipped(self):
        text = self.row("run_python", {"description": "palavra " * 40})
        self.assertTrue(text.startswith("Executando Python — palavra"))
        self.assertTrue(text.endswith("…"))


class HeaderStatusTest(unittest.TestCase):
    def test_shows_the_running_tool_and_the_seconds(self):
        running = ToolCall("a", "measure", {}, status="running")
        session = _Session([running])
        self.assertEqual(activity.working_text(session, now=112.4), "Trabalhando: Medindo… 12 s")

    def test_latest_running_tool_wins_and_finished_ones_are_ignored(self):
        tools = [
            ToolCall("a", "run_python", {}, status="running"),
            ToolCall("b", "measure", {}, status="ok"),
            ToolCall("c", "screenshot", {"view": "front"}, status="running"),
        ]
        text = activity.working_text(_Session(tools), now=103)
        self.assertEqual(text, "Trabalhando: Tirando print (front)… 3 s")

    def test_thinking_while_no_tool_runs(self):
        done = ToolCall("a", "measure", {}, status="ok")
        self.assertEqual(activity.working_text(_Session([done]), now=105), "Trabalhando: Pensando… 5 s")
        self.assertEqual(activity.working_text(_Session(), now=100), "Trabalhando: Pensando… 0 s")

    def test_english(self):
        with mock.patch.object(i18n, "_setting", return_value="EN"):
            self.assertEqual(activity.working_text(_Session(), now=107), "Working: Thinking… 7 s")

    def test_without_a_session_or_start_time(self):
        self.assertEqual(activity.working_text(None), "Trabalhando: Pensando… 0 s")

    def test_panel_badge_text(self):
        session = _Session([ToolCall("a", "mesh_topology", {}, status="running")])
        with mock.patch.object(activity.time, "monotonic", return_value=104.0):
            self.assertEqual(panel._status_text("WORKING", session), "Trabalhando: Checando a malha… 4 s")
        self.assertEqual(panel._status_text("IDLE", session), i18n.t("idle"))
        self.assertEqual(panel._status_text("ERROR", None), i18n.t("error"))
        self.assertEqual(panel._status_text("WORKING", None), i18n.t("working"))

    def test_session_records_when_the_request_started(self):
        sent = []

        class _Proc:
            alive = True

            def send_user(self, content):
                sent.append(content)

            def stop(self):
                pass

        store = mock.Mock()
        conversation = mock.Mock(messages=[], claude_session_id=None)
        session = AgentSession(conversation, store, lambda *a: _Proc(), lambda n: "read")
        self.assertIsNone(session.turn_started)
        with mock.patch("blendercraft_assistant.claude.session.time.monotonic", return_value=55.0):
            session.send("oi")
        self.assertEqual(session.turn_started, 55.0)


class TickHookTest(unittest.TestCase):
    def test_runtime_redraws_about_once_a_second_while_working(self):
        session = mock.Mock(status="WORKING")
        with mock.patch.object(runtime, "session", session), \
                mock.patch.object(runtime, "tag_redraw") as redraw, \
                mock.patch.object(runtime.time, "monotonic", side_effect=[1000.0, 1000.4, 1001.2]):
            runtime._last_working_redraw = 0.0
            runtime._working_tick()
            runtime._working_tick()
            runtime._working_tick()
        self.assertEqual(redraw.call_count, 2)

    def test_no_redraw_when_idle_or_without_a_session(self):
        with mock.patch.object(runtime, "tag_redraw") as redraw:
            with mock.patch.object(runtime, "session", None):
                runtime._working_tick()
            with mock.patch.object(runtime, "session", mock.Mock(status="IDLE")):
                runtime._working_tick()
        redraw.assert_not_called()

    def test_the_timer_runs_its_hooks_and_survives_a_failing_one(self):
        calls = []

        def bad():
            raise RuntimeError("x")

        mainthread.tick_hooks[:] = [bad, lambda: calls.append(1)]
        self.addCleanup(mainthread.tick_hooks.clear)
        with mock.patch("traceback.print_exc"):
            mainthread._tick()
        self.assertEqual(calls, [1])


class PromptWordingTest(unittest.TestCase):
    def test_descriptions_in_the_users_language_and_progress_notes(self):
        for language in ("pt", "en"):
            text = build_system_prompt("5.2.2", "mm", language)
            self.assertIn("write it in the user's language", text)
            self.assertIn("one-line progress note", text)
            self.assertIn("Before a long step", text)

    def test_run_python_description_argument_says_the_same(self):
        from blendercraft_assistant.tools import load_all
        from blendercraft_assistant.tools.base import REGISTRY

        load_all()
        schema = REGISTRY["run_python"].input_schema
        self.assertIn("user's language", schema["properties"]["description"]["description"])


if __name__ == "__main__":
    unittest.main()
