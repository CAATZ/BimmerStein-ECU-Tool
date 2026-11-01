"""430055 measurements execute its own handlers and relocated native producers."""

import pytest

from ds2 import DS2Error
from ms42_ms43 import MS42MS43ECU
import ms42_ms43_live as live
from tests.test_ms42_ms43 import Link, reply
from tests.test_ms42_ms43_config_daten import packages as packages, native as native
from tests.test_ms42_ms43_config_revisions import private_sources as private_sources
from tests.test_ms43_x001_live_native import read, write, seed_measurements
from tests.test_ms42_ms43_live import capture


@pytest.fixture
def emu(packages, native):
    return native["MS43"](packages["430055"].image, experimental=True)


def native_ecu(emu):
    def respond(command, args):
        assert command in (6, 0x0B), "Measurement fixture must not write or run actuator tests"
        for index, byte in enumerate(args):
            write(emu, 0xF6D0 + index, byte, 1)
        result = emu.call(0xA53D4 if command == 0x0B else 0xA61EA, max_steps=10000)
        assert result.exit_reason == "stop_at", result
        length = read(emu, 0xF74E, 1)[0] - 4
        assert 0 <= length <= 123
        return reply(read(emu, 0xF750, length), status=read(emu, 0xF74F, 1)[0])
    return MS42MS43ECU(Link(respond), "MS43")


@pytest.mark.parametrize("selector,size", ((3, 41), (4, 6), (0x90, 12), (0x91, 11), (0x92, 27), (0x93, 2)))
def test_original_055_dispatcher_implements_the_fixed_group_contract(emu, selector, size):
    original = bytes(emu.mem.image)
    seed_measurements(emu, 31)
    ecu = native_ecu(emu)
    parameters = [p for p in live.catalog_for("MS43", ecu_id="430055") if p.selector == selector]
    payload = ecu.execute(0x0B, bytes((selector,)))
    assert len(payload) == size
    assert len(live._group_read(ecu, parameters)) == len(parameters)
    assert bytes(emu.mem.image) == original


@pytest.mark.parametrize("seed", (0, 31, 53, 137))
def test_every_055_direct_alias_and_adc_matches_native_fixed_groups(emu, seed):
    seed_measurements(emu, seed)
    ecu = native_ecu(emu)
    parameters = [p for p in live.catalog_for("MS43", ecu_id="430055")
                  if p.selector != 6 and (p.address is not None or p.adc is not None)]
    expected = live._group_read(ecu, parameters)
    assert len(parameters) == 30
    for start in range(0, len(parameters), 24):
        selected = parameters[start:start + 24]
        assert ecu.execute(0x0B, live.build_telegram_setup("MS43", [p.descriptor for p in selected])) == b""
        payload = ecu.execute(0x0B, b"\x00")
        offset = 0
        for channel in selected:
            assert channel.decode(payload[offset:offset + channel.length]) == expected[channel.id], channel.id
            offset += channel.length
        assert offset == len(payload)
        assert ecu.execute(0x0B, b"\xff") == b""


@pytest.mark.parametrize("args", (b"\x01\x00", b"\x01\x19",
                                  bytes.fromhex("01 01 01 00 00 E0 E5"),
                                  bytes.fromhex("01 01 00 00 0F 00 00"),
                                  bytes.fromhex("01 01 02 00 00 00 1B")))
def test_055_native_selected_memory_rejects_invalid_descriptors(emu, args):
    original = bytes(emu.mem.image)
    with pytest.raises(DS2Error):
        native_ecu(emu).execute(0x0B, args)
    assert not int.from_bytes(read(emu, 0xFD28, 2), "little") & 0x200
    assert bytes(emu.mem.image) == original


@pytest.mark.parametrize("software", ("430055", "MS430055"))
def test_055_live_worker_uses_native_telegrams_and_keeps_supply_rails_distinct(emu, software, monkeypatch):
    original = bytes(emu.mem.image)
    assert original[0x59AD2:0x59AE6] == bytes.fromhex(
        "f2f468e57c24f7f8d5fbf2f466e57c24f7f8d6fb")
    write(emu, 0xE568, 25 << 2)
    write(emu, 0xE566, 122 << 2)
    result = emu.run_from(0xD9AD2, stop_at=0xD9AE6, max_steps=8)
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
    assert bytes(emu.mem.image) == original


def test_055_roughness_phase_tables_and_inhibit_instructions_are_actual_sources(emu, private_sources):
    image = emu.mem.image
    assert image[0x6C608:0x6C650] == private_sources["stock"][0x6C66C:0x6C6B4]
    assert image[0x2B002:0x2B026] == bytes.fromhex(
        "9a560210ea002eb19a560220ea002eb19a200270ea002eb1"
        "f2f4f2e146f4ffffea202eb1")


@pytest.mark.parametrize("phase", range(6))
def test_055_original_roughness_producer_and_native_memory_reader(emu, phase):
    original = bytes(emu.mem.image)
    for address, value, size in ((0x4017C, phase, 1), (0x40088, phase, 1),
                                 (0xE1F2, 1000, 2), (0x407BC, 0x3F, 2),
                                 (0xE0E4, 800, 2), (0xE338, 0x4000, 2),
                                 (0x40411, 128, 1)):
        write(emu, address, value, size)
    for slot in range(6):
        write(emu, 0x407BE + slot * 2, 2000 + slot)
        write(emu, 0x407E2 + slot * 2, 100 + 19 * slot)
    before = read(emu, 0x407BE, 12)
    result = emu.call(0xAAFF2, max_steps=4000)
    assert result.exit_reason == "stop_at"
    after = read(emu, 0x407BE, 12)
    assert [slot for slot in range(6) if before[2*slot:2*slot+2] != after[2*slot:2*slot+2]] == [(phase + 2) % 6]
    ecu = native_ecu(emu)
    sample = live.read_passive_roughness(ecu, "430055")
    assert sample.state == "available"
    for slot, cylinder in enumerate((1, 5, 3, 6, 2, 4)):
        assert sample.cylinders[cylinder - 1] == int.from_bytes(after[slot*2:slot*2+2], "little", signed=True) * .00122
    for address, value in ((0xFDAC, 2), (0xFDAC, 4), (0xFD40, 0x80), (0xE1F2, 0xFFFF)):
        previous = read(emu, address, 2)
        write(emu, address, value)
        assert live.read_passive_roughness(ecu, "MS430055").cylinders == (None,) * 6
        write(emu, address, int.from_bytes(previous, "little"))
    assert all(command == 6 for _, command, _ in ecu.transport.calls)
    assert bytes(emu.mem.image) == original


@pytest.mark.parametrize("slot", range(6))
@pytest.mark.parametrize("row,column", ((0, 0), (7, 2), (15, 3)))
def test_055_native_knock_consumer_uses_the_displayed_cells(emu, slot, row, column):
    write(emu, 0xFB6B, row, 1)
    write(emu, 0xFB6C, column, 1)
    result = emu.run_from(0xB82E4, stop_at=0xB82F2, max_steps=8)
    assert result.exit_reason == "stop_at"
    assert read(emu, 0x403B1, 1)[0] == row * 4 + column
    write(emu, 0x40213 + slot * 64 + row * 4 + column, 111, 1)
    emu.reg.set_word(8, slot)
    result = emu.run_from(0xB848E, stop_at=0xB849E, max_steps=10)
    assert result.exit_reason == "stop_at" and emu.reg.get_word(7) == 111


def test_055_adaptation_axis_and_table_read_uses_its_native_memory_service(emu):
    image = bytes(emu.mem.image)
    assert image[0x382C4:0x382E4] == bytes.fromhex(
        "e6fc0b00e6fd1d00c2feaa83da0d6204e6fc9400e6fd1d00c2fea983da0dee03")
    assert image[0x7400B] == 16 and image[0x74094] == 4
    for offset in range(384):
        write(emu, 0x40213 + offset, offset % 256, 1)
    result = live.read_adaptations(native_ecu(emu), "430055")
    assert result["errors"] == []
    assert result["rpm"] == [value * 32 for value in image[0x7400C:0x7401C]]
    assert result["load"] == [value * 1389 / 255 for value in image[0x74095:0x74099]]
    for slot in range(6):
        assert result["knock"][slot] == [[((slot * 64 + row * 4 + col) % 256 - 128) * .375
                                         for col in range(4)] for row in range(16)]
    assert bytes(emu.mem.image) == image


def test_055_profile_does_not_reuse_069_direct_addresses_or_expand_unknown_revisions():
    current = {p.id: p for p in live.catalog_for("MS43", ecu_id="430055")}
    alias = {p.id: p for p in live.catalog_for("MS43", ecu_id="MS430055")}
    original = {p.id: p for p in live.catalog_for("MS43", ecu_id="430069")}
    assert current == alias
    assert current["VS"].address == 0x4045E != original["VS"].address
    assert current["MAF_KGH"].address == 0xE32C
    assert current["BS_ROUGHNESS_CYL1"].address == 0x407BE
    with pytest.raises(ValueError, match="not mapped"):
        live.read_passive_roughness(type("ECU", (), {"family": "MS43"})(), "430056")
