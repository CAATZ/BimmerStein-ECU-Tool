import pytest

from ds2 import DS2Error, DS2Timeout
from vehicle_coding import (
    GM3NeedsAdsError,
    UnsupportedCodingModule,
    UnsupportedGM3Profile,
    read_gm3_coding,
    read_module_coding,
    write_gm3_coding,
    write_module_coding,
)


def _xor(data):
    value = 0
    for byte in data:
        value ^= byte
    return value


def _response(payload):
    body = bytearray((0x00, 0x00, 0xA0)) + bytearray(payload)
    body[1] = len(body) + 1
    return bytes(body) + bytes((_xor(body),))


def _ident(coding_index, diagnostic_index=0x25):
    payload = bytearray(12)
    payload[5] = ((coding_index // 10) << 4) | (coding_index % 10)
    payload[6] = diagnostic_index
    return _response(payload)


def _coding(data):
    return _response(bytes(data) + bytes((_xor(data),)))


def _whole_block(data, address=0x72):
    body = bytearray((address, len(data) + 4, 0xA0)) + bytearray(data)
    return bytes(body) + bytes((_xor(body),))


def _module_response(address, payload):
    body = bytearray((address, 0, 0xA0)) + bytearray(payload)
    body[1] = len(body) + 1
    return bytes(body) + bytes((_xor(body),))


class FakeDS2:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def send_frame(self, frame, resp_addr, timeout):
        self.calls.append((bytes(frame), resp_addr, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_read_c04_decodes_humanized_comfort_bits():
    data = bytearray(17)
    data[6] = 0x08
    data[13] = 0x01
    ds2 = FakeDS2((_ident(4), _coding(data)))

    state = read_gm3_coding(ds2)

    assert state.profile.key == "GM3.C04"
    assert {key: state.values[key] for key in (
        "door_open", "door_close", "remote_open", "remote_close"
    )} == {
        "door_open": True,
        "door_close": False,
        "remote_open": True,
        "remote_close": False,
    }
    assert [call[0] for call in ds2.calls] == [
        bytes.fromhex("00 05 00 00 05"),
        bytes.fromhex("00 05 08 00 0D"),
    ]


def test_write_c10_changes_only_known_bits_and_verifies():
    before = bytearray(range(25))
    after = bytearray(before)
    after[11] &= ~0x04
    after[17] |= 0x02
    ds2 = FakeDS2((
        _ident(10), _coding(before), _response(b""), _coding(after),
    ))

    state = write_gm3_coding(
        ds2, "GM3.C10", bytes(before),
        {"door_open": True, "remote_close": True},
    )

    assert state.raw_data == bytes(after)
    write_frame = ds2.calls[2][0]
    assert write_frame[:4] == bytes((0x00, 31, 0x09, 0x00))
    assert write_frame[4:29] == bytes(after)
    assert write_frame[29] == _xor(after)
    assert write_frame[30] == _xor(write_frame[:-1])


def test_unknown_profile_and_bad_block_are_locked():
    # Same CI and length, but DI 85 identifies the different E53 redesign.
    with pytest.raises(UnsupportedGM3Profile, match="index 04"):
        read_gm3_coding(FakeDS2((_ident(4, 0x85),)))

    bad = bytearray(17)
    with pytest.raises(DS2Error, match="coding-block checksum"):
        read_gm3_coding(FakeDS2((_ident(4), _response(bytes(bad) + b"\x01"))))

    changed = bytes([1]) + bytes(16)
    ds2 = FakeDS2((_ident(4), _coding(changed)))
    with pytest.raises(DS2Error, match="changed since it was read"):
        write_gm3_coding(ds2, "GM3.C04", bytes(17), {"remote_open": True})
    assert len(ds2.calls) == 2

    cross_profile = FakeDS2((_ident(5), _coding(bytes(17))))
    with pytest.raises(DS2Error, match="profile changed"):
        write_gm3_coding(
            cross_profile, "GM3.C04", bytes(17), {"remote_open": True}
        )
    assert len(cross_profile.calls) == 2


def test_no_k_line_response_explains_ads_boundary():
    with pytest.raises(GM3NeedsAdsError, match="ADS/L-line"):
        read_gm3_coding(FakeDS2((DS2Timeout("no response"),)))

    with pytest.raises(UnsupportedCodingModule, match="no reviewed built-in profile"):
        read_module_coding(FakeDS2(()), "lcm")


def test_returned_rejection_is_not_misreported_as_ads():
    with pytest.raises(DS2Error, match="busy") as error:
        read_gm3_coding(FakeDS2((_response(b"")[:2] + b"\xA1" + _response(b"")[3:],)))
    assert not isinstance(error.value, GM3NeedsAdsError)


def test_e46_seat_read_write_uses_exact_whole_block_transport_and_retries_busy():
    seat_ident = _module_response(
        0x72, bytes.fromhex("08 09 90 67 00 01 00 00 00 00 00 00"))
    before = bytes((0xF8,))
    after = bytes((0xFD,))
    busy = bytes.fromhex("72 04 A1 D7")
    ds2 = FakeDS2((
        seat_ident, _whole_block(before),
        seat_ident, _whole_block(before), busy, bytes.fromhex("72 04 A0 D6"),
        busy, _whole_block(after),
    ))

    state = read_module_coding(ds2, "sm_e46")
    assert state.values == {
        "automatic_seat_adjustment_timing": "unlock",
        "one_touch_memory": False,
    }
    updated = write_module_coding(
        ds2, "sm_e46", "SM_E46.C01", before,
        {"automatic_seat_adjustment_timing": "unlock_and_door",
         "one_touch_memory": True},
    )

    assert updated.raw_data == after
    frames = [call[0] for call in ds2.calls]
    assert frames[0] == bytes.fromhex("72 04 00 76")
    assert frames[1] == bytes.fromhex("72 04 08 7E")
    assert bytes.fromhex("72 05 09 FD 83") in frames
    assert frames[-2:] == [bytes.fromhex("72 04 08 7E")] * 2


def test_e46_seat_unknown_identity_and_reserved_choice_are_read_only():
    other_ident = _module_response(
        0x72, bytes.fromhex("08 09 90 69 00 01 00 00 00 00 00 00"))
    with pytest.raises(UnsupportedGM3Profile):
        read_module_coding(FakeDS2((other_ident,)), "sm_e46")

    seat_ident = _module_response(
        0x72, bytes.fromhex("08 09 90 67 00 01 00 00 00 00 00 00"))
    with pytest.raises(DS2Error, match="unsupported AUT_SITZVERSTELLUNG"):
        read_module_coding(FakeDS2((seat_ident, _whole_block(b"\x02"))), "sm_e46")
