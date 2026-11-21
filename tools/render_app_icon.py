"""Render the KanaMemory launcher icon set (app/icons) from its hand-drawn geometry.

The sign is the kana ka drawn as three monoline strokes; its third stroke is a clockwise arc with an
arrowhead ("again"). Nothing comes from a font. One geometry on a 108-unit canvas (the Android adaptive-icon
grid) is written as the two source drawings (app/icons/source/*.svg) and rendered with Pillow
(supersampled, then reduced) into the PNG files that app/pyproject.toml's `icon = "icons/kanamemory"` names:

    kanamemory-square-<n>.png    legacy square icon (vermilion rounded square on transparent) and the splash
    kanamemory-round-<n>.png     legacy round icon (vermilion disc on transparent)
    kanamemory-adaptive-<n>.png  adaptive-icon foreground: a full-bleed vermilion square with the sign,
                                 scaled so it fills the launcher's visible 72-unit viewport as on the legacy icons

Usage: python tools/render_app_icon.py [--out app/icons]
"""

from __future__ import annotations

import argparse
import math
import struct
import zlib
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
VERMILION, WHITE, INK = "#E0452F", "#FFFFFF", "#1B1B1F"
STROKE = 7.25  # monoline stroke width on the 108-unit canvas
CANVAS = 108.0

# Icon variants and pixel sizes the pinned Briefcase Android template asks for (its briefcase.toml).
SIZES = {
    "square": (48, 72, 96, 144, 192, 320, 480, 640, 960, 1280),
    "round": (48, 72, 96, 144, 192),
    "adaptive": (108, 162, 216, 324, 432),
}
SQUARE_CORNER = 0.22  # corner radius of the legacy square icon, as a share of its side
# The adaptive foreground is 108 units, of which a launcher shows the central 72 (safe circle 66). The legacy
# icons show the whole 108-unit drawing, so the sign is scaled by 72/108 to look the same size on both.
ADAPTIVE_SCALE = 72.0 / 108.0

# ---- the sign (108-unit canvas, centre 54,54) ----
STROKE1 = [("M", (28.1, 48.85)), ("L", (53.3, 44.8)), ("C", (59.15, 43.9), (61.22, 46.96), (60.86, 52.72)),
           ("L", (59.96, 69.55)), ("C", (59.6, 76.12), (57.44, 79.18), (51.5, 77.2))]
STROKE2 = [("M", (48.35, 30.4)), ("C", (48.08, 44.8), (43.94, 61.9), (31.7, 80.35))]
ARC_CENTRE, ARC_RADIUS, ARC_START, ARC_END = (63.5, 47.5), 15.0, -100.0, 20.0  # degrees, clockwise
HEAD_BASE, HEAD_LENGTH = 1.95 * STROKE, 1.2 * STROKE
ARC_OVERLAP = 0.6  # the arc runs this far into the arrowhead so the two join without a seam


def _point(angle):
    a = math.radians(angle)
    return (ARC_CENTRE[0] + ARC_RADIUS * math.cos(a), ARC_CENTRE[1] + ARC_RADIUS * math.sin(a))


def arrowhead():
    """Triangle whose base is perpendicular to the arc's end tangent, pointing along the clockwise travel."""
    end = _point(ARC_END)
    t, n = math.radians(ARC_END + 90), math.radians(ARC_END)
    tip = (end[0] + HEAD_LENGTH * math.cos(t), end[1] + HEAD_LENGTH * math.sin(t))
    half = HEAD_BASE / 2
    return [tip, (end[0] + half * math.cos(n), end[1] + half * math.sin(n)),
            (end[0] - half * math.cos(n), end[1] - half * math.sin(n))]


def _fmt(value):
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def _path_data(commands):
    return " ".join(cmd + " " + " ".join(f"{_fmt(x)} {_fmt(y)}" for x, y in points) for cmd, *points in commands)


def svg(sign_colour, background=None):
    """The source drawing: the sign on the 108-unit canvas, with or without the vermilion field."""
    end = _point(ARC_END + math.degrees(ARC_OVERLAP / ARC_RADIUS))
    start = _point(ARC_START)
    head = arrowhead()
    field = f'  <rect id="background" width="108" height="108" fill="{background}"/>\n' if background else ""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 108 108" width="108" height="108">\n'
        "  <title>KanaMemory icon: the kana ka, its third stroke an arrow for again</title>\n"
        f"{field}"
        '  <g id="sign_ka_again">\n'
        f'    <g id="ka_strokes_1_2" fill="none" stroke="{sign_colour}" stroke-width="{_fmt(STROKE)}" '
        'stroke-linecap="round" stroke-linejoin="round">\n'
        f'      <path id="ka_stroke1" d="{_path_data(STROKE1)}"/>\n'
        f'      <path id="ka_stroke2" d="{_path_data(STROKE2)}"/>\n'
        "    </g>\n"
        f'    <g id="ka_stroke3_again" fill="{sign_colour}">\n'
        f'      <path id="again_arc" d="M {_fmt(start[0])} {_fmt(start[1])} A {_fmt(ARC_RADIUS)} {_fmt(ARC_RADIUS)} '
        f'0 0 1 {_fmt(end[0])} {_fmt(end[1])}" fill="none" stroke="{sign_colour}" stroke-width="{_fmt(STROKE)}" '
        'stroke-linecap="round"/>\n'
        f'      <path id="again_arrowhead" d="M {" L ".join(f"{_fmt(x)} {_fmt(y)}" for x, y in head)} Z" '
        f'stroke="{sign_colour}" stroke-width="1" stroke-linejoin="round"/>\n'
        "    </g>\n"
        "  </g>\n"
        "</svg>\n"
    )


def _flatten(commands, steps=48):
    points, current = [], None
    for cmd, *args in commands:
        if cmd in ("M", "L"):
            current = args[0]
            points.append(current)
        else:  # cubic Bezier
            (x1, y1), (x2, y2), (x3, y3) = args
            x0, y0 = current
            for i in range(1, steps + 1):
                t = i / steps
                u = 1 - t
                points.append((u**3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t**3 * x3,
                               u**3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t**3 * y3))
            current = (x3, y3)
    return points


def _polylines():
    arc_end = ARC_END + math.degrees(ARC_OVERLAP / ARC_RADIUS)
    count = 96
    arc = [_point(ARC_START + (arc_end - ARC_START) * i / count) for i in range(count + 1)]
    return [_flatten(STROKE1), _flatten(STROKE2), arc]


def _stroke(draw, pts, width, colour):
    """A round-capped, round-joined monoline stroke: a quad per segment and a disc at every vertex."""
    radius = width / 2
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        length = math.hypot(x1 - x0, y1 - y0)
        if length == 0:
            continue
        nx, ny = -(y1 - y0) / length * radius, (x1 - x0) / length * radius
        draw.polygon([(x0 + nx, y0 + ny), (x1 + nx, y1 + ny), (x1 - nx, y1 - ny), (x0 - nx, y0 - ny)], fill=colour)
    for x, y in pts:
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=colour)


def _draw_sign(draw, transform, scale, colour):
    for line in _polylines():
        _stroke(draw, [transform(p) for p in line], STROKE * scale, colour)
    head = [transform(p) for p in arrowhead()]
    draw.polygon(head, fill=colour)
    _stroke(draw, head + [head[0]], 1.0 * scale, colour)  # the drawing's 1-unit round-joined outline


def render(variant, size):
    """One icon PNG as an RGBA image of size x size pixels."""
    factor = 4 if size <= 512 else 2
    big = size * factor
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if variant == "square":
        draw.rounded_rectangle((0, 0, big - 1, big - 1), radius=round(big * SQUARE_CORNER), fill=VERMILION)
    elif variant == "round":
        draw.ellipse((0, 0, big - 1, big - 1), fill=VERMILION)
    else:
        draw.rectangle((0, 0, big, big), fill=VERMILION)
    sign_scale = ADAPTIVE_SCALE if variant == "adaptive" else 1.0
    unit = big / CANVAS * sign_scale

    def transform(point):
        return (big / 2 + (point[0] - 54) * unit, big / 2 + (point[1] - 54) * unit)

    _draw_sign(draw, transform, unit, WHITE)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def _with_srgb_chunk(data):
    """Insert an sRGB chunk (perceptual intent) after IHDR, so the colour space is stated."""
    chunk = b"sRGB" + b"\x00"
    insert = struct.pack(">I", 1) + chunk + struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF)
    ihdr_end = 8 + 4 + 4 + 13 + 4
    return data[:ihdr_end] + insert + data[ihdr_end:]


def write_png(image, path):
    from io import BytesIO

    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    path.write_bytes(_with_srgb_chunk(buffer.getvalue()))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "app/icons")
    args = parser.parse_args(argv)
    out = args.out
    (out / "source").mkdir(parents=True, exist_ok=True)
    for name, text in (("kanamemory_icon.svg", svg(WHITE, VERMILION)), ("kanamemory_icon_mono.svg", svg(INK))):
        (out / "source" / name).write_bytes(text.encode("utf-8"))
    for variant, sizes in SIZES.items():
        for size in sizes:
            write_png(render(variant, size), out / f"kanamemory-{variant}-{size}.png")
    print(f"Wrote {sum(len(s) for s in SIZES.values())} PNG files and 2 drawings to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
