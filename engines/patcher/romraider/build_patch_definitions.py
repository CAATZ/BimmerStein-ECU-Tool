#!/usr/bin/env python3
"""Inject the current MS41.2/MS41.3 patch controls into a RomRaider definition.

The input definition is kept byte-for-byte apart from the three target ROM
blocks.  SS1v2 and the 24 KB ID12 use calibration-relative addresses; the
256 KB ID12 receives the ECU's bank-XOR full-read mapping.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import xml.etree.ElementTree as ET


HERE = Path(__file__).resolve().parent
DEFAULT_FRAGMENT = HERE / "ms412_ignition_cut_v7_launch_control_v4.xml"
BEGIN = "<!-- OPENMS41 V7/V4 PATCH TABLES BEGIN -->"
END = "<!-- OPENMS41 V7/V4 PATCH TABLES END -->"
LEGACY_MARKERS = [
    (
        "<!-- OPENMS41 V6/V4 PATCH TABLES BEGIN -->",
        "<!-- OPENMS41 V6/V4 PATCH TABLES END -->",
    ),
    (
        "<!-- OPENMS41 V6/V3 PATCH TABLES BEGIN -->",
        "<!-- OPENMS41 V6/V3 PATCH TABLES END -->",
    ),
]
ROM_RE = re.compile(r"<rom\b[^>]*>.*?</rom>", re.DOTALL | re.IGNORECASE)
ADDRESS_RE = re.compile(r'(storageaddress\s*=\s*")0x([0-9a-f]+)(")', re.IGNORECASE)


def full_read_address(storage_address: int) -> int:
    """Map a 24 KB calibration storage address to a 256 KB full-read offset."""
    return (0x10000 + int(storage_address)) ^ 0x4000


def _metadata(block: str) -> tuple[str, str]:
    xmlid = re.search(r"<xmlid>\s*([^<]+?)\s*</xmlid>", block, re.IGNORECASE)
    filesize = re.search(r"<filesize>\s*([^<]+?)\s*</filesize>", block, re.IGNORECASE)
    return (
        xmlid.group(1).strip() if xmlid else "",
        filesize.group(1).strip().lower() if filesize else "",
    )


def _tables_only(fragment: str) -> str:
    # The standalone fragment's opening usage comment does not belong inside
    # each ROM. Keep the actual table XML and its descriptions unchanged.
    return re.sub(r"^\s*<!--.*?-->\s*", "", fragment, count=1, flags=re.DOTALL)


def _for_full_read(fragment: str) -> str:
    def replace(match: re.Match[str]) -> str:
        mapped = full_read_address(int(match.group(2), 16))
        return f'{match.group(1)}0x{mapped:X}{match.group(3)}'

    return ADDRESS_RE.sub(replace, fragment)


def _payload(fragment: str, *, full_read: bool) -> str:
    tables = _tables_only(fragment).strip()
    if full_read:
        tables = _for_full_read(tables)
    address_note = (
        "256 KB full-read addresses use fo(SA) = (0x10000 + SA) XOR 0x4000."
        if full_read
        else "24 KB calibration-relative addresses."
    )
    return (
        f"{BEGIN}\n"
        "<!-- Ignition Cut V7 + Launch Control V4 controls. "
        f"{address_note} -->\n"
        f"{tables}\n"
        f"{END}"
    )


def inject_definition(source: str, fragment: str) -> str:
    """Return *source* with one current patch block in each supported ROM."""
    wanted = {("SS1v2", "24kb"), ("12", "24kb"), ("12", "256kb")}
    found: set[tuple[str, str]] = set()

    def inject(block_match: re.Match[str]) -> str:
        block = block_match.group(0)
        key = _metadata(block)
        if key not in wanted:
            return block
        found.add(key)
        replacement = _payload(fragment, full_read=(key[1] == "256kb"))
        for begin, end in [(BEGIN, END), *LEGACY_MARKERS]:
            marked = re.compile(
                re.escape(begin) + r".*?" + re.escape(end),
                re.DOTALL,
            )
            if marked.search(block):
                return marked.sub(replacement, block, count=1)
        if "Ignition Cut - Switch Input" in block or "LC - Switch / Mode" in block:
            raise ValueError(
                f"{key[0]} {key[1]} already contains unmarked patch tables; "
                "remove the old definitions before rebuilding"
            )
        return re.sub(r"\s*</rom>\s*$", f"\n\n{replacement}\n\n</rom>", block)

    result = ROM_RE.sub(inject, source)
    missing = sorted(wanted - found)
    if missing:
        raise ValueError(f"definition is missing target ROM blocks: {missing}")

    # Validate the generated artifact as a complete XML definition, including
    # its internal DTD. ElementTree accepts the DOCTYPE declarations in source.
    ET.fromstring(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--fragment", type=Path, default=DEFAULT_FRAGMENT)
    args = parser.parse_args()

    source = args.source.read_text(encoding="utf-8-sig")
    fragment = args.fragment.read_text(encoding="utf-8")
    result = inject_definition(source, fragment)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(result, encoding="utf-8", newline="")
    print(f"wrote {args.output} ({len(result.encode('utf-8'))} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
