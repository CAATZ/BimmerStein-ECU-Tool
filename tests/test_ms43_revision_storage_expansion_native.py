"""Native restore boundaries for additional revision-specific EEPROM fields."""

from hashlib import sha256
import os
from pathlib import Path

import pytest

from tests.ms42_ms43_revision_sources import composed_image, full_image
from tests.ms43_revision_storage_expansion_profiles import PROFILES
from tests.test_ms42_ms43_config_native import native as native
from tests.test_ms42_ms43_storage_inspection import put_record
from tests.test_ms43_revision_storage_native import OWNERS, BINDINGS
from tests.test_ms43_storage_edit_native import restore
from tests.test_ms42_ms43_storage_completion import counter_record
from ms42_ms43_diagnostics import storage_operating_counter
from ms42_ms43_storage import make_snapshot
from ms42_ms43_storage_edit import editable_fields, edit_storage
from ms42_ms43_storage_units import storage_display_value
from ms42_ms43_live import catalog_for
from tests.test_ms43_revision_storage_native import sample as revision_sample
from tests.test_ms43_430055_storage_units import sample as sample055


# VANOS validity word and two bits; knock flag; throttle marker; retained booleans.
FLAGS = {
    "430037": ((0xFD36, 10, 11), (0xFD38, 4), (0xFD14, 5), (0xFD10, 3, 4)),
    "430055": ((0xFD38, 9, 10), (0xFD3A, 3), (0xFD14, 3), (0xFD10, 6, 7)),
    "430064": ((0xFD3A, 2, 3), (0xFD3A, 12), (0xFD14, 3), (0xFD10, 6, 7)),
    "430065": ((0xFD3A, 8, 9), (0xFD3C, 2), (0xFD14, 4), (0xFD10, 6, 7)),
    "430066": ((0xFD3A, 4, 5), (0xFD3A, 14), (0xFD14, 0), (0xFD10, 2, 3)),
}
COUNTERS = {
    "430037": (0xAE1D2, 0xAE200, 0x407B2),
    "430055": (0xAD5A6, 0xAD5D4, 0x407EE),
    "430064": (0xAD784, 0xAD7B2, 0x407FE),
    "430065": (0xD71D8, 0xD7206, 0x40816),
    "430066": (0xD8FAE, 0xD8FDC, 0x40824),
}
UNIT_OWNERS = {
    # Pressure startup, gear-factor arithmetic window, stopped-state coolant copies, BMW dispatcher.
    "430037": (0x901A0, 0xAE00C, (0xAC356, 0xAC362), 0xA6286),
    "430055": (0xD3F14, 0xAD320, (0xAB68A, 0xAB696), 0xA53D4),
    "430064": (0xD3F14, 0xAD4FE, (0xAB7BE, 0xAB7CA), 0xA5504),
    "430065": (0xD3F14, 0xD6F52, (0xABE76, 0xABE82), 0xA5C1E),
    "430066": (0xD3F14, 0xD8D28, (0xAA0A0, 0xAA0AC), 0xA5C1E),
}


def verify_scalar_unit_owners(emu, image, key):
    pressure_init, gear_window, coolant_copies, dispatcher = UNIT_OWNERS[key.split("-")[0]]
    word = lambda offset: int.from_bytes(image[offset:offset + 2], "little")
    pressure = next(fields[0][3][0] for group, _, _, _, fields in PROFILES[key] if group == 0x300)
    start = pressure_init & 0x7FFFF
    maximum, minimum, nominal = (word(0x70000 + word(start + delta)) for delta in (14, 20, 26))
    emu.write(word(start + 2), (minimum + maximum) // 2)
    restore(emu, pressure_init)
    physical_word = lambda address: emu.mem._physical_read(address) | emu.mem._physical_read(address + 1) << 8
    actual, filtered = physical_word(pressure), physical_word(pressure - 2)
    assert abs(actual - filtered * 32768 / nominal) <= 1
    # The original consumer divides the speed ratio by its unity-at-0100 correction.
    start = gear_window & 0x7FFFF
    assert image[start:start + 2] == b"\xF2\xFC"
    assert image[start + 4:start + 10] == bytes.fromhex("e6fd0001f2fe")
    ratio, factor, corrected = (word(start + delta) for delta in (2, 10, 18))
    for value in (128, 256, 512):
        emu.write(ratio, 1280)
        emu.write(factor, value)
        result = emu.cpu.run(gear_window, stop_at=gear_window + 20, max_steps=10000)
        assert result.exit_reason == "stop_at", result
        assert emu.read(corrected) == 1280 * 256 // value
    # The stored bytes and the public BMW coolant field use the identical raw code.
    channel = next(item for item in catalog_for("MS43", ecu_id=key.split("-")[0])
                   if item.name == "Coolant Temperature")
    coolant_offset = next(offset for group, offset, _, _, _ in PROFILES[key] if group == 0x32A)
    field = next(item for item in editable_fields(complete_sample(key)) if item["offset"] == coolant_offset)
    for value in (0, 128, 254):
        for entry in coolant_copies:
            at = entry & 0x7FFFF
            assert image[at] == 0xF3 and image[at + 4] == 0xF7
            source, destination = word(at + 2), word(at + 6)
            emu.write_byte(source, value)
            result = emu.cpu.run(entry, stop_at=entry + 8, max_steps=10)
            assert result.exit_reason == "stop_at", result
            assert emu.mem.read_byte(destination) == value
        emu.write_byte(0xF6D0, 3)
        restore(emu, dispatcher)
        assert emu.mem.read_byte(0xF74F) == 0xA0
        assert emu.mem.read_byte(0xF750 + channel.offset - 3) == value
        assert storage_display_value(field, value) == channel.decode(bytes((value,)))


def complete_sample(key):
    source = sample055() if key == "430055" else revision_sample(key)
    raw = bytearray(source["data"])
    raw[:6] = counter_record(1, 2, 3)
    for canonical, offset, size, _, fields in PROFILES[key]:
        payload = bytearray(raw[offset:offset + size])
        if canonical not in (0x28A, 0x2A0, 0x348):
            payload[:] = b"\xA5" * size
        for start, width, _, _ in fields:
            value = 0x5555 if canonical == 0x348 else 0
            payload[start:start + width] = value.to_bytes(width, "little")
        put_record(raw, offset, payload)
    return make_snapshot("MS43", "eeprom", raw, software_id=key.split("-")[0])


@pytest.mark.parametrize("key", PROFILES)
def test_complete_revision_fields_units_bounds_and_noop(key):
    source = complete_sample(key)
    image = full_image(key) if key == "430055" else composed_image(key, "430069").image
    fields = editable_fields(source)
    expected = 152 if key == "430037" else 167 if key in ("430055", "430064") else 168
    assert len(fields) == expected - 8
    assert len(editable_fields(source, calibration=image)) == expected
    assert edit_storage(source, {field["offset"]: field["value"] for field in fields})["data"] == source["data"]
    for high in (False, True):
        changes = {}
        for canonical, offset, _, _, items in PROFILES[key]:
            for start, _, maximum, _ in items:
                value = maximum if high else 0
                if canonical == 0x348:
                    value = 0xAAAA if high else 0x5555
                changes[offset + start] = value
        target = edit_storage(source, changes)
        allowed = set()
        for canonical, offset, size, _, items in PROFILES[key]:
            allowed.update((offset + size, offset + size + 1))
            for start, width, maximum, _ in items:
                allowed.update(range(offset + start, offset + start + width))
                value = int.from_bytes(target["data"][offset + start:offset + start + width], "little")
                assert value == changes[offset + start]
        assert all(a == b or i in allowed for i, (a, b) in enumerate(zip(source["data"], target["data"])))


@pytest.mark.parametrize("key", PROFILES)
def test_native_extra_fields_restore_both_raw_boundaries(key, native, monkeypatch):
    import tests
    root = Path(os.environ.get("MS41EMU_ROOT", Path.home() / "ECU Emulator"))
    monkeypatch.setattr(tests, "__path__", [*tests.__path__, str(root / "tests")])
    from tests.test_c167_lifecycle import prepare_lifecycle

    image = bytearray(full_image(key) if key == "430055" else composed_image(key, "430069").image)
    image[0x6FFF0:0x6FFF4] = bytes.fromhex("90482442")
    image[0x7FFF0:0x7FFF4] = bytes.fromhex("42244890")
    image = bytes(image)
    cls = type(native("MS43"))
    monkeypatch.setattr(cls, "IMAGE_SHA256", sha256(image).hexdigest())
    emu, peer, _ = prepare_lifecycle(cls, image)
    peer.storage[:] = b"\xFF" * 2048
    put_record(peer.storage, 0x32, b"43241010" if key == "430055" else BINDINGS[key])
    binding = 0xC393A if key == "430055" else OWNERS[key][0][0]
    result = emu.cpu.run(emu.cpu.pc, stop_at=binding, max_steps=2_000_000)
    assert result.exit_reason == "stop_at", result
    emu.cpu.set_trace(None)
    restore(emu, binding)
    source = complete_sample(key)
    peer.storage[:] = source["data"]
    flags = FLAGS[key.split("-")[0]]
    for canonical, offset, size, entry, fields in PROFILES[key]:
        for high in (False, True):
            payload = bytearray(b"\xA5" * size)
            for start, width, maximum, _ in fields:
                value = maximum if high else 0
                if canonical == 0x348:
                    value = 0xAAAA if high else 0x5555
                payload[start:start + width] = value.to_bytes(width, "little")
            changes = {offset + start: int.from_bytes(payload[start:start + width], "little")
                       for start, width, _, _ in fields}
            changed = edit_storage(make_snapshot("MS43", "eeprom", peer.storage,
                                                 software_id=key.split("-")[0]), changes)
            peer.storage[:] = changed["data"]
            payload = peer.storage[offset:offset + size]
            before = bytes(peer.storage)
            writes = peer.storage_write_attempts
            restore(emu, entry)
            for start, width, _, addresses in fields:
                expected = bytes(payload[start:start + width])
                for address in addresses:
                    assert bytes(emu.mem._physical_read(address + i) for i in range(width)) == expected, (
                        key, hex(offset + start), hex(address), expected)
            selected = {0x28A: flags[0], 0x2A0: flags[1], 0x348: flags[2], 0x31A: flags[3]}.get(canonical)
            if selected:
                address, *bits = selected
                for bit in bits:
                    assert bool(emu.mem.read_word_direct(address) & (1 << bit)) == high
            assert bytes(peer.storage) == before and peer.storage_write_attempts == writes
    counter_read, counter_save, counter_ram = COUNTERS[key.split("-")[0]]
    for triplet in ((0x1234, 0x1235, 0x1236), (0xBEEF, 0x1235, 0x1236),
                    (0x1234, 0xBEEF, 0x1236), (0x1234, 0x1235, 0xBEEF),
                    (0xFFFE, 0xFFFF, 0), (0xFFFF, 0, 1),
                    (0xBEEF, 0, 1), (0, 10, 20), (0xFFFF, 0xFFFF, 0xFFFF)):
        peer.storage[:6] = counter_record(*triplet)
        before = bytes(peer.storage)
        writes = peer.storage_write_attempts
        restore(emu, counter_read)
        expected = storage_operating_counter(peer.storage)
        actual = emu.mem._physical_read(counter_ram) | emu.mem._physical_read(counter_ram + 1) << 8
        assert actual == (expected if expected is not None else 0)
        assert bytes(peer.storage) == before and peer.storage_write_attempts == writes
        if expected is not None:
            restore(emu, counter_save)
            assert bytes(peer.storage[:6]) == counter_record(expected, (expected + 1) & 0xFFFF,
                                                            (expected + 2) & 0xFFFF)
            assert bytes(peer.storage[6:]) == before[6:]
    snapshot = make_snapshot("MS43", "eeprom", source["data"], software_id=key.split("-")[0])
    peer.storage[:] = edit_storage(snapshot, {0: 0xFFFF})["data"]
    restore(emu, counter_read)
    assert emu.mem._physical_read(counter_ram) | emu.mem._physical_read(counter_ram + 1) << 8 == 0xFFFF
    verify_scalar_unit_owners(emu, image, key)
    assert bytes(emu.mem.image) == image
