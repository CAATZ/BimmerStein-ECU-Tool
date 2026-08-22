import json
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


def test_capture_reconnects_same_ecu_and_records_reset_evidence(
        monkeypatch, tmp_path):
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
            "ms413_program_signature": {"matches": True},
            "patch_probes": {
                name: {"matches": True}
                for name, _address, _expected in ms413_diag.PATCH_PROBES
            },
        }

    def slow_snapshot(_ds2, phase):
        events.append(("slow", phase))
        return {"type": phase, "captured_utc": "test"}

    def recovery_snapshot(_ds2):
        events.append(("recovery_evidence", 2))
        return {"type": "transport_recovery_evidence",
                "captured_utc": "test"}

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
    assert records[0]["operator_note"] == (
        "Audi coils; coil DTCs disabled; phase 1 idle")
    assert records[0]["stationary_wideband"] is True
    assert any(
        entry["name"] == "wbo_telemetry"
        for entry in records[0]["batch_layout"])
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
        *[(address, len(expected))
          for _name, address, expected in ms413_diag.PATCH_PROBES],
    ]
    assert snapshot["dtcs"][0]["self_test_reason"] == "0x2440"
    assert snapshot["dtc100"]["latched_reason"] == "0x2440"
    assert snapshot["dtc100"]["live_ea0c"] == "0x2440"
    assert snapshot["post_reconnect_values"] == {
        "cut_state_raw": "0xA3",
        "launch_latch_raw": "0x40",
        "battery_v": 12.235,
    }
    assert all(
        probe["matches"] for probe in snapshot["patch_probes"].values())


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
