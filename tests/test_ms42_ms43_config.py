"""Configuration stays inside the matching calibration and uses existing images."""

from types import SimpleNamespace

import pytest

import ms42_ms43_config as config
from ms42_ms43_image import PROFILES, checksum_status, correct_checksums, extract_calibration
from tests.test_ms42_ms43_image import synthetic_image


@pytest.mark.parametrize("family", ("MS42", "MS43"))
@pytest.mark.parametrize("partial", (False, True))
def test_every_stock_setting_roundtrips_without_changing_other_bytes(family, partial):
    full = synthetic_image(family)
    data = extract_calibration(full) if partial else bytes(full)
    before = bytes(data)
    base = 0 if partial else PROFILES[family].calibration_offset
    software = "0110C6" if family == "MS42" else "430069"
    for feature in config.features_for(family, software):
        if feature.full_file_only:
            continue  # Paired full-image metadata has its own preservation contract below.
        for label, value in feature.options:
            edited = config.apply_config(data, {feature.name: label}, family, software)
            expected = bytearray(data)
            expected[base + feature.byte] = value
            assert edited == bytes(expected)
            assert config.read_config(edited)[feature.name] == label
    assert bytes(data) == before


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_edit_uses_existing_checksum_workflow(family):
    source = synthetic_image(family)
    edited = config.apply_config(source, {"Electrical cooling fan": "Fitted"})
    corrected, _ = correct_checksums(edited, family)
    status = checksum_status(corrected, family)
    assert status["cal"] and status["program"] and status["boot"]
    assert config.read_config(corrected)["Electrical cooling fan"] == "Fitted"


@pytest.mark.parametrize("family,software,spans", [
    ("MS42", "0110C6", [(0x4816D, 1), (0x481EF, 96), (0x48287, 28), (0x48319, 4), (0x483AA, 37)]),
    ("MS43", "430069", [(0x70A83, 1), (0x70B16, 21), (0x70BBB, 1), (0x70C4E, 8), (0x70CD7, 38), (0x70DEF, 34)]),
])
def test_live_read_uses_small_spans_no_identity_reread(family, software, spans):
    data = synthetic_image(family)
    calls = []
    ecu = SimpleNamespace(family=family)
    ecu.read_memory = lambda at, count: calls.append((at, count)) or bytes(data[at:at + count])
    assert config.read_live_config(ecu, software) == config.read_config(extract_calibration(data))
    assert calls == spans
    ecu.read_memory = lambda *_: b""
    with pytest.raises(ValueError, match="Short"):
        config.read_live_config(ecu, software)


def test_later_configuration_read_failure_is_not_reported_as_complete():
    data = synthetic_image("MS43")
    calls = []

    def read(at, count):
        calls.append((at, count))
        return bytes(data[at:at + count]) if len(calls) == 1 else b""

    with pytest.raises(ValueError, match="Short configuration"):
        config.read_live_config(SimpleNamespace(family="MS43", read_memory=read), "430069")
    assert calls == [(0x70A83, 1), (0x70B16, 21)]


def test_native_switch_polarities_and_multistate_values():
    data = synthetic_image("MS43")
    edited = config.apply_config(data, {
        "DS2 bulk-adaptation writes": "Allowed with native conditions",
        "Ambient temperature plausibility diagnosis": "Enabled",
        "Accelerator / brake plausibility": "Limit pedal demand and report faults",
    })
    assert edited[0x70DEF] == edited[0x70E03] == 0
    assert edited[0x70E05] == 2
    with pytest.raises(ValueError, match="Invalid choice"):
        config.apply_config(data, {"Accelerator / brake plausibility": "Custom (0x03)"})


def test_unverified_definition_labels_are_not_exposed_as_switches():
    ms42_offsets = {f.byte for f in config.features_for("MS42", "0110C6")}
    ms43_offsets = {f.byte for f in config.features_for("MS43", "430069")}
    assert 0x28D not in ms42_offsets  # Named by XDF, no verified native consumer.
    assert 0xC53 not in ms43_offsets  # AMT state ST lacks a verified user-facing meaning.
    assert 0xBDD not in ms43_offsets  # Factory test mode has no ordinary configuration role.


@pytest.mark.parametrize("family", ("MS42", "MS43"))
@pytest.mark.parametrize("partial", (False, True))
def test_custom_purge_thresholds_survive_display_and_unrelated_edits(family, partial):
    full = synthetic_image(family)
    software = "0110C6" if family == "MS42" else "430069"
    features = {feature.name: feature for feature in config.features_for(family, software)}
    for name in ("EVAP purge operation", "Purge-valve electrical diagnosis", "Automatic purge-flow diagnosis"):
        full[PROFILES[family].calibration_offset + features[name].byte] = 200
    source = extract_calibration(full) if partial else bytes(full)
    selections = config.read_config(source)
    assert selections["EVAP purge operation"] == "Custom (0xC8)"
    assert config.apply_config(source, selections) == source
    selections["Electrical cooling fan"] = "Fitted"
    expected = bytearray(source)
    base = 0 if partial else PROFILES[family].calibration_offset
    expected[base + features["Electrical cooling fan"].byte] = 1
    assert config.apply_config(source, selections) == expected


@pytest.mark.parametrize("family,offset", [
    ("MS42", 0x16D), ("MS43", 0xC4E), ("MS43", 0xC54), ("MS43", 0xC55),
])
@pytest.mark.parametrize("partial", (False, True))
@pytest.mark.parametrize("value", (2, 0xFF))
def test_custom_flap_actuation_values_are_preserved(family, offset, partial, value):
    full = synthetic_image(family)
    full[PROFILES[family].calibration_offset + offset] = value
    source = extract_calibration(full) if partial else bytes(full)
    software = "0110C6" if family == "MS42" else "430069"
    feature = next(f for f in config.features_for(family, software) if f.byte == offset)
    current = config.read_config(source)[feature.name]
    assert current == f"Custom (0x{value:02X})"
    assert config.apply_config(source, {feature.name: current}) == source


@pytest.mark.parametrize("partial", [False, True])
def test_monitor_checksum_setting_changes_calibration_only(partial):
    full = synthetic_image("MS43")
    full[0x70E07] = 1
    full = correct_checksums(full, "MS43")[0]
    source = extract_calibration(full) if partial else full
    context = full if partial else None
    edited = config.apply_config(source, {"Calibration monitor checksum": "Disabled"})
    offset = 0xE07 if partial else 0x70E07
    assert [i for i, (a, b) in enumerate(zip(source, edited)) if a != b] == [offset]
    assert edited[offset] == 0xA5
    corrected = correct_checksums(edited, "MS43", context)[0]
    assert config.read_config(corrected)["Calibration monitor checksum"] == "Disabled"
    assert checksum_status(corrected, "MS43", context)["cal"] is True
    if not partial:
        assert corrected[:0x70000] == full[:0x70000]
        assert checksum_status(corrected, "MS43")["program"] is True


@pytest.mark.parametrize("label,metadata,marker", [("Disabled", b"\xFF\xFF", 0xA8), ("Enabled", b"\0\0", 0)])
def test_ms42_calibration_crc_is_a_paired_full_image_metadata_edit(label, metadata, marker):
    data = synthetic_image("MS42")
    before = bytes(data)
    result = config.apply_config(data, {"Calibration CRC": label})
    expected = bytearray(data)
    expected[0x4FEFE:0x4FF00] = metadata
    expected[0x5002C] = marker
    assert result == expected and bytes(data) == before
    corrected = correct_checksums(result, "MS42")[0]
    assert config.read_config(corrected)["Calibration CRC"] == label
    assert all(checksum_status(corrected, "MS42")[key] for key in ("boot", "program", "cal"))
    partial = extract_calibration(data)
    assert config.read_config(partial)["Calibration CRC"] == config.FULL_IMAGE_REQUIRED
    with pytest.raises(ValueError, match="requires a full ROM file"):
        config.apply_config(partial, {"Calibration CRC": label})


@pytest.mark.parametrize("metadata,marker", [(0xFFFF, 0), (0, 0xA8), (0x1234, 0x56)])
def test_ms42_unknown_crc_metadata_combination_is_preserved(metadata, marker):
    data = synthetic_image("MS42")
    data[0x4FEFE:0x4FF00] = metadata.to_bytes(2, "little")
    data[0x5002C] = marker
    current = config.read_config(data)
    assert current["Calibration CRC"].startswith("Custom (")
    assert config.apply_config(data, current) == data
    with pytest.raises(ValueError, match="Invalid choice"):
        config.apply_config(data, {"Calibration CRC": "Custom (0x654321)"})


def test_unknown_current_value_can_be_preserved_but_not_invented():
    data = synthetic_image("MS43")
    data[0x70B1D] = 0xA7
    assert config.read_config(data)["Electrical cooling fan"] == "Custom (0xA7)"
    assert config.apply_config(data, {"Electrical cooling fan": "Custom (0xA7)"}) == data
    with pytest.raises(ValueError, match="Invalid choice"):
        config.apply_config(data, {"Electrical cooling fan": "Custom (0xA8)"})
    with pytest.raises(ValueError, match="Unknown configuration"):
        config.apply_config(data, {"EWS delete": "Enabled"})


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_unknown_or_mismatched_software_cannot_write_known_offsets(family):
    data = synthetic_image(family)
    with pytest.raises(ValueError, match="software does not match"):
        config.apply_config(data, {}, family, "999999")
    data[PROFILES[family].sw_offset:PROFILES[family].sw_offset + 6] = b"999999"
    with pytest.raises(ValueError, match="No verified"):
        config.read_config(data)
    ecu = SimpleNamespace(family=family, read_memory=lambda *_: pytest.fail("No request expected"))
    with pytest.raises(ValueError, match="No verified"):
        config.read_live_config(ecu, "999999")
    assert config.features_for(family, "999999") == ()


def test_mismatched_full_calibration_is_not_edited():
    data = synthetic_image("MS43")
    data[0x70008:0x7000E] = b"430066"
    with pytest.raises(ValueError, match="do not match"):
        config.apply_config(data, {"Electrical cooling fan": "Fitted"})


def test_native_ms42_lambda_mapping_does_not_copy_ms43_learning_case_four():
    ms42 = next(f for f in config.features_for("MS42", "0110C6")
                if f.name == "Lambda sensor configuration")
    ms43 = next(f for f in config.features_for("MS43", "430069")
                if f.name == "Lambda sensor configuration")
    assert [value for _, value in ms42.options] == [0, 1, 2, 3]
    assert [value for _, value in ms43.options] == [0, 1, 2, 3, 4]
    assert config.features_for("MS41.2", "1406464") == ()
