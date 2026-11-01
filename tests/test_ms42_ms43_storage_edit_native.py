"""Stock 0110C6 selection/reload of synthetic storage; no hardware or flash code changes."""

import pytest

from checksum import _crc
from tests.test_ms42_ms43_config_native import native as native


from ms42_ms43_storage_edit import select_ms42_slot, edit_storage
from ms42_ms43_storage import make_snapshot


def slot(sequence, *, footer=None, length=0x312, descriptor=b"6577225107",
         marker=0, bad_crc=False, idle=b"\x00\x00" * 3 + b"\x80\xA5"):
    raw = bytearray(b"\xFF" * 0x400)
    raw[:4] = sequence.to_bytes(4, "little")
    raw[4:14] = descriptor
    raw[14:16] = marker.to_bytes(2, "little")
    raw[0x114:0x11C] = idle
    raw[0x11C:0x11E] = _crc(idle, 0xF).to_bytes(2, "little")
    raw[0x3F6:0x3F8] = length.to_bytes(2, "little")
    raw[0x3F8:0x3FC] = (sequence ^ 0xFFFFFFFF if footer is None else footer).to_bytes(4, "little")
    raw[0x3FE:] = (_crc(raw[:length], 0xF) ^ int(bad_crc)).to_bytes(2, "little")
    return raw


def journal(entries):
    raw = bytearray(b"\xFF" * 0x4000)
    for index, record in entries:
        raw[index * 0x400:(index + 1) * 0x400] = record
    return bytes(raw)


def native_selection(emu, raw):
    emu.mem.image[0x4000:0x8000] = raw
    before = bytes(emu.mem.image)
    emu.reg.set_word(12, 0x3C)
    emu.reg.set_word(13, 0x14)
    result = emu.call(0x076354, max_steps=500_000)
    assert result.exit_reason == "stop_at"
    assert bytes(emu.mem.image) == before
    flags = emu.read(0xFD18)
    if flags & 0x100 and not flags & 0x400:
        address = (emu.read(0xF92E) << 14) + emu.read(0xF92C)
        return (address - 0x4010) // 0x400
    return None


@pytest.mark.parametrize("entries,expected", [
    ([], None),
    ([(0, slot(4)), (1, slot(5))], 1),
    ([(0, slot(5)), (1, slot(4))], 0),
    ([(0, slot(5)), (9, slot(5))], 0),
    ([(0, slot(0xFFFFFFFE)), (1, slot(1))], 0),
    ([(0, slot(0xFFFFFFFE)), (1, slot(0x12345678, footer=0))], 1),
    ([(0, slot(9)), (1, slot(10, footer=0x77777777))], 1),
    ([(0, slot(9)), (1, slot(0x77777777, footer=0xFFFFFFF5))], 1),
    ([(0, slot(100, footer=100)), (1, slot(101, footer=200))], 1),
    ([(0, slot(9)), (1, slot(0xFFFFFFFF, footer=12)),
      (2, slot(10, footer=0x77777777))], 2),
    ([(15, slot(9)), (0, slot(10, footer=0x77777777))], 0),
    ([(0, slot(100, footer=100)), (3, slot(200, footer=200)),
      (9, slot(300, footer=300))], 3),
    ([(9, slot(100, footer=100)), (12, slot(200, footer=200))], 12),
    ([(0, slot(0)), (2, slot(0xFFFFFFFF))], 2),
    ([(0, slot(4)), (1, slot(5, bad_crc=True))], 0),
    ([(0, slot(4)), (1, slot(5, bad_crc=True, marker=0xFFFF))], 0),
    ([(0, slot(4)), (1, slot(5, marker=0xFFFF))], 1),
    ([(0, slot(4)), (1, slot(5, descriptor=b"1234567890"))], None),
    ([(0, slot(5, length=16))], None),
    ([(0, slot(5, length=17))], 0),
    ([(0, slot(5, length=998))], 0),
    ([(0, slot(5, length=999))], None),
])
def test_ms42_selected_slot_matches_original_scanner(native, entries, expected):
    raw = journal(entries)
    assert native_selection(native("MS42"), raw) == expected
    assert select_ms42_slot(raw) == expected


@pytest.mark.parametrize("length", [0, 0x3FFF, 0x4001])
def test_ms42_selected_slot_rejects_only_wrong_buffer_length(length):
    with pytest.raises(ValueError, match="16 KiB"):
        select_ms42_slot(bytes(length))


@pytest.mark.parametrize("previous,expected", [(0, 1), (0xFFFFFFFD, 0xFFFFFFFE),
                                                (0xFFFFFFFE, 0xFFFFFFFE), (0xFFFFFFFF, 0)])
def test_native_sequence_increment_preserves_its_actual_boundary_behavior(native, previous, expected):
    emu = native("MS42")
    emu.write(0xF91C, previous & 0xFFFF)
    emu.write(0xF91E, previous >> 16)
    before = bytes(emu.mem.image)
    result = emu.run_from(0x076032, stop_at=(0x076058,), max_steps=20)
    assert result.exit_reason == "stop_at"
    assert emu.read(0xF91C) | (emu.read(0xF91E) << 16) == expected
    assert bytes(emu.mem.image) == before


@pytest.mark.parametrize("words", [(-32768, 32767, -1), (0, 0, 0), (1234, -4321, 8765)])
@pytest.mark.parametrize("damaged_newer", [False, True])
def test_edited_idle_words_reload_without_scaling_or_clamping(native, words, damaged_newer):
    emu = native("MS42")
    original = bytearray(emu.mem.image[0x4000:0x8000])
    if damaged_newer:
        sequence = int.from_bytes(original[0x400:0x404], "little") + 1
        original[0x800:0xC00] = slot(sequence, bad_crc=True)
    original = bytes(original)
    selected = select_ms42_slot(original)
    assert selected == 1
    base = selected * 0x400
    start = base + 0x114
    snapshot = make_snapshot("MS42", "retained", original, software_id="0110C6")
    modified = edit_storage(snapshot, {start + i * 2: value for i, value in enumerate(words)})["data"]
    assert snapshot["data"] == original
    assert modified[:base] == original[:base]
    assert modified[base + 0x400:] == original[base + 0x400:]
    changed = {i for i, (old, new) in enumerate(zip(original, modified)) if old != new}
    assert changed <= set(range(start, start + 6)) | {start + 8, start + 9, base + 0x3FE, base + 0x3FF}
    assert native_selection(emu, modified) == selected
    emu.write(0xF94A, emu.read(0xF92C))
    emu.write(0xF94C, emu.read(0xF92E))
    result = emu.call(0x02B324, max_steps=100_000)
    assert result.exit_reason == "stop_at"
    assert [emu.read(address) for address in (0xE148, 0xE14A, 0xE14C)] == [value & 0xFFFF for value in words]
    assert bytes(emu.mem.image[0x4000:0x8000]) == modified


def restore_record(emu, offset, payload, entry, valid):
    start = 0x4010 + offset
    emu.mem.image[start:start + len(payload)] = payload
    emu.mem.image[start + len(payload):start + len(payload) + 2] = (
        _crc(payload, 0xF) ^ int(not valid)).to_bytes(2, "little")
    emu.write(0xF94A, 0x10)
    emu.write(0xF94C, 1)
    emu.write(0xFD18, 0x100)
    before = bytes(emu.mem.image)
    result = emu.call(entry, max_steps=100_000)
    assert result.exit_reason == "stop_at"
    assert bytes(emu.mem.image) == before


@pytest.mark.parametrize("valid", [False, True])
@pytest.mark.parametrize("offset,entry,payload,words,byte", [
    (0x5C, 0x036C4C, b"\x34\x12\xA5\x5A", ((0xFA36, 0x1234, 0x7FFF),), None),
    (0x21A, 0x02633C, b"\xA3\x6E", (), (0xFAF8, 0xA3, 1)),
    (0x258, 0x0348BA, b"\x34\x12\x78\x56\x9A\xA5",
     ((0xE1C6, 0x1234, 0), (0xE1C8, 0x5678, 0)), (0xC1E3, 0x9A, 0)),
])
def test_new_ms42_record_fields_follow_native_restore(native, offset, entry, payload, words, byte, valid):
    emu = native("MS42")
    restore_record(emu, offset, payload, entry, valid)
    for address, accepted, rejected in words:
        assert emu.read(address) == (accepted if valid else rejected)
    if byte:
        address, accepted, rejected = byte
        assert emu.read_byte(address) == (accepted if valid else rejected)


@pytest.mark.parametrize("valid", [False, True])
@pytest.mark.parametrize("out_of_range", [False, True])
def test_fuel_adaptation_stored_words_and_tune_limits(native, valid, out_of_range):
    emu = native("MS42")
    word = lambda address: int.from_bytes(emu.mem.image[address:address + 2], "little")
    add_min, add_max = word(0x487E0), word(0x487DE)
    mul_min, mul_max = word(0x487E6), word(0x487E4)
    values = [0x1234, add_min, mul_max, add_max, mul_min]
    if out_of_range:
        values[1], values[2] = add_min - 1, mul_max + 1
    payload = b"".join(value.to_bytes(2, "little") for value in values)
    restore_record(emu, 0x78, payload, 0x031E3E, valid)
    expected = [0x8000] * 5 if not valid else values[:]
    if valid and out_of_range:
        # Rejected bank1 additive data also resets the common correction.
        expected[:3] = [0x8000] * 3
    assert [emu.read(address) for address in (0xE518, 0xE3C8, 0xE3CA, 0xE4B2, 0xE4B4)] == expected
    assert [emu.read(address) for address in (0xE206, 0xE34A, 0xE350, 0xE434, 0xE43A)] == expected
