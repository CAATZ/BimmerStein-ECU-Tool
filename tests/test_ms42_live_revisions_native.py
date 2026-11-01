"""C7 measurements from supplied BMW bytes, not a software-ID-only alias.

The sparse package fixture is analysis input only, never a flashable image.
Native calls require every executed instruction to exist in the supplied package.
"""

import pytest

from ms42_ms43 import MS42MS43ECU
import ms42_ms43_live as live
from tests.test_ms42_ms43 import Link, reply
from tests.test_ms42_ms43_config_native import native as native
from tests.test_ms42_ms43_config_revisions import private_sources as private_sources
from tests.test_ms42_ms43_live import capture, imported_definition


@pytest.fixture
def revision(native, private_sources):
    base = native("MS42")
    memory = dict(private_sources["c7_memory"])
    memory.update(private_sources["c7_cal_memory"])
    image = bytearray(b"\xff" * 0x80000)
    for address, value in memory.items():
        image[address] = value
    return type(base), bytes(base.mem.image), bytes(image), memory


def checked_emu(revision, source):
    target, base, image, memory = revision
    emu = target(base if source == "base" else image, experimental=True)

    def trace(pc, opcode):
        assert all(at in memory for at in range(pc, pc + 4)), hex(pc)
        assert image[pc:pc + 4] == base[pc:pc + 4], hex(pc)

    emu.cpu.set_trace(trace)
    return emu


def native_ecu(emu):
    def respond(command, args):
        assert command in (6, 0x0B), "Only measurement services are permitted"
        for index, byte in enumerate(args):
            emu.write_byte(0xF7A1 + index, byte)
        result = emu.call(0x20054 if command == 0x0B else 0x20966, max_steps=10000)
        assert result.exit_reason == "stop_at", result
        count = emu.read_byte(0xF81F) - 4
        assert 0 <= count <= 123
        return reply(bytes(emu.read_byte(0xF821 + i) for i in range(count)),
                     status=emu.read_byte(0xF820))
    return MS42MS43ECU(Link(respond), "MS42")


def seed_measurements(emu, seed):
    for first, end in ((0xC000, 0xE800), (0xFA00, 0xFD00)):
        for address in range(first, end):
            emu.write_byte(address, (address * 7 + seed) & 255)
    emu.write(0xFD42, 8)


def test_c7_complete_measurement_roughness_and_knock_owners_are_supplied(revision):
    _, base, image, memory = revision
    for first, end in ((0x20054, 0x20A50), (0x1F33A, 0x1F4CE),
                       (0x2DE02, 0x2F73C), (0x5210B, 0x52154)):
        assert all(at in memory for at in range(first, end))
        assert image[first:end] == base[first:end]
    for first, size in ((0x4890D, 16), (0x4891E, 4)):
        assert all(at in memory for at in range(first - 1, first + size))
        assert image[first - 1] == size
        assert list(image[first:first + size]) == sorted(image[first:first + size])


@pytest.mark.parametrize("selector", (3, 4, 0x90, 0x91, 0x92, 0x93))
@pytest.mark.parametrize("seed", (0, 31))
def test_c7_fixed_groups_execute_and_match_base_wire_contract(revision, selector, seed):
    results = []
    for source in ("base", "c7"):
        emu = checked_emu(revision, source)
        seed_measurements(emu, seed)
        channels = [p for p in live.catalog_for("MS42", ecu_id="0110C7") if p.selector == selector]
        ecu = native_ecu(emu)
        results.append((ecu.execute(0x0B, bytes((selector,))), live._group_read(ecu, channels)))
    assert results[0] == results[1]


def test_c7_selected_memory_and_adc_match_fixed_groups_and_cleanup(revision):
    emu = checked_emu(revision, "c7")
    seed_measurements(emu, 31)
    ecu = native_ecu(emu)
    channels = [p for p in live.catalog_for("MS42") if p.address is not None or p.adc is not None]
    expected = live._group_read(ecu, channels)
    for start in range(0, len(channels), 24):
        selected = channels[start:start + 24]
        assert ecu.execute(0x0B, live.build_telegram_setup("MS42", [p.descriptor for p in selected])) == b""
        payload = ecu.execute(0x0B, b"\x00")
        offset = 0
        for channel in selected:
            assert channel.decode(payload[offset:offset + channel.length]) == expected[channel.id]
            offset += channel.length
        assert offset == len(payload)
        assert ecu.execute(0x0B, b"\xff") == b""
        assert not emu.read(0xFD1C) & 0x800
    assert bytes(emu.mem.image) == revision[2]


@pytest.mark.parametrize("software", ("0110C7", "7505859"))
def test_c7_real_poller_uses_native_selected_reads(revision, software, monkeypatch):
    emu = checked_emu(revision, "c7")
    emu.write(0xFA22, 1500)
    poller = live.MS42MS43LiveDataPoller(native_ecu(emu), ecu_id=software,
                                      use_telegram=True, telegram_fallback=False, selected_ids=("N",))
    assert capture(monkeypatch, poller) == {"Engine Speed": ("1500", "rpm")}
    assert [args[0] for _, _, args in poller._ds2.transport.calls] == [1, 0, 255]


@pytest.mark.parametrize("phase", range(6))
def test_c7_native_roughness_producer_and_faults_feed_existing_reader(revision, phase):
    emu = checked_emu(revision, "c7")
    for at, value in ((0xE27A, 1000), (0xCFB0, 0x3F), (0xFA22, 800), (0xFA44, 0x4000)):
        emu.write(at, value)
    emu.write_byte(0xC618, phase)
    emu.write_byte(0xC42D, phase)
    emu.write_byte(0xC223, 128)
    for slot in range(6):
        emu.write(0xCFB2 + 2 * slot, 2000 + slot)
        emu.write(0xCFD6 + 2 * slot, 100 + 19 * slot)
    before = [emu.read(0xCFB2 + 2 * slot) for slot in range(6)]
    assert emu.call(0x1F33A, max_steps=4000).exit_reason == "stop_at"
    after = [emu.read(0xCFB2 + 2 * slot) for slot in range(6)]
    assert [slot for slot in range(6) if before[slot] != after[slot]] == [(phase + 2) % 6]
    ecu = native_ecu(emu)
    sample = live.read_passive_roughness(ecu, "0110C7")
    assert sample.state == "available"
    for slot, cylinder in enumerate((1, 5, 3, 6, 2, 4)):
        value = after[slot] - 65536 if after[slot] >= 32768 else after[slot]
        assert sample.cylinders[cylinder - 1] == pytest.approx(value * .00122)
    for address, mask in ((0xFD84, 2), (0xFD84, 4), (0xFD06, 0x1000), (0xE27A, 0xFFFF)):
        old = emu.read(address)
        emu.write(address, mask)
        assert live.read_passive_roughness(ecu, "7505859").cylinders == (None,) * 6
        emu.write(address, old)
    assert bytes(emu.mem.image) == revision[2]


def test_c7_adaptation_axes_and_six_slot_tables_use_native_memory_reads(revision):
    emu = checked_emu(revision, "c7")
    emu.write(0xFD42, 8)
    for offset in range(384):
        emu.write_byte(0xC486 + offset, offset % 256)
    data = live.read_adaptations(native_ecu(emu), "0110C7")
    assert not data["errors"]
    assert data["rpm"] == [byte * 32 for byte in revision[2][0x4890D:0x4891D]]
    assert data["load"] == [byte * 1389 / 255 for byte in revision[2][0x4891E:0x48922]]
    for slot in range(6):
        assert data["knock"][slot] == [[(((slot*64 + row*4 + col) % 256) - 128) * .375
                                       for col in range(4)] for row in range(16)]


@pytest.mark.parametrize("software", ("0110C7", "7505859"))
def test_c7_matching_imported_logger_reuses_verified_addresses(tmp_path, software):
    path = imported_definition(tmp_path, family="MS42", address="0xFA22")
    channels = live.catalog_for("MS42", path, software)
    assert channels
    assert channels == live.catalog_for("MS42", path, "0110C6")
