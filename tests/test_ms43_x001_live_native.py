"""Execute the supplied X001 producers and DS2 handlers; no serial hardware.

The canonical C167 emulator receives explicit runtime inputs. This establishes
the listed wire/RAM contracts, not an engine scheduler or physical validation.
"""

import hashlib
import os
from pathlib import Path

import pytest

from ds2 import DS2Error
from ms42_ms43 import MS42MS43ECU
import ms42_ms43_live as live
from tests.test_ms42_ms43 import Link, reply
from tests.test_ms42_ms43_config_native import native as native
from tests.test_ms42_ms43_live import capture, imported_definition


@pytest.fixture
def images(native):
    path = Path(os.environ.get("MS43_X001_IMAGE", Path.home() /
                               "ECU-reference-data/Siemens_MS43_MS43X001_E46_M54B30_US.bin"))
    if not path.is_file():
        pytest.skip("private X001 reference unavailable; set MS43_X001_IMAGE")
    image = path.read_bytes()
    assert hashlib.sha256(image).hexdigest() == "997975e61fb237094216052c300964dffd97e2ab57a94ddea5d710216389e21d"
    base = native("MS43")
    return type(base), bytes(base.mem.image), image


def write(emu, address, value, size=2):
    for index in range(size):
        emu.mem._physical_write(address + index, (value >> (8 * index)) & 255)


def read(emu, address, size):
    return bytes(emu.mem._physical_read(address + index) for index in range(size))


def native_ecu(emu):
    def respond(command, args):
        assert command in (6, 0x0B), "This fixture permits measurement reads only"
        for index, byte in enumerate(args):
            write(emu, 0xF6D0 + index, byte, 1)
        result = emu.call(0xA5C1E if command == 0x0B else 0xA6A38, max_steps=10000)
        assert result.exit_reason == "stop_at", result
        length = read(emu, 0xF74E, 1)[0] - 4
        assert 0 <= length <= 123
        return reply(read(emu, 0xF750, length), status=read(emu, 0xF74F, 1)[0])
    return MS42MS43ECU(Link(respond), "MS43")


def seed_measurements(emu, seed):
    for first, end in ((0x40000, 0x41200), (0xE000, 0xE600), (0xFBA0, 0xFCC0)):
        for address in range(first, end):
            write(emu, address, (address * 7 + seed) & 255, 1)
    write(emu, 0xFD1C, 4)


def test_used_native_bodies_and_lookup_tables_match_without_aliasing_changed_dispatch(images):
    _, base, x001 = images
    for first, end in ((0xA5CD0, 0xA6536), (0xA6A1C, 0xA6B64),
                       (0xA9320, 0xA93DC), (0xA9A00, 0xA9B6A),
                       (0xE184E, 0xE1B6E), (0xE5744, 0xE5804),
                       (0xEC62C, 0xEC6B4), (0xB5B78, 0xB60EA),
                       (0xB6108, 0xB6E88), (0xD0002, 0xD0536),
                       (0xD114A, 0xD116A), (0xD1308, 0xD135C),
                       (0xD174A, 0xD1782), (0xD1930, 0xD194C),
                       (0xD1B8E, 0xD1BBA)):
        assert x001[first - 0x80000:end - 0x80000] == base[first - 0x80000:end - 0x80000], hex(first)
    assert x001[0x25C1E:0x25CD0] != base[0x25C1E:0x25CD0]
    assert x001[0x360EA:0x36108] != base[0x360EA:0x36108]
    for axis, count in ((0x7400C, 16), (0x74095, 4)):
        assert x001[axis - 1] == count
        assert list(x001[axis:axis + count]) == sorted(x001[axis:axis + count])


@pytest.mark.parametrize("selector", (3, 4, 0x90, 0x91, 0x92, 0x93))
@pytest.mark.parametrize("seed", (0, 53))
def test_actual_x001_dispatch_matches_each_fixed_payload_and_decoder(images, selector, seed):
    target, base, x001 = images
    responses = []
    for image in (base, x001):
        emu = target(image, experimental=True)
        seed_measurements(emu, seed)
        ecu = native_ecu(emu)
        channels = [p for p in live.catalog_for("MS43", ecu_id="43X001") if p.selector == selector]
        payload = ecu.execute(0x0B, bytes((selector,)))
        values = live._group_read(ecu, channels)
        responses.append((payload, values))
        assert bytes(emu.mem.image) == image
    assert responses[0] == responses[1]


def test_native_selected_memory_and_adc_agree_with_every_direct_catalog_channel(images):
    target, _, image = images
    emu = target(image, experimental=True)
    seed_measurements(emu, 31)
    ecu = native_ecu(emu)
    channels = [p for p in live.catalog_for("MS43") if p.address is not None or p.adc is not None]
    expected = live._group_read(ecu, channels)
    for start in range(0, len(channels), 24):
        selected = channels[start:start + 24]
        assert ecu.execute(0x0B, live.build_telegram_setup("MS43", [p.descriptor for p in selected])) == b""
        payload = ecu.execute(0x0B, b"\x00")
        offset = 0
        for channel in selected:
            assert channel.decode(payload[offset:offset + channel.length]) == expected[channel.id]
            offset += channel.length
        assert offset == len(payload)
        assert ecu.execute(0x0B, b"\xFF") == b""
        assert not int.from_bytes(read(emu, 0xFD28, 2), "little") & 0x200
    assert bytes(emu.mem.image) == image


@pytest.mark.parametrize("args", (b"\x01\x00", b"\x01\x19",
                                  bytes.fromhex("01 01 01 00 00 E0 E5"),
                                  bytes.fromhex("01 01 00 00 0F 00 00"),
                                  bytes.fromhex("01 01 02 00 00 00 1B")))
def test_x001_native_selected_memory_rejects_invalid_descriptors(images, args):
    target, _, image = images
    emu = target(image, experimental=True)
    with pytest.raises(DS2Error):
        native_ecu(emu).execute(0x0B, args)
    assert not int.from_bytes(read(emu, 0xFD28, 2), "little") & 0x200
    assert bytes(emu.mem.image) == image


@pytest.mark.parametrize("software", ("43X001", "MS43X001"))
def test_x001_live_worker_uses_native_telegrams_and_keeps_supply_rails_distinct(images, software, monkeypatch):
    target, _, image = images
    emu = target(image, experimental=True)
    write(emu, 0xE572, 25 << 2)
    write(emu, 0xE570, 122 << 2)
    result = emu.run_from(0xE184E, stop_at=(0xE1862,), max_steps=8)
    assert result.exit_reason == "stop_at"
    write(emu, 0xE0E4, 1500)
    poller = live.MS42MS43LiveDataPoller(native_ecu(emu), ecu_id=software, use_telegram=True,
                                      telegram_fallback=False, selected_ids=("N", "VB", "VB_IGK"))
    assert capture(monkeypatch, poller) == {
        "Engine Speed": ("1500", "rpm"),
        "Battery Voltage (after main relay)": ("2.539", "V"),
        "Ignition Supply Voltage (KL15)": ("12.390", "V"),
    }
    assert [args[0] for _, _, args in poller._ds2.transport.calls] == [1, 0, 255]
    assert bytes(emu.mem.image) == image


@pytest.mark.parametrize("phase", range(6))
def test_x001_passive_roughness_producer_maps_each_slot_and_preserves_inhibits(images, phase):
    target, _, image = images
    emu = target(image, experimental=True)
    for address, value, size in ((0x40186, phase, 1), (0x40088, phase, 1), (0xE1F2, 1000, 2),
                                 (0x407F2, 0x3F, 2), (0xE0E4, 800, 2),
                                 (0xE33A, 0x4000, 2), (0x40426, 128, 1)):
        write(emu, address, value, size)
    for slot in range(6):
        write(emu, 0x407F4 + 2 * slot, 2000 + slot)
        write(emu, 0x40818 + 2 * slot, 100 + 19 * slot)
    before = read(emu, 0x407F4, 12)
    result = emu.call(0xA9A00, max_steps=4000)
    assert result.exit_reason == "stop_at"
    after = read(emu, 0x407F4, 12)
    changed = (phase + 2) % 6
    assert [i for i in range(6) if before[2*i:2*i+2] != after[2*i:2*i+2]] == [changed]
    ecu = native_ecu(emu)
    sample = live.read_passive_roughness(ecu, "43X001")
    assert sample.state == "available"
    for slot, cylinder in enumerate((1, 5, 3, 6, 2, 4)):
        assert sample.cylinders[cylinder - 1] == int.from_bytes(after[2*slot:2*slot+2], "little", signed=True) * .00122
    for address, mask in ((0xFDB2, 2), (0xFDB2, 4), (0xFD42, 0x40), (0xE1F2, 0xFFFF)):
        old = read(emu, address, 2)
        write(emu, address, mask)
        assert live.read_passive_roughness(ecu, "MS43X001").cylinders == (None,) * 6
        write(emu, address, int.from_bytes(old, "little"))
    assert all(command == 6 for _, command, _ in ecu.transport.calls)
    assert bytes(emu.mem.image) == image


def test_x001_adaptation_axes_and_slot_tables_are_read_through_native_memory_service(images):
    target, _, image = images
    emu = target(image, experimental=True)
    for offset in range(384):
        write(emu, 0x40221 + offset, offset % 256, 1)
    data = live.read_adaptations(native_ecu(emu), "43X001")
    assert not data["errors"]
    assert data["rpm"] == [byte * 32 for byte in image[0x7400C:0x7401C]]
    assert data["load"] == [byte * 1389 / 255 for byte in image[0x74095:0x74099]]
    for slot in range(6):
        assert data["knock"][slot] == [[(((slot*64 + row*4 + col) % 256) - 128) * .375
                                       for col in range(4)] for row in range(16)]
    assert data["knock_labels"] == [f"Ignition slot {i}" for i in range(1, 7)]
    assert bytes(emu.mem.image) == image


@pytest.mark.parametrize("slot", range(6))
@pytest.mark.parametrize("row,column", ((0, 0), (7, 2), (15, 3)))
def test_x001_native_knock_consumer_indexes_the_displayed_slot_row_column(images, slot, row, column):
    target, _, image = images
    emu = target(image, experimental=True)
    write(emu, 0xFB6B, row, 1)
    write(emu, 0xFB6C, column, 1)
    result = emu.run_from(0xB5EE8, stop_at=(0xB5EF6,), max_steps=8)
    assert result.exit_reason == "stop_at"
    assert read(emu, 0x403BF, 1)[0] == row * 4 + column
    write(emu, 0x40221 + slot * 64 + row * 4 + column, 111, 1)
    emu.reg.set_word(8, slot)
    result = emu.run_from(0xB6092, stop_at=(0xB60A2,), max_steps=10)
    assert result.exit_reason == "stop_at"
    assert emu.reg.get_word(7) == 111
    assert bytes(emu.mem.image) == image


@pytest.mark.parametrize("sample,expected,event_bit", ((50, 123, 1), (200, 125, 2)))
def test_changed_x001_knock_branch_retains_raw_correction_and_adds_separate_event_bits(images, sample, expected, event_bit):
    target, base, x001 = images
    for image, event in ((base, 0), (x001, event_bit)):
        emu = target(image, experimental=True)
        emu.reg.set_word(4, 100)
        emu.reg.set_word(5, sample)
        emu.reg.set_word(7, 120)
        write(emu, 0x403C0, 3, 1)
        write(emu, 0x403C1, 5, 1)
        result = emu.run_from(0xB60E8, stop_at=(0xB6152,), max_steps=50)
        assert result.exit_reason == "stop_at"
        assert emu.reg.get_word(7) == expected
        assert int.from_bytes(read(emu, 0xFDFA, 2), "little") == event
        assert bytes(emu.mem.image) == image


def test_x001_imported_definition_alias_does_not_substitute_stock(tmp_path):
    path = imported_definition(tmp_path)
    with pytest.raises(ValueError, match="no channels"):
        live.catalog_for("MS43", path, "43X001")
    path.write_text(path.read_text().replace("7551615", "43X001"))
    assert live.catalog_for("MS43", path, "MS43X001")[0].id == "TEST"
    with pytest.raises(ValueError, match="does not belong"):
        live.catalog_for("MS42", path, "43X001")
