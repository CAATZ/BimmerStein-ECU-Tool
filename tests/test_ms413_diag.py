import json
from io import StringIO
from types import SimpleNamespace

import pytest

import ms413_diag


def test_developer_test_entrypoint_uses_injected_context(monkeypatch):
    records = []

    class Context:
        duration_seconds = 30
        note = "bench"

        def record(self, record):
            records.append(record)

        @staticmethod
        def cancelled():
            return False

        @staticmethod
        def check_cancelled():
            return None

        @staticmethod
        def sleep(_seconds):
            return None

        @staticmethod
        def progress(*_args):
            return None

    context = Context()

    def capture(args):
        assert args.ds2 is context
        assert args.stationary_wideband is True
        assert args.recovery_tracking is True
        assert args.note == "bench"
        args.report.write('{"type":"sample","value":1}\n')
        return 0

    monkeypatch.setattr(ms413_diag, "run_capture", capture)

    assert ms413_diag.run(context) == {"exit_code": 0}
    assert records == [{"type": "sample", "value": 1}]


def test_decode_dtc_payload_uses_count_and_real_record_offsets():
    dtc8 = bytes.fromhex("08 61 03 04 17 0B 30 05 AB 57")
    dtc100 = bytes.fromhex("64 78 01 28 00 00 03 00 00 00")
    decoded = ms413_diag.decode_dtc_payload(b"\x02" + dtc8 + dtc100)

    assert decoded[0]["code"] == 8
    assert decoded[0]["flags"] == "0x61"
    assert decoded[0]["active"]
    assert decoded[0]["raw_record"] == dtc8.hex()
    assert decoded[1]["code"] == 100
    assert decoded[1]["self_test_reason"] == "0x0003"


def _payload(values, layout=ms413_diag.BATCH_LAYOUT):
    raw = bytearray(38)
    offset = 2
    for index, (name, _address, length) in enumerate(layout):
        if index == 20:
            offset = 30
        raw[offset:offset + length] = int(values.get(name, 0)).to_bytes(
            length, "big")
        offset += length
    return bytes(raw)


def test_ms413_capture_layout_and_decoding():
    layout = ms413_diag.BATCH_LAYOUT
    assert len(layout) == 24
    assert sum(length for _name, _address, length in layout[:20]) == 26
    assert sum(length for _name, _address, length in layout[20:]) == 8

    sample = ms413_diag.decode_batch_payload(_payload({
        "cut_state": 0xA1,
        "launch_latch": 0x40,
        "battery": 120,
        "native_limiter": 0x80,
        "fuel_cut_stage": 3,
        "native_soft_rpm": 200,
        "cut_rpm": 125,
        "working_speed": 12,
        "throttle": 128,
        "rpm_mirror": 4000,
        "base_ipw": 500,
        "final_ipw_b1": 600,
        "final_ipw_b2": 700,
        "stft_b1": 0x8000,
        "stft_b2": 0x9000,
        "front_o2_b1": 512,
        "front_o2_b2": 1023,
    }))["values"]

    assert sample["rpm"] == sample["cut_rpm"] == 4000
    assert sample["cut_patch_runtime"]
    assert sample["standalone_ignition_cut"]
    assert sample["launch_armed"]
    assert sample["native_limiter_active"]
    assert sample["fuel_cut_stage"] == 3
    assert sample["battery_v"] == 12.235
    assert sample["final_ipw_b1_ms"] == 3.204
    assert sample["stft_b1_pct"] == 0
    assert sample["stft_b2_pct"] > 0
    assert sample["front_o2_b1_v"] == 2.5024
    assert sample["front_o2_b2_v"] == 5.0

    slow_reads = {
        name: (address, length)
        for name, address, length in ms413_diag.SLOW_READS
    }
    assert slow_reads["wbo_input_select"] == (0x133C0, 2)
    assert slow_reads["diagnostic_flags"] == (0xFD30, 2)
    assert slow_reads["diagnostic_sources"] == (0xFD38, 2)
    assert slow_reads["dtc8_record"] == (0xEA26, 12)
    assert slow_reads["dtc8_branch"] == (0xEA0E, 1)
    assert slow_reads["filtered_load"] == (0xE8E8, 2)
    assert slow_reads["maf_adc"] == (0xFA9E, 2)
    assert slow_reads["maf_fault_adc_snapshot"] == (0xE9F6, 1)
    assert slow_reads["p1l"] == (0xFF04, 2)
    assert slow_reads["legacy_launch_state"] == (0xFD5A, 1)
    assert slow_reads["input_82_latch"] == (0xFD60, 1)
    assert slow_reads["oxygen_sensor_config_byte6"] == (0x10006, 1)
    assert slow_reads["narrowband_emulation_switch"] == (0x1479F, 1)
    assert slow_reads["wbo_voltage_endpoints"] == (0x133C3, 2)
    assert slow_reads["wbo_afr_endpoints"] == (0x147CF, 2)
    assert slow_reads["diagnostic_masks"] == (0x133DB, 5)
    assert slow_reads["coil_monitor_switches"] == (0x1023D, 2)
    assert slow_reads["wbo_target"] == (0xE810, 2)
    assert slow_reads["lambda_compare_b1"] == (0xF043, 3)
    assert slow_reads["lambda_compare_b2"] == (0xF0EF, 3)
    assert slow_reads["lambda_control_flags"] == (0xFD46, 2)

    reasons = ms413_diag._self_test_reasons(0x2440)
    assert [reason["mask"] for reason in reasons] == [
        "0x0040", "0x0400", "0x2000"]


def test_stationary_wideband_layout_replaces_only_fast_pin82():
    layout = ms413_diag.STATIONARY_WIDEBAND_BATCH_LAYOUT
    entries = {name: (address, length) for name, address, length in layout}
    assert len(layout) == 24
    assert sum(length for _name, _address, length in layout[:20]) == 26
    assert sum(length for _name, _address, length in layout[20:]) == 8
    assert entries["working_speed"] == (0xF19A, 1)
    assert entries["wbo_telemetry"] == (0xE800, 1)
    assert "input_82" not in entries

    sample = ms413_diag.decode_batch_payload(
        _payload({"wbo_telemetry": 135}, layout), layout)["values"]
    assert sample["wbo_afr"] == 15.0
    assert "input_82" not in sample

    sample.update({
        "cut_state_raw": "0xA0",
        "native_limiter_active": False,
        "fuel_cut_stage": 0,
        "final_ipw_b1_ms": 2.0,
        "final_ipw_b2_ms": 2.0,
        "stft_b1_pct": 0.0,
        "stft_b2_pct": 0.0,
    })
    line = ms413_diag._status_line({"elapsed_s": 1.0, "values": sample})
    assert "WBO=15.00 AFR" in line


@pytest.mark.parametrize("recovered_revision", ["V11", "V10", "V9", None, "unreadable"])
def test_capture_reconnects_same_ecu_and_records_reset_evidence(
        monkeypatch, tmp_path, recovered_revision):
    identity = bytes(range(ms413_diag.IDENTITY_LENGTH))
    layout = ms413_diag.STATIONARY_WIDEBAND_BATCH_LAYOUT
    expected_entries = tuple(
        (address, length) for _name, address, length in layout)
    events = []

    class FakeDS2:
        def __init__(self, **kwargs):
            self.baud = kwargs["baud"]
            self.open_count = 0

        def open(self):
            self.open_count += 1
            events.append(("open", self.baud))

        def close(self):
            events.append(("close", self.open_count))

        def identify(self):
            events.append(("reconnect_identity", self.open_count))
            return identity

        def setup_telegram_batch(self, *, entries):
            events.append(("setup", self.open_count, tuple(entries)))

        def poll_telegram_batch(self):
            events.append(("poll", self.open_count))
            if self.open_count == 1:
                raise ms413_diag.DS2Error("suspected reset")
            return _payload({
                "cut_state": 0xA0,
                "rpm_mirror": 800,
                "wbo_telemetry": 135,
            }, layout)

    def identity_snapshot(_ds2):
        return identity, {
            "identify_length": len(identity),
            "identify_sha256": "test-sha256",
            "ecu_id": "SHINDE1",
            "firmware_version": "SS1v2",
            "ignition_hook_revision": "V11",
            "ms413_program_signature": {"matches": True},
            "patch_probes": {
                name: {"matches": True}
                for name, _address, _expected in ms413_diag.PATCH_PROBES
            },
        }

    def slow_snapshot(_ds2, phase, revision, report=None):
        assert revision == "V11"
        events.append(("slow", phase))
        return {"type": phase, "captured_utc": "test"}

    def recovery_snapshot(_ds2):
        events.append(("recovery_evidence", 2))
        if recovered_revision == "unreadable":
            raise ms413_diag.DS2Error("hook probes unreadable")
        return {"type": "transport_recovery_evidence",
                "captured_utc": "test",
                "ignition_hook_revision": recovered_revision,
                "patch_probes": {
                    name: {"matches": bool(recovered_revision) and (
                        name.startswith("ignition_cut_" + recovered_revision.lower())
                        or name.startswith("launch_control_"))}
                    for name, _address, _expected in ms413_diag.PATCH_PROBES
                }}

    monkeypatch.setattr(ms413_diag, "DS2Interface", FakeDS2)
    monkeypatch.setattr(ms413_diag, "_identity_snapshot", identity_snapshot)
    monkeypatch.setattr(ms413_diag, "_slow_snapshot", slow_snapshot)
    monkeypatch.setattr(
        ms413_diag, "_recovery_evidence_snapshot", recovery_snapshot)
    monkeypatch.setattr(ms413_diag.time, "sleep", lambda _delay: None)
    output = tmp_path / "capture.jsonl"

    result = ms413_diag.run_capture(SimpleNamespace(
        port="COM1", seconds=0, interval=0.12, output=str(output),
        verbose=False, no_echo=False,
        note="Audi coils; coil DTCs disabled; phase 1 idle",
        reconnect_seconds=20,
        stationary_wideband=True,
    ))

    records = [json.loads(line) for line in output.read_text().splitlines()]
    if recovered_revision != "V11":
        assert result == 1
        assert not any(record["type"] == "sample" for record in records)
        assert [event for event in events if event[0] == "slow"] == [
            ("slow", "preflight")]
        if recovered_revision != "unreadable":
            assert any(record["type"] == "transport_recovery_evidence" for record in records)
        return
    assert result == 0
    assert [record["type"] for record in records].count("transport_gap") == 1
    recovered = next(
        record for record in records
        if record["type"] == "transport_recovered")
    gap = next(
        record for record in records if record["type"] == "transport_gap")
    assert recovered["baud"] == 9600
    assert recovered["identity_matches_original"] is True
    assert recovered["reconnect_exceptions"] == []
    assert gap["poll_attempts"] == 3
    assert len(gap["poll_exceptions"]) == 3
    assert gap["poll_duration_s"] >= 0
    start = next(record for record in records if record["type"] == "start")
    assert start["operator_note"] == (
        "Audi coils; coil DTCs disabled; phase 1 idle")
    assert start["stationary_wideband"] is True
    assert any(
        entry["name"] == "wbo_telemetry"
        for entry in start["batch_layout"])
    identity_record = next(
        record for record in records if record["type"] == "identity")
    assert identity_record["identify_length"] == ms413_diag.IDENTITY_LENGTH
    assert "identify_raw" not in identity_record
    assert records[-1]["transport_gaps"] == 1
    resumed_sample = next(
        record for record in records if record["type"] == "sample")
    assert "missing_sample_interval_s" in resumed_sample
    assert resumed_sample["poll_attempts"] == 1
    assert resumed_sample["values"]["wbo_afr"] == 15.0
    assert [event for event in events if event[0] == "setup"] == [
        ("setup", 1, expected_entries),
        ("setup", 2, expected_entries),
    ]
    assert events.index(("reconnect_identity", 2)) < events.index(
        ("recovery_evidence", 2)) < events.index(
            ("slow", "transport_recovery")) < events.index(
                ("setup", 2, expected_entries))


def test_recovery_evidence_reads_volatile_markers_first():
    calls = []
    values = {
        (0xEA0C, 2): bytes.fromhex("40 24"),
        (0xE847, 1): bytes.fromhex("A3"),
        (0xFDB6, 1): bytes.fromhex("40"),
        (0xFC9D, 1): bytes.fromhex("78"),
        (0xE732, 2): bytes.fromhex("12 34"),
        (0xEC2A, 12): bytes.fromhex(
            "00 00 00 00 00 00 40 24 00 00 00 00"),
    }
    values.update({
        (address, len(expected)): expected
        for _name, address, expected in ms413_diag.PATCH_PROBES
    })

    class FakeDS2:
        def read_mem(self, address, length):
            calls.append((address, length))
            return values[(address, length)]

        def read_dtc(self):
            calls.append("dtc")
            return bytes.fromhex("01 64 21 00 00 00 00 40 24 00 00")

    snapshot = ms413_diag._recovery_evidence_snapshot(FakeDS2())

    assert calls == [
        (0xEA0C, 2), (0xE847, 1), (0xFDB6, 1), (0xFC9D, 1),
        (0xE732, 2), (0xEC2A, 12), "dtc",
        *dict.fromkeys((address, len(expected))
                       for _name, address, expected in ms413_diag.PATCH_PROBES),
    ]
    assert snapshot["dtcs"][0]["self_test_reason"] == "0x2440"
    assert snapshot["dtc100"]["latched_reason"] == "0x2440"
    assert snapshot["dtc100"]["live_ea0c"] == "0x2440"
    assert snapshot["post_reconnect_values"] == {
        "cut_state_raw": "0xA3",
        "launch_latch_raw": "0x40",
        "battery_v": 12.235,
    }
    assert ms413_diag._required_probe_failures(snapshot["patch_probes"]) == []
    assert snapshot["ignition_hook_revision"] == "V9"


def test_reconnect_retries_a_short_identity_before_exact_match():
    expected = b"A" * ms413_diag.IDENTITY_LENGTH

    class FakeDS2:
        baud = 187500
        open_count = 0

        def open(self):
            self.open_count += 1

        def close(self):
            pass

        def identify(self):
            return expected[:-1] if self.open_count == 1 else expected

    ds2 = FakeDS2()
    evidence = {}
    attempts = ms413_diag._reopen_same_ecu(
        ds2, expected, deadline=ms413_diag.time.monotonic() + 1, delay=0,
        evidence=evidence)
    assert attempts == ds2.open_count == 2
    assert len(evidence["exceptions"]) == 1
    assert ds2.baud == 9600


def test_only_ignition_and_launch_hooks_are_required():
    probes = {
        name: {"matches": True}
        for name, _address, _expected in ms413_diag.PATCH_PROBES
        if not name.startswith(("ignition_cut_v10_", "ignition_cut_v11_"))
    }
    assert set(ms413_diag.REQUIRED_RUNTIME_PROBES) == {
        name for name in probes
        if name not in {"calguard_v4_hook", "softbsl_v10_hook"}
    }
    probes["calguard_v4_hook"]["matches"] = False
    probes["softbsl_v10_hook"]["matches"] = False
    assert ms413_diag._required_probe_failures(probes) == []

    probes["ignition_cut_v9_coil_hook"]["matches"] = False
    assert ms413_diag._required_probe_failures(probes) == [
        "ignition_cut_v9_coil_hook"]


@pytest.mark.parametrize("revision", ["V9", "V10", "V11"])
def test_hook_profiles_match_exact_installed_revision(revision):
    from engines.patcher import patch_ms41
    from tests.conftest import ref

    patches = patch_ms41.load_patches()
    ignition = "ignition_cut_" + revision.lower()
    patches["launch_control_v7"] = dict(
        patches["launch_control_v7"], requires=[ignition])
    image, _ = patch_ms41.build(
        ref("MS41.3"), [ignition, "launch_control_v7"],
        patches=patches, allow_deprecated=True)

    class ImageDS2:
        def read_mem(self, address, length):
            return bytes(image[(address + i) ^ 0x4000] for i in range(length))

    probes = ms413_diag._patch_probe_snapshot(ImageDS2())
    assert ms413_diag._runtime_revision(probes) == revision
    assert ms413_diag._required_probe_failures(probes) == []
    for name, probe in probes.items():
        if name.startswith("ignition_cut_" + revision.lower() + "_"):
            probe["matches"] = False
            break
    assert ms413_diag._runtime_revision(probes) is None
    assert ms413_diag._required_probe_failures(probes)
    if revision in ("V10", "V11"):
        # Shared hooks alone cannot certify V11's restored native operand.
        image = bytearray(image)
        image[0x2F5A6:0x2F5AA] = bytes(4)
        probes = ms413_diag._patch_probe_snapshot(ImageDS2())
        assert ms413_diag._runtime_revision(probes) is None
        assert ms413_diag._required_probe_failures(probes)


@pytest.mark.parametrize("revision", ["V10", "V11"])
def test_recovery_byte_is_decoded_only_for_v10_and_v11(revision):
    class FakeDS2:
        def read_mem(self, address, length):
            return b"\x05" if address == 0xE848 else bytes(length)

        def read_dtc(self):
            return b"\x00"

    legacy = ms413_diag._slow_snapshot(FakeDS2(), "preflight", "V9")
    current = ms413_diag._slow_snapshot(FakeDS2(), "preflight", revision)
    assert legacy["slow_values"]["cut_recovery_pending"] is None
    assert current["slow_values"]["cut_recovery_pending"] == {
        "front_b1": True, "front_b2": False, "rear_b1": True, "rear_b2": False}
    assert ms413_diag._self_test_reasons(0x1000)[0]["meaning"] == (
        "segment-progression check or ASC1 command 0x103 timeout")


def test_reconnect_rejects_a_different_42_byte_identity():
    class FakeDS2:
        baud = 187500

        def open(self):
            pass

        def close(self):
            pass

        def identify(self):
            return b"B" * ms413_diag.IDENTITY_LENGTH

    ds2 = FakeDS2()
    with pytest.raises(ms413_diag.DS2Error, match="differs from the original"):
        ms413_diag._reopen_same_ecu(
            ds2, b"A" * ms413_diag.IDENTITY_LENGTH,
            deadline=ms413_diag.time.monotonic() + 1)
    assert ds2.baud == 9600


@pytest.mark.parametrize("pending", [0x00, 0x0F, 0xC4])
def test_recovery_batch_preserves_raw_byte_and_separates_unknown_bits(pending):
    layout = ms413_diag.RECOVERY_BATCH_LAYOUT
    entries = {name: (address, width) for name, address, width in layout}
    assert len(layout) == 24
    assert sum(width for _name, _address, width in layout[:20]) == 26
    assert sum(width for _name, _address, width in layout[20:]) == 8
    assert entries["cut_recovery_pending"] == (0xE848, 1)
    assert entries["wbo_telemetry"] == (0xE800, 1)
    assert "input_80_81" not in entries

    sample = ms413_diag.decode_batch_payload(_payload({
        "cut_state": 0xA0, "cut_recovery_pending": pending,
        "wbo_telemetry": 135,
    }, layout), layout)
    values = sample["values"]
    assert sample["raw_by_address"]["0xE848"] == f"{pending:02x}"
    assert values["cut_recovery_pending_raw"] == f"0x{pending:02X}"
    assert values["cut_recovery_unknown_bits"] == f"0x{pending & 0xF0:02X}"
    assert values["cut_recovery_pending"] == {
        "front_b1": bool(pending & 1), "front_b2": bool(pending & 2),
        "rear_b1": bool(pending & 4), "rear_b2": bool(pending & 8),
    }
    assert values["input_80"] is None and values["input_81"] is None
    assert values["wbo_afr"] == 15.0
    legacy = ms413_diag.decode_batch_payload(
        _payload({"input_80_81": 3}))["values"]
    assert legacy["input_80"] is True and legacy["input_81"] is True


@pytest.mark.parametrize("exhausted", [False, True])
def test_exact_read_retains_retry_and_timing_evidence(monkeypatch, exhausted):
    responses = iter([b"", OSError("adapter read failed"),
                      b"" if exhausted else b"\xC4"])

    def read_mem(address, length):
        assert (address, length) == (0xE848, 1)
        value = next(responses)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(ms413_diag.time, "sleep", lambda _seconds: None)
    evidence = {}
    client = SimpleNamespace(read_mem=read_mem)
    if exhausted:
        with pytest.raises(ms413_diag.DS2Error, match="failed after 3 attempts"):
            ms413_diag._read_exact(client, 0xE848, 1, evidence)
    else:
        assert ms413_diag._read_exact(client, 0xE848, 1, evidence) == b"\xC4"
    assert evidence["attempts"] == 3
    assert len(evidence["exceptions"]) == (3 if exhausted else 2)
    assert "0/1 bytes" in evidence["exceptions"][0]
    assert "OSError: adapter read failed" in evidence["exceptions"][1]
    assert evidence["started_utc"] <= evidence["completed_utc"]
    assert evidence["duration_s"] >= 0


@pytest.mark.parametrize("wrapped,failure", [
    (False, None), (True, "checksum"), (False, "short"), (True, "negative"),
])
def test_wire_capture_preserves_reply_bytes_and_parser_cleanup(
        monkeypatch, wrapped, failure):
    from ds2 import DS2Interface, _xor
    from tests.test_ds2_transport import _FakeReadSerial

    responses = []

    class Serial(_FakeReadSerial):
        def write(self, frame):
            self.requests.append(bytes(frame))
            selected = frame[2] == 0x06 and frame[3:7] == b"\x00\x00\xE8\x48"
            payload = b"\xC4" if selected else b"\x00"
            if frame[2:4] == b"\x0B\x00":
                payload = _payload({"cut_recovery_pending": 0xC4},
                                   ms413_diag.RECOVERY_BATCH_LAYOUT)
            status = 0xA1 if selected and failure == "negative" else 0xA0
            response = bytes((0x12, len(payload) + 4, status)) + payload
            response += bytes((_xor(response),))
            if selected and failure == "checksum":
                response = response[:-1] + bytes((response[-1] ^ 1,))
            if selected and failure == "short":
                response = response[:-1]
            responses.append(response)
            self._pending = frame + response  # Real echo path must stay separate.
            return len(frame)

    monkeypatch.setattr(ms413_diag.time, "sleep", lambda _seconds: None)
    transport = DS2Interface("COM_TEST", echo=True)
    transport._ser = Serial()
    original_execute, original_read = transport._execute, transport._read_exact
    if wrapped:
        # Android owns the raw transport; also preserve existing instance hooks.
        transport._execute, transport._read_exact = original_execute, original_read
    client = SimpleNamespace(_ds2=transport) if wrapped else transport
    report = StringIO()

    def capture():
        with ms413_diag._wire_capture(client, report):
            assert transport.read_mem(0x133DB, 1) == b"\x00"
            assert transport.read_mem(0xE848, 1) == b"\xC4"
            assert transport.execute(0x0B, b"\x00\x1A") == responses[-1][3:-1]

    if failure:
        with pytest.raises(ms413_diag.DS2Error):
            capture()
    else:
        capture()
    assert transport._execute == original_execute
    assert transport._read_exact == original_read
    assert ("_execute" in transport.__dict__) is wrapped
    assert ("_read_exact" in transport.__dict__) is wrapped

    records = [json.loads(line) for line in report.getvalue().splitlines()]
    assert records[0]["available"] is True
    exchanges = records[1:]
    assert len(exchanges) == (1 if failure else 2)
    for record, request, response in zip(exchanges, transport._ser.requests[1:], responses[1:]):
        assert record["type"] == "wire_exchange"
        assert record["request_frame"] == request.hex()
        assert record["response_raw"] == response.hex()
        assert [chunk["phase"] for chunk in record["response_chunks"]] == [
            "response_header", "response_body"]
        assert record["accepted"] is (failure is None)
        assert record["started_utc"] <= record["completed_utc"]
    if failure:
        assert "error" in exchanges[0]
    saved = report.getvalue()
    assert transport.read_mem(0x133DB, 1) == b"\x00"
    assert report.getvalue() == saved


@pytest.mark.parametrize("failed_snapshot", [False, True])
def test_slow_snapshot_preserves_individual_recovery_read_evidence(
        monkeypatch, failed_snapshot):
    pairs = iter([bytes.fromhex("A0 C4"), bytes.fromhex("A1 04")])
    neighbors = bytes.fromhex("00 01 02 03 04 05 06 A0 C4 09 0A 0B 0C 0D 0E 0F")
    pending_reads = 0

    def read_mem(address, length):
        nonlocal pending_reads
        if address == 0xE848:
            pending_reads += 1
            if pending_reads == 1:
                raise ms413_diag.DS2Error("transient recovery read")
            return b"\xC4"
        if address == 0xE847:
            assert length == 2
            return next(pairs)
        if address == 0xE840:
            assert length == 16
            return neighbors
        if failed_snapshot and address == 0xEA26:
            raise ms413_diag.DS2Error("later field unavailable")
        return bytes(length)

    monkeypatch.setattr(ms413_diag.time, "sleep", lambda _seconds: None)
    report = StringIO()
    client = SimpleNamespace(read_mem=read_mem, read_dtc=lambda: b"\x00")
    if failed_snapshot:
        with pytest.raises(ms413_diag.DS2Error, match="later field unavailable"):
            ms413_diag._slow_snapshot(client, "postflight", "V11", report)
    else:
        snapshot = ms413_diag._slow_snapshot(client, "postflight", "V11", report)
        assert snapshot["raw_reads"]["cut_state_and_recovery"]["raw"] == "a0c4"
        assert snapshot["raw_reads"]["cut_state_and_recovery_repeat"]["raw"] == "a104"
        assert snapshot["raw_reads"]["recovery_neighbors"]["raw"] == neighbors.hex()

    records = [json.loads(line) for line in report.getvalue().splitlines()]
    assert [record["raw"] for record in records[:4]] == [
        "c4", "a0c4", "a104", neighbors.hex()]
    evidence = records[0]["read_evidence"]
    assert evidence["attempts"] == 2
    assert len(evidence["exceptions"]) == 1
    assert "transient recovery read" in evidence["exceptions"][0]
    for record in records:
        assert record["type"] == "slow_read"
        assert record["snapshot_phase"] == "postflight"
        evidence = record["read_evidence"]
        assert evidence["started_utc"] <= evidence["completed_utc"]
        assert evidence["duration_s"] >= 0
    if failed_snapshot:
        assert len(records) == 5
        assert records[-1]["read_evidence"]["attempts"] == 3
        assert "later field unavailable" in records[-1]["error"]


def test_capture_confirms_only_first_recovery_anomaly(monkeypatch):
    from ds2 import DS2Interface, _xor
    from tests.test_ds2_transport import _FakeReadSerial

    confirmations = iter(["a0c40000", "a0040000", "a0c40000"])

    class Serial(_FakeReadSerial):
        polls = 0

        def write(self, frame):
            self.requests.append(bytes(frame))
            if frame[2:4] == b"\x0B\x00":
                self.polls += 1
                payload = _payload({
                    "cut_state": 0xA0,
                    "cut_recovery_pending": 4 if self.polls == 1 else 0xC4,
                }, ms413_diag.RECOVERY_BATCH_LAYOUT)
            elif frame[2] == 0x06:
                assert frame[3:-1] == bytes.fromhex("0000e84704")
                payload = bytes.fromhex(next(confirmations))
            else:
                assert frame[2:4] == b"\x0B\x01"
                payload = b""
            response = bytes((0x12, len(payload) + 4, 0xA0)) + payload
            self._pending = response + bytes((_xor(response),))
            return len(frame)

    transport = DS2Interface("COM_TEST", echo=False)
    transport._ser = Serial()
    identity = {
        "ignition_hook_revision": "V11",
        "ms413_program_signature": {"matches": True},
        "patch_probes": {name: {"matches": True}
                         for name, _address, _expected in ms413_diag.PATCH_PROBES},
    }
    monkeypatch.setattr(ms413_diag, "_identity_snapshot",
                        lambda _ds2: (bytes(ms413_diag.IDENTITY_LENGTH), identity))
    monkeypatch.setattr(ms413_diag, "_slow_snapshot",
                        lambda _ds2, phase, _revision, report=None: {"type": phase})
    report = StringIO()
    result = ms413_diag.run_capture(SimpleNamespace(
        ds2=transport, report=report, seconds=60, interval=0, quiet=True,
        recovery_tracking=True, cancelled=lambda: transport._ser.polls == 3,
    ))

    assert result == 130  # Deterministic user cancellation after three samples.
    records = [json.loads(line) for line in report.getvalue().splitlines()]
    samples = [record for record in records if record["type"] == "sample"]
    assert [sample["values"]["cut_recovery_pending_raw"] for sample in samples] == [
        "0x04", "0xC4", "0xC4"]
    confirmed = [record for record in records if record["type"] == "recovery_confirmation"]
    assert [record["raw"] for record in confirmed] == [
        "a0c40000", "a0040000", "a0c40000"]
    assert [record["index"] for record in confirmed] == [0, 1, 2]
    assert all(record["trigger_elapsed_s"] == samples[1]["elapsed_s"]
               for record in confirmed)
    assert records.index(samples[0]) < records.index(samples[1]) < records.index(confirmed[0])
    assert records.index(confirmed[-1]) < records.index(samples[2])
    assert records[-1]["recovery_anomaly_samples"] == 2
    assert "_execute" not in transport.__dict__
    assert "_read_exact" not in transport.__dict__
