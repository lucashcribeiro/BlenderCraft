"""A practical Markdown subset, parsed into blocks for the conversation list.

Blender labels show one style per line, so inline markup (**bold**,
`code`, links) is reduced to its text; block structure (headings, lists,
code fences, rules and tables) is kept and drawn row by row. Messages sent
to Claude are never changed: this is only for display. Must not import bpy.
"""

import re

_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_RULE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_NUMBERED = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")

_BOLD = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")

TAB = "    "
MIN_COL_CHARS = 3
MAX_COL_CHARS = 30


def inline(text: str) -> str:
    """Text of a line without inline markup (bold, code spans, links)."""
    text = _LINK.sub(r"\1", text)
    text = _CODE.sub(r"\1", text)
    return _BOLD.sub(r"\2", text)


def split_cells(line: str) -> list[str]:
    """Cells of a table row (outer pipes optional, \\| kept as a pipe)."""
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    cells = re.split(r"(?<!\\)\|", body)
    return [inline(c.strip().replace("\\|", "|")) for c in cells]


def _is_table_start(lines: list[str], i: int) -> bool:
    return (
        "|" in lines[i]
        and i + 1 < len(lines)
        and "-" in lines[i + 1]
        and bool(_TABLE_SEP.match(lines[i + 1]))
    )


def parse(text: str) -> list[tuple]:
    """Blocks of text, in order:

    ("heading", level, text), ("para", text), ("blank",),
    ("item", depth, marker, text), ("code", line), ("rule",),
    ("table", header_cells, rows_of_cells).
    """
    blocks: list[tuple] = []
    lines = text.replace("\r\n", "\n").split("\n")
    in_code = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if _FENCE.match(line):
            in_code = not in_code
            i += 1
            continue
        if in_code:
            blocks.append(("code", line.replace("\t", TAB)))
            i += 1
            continue
        if not line.strip():
            if blocks and blocks[-1] != ("blank",):
                blocks.append(("blank",))
            i += 1
            continue
        if _is_table_start(lines, i):
            header = split_cells(line)
            rows = []
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                cells = split_cells(lines[i])
                cells = (cells + [""] * len(header))[: len(header)]
                rows.append(cells)
                i += 1
            blocks.append(("table", header, rows))
            continue
        heading = _HEADING.match(line)
        if heading:
            blocks.append(("heading", len(heading.group(1)), inline(heading.group(2))))
        elif _RULE.match(line):
            blocks.append(("rule",))
        elif (bullet := _BULLET.match(line)) is not None:
            depth = len(bullet.group(1).replace("\t", TAB)) // 2
            blocks.append(("item", depth, "•", inline(bullet.group(2))))
        elif (numbered := _NUMBERED.match(line)) is not None:
            depth = len(numbered.group(1).replace("\t", TAB)) // 2
            blocks.append(("item", depth, f"{numbered.group(2)}.", inline(numbered.group(3))))
        else:
            blocks.append(("para", inline(line.strip())))
        i += 1
    while blocks and blocks[-1] == ("blank",):
        blocks.pop()
    return blocks


def column_fractions(header: list[str], rows: list[list[str]]) -> list[float]:
    """Share of the width for each column, from its longest cell."""
    widths = []
    for col in range(len(header)):
        longest = max(len(r[col]) if col < len(r) else 0 for r in [header, *rows])
        widths.append(min(MAX_COL_CHARS, max(MIN_COL_CHARS, longest)))
    total = sum(widths) or 1
    return [w / total for w in widths]
