#!/usr/bin/env python3
"""Build standalone or combined RomRaider definitions for current patches.

Standalone output contains only BimmerStein patch controls.  Combined output
keeps the input definition byte-for-byte apart from the target ROM blocks.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import xml.etree.ElementTree as ET


HERE = Path(__file__).resolve().parent
DEFAULT_FRAGMENT = HERE / "ms412_ignition_cut_v7_launch_control_v4.xml"
DEFAULT_OUTPUT = HERE / "BimmerStein MS41 Patch Definitions.xml"
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

DTD = """<!DOCTYPE roms [
<!ELEMENT roms ( rom+ ) >
<!ELEMENT rom ( romid, table+ ) >
<!ATTLIST rom base CDATA #IMPLIED>
<!ELEMENT romid ( xmlid, internalidaddress, internalidstring, caseid?, ecuid, year, market, make, model, submodel, transmission, flashmethod?, memmodel, filesize, obsolete? ) >
<!ELEMENT xmlid (#PCDATA) >
<!ELEMENT internalidaddress (#PCDATA) >
<!ELEMENT internalidstring (#PCDATA) >
<!ELEMENT caseid (#PCDATA) >
<!ELEMENT ecuid (#PCDATA) >
<!ELEMENT year (#PCDATA) >
<!ELEMENT market (#PCDATA) >
<!ELEMENT make (#PCDATA) >
<!ELEMENT model (#PCDATA) >
<!ELEMENT submodel (#PCDATA) >
<!ELEMENT transmission (#PCDATA) >
<!ELEMENT flashmethod (#PCDATA) >
<!ELEMENT memmodel (#PCDATA) >
<!ELEMENT filesize (#PCDATA) >
<!ELEMENT obsolete (#PCDATA) >
<!ELEMENT table ( #PCDATA | scaling | table | description | data | state )* >
<!ATTLIST table type NMTOKENS #IMPLIED name CDATA #IMPLIED category CDATA #IMPLIED storagetype CDATA #IMPLIED endian (little | big) #IMPLIED sizex CDATA #IMPLIED sizey CDATA #IMPLIED userlevel (1 | 2 | 3 | 4 | 5) #IMPLIED logparam CDATA #IMPLIED storageaddress CDATA #IMPLIED >
<!ELEMENT scaling EMPTY >
<!ATTLIST scaling units CDATA #REQUIRED expression CDATA #REQUIRED to_byte CDATA #REQUIRED format CDATA #REQUIRED fineincrement CDATA #REQUIRED coarseincrement CDATA #REQUIRED >
<!ELEMENT description (#PCDATA) >
<!ELEMENT data (#PCDATA) >
<!ELEMENT state (#PCDATA) >
<!ATTLIST state name CDATA #REQUIRED data CDATA #REQUIRED >
]>"""

VANOS_TABLE_MS410 = """<table type="2D" name="VANOS Retrofit - Minimum RPM (Closed Throttle)" category="VANOS Retrofit" storagetype="uint8" sizey="1" storageaddress="0x3000">
  <scaling units="RPM" expression="x*32" to_byte="x/32" format="0" fineincrement="32" coarseincrement="320" />
  <table type="Static Y Axis" name="Engage Above" sizey="1"><data>RPM</data></table>
  <description>Minimum RPM for closed-throttle VANOS engagement added by the tested, checksum-correct MS41.0 VANOSRT3 retrofit. Raw 0xFF preserves stock behavior; use only after that patch is installed.</description>
</table>"""

VANOS_TABLE_MS411 = """<table type="2D" name="VANOS Retrofit - Minimum RPM (Closed Throttle)" category="VANOS Retrofit" storagetype="uint8" sizey="1" storageaddress="0x3720">
  <scaling units="RPM" expression="x*32" to_byte="x/32" format="0" fineincrement="32" coarseincrement="320" />
  <table type="Static Y Axis" name="Engage Above" sizey="1"><data>RPM</data></table>
  <description>Minimum RPM for closed-throttle VANOS engagement added by the MS41.1 VANOSRT2 retrofit. Raw 0xFF preserves stock behavior; use only after that patch is installed.</description>
</table>"""

MS410_ADDRESS_MAP = {
    0x2A65: 0x3010, 0x2A66: 0x3011,
    **{0x352C + index: 0x3020 + index for index in range(8)},
}
MS411_ADDRESS_MAP = {
    0x2A65: 0x3700, 0x2A66: 0x3701,
    **{0x352C + index: 0x3710 + index for index in range(8)},
}
MS413_ADDRESS_MAP = {
    0x352C + index: 0x47E0 + index for index in range(8)
}

IGNITION_LAUNCH_VARIANTS = (
    ("BIMMERSTEIN_MS413_SS1V2_24K", "33BB", "SS1v2", "SHINDE1", "MS41.3 SS1v2 + BimmerStein patches (24KB)", "24kb", False, MS413_ADDRESS_MAP),
    ("BIMMERSTEIN_MS413_SS1V2_256K", "173BB", "SS1v2", "SHINDE1", "MS41.3 SS1v2 + BimmerStein patches (256KB)", "256kb", True, MS413_ADDRESS_MAP),
    ("BIMMERSTEIN_MS412_ID12_24K", "E", "12", "1406464", "MS41.2 ID12 + BimmerStein patches (24KB)", "24kb", False, None),
    ("BIMMERSTEIN_MS412_ID12_256K", "1400E", "12", "1406464", "MS41.2 ID12 + BimmerStein patches (256KB)", "256kb", True, None),
    ("BIMMERSTEIN_MS410_ID41_24K", "E", "41", "1429861", "MS41.0 1429861 + BimmerStein patches (24KB)", "24kb", False, MS410_ADDRESS_MAP),
    ("BIMMERSTEIN_MS410_ID41_256K", "1400E", "41", "1429861", "MS41.0 1429861 + BimmerStein patches (256KB)", "256kb", True, MS410_ADDRESS_MAP),
    ("BIMMERSTEIN_MS411_ID60_24K", "E", "60", "1437806", "MS41.1 1437806 + BimmerStein patches (24KB)", "24kb", False, MS411_ADDRESS_MAP),
    ("BIMMERSTEIN_MS411_ID60_256K", "1400E", "60", "1437806", "MS41.1 1437806 + BimmerStein patches (256KB)", "256kb", True, MS411_ADDRESS_MAP),
)

VANOS_VARIANTS = (
    ("BIMMERSTEIN_MS410_VANOSRT3_24K", "3008", "VANOSRT3", "1429861", "MS41.0 1429861 + VANOSRT3 (24KB)", "24kb", False, MS410_ADDRESS_MAP, VANOS_TABLE_MS410),
    ("BIMMERSTEIN_MS410_VANOSRT3_256K", "17008", "VANOSRT3", "1429861", "MS41.0 1429861 + VANOSRT3 (256KB)", "256kb", True, MS410_ADDRESS_MAP, VANOS_TABLE_MS410),
    ("BIMMERSTEIN_MS411_VANOSRT2_24K", "3728", "VANOSRT2", "1437806", "MS41.1 1437806 + VANOSRT2 (24KB)", "24kb", False, MS411_ADDRESS_MAP, VANOS_TABLE_MS411),
    ("BIMMERSTEIN_MS411_VANOSRT2_256K", "17728", "VANOSRT2", "1437806", "MS41.1 1437806 + VANOSRT2 (256KB)", "256kb", True, MS411_ADDRESS_MAP, VANOS_TABLE_MS411),
)


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


def _remap_addresses(fragment: str, address_map: dict[int, int] | None) -> str:
    if not address_map:
        return fragment

    def replace(match: re.Match[str]) -> str:
        address = int(match.group(2), 16)
        mapped = address_map.get(address, address)
        return f'{match.group(1)}0x{mapped:X}{match.group(3)}'

    return ADDRESS_RE.sub(replace, fragment)


def _payload(
    fragment: str,
    *,
    full_read: bool,
    address_map: dict[int, int] | None = None,
) -> str:
    tables = _tables_only(fragment).strip()
    tables = _remap_addresses(tables, address_map)
    if full_read:
        tables = _for_full_read(tables)
    address_note = (
        "256 KB full-read addresses use fo(SA) = (0x10000 + SA) XOR 0x4000."
        if full_read
        else "24 KB calibration-relative addresses."
    )
    return (
        f"{BEGIN}\n"
        "<!-- Ignition Cut V7 + current Launch Control controls. "
        f"{address_note} -->\n"
        f"{tables}\n"
        f"{END}"
    )


def _rom(
    *, xmlid: str, id_address: str, id_string: str, ecuid: str,
    submodel: str, filesize: str, tables: str,
) -> str:
    return f"""<rom>
  <romid>
    <xmlid>{xmlid}</xmlid>
    <internalidaddress>{id_address}</internalidaddress>
    <internalidstring>{id_string}</internalidstring>
    <ecuid>{ecuid}</ecuid>
    <year>1996-1999</year>
    <market>All</market>
    <make>BMW</make>
    <model>E36/E39/Z3</model>
    <submodel>{submodel}</submodel>
    <transmission>MT/AT</transmission>
    <memmodel>80C166W-M-T3</memmodel>
    <filesize>{filesize}</filesize>
  </romid>
{tables}
</rom>"""


def build_standalone_definition(fragment: str) -> str:
    """Return a complete definition containing only patch-added calibrations."""
    patch_tables = _tables_only(fragment).strip()
    roms = []
    # Marker-specific blocks come first so a VANOS-patched partial/full image
    # wins over the generic CAL-ID entry and exposes all applicable controls.
    for (
        xmlid, id_address, id_string, ecuid, submodel, filesize, full_read,
        address_map, vanos_table,
    ) in VANOS_VARIANTS:
        tables = f"{_remap_addresses(patch_tables, address_map)}\n\n{vanos_table}"
        if full_read:
            tables = _for_full_read(tables)
        roms.append(_rom(
            xmlid=xmlid, id_address=id_address, id_string=id_string,
            ecuid=ecuid, submodel=submodel, filesize=filesize, tables=tables,
        ))
    for (
        xmlid, id_address, id_string, ecuid, submodel, filesize, full_read,
        address_map,
    ) in IGNITION_LAUNCH_VARIANTS:
        tables = _remap_addresses(patch_tables, address_map)
        if full_read:
            tables = _for_full_read(tables)
        roms.append(_rom(
            xmlid=xmlid, id_address=id_address, id_string=id_string,
            ecuid=ecuid, submodel=submodel, filesize=filesize, tables=tables,
        ))
    result = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f"{DTD}\n"
        "<!-- Patch-only definition. Install the matching firmware patch before editing. -->\n"
        "<roms>\n\n"
        + "\n\n".join(roms)
        + "\n\n</roms>\n"
    )
    ET.fromstring(result)
    return result


def inject_definition(source: str, fragment: str) -> str:
    """Return *source* with one current patch block in each supported ROM."""
    wanted = {
        ("SS1v2", "24kb"): MS413_ADDRESS_MAP,
        ("12", "24kb"): None,
        ("12", "256kb"): None,
    }
    found: set[tuple[str, str]] = set()

    def inject(block_match: re.Match[str]) -> str:
        block = block_match.group(0)
        key = _metadata(block)
        if key not in wanted:
            return block
        found.add(key)
        replacement = _payload(
            fragment,
            full_read=(key[1] == "256kb"),
            address_map=wanted[key],
        )
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
    missing = sorted(set(wanted) - found)
    if missing:
        raise ValueError(f"definition is missing target ROM blocks: {missing}")

    # Validate the generated artifact as a complete XML definition, including
    # its internal DTD. ElementTree accepts the DOCTYPE declarations in source.
    ET.fromstring(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths", type=Path, nargs="*",
        help="standalone: [output]; combined: source output",
    )
    parser.add_argument("--fragment", type=Path, default=DEFAULT_FRAGMENT)
    parser.add_argument(
        "--standalone", action="store_true",
        help=f"write a patch-only definition (default output: {DEFAULT_OUTPUT.name})",
    )
    args = parser.parse_args()

    fragment = args.fragment.read_text(encoding="utf-8")
    if args.standalone:
        if len(args.paths) > 1:
            parser.error("--standalone accepts at most one output path")
        output = args.paths[0] if args.paths else DEFAULT_OUTPUT
        result = build_standalone_definition(fragment)
    else:
        if len(args.paths) != 2:
            parser.error("combined mode requires source and output paths")
        source, output = args.paths
        result = inject_definition(source.read_text(encoding="utf-8-sig"), fragment)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(result, encoding="utf-8", newline="")
    print(f"wrote {output} ({len(result.encode('utf-8'))} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
