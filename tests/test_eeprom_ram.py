import hashlib
import json
import os
from pathlib import Path

import pytest

from engines.softbsl import checksum
from engines.softbsl import eeprom_ram


def _private_reference(filename):
    value = os.environ.get("MS41_TEST_DATA_ROOT", "").strip()
    if not value:
        pytest.skip("private EEPROM reference unavailable; set MS41_TEST_DATA_ROOT")
    root = Path(value).expanduser()
    match = next(root.rglob(filename), None) if root.is_dir() else None
    if match is None:
        pytest.skip(f"private EEPROM reference unavailable: {filename}")
    return match.read_bytes()


def _valid_image(variant="MS41.3"):
    image = bytearray(eeprom_ram.EEPROM_SIZE)
    image[0] = 0x31
    image[-1] = 0xA5
    for field in eeprom_ram.fields_for_variant(variant):
        if field.checked:
            payload = image[field.offset:field.offset + field.length - 2]
            image[field.offset + field.length - 2:field.offset + field.length] = (
                eeprom_ram.additive_check(payload).to_bytes(2, "little")
            )
    offset = eeprom_ram.transmission_offset(variant)
    image[offset:offset + 4] = bytes.fromhex("0e 00 0f 00")
    return bytes(image)


def _admission(selector=0x2C, variant="MS41.3", marker=0, bank="B"):
    return eeprom_ram.Preflight(
        "COM1",
        marker,
        selector,
        variant,
        bank,
        eeprom_ram.SOFTBSL_DOOR_PATCHES[variant],
    )


def test_live_mirror_checks_and_padding_are_reported():
    image = _private_reference("eeprom_mirror_live.bin")
    report = eeprom_ram.inspect_image(image)

    assert len(image) == 512
    assert report["sha256"] == (
        "0a6ea504cbb9d129bd37e96c9be72ab9a956a5787ae3623c031f9a1cd52657bd"
    )
    assert all(row.get("check_ok", True) for row in report["fields"])
    assert report["decoded"]["looks_like_zero_padded_ram_mirror"] is True
    assert report["decoded"]["transmission"]["mode"] == "manual"


@pytest.mark.parametrize(
    ("variant", "offsets", "transmission"),
    (
        ("MS41.0", (0x052, 0x056, 0x060, 0x0EA), 0x196),
        ("MS41.1", (0x032, 0x036, 0x040, 0x0E8), 0x1CC),
        ("MS41.2", (0x052, 0x056, 0x060, 0x11A), 0x1CA),
        ("MS41.3", (0x052, 0x056, 0x060, 0x11A), 0x1CA),
    ),
)
def test_variant_field_offsets_and_descriptions(variant, offsets, transmission):
    fields = eeprom_ram.field_report(bytes(eeprom_ram.EEPROM_SIZE), variant)
    by_key = {field["key"]: field for field in fields}
    next_offset = 0
    for field in fields:
        assert field["offset"] == next_offset
        next_offset += field["length"]

    assert next_offset == eeprom_ram.DECODE_LAYOUTS[variant]["mirror_size"]
    assert (
        by_key["load_model_correction"]["offset"],
        by_key["vanos_adaptation"]["offset"],
        by_key["engine_roughness_segment_adaptation"]["offset"],
        by_key["idle_regulator_adaptation"]["offset"],
    ) == offsets
    assert by_key["transmission"]["offset"] == transmission
    assert by_key["load_model_correction"]["label"] == (
        "Learned load-model correction (units unknown)")
    assert by_key["vanos_adaptation"]["label"] == (
        "VANOS learned reference and controller state")
    assert by_key["engine_roughness_segment_adaptation"]["label"] == (
        "Learned crankshaft segment corrections (misfire detection)")
    assert by_key["idle_regulator_adaptation"]["label"] == (
        "Idle-control learned factor and integrator values")
    assert by_key["identity_gate"]["category"] == "system"


def test_tail_identity_names_match_the_traced_fields():
    image = bytearray(eeprom_ram.EEPROM_SIZE)
    image[0x1E3:0x1EF] = b"111009091202"
    image[0x1EF:0x1F6] = b"1406464"
    image[0x1F6:0x1FD] = b"1406464"

    decoded = eeprom_ram.decoded_values(image)

    assert decoded["tail_descriptor"] == "111009091202"
    assert decoded["tail_dme_part_numbers"] == ["1406464", "1406464"]
    assert "tail_program_ids" not in decoded


@pytest.mark.parametrize(
    ("descriptor", "part", "expected"),
    (
        (b"111006064101", b"1429861", ("MS41.0",)),
        (b"111009096000", b"1437806", ("MS41.1",)),
        (b"111009091202", b"1406464", ("MS41.2", "MS41.3")),
    ),
)
def test_layout_detection_uses_exact_tail_identity(descriptor, part, expected):
    image = bytearray(eeprom_ram.EEPROM_SIZE)
    image[0x1E3:0x1EF] = descriptor
    image[0x1EF:0x1F6] = part
    image[0x1F6:0x1FD] = part

    assert eeprom_ram.detect_layouts(image) == expected


def test_layout_detection_fails_closed_on_unknown_or_conflicting_tail():
    unknown = bytearray(eeprom_ram.EEPROM_SIZE)
    unknown[0x1E3:0x1EF] = b"UNKNOWN-LAY!"
    unknown[0x1EF:0x1FD] = b"76543217654321"
    assert eeprom_ram.detect_layouts(unknown) == ()

    conflicting = bytearray(unknown)
    conflicting[0x1E3:0x1EF] = b"111006064101"
    conflicting[0x1EF:0x1FD] = b"14378061437806"
    assert eeprom_ram.detect_layouts(conflicting) == ()

    conflicting[0x1F6:0x1FD] = b"1406464"
    assert eeprom_ram.detect_layouts(conflicting) == ()


def test_physical_capture_rejects_one_stale_ch341a_packet_rotation():
    image = bytearray(_valid_image("MS41.3"))
    image[0x1E3:0x1EF] = b"111009091202"
    image[0x1EF:0x1FD] = b"14064641406464"
    rotated = image[-32:] + image[:-32]

    with pytest.raises(eeprom_ram.EepromError, match="rotated by one 32-byte"):
        eeprom_ram.validate_physical_capture(rotated)


@pytest.mark.parametrize("variant", tuple(eeprom_ram.FIELDS_BY_VARIANT))
def test_transmission_shortcut_changes_only_mode_bits_and_check(variant):
    before = _valid_image(variant)
    target = eeprom_ram.set_transmission_mode(before, "at", variant)
    offset = eeprom_ram.transmission_offset(variant)

    assert eeprom_ram.changed_offsets(before, target) == (offset, offset + 2)
    assert int.from_bytes(target[offset:offset + 2], "little") & 0xFFFC == (
        int.from_bytes(before[offset:offset + 2], "little") & 0xFFFC
    )
    assert eeprom_ram.transmission_record(target, variant)["mode"] == "automatic"
    assert eeprom_ram.transmission_record(target, variant)["check_ok"]


@pytest.mark.parametrize("variant", tuple(eeprom_ram.FIELDS_BY_VARIANT))
def test_check_update_only_touches_records_with_edited_payloads(variant):
    before = bytearray(_valid_image(variant))
    edited = next(
        field for field in eeprom_ram.fields_for_variant(variant)
        if field.key == "load_model_correction")
    untouched = next(
        field for field in eeprom_ram.fields_for_variant(variant)
        if field.key == "transmission")
    untouched_end = untouched.offset + untouched.length
    before[untouched_end - 2] ^= 0x80
    target = bytearray(before)
    target[edited.offset] ^= 1

    updated = eeprom_ram.update_checks_for_changed_records(
        before, target, variant)
    edited_end = edited.offset + edited.length

    assert updated[edited_end - 2:edited_end] == eeprom_ram.additive_check(
        updated[edited.offset:edited_end - 2]).to_bytes(2, "little")
    assert updated[untouched.offset:untouched_end] == before[
        untouched.offset:untouched_end]
    assert eeprom_ram.changed_offsets(target, updated) == (edited_end - 2,)
    eeprom_ram.build_write_plan(before, updated, variant)

    check_only = bytearray(before)
    check_only[edited_end - 2] ^= 0x40
    unchanged = eeprom_ram.update_checks_for_changed_records(
        before, check_only, variant)
    assert unchanged == bytes(check_only)
    with pytest.raises(eeprom_ram.EepromError, match="invalid target check"):
        eeprom_ram.build_write_plan(before, unchanged, variant)


@pytest.mark.parametrize("variant", tuple(eeprom_ram.FIELDS_BY_VARIANT))
def test_write_plan_invalidates_changed_checked_record_and_restores_check_last(variant):
    before = _valid_image(variant)
    target = eeprom_ram.set_transmission_mode(before, "at", variant)
    offset = eeprom_ram.transmission_offset(variant)
    plan = eeprom_ram.build_write_plan(before, target, variant)

    assert [(item.offset, item.reason) for item in plan] == [
        (offset + 2, "invalidate-check"),
        (offset, "payload"),
        (offset + 2, "check-last"),
    ]
    state = bytearray(before)
    for item in plan:
        assert state[item.offset] == item.expected
        state[item.offset] = item.replacement
    assert bytes(state) == target


def test_write_plan_rejects_bad_changed_record_but_allows_raw_tail_byte():
    before = _valid_image()
    invalid = bytearray(before)
    invalid[0x1CA] ^= 1
    with pytest.raises(eeprom_ram.EepromError, match="invalid target check"):
        eeprom_ram.build_write_plan(before, invalid, "MS41.3")

    raw = bytearray(before)
    raw[0x1F0] ^= 0x55
    assert eeprom_ram.build_write_plan(before, raw, "MS41.3") == (
        eeprom_ram.ByteWrite(0x1F0, before[0x1F0], raw[0x1F0], "raw"),
    )


def test_write_image_validation_rejects_uniform_and_padded_ram_mirror():
    with pytest.raises(eeprom_ram.EepromError, match="512 bytes of"):
        eeprom_ram.validate_write_image(bytes(512), "MS41.3")
    padded = bytearray(_valid_image())
    padded[eeprom_ram.DECODE_LAYOUTS["MS41.3"]["mirror_size"]:] = bytes(
        512 - eeprom_ram.DECODE_LAYOUTS["MS41.3"]["mirror_size"]
    )
    with pytest.raises(eeprom_ram.EepromError, match="padded RAM mirror"):
        eeprom_ram.validate_write_image(padded, "MS41.3")


def test_capture_save_is_durable_and_never_overwrites(tmp_path):
    image = _valid_image()
    target = tmp_path / "capture.bin"
    assert eeprom_ram.save_capture(target, image) == target
    assert target.read_bytes() == image
    with pytest.raises(FileExistsError):
        eeprom_ram.save_capture(target, image)


def test_atomic_capture_falls_back_when_hard_links_are_forbidden(
    tmp_path, monkeypatch,
):
    image = _valid_image()
    target = tmp_path / "capture.bin"
    monkeypatch.setattr(
        eeprom_ram.os, "link",
        lambda *_args: (_ for _ in ()).throw(PermissionError("hard links disabled")),
    )

    assert eeprom_ram._save_capture_atomic(target, image) == target
    assert target.read_bytes() == image
    assert not list(tmp_path.glob(".*.tmp"))


class _FakeDS2:
    baud = 9600

    def __init__(self, reads=()):
        self.reads = list(reads)

    def _read_exact(self, length, _timeout):
        value = self.reads.pop(0)
        assert len(value) == length
        return value


class _FakeSoftBSL:
    def __init__(self, reads=(), statuses=()):
        self.ds2 = _FakeDS2(reads)
        self.statuses = list(statuses)
        self.sent = []

    def _tx(self, value):
        self.sent.append(bytes((value,)))

    def _txs(self, value):
        self.sent.append(bytes(value))

    def _rx(self, timeout=2.0):
        return self.statuses.pop(0)


def test_protocol_dump_requires_agent_crc():
    image = bytes(range(256)) * 2
    crc = checksum._crc(b"\x01" + image, 0xFFFF).to_bytes(2, "big")
    fake = _FakeSoftBSL([image + crc], [1])
    assert eeprom_ram.EepromProtocol(fake).dump_once() == image
    assert fake.sent == [b"d"]

    bad = _FakeSoftBSL([image + b"\x00\x00"], [1])
    with pytest.raises(eeprom_ram.EepromError, match="CRC mismatch"):
        eeprom_ram.EepromProtocol(bad).dump_once()


@pytest.mark.parametrize("fill", (0x00, 0xFF, 0xA5))
def test_stable_physical_dump_rejects_uniform_reads(fill):
    image = bytes((fill,)) * eeprom_ram.EEPROM_SIZE
    crc = checksum._crc(b"\x01" + image, 0xFFFF).to_bytes(2, "big")
    fake = _FakeSoftBSL([image + crc, image + crc], [1, 1])
    with pytest.raises(eeprom_ram.EepromError, match=f"0x{fill:02X}"):
        eeprom_ram.EepromProtocol(fake).stable_dump()


def test_protocol_generic_write_frame_is_crc_protected():
    operation = eeprom_ram.ByteWrite(0x1CA, 0x0E, 0x0D)
    fake = _FakeSoftBSL(statuses=[1])
    eeprom_ram.EepromProtocol(fake).write_byte(operation)
    body = bytes.fromhex("77 01 ca 0e 0d")
    assert fake.sent == [body + checksum._crc(body, 0xFFFF).to_bytes(2, "big")]


@pytest.mark.parametrize("status", (2, 3, 4, 5, 6))
def test_protocol_generic_write_surfaces_agent_failure_status(status):
    with pytest.raises(eeprom_ram.EepromError, match="byte write"):
        eeprom_ram.EepromProtocol(_FakeSoftBSL(statuses=[status])).write_byte(
            eeprom_ram.ByteWrite(0, 1, 2)
        )


@pytest.mark.parametrize(
    ("reply", "accepted"),
    (
        (bytes((3, 0x0F, 0)), True),
        (bytes((3, 0x0F, 1)), True),
        (bytes((3, 0x0F, 3)), True),
        (bytes((2, 0x0F, 1)), False),
        (bytes((3, 0x03, 1)), False),
        (bytes((3, 0x0F, 2)), False),
    ),
)
def test_agent_identify_requires_v3_safe_reader(reply, accepted):
    protocol = eeprom_ram.EepromProtocol(_FakeSoftBSL([reply]))
    if accepted:
        assert protocol.identify()["version"] == 3
    else:
        with pytest.raises(eeprom_ram.EepromError):
            protocol.identify()


def test_frozen_eeprom_agent_matches_manifest():
    payload = eeprom_ram.load_eeprom_agent()
    assert len(payload) == 1442
    assert hashlib.sha256(payload).hexdigest() == (
        "e1c17e3a4e3684ab99f8d3ca98506a1829d37315028a00ff86a04c6f4ca3949f"
    )


@pytest.mark.parametrize(
    ("requested", "tiers"),
    (("auto", ("high", "low")), ("high", ("high", "low")),
     ("mid", ("mid", "low")), ("low", ("low",))),
)
def test_agent_baud_tiers_fall_back_to_9600(requested, tiers):
    assert eeprom_ram._agent_tiers(requested) == tiers


def test_open_agent_retries_complete_entry_at_9600(monkeypatch):
    calls = []
    admission = _admission()
    serial_factory = object()
    preflight_factories = []
    interface = type("Interface", (), {"is_open": True, "close": lambda self: None})()

    class Protocol:
        def __init__(self, _softbsl):
            pass

        def identify(self):
            return {"version": 3, "capabilities": 0x0F, "entry_marker": 0}

    def open_session(_port, _log, **kwargs):
        calls.append(kwargs)
        if kwargs["baud_tier"] == "high":
            raise RuntimeError("fast entry unavailable")
        return interface, object()

    def preflight(_port, *, serial_factory=None):
        preflight_factories.append(serial_factory)
        return admission

    monkeypatch.setattr(eeprom_ram, "preflight", preflight)
    monkeypatch.setattr(eeprom_ram, "load_eeprom_agent", lambda: b"agent")
    monkeypatch.setattr(eeprom_ram, "EepromProtocol", Protocol)
    monkeypatch.setattr(eeprom_ram.softbsl_service, "_open_session", open_session)

    _, result_interface, protocol = eeprom_ram._open_agent(
        "COM1", "auto", lambda *_args: None,
        serial_factory=serial_factory,
    )
    assert result_interface is interface
    assert protocol.baud_tier == "low"
    assert preflight_factories == [serial_factory]
    assert all(call["serial_factory"] is serial_factory for call in calls)
    assert [(call["baud_tier"], call["require_d2xx"]) for call in calls] == [
        ("high", True), ("low", False)
    ]
    assert all(call["agent_payload"] == b"agent" for call in calls)


@pytest.mark.parametrize(
    ("variant", "entry_marker", "selector"),
    (
        ("MS41.0", 0, None),
        ("MS41.1", 1, 0x2C),
        ("MS41.2", 3, 0x2C),
        ("MS41.3", 0, 0x2C),
    ),
)
def test_preflight_accepts_installed_softbsl_across_all_families(
    monkeypatch, variant, entry_marker, selector
):
    class Interface:
        def open(self):
            pass

        def close(self):
            pass

        def identify(self):
            pass

        def read_mem(self, address, length):
            if address == 0xE740:
                return bytes((entry_marker,))
            if address == eeprom_ram.ecu_info.BANK_MARKER_ADDR:
                return bytes.fromhex("a5 5a 42 bd")
            if address == 0xF1A0:
                return b"" if selector is None else bytes((selector,))
            raise AssertionError(f"unexpected read 0x{address:X}/{length}")

    monkeypatch.setattr(eeprom_ram.ds2, "DS2Interface", lambda *_a, **_k: Interface())
    monkeypatch.setattr(
        eeprom_ram._sb, "_detect_ecu_variant",
        lambda *_a, **_k: ("MS41.3", variant, False),
    )
    monkeypatch.setattr(eeprom_ram._sb, "_live_patch_applied", lambda *_a: True)
    assert eeprom_ram.preflight("COM1").program_variant == variant


def test_preflight_routes_stock_ecu_to_ch341a(monkeypatch):
    class Interface:
        def open(self):
            pass

        def close(self):
            pass

        def identify(self):
            pass

        def read_mem(self, address, _length):
            if address == 0xE740:
                return b"\x00"
            if address == eeprom_ram.ecu_info.BANK_MARKER_ADDR:
                return b"\xff" * eeprom_ram.ecu_info.BANK_MARKER_LEN
            raise AssertionError(address)

    monkeypatch.setattr(eeprom_ram.ds2, "DS2Interface", lambda *_a, **_k: Interface())
    monkeypatch.setattr(
        eeprom_ram._sb, "_detect_ecu_variant",
        lambda *_a, **_k: ("MS41.2", "MS41.2", True),
    )
    with pytest.raises(eeprom_ram.EepromError, match="must use CH341A"):
        eeprom_ram.preflight("COM1")


def test_read_failure_still_requests_nonmutating_agent_exit(monkeypatch):
    events = []

    class Interface:
        is_open = True

        def close(self):
            events.append("closed")

    class Protocol:
        def stable_dump(self):
            raise eeprom_ram.EepromError("injected read failure")

        def quit_to_normal(self):
            events.append("quit")
            return True

    monkeypatch.setattr(
        eeprom_ram, "_open_agent",
        lambda *_a, **_k: (_admission(), Interface(), Protocol()),
    )
    with pytest.raises(eeprom_ram.EepromError, match="injected"):
        eeprom_ram.read_eeprom("COM1")
    assert events == ["quit", "closed"]


def test_generic_write_archives_before_and_after_and_verifies_exact_image(
    tmp_path, monkeypatch
):
    before = _valid_image()
    target = eeprom_ram.set_transmission_mode(before, "at")
    storage = bytearray(before)
    writes = []

    class Interface:
        is_open = True

        def close(self):
            self.is_open = False

    class Protocol:
        identity = {"version": 3, "capabilities": 0x0F, "entry_marker": 0}
        baud_tier = "high"

        def stable_dump(self):
            return bytes(storage)

        def write_byte(self, operation):
            assert storage[operation.offset] == operation.expected
            storage[operation.offset] = operation.replacement
            writes.append(operation)

        def quit_to_normal(self):
            return True

    monkeypatch.setattr(
        eeprom_ram, "_open_agent",
        lambda *_a, **_k: (_admission(), Interface(), Protocol()),
    )
    backup = tmp_path / "before.bin"
    result = eeprom_ram.write_image(
        "COM1", target, variant="MS41.3", backup_path=backup,
        confirm=lambda _message: True, log=lambda *_args: None,
    )

    assert result.image == target
    assert result.write_performed is True
    assert backup.read_bytes() == before
    assert (tmp_path / "before.after.bin").read_bytes() == target
    assert writes == list(eeprom_ram.build_write_plan(before, target, "MS41.3"))


def test_identical_target_archives_and_succeeds_without_writing(
    tmp_path, monkeypatch
):
    before = _valid_image()
    events = []

    class Interface:
        is_open = True

        def close(self):
            self.is_open = False
            events.append("closed")

    class Protocol:
        identity = {"version": 3, "capabilities": 0x0F, "entry_marker": 0}
        baud_tier = "high"

        def stable_dump(self):
            events.append("stable_dump")
            return before

        def write_byte(self, _operation):
            pytest.fail("an identical target must not send a byte write")

        def quit_to_normal(self):
            events.append("quit")
            return True

    monkeypatch.setattr(
        eeprom_ram, "_open_agent",
        lambda *_a, **_k: (_admission(), Interface(), Protocol()),
    )
    backup = tmp_path / "before.bin"

    result = eeprom_ram.write_image(
        "COM1",
        before,
        variant="MS41.3",
        backup_path=backup,
        confirm=lambda _message: pytest.fail(
            "an identical target must not require confirmation"),
        log=lambda *_args: None,
    )

    assert result.image == before
    assert result.write_performed is False
    assert backup.read_bytes() == before
    assert not (tmp_path / "before.after.bin").exists()
    assert events == ["stable_dump", "quit", "closed"]
    journal = [
        json.loads(line)
        for line in backup.with_suffix(".journal.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert journal[-1]["fields"] == {
        "before_path": str(backup),
        "before_sha256": hashlib.sha256(before).hexdigest(),
        "changed_offsets": [],
        "outcome": "success",
        "resolution": "target_already_matches_live_image",
        "target_sha256": hashlib.sha256(before).hexdigest(),
        "writes_sent": 0,
    }


def test_transmission_write_refuses_stale_preflight_before_any_byte(
    tmp_path, monkeypatch
):
    reviewed = _valid_image()
    live = bytearray(reviewed)
    live[0x20] ^= 0x01
    events = []

    class Interface:
        is_open = True

        def close(self):
            self.is_open = False
            events.append("closed")

    class Protocol:
        identity = {"version": 3, "capabilities": 0x0F, "entry_marker": 0}
        baud_tier = "high"

        def stable_dump(self):
            return bytes(live)

        def write_byte(self, _operation):
            pytest.fail("stale preflight must be rejected before a byte write")

        def quit_to_normal(self):
            events.append("quit")
            return True

    monkeypatch.setattr(
        eeprom_ram, "_open_agent",
        lambda *_a, **_k: (_admission(), Interface(), Protocol()),
    )
    with pytest.raises(eeprom_ram.EepromError, match="changed since"):
        eeprom_ram.write_transmission(
            "COM1",
            "at",
            backup_path=tmp_path / "before.bin",
            confirm=lambda _message: pytest.fail("stale write cannot be confirmed"),
            expected_before=reviewed,
        )
    assert events == ["quit", "closed"]


def test_image_write_refuses_stale_preflight_before_any_byte(tmp_path, monkeypatch):
    reviewed = _valid_image()
    live = bytearray(reviewed)
    live[0x20] ^= 0x01
    events = []

    class Interface:
        is_open = True

        def close(self):
            self.is_open = False
            events.append("closed")

    class Protocol:
        identity = {"version": 3, "capabilities": 0x0F, "entry_marker": 0}
        baud_tier = "high"

        def stable_dump(self):
            return bytes(live)

        def write_byte(self, _operation):
            pytest.fail("stale image must be rejected before a byte write")

        def quit_to_normal(self):
            events.append("quit")
            return True

    monkeypatch.setattr(
        eeprom_ram, "_open_agent",
        lambda *_a, **_k: (_admission(), Interface(), Protocol()),
    )
    with pytest.raises(eeprom_ram.EepromError, match="changed since"):
        eeprom_ram.write_image(
            "COM1",
            reviewed,
            variant="MS41.3",
            backup_path=tmp_path / "before.bin",
            confirm=lambda _message: pytest.fail("stale write cannot be confirmed"),
            expected_before=reviewed,
        )
    assert events == ["quit", "closed"]


def test_generic_write_cancelled_before_first_byte(monkeypatch, tmp_path):
    before = _valid_image()
    target = eeprom_ram.set_transmission_mode(before, "at")
    events = []

    class Interface:
        is_open = True

        def close(self):
            self.is_open = False
            events.append("closed")

    class Protocol:
        identity = {"version": 3, "capabilities": 0x0F, "entry_marker": 0}
        baud_tier = "low"

        def stable_dump(self):
            return before

        def write_byte(self, _operation):
            pytest.fail("no byte may be written")

        def quit_to_normal(self):
            events.append("quit")
            return True

    monkeypatch.setattr(
        eeprom_ram, "_open_agent",
        lambda *_a, **_k: (_admission(), Interface(), Protocol()),
    )
    with pytest.raises(eeprom_ram.EepromCancelled):
        eeprom_ram.write_image(
            "COM1", target, variant="MS41.3",
            backup_path=tmp_path / "before.bin",
            confirm=lambda _message: False,
        )
    assert events == ["quit", "closed"]


def test_retained_session_resumes_only_remaining_prepared_bytes(tmp_path):
    before = _valid_image()
    target = eeprom_ram.set_transmission_mode(before, "at")
    full_plan = eeprom_ram.build_write_plan(before, target, "MS41.3")
    storage = bytearray(before)
    first = full_plan[0]
    storage[first.offset] = first.replacement
    resumed = []

    class Interface:
        is_open = True

        def close(self):
            self.is_open = False

    class Protocol:
        def stable_dump(self):
            return bytes(storage)

        def write_byte(self, operation):
            assert storage[operation.offset] == operation.expected
            storage[operation.offset] = operation.replacement
            resumed.append(operation)

        def quit_to_normal(self):
            return True

    journal = eeprom_ram.OperationJournal(
        tmp_path / "resume.journal.jsonl", operation="eeprom_image"
    )
    recovery = eeprom_ram.EepromWriteRecovery(
        Interface(), Protocol(), before, target, "MS41.3", full_plan,
        tmp_path / "resume.after.bin", journal, _admission(),
    )
    result = eeprom_ram.repair_write_recovery(
        recovery, confirm=lambda _message: True
    )
    assert result == target
    assert resumed == list(eeprom_ram.build_write_plan(
        bytes(bytearray(before[:first.offset]) + bytes((first.replacement,)) +
              before[first.offset + 1:]),
        target,
        "MS41.3",
    ))
