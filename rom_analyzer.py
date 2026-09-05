"""
rom_analyzer.py — Offline BMW MS41 ROM/tune analyzer.

Data-driven: it loads a user-selected XML-format MS41 ECU definition,
matches the loaded .bin to the correct rom (FULL 256 KB vs PARTIAL 24 KB, and the
right variant incl. MS41.3) by internalidaddress + internalidstring + filesize,
resolves the inheritance chain, and lists every table — computing scalar values.

Identification (ECU ID, CAL ID, variant) and checksum state are reported for
256 KB images. If no definition is selected it still reports identity
and checksum, just without the table list.
"""

from dataclasses import dataclass
from typing import Optional, List, Tuple
from checksum import checksum_state, verify_checksum, bootloader_checksum_ok
from ms41 import MS41ECU
import romraider_defs as rrd

FULL_ROM_SIZE = 256 * 1024
TUNE_SIZE     = 24  * 1024
TUNE_BASE     = 0x14000     # the 24 KB tune space sits here inside a full ROM


def _extract(defs, rom, buf, disp_offset):
    """Extract (rows, n_scalars, n_maps) for a matched rom from `buf`.
    Values are read from `buf`; map addresses are shown with `disp_offset` added
    so a full-file view reports full-ROM offsets for the embedded tune tables."""
    merged = defs.resolve(rom)
    rows = []
    n_scalars = n_maps = 0
    for name, attrs in merged.items():
        if not attrs.get("storageaddress"):
            continue
        cat  = attrs.get("category", "Uncategorised")
        kind = rrd.classify(attrs)
        if kind == "scalar":
            val, unit, fmt = rrd.read_scalar(buf, attrs)
            rows.append((cat, name, rrd.fmt_value(val, fmt), unit)); n_scalars += 1
        elif kind == "switch":
            st = rrd.switch_state(buf, attrs)
            rows.append((cat, name, st if st is not None else "—", "switch")); n_scalars += 1
        else:
            sx = attrs.get("sizex") or "1"; sy = attrs.get("sizey") or "1"
            try:
                addr = f"0x{int(attrs['storageaddress'], 16) + disp_offset:05X}"
            except ValueError:
                addr = attrs.get("storageaddress", "")
            rows.append((cat, name, f"{sx}×{sy} map", addr)); n_maps += 1
    return rows, n_scalars, n_maps


@dataclass
class AnalysisResult:
    file_type:     str
    variant:       Optional[str]
    cal_id:        Optional[str]
    ecu_id:        Optional[str]
    matched_label: Optional[str]
    checksum:      str
    bootloader_ok: Optional[bool]
    cs_details:    List[str]
    params:        List[Tuple[str, str, str, str]]   # (category, name, value, unit/info)
    n_scalars:     int
    n_maps:        int
    warnings:      List[str]

    @property
    def cs_ok(self) -> bool:
        return self.checksum in ("enabled", "disabled")

    # Backwards-compatible view used by older callers/tests.
    @property
    def scalars(self) -> List[Tuple[str, str, str, str]]:
        return [(name, val, unit, cat) for (cat, name, val, unit) in self.params]


def analyze(data: bytes, definition_path=None) -> AnalysisResult:
    data  = bytearray(data)
    size  = len(data)
    warns: List[str] = []
    params: List[Tuple[str, str, str, str]] = []
    n_scalars = n_maps = 0

    if size == FULL_ROM_SIZE:
        file_type = "Full ROM (256 KB)"
    elif size == TUNE_SIZE:
        file_type = "Tune Region (24 KB)"
    else:
        file_type = f"Unknown ({size:,} bytes)"
        warns.append(f"File size {size:,} bytes is not a recognised MS41 image size.")

    ecu_id       = MS41ECU.read_ecu_id(data)
    cal_variant  = MS41ECU.detect_variant(data)           # from calibration region
    prog_variant = MS41ECU.detect_program_variant(data)   # from program region (full ROMs only)
    variant      = cal_variant                             # display value; updated below for hybrids

    # Hybrid detection — program and calibration from different variants.
    if size == FULL_ROM_SIZE and prog_variant and prog_variant != cal_variant:
        hybrid = MS41ECU.check_hybrid(data)
        if hybrid:
            warns.append(f"HYBRID ROM DETECTED — {hybrid}  "
                         "Flashing this image will brick the ECU.")
            variant = f"{prog_variant} (prog) + {cal_variant} (cal)"
    # MS41.3 shares the "12" CAL ID bytes with MS41.2 at 0x1400E; its real
    # identifier is "SS1v*" stored at 0x173BB (full ROM) or 0x033BB (24KB partial).
    if cal_variant == "MS41.3":
        for marker_addr in (0x173BB, 0x033BB):
            if marker_addr + 5 <= size:
                chunk = bytes(data[marker_addr:marker_addr + 5])
                if all(0x20 <= b <= 0x7E for b in chunk):
                    cal_id = chunk.decode("ascii").strip()
                    break
        else:
            cal_id = "SS"
    else:
        cal_id = MS41ECU.read_calid(data)

    # Checksum status (full ROM only)
    cs_state = checksum_state(data)
    bl_ok    = bootloader_checksum_ok(data)
    _, cs_details = verify_checksum(data)

    # ── XML definition match + table extraction ─────────────────────────
    matched_label = None
    try:
        defs = rrd.get_definitions(definition_path)
    except rrd.DefinitionError as exc:
        defs = None
        warns.append(f"The selected calibration definition could not be loaded: {exc}")
    if defs is None:
        if definition_path is None:
            warns.append(
                "No calibration definition is selected. Identity and checksum information "
                "is still available; use Load Definition to enable parameter matching."
            )
    else:
        rows = []
        # 1) Match the file as-is (FULL -> OBDII/readiness def; PARTIAL -> tune def).
        rom = defs.match(data)
        if rom is not None:
            r, ns, nm = _extract(defs, rom, data, 0)
            rows += r; n_scalars += ns; n_maps += nm

        # 2) For a FULL ROM, the 24 KB "tune space" lives at TUNE_BASE; match and
        #    extract it too so a full file exposes ALL the partial-file parameters
        #    (this is also what makes MS41.3 full files show their tune tables).
        trom = None
        if size == FULL_ROM_SIZE:
            # CPU/DS2-order descramble — NOT a file slice.  file = CPU XOR 0x4000 per 16 KB,
            # so data[0x14000:0x1A000] would drop the extended AlphaN + SS1v2 high-cal (last
            # 8 KB).  tune_from_full yields the live-tune-read layout (storageaddress == offset);
            # display tune addresses in CPU/mem space (0x10000 + storageaddress).
            slice_ = MS41ECU.tune_from_full(data)
            trom = defs.match(slice_)
            if trom is not None:
                r, ns, nm = _extract(defs, trom, slice_, MS41ECU.TUNE_DS2_BASE)
                rows += r; n_scalars += ns; n_maps += nm
            else:
                warns.append("Could not match the embedded 24 KB tune space to a "
                             "definition (unknown calibration).")

        # Build matched_label using submodel (friendly name) with xmlid in brackets.
        # For MS41.3, the tune space identity is primary — the program block shares
        # MS41.2's "12" xmlid which would be misleading as the lead entry.
        def _def_str(r):
            return f"{r.submodel}  [{r.xmlid}]"

        if cal_variant == "MS41.3":
            if trom is not None:
                # Cal region matched MS41.3 (SS1v* 24KB def) — use that as the lead entry.
                matched_label = _def_str(trom)
                if rom is not None and rom.xmlid != trom.xmlid:
                    if prog_variant == "MS41.3" or prog_variant is None:
                        # Normal MS41.3 full ROM: program is also MS41.3 but has no
                        # dedicated 256KB XML def (falls back to MS41.2's ID12).
                        matched_label += "  +  MS41.3 (256 KB) [definition unavailable]"
                    else:
                        # Hybrid: defs.match() is unreliable here because all 256KB
                        # definitions identify by the CAL ID at 0x1400E, not the
                        # program region.  Look up the 256KB def for the program
                        # variant directly by its CAL-family idstr.
                        _V_CALID = {"MS41.0": "41", "MS41.1": "60", "MS41.2": "12"}
                        prefix   = _V_CALID.get(prog_variant)
                        prog_rom = next(
                            (rd for rd in defs._roms
                             if rd.filesize == "256kb" and rd.idstr == prefix),
                            None) if prefix else None
                        if prog_rom is not None:
                            matched_label += f"  +  {_def_str(prog_rom)}"
                        else:
                            matched_label += (
                                f"  +  {prog_variant} (256 KB) [definition unavailable]"
                            )
            elif rom is not None:
                matched_label = _def_str(rom)
        else:
            if rom is not None:
                matched_label = _def_str(rom)
                if trom is not None and trom.xmlid != rom.xmlid:
                    matched_label += f"  +  {_def_str(trom)}"
            elif trom is not None:
                matched_label = _def_str(trom)

        if rom is None and size != FULL_ROM_SIZE:
            warns.append("No matching XML definition for this file "
                         "(unknown CAL ID or size).")
        params = sorted(rows, key=lambda r: (r[0].lower(), r[1].lower()))

    return AnalysisResult(
        file_type=file_type, variant=variant, cal_id=cal_id, ecu_id=ecu_id,
        matched_label=matched_label, checksum=cs_state, bootloader_ok=bl_ok,
        cs_details=cs_details, params=params, n_scalars=n_scalars, n_maps=n_maps,
        warnings=warns,
    )
