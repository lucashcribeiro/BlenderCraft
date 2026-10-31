"""Line wrapping for panel labels.

Blender labels do not wrap, so the panel splits text into lines that fit
its width. This module must not import bpy.
"""

import textwrap

# Average label glyph width in pixels at ui_scale 1.
CHAR_PX = 7
_MIN_CHARS = 10


def wrap(text: str, width_px: int, ui_scale: float) -> list[str]:
    """Lines of at most max(10, width_px / (7 * ui_scale)) characters.

    Every input line break is kept (an empty line stays empty); long words
    are split. Background mode reports ui_scale 0, treated as 1.
    """
    if not text:
        return []
    scale = ui_scale if ui_scale > 0 else 1.0
    chars = max(_MIN_CHARS, int(width_px / (CHAR_PX * scale)))
    lines: list[str] = []
    for paragraph in text.split("\n"):
        lines.extend(
            textwrap.wrap(
                paragraph,
                width=chars,
                break_long_words=True,
                break_on_hyphens=False,
            )
            or [""]
        )
    return lines

