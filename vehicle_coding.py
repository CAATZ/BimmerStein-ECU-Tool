"""Self-contained vehicle-module coding over the app's existing K-line stack."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Mapping

from ds2 import DS2Error, DS2Timeout
from transmission_conversion import (
    _bmw_fast_payload,
    _positive,
    _read_words,
    _write_mk60_data,
    _write_words,
)
from vehicle_coding_profiles import (
    CodingProfile as ModuleCodingProfile,
    CodingRegion,
    TARGET_BY_KEY,
    profiles_for_target,
    resolve_profile,
)


class CodingRequiresAdsError(DS2Error):
    """The selected module requires ADS/L-line hardware."""


class UnsupportedCodingProfile(DS2Error):
    """The connected module has no exact embedded coding profile."""


class UnsupportedCodingModule(DS2Error):
    """The requested target has no implemented coding transport."""


# Compatibility names retained for callers of the first GM3-only version.
GM3NeedsAdsError = CodingRequiresAdsError
UnsupportedGM3Profile = UnsupportedCodingProfile
GM3Profile = ModuleCodingProfile


@dataclass(frozen=True)
class ModuleCodingState:
    profile: ModuleCodingProfile
    raw_regions: tuple[tuple[str, bytes], ...]
    raw_ident: bytes

    @property
    def region_data(self) -> dict[str, bytes]:
        return dict(self.raw_regions)

    @property
    def raw_data(self) -> bytes:
        return b"".join(value for _key, value in self.raw_regions)

    @property
    def decoded_values(self) -> dict[str, bool | str | None]:
        regions = self.region_data
        return {
            setting.key: setting.decode(regions)
            for setting in self.profile.settings
        }

    @property
    def values(self) -> dict[str, bool | str]:
        return {
            key: value for key, value in self.decoded_values.items()
            if value is not None
        }


GM3CodingState = ModuleCodingState
_REGEN_SECONDS = 0.025
_CHUNK = 32
_E46_SEAT_BMW_NUMBERS = frozenset(bytes.fromhex(value) for value in (
    "08 09 90 67", "08 09 90 68", "08 26 31 33",
    "08 26 31 34", "08 09 92 37", "08 09 92 38",
))


def _xor(data: bytes) -> int:
    value = 0
    for byte in data:
        value ^= byte
    return value


def _bcd(value: int) -> int:
    high, low = value >> 4, value & 0x0F
    if high > 9 or low > 9:
        raise DS2Error(f"invalid module coding-index BCD 0x{value:02X}")
    return high * 10 + low


def _request(ds2, profile: ModuleCodingProfile, body: bytes, *, write: bool = False) -> bytes:
    if profile.transport == "gm3_selected":
        time.sleep(_REGEN_SECONDS)
    if profile.transport == "gm3_selected":
        write_attempts = 1
    elif (profile.transport == "light_selected" and profile.chassis == "E39"
          and profile.coding_index_raw >= 0x20):
        write_attempts = 11
    elif profile.family.startswith("MRS4") or profile.family == "MRS3":
        write_attempts = 6
    else:
        write_attempts = 3
    return _positive(
        ds2, body, profile.address,
        retries=write_attempts if write else (
            1 if profile.transport == "gm3_selected" else 3
        ),
        delay=_REGEN_SECONDS,
    )


def _identify(ds2, target_key: str) -> tuple[ModuleCodingProfile, bytes, int]:
    target = TARGET_BY_KEY.get(target_key)
    if target is None or not target.profile_keys:
        raise UnsupportedCodingModule(
            f"{target_key} has no embedded coding profiles")
    if not target.available:
        raise CodingRequiresAdsError(target.unavailable_reason)

    probe = profiles_for_target(target_key)[0]
    if probe.transport == "mk60":
        ident = bytes(ds2.send_bmw_fast(
            bytes.fromhex("B8 29 F1 02 1A 80"), target=0x29, timeout=2.0))
        _bmw_fast_payload(ident, 0x1A, b"\x80")
        if len(ident) <= 13:
            raise DS2Error("MK60 identification response is too short")
        raw_ci = ident[13]
        _bcd(raw_ci)
        profile = resolve_profile(target_key, raw_ci)
        if profile is None:
            raise UnsupportedCodingProfile(
                f"{target.name} coding index {_bcd(raw_ci):02d} is not supported")
        return profile, ident, raw_ci

    payload = b"\x00" if probe.transport == "gm3_selected" else (
        b"\x05" if probe.transport == "seat_e38" else b""
    )
    try:
        response = _request(
            ds2, probe,
            bytes((probe.address, len(payload) + 4, 0x00)) + payload,
        )
    except DS2Error as error:
        if probe.family == "ASC5":
            response = _request(
                ds2, probe, bytes((probe.address, 0x04, 0x53))
            )
        elif isinstance(error, DS2Timeout) and probe.transport == "gm3_selected":
            raise CodingRequiresAdsError(
                "No Concept-6 K-line response from the General Module; "
                "this car may require ADS/L-line hardware") from error
        else:
            raise
    if len(response) < 10:
        raise DS2Error(
            f"short {target.name} identification response ({len(response)} bytes)")
    if probe.transport == "gm3_selected" and response[9] != 0x25:
        raise UnsupportedCodingProfile(
            "The connected body module is not the supported GM3 diagnostic family")
    if target_key == "e46_seat" and response[3:7] not in _E46_SEAT_BMW_NUMBERS:
        raise UnsupportedCodingProfile(
            "The connected seat module is not an exact supported E46 variant")
    raw_ci = response[8]
    _bcd(raw_ci)
    profile = resolve_profile(target_key, raw_ci)
    if profile is None:
        raise UnsupportedCodingProfile(
            f"{target.name} coding index {_bcd(raw_ci):02d} is not supported")
    diagnostic_index = response[9]
    if profile.family == "EWS":
        valid = (
            diagnostic_index & 0x80 == 0 if raw_ci in {0x01, 0x02}
            else diagnostic_index & 0x80 != 0 and diagnostic_index != 0x82
        )
        if not valid:
            raise UnsupportedCodingProfile(
                "The immobilizer diagnostic variant does not match its coding profile")
    if target_key == "e39_ike" and not 0x01 <= diagnostic_index <= 0x04:
        raise UnsupportedCodingProfile("The connected cluster is not an E39 IKE")
    if target_key == "e39_kmb" and not 0x1E <= diagnostic_index <= 0x28:
        raise UnsupportedCodingProfile("The connected cluster is not an E39 KMB")
    return profile, response, raw_ci


def identify_module(
    ds2, target_key: str
) -> tuple[ModuleCodingProfile | None, bytes, int]:
    """Identify one target and return its exact embedded profile."""
    profile, ident, raw_ci = _identify(ds2, target_key)
    return profile, ident, _bcd(raw_ci)


def identify_gm3(ds2) -> tuple[ModuleCodingProfile | None, bytes, int]:
    return identify_module(ds2, "e39_gm3")


def _expect_data(response: bytes, length: int, name: str) -> bytes:
    data = response[3:-1]
    if len(data) != length:
        raise DS2Error(
            f"invalid {name} coding response length {len(data)}; expected {length}")
    return data


def _mrs_prepare(
    ds2, profile: ModuleCodingProfile, ident: bytes, *, write: bool
) -> bytes:
    family = profile.family
    legacy_both = family in {"ABGZ", "ABGB"}
    legacy_write = family in {"ZAE2", "MRSZ", "MRS2"}
    if legacy_both or write and legacy_write or family.startswith("MRS4"):
        _request(ds2, profile, bytes.fromhex("A4 06 90 FF FF"), write=write)
    if family.startswith("MRS4"):
        ident = _request(ds2, profile, bytes.fromhex("A4 04 00"))
        if len(ident) < 16:
            raise DS2Error("airbag security identification response is too short")
        _request(
            ds2, profile, bytes.fromhex("A4 08 95") + ident[0x0B:0x0F],
            write=write,
        )
    return ident


def _read_chunks(ds2, profile: ModuleCodingProfile, region: CodingRegion) -> bytes:
    result = bytearray()
    while len(result) < region.length:
        address = region.address + len(result)
        count = min(_CHUNK, region.length - len(result))
        if profile.transport == "airbag_addressed":
            body = bytes((
                profile.address, 0x07, 0x06,
                address >> 8, address & 0xFF, count,
            ))
        else:
            selector = 0x03 if profile.transport == "climate_e36" else 0x00
            body = bytes((
                profile.address, 0x09, 0x06, selector, 0x00,
                address >> 8, address & 0xFF, count,
            ))
        result.extend(_expect_data(
            _request(ds2, profile, body), count, profile.key
        ))
    return bytes(result)


def _read_region(ds2, profile: ModuleCodingProfile, region: CodingRegion) -> bytes:
    transport = profile.transport
    if transport == "word":
        if region.address % 2 or region.length % 2:
            raise DS2Error(f"invalid {profile.key} word region")
        return _read_words(ds2, region.address // 2, region.length // 2)
    if transport == "mk60":
        response = bytes(ds2.send_bmw_fast(
            bytes.fromhex("B8 29 F1 03 22 30 00"),
            target=0x29, timeout=2.0,
        ))
        data = _bmw_fast_payload(response, 0x22, b"\x30\x00")
        if len(data) != region.length:
            raise DS2Error(f"invalid {profile.key} coding response length")
        return data
    if transport in {"gm3_selected", "selected_plain", "light_selected", "mrs_selected"}:
        response = _request(ds2, profile, bytes((
            profile.address, 0x05, 0x08, region.selector,
        )))
        if transport == "gm3_selected":
            if len(response) != region.length + 5:
                raise DS2Error(f"invalid {profile.key} coding response length")
            data = response[3:3 + region.length]
            if response[-2] != _xor(data):
                raise DS2Error(f"invalid {profile.key} coding-block checksum")
            return data
        return _expect_data(response, region.length, profile.key)
    if transport in {"whole", "whole_delay"}:
        return _expect_data(
            _request(ds2, profile, bytes((profile.address, 0x04, 0x08))),
            region.length, profile.key,
        )
    if transport == "seat_e38":
        return _expect_data(
            _request(ds2, profile, bytes.fromhex("00 0A 06 05 00 00 B7 26 01")),
            region.length, profile.key,
        )
    if transport in {"airbag_addressed", "climate_e36"}:
        return _read_chunks(ds2, profile, region)
    if transport == "climate":
        if region.address:
            return _read_chunks(ds2, profile, region)
        return _expect_data(
            _request(ds2, profile, bytes.fromhex("5B 09 08 00 00 00 00 00")),
            region.length, profile.key,
        )
    if transport == "addressed":
        response = _request(ds2, profile, bytes((
            profile.address, 0x06, 0x08, region.address, region.length,
        )))
        return _expect_data(response, region.length, profile.key)
    raise UnsupportedCodingModule(f"{profile.key} transport is not implemented")


def _light_checksum(profile: ModuleCodingProfile, region: CodingRegion, data: bytearray) -> None:
    if profile.chassis == "E39" and profile.coding_index_raw >= 0x20:
        return
    index = None
    end = None
    if region.selector == 0x0E:
        index, end = 30, 30
    elif region.selector == 0x0F and profile.family == "LSZ":
        index, end = 30, 30
    elif region.selector == 0x0F and profile.family == "LCM":
        index = 9 if profile.coding_index_raw <= 0x16 else 15
        end = index
    if index is not None:
        data[index] = _xor(data[:end])


def _word_checksum_geometry(
    profile: ModuleCodingProfile,
) -> tuple[tuple[int, int, int, int, bool], ...]:
    if profile.chassis == "E36":
        return (
            (0x6E, 0x40, 0x70, 0x6E, True),
            (0xD9, 0xDA, 0x180, 0xD8, False),
        )
    if profile.chassis in {"E38", "E39"}:
        return ((0x58, 0x5A, 0x100, 0x58, False),)
    if profile.coding_index_raw in {0x02, 0x03, 0x04, 0x05, 0x06, 0x20, 0x21, 0x22}:
        return ((0x3F, 0x40, 0x200, 0x3E, False),)
    return ((0x16E, 0x70, 0x16E, 0x16E, False),)


def _apply_word_checksum(
    profile: ModuleCodingProfile, region: CodingRegion, data: bytearray
) -> None:
    for stored, start, end, _word, includes_stored in _word_checksum_geometry(profile):
        if not (region.address <= stored < region.address + len(data)
                and region.address <= start <= end <= region.address + len(data)):
            raise DS2Error(f"{profile.key} checksum domain is outside its coding image")
        if includes_stored:
            data[stored - region.address] = 0
        data[stored - region.address] = _xor(
            data[start - region.address:end - region.address]
        )


def _validate_regions(profile: ModuleCodingProfile, regions: Mapping[str, bytes]) -> None:
    for region in profile.regions:
        data = regions[region.key]
        if len(data) != region.length:
            raise DS2Error(f"invalid {profile.key} region {region.key} length")
        if profile.transport == "light_selected":
            expected = bytearray(data)
            _light_checksum(profile, region, expected)
            if expected != data:
                raise DS2Error(f"invalid {profile.key} coding checksum in block {region.selector}")
    if profile.transport == "word":
        region = profile.regions[0]
        original = regions[region.key]
        expected = bytearray(original)
        _apply_word_checksum(profile, region, expected)
        if any(
            expected[stored - region.address] != original[stored - region.address]
            for stored, _start, _end, _word, _includes in _word_checksum_geometry(profile)
        ):
            raise DS2Error(f"invalid {profile.key} storage checksum")
    if profile.transport == "mk60":
        data = regions[profile.regions[0].key]
        if len(data) != 15 or data[0] != (_xor(data[1:]) + 1) & 0xFF:
            raise DS2Error("invalid MK60 coding checksum")


def read_module_coding(ds2, target_key: str) -> ModuleCodingState:
    """Read one exact embedded coding profile without external runtime files."""
    profile, ident, _raw_ci = _identify(ds2, target_key)
    if profile.family in {"ABGZ", "ABGB"} or profile.family.startswith("MRS4"):
        ident = _mrs_prepare(ds2, profile, ident, write=False)
    regions = tuple(
        (region.key, _read_region(ds2, profile, region))
        for region in profile.regions
    )
    values = dict(regions)
    _validate_regions(profile, values)
    return ModuleCodingState(profile, regions, ident)


def read_gm3_coding(ds2) -> ModuleCodingState:
    return read_module_coding(ds2, "e39_gm3")


def _changed_spans(before: bytes, after: bytes):
    index = 0
    while index < len(before):
        if before[index] == after[index]:
            index += 1
            continue
        end = index + 1
        while end < len(before) and before[end] != after[end] and end - index < _CHUNK:
            end += 1
        yield index, after[index:end]
        index = end


def _write_region(
    ds2, profile: ModuleCodingProfile, region: CodingRegion,
    before: bytes, after: bytes,
) -> None:
    transport = profile.transport
    if transport == "word":
        intermediate = bytearray(after)
        for _stored, _start, _end, checksum_word, _includes in _word_checksum_geometry(profile):
            checksum_offset = checksum_word - region.address
            intermediate[checksum_offset:checksum_offset + 2] = before[
                checksum_offset:checksum_offset + 2
            ]
        _write_words(ds2, region.address // 2, before, intermediate)
        _write_words(ds2, region.address // 2, intermediate, after)
        if profile.chassis == "E36":
            _request(ds2, profile, bytes.fromhex("80 04 12"), write=True)
            time.sleep(5.0)
        return
    if transport == "mk60":
        _write_mk60_data(ds2, after)
        return
    if transport == "gm3_selected":
        body = bytes((profile.address, len(after) + 6, 0x09, region.selector))
        _request(ds2, profile, body + after + bytes((_xor(after),)), write=True)
        return
    if transport in {"selected_plain", "light_selected", "mrs_selected"}:
        body = bytes((profile.address, len(after) + 5, 0x09, region.selector))
        _request(ds2, profile, body + after, write=True)
        return
    if transport in {"whole", "whole_delay"}:
        body = bytes((profile.address, len(after) + 4, 0x09))
        _request(ds2, profile, body + after, write=True)
        if transport == "whole_delay":
            time.sleep(4.0)
        return
    if transport == "seat_e38":
        body = bytes.fromhex("00 0B 07 05 00 00 B7 26 01") + after
        _request(ds2, profile, body, write=True)
        return
    if transport == "addressed":
        body = bytes((
            profile.address, len(after) + 5, 0x09, region.address,
        )) + after
        _request(ds2, profile, body, write=True)
        return
    if transport == "climate" and not region.address:
        body = bytes.fromhex("5B 0D 09 00 00 00 00 00") + after
        _request(ds2, profile, body, write=True)
        return
    if transport in {"climate", "climate_e36"}:
        for offset in range(0, len(after), _CHUNK):
            data = after[offset:offset + _CHUNK]
            address = region.address + offset
            body = bytes((
                profile.address, len(data) + 9, 0x07, 0x03, 0x00,
                address >> 8, address & 0xFF, len(data),
            )) + data
            _request(ds2, profile, body, write=True)
        return
    if transport == "airbag_addressed":
        for offset, data in _changed_spans(before, after):
            address = region.address + offset
            body = bytes((
                profile.address, len(data) + 6, 0x07,
                address >> 8, address & 0xFF,
            )) + data
            _request(ds2, profile, body, write=True)
        return
    raise UnsupportedCodingModule(f"{profile.key} transport is not implemented")


def _split_expected(profile: ModuleCodingProfile, raw: bytes) -> dict[str, bytes]:
    raw = bytes(raw)
    if len(raw) != profile.data_length:
        raise ValueError(
            f"{profile.name} expected coding image has the wrong length")
    result = {}
    offset = 0
    for region in profile.regions:
        result[region.key] = raw[offset:offset + region.length]
        offset += region.length
    return result


def write_module_coding(
    ds2,
    target_key: str,
    expected_profile_key: str,
    expected_data: bytes,
    values: Mapping[str, bool | str],
) -> ModuleCodingState:
    """Mask-edit selected fields, write them, and require exact readback."""
    state = read_module_coding(ds2, target_key)
    if state.profile.key != expected_profile_key:
        raise DS2Error(
            f"{state.profile.name} profile changed from {expected_profile_key} "
            f"to {state.profile.key}; read it again")
    expected = _split_expected(state.profile, expected_data)
    if state.region_data != expected:
        raise DS2Error(
            f"{state.profile.name} coding changed since it was read; read it again")

    settings = {setting.key: setting for setting in state.profile.settings}
    unknown = set(values) - set(settings)
    if unknown:
        raise ValueError(
            f"unknown {state.profile.name} coding option: {sorted(unknown)[0]}")
    updated = {key: bytearray(value) for key, value in expected.items()}
    for key, value in values.items():
        settings[key].apply(updated, value)

    if state.profile.transport == "light_selected":
        for region in state.profile.regions:
            _light_checksum(state.profile, region, updated[region.key])
    elif state.profile.transport == "word":
        region = state.profile.regions[0]
        _apply_word_checksum(state.profile, region, updated[region.key])
    elif state.profile.transport == "mk60":
        region = state.profile.regions[0]
        updated[region.key][0] = (_xor(updated[region.key][1:]) + 1) & 0xFF

    changed = tuple(
        region for region in state.profile.regions
        if expected[region.key] != bytes(updated[region.key])
    )
    if not changed:
        return state
    if state.profile.transport in {"mrs_selected", "airbag_addressed"}:
        _mrs_prepare(ds2, state.profile, state.raw_ident, write=True)

    for region in changed:
        before, after = expected[region.key], bytes(updated[region.key])
        try:
            _write_region(ds2, state.profile, region, before, after)
        except (DS2Error, DS2Timeout) as error:
            # Never repeat an ambiguous write; accept only an exact readback.
            try:
                if _read_region(ds2, state.profile, region) == after:
                    continue
            except Exception:
                pass
            raise error

    verified = read_module_coding(ds2, target_key)
    target = b"".join(bytes(updated[region.key]) for region in state.profile.regions)
    if verified.profile.key != state.profile.key or verified.raw_data != target:
        raise DS2Error(f"{state.profile.name} coding write verification failed")
    return verified


def write_gm3_coding(
    ds2,
    expected_profile_key: str,
    expected_data: bytes,
    values: Mapping[str, bool | str],
) -> ModuleCodingState:
    return write_module_coding(
        ds2, "e39_gm3", expected_profile_key, expected_data, values
    )
