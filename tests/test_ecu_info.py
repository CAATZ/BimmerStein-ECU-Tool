import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ecu_info as ei


def test_decode_flash_chip_amd():
    sig = bytes.fromhex("e00e0d58f04ec084")
    assert ei.decode_flash_chip(sig) == "AMD driver — 29F200 / 29F400 (bottom half)"


def test_decode_flash_chip_intel():
    sig = bytes.fromhex("e6f45000b84c6fe0")
    assert ei.decode_flash_chip(sig) == "Intel driver — 28F200"


def test_decode_flash_chip_unknown_never_guesses():
    sig = bytes.fromhex("0011223344556677")
    result = ei.decode_flash_chip(sig)
    assert result == "Unknown (unexpected signature: 0011223344556677)"


def test_decode_flash_chip_no_response():
    assert ei.decode_flash_chip(b"") == "Unknown (unexpected signature: no response)"


def test_decode_firmware_version_valid():
    raw = b"1437806"
    assert ei.decode_firmware_version(raw) == "1437806"


def test_decode_firmware_version_wrong_length_is_unavailable():
    assert ei.decode_firmware_version(b"14378") == "Unavailable"


def test_decode_firmware_version_non_digit_is_unavailable():
    assert ei.decode_firmware_version(b"14378?6") == "Unavailable"


def test_decode_transmission_automatic():
    assert ei.decode_transmission(bytes([0x80])) == "Automatic"


def test_decode_transmission_manual():
    assert ei.decode_transmission(bytes([0x00])) == "Manual"


def test_decode_transmission_empty_is_unavailable():
    assert ei.decode_transmission(b"") == "Unavailable"


def _isn_block(marker=b"1585", serial=b"012345678"):
    # ISN_BLOCK_ADDR=0x1CE0, serial starts at offset 5 (0x1CE5-0x1CE0) --
    # one filler byte between the 4-byte marker and the serial.
    block = bytearray(marker)
    block += b"\x00"
    block += serial
    return bytes(block)


def test_decode_full_isn_verified_shows_bold_last_four():
    block = _isn_block(serial=b"012345678")
    result = ei.decode_full_isn(block, isn4_live="5678")
    assert result == "01234<b>5678</b>"


def test_decode_full_isn_marker_mismatch_falls_back():
    block = _isn_block(marker=b"XXXX", serial=b"012345678")
    result = ei.decode_full_isn(block, isn4_live="5678")
    assert result.startswith("5678")
    assert "unverified" in result


def test_decode_full_isn_digit_mismatch_falls_back():
    block = _isn_block(serial=b"012349999")   # last 4 don't match isn4_live
    result = ei.decode_full_isn(block, isn4_live="5678")
    assert result.startswith("5678")
    assert "unverified" in result


def test_decode_full_isn_no_live_isn_and_bad_block_is_unavailable():
    block = _isn_block(marker=b"XXXX")
    assert ei.decode_full_isn(block, isn4_live="") == "Unavailable"


def test_format_new_fields_assembles_all_four_keys():
    fields = ei.format_new_fields(
        fw_raw=b"1437806",
        isn_block=_isn_block(serial=b"012345678"),
        isn4_live="5678",
        chip_sig=bytes.fromhex("e00e0d58f04ec084"),
        trans_raw=bytes([0x00]),
    )
    assert fields == {
        "Firmware Version": "1437806",
        "ISN": "01234<b>5678</b>",
        "Flash Chip": "AMD driver — 29F200 / 29F400 (bottom half)",
        "Transmission": "Manual",
    }


def test_decode_bank_marker_bottom():
    raw = bytes([0xA5, 0x5A, 0x42, 0x42 ^ 0xFF])
    assert ei.decode_bank_marker(raw) == "B"


def test_decode_bank_marker_top():
    raw = bytes([0xA5, 0x5A, 0x54, 0x54 ^ 0xFF])
    assert ei.decode_bank_marker(raw) == "T"


def test_decode_bank_marker_absent_or_invalid():
    assert ei.decode_bank_marker(b"\xFF\xFF\xFF\xFF") is None
    assert ei.decode_bank_marker(b"") is None
    assert ei.decode_bank_marker(bytes([0xA5, 0x5A, 0x42, 0x00])) is None  # bad checksum-complement


def test_chip_family_amd():
    assert ei.chip_family(bytes.fromhex("e00e0d58f04ec084")) == "amd"


def test_chip_family_intel():
    assert ei.chip_family(bytes.fromhex("e6f45000b84c6fe0")) == "intel"


def test_chip_family_unknown_is_none():
    assert ei.chip_family(bytes.fromhex("0011223344556677")) is None
    assert ei.chip_family(b"") is None


def test_image_chip_family_reads_file_order_driver_signature():
    image = bytearray(b"\xFF" * 0x40000)
    image[ei.DRV_SIG_FILE_OFFSET:ei.DRV_SIG_FILE_OFFSET + ei.DRV_SIG_LEN] = bytes.fromhex(
        "e00e0d58f04ec084")
    assert ei.image_chip_family(image) == "amd"

    image[ei.DRV_SIG_FILE_OFFSET:ei.DRV_SIG_FILE_OFFSET + ei.DRV_SIG_LEN] = bytes.fromhex(
        "e6f45000b84c6fe0")
    assert ei.image_chip_family(image) == "intel"


def test_image_chip_family_unknown_or_short_is_none():
    image = bytearray(b"\xFF" * 0x40000)
    assert ei.image_chip_family(image) is None
    assert ei.image_chip_family(b"\xFF" * ei.DRV_SIG_FILE_OFFSET) is None
