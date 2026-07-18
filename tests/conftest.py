"""Discover optional private reference images used by integration tests.

Set ``MS41_TEST_DATA_ROOT`` to a directory containing the private reference
images. The repository does not assume a workstation layout or publish the
per-unit names of those files. Tests that need an unavailable image skip
cleanly, while CI-safe tests continue to run.

Reference identities are replaced in memory with explicit synthetic fixtures.
The serial and VIN fields are outside the checksummed ranges, so this preserves
the firmware and checksum behaviour exercised by the suite without exposing
the identity of a physical ECU.
"""

from __future__ import annotations

import os
from pathlib import Path
import re

import pytest


FULL_IMAGE_SIZE = 0x40000
PARTIAL_IMAGE_SIZE = 0x6000
SERIAL_OFF = 0x5CE5
SERIAL_NUL_OFF = 0x5CEE
VIN_OFF = 0x5D07
VIN_LEN = 13
_VIN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

SYNTHETIC_IDENTITIES = {
    "MS41.0": ("900000000", "WBAZZ0000TEST0000"),
    "MS41.1": ("900000001", "WBAZZ0000TEST0001"),
    "MS41.1b": ("900000002", "WBAZZ0000TEST0002"),
    "MS41.2": ("900000003", "WBAZZ0000TEST0003"),
    "MS41.3": ("900000004", "WBAZZ0000TEST0004"),
    "MS41.3clean": ("900000005", "WBAZZ0000TEST0005"),
    "MS41.2or3": ("900000006", "WBAZZ0000TEST0006"),
}


def _optional_data_root() -> Path | None:
    value = os.environ.get("MS41_TEST_DATA_ROOT", "").strip()
    if not value:
        return None
    root = Path(value).expanduser()
    return root if root.is_dir() else None


def _private_images(root: Path | None, size: int) -> list[Path]:
    if root is None:
        return []
    return sorted(
        path
        for path in root.rglob("*.bin")
        if path.is_file() and path.stat().st_size == size
    )


def _relative_text(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix().lower()


def _is_reference_directory(path: Path, variant: str, root: Path) -> bool:
    marker = f"ref_{variant.lower()}"
    return marker in _relative_text(path, root).split("/")


def _discover_full(key: str, root: Path | None, images: list[Path]) -> Path | None:
    if root is None:
        return None

    def matches(path: Path) -> bool:
        name = path.name.lower()
        rel = _relative_text(path, root)
        if key in {"MS41.0", "MS41.1", "MS41.2"}:
            return _is_reference_directory(path, key, root) and "full" in name
        if key == "MS41.1b":
            return (
                "1437806" in name
                and "full" in name
                and not _is_reference_directory(path, "MS41.1", root)
            )
        if key == "MS41.3":
            return (
                _is_reference_directory(path, key, root)
                and "stock" in name
                and "full" in name
                and "cksum" not in name
            )
        if key == "MS41.3clean":
            return "full" in name and "ref" in name and "ref_ms41.3/" not in rel
        if key == "MS41.2or3":
            return "1406464" in name and "full" in name
        return False

    candidates = [path for path in images if matches(path)]
    return candidates[0] if candidates else None


def _stem_tokens(path: Path) -> set[str]:
    ignored = {"full", "fullread", "partial", "read", "ref", "stock"}
    return {
        token
        for token in re.split(r"[^a-z0-9.]+", path.stem.lower())
        if token and token not in ignored
    }


def _discover_partial(full: Path | None, partials: list[Path]) -> Path | None:
    if full is None:
        return None
    siblings = [path for path in partials if path.parent == full.parent]
    if not siblings:
        return None
    full_tokens = _stem_tokens(full)
    return max(
        siblings,
        key=lambda path: (len(full_tokens & _stem_tokens(path)), str(path).lower()),
    )


def _encode_fixture_vin(vin: str) -> bytes:
    """Pack a synthetic VIN independently of the production identity module."""
    padded = "000" + vin
    output = bytearray()
    for start in range(0, len(padded), 4):
        value = 0
        for char in padded[start:start + 4]:
            value = (value << 6) | _VIN_CHARS.index(char)
        output.extend(((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF))
    return bytes(output[2:])


def _with_synthetic_identity(data: bytes, key: str) -> bytes:
    serial, vin = SYNTHETIC_IDENTITIES[key]
    output = bytearray(data)
    output[SERIAL_OFF:SERIAL_NUL_OFF] = serial.encode("ascii")
    output[SERIAL_NUL_OFF] = 0
    packed_vin = _encode_fixture_vin(vin)
    assert len(packed_vin) == VIN_LEN
    output[VIN_OFF:VIN_OFF + VIN_LEN] = packed_vin
    return bytes(output)


_DATA_ROOT = _optional_data_root()
_FULL_IMAGES = _private_images(_DATA_ROOT, FULL_IMAGE_SIZE)
_PARTIAL_IMAGES = _private_images(_DATA_ROOT, PARTIAL_IMAGE_SIZE)

REF_PATHS = {
    key: _discover_full(key, _DATA_ROOT, _FULL_IMAGES)
    for key in SYNTHETIC_IDENTITIES
}
REF_PARTIAL_PATHS = {
    key: _discover_partial(REF_PATHS[key], _PARTIAL_IMAGES)
    for key in SYNTHETIC_IDENTITIES
}


def ref(key):
    """Return a sanitized private full image or skip when it is unavailable."""
    path = REF_PATHS.get(key)
    if path is None or not path.is_file():
        pytest.skip(
            f"private reference image unavailable for {key}; "
            "set MS41_TEST_DATA_ROOT"
        )
    return _with_synthetic_identity(path.read_bytes(), key)


def ref_partial(key):
    """Return the real 24 KB partial paired with ``ref(key)`` when available."""
    path = REF_PARTIAL_PATHS.get(key)
    if path is None or not path.is_file():
        pytest.skip(
            f"private reference partial unavailable for {key}; "
            "set MS41_TEST_DATA_ROOT"
        )
    return path.read_bytes()
