"""Generates the SkyFish logo files in this folder.

Run from the SkyFish folder:  uv run python assets/make_logo.py

Drawn in the style of the Westbound Designs logo: one colour (#00AEEF),
even monoline strokes with round ends, sharp geometric shapes, a wave line,
and an optional thin circle. The fish faces left (westbound) and its tail is
the Westbound arrow's three nested chevrons; the wave below it puts the fish
in the sky.

Writes:
  skyfish_mark.svg / .png       the fish alone (transparent background)
  skyfish_icon.svg / .png       the fish in a circle (like logo_with_circle_nowords)
  skyfish_logo.svg / .png       the fish with "SkyFish / Code Analytics"
  skyfish.ico                   the circle icon, for the app window and taskbar

The words are converted to outlines, so the SVGs look the same on any
machine whether or not it has the font.
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QByteArray, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QFont, QGuiApplication, QImage, QPainter, QPainterPath  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

HERE = Path(__file__).parent
BLUE = "#00AEEF"   # the Westbound Designs blue
STROKE = 19        # main line weight, in mark units (the mark is drawn in a 512 box)
RING = 10          # the circle's weight - thinner, as in the Westbound circle logo
FONT = "Arial"     # closest common match to the Westbound wordmark's grotesque


# --- the mark (drawn in a 512 x 512 box) ---------------------------------

def _polyline(points) -> str:
    return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in points)


def _wave(x0: float, x1: float, y: float, amplitude: float, humps: int) -> str:
    """A smooth sine line from x0 to x1 - the Westbound wave."""
    steps = 120
    points = [(x0 + (x1 - x0) * i / steps,
               y - amplitude * math.sin(math.pi * humps * i / steps)) for i in range(steps + 1)]
    return _polyline(points)


def mark_paths() -> list[str]:
    """Stroke paths for the fish and the wave (no fill)."""
    nose, top, joint, bottom = (104, 236), (226, 148), (330, 236), (226, 324)
    body = f"M{nose[0]},{nose[1]} L{top[0]},{top[1]} L{joint[0]},{joint[1]} L{bottom[0]},{bottom[1]} Z"

    # Tail: the Westbound arrow's nested chevrons, each opening to the right,
    # the first one hinged on the body.
    tail = []
    for vertex_x, arm in ((330, 104), (370, 74), (410, 44)):
        d = arm / math.sqrt(2)
        tail.append(_polyline([(vertex_x + d, 236 - d), (vertex_x, 236), (vertex_x + d, 236 + d)]))

    wave = _wave(118, 398, 384, 11, 6)
    return [body, *tail, wave]


def mark_svg(offset=(0, 0), scale=1.0) -> str:
    ox, oy = offset
    strokes = "\n    ".join(f'<path d="{d}"/>' for d in mark_paths())
    # On the body's centre line, clear of the head's edges.
    eye = '<circle cx="152" cy="234" r="11" fill="{0}" stroke="none"/>'.format(BLUE)
    return (f'<g transform="translate({ox} {oy}) scale({scale})" fill="none" stroke="{BLUE}" '
            f'stroke-width="{STROKE}" stroke-linecap="round" stroke-linejoin="round">\n'
            f'    {strokes}\n    {eye}\n  </g>')


# --- text as outlines ----------------------------------------------------

def text_path(text: str, x: float, baseline: float, pixel_size: float, weight=QFont.Normal) -> str:
    font = QFont(FONT)
    font.setPixelSize(round(pixel_size))
    font.setWeight(weight)
    path = QPainterPath()
    path.addText(QPointF(x, baseline), font, text)
    return _path_to_svg(path)


def _path_to_svg(path: QPainterPath) -> str:
    parts = []
    i = 0
    n = path.elementCount()
    while i < n:
        e = path.elementAt(i)
        if e.type == QPainterPath.MoveToElement:
            parts.append(f"M{e.x:.2f},{e.y:.2f}")
            i += 1
        elif e.type == QPainterPath.LineToElement:
            parts.append(f"L{e.x:.2f},{e.y:.2f}")
            i += 1
        else:  # CurveTo + two CurveToData
            c1, c2, end = path.elementAt(i), path.elementAt(i + 1), path.elementAt(i + 2)
            parts.append(f"C{c1.x:.2f},{c1.y:.2f} {c2.x:.2f},{c2.y:.2f} {end.x:.2f},{end.y:.2f}")
            i += 3
    return " ".join(parts) + " Z"


# --- files -----------------------------------------------------------------

def svg(width: float, height: float, body: str) -> str:
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            f'width="{width}" height="{height}">\n  {body}\n</svg>\n')


def build() -> dict[str, str]:
    files = {}
    files["skyfish_mark.svg"] = svg(512, 512, mark_svg())

    ring = (f'<circle cx="256" cy="256" r="{256 - RING / 2 - 2}" fill="none" '
            f'stroke="{BLUE}" stroke-width="{RING}"/>')
    # Inside the circle the mark is a little smaller, centred.
    files["skyfish_icon.svg"] = svg(512, 512, ring + "\n  " + mark_svg(offset=(40, 46), scale=0.84))

    # Logo: the mark, with the words lower right, as in the Westbound logo.
    # The mark is moved up so there's no empty band above it.
    name = text_path("SkyFish", 476, 262, 150, QFont.Medium)
    tagline = text_path("Code Analytics", 482, 352, 74)
    words = f'<path d="{name}" fill="{BLUE}"/>\n  <path d="{tagline}" fill="{BLUE}"/>'
    # 1012 wide: ends just after the words, so nothing placed beside the logo
    # (the app's version label) sits behind a blank margin.
    files["skyfish_logo.svg"] = svg(1012, 380, mark_svg(offset=(0, -118)) + "\n  " + words)
    return files


def render_png(svg_text: str, out: Path, width: int) -> QImage:
    renderer = QSvgRenderer(QByteArray(svg_text.encode()))
    size = renderer.defaultSize()
    height = round(width * size.height() / size.width())
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter, QRectF(0, 0, width, height))
    painter.end()
    image.save(str(out))
    return image


def main() -> None:
    QGuiApplication(sys.argv[:1])
    files = build()
    for name, text in files.items():
        (HERE / name).write_text(text, encoding="utf-8")
    render_png(files["skyfish_mark.svg"], HERE / "skyfish_mark.png", 512)
    icon = render_png(files["skyfish_icon.svg"], HERE / "skyfish_icon.png", 512)
    render_png(files["skyfish_logo.svg"], HERE / "skyfish_logo.png", 1476)
    icon.scaled(256, 256, Qt.KeepAspectRatio, Qt.SmoothTransformation).save(str(HERE / "skyfish.ico"))
    print("Wrote:", ", ".join(sorted([*files, "skyfish_mark.png", "skyfish_icon.png",
                                      "skyfish_logo.png", "skyfish.ico"])))


if __name__ == "__main__":
    main()
