import json
import unittest
import urllib.error
import urllib.request

import bpy

import blendercraft_assistant as addon
from blendercraft_assistant import runtime

ALL_TOOLS = {
    "list_operators",
    "describe_operator",
    "run_python",
    "inspect_scene",
    "inspect_object",
    "measure",
    "mesh_topology",
    "print_check",
    "screenshot",
    "joint_create",
    "joint_set_angle",
    "joint_test",
    "undo",
    "redo",
    "import_file",
    "export_file",
    "save",
}


def tools_list(url: str, token: str) -> list[str]:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return [t["name"] for t in json.loads(response.read())["result"]["tools"]]


class RegisterTest(unittest.TestCase):
    def setUp(self):
        runtime.mode = "AUTO"

    def test_register_unregister(self):
        addon.register()
        try:
            self.assertTrue(hasattr(bpy.types, "VIEW3D_PT_bc_assistant"))
            self.assertTrue(hasattr(bpy.types, "VIEW3D_PT_bc_joint"))
            self.assertEqual(bpy.context.window_manager.bc.mode, "AUTO")
            # register() starts nothing: the runtime waits for the first tick.
            self.assertIsNone(runtime.server)
            self.assertTrue(bpy.app.timers.is_registered(addon._start_runtime))
            self.assertIsNone(addon._start_runtime())  # the tick, by hand
            self.assertIsNotNone(runtime.server)
            url, token = runtime.server.url, runtime.token
            self.assertIn("run_python", tools_list(url, token))
        finally:
            addon.unregister()
        self.assertFalse(hasattr(bpy.types, "VIEW3D_PT_bc_assistant"))
        self.assertFalse(hasattr(bpy.types.WindowManager, "bc"))
        self.assertFalse(bpy.app.timers.is_registered(addon._start_runtime))
        self.assertIsNone(runtime.server)
        with self.assertRaises(urllib.error.URLError):
            tools_list(url, token)

    def test_unregister_before_first_tick(self):
        addon.register()
        addon.unregister()
        self.assertFalse(bpy.app.timers.is_registered(addon._start_runtime))
        self.assertIsNone(runtime.server)
        self.assertFalse(hasattr(bpy.types, "VIEW3D_PT_bc_assistant"))

    def test_tools_list_contains_all(self):
        addon.register()
        try:
            addon._start_runtime()
            names = set(tools_list(runtime.server.url, runtime.token))
        finally:
            addon.unregister()
        self.assertLessEqual(ALL_TOOLS, names)


if __name__ == "__main__":
    unittest.main()
