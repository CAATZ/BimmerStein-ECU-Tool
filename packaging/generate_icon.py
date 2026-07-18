"""Build the Windows multi-resolution icon from the supplied PNG artwork."""

from __future__ import annotations

import struct
from pathlib import Path

from PyQt5.QtCore import QBuffer, QIODevice, Qt
from PyQt5.QtGui import QImage


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "bimmerstein_ecu_tool.png"
OUTPUT = ROOT / "assets" / "bimmerstein_ecu_tool.ico"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def _png_bytes(image: QImage) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError("Qt could not encode an icon image as PNG")
    return bytes(buffer.data())


def clean_source_metadata() -> QImage:
    """Rewrite the canonical PNG from pixels only, discarding all metadata."""
    source = QImage(str(SOURCE))
    if source.isNull():
        raise RuntimeError(f"could not read icon source: {SOURCE}")
    cleaned = _png_bytes(source.convertToFormat(QImage.Format_ARGB32))
    SOURCE.write_bytes(cleaned)
    result = QImage.fromData(cleaned, "PNG")
    if result.isNull():
        raise RuntimeError("could not reload the metadata-clean icon source")
    return result


def build_icon() -> None:
    source = clean_source_metadata()

    images: list[tuple[int, bytes]] = []
    for size in SIZES:
        # The supplied artwork is almost square; crop its transparent/black
        # canvas centrally so every Windows icon frame is square and crisp.
        scaled = source.scaled(
            size,
            size,
            Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation,
        )
        left = max(0, (scaled.width() - size) // 2)
        top = max(0, (scaled.height() - size) // 2)
        images.append((size, _png_bytes(scaled.copy(left, top, size, size))))

    directory = bytearray(struct.pack("<HHH", 0, 1, len(images)))
    payload = bytearray()
    offset = 6 + 16 * len(images)
    for size, data in images:
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

    OUTPUT.write_bytes(directory + payload)
    print(f"cleaned {SOURCE} ({SOURCE.stat().st_size} bytes)")
    print(f"wrote {OUTPUT} ({OUTPUT.stat().st_size} bytes)")


if __name__ == "__main__":
    build_icon()
