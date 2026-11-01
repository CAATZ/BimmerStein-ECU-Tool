"""Each DATEN revision executes its own measurement and adaptation owners.

The sparse package buffers are bounded analysis fixtures, never flash images.
Every executed instruction must exist in the pinned original BMW package.
"""

import random
from types import SimpleNamespace

import pytest

from ds2 import DS2Error
from ms42_ms43 import MS42MS43ECU
import ms42_ms43_live as live
from tests.test_ms42_ms43 import Link, reply
from tests.test_ms42_ms43_config_daten import packages as packages, native as native
from tests.test_ms42_ms43_config_revisions import private_sources as private_sources
from tests.test_ms42_ms43_live import capture
from tests.test_ms43_x001_live_native import read, write


# Dispatcher, memory reader, roughness producer/phase/cycle/history/load/temp/table,
# knock index/lookup/indices/cell/table/load-axis, supply producer/ADC inputs.
OWNERS = {
    "430037": (0xA6286, 0xA6FCC,
               (0xABCB6, 0x40158, 0x4006A, 0x407A6, 0xE31A, 0x403DD, 0xEC4F0),
               (0xB8FD6, 0xB9180, 0xFDF7, 0xFDF6, 0x40382, 0x401E4, 0x7408E),
               (0xD6BEE, 0xE542, 0xE540)),
    "430064": (0xA5504, 0xA631A,
               (0xAB122, 0x40181, 0x40088, 0x407F2, 0xE33A, 0x40420, 0xEC608),
               (0xB8B8E, 0xB8D38, 0xFB6B, 0xFB6C, 0x403B9, 0x4021B, 0x74095),
               (0xDE108, 0xE572, 0xE570)),
    "430065": (0xA5C1E, 0xA6A38,
               (0xAB7D6, 0x40186, 0x40088, 0x4080A, 0xE33A, 0x40426, 0xEC666),
               (0xB7F2C, 0xB80D6, 0xFB6B, 0xFB6C, 0x403BF, 0x40221, 0x74095),
               (0xDF8B0, 0xE572, 0xE570)),
    "430066": (0xA5C1E, 0xA6A38,
               (0xA9A00, 0x40186, 0x40088, 0x40818, 0xE33A, 0x40426, 0xEC66C),
               (0xB5EE8, 0xB6092, 0xFB6B, 0xFB6C, 0x403BF, 0x40221, 0x74095),
               (0xE184E, 0xE572, 0xE570)),
}


@pytest.fixture(params=(*OWNERS, "430066-later"))
def revision(request, packages, native):
    key = request.param
    package = packages[key]
    software = key.split("-")[0]
    emu = native["MS43"](package.image, experimental=True)

    def trace(pc, opcode):
        assert all(at in package.memory for at in range(pc, pc + 4)), hex(pc)
    emu.cpu.set_trace(trace)
    return SimpleNamespace(emu=emu, image=package.image, software=software, owners=OWNERS[software])


def native_ecu(revision):
    emu = revision.emu
    def respond(command, args):
        assert command in (6, 0x0B), "Only measurement services are permitted"
        for i, byte in enumerate(args):
            write(emu, 0xF6D0 + i, byte, 1)
        result = emu.call(revision.owners[0 if command == 0x0B else 1], max_steps=10000)
        assert result.exit_reason == "stop_at", result
        count = read(emu, 0xF74E, 1)[0] - 4
        assert 0 <= count <= 123
        return reply(read(emu, 0xF750, count), status=read(emu, 0xF74F, 1)[0])
    return MS42MS43ECU(Link(respond), "MS43")


@pytest.mark.parametrize("selector,size", ((3, 41), (4, 6), (0x90, 12), (0x91, 11), (0x92, 27), (0x93, 2)))
def test_each_revision_fixed_groups_keep_the_bmw_payload_contract(revision, selector, size):
    ecu = native_ecu(revision)
    channels = [p for p in live.catalog_for("MS43", ecu_id=revision.software) if p.selector == selector]
    assert len(ecu.execute(0x0B, bytes((selector,)))) == size
    assert len(live._group_read(ecu, channels)) == len(channels)
    assert bytes(revision.emu.mem.image) == revision.image


@pytest.mark.parametrize("seed", (0, 31, 53, 137))
def test_native_selected_descriptors_match_every_actual_fixed_group_alias(revision, seed):
    randomizer = random.Random(seed)
    for first, end in ((0x40000, 0x41200), (0xE000, 0xE600), (0xFB00, 0xFCC0)):
        for address in range(first, end):
            write(revision.emu, address, randomizer.randrange(256), 1)
    ecu = native_ecu(revision)
    channels = [p for p in live.catalog_for("MS43", ecu_id=revision.software)
                if p.selector != 6 and (p.address is not None or p.adc is not None)]
    assert len(channels) == 30
    expected = live._group_read(ecu, channels)
    for first in range(0, len(channels), 24):
        selected = channels[first:first + 24]
        assert ecu.execute(0x0B, live.build_telegram_setup("MS43", [p.descriptor for p in selected])) == b""
        payload = ecu.execute(0x0B, b"\x00")
        offset = 0
        for p in selected:
            assert p.decode(payload[offset:offset + p.length]) == expected[p.id], p.id
            offset += p.length
        assert offset == len(payload)
        assert ecu.execute(0x0B, b"\xff") == b""
    assert bytes(revision.emu.mem.image) == revision.image


@pytest.mark.parametrize("phase", range(6))
def test_native_roughness_producers_and_their_actual_inhibits(revision, phase, private_sources):
    emu = revision.emu
    entry, phase_address, cycle, history, load, temperature, table = revision.owners[2]
    layout = live._revision_layout("MS43", revision.software)
    address, rpm, input_address, faults = layout["roughness"]
    assert revision.image[table - 0x80000:table - 0x80000 + 72] == private_sources["stock"][0x6C66C:0x6C6B4]
    for at, value, size in ((phase_address, phase, 1), (cycle, phase, 1),
                            (input_address, 1000, 2), (address, 63, 2), (rpm, 800, 2),
                            (load, 0x4000, 2), (temperature, 128, 1)):
        write(emu, at, value, size)
    for slot in range(6):
        write(emu, address + 2 + slot * 2, 2000 + slot)
        write(emu, history + slot * 2, 100 + 19 * slot)
    before = read(emu, address + 2, 12)
    assert emu.call(entry, max_steps=4000).exit_reason == "stop_at"
    after = read(emu, address + 2, 12)
    assert [i for i in range(6) if before[i*2:i*2+2] != after[i*2:i*2+2]] == [(phase + 2) % 6]
    ecu = native_ecu(revision)
    sample = live.read_passive_roughness(ecu, revision.software)
    assert sample.state == "available"
    for slot, cylinder in enumerate((1, 5, 3, 6, 2, 4)):
        assert sample.cylinders[cylinder - 1] == int.from_bytes(after[slot*2:slot*2+2], "little", signed=True) * .00122
    for location, mask in (faults[0], faults[1], (input_address, 0xFFFF)):
        previous = int.from_bytes(read(emu, location, 2), "little")
        write(emu, location, mask)
        assert live.read_passive_roughness(ecu, "MS" + revision.software).cylinders == (None,) * 6
        assert emu.call(entry, max_steps=4000).exit_reason == "stop_at"
        assert int.from_bytes(read(emu, address, 2), "little") != 63
        write(emu, location, previous)
        write(emu, address, 63)
    assert bytes(emu.mem.image) == revision.image


@pytest.mark.parametrize("slot", range(6))
@pytest.mark.parametrize("row,column", ((0, 0), (7, 2), (15, 3)))
def test_native_knock_cell_index_and_actual_consumer(revision, slot, row, column):
    index, consumer, row_address, column_address, cell, table, _ = revision.owners[3]
    emu = revision.emu
    write(emu, row_address, row, 1)
    write(emu, column_address, column, 1)
    assert emu.run_from(index, stop_at=index + 14, max_steps=8).exit_reason == "stop_at"
    assert read(emu, cell, 1)[0] == row * 4 + column
    write(emu, table + slot * 64 + row * 4 + column, 111, 1)
    emu.reg.set_word(8, slot)
    assert emu.run_from(consumer, stop_at=consumer + 16, max_steps=10).exit_reason == "stop_at"
    assert emu.reg.get_word(7) == 111
    assert bytes(emu.mem.image) == revision.image


def test_native_knock_axes_and_all_cells_read_through_original_memory_handler(revision):
    index, _, _, _, _, table, load_axis = revision.owners[3]
    axes = revision.image[index - 0x80000 - 32:index - 0x80000]
    assert axes[:8] == bytes.fromhex("e6fc0b00e6fd1d00")
    assert axes[16:24] == bytes.fromhex("e6fc") + (load_axis - 1 - 0x74000).to_bytes(2, "little") + bytes.fromhex("e6fd1d00")
    assert revision.image[0x7400B] == 16 and revision.image[load_axis - 1] == 4
    for i in range(384):
        write(revision.emu, table + i, i % 256, 1)
    data = live.read_adaptations(native_ecu(revision), revision.software)
    assert not data["errors"]
    assert data["rpm"] == [v * 32 for v in revision.image[0x7400C:0x7401C]]
    assert data["load"] == [v * 1389 / 255 for v in revision.image[load_axis:load_axis + 4]]
    assert data["knock"] == [[[( (slot * 64 + row * 4 + col) % 256 - 128) * .375
                                 for col in range(4)] for row in range(16)] for slot in range(6)]
    assert bytes(revision.emu.mem.image) == revision.image


def test_real_live_worker_reads_the_actual_supply_producer_and_cleans_up(revision, monkeypatch):
    emu = revision.emu
    producer, main, ignition = revision.owners[4]
    write(emu, main, 25 << 2)
    write(emu, ignition, 122 << 2)
    assert emu.run_from(producer, stop_at=producer + 20, max_steps=8).exit_reason == "stop_at"
    channels = {p.id: p for p in live.catalog_for("MS43", ecu_id=revision.software)}
    write(emu, channels["N"].address, 1500)
    poller = live.MS42MS43LiveDataPoller(native_ecu(revision), ecu_id=revision.software,
        use_telegram=True, telegram_fallback=False, selected_ids=("N", "VB", "VB_IGK"))
    assert capture(monkeypatch, poller) == {
        "Engine Speed": ("1500", "rpm"), "Battery Voltage (after main relay)": ("2.539", "V"),
        "Ignition Supply Voltage (KL15)": ("12.390", "V")}
    assert [args[0] for _, _, args in poller._ds2.transport.calls] == [1, 0, 255]
    assert bytes(emu.mem.image) == revision.image


@pytest.mark.parametrize("args", (b"\x01\x00", b"\x01\x19", bytes.fromhex("0101010000E0E5"),
                                  bytes.fromhex("010100000F0000"), bytes.fromhex("0101020000001B")))
def test_native_selected_descriptor_boundaries_still_reject_bad_reads(revision, args):
    with pytest.raises(DS2Error):
        native_ecu(revision).execute(0x0B, args)
    assert bytes(revision.emu.mem.image) == revision.image
