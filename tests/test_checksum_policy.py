import pytest

import checksum
from checksum import correct_checksums, checksum_status, verify_checksum
from engines.softbsl import checksum as softbsl_checksum
from tests.conftest import ref


def _native_program_crc(image, ends):
    """Independent bitwise CRC over CPU-addressed firmware scan intervals."""
    crc = int.from_bytes(image[0x6066:0x6068], "big")
    for start, end in zip((0x2100, 0x4000, 0x20000), ends):
        for address in range(start, end):
            crc ^= image[address ^ 0x4000]
            for _ in range(8):
                crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return crc


def _synthetic_program_image():
    image = bytearray(b"\xFF" * 0x40000)
    image[0x6066:0x6068] = bytes.fromhex("3130")
    for offset, end in zip((0x634E, 0x6352, 0x6356), (0x2200, 0x4100, 0x20100)):
        image[offset:offset + 4] = end.to_bytes(4, "little")
    for address, value in ((0x2100, 0x12), (0x4000, 0x34), (0x20000, 0x56)):
        image[address ^ 0x4000] = value
    return image


@pytest.mark.parametrize("gate", [0x30, 0xFF])
def test_program_crc_uses_declared_ranges_including_trailing_ff(gate):
    source = _synthetic_program_image()
    source[0x605C] = gate
    ends = (0x2200, 0x4100, 0x20100)
    expected = _native_program_crc(source, ends)

    # New code in any excluded gap must not extend the native CRC intervals.
    outside = source[:]
    for address in (*ends, 0x3FFFF):
        outside[address ^ 0x4000] ^= 1
    assert _native_program_crc(outside, ends) == expected
    before = bytes(outside)
    corrected, _ = correct_checksums(outside)
    assert int.from_bytes(corrected[0x6050:0x6052], "little") == expected
    assert bytes(outside) == before
    assert corrected[0x605C] == gate
    assert checksum_status(corrected)["program"] is True
    assert softbsl_checksum.correct_checksums(outside)[0] == corrected

    # The last FF in each covered interval is included, not trimmed away.
    for end in ends:
        inside = source[:]
        inside[(end - 1) ^ 0x4000] ^= 1
        changed = _native_program_crc(inside, ends)
        assert changed != expected
        corrected, _ = correct_checksums(inside)
        assert int.from_bytes(corrected[0x6050:0x6052], "little") == changed
        assert corrected[0x605C] == gate


@pytest.mark.parametrize("variant,ends", [
    ("MS41.0", (0x36AE, 0x5E80, 0x32816)),
    ("MS41.1", (0x3FF0, 0x5F02, 0x3F584)),
    ("MS41.2", (0x3FB4, 0x5F02, 0x3DA48)),
    ("MS41.3", (0x3FB4, 0x5F02, 0x3DA48)),
])
def test_private_families_match_native_program_ranges(variant, ends):
    source = bytearray(ref(variant))
    expected = _native_program_crc(source, ends)
    source[0x3FFFF ^ 0x4000] ^= 1
    corrected, _ = correct_checksums(source)
    assert int.from_bytes(corrected[0x6050:0x6052], "little") == expected
    assert corrected[0x605C] == source[0x605C]
    assert softbsl_checksum.correct_checksums(source)[0] == corrected


def test_softbsl_checksum_uses_canonical_implementation():
    assert softbsl_checksum.correct_checksums is checksum.correct_checksums
    assert softbsl_checksum._prog_calc is checksum._prog_calc


def test_invalid_program_bounds_refuse_correction_without_partial_changes():
    source = _synthetic_program_image()
    source[0x6356:0x635A] = (0x40001).to_bytes(4, "little")
    before = bytes(source)

    corrected, details = correct_checksums(source)

    assert bytes(corrected) == before
    assert bytes(source) == before
    assert any("refus" in line.lower() for line in details)
    assert checksum_status(source)["program"] is None
    assert verify_checksum(source)[0] is False
    with pytest.raises(ValueError):
        checksum._prog_calc(source)


def test_ms413_default_correction_updates_program_checksum():
    source = bytearray(ref("MS41.3"))
    original = bytes(source[0x6050:0x6052])

    image, _details = correct_checksums(source)

    assert image[0x6050:0x6052] != original
    assert checksum_status(image)["program"] is True


def test_ms413_disabled_program_mismatch_is_informational():
    image, _details = correct_checksums(
        bytearray(ref("MS41.3")), correct_program=False)
    status = checksum_status(image)

    assert status["boot"] is True
    assert status["cal"] is True
    assert status["prog_disabled"] is True
    ok, details = verify_checksum(image)
    assert ok is True
    assert any("MISMATCH (IGNORED" in line for line in details)


def test_disabled_program_gate_does_not_hide_boot_or_cal_failure():
    image, _details = correct_checksums(
        bytearray(ref("MS41.3")), correct_program=False)
    image[0x4000] ^= 0x01

    ok, _details = verify_checksum(image)

    assert ok is False


def test_enabled_program_mismatch_remains_invalid():
    image, _details = correct_checksums(bytearray(ref("MS41.2")))
    assert checksum_status(image)["prog_disabled"] is False
    image[0x6100] ^= 0x01

    ok, details = verify_checksum(image)

    assert ok is False
    assert any(line.endswith("MISMATCH") for line in details if line.startswith("Program"))
