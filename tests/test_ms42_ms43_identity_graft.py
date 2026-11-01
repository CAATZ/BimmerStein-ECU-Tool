from pathlib import Path

import pytest

from ms42_ms43_identity import (
    IDENTITY_GRAFT_RANGES, IDENTITY_OFFSET, IDENTITY_SIZE, PAIRING_POINTERS,
    graft_identity,
)
from ms42_ms43_image import FULL_SIZE, checksum_status, correct_checksums
from ms42_ms43_vin import AIF_ADDRESSES, AIF_SIZE, encode_vin
from tests.test_ms42_ms43_image import synthetic_image


def fixture(family):
    target = synthetic_image(family)
    pointer_at, pairing_at = PAIRING_POINTERS[family]
    target[pointer_at:pointer_at + 4] = pairing_at.to_bytes(4, "little")
    live = bytearray(b"\x5A" * IDENTITY_SIZE)
    pointer = pairing_at.to_bytes(4, "little")
    if family == "MS42":
        live[pointer_at - IDENTITY_OFFSET:pointer_at - IDENTITY_OFFSET + 4] = pointer
    return target, live, pointer


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_live_identity_replaces_donor_without_changing_any_other_byte(family):
    target, live, pointer = fixture(family)
    aif = AIF_ADDRESSES[family] - IDENTITY_OFFSET
    for index in range(14):
        record = bytearray((index + value) & 255 for value in range(46))
        record[:13] = encode_vin(f"WBAAM31010FG{index:05d}")
        live[aif + index * 46:aif + (index + 1) * 46] = record
    before = bytes(target), bytes(live)
    result = graft_identity(target, live, family, pairing_pointer=pointer)
    expected = bytearray(target)
    for start, end in IDENTITY_GRAFT_RANGES[family]:
        expected[start:end] = live[start - IDENTITY_OFFSET:end - IDENTITY_OFFSET]
    assert result == expected
    assert result[AIF_ADDRESSES[family]:AIF_ADDRESSES[family] + AIF_SIZE] == live[aif:aif + AIF_SIZE]
    assert bytes(target) == before[0] and bytes(live) == before[1]
    assert isinstance(result, bytes)
    target[:] = b"\x00" * FULL_SIZE
    live[:] = b"\x00" * IDENTITY_SIZE
    assert result == expected  # Frozen retry target cannot follow mutable inputs.


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_erased_or_interrupted_identity_is_preserved_without_decoding(family):
    target, live, pointer = fixture(family)
    for start, end in IDENTITY_GRAFT_RANGES[family]:
        live[start - IDENTITY_OFFSET:end - IDENTITY_OFFSET] = b"\xFF" * (end - start)
    live[AIF_ADDRESSES[family] - IDENTITY_OFFSET + 25] = 0x01
    result = graft_identity(target, live, family, pairing_pointer=pointer)
    for start, end in IDENTITY_GRAFT_RANGES[family]:
        assert result[start:end] == live[start - IDENTITY_OFFSET:end - IDENTITY_OFFSET]


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_graft_keeps_all_existing_checksum_results(family):
    target, live, pointer = fixture(family)
    target, _ = correct_checksums(target, family)
    before = checksum_status(target, family)
    result = graft_identity(target, live, family, pairing_pointer=pointer)
    assert before == checksum_status(result, family)
    assert before["boot"] is True


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("source", [True, False])
def test_relocated_pairing_pointer_is_rejected_before_grafting(family, source):
    target, live, pointer = fixture(family)
    at, _ = PAIRING_POINTERS[family]
    if not source:
        target[at] ^= 2
    elif family == "MS42":
        live[at - IDENTITY_OFFSET] ^= 2
    else:
        pointer = bytes((pointer[0] ^ 2,)) + pointer[1:]
    with pytest.raises(ValueError, match="Immobilizer data address"):
        graft_identity(target, live, family, pairing_pointer=pointer)


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_unused_far_pointer_high_byte_is_not_a_new_fingerprint(family):
    target, live, pointer = fixture(family)
    at, _ = PAIRING_POINTERS[family]
    target[at + 3] = 0xE1
    if family == "MS42":
        live[at - IDENTITY_OFFSET + 3] = 0xA3
    else:
        pointer = pointer[:3] + b"\xA3"
    result = graft_identity(target, live, family, pairing_pointer=pointer)
    assert result[at:at + 4] == target[at:at + 4]


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_graft_does_not_silently_invalidate_a_changed_boot_crc_layout(family):
    target, live, pointer = fixture(family)
    target[0x3C28:0x3C2C] = (0x3C30).to_bytes(4, "little")
    target[0x3C2C:0x3C30] = (0x3FFF).to_bytes(4, "little")
    with pytest.raises(ValueError, match="Boot checksum range overlaps"):
        graft_identity(target, live, family, pairing_pointer=pointer)


def test_incomplete_inputs_and_unknown_family_are_rejected():
    target, live, pointer = fixture("MS43")
    for args, kwargs in (((target[:-1], live, "MS43"), {"pairing_pointer": pointer}),
                         ((target, live[:-1], "MS43"), {"pairing_pointer": pointer}),
                         ((target, live, "MS43"), {}),
                         ((target, live, "MS41"), {})):
        with pytest.raises(ValueError):
            graft_identity(*args, **kwargs)


@pytest.mark.parametrize("family,filename", [
    ("MS42", "Siemens_MS42_0110C6_E46_M52TUB28_EU3_LHD.bin"),
    ("MS43", "Siemens_MS43_MS430069_E46_M54B30_US.bin"),
])
def test_exact_reference_layout_is_checksum_neutral_when_available(family, filename):
    path = Path(__file__).resolve().parents[2] / "_shared" / f"REF_{family}" / filename
    if not path.is_file():
        pytest.skip("Exact reference image is not installed")
    target = path.read_bytes()
    live = bytearray(target[IDENTITY_OFFSET:IDENTITY_OFFSET + IDENTITY_SIZE])
    for start, end in IDENTITY_GRAFT_RANGES[family]:
        live[start - IDENTITY_OFFSET:end - IDENTITY_OFFSET] = b"\x6D" * (end - start)
    at, _ = PAIRING_POINTERS[family]
    result = graft_identity(target, live, family, pairing_pointer=target[at:at + 4])
    assert checksum_status(result, family) == checksum_status(target, family)
