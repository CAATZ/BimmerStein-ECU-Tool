"""Read-only identity, metadata, and byte comparison for catalogued MS41 images."""

from __future__ import annotations

import hashlib

from checksum import checksum_status
from ms41 import MS41ECU
import patch_service


_MAX_DISPLAY_RANGES = 200


def analyze_image(data: bytes, stored_sha256: str = "") -> dict:
    """Inspect current bytes without updating the catalogue baseline."""
    data = bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    stored = str(stored_sha256 or "").strip().lower()
    if not stored:
        identity = "NOT RECORDED (legacy entry)"
    elif stored == digest:
        identity = "VERIFIED"
    else:
        identity = "REPLACED OR MODIFIED"

    common = {
        "size": len(data),
        "sha256": digest,
        "catalog_sha256": stored,
        "identity": identity,
        "checksum": checksum_status(data),
        "patch_base": None,
        "installed_patches": None,
        "program_variant": None,
        "calibration_variant": None,
        "hybrid": None,
    }
    if len(data) not in (MS41ECU.TUNE_SIZE, MS41ECU.FULL_ROM_SIZE):
        return {
            **common,
            "type": "Unknown",
            "analysis_error": (
                f"unsupported Bin size {len(data):,}; "
                "expected 24,576 or 262,144 bytes"
            ),
        }
    if len(data) == MS41ECU.FULL_ROM_SIZE and MS41ECU.looks_cpu_order(data):
        return {
            **common,
            "type": "Full ROM (CPU order)",
            "analysis_error": (
                "CPU-order full ROM; use standard file order for detailed comparison"
            ),
        }

    if len(data) == MS41ECU.FULL_ROM_SIZE:
        resolved = MS41ECU.resolve_version(data)
        base_version = patch_service.base_version(data)
        patches = None if base_version is None else [
            {
                "id": item["id"],
                "title": item.get("title", ""),
                "version": item.get("version", ""),
            }
            for item in patch_service.available_patches(data)
            if item.get("installed")
        ]
        program = resolved["program"]
        cal = resolved["cal"]
        hybrid = resolved["hybrid"]
        image_type = "Full ROM"
    else:
        base_version = None
        patches = None
        program = None
        cal = MS41ECU.detect_variant(data)
        hybrid = None
        image_type = "Tune"

    return {
        **common,
        "type": image_type,
        "program_variant": program,
        "calibration_variant": cal,
        "hybrid": hybrid,
        "patch_base": base_version,
        "installed_patches": patches,
        "analysis_error": "",
    }


def support_metadata(entry) -> dict:
    """Return selected-Bin metadata with no filename, identity, notes, or raw bytes."""
    with open(entry.path, "rb") as stream:
        result = analyze_image(stream.read(), getattr(entry, "sha256", ""))
    source = str(getattr(entry, "source", "") or "").strip()
    if source:
        result["catalog_source"] = source
    return result


def compare_entries(first, second) -> str:
    """Build a bounded plain-text report for two catalogue entries."""
    with open(first.path, "rb") as stream:
        first_data = stream.read()
    with open(second.path, "rb") as stream:
        second_data = stream.read()

    first_info = analyze_image(first_data, getattr(first, "sha256", ""))
    second_info = analyze_image(second_data, getattr(second, "sha256", ""))
    lines = [
        "BIN COMPARISON (READ-ONLY)",
        "",
        f"A: {getattr(first, 'filename', 'First Bin')}",
        f"B: {getattr(second, 'filename', 'Second Bin')}",
        "",
        "IDENTITY",
    ]
    lines.extend(_identity_lines("A", first_info))
    lines.extend(_identity_lines("B", second_info))
    lines.extend(["", "IMAGE DETAILS"])
    lines.extend(_detail_lines("A", first_info))
    lines.extend(_detail_lines("B", second_info))
    errors = [
        f"{label}: {info['analysis_error']}"
        for label, info in (("A", first_info), ("B", second_info))
        if info["analysis_error"]
    ]
    lines.extend(["", "BYTE DIFFERENCES"])
    if errors:
        lines.append("Unavailable: " + "; ".join(errors))
        return "\n".join(lines)

    left, right, base, scope = _comparison_bytes(first_data, second_data)
    changed, range_count, ranges = _changed_ranges(left, right, base)
    lines.extend([
        f"Scope: {scope}",
        f"Changed bytes: {changed:,} of {len(left):,}",
        f"Changed ranges: {range_count:,}",
    ])
    if not ranges:
        lines.append("  None")
    else:
        lines.extend(f"  {item}" for item in ranges)
        if range_count > len(ranges):
            lines.append(
                f"  ... {range_count - len(ranges):,} more ranges omitted from display"
            )
    return "\n".join(lines)


def _comparison_bytes(first: bytes, second: bytes):
    full = MS41ECU.FULL_ROM_SIZE
    tune = MS41ECU.TUNE_SIZE
    sizes = {len(first), len(second)}
    if not sizes <= {full, tune}:
        raise ValueError("both Bins must be recognized 24 KB tunes or 256 KB full ROMs")
    if sizes == {full}:
        return first, second, 0, "Full-ROM file offsets"
    left = MS41ECU.tune_from_full(first) if len(first) == full else first
    right = MS41ECU.tune_from_full(second) if len(second) == full else second
    note = (
        "Calibration DS2 addresses (full ROM reduced to its tune region)"
        if sizes == {full, tune}
        else "Calibration DS2 addresses"
    )
    return left, right, MS41ECU.TUNE_DS2_BASE, note


def _changed_ranges(first: bytes, second: bytes, base: int):
    changed = 0
    range_count = 0
    displayed = []
    start = None
    last = None

    def finish_range():
        nonlocal range_count
        if start is None:
            return
        range_count += 1
        if len(displayed) < _MAX_DISPLAY_RANGES:
            begin = base + start
            end = base + last
            displayed.append(
                f"0x{begin:05X}" if begin == end else f"0x{begin:05X}-0x{end:05X}"
            )

    for offset, (left, right) in enumerate(zip(first, second)):
        if left == right:
            finish_range()
            start = last = None
            continue
        changed += 1
        if start is None:
            start = offset
        last = offset
    finish_range()
    return changed, range_count, displayed


def _identity_lines(label: str, info: dict):
    baseline = info["catalog_sha256"] or "not recorded"
    return [
        f"{label} current SHA-256: {info['sha256']}",
        f"{label} catalog SHA-256: {baseline}",
        f"{label} identity status: {info['identity']}",
    ]


def _detail_lines(label: str, info: dict):
    checksum = info["checksum"]
    checksum_text = ", ".join(
        f"{name}={_result(value)}"
        for name, value in (
            ("boot", checksum["boot"]),
            ("program", checksum["program"]),
            ("cal", checksum["cal"]),
        )
    )
    if checksum["prog_disabled"]:
        checksum_text += ", program-check=disabled"
    if checksum["cal_disabled"]:
        checksum_text += ", cal-check=disabled"

    patches = info["installed_patches"]
    if patches is None:
        patch_text = (
            "unavailable in a tune"
            if info["type"] == "Tune"
            else (
                "unavailable (patch base not recognized)"
                if info["type"] == "Full ROM"
                else "unavailable for this image"
            )
        )
    elif not patches:
        patch_text = "none detected"
    else:
        patch_text = ", ".join(
            item["id"] + (f" {item['version']}" if item["version"] else "")
            for item in patches
        )

    lines = [
        (
            f"{label}: {info['type']}, {info['size']:,} bytes, "
            f"program={info['program_variant'] or 'N/A'}, "
            f"calibration={info['calibration_variant'] or 'Unknown'}"
        ),
        f"{label} hybrid: {info['hybrid'] or 'none detected'}",
        f"{label} checksums: {checksum_text}",
        f"{label} installed patches: {patch_text}",
    ]
    if info["analysis_error"]:
        lines.append(f"{label} analysis: {info['analysis_error']}")
    return lines


def _result(value):
    if value is None:
        return "N/A"
    return "OK" if value else "MISMATCH"
