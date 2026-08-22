import pytest

from vehicle_coding_profiles import (
    CODING_PROFILES,
    GM3_PROFILES,
    PROFILE_BY_KEY,
    SM_E46_PROFILES,
    profiles_for_module,
)


def _feature(profile_key, feature_key):
    return next(
        feature for feature in PROFILE_BY_KEY[profile_key].features
        if feature.key == feature_key
    )


@pytest.mark.parametrize(
    "profile_key,data_length,expected",
    (
        ("GM3.C04", 17, {
            "door_open": (6, 0x04, False),
            "door_close": (6, 0x08, False),
            "remote_open": (13, 0x01, True),
            "remote_close": (13, 0x02, True),
            "auto_lock": (1, 0x02, True),
        }),
        ("GM3.C05", 17, {
            "door_open": (6, 0x04, False),
            "remote_open": (13, 0x01, True),
            "auto_lock": (1, 0x02, True),
            "auto_relock": (13, 0x04, True),
        }),
        ("GM3.C10", 25, {
            "door_open": (11, 0x04, False),
            "remote_open": (17, 0x01, True),
            "auto_lock": (15, 0x08, True),
            "auto_relock": (16, 0x08, True),
        }),
    ),
)
def test_exact_gm3_profile_layouts(profile_key, data_length, expected):
    profile = PROFILE_BY_KEY[profile_key]

    assert (profile.module_key, profile.address, profile.coding_index,
            profile.diagnostic_index) == (
        "gm3", 0x00, int(profile_key[-2:]), 0x25
    )
    assert profile.data_length == data_length
    assert {
        key: (feature.byte_index, feature.mask, feature.active_when_set)
        for key in expected
        for feature in (_feature(profile_key, key),)
    } == expected


def test_active_low_and_active_high_settings_preserve_other_bits():
    door_open = _feature("GM3.C05", "door_open")
    remote_open = _feature("GM3.C05", "remote_open")
    data = bytearray([0xFF] * 17)

    door_open.apply(data, True)
    remote_open.apply(data, False)

    assert data[6] == 0xFB
    assert data[13] == 0xFE
    assert door_open.decode(data) is True
    assert remote_open.decode(data) is False
    assert all(byte == 0xFF for index, byte in enumerate(data) if index not in (6, 13))


def test_catalog_is_exact_and_internally_consistent():
    assert tuple(PROFILE_BY_KEY) == (
        "GM3.C04", "GM3.C05", "GM3.C10", "SM_E46.C01",
    )
    assert profiles_for_module("gm3") == GM3_PROFILES
    assert profiles_for_module("unknown") == ()
    assert profiles_for_module("sm_e46") == SM_E46_PROFILES

    for profile in CODING_PROFILES:
        keys = [feature.key for feature in profile.features]
        assert len(keys) == len(set(keys))
        assert {feature.level for feature in profile.features} <= {"basic", "advanced"}
        assert "basic" in {feature.level for feature in profile.features}
        for feature in profile.features:
            assert feature.reference
            assert 0 <= feature.byte_index < profile.data_length
            assert 0 < feature.mask <= 0x80
            if feature.choices:
                assert len({choice.value for choice in feature.choices}) == len(
                    feature.choices)
                assert all(choice.raw_value & ~feature.mask == 0
                           for choice in feature.choices)
            else:
                assert feature.mask & (feature.mask - 1) == 0


def test_e46_seat_choice_and_toggle_preserve_unrelated_bits():
    profile = PROFILE_BY_KEY["SM_E46.C01"]
    timing, one_touch = profile.features
    data = bytearray((0xF8,))

    timing.apply(data, "unlock_and_door")
    one_touch.apply(data, True)

    assert data == bytearray((0xFD,))
    assert timing.decode(data) == "unlock_and_door"
    assert one_touch.decode(data) is True
    assert [(choice.value, choice.raw_value) for choice in timing.choices] == [
        ("unlock", 0), ("unlock_and_door", 1), ("off", 3),
    ]
