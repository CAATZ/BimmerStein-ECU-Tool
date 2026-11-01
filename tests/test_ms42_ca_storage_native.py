"""Original CA journal owners with a named C6 boot / CA DATEN test fixture."""

import os
from pathlib import Path

import pytest

from checksum import _crc
from ms42_ms43_diagnostics import select_ms42_slot, storage_operating_counter
from ms42_ms43_storage import make_snapshot
from ms42_ms43_storage_edit import editable_fields, edit_storage, storage_knock_axes
from tests.ms42_ms43_revision_sources import composed_image, full_image
from tests.test_ms42_ms43_storage_edit_native import (
    slot, journal, test_ms42_selected_slot_matches_original_scanner as _selection_cases,
)
from tests.test_ms42_ms43_storage_inspection import put_record
from tests.test_ms42_live_revisions_native import native_ecu


@pytest.fixture
def target(monkeypatch):
    root = Path(os.environ.get("MS41EMU_ROOT", Path.home() / "ECU Emulator"))
    if not (root / "ms41emu").is_dir():
        pytest.skip("canonical C167 emulator unavailable; set MS41EMU_ROOT")
    monkeypatch.syspath_prepend(str(root))
    from ms41emu import MS42
    source = composed_image("0110CA", "0110C6")
    emu = MS42(source.image, experimental=True)
    emu.enable_flash()
    return emu


def ca_slot(record):
    raw = bytearray(record)
    length = int.from_bytes(raw[0x3F6:0x3F8], "little")
    difference = int.from_bytes(raw[0x3FE:], "little") ^ _crc(raw[:length], 15)
    if raw[4:14] == b"6577225107":
        raw[4:14] = b"6577225109"
    raw[0x3FE:] = (_crc(raw[:length], 15) ^ difference).to_bytes(2, "little")
    return raw


def select(target, raw):
    target.mem.image[0x4000:0x8000] = raw
    original = bytes(target.mem.image)
    target.reg.set_word(12, 0x3C)
    target.reg.set_word(13, 0x14)
    result = target.call(0x76354, max_steps=500000)
    assert result.exit_reason == "stop_at", result
    assert bytes(target.mem.image) == original
    flags = target.read(0xFD18)
    if flags & 0x400 and not flags & 0x1000:
        pointer = (target.read(0xF92E) << 14) + target.read(0xF92C)
        target.write(0xF94A, target.read(0xF92C))
        target.write(0xF94C, target.read(0xF92E))
        return (pointer - 0x4010) // 0x400
    return None


def update_crc(raw):
    length = int.from_bytes(raw[0x7F6:0x7F8], "little")
    raw[0x7FE:0x800] = _crc(raw[0x400:0x400 + length], 15).to_bytes(2, "little")
    return raw


def restore(target, raw, entry):
    assert select(target, raw) == 1
    original = bytes(target.mem.image)
    result = target.call(entry, max_steps=100000)
    assert result.exit_reason == "stop_at", result
    assert bytes(target.mem.image) == original


CASES = next(mark.args[1] for mark in _selection_cases.pytestmark
             if mark.name == "parametrize" and mark.args[0] == "entries,expected")


@pytest.mark.parametrize("entries,expected", CASES)
def test_original_ca_selection_agrees_with_host(target, entries, expected):
    raw = journal([(index, ca_slot(record)) for index, record in entries])
    assert select(target, raw) == expected
    assert select_ms42_slot(raw, "0110CA") == expected


@pytest.mark.parametrize("valid", (True, False))
def test_ca_idle_and_knock_native_crc_and_exact_editor_changes(target, valid):
    raw = bytearray(journal([(1, ca_slot(slot(4)))]))
    put_record(raw, 0x428, bytes(range(64)) + b"\x34\x12")
    update_crc(raw)
    snapshot = make_snapshot("MS42", "retained", raw, software_id="0110CA")
    edited = edit_storage(snapshot, {0x514: -1234, 0x516: 4321, 0x518: -2500,
                                     0x428: 128, 0x467: 116})["data"]
    allowed = {*range(0x514, 0x51A), 0x51C, 0x51D, 0x428, 0x467, 0x46A, 0x46B, 0x7FE, 0x7FF}
    assert all(a == b or index in allowed for index, (a, b) in enumerate(zip(raw, edited)))
    data = bytearray(edited)
    if not valid:
        data[0x51C] ^= 1
        data[0x46A] ^= 1
        update_crc(data)
    restore(target, data, 0x2B324)
    expected = (-1234, 4321, -2500) if valid else (0, 0, 0)
    assert [target.read(at) for at in (0xE148, 0xE14A, 0xE14C)] == [n & 0xFFFF for n in expected]
    restore(target, data, 0x2FBE4)
    cells = (bytes((128,)) + bytes(range(1, 63)) + b"\x74") if valid else b"\x80" * 64
    for bank in range(6):
        assert bytes(target.read_byte(0xC486 + bank * 64 + index) for index in range(64)) == cells
    assert target.read(0xFA98) == (0x1234 if valid else 0)


def test_ca_cam_reference_edits_reach_original_ram_words(target):
    raw = bytearray(journal([(1, ca_slot(slot(4)))]))
    put_record(raw, 0x610, b"\x00\x80\x00\x80" + bytes(16))
    update_crc(raw)
    source = make_snapshot("MS42", "retained", raw, software_id="0110CA")
    changed = edit_storage(source, {0x610: 0xA000, 0x612: 0x9000})
    restore(target, changed["data"], 0x2DC24)
    assert (target.read(0xD042), target.read(0xD1B0)) == (0xA000, 0x9000)
    assert source["data"] == bytes(raw)
    fields = {field["offset"]: field for field in editable_fields(source)}
    assert fields[0x610]["unit"] == fields[0x612]["unit"] == "°"


def test_ca_counter_recovery_and_outer_check_follow_native_owner(target):
    raw = bytearray(journal([(1, ca_slot(slot(4)))]))
    for copies in ((0, 1, 2), (0xFFFE, 0xFFFF, 0), (0xFFFF, 0, 1),
                   (0xBEEF, 0x1235, 0x1236), (0x1234, 0xBEEF, 0x1236),
                   (0x1234, 0x1235, 0xBEEF), (0xFFFF,) * 3, (0, 10, 20)):
        raw[0x410:0x416] = b"".join(value.to_bytes(2, "little") for value in copies)
        update_crc(raw)
        restore(target, raw, 0x1FD2E)
        value = storage_operating_counter(raw[0x410:0x416])
        assert target.read(0xCF04) == (value if value is not None else 0)
    raw[0x410:0x416] = b"\x00\x00\x01\x00\x02\x00"
    update_crc(raw)
    source = make_snapshot("MS42", "retained", raw, software_id="0110CA")
    changed = edit_storage(source, {0x410: 0xFFFF})
    restore(target, changed["data"], 0x1FD2E)
    assert target.read(0xCF04) == 0xFFFF


@pytest.mark.parametrize("out_of_range", (False, True))
@pytest.mark.parametrize("valid", (False, True))
def test_ca_fuel_limits_native_restore_and_bmw_measurement(target, valid, out_of_range):
    image = bytes(target.mem.image)
    word = lambda at: int.from_bytes(image[at:at + 2], "little")
    add_min, add_max = word(0x487E4), word(0x487E2)
    mul_min, mul_max = word(0x487EA), word(0x487E8)
    values = [0x1234, add_min, mul_max, add_max, mul_min]
    if out_of_range:
        values[1], values[2] = add_min - 1, mul_max + 1
    raw = bytearray(journal([(1, ca_slot(slot(4)))]))
    put_record(raw, 0x488, b"".join(value.to_bytes(2, "little") for value in values))
    if not valid:
        raw[0x492] ^= 1
    update_crc(raw)
    restore(target, raw, 0x31E74)
    expected = values[:] if valid else [0x8000] * 5
    if valid and out_of_range:
        expected[:3] = [0x8000] * 3
    assert [target.read(at) for at in (0xE528, 0xE3CC, 0xE3CE, 0xE4BE, 0xE4C0)] == expected
    target.write(0xFD42, target.read(0xFD42) | 8)
    payload = native_ecu(target).execute(0x0B, b"\x91")
    assert [int.from_bytes(payload[i:i + 2], "big") for i in range(0, 8, 2)] == expected[1:]
    if valid and not out_of_range:
        source = make_snapshot("MS42", "retained", raw, software_id="0110CA")
        fields = {field["offset"]: field for field in editable_fields(source, calibration=image)}
        assert (fields[0x48A]["minimum"], fields[0x48A]["maximum"]) == (add_min, add_max)
        assert (fields[0x48C]["minimum"], fields[0x48C]["maximum"]) == (mul_min, mul_max)
        assert fields[0x48A]["unit"] == "ms" and fields[0x48C]["unit"] == "%"
        changed = edit_storage(source, {0x48A: add_max, 0x48C: mul_min}, calibration=image)
        restore(target, changed["data"], 0x31E74)
        assert (target.read(0xE3CC), target.read(0xE3CE)) == (add_max, mul_min)
        with pytest.raises(ValueError, match="integer"):
            edit_storage(source, {0x48A: add_min - 1}, calibration=image)


def test_ca_idle_learning_byte_uses_its_unchanged_native_calibration_limits(target):
    image = bytes(target.mem.image)
    raw = bytearray(journal([(1, ca_slot(slot(4)))]))
    source = make_snapshot("MS42", "retained", raw, software_id="0110CA")
    field = next(field for field in editable_fields(source, calibration=image) if field["offset"] == 0x51A)
    assert (field["minimum"], field["maximum"]) == (image[0x48300] + 1, image[0x482FF])
    for value in (field["minimum"], field["maximum"]):
        changed = edit_storage(source, {0x51A: value}, calibration=image)
        restore(target, changed["data"], 0x2B324)
        assert (target.read_byte(0xE193), target.read_byte(0xE194)) == (value, value)


def test_ca_coolant_history_reloads_through_its_relocated_owner(target):
    raw = bytearray(journal([(1, ca_slot(slot(4)))]))
    put_record(raw, 0x5FE, bytes((120, 100)))
    update_crc(raw)
    source = make_snapshot("MS42", "retained", raw, software_id="0110CA")
    changed = edit_storage(source, {0x5FE: 128, 0x5FF: 116})
    restore(target, changed["data"], 0x3F54E)
    assert (target.read_byte(0xC1A6), target.read_byte(0xC188)) == (128, 116)
    fields = {field["offset"]: field for field in editable_fields(changed)}
    assert fields[0x5FE]["unit"] == fields[0x5FF]["unit"] == "°C"


def test_ca_uses_its_actual_knock_axes_and_does_not_enable_unreviewed_fields():
    image = composed_image("0110CA", "0110C6").image
    base = full_image("0110C6")
    for at, old, new in ((0x2EF2A, 0x90C, 0x910), (0x2EF36, 0x91D, 0x921),
                         (0x2EF50, 0x90C, 0x910), (0x2EF5C, 0x91D, 0x921)):
        assert base[at - 2:at + 2] == b"\xE6\xFC" + old.to_bytes(2, "little")
        assert image[at - 2:at + 2] == b"\xE6\xFC" + new.to_bytes(2, "little")
    raw = bytearray(journal([(1, ca_slot(slot(4)))]))
    put_record(raw, 0x428, bytes(66))
    update_crc(raw)
    source = make_snapshot("MS42", "retained", raw, software_id="0110CA")
    axes = storage_knock_axes(source, image)
    assert axes == {"rpm": [n * 32 for n in image[0x48911:0x48921]],
                    "load": [n * 1389 / 255 for n in image[0x48922:0x48926]]}
    assert edit_storage(source, {}, calibration=image) == source
    with pytest.raises(ValueError, match="not editable"):
        edit_storage(source, {0x472: 32768}, calibration=image)
