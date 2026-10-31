"""Custom icons (assets/icons/*.png) loaded into a previews collection.

The PNGs are drawn by assets/icons/make_icons.py.
"""

from pathlib import Path

import bpy.utils.previews

ICON_DIR = Path(__file__).resolve().parent.parent / "assets" / "icons"
NAMES = ("chat", "chat_working", "chat_error")

_previews = None


def register() -> None:
    global _previews
    if _previews is not None:
        return
    _previews = bpy.utils.previews.new()
    for name in NAMES:
        _previews.load(name, str(ICON_DIR / f"{name}.png"), "IMAGE")


def unregister() -> None:
    global _previews
    if _previews is not None:
        bpy.utils.previews.remove(_previews)
        _previews = None


def icon_id(name: str) -> int:
    """icon_value for a layout call; 0 when the icons are not loaded."""
    if _previews is None or name not in _previews:
        return 0
    return _previews[name].icon_id
