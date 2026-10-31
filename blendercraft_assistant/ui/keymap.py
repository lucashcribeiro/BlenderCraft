"""Add-on shortcuts: Enter and Ctrl/Cmd+Enter in the message field, and
Ctrl/Cmd+Shift+O for File > Open model….

The items react to the key *release*: the press is taken by the text
field, which commits; the release that follows reaches the Window keymap.
bc.send_key only runs right after the field's commit (see its poll), so
Enter anywhere else in Blender is left alone.
"""

import bpy

# (keymap, keymap item) pairs added by register().
items: list = []


def register() -> None:
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:  # some background sessions have no add-on keyconfig
        return
    km = kc.keymaps.new(name="Window", space_type="EMPTY")
    for key in ("RET", "NUMPAD_ENTER"):
        items.append((km, km.keymap_items.new("bc.send_key", key, "RELEASE")))
        items.append((km, km.keymap_items.new("bc.send_key_now", key, "RELEASE", ctrl=True)))
        items.append((km, km.keymap_items.new("bc.send_key_now", key, "RELEASE", oskey=True)))
    # File > Open model… (takes over Blender's Open Recent shortcut).
    items.append((km, km.keymap_items.new("bc.open_model", "O", "PRESS", ctrl=True, shift=True)))
    items.append((km, km.keymap_items.new("bc.open_model", "O", "PRESS", oskey=True, shift=True)))


def unregister() -> None:
    for km, kmi in items:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):  # keyconfig already gone
            pass
    items.clear()
