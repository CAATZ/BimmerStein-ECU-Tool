"""Revision program options retain the existing file and live Config workflow."""

from types import SimpleNamespace

import pytest

import ms42_ms43_config as stock
import ms42_ms43_config_patches as config
import ms42_ms43_image as images
from tests.ms42_ms43_revision_sources import composed_image
from tests.test_ms42_ms43_revision_patches_native import LAYOUTS, source


KEYS = ("0110C7", "0110CA", *LAYOUTS)
OPTIONS = ("Immobilizer bypass", "Calibration checksum bypass")


def reference(key):
    return composed_image(key, "0110C6").image if key.startswith("0110") else source(key)


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("option", OPTIONS)
def test_exact_reference_enable_disable_and_checksum_correction(key, option):
    original = reference(key)
    identity = images.image_identity(original)
    patch = next(item for item in config.patches_for(identity["family"], identity["software_id"])
                 if item.name == option)
    assert config.read_config(original)[option] == "Disabled"
    enabled = config.apply_config(original, {option: "Enabled"})
    expected = bytearray(original)
    for offset, before, after in patch.entries:
        assert original[offset:offset + len(before)] == before
        expected[offset:offset + len(after)] = after
    assert enabled == expected
    assert enabled[:0x10000] == original[:0x10000]
    assert config.read_config(enabled)[option] == "Enabled"
    assert config.apply_config(enabled, {option: "Enabled"}) == enabled
    assert config.apply_config(enabled, {option: "Disabled"}) == original
    corrected, _ = images.correct_checksums(enabled, identity["family"])
    status = images.checksum_status(corrected, identity["family"])
    assert all(status[part] for part in ("boot", "program", "cal"))
    assert config.read_config(corrected)[option] == "Enabled"


@pytest.mark.parametrize("key", KEYS)
def test_live_state_adds_only_patch_bytes_and_requests_existing_full_write(key):
    image = reference(key)
    identity = images.image_identity(image)
    calls = []
    def read(address, count):
        calls.append((address, count))
        offset = address - 0x80000 if identity["family"] == "MS43" and address >= 0x90000 else address
        return image[offset:offset + count]
    ecu = SimpleNamespace(family=identity["family"], read_memory=read)
    values = stock.read_live_config(ecu, identity["software_id"])
    stock_bytes = sum(count for _, count in calls)
    calls.clear()
    actual = config.read_live_config(ecu, identity["software_id"])
    assert actual == {**values, **dict.fromkeys(OPTIONS, "Disabled")}
    assert sum(count for _, count in calls) == stock_bytes + 18
    assert config.requires_full_write({OPTIONS[0]: "Enabled"}, actual,
                                      identity["family"], identity["software_id"])
    assert not config.requires_full_write(actual, actual,
                                          identity["family"], identity["software_id"])


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("owner", ("boot", "application"))
def test_unknown_checksum_owner_blocks_only_its_requested_edit(key, owner):
    image = bytearray(reference(key))
    if key.startswith("0110"):
        offset = 0xE88 if owner == "boot" else 0x1C3C8
    else:
        offset = 0x1628 if owner == "boot" else LAYOUTS[key][2]
    image[offset] ^= 1
    before = bytes(image)
    assert config.read_config(image)[OPTIONS[1]] == "Custom / unsupported bytes"
    with pytest.raises(ValueError, match="unrecognized checksum handler"):
        config.apply_config(image, {OPTIONS[1]: "Enabled"})
    assert bytes(image) == before
    assert config.read_config(config.apply_config(image, {OPTIONS[0]: "Enabled"}))[OPTIONS[0]] == "Enabled"


@pytest.mark.parametrize("key", KEYS)
def test_partial_images_keep_program_options_read_only(key):
    tune = images.extract_calibration(reference(key))
    values = config.read_config(tune)
    assert all(values[name] == stock.FULL_IMAGE_REQUIRED for name in OPTIONS)
    for name in OPTIONS:
        with pytest.raises(ValueError, match="full ROM"):
            config.apply_config(tune, {name: "Enabled"})


def test_065_factory_crc_permissions_are_checked_and_never_rewritten():
    original = reference("430065")
    assert original[0x6FFB0] == 0xA8 and original[0x61735] == 0x94
    for label in ("Enabled", "Disabled"):
        image = config.apply_config(original, {OPTIONS[1]: label})
        assert image[0x6FFB0] == 0xA8 and image[0x61735] == 0x94
    for offset in (0x6FFB0, 0x61735):
        bad = bytearray(original)
        bad[offset] ^= 1
        assert config.read_config(bad)[OPTIONS[1]] == "Custom / unsupported bytes"
        with pytest.raises(ValueError, match="unrecognized"):
            config.apply_config(bad, {OPTIONS[1]: "Enabled"})


@pytest.mark.parametrize("key", ("430066", "430066-later"))
@pytest.mark.parametrize("partial", (False, True))
def test_both_066_programs_allow_their_runtime_tank_setting_without_extra_context(key, partial):
    image = reference(key)
    data = images.extract_calibration(image) if partial else image
    name = "Fuel tank level plausibility diagnosis"
    feature = next(item for item in stock.features_for("MS43", "430066") if item.name == name)
    offset = feature.byte if partial else 0x70000 + feature.byte
    for label in ("Enabled", "Disabled"):
        result = config.apply_config(data, {name: label})
        expected = bytearray(data)
        expected[offset] = feature.apply(data[offset], label)
        assert result == expected
        assert config.read_config(result)[name] == label


@pytest.mark.parametrize("key", ("430066", "430066-later"))
def test_live_patch_wrapper_needs_no_separate_program_context_read(key):
    image = reference(key)
    calls = []
    def read(address, count):
        calls.append((address, count))
        offset = address - 0x80000 if address >= 0x90000 else address
        return image[offset:offset + count]
    ecu = SimpleNamespace(family="MS43", read_memory=read)
    values = config.read_live_config(ecu, "430066")
    assert values == config.read_config(image)
    assert (0xC0D50, 18) not in calls
