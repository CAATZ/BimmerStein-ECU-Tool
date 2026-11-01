"""Original revision EEPROM owners with known boot plus pinned DATEN application bytes."""

from hashlib import sha256
import json
import os
from pathlib import Path

import pytest

from ms42_ms43_storage import make_snapshot
from ms42_ms43_storage_edit import edit_storage, editable_fields, storage_knock_axes
from ms42_ms43_storage_units import MS43_STORAGE_RECORD_MAPS, storage_field_presentation
from tests.ms42_ms43_revision_sources import composed_image
from tests.test_ms42_ms43_config_native import native as native
from tests.test_ms42_ms43_storage_inspection import put_record
from tests.test_ms43_430055_storage_units import words
from tests.test_ms43_storage_edit_native import restore


# Binding, idle, cam references, knock, throttle, fuel, postcat, coil, heat;
# original initialized application poller, BMW measurement dispatcher, knock RAM.
OWNERS = {
    "430037": ((0xBF86C, 0xB4C4E, 0xB7A66, 0xB9D34, 0x9A8BE,
                0xC1ECC, 0xC20B4, None, None), 0x9FD12, 0xA6286, 0x401E4),
    "430064": ((0xBF774, 0xB55DA, 0xB7614, 0xB99A6, 0x9AC12,
                0xD80B6, 0xD829E, 0xB1E8E, 0xAF980), 0xA3DA6, 0xA5504, 0x4021B),
    "430065": ((0xBFC10, 0xB396C, 0xB69B2, 0xB8D44, 0x9B28E,
                0xD9536, 0xD971E, 0xAF7FA, 0xB4BDC), 0x9FA4E, 0xA5C1E, 0x40221),
    "430066": ((0xBDC4C, 0xB1928, 0xB496E, 0xB6D00, 0x9B2F2,
                0xDB30C, 0xDB4F4, 0xADA28, 0xB2B98), 0x9FAAE, 0xA5C1E, 0x40221),
    "430066-later": ((0xBDC4C, 0xB1928, 0xB496E, 0xB6D00, 0x9B2F2,
                      0xDB324, 0xDB50C, 0xADA28, 0xB2B98), 0x9FAAE, 0xA5C1E, 0x40221),
}
BINDINGS = {"430037": b"43153010", "430064": b"43270010", "430065": b"43271T10",
            "430066": b"43281010", "430066-later": b"43283010"}
CALIBRATION = {
    "430037": (0xB3B, 0xB3A, 0x890, 0x88E, 0x898, 0x896, 0xD5C, 0xD5A),
    "430064": (0xBD2, 0xBD1, 0x908, 0x906, 0x910, 0x90E, 0xE14, 0xE12),
    "430065": (0xC26, 0xC25, 0x94C, 0x94A, 0x954, 0x952, 0xE80, 0xE7E),
    "430066": (0xC28, 0xC27, 0x94E, 0x94C, 0x956, 0x954, 0xE84, 0xE82),
}
# Actual RAM destinations: idle learning byte, common, bank1 add/factor,
# bank2 add/factor, bank1/2 postcat corrections, optional coil type.
RESTORED = {
    "430037": (0x401B4, 0x40BE2, 0x40C46, 0x40C48, 0x40DA4, 0x40DA6, 0x40C36, 0x40D94, None),
    "430064": (0x401E8, 0x40C40, 0x40CA8, 0x40CAA, 0x40E14, 0x40E16, 0x40C98, 0x40E04, 0x401D2),
    "430065": (0x401EE, 0x40C58, 0x40CC0, 0x40CC2, 0x40E2C, 0x40E2E, 0x40CB0, 0x40E1C, 0x401D8),
    "430066": (0x401EE, 0x40C66, 0x40CCE, 0x40CD0, 0x40E3C, 0x40E3E, 0x40CBE, 0x40E2C, 0x401D8),
}


def sample(key):
    software = key.split("-")[0]
    raw = bytearray(b"\xFF" * 0x800)
    put_record(raw, 0x32, BINDINGS[key])
    payloads = {
        0x280: words(1234, -4321, 2500) + b"\x80\xA5",
        0x28A: words(0xA000, 0x8000) + bytes(range(14)) + b"\x00\x01",
        0x2A0: words(0, 0) + bytes((116,)) * 64,
        0x2E6: words(0x8000, 0x8001, 0x7FFF, 0x7FFE, 0x8002),
        0x2F2: words(0, 0, 123, 456, 789, 900),
        0x348: words(123, 456, 234, 567, 0x5555),
        0x3E2: b"\x01\xA5", 0x3E6: b"\x00\x5A",
    }
    for actual, (canonical, size) in MS43_STORAGE_RECORD_MAPS[software].items():
        if canonical not in payloads:
            continue
        assert len(payloads[canonical]) == size
        put_record(raw, actual, payloads[canonical])
    return make_snapshot("MS43", "eeprom", raw, software_id=software)


@pytest.mark.parametrize("key", OWNERS)
def test_revision_fields_units_and_noop_preserve_every_byte(key):
    source = sample(key)
    image = composed_image(key, "430069").image
    fields = editable_fields(source)
    assert len(fields) == (96 if key == "430037" else 98)
    assert len(editable_fields(source, calibration=image)) == len(fields) + 8
    assert edit_storage(source, {field["offset"]: field["value"] for field in fields})["data"] == source["data"]
    for actual, (canonical, size) in MS43_STORAGE_RECORD_MAPS[source["software_id"]].items():
        for field in fields:
            if actual <= field["offset"] < actual + size:
                expected = storage_field_presentation("MS43", canonical + field["offset"] - actual,
                                                      software_id="430069")
                assert field.get("unit") == expected.get("unit")
    axes = storage_knock_axes(source, image)
    load = 0x7408E if key == "430037" else 0x74095
    assert axes["load"] == [value * 1389 / 255 for value in image[load:load + 4]]
    changed = bytearray(source["data"])
    put_record(changed, 0x32, b"43290010")
    bad = make_snapshot("MS43", "eeprom", changed, software_id=source["software_id"])
    with pytest.raises(ValueError, match="binding"):
        editable_fields(bad)


def group(emu, entry, selector):
    emu.mem._physical_write(0xF6D0, selector)
    assert emu.call(entry, max_steps=10000).exit_reason == "stop_at"
    assert emu.mem._physical_read(0xF74F) == 0xA0
    return bytes(emu.mem._physical_read(0xF750 + index)
                 for index in range(emu.mem._physical_read(0xF74E) - 4))


@pytest.mark.parametrize("key", OWNERS)
def test_original_revision_startup_and_edited_storage_restore(key, native, monkeypatch, tmp_path):
    import tests
    root = Path(os.environ.get("MS41EMU_ROOT", Path.home() / "ECU Emulator"))
    monkeypatch.setattr(tests, "__path__", [*tests.__path__, str(root / "tests")])
    from tests.test_c167_lifecycle import prepare_lifecycle

    image = bytearray(composed_image(key, "430069").image)
    # DATEN omits these completed-transfer markers. The original finalizer writes
    # them; supplying that state permits stock boot without changing instructions.
    image[0x6FFF0:0x6FFF4] = bytes.fromhex("90 48 24 42")
    image[0x7FFF0:0x7FFF4] = bytes.fromhex("42 24 48 90")
    image = bytes(image)
    assert image[0x6FFB2:0x6FFBA] == BINDINGS[key]
    cls = type(native("MS43"))
    monkeypatch.setattr(cls, "IMAGE_SHA256", sha256(image).hexdigest())
    emu, peer, _ = prepare_lifecycle(cls, image)
    source = sample(key)
    peer.storage[:] = source["data"]
    owners, _, dispatcher, knock_ram = OWNERS[key]
    # Preserve the actual startup caller's synchronous storage context. A later
    # diagnostic-poller stop can leave the stock asynchronous driver occupied.
    result = emu.cpu.run(emu.cpu.pc, stop_at=owners[0], max_steps=2_000_000)
    assert result.exit_reason == "stop_at", result
    emu.cpu.set_trace(None)
    records = {canonical: actual for actual, (canonical, _) in
               MS43_STORAGE_RECORD_MAPS[source["software_id"]].items()}
    idle, cam, knock, throttle = (records[offset] for offset in (0x280, 0x28A, 0x2A0, 0x348))
    changes = {idle: -32768, idle + 2: 32767, idle + 4: -1,
               cam: 0xA100, cam + 2: 0x8100, knock + 4: 0, knock + 67: 128, throttle: 512}
    if 0x3E2 in records:
        changes.update({records[0x3E2]: 2, records[0x3E6]: 1})
    target = edit_storage(source, changes)
    peer.storage[:] = target["data"]
    writes = peer.storage_write_attempts
    commands = []
    transmit = emu.hardware.ssc.write_tx
    def trace_transmit(value):
        if emu.hardware.ssc.config & 15 == 15:
            commands.append((f"{emu.cpu.pc:06X}", f"{value:04X}"))
        return transmit(value)
    monkeypatch.setattr(emu.hardware.ssc, "write_tx", trace_transmit)
    traces = {}
    read_byte, write_byte, read_word = emu.mem._physical_read, emu.mem._physical_write, emu.mem.read_word
    for entry in owners:
        if entry is None:
            continue
        reads, stored = [], []
        commands.clear()
        def traced_read(address):
            value = read_byte(address)
            if 0x70000 <= (address & 0x7FFFF) < 0x80000:
                reads.append((f"{emu.cpu.pc:06X}", f"{address & 0x7FFFF:06X}", value))
            return value
        def traced_word(address):
            physical = emu.mem.translate(address) & 0x7FFFF
            value = read_word(address)
            if 0x70000 <= physical < 0x80000:
                reads.append((f"{emu.cpu.pc:06X}", f"{physical:06X}", value))
            return value
        def traced_write(address, value):
            if 0x40000 <= address < 0x42000:
                stored.append((f"{emu.cpu.pc:06X}", f"{address:06X}", value))
            return write_byte(address, value)
        with monkeypatch.context() as tracing:
            tracing.setattr(emu.mem, "_physical_read", traced_read)
            tracing.setattr(emu.mem, "read_word", traced_word)
            tracing.setattr(emu.mem, "_physical_write", traced_write)
            restore(emu, entry)
        traces[f"{entry:06X}"] = {"calibration_reads": reads, "ram_writes": stored,
            "scratch": bytes(emu.mem._physical_read(0xF9C0+i) for i in range(16)).hex(),
            "flags": bytes(emu.mem._physical_read(0xFD00+i) for i in range(128)).hex(),
            "peer_reads": peer.storage_reads, "commands": list(commands),
            "dpp": list(emu.reg.dpp), "r0": emu.reg.get_word(0),
            "mode": emu.mem._physical_read(0xFB55 if key=="430037" else 0xFB69),
            "psw": emu.mem.read_word_direct(0xFF10)}
    (tmp_path / "owners.json").write_text(json.dumps(traces, indent=2))
    payload = group(emu, dispatcher, 0x92)
    assert tuple(int.from_bytes(payload[i:i + 2], "big", signed=True)
                 for i in (0, 2, 4)) == (-32768, 32767, -1)
    assert [int.from_bytes(payload[i:i + 2], "big") for i in (14, 16, 22, 24)] == [512, 456, 234, 567]
    payload = group(emu, dispatcher, 0x90)
    assert (payload[2], payload[8]) == (0xA1, 0x81)
    for bank in range(6):
        assert bytes(emu.mem._physical_read(knock_ram + bank * 64 + index) for index in range(64)) == target["data"][knock + 4:knock + 68]
    assert bytes(emu.mem.image) == image
    assert bytes(peer.storage) == target["data"]
    assert peer.storage_write_attempts == writes
    software = source["software_id"]
    restored = RESTORED[software]
    if restored[-1] is not None:
        assert emu.mem._physical_read(restored[-1]) == 2
        address, mask = ((0xFD1C, 0x8000) if software == "430064" else
                         (0xFD1E, 1) if software == "430065" else (0xFD1C, 0x1000))
        assert emu.mem.read_word_direct(address) & mask
        for state in (0, 1):
            put_record(peer.storage, records[0x3E6], bytes((state, 0x5A)))
            restore(emu, owners[8])
            assert bool(emu.mem.read_word_direct(address) & mask) == bool(state)
    if key == "430065":
        # This reader requests odd byte address3E3; its lower driver rounds down
        # to a word and includes one extra word. The CRC belongs to physical3E2.
        put_record(peer.storage, 0x3E2, words(0x1234, 0x5678))
        commands.clear()
        restore(emu, 0x9569C)
        assert [value for _, value in commands if int(value, 16) & 0x1C00 == 0x1800] == ["19F1", "19F2", "19F3", "19F4"]
        assert bytes(emu.mem._physical_read(0xF9C0 + i) for i in range(6)) == bytes(peer.storage[0x3E2:0x3E8])
    bounds = CALIBRATION[software]
    actual_reads = {int(address, 16) - 0x70000 for entry in (owners[1], owners[5], owners[6])
                    for _, address, _ in traces[f"{entry:06X}"]["calibration_reads"]}
    assert set(bounds) <= actual_reads
    tune = bytearray(image)
    tune[0x70000 + bounds[0]], tune[0x70000 + bounds[1]] = 109, 149
    for offset, value in zip(bounds[2:], (0x7400, 0x8C00, 0x7500, 0x8B00, -750, 650)):
        tune[0x70000 + offset:0x70002 + offset] = words(value)
    emu.mem.image[:] = tune
    fuel, postcat = records[0x2E6], records[0x2F2]
    for boundary in ("minimum", "maximum"):
        fields = [field for field in editable_fields(source, calibration=tune) if field["calibration_dependent"]]
        assert len(fields) == 8
        changes = {field["offset"]: field[boundary] for field in fields}
        changes[fuel] = 0x1234
        peer.storage[:] = edit_storage(source, changes, calibration=tune)["data"]
        for entry in (owners[1], owners[5], owners[6]):
            restore(emu, entry)
        assert emu.mem._physical_read(restored[0]) == changes[idle + 6]
        for offset, address in zip((fuel, fuel + 2, fuel + 4, fuel + 6, fuel + 8, postcat, postcat + 2), restored[1:8]):
            value = emu.mem._physical_read(address) | emu.mem._physical_read(address + 1) << 8
            assert value == changes[offset] & 0xFFFF
    assert bytes(emu.mem.image) == bytes(tune)
    assert tune[:0x70000] == image[:0x70000]
    assert peer.storage_write_attempts == writes
