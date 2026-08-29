from vehicle_coding_profiles import (
    CODING_TARGETS,
    PROFILE_BY_KEY,
    TARGET_BY_KEY,
    profiles_for_target,
    resolve_profile,
    targets_for_chassis,
)


def test_catalog_has_complete_humanized_target_matrix():
    assert {target.chassis for target in CODING_TARGETS} == {
        "E36", "E38", "E39", "E46",
    }
    assert len(CODING_TARGETS) == 50
    assert len(PROFILE_BY_KEY) == 178
    assert all(target.profile_keys for target in CODING_TARGETS)
    assert all(target.name and target.group for target in CODING_TARGETS)
    assert max(
        sum(setting.level == "basic" for setting in profile.settings)
        for profile in PROFILE_BY_KEY.values()
    ) <= 24

    e36 = {target.key: target for target in targets_for_chassis("e36")}
    assert not e36["e36_gm4"].available
    assert "ADS/L-line" in e36["e36_gm4"].unavailable_reason
    assert e36["e36_compact_cluster"].available
    assert e36["e36_asc5"].available
    assert e36["e36_mk60"].available


def test_every_embedded_choice_round_trips_without_touching_other_bits():
    for profile in PROFILE_BY_KEY.values():
        regions = {region.key: region for region in profile.regions}
        assert len(regions) == len(profile.regions)
        assert len({setting.key for setting in profile.settings}) == len(
            profile.settings)
        for setting in profile.settings:
            assert setting.label and setting.description and setting.reference
            assert setting.level in {"basic", "advanced"}
            assert len(setting.choices) >= 2
            assert len({choice.value for choice in setting.choices}) == len(
                setting.choices)
            for part in setting.parts:
                assert part.region in regions
                assert part.offset + len(part.mask) <= regions[part.region].length
                assert any(part.mask)
            for choice in setting.choices:
                data = {
                    key: bytearray([0xA5] * region.length)
                    for key, region in regions.items()
                }
                before = {key: bytes(value) for key, value in data.items()}
                setting.apply(data, choice.value)
                decoded = setting.decode({key: bytes(value) for key, value in data.items()})
                expected = (
                    choice.value == setting.toggle[0]
                    if setting.toggle else choice.value
                )
                assert decoded == expected, (profile.key, setting.reference)
                owned = {(part.region, part.offset + index)
                         for part in setting.parts
                         for index in range(len(part.mask))}
                assert all(
                    byte == before[key][index]
                    for key, value in data.items()
                    for index, byte in enumerate(value)
                    if (key, index) not in owned
                )


def test_exact_profile_resolution_and_useful_window_setting():
    profile = resolve_profile("e39_gm3", 0x04)
    assert profile is PROFILE_BY_KEY["E39.GM3.C04"]
    assert resolve_profile("e39_gm3", 0x01) is None
    assert profiles_for_target("missing") == ()
    assert TARGET_BY_KEY[profile.target].name == "General Module (GM3)"

    comfort = next(
        setting for setting in profile.settings
        if setting.reference == "KOMFORTOEFFNUNG"
    )
    assert comfort.level == "basic"
    assert "Comfort opening" in comfort.label
    assert comfort.toggle is not None
    assert comfort.parts[0].region == "b0"


def test_unknown_current_choice_is_preserved_not_fatal():
    profile = PROFILE_BY_KEY["E46.SM_E46.C01"]
    timing = next(
        setting for setting in profile.settings
        if setting.reference == "AUT_SITZVERSTELLUNG"
    )
    regions = {"whole": b"\x02"}
    assert timing.decode(regions) is None

    updated = {"whole": bytearray(regions["whole"])}
    one_touch = next(
        setting for setting in profile.settings
        if setting.reference == "MEMORY_TIPP_BETRIEB"
    )
    one_touch.apply(updated, True)
    assert updated["whole"][0] & 0x03 == 0x02


def test_newer_lcm_profiles_keep_explicit_selector_blocks():
    profile = PROFILE_BY_KEY["E39.LCM.C20"]
    parameters = [
        setting for setting in profile.settings
        if setting.reference.startswith("PROGRAMMPARAMETER_LCM_")
    ]

    assert [setting.parts[0].region for setting in parameters] == [
        f"b{selector}" for selector in range(12, 23)
    ]
    assert all(setting.parts[0].offset == 0 for setting in parameters)
