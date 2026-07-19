"""Render the canonical BimmerStein ECU Tool vector into app icon assets."""

from __future__ import annotations

import os
import struct
from pathlib import Path

from PyQt5.QtCore import QByteArray, QBuffer, QIODevice
from PyQt5.QtGui import QGuiApplication, QImage, QPainter
from PyQt5.QtSvg import QSvgRenderer


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "bimmerstein_ecu_tool.svg"
PNG_OUTPUT = ROOT / "assets" / "bimmerstein_ecu_tool.png"
ICON_OUTPUT = ROOT / "assets" / "bimmerstein_ecu_tool.ico"
PNG_SIZE = 1024
SIZES = (16, 24, 32, 48, 64, 128, 256)


def _render_png(renderer: QSvgRenderer, size: int) -> bytes:
    image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    renderer.render(painter)
    painter.end()

    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError(f"could not encode {size}x{size} icon frame")
    return bytes(data)


def build_icon() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QGuiApplication.instance() or QGuiApplication([])
    renderer = QSvgRenderer(str(SOURCE))
    if not renderer.isValid():
        raise RuntimeError(f"could not load icon source: {SOURCE}")

    PNG_OUTPUT.write_bytes(_render_png(renderer, PNG_SIZE))
    frames = [(size, _render_png(renderer, size)) for size in SIZES]

    directory = bytearray(struct.pack("<HHH", 0, 1, len(frames)))
    payload = bytearray()
    offset = 6 + 16 * len(frames)
    for size, data in frames:
        dimension = 0 if size == 256 else size
        directory.extend(
            struct.pack(
                "<BBBBHHII",
                dimension,
                dimension,
                0,
                0,
                1,
                32,
                len(data),
                offset,
            )
        )
        payload.extend(data)
        offset += len(data)

    ICON_OUTPUT.write_bytes(directory + payload)
    app.processEvents()
    print(f"wrote {PNG_OUTPUT} ({PNG_SIZE}x{PNG_SIZE})")
    print(
        f"wrote {ICON_OUTPUT} "
        f"({', '.join(f'{size}px' for size in SIZES)})"
    )


if __name__ == "__main__":
    build_icon()
