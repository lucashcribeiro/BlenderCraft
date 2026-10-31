"""Draw the Assistant's chat-bubble icons (64x64 PNG with transparency).

Run from the repository root:

    /Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup \
        --python blendercraft_assistant/assets/icons/make_icons.py

Pure Python coverage drawing (4x4 supersampling), saved through Blender's
image API. Writes chat.png (idle), chat_working.png (blue dot) and
chat_error.png (orange-red dot) next to this script.
"""

import os

import bpy

SIZE = 64
SAMPLES = 4
LIGHT = (0.90, 0.90, 0.90)  # Blender's monochrome icon grey
BLUE = (0x3B / 255, 0x82 / 255, 0xF6 / 255)
RED = (0xF9 / 255, 0x73 / 255, 0x16 / 255)
STROKE = 4.5

# Bubble body (rounded rectangle) and tail, in pixels from the top left.
X0, Y0, X1, Y1, R = 5.0, 9.0, 59.0, 45.0, 13.0
TAIL = ((14.0, 41.0), (27.0, 41.0), (9.0, 58.0))
DOTS = ((20.0, 27.0), (32.0, 27.0), (44.0, 27.0))
DOT_R = 3.6
BADGE = (51.0, 13.0, 10.0)  # status dot: centre x, y and radius


def _in_round_rect(x, y, inset):
    x0, y0, x1, y1, r = X0 + inset, Y0 + inset, X1 - inset, Y1 - inset, max(0.0, R - inset)
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return False
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def _in_triangle(x, y, tri):
    (ax, ay), (bx, by), (cx, cy) = tri

    def side(px, py, qx, qy):
        return (qx - px) * (y - py) - (qy - py) * (x - px)

    s1, s2, s3 = side(ax, ay, bx, by), side(bx, by, cx, cy), side(cx, cy, ax, ay)
    return (s1 >= 0 and s2 >= 0 and s3 >= 0) or (s1 <= 0 and s2 <= 0 and s3 <= 0)


def _shrunk_tail(inset):
    (ax, ay), (bx, by), (cx, cy) = TAIL
    return ((ax + inset, ay - inset), (bx - inset * 1.6, by - inset), (cx + inset * 0.9, cy - inset * 2.2))


def _bubble(x, y):
    """Outline of the bubble with three dots inside."""
    outer = _in_round_rect(x, y, 0.0) or _in_triangle(x, y, TAIL)
    inner = _in_round_rect(x, y, STROKE) or (_in_triangle(x, y, _shrunk_tail(STROKE)) and y < Y1)
    dot = any((x - dx) ** 2 + (y - dy) ** 2 <= DOT_R ** 2 for dx, dy in DOTS)
    return (outer and not inner) or dot


def _badge(x, y, ring):
    bx, by, br = BADGE
    return (x - bx) ** 2 + (y - by) ** 2 <= (br + ring) ** 2


def draw(badge_color=None):
    pixels = []
    for row in range(SIZE):
        y_top = SIZE - 1 - row  # Blender images start at the bottom row
        for col in range(SIZE):
            bubble = badge = ring = 0
            for i in range(SAMPLES):
                for j in range(SAMPLES):
                    x = col + (i + 0.5) / SAMPLES
                    y = y_top + (j + 0.5) / SAMPLES
                    if badge_color is not None and _badge(x, y, 0.0):
                        badge += 1
                    elif badge_color is not None and _badge(x, y, 3.0):
                        ring += 1  # transparent gap around the dot
                    elif _bubble(x, y):
                        bubble += 1
            total = SAMPLES * SAMPLES
            a_bubble, a_badge = bubble / total, badge / total
            alpha = a_bubble + a_badge
            if alpha <= 0:
                pixels += (0.0, 0.0, 0.0, 0.0)
                continue
            color = badge_color or LIGHT
            rgb = [(LIGHT[k] * a_bubble + color[k] * a_badge) / alpha for k in range(3)]
            pixels += (*rgb, alpha)
    return pixels


def save(name, pixels, folder):
    image = bpy.data.images.new(name, SIZE, SIZE, alpha=True)
    image.alpha_mode = "STRAIGHT"
    image.pixels = pixels
    image.filepath_raw = os.path.join(folder, f"{name}.png")
    image.file_format = "PNG"
    image.save()
    bpy.data.images.remove(image)


def main():
    folder = os.path.dirname(os.path.abspath(__file__))
    save("chat", draw(), folder)
    save("chat_working", draw(BLUE), folder)
    save("chat_error", draw(RED), folder)


main()
