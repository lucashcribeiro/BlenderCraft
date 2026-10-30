"""Side panel, operators, properties and preferences.

Submodules import bpy; they are only imported by register()/unregister()
(text_wrap stays bpy-free).
"""


def _modules():
    from . import operators, panel, prefs, props

    return (props, prefs, operators, panel)


def register() -> None:
    for module in _modules():
        module.register()


def unregister() -> None:
    for module in reversed(_modules()):
        module.unregister()
