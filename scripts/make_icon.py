"""Draws the BlenderCraft app icon (1024 x 1024 PNG).

An original mark in Blender's palette, not Blender's logo: a deep blue to
navy macOS-style tile, a white isometric cube with an orange orbit ring
around it (behind the cube at the back, in front of it at the bottom) and a
small white chat bubble with blue dots for the assistant. Every shape is a
signed distance field, so edges are antialiased at any size.

Runs with Blender's own Python (it ships numpy), from the repository root:

    /Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup \
        --python scripts/make_icon.py -- scripts/assets/BlenderCraft.png
"""

import math
import struct
import sys
import zlib
from pathlib import Path

import numpy as np

SIZE = 1024
SS = 2  # supersampling factor for the final downscale

# Blender's palette: orange, blue, white, on a deep blue to navy tile.
TILE_TOP = (38, 87, 135)  # #265787
TILE_BOTTOM = (16, 28, 52)
ORANGE = (245, 121, 42)  # #F5792A
ORANGE_DARK = (232, 125, 13)  # #E87D0D
BLUE = (86, 128, 194)  # #5680C2
WHITE = (255, 255, 255)
CUBE_LEFT = (214, 225, 240)
CUBE_RIGHT = (170, 190, 220)
EDGE = (20, 36, 64)


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
        """color: an RGB triple (0-255) or an (n, n, 3) array of them."""
        a = cov * opacity
        col = np.asarray(color, dtype=np.float32) / 255.0
        out_a = a + self.alpha * (1.0 - a)
        safe = np.where(out_a > 0, out_a, 1.0)
        self.rgb = (col * a[..., None] + self.rgb * (self.alpha * (1.0 - a))[..., None]) / safe[..., None]
        self.alpha = out_a

    def fill(self, sd, color):
        self.paint(self.coverage(sd), color)

    def stroke(self, sd_unsigned, width, color):
        self.paint(self.coverage(sd_unsigned - width / 2.0), color)


def iso_cube(cx, cy, edge):
    """Faces of an isometric cube whose front vertical edge starts at (cx, cy)."""
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
    }


def sd_ellipse_ring(x, y, cx, cy, rx, ry, angle):
    """Approximate distance to a rotated ellipse's outline, and the local
    vertical coordinate (positive on the near, lower half)."""
    c, s = math.cos(angle), math.sin(angle)
    u = (x - cx) * c + (y - cy) * s
    v = -(x - cx) * s + (y - cy) * c
    f = np.sqrt((u / rx) ** 2 + (v / ry) ** 2)
    grad = np.sqrt((u / rx**2) ** 2 + (v / ry**2) ** 2) / np.maximum(f, 1e-6)
    return np.abs(f - 1.0) / np.maximum(grad, 1e-6), v


def draw(n):
    cv = Canvas(n)
    x, y = cv.x, cv.y
    centre = SIZE / 2

    # Tile: macOS icon grid (824 px of 1024), soft drop shadow underneath.
    half, radius = 412.0, 185.0
    tile = sd_round_box(x, y, centre, centre, half, half, radius)
    shadow = sd_round_box(x, y, centre, centre + 12, half, half, radius)
    cv.paint(np.clip(1.0 - (shadow + 4.0) / 26.0, 0.0, 1.0) ** 2, (0, 0, 0), opacity=0.3)
    t = np.clip((y - (centre - half)) / (2 * half), 0.0, 1.0)[..., None]
    gradient = np.array(TILE_TOP) * (1.0 - t) + np.array(TILE_BOTTOM) * t
    cv.fill(tile, gradient)

    # The orbit: a tilted orange ring around the cube.
    cube_cx, cube_cy, edge = 484.0, 596.0, 232.0
    ring, near = sd_ellipse_ring(x, y, cube_cx, cube_cy - edge / 2 + 14, 342.0, 116.0, math.radians(-16))
    ring_width = 54.0
    back = cv.coverage(ring - ring_width / 2) * (near < 0)
    cv.paint(back, ORANGE_DARK)

    # The cube: white top, light blue sides, thin navy edges.
    cube = iso_cube(cube_cx, cube_cy, edge)
    cv.fill(sd_polygon(x, y, cube["left"]), CUBE_LEFT)
    cv.fill(sd_polygon(x, y, cube["right"]), CUBE_RIGHT)
    cv.fill(sd_polygon(x, y, cube["top"]), WHITE)
    t_, r_, c_, l_ = cube["top"]
    _, _, b_, _ = cube["left"]
    edges = np.minimum(sd_polyline(x, y, [l_, c_, r_]), sd_segment(x, y, c_, b_))
    cv.stroke(edges, 10.0, EDGE)
    cv.stroke(np.abs(sd_polygon(x, y, cube["silhouette"])), 14.0, EDGE)

    # The near half of the orbit passes in front of the cube.
    front = cv.coverage(ring - ring_width / 2) * (near >= 0)
    cv.paint(front, ORANGE)

    # Assistant chat bubble, top right: white with Blender-blue dots.
    bx, by, bw, bh, br = 718.0, 262.0, 116.0, 82.0, 60.0
    body = sd_round_box(x, y, bx, by, bw, bh, br)
    tail = sd_polygon(x, y, [(bx - 74, by + 40), (bx - 118, by + 128), (bx - 8, by + 72)])
    cv.fill(np.minimum(body, tail), WHITE)
    for i in (-1, 0, 1):
        cv.fill(sd_circle(x, y, bx + i * 44.0, by, 16.0), BLUE)

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
