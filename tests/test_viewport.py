import unittest
from unittest import mock

from blendercraft_assistant import mainthread, viewport
from blendercraft_assistant.tools import dispatch
from blendercraft_assistant.tools.base import REGISTRY, Tool, ToolResult, register_tool


class _Region:
    def __init__(self):
        self.tagged = 0

    def tag_redraw(self):
        self.tagged += 1


class _Area(_Region):
    def __init__(self, type_):
        super().__init__()
        self.type = type_
        self.regions = [_Region(), _Region()]


def _wm(*window_areas):
    windows = [
        mock.Mock(screen=mock.Mock(areas=areas)) for areas in window_areas
    ]
    return mock.Mock(windows=windows)


class RefreshTest(unittest.TestCase):
    def test_tags_every_area_and_region_of_every_window_and_updates(self):
        areas = [[_Area("VIEW_3D"), _Area("OUTLINER")], [_Area("PROPERTIES")]]
        update = mock.Mock()
        viewport.refresh(_wm(*areas), update=update)
        update.assert_called_once_with()
        for area in areas[0] + areas[1]:
            self.assertEqual(area.tagged, 1, area.type)
            self.assertEqual([r.tagged for r in area.regions], [1, 1])

    def test_a_failing_update_or_window_never_raises(self):
        broken = mock.Mock(windows=[mock.Mock(screen=None)])
        with mock.patch("traceback.print_exc"):
            viewport.refresh(broken, update=mock.Mock(side_effect=RuntimeError("x")))
            viewport.refresh(mock.Mock(windows=3), update=mock.Mock())

    def test_flush_only_refreshes_when_marked(self):
        viewport.refresh(_wm(), update=mock.Mock())  # clears the flag
        with mock.patch.object(viewport, "refresh") as refresh:
            self.assertFalse(viewport.flush_if_dirty())
            refresh.assert_not_called()
            viewport.mark_dirty()
            self.assertTrue(viewport.flush_if_dirty())
            refresh.assert_called_once_with()


class DispatchRefreshTest(unittest.TestCase):
    def setUp(self):
        for name, kind in (("vp_read", "read"), ("vp_scene", "scene"), ("vp_file", "file")):
            register_tool(
                Tool(
                    name=name,
                    description="x",
                    input_schema={"type": "object", "properties": {}},
                    handler=lambda args: ToolResult(text="ok"),
                    kind=kind,
                    push_undo=False,
                )
            )
            self.addCleanup(REGISTRY.pop, name, None)

    def test_scene_and_file_tools_refresh_the_views_and_read_tools_do_not(self):
        with mock.patch.object(dispatch.viewport, "refresh") as refresh, \
                mock.patch.object(dispatch.viewport, "mark_dirty") as dirty:
            dispatch.call_tool("vp_read", {})
            refresh.assert_not_called()
            dispatch.call_tool("vp_scene", {})
            dispatch.call_tool("vp_file", {})
            self.assertEqual(refresh.call_count, 2)
            self.assertEqual(dirty.call_count, 2)

    def test_a_failing_scene_tool_refreshes_too(self):
        def boom(args):
            raise RuntimeError("after changing things")

        REGISTRY["vp_scene"] = Tool(
            name="vp_scene", description="x", input_schema={"type": "object", "properties": {}},
            handler=boom, kind="scene", push_undo=False,
        )
        with mock.patch.object(dispatch.viewport, "refresh") as refresh:
            result = dispatch.call_tool("vp_scene", {})
        self.assertTrue(result.is_error)
        refresh.assert_called_once_with()

    def test_the_timer_refreshes_after_a_batch_that_ran_tool_calls(self):
        viewport.mark_dirty()
        mainthread.post(lambda: None)
        with mock.patch.object(viewport, "refresh") as refresh:
            mainthread._tick()
            refresh.assert_called_once_with()
            refresh.reset_mock()
            mainthread._tick()  # nothing ran: no refresh
            refresh.assert_not_called()


if __name__ == "__main__":
    unittest.main()
