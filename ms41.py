"""
ms41.py — BMW MS41 ROM image utilities (offline file analysis).

Static helpers for identifying an MS41 .bin image: variant detection, CAL ID,
and DME ECU ID.  Used by the GUI, ROM analyzer, backup manager and config
editor — all of which operate on file images, not the live ECU.

LIVE ECU COMMUNICATION IS DS2 (see ds2.py).  MS41 predates BMW's KWP2000
adoption; the earlier KWP2000/K-Line implementation here (and kline.py) was the
wrong protocol for MS41 and has been removed.

Flash chip: Intel 28F200 (or compatible Am29F200), 2 Mbit / 256 KB, PSOP44 —
electrically erasable/programmable in-circuit.  Bench recovery uses a PSOP44
programmer.

ROM layout:
  Full ROM    : 0x000000–0x03FFFF  (256 KB)
  Tune region : 0x014000–0x019FFF  (24 KB calibration/tune area)

Identification (verified against real dumps + RomRaider MS41 definitions):
  CAL ID  — ASCII at 0x1400E (full ROM) / 0x0000E (24 KB tune file); first two
            chars identify the family (60=MS41.1, 12=MS41.2, 41/42/59/85=MS41.0).
  MS41.3  — shares the "12" CAL ID prefix with MS41.2. Calibration-side evidence is
            "SS1" at 0x173BB or "ABHISHEK" at 0x11F60; program-side evidence is the
            exact 9a116390 signature at 0x39A9A. Program/cal are resolved independently.
  ECU ID  — 7 ASCII digits at 0x6025 (full ROM), e.g. "1437806".
  VIN     — 13 bytes of 6-bit packing at 0x5D07 (full ROM), in the protected
            bootloader block (0x4000-0x5FFF) so it survives reflashing.  This is
            the same data DS2 read_mem(0x1D07, 13) returns from a live ECU
            (DS2 0x1D07 maps to file 0x5D07 via the 0x4000 block swap).
"""

import identity

# CAL ID location and variant mapping (RomRaider MS41 definitions, verified).
CALID_ADDR_256K = 0x1400E   # full 256 KB ROM
CALID_ADDR_24K  = 0x0000E   # 24 KB tune-region file
CALID_VARIANT = {
    "60": "MS41.1",
    "12": "MS41.2",
    "41": "MS41.0",
    "42": "MS41.0",
    "59": "MS41.0",
    "85": "MS41.0",
}

# MS41.3 is identified by an "SS1.." marker (it shares the "12" CAL ID prefix
# with MS41.2).  Verified: "SS1v2" at 0x173BB on an MS41.3 S52 dump.
MS41_3_MARKER_256K   = 0x173BB
MS41_3_MARKER_24K    = 0x033BB
MS41_3_MARKER_PREFIX = b"SS1"

# BMW DME ECU ID — 7 ASCII digits at file offset 0x6025 (full ROM only).
ECU_ID_ADDR = 0x6025

# VIN — 13 bytes, 6-bit packed (4 chars per 3 bytes), at file offset 0x5D07 in a
# full ROM (= DS2 address 0x1D07 via the 0x4000 block swap; see ds2.read_vin).
# Only present in a 256 KB full ROM (it lives in the program/bootloader region,
# not the 24 KB tune).  Decodes to the 17-character VIN.
VIN_ADDR  = identity.VIN_OFF
VIN_BYTES = identity.VIN_LEN

# MS41.3 calibration-resident identity marker. File 0x11F60 =
# DS2 0x15F60 = cal 0x5F60, inside the tune partition (DS2 0x10000-0x15FFF), so a tune write
# overwrites it. It is useful for calibration identification but cannot identify
# the program half. For program identification, use
# MS41_3_PROG_CODE_RANGE below (a genuine program-region marker).
MS41_3_PROG_MARKER_ADDR   = 0x11F60
MS41_3_PROG_MARKER_STRING = b"ABHISHEK"
# The legacy name above is retained for compatibility; this string is calibration-resident.
MS41_3_CREDIT_MARKER_256K = MS41_3_PROG_MARKER_ADDR
MS41_3_CREDIT_MARKER_24K  = 0x05F60

# Program-region MS41.3 marker: SS1v2 fills the program
# tail 0x39A9A-0x39B69 with code where stock MS41.2 leaves 0xFF.  It lives in the PROGRAM
# sector (SA5/6) so a cal/tune flash never touches it — unlike ABHISHEK it identifies the
# PROGRAM half and survives a cal reflash.  Ends at 0x39B6A, exactly where our patch caves
# begin, so the marker does not collide with the patch caves.
MS41_3_PROG_CODE_RANGE = (0x39A9A, 0x39B6A)   # [lo, hi) file offsets
MS41_3_PROG_CODE_MIN   = 64                    # non-FF bytes needed to call it SS1v2 (.2->0, .3->208)

# ★ EXACT SS1v2 program signature — the first 4 bytes of the SS1v2 program-tail code.
# MS41.3 = 9a116390; MS41.1 ALSO fills 0x39A9A (with e6fc7c1b...), so the coarse
# non-FF count in has_ss1v2_program mislabels a .1 ROM as .3. The exact signature
# is the reliable program-half discriminator (verified: .3 matches, .0/.1/.2 do not).
SS1V2_PROG_SIG      = bytes.fromhex("9a116390")
SS1V2_PROG_SIG_ADDR = 0x39A9A

# ECU ID strings that map to known variants (for detect_program_variant).
_PROG_ECU_ID_MAP = {
    "1437806": "MS41.1", "1438068": "MS41.1",
    "1406464": "MS41.2",
    "1429861": "MS41.0", "1432401": "MS41.0",
    "1429373": "MS41.0", "1438137": "MS41.0",
}

# Calibration-family label per variant (MS41.2 and MS41.3 share "ID12").
_VARIANT_FAMILY = {
    "MS41.0": "ID41", "MS41.1": "ID60",
    "MS41.2": "ID12", "MS41.3": "ID12",
}

# The protected boot/parameter region exposes the live three-byte coding-family
# value at DS2 0x1CF4 (= file 0x5CF4).  When that region is preserved during a
# cross-variant full write, the same value must be copied into the target
# program descriptors and calibration headers before checksums are corrected.
CODING_FAMILY_DS2_ADDR = 0x1CF4
CODING_FAMILY_FILE_ADDR = 0x5CF4
CODING_FAMILY_PROGRAM_ADDRS = (0x6006, 0x6012, 0x601E)
CODING_FAMILY_CAL_ADDRS = (0x1400D, 0x14017, 0x14027, 0x14037)


class MS41ECU:
    """MS41 ROM-image utilities (static).  Not a live-ECU interface — see ds2.py."""

    FULL_ROM_SIZE = 256 * 1024   # 262144 bytes  (Intel 28F200)
    TUNE_SIZE     = 24  * 1024   # 24576  bytes
    TUNE_OFFSET   = 0x014000     # file-order base of the DENSE cal block (first 16 KB only)
    TUNE_DS2_BASE = 0x010000     # the ECU tune partition is CPU/DS2-order @ 0x10000-0x15FFF
    CODING_FAMILY_DS2_ADDR = CODING_FAMILY_DS2_ADDR
    CODING_FAMILY_FILE_ADDR = CODING_FAMILY_FILE_ADDR

    @staticmethod
    def graft_coding_family(target: bytes, source_family: bytes) -> bytearray:
        """Make a target ROM compatible with a preserved source boot region.

        ``source_family`` is the live three-byte value read at DS2 0x1CF4.
        The full triplet is repeated in three program descriptors; its final
        digit prefixes four calibration records.  This is required only when a
        conversion preserves the ECU's boot/parameter region.  A true boot
        overwrite must keep the target ROM's own internally consistent values.
        """
        if len(target) != MS41ECU.FULL_ROM_SIZE:
            raise ValueError(
                f"coding-family graft expects a {MS41ECU.FULL_ROM_SIZE} B full ROM, "
                f"got {len(target)}"
            )
        family = bytes(source_family)
        if len(family) != 3 or not family.isdigit():
            raise ValueError(
                "live coding-family value must be exactly three ASCII digits"
            )
        output = bytearray(target)
        for address in CODING_FAMILY_PROGRAM_ADDRS:
            output[address:address + 3] = family
        for address in CODING_FAMILY_CAL_ADDRS:
            output[address] = family[2]
        return output

    @staticmethod
    def tune_from_full(full: bytes) -> bytes:
        """Carve the 24 KB tune partition out of a FILE-order full ROM.

        The ECU tune partition is CPU/DS2-order (DS2 0x10000-0x15FFF) — the same layout
        ds2.read_partial returns and RomRaider's cal-relative storageaddress expects.
        Because file = CPU XOR 0x4000 per 16 KB block, it is NOT a contiguous file slice:
        the two 16 KB halves are block-swapped.  A plain full[0x14000:0x1A000] slice
        silently drops the last 8 KB (extended AlphaN 16x20 @0x4048/0x4188/0x42C8 +
        SS1v2 O2/lambda/signature) and pads it with 0xFF.
        """
        if len(full) != MS41ECU.FULL_ROM_SIZE:
            raise ValueError(f"expected a {MS41ECU.FULL_ROM_SIZE} B full ROM, got {len(full)}")
        return bytes(full[(MS41ECU.TUNE_DS2_BASE + i) ^ 0x4000] for i in range(MS41ECU.TUNE_SIZE))

    @staticmethod
    def tune_into_full(full: bytes, partial: bytes) -> bytearray:
        """Merge a 24 KB CPU/DS2-order tune partition back into a FILE-order full ROM
        (exact inverse of tune_from_full)."""
        if len(partial) != MS41ECU.TUNE_SIZE:
            raise ValueError(f"expected a {MS41ECU.TUNE_SIZE} B tune partition, got {len(partial)}")
        out = bytearray(full)
        for i in range(MS41ECU.TUNE_SIZE):
            out[(MS41ECU.TUNE_DS2_BASE + i) ^ 0x4000] = partial[i]
        return out

    @staticmethod
    def read_calid(data: bytes):
        """Return the ASCII CAL ID (e.g. "60011110"), or None if not found.

        Located at 0x1400E in a 256 KB full ROM, 0x000E in a 24 KB tune file.
        A valid CAL ID is printable ASCII whose first two characters are digits.
        """
        for addr in (CALID_ADDR_256K, CALID_ADDR_24K):
            if addr + 8 <= len(data):
                chunk = bytes(data[addr:addr + 8])
                if all(0x30 <= b <= 0x7E for b in chunk) and chunk[:2].isdigit():
                    return chunk.decode("ascii")
        return None

    @staticmethod
    def detect_variant(data: bytes):
        """Detect 'MS41.0' / '.1' / '.2' / '.3' from a ROM or tune image, or None.

        MS41.3 is checked first via its SS1 marker because it shares MS41.2's
        "12" CAL ID prefix.
        """
        marker_addrs = ((MS41_3_MARKER_256K, MS41_3_CREDIT_MARKER_256K)
                        if len(data) >= MS41ECU.FULL_ROM_SIZE else
                        (MS41_3_MARKER_24K, MS41_3_CREDIT_MARKER_24K))
        for addr in marker_addrs[:1]:
            if addr + len(MS41_3_MARKER_PREFIX) <= len(data) and \
               bytes(data[addr:addr + len(MS41_3_MARKER_PREFIX)]) == MS41_3_MARKER_PREFIX:
                return "MS41.3"
        # Second independent calibration-side marker. Either cal marker identifies an
        # MS41.3 calibration; requiring both would reject a legitimate custom tune that
        # overwrote one string. The program signature is resolved independently.
        for addr in marker_addrs[1:]:
            marker = MS41_3_PROG_MARKER_STRING
            if addr + len(marker) <= len(data) and bytes(data[addr:addr + len(marker)]) == marker:
                return "MS41.3"
        calid = MS41ECU.read_calid(data)
        if calid:
            return CALID_VARIANT.get(calid[:2])
        return None

    @staticmethod
    def read_ecu_id(data: bytes):
        """Return the 7-digit BMW DME ECU ID from a ROM image, or None."""
        if len(data) >= ECU_ID_ADDR + 7:
            s = bytes(data[ECU_ID_ADDR:ECU_ID_ADDR + 7])
            if s.isdigit():
                return s.decode("ascii")
        return None

    @classmethod
    def vin_from_image(cls, data: bytes):
        """Decode the 17-character VIN from a 256 KB full-ROM image, or None.

        The VIN is stored 6-bit packed at 0x5D07 (= DS2 0x1D07).  Returns None
        for tune-only files, truncated images, or an unprogrammed/invalid VIN
        field.  Mirrors ds2.DS2Interface._decode_vin so an offline full-ROM
        backup yields the same VIN a live read would.
        """
        return identity.decode_vin(data)

    @staticmethod
    def has_ss1v2_program(data: bytes) -> bool:
        """True if the program tail carries SS1v2 code — the genuine MS41.3 PROGRAM marker.

        Stock MS41.2 leaves MS41_3_PROG_CODE_RANGE (file 0x39A9A-0x39B69) as 0xFF padding;
        SS1v2 fills it with ~208 B of code.  It lives in the program sector (SA5/6), so a
        cal/tune flash never touches it — this identifies the PROGRAM half, not the cal.
        """
        lo, hi = MS41_3_PROG_CODE_RANGE
        if len(data) < hi:
            return False
        return sum(1 for i in range(lo, hi) if data[i] != 0xFF) >= MS41_3_PROG_CODE_MIN

    @staticmethod
    def has_ss1v2_program_sig(data: bytes) -> bool:
        """True only for a genuine MS41.3 program — exact SS1v2 signature match.

        Unlike has_ss1v2_program (a non-FF byte count that MS41.1 also trips), this
        matches the exact 4 bytes 9a116390 at 0x39A9A, which only MS41.3 carries.
        """
        a = SS1V2_PROG_SIG_ADDR
        return len(data) >= a + len(SS1V2_PROG_SIG) and \
            bytes(data[a:a + len(SS1V2_PROG_SIG)]) == SS1V2_PROG_SIG

    @staticmethod
    def detect_program_variant(data: bytes):
        """Identify the PROGRAM half of a 256 KB full ROM (variant) — program-region only.

        MS41.3 is detected by the genuine program-region SS1v2 marker (has_ss1v2_program:
        SS1v2 code in the program tail that stock MS41.2 leaves 0xFF); every other variant
        maps the ECU ID (file 0x6025, boot/param).  Both are true program-region reads,
        unaffected by a calibration reflash, so unlike the calibration-resident marker this
        identifies the program half.

        (MS41.3 shares MS41.2's ECU ID 1406464, so the ECU-ID fallback alone would report .3
        programs as .2 — the program-code marker is what separates them.)

        Returns 'MS41.0', 'MS41.1', 'MS41.2', 'MS41.3', or None.
        """
        if len(data) < MS41ECU.FULL_ROM_SIZE:
            return None
        if MS41ECU.has_ss1v2_program_sig(data):
            return "MS41.3"
        return _PROG_ECU_ID_MAP.get(MS41ECU.read_ecu_id(data))

    @staticmethod
    def check_hybrid(data: bytes):
        """Check a 256 KB full ROM for a program / calibration variant mismatch.

        A hybrid ROM pairs program + calibration from incompatible variants.  Cross-FAMILY
        pairings (MS41.0/.1 vs .2/.3) brick the ECU; an MS41.2↔MS41.3 mismatch mis-runs (the
        .3 program expects SS1v2 cal features the .2 cal lacks, and vice-versa).

        This catches cross-family mismatches through the ECU ID and MS41.2/MS41.3
        mismatches through the program-region SS1v2 marker versus the calibration marker.

        Returns a human-readable description string if a mismatch is detected,
        or None if the ROM is internally consistent (or cannot be identified).
        """
        if len(data) < MS41ECU.FULL_ROM_SIZE:
            return None
        prog_v = MS41ECU.detect_program_variant(data)   # genuine program-region read
        cal_v  = MS41ECU.detect_variant(data)           # cal (SS1 marker / CAL ID)
        if prog_v is None or cal_v is None:
            return None
        if prog_v == cal_v:
            return None
        pf = _VARIANT_FAMILY.get(prog_v, "?")
        cf = _VARIANT_FAMILY.get(cal_v,  "?")
        kind = "cross-family (brick risk)" if pf != cf else "MS41.2/.3 program-cal mismatch"
        return (f"Program region: {prog_v} ({pf})  —  "
                f"Calibration region: {cal_v} ({cf})  [{kind}]")

    @staticmethod
    def looks_cpu_order(data: bytes) -> bool:
        """Detect a full image saved in CPU order instead of standard file order.

        A standard full ROM carries its CAL ID at file 0x1400E. In a CPU-order image the
        per-16 KB GAL swap moves it to 0x1000E. Such an image must not be passed through the
        normal file->CPU transform again.
        """
        if len(data) != MS41ECU.FULL_ROM_SIZE:
            return False

        def _is_cal_id(offset):
            value = bytes(data[offset:offset + 8])
            return (len(value) == 8 and value[:2].isdigit()
                    and all(0x30 <= byte <= 0x7E for byte in value))

        return _is_cal_id(0x1000E) and not _is_cal_id(0x1400E)

    @staticmethod
    def resolve_version(data: bytes) -> dict:
        """The one canonical version read every tab consumes.

        program : program-half variant (exact SS1v2 sig -> ECU-ID fallback)
        cal     : cal-half variant (SS1 marker -> CAL-ID family)
        hybrid  : human-readable mismatch description, or None if consistent
        ecu_id / cal_id / vin : identity strings (None if not present)
        """
        return {
            "program": MS41ECU.detect_program_variant(data),
            "cal":     MS41ECU.detect_variant(data),
            "hybrid":  MS41ECU.check_hybrid(data),
            "ecu_id":  MS41ECU.read_ecu_id(data),
            "cal_id":  MS41ECU.read_calid(data),
            "vin":     MS41ECU.vin_from_image(data),
        }
