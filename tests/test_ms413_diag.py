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
    return b"".join(int(values.get(name, 0)).to_bytes(
        length, "little" if length == 2 and address & 1 else "big")
        for name, address, length in layout)


@pytest.mark.parametrize("length", [33, 35, 38])
def test_batch_decoder_requires_descriptor_bytes_without_service_headers(length):
    with pytest.raises(ms413_diag.DS2Error, match=f"{length}/34 bytes"):
        ms413_diag.decode_batch_payload(bytes(length))


def test_ms413_capture_layout_and_decoding():
    layout = ms413_diag.BATCH_LAYOUT
    assert len(layout) == 24
    assert sum(length for _name, _address, length in layout[:20]) == 26
    assert sum(length for _name, _address, length in layout[20:]) == 8

    raw = _payload({
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
        "engine_load": 0x1234,
        "maf": 0x0200,
        "front_o2_b1": 512,
        "front_o2_b2": 1023,
    })
    assert raw == bytes.fromhex(
        "a1 40 78 80 03 c8 7d 0c 80 00 00 00 00 00 "
        "0fa0 01f4 0258 02bc 8000 9000 1234 0200 0200 03ff")
    sample = ms413_diag.decode_batch_payload(raw)["values"]

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
    assert sample["engine_load_mg_st"] == 98.769
    assert sample["maf_kg_h"] == 128.
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


def test_recovery_evidence_identifies_program_before_selecting_cut_ram():
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
        (0xEA0C, 2),
        *dict.fromkeys((address, len(expected))
                       for _name, address, expected in ms413_diag.PATCH_PROBES),
        (0xE847, 1), (0xFDB6, 1), (0xFC9D, 1),
        (0xE732, 2), (0xEC2A, 12), "dtc",
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
        if not name.startswith(("ignition_cut_v10_", "ignition_cut_v11_", "ignition_cut_v14_",
                                "launch_control_v14_", "ignition_cut_v16_", "launch_control_v16_",
                                "ignition_cut_v17_", "launch_control_v17_",
                                "ignition_cut_v18_", "launch_control_v18_"))
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


@pytest.mark.parametrize("revision", ["V9", "V10", "V11", "V14", "V16", "V17", "V18"])
def test_hook_profiles_match_exact_installed_revision(revision):
    from engines.patcher import patch_ms41
    from tests.conftest import ref

    patches = patch_ms41.load_patches()
    ignition = "ignition_cut_" + revision.lower()
    launch = "launch_control_" + revision.lower() if revision in ("V14", "V16", "V17", "V18") else "launch_control_v7"
    patches[launch] = dict(patches[launch], requires=[ignition])
    image, _ = patch_ms41.build(
        ref("MS41.3"), [ignition, launch],
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


@pytest.mark.parametrize("revision", ["V10", "V11", "V14"])
def test_recovery_byte_is_decoded_only_for_supported_layouts(revision):
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


def test_v16_slow_and_batch_capture_use_new_state_and_preserve_legacy_layouts():
    calls = []

    class FakeDS2:
        def read_mem(self, address, length):
            calls.append((address, length))
            if (address, length) == (0xD802, 1):
                return b"\x1f"
            if (address, length) == (0xD800, 16):
                return b"\x00\xa0\x1f\xc3\x03" + bytes(11)
            return bytes(length)

        def read_dtc(self):
            return b"\x00"

    snapshot = ms413_diag._slow_snapshot(FakeDS2(), "preflight", "V16")
    assert snapshot["raw_reads"]["cut_recovery_pending"]["address"] == "0x0D802"
    assert snapshot["raw_reads"]["cut_state_and_recovery"]["address"] == "0x0D801"
    assert snapshot["slow_values"]["misfire_recovery_pending"] is True
    assert snapshot["slow_values"]["timing_state"] == "timing override published"
    assert snapshot["slow_values"]["timing_source"] == "standalone ignition band"
    assert not any(0xE847 <= address <= 0xE84A for address, _length in calls)
    for revision in ("V10", "V11", "V14"):
        assert ms413_diag._slow_reads_for_revision(revision) == ms413_diag.SLOW_READS
    layout = ms413_diag._batch_layout_for_revision(ms413_diag.V14_RECOVERY_BATCH_LAYOUT, "V16")
    entries = {name: (address, length) for name, address, length in layout}
    assert entries["cut_state"] == (0xD801, 1)
    assert entries["cut_recovery_pending"] == (0xD802, 1)
    assert entries["timing_state_source"] == (0xD803, 2)
    wire_entries = ms413_diag._batch_entries(layout)
    assert len(wire_entries) == 27
    assert wire_entries[-4:] == [(0xD803, 1), (0xD804, 1), (0xFD60, 1), (0xFD61, 1)]
    ms413_diag.DS2Interface._build_custom_batch_setup(wire_entries)
    raw = _payload({"cut_state": 0xA0, "cut_recovery_pending": 0x1F,
                    "timing_state_source": 0x03C3}, layout)
    decoded = ms413_diag.decode_batch_payload(raw, layout, "V16")
    assert len(raw) == 36 and raw[-4:-2] == b"\xc3\x03"
    assert decoded["raw_by_address"]["0xD803"] == "c303"
    assert decoded["values"]["misfire_recovery_pending"] is True
    assert decoded["values"]["timing_source"] == "standalone ignition band"


def test_v17_knock_qualification_keeps_raw_histories_separate_from_fault_veto():
    layout = ms413_diag._batch_layout_for_revision(ms413_diag.V17_RECOVERY_BATCH_LAYOUT, "V17")
    entries = ms413_diag._batch_entries(layout)
    ms413_diag.DS2Interface._build_custom_batch_setup(entries)
    assert len(entries) == 43
    assert sum(length for _name, _address, length in layout) == 54
    values = {"timing_state_source": 0x03C3, "knock_global_history": 7,
              "knock_guard_marker": 0xA7, "knock_clean_window_mask": 0x3F,
              "knock_open_phase_mask": 0x04, "knock_old_reply_drained": 1,
              "knock_request_qualified": 0, "knock_snapshot_qualified": 1,
              "knock_retained_valid_mask": 0x15,
              **{f"knock_history_channel_{index + 1}": index + 1 for index in range(6)}}
    decoded = ms413_diag.decode_batch_payload(_payload(values, layout), layout, "V17")["values"]
    assert decoded["timing_state"] == "limiter timing constraint active"
    assert decoded["knock_global_history_raw"] == 7
    assert decoded["knock_channel_histories_raw"] == [1, 2, 3, 4, 5, 6]
    assert decoded["native_timing_fault_veto"] is False
    assert decoded["knock_guard_raw"] == "a73f0401000115"
    assert decoded["knock_guard_initialized"] is True
    assert decoded["knock_request_qualified"] is False
    assert decoded["knock_snapshot_qualified"] is True
    assert decoded["knock_retained_valid_mask_raw"] == "0x15"
    assert ms413_diag._decode_knock_qualification(7, [1] * 6, 0xFF80, 0x3FFF)[
        "native_timing_fault_veto"] is False
    for name, mask in (("knock_fault_flags_a", 0x007F), ("knock_fault_flags_b", 0xC000)):
        values[name] = mask
        decoded = ms413_diag.decode_batch_payload(_payload(values, layout), layout, "V17")["values"]
        assert decoded["native_timing_fault_veto"] is True
        values[name] = 0
    assert ms413_diag._decode_timing(0xC3, 3, "V16")["timing_state"] == "timing override published"
    assert ms413_diag._decode_knock_guard(bytes(7))["knock_request_qualified"] is None
    assert ms413_diag._decode_knock_guard(bytes.fromhex("a7000002000000"))[
        "knock_old_reply_drained"] is None


def test_v17_recorder_rejects_an_old_app_ram_mapping(monkeypatch):
    monkeypatch.setattr(ms413_diag, "cut_state_address", lambda _revision: 0xE847)
    with pytest.raises(RuntimeError, match="updated app/core"):
        ms413_diag._batch_layout_for_revision(ms413_diag.V17_RECOVERY_BATCH_LAYOUT, "V17")


def test_knock_snapshot_preserves_selected_cells_retained_words_and_all_learned_rows():
    class FakeDS2:
        def read_mem(self, address, length):
            if address == 0xD805:
                return bytes.fromhex("a73f0401000115")
            if 0xD840 <= address <= 0xD980:
                return bytes([(address - 0xD840) // 64 + 1]) * length
            if address == 0xE9C0:
                return bytes.fromhex("010002000300040005000600")
            if address == 0xE9BA or address == 0xE9CC:
                return bytes(range(6))
            if address == 0xE9D9:
                return b"\x07"
            if address == 0xFD30 and length == 4:
                return bytes.fromhex("00000080")
            return bytes(length)

        def read_dtc(self):
            return b"\x00"

    snapshot = ms413_diag._slow_snapshot(FakeDS2(), "preflight", "V17")
    knock = snapshot["knock_learning"]
    assert knock["selected_cells_raw"] == list(range(6))
    assert knock["retained_channel_words_raw"] == [1, 2, 3, 4, 5, 6]
    assert knock["learned_rows_raw"] == [(bytes([index]) * 64).hex() for index in range(1, 7)]
    assert knock["knock_global_history_raw"] == 7
    assert knock["native_timing_fault_veto"] is True
    assert knock["knock_guard_raw"] == "a73f0401000115"
    assert snapshot["raw_reads"]["knock_guard"]["address"] == "0x0D805"


def test_v16_reconnect_and_confirmation_select_new_ram_from_actual_program():
    from engines.patcher import patch_ms41
    from tests.conftest import ref

    image, _ = patch_ms41.build(
        ref("MS41.3"), ["ignition_cut_v16", "launch_control_v16"], allow_deprecated=True)
    calls = []

    class ImageDS2:
        def read_mem(self, address, length):
            calls.append((address, length))
            if address >= 0x10000 or address < 0xD000:
                return bytes(image[(address + i) ^ 0x4000] for i in range(length))
            if address == 0xD801:
                return bytes.fromhex("a7 1f c3 03")[:length]
            return bytes(length)

        def read_dtc(self):
            return b"\x00"

    ds2 = ImageDS2()
    snapshot = ms413_diag._recovery_evidence_snapshot(ds2)
    assert snapshot["ignition_hook_revision"] == "V16"
    assert snapshot["raw_reads"]["cut_state"]["address"] == "0x0D801"
    assert snapshot["post_reconnect_values"]["cut_state_raw"] == "0xA7"
    assert (0xE847, 1) not in calls
    report = StringIO()
    ms413_diag._recovery_confirmation(ds2, report, 1.0, "V16")
    records = [json.loads(line) for line in report.getvalue().splitlines()]
    assert len(records) == 3
    assert all(record["address"] == "0xD801" and record["raw"] == "a71fc303" for record in records)


@pytest.mark.parametrize("ignition,launch", [
    ("V14", "V16"), ("V16", "V14"), ("V16", "V17"), ("V17", "V16"), ("V18", "V17"), ("V17", "V18"),
])
def test_diagnostic_rejects_mixed_legacy_and_v16_state_owners(ignition, launch):
    from engines.patcher import patch_ms41

    # Intentionally bypass dependency admission to model an inconsistent image.
    # Hooks and cave payloads are the actual emitted descriptor bytes.
    image = bytearray(b"\xff" * 0x40000)
    patches = patch_ms41.load_patches()
    for name in ("ignition_cut_" + ignition.lower(), "launch_control_" + launch.lower()):
        for edit in patches[name]["edits"]:
            payload = bytes.fromhex(edit["data"])
            image[edit["off"]:edit["off"] + len(payload)] = payload

    class ImageDS2:
        def read_mem(self, address, length):
            return bytes(image[(address + i) ^ 0x4000] for i in range(length))

    probes = ms413_diag._patch_probe_snapshot(ImageDS2())
    assert ms413_diag._runtime_revision(probes) == ignition
    assert ms413_diag._required_probe_failures(probes)


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
@pytest.mark.parametrize("address", [0xE848, 0xD801, 0xD805, 0xD80C])
def test_wire_capture_preserves_reply_bytes_and_parser_cleanup(
        monkeypatch, wrapped, failure, address):
    from ds2 import DS2Interface, _xor
    from tests.test_ds2_transport import _FakeReadSerial

    responses = []

    class Serial(_FakeReadSerial):
        def write(self, frame):
            self.requests.append(bytes(frame))
            selected = frame[2] == 0x06 and int.from_bytes(frame[3:7], "big") == address
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
            assert transport.read_mem(address, 1) == b"\xC4"
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
                }, ms413_diag._batch_layout_for_revision(ms413_diag.RECOVERY_BATCH_LAYOUT, "V11"))
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
    # Samples can have identical payloads and rounded timestamps.
    sample_positions = [i for i, record in enumerate(records) if record["type"] == "sample"]
    confirmation_positions = [i for i, record in enumerate(records)
                              if record["type"] == "recovery_confirmation"]
    assert sample_positions[0] < sample_positions[1] < confirmation_positions[0]
    assert confirmation_positions[-1] < sample_positions[2]
    assert records[-1]["recovery_anomaly_samples"] == 2
    assert "_execute" not in transport.__dict__
    assert "_read_exact" not in transport.__dict__


@pytest.mark.parametrize("revision", ["V14", "V16", "V17", "V18"])
def test_current_profiles_cals_and_runtime_match_current_descriptors(monkeypatch, revision):
    from engines.patcher import patch_ms41

    patches = patch_ms41.load_patches()
    image = bytearray(b"\xff" * 0x40000)
    for name in ("ignition_cut_" + revision.lower(), "launch_control_" + revision.lower()):
        for edit in patches[name]["edits"]:
            data = bytes.fromhex(edit["data"])
            image[edit["off"]:edit["off"] + len(data)] = data

    class ImageDS2:
        def read_mem(self, address, length):
            return bytes(image[(address + index) ^ 0x4000] for index in range(length))

    probes = ms413_diag._patch_probe_snapshot(ImageDS2())
    assert ms413_diag._runtime_revision(probes) == revision
    assert not ms413_diag._required_probe_failures(probes)
    profile = getattr(ms413_diag, revision + "_PATCH_PROBES") + getattr(ms413_diag, revision + "_LAUNCH_PROBES")
    for name, _address, _expected in profile:
        probes[name]["matches"] = False
        # Shared hooks can tie another historical profile's failure count.
        # Every missing required probe must still reject admission.
        assert ms413_diag._required_probe_failures(probes)
        probes[name]["matches"] = True

    layout = ms413_diag._batch_layout_for_revision(
        ms413_diag.V18_RECOVERY_BATCH_LAYOUT if revision == "V18" else
        ms413_diag.V17_RECOVERY_BATCH_LAYOUT if revision == "V17" else ms413_diag.V14_RECOVERY_BATCH_LAYOUT,
        revision)
    entries = ms413_diag._batch_entries(layout)
    ms413_diag.DS2Interface._build_custom_batch_setup(entries)
    raw = _payload({"cut_state": 0xA0, "cut_recovery_pending": 0x1F,
                    "timing_state_source": 0x03C3}, layout)
    values = ms413_diag.decode_batch_payload(raw, layout, revision)["values"]
    assert values["cut_recovery_unknown_bits"] == "0x00"
    assert values["misfire_recovery_pending"] is True
    assert values["timing_state"] == (
        "limiter timing constraint active" if revision in ("V17", "V18") else "timing override published")
    assert values["timing_source"] == "standalone ignition band"
    assert values["front_o2_b2_v"] is None
    assert ms413_diag._decode_recovery(0x10, "V11")["cut_recovery_unknown_bits"] == "0x10"
    assert ms413_diag._decode_recovery(0xFF, "V14")["cut_recovery_unknown_bits"] == "0xE0"
    assert ms413_diag._decode_timing(0xC1, 2)["timing_state"] == "eligible, native protection veto"
    assert ms413_diag._decode_timing(0, 0)["timing_state"] == "unknown/uninitialized"

    ignition = bytes.fromhex("00 7d 03 ff ff 06 2f")
    launch = bytes.fromhex("00 01 00 7d 03 14 40 78 03 ff ff 02 ff 3f 06 ff")
    assert patches["ignition_cut_v14"]["cave"]["cals"]["CUT_IGN_ANGLE"] ^ 0x4000 == 0x12A6B
    assert patches["launch_control_v14"]["cave"]["cals"]["LC_CUT_IGN_ANGLE"] ^ 0x4000 == 0x147EF
    assert ms413_diag._decode_ignition_cals(ignition, "V14")["fixed_angle_deg_btdc"] == -6.0
    assert "fixed_angle_deg_btdc" not in ms413_diag._decode_ignition_cals(ignition, "V11")
    cals = ms413_diag._decode_launch_cals(launch, "V14")
    assert cals["hard_rpm"] == 3840  # Independent hard may be below soft.
    assert cals["fuel_hysteresis_b_rpm"] == 64
    assert cals["fuel_hysteresis_a_rpm"] is None
    assert cals["fuel_cut_angle_deg_btdc"] == 0
    assert cals["retard_range_rpm"] == 192
    assert cals["ignition_cut_angle_deg_btdc"] is None

    # Exercise actual capture admission and selected telegram layout without hardware.
    report = StringIO()
    client = SimpleNamespace(
        setup_telegram_batch=lambda *, entries: None,
        poll_telegram_batch=lambda: raw,
    )
    monkeypatch.setattr(ms413_diag, "_wire_capture",
                        lambda *_args: ms413_diag.nullcontext(True))
    monkeypatch.setattr(ms413_diag, "_identity_snapshot", lambda _ds2: (
        bytes(ms413_diag.IDENTITY_LENGTH), {"ignition_hook_revision": revision,
        "ms413_program_signature": {"matches": True}, "patch_probes": probes}))
    monkeypatch.setattr(ms413_diag, "_slow_snapshot",
                        lambda _ds2, phase, _revision, report=None: {"type": phase})
    assert ms413_diag.run_capture(SimpleNamespace(
        ds2=client, report=report, seconds=0, interval=0, quiet=True,
        recovery_tracking=True)) == 0
    records = [json.loads(line) for line in report.getvalue().splitlines()]
    sample = next(record for record in records if record["type"] == "sample")
    assert sample["values"]["timing_source"] == "standalone ignition band"
    assert records[-1]["recovery_anomaly_samples"] == 0
    actual_layout = next(record for record in records if record["type"] == "batch_layout")
    actual_addresses = {item["name"]: item["address"] for item in actual_layout["batch_layout"]}
    assert actual_addresses["cut_state"] == ("0xD801" if revision in ("V16", "V17", "V18") else "0xE847")


def test_v18_knock_marker_distinguishes_modified_episode_without_changing_v17():
    clean = ms413_diag._decode_knock_guard(bytes.fromhex("a73f000101013f"), "V18")
    dirty = ms413_diag._decode_knock_guard(bytes.fromhex("e7000000000000"), "V18")
    assert clean["knock_guard_initialized"] and not clean["knock_modified_timing_episode"]
    assert dirty["knock_guard_initialized"] and dirty["knock_modified_timing_episode"]
    assert not ms413_diag._decode_knock_guard(bytes.fromhex("e7000000000000"), "V17")["knock_guard_initialized"]
    assert ms413_diag._decode_knock_guard(bytes(7), "V18")["knock_modified_timing_episode"] is None


def test_feedback_fields_preserve_bank_ipw_keys_and_existing_throttle_scale():
    values = {"throttle": 100, "cut_state": 0xA8, "final_ipw_b1": 600, "final_ipw_b2": 700}
    sample = ms413_diag.decode_batch_payload(_payload(values))["values"]
    assert sample["throttle_pct"] == round(100 * 100 / 255, 3)
    assert sample["launch_tps_threshold_pct"] == 47
    assert sample["native_fuel_recovery_priority"]
    assert sample["final_ipw_b1_ms"] == 3.204


@pytest.mark.parametrize("revision,base", [("V11",ms413_diag.RECOVERY_BATCH_LAYOUT),
    ("V14",ms413_diag.V14_RECOVERY_BATCH_LAYOUT), ("V16",ms413_diag.V14_RECOVERY_BATCH_LAYOUT),
    ("V17",ms413_diag.V17_RECOVERY_BATCH_LAYOUT), ("V18",ms413_diag.V18_RECOVERY_BATCH_LAYOUT)])
def test_v8_recovery_restores_only_missing_input_bytes_within_native_batch_limit(revision, base):
    layout = ms413_diag._batch_layout_for_revision(base, revision)
    assert layout[-2:] == (("input_82",0xFD60,1), ("input_80_81",0xFD61,1))
    assert [name for name, *_ in layout[:-2]] == [name for name, *_ in base]
    assert len({name for name, *_ in layout}) == len(layout)
    assert ms413_diag._batch_layout_for_revision(layout, revision) == layout
    entries = ms413_diag._batch_entries(layout)
    ms413_diag.DS2Interface._build_custom_batch_setup(entries)
    assert len(entries) <= 49
    values = {"input_82":128, "input_80_81":3, "cut_state":0xAA,
              "native_limiter":128, "native_soft_rpm":125, "throttle":128}
    decoded = ms413_diag.decode_batch_payload(_payload(values,layout),layout,revision)["values"]
    assert decoded["input_80"] and decoded["input_81"] and decoded["input_82"]
    assert decoded["native_fuel_limiter_active"] == decoded["native_limiter_active"] is True
    assert decoded["effective_soft_threshold_rpm"] == decoded["native_soft_threshold_rpm"] == 4000
    assert decoded["throttle_raw"] == 128
    assert decoded["launch_ignition_cut"] and decoded["native_fuel_recovery_priority"]


def test_v8_snapshot_keeps_native_context_and_calibrated_target_separate():
    calls = []
    memory = bytearray(0x20000)
    for address,value in ((0xD801,0xAA),(0xFD13,128),(0xFDB6,64),(0xF014,203),
                          (0xFD60,128),(0xFD61,3),(0x147E3,125),(0x147E6,128)):
        memory[address] = value

    class ReadOnlyDS2:
        def read_mem(self,address,length):
            calls.append((address,length))
            return memory[address:address+length]

        def read_dtc(self):
            calls.append("read_dtc")
            return b"\x00"

    result = ms413_diag._slow_snapshot(ReadOnlyDS2(),"preflight","V18")
    intended_context_reads = {"effective_soft_rpm":(0xF014,1), "native_fuel_limiter":(0xFD13,1),
                              "launch_armed_latch":(0xFDB6,1), "input_80_81_latch":(0xFD61,1)}
    for name,address in intended_context_reads.items():
        assert calls.count(address) == 1
        assert (int(result["raw_reads"][name]["address"],16), result["raw_reads"][name]["length"]) == address
    assert calls == [(a,n) for _name,a,n in ms413_diag._slow_reads_for_revision("V18")] + ["read_dtc"]
    assert result["limiter_context"]["effective_soft_threshold_rpm"] == 6496
    assert result["limiter_context"]["native_fuel_limiter_active"]
    assert result["limiter_context"]["launch_armed"]
    assert result["limiter_context"]["native_fuel_recovery_priority"]
    assert result["launch_control_cals"]["rpm"] == 4000
    assert result["launch_control_cals"]["min_tps_raw"] == 128
    assert result["launch_control_cals"]["min_tps_pct"] == 60.16
    assert all(result["slow_values"][f"input_{pin}"] for pin in (80,81,82))


@pytest.mark.parametrize("raw,label", [(0xA0,"native"), (0xA1,"fixed base override"),
                                       (0xA2,"native fallback"), (0,"unknown/uninitialized")])
def test_v18_fixed_ipw_status(raw, label):
    layout = ms413_diag._batch_layout_for_revision(ms413_diag.V18_RECOVERY_BATCH_LAYOUT, "V18")
    sample = ms413_diag.decode_batch_payload(_payload({"fixed_ipw_state":raw}, layout), layout, "V18")
    assert sample["values"]["fixed_ipw_state"] == label
    assert ms413_diag._decode_fixed_ipw_state(raw)["fixed_ipw_state_raw"] == f"0x{raw:02X}"
