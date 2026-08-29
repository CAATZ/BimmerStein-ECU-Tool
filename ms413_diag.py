# bimmerstein-test: {"api":1,"id":"ms413-ignition-cut","name":"MS41.3 ignition-cut diagnosis","description":"Read-only ignition-cut, limiter, fueling, and self-test capture.","version":"1","mode":"read_only","ecu_ids":["SHINDE1"],"default_duration_seconds":30,"result_schema":"bimmerstein-ms413-diagnostic-v1"}
"""Read-only MS41.3/SS1v2 cut, limiter, fueling, and self-test capture."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from ds2 import DS2Error, DS2Interface
from ds2_fast_read import IDENTITY_LENGTH
from dtc import parse_ds2_dtc_response
from ecu_info import (
    BANK_MARKER_ADDR,
    BANK_MARKER_LEN,
    FW_VERSION_ADDR,
    FW_VERSION_LEN,
    decode_bank_marker,
    decode_firmware_version,
)
from ms41 import (
    MS41_3_MARKER_256K,
    SS1V2_PROG_SIG,
    SS1V2_PROG_SIG_ADDR,
)


# Group 1 must contain 26 data bytes and group 2 exactly 8. Keeping the
# transient cut, limiter, and fueling values in one ECU telegram avoids the
# time skew of many individual 9600-baud reads.
BATCH_LAYOUT = (
    ("cut_state", 0xE847, 1),
    ("launch_latch", 0xFDB6, 1),
    ("battery", 0xFC9D, 1),
    ("native_limiter", 0xFD13, 1),
    ("fuel_cut_stage", 0xF013, 1),
    ("native_soft_rpm", 0xF014, 1),
    ("cut_rpm", 0xFC3C, 1),
    ("working_speed", 0xF19A, 1),
    ("throttle", 0xE8D0, 1),
    ("input_82", 0xFD60, 1),
    ("input_80_81", 0xFD61, 1),
    ("operating_state", 0xFD14, 1),
    ("lambda_state_b1", 0xF01A, 1),
    ("lambda_state_b2", 0xF0C6, 1),
    ("rpm_mirror", 0xDA2A, 2),
    ("base_ipw", 0xEF78, 2),
    ("final_ipw_b1", 0xEF7E, 2),
    ("final_ipw_b2", 0xEF80, 2),
    ("stft_b1", 0xF01E, 2),
    ("stft_b2", 0xF0CA, 2),
    ("engine_load", 0xFC52, 2),
    ("maf", 0xDA34, 2),
    ("front_o2_b1", 0xFA9A, 2),
    ("front_o2_b2", 0xFA98, 2),
)
STATIONARY_WIDEBAND_BATCH_LAYOUT = tuple(
    ("wbo_telemetry", 0xE800, 1) if name == "input_82"
    else (name, address, length)
    for name, address, length in BATCH_LAYOUT
)

SLOW_READS = (
    ("dtc8_record", 0xEA26, 12),
    ("dtc8_branch", 0xEA0E, 1),
    ("dtc100_record", 0xEC2A, 12),
    ("self_test_live", 0xEA0C, 2),
    ("self_test_scheduler", 0xE655, 1),
    ("boot_scratch_support_only", 0xE732, 2),
    ("softbsl_phase", 0xE740, 1),
    ("native_resume_state", 0xF015, 2),
    ("native_thresholds", 0xDB86, 2),
    ("native_state_flags", 0xFD44, 2),
    ("legacy_launch_state", 0xFD5A, 1),
    ("input_82_latch", 0xFD60, 1),
    ("battery", 0xFC9D, 1),
    ("additive_trim_b1", 0xF028, 2),
    ("additive_trim_b2", 0xF0D4, 2),
    ("ltft_b1", 0xF030, 2),
    ("ltft_b2", 0xF0DC, 2),
    ("lambda_counter", 0xEDE6, 2),
    ("lambda_functions", 0xFD0E, 4),
    ("oxygen_sensor_config_byte6", 0x10006, 1),
    ("wbo_input_select", 0x133C0, 2),
    ("wbo_voltage_endpoints", 0x133C3, 2),
    ("narrowband_emulation_switch", 0x1479F, 1),
    ("wbo_afr_endpoints", 0x147CF, 2),
    ("feature_flags", 0xFD22, 2),
    ("diagnostic_flags", 0xFD30, 2),
    ("diagnostic_sources", 0xFD38, 2),
    ("filtered_load", 0xE8E8, 2),
    ("maf_adc", 0xFA9E, 2),
    ("maf_fault_adc_snapshot", 0xE9F6, 1),
    ("p1l", 0xFF04, 2),
    ("wbo_telemetry", 0xE800, 1),
    ("wbo_target", 0xE810, 2),
    ("lambda_compare_b1", 0xF043, 3),
    ("lambda_compare_b2", 0xF0EF, 3),
    ("lambda_control_flags", 0xFD46, 2),
    ("heater_rear_b1", 0xF064, 1),
    ("heater_rear_b2", 0xF110, 1),
    ("heater_front_b1", 0xF189, 1),
    ("heater_front_b2", 0xF191, 1),
    ("diagnostic_masks", 0x133DB, 5),
    ("coil_monitor_switches", 0x1023D, 2),
    ("ignition_cals", 0x12A65, 5),
    ("launch_cals", 0x147E0, 11),
)

SELF_TEST_BITS = {
    0x0001: "startup RAM pattern test: FA00-FDFE",
    0x0002: "startup RAM pattern test: D800-DBFE or E420-F7F2",
    0x0004: "SSC peripheral status bit 0",
    0x0008: "startup byte-RAM test: D080-D0FF",
    0x0010: "SSC identity did not match ASCII 04",
    0x0020: "program/calibration compatibility-header mismatch",
    0x0040: "serial EEPROM ready/busy timeout",
    0x0080: "serial EEPROM write/readback verify failed",
    0x0100: "program-region CRC/checksum mismatch",
    0x0200: "SSC peripheral status bit 1",
    0x0400: "calibration checksum-block verification failed",
    0x0800: "unresolved: no set-producer found in exact SS1v2",
    0x1000: "internal periodic self-test condition (underlying test unresolved)",
    0x2000: "watchdog reset",
}

PATCH_PROBES = (
    ("ignition_cut_v9_hook", 0x3D92A, bytes.fromhex("da0370dc")),
    ("ignition_cut_v9_coil_hook", 0x3D98E, bytes.fromhex("da0370dc")),
    ("ignition_cut_v9_control_hook", 0x2755A, bytes.fromhex("da03a0de")),
    ("ignition_cut_v9_stft_hook", 0x2BF10, bytes.fromhex("fa03a0e1")),
    ("ignition_cut_v9_ltft_hook", 0x2C86E, bytes.fromhex("da03c6e1")),
    ("ignition_cut_v9_additive_hook", 0x2C89C, bytes.fromhex("da03eae1")),
    ("ignition_cut_v9_monitor_hook", 0x3553E, bytes.fromhex("fa030ee2")),
    ("ignition_cut_v9_front_o2_hook", 0x2B05C, bytes.fromhex("fa0336e2")),
    ("ignition_cut_v9_rear_o2_hook", 0x2B11E, bytes.fromhex("fa035ee2")),
    ("ignition_cut_v9_catalyst_b1_hook", 0x36248, bytes.fromhex("fa0386e2")),
    ("ignition_cut_v9_catalyst_b2_hook", 0x36274, bytes.fromhex("fa03aee2")),
    ("ignition_cut_v9_misfire_hook", 0x3025C, bytes.fromhex("fa03d6e2")),
    ("launch_control_v7_hook", 0x39928, bytes.fromhex("fa0380df")),
    ("launch_control_v7_soft_hook", 0x207D2, bytes.fromhex("fa0300e1")),
    ("launch_control_v7_hard_hook_a", 0x2088A, bytes.fromhex("da0340e1")),
    ("launch_control_v7_hard_hook_b", 0x2092A, bytes.fromhex("da0340e1")),
    ("calguard_v4_hook", 0x093A, bytes.fromhex("ea00101ecc00cc00")),
    ("softbsl_v10_hook", 0x15A0, bytes.fromhex("da00921d")),
)
REQUIRED_RUNTIME_PROBES = (
    "ignition_cut_v9_hook",
    "ignition_cut_v9_coil_hook",
    "ignition_cut_v9_control_hook",
    "ignition_cut_v9_stft_hook",
    "ignition_cut_v9_ltft_hook",
    "ignition_cut_v9_additive_hook",
    "ignition_cut_v9_monitor_hook",
    "ignition_cut_v9_front_o2_hook",
    "ignition_cut_v9_rear_o2_hook",
    "ignition_cut_v9_catalyst_b1_hook",
    "ignition_cut_v9_catalyst_b2_hook",
    "ignition_cut_v9_misfire_hook",
    "launch_control_v7_hook",
    "launch_control_v7_soft_hook",
    "launch_control_v7_hard_hook_a",
    "launch_control_v7_hard_hook_b",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _retry(label, call, evidence=None):
    started = time.monotonic()
    last_error = None
    errors = []
    for attempt in range(3):
        try:
            result = call()
            if evidence is not None:
                evidence.update({
                    "attempts": attempt + 1,
                    "exceptions": errors,
                    "duration_s": round(time.monotonic() - started, 4),
                })
            return result
        except (DS2Error, OSError) as error:
            last_error = error
            errors.append(f"{type(error).__name__}: {error}")
            if attempt < 2:
                time.sleep(0.05)
    if evidence is not None:
        evidence.update({
            "attempts": 3,
            "exceptions": errors,
            "duration_s": round(time.monotonic() - started, 4),
        })
    raise DS2Error(f"{label} failed after 3 attempts: {last_error}")


def decode_dtc_payload(payload: bytes) -> list[dict]:
    """Decode count + 10-byte MS41 DTC records without changing ECU state."""
    decoded = []
    for dtc in parse_ds2_dtc_response(payload):
        flags = dtc.status_raw
        item = {
            "code": dtc.code,
            "flags": f"0x{flags:02X}",
            "recorded": bool(flags & 0x20),
            "active": dtc.is_active,
            "history": bool(flags & 0x80),
            "subtype": flags & 0x1F,
            "system": dtc.system,
            "description": dtc.description,
            "raw_record": dtc.raw_record.hex(),
        }
        if dtc.self_test_reason is not None:
            item["self_test_reason"] = dtc.self_test_reason
        decoded.append(item)
    return decoded


def _read_exact(ds2, address: int, length: int) -> bytes:
    def read():
        data = bytes(ds2.read_mem(address, length))
        if len(data) != length:
            raise DS2Error(
                f"short read at 0x{address:05X}: {len(data)}/{length} bytes")
        return data

    return _retry(f"read 0x{address:05X}", read)


def _write_record(report, record) -> None:
    report.write(json.dumps(record, separators=(",", ":")) + "\n")
    report.flush()


def _le(raw: bytes) -> int:
    return int.from_bytes(raw, "little")


def _trim_percent(raw: int) -> float:
    return round((raw - 32768) * 100 / 65535, 3)


def _additive_ms(raw: int) -> float:
    return round((raw - 32768) * 0.00534, 4)


def _self_test_reasons(value: int) -> list[dict]:
    reasons = [
        {"mask": f"0x{mask:04X}", "meaning": meaning}
        for mask, meaning in SELF_TEST_BITS.items()
        if value & mask
    ]
    unknown = value & ~sum(SELF_TEST_BITS)
    if unknown:
        reasons.append({
            "mask": f"0x{unknown:04X}",
            "meaning": "unknown/unmapped self-test bit",
        })
    return reasons


def _selector(raw: int) -> str:
    return {
        0x00: "always",
        0x01: "input 80",
        0x02: "input 81",
        0x04: "input 82",
        0xFF: "off",
    }.get(raw, f"invalid 0x{raw:02X}")


def _decode_ignition_cals(raw: bytes) -> dict:
    ipw = _le(raw[3:5])
    return {
        "switch": _selector(raw[0]),
        "rpm": raw[1] * 32,
        "hysteresis_rpm": 0 if raw[2] == 0xFF else raw[2] * 32,
        "fixed_ipw_mode": "stock" if ipw == 0xFFFF else "fixed",
        "fixed_ipw_ms": None if ipw == 0xFFFF else round(ipw * 0.00534, 4),
    }


def _decode_launch_cals(raw: bytes) -> dict:
    ipw = _le(raw[9:11])
    return {
        "switch": _selector(raw[0]),
        "cut_type": {0: "fuel", 1: "ignition"}.get(
            raw[1], f"invalid {raw[1]}"),
        "clutch_polarity": "active-high" if raw[2] == 0 else "active-low",
        "rpm": raw[3] * 32,
        "arm_speed_kmh": raw[4],
        "max_speed_kmh": raw[5],
        "min_tps_pct": round(raw[6] * 0.47, 2),
        "hard_rpm": None if raw[7] == 0xFF else raw[7] * 32,
        "hard_rpm_mode": "soft + 96" if raw[7] == 0xFF else "fixed",
        "hysteresis_rpm": 0 if raw[8] == 0xFF else raw[8] * 32,
        "fixed_ipw_mode": "stock" if ipw == 0xFFFF else "fixed",
        "fixed_ipw_ms": None if ipw == 0xFFFF else round(ipw * 0.00534, 4),
    }


def _patch_probe_snapshot(ds2) -> dict:
    probes = {}
    for name, address, expected in PATCH_PROBES:
        raw = _read_exact(ds2, address, len(expected))
        probes[name] = {
            "address": f"0x{address:05X}",
            "raw": raw.hex(),
            "expected": expected.hex(),
            "matches": raw == expected,
        }
    return probes


def _required_probe_failures(probes: dict) -> list[str]:
    return [
        name for name in REQUIRED_RUNTIME_PROBES
        if not probes.get(name, {}).get("matches", False)
    ]


def _identity_snapshot(ds2) -> tuple[bytes, dict]:
    identify = bytes(_retry("identify", ds2.identify))
    if len(identify) != IDENTITY_LENGTH:
        raise DS2Error(
            f"identification response: expected {IDENTITY_LENGTH} bytes, "
            f"got {len(identify)}")
    ecu_id = identify[:7].decode("ascii", errors="strict")
    firmware_raw = _read_exact(ds2, FW_VERSION_ADDR, FW_VERSION_LEN)
    signature_addr = SS1V2_PROG_SIG_ADDR ^ 0x4000
    signature = _read_exact(ds2, signature_addr, len(SS1V2_PROG_SIG))
    cal_marker_addr = MS41_3_MARKER_256K ^ 0x4000
    cal_marker = _read_exact(ds2, cal_marker_addr, 5)
    cal_prefix = _read_exact(ds2, 0x1000E, 2)
    bank_raw = _read_exact(ds2, BANK_MARKER_ADDR, BANK_MARKER_LEN)

    probes = _patch_probe_snapshot(ds2)

    return identify, {
        "identify_length": len(identify),
        "identify_sha256": hashlib.sha256(identify).hexdigest(),
        "ecu_id": ecu_id,
        "firmware_version": decode_firmware_version(firmware_raw),
        "cal_id_prefix": cal_prefix.decode("ascii", errors="replace"),
        "ms413_program_signature": {
            "address": f"0x{signature_addr:05X}",
            "raw": signature.hex(),
            "expected": SS1V2_PROG_SIG.hex(),
            "matches": signature == SS1V2_PROG_SIG,
        },
        "ms413_cal_marker": {
            "address": f"0x{cal_marker_addr:05X}",
            "ascii": cal_marker.decode("ascii", errors="replace"),
            "raw": cal_marker.hex(),
            "starts_with_ss1": cal_marker.startswith(b"SS1"),
        },
        "softbsl_bank_marker": {
            "address": f"0x{BANK_MARKER_ADDR:05X}",
            "raw": bank_raw.hex(),
            "bank": decode_bank_marker(bank_raw),
        },
        "patch_probes": probes,
    }


def _reopen_same_ecu(ds2, expected_identity: bytes, *, deadline: float,
                     delay=0.3, evidence=None) -> int:
    expected_identity = bytes(expected_identity)
    if len(expected_identity) != IDENTITY_LENGTH:
        raise DS2Error(
            f"original ECU identity must be exactly {IDENTITY_LENGTH} bytes")

    reconnect = getattr(ds2, "reconnect_same_ecu", None)
    if callable(reconnect):
        result = reconnect(max(0.1, deadline - time.monotonic()))
        if evidence is not None:
            evidence.update(result)
        return int(result["attempts"])

    try:
        ds2.close()
    except Exception:
        pass

    last_error = None
    errors = []
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        ds2.baud = 9600
        try:
            ds2.open()
            reopened_identity = bytes(ds2.identify())
        except Exception as error:
            last_error = error
            errors.append(
                f"attempt {attempt}: {type(error).__name__}: {error}")
            try:
                ds2.close()
            except Exception:
                pass
        else:
            if len(reopened_identity) == IDENTITY_LENGTH:
                if reopened_identity != expected_identity:
                    try:
                        ds2.close()
                    except Exception:
                        pass
                    if evidence is not None:
                        evidence.update({
                            "attempts": attempt,
                            "exceptions": errors,
                        })
                    raise DS2Error(
                        "reopened ECU identity differs from the original "
                        f"{IDENTITY_LENGTH}-byte identity")
                if evidence is not None:
                    evidence.update({
                        "attempts": attempt,
                        "exceptions": errors,
                    })
                return attempt
            last_error = DS2Error(
                "reopened ECU identify returned "
                f"{len(reopened_identity)} bytes; expected {IDENTITY_LENGTH}")
            errors.append(
                f"attempt {attempt}: {type(last_error).__name__}: "
                f"{last_error}")
            try:
                ds2.close()
            except Exception:
                pass

        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(delay, remaining))

    if evidence is not None:
        evidence.update({"attempts": attempt, "exceptions": errors})
    raise DS2Error(
        f"ECU did not return at DS2 9600 before the reconnect deadline "
        f"after {attempt} attempts: "
        f"{last_error}")


def _recovery_evidence_snapshot(ds2) -> dict:
    live = _read_exact(ds2, 0xEA0C, 2)
    cut_state = _read_exact(ds2, 0xE847, 1)
    launch_latch = _read_exact(ds2, 0xFDB6, 1)
    battery = _read_exact(ds2, 0xFC9D, 1)
    boot_scratch = _read_exact(ds2, 0xE732, 2)
    record = _read_exact(ds2, 0xEC2A, 12)
    dtc_payload = bytes(_retry("read DTC after reconnect", ds2.read_dtc))
    probes = _patch_probe_snapshot(ds2)
    reason = record[6] | (record[7] << 8)
    live_reason = _le(live)

    return {
        "type": "transport_recovery_evidence",
        "captured_utc": _utc_now(),
        "raw_reads": {
            "self_test_live": {
                "address": "0x0EA0C", "length": 2, "raw": live.hex()},
            "cut_state": {
                "address": "0x0E847", "length": 1,
                "raw": cut_state.hex()},
            "launch_latch": {
                "address": "0x0FDB6", "length": 1,
                "raw": launch_latch.hex()},
            "battery": {
                "address": "0x0FC9D", "length": 1,
                "raw": battery.hex()},
            "boot_scratch_support_only": {
                "address": "0x0E732", "length": 2,
                "raw": boot_scratch.hex()},
            "dtc100_record": {
                "address": "0x0EC2A", "length": 12,
                "raw": record.hex()},
        },
        "dtc_payload_raw": dtc_payload.hex(),
        "dtcs": decode_dtc_payload(dtc_payload),
        "patch_probes": probes,
        "post_reconnect_values": {
            "cut_state_raw": f"0x{cut_state[0]:02X}",
            "launch_latch_raw": f"0x{launch_latch[0]:02X}",
            "battery_v": round(battery[0] * 0.10196, 3),
        },
        "dtc100": {
            "latched_reason": f"0x{reason:04X}",
            "latched_reasons": _self_test_reasons(reason),
            "live_ea0c": f"0x{live_reason:04X}",
            "live_reasons": _self_test_reasons(live_reason),
            "note": "Captured immediately after exact-identity reconnect; "
                    "EC30/EC31 are authoritative and E732 is support-only. "
                    "Do not require bit 0x2000 as the sole reset signature; "
                    "controlled watchdog reset checks can latch reason 0x0003.",
        },
    }


def _slow_snapshot(ds2, phase: str) -> dict:
    raw = {
        name: _read_exact(ds2, address, length)
        for name, address, length in SLOW_READS
    }
    dtc_payload = bytes(_retry("read DTC", ds2.read_dtc))
    dtcs = decode_dtc_payload(dtc_payload)
    record = raw["dtc100_record"]
    reason = record[6] | (record[7] << 8)
    live_reason = _le(raw["self_test_live"])

    return {
        "type": phase,
        "captured_utc": _utc_now(),
        "raw_reads": {
            name: {
                "address": f"0x{address:05X}",
                "length": length,
                "raw": raw[name].hex(),
            }
            for name, address, length in SLOW_READS
        },
        "dtc_payload_raw": dtc_payload.hex(),
        "dtcs": dtcs,
        "dtc100": {
            "latched_reason": f"0x{reason:04X}",
            "latched_reasons": _self_test_reasons(reason),
            "live_ea0c": f"0x{live_reason:04X}",
            "live_reasons": _self_test_reasons(live_reason),
            "note": "EC30/EC31 in the 12-byte record are authoritative; "
                    "EA0C is volatile and E732 is support-only boot scratch. "
                    "Do not require bit 0x2000 as the sole reset signature; "
                    "controlled watchdog reset checks can latch reason 0x0003.",
        },
        "slow_values": {
            "softbsl_phase_e740": raw["softbsl_phase"][0],
            "native_resume_f015": raw["native_resume_state"][0],
            "native_resume_f016": raw["native_resume_state"][1],
            "native_soft_db86_rpm": raw["native_thresholds"][0] * 32,
            "native_hard_db87_rpm": raw["native_thresholds"][1] * 32,
            "legacy_launch_request": bool(
                raw["legacy_launch_state"][0] & 0x80),
            "narrowband_emulation": bool(
                raw["legacy_launch_state"][0] & 0x04),
            "input_82": bool(raw["input_82_latch"][0] & 0x80),
            "battery_v": round(raw["battery"][0] * 0.10196, 3),
            "additive_trim_b1_ms": _additive_ms(_le(raw["additive_trim_b1"])),
            "additive_trim_b2_ms": _additive_ms(_le(raw["additive_trim_b2"])),
            "ltft_b1_pct": _trim_percent(_le(raw["ltft_b1"])),
            "ltft_b2_pct": _trim_percent(_le(raw["ltft_b2"])),
            "oxygen_sensor_config_byte6": (
                f"0x{raw['oxygen_sensor_config_byte6'][0]:02X}"),
            "oxygen_sensor_mode": {
                0x0C: "single-channel",
                0x14: "dual-channel",
            }.get(raw["oxygen_sensor_config_byte6"][0], "unknown"),
            "wbo_input_select": f"0x{_le(raw['wbo_input_select']):04X}",
            "wbo_voltage_endpoints_v": [
                round(value * 5 / 255, 4)
                for value in raw["wbo_voltage_endpoints"]
            ],
            "narrowband_emulation_switch": (
                f"0x{raw['narrowband_emulation_switch'][0]:02X}"),
            "narrowband_emulation_config": {
                0x00: "enabled",
                0xFF: "disabled",
            }.get(raw["narrowband_emulation_switch"][0], "unknown"),
            "wbo_afr_endpoints": [
                round(value * 0.05 + 8.25, 2)
                for value in raw["wbo_afr_endpoints"]
            ],
            "wbo_acquisition_enabled": bool(
                _le(raw["feature_flags"]) & 0x0100),
            "tps_fault_active": bool(
                _le(raw["diagnostic_flags"]) & 0x0001),
            "maf_fault_fallback_active": bool(
                _le(raw["diagnostic_flags"]) & 0x0002),
            "dtc_evaluator_result_bit6": bool(
                _le(raw["diagnostic_sources"]) & 0x0040),
            "dtc8_branch_ea0e": raw["dtc8_branch"][0],
            "dtc8_latched_branch_ea26": raw["dtc8_record"][0] & 0x07,
            "dtc8_filtered_load_e8e8": _le(raw["filtered_load"]),
            "dtc8_filtered_load_high_e8e9": raw["filtered_load"][1],
            "dtc8_maf_adc_fa9e": _le(raw["maf_adc"]),
            "dtc8_maf_adc_snapshot_e9f6": raw["maf_fault_adc_snapshot"][0],
            "dtc8_p1l_10": bool(_le(raw["p1l"]) & 0x0400),
            "wbo_telemetry_afr": round(
                raw["wbo_telemetry"][0] * 0.05 + 8.25, 2),
            "narrowband_target_v": round(
                raw["wbo_target"][0] * 5 / 255, 4),
            "wbo_target_afr": round(
                raw["wbo_target"][1] * 0.05 + 8.25, 2),
            "lambda_compare_b1_v": [
                round(value * 5 / 255, 4)
                for value in raw["lambda_compare_b1"]
            ],
            "lambda_compare_b2_v": [
                round(value * 5 / 255, 4)
                for value in raw["lambda_compare_b2"]
            ],
            "narrowband_wrapper_ran": bool(
                _le(raw["lambda_control_flags"]) & 0x0002),
            "heater_front_b1_pct": round(raw["heater_front_b1"][0] * 100 / 255, 2),
            "heater_front_b2_pct": round(raw["heater_front_b2"][0] * 100 / 255, 2),
            "heater_rear_b1_pct": round(raw["heater_rear_b1"][0] * 100 / 255, 2),
            "heater_rear_b2_pct": round(raw["heater_rear_b2"][0] * 100 / 255, 2),
            "diagnostic_masks_133db": raw["diagnostic_masks"].hex(),
            "coil_monitor_switches_1023d": raw[
                "coil_monitor_switches"].hex(),
        },
        "ignition_cut_cals": _decode_ignition_cals(raw["ignition_cals"]),
        "launch_control_cals": _decode_launch_cals(raw["launch_cals"]),
    }


def decode_batch_payload(raw: bytes, layout=BATCH_LAYOUT) -> dict:
    if len(raw) != 38:
        raise DS2Error(f"short telegram payload: {len(raw)}/38 bytes")

    values = {}
    raw_by_address = {}
    offset = 2
    for index, (name, address, length) in enumerate(layout):
        if index == 20:
            offset = 30
        chunk = raw[offset:offset + length]
        if len(chunk) != length:
            raise DS2Error(f"short telegram value at 0x{address:04X}")
        values[name] = int.from_bytes(chunk, "big")
        raw_by_address[f"0x{address:04X}"] = chunk.hex()
        offset += length

    cut = values["cut_state"]
    decoded = {
            "rpm": values["rpm_mirror"],
            "cut_rpm": values["cut_rpm"] * 32,
            "working_speed_kmh": values["working_speed"],
            "battery_v": round(values["battery"] * 0.10196, 3),
            "throttle_pct": round(values["throttle"] * 100 / 255, 3),
            "cut_state_raw": f"0x{cut:02X}",
            "cut_patch_runtime": (cut & 0xF0) == 0xA0,
            "standalone_ignition_cut": bool(cut & 0x01),
            "launch_ignition_cut": bool(cut & 0x02),
            "launch_fuel_cut": bool(cut & 0x04),
            "launch_armed": bool(values["launch_latch"] & 0x40),
            "native_limiter_active": bool(values["native_limiter"] & 0x80),
            "fuel_cut_stage": values["fuel_cut_stage"],
            "native_soft_threshold_rpm": values["native_soft_rpm"] * 32,
            "input_80": bool(values["input_80_81"] & 0x02),
            "input_81": bool(values["input_80_81"] & 0x01),
            "decel_fuel_cut": bool(values["operating_state"] & 0x20),
            "lambda_regulation_b1": bool(values["lambda_state_b1"] & 0x08),
            "lambda_regulation_b2": bool(values["lambda_state_b2"] & 0x08),
            "base_ipw_ms": round(values["base_ipw"] * 0.00534, 4),
            "final_ipw_b1_ms": round(values["final_ipw_b1"] * 0.00534, 4),
            "final_ipw_b2_ms": round(values["final_ipw_b2"] * 0.00534, 4),
            "stft_b1_pct": _trim_percent(values["stft_b1"]),
            "stft_b2_pct": _trim_percent(values["stft_b2"]),
            "engine_load_mg_st": round(values["engine_load"] * 0.021195, 3),
            "maf_kg_h": round(values["maf"] * 0.25, 3),
            "front_o2_b1_v": round((values["front_o2_b1"] & 0x03FF) * 5 / 1023, 4),
            "front_o2_b2_v": round((values["front_o2_b2"] & 0x03FF) * 5 / 1023, 4),
    }
    if "input_82" in values:
        decoded["input_82"] = bool(values["input_82"] & 0x80)
    if "wbo_telemetry" in values:
        decoded["wbo_afr"] = round(
            values["wbo_telemetry"] * 0.05 + 8.25, 2)
    return {"raw_by_address": raw_by_address, "values": decoded}


def _status_line(sample: dict) -> str:
    value = sample["values"]
    wideband = (
        f"  WBO={value['wbo_afr']:.2f} AFR"
        if "wbo_afr" in value else ""
    )
    return (
        f"{sample['elapsed_s']:6.2f}s  rpm={value['rpm']:4d}  "
        f"E847={value['cut_state_raw']}  native={int(value['native_limiter_active'])}  "
        f"stage={value['fuel_cut_stage']}  "
        f"IPW={value['final_ipw_b1_ms']:.2f}/{value['final_ipw_b2_ms']:.2f}ms  "
        f"STFT={value['stft_b1_pct']:+.1f}/{value['stft_b2_pct']:+.1f}%"
        f"{wideband}"
    )


def _default_output() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path("logs") / f"ms413_diagnostic_{stamp}.jsonl"


def run_capture(args) -> int:
    external_report = getattr(args, "report", None)
    output = None if external_report is not None else (
        Path(args.output) if args.output else _default_output()
    )
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
    stationary_wideband = bool(getattr(args, "stationary_wideband", False))
    batch_layout = (
        STATIONARY_WIDEBAND_BATCH_LAYOUT
        if stationary_wideband else BATCH_LAYOUT
    )
    managed_ds2 = getattr(args, "ds2", None) is None
    ds2 = getattr(args, "ds2", None) or DS2Interface(
        port=args.port,
        baud=9600,
        verbose=args.verbose,
        echo=not args.no_echo,
        serial_factory=getattr(args, "serial_factory", None),
    )
    cancelled = getattr(args, "cancelled", lambda: False)
    sleep = getattr(args, "sleep", time.sleep)
    progress = getattr(args, "progress", lambda *_args: None)
    quiet = bool(getattr(args, "quiet", False))
    sample_count = 0
    transport_gaps = 0
    exit_code = 0
    ready = False
    error_text = None

    report_owner = (
        output.open("x", encoding="utf-8", newline="\n")
        if output is not None else nullcontext(external_report)
    )
    with report_owner as report:
        _write_record(report, {
            "type": "start",
            "schema": "bimmerstein-ms413-diagnostic-v1",
            "captured_utc": _utc_now(),
            "transport": "DS2 9600 8E2",
            "operator_note": getattr(args, "note", ""),
            "stationary_wideband": stationary_wideband,
            "safety": "Only identify, DTC read, memory read, and non-persistent "
                      "telegram setup/poll are used; no flash, erase, explicit "
                      "memory write, reset, DTC clear, or adaptation clear.",
            "batch_layout": [
                {"name": name, "address": f"0x{address:04X}", "length": length}
                for name, address, length in batch_layout
            ],
        })
        try:
            if managed_ds2:
                ds2.open()
            original_identity, identity = _identity_snapshot(ds2)
            _write_record(report, {"type": "identity", **identity})
            if not quiet:
                print(
                    f"Connected: {identity['ecu_id']} / "
                    f"{identity['firmware_version']}  "
                    f"SS1v2={identity['ms413_program_signature']['matches']}")
            if not identity["ms413_program_signature"]["matches"]:
                raise RuntimeError(
                    "exact MS41.3/SS1v2 program signature is missing; "
                    "capture stopped before using MS41.3 RAM mappings")
            required_probe_failures = _required_probe_failures(
                identity["patch_probes"])
            if required_probe_failures:
                raise RuntimeError(
                    "required ignition/launch hooks do not match: "
                    + ", ".join(required_probe_failures))

            _write_record(report, _slow_snapshot(ds2, "preflight"))
            ready = True
            ds2.setup_telegram_batch(
                entries=[
                    (address, length)
                    for _name, address, length in batch_layout
                ])
            started = time.monotonic()
            deadline = started + args.seconds
            last_print = -1.0
            last_state = None
            last_sample_at = None
            gap_missing_since = None

            while True:
                if cancelled():
                    raise KeyboardInterrupt
                cycle_started = time.monotonic()
                poll_evidence = {}
                try:
                    raw = bytes(_retry(
                        "telegram poll", ds2.poll_telegram_batch,
                        poll_evidence))
                    sample = decode_batch_payload(raw, batch_layout)
                except (DS2Error, OSError) as gap_error:
                    gap_started = time.monotonic()
                    if gap_missing_since is None:
                        gap_missing_since = last_sample_at or cycle_started
                    transport_gaps += 1
                    _write_record(report, {
                        "type": "transport_gap",
                        "captured_utc": _utc_now(),
                        "elapsed_s": round(gap_started - started, 4),
                        "gap_index": transport_gaps,
                        "samples_before_gap": sample_count,
                        "poll_attempts": poll_evidence.get("attempts", 0),
                        "poll_exceptions": poll_evidence.get(
                            "exceptions", []),
                        "poll_duration_s": poll_evidence.get(
                            "duration_s", 0.0),
                        "seconds_since_last_sample": round(
                            gap_started - gap_missing_since, 4),
                        "error": f"{type(gap_error).__name__}: {gap_error}",
                    })
                    if not quiet:
                        print(
                            "\nDS2 polling stopped; reopening at 9600 and "
                            "verifying the exact ECU identity...")
                    reconnect_deadline = gap_started + getattr(
                        args, "reconnect_seconds", 20.0)
                    reconnect_evidence = {}
                    try:
                        reconnect_attempts = _reopen_same_ecu(
                            ds2, original_identity,
                            deadline=reconnect_deadline,
                            evidence=reconnect_evidence)
                    except Exception as reconnect_error:
                        _write_record(report, {
                            "type": "transport_reconnect_failed",
                            "captured_utc": _utc_now(),
                            "gap_index": transport_gaps,
                            "reconnect_attempts": reconnect_evidence.get(
                                "attempts", 0),
                            "reconnect_exceptions": reconnect_evidence.get(
                                "exceptions", []),
                            "error": (
                                f"{type(reconnect_error).__name__}: "
                                f"{reconnect_error}"),
                        })
                        raise
                    recovered_at = time.monotonic()
                    _write_record(report, {
                        "type": "transport_recovered",
                        "captured_utc": _utc_now(),
                        "elapsed_s": round(recovered_at - started, 4),
                        "gap_index": transport_gaps,
                        "gap_duration_s": round(
                            recovered_at - gap_started, 4),
                        "reconnect_attempts": reconnect_attempts,
                        "reconnect_exceptions": reconnect_evidence[
                            "exceptions"],
                        "baud": 9600,
                        "identity_matches_original": True,
                        "missing_sample_interval_so_far_s": round(
                            recovered_at - gap_missing_since, 4),
                    })
                    if not quiet:
                        print(
                            "ECU identity confirmed; capturing reset/self-test "
                            "evidence before resuming.")
                    _write_record(report, _recovery_evidence_snapshot(ds2))
                    _write_record(
                        report,
                        _slow_snapshot(ds2, "transport_recovery"),
                    )
                    if args.seconds > 0 and time.monotonic() >= deadline:
                        break
                    ds2.setup_telegram_batch(entries=[
                        (address, length)
                        for _name, address, length in batch_layout
                    ])
                    continue
                sample_at = time.monotonic()
                sample["type"] = "sample"
                sample["elapsed_s"] = round(sample_at - started, 4)
                sample["captured_utc"] = _utc_now()
                sample["poll_attempts"] = poll_evidence["attempts"]
                sample["poll_exceptions"] = poll_evidence["exceptions"]
                sample["poll_duration_s"] = poll_evidence["duration_s"]
                if last_sample_at is not None:
                    sample["sample_interval_s"] = round(
                        sample_at - last_sample_at, 4)
                if gap_missing_since is not None:
                    sample["missing_sample_interval_s"] = round(
                        sample_at - gap_missing_since, 4)
                    gap_missing_since = None
                last_sample_at = sample_at
                _write_record(report, sample)
                sample_count += 1
                progress(
                    "Capturing",
                    min(int(sample["elapsed_s"] * 1000), int(args.seconds * 1000)),
                    int(args.seconds * 1000),
                )

                value = sample["values"]
                state = (
                    value["cut_state_raw"],
                    value["launch_armed"],
                    value["native_limiter_active"],
                    value["fuel_cut_stage"],
                )
                if state != last_state or sample["elapsed_s"] - last_print >= 1:
                    if not quiet:
                        print(_status_line(sample))
                    last_state = state
                    last_print = sample["elapsed_s"]

                if args.seconds == 0 or time.monotonic() >= deadline:
                    break
                delay = args.interval - (time.monotonic() - cycle_started)
                if delay > 0:
                    sleep(delay)
        except KeyboardInterrupt:
            exit_code = 130
            error_text = "capture interrupted by user"
            if not quiet:
                print("\nCapture interrupted; preserving the report.")
        except Exception as error:
            exit_code = 1
            error_text = f"{type(error).__name__}: {error}"
            if not quiet:
                print(f"\nCapture failed: {error_text}")
            _write_record(report, {
                "type": "error",
                "captured_utc": _utc_now(),
                "error": error_text,
            })
        finally:
            if ready:
                try:
                    _write_record(report, _slow_snapshot(ds2, "postflight"))
                except Exception as post_error:
                    post_text = f"{type(post_error).__name__}: {post_error}"
                    _write_record(report, {
                        "type": "postflight_error",
                        "captured_utc": _utc_now(),
                        "error": post_text,
                    })
                    if exit_code == 0:
                        exit_code = 1
                        error_text = post_text
            if managed_ds2:
                try:
                    ds2.close()
                except Exception:
                    pass
            _write_record(report, {
                "type": "summary",
                "captured_utc": _utc_now(),
                "samples": sample_count,
                "transport_gaps": transport_gaps,
                "complete": exit_code == 0,
                "error": error_text,
            })

    if not quiet and output is not None:
        print(f"Saved {sample_count} samples: {output.resolve()}")
    return exit_code


class _ContextReport:
    def __init__(self, context):
        self.context = context
        self.error = None

    def write(self, value):
        for line in value.splitlines():
            if line.strip():
                record = json.loads(line)
                if record.get("type") in ("error", "postflight_error", "summary") and record.get("error"):
                    self.error = record["error"]
                self.context.record(record)

    def flush(self):
        pass


def run(ctx):
    """Developer-test API 1 entrypoint; the app owns USB and result storage."""
    report = _ContextReport(ctx)
    args = argparse.Namespace(
        port="in-process",
        seconds=ctx.duration_seconds,
        interval=0.12,
        output=None,
        verbose=False,
        no_echo=False,
        note=ctx.note,
        reconnect_seconds=20,
        stationary_wideband=True,
        ds2=ctx,
        report=report,
        cancelled=ctx.cancelled,
        sleep=ctx.sleep,
        progress=ctx.progress,
        quiet=True,
    )
    exit_code = run_capture(args)
    if exit_code == 130:
        ctx.check_cancelled()
    if exit_code != 0:
        raise RuntimeError(report.error or "MS41.3 diagnostic capture failed")
    return {"exit_code": exit_code}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only MS41.3 patch/limiter/fueling/self-test capture",
        epilog=(
            "Close BimmerStein and every other diagnostic application using the "
            "adapter first.\nExample: python ms413_diag.py COM7 --seconds 45"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("port", help="K-line adapter port, for example COM7")
    parser.add_argument(
        "--seconds", type=float, default=30.0,
        help="high-rate capture duration; 0 records one sample (default: 30)")
    parser.add_argument(
        "--interval", type=float, default=0.12,
        help="minimum seconds between sample starts (default: 0.12)")
    parser.add_argument("--output", help="new .jsonl report path")
    parser.add_argument(
        "--note", default="",
        help="operator note stored in the report (coil setup, DTC policy, phase)")
    parser.add_argument(
        "--reconnect-seconds", type=float, default=20.0,
        help="maximum seconds to re-identify after a transport gap (default: 20)")
    parser.add_argument(
        "--stationary-wideband", action="store_true",
        help="replace unused fast pin-82 sampling with E800 WBO AFR telemetry")
    parser.add_argument(
        "--no-echo", action="store_true",
        help="direct ASC0 tap only; normal K-line adapters must keep echo enabled")
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if args.seconds < 0:
        print("--seconds must be zero or greater", file=sys.stderr)
        return 2
    if args.interval < 0.05:
        print("--interval must be at least 0.05 seconds", file=sys.stderr)
        return 2
    if args.reconnect_seconds <= 0:
        print("--reconnect-seconds must be greater than zero", file=sys.stderr)
        return 2
    try:
        return run_capture(args)
    except FileExistsError as error:
        print(f"Refusing to overwrite an existing report: {error.filename}",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
