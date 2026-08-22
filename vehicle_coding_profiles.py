"""Reviewed, self-contained vehicle-coding profiles.

The catalog intentionally contains only exact Concept-6 profiles whose coding
bytes are understood. External research data is not a runtime dependency.
Unknown coding indices must remain read-only.
"""

from dataclasses import dataclass
from typing import Literal


CodingLevel = Literal["basic", "advanced"]
CodingTransport = Literal["gm3_selected", "whole_block"]


@dataclass(frozen=True)
class CodingChoice:
    value: str
    label: str
    raw_value: int


@dataclass(frozen=True)
class CodingSetting:
    key: str
    label: str
    description: str
    reference: str
    section: str
    level: CodingLevel
    byte_index: int
    mask: int
    active_when_set: bool = True
    choices: tuple[CodingChoice, ...] = ()

    def decode(self, data: bytes) -> bool | str:
        if self.choices:
            raw = data[self.byte_index] & self.mask
            for choice in self.choices:
                if choice.raw_value == raw:
                    return choice.value
            raise ValueError(
                f"unsupported {self.reference} value 0x{raw:02X}")
        return bool(data[self.byte_index] & self.mask) == self.active_when_set

    def apply(self, data: bytearray, value: bool | str) -> None:
        if self.choices:
            choice = next(
                (choice for choice in self.choices if choice.value == value), None)
            if choice is None:
                raise ValueError(f"invalid {self.key} choice: {value!r}")
            data[self.byte_index] = (
                (data[self.byte_index] & ~self.mask)
                | (choice.raw_value & self.mask)
            )
            return
        if type(value) is not bool:
            raise ValueError(f"{self.key} must be on or off")
        if value == self.active_when_set:
            data[self.byte_index] |= self.mask
        else:
            data[self.byte_index] &= ~self.mask & 0xFF


@dataclass(frozen=True)
class CodingProfile:
    key: str
    module_key: str
    module_name: str
    address: int
    coding_index: int
    diagnostic_index: int
    # Coding bytes only; the module checksum is separate.
    data_length: int
    features: tuple[CodingSetting, ...]
    transport: CodingTransport = "gm3_selected"
    ident_bmw_numbers: tuple[bytes, ...] = ()

    def matches_ident(self, response: bytes) -> bool:
        return (
            len(response) >= 10
            and (self.diagnostic_index is None
                 or response[9] == self.diagnostic_index)
            and (not self.ident_bmw_numbers
                 or response[3:7] in self.ident_bmw_numbers)
        )


def _setting(
    key: str,
    label: str,
    description: str,
    reference: str,
    section: str,
    level: CodingLevel,
    byte_index: int,
    mask: int,
    active_when_set: bool = True,
    choices: tuple[CodingChoice, ...] = (),
) -> CodingSetting:
    return CodingSetting(
        key, label, description, reference, section, level,
        byte_index, mask, active_when_set, choices,
    )


def _gm3_features(
    *,
    door_byte: int,
    remote_byte: int,
    memory_byte: int,
    confirmation_byte: int,
    auto_lock_byte: int,
    auto_lock_mask: int,
    auto_relock: tuple[int, int] | None,
) -> tuple[CodingSetting, ...]:
    features = [
        _setting(
            "door_open", "Open windows with the door key",
            "Hold the key in the door unlock position to open the windows and sunroof.",
            "KOMFORTOEFFNUNG", "Windows and sunroof", "basic",
            door_byte, 0x04, False,
        ),
        _setting(
            "door_close", "Close windows with the door key",
            "Hold the key in the door lock position to close the windows and sunroof.",
            "KOMFORTSCHLIESSUNG", "Windows and sunroof", "basic",
            door_byte, 0x08, False,
        ),
        _setting(
            "remote_open", "Open windows with the remote",
            "Hold Unlock on the remote to open the windows and sunroof.",
            "KOMFORTOEFFNUNG_FB", "Windows and sunroof", "basic",
            remote_byte, 0x01,
        ),
        _setting(
            "remote_close", "Close windows with the remote",
            "Hold Lock on the remote to close the windows and sunroof.",
            "KOMFORTSCHLIESSUNG_FB", "Windows and sunroof", "basic",
            remote_byte, 0x02,
        ),
        _setting(
            "auto_lock", "Lock the doors after driving away",
            "Automatically lock the doors after the car begins moving.",
            "VERRIEGELN_AUT_AB_X_KM/H", "Locks", "basic",
            auto_lock_byte, auto_lock_mask,
        ),
        _setting(
            "key_memory", "Remember personal settings for each key",
            "Allow supported memory features to follow the key used to unlock the car.",
            "SCHLUESSELMEMORY", "Memory equipment", "advanced",
            memory_byte, 0x10,
        ),
        _setting(
            "mirror_memory", "Enable mirror memory",
            "Enable this only when compatible memory mirrors are installed.",
            "SPIEGELMEMORY", "Memory equipment", "advanced",
            0, 0x80,
        ),
        _setting(
            "steering_column_memory", "Enable steering-column memory",
            "Enable this only when the powered memory steering column is installed.",
            "LENKSAEULENMEMORY", "Memory equipment", "advanced",
            1, 0x08,
        ),
        _setting(
            "driver_seat_memory", "Driver seat-memory module installed",
            "Tell the General Module that a compatible driver seat-memory module is installed.",
            "PM_SITZMEMORY", "Memory equipment", "advanced",
            memory_byte, 0x02,
        ),
        _setting(
            "passenger_seat_memory", "Passenger seat-memory module installed",
            "Tell the General Module that a compatible passenger seat-memory module is installed.",
            "PM_SITZMEMORY_BEIFAHRER", "Memory equipment", "advanced",
            memory_byte, 0x04,
        ),
        _setting(
            "visual_lock_confirmation", "Flash the lights when locking",
            "Use the optical acknowledgement supported by the fitted alarm configuration.",
            "QUIT_OPT_SCHAERF", "Lock confirmation", "advanced",
            confirmation_byte, 0x02,
        ),
        _setting(
            "acoustic_lock_confirmation", "Sound a confirmation when locking",
            "Use the acoustic acknowledgement supported by the fitted alarm configuration.",
            "QUIT_AKUST_SCHAERF", "Lock confirmation", "advanced",
            confirmation_byte, 0x04,
        ),
        _setting(
            "visual_unlock_confirmation", "Flash the lights when unlocking",
            "Use the optical acknowledgement supported by the fitted alarm configuration.",
            "QUIT_OPT_ENTSCH", "Lock confirmation", "advanced",
            confirmation_byte, 0x08,
        ),
        _setting(
            "acoustic_unlock_confirmation", "Sound a confirmation when unlocking",
            "Use the acoustic acknowledgement supported by the fitted alarm configuration.",
            "QUIT_AKUST_ENTSCH", "Lock confirmation", "advanced",
            confirmation_byte, 0x10,
        ),
    ]
    if auto_relock is not None:
        features.insert(5, _setting(
            "auto_relock", "Relock automatically after two minutes",
            "Enable the General Module's automatic two-minute relock behavior.",
            "VERRIEGELN_AUT_NACH_2_MIN", "Locks", "basic",
            *auto_relock,
        ))
    return tuple(features)


GM3_PROFILES = (
    CodingProfile(
        "GM3.C04", "gm3", "General Module (GM3)", 0x00, 4, 0x25, 17,
        _gm3_features(
            door_byte=6, remote_byte=13, memory_byte=3,
            confirmation_byte=9, auto_lock_byte=1, auto_lock_mask=0x02,
            auto_relock=None,
        ),
    ),
    CodingProfile(
        "GM3.C05", "gm3", "General Module (GM3)", 0x00, 5, 0x25, 17,
        _gm3_features(
            door_byte=6, remote_byte=13, memory_byte=3,
            confirmation_byte=9, auto_lock_byte=1, auto_lock_mask=0x02,
            auto_relock=(13, 0x04),
        ),
    ),
    CodingProfile(
        "GM3.C10", "gm3", "General Module (GM3)", 0x00, 10, 0x25, 25,
        _gm3_features(
            door_byte=11, remote_byte=17, memory_byte=5,
            confirmation_byte=9, auto_lock_byte=15, auto_lock_mask=0x08,
            auto_relock=(16, 0x08),
        ),
    ),
)

SM_E46_PROFILES = (
    CodingProfile(
        "SM_E46.C01", "sm_e46", "Driver Seat Memory (E46)",
        0x72, 1, None, 1,
        (
            _setting(
                "automatic_seat_adjustment_timing",
                "Automatic seat adjustment",
                "Choose when the saved driver-seat position is recalled after unlocking.",
                "AUT_SITZVERSTELLUNG", "Seat memory", "basic", 0, 0x03,
                choices=(
                    CodingChoice("unlock", "After remote unlock", 0),
                    CodingChoice(
                        "unlock_and_door",
                        "After unlock and opening the driver's door",
                        1,
                    ),
                    CodingChoice("off", "Off", 3),
                ),
            ),
            _setting(
                "one_touch_memory", "One-touch seat-memory buttons",
                "Recall a saved seat position with one press instead of holding the button.",
                "MEMORY_TIPP_BETRIEB", "Seat memory", "basic", 0, 0x04,
            ),
        ),
        transport="whole_block",
        ident_bmw_numbers=tuple(bytes.fromhex(value) for value in (
            "08 09 90 67", "08 09 90 68", "08 26 31 33",
            "08 26 31 34", "08 09 92 37", "08 09 92 38",
        )),
    ),
)

CODING_PROFILES = GM3_PROFILES + SM_E46_PROFILES
PROFILE_BY_KEY = {profile.key: profile for profile in CODING_PROFILES}


def profiles_for_module(module_key: str) -> tuple[CodingProfile, ...]:
    return tuple(
        profile for profile in CODING_PROFILES
        if profile.module_key == module_key
    )
