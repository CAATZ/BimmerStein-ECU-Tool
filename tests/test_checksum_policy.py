from checksum import correct_checksums, checksum_status, verify_checksum
from tests.conftest import ref


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
