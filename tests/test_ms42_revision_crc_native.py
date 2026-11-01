"""C7/CA CRC exception uses a known boot plus the actual supplied app owner."""

import pytest
import ms42_ms43_config as config

from tests.ms42_ms43_revision_sources import composed_image, full_image, package_memory
from tests.test_ms42_ms43_config_daten import native as native


@pytest.fixture(params=("0110C7", "0110CA"))
def source(request):
    return composed_image(request.param, "0110C6")


def test_crc_application_owner_is_present_and_identical_to_c6(source):
    memory = package_memory(source.software)
    assert all(at in memory for at in range(0x1C3C8, 0x1C410))
    assert source.image[0x1C3C8:0x1C410] == full_image("0110C6")[0x1C3C8:0x1C410]
    assert source.image[0x4FEFE:0x4FF00] == b"\0\0" and source.image[0x5002C] == 0
    assert config.ms42_crc_owner_matches(source.image)


def test_crc_edit_preserves_other_bytes_and_roundtrips(source):
    changed = config.apply_config(source.image, {"Calibration CRC": "Disabled"})
    expected = bytearray(source.image)
    expected[0x4FEFE:0x4FF00] = b"\xff\xff"
    expected[0x5002C] = 0xA8
    assert changed == expected
    assert config.read_config(changed)["Calibration CRC"] == "Disabled"
    assert config.apply_config(changed, {"Calibration CRC": "Enabled"}) == source.image


@pytest.mark.parametrize("address", (0xE88, 0xED2, 0x1C3C8, 0x1C3F8))
def test_changed_crc_owner_blocks_only_a_requested_crc_edit(source, address):
    image = bytearray(source.image)
    image[address] ^= 1
    assert not config.ms42_crc_owner_matches(image)
    assert config.apply_config(image, {"Calibration CRC": "Enabled"}) == image
    with pytest.raises(ValueError, match="CRC owners differ"):
        config.apply_config(image, {"Calibration CRC": "Disabled"})
    changed = config.apply_config(image, {"Electrical cooling fan": "Not fitted"})
    expected = bytearray(image)
    expected[0x4828A] = 0
    assert changed == expected


@pytest.mark.parametrize("boot", (False, True))
@pytest.mark.parametrize("metadata,marker,expected", (
    (0, 0, 8), (0, 0xA8, 8), (0xFFFF, 0, 0x18), (0xFFFF, 0xA8, 9)))
def test_both_native_crc_paths_require_the_coordinated_metadata(source, native, boot, metadata, marker, expected):
    emu = native["MS42"](source.image, experimental=True)
    emu.mem.image[0x4FEFE:0x4FF00] = metadata.to_bytes(2, "little")
    emu.mem.image[0x5002C] = marker
    image = bytes(emu.mem.image)
    store = 0x4FEE0
    count = int.from_bytes(image[store + 2:store + 4], "little")
    end = int.from_bytes(image[store + 8 + (count - 1) * 8:store + 12 + (count - 1) * 8], "little")
    emu.write(0xF756, int.from_bytes(image[store:store + 2], "little") ^ 1)
    if boot:
        emu.write(0xF72E, 2)
        emu.write_byte(0xF765, count - 1)
        emu.write(0xF760, (end + 1) & 0xFFFF)
        emu.write(0xF762, (end + 1) >> 16)
        result = emu.call(0xAC2, max_steps=500)
    else:
        emu.write(0xF72E, 8)
        result = emu.run_from(0x1C3C8, stop_at=0x1C410, max_steps=40)
    assert result.exit_reason == "stop_at"
    assert emu.read(0xF72E) == expected
    assert bytes(emu.mem.image) == image
