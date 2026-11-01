"""Original revision readers and response builders establish read-only OBD fields.

Uses actual pinned DATEN application bytes with a named known boot. Record
checks, bank selection and response serialization run in the canonical model;
no physical communication, storage write or guessed engineering unit is used.
"""

from hashlib import sha256
import os
from pathlib import Path
import pytest
from tests.ms42_ms43_revision_sources import composed_image, full_image
from tests.test_ms42_ms43_config_native import native as native
from tests.test_ms42_ms43_storage_inspection import put_record
from tests.test_ms43_storage_edit_native import restore
from tests.test_ms43_revision_storage_native import OWNERS, BINDINGS
from ms42_ms43_diagnostics import inspect_storage_records

# oxygen offset, result offset/size, reader entries, response entries1/2/3/5/6,
# oxygen window, oxygen RAM, bank-count byte, response flag/output.
PROFILES = {
    '430037': (0x19C, 0x1BA, 120, 0x95062, 0x95190,
               (0x95694, 0x95724, 0x95B34, 0x95BC8, 0x959B0),
               0x948BA, 0x851C, 0x8BE0, 0xFBDA, 0xE5D5),
    '430055': (0x19E, 0x1BC, 120, 0x951E0, 0x9530E,
               (0x958AE, 0x9593E, 0x95D4E, 0x95DE2, 0x95BCA),
               0x94A38, 0x8558, 0x8C2A, 0xFBF1, 0xE5FB),
    '430064': (0x19E, 0x1BC, 120, 0x951F0, 0x9531E,
               (0x958BE, 0x9594E, 0x95D5E, 0x95DF2, 0x95BDA),
               0x94A48, 0x8566, 0x8C3E, 0xFBF3, 0xE605),
    '430065': (0x1A8, 0x1C6, 120, 0x9525A, 0x95388,
               (0x95928, 0x959B8, 0x95DC8, 0x95E5C, 0x95C44),
               0x94AA4, 0x857E, 0x8C56, 0xFBF3, 0xE605),
    '430066': (0x1A8, 0x1C6, 128, 0x9525A, 0x95388,
               (0x95948, 0x959D8, 0x95E2C, 0x95EC0, 0x95CA8),
               0x94AA4, 0x8588, 0x8C64, 0xFBF3, 0xE605),
}
PROFILES['430066-later'] = PROFILES['430066']

def initialized_reader(key, native, monkeypatch):
    import tests
    root = Path(os.environ['MS41EMU_ROOT'])
    monkeypatch.setattr(tests, '__path__', [*tests.__path__, str(root / 'tests')])
    from tests.test_c167_lifecycle import prepare_lifecycle
    image = bytearray(full_image(key) if key == '430055' else composed_image(key, '430069').image)
    image[0x6FFF0:0x6FFF4] = bytes.fromhex('90482442')
    image[0x7FFF0:0x7FFF4] = bytes.fromhex('42244890')
    image = bytes(image)
    cls = type(native('MS43'))
    monkeypatch.setattr(cls, 'IMAGE_SHA256', sha256(image).hexdigest())
    emu, peer, _ = prepare_lifecycle(cls, image)
    peer.storage[:] = b'\xff' * 2048
    put_record(peer.storage, 0x32, b'43241010' if key == '430055' else BINDINGS[key])
    binding = 0xC393A if key == '430055' else OWNERS[key][0][0]
    outcome = emu.cpu.run(emu.cpu.pc, stop_at=binding, max_steps=2_000_000)
    assert outcome.exit_reason == 'stop_at', outcome
    emu.cpu.set_trace(None)
    restore(emu, binding)
    return emu, peer, image


def result_fields(legacy):
    """Independent expectations established from each native response owner."""
    fields = {}
    stride = 36 if legacy else 40
    for bank in range(2):
        base = stride * bank
        fields[1, bank + 1] = (base, base + 2, 2)
        for item in range(4):
            fields[2, bank * 4 + item + 1] = (base + 4 + item * 2, base + 12 + item * 2, 2)
        fields[3, 0x85 + bank] = (base + 20, base + 22, 2)
        for item in range(2):
            fields[6, bank + 1 + item * 2] = (base + 24 + item * 2, base + 28 + item * 2, 2)
        if legacy:
            fields[2, 0x89 + bank] = (base + 32, base + 34, 2)
        else:
            fields[2, 0x30 + bank * 2] = (base + 32, base + 36, 2)
            fields[2, 0xB1 + bank * 2] = (base + 34, base + 38, 2)
    for component, start in ((0x92, 0), (0x13, 4), (0x14, 8), (0x95, 12), (0x96, 16),
                             (0x17, 20), (0x98, 24), (0x87, 28), (0x88, 32), (9, 36)):
        base = stride * 2 + start
        fields[5, component] = (base, base + 2, 1 if component == 0x17 else 2)
    return fields


def responses(emu, profile):
    _, _, size, _, _, entries, _, _, _, flag, output = profile
    values = {}
    for test, entry, count in zip((1, 2, 3, 5, 6), entries,
                                   (2, 12 if size == 128 else 10, 2, 10, 4)):
        emu.mem.write_byte(flag, 0)
        for _ in range(count):
            restore(emu, entry)
            component = emu.mem.read_byte(output)
            if test == 6:
                component &= 0x7F
            encoded = bytes(emu.mem.read_byte(output + i) for i in range(1, 5))
            values[test, component] = (int.from_bytes(encoded[:2], 'big'),
                                       int.from_bytes(encoded[2:], 'big'))
        assert emu.mem.read_byte(flag) == 0
    return values


def inspected_record(raw, key, name):
    rows = inspect_storage_records(raw, 'MS43', key.split('-')[0], 'eeprom')
    return next(row for row in rows if row['name'] == name)


def decoded_values(row):
    return {label: int(value.split()[0], 16) if isinstance(value, str) else value
            for label, value in row['values']}


@pytest.mark.parametrize('key', PROFILES)
def test_obd_records(key, native, monkeypatch):
    emu, peer, image = initialized_reader(key, native, monkeypatch)
    oxygen, results, size, oxygen_read, result_read, entries, tiny, bank_ram, bank_count, flag, output = PROFILES[key]
    # Pin the runtime bank selector from the actual original reader operand.
    code = image[oxygen_read & 0x7FFFF:(oxygen_read & 0x7FFFF) + 0x8A]
    marker = code.index(bytes.fromhex('49823d0d'))
    assert int.from_bytes(code[marker - 2:marker], 'little') == bank_count
    emu.mem.write_byte(bank_count, 2)
    writes = peer.storage_write_attempts
    for tag in (0x3000, 0xA000):
        payload = b''.join((tag + i).to_bytes(2, 'little') for i in range(size // 2))
        put_record(peer.storage, results, payload)
        before = bytes(peer.storage)
        restore(emu, result_read)
        expected = {field: tuple(int.from_bytes(payload[offset:offset + width], 'little')
                                for offset in (result, limit))
                    for field, (result, limit, width) in result_fields(size == 120).items()}
        assert responses(emu, PROFILES[key]) == expected
        labels = {}
        for (test, component), pair in expected.items():
            name = f'{component:02X}/{component | 0x80:02X}' if test == 6 else f'{component:02X}'
            for meaning, value in zip(('result', 'limit'), pair):
                labels[f'Test {test:02X}, component {name} {meaning} (raw)'] = value
        row = inspected_record(peer.storage, key, 'OBD result blocks')
        assert row['address'] == results and row['size'] == size and row['crc_valid']
        assert decoded_values(row) == labels
        assert bytes(peer.storage) == before

    peer.storage[results + size] ^= 1
    before = bytes(peer.storage)
    restore(emu, result_read)
    rejected = responses(emu, PROFILES[key])
    assert all(rejected[field] != value for field, value in expected.items())
    row = inspected_record(peer.storage, key, 'OBD result blocks')
    assert not row['crc_valid'] and row['values'] == []
    assert bytes(peer.storage) == before

    # Both banks use the same 28-byte format. Exercise direct byte values,
    # scaled words, and saturation through the original response builder.
    for high in (False, True):
        payload = b''.join(b''.join(((0x1000 if high else 0) + value + bank).to_bytes(2, 'little')
                                   for value in (10, 20, 30, 40, 50, 60))
                           + bytes((70 + bank, 80 + bank)) for bank in range(2))
        put_record(peer.storage, oxygen, payload)
        before = bytes(peer.storage)
        restore(emu, oxygen_read)
        assert bytes(emu.mem.read_byte(bank_ram + i) for i in range(28)) == payload
        labels = {}
        for bank in range(2):
            for index, (test, result, limit, width) in enumerate(
                    ((3, 13, None, 1), (4, 12, None, 1), (5, 6, 10, 2),
                     (6, 4, 8, 2), (7, 0, 2, 2))):
                emu.reg.set_word(9, bank_ram + bank * 14)
                outcome = emu.run_from(tiny + index * 16, stop_at=(tiny + 0x5C,), max_steps=2000)
                assert outcome.exit_reason == 'stop_at', outcome
                for offset, output_delta in ((result, 1), (limit, 3)):
                    if offset is None:
                        continue
                    at = bank * 14 + offset
                    expected = int.from_bytes(payload[at:at + width], 'little')
                    meaning = 'result' if output_delta == 1 else 'limit'
                    sensor = 1 if bank == 0 else 0x10
                    labels[f'Sensor {sensor:02X}, test {test:02X} {meaning} (raw)'] = expected
                    if test in (5, 6):
                        expected = min(255, expected * 5)
                    elif test == 7:
                        expected = min(255, expected // 2)
                    assert emu.mem.read_byte(output + output_delta) == expected
        row = inspected_record(peer.storage, key, 'OBD monitor banks')
        assert row['address'] == oxygen and row['size'] == 28 and row['crc_valid']
        assert decoded_values(row) == labels
        assert bytes(peer.storage) == before

    # A one-bank runtime only restores its first saved bank.
    emu.mem.write_byte(bank_count, 1)
    for i in range(28):
        emu.mem.write_byte(bank_ram + i, 0xA5)
    restore(emu, oxygen_read)
    assert bytes(emu.mem.read_byte(bank_ram + i) for i in range(28)) == payload[:14] + b'\xA5' * 14
    emu.mem.write_byte(bank_count, 2)

    # A bad oxygen-record checksum does not reload either RAM bank.
    peer.storage[oxygen + 28] ^= 1
    for i in range(28):
        emu.mem.write_byte(bank_ram + i, 0xA5)
    before = bytes(peer.storage)
    restore(emu, oxygen_read)
    assert bytes(emu.mem.read_byte(bank_ram + i) for i in range(28)) == b'\xA5' * 28
    row = inspected_record(peer.storage, key, 'OBD monitor banks')
    assert not row['crc_valid'] and row['values'] == []
    assert bytes(peer.storage) == before
    assert peer.storage_write_attempts == writes
    assert bytes(emu.mem.image) == image
