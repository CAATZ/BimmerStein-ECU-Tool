"""0110CA measurements execute only bytes supplied by its original DATEN package."""

import random

import pytest

from ds2 import DS2Error
import ms42_ms43_live as live
from tests.test_ms42_ms43_config_daten import packages as packages, native as native
from tests.test_ms42_ms43_config_revisions import private_sources as private_sources
from tests.test_ms42_ms43_live import capture
from tests.test_ms42_live_revisions_native import native_ecu, seed_measurements


@pytest.fixture
def emu(packages, native):
    package = packages["0110CA"]
    target = native["MS42"](package.image, experimental=True)
    def trace(pc, opcode):
        assert all(at in package.memory for at in range(pc, pc + 4)), hex(pc)
    target.cpu.set_trace(trace)
    return target


def test_ca_roughness_owner_has_only_its_three_calibration_relocations(emu, private_sources):
    expected = bytearray(private_sources["c6"][0x1F33A:0x1F4CE])
    for address, before, after in ((0x1F388, 0xDC, 0xE0), (0x1F394, 0x52, 0x56), (0x1F3A0, 0x6A, 0x6E)):
        assert expected[address - 0x1F33A] == before
        expected[address - 0x1F33A] = after
    assert emu.mem.image[0x1F33A:0x1F4CE] == expected
    assert emu.mem.image[0x5210B:0x52154] == private_sources["c6"][0x5210B:0x52154]


@pytest.mark.parametrize("selector,size", ((3, 34), (4, 6), (0x90, 13), (0x91, 10), (0x92, 32), (0x93, 1)))
@pytest.mark.parametrize("seed", (0, 31))
def test_ca_fixed_groups_keep_the_bmw_layout(emu, selector, size, seed):
    original = bytes(emu.mem.image)
    seed_measurements(emu, seed)
    channels = [p for p in live.catalog_for("MS42", ecu_id="0110CA") if p.selector == selector]
    ecu = native_ecu(emu)
    assert len(ecu.execute(0x0B, bytes((selector,)))) == size
    assert len(live._group_read(ecu, channels)) == len(channels)
    assert bytes(emu.mem.image) == original


@pytest.mark.parametrize("seed", (0, 31, 53, 137))
def test_ca_selected_native_aliases_and_adc_match_all_fixed_fields(emu, seed):
    randomizer = random.Random(seed)
    for first, end in ((0xC000, 0xE800), (0xFA00, 0xFD00)):
        for address in range(first, end):
            emu.write_byte(address, randomizer.randrange(256))
    ecu = native_ecu(emu)
    channels = [p for p in live.catalog_for("MS42", ecu_id="0110CA")
                if p.selector != 6 and (p.address is not None or p.adc is not None)]
    expected = live._group_read(ecu, channels)
    assert len(channels) == 30
    for first in range(0, len(channels), 24):
        selected = channels[first:first + 24]
        assert ecu.execute(0x0B, live.build_telegram_setup("MS42", [p.descriptor for p in selected])) == b""
        data = ecu.execute(0x0B, b"\x00")
        offset = 0
        for p in selected:
            assert p.decode(data[offset:offset + p.length]) == expected[p.id], p.id
            offset += p.length
        assert offset == len(data)
        assert ecu.execute(0x0B, b"\xff") == b""


@pytest.mark.parametrize("phase", range(6))
def test_ca_actual_roughness_producer_and_inhibits(emu, phase):
    for address, value in ((0xE27A, 1000), (0xCFB0, 63), (0xFA22, 800), (0xFA44, 0x4000)):
        emu.write(address, value)
    for address, value in ((0xC618, phase), (0xC42D, phase), (0xC223, 128)):
        emu.write_byte(address, value)
    for slot in range(6):
        emu.write(0xCFB2 + slot * 2, 2000 + slot)
        emu.write(0xCFD6 + slot * 2, 100 + 19 * slot)
    before = [emu.read(0xCFB2 + slot * 2) for slot in range(6)]
    assert emu.call(0x1F33A, max_steps=4000).exit_reason == "stop_at"
    after = [emu.read(0xCFB2 + slot * 2) for slot in range(6)]
    assert [slot for slot in range(6) if before[slot] != after[slot]] == [(phase + 2) % 6]
    ecu = native_ecu(emu)
    sample = live.read_passive_roughness(ecu, "0110CA")
    assert sample.state == "available"
    for slot, cylinder in enumerate((1, 5, 3, 6, 2, 4)):
        assert sample.cylinders[cylinder - 1] == (after[slot] - 65536 if after[slot] >= 32768 else after[slot]) * .00122
    for address, mask in ((0xFD84, 2), (0xFD84, 4), (0xFD06, 0x1000), (0xE27A, 0xFFFF)):
        previous = emu.read(address)
        emu.write(address, mask)
        assert live.read_passive_roughness(ecu, "0110CA").cylinders == (None,) * 6
        assert emu.call(0x1F33A, max_steps=4000).exit_reason == "stop_at"
        assert emu.read(0xCFB0) != 63
        emu.write(address, previous)
        emu.write(0xCFB0, 63)


@pytest.mark.parametrize("slot", range(6))
@pytest.mark.parametrize("row,column", ((0, 0), (7, 2), (15, 3)))
def test_ca_native_knock_index_and_consumer(emu, slot, row, column):
    emu.write_byte(0xFA0F, row)
    emu.write_byte(0xFA0E, column)
    assert emu.run_from(0x2EF66, stop_at=0x2EF74, max_steps=8).exit_reason == "stop_at"
    assert emu.read_byte(0xC47D) == row * 4 + column
    emu.write_byte(0xC486 + slot * 64 + row * 4 + column, 111)
    emu.reg.set_word(8, slot)
    assert emu.run_from(0x2F0B8, stop_at=0x2F0C8, max_steps=10).exit_reason == "stop_at"
    assert emu.reg.get_word(7) == 111


def test_ca_adaptation_axes_come_from_its_actual_native_consumer(emu):
    image = bytes(emu.mem.image)
    emu.write(0xFD42, 8)
    for address, expected in ((0x2EF28, "e6fc1009"), (0x2EF34, "e6fc2109"),
                              (0x2EF4E, "e6fc1009"), (0x2EF5A, "e6fc2109")):
        assert image[address:address + 4] == bytes.fromhex(expected)
    assert image[0x48910] == 16 and image[0x48921] == 4
    for i in range(384):
        emu.write_byte(0xC486 + i, i % 256)
    data = live.read_adaptations(native_ecu(emu), "0110CA")
    assert not data["errors"]
    assert data["rpm"] == [v * 32 for v in image[0x48911:0x48921]]
    assert data["load"] == [v * 1389 / 255 for v in image[0x48922:0x48926]]
    assert data["knock"] == [[[((slot * 64 + row * 4 + col) % 256 - 128) * .375
                                 for col in range(4)] for row in range(16)] for slot in range(6)]
    assert bytes(emu.mem.image) == image


def test_ca_real_worker_uses_native_selected_read_and_cleanup(emu, monkeypatch):
    emu.write(0xFA22, 1500)
    poller = live.MS42MS43LiveDataPoller(native_ecu(emu), ecu_id="0110CA", selected_ids=("N",),
                                      use_telegram=True, telegram_fallback=False)
    assert capture(monkeypatch, poller) == {"Engine Speed": ("1500", "rpm")}
    assert [args[0] for _, _, args in poller._ds2.transport.calls] == [1, 0, 255]


@pytest.mark.parametrize("args", (b"\x01\x00", b"\x01\x19", bytes.fromhex("0101010000FA23"),
                                  bytes.fromhex("01010000080000"), bytes.fromhex("0101020000001C")))
def test_ca_native_descriptor_boundaries(emu, args):
    with pytest.raises(DS2Error):
        native_ecu(emu).execute(0x0B, args)
