"""Graft live MS42/MS43 boot identity without replacing target firmware code.

The 1 KiB window must come from the raw RAM agent: stock reads mask 3F00..3F63.
Capture it before any flash mutation and retain the returned immutable target
for retries; never rebuild identity from partially erased/programmed flash.
Exact source: native identification MS42 002102 / MS43 0006C8, pairing restore
02951A / 0AECA0, and redundant boot hardware references 0011C2 / 002126.
"""

from ms42_ms43_image import FULL_SIZE


IDENTITY_OFFSET = 0x3C00
IDENTITY_SIZE = 0x400
PAIRING_POINTERS = {"MS42": (0x3C04, 0x3EDE), "MS43": (0x0250, 0x3ED4)}

# Native command 00 physical identity, XDF ECU Number and clear-immobilizer
# spans, complete fourteen-record AIF, and the native eight-byte HW triplet.
# Coding/software references and executable-region pointers remain the target's.
IDENTITY_GRAFT_RANGES = {
    "MS42": ((0x3C40, 0x3C4A), (0x3C4A, 0x3ECE), (0x3EDE, 0x3F20),
             (0x3F80, 0x3F88), (0x3F8C, 0x3F94), (0x3F98, 0x3F9A),
             (0x3F9C, 0x3F9E), (0x3FE8, 0x4000)),
    "MS43": ((0x3C36, 0x3C40), (0x3C40, 0x3EC4), (0x3ED4, 0x3F20),
             (0x3F80, 0x3F88), (0x3F8C, 0x3F94), (0x3F98, 0x3F9A),
             (0x3F9C, 0x3F9E), (0x3FE8, 0x4000)),
}


def graft_identity(target, live_window, family, *, pairing_pointer=None):
    """Return a full target with only verified identity ranges copied verbatim.

    For MS43, pairing_pointer is the additional four raw bytes at 0x0250.
    MS42's pointer is already inside live_window. Their effective 24-bit pointers
    must match the verified layouts; the unused high byte is not a fingerprint.
    The caller retains ordinary image/family/checksum preflight and controls the
    preserve-identity opt-out by skipping this function.
    """
    if family not in IDENTITY_GRAFT_RANGES:
        raise ValueError("Expected an MS42 or MS43 identity layout")
    if not isinstance(target, (bytes, bytearray, memoryview)) or len(target) != FULL_SIZE:
        raise ValueError("Identity grafting requires a complete 512 KiB target")
    if (not isinstance(live_window, (bytes, bytearray, memoryview))
            or len(live_window) != IDENTITY_SIZE):
        raise ValueError("Live identity requires the raw 1 KiB window at 0x3C00")
    target, live_window = bytes(target), bytes(live_window)
    pointer_at, pairing_at = PAIRING_POINTERS[family]
    if family == "MS42":
        relative = pointer_at - IDENTITY_OFFSET
        pairing_pointer = live_window[relative:relative + 4]
    if (not isinstance(pairing_pointer, (bytes, bytearray, memoryview))
            or len(pairing_pointer) != 4):
        raise ValueError("MS43 identity requires its raw four-byte pairing pointer at 0x0250")
    for pointer in (pairing_pointer, target[pointer_at:pointer_at + 4]):
        if int.from_bytes(pointer[:3], "little") != pairing_at:
            raise ValueError("Immobilizer data address differs from the verified firmware layout")

    # Stock CRC ranges end before identity. Reject a changed descriptor that
    # would make this otherwise checksum-neutral graft invalidate the boot CRC.
    start = int.from_bytes(target[0x3C28:0x3C2C], "little")
    end = int.from_bytes(target[0x3C2C:0x3C30], "little")
    ranges = IDENTITY_GRAFT_RANGES[family]
    if any(start < hi and end >= lo for lo, hi in ranges):
        raise ValueError("Boot checksum range overlaps live ECU identity")
    result = bytearray(target)
    for lo, hi in ranges:
        result[lo:hi] = live_window[lo - IDENTITY_OFFSET:hi - IDENTITY_OFFSET]
    return bytes(result)
