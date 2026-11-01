"""Exact additional storage revisions: local files only, no transport admission."""

import pytest

from ms42_ms43_diagnostics import select_ms42_slot, inspect_storage_records, format_storage_inspection
from ms42_ms43_storage import make_snapshot
from ms42_ms43_storage_edit import editable_fields, edit_storage
from tests.test_ms42_ms43_config_revisions import private_sources as private_sources
from tests.test_ms42_ms43_storage_edit_native import slot, journal, native_selection

_OWNERS = {
    "MS42": (
        (0x76354, 0x76410),
        (0x7684e, 0x76942),
        (0x764ca, 0x7684e),
        (0x75db2, 0x75dd0),
        (0x36c4c, 0x36cb0),
        (0x36cb0, 0x36d3e),
        (0x27fb4, 0x28058),
        (0x28058, 0x2818e),
        (0x3871a, 0x38924),
        (0x38924, 0x38ac4),
        (0x31e3e, 0x32030),
        (0x32030, 0x321c0),
        (0x2b324, 0x2b3ea),
        (0x2b3ea, 0x2b504),
        (0x3f3c0, 0x3f430),
        (0x3f430, 0x3f4e8),
        (0x2dc24, 0x2ddae),
        (0x2ddae, 0x2df88),
        (0x2633c, 0x2639e),
        (0x2639e, 0x2642c),
        (0x3f4e8, 0x3f5ac),
        (0x3f5ac, 0x3f736),
        (0x321c0, 0x323c2),
        (0x323c2, 0x3263a),
        (0x348ba, 0x3493a),
        (0x3493a, 0x34a1c),
    ),
    "MS43": (
        (0xbdc2c, 0xbdc92),
        (0xbdc92, 0xbdce4),
        (0xbd7b0, 0xbd88e),
        (0xbd918, 0xbdb28),
        (0xc9cf8, 0xc9d1a),
        (0xb1928, 0xb19c6),
        (0xb19c6, 0xb1a30),
        (0xb496e, 0xb4ae8),
        (0xb4ae8, 0xb4ba2),
        (0xb6d00, 0xb6dcc),
        (0xb6dcc, 0xb6e88),
        (0xdb310, 0xdb472),
        (0xdb472, 0xdb4f8),
        (0xdb4f8, 0xdb62e),
        (0xdb62e, 0xdb79c),
        (0xba4c0, 0xba57c),
        (0xba57c, 0xba61a),
        (0x93748, 0x937a6),
        (0x937a6, 0x93808),
        (0xad556, 0xad5d6),
        (0xad5d6, 0xad638),
        (0xc3df6, 0xc3ef8),
        (0xc3ef8, 0xc3fa0),
        (0x9b2f2, 0x9b38c),
        (0x9b38c, 0x9b3fc),
        (0x914cc, 0x9158c),
        (0x9158c, 0x91614),
        (0xa20ac, 0xa21ec),
        (0xa21ec, 0xa2264),
        (0x956bc, 0x95714),
        (0x95714, 0x95758),
        (0xada30, 0xadb62),
        (0xadb62, 0xadb9e),
        (0xb2b98, 0xb2be8),
        (0xb2be8, 0xb2c36),
    ),
}


_INSPECTION_OWNERS = {
    "MS42": (
        (0x145a2, 0x14600),
        (0x14600, 0x14666),
        (0x14666, 0x146cc),
        (0x146cc, 0x1473a),
        (0x1473a, 0x147da),
        (0x147da, 0x1487a),
        (0x1487a, 0x1491a),
        (0x1491a, 0x149ba),
        (0x24bf6, 0x24d6c),
        (0x24d6c, 0x24f46),
        (0x2ff4a, 0x2fff8),
    ),
    "MS43": (
        (0x9525a, 0x952e4),
        (0x952e4, 0x9535a),
        (0x95388, 0x9542a),
        (0x9542a, 0x954ba),
        (0x9bdbe, 0x9be20),
        (0x9be20, 0x9be7e),
        (0x9d968, 0x9da76),
        (0x9da76, 0x9dace),
        (0xa257e, 0xa2646),
        (0xa2646, 0xa26fe),
        (0xa3e0c, 0xa3eac),
        (0xa3eac, 0xa3f00),
        (0xa4550, 0xa45a8),
        (0xa45a8, 0xa45ec),
        (0xa4c16, 0xa4c86),
        (0xa4c86, 0xa4cda),
        (0xab112, 0xab15e),
        (0xab15e, 0xab19a),
        (0xab974, 0xaba94),
        (0xaba94, 0xabb68),
        (0xb2a94, 0xb2b08),
        (0xb2b08, 0xb2b4a),
        (0xbbede, 0xbbf2e),
        (0xbbf2e, 0xbbf6a),
        (0xbdd3c, 0xbdd88),
        (0xbef00, 0xbef56),
        (0xbf5f0, 0xbf664),
        (0xbf664, 0xbf6a8),
        (0xbf8e6, 0xbf938),
        (0xbf94c, 0xbf98e),
        (0xc0000, 0xc0078),
        (0xc0078, 0xc00d0),
        (0xc00d0, 0xc0412),
        (0xc0412, 0xc07aa),
        (0xc3d5a, 0xc3db2),
        (0xc3db2, 0xc3df6),
        (0xc8cea, 0xc8f84),
        (0xc8f84, 0xc92fc),
        (0xd5f58, 0xd5ff0),
        (0xd5ff0, 0xd60a0),
        (0xd7eb8, 0xd7fb8),
        (0xd7fb8, 0xd80ac),
        (0xd90f2, 0xd9156),
        (0xd9156, 0xd9192),
        (0xdb79c, 0xdb7e8),
        (0xdb7e8, 0xdb858),
    ),
}

def test_complete_storage_owners_and_bindings_are_independently_matched(private_sources):
    for start, end in (*_OWNERS["MS42"], *_INSPECTION_OWNERS["MS42"]):
        memory = private_sources["c7_memory"]
        assert all(address in memory for address in range(start, end))
        assert bytes(memory[address] for address in range(start, end)) == private_sources["c6"][start:end]
    for start, end in (*_OWNERS["MS43"], *_INSPECTION_OWNERS["MS43"]):
        assert private_sources["x001"][start - 0x80000:end - 0x80000] == private_sources["stock"][start - 0x80000:end - 0x80000]
    assert bytes(private_sources["c7_memory"][a] for a in range(0x5003C, 0x50046)) == b"6577225108"
    assert private_sources["c6"][0x5003C:0x50046] == b"6577225107"
    assert private_sources["x001"][0x6FFB2:0x6FFBA] == private_sources["stock"][0x6FFB2:0x6FFBA] == b"43290010"
    # X001 changes only one output flag in the secondary-air restore. Its CRC
    # envelope remains verified; no decoded/editable payload fields are claimed.
    assert private_sources["x001"][0x3EE68:0x3EEEE] == private_sources["stock"][0x3EE68:0x3EEEE]
    assert private_sources["x001"][0x3EEF0:0x3EF00] == private_sources["stock"][0x3EEF0:0x3EF00]
    assert private_sources["stock"][0x3EEEE:0x3EEF0] == b"\x3F\x0E"  # BSET FD1C.3
    assert private_sources["x001"][0x3EEEE:0x3EEF0] == b"\xCC\x00"  # NOP


def test_x001_changed_synchronization_is_not_interpreted_as_stock():
    raw = bytes(0x800)
    records = inspect_storage_records(raw, "MS43", "43X001", "eeprom")
    assert len(records) == 39
    assert 0x26 not in {record["address"] for record in records}
    secondary_air = next(record for record in records if record["address"] == 0x31E)
    assert secondary_air["values"] == []
    text = format_storage_inspection(raw, "MS43", "43X001", "eeprom")
    assert "remain raw: stock synchronization interpretation is not verified" in text
    assert "secondary-air record has different restore flags" in text
    assert "Immobilizer synchronization" not in text and "All decoded copies agree" not in text


@pytest.mark.parametrize("software,binding", (("0110C6", b"6577225107"), ("0110C7", b"6577225108")))
def test_ms42_revision_binding_is_not_guessed_from_family(software, binding):
    raw = journal(((0, slot(10, descriptor=binding)), (1, slot(11, descriptor=binding))))
    source = make_snapshot("MS42", "retained", raw, software_id=software)
    assert select_ms42_slot(raw, software) == 1
    assert len(editable_fields(source)) == 3
    assert inspect_storage_records(raw, "MS42", software, "retained")
    other = "0110C7" if software == "0110C6" else "0110C6"
    assert select_ms42_slot(raw, other) is None
    source["software_id"] = other
    with pytest.raises(ValueError, match="No MS42 journal"):
        edit_storage(source, {0x514: 123})


def test_c7_edited_idle_record_reloads_in_original_supplied_program(private_sources, monkeypatch):
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path.home() / "ECU Emulator"))
    from ms41emu.ms42 import MS42

    image = bytearray(b"\xFF" * 0x80000)
    for memory in (private_sources["c7_memory"], private_sources["c7_cal_memory"]):
        for address, value in memory.items():
            image[address] = value
    raw = journal(((0, slot(10, descriptor=b"6577225108")),))
    source = make_snapshot("MS42", "retained", raw, software_id="0110C7")
    field = next(field for field in editable_fields(source, calibration=image) if field["offset"] == 0x11A)
    target = edit_storage(source, {0x114: -1234, 0x11A: field["maximum"]}, calibration=image)["data"]
    emu = MS42(image, experimental=True)
    assert native_selection(emu, target) == select_ms42_slot(target, "0110C7") == 0
    emu.write(0xF94A, emu.read(0xF92C))
    emu.write(0xF94C, emu.read(0xF92E))
    result = emu.call(0x2B324, max_steps=100_000)
    assert result.exit_reason == "stop_at"
    assert emu.read(0xE148) == -1234 & 0xFFFF
    assert emu.read_byte(0xE193) == field["maximum"]
    assert bytes(emu.mem.image[:0x4000]) == image[:0x4000]
    assert bytes(emu.mem.image[0x8000:]) == image[0x8000:]


def test_x001_edited_eeprom_record_reloads_through_native_binding(private_sources, monkeypatch):
    from pathlib import Path
    import tests
    from tests.test_ms43_storage_edit_native import restore, read_word
    from tests.test_ms42_ms43_storage_inspection import put_record

    root = Path.home() / "ECU Emulator"
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.setattr(tests, "__path__", [*tests.__path__, str(root / "tests")])
    from ms41emu.ms43 import MS43
    from tests.test_c167_lifecycle import prepare_lifecycle

    class ReviewedX001(MS43):
        @classmethod
        def load(cls, image):
            return cls(image, experimental=True)

    image = private_sources["x001"]
    emu, peer, _ = prepare_lifecycle(ReviewedX001, image)
    result = emu.cpu.run(emu.cpu.pc, stop_at=0xE0094, max_steps=1_000_000)
    assert result.exit_reason == "stop_at"
    emu.cpu.set_trace(None)
    put_record(peer.storage, 0x280, bytes.fromhex("01000200030080a5"))
    source = make_snapshot("MS43", "eeprom", peer.storage, software_id="43X001")
    field = next(field for field in editable_fields(source, calibration=image) if field["offset"] == 0x286)
    target = edit_storage(source, {0x280: -1234, 0x286: field["minimum"]}, calibration=image)["data"]
    peer.storage[:] = target
    writes = peer.storage_write_attempts
    restore(emu, 0xBDC2C)
    assert not emu.mem.read_word_direct(0xFD46) & 0x20
    restore(emu, 0xB1928)
    assert read_word(emu, 0x40882) == -1234 & 0xFFFF
    assert emu.mem._physical_read(0x401EE) == field["minimum"]
    assert bytes(peer.storage) == target and peer.storage_write_attempts == writes
    assert bytes(emu.mem.image) == image
    assert inspect_storage_records(target, "MS43", "43X001", "eeprom")
    assert inspect_storage_records(bytes(0x4000), "MS43", "43X001", "retained") is None
