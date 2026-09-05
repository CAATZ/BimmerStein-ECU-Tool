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


def test_decode_firmware_version_does_not_strip_malformed_bytes():
    for raw in (b"x1437806", b"1437?806", b"1437806\x00"):
        assert ei.decode_firmware_version(raw) == "Unavailable"


def test_decode_transmission_automatic():
    assert ei.decode_transmission(bytes([0x80])) == "Automatic"


def test_decode_transmission_manual():
    assert ei.decode_transmission(bytes([0x00])) == "Manual"


def test_decode_transmission_empty_is_unavailable():
    assert ei.decode_transmission(b"") == "Unavailable"


def test_transmission_flag_address_is_program_family_specific():
    assert ei.transmission_flag_address("MS41.0") == 0xFD4C
    assert ei.transmission_flag_address("MS41.1") == 0xFD5C
    assert ei.transmission_flag_address("MS41.3") == 0xFD5C
    assert ei.transmission_flag_address(None) is None


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


def test_decode_full_isn_text_preserves_verification_gate():
    assert ei.decode_full_isn_text(_isn_block(serial=b"012345678"), "5678") == (
        "012345678 (ISN 5678)"
    )
    assert ei.decode_full_isn_text(_isn_block(serial=b"012349999"), "5678") == (
        "5678 (full serial unverified)"
    )


def test_format_new_fields_assembles_named_identity_fields():
    fields = ei.format_new_fields(
        fw_raw=b"1437806",
        isn_block=_isn_block(serial=b"012345678"),
        isn4_live="5678",
        chip_sig=bytes.fromhex("e00e0d58f04ec084"),
        trans_raw=bytes([0x00]),
    )
    assert fields == {
        "BMW Program Part Number": "1437806",
        "DME Production Serial": "012345678",
        "EWS2 ISN": "5678",
        "Flash Command-Set Driver": "AMD driver — 29F200 / 29F400 (bottom half)",
        "Transmission Mode": "Manual",
    }


def test_ident_and_aif_decode_exact_bmw_fields():
    ident = (
        b"1406464" + b"12" + b"34" + b"56" + b"78" + b"40" + b"97" +
        b"SIEMENS585" + b"10" + b"11" + b"012345678"
    )
    decoded = ei.decode_identification(ident)
    assert len(ident) == 42
    assert decoded == {
        "reported_identifier": "1406464",
        "bmw_hardware_number": "12",
        "coding_index": "34",
        "diagnostic_index": "56",
        "bus_index": "78",
        "manufacturing_week": "40",
        "manufacturing_year": "97",
        "supplier_number": "SIEMENS585",
        "software_index": "10",
        "change_index": "11",
        "dme_production_serial": "012345678",
    }

    record = bytearray(0x2E)
    record[0x0E:0x10] = bytes((0x39, 0xE1))  # 07.03.1997
    record[0x11:0x14] = (1407152).to_bytes(3, "big")
    record[0x1B:0x1E] = (1407151).to_bytes(3, "big")
    aif = bytes(record) + b"\xFF" * (ei.AIF_LEN - len(record))
    assert ei.decode_aif_history(aif, "MS41.3") == {
        "recorded_zb_zusb": "1407151",
        "programming_date": "07.03.1997",
        "recorded_software_number": "1407152",
        "programming_count": 1,
    }
    assert ei.decode_aif_history(aif, "MS41.1") == {}
    assert ei.format_daten_lineage("1407151", "1406464") == (
        "Type 1406680; program 1406464 C; calibration 1407152DA — "
        "matches live program"
    )


def test_aif_rejects_noncontiguous_programming_history():
    occupied = bytes(0x2E)
    raw = occupied + b"\xFF" * 0x2E + occupied + b"\xFF" * (ei.AIF_LEN - 0x8A)
    assert ei.decode_aif_history(raw, "MS41.2") == {}


def test_program_calibration_match_keeps_mismatch_visible():
    assert ei.format_program_calibration_match(b"0912", b"0912") == (
        "0912 / 0912 — Matched"
    )
    assert ei.format_program_calibration_match(b"0960", b"0641") == (
        "0960 / 0641 — Mismatch"
    )


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


def test_live_variant_resolver_recovers_all_families_from_independent_evidence():
    cases = (
        ({"cal_id": b"41000000", "program_part": b"1429861",
          "program_compat": b"0641", "calibration_compat": b"0641"}, "MS41.0"),
        ({"cal_id": b"60000000", "program_part": b"1437806",
          "program_compat": b"0960", "calibration_compat": b"0960"}, "MS41.1"),
        ({"cal_id": b"12000000", "program_part": b"1406464",
          "program_compat": b"0912", "calibration_compat": b"0912",
          "program_signature": b"\xFF" * 4}, "MS41.2"),
        ({"cal_id": b"12000000", "program_part": b"1406464",
          "program_compat": b"0912", "calibration_compat": b"0912",
          "program_signature": bytes.fromhex("9a116390")}, "MS41.3"),
    )
    for evidence, expected in cases:
        assert ei.resolve_live_variants("???????", **evidence) == (
            expected, expected, True)


def test_live_variant_resolver_keeps_conflicting_program_and_cal_evidence_visible():
    cal, program, consistent = ei.resolve_live_variants(
        "???????", cal_id=b"41000000", program_compat=b"0960",
    )
    assert (cal, program, consistent) == ("MS41.0", "MS41.1", False)
