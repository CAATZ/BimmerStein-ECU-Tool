"""Self-contained, exact vehicle-module coding over concept-6 DS2."""

from dataclasses import dataclass
import time
from typing import Mapping

from ds2 import DS2Error, DS2Timeout
from vehicle_coding_profiles import (
    CodingProfile as ModuleCodingProfile,
    GM3_PROFILES as GM3_PROFILES,
    SM_E46_PROFILES as SM_E46_PROFILES,
    profiles_for_module,
)


class GM3NeedsAdsError(DS2Error):
    """The GM3 did not accept its concept-6 K-line probe."""


class UnsupportedGM3Profile(DS2Error):
    """The connected GM3 coding index has no reviewed built-in profile."""


class UnsupportedCodingModule(DS2Error):
    """The requested module has no reviewed built-in coding transport."""


@dataclass(frozen=True)
class ModuleCodingState:
    profile: ModuleCodingProfile
    raw_data: bytes
    raw_ident: bytes

    @property
    def values(self) -> dict[str, bool | str]:
        return {feature.key: feature.decode(self.raw_data)
                for feature in self.profile.features}

GM3Profile = ModuleCodingProfile
GM3CodingState = ModuleCodingState

_REGEN_SECONDS = 0.025


def _xor(data: bytes) -> int:
    value = 0
    for byte in data:
        value ^= byte
    return value


def _frame(body: bytes) -> bytes:
    return body + bytes((_xor(body),))


def _telegram(address: int, function: int, *payload: int) -> bytes:
    body = bytes((address, len(payload) + 4, function, *payload))
    return _frame(body)


def _send_a0(ds2, profile: ModuleCodingProfile, frame: bytes) -> bytes:
    attempts = 3 if profile.transport == "whole_block" else 1
    for attempt in range(attempts):
        if profile.transport == "gm3_selected":
            time.sleep(_REGEN_SECONDS)
        response = bytes(ds2.send_frame(
            frame, resp_addr=profile.address, timeout=2.0))
        if len(response) < 4:
            raise DS2Error(
                f"invalid {profile.module_name} response length {len(response)}")
        if response[2] == 0xA0:
            return response
        if response[2] == 0xA1 and attempt + 1 < attempts:
            continue
        status = {
            0xA1: "busy", 0xA2: "request rejected",
            0xB0: "invalid parameter", 0xB1: "unsupported function",
            0xB2: "unsupported number", 0xFF: "negative acknowledgement",
        }.get(response[2], f"status 0x{response[2]:02X}")
        raise DS2Error(
            f"{profile.module_name} rejected the coding request: {status} "
            f"(0x{response[2]:02X})"
        )
    raise AssertionError("unreachable")


def _bcd(byte: int) -> int:
    high, low = byte >> 4, byte & 0x0F
    if high > 9 or low > 9:
        raise DS2Error(f"invalid module coding-index BCD 0x{byte:02X}")
    return high * 10 + low


def identify_module(
    ds2, module_key: str
) -> tuple[ModuleCodingProfile | None, bytes, int]:
    """Probe one exact Concept-6 module and bind its reviewed coding index."""
    profiles = profiles_for_module(module_key)
    if not profiles:
        raise UnsupportedCodingModule(
            f"{module_key} coding has no reviewed built-in profile")
    probe = profiles[0]
    try:
        identify_payload = (0x00,) if probe.transport == "gm3_selected" else ()
        response = _send_a0(
            ds2, probe, _telegram(probe.address, 0x00, *identify_payload))
    except DS2Timeout as error:
        raise GM3NeedsAdsError(
            f"No concept-6 K-line response from {probe.module_name}; "
            "ADS/L-line may be required"
        ) from error
    if len(response) < 10:
        raise DS2Error(
            f"short {probe.module_name} identification response "
            f"({len(response)} bytes)")
    coding_index = _bcd(response[8])
    profile = next((
        candidate for candidate in profiles
        if candidate.coding_index == coding_index
        and candidate.matches_ident(response)
    ), None)
    return (
        profile,
        response,
        coding_index,
    )


def identify_gm3(ds2) -> tuple[ModuleCodingProfile | None, bytes, int]:
    return identify_module(ds2, "gm3")


def _read_block(ds2, profile: ModuleCodingProfile) -> bytes:
    selector = (0x00,) if profile.transport == "gm3_selected" else ()
    response = _send_a0(
        ds2, profile, _telegram(profile.address, 0x08, *selector))
    expected = profile.data_length + (
        5 if profile.transport == "gm3_selected" else 4)
    if len(response) != expected:
        raise DS2Error(
            f"invalid {profile.key} coding response length {len(response)}; "
            f"expected {expected}"
        )
    if profile.transport == "gm3_selected":
        data = response[3:-2]
        if response[-2] != _xor(data):
            raise DS2Error(f"invalid {profile.key} coding-block checksum")
    else:
        data = response[3:-1]
    return data


def read_module_coding(ds2, module_key: str) -> ModuleCodingState:
    """Read one exact built-in coding profile without BMW runtime files."""
    profile, ident, coding_index = identify_module(ds2, module_key)
    if profile is None:
        raise UnsupportedGM3Profile(
            f"{module_key.upper()} coding index {coding_index:02d} is not supported"
        )
    state = ModuleCodingState(profile, _read_block(ds2, profile), ident)
    try:
        state.values
    except ValueError as error:
        raise DS2Error(f"{profile.module_name} has {error}") from error
    return state


def read_gm3_coding(ds2) -> ModuleCodingState:
    return read_module_coding(ds2, "gm3")


def write_module_coding(
    ds2,
    module_key: str,
    expected_profile_key: str,
    expected_data: bytes,
    values: Mapping[str, bool | str],
) -> ModuleCodingState:
    """Mask-edit reviewed fields, write them, and require an exact readback."""
    state = read_module_coding(ds2, module_key)
    if state.profile.key != expected_profile_key:
        raise DS2Error(
            f"{state.profile.module_name} profile changed from "
            f"{expected_profile_key} to {state.profile.key}; read it again"
        )
    if state.raw_data != bytes(expected_data):
        raise DS2Error(
            f"{state.profile.module_name} coding changed since it was read; "
            "read it again")

    features = {feature.key: feature for feature in state.profile.features}
    unknown = set(values) - set(features)
    if unknown:
        raise ValueError(
            f"unknown {state.profile.module_name} coding option: "
            f"{sorted(unknown)[0]}")

    updated = bytearray(state.raw_data)
    for key, value in values.items():
        features[key].apply(updated, value)
    updated_bytes = bytes(updated)
    if updated_bytes == state.raw_data:
        return state

    if state.profile.transport == "gm3_selected":
        body = bytes((
            state.profile.address, state.profile.data_length + 6, 0x09, 0x00,
        ))
        frame = _frame(body + updated_bytes + bytes((_xor(updated_bytes),)))
    else:
        body = bytes((
            state.profile.address, state.profile.data_length + 4, 0x09,
        ))
        frame = _frame(body + updated_bytes)
    _send_a0(ds2, state.profile, frame)
    verified = _read_block(ds2, state.profile)
    if verified != updated_bytes:
        raise DS2Error(
            f"{state.profile.module_name} coding write verification failed")
    return ModuleCodingState(state.profile, verified, state.raw_ident)


def write_gm3_coding(
    ds2,
    expected_profile_key: str,
    expected_data: bytes,
    values: Mapping[str, bool | str],
) -> ModuleCodingState:
    return write_module_coding(
        ds2, "gm3", expected_profile_key, expected_data, values
    )
