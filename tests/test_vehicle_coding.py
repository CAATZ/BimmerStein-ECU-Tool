import pytest

from ds2 import DS2Error, DS2Timeout
from vehicle_coding import (
    CodingRequiresAdsError,
    _apply_word_checksum,
    _light_checksum,
    _validate_regions,
    read_module_coding,
    write_module_coding,
)
from vehicle_coding_profiles import CodingRegion, PROFILE_BY_KEY


def _xor(data):
    value = 0
    for byte in data:
        value ^= byte
    return value


def _response(address, payload=b"", function=0xA0):
    body = bytes((address, len(payload) + 4, function)) + bytes(payload)
    return body + bytes((_xor(body),))


def _ident(address, coding_index, diagnostic_index=0, prefix=b"\0\0\0\0"):
    payload = bytearray(12)
    payload[:4] = prefix
    payload[5] = coding_index
    payload[6] = diagnostic_index
    return _response(address, payload)


def _selected(address, data, *, inner=False):
    payload = bytes(data) + (bytes((_xor(data),)) if inner else b"")
    return _response(address, payload)


class FakeDS2:
    def __init__(self, responses=(), fast_responses=()):
        self.responses = list(responses)
        self.fast_responses = list(fast_responses)
        self.calls = []
        self.fast_calls = []

    def send_frame(self, frame, resp_addr, timeout):
        self.calls.append((bytes(frame), resp_addr, timeout))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def send_bmw_fast(self, frame, target, timeout):
        self.fast_calls.append((bytes(frame), target, timeout))
        value = self.fast_responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def _gm3_read(profile, blocks):
    return [_ident(0, profile.coding_index_raw, 0x25)] + [
        _selected(0, blocks[region.key], inner=True)
        for region in profile.regions
    ]


def test_gm3_reads_all_three_blocks_and_mask_writes_one_setting():
    profile = PROFILE_BY_KEY["E39.GM3.C04"]
    before = {region.key: bytes(region.length) for region in profile.regions}
    setting = next(
        item for item in profile.settings
        if item.reference == "KOMFORTOEFFNUNG"
    )
    after = dict(before)
    changed = bytearray(after["b0"])
    changed[6] |= 0x04
    after["b0"] = bytes(changed)

    responses = (
        _gm3_read(profile, before)
        + _gm3_read(profile, before)
        + [_response(0)]
        + _gm3_read(profile, after)
    )
    ds2 = FakeDS2(responses)

    initial = read_module_coding(ds2, "e39_gm3")
    assert initial.decoded_values[setting.key] is True
    assert len(initial.raw_regions) == 3

    verified = write_module_coding(
        ds2, "e39_gm3", profile.key, initial.raw_data,
        {setting.key: False},
    )
    assert verified.decoded_values[setting.key] is False
    writes = [frame for frame, _address, _timeout in ds2.calls if frame[2] == 0x09]
    assert len(writes) == 1
    assert writes[0][:4] == bytes((0x00, 23, 0x09, 0x00))
    assert writes[0][4:21] == after["b0"]
    assert writes[0][21] == _xor(after["b0"])


def test_unknown_current_seat_choice_does_not_block_another_setting():
    profile = PROFILE_BY_KEY["E46.SM_E46.C01"]
    prefix = bytes.fromhex("08 09 90 67")
    ident = _ident(0x72, 0x01, prefix=prefix)
    before = b"\x02"
    after = b"\x06"
    ds2 = FakeDS2((
        ident, _selected(0x72, before),
        ident, _selected(0x72, before), _response(0x72),
        ident, _selected(0x72, after),
    ))

    state = read_module_coding(ds2, "e46_seat")
    timing = next(s for s in profile.settings if s.reference == "AUT_SITZVERSTELLUNG")
    one_touch = next(s for s in profile.settings if s.reference == "MEMORY_TIPP_BETRIEB")
    assert state.decoded_values[timing.key] is None

    verified = write_module_coding(
        ds2, "e46_seat", profile.key, state.raw_data,
        {one_touch.key: True},
    )
    assert verified.raw_data == after
    assert verified.decoded_values[timing.key] is None
    assert ds2.calls[4][0] == bytes.fromhex("72 05 09 06 78")


def test_stale_read_blocks_write_before_any_write_request():
    profile = PROFILE_BY_KEY["E46.SM_E46.C01"]
    ident = _ident(0x72, 0x01, prefix=bytes.fromhex("08 09 90 67"))
    ds2 = FakeDS2((ident, _selected(0x72, b"\x01")))
    with pytest.raises(DS2Error, match="changed since it was read"):
        write_module_coding(
            ds2, "e46_seat", profile.key, b"\x00", {},
        )
    assert not any(frame[2] in {0x07, 0x09} for frame, *_rest in ds2.calls)


def test_unsupported_e36_transport_fails_before_bus_access():
    ds2 = FakeDS2()
    with pytest.raises(CodingRequiresAdsError, match="ADS/L-line"):
        read_module_coding(ds2, "e36_gm4")
    assert ds2.calls == []


def test_gm3_timeout_explains_possible_ads_fallback():
    ds2 = FakeDS2((DS2Timeout("no response"),))
    with pytest.raises(CodingRequiresAdsError, match="ADS/L-line"):
        read_module_coding(ds2, "e39_gm3")


def test_light_checksum_policies_are_profile_and_selector_exact():
    cases = (
        ("E39.LCM.C14", 0x0F, 9),
        ("E39.LCM.C17", 0x0F, 15),
        ("E46.LSZ.C26", 0x0F, 30),
        ("E39.LCM.C20", 0x0F, None),
    )
    for key, selector, index in cases:
        profile = PROFILE_BY_KEY[key]
        region = next(
            (item for item in profile.regions if item.selector == selector),
            CodingRegion(f"b{selector}", selector, selector * 31, 31),
        )
        data = bytearray(range(31))
        before = bytes(data)
        _light_checksum(profile, region, data)
        if index is None:
            assert data == before
        else:
            assert data[index] == _xor(before[:index])
            if region.key in {item.key for item in profile.regions}:
                _validate_regions(profile, {
                    item.key: bytes(data) if item.key == region.key
                    else _valid_light_region(profile, item)
                    for item in profile.regions
                })


def _valid_light_region(profile, region):
    data = bytearray(region.length)
    _light_checksum(profile, region, data)
    return bytes(data)


def test_word_checksum_domains_cover_e36_e39_and_e46():
    for key in (
        "E36.KMB_E36.C02", "E39.KMB_E39.C03",
        "E46.KMB_E46.C02", "E46.KMB_E46.C07",
    ):
        profile = PROFILE_BY_KEY[key]
        region = profile.regions[0]
        data = bytearray((index * 17 + 3) & 0xFF for index in range(region.length))
        _apply_word_checksum(profile, region, data)
        _validate_regions(profile, {region.key: bytes(data)})

    profile = PROFILE_BY_KEY["E36.KMB_E36.C02"]
    region = profile.regions[0]
    data = bytearray(region.length)
    data[0xD8 - region.address] = 0x5A
    _apply_word_checksum(profile, region, data)
    assert data[0xD8 - region.address] == 0x5A


def test_mk60_generic_owner_accepts_exact_c06_profile():
    profile = PROFILE_BY_KEY["E46.MK60.C06"]
    ident = bytearray(15)
    ident[4:6] = b"\x5A\x80"
    ident[13] = 0x06
    data = bytearray(15)
    data[0] = 1
    read = bytes.fromhex("00 00 00 00 62 30 00") + bytes(data) + b"\x00"
    ds2 = FakeDS2(fast_responses=(bytes(ident), read))
    state = read_module_coding(ds2, "e46_mk60")
    assert state.profile is profile
    assert state.raw_data == bytes(data)
