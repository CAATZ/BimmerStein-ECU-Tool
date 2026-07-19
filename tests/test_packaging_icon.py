from __future__ import annotations

import importlib.util
import struct
from pathlib import Path

import pytest

pytest.importorskip("PyQt5", reason="PyQt5 not available")
from PyQt5.QtGui import QImage
from PyQt5.QtSvg import QSvgRenderer


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "bimmerstein_ecu_tool.svg"
PNG = ROOT / "assets" / "bimmerstein_ecu_tool.png"
ICON = ROOT / "assets" / "bimmerstein_ecu_tool.ico"


def _icon_builder():
    path = ROOT / "packaging" / "generate_icon.py"
    spec = importlib.util.spec_from_file_location("bimmerstein_icon_builder", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ico_frames(path: Path) -> dict[tuple[int, int], bytes]:
    data = path.read_bytes()
    reserved, image_type, count = struct.unpack_from("<HHH", data)
    assert (reserved, image_type) == (0, 1)

    frames: dict[tuple[int, int], bytes] = {}
    for index in range(count):
        entry = 6 + (index * 16)
        width_byte, height_byte = struct.unpack_from("<BB", data, entry)
        payload_size, payload_offset = struct.unpack_from("<II", data, entry + 8)
        size = (width_byte or 256, height_byte or 256)
        frames[size] = data[payload_offset : payload_offset + payload_size]
    return frames


def _png_image(payload: bytes) -> QImage:
    image = QImage.fromData(payload, "PNG")
    assert not image.isNull()
    return image.convertToFormat(QImage.Format_ARGB32)


def test_icon_source_is_the_white_variant_of_the_canonical_vector():
    svg = SOURCE.read_text(encoding="utf-8")
    assert 'viewBox="0 0 428 427"' in svg
    assert "#ffffff" in svg
    assert "#e8e8e8" in svg
    assert "#b8b8b8" in svg
    assert "#ff5964" not in svg
    assert "#e5484d" not in svg
    assert "#a81228" not in svg


def test_png_and_windows_icon_frames_match_the_canonical_svg(qtbot):
    builder = _icon_builder()
    renderer = QSvgRenderer(str(SOURCE))
    assert renderer.isValid()

    assert _png_image(PNG.read_bytes()) == _png_image(
        builder._render_png(renderer, builder.PNG_SIZE)
    )
    frames = _ico_frames(ICON)
    assert set(frames) == {(size, size) for size in builder.SIZES}
    for size in builder.SIZES:
        assert _png_image(frames[(size, size)]) == _png_image(
            builder._render_png(renderer, size)
        )


def test_icon_keeps_transparent_rounded_corners():
    image = QImage(str(PNG))
    assert not image.isNull()
    assert image.pixelColor(0, 0).alpha() == 0
    assert image.pixelColor(image.width() // 2, image.height() // 2).alpha() == 255
