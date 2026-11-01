"""Additional original CA journal consumers, including calibration bounds."""
import pytest

from tests.ms42_ms43_revision_sources import full_image, composed_image
from tests.test_ms42_ca_storage_native import (
    target as target, ca_slot, slot, journal, update_crc, restore,
)
from tests.test_ms42_ms43_storage_inspection import put_record
from ms42_ms43_storage import make_snapshot
from ms42_ms43_storage_edit import edit_storage, editable_fields


def words(*values):
    return b"".join((value & 0xFFFF).to_bytes(2, "little") for value in values)


def record_image(offset, payload, *, valid=True):
    raw = bytearray(journal([(1, ca_slot(slot(4)))]))
    put_record(raw, 0x410 + offset, payload)
    if not valid:
        raw[0x410 + offset + len(payload)] ^= 1
    return update_crc(raw)


@pytest.mark.parametrize("old,new,length", [
    (0x36C4C, 0x36DDA, 100), (0x2633C, 0x2633C, 98),
    (0x348BA, 0x34A48, 128), (0x27FB4, 0x27FB4, 160),
    (0x3F4E8, 0x3F676, 196),
])
def test_ca_consumer_contract_changes_only_its_actual_admission_bits(old, new, length):
    base = full_image("0110C6")[old:old + length]
    actual = composed_image("0110CA", "0110C6").image[new:new + length]
    assert [(index, a, b) for index, (a, b) in enumerate(zip(base, actual)) if a != b] == [
        (3, 0x80, 0xA0), (7, 0xA0, 0xC0)]


@pytest.mark.parametrize("valid", [True, False])
@pytest.mark.parametrize("offset,entry,payload,word_fields,byte_fields", [
    (0x5C, 0x36DDA, b"\x34\x12\xA5\x5A", ((0xFA36, 0x1234, 0x7FFF),), ()),
    (0x21A, 0x2633C, b"\xA3\x6E", (), ((0xFAF8, 0xA3, 1),)),
    (0x258, 0x34A48, b"\x34\x12\x78\x56\x9A\xA5",
     ((0xE1C6, 0x1234, 0), (0xE1C8, 0x5678, 0)), ((0xC1E3, 0x9A, 0),)),
    (0x70, 0x27FB4, bytes.fromhex("8182838485a5"), (),
     tuple((address, value, 0x80) for address, value in
           zip((0xFA9D, 0xFA9F, 0xFAA1, 0xFAA3, 0xFAA5), range(0x81, 0x86)))),
    (0x240, 0x3F676, b"\x91\x92\x93\x94\x95\x96\x34\x12",
     ((0xCE92, 0x1234, 0xBEEF),),
     tuple((address, value, default) for address, value, default in
           zip((0xC17F, 0xC323, 0xC324, 0xC33A, 0xC33B, 0xC33E),
               range(0x91, 0x97), (255, 255, 0, 0, 0, 255)))),
    (0x1FC, 0x326F0, b"\xA3\x5A", (), ((0xE527, 0xA3, 0),)),
])
def test_ca_native_restore_and_bad_inner_crc(target, valid, offset, entry, payload, word_fields, byte_fields):
    target.write(0xCE92, 0xBEEF)
    raw = record_image(offset, payload, valid=valid)
    restore(target, raw, entry)
    for address, accepted, rejected in word_fields:
        assert target.read(address) == (accepted if valid else rejected)
    for address, accepted, rejected in byte_fields:
        assert target.read_byte(address) == (accepted if valid else rejected)


@pytest.mark.parametrize("maximum", [False, True], ids=["minimum", "maximum"])
def test_ca_endpoints_native_limits_and_high_bytes(target, maximum):
    image = bytes(target.mem.image)
    word = lambda at: int.from_bytes(image[at:at + 2], "little")
    throttle = word(0x486B0 if maximum else 0x486B2)
    pedal = word(0x48704 if maximum else 0x48706)
    target.write(0xE0EC, 0x1234)
    target.write(0xE0FE, 0x5678)
    raw = record_image(0x62, words(throttle, throttle, pedal, pedal) + b"\x82\x83\x00\xA5")
    restore(target, raw, 0x388A8)
    assert [target.read(at) for at in (0xE0E2, 0xE0E4, 0xE0C0, 0xE0C2)] == [throttle] * 2 + [pedal] * 2
    assert (target.read(0xE0EC), target.read(0xE0FE)) == (0x8234, 0x8378)


@pytest.mark.parametrize("maximum", [False, True], ids=["minimum", "maximum"])
def test_ca_lambda_words_use_actual_calibration_and_relocated_ram(target, maximum):
    image = bytes(target.mem.image)
    address = 0x480E6 if maximum else 0x480E8
    value = int.from_bytes(image[address:address + 2], "little", signed=True)
    raw = record_image(0x24A, words(123, 456, value, value, 789, 900))
    restore(target, raw, 0x321F6)
    assert [target.read(at) for at in (0xE3E2, 0xE4D4, 0xE3BC, 0xE4AE, 0xE3C6, 0xE4B8)] == [
        123, 456, value & 0xFFFF, value & 0xFFFF, 789, 900]


def test_ca_lambda_unset_sentinels_keep_the_native_distinction(target):
    raw = record_image(0x24A, words(0xFFFF, 0xFFFF, 0, 0, 0xFFFF, 0xFFFF))
    restore(target, raw, 0x321F6)
    assert [target.read(at) for at in (0xE3E2, 0xE4D4, 0xE3C6, 0xE4B8)] == [0, 0, 0xFFFF, 0xFFFF]


@pytest.mark.parametrize("valid", [True, False])
def test_ca_full_vanos_record_native_restore(target, valid):
    image = bytes(target.mem.image)
    payload = words(0xA000, 0x9000) + b"\x65\x54" + bytes(range(0x81, 0x8D)) + b"\x01\x02"
    restore(target, record_image(0x200, payload, valid=valid), 0x2DC24)
    assert [target.read(at) for at in (0xD042, 0xD1B0)] == (
        [0xA000, 0x9000] if valid else [image[0x4830B] << 8, image[0x4830A] << 8])
    assert [target.read_byte(at) for at in (0xD06D, 0xD1DB)] == ([0x65, 0x54] if valid else [0, 0])
    assert bytes(target.read_byte(0xD1A3 + i) for i in range(6)) == (payload[6:12] if valid else b"\x80" * 6)
    assert bytes(target.read_byte(0xD311 + i) for i in range(6)) == (payload[12:18] if valid else b"\x80" * 6)
    assert target.read(0xFD16) & 0x18 == (8 if valid else 0)


def test_ca_actual_startup_vanos_context_and_payload(monkeypatch):
    from hashlib import sha256
    import os
    from pathlib import Path
    import tests
    root = Path(os.environ.get("MS41EMU_ROOT", Path.home() / "ECU Emulator"))
    if not (root / "ms41emu").is_dir():
        pytest.skip("canonical C167 emulator unavailable; set MS41EMU_ROOT")
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.setattr(tests, "__path__", [*tests.__path__, str(root / "tests")])
    from ms41emu import MS42
    from tests.test_c167_lifecycle import prepare_lifecycle

    image = bytearray(composed_image("0110CA", "0110C6").image)
    image[0x50000:0x50004] = bytes.fromhex("90482442")
    image[0x4FFF0:0x4FFF4] = bytes.fromhex("42244890")
    payload = words(0xA000, 0x9000) + b"\x65\x54" + bytes(range(0x81, 0x8D)) + b"\x01\x02"
    raw = record_image(0x200, payload)
    raw[0x416:0x419] = b"\x00\x01\x02"  # Supplied warm retained counter.
    image[0x4000:0x8000] = update_crc(raw)
    image = bytes(image)
    monkeypatch.setattr(MS42, "IMAGE_SHA256", sha256(image).hexdigest())
    target, _, _ = prepare_lifecycle(MS42, image)
    seen = []
    def trace(pc, opcode):
        if pc in (0x2A31A, 0x2DC24, 0x2DCAC):
            seen.append((pc, tuple(target.mem.read_word_direct(at) for at in range(0xFE00, 0xFE08, 2))))
    target.cpu.set_trace(trace)
    result = target.cpu.run(target.cpu.pc, stop_at=0x2DC24, max_steps=1000000)
    assert result.exit_reason == "stop_at", result
    result = target.cpu.run(target.cpu.pc, stop_at=0x2A31E, max_steps=100000)
    assert result.exit_reason == "stop_at", result
    assert {pc for pc, _ in seen} == {0x2A31A, 0x2DC24, 0x2DCAC}
    assert all(dpp == (0x12, 0x14, 0x13, 3) for _, dpp in seen)
    assert [target.read(at) for at in (0xD042, 0xD1B0)] == [0xA000, 0x9000]
    assert [target.read_byte(at) for at in (0xD06D, 0xD1DB)] == [0x65, 0x54]
    assert bytes(target.mem.image) == image


@pytest.mark.parametrize("custom_limits", [False, True], ids=["stock-calibration", "changed-calibration"])
def test_ca_new_editor_fields_reload_and_preserve_padding(target, custom_limits):
    if custom_limits:
        for address, value in ((0x486B2, 200), (0x486B0, 400), (0x48706, 500),
                               (0x48704, 800), (0x480E8, -750), (0x480E6, 650)):
            target.mem.image[address:address + 2] = words(value)
    calibration = bytes(target.mem.image)
    word = lambda at: int.from_bytes(calibration[at:at + 2], "little")
    raw = bytearray(journal([(1, ca_slot(slot(4)))]))
    records = {0x5C: words(0, 0xA5A5),
               0x62: words(word(0x486B2), word(0x486B0), word(0x48706), word(0x48704)) + b"\x80\x80\x00\xA5",
               0x70: bytes(5) + b"\xA5", 0x1FC: b"\x00\xA5",
               0x200: words(0x8000, 0x8000) + bytes(16),
               0x21A: b"\x00\xA5", 0x240: bytes(8),
               0x24A: words(123, 456, 0, 0, 789, 900),
               0x258: words(0, 0) + b"\x00\xA5"}
    for offset, payload in records.items():
        put_record(raw, 0x410 + offset, payload)
    update_crc(raw)
    snapshot = make_snapshot("MS42", "retained", raw, software_id="0110CA")
    minimum = int.from_bytes(calibration[0x480E8:0x480EA], "little", signed=True)
    maximum = int.from_bytes(calibration[0x480E6:0x480E8], "little", signed=True)
    relative = {0x5C: 0xFEDC, 0x62: word(0x486B0), 0x64: word(0x486B2),
                0x66: word(0x48704), 0x68: word(0x48706), 0x6A: 0x82, 0x6B: 0x83,
                0x70: 0x91, 0x74: 0x95, 0x1FC: 255, 0x200: 0xA000, 0x202: 0x9000,
                0x204: 0x65, 0x205: 0x54, 0x206: 0x81, 0x211: 0x8C, 0x212: 1, 0x213: 1,
                0x21A: 0x67, 0x240: 0xA1, 0x245: 0xA6, 0x246: 0x89AB,
                0x24A: 0, 0x24C: 0xFFFE, 0x24E: minimum, 0x250: maximum,
                0x252: 0xFFFF, 0x254: 0, 0x258: 0x2345, 0x25A: 0x6789, 0x25C: 0xBC}
    changes = {0x410 + offset: value for offset, value in relative.items()}
    fields = {field["offset"]: field for field in editable_fields(snapshot, calibration=calibration)}
    changed = edit_storage(snapshot, changes, calibration=calibration)["data"]
    allowed = {0x7FE, 0x7FF}
    for offset in changes:
        allowed.update(range(offset, offset + fields[offset]["width"]))
    for offset, payload in records.items():
        allowed.update((0x410 + offset + len(payload), 0x411 + offset + len(payload)))
    assert all(a == b or index in allowed for index, (a, b) in enumerate(zip(raw, changed)))
    assert snapshot["data"] == bytes(raw)
    target.write(0xE0EC, 0x1234)
    target.write(0xE0FE, 0x5678)
    for entry in (0x36DDA, 0x388A8, 0x27FB4, 0x326F0, 0x2DC24,
                  0x2633C, 0x3F676, 0x321F6, 0x34A48):
        restore(target, changed, entry)
    expected_words = {0xFA36: 0xFEDC, 0xE0E2: relative[0x62], 0xE0E4: relative[0x64],
                      0xE0C0: relative[0x66], 0xE0C2: relative[0x68], 0xE0EC: 0x8234, 0xE0FE: 0x8378,
                      0xD042: 0xA000, 0xD1B0: 0x9000, 0xCE92: 0x89AB,
                      0xE3E2: 0, 0xE4D4: 0xFFFE, 0xE3BC: minimum & 0xFFFF,
                      0xE4AE: maximum & 0xFFFF, 0xE3C6: 0xFFFF, 0xE4B8: 0,
                      0xE1C6: 0x2345, 0xE1C8: 0x6789}
    assert {address: target.read(address) for address in expected_words} == expected_words
    expected_bytes = {0xFA9D: 0x91, 0xFAA5: 0x95, 0xE527: 255, 0xD06D: 0x65, 0xD1DB: 0x54,
                      0xD1A3: 0x81, 0xD316: 0x8C, 0xFAF8: 0x67, 0xC17F: 0xA1,
                      0xC33E: 0xA6, 0xC1E3: 0xBC}
    assert {address: target.read_byte(address) for address in expected_bytes} == expected_bytes
    assert target.read(0xFD16) & 0x18 == 0x18
    assert changed[0x46E:0x470] == b"\xA5\xA5"
    assert changed[0x47C:0x47E] == b"\x00\xA5"
    with pytest.raises(ValueError, match="integer"):
        edit_storage(snapshot, {0x472: word(0x486B2) - 1}, calibration=calibration)
    with pytest.raises(ValueError, match="integer"):
        edit_storage(snapshot, {0x65E: minimum - 1}, calibration=calibration)
