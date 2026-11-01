# bimmerstein-test: {"api":1,"id":"ms413-ignition-cut","name":"MS41.3 ignition-cut recovery diagnosis","description":"MS41.3 only: V18 and legacy cut, launch inputs, effective limiter threshold, timing and recovery capture.","version":"8","mode":"read_only","ecu_ids":["SHINDE1"],"default_duration_seconds":60,"result_schema":"bimmerstein-ms413-diagnostic-v5"}
"""Read-only MS41.3/SS1v2 cut, limiter, fueling, and self-test capture."""

from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from ds2 import DS2Error, DS2Interface
from cut_runtime import cut_program_state_probe, cut_state_address
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


# Keep transient cut, limiter, and fueling values in one ECU telegram to
# avoid the time skew of many individual 9600-baud reads.
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
RECOVERY_BATCH_LAYOUT = tuple(
    ("cut_recovery_pending", 0xE848, 1) if name == "input_80_81"
    else (name, address, length)
    for name, address, length in STATIONARY_WIDEBAND_BATCH_LAYOUT
)
# Keep the 34-byte payload. V14 uses the second O2 voltage
# slot for the timing state/source word; both O2 voltages remain in snapshots.
V14_RECOVERY_BATCH_LAYOUT = tuple(
    ("timing_state_source", 0xE849, 2) if name == "front_o2_b2"
    else (name, address, length)
    for name, address, length in RECOVERY_BATCH_LAYOUT
)
V17_KNOCK_GUARD_LAYOUT = tuple(
    (name, 0xD805 + index, 1) for index, name in enumerate((
        "knock_guard_marker", "knock_clean_window_mask", "knock_open_phase_mask",
        "knock_old_reply_drained", "knock_request_qualified", "knock_snapshot_qualified",
        "knock_retained_valid_mask"))
)
V17_RECOVERY_BATCH_LAYOUT = V14_RECOVERY_BATCH_LAYOUT + (
    ("knock_global_history", 0xE9D9, 1),
    *((f"knock_history_channel_{index + 1}", 0xE9CC + index, 1) for index in range(6)),
    ("knock_fault_flags_a", 0xFD30, 2),
    ("knock_fault_flags_b", 0xFD32, 2),
) + V17_KNOCK_GUARD_LAYOUT
V18_RECOVERY_BATCH_LAYOUT = V17_RECOVERY_BATCH_LAYOUT + (("fixed_ipw_state", 0xD80C, 1),)

SLOW_READS = (
    ("cut_recovery_pending", 0xE848, 1),
    ("cut_state_and_recovery", 0xE847, 2),
    ("cut_state_and_recovery_repeat", 0xE847, 2),
    ("recovery_neighbors", 0xE840, 16),
    ("dtc8_record", 0xEA26, 12),
    ("dtc8_branch", 0xEA0E, 1),
    ("dtc100_record", 0xEC2A, 12),
    ("self_test_live", 0xEA0C, 2),
    ("self_test_scheduler", 0xE655, 1),
    ("boot_scratch_support_only", 0xE732, 2),
    ("softbsl_phase", 0xE740, 1),
    ("native_resume_state", 0xF015, 2),
    ("effective_soft_rpm", 0xF014, 1),
    ("native_fuel_limiter", 0xFD13, 1),
    ("launch_armed_latch", 0xFDB6, 1),
    ("native_thresholds", 0xDB86, 2),
    ("native_state_flags", 0xFD44, 2),
    ("legacy_launch_state", 0xFD5A, 1),
    ("input_82_latch", 0xFD60, 1),
    ("input_80_81_latch", 0xFD61, 1),
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
    ("ignition_cals", 0x12A65, 7),
    ("launch_cals", 0x147E0, 16),
    ("front_o2_b1", 0xFA9A, 2),
    ("front_o2_b2", 0xFA98, 2),
    ("knock_selected_cells", 0xE9BA, 6),
    ("knock_retained_channels", 0xE9C0, 12),
    ("knock_channel_histories", 0xE9CC, 6),
    ("knock_global_history", 0xE9D9, 1),
    ("knock_fault_flags", 0xFD30, 4),
    *((f"knock_learned_row_{index + 1}", 0xD840 + 64 * index, 64) for index in range(6)),
)


def _state_address(revision):
    address = cut_state_address(revision)
    if revision in ("V17", "V18") and address != 0xD801:
        raise RuntimeError("V17/V18 recording requires an updated app/core; update the application and try again")
    return address


def _batch_layout_for_revision(layout, revision):
    base = _state_address(revision)
    addresses = {"cut_state": base, "cut_recovery_pending": base + 1,
                 "timing_state_source": base + 2}
    resolved = tuple((name, addresses.get(name, address), length)
                     for name, address, length in layout)
    # Recovery used the input slots for WBO/recovery state. Keep those values
    # and restore the switch inputs within the 49-entry native telegram limit.
    if any(name == "cut_recovery_pending" for name, _address, _length in resolved):
        names = {name for name, _address, _length in resolved}
        resolved += tuple(item for item in (("input_82", 0xFD60, 1),
                                           ("input_80_81", 0xFD61, 1))
                          if item[0] not in names)
    return resolved


def _batch_entries(layout):
    """Read odd-address words as two bytes; native word descriptors require alignment."""
    entries = []
    for _name, address, length in layout:
        if length == 2 and address & 1:
            entries.extend(((address, 1), (address + 1, 1)))
        else:
            entries.append((address, length))
    return entries


def _slow_reads_for_revision(revision):
    if revision not in ("V16", "V17", "V18"):
        return SLOW_READS
    addresses = {"cut_recovery_pending": 0xD802,
                 "cut_state_and_recovery": 0xD801,
                 "cut_state_and_recovery_repeat": 0xD801,
                 "recovery_neighbors": 0xD800}
    return tuple((name, addresses.get(name, address), length)
                 for name, address, length in SLOW_READS) + (
        (("knock_guard", 0xD805, 7),) if revision in ("V17", "V18") else ()) + (
        (("fixed_ipw_state", 0xD80C, 1),) if revision == "V18" else ())

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
    0x1000: "segment-progression check or ASC1 command 0x103 timeout",
    0x2000: "watchdog reset",
}

# BEGIN V10 PROBES
V10_PATCH_PROBES = (
    ("ignition_cut_v10_3d92a_hook", 0x3D92A, bytes.fromhex("da0370dc")),
    ("ignition_cut_v10_3d98e_hook", 0x3D98E, bytes.fromhex("da0370dc")),
    ("ignition_cut_v10_2755a_hook", 0x2755A, bytes.fromhex("da0300e6")),
    ("ignition_cut_v10_2bf10_hook", 0x2BF10, bytes.fromhex("fa0310e2")),
    ("ignition_cut_v10_2c53c_hook", 0x2C53C, bytes.fromhex("fa032ee2")),
    ("ignition_cut_v10_2bd32_hook", 0x2BD32, bytes.fromhex("fa0350e2")),
    ("ignition_cut_v10_2c0b4_hook", 0x2C0B4, bytes.fromhex("fa036ee2")),
    ("ignition_cut_v10_2c30a_hook", 0x2C30A, bytes.fromhex("fa038ce2")),
    ("ignition_cut_v10_3553e_hook", 0x3553E, bytes.fromhex("fa03aae2")),
    ("ignition_cut_v10_2b5a6_hook", 0x2B5A6, bytes.fromhex("fa03c2e2")),
    ("ignition_cut_v10_2b05c_hook", 0x2B05C, bytes.fromhex("fa03e6e2")),
    ("ignition_cut_v10_2b11e_hook", 0x2B11E, bytes.fromhex("fa03fee2")),
    ("ignition_cut_v10_3021e_hook", 0x3021E, bytes.fromhex("fa0316e3")),
    ("ignition_cut_v10_30468_hook", 0x30468, bytes.fromhex("fa033ee3")),
    ("ignition_cut_v10_2b258_hook", 0x2B258, bytes.fromhex("fa0362e3")),
    ("ignition_cut_v10_2b054_hook", 0x2B054, bytes.fromhex("da0300e5")),
    ("ignition_cut_v10_2b0c2_hook", 0x2B0C2, bytes.fromhex("da0300e5")),
    ("ignition_cut_v10_2b116_hook", 0x2B116, bytes.fromhex("da032ce5")),
    ("ignition_cut_v10_2b188_hook", 0x2B188, bytes.fromhex("da032ce5")),
    ("ignition_cut_v10_291e2_hook", 0x291E2, bytes.fromhex("fa0358e5")),
)
# END V10 PROBES

# V11 preserves the operand that V10 mistook for an instruction boundary.
V11_PATCH_PROBES = tuple(
    (name.replace("v10_", "v11_"), address, expected)
    for name, address, expected in V10_PATCH_PROBES if address != 0x2B5A6
) + (("ignition_cut_v11_2b5a6_stock", 0x2B5A6, bytes.fromhex("d633c2fd")),)

# These probes identify the installed runtime layout, not full-ROM integrity.
V14_PATCH_PROBES = tuple(
    (name.replace("v11_", "v14_"), address, {
        0x30468: bytes.fromhex("fa0346e3"),
        0x2B258: bytes.fromhex("fa036ae3"),
        0x2B116: bytes.fromhex("da032ee5"),
        0x2B188: bytes.fromhex("da032ee5"),
        0x291E2: bytes.fromhex("fa035ce5"),
    }.get(address, expected))
    for name, address, expected in V11_PATCH_PROBES
) + (
    ("ignition_cut_v14_timing_a", 0x05D58, bytes.fromhex("da0300e7cc00")),
    ("ignition_cut_v14_timing_b", 0x05E2C, bytes.fromhex("da0300e7cc00")),
    ("ignition_cut_v14_control", 0x3E600, bytes.fromhex("ec88be88ec00ecf5e6000400da03b0dc")),
    ("ignition_cut_v14_timing", 0x3E700, bytes.fromhex("ecf4da0300e847f83f00ed07e6f2ff00")),
    ("ignition_cut_v14_qualification", 0x3E740, bytes.fromhex(
        "ec88ecf4ecf5ecf747fdc1003d1af2f430fd66f47f003d15f2f432fd66f400c03d10f3f8d9e947f8")),
    ("ignition_cut_v14_state_entry", 0x3E800, bytes.fromhex("fa0300eacc00cc00cc00fa030aea")),
    ("ignition_cut_v14_state", 0x3EA00, bytes.fromhex("ec88ecf7e0070d04cc00ec88ecf7e017")),
    ("ignition_cut_v14_gate", 0x3DC70, bytes.fromhex("ecf4f3f847e867f8f00047f8a0003d06")),
)
_v14_launch_state_probe = cut_program_state_probe("launch_control_v14")
V14_LAUNCH_PROBES = (
    ("launch_control_v14_stock_prologue", 0x207D2, bytes.fromhex("fe2dfe11")),
    ("launch_control_v14_soft_hook", 0x207E8, bytes.fromhex("fa0300dd")),
    ("launch_control_v14_hyst_a", 0x207F4, bytes.fromhex("da0320de")),
    ("launch_control_v14_hyst_b", 0x20816, bytes.fromhex("da03bcdd")),
    ("launch_control_v14_hard_a", 0x2088A, bytes.fromhex("da0340e1")),
    ("launch_control_v14_hard_b", 0x2092A, bytes.fromhex("da0340e1")),
    ("launch_control_v14_marker", 0x3DDBA, bytes.fromhex("0ef2")),
    ("launch_control_v14_retired_soft", 0x3E100, bytes.fromhex("0df2ffffffffffffffffffffffffffff")),
    ("launch_control_v14_hook", 0x39928, bytes.fromhex("fa0380df")),
    ("launch_control_v14_revision", 0x3DDEC, bytes.fromhex("2d01f0d4fcf4fc00fc88f0dddb00ffff")),
) + ((("launch_control_v14_state_operand", *_v14_launch_state_probe),)
     if _v14_launch_state_probe else ())

V16_PATCH_PROBES = tuple(
    (name.replace("v14_", "v16_"), address,
     expected.replace(bytes.fromhex("47e8"), bytes.fromhex("01d8"))
     if name.endswith("_gate") else expected)
    for name, address, expected in V14_PATCH_PROBES
)
_v16_launch_state_probe = cut_program_state_probe("launch_control_v16")
V16_LAUNCH_PROBES = tuple(
    (name.replace("v14_", "v16_"), address, expected)
    for name, address, expected in V14_LAUNCH_PROBES if not name.endswith("_state_operand")
) + ((("launch_control_v16_state_operand", *_v16_launch_state_probe),)
     if _v16_launch_state_probe else ())

# Exact V17 provenance code and every native hook into it.
V17_KNOCK_PROBES = (
    ("ignition_cut_v17_knock_3f000", 0x3F000, bytes.fromhex(
        "ec88be88ecf4e004f6f406d8f6f408d8f6f40ad8e7f8a700f7f805d8fcf4fc88db00f3f805d847f8a7002d02da0300f0"
        "db00da0322f0f3f801d867f8f00047f8a0003d0df3f801d869873d05f3f803d847f8c3003d04da0300f0e014db00e004"
        "db00ec88be88ecf4ecf5da0332f03d0cf3f808d849813d08c2f480fa4845ed04e0154c54f7fa07d8fcf5fcf4fc88db00"
        "ec88be88ecf4da0332f03d07f3f807d875f806d8e108f7f807d8fcf4fc88db00ec88be88ecf4da0332f03d07f3f806d8")),
    ("ignition_cut_v17_knock_3f0c0", 0x3F0C0, bytes.fromhex(
        "47f83f003d02e1180d01e108f7f809d8f3f80bd847f83f002d03e1c8f7f8dff6fcf4fc88e6f1def6db00ec88be88ecf4"
        "da0332f0e108f7f80ad8f2f6def649d5ed07f3f809d8f7f80ad8e118f7f808d8fcf4fc88f066db00ec88be88ecf4ecf5"
        "da0332f0c2f1e1f6f7f293f44815ed12f3f4e0f6e44176fcec88e0154c51f3f80ad849813d0375fa0bd80d03915065fa"
        "0bd80d01ec88fcf466f4fff7f2f512fed455040066f500087045f2f512fec4450400fcf5fcf4fc88fa03124fecf4ecf5")),
    ("ignition_cut_v17_knock_3f180", 0x3F180, bytes.fromhex(
        "da0332f03d0ce0154c56f3f80bd8618a2d06fcf5fcf4f0465c14fa02bca7f2f41cfd68442d0d8a1917e08a1915f08a2f"
        "13808a2f11a0f2f40cea66f400103d0cfcf5fcf4e6f4fffb64f41cfdf4e6cce9c0e75c87fa024cadfcf5fcf4fa0200ac"
        "ec88ecf4da0332f03d0ef3f80bd847f83f003d09f2f41cfd66f400042d04fcf4fc88fa021ca2fcf4fc88fa02aca243f0"
        "90fa3d054fe2da0362f0fa004c4343f091fa3d034ee2da0390f0fa004c4343f090fa3d054fe2da0362f0fa00725143f0")),
    ("ignition_cut_v17_knock_3f240", 0x3F240, bytes.fromhex("91fa3d034ee2da0390f0fa007251")),
    ("ignition_cut_v17_knock_0433a", 0x0433A, bytes.fromhex("fa030ef2")),
    ("ignition_cut_v17_knock_05160", 0x05160, bytes.fromhex("fa032ef2")),
    ("ignition_cut_v17_knock_3d568", 0x3D568, bytes.fromhex("da03b0f0")),
    ("ignition_cut_v17_knock_043d4", 0x043D4, bytes.fromhex("da03eaf0")),
    ("ignition_cut_v17_knock_0448c", 0x0448C, bytes.fromhex("da03eaf0")),
    ("ignition_cut_v17_knock_04542", 0x04542, bytes.fromhex("da03eaf0")),
    ("ignition_cut_v17_knock_04822", 0x04822, bytes.fromhex("da03eaf0")),
    ("ignition_cut_v17_knock_048da", 0x048DA, bytes.fromhex("da03eaf0")),
    ("ignition_cut_v17_knock_04990", 0x04990, bytes.fromhex("da03eaf0")),
    ("ignition_cut_v17_knock_051b2", 0x051B2, bytes.fromhex("da03eaf0")),
    ("ignition_cut_v17_knock_05248", 0x05248, bytes.fromhex("da03eaf0")),
    ("ignition_cut_v17_knock_052de", 0x052DE, bytes.fromhex("da03eaf0")),
    ("ignition_cut_v17_knock_0538a", 0x0538A, bytes.fromhex("da03eaf0")),
    ("ignition_cut_v17_knock_05420", 0x05420, bytes.fromhex("da03eaf0")),
    ("ignition_cut_v17_knock_054b4", 0x054B4, bytes.fromhex("da03eaf0")),
    ("ignition_cut_v17_knock_34efe", 0x34EFE, bytes.fromhex("fa0318f1")),
    ("ignition_cut_v17_knock_2a7b8", 0x2A7B8, bytes.fromhex("fa037cf1")),
    ("ignition_cut_v17_knock_2a218", 0x2A218, bytes.fromhex("fa03e0f1")),
)

V17_PATCH_PROBES = tuple(
    (name.replace("v16_", "v17_"), address, {
        0x3DC70: bytes.fromhex(
            "ecf4f3f801d867f8f00047f8a0003d06da0360e6f3f801d869833d04fcf465f204ffdb00da0300f0e7f81f0075f802d8fcf4db00"
            "cc00cc00cc00cc00cc00cc00f3fa01d867faf00047faa0002d0ce6f500c0f6f502d8f7fa04d8da0300f0e7faa000f7fa01d8db00"),
        0x3E700: bytes.fromhex(
            "ecf4da0300e847f83f00ed0ce6f0ff00210840209d01f020da0300f0e7f81f0075f802d8fcf4f00267f00f00db00"),
        0x3E740: bytes.fromhex(
            "ec88ecf447fdc1003d0cf2f430fd66f47f003d07f2f432fd66f400c03d02e7fdc300fcf4fc88db00"),
    }.get(address, expected))
    for name, address, expected in V16_PATCH_PROBES
) + V17_KNOCK_PROBES
V17_LAUNCH_PROBES = tuple(
    (name.replace("v16_", "v17_"), address,
     bytes.fromhex("2d01f0d4fcf4fc00fc88f0dddb00cc00") if name.endswith("_revision") else expected)
    for name, address, expected in V16_LAUNCH_PROBES
)

# BEGIN V18 PROBES
# Generated from the V18 descriptors; calibration values are read separately.
V18_PATCH_PROBES = (
    ("ignition_cut_v18_3d92a", 0x3D92A, bytes.fromhex(
        "da0370dc"
    )),
    ("ignition_cut_v18_3d98e", 0x3D98E, bytes.fromhex(
        "da0370dc"
    )),
    ("ignition_cut_v18_2755a", 0x2755A, bytes.fromhex(
        "da0300e6da0300f4fa02ea75"
    )),
    ("ignition_cut_v18_3dea0", 0x3DEA0, bytes.fromhex(
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    )),
    ("ignition_cut_v18_3df20", 0x3DF20, bytes.fromhex(
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    )),
    ("ignition_cut_v18_2bf10", 0x2BF10, bytes.fromhex(
        "fa0310e2"
    )),
    ("ignition_cut_v18_2c86e", 0x2C86E, bytes.fromhex(
        "c4861800"
    )),
    ("ignition_cut_v18_2c89c", 0x2C89C, bytes.fromhex(
        "c4471000"
    )),
    ("ignition_cut_v18_3553e", 0x3553E, bytes.fromhex(
        "fa03aae2"
    )),
    ("ignition_cut_v18_2b05c", 0x2B05C, bytes.fromhex(
        "fa03e6e2"
    )),
    ("ignition_cut_v18_2b11e", 0x2B11E, bytes.fromhex(
        "fa03fee2"
    )),
    ("ignition_cut_v18_36248", 0x36248, bytes.fromhex(
        "e6fc46ee"
    )),
    ("ignition_cut_v18_36274", 0x36274, bytes.fromhex(
        "e6fc52ee"
    )),
    ("ignition_cut_v18_3025c", 0x3025C, bytes.fromhex(
        "f3f83cfc"
    )),
    ("ignition_cut_v18_3e1a0", 0x3E1A0, bytes.fromhex(
        "f3f801d867f8f00047f8a0003d08f3f801d869872d03e1f875f802d8db00e004db00da03a2e33d0ff3f801d867f8f000"
        "47f8a0003df4f3f802d846f518f03d0269810d016982db00da03a2e33d10f3f801d867f8f00047f8a0003de1f3f802d8"
        "46f518f03d0269850d0267f80a00db00ecf4ecf5f05eda03c2e12d03fcf5fcf4"
    )),
    ("ignition_cut_v18_3e220", 0x3E220, bytes.fromhex(
        "db00fcf5fcf488d08880fa0214bf8a180b10ecf4ecf5f05cda03c2e12d03fcf5fcf4db00fcf5fcf488608880fa0240c5"
        "ecf4ecf5f05cda03c2e12d03fcf5fcf4db00fcf5fcf488608890fa0236bdecf4ecf5f05cda03c2e12d03fcf5fcf4db00"
        "fcf5fcf488608890fa02b8c0ecf4ecf5f05cda03c2e12d03fcf5fcf4db00fcf5"
    )),
    ("ignition_cut_v18_3e2a0", 0x3E2A0, bytes.fromhex(
        "fcf488808890fa020ec3ecf4da03a0e12d03fcf4fa032e58fcf4f3fa34fcfa0342558a180c10ecf4ecf5f059da03c2e1"
        "2d04fcf5fcf4fa02a8b6fcf5fcf4f4893300fa02aab5ecf4da03a2e32d03fcf4fa02bcb0fcf4e6f4c4f0fa0260b0ecf4"
        "da03a2e32d03fcf4fa027eb1fcf4e6f418f0fa0222b1ecf4da03bae72d0fe6f4"
    )),
    ("ignition_cut_v18_3e320", 0x3E320, bytes.fromhex(
        "0202f6f43af5f6f43cf5f6f43ef5e7f8ef0065f802d8fcf4fa037003fcf4f489d4abfa032202ecf4da03bae73d0bf489"
        "f2abc084f4843af53d05fcf4c2f45af5fa036c04fcf4fa03da048a181410ecf4ecf5f059da03c2e12d0cd4490200f6f4"
        "0efdd4490400f6f410fdfcf5fcf4fa0222b4fcf5fcf48a0a0200fa025cb2fa02"
    )),
    ("ignition_cut_v18_3e3a0", 0x3E3A0, bytes.fromhex(
        "9ab2da03a0e13d14f3f801d867f8f00047f8a0003d0ef3f803d847f8c3003d09da030ae847f8c3003d04e1f875f802d8"
        "db00e004db00"
    )),
    ("ignition_cut_v18_2c53c", 0x2C53C, bytes.fromhex(
        "fa032ee2"
    )),
    ("ignition_cut_v18_2bd32", 0x2BD32, bytes.fromhex(
        "fa0350e2"
    )),
    ("ignition_cut_v18_2c0b4", 0x2C0B4, bytes.fromhex(
        "fa036ee2"
    )),
    ("ignition_cut_v18_2c30a", 0x2C30A, bytes.fromhex(
        "fa038ce2"
    )),
    ("ignition_cut_v18_2b5a6", 0x2B5A6, bytes.fromhex(
        "d633c2fd"
    )),
    ("ignition_cut_v18_3021e", 0x3021E, bytes.fromhex(
        "fa0316e3"
    )),
    ("ignition_cut_v18_30468", 0x30468, bytes.fromhex(
        "fa0346e3"
    )),
    ("ignition_cut_v18_2b258", 0x2B258, bytes.fromhex(
        "fa036ae3"
    )),
    ("ignition_cut_v18_2b054", 0x2B054, bytes.fromhex(
        "da0300e5"
    )),
    ("ignition_cut_v18_2b0c2", 0x2B0C2, bytes.fromhex(
        "da0300e5"
    )),
    ("ignition_cut_v18_2b116", 0x2B116, bytes.fromhex(
        "da032ee5"
    )),
    ("ignition_cut_v18_2b188", 0x2B188, bytes.fromhex(
        "da032ee5"
    )),
    ("ignition_cut_v18_291e2", 0x291E2, bytes.fromhex(
        "fa035ce5"
    )),
    ("ignition_cut_v18_3e500", 0x3E500, bytes.fromhex(
        "ec88be88ecf4da0390e73d0ee7f8fe0046f918f02d02e7f8fd0065f802d8fcf4fc88e4c92b00db00fcf4fc88db00ec88"
        "be88ecf4da0390e73d0ee7f8fb0046f918f02d02e7f8f70065f802d8fcf4fc88e4894200db00fcf4fc88db00ecf4ecf5"
        "f059da03e8e12d23e004e4894000c4495e00c4496000c4496200c4496400d449"
    )),
    ("ignition_cut_v18_3e580", 0x3E580, bytes.fromhex(
        "6e0042f46800ed0fe004c4496600c4496800c4496a00c4496c00c4496e00fcf5fcf4fa027694fcf5fcf4fa02e093fcf5"
        "fcf48a080220fa02da93fa02ea91"
    )),
    ("ignition_cut_v18_3e790", 0x3E790, bytes.fromhex(
        "da03a0e13d0ff3f801d867f8f00047f8a0003d09f3f803d847f8c3003d04e1f875f802d8db00e004db00da03a2e33d0b"
        "f3f801d867f8f00047f8a0003df4f3f802d867f81000db00"
    )),
    ("ignition_cut_v18_05d58", 0x05D58, bytes.fromhex(
        "da0300e7cc00"
    )),
    ("ignition_cut_v18_05e2c", 0x05E2C, bytes.fromhex(
        "da0300e7cc00"
    )),
    ("ignition_cut_v18_3e600", 0x3E600, bytes.fromhex(
        "ec88be88ec00ecf5e6000400da03b0dcf3fa01d869a72d03e1fa75fa02d8fcf5fc00fc88f2f47cefdb00cc00cc00cc00"
        "cc00cc00cc00cc00cc00cc00cc00cc00cc00cc00cc00cc00cc00cc00cc00cc00cc00cc00cc00cc00cc00cc00cc00cc00"
        "ec00ecf5ecf6e6000400f3fa652a47faff002d3849a02d1549a13d05f3fa61fd"
    )),
    ("ignition_cut_v18_3e680", 0x3E680, bytes.fromhex(
        "69a22d300d0e49a23d05f3fa61fd69a12d290d0749a43d26f3fa60fd67fa80002d21f3fa01d869a12d14f3fa672a47fa"
        "ff002d07f3fc662a41ca8d032d0221ca0d03f3fc662a0d00f3fa3cfc41ac8d0a0d05f3fa3cfc43fa662a8d04e11a75fa"
        "01d80d04e7fafe0065fa01d8fcf6fcf5fc00db00"
    )),
    ("ignition_cut_v18_3e700", 0x3E700, bytes.fromhex(
        "ecf4da0300e847f83f00ed0de6f0ff00210840209d03f020da0336f0e7f81f0075f802d80d02da034ef0fcf4f00267f0"
        "0f00db00"
    )),
    ("ignition_cut_v18_3e740", 0x3E740, bytes.fromhex(
        "ec88ecf447fdc1003d0cf2f430fd66f47f003d07f2f432fd66f400c03d02e7fdc300fcf4fc88db00ffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    )),
    ("ignition_cut_v18_3e800", 0x3E800, bytes.fromhex(
        "fa0300eacc00cc00cc00fa030aea"
    )),
    ("ignition_cut_v18_3ea00", 0x3EA00, bytes.fromhex(
        "ec88ecf7e0070d04cc00ec88ecf7e017ec00ecf4ecf5ecf6ecf3e6000400f3fc03d8e7fdc000e6f3ff00f3f801d867f8"
        "f80047f8a000ea3038ecf3fa3cfc43fa87dbea9038ece6000800f2f4e80746f4fa03ea309cebf2f4ea0746f400ddea30"
        "9cebe6000400f3f8b6fd67f84000ea20a0ebf3f89af143f8e547ea90a0ebf3f8"
    )),
    ("ignition_cut_v18_3ea80", 0x3EA80, bytes.fromhex(
        "d0e843f8e647ea80a0ebf3f8e0474980ea20e2ea4981ea30a4eaf3f861fd6982ea00c2ea4982ea30b4eaf3f861fd6981"
        "ea00c2ea4984ea30a0ebf3f860fd67f88000f3fae24749a0ea20dcea49a1ea30a0eb4980ea30a0ebea00e2ea4980ea20"
        "a0ebf3f8e1474980ea200ceb4981ea30a0ebf3f6ef4747f63f00eae0a0ebf3f8"
    )),
    ("ignition_cut_v18_3eb00", 0x3EB00, bytes.fromhex(
        "e347f3faee47e127ea00feebf3f6ed4747f63f00eae0a0ebf3fa3cfcf3f8e34743f814f0eae0a0ebea2034eb43fa14f0"
        "ea90a0ebf3fae74747faff00ea3056ebf3fae34747fafd00ea8054ebe7faff00ea0056eb09a343fa87dbea8062ebf3fa"
        "87db41a8ea906aebf18af3fa3cfc41a8ea9096eb67fcfd0047fcc100ea30a0eb"
    )),
    ("ignition_cut_v18_3eb80", 0x3EB80, bytes.fromhex(
        "f3f804d84981ea30a0ebf3f813fd67f88000ea20a0ebe117ea0026ece6000400e6f3ff00f3f8652a4980ea20e8eb4981"
        "ea30c2ebf3f861fd6982ea2038ecea00e8eb4982ea30d6ebf3f861fd6981ea2038ecea00e8eb4984ea3038ecf3f860fd"
        "67f88000ea2038ecf3f66b2a47f63f00eae038ecf3f8662af3fa6a2ae13749a0"
    )),
    ("ignition_cut_v18_3ec00", 0x3EC00, bytes.fromhex(
        "ea202eec41a8ea902eec43f83cfceaf02eec218af3fa3cfc41a8ea802eec43fa14f0ea902eece7fdc100ea0038ec4972"
        "ea20a0ebe6f3ff00da0340e749e1ea205eece7ffff0047fdc300ea3050ecf1f6be88f7f704d8f7fd03d8ea0084ece7ff"
        "c00047fdc300ea3084ecf3f803d847f8c300ea3084ecf3f804d84187ea3084ec"
    )),
    ("ignition_cut_v18_3ec80", 0x3EC80, bytes.fromhex(
        "e7ffc300fcf3fcf6fcf5fcf4fc00f18ffcf7fc88db00"
    )),
    ("ignition_cut_v18_3dc70", 0x3DC70, bytes.fromhex(
        "ecf4f3f801d867f8f00047f8a0003d06da0360e6f3f801d869833d04fcf465f204ffdb00da0300f0e7f81f0075f802d8"
        "fcf4db00cc00cc00cc00cc00cc00cc00f3fa01d867faf00047faa0002d0ee6f500c0f6f502d8f7fa04d8da0300f0e7fa"
        "a000f7fa0cd8f7fa01d8db00"
    )),
    ("ignition_cut_v18_3f000", 0x3F000, bytes.fromhex(
        "ec88be88ecf4e004f6f406d8f6f408d8f6f40ad8f3f805d847f8e7003d05f3f803d847f8c3002d04e7f8a700f7f805d8"
        "fcf4fc88db00ec88be88da0300f0ecf4e7f8e700f7f805d8fcf4fc88db00ec88be88ecf4f3f803d847f8c3002d09f3f8"
        "05d847f8e7003d04e7f8a700f7f805d8fcf4fc88db00f3f805d867f8bf0047f8"
    )),
    ("ignition_cut_v18_3f080", 0x3F080, bytes.fromhex(
        "a7002d02da0300f0db00ec88be88da0376f0f3f801d867f8f00047f8a0003d12f3f801d869873d0af3f803d847f8c300"
        "3d09f3f805d847f8e7003d08da0300f0e0140d05e7f8a700f7f805d8e004fc884840db00ec88be88ecf4ecf5da038af0"
        "3d0cf3f808d849813d08c2f480fa4845ed04e0154c54f7fa07d8fcf5fcf4fc88"
    )),
    ("ignition_cut_v18_3f100", 0x3F100, bytes.fromhex(
        "db00ec88be88ecf4da038af03d07f3f807d875f806d8e108f7f807d8fcf4fc88db00ec88be88ecf4da038af03d07f3f8"
        "06d847f83f003d02e1180d01e108f7f809d8f3f80bd847f83f002d03e1c8f7f8dff6fcf4fc88e6f1def6db00ec88be88"
        "ecf4da038af0e108f7f80ad8f2f6def649d5ed07f3f809d8f7f80ad8e118f7f8"
    )),
    ("ignition_cut_v18_3f180", 0x3F180, bytes.fromhex(
        "08d8fcf4fc88f066db00ec88be88ecf4ecf5da038af0c2f1e1f6f7f293f44815ed12f3f4e0f6e44176fcec88e0154c51"
        "f3f80ad849813d0375fa0bd80d03915065fa0bd80d01ec88fcf466f4fff7f2f512fed455040066f500087045f2f512fe"
        "c4450400fcf5fcf4fc88fa03124fecf4ecf5da038af03d0ce0154c56f3f80bd8"
    )),
    ("ignition_cut_v18_3f200", 0x3F200, bytes.fromhex(
        "618a2d06fcf5fcf4f0465c14fa02bca7f2f41cfd68442d0d8a1917e08a1915f08a2f13808a2f11a0f2f40cea66f40010"
        "3d0cfcf5fcf4e6f4fffb64f41cfdf4e6cce9c0e75c87fa024cadfcf5fcf4fa0200acec88ecf4da038af03d0ef3f80bd8"
        "47f83f003d09f2f41cfd66f400042d04fcf4fc88fa021ca2fcf4fc88fa02aca2"
    )),
    ("ignition_cut_v18_3f280", 0x3F280, bytes.fromhex(
        "43f090fa3d054fe2da03d4f0fa004c4343f091fa3d034ee2da0302f1fa004c4343f090fa3d054fe2da03d4f0fa007251"
        "43f091fa3d034ee2da0302f1fa007251"
    )),
    ("ignition_cut_v18_0433a", 0x0433A, bytes.fromhex(
        "fa0380f2"
    )),
    ("ignition_cut_v18_05160", 0x05160, bytes.fromhex(
        "fa03a0f2"
    )),
    ("ignition_cut_v18_3d568", 0x3D568, bytes.fromhex(
        "da0322f1"
    )),
    ("ignition_cut_v18_043d4", 0x043D4, bytes.fromhex(
        "da035cf1"
    )),
    ("ignition_cut_v18_0448c", 0x0448C, bytes.fromhex(
        "da035cf1"
    )),
    ("ignition_cut_v18_04542", 0x04542, bytes.fromhex(
        "da035cf1"
    )),
    ("ignition_cut_v18_04822", 0x04822, bytes.fromhex(
        "da035cf1"
    )),
    ("ignition_cut_v18_048da", 0x048DA, bytes.fromhex(
        "da035cf1"
    )),
    ("ignition_cut_v18_04990", 0x04990, bytes.fromhex(
        "da035cf1"
    )),
    ("ignition_cut_v18_051b2", 0x051B2, bytes.fromhex(
        "da035cf1"
    )),
    ("ignition_cut_v18_05248", 0x05248, bytes.fromhex(
        "da035cf1"
    )),
    ("ignition_cut_v18_052de", 0x052DE, bytes.fromhex(
        "da035cf1"
    )),
    ("ignition_cut_v18_0538a", 0x0538A, bytes.fromhex(
        "da035cf1"
    )),
    ("ignition_cut_v18_05420", 0x05420, bytes.fromhex(
        "da035cf1"
    )),
    ("ignition_cut_v18_054b4", 0x054B4, bytes.fromhex(
        "da035cf1"
    )),
    ("ignition_cut_v18_34efe", 0x34EFE, bytes.fromhex(
        "fa038af1"
    )),
    ("ignition_cut_v18_2a7b8", 0x2A7B8, bytes.fromhex(
        "fa03eef1"
    )),
    ("ignition_cut_v18_2a218", 0x2A218, bytes.fromhex(
        "fa0352f2"
    )),
    ("ignition_cut_v18_3f400", 0x3F400, bytes.fromhex(
        "ecf1ecf2ecf3ecf6ecf7ecf8ecf9ecfaecfbecfeecffec00e6000400ec88be88c2f101d86813f3f380faf2f28ef6f2f3"
        "74faf18269822d05f3fae947f3fbea470d08f1826981ea20e4f4f3fa682af3fb692a46f5ffffea20e4f4f2f45efd66f4"
        "00a446f40004ea30ecf4f2f402fd66f40800ea30ecf4f3f881faea30ecf44935"
    )),
    ("ignition_cut_v18_3f480", 0x3F480, bytes.fromhex(
        "eae0ecf4f3f8a0fc4985eae0ecf4f2f450ff66f4004f46f40043ea30ecf4f2f446ff66f4cf0046f44100ea30ecf44830"
        "ea20ecf442f346feeaf0ecf4f0e5f0f5f0537c352052ea80ecf44850ea20ecf42851f0455c14084140e4eae0ecf4fc88"
        "ea0012f5fc88e6f1a0000d03fc88e6f1a200f3f80cd847f8a1002d04f7f20cd8"
    )),
    ("ignition_cut_v18_3f500", 0x3F500, bytes.fromhex(
        "ea0074f6e003f2fe7eeff2ff80eff2f28ef6f0cec2fd28dc5c4d06fd0008da0390f6ea80eef4f064f0cfc2fd29dc5c4d"
        "06fd0008da0390f6ea80eef4f074f0cec2fd2adc5c4d06fd0008da0390f6ea80eef4f084f0cfc2fd2bdc5c4d06fd0008"
        "da0390f6ea80eef4f094f0cec2fd2cdc5c4d06fd0008da0390f6ea80eef4f0a4"
    )),
    ("ignition_cut_v18_3f580", 0x3F580, bytes.fromhex(
        "f0cfc2fd2ddc5c4d06fd0008da0390f6ea80eef4f0b4ec88be884830ea202af642f374faea306ef642f28ef6ea306ef6"
        "43f380faea306ef6f3f801d869834182ea306ef6f2f45efd66f400a446f40004ea306ef6f2f402fd66f40800ea306ef6"
        "f3f881faea306ef64935eae06ef6f3f8a0fc4985eae06ef6f2f450ff66f4004f"
    )),
    ("ignition_cut_v18_3f600", 0x3F600, bytes.fromhex(
        "46f40043ea306ef6f2f446ff66f4cf0046f44100ea306ef64830ea206ef642f346feeaf06ef6e6f1a100f6fe7eeff6ff"
        "80eff04e00429d02e6f4fffff6f420fcf04f00429d02e6f4fffff6f422fcf6f628fcf6f72afcf6f82cfcf6f92efcf6fa"
        "30fcf6fb32fcf7f20cd8fc880d03fc88ea00eef4fc00fcfffcfefcfbfcfafcf9"
    )),
    ("ignition_cut_v18_3f680", 0x3F680, bytes.fromhex(
        "fcf8fcf7fcf6fcf3fcf2fcf1ea00c6f61bcdf2f40cfe46f400108d03e6f4ffff0d055c44f2fd0efe7ccd704d48302d06"
        "4045ed0748403d0248202d03204c1e88db001f88db00f2f47ceff6f482efc2f5a0fcf485ceab3d14f2fc7eefc2f4a0fc"
        "f4a428dcc0ad5c4d06fd0008da03dc4622f47eefc2f5a0fc5c15c44528fc0d13"
    )),
    ("ignition_cut_v18_3f700", 0x3F700, bytes.fromhex(
        "f2fc80efc2f4a0fcf4a428dcc0ad5c4d06fd0008da03dc4622f480efc2f5a0fc5c15c44528fcf2fc7ceff2fd8ef6da03"
        "8444f6f47ceff2fc7eeff2fd8ef6da038444f6f420fcf2fc80eff2fd8ef6da038444f6f422fcdb00"
    )),
)
V18_LAUNCH_PROBES = (
    ("launch_control_v18_207d2", 0x207D2, bytes.fromhex(
        "fe2dfe11"
    )),
    ("launch_control_v18_207e8", 0x207E8, bytes.fromhex(
        "fa0300dd"
    )),
    ("launch_control_v18_207f4", 0x207F4, bytes.fromhex(
        "da0320de"
    )),
    ("launch_control_v18_20816", 0x20816, bytes.fromhex(
        "da03bcdd"
    )),
    ("launch_control_v18_2088a", 0x2088A, bytes.fromhex(
        "da0340e1"
    )),
    ("launch_control_v18_2092a", 0x2092A, bytes.fromhex(
        "da0340e1"
    )),
    ("launch_control_v18_3dd00", 0x3DD00, bytes.fromhex(
        "ec00ecf4ecf5e6000400f3f801d867f8f00047f8a0003d288a0904f0e7f8f70065f801d89a5b1b60f3f8e14749803d17"
        "f3f814f043f8e347fd08f3fa3cfc41a89d0443fa87db9d010d03e18a75fa01d8f3fae34741a89d08f7fa14f00d059a09"
        "03f0e18a75fa01d8fcf5fcf4fc008a0902f0fa022609fa02f007ffffffffffff"
    )),
    ("launch_control_v18_3dd80", 0x3DD80, bytes.fromhex(
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffff0ef2"
    )),
    ("launch_control_v18_3ddbc", 0x3DDBC, bytes.fromhex(
        "ec88ec00ecf4e6000400c2fdb9029a5b1160f3f8e14749803d0df3f801d867f8f80047f8a0003d06c2f4eb4746f4ff00"
        "2d01f0d4fcf4fc00fc88f0dddb00f044"
    )),
    ("launch_control_v18_3de20", 0x3DE20, bytes.fromhex(
        "ec88ec00ecf4e6000400c2fdba029a5b1160f3f8e14749803d0df3f801d867f8f80047f8a0003d06c2f4ec4746f4ff00"
        "2d01f0d4fcf4fc00fc88f0dddb00ffffffffffffffffffffffffffffffffffff"
    )),
    ("launch_control_v18_3df80", 0x3DF80, bytes.fromhex(
        "ec00ecf4ecf5ecf6e6000400f3fa01d867faf00047faa0002d04e7faa000f7fa01d8f3f8e04747f8ff002d31f3f89af1"
        "43f8e5479d2cf3f8d0e843f8e6478d27f3f89af143f8e4479d23f3f8e04749802d1c49813d04f3f861fd69820d0c4982"
        "3d04f3f861fd69810d0649843d10f3f860fd67f88000f3fae24749a02d034980"
    )),
    ("launch_control_v18_3e000", 0x3E000, bytes.fromhex(
        "2d040d0649803d010d036f5b0d016e5bf3f8b6fd67f840002d42f3f8e14749802d2c49813d3ce7f8fb0065f801d8f3f8"
        "01d869822d14f3f8e84747f8ff002d07f3fae34741a88d032d0221a80d03f3fae3470d00f3f83cfc418a8d0a0d05f3f8"
        "3cfc43f8e3478d04e12875f801d80d1be7f8fd0065f801d80d16e7f8fd0065f8"
    )),
    ("launch_control_v18_3e080", 0x3E080, bytes.fromhex(
        "01d8f3f813fd67f880002d04e14875f801d80d09e7f8fb0065f801d80d04e7f8f90065f801d8fcf6fcf5fcf4fc00f3f8"
        "9af1fa032c99"
    )),
    ("launch_control_v18_3e100", 0x3E100, bytes.fromhex(
        "0df2ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffff"
    )),
    ("launch_control_v18_3e140", 0x3E140, bytes.fromhex(
        "ec00ecf5e6000400f3fab6fd67fa40002d18f3fae14749a03d14f3fae74747faff003d0af3fae34747fafd008d03e7fa"
        "ff000d0209a30d0043fa87db9d02418a0d0243f887dbfcf5fc00db00ffffffffffffffffffff"
    )),
    ("launch_control_v18_39928", 0x39928, bytes.fromhex(
        "fa0380df"
    )),
)
# END V18 PROBES

PATCH_PROBES = V18_PATCH_PROBES + V18_LAUNCH_PROBES + V17_PATCH_PROBES + V17_LAUNCH_PROBES + V16_PATCH_PROBES + V16_LAUNCH_PROBES + V14_PATCH_PROBES + V14_LAUNCH_PROBES + V11_PATCH_PROBES + V10_PATCH_PROBES + (
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


def _read_exact(ds2, address: int, length: int, evidence=None) -> bytes:
    def read():
        data = bytes(ds2.read_mem(address, length))
        if len(data) != length:
            raise DS2Error(
                f"short read at 0x{address:05X}: {len(data)}/{length} bytes")
        return data

    if evidence is not None:
        evidence["started_utc"] = _utc_now()
    try:
        return _retry(f"read 0x{address:05X}", read, evidence)
    finally:
        if evidence is not None:
            evidence["completed_utc"] = _utc_now()


def _write_record(report, record) -> None:
    report.write(json.dumps(record, separators=(",", ":")) + "\n")
    report.flush()


@contextmanager
def _wire_capture(ds2, report):
    """Observe the existing parser's reads; never reconstruct a reply from data."""
    transport = getattr(ds2, "_ds2", ds2)  # Android API 1 owns the same DS2 object.
    execute = getattr(transport, "_execute", None)
    read = getattr(transport, "_read_exact", None)
    available = callable(execute) and callable(read)
    _write_record(report, {"type": "wire_capture", "available": available,
                          "scope": "batch polls and recovery RAM reads only"})
    if not available:
        yield False
        return

    active = None

    def traced_read(length, timeout):
        data = read(length, timeout)
        if active is not None:
            record, context = active
            phase = context.get("phase", "")
            if phase in ("response_header", "response_body"):
                record["response_chunks"].append({"phase": phase, "raw": data.hex()})
        return data

    def traced_execute(command, args, timeout, context, **kwargs):
        nonlocal active
        address = int.from_bytes(args[:4], "big") if command == 0x06 else None
        selected = ((command == 0x0B and args[:1] == b"\x00")
                    or (command == 0x06 and address is not None
                        and (0xE840 <= address <= 0xE84F
                             or 0xD800 <= address <= 0xD80C)))
        if not selected:
            return execute(command, args, timeout, context, **kwargs)
        record = {"type": "wire_exchange", "started_utc": _utc_now(),
                  "request_frame": transport._command_frame(command, args).hex(),
                  "response_chunks": [], "accepted": False}
        active = record, context
        try:
            result = execute(command, args, timeout, context, **kwargs)
            record["accepted"] = True
            return result
        except Exception as error:
            record["error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            active = None
            record["completed_utc"] = _utc_now()
            record["response_raw"] = "".join(item["raw"] for item in record["response_chunks"])
            record["phase"] = context.get("phase")
            _write_record(report, record)

    original_attributes = {name: transport.__dict__.get(name)
                           for name in ("_execute", "_read_exact")}
    transport._execute, transport._read_exact = traced_execute, traced_read
    try:
        yield True
    finally:
        for name, original in original_attributes.items():
            if original is None:
                transport.__dict__.pop(name, None)
            else:
                setattr(transport, name, original)


def _recovery_confirmation(ds2, report, elapsed_s, revision=None):
    """Three adjacent-byte reads after the first anomalous fast sample."""
    for index in range(3):
        evidence = {}
        record = {"type": "recovery_confirmation", "index": index,
                  "trigger_elapsed_s": elapsed_s,
                  "address": f"0x{_state_address(revision):04X}", "length": 4}
        try:
            record["raw"] = _read_exact(ds2, _state_address(revision), 4, evidence).hex()
        except (DS2Error, OSError) as error:
            record["error"] = f"{type(error).__name__}: {error}"
        record["read_evidence"] = evidence
        _write_record(report, record)


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


def _angle(raw: int) -> float | None:
    return round(raw * 0.375 - 23.625, 3) if raw <= 63 else None


def _decode_ignition_cals(raw: bytes, revision=None) -> dict:
    ipw = _le(raw[3:5])
    return {
        "switch": _selector(raw[0]),
        "rpm": raw[1] * 32,
        "hysteresis_rpm": 0 if raw[2] == 0xFF else raw[2] * 32,
        "fixed_ipw_mode": "stock" if ipw == 0xFFFF else "fixed",
        "fixed_ipw_ms": None if ipw == 0xFFFF else round(ipw * 0.00534, 4),
        **({"retard_range_rpm": raw[5] * 32,
            "fixed_angle_raw": f"0x{raw[6]:02X}",
            "fixed_angle_deg_btdc": _angle(raw[6])} if revision in ("V14", "V16", "V17", "V18") else {}),
    }


def _decode_launch_cals(raw: bytes, revision=None) -> dict:
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
        "min_tps_raw": raw[6],
        "throttle_comparison_scale": "raw * 0.47; distinct from normalized throttle percent",
        "hard_rpm": None if raw[7] == 0xFF else raw[7] * 32,
        "hard_rpm_mode": ("soft + 96, capped by native hard RPM" if raw[7] == 0xFF
                          else "independent, capped by native hard RPM")
                         if revision in ("V14", "V16", "V17", "V18") else
                         ("soft + 96" if raw[7] == 0xFF else "fixed"),
        "hysteresis_rpm": 0 if raw[8] == 0xFF else raw[8] * 32,
        "fixed_ipw_mode": "stock" if ipw == 0xFFFF else "fixed",
        "fixed_ipw_ms": None if ipw == 0xFFFF else round(ipw * 0.00534, 4),
        **({"fuel_hysteresis_b_rpm": None if raw[11] == 0xFF else raw[11] * 32,
            "fuel_hysteresis_a_rpm": None if raw[12] == 0xFF else raw[12] * 32,
            "fuel_hysteresis_default": "None means follow native calibration",
            "fuel_cut_angle_raw": f"0x{raw[13]:02X}",
            "fuel_cut_angle_deg_btdc": _angle(raw[13]),
            "retard_range_rpm": raw[14] * 32,
            "ignition_cut_angle_raw": f"0x{raw[15]:02X}",
            "ignition_cut_angle_deg_btdc": _angle(raw[15])}
           if revision in ("V14", "V16", "V17", "V18") else {}),
    }


def _patch_probe_snapshot(ds2) -> dict:
    probes = {}
    reads = {}
    for name, address, expected in PATCH_PROBES:
        key = address, len(expected)
        if key not in reads:
            reads[key] = _read_exact(ds2, *key)
        raw = reads[key]
        probes[name] = {
            "address": f"0x{address:05X}",
            "raw": raw.hex(),
            "expected": expected.hex(),
            "matches": raw == expected,
        }
    return probes


def _required_probe_failures(probes: dict) -> list[str]:
    profiles = tuple(
        tuple(name for name, _address, _expected in ignition_probes)
        + tuple(name for name in REQUIRED_RUNTIME_PROBES
                if name.startswith("launch_control_"))
        for ignition_probes in (V11_PATCH_PROBES, V10_PATCH_PROBES)
    ) + (REQUIRED_RUNTIME_PROBES,
         tuple(name for name, _address, _expected in V14_PATCH_PROBES + V14_LAUNCH_PROBES),
         tuple(name for name, _address, _expected in V16_PATCH_PROBES + V16_LAUNCH_PROBES),
         tuple(name for name, _address, _expected in V17_PATCH_PROBES + V17_LAUNCH_PROBES),
         tuple(name for name, _address, _expected in V18_PATCH_PROBES + V18_LAUNCH_PROBES))
    return min(([
        name for name in profile
        if not probes.get(name, {}).get("matches", False)
    ] for profile in profiles), key=len)


def _runtime_revision(probes: dict) -> str | None:
    for label in ("V18", "V17", "V16", "V14", "V11", "V10", "V9"):
        prefix = "ignition_cut_" + label.lower() + "_"
        names = [name for name, _address, _expected in PATCH_PROBES
                 if name.startswith(prefix)]
        if names and all(probes.get(name, {}).get("matches", False) for name in names):
            return label
    return None


def _decode_recovery(pending: int, revision) -> dict:
    decoded = {
        "cut_recovery_pending_raw": f"0x{pending:02X}",
        "cut_recovery_unknown_bits": f"0x{pending & (0xE0 if revision in ('V14', 'V16', 'V17', 'V18') else 0xF0):02X}",
        "cut_recovery_pending": {
            name: bool(pending & mask)
            for name, mask in (("front_b1", 1), ("front_b2", 2),
                               ("rear_b1", 4), ("rear_b2", 8))},
    }
    if revision in ("V14", "V16", "V17", "V18"):
        decoded["misfire_recovery_pending"] = bool(pending & 0x10)
    return decoded


def _decode_timing(state: int, source: int, revision=None) -> dict:
    return {
        "timing_state_raw": f"0x{state:02X}",
        "timing_state": {0xC0: "inactive",
                         0xC1: ("eligible, native fault veto" if revision in ("V17", "V18")
                                else "eligible, native protection veto"),
                         0xC3: ("limiter timing constraint active" if revision in ("V17", "V18")
                                else "timing override published")}.get(state, "unknown/uninitialized"),
        "timing_source_raw": f"0x{source:02X}",
        "timing_source": {0: "none", 1: "fuel launch", 2: "ignition launch",
                          3: "standalone ignition band"}.get(source, "unknown"),
    }


def _decode_knock_qualification(global_history, channel_histories, faults_a, faults_b):
    return {
        "knock_global_history_raw": global_history,
        "knock_channel_histories_raw": list(channel_histories),
        "knock_fault_flags_a_raw": f"0x{faults_a:04X}",
        "knock_fault_flags_b_raw": f"0x{faults_b:04X}",
        "native_timing_fault_veto": bool(faults_a & 0x007F or faults_b & 0xC000),
    }


def _decode_knock_guard(raw, revision="V17"):
    initialized = raw[0] in ((0xA7, 0xE7) if revision == "V18" else (0xA7,))
    return {
        "knock_guard_raw": bytes(raw).hex(),
        "knock_guard_initialized": initialized,
        **({"knock_modified_timing_episode": raw[0] == 0xE7 if initialized else None}
           if revision == "V18" else {}),
        "knock_clean_window_mask_raw": f"0x{raw[1]:02X}",
        "knock_open_phase_mask_raw": f"0x{raw[2]:02X}",
        **{name: bool(value) if initialized and value in (0, 1) else None
           for name, value in zip(("knock_old_reply_drained", "knock_request_qualified",
                                   "knock_snapshot_qualified"), raw[3:6])},
        "knock_retained_valid_mask_raw": f"0x{raw[6]:02X}",
    }


def _decode_fixed_ipw_state(value):
    return {
        "fixed_ipw_state_raw": f"0x{value:02X}",
        "fixed_ipw_state": {0xA0: "native", 0xA1: "fixed base override",
                            0xA2: "native fallback"}.get(value, "unknown/uninitialized"),
    }


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
        "ignition_hook_revision": _runtime_revision(probes),
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
    probes = _patch_probe_snapshot(ds2)
    revision = _runtime_revision(probes)
    cut_address = _state_address(revision)
    cut_state = _read_exact(ds2, cut_address, 1)
    launch_latch = _read_exact(ds2, 0xFDB6, 1)
    battery = _read_exact(ds2, 0xFC9D, 1)
    boot_scratch = _read_exact(ds2, 0xE732, 2)
    record = _read_exact(ds2, 0xEC2A, 12)
    dtc_payload = bytes(_retry("read DTC after reconnect", ds2.read_dtc))
    reason = record[6] | (record[7] << 8)
    live_reason = _le(live)

    return {
        "type": "transport_recovery_evidence",
        "captured_utc": _utc_now(),
        "raw_reads": {
            "self_test_live": {
                "address": "0x0EA0C", "length": 2, "raw": live.hex()},
            "cut_state": {
                "address": f"0x{cut_address:05X}", "length": 1,
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
        "ignition_hook_revision": revision,
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
                    "An emulator reset fixture produced 0x0003 with unresolved "
                    "early DPP/bus mapping; this is not a proven physical reset signature.",
        },
    }


def _slow_snapshot(ds2, phase: str, revision: str | None = None, report=None) -> dict:
    raw, read_evidence = {}, {}
    reads = _slow_reads_for_revision(revision)
    for name, address, length in reads:
        read_evidence[name] = {}
        observation = {"type": "slow_read", "snapshot_phase": phase,
                       "name": name, "address": f"0x{address:05X}", "length": length}
        try:
            raw[name] = _read_exact(ds2, address, length, read_evidence[name])
            observation["raw"] = raw[name].hex()
        except (DS2Error, OSError) as error:
            observation["error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            if report is not None:
                observation["read_evidence"] = read_evidence[name]
                _write_record(report, observation)
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
                "read_evidence": read_evidence[name],
            }
            for name, address, length in reads
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
                    "An emulator reset fixture produced 0x0003 with unresolved "
                    "early DPP/bus mapping; this is not a proven physical reset signature.",
        },
        "slow_values": {
            **_decode_recovery(raw["cut_recovery_pending"][0], revision),
            **({"cut_recovery_pending": None} if revision not in ("V10", "V11", "V14", "V16", "V17", "V18") else {}),
            **(_decode_timing(raw["recovery_neighbors"][3 if revision in ("V16", "V17", "V18") else 9],
                             raw["recovery_neighbors"][4 if revision in ("V16", "V17", "V18") else 10], revision)
               if revision in ("V14", "V16", "V17", "V18") else {}),
            "front_o2_b1_v": round((_le(raw["front_o2_b1"]) & 0x03FF) * 5 / 1023, 4),
            "front_o2_b2_v": round((_le(raw["front_o2_b2"]) & 0x03FF) * 5 / 1023, 4),
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
            "input_80": bool(raw["input_80_81_latch"][0] & 0x02),
            "input_81": bool(raw["input_80_81_latch"][0] & 0x01),
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
        **(_decode_fixed_ipw_state(raw["fixed_ipw_state"][0]) if revision == "V18" else {}),
        "ignition_cut_cals": _decode_ignition_cals(raw["ignition_cals"], revision),
        "launch_control_cals": _decode_launch_cals(raw["launch_cals"], revision),
        "limiter_context": {
            "native_fuel_limiter_active": bool(raw["native_fuel_limiter"][0] & 0x80),
            "effective_soft_threshold_rpm": raw["effective_soft_rpm"][0] * 32,
            "launch_armed": bool(raw["launch_armed_latch"][0] & 0x40),
            "native_fuel_recovery_priority": bool(raw["cut_state_and_recovery"][0] & 8),
            "runtime_valid": (raw["cut_state_and_recovery"][0] & 0xF0) == 0xA0,
            "note": "Sequential snapshot. Fuel-limiter active does not identify the threshold source. "
                    "Recovery priority selects native recovery settings; it is not a cut request. "
                    "Compare the effective threshold with the configured ignition and launch targets.",
        },
        "knock_learning": {
            **(_decode_knock_guard(raw["knock_guard"], revision) if revision in ("V17", "V18") else {}),
            **_decode_knock_qualification(
                raw["knock_global_history"][0], raw["knock_channel_histories"],
                _le(raw["knock_fault_flags"][:2]), _le(raw["knock_fault_flags"][2:])),
            "selected_cells_raw": list(raw["knock_selected_cells"]),
            "retained_channel_words_raw": [
                _le(raw["knock_retained_channels"][index:index + 2])
                for index in range(0, 12, 2)],
            "learned_rows_raw": [raw[f"knock_learned_row_{index + 1}"].hex()
                                 for index in range(6)],
            "note": "Sequential snapshots in native channel order; raw values have no asserted "
                    "physical units or cylinder numbering. Changes do not establish their cause.",
        },
    }


def decode_batch_payload(raw: bytes, layout=BATCH_LAYOUT, revision=None) -> dict:
    expected_length = sum(length for _name, _address, length in layout)
    if len(raw) != expected_length:
        raise DS2Error(f"telegram payload length: {len(raw)}/{expected_length} bytes")

    values = {}
    raw_by_address = {}
    offset = 0
    for name, address, length in layout:
        chunk = raw[offset:offset + length]
        if len(chunk) != length:
            raise DS2Error(f"short telegram value at 0x{address:04X}")
        # Split odd words arrive low byte first; native word descriptors are BE.
        endian = "little" if length == 2 and address & 1 else "big"
        values[name] = int.from_bytes(chunk, endian)
        raw_by_address[f"0x{address:04X}"] = chunk.hex()
        offset += length

    cut = values["cut_state"]
    decoded = {
            "rpm": values["rpm_mirror"],
            "cut_rpm": values["cut_rpm"] * 32,
            "working_speed_kmh": values["working_speed"],
            "battery_v": round(values["battery"] * 0.10196, 3),
            "throttle_pct": round(values["throttle"] * 100 / 255, 3),
            "throttle_raw": values["throttle"],
            "launch_tps_threshold_pct": round(values["throttle"] * 0.47, 3),
            "cut_state_raw": f"0x{cut:02X}",
            "cut_patch_runtime": (cut & 0xF0) == 0xA0,
            "standalone_ignition_cut": bool(cut & 0x01),
            "launch_ignition_cut": bool(cut & 0x02),
            "launch_fuel_cut": bool(cut & 0x04),
            "native_fuel_recovery_priority": bool(cut & 0x08),
            "launch_armed": bool(values["launch_latch"] & 0x40),
            "native_limiter_active": bool(values["native_limiter"] & 0x80),
            "native_fuel_limiter_active": bool(values["native_limiter"] & 0x80),
            "fuel_cut_stage": values["fuel_cut_stage"],
            "native_soft_threshold_rpm": values["native_soft_rpm"] * 32,
            "effective_soft_threshold_rpm": values["native_soft_rpm"] * 32,
            "input_80": (bool(values["input_80_81"] & 0x02)
                         if "input_80_81" in values else None),
            "input_81": (bool(values["input_80_81"] & 0x01)
                         if "input_80_81" in values else None),
            "decel_fuel_cut": bool(values["operating_state"] & 0x20),
            "lambda_regulation_b1": bool(values["lambda_state_b1"] & 0x08),
            "lambda_regulation_b2": bool(values["lambda_state_b2"] & 0x08),
            "base_ipw_ms": round(values["base_ipw"] * 0.00534, 4),
            # Keep legacy result keys; these are bank base widths before latency/cylinder correction.
            "final_ipw_b1_ms": round(values["final_ipw_b1"] * 0.00534, 4),
            "final_ipw_b2_ms": round(values["final_ipw_b2"] * 0.00534, 4),
            "stft_b1_pct": _trim_percent(values["stft_b1"]),
            "stft_b2_pct": _trim_percent(values["stft_b2"]),
            "engine_load_mg_st": round(values["engine_load"] * 0.021195, 3),
            "maf_kg_h": round(values["maf"] * 0.25, 3),
            "front_o2_b1_v": round((values["front_o2_b1"] & 0x03FF) * 5 / 1023, 4),
            "front_o2_b2_v": (round((values["front_o2_b2"] & 0x03FF) * 5 / 1023, 4)
                              if "front_o2_b2" in values else None),
    }
    if "input_82" in values:
        decoded["input_82"] = bool(values["input_82"] & 0x80)
    if "wbo_telemetry" in values:
        decoded["wbo_afr"] = round(
            values["wbo_telemetry"] * 0.05 + 8.25, 2)
    if "cut_recovery_pending" in values:
        decoded.update(_decode_recovery(values["cut_recovery_pending"], revision))
    if "timing_state_source" in values:
        state = values["timing_state_source"]
        decoded.update(_decode_timing(state & 0xFF, state >> 8, revision))
    if "knock_global_history" in values:
        decoded.update(_decode_knock_qualification(
            values["knock_global_history"],
            [values[f"knock_history_channel_{index + 1}"] for index in range(6)],
            values["knock_fault_flags_a"], values["knock_fault_flags_b"]))
    if "knock_guard_marker" in values:
        decoded.update(_decode_knock_guard(
            [values[name] for name, _address, _length in V17_KNOCK_GUARD_LAYOUT], revision))
    if "fixed_ipw_state" in values:
        decoded.update(_decode_fixed_ipw_state(values["fixed_ipw_state"]))
    return {"raw_by_address": raw_by_address, "values": decoded}


def _status_line(sample: dict) -> str:
    value = sample["values"]
    wideband = (
        f"  WBO={value['wbo_afr']:.2f} AFR"
        if "wbo_afr" in value else ""
    )
    recovery = (f"  recovery={value['cut_recovery_pending_raw']}"
                if "cut_recovery_pending_raw" in value else "")
    fuel_state = (f"  fuel={value['fixed_ipw_state']}" if "fixed_ipw_state" in value else "")
    return (
        f"{sample['elapsed_s']:6.2f}s  rpm={value['rpm']:4d}  "
        f"cut={value['cut_state_raw']}  fuel limiter={int(value['native_limiter_active'])}  "
        f"effective soft={value['effective_soft_threshold_rpm']}rpm  "
        f"armed={int(value['launch_armed'])}  "
        f"native recovery={int(value['native_fuel_recovery_priority'])}  "
        f"stage={value['fuel_cut_stage']}  "
        f"bank base IPW={value['final_ipw_b1_ms']:.2f}/{value['final_ipw_b2_ms']:.2f}ms  "
        f"STFT={value['stft_b1_pct']:+.1f}/{value['stft_b2_pct']:+.1f}%"
        f"{wideband}{recovery}{fuel_state}"
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
    recovery_tracking = bool(getattr(args, "recovery_tracking", False))
    stationary_wideband = recovery_tracking or bool(getattr(args, "stationary_wideband", False))
    batch_layout = (
        RECOVERY_BATCH_LAYOUT if recovery_tracking else STATIONARY_WIDEBAND_BATCH_LAYOUT
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
    recovery_anomaly_samples = 0
    recovery_confirmed = False
    transport_gaps = 0
    exit_code = 0
    ready = False
    error_text = None

    report_owner = (
        output.open("x", encoding="utf-8", newline="\n")
        if output is not None else nullcontext(external_report)
    )
    with report_owner as report, _wire_capture(ds2, report) as wire_available:
        _write_record(report, {
            "type": "start",
            "schema": "bimmerstein-ms413-diagnostic-v5",
            "recorder_version": "8",
            "supported_family": "MS41.3 / exact SS1v2 program signature",
            "measurement_notes": {
                "native_fuel_limiter": "Native fuel-limiter episode, including fuel-mode launch. "
                                       "The active flag alone does not identify its threshold source.",
                "timing_state": "V17/V18 C3 means the limiter timing constraint is active. "
                                "The native protected angle may win or tie; no physical spark timing is measured.",
                "launch_latch": "Throttle below minimum or speed at/above maximum releases launch. "
                                "Arming requires speed below arm speed; switch release alone retains the latch.",
                "sampling": "Telegram values are acquired together but are not an atomic engine-event trace. "
                            "Calibration and slow context snapshots are read sequentially.",
            },
            "captured_utc": _utc_now(),
            "transport": "DS2 9600 8E2",
            "operator_note": getattr(args, "note", ""),
            "stationary_wideband": stationary_wideband,
            "recovery_tracking": recovery_tracking,
            "safety": "Only identify, DTC read, memory read, and non-persistent "
                      "telegram setup/poll are used; no flash, erase, explicit "
                      "memory write, reset, DTC clear, or adaptation clear.",
            "batch_layout": [
                {"name": name, "address": f"0x{address:04X}", "length": length}
                for name, address, length in batch_layout
            ],
        })
        try:
            if recovery_tracking and not wire_available:
                raise RuntimeError("This recorder requires the DS2 raw-reply capture interface")
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

            revision = identity["ignition_hook_revision"]
            if recovery_tracking and revision not in ("V10", "V11", "V14", "V16", "V17", "V18"):
                raise RuntimeError("Recovery tracking requires a supported V10/V11/V14/V16/V17/V18 layout")
            if recovery_tracking and revision in ("V14", "V16", "V17", "V18"):
                batch_layout = _batch_layout_for_revision(
                    V18_RECOVERY_BATCH_LAYOUT if revision == "V18" else
                    V17_RECOVERY_BATCH_LAYOUT if revision == "V17" else V14_RECOVERY_BATCH_LAYOUT,
                    revision)
                _write_record(report, {
                    "type": "batch_layout", "ignition_hook_revision": revision,
                    "note": f"{revision} timing state/source replaces fast bank-2 O2 voltage; "
                            "both O2 voltages remain in preflight/postflight snapshots. "
                            "Timing state describes software decisions, not measured spark timing.",
                    "batch_layout": [{"name": name, "address": f"0x{address:04X}", "length": length}
                                     for name, address, length in batch_layout],
                })
            batch_layout = _batch_layout_for_revision(batch_layout, revision)
            if revision in ("V16", "V17", "V18") and not recovery_tracking:
                _write_record(report, {
                    "type": "batch_layout", "ignition_hook_revision": revision,
                    "batch_layout": [{"name": name, "address": f"0x{address:04X}", "length": length}
                                     for name, address, length in batch_layout],
                })
            _write_record(report, _slow_snapshot(ds2, "preflight", revision, report))
            ready = True
            ds2.setup_telegram_batch(entries=_batch_entries(batch_layout))
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
                    sample = decode_batch_payload(raw, batch_layout, revision)
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
                    ready = False
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
                    recovery = _recovery_evidence_snapshot(ds2)
                    _write_record(report, recovery)
                    prior_revision = revision
                    revision = recovery["ignition_hook_revision"]
                    if (revision != prior_revision
                            or _required_probe_failures(recovery["patch_probes"])):
                        raise RuntimeError(
                            "ignition/launch hook profile changed after reconnect; "
                            "capture stopped before interpreting runtime state")
                    ready = True
                    _write_record(
                        report,
                        _slow_snapshot(ds2, "transport_recovery", revision, report),
                    )
                    if args.seconds > 0 and time.monotonic() >= deadline:
                        break
                    ds2.setup_telegram_batch(entries=_batch_entries(batch_layout))
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
                if sample["values"].get("cut_recovery_unknown_bits", "0x00") != "0x00":
                    recovery_anomaly_samples += 1
                    if not recovery_confirmed:
                        recovery_confirmed = True
                        _recovery_confirmation(ds2, report, sample["elapsed_s"], revision)
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
                    value.get("cut_recovery_pending_raw"),
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
                    _write_record(report, _slow_snapshot(ds2, "postflight", revision, report))
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
                "recovery_anomaly_samples": recovery_anomaly_samples,
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
        recovery_tracking=True,
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
        "--recovery-tracking", action="store_true",
        help="capture revision-specific recovery flags and WBO in every frame; pins 80/81 remain unsampled")
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
