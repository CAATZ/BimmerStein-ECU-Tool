"""Exact MS42 configuration consumers, executed with supplied runtime state.

These bounded native checks establish calibration meanings. They do not model
the complete engine scheduler or establish behavior on a physical ECU.
"""

import pytest

from tests.test_ms42_ms43_config_native import native as native
from tests.test_ms42_ms43_config_revisions import private_sources as private_sources


PURGE_BANK_OWNERS = (
    (0x0168B6, 334), (0x016A04, 1274), (0x016EFE, 62),
    (0x017338, 410), (0x0175FC, 680), (0x0178A4, 600),
    (0x017AFC, 240), (0x030F56, 1072), (0x0337C6, 112),
)
LAMBDA_BANK_OWNERS = ((0x03006E, 402), (0x030200, 502), (0x0303F6, 1424))
BANKS = (0xE33A, 0xE424)


def _select_purge_bank(emu, value):
    emu.mem.image[0x48291] = value
    result = emu.run_from(0x0168E6, stop_at=(0x0168FE,), max_steps=8)
    assert result.exit_reason == "stop_at"
    assert emu.read(0xE232) == BANKS[bool(value)]


@pytest.mark.parametrize("value", (0, 1, 255))
def test_purge_feedback_reads_status_from_the_selected_lambda_bank(native, value):
    emu = native("MS42")
    _select_purge_bank(emu, value)
    statuses = ((0x1234, 0x5678), (0xABCD, 0xEF01))
    for bank, words in zip(BANKS, statuses):
        emu.write(bank + 2, words[0])
        emu.write(bank + 4, words[1])

    result = emu.run_from(0x016B96, stop_at=(0x016BAA,), max_steps=8)

    assert result.exit_reason == "stop_at"
    assert (emu.read(0xFD36), emu.read(0xFD38)) == statuses[bool(value)]
    for bank, words in zip(BANKS, statuses):
        assert (emu.read(bank + 2), emu.read(bank + 4)) == words


@pytest.mark.parametrize("value", (0, 1, 255))
@pytest.mark.parametrize("current_bank", BANKS)
def test_purge_event_snapshot_only_updates_the_selected_bank(native, value, current_bank):
    emu = native("MS42")
    _select_purge_bank(emu, value)
    emu.reg.set_word(9, current_bank)
    for bank, count, previous in zip(BANKS, (0x11, 0x22), (0xAA, 0xBB)):
        emu.write_byte(bank + 0xD1, count)
        emu.write_byte(bank + 0xBF, previous)

    result = emu.run_from(0x033822, stop_at=(0x033832,), max_steps=8)

    assert result.exit_reason == "stop_at"
    for bank, count, previous in zip(BANKS, (0x11, 0x22), (0xAA, 0xBB)):
        selected = bank == current_bank == BANKS[bool(value)]
        assert emu.read_byte(bank + 0xBF) == (count if selected else previous)
        assert emu.read_byte(bank + 0xD1) == count


def test_c7_has_the_same_purge_bank_producer_consumers_and_bank_contexts(private_sources):
    memory = private_sources["c7_memory"]
    for start, size in PURGE_BANK_OWNERS + LAMBDA_BANK_OWNERS:
        addresses = range(start, start + size)
        assert all(address in memory for address in addresses)
        assert bytes(memory[address] for address in addresses) == private_sources["c6"][start:start + size]
    assert private_sources["c7_cal_memory"][0x48291] == private_sources["c6"][0x48291] == 0


@pytest.mark.parametrize("lambda_mode", range(5))
@pytest.mark.parametrize("mil_mode,flags", [(0, (False, True)), (1, (False, False)),
                                           (2, (True, False)), (3, (True, True))])
@pytest.mark.parametrize("unused_eu2_setting", (0, 3))
def test_mil_policy_uses_one_setting_for_every_lambda_configuration(
        native, monkeypatch, lambda_mode, mil_mode, flags, unused_eu2_setting):
    emu = native("MS42")
    emu.mem.image[0x48287] = lambda_mode
    emu.mem.image[0x4828C] = mil_mode
    emu.mem.image[0x4828D] = unused_eu2_setting
    emu.write(0xFD06, 0xA55A)
    reads = []
    original_offset = emu.mem._file_offset

    def record_variant_access(address):
        if 0x48287 <= address <= 0x4828D:
            reads.append(address)
        return original_offset(address)

    monkeypatch.setattr(emu.mem, "_file_offset", record_variant_access)
    result = emu.run_from(0x0133FA, stop_at=(0x013466,), max_steps=100)

    assert result.exit_reason == "stop_at"
    assert (bool(emu.read(0xFD04) & 0x8000), bool(emu.read(0xFD06) & 1)) == flags
    assert emu.read(0xFD06) & 0xFFFE == 0xA55A
    assert set(reads) == {0x48287, 0x4828C}


@pytest.mark.parametrize("ldp_increment", (0, 1, 5, 255))
@pytest.mark.parametrize("secondary_air_increment", (0, 1))
def test_config_ldp_reports_the_fault_counter_increment(native, ldp_increment, secondary_air_increment):
    emu = native("MS42")
    emu.mem.image[0x481A9] = ldp_increment
    emu.mem.image[0x481C3] = secondary_air_increment
    emu.write_byte(0xF7A1, 0x94)

    result = emu.call(0x020054, max_steps=400)

    assert result.exit_reason == "stop_at"
    assert emu.read_byte(0xF820) == 0xA0
    assert bool(emu.read_byte(0xF823) & 0x10) == bool(ldp_increment)
    assert bool(emu.read_byte(0xF823) & 0x20) == bool(secondary_air_increment)


@pytest.mark.parametrize("increment", (0, 1, 255))
def test_ldp_driver_fault_path_passes_increment_and_limit_to_the_counter(native, increment):
    emu = native("MS42")
    emu.mem.image[0x481A9] = increment
    emu.mem.image[0x481EF] = 23

    result = emu.run_from(0x01B504, stop_at=(0x01B514,), max_steps=8)

    assert result.exit_reason == "stop_at"
    assert emu.reg.get_word(12) == 0xCC02
    assert emu.reg.get_word(13) == 0x6958
    assert emu.reg.get_word(14) == increment
    assert emu.reg.get_word(15) == 23
