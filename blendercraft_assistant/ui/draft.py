"""Multi-line message drafts.

Blender's text field is single-line: a pasted Markdown text with headings
and tables loses its lines. The draft holds such text:

- Paste (bc.paste_draft) puts the clipboard into WindowManager.bc.draft,
  which no field edits, so every line is kept.
- Editor (bc.edit_draft) moves the draft into a Text block shown in a Text
  Editor window (the composer). While that block exists it is the draft.

Sending takes the draft plus the typed line and clears both: the Text block
is removed and its window closed. The block has no fake user, so a draft
still open when the file is saved is not written into the .blend.
"""

import bpy

from ..i18n import t
from .text_wrap import wrap

DRAFT_KEY = "bc_draft"  # ID property marking the composer's Text block
PREVIEW_LINES = 3


def read_clipboard() -> str:
    return bpy.context.window_manager.clipboard or ""


def composer():
    """The composer's Text block, if one is open."""
    return next((txt for txt in bpy.data.texts if txt.get(DRAFT_KEY)), None)


def text(props) -> str:
    txt = composer()
    return txt.as_string() if txt is not None else props.draft


def exists(props) -> bool:
    return composer() is not None or bool(props.draft)


def append(props, value: str) -> None:
    """Add value to the draft, on a new line when the draft has text."""
    current = text(props)
    joined = f"{current.rstrip(chr(10))}\n{value}" if current.strip() else value
    txt = composer()
    if txt is not None:
        txt.clear()
        txt.write(joined)
    else:
        props.draft = joined


def open_composer(props):
    """Text block holding the draft (created from props.draft if needed)."""
    txt = composer()
    if txt is None:
        txt = bpy.data.texts.new(t("draft_name"))
        txt.use_fake_user = False
        txt[DRAFT_KEY] = True
        txt.write(props.draft)
        props.draft = ""
    return txt


def _composer_windows(txt) -> list:
    windows = []
    wm = bpy.context.window_manager
    for window in wm.windows:
        screen = window.screen
        if screen is None or len(screen.areas) != 1:
            continue
        area = screen.areas[0]
        if area.type == "TEXT_EDITOR" and area.spaces.active.text == txt:
            windows.append(window)
    return windows


def show_window(context, txt) -> bool:
    """Open a window with a Text Editor on txt; False without a UI."""
    if bpy.app.background or context.window is None:
        return False
    if _composer_windows(txt):
        return True
    wm = context.window_manager
    before = set(wm.windows[:])
    try:
        # Duplicating the current area opens it alone in a new window.
        bpy.ops.screen.area_dupli("INVOKE_DEFAULT")
    except RuntimeError:
        return False
    new = [w for w in wm.windows if w not in before]
    if not new:
        return False
    area = new[0].screen.areas[0]
    area.type = "TEXT_EDITOR"
    space = area.spaces.active
    space.text = txt
    space.show_word_wrap = True
    space.show_line_numbers = False
    return True


def clear(props) -> None:
    """Forget the draft: remove the composer's block and close its window."""
    props.draft = ""
    txt = composer()
    if txt is None:
        return
    for window in _composer_windows(txt):
        try:
            with bpy.context.temp_override(window=window):
                bpy.ops.wm.window_close()
        except RuntimeError:
            pass
    bpy.data.texts.remove(txt)


def preview(value: str, width_px: int, scale: float) -> tuple[list[str], int]:
    """First wrapped lines of the draft and its number of lines."""
    lines = value.rstrip("\n").split("\n") if value else []
    shown = wrap("\n".join(lines[:PREVIEW_LINES]), width_px, scale)[:PREVIEW_LINES]
    return shown, len(lines)
