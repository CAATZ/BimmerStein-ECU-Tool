"""Verified Info fields, passive roughness and read-only snapshot parity."""

from pathlib import Path

import pytest

from checksum import _crc, _TABLE
from live_log import parse_log
from ms42_ms43 import MS42MS43ECU
import ms42_ms43_diagnostics as diagnostics
import ms42_ms43_live as live
from tests.test_ms42_ms43 import Link, reply
from tests.test_ms42_ms43_diagnostics import identity_ecu
from tests.test_ms42_ms43_live import capture
from tests import test_ms42_ms43_desktop as desktop


window = desktop.window


SOFTWARE = {"MS42": "0110C6", "MS43": "430069"}


@pytest.mark.parametrize("family", SOFTWARE)
def test_explicit_info_and_saved_snapshot_preserve_verified_details(family):
    ecu, link = identity_ecu(family)
    info = diagnostics.read_identity(ecu)
    assert info["transmission_mode"] == "Automatic"
    assert info["program_calibration_match"] == "References agree"
    assert info["flash_driver"] == "AMD-compatible word-program driver"
    loaded = diagnostics.decode_snapshot(diagnostics.encode_snapshot(info))
    for key in ("transmission_mode", "program_calibration_match", "flash_driver"):
        assert loaded[key] == info[key]
        assert info[key] in diagnostics.format_identity(loaded)
    _, program, calibration, driver, signature = diagnostics._INFO_LAYOUTS[family]
    reads = [(int.from_bytes(args[:4], "big"), args[4]) for _, command, args in link.calls if command == 6]
    assert (program, 4) in reads and (calibration, 4) in reads
    assert (driver, len(signature)) in reads
    assert len(signature) == 16
    assert all(command in (0, 6, 0x0B, 0x0D) for _, command, _ in link.calls)


@pytest.mark.parametrize("family", SOFTWARE)
def test_regular_ecu_info_populates_existing_rows(window, family):
    desktop.connect(window, family)
    ecu, _ = identity_ecu(family)
    info = diagnostics.read_identity(ecu)
    window._ms42_ms43_show_info(info)
    assert window._info_labels["Transmission Mode"].text() == "Automatic"
    assert window._info_labels["Program / Calibration Match"].text() == "References agree"
    assert window._info_labels["Flash Command-Set Driver"].text() == "AMD-compatible word-program driver"


@pytest.mark.parametrize("family", SOFTWARE)
@pytest.mark.parametrize("copies", [(0, 1), (0, 2), (1, 2)])
def test_native_redundancy_pair_order_and_last_byte_semantics(family, copies):
    raw = [bytearray(b"1" * 12), bytearray(b"2" * 12), bytearray(b"3" * 12)]
    for index in copies:
        raw[index] = bytearray(b"A" * 12)
    assert diagnostics._identity_copy(b"".join(raw), 12, family) == b"A" * 12
    raw[copies[1]][-1] = ord("B")
    assert diagnostics._identity_copy(b"".join(raw), 12, family) == (b"A" * 12 if family == "MS42" else None)


@pytest.mark.parametrize("family", SOFTWARE)
@pytest.mark.parametrize("failure", ["transmission", "reference", "driver", "unknown_software"])
def test_info_failure_is_local_and_does_not_gate_other_fields(family, failure):
    ecu, link = identity_ecu(family)
    original = link.responder
    _, pointer, _, driver, _ = diagnostics._INFO_LAYOUTS[family]

    def respond(command, args):
        address = int.from_bytes(args[:4], "big")
        if failure == "transmission" and command == 0x0B:
            return reply(status=0xA2)
        if failure == "reference" and command == 6 and address == pointer:
            return reply(b"\xFF" * 4)
        if failure == "driver" and command == 6 and address == driver:
            return reply(bytes(16))
        if failure == "unknown_software" and command == 6 and address == ecu.profile.sw_offset + (0x80000 if family == "MS43" else 0):
            return reply(b"999999")
        return original(command, args)

    link.responder = respond
    info = diagnostics.read_identity(ecu)
    assert info["vin"] == "WBAAM31010FG12345"
    assert info["transmission_mode"] == ("Unavailable" if failure == "transmission" else "Automatic")
    assert info["program_calibration_match"] == ("Unavailable" if failure in ("reference", "unknown_software") else "References agree")
    expected = {"driver": "Unknown (driver signature differs)", "unknown_software": "Unavailable"}
    assert info["flash_driver"] == expected.get(failure, "AMD-compatible word-program driver")
    if failure == "unknown_software":
        assert not any(command == 6 and int.from_bytes(args[:4], "big") in (pointer, driver)
                       for _, command, args in link.calls)


def passive_ecu(family, *, mask=0x3F, rpm=800, invalid=None):
    base, rpm_address, input_address, faults = live._ROUGHNESS_LAYOUTS[family]
    words = (-1, -32768, 100, 32767, -200, 0)
    memory = {base: mask.to_bytes(2, "little") + b"".join(value.to_bytes(2, "little", signed=True) for value in words),
              rpm_address: rpm.to_bytes(2, "little"), input_address: b"\x01\x00"}
    memory.update((address, bytes(2)) for address, _ in faults)
    if invalid == "input":
        memory[input_address] = b"\xFF\xFF"
    elif invalid in (0, 1):
        address, bits = faults[invalid]
        memory[address] = bits.to_bytes(2, "little")

    def respond(command, args):
        if command == 0x0B:
            assert args == b"\x03"
            return reply(b"\x03\x20")
        assert command == 6  # No system-check / adaptation mutation is allowed.
        address, count = int.from_bytes(args[:4], "big"), args[4]
        raw = memory[address]
        assert count == len(raw)
        return reply(raw)

    link = Link(respond)
    return MS42MS43ECU(link, family), link


@pytest.mark.parametrize("family", SOFTWARE)
@pytest.mark.parametrize("mask", [0, 1, 0x3F])
def test_passive_snapshot_qualifies_native_slots_and_preserves_signed_minus_one(family, mask):
    ecu, link = passive_ecu(family, mask=mask)
    reading = live.read_passive_roughness(ecu, SOFTWARE[family])
    expected = (-1, -200, 100, 0, -32768, 32767)
    for index, value in enumerate(expected):
        slot = live._FIRING_ORDER.index(index + 1)
        if mask & (1 << slot):
            assert reading.cylinders[index] == pytest.approx(value * .00122)
        else:
            assert reading.cylinders[index] is None
    assert reading.state == {0: "unavailable", 1: "partial", 0x3F: "available"}[mask]
    assert all(command == 6 for _, command, _ in link.calls)
    assert sum(args[4] for _, _, args in link.calls) == 22


@pytest.mark.parametrize("family", SOFTWARE)
@pytest.mark.parametrize("invalid", ["input", 0, 1, "stopped"])
def test_passive_snapshot_never_publishes_stale_inhibited_values(family, invalid):
    ecu, _ = passive_ecu(family, invalid=invalid, rpm=0 if invalid == "stopped" else 800)
    reading = live.read_passive_roughness(ecu, SOFTWARE[family])
    assert reading.cylinders == (None,) * 6
    assert reading.state == ("engine_stopped" if invalid == "stopped" else "unavailable")


def test_unknown_software_passive_ram_is_not_guessed():
    ecu, link = passive_ecu("MS43")
    with pytest.raises(ValueError, match="not mapped"):
        live.read_passive_roughness(ecu, "430056")
    assert not link.calls
    assert not any(p.id in live._ROUGHNESS_IDS for p in live.catalog_for("MS43", ecu_id="430056"))


@pytest.mark.parametrize("family", SOFTWARE)
@pytest.mark.parametrize("mode", ["standard", "auto", "telegram"])
def test_passive_channels_use_existing_real_worker_csv_without_system_check(
        monkeypatch, tmp_path, family, mode):
    ecu, link = passive_ecu(family, mask=1)
    poller = live.MS42MS43LiveDataPoller(ecu, ecu_id=SOFTWARE[family],
                                      selected_ids=list(live._ROUGHNESS_IDS),
                                      use_telegram=mode != "standard", telegram_fallback=mode != "telegram")
    path = tmp_path / "passive.csv"
    values = capture(monkeypatch, poller, str(path))
    assert values["Cylinder 1 Roughness Quotient"] == ("-0.001", "")
    assert values["Cylinder 5 Roughness Quotient"] == ("—", "")
    assert all(command == 6 for _, command, _ in link.calls)
    reopened = parse_log(path.read_bytes(), family=family)
    assert reopened["series"][0]["unit"] == ""  # BMW quotient, not MS41 raw units.
    assert reopened["series"][0]["values"] == [-.001]
    assert reopened["series"][4]["values"] == [None]
    assert parse_log(path.read_bytes())["series"][0]["unit"] == ""


@pytest.mark.parametrize("family", SOFTWARE)
def test_no_passive_probes_unless_selected_and_failure_keeps_other_channels(monkeypatch, family):
    ecu, link = passive_ecu(family)
    poller = live.MS42MS43LiveDataPoller(ecu, ecu_id=SOFTWARE[family], selected_ids=["N"])
    assert capture(monkeypatch, poller)["Engine Speed"] == ("800", "rpm")
    assert link.calls == [(0x12, 0x0B, b"\x03")]
    original = link.responder
    link.responder = lambda command, args: reply(status=0xA2) if command == 6 else original(command, args)
    poller = live.MS42MS43LiveDataPoller(ecu, ecu_id=SOFTWARE[family], selected_ids=["N", "BS_ROUGHNESS_CYL1"])
    values = capture(monkeypatch, poller)
    assert values["Engine Speed"] == ("800", "rpm")
    assert values["Cylinder 1 Roughness Quotient"] == ("—", "")
    assert poller.pop_errors()


@pytest.mark.parametrize("family", SOFTWARE)
@pytest.mark.parametrize("kind", ["valid", "bad_crc", "interrupted", "bad_length", "erased"])
def test_retained_snapshot_summary_distinguishes_raw_restore_from_record_validity(family, kind):
    raw = bytearray(b"\xFF" * diagnostics.RETAINED_SIZE)
    if kind != "erased":
        raw[:4] = (7).to_bytes(4, "little")
        raw[0x3F8:0x3FC] = (0xFFFFFFF8 if kind != "interrupted" else 0xFFFFFFFF).to_bytes(4, "little")
        raw[0x3F6:0x3F8] = (0x20 if kind != "bad_length" else 0x3FE).to_bytes(2, "little")
        crc = _crc(raw[:0x20], 0xF) ^ (1 if kind == "bad_crc" else 0)
        raw[0x3FE:0x400] = crc.to_bytes(2, "little")
    records = diagnostics.decode_retained_records(raw, family, SOFTWARE[family])
    assert len(records) == 16
    assert records[0]["erased"] == (kind == "erased")
    assert records[0]["crc_valid"] is ({"valid": True, "interrupted": True, "bad_crc": False}.get(kind))
    assert records[0]["sequence_valid"] == (kind not in ("erased", "interrupted"))
    assert diagnostics.decode_retained_records(raw, family, "unknown") is None
    text = diagnostics.format_snapshot(dict(family=family, software_id=SOFTWARE[family],
                                            retained_raw=raw, retained_provenance=diagnostics.RETAINED_PROVENANCE))
    assert "restoration is a separate explicit storage operation" in text
    assert "does not identify the active record" in text
    if family == "MS43":
        assert "separate serial EEPROM" in text
        assert "payload and activation are unverified" in text


@pytest.mark.parametrize("family", SOFTWARE)
def test_reference_bin_binds_info_signatures_and_reference_decoding(family):
    root = Path.home() / "ECU-reference-data" / f"REF_{family}"
    name = ("Siemens_MS42_0110C6_E46_M52TUB28_EU3_LHD.bin" if family == "MS42" else
            "Siemens_MS43_MS430069_E46_M54B30_US.bin")
    path = root / name
    if not path.exists():
        pytest.skip("Private exact reference is unavailable")
    raw = path.read_bytes()
    table = 0x52CF2 if family == "MS42" else 0x6E87A
    assert raw[table:table + 512] == b"".join(value.to_bytes(2, "little") for value in _TABLE)
    ecu, _ = identity_ecu(family)
    ecu.read_memory = lambda address, count: raw[(address - 0x80000 if address >= 0x80000 else address):
                                                (address - 0x80000 if address >= 0x80000 else address) + count]
    info = dict(software_id=SOFTWARE[family], warnings=[])
    diagnostics._read_info_details(ecu, info)
    assert not info["warnings"]
    assert info["program_calibration_match"] == "References agree"
    assert info["flash_driver"] == "AMD-compatible word-program driver"
