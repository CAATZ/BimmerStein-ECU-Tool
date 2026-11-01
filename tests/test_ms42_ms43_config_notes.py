"""Configuration help describes the reviewed setting without hiding existing limits."""

import pytest

from ms42_ms43_config import features_for


@pytest.mark.parametrize("family,software", [("MS42", "0110C6"), ("MS43", "430069")])
def test_all_configuration_settings_have_descriptive_help(family, software):
    for feature in features_for(family, software):
        assert feature.note.strip(), feature.name
        assert feature.note != feature.name


@pytest.mark.parametrize("family,software,name,detail", [
    ("MS42", "0110C6", "Calibration CRC", "Requires a full ROM file."),
    ("MS42", "0110C6", "Lambda sensor configuration", "Reset learned variants"),
    ("MS43", "430069", "CAN calibration logic", "not a CAN communications on/off switch"),
    ("MS43", "430069", "DS2 bulk-adaptation writes", "not adaptation learning"),
    ("MS43", "430069", "Individual-cylinder cut during traction intervention", "does not disable ASC/DSC"),
    ("MS43", "430069", "Calibration monitor checksum", "Does not change program monitoring or flash CRC checks."),
])
def test_existing_configuration_limits_remain_visible(family, software, name, detail):
    notes = {feature.name: feature.note for feature in features_for(family, software)}
    assert detail in notes[name]
