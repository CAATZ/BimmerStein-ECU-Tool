"""Runtime tank diagnosis exists in both066 programs; mask cleanup differs."""

from types import SimpleNamespace

import pytest

import ms42_ms43_config as config
from ms42_ms43_image import extract_calibration
from tests.test_ms42_ms43_config_daten import packages as packages, native as native


NAME = "Fuel tank level plausibility diagnosis"


@pytest.mark.parametrize("enabled", (False, True))
def test_later_program_additionally_clears_both_tank_diagnosis_masks(packages, native, enabled):
    source = packages["430066-later"]
    assert source.image[0x40D50:0x40D62] == bytes.fromhex("f3f8050e3d06e6f4fdff64f4f49064f40a91")
    label = "Enabled" if enabled else "Disabled"
    image = config.apply_config(source.image, {NAME: label})
    expected = bytearray(source.image)
    expected[0x70E05] = int(enabled)
    assert image == expected
    emu = native["MS43"](image, experimental=True)
    for address in (0x90F4, 0x910A):
        emu.write(address, 0xA55B)
    assert emu.run_from(0xC0D50, stop_at=0xC0D62, max_steps=8).exit_reason == "stop_at"
    assert [emu.read(address) for address in (0x90F4, 0x910A)] == [0xA55B if enabled else 0xA559] * 2


@pytest.mark.parametrize("partial", (False, True))
@pytest.mark.parametrize("key,offset", [("430065", 0xE04), ("430066", 0xE05), ("430066-later", 0xE05)])
def test_runtime_supported_tank_setting_uses_ordinary_full_and_partial_workflows(packages, partial, key, offset):
    source = packages[key].image
    data = extract_calibration(source) if partial else source
    assert config.apply_config(data, config.read_config(data)) == data
    for label, value in (("Enabled", 1), ("Disabled", 0)):
        changed = config.apply_config(data, {NAME: label})
        expected = bytearray(data)
        expected[offset if partial else 0x70000 + offset] = value
        assert changed == expected
        assert config.read_config(changed)[NAME] == label


@pytest.mark.parametrize("key", ("430065", "430066", "430066-later"))
def test_live_tank_setting_needs_only_existing_compact_calibration_reads(packages, key):
    source = packages[key].image
    reads = []
    def read(address, size):
        reads.append((address, size))
        return source[address:address + size]
    live = config.read_live_config(SimpleNamespace(family="MS43", read_memory=read), key.split("-")[0])
    assert live[NAME] == config.read_config(source)[NAME]
    assert all(0x70000 <= address < address + size <= 0x80000 and size <= 123 for address, size in reads)
