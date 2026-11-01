"""Original header-copy helpers; supplied flash status is separate admission.

These read-only calls establish the stored-copy display. They do not establish
the complete immobilizer handshake or authorize pairing with another module.
"""

import pytest

from ms42_ms43_diagnostics import _ms43_start_triplet, _ms43_sync_copies, format_storage_inspection
from tests.ms42_ms43_revision_sources import full_image
from tests.test_ms42_ms43_config_native import native as native
from tests.test_ms43_revision_obd_storage_native import initialized_reader
from tests.test_ms43_storage_edit_native import restore


# Byte-copy helper, synchronization reader, flash-state RAM, recovered RAM,
# primary/backup rejection flag register and its first bit.
OWNERS = {
    '430037': (0xBF924, 0xB30D8, 0xE2BA, 0xE2AA, 0xFD30, 10),
    '430055': (0xC39F2, 0xB35FC, 0xE2D8, 0xE2C8, 0xFD32, 9),
    '430064': (0xBF82C, 0xB3786, 0xE2DA, 0xE2CA, 0xFD34, 2),
    '430065': (0xBFCC8, 0xB15BC, 0xE2DA, 0xE2CA, 0xFD34, 8),
    '430066': (0xBDD04, 0xAF320, 0xE2DA, 0xE2CA, 0xFD34, 4),
    '430066-later': (0xBDD04, 0xAF320, 0xE2DA, 0xE2CA, 0xFD34, 4),
}


def copies(value, damaged=None):
    encoded = (value, value ^ 0x00FFFF00, value ^ 0xFF0000FF)
    result = bytearray(b''.join(word.to_bytes(4, 'little') for word in encoded) + b'\x35\xA5')
    if damaged == 'all':
        result[:12] = b'\0' * 12
    elif damaged is not None:
        result[damaged * 4] ^= 0x40
    return result


@pytest.mark.parametrize('key', OWNERS)
def test_read_only_header_copy_recovery_matches_native(key, native, monkeypatch):
    emu, peer, image = initialized_reader(key, native, monkeypatch)
    byte_helper, sync_reader, flash_state, recovered, flags, first_bit = OWNERS[key]
    reference = full_image('430069')
    start = byte_helper & 0x7FFFF
    assert image[start:start + 0x58] == reference[0x3DCE4:0x3DD3C]
    for triplet in ((1, 2, 3), (255, 0, 1), (255, 0, 19), (90, 2, 3),
                    (1, 90, 3), (1, 2, 90), (1, 8, 19), (0, 255, 2)):
        raw = bytes(6) + bytes(triplet)
        for index, value in enumerate(triplet):
            emu.write_byte(0xF9C0 + index, value)
        emu.reg.set_word(12, 0xF9C0)
        emu.reg.set_word(13, 1)
        restore(emu, byte_helper)
        expected = _ms43_start_triplet(raw)
        assert (emu.reg.get_word(4) & 0xFF) == (2 if expected is None else 1)
        if expected is not None:
            assert emu.mem.read_byte(0xF9C0) == expected

    value = 0x12345678
    writes = peer.storage_write_attempts
    for primary_bad, backup_bad in ((None, None), (0, None), (1, None), (2, None),
                                    ('all', None), ('all', 0), ('all', 1),
                                    ('all', 2), ('all', 'all')):
        peer.storage[0xA:0x18] = copies(value, backup_bad)
        peer.storage[0x18:0x26] = copies(value, primary_bad)
        before = bytes(peer.storage)
        emu.write(flash_state, 0xFEFE)
        restore(emu, sync_reader)
        primary = _ms43_sync_copies(peer.storage, 0x18)[2]
        backup = _ms43_sync_copies(peer.storage, 0xA)[2]
        assert primary == (None if primary_bad == 'all' else value)
        assert backup == (None if backup_bad == 'all' else value)
        selected = primary if primary is not None else backup
        actual = emu.read(recovered) | emu.read(recovered + 2) << 16
        assert actual == (0 if selected is None else ~selected & 0xFFFFFFFF)
        assert bool(emu.read(flags) & (1 << first_bit)) == (primary is None)
        assert bool(emu.read(flags) & (2 << first_bit)) == (selected is None)
        assert bytes(peer.storage) == before
    for value in (0, 0xFFFFFFFF):
        peer.storage[0xA:0x18] = copies(value)
        peer.storage[0x18:0x26] = copies(value)
        before = bytes(peer.storage)
        for supplied_state in (0xFEFE, 0xECEC, 0xC8C8, 0xFFFF):
            emu.write(flash_state, supplied_state)
            restore(emu, sync_reader)
            actual = emu.read(recovered) | emu.read(recovered + 2) << 16
            assert actual == (0 if supplied_state == 0xFFFF else ~value & 0xFFFFFFFF)
            assert bytes(peer.storage) == before
    peer.storage[0xA:0x18] = copies(0x12345678)
    peer.storage[0x18:0x26] = copies(0x12345678)
    before = bytes(peer.storage)
    report = format_storage_inspection(peer.storage, 'MS43', key.split('-')[0], 'eeprom')
    assert 'Recoverable stored value: 0x12345678' in report
    assert 'remain raw for this revision' not in report
    assert 'not the flash ISN' in report and 'depends on flash pairing state' in report
    assert bytes(peer.storage) == before
    assert peer.storage_write_attempts == writes
    assert bytes(emu.mem.image) == image
