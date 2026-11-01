import struct

import pytest

import ms42_ms43_image as image


def _word(data, at, value):
    struct.pack_into("<H", data, at, value)


def _dword(data, at, value):
    struct.pack_into("<I", data, at, value)


def _descriptor(data, at, ranges):
    _word(data, at, 0)
    _word(data, at + 2, len(ranges))
    for index, (start, end) in enumerate(ranges):
        _dword(data, at + 4 + index * 8, start)
        _dword(data, at + 8 + index * 8, end)


def synthetic_image(family):
    profile = image.PROFILES[family]
    data = bytearray((i * 13 + 5) & 255 for i in range(image.FULL_SIZE))
    for hw, sw, suffix in ((profile.hw_offset, profile.sw_offset, "53"),
                           (profile.calibration_offset + 0x20,
                            profile.calibration_offset + 8, "00")):
        data[hw:hw + 8] = (profile.hw_prefix + suffix).encode()
        data[sw:sw + 6] = b"430069" if family == "MS43" else b"0110C6"
    _dword(data, 0x3C28, 0x100)
    _dword(data, 0x3C2C, 0x1FF)
    _word(data, 0x3FE6, 0x2D2D)
    if family == "MS42":
        _dword(data, 0x502CE, 0x50306)
        _dword(data, 0x502D2, 0x50032)
        _dword(data, 0x502F2, 0x4FEE0)
        _dword(data, 0x502F6, 0x4800C)
        _descriptor(data, 0x50306, [(0x11000, 0x110FF), (0x51000, 0x51080)])
        _descriptor(data, 0x4FEE0, [(0x48000, 0x4807F), (0x48100, 0x48142)])
    else:
        _dword(data, 0x6ED42, 0xEFFB6)
        _dword(data, 0x6ED5A, 0x7000C)
        _descriptor(data, 0x6FDE0, [(0x90000, 0x900FF), (0xEFDA0, 0xEFDDD)])
        _descriptor(data, 0x73FE0, [(0x70000, 0x72FFF), (0x74000, 0x7400F)])
        _dword(data, 0x6FDB2, 0xFFFFFFF0)
        _dword(data, 0x6FDB8, 0xA5A5A5A5)
        for at, value in ((0x6FDBE, 0xD0020), (0x6FDC2, 0xD0060),
                          (0x6FDC6, 0xE0020), (0x6FDCA, 0xE0040),
                          (0x6FDCE, 0x70080), (0x6FDD2, 0x70090),
                          (0x6FDD6, 0x700C0), (0x6FDDA, 0x700E0)):
            _dword(data, at, value)
    return data


def _bitwise_crc(data, initial):
    for byte in data:
        initial ^= byte
        for _ in range(8):
            initial = (initial >> 1) ^ (0xA001 if initial & 1 else 0)
    return initial


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_detect_identity_and_partial_layout(family):
    source = synthetic_image(family)
    profile = image.PROFILES[family]
    assert image.image_profile(source) == profile
    partial = source[profile.calibration_offset:profile.calibration_offset + profile.partial_size]
    assert image.image_profile(partial) == profile
    assert image.image_identity(source)["partial"] is False
    identity = image.image_identity(partial)
    assert identity["partial"] is True
    assert identity["hardware_id"] == profile.hw_prefix + "00"
    assert identity["software_id"] == image.image_identity(source)["software_id"]
    assert image.image_profile(bytes(len(source))) is None
    assert image.image_profile(source[:-1]) is None
    with pytest.raises(ValueError, match="identifier"):
        image.correct_checksums(source, "MS43" if family == "MS42" else "MS42")


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_correction_is_complete_idempotent_and_preserves_source(family):
    source = synthetic_image(family)
    before = bytes(source)
    corrected, details = image.correct_checksums(source, family)
    assert source == before
    assert corrected != before
    assert image.correct_checksums(corrected, family)[0] == corrected
    status = image.checksum_status(corrected)
    assert all(status[key] is True for key in ("boot", "program", "cal"))
    assert len(details) == (3 if family == "MS42" else 5)
    expected = _bitwise_crc(source[0x100:0x200], 0x2D2D)
    assert int.from_bytes(corrected[0x3C24:0x3C26], "little") == expected


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_partial_matches_full_correction_and_crc_includes_last_byte(family):
    source = synthetic_image(family)
    profile = image.PROFILES[family]
    source[0x48142 if family == "MS42" else 0x7400F] ^= 1
    full, _ = image.correct_checksums(source, family)
    partial = source[profile.calibration_offset:profile.calibration_offset + profile.partial_size]
    corrected, _ = image.correct_checksums(partial, family, context=source)
    assert corrected == full[profile.calibration_offset:profile.calibration_offset + profile.partial_size]
    assert image.checksum_status(corrected, context=source)["cal"] is True
    assert image.checksum_status(corrected, context=source)["program"] is None


def test_ms43_monitor_word_sum_carry_and_exclusive_end():
    source = synthetic_image("MS43")
    expected = (0xFFFFFFF0 + sum(struct.unpack("<32H", source[0x50020:0x50060]))
                + sum(struct.unpack("<16H", source[0x60020:0x60040]))) & 0xFFFFFFFF
    corrected, _ = image.correct_checksums(source, "MS43")
    assert int.from_bytes(corrected[0x6FDAE:0x6FDB2], "little") == expected
    source[0x50060] ^= 0xFF
    assert image.correct_checksums(source, "MS43")[0][0x6FDAE:0x6FDB2] == corrected[0x6FDAE:0x6FDB2]
    source[0x5005F] ^= 0xFF
    assert image.correct_checksums(source, "MS43")[0][0x6FDAE:0x6FDB2] != corrected[0x6FDAE:0x6FDB2]


def test_ms43_partial_requires_context_and_accepts_exact_small_context():
    source = synthetic_image("MS43")
    corrected, _ = image.correct_checksums(source, "MS43")
    partial = corrected[0x70000:]
    status = image.checksum_status(partial)
    assert status["cal"] is None
    assert any("context" in message for message in status["details"])
    with pytest.raises(ValueError, match="context"):
        image.correct_checksums(partial, "MS43")
    context = source[0x6FDB8:0x6FDE0]
    assert len(context) == image.MS43_MONITOR_CONTEXT_SIZE == 40
    assert image.correct_checksums(partial, "MS43", context)[0] == partial
    assert image.checksum_status(partial, context=context)["cal"] is True
    for invalid in (context[:-1], context + b"\x00"):
        with pytest.raises(ValueError, match="40 bytes"):
            image.correct_checksums(partial, "MS43", invalid)
    source[0x6FFBA:0x6FFC0] = b"430056"
    with pytest.raises(ValueError, match="software"):
        image.correct_checksums(partial, "MS43", source)


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("malformation", ["zero_count", "too_many", "reversed", "outside", "self"])
def test_invalid_crc_descriptors_refuse_without_mutation(family, malformation):
    source = synthetic_image(family)
    store = 0x4FEE0 if family == "MS42" else 0x73FE0
    if malformation == "zero_count":
        _word(source, store + 2, 0)
    elif malformation == "too_many":
        _word(source, store + 2, 0xFFFF)
    elif malformation == "reversed":
        _dword(source, store + 8, 1)
    elif malformation == "outside":
        _dword(source, store + 8, 0xFFFFFF)
    else:
        _dword(source, store + 4, store)
        _dword(source, store + 8, store + 1)
    before = bytes(source)
    with pytest.raises(ValueError):
        image.correct_checksums(source, family)
    assert source == before
    assert image.checksum_status(source)["cal"] is not True


def test_ms43_bad_monitor_descriptors_are_not_silently_truncated():
    source = synthetic_image("MS43")
    _dword(source, 0x6FDD2, 0x70091)
    with pytest.raises(ValueError, match="word range"):
        image.correct_checksums(source, "MS43")
    partial = source[0x70000:]
    _dword(source, 0x6FDCE, 0x90000)
    _dword(source, 0x6FDD2, 0x90010)
    with pytest.raises(ValueError, match="leaves the calibration"):
        image.correct_checksums(partial, "MS43", source)


def test_ms43_empty_monitor_range_and_unknown_software_revision():
    source = synthetic_image("MS43")
    source[0x6FFBA:0x6FFC0] = b"430099"
    source[0x70008:0x7000E] = b"430099"
    _dword(source, 0x6FDCE, 0x70000)
    _dword(source, 0x6FDD2, 0x70000)
    full, _ = image.correct_checksums(source, "MS43")
    partial, _ = image.correct_checksums(source[0x70000:], "MS43", source)
    assert partial == full[0x70000:]
    assert image.checksum_status(partial, context=source)["cal"] is True


def test_profiles_preserve_distinct_read_and_write_plans():
    ms42 = image.PROFILES["MS42"]
    assert ms42.read_regions[1] == (0x11000, 0x11000, 0x30000)
    assert ms42.program_write_regions == ((0x11000, 0x11000, 0x2F000),
                                          (0x5002C, 0x5002C, 0x2FFD4))
    ms43 = image.PROFILES["MS43"]
    assert ms43.read_regions[1] == (0x90000, 0x10000, 0x60000)
    assert ms43.program_write_regions == ((0x90000, 0x10000, 0x5FFF0),)
    assert ms43.calibration_write_length == 0xFFEF


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_extract_and_merge_preserve_source_and_honor_checksum_option(family):
    full = synthetic_image(family)
    before = bytes(full)
    profile = image.PROFILES[family]
    start = profile.calibration_offset
    end = start + profile.partial_size
    partial = bytearray(image.extract_calibration(full))
    assert partial == full[start:end]
    partial[0x80] ^= 1
    supplied = bytes(partial)

    unchecked, details = image.merge_calibration(full, partial, correct_checksum=False)
    assert unchecked[:start] == full[:start]
    assert unchecked[end:] == full[end:]
    assert unchecked[start:end] == partial
    assert details == []

    # Initial boot/program CRCs need not already be valid for offline merging.
    corrected, details = image.merge_calibration(full, partial)
    assert corrected == image.correct_checksums(unchecked, family)[0]
    assert all(image.checksum_status(corrected)[key] is True
               for key in ("boot", "program", "cal"))
    assert details
    assert full == before and partial == supplied


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_extract_and_merge_require_matching_family_and_software(family):
    full = synthetic_image(family)
    profile = image.PROFILES[family]
    partial = bytearray(image.extract_calibration(full))
    with pytest.raises(ValueError, match="512 KiB"):
        image.extract_calibration(partial)
    with pytest.raises(ValueError, match="KiB calibration"):
        image.merge_calibration(full, full)
    partial[8:14] = b"OTHER1"
    with pytest.raises(ValueError, match="software"):
        image.merge_calibration(full, partial)

    # Unknown revisions remain supported when the two identifiers agree.
    full[profile.sw_offset:profile.sw_offset + 6] = b"NEW123"
    full[profile.calibration_offset + 8:profile.calibration_offset + 14] = b"NEW123"
    assert image.extract_calibration(full)[8:14] == b"NEW123"
    full[profile.calibration_offset + 8:profile.calibration_offset + 14] = b"OTHER1"
    with pytest.raises(ValueError, match="software"):
        image.extract_calibration(full)

    full = synthetic_image(family)
    full[profile.calibration_offset + 0x20:profile.calibration_offset + 0x26] = b"000000"
    with pytest.raises(ValueError, match="identifier"):
        image.extract_calibration(full)
    partial = bytearray(image.extract_calibration(synthetic_image(family)))
    partial[0x20:0x26] = b"000000"
    with pytest.raises(ValueError, match="identifier"):
        image.merge_calibration(synthetic_image(family), partial)
