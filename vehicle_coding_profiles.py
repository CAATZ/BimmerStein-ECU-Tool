"""Embedded, human-readable vehicle coding profiles.

The generated payload contains only reviewed coding fields and exact value
encodings. It has no runtime dependency on external diagnostic databases.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from functools import lru_cache
import json
from typing import Literal
import zlib

from vehicle_coding_catalog_data import CATALOG_PAYLOAD_B85


CodingLevel = Literal["basic", "advanced"]


def _masked_value(raw: bytes, mask: bytes) -> int:
    value = int.from_bytes(raw, "big")
    bits = int.from_bytes(mask, "big")
    if not bits:
        raise ValueError("coding mask cannot be zero")
    shift = (bits & -bits).bit_length() - 1
    return (value & bits) >> shift


@dataclass(frozen=True)
class CodingChoice:
    value: str
    label: str
    reference: str
    data: tuple[bytes, ...]


@dataclass(frozen=True)
class CodingPart:
    region: str
    offset: int
    mask: bytes


@dataclass(frozen=True)
class CodingSetting:
    key: str
    label: str
    description: str
    reference: str
    level: CodingLevel
    parts: tuple[CodingPart, ...]
    choices: tuple[CodingChoice, ...]
    toggle: tuple[str, str] | None = None

    def decode(self, regions: Mapping[str, bytes]) -> bool | str | None:
        current = tuple(
            _masked_value(
                regions[part.region][part.offset:part.offset + len(part.mask)],
                part.mask,
            )
            for part in self.parts
        )
        choice = next((
            candidate for candidate in self.choices
            if tuple(int.from_bytes(value, "big") for value in candidate.data)
            == current
        ), None)
        if choice is None:
            return None
        if self.toggle:
            return choice.value == self.toggle[0]
        return choice.value

    def apply(self, regions: dict[str, bytearray], value: bool | str) -> None:
        if self.toggle and type(value) is bool:
            value = self.toggle[0 if value else 1]
        choice = next(
            (candidate for candidate in self.choices if candidate.value == value),
            None,
        )
        if choice is None:
            raise ValueError(f"invalid {self.key} choice: {value!r}")
        for part, logical in zip(self.parts, choice.data):
            region = regions[part.region]
            end = part.offset + len(part.mask)
            current = int.from_bytes(region[part.offset:end], "big")
            mask = int.from_bytes(part.mask, "big")
            shift = (mask & -mask).bit_length() - 1
            updated = (current & ~mask) | ((int.from_bytes(logical, "big") << shift) & mask)
            region[part.offset:end] = updated.to_bytes(len(part.mask), "big")


@dataclass(frozen=True)
class CodingRegion:
    key: str
    selector: int
    address: int
    length: int


@dataclass(frozen=True)
class CodingProfile:
    key: str
    target: str
    chassis: str
    family: str
    name: str
    address: int
    transport: str
    coding_index_raw: int
    coding_indexes_raw: tuple[int, ...]
    memory_structure: str
    regions: tuple[CodingRegion, ...]
    settings: tuple[CodingSetting, ...]

    @property
    def module_key(self) -> str:
        return self.target

    @property
    def module_name(self) -> str:
        return self.name

    @property
    def coding_index(self) -> int:
        return _decode_bcd(self.coding_index_raw)

    @property
    def features(self) -> tuple[CodingSetting, ...]:
        return self.settings

    @property
    def data_length(self) -> int:
        return sum(region.length for region in self.regions)


@dataclass(frozen=True)
class CodingTarget:
    key: str
    chassis: str
    group: str
    name: str
    address: int
    transport: str
    available: bool
    unavailable_reason: str
    profile_keys: tuple[str, ...]


def _decode_bcd(value: int) -> int:
    high, low = value >> 4, value & 0x0F
    if high > 9 or low > 9:
        raise ValueError(f"invalid coding-index BCD 0x{value:02X}")
    return high * 10 + low


_CATALOG = json.loads(zlib.decompress(base64.b85decode(CATALOG_PAYLOAD_B85)))
if _CATALOG.get("schema") != 1:
    raise RuntimeError("unsupported embedded vehicle-coding catalog")
_RAW_PROFILES = {profile["key"]: profile for profile in _CATALOG["profiles"]}


def _choice(value: dict) -> CodingChoice:
    return CodingChoice(
        value["value"], value["label"], value["reference"],
        tuple(bytes.fromhex(item) for item in value["data"]),
    )


@lru_cache(maxsize=None)
def profile_by_key(key: str) -> CodingProfile:
    value = _RAW_PROFILES[key]
    return CodingProfile(
        value["key"], value["target"], value["chassis"], value["family"],
        value["name"], value["address"], value["transport"],
        value["coding_index_raw"], tuple(value["coding_indexes_raw"]),
        value["memory_structure"],
        tuple(CodingRegion(**region) for region in value["regions"]),
        tuple(CodingSetting(
            setting["key"], setting["label"], setting["description"],
            setting["reference"], setting["level"],
            tuple(CodingPart(
                part["region"], part["offset"], bytes.fromhex(part["mask"])
            ) for part in setting["parts"]),
            tuple(_choice(choice) for choice in setting["choices"]),
            tuple(setting["toggle"]) if setting["toggle"] else None,
        ) for setting in value["settings"]),
    )


class _ProfileRegistry(Mapping[str, CodingProfile]):
    def __getitem__(self, key: str) -> CodingProfile:
        return profile_by_key(key)

    def __iter__(self) -> Iterator[str]:
        return iter(_RAW_PROFILES)

    def __len__(self) -> int:
        return len(_RAW_PROFILES)


PROFILE_BY_KEY: Mapping[str, CodingProfile] = _ProfileRegistry()
CODING_PROFILES = PROFILE_BY_KEY.values()
CODING_TARGETS = tuple(CodingTarget(
    value["key"], value["chassis"], value["group"], value["name"],
    value["address"], value["transport"], value["available"],
    value["unavailable_reason"], tuple(value["profile_keys"]),
) for value in _CATALOG["targets"])
TARGET_BY_KEY = {target.key: target for target in CODING_TARGETS}


def targets_for_chassis(chassis: str) -> tuple[CodingTarget, ...]:
    chassis = str(chassis).upper()
    return tuple(target for target in CODING_TARGETS if target.chassis == chassis)


def profiles_for_target(target_key: str) -> tuple[CodingProfile, ...]:
    target = TARGET_BY_KEY.get(target_key)
    if target is None:
        return ()
    return tuple(profile_by_key(key) for key in target.profile_keys)


def resolve_profile(target_key: str, coding_index_raw: int) -> CodingProfile | None:
    candidates = tuple(
        profile for profile in profiles_for_target(target_key)
        if coding_index_raw in profile.coding_indexes_raw
    )
    exact = tuple(
        profile for profile in candidates
        if profile.coding_index_raw == coding_index_raw
    )
    pool = exact or candidates
    return pool[0] if len(pool) == 1 else None


# Compatibility names used by older callers while they migrate to target keys.
GM3_PROFILES = profiles_for_target("e39_gm3")
SM_E46_PROFILES = profiles_for_target("e46_seat")


def profiles_for_module(module_key: str) -> tuple[CodingProfile, ...]:
    return profiles_for_target({"gm3": "e39_gm3", "sm_e46": "e46_seat"}.get(
        module_key, module_key
    ))
