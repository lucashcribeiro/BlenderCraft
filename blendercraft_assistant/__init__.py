"""BlenderCraft Assistant: Claude Code embedded in a Blender side panel.

This module must not import bpy at top level. Only register() and
unregister() may import it, so the package stays importable by plain
Python (for example by scripts/record_fixture.py).
"""


def _start_runtime():
    """First timer tick after register(): start the MCP server and friends.

    Runs once (returns None). Deferred so enabling the extension stays quick
    and nothing starts while Blender is still loading.
    """
    import traceback

    from . import runtime

    try:
        runtime.start()
    except Exception:  # noqa: BLE001 - must not break Blender's event loop
        traceback.print_exc()
        import logging

        logging.getLogger(__name__).exception("the runtime did not start")
    return None


def register():
    import bpy

    from . import tools, ui

    tools.load_all()  # tools register themselves on import
    ui.register()
    # Persistent: the startup file loads after add-ons register, and a
    # plain timer would be dropped by that load.
    if not bpy.app.timers.is_registered(_start_runtime):
        bpy.app.timers.register(_start_runtime, first_interval=0.0, persistent=True)


def unregister():
    import bpy

    from . import log, runtime, ui

    if bpy.app.timers.is_registered(_start_runtime):
        bpy.app.timers.unregister(_start_runtime)
    runtime.stop()
    ui.unregister()
    log.shutdown()
