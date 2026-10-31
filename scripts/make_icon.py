"""Draws the BlenderCraft app icon (1024 x 1024 PNG) in the "Craft apps" style.

Flat, vector-like: a violet macOS-style tile, a cream isometric cube with
thick black outlines and mesh lines on its top, Blender's orange on the selected
vertex and a cream chat bubble for the assistant. Every shape is a signed
distance field, so edges are antialiased at any size.

Runs with Blender's own Python (it ships numpy), from the repository root:

    /Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup \
        --python scripts/make_icon.py -- scripts/assets/BlenderCraft.png
"""

import struct
import sys
import zlib
from pathlib import Path

import numpy as np

SIZE = 1024
SS = 2  # supersampling factor for the final downscale
OUTLINE = 30.0  # black outline width, in 1024-px units

# Family palette: one saturated tile colour (a sibling-free violet), cream
# illustration, black outlines, orange accent.
TILE = (108, 78, 224)
GRID = (134, 108, 236)
CREAM = (241, 234, 218)
CREAM_LEFT = (226, 216, 194)
CREAM_RIGHT = (208, 196, 170)
INK = (20, 18, 24)
ORANGE = (242, 122, 58)


def _grid(n):
    ys, xs = np.mgrid[0:n, 0:n].astype(np.float32)
    scale = SIZE / n
    return (xs + 0.5) * scale, (ys + 0.5) * scale, scale


def sd_round_box(x, y, cx, cy, half_w, half_h, radius):
    qx = np.abs(x - cx) - (half_w - radius)
    qy = np.abs(y - cy) - (half_h - radius)
    outside = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0))
    inside = np.minimum(np.maximum(qx, qy), 0)
    return outside + inside - radius


def sd_segment(x, y, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    t = np.clip(((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy), 0.0, 1.0)
    return np.hypot(x - ax - t * dx, y - ay - t * dy)


def sd_polygon(x, y, pts):
    """Signed distance to a simple polygon: negative inside."""
    d = np.full(x.shape, np.inf, dtype=np.float32)
    inside = np.zeros(x.shape, dtype=bool)
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        d = np.minimum(d, sd_segment(x, y, a, b))
        (ax, ay), (bx, by) = a, b
        crosses = (ay > y) != (by > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            xi = ax + (y - ay) * (bx - ax) / (by - ay)
        inside ^= crosses & (x < xi)
    return np.where(inside, -d, d)


def sd_circle(x, y, cx, cy, r):
    return np.hypot(x - cx, y - cy) - r


def sd_polyline(x, y, pts):
    d = np.full(x.shape, np.inf, dtype=np.float32)
    for a, b in zip(pts, pts[1:]):
        d = np.minimum(d, sd_segment(x, y, a, b))
    return d


class Canvas:
    def __init__(self, n):
        self.x, self.y, self.px = _grid(n)
        self.rgb = np.zeros((n, n, 3), dtype=np.float32)
        self.alpha = np.zeros((n, n), dtype=np.float32)

    def coverage(self, sd):
        # One output pixel of antialiasing.
        return np.clip(0.5 - sd / self.px, 0.0, 1.0)

    def paint(self, cov, color, opacity=1.0):
        a = cov * opacity
        col = np.array(color, dtype=np.float32) / 255.0
        out_a = a + self.alpha * (1.0 - a)
        safe = np.where(out_a > 0, out_a, 1.0)
        self.rgb = (col * a[..., None] + self.rgb * (self.alpha * (1.0 - a))[..., None]) / safe[..., None]
        self.alpha = out_a

    def fill(self, sd, color):
        self.paint(self.coverage(sd), color)

    def stroke(self, sd_unsigned, width, color):
        self.paint(self.coverage(sd_unsigned - width / 2.0), color)

    def fill_and_outline(self, sd, color, width=OUTLINE):
        # Outline centred on the edge: half of it eats into the fill.
        self.fill(sd, color)
        self.stroke(np.abs(sd), width, color=INK)


def iso_cube(cx, cy, edge):
    """Corners of an isometric cube whose front vertical edge starts at (cx, cy)."""
    c30 = edge * 0.8660254
    top = (cx, cy - edge)
    right = (cx + c30, cy - edge / 2)
    left = (cx - c30, cy - edge / 2)
    centre = (cx, cy)
    bottom = (cx, cy + edge)
    bottom_right = (cx + c30, cy + edge / 2)
    bottom_left = (cx - c30, cy + edge / 2)
    return {
        "top": [top, right, centre, left],
        "left": [left, centre, bottom, bottom_left],
        "right": [centre, right, bottom_right, bottom],
        "silhouette": [top, right, bottom_right, bottom, bottom_left, left],
        "centre": centre,
        "top_vertex": top,
        "right_vertex": right,
    }


def lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def draw(n):
    cv = Canvas(n)
    x, y = cv.x, cv.y
    centre = SIZE / 2

    # Tile: macOS icon grid (824 px of 1024), soft drop shadow underneath.
    half, radius = 412.0, 185.0
    tile = sd_round_box(x, y, centre, centre, half, half, radius)
    shadow = sd_round_box(x, y, centre, centre + 12, half, half, radius)
    cv.paint(np.clip(1.0 - (shadow + 4.0) / 26.0, 0.0, 1.0) ** 2, (0, 0, 0), opacity=0.28)
    cv.fill(tile, TILE)

    # Faint print-bed grid on the tile, clipped to it.
    step = 103.0
    gx = np.abs(((x - centre) / step + 0.5) % 1.0 - 0.5) * step
    gy = np.abs(((y - centre) / step + 0.5) % 1.0 - 0.5) * step
    grid = np.minimum(gx, gy) - 2.0
    cv.paint(cv.coverage(grid) * cv.coverage(tile + 6.0), GRID)

    # The cube, slightly left and low; the bubble sits top right.
    cube = iso_cube(470.0, 575.0, 262.0)
    cv.fill(sd_polygon(x, y, cube["left"]), CREAM_LEFT)
    cv.fill(sd_polygon(x, y, cube["right"]), CREAM_RIGHT)
    cv.fill(sd_polygon(x, y, cube["top"]), CREAM)

    # Mesh lines on the top face only: more detail is noise at 32 px.
    t_, r_, c_, l_ = cube["top"]
    _, _, b_, _ = cube["left"]
    mesh = np.minimum(
        sd_segment(x, y, lerp(t_, r_, 0.5), lerp(l_, c_, 0.5)),
        sd_segment(x, y, lerp(t_, l_, 0.5), lerp(r_, c_, 0.5)),
    )
    cv.stroke(mesh, 13.0, INK)

    # Outlines: silhouette and the three inner edges.
    cv.stroke(np.abs(sd_polygon(x, y, cube["silhouette"])), OUTLINE, INK)
    inner = sd_polyline(x, y, [l_, c_, r_])
    inner = np.minimum(inner, sd_segment(x, y, c_, b_))
    cv.stroke(inner, OUTLINE, INK)

    # Selected vertex, Blender orange.
    vx, vy = cube["top_vertex"]
    cv.fill_and_outline(sd_circle(x, y, vx, vy, 34.0), ORANGE, width=18.0)

    # Assistant chat bubble with a tail pointing at the cube.
    bx, by, bw, bh, br = 690.0, 300.0, 132.0, 96.0, 48.0
    body = sd_round_box(x, y, bx, by, bw, bh, br)
    tail = sd_polygon(x, y, [(bx - 70, by + 60), (bx - 128, by + 152), (bx + 4, by + 80)])
    bubble = np.minimum(body, tail)
    cv.fill_and_outline(bubble, CREAM, width=OUTLINE)
    for i in (-1, 0, 1):
        cv.fill(sd_circle(x, y, bx + i * 50.0, by, 17.0), ORANGE if i == 1 else INK)

    rgba = np.concatenate([cv.rgb, cv.alpha[..., None]], axis=-1)
    return np.clip(rgba, 0.0, 1.0)


def downscale(img, factor):
    n = img.shape[0] // factor
    # Average premultiplied colour so edges do not darken.
    pre = img.copy()
    pre[..., :3] *= pre[..., 3:4]
    pre = pre.reshape(n, factor, n, factor, 4).mean(axis=(1, 3))
    alpha = pre[..., 3:4]
    pre[..., :3] = np.where(alpha > 0, pre[..., :3] / np.where(alpha > 0, alpha, 1), 0)
    return pre


def write_png(path: Path, img) -> None:
    data = (np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8)
    h, w = data.shape[:2]
    raw = b"".join(b"\x00" + data[row].tobytes() for row in range(h))

    def chunk(kind, payload):
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def main() -> None:
    args = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    out = Path(args[0]) if args else Path(__file__).resolve().parent / "assets" / "BlenderCraft.png"
    write_png(out, downscale(draw(SIZE * SS), SS))
    print(f"BlenderCraft icon written to {out}")


main()
