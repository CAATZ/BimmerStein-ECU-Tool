#!/usr/bin/env python3
"""Execute the current MS41.0-MS41.3 patch set in canonical ms41emu.

This is a behavioural gate for the port, not a replacement for on-car/HIL tests.
It composes the real JSON descriptors through ``patch_ms41.build``, keeps every
case checksummed, and executes the resulting C166 machine code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
_GROUP_NAMES = (
    "cal-guard", "loader-doors", "intel-flash", "amd-flash",
    "features-ms410", "features-ms411", "features-ms412", "features-ms413",
)


def _parse_args(argv):
    parser = argparse.ArgumentParser(description="private MS41 patch admission")
    parser.add_argument("--group", action="append", choices=_GROUP_NAMES)
    parser.add_argument("--list", action="store_true")
    return parser.parse_args(argv)


if __name__ == "__main__":
    _early_args = _parse_args(sys.argv[1:])
    if _early_args.list:
        print("\n".join(_GROUP_NAMES))
        raise SystemExit(0)

if not __debug__:
    raise SystemExit(
        "private emulator admission refuses optimized Python; assertions must execute")


def _required_directory(variable: str) -> Path:
    value = os.environ.get(variable, "").strip()
    if not value:
        raise SystemExit(f"set {variable} to run the private emulator gate")
    path = Path(value).expanduser()
    if not path.is_dir():
        raise SystemExit(f"{variable} does not identify an available directory")
    return path


EMU_ROOT = _required_directory("MS41EMU_ROOT")
TEST_DATA_ROOT = _required_directory("MS41_TEST_DATA_ROOT")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(EMU_ROOT))

import checksum  # noqa: E402
from dtc import parse_ds2_dtc_response  # noqa: E402
from engines.patcher import patch_ms41  # noqa: E402
from engines.patcher.cal_guard_exact import (  # noqa: E402
    BOOT_EXIT,
    BOOT_FALLBACK,
    CAVE_CPU,
    CAVE_FILE,
    CAVE_SIZE,
    POLL_COUNT,
    RECOVER_EXIT,
    RECOVERY_TOKEN,
    assemble as assemble_cal_guard,
    assemble_stub as assemble_cal_guard_stub,
)
from ms41emu import Emulator  # noqa: E402
from ms41emu.peripherals import (  # noqa: E402
    AmdFlashModel,
    Eeprom24C04,
    FlashModel,
    Timer1,
)
from ms41emu.references import resolve_reference  # noqa: E402
from tools.opcode_coverage import (  # noqa: E402
    manual_evidence_paths,
    require_trusted,
)


_ADMISSION_EMULATORS = []


def _load_emulator(*args, **kwargs):
    emu = Emulator.load(*args, **kwargs)
    _ADMISSION_EMULATORS.append(emu)
    return emu


STOCK_410_PATH = resolve_reference(".0", root=TEST_DATA_ROOT, required=True)
STOCK_411_PATH = resolve_reference(".1", root=TEST_DATA_ROOT, required=True)
STOCK_PATH = resolve_reference(".2", root=TEST_DATA_ROOT, required=True)
STOCK_413_PATH = resolve_reference(".3", root=TEST_DATA_ROOT, required=True)
_REFERENCE_VARIANTS = {
    STOCK_410_PATH.resolve(): "1429861",
    STOCK_411_PATH.resolve(): "1437806",
    STOCK_PATH.resolve(): "1406464",
    STOCK_413_PATH.resolve(): "SS1v2",
}
_BOUND_VARIANTS = {}
_LAUNCH_LATCH_BY_VARIANT = {
    "1429861": 0xFD80,
    "1437806": 0xFDB6,
    "1406464": 0xFDB6,
    "SS1v2": 0xFDB6,
}
_WATCHDOG_LAYOUTS = {
    # pending flag, dispatcher entry/exit, task, SRVWDT
    "1429861": (0xFD50, 0x20CAE, 0x20CB8, 0x03248, 0x26772),
    "1437806": (0xFD60, 0x20DE2, 0x20DEC, 0x0393C, 0x28D50),
    "1406464": (0xFD60, 0x20EA2, 0x20EAC, 0x03900, 0x28AE0),
    "SS1v2": (0xFD60, 0x20EA2, 0x20EAC, 0x03900, 0x28AE0),
}
_DTC100_LAYOUTS = {
    # Runtime CSP:IP CPU addresses. Memory code-fetch alone maps physical flash
    # to its backing-file offset; the fetched-byte signatures bind each entry.
    # boot stop, dispatcher, TX arm, evaluator, status byte, reason word
    "1429861": (0x2B388, 0x22428, 0x227E4, 0x25BDC, 0xEBD2, 0xEBD8),
    "1437806": (0x2FC64, 0x23168, 0x23536, 0x27FC6, 0xEC2A, 0xEC30),
    "1406464": (0x2FA24, 0x23246, 0x23614, 0x27C36, 0xEC2A, 0xEC30),
    "SS1v2": (0x2FA24, 0x23246, 0x23614, 0x27C36, 0xEC2A, 0xEC30),
}
_FUEL_TASK_LAYOUTS = {
    # scheduled task, displaced hook, patch cave, RPM, compared outputs
    "1429861": (
        0x254F2, 0x25840, 0x32A00, 0xFAE6,
        (0xECBC, 0xECBE, 0xFACA, 0xFACC),
    ),
    "1437806": (
        0x276F4, 0x27A5A, 0x3F8C0, 0xFC3C,
        (0xEF96, 0xEF98, 0xFC20, 0xFC22),
    ),
    "1406464": (
        0x271F4, 0x2755A, 0x3DEA0, 0xFC3C,
        (0xEF7E, 0xEF80, 0xFC20, 0xFC22),
    ),
    "SS1v2": (
        0x271F4, 0x2755A, 0x3DEA0, 0xFC3C,
        (0xEF7E, 0xEF80, 0xFC20, 0xFC22),
    ),
}
_CC6_IGNITION_HOOKS = {
    "1429861": 0x326F8,
    "1437806": 0x3F466,
    "1406464": 0x3D92A,
    "SS1v2": 0x3D92A,
}
_ASC0_RX_HANDLERS = {
    "1429861": 0x2FD10,
    "1437806": 0x3EB0C,
    "1406464": 0x3CFC4,
    "SS1v2": 0x3CFC4,
}
_FULL_STACK_PATCH_IDS = {
    "1429861": (
        "amd_flash", "softbsl_loader", "cal_guard", "door_magic_ms410",
        "ignition_cut_v7_ms410", "launch_control_v4_ms410",
    ),
    "1437806": (
        "amd_flash", "softbsl_loader", "cal_guard", "door_magic_ms411",
        "ignition_cut_v7_ms411", "launch_control_v4_ms411",
    ),
    "1406464": (
        "amd_flash", "softbsl_loader", "cal_guard", "door_magic",
        "ignition_cut_v7", "launch_control_v4_ms412",
    ),
    "SS1v2": (
        "amd_flash", "softbsl_loader", "cal_guard", "door_magic",
        "ignition_cut_v7", "launch_control_v5",
    ),
}
assert set(_FULL_STACK_PATCH_IDS) == set(_WATCHDOG_LAYOUTS)
assert set(_CC6_IGNITION_HOOKS) == set(_WATCHDOG_LAYOUTS)
assert set(_ASC0_RX_HANDLERS) == set(_WATCHDOG_LAYOUTS)
assert set(_DTC100_LAYOUTS) == set(_WATCHDOG_LAYOUTS)
assert set(_FUEL_TASK_LAYOUTS) == set(_WATCHDOG_LAYOUTS)

def _bind_image(image, variant):
    image = bytes(image)
    digest = hashlib.sha256(image).hexdigest()
    assert _BOUND_VARIANTS.get(digest, variant) == variant
    _BOUND_VARIANTS[digest] = variant
    return image


def _bound_variant(image):
    digest = hashlib.sha256(image).hexdigest()
    assert digest in _BOUND_VARIANTS, (
        "composed image has no independently bound emulator variant", digest)
    return _BOUND_VARIANTS[digest]


def _launch_latch(image):
    return _LAUNCH_LATCH_BY_VARIANT[_bound_variant(image)]


PATCHES = patch_ms41.load_patches()
LATEST = [
    "amd_flash", "softbsl_loader", "cal_guard", "door_magic",
    "ignition_cut_v7", "launch_control_v4_ms412",
]


def _build(ids):
    return _build_from(STOCK_PATH, ids)


def _build_from(stock_path, ids):
    stock = stock_path.read_bytes()
    image, _log = patch_ms41.build(stock, ids, marker="B")
    status = checksum.checksum_status(image)
    assert status["boot"] and status["program"] and status["cal"], status
    return _bind_image(image, _REFERENCE_VARIANTS[stock_path.resolve()])


FULL_IMAGE = _build(LATEST)
BOOTSTRAP_IMAGE = _build(["amd_flash", "softbsl_loader", "door_0x43"])


def _build_softbsl_pair(stock_path, bootstrap_door, persistent_door):
    common = ["amd_flash", "softbsl_loader"]
    return (
        _build_from(stock_path, [*common, "cal_guard", persistent_door]),
        _build_from(stock_path, [*common, bootstrap_door]),
    )


SOFTBSL_410_IMAGE, BOOTSTRAP_410_IMAGE = _build_softbsl_pair(
    STOCK_410_PATH, "door_0x43_ms410", "door_magic_ms410")
SOFTBSL_411_IMAGE, BOOTSTRAP_411_IMAGE = _build_softbsl_pair(
    STOCK_411_PATH, "door_0x43_ms411", "door_magic_ms411")
SOFTBSL_413_IMAGE, BOOTSTRAP_413_IMAGE = _build_softbsl_pair(
    STOCK_413_PATH, "door_0x43", "door_magic")

SOFTBSL_VARIANTS = {
    "MS41.0": (SOFTBSL_410_IMAGE, BOOTSTRAP_410_IMAGE,
               0x2556, 0x2A06, 0x2536, 0x3D96, 0xDBEC),
    "MS41.1": (SOFTBSL_411_IMAGE, BOOTSTRAP_411_IMAGE,
               0x32A8, 0x376C, 0x3276, 0x508E, 0xF606),
    "MS41.2": (FULL_IMAGE, BOOTSTRAP_IMAGE,
               0x3386, 0x385E, 0x3354, 0x51CC, 0xDBEC),
    "MS41.3": (SOFTBSL_413_IMAGE, BOOTSTRAP_413_IMAGE,
               0x3386, 0x385E, 0x3354, 0x51CC, 0xDBEC),
}

_SOFTBSL_LIFECYCLE_FIRMWARE = (
    # Display name, bound variant, exact stock, persistent-door patch/hook.
    ("MS41.0", "1429861", STOCK_410_PATH, "door_magic_ms410", 0x2556),
    ("MS41.1", "1437806", STOCK_411_PATH, "door_magic_ms411", 0x32A8),
    ("MS41.2", "1406464", STOCK_PATH, "door_magic", 0x3386),
    ("MS41.3", "SS1v2", STOCK_413_PATH, "door_magic", 0x3386),
)
_SOFTBSL_LIFECYCLE_CHIPS = (
    # Catalog lower-bank key, manifest agent, install AMD driver, emulator
    # device. 29F400 TOP is a distinct cross-bank lifecycle, not a row here.
    ("28f200", "intel_28f200", False, None),
    ("29f200", "amd", True, "am29f200bb"),
    ("29f400", "amd", True, "am29f400bb"),
)
_SOFTBSL_COMMIT = 0x1A62
_SOFTBSL_RECOVERY_DISPATCH = 0x15A0
_SOFTBSL_LOADER = 0x1F8C
_SOFTBSL_TX = 0x1CA0
_SOFTBSL_AGENT = 0xD800
_SOFTBSL_MARKER = 0xE740
_SOFTBSL_FINALIZER_EEPROM = (0x1DD, 0x1E0, b"\x00\x01\x02")

assert all(
    SOFTBSL_VARIANTS[version][2] == door_hook
    for version, _variant, _stock, _door_id, door_hook
    in _SOFTBSL_LIFECYCLE_FIRMWARE
)


def _build_413():
    stock = STOCK_413_PATH.read_bytes()
    image, _log = patch_ms41.build(
        stock,
        ["alphan_failsafe", "ignition_cut_v7", "launch_control_v5"],
        marker="B",
    )
    status = checksum.checksum_status(image)
    assert status["boot"] and status["program"] and status["cal"], status
    assert status["prog_disabled"], status
    return _bind_image(image, "SS1v2")


FULL_413_IMAGE = _build_413()


def _run_calguard(image, *, variant, e740=3, uart=None):
    cave_file = CAVE_CPU ^ 0x4000
    cave = assemble_cal_guard()
    assert image[cave_file:cave_file + len(cave)] == cave
    emu = _load_emulator(image, force_variant=variant)
    emu.write_byte(0xE740, e740)
    if uart:
        for address, value in uart.items():
            emu.write(address, value)
    result = _run(emu,
        CAVE_CPU,
        stop_at=(BOOT_FALLBACK, RECOVER_EXIT),
        max_steps=POLL_COUNT * 5 + 5000,
    )
    return result.final_ip, emu


def _verify_calguard(image, *, variant, e740=3):
    exit_pc, _emu = _run_calguard(image, variant=variant, e740=e740)
    return {
        BOOT_FALLBACK: "BOOT",
        RECOVER_EXIT: "RECOVER",
    }.get(exit_pc, f"NO EXIT @0x{exit_pc:04X}")


def verify_calguard_compatibility():
    """Execute exact installed V5 bytes on every canonical family and failures."""
    guard = PATCHES["cal_guard"]
    guard_bytes = bytes.fromhex(next(
        edit["data"] for edit in guard["edits"]
        if edit["off"] == CAVE_FILE
    ))
    assert guard_bytes == assemble_cal_guard()
    for offset, expected in assemble_cal_guard_stub().items():
        installed = bytes.fromhex(next(
            edit["data"] for edit in guard["edits"] if edit["off"] == offset))
        assert installed == expected

    installed = {
        "MS41.0": (SOFTBSL_410_IMAGE, "1429861"),
        "MS41.1": (SOFTBSL_411_IMAGE, "1437806"),
        "MS41.2": (FULL_IMAGE, "1406464"),
        "MS41.3": (SOFTBSL_413_IMAGE, "SS1v2"),
    }
    for version, (image, variant) in installed.items():
        assert _verify_calguard(image, variant=variant) == "BOOT", version

    # Enter through the exact installed splice, not only the cave entry.
    hook_cpu = guard["cave"]["splice_off"] ^ 0x4000
    emu = _load_emulator(
        installed["MS41.2"][0], force_variant=installed["MS41.2"][1])
    emu.write_byte(0xE740, 3)
    visited = []
    emu.cpu.set_trace(lambda pc, _opcode: visited.append(pc))
    result = _run(emu,
        hook_cpu, stop_at=(BOOT_FALLBACK, RECOVER_EXIT),
        max_steps=POLL_COUNT * 5 + 5000)
    assert result.final_ip == BOOT_FALLBACK and CAVE_CPU in visited

    # Same broad generation is insufficient: ID41 calibration with ID59
    # program (or vice versa) must remain in the stock flash listener.
    id41_to_id59 = bytearray(installed["MS41.0"][0])
    assert id41_to_id59[0x6007:0x600B] == b"0641"
    id41_to_id59[0x1400C:0x14010] = b"0659"
    assert _verify_calguard(
        id41_to_id59, variant=installed["MS41.0"][1]) == "RECOVER"

    # The strict SS1v2 identity still takes precedence over legacy suffixes.
    strict_mismatch = bytearray(installed["MS41.3"][0])
    assert strict_mismatch[0x173BB:0x173C0] == b"SS1v2"
    assert strict_mismatch[0x6007:0x600B] != b"0641"
    strict_mismatch[0x6007:0x600B] = b"0641"
    assert _verify_calguard(
        strict_mismatch, variant=installed["MS41.3"][1]) == "RECOVER"

    # E740=1 remains the untouched stock branch ahead of the new trampoline.
    emu = _load_emulator(
        installed["MS41.2"][0], force_variant=installed["MS41.2"][1])
    emu.write_byte(0xE740, 1)
    visited = []
    emu.cpu.set_trace(lambda pc, _opcode: visited.append(pc))
    result = _run(emu, 0x093A, stop_at=(BOOT_EXIT, RECOVER_EXIT), max_steps=20)
    assert result.final_ip == RECOVER_EXIT and CAVE_CPU not in visited

    # A normal program replacement erases the end marker. The boot-local
    # trampoline must then replay stock boot instead of jumping into FF.
    without_guard = bytearray(installed["MS41.2"][0])
    without_guard[CAVE_FILE + CAVE_SIZE - 2:CAVE_FILE + CAVE_SIZE] = b"\xFF\xFF"
    emu = _load_emulator(without_guard, force_variant=installed["MS41.2"][1])
    emu.write_byte(0xE740, 3)
    visited = []
    emu.cpu.set_trace(lambda pc, _opcode: visited.append(pc))
    result = _run(
        emu, hook_cpu, stop_at=(BOOT_FALLBACK, RECOVER_EXIT), max_steps=100)
    assert result.final_ip == BOOT_FALLBACK and CAVE_CPU not in visited

    # A quiet boot must restore the UART exactly before continuing.
    uart = {0xFEB4: 0x1234, 0xFFB0: 0x5678, 0xFF6E: 0x9ABC}
    exit_pc, emu = _run_calguard(
        installed["MS41.2"][0], variant=installed["MS41.2"][1], uart=uart)
    assert exit_pc == BOOT_FALLBACK
    assert {address: emu.read(address) for address in uart} == uart

    # The raw pre-arm token must ACK once and enter the existing stock listener.
    cave = assemble_cal_guard()
    poll_loop = CAVE_CPU + cave.find(bytes.fromhex("a758a7a728c1"))
    poll_byte = CAVE_CPU + cave.find(bytes.fromhex("f3f8b2fe7eb7"))
    assert poll_loop >= CAVE_CPU and poll_byte >= CAVE_CPU
    emu = _load_emulator(FULL_IMAGE, force_variant="1406464")
    emu.write_byte(0xE740, 3)
    assert _run(emu,
        CAVE_CPU, breakpoints=(poll_loop,), max_steps=1000).final_pc == poll_loop
    for index, byte in enumerate(RECOVERY_TOKEN):
        emu.asc0.rx_inject(bytes([byte]))
        emu.write_byte(0xFF6E, emu.read_byte(0xFF6E) | 0x80)
        assert _run(emu,
            poll_loop, breakpoints=(poll_byte,), max_steps=10).final_pc == poll_byte
        if index + 1 < len(RECOVERY_TOKEN):
            assert _run(emu,
                poll_byte, breakpoints=(poll_loop,), max_steps=20
            ).final_pc == poll_loop
    emu.write_byte(0xFF6C, emu.read_byte(0xFF6C) | 0x80)
    assert _run(emu,
        poll_byte, stop_at=(RECOVER_EXIT,), max_steps=100).final_ip == RECOVER_EXIT
    assert bytes(emu.asc0.tx) == b"\x06"


OLDER_FEATURE_LAYOUTS = {
    "MS41.0": {
        "stock_path": STOCK_410_PATH,
        "patch_ids": (
            "ignition_cut_v7_ms410",
            "launch_control_v4_ms410",
            "vanos_minrpm_v2_ms410",
        ),
        "ignition_id": "ignition_cut_v7_ms410",
        "launch_id": "launch_control_v4_ms410",
        "vanos_id": "vanos_minrpm_v2_ms410",
        "ignition_hooks": (0x26F8, 0x275C),
        "ignition_entry": 0x26E8,
        "ignition_cave_cpu": 0x32820,
        "ignition_replay_cpu": 0x3288A,
        "control_hook": 0x5840,
        "ipw_addresses": (0xECBC, 0xECBE),
        "rpm_address": 0xFAE6,
        "speed_address": 0xEDF4,
        "paired_selector": 0xFD4E,
        "input_bytes": (0xFD50, 0xFD51),
        "input_latch": (0x2364, 0x2370),
        "launch_latch": 0xFD80,
        "launch_hook": 0x0710,
        "launch_continuations": (0x0714, 0x0726),
        "soft_limit_address": 0xED52,
        "stock_hard_address": 0x01D3,
        "hard_sites": (
            (0x07C4, (0x07CE, 0x081E)),
            (0x0864, (0x0880, 0x086E)),
        ),
    },
    "MS41.1": {
        "stock_path": STOCK_411_PATH,
        "patch_ids": (
            "ignition_cut_v7_ms411",
            "launch_control_v4_ms411",
            "vanos_minrpm_ms411",
        ),
        "ignition_id": "ignition_cut_v7_ms411",
        "launch_id": "launch_control_v4_ms411",
        "vanos_id": "vanos_minrpm_ms411",
        "ignition_hooks": (0xF466, 0xF4CA),
        "ignition_entry": 0xF456,
        "ignition_cave_cpu": 0x3F680,
        "ignition_replay_cpu": 0x3F6EA,
        "control_hook": 0x7A5A,
        "ipw_addresses": (0xEF96, 0xEF98),
        "rpm_address": 0xFC3C,
        "speed_address": 0xF1BE,
        "paired_selector": 0xFD5E,
        "input_bytes": (0xFD60, 0xFD61),
        "launch_latch": 0xFDB6,
        "launch_hook": 0x07D6,
        "launch_continuations": (0x07DA, 0x07EC),
        "soft_limit_address": 0xF02C,
        "stock_hard_address": 0x02DB,
        "hard_sites": (
            (0x088A, (0x0894, 0x08E4)),
            (0x092A, (0x0946, 0x0934)),
        ),
        "vanos_hook": 0xBBC0,
        "vanos_outcomes": (0xBBC8, 0xBBCE),
    },
}

for _layout in OLDER_FEATURE_LAYOUTS.values():
    _layout["image"] = _build_from(
        _layout["stock_path"], list(_layout["patch_ids"]))


def _case_image(values):
    """Set patch calibration bytes, then restore all MS41.2 checksums."""
    image = bytearray(FULL_IMAGE)
    cal_offsets = {}
    for patch_id in ("ignition_cut_v7", "launch_control_v4_ms412"):
        cal_offsets.update(PATCHES[patch_id]["cave"]["cals"])
    for name, value in values.items():
        offset = cal_offsets[name]
        image[offset] = value & 0xFF
    image, _details = checksum.correct_checksums(image, correct_program=True)
    status = checksum.checksum_status(image)
    assert status["boot"] and status["program"] and status["cal"], status
    return _bind_image(image, "1406464")


def _case_image_413(values):
    """Set the MS41.3 patch controls and restore its active checksums."""
    image = bytearray(FULL_413_IMAGE)
    cal_offsets = {}
    for patch_id in ("ignition_cut_v7", "launch_control_v5"):
        cal_offsets.update(PATCHES[patch_id]["cave"]["cals"])
    for name, value in values.items():
        offset = cal_offsets[name]
        image[offset] = value & 0xFF
    image, _details = checksum.correct_checksums(image)
    status = checksum.checksum_status(image)
    assert status["boot"] and status["program"] and status["cal"], status
    assert status["prog_disabled"], status
    return _bind_image(image, "SS1v2")


def _case_image_older(layout, values):
    image = bytearray(layout["image"])
    cal_offsets = {}
    for patch_id in layout["patch_ids"]:
        cal_offsets.update(PATCHES[patch_id].get("cave", {}).get("cals", {}))
    for name, value in values.items():
        offset = cal_offsets[name]
        image[offset] = value & 0xFF
    image, _details = checksum.correct_checksums(image, correct_program=True)
    status = checksum.checksum_status(image)
    assert status["boot"] and status["program"] and status["cal"], status
    return _bind_image(image, _bound_variant(layout["image"]))


def _emu(image, dpp0=5):
    emu = _load_emulator(image, force_variant=_bound_variant(image))
    emu.reg.dpp[0] = dpp0
    # Function-level calls skip stock startup, which normally establishes the
    # calibration flash pages as DPP0=4 and DPP1=5.
    emu.reg.dpp[1] = 5
    return emu


def _run(emu, start, *, stop_at=(), breakpoints=(), **kwargs):
    """Run legacy verifier IP constants through the full CSP:IP public API."""
    csp = emu.cpu.csp

    def full_start(value):
        return value if value > 0xFFFF else (csp << 16) | value

    def full_stops(values):
        if isinstance(values, int):
            values = (values,)
        return tuple(
            address
            for value in values
            for address in (
                (value,) if value > 0xFFFF
                else tuple((segment << 16) | value for segment in range(4)))
        )

    return emu.run_from(
        full_start(start),
        stop_at=full_stops(stop_at),
        breakpoints=full_stops(breakpoints),
        **kwargs)


def _call_native(emu, entry, *, max_steps=10000):
    """Call one stock function with a complete near-return frame."""
    emu.reg.r[0] = 0xFA16
    emu.reg.sp = 0xFBFC
    emu.reg.dpp[:] = [4, 5, 0, 3]
    emu.mem.write_word_direct(0xFBFC, 0xE000)
    emu.mem.write_word_direct(0xFBFE, 0)
    res = _run(emu, entry, stop_at=(0xE000,), max_steps=max_steps)
    assert (
        res.final_pc == 0xE000
        and res.exit_reason == "stop_at"
        and emu.reg.sp == 0xFC00
        and emu.reg.r[0] == 0xFA16
    ), (hex(entry), res)
    return res


def verify_alphan_failsafe():
    patch = PATCHES["alphan_failsafe"]
    assert patch_ms41.is_applied(FULL_413_IMAGE, patch)
    stock = STOCK_413_PATH.read_bytes()
    v3, _log = patch_ms41.build(stock, ["alphan_failsafe"])
    stock = _bind_image(stock, "SS1v2")
    v3 = _bind_image(v3, "SS1v2")
    load_words = (0xFC52, 0xFC54, 0xE80A, 0xE80C, 0xE8E4, 0xE8E8)

    def run_load(image, selector=None, fault=None, *, emu=None):
        emu = _emu(image, dpp0=4) if emu is None else emu
        emu.reg.r[0] = 0xFA16
        emu.reg.sp = 0xFC00
        emu.reg.dpp[:] = [4, 5, 0, 3]
        if selector is not None:
            emu.write(0xFD22, selector)
        if fault is not None:
            emu.write(0xFD30, fault)
        for address, value in (
            (0xFC52, 0x0200), (0xFC54, 0x0400), (0xFC3A, 0x0600),
            (0xE902, 0x4000), (0xE8E8, 0x0100), (0xE8E4, 0x0100),
            (0xFD12, 0),
        ):
            emu.write(address, value)
        emu.write_byte(0xFC3C, 60)
        emu.write_byte(0xE8D0, 80)
        emu.write_byte(0xE900, 80)

        visited = set()
        captured = {}

        def trace(pc, _opcode):
            visited.add(pc)
            if pc == 0x3DB96:
                captured["dtc12_raw"] = emu.reg.r[4] & 0xFF
            elif pc == 0x3DB9A:
                captured["dtc12_reduced"] = emu.reg.r[4]

        emu.cpu.set_trace(trace)
        try:
            result = _run(
                emu, 0x34F68, stop_at=0x34FDA, max_steps=20_000)
        finally:
            emu.cpu.set_trace(None)
        assert result.exit_reason == "stop_at" and result.final_pc == 0x34FDA
        return (
            emu,
            tuple(emu.read(address) for address in load_words),
            visited,
            captured,
        )

    stock_healthy = run_load(stock, 0, 0)
    v3_healthy = run_load(v3, 0, 0)
    assert stock_healthy[1][:4] == (0x0800, 0x0030, 0x0200, 0x0400)
    assert stock_healthy[1][4] == stock_healthy[1][5]
    assert v3_healthy[1] == stock_healthy[1]
    assert {0x3DB6A, 0x3DB6E, 0x3DB7A, 0x3DB7E} <= v3_healthy[2]

    stock_selected = run_load(stock, 0x0400, 0)
    v3_selected = run_load(v3, 0x0400, 0)
    selected_fc52, selected_fc54, selected_e80a, selected_e80c, selected_e8e4, selected_e8e8 = (
        stock_selected[1])
    assert selected_fc52 == min(selected_e80a << 2, 0xFFFF)
    assert selected_e80c == (selected_e80a * 0x0600) >> 16
    assert selected_fc54 == (selected_fc52 * 0x0600) >> 16
    assert selected_e8e4 == selected_e8e8
    assert v3_selected[1] == stock_selected[1]
    assert {0x3DB6A, 0x3DB6E, 0x3DB72, 0x2E86E, 0x2E872} <= v3_selected[2]

    stock_sd = run_load(stock, 0x0200, 0)
    v3_sd = run_load(v3, 0x0200, 0)
    assert v3_sd[1] == stock_sd[1]
    assert 0x3DB6A not in v3_sd[2]

    dtc_emu = _emu(v3, dpp0=4)
    configured = _run(dtc_emu, 0x20670, stop_at=0x20714, max_steps=1000)
    assert configured.final_pc == 0x20714
    assert dtc_emu.read(0xFD22) & 0x0600 == 0
    dtc_emu.write(0xFD06, 0x0020)
    dtc_emu.write(0xFD14, 0)
    dtc_emu.write_byte(0xE8E9, 0xFF)
    dtc_emu.write_byte(0xFA9E, 0)
    for _ in range(10):
        _call_native(dtc_emu, 0x2E5B2)
    assert bytes(dtc_emu.read_byte(0xEA26 + i) for i in range(12)) == bytes.fromhex(
        "711401280000000000004014")
    assert dtc_emu.read(0xFD38) == 0x0071
    assert dtc_emu.read(0xFD30) & 0x0002

    matured_fault = run_load(v3, emu=dtc_emu)
    assert matured_fault[1] == stock_selected[1]
    assert matured_fault[1][2] != 0x0200
    assert {0x3DB6A, 0x3DB76, 0x2E872} <= matured_fault[2]

    dtc12 = run_load(v3, 0, 0x0003)
    assert {0x3DB82, 0x3DB8E, 0x3DB92, 0x3DB96, 0x3DB98,
            0x3DB9A, 0x3DB9C} <= dtc12[2]
    assert set(dtc12[3]) == {"dtc12_raw", "dtc12_reduced"}
    assert dtc12[3]["dtc12_raw"] > 3
    assert dtc12[3]["dtc12_reduced"] == dtc12[3]["dtc12_raw"] >> 2
    fc52, fc54, e80a, e80c, _e8e4, _e8e8 = dtc12[1]
    assert fc52 == min(e80a << 2, 0xFFFF)
    assert e80c == (e80a * 0x0600) >> 16
    assert fc54 == (fc52 * 0x0600) >> 16

    def guard_route(image, hook, state, stops, seeds=()):
        emu = _emu(image, dpp0=4)
        emu.write_byte(0xE847, state)
        for address, value in seeds:
            emu.write(address, value)
        emu.cpu.csp = hook >> 16
        sp0 = emu.reg.sp
        architectural = (emu.reg.cp, tuple(emu.reg.dpp))
        result = _run(
            emu, hook & 0xFFFF,
            stop_at=tuple(stop & 0xFFFF for stop in stops), max_steps=100)
        assert (emu.reg.cp, tuple(emu.reg.dpp)) == architectural
        return emu, result, (emu.cpu.csp << 16) | result.final_ip, sp0

    routes = (
        (0x0000, 0x0000, False),
        (0x0000, 0x0001, False),
        (0x0400, 0x0000, True),
        (0x0000, 0x0002, True),
    )
    for hook, fallthrough, taken in (
        (0x38F60, 0x38F64, 0x38F82),
        (0x3905A, 0x3905E, 0x3907C),
    ):
        for selector, fault, should_take in routes:
            guard_emu, result, final, sp0 = guard_route(
                v3, hook, 0xA0, (fallthrough, taken),
                seeds=((0xFD22, selector), (0xFD30, fault)),
            )
            assert result.exit_reason == "stop_at"
            assert final == (taken if should_take else fallthrough)
            assert guard_emu.reg.sp == sp0

    def run_consumer(emu):
        captured = {}

        def trace(pc, _opcode):
            if pc == 0x3C99A:
                captured["load"] = emu.reg.r[13]

        emu.cpu.set_trace(trace)
        try:
            _call_native(emu, 0x3C8C4, max_steps=20_000)
        finally:
            emu.cpu.set_trace(None)
        assert captured["load"] == emu.read(0xE80A)
        return emu.read(0xEF78)

    assert run_consumer(matured_fault[0]) == run_consumer(v3_selected[0])


def _crc16(data, init=0xFFFF):
    crc = init
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return crc & 0xFFFF


def verify_loader_and_doors():
    for version, case in SOFTBSL_VARIANTS.items():
        (image, bootstrap, persistent_hook, nak_handler,
         bootstrap_hook, clear_handler, bootstrap_tx) = case

        # The common SA1 dispatcher, loader, and CRC helper execute on every base.
        emu = _emu(image)
        res = _run(emu, 0x15A0, stop_at=(_SOFTBSL_LOADER,), max_steps=10)
        assert res.final_ip == _SOFTBSL_LOADER and res.exit_reason == "stop_at", (version, res)

        emu = _emu(image)
        emu.write_byte(0xE653, 0x00)
        res = _run(emu, _SOFTBSL_LOADER, stop_at=(0x0A44,), max_steps=100)
        assert res.final_ip == 0x0A44 and res.exit_reason == "stop_at", (version, res)

        emu = _emu(image)
        emu.write_byte(0xE653, 0x5A)
        emu.write_byte(0xE423, 0x9C)
        emu.write_byte(0xE424, 0x9C)
        emu.write_byte(0xE425, 0x00)
        emu.write_byte(0xE426, 0x01)
        res = _run(emu, _SOFTBSL_LOADER, stop_at=(_SOFTBSL_TX,), max_steps=100)
        assert (
            res.final_ip == _SOFTBSL_TX
            and res.exit_reason == "stop_at"
            and (emu.reg.r[4] & 0xFF) == 0x06
        ), (version, res, emu.reg.r[4])

        # V11 rejects empty and oversized agents before ACK, receive, RAM copy,
        # CRC, or execution. Stop at s_tx itself so RL4 exposes the selected NAK.
        for length in (0, 0x0801):
            emu = _emu(image)
            for address, value in (
                (0xE653, 0x5A),
                (0xE423, 0x9C),
                (0xE424, 0x9C),
                (0xE425, length >> 8),
                (0xE426, length & 0xFF),
            ):
                emu.write_byte(address, value)
            emu.write_byte(0xD800, 0x3C)
            emu.asc0.rx_inject(b"\xA5")
            res = _run(
                emu, _SOFTBSL_LOADER, stop_at=(_SOFTBSL_TX,), max_steps=100)
            assert (
                res.final_ip == _SOFTBSL_TX
                and res.exit_reason == "stop_at"
                and (emu.reg.r[4] & 0xFF) == 0x15
                and emu.read_byte(0xD800) == 0x3C
                and bytes(emu.asc0.rx) == b"\xA5"
                and bytes(emu.asc0.tx) == b""
            ), (version, length, res, emu.reg.r[4])

        # Execute the complete resident upload protocol through the ASC0 model:
        # initial ACK, bounded receive, CRC ACK, then the payload call.
        for upload in (
            b"\xDB",
            b"\xDB\x00",
            bytes(index & 0xFF for index in range(0x800)),
        ):
            upload_crc = _crc16(upload)
            emu = _emu(image)
            for address, value in (
                (0xE653, 0x5A),
                (0xE423, 0x9C),
                (0xE424, 0x9C),
                (0xE425, len(upload) >> 8),
                (0xE426, len(upload) & 0xFF),
                (0xE427, upload_crc >> 8),
                (0xE428, upload_crc & 0xFF),
            ):
                emu.write_byte(address, value)
            emu.asc0.rx_inject(upload)
            res = _run(
                emu, _SOFTBSL_LOADER, stop_at=(0xD800,), max_steps=300000)
            assert res.final_ip == 0xD800 and res.exit_reason == "stop_at", (
                version, len(upload), res)
            assert bytes(emu.asc0.tx) == b"\x06\x06", (
                version, len(upload), bytes(emu.asc0.tx))
            assert bytes(
                emu.read_byte(0xD800 + index) for index in range(len(upload))
            ) == upload
            assert not emu.asc0.rx

        payload = f"{version} relocated loader".encode()
        expected = _crc16(payload)
        for supplied, want_ip, want_rl4 in (
            (expected, 0x1C70, 0), (expected ^ 1, 0x1C74, 1),
        ):
            emu = _emu(image)
            for index, value in enumerate(payload):
                emu.write_byte(0xD800 + index, value)
            emu.reg.r[5] = 0xD800 + len(payload)
            emu.write_byte(0xE427, supplied >> 8)
            emu.write_byte(0xE428, supplied & 0xFF)
            res = _run(emu,
                0x1C32, stop_at=(0x1C70, 0x1C74), max_steps=10000)
            assert res.final_ip == want_ip and (emu.reg.r[4] & 0xFF) == want_rl4, (
                version, supplied, res, emu.reg.r[4])

        # Persistent 0x2A door: stock NAK passthrough and matched commit paths.
        emu = _emu(image)
        emu.cpu.csp = 2
        emu.write_byte(0xE653, 0x00)
        res = _run(emu, persistent_hook, stop_at=(nak_handler,), max_steps=100)
        assert res.final_ip == nak_handler, (version, res)

        emu = _emu(image)
        emu.cpu.csp = 2
        emu.write_byte(0xE653, 0x2A)
        res = _run(emu, persistent_hook, stop_at=(0x1A62,), max_steps=100)
        assert res.final_ip == 0x1A62 and emu.read(0xE740) == 1, (
            version, res, emu.read(0xE740))

        # Disposable 0x43 door: stock clear-adapts passthrough and RAM-agent upload.
        emu = _emu(bootstrap)
        emu.cpu.csp = 2
        emu.write_byte(0xE653, 0x00)
        res = _run(emu,
            bootstrap_hook, stop_at=(clear_handler,), max_steps=100)
        assert res.final_ip == clear_handler, (version, res)

        emu = _emu(bootstrap)
        emu.cpu.csp = 2
        emu.write_byte(0xE653, 0x43)
        emu.write_byte(0xE423, 0x9C)
        emu.write_byte(0xE424, 0x9C)
        res = _run(emu,
            bootstrap_hook, stop_at=(bootstrap_tx,), max_steps=100)
        assert res.final_ip == bootstrap_tx and emu.cpu.csp == 3, (version, res)


def _softbsl_agent_payload(agent_key):
    root = ROOT / "engines" / "softbsl"
    manifest = json.loads(
        (root / "agent_manifest.json").read_text(encoding="utf-8"))
    bindings = {
        "amd": (
            "agent.hex", 1498,
            "00eea04eae248f35f77140913bd27a0ffc0003251acd361db2ee80c4b336cb72",
        ),
        "intel_28f200": (
            "agent_28f.hex", 1464,
            "5c35c219cf350f9dfd936be92907b2a44d9c52e0cb40d0831f805f49f8a418c2",
        ),
    }
    expected_name, expected_size, expected_digest = bindings[agent_key]
    metadata = manifest["agents"][agent_key]
    assert metadata["payload"] == expected_name
    assert metadata["payload_size"] == expected_size
    assert metadata["payload_sha256"] == expected_digest
    payload = bytes.fromhex(
        (root / metadata["payload"]).read_text(encoding="ascii"))
    assert len(payload) == metadata["payload_size"]
    assert hashlib.sha256(payload).hexdigest() == metadata["payload_sha256"]
    return payload


def _run_until_state(emu, start, predicate, *, max_steps, visited=None):
    """Continue real firmware until a peripheral-visible state transition."""
    if predicate():
        return

    class _Reached(Exception):
        pass

    def trace(pc, _opcode):
        if visited is not None:
            visited.add(pc)
        if predicate():
            raise _Reached

    emu.cpu.set_trace(trace)
    try:
        try:
            _run(emu, start, max_steps=max_steps)
        except _Reached:
            return
    finally:
        emu.cpu.set_trace(None)
    raise AssertionError(("firmware state transition timed out", hex(start)))


def _agent_exchange(emu, wire, reply_length, *, max_steps=4_000_000):
    """Send one complete host request to the resident agent command loop."""
    start = len(emu.asc0.tx)
    emu.asc0.rx_inject(wire)
    _run_until_state(
        emu,
        emu.cpu.pc,
        lambda: len(emu.asc0.tx) >= start + reply_length,
        max_steps=max_steps,
    )
    reply = bytes(emu.asc0.tx[start:])
    assert len(reply) == reply_length, (wire[:1], len(reply), reply_length)
    return reply


def _agent_address(address):
    assert 0 <= address <= 0x3FFFF
    return address.to_bytes(3, "big")


def _agent_erase(address):
    encoded = _agent_address(address)
    return b"E" + encoded + bytes((sum(encoded) & 0xFF,))


def _agent_chunk(address, data):
    assert len(data) == 0x400
    encoded = _agent_address(address)
    body = encoded + data
    return b"C" + body + _crc16(body).to_bytes(2, "big")


def _agent_read(address, length):
    assert 0 <= length <= 0x400
    return b"K" + _agent_address(address) + length.to_bytes(2, "big")


def _cpu_flash_bytes(image, address, length):
    """Read the below-64K C166 flash window from file/chip order."""
    return bytes(image[(address + offset) ^ 0x4000] for offset in range(length))


def _verify_joined_softbsl_agent_case(firmware_case, chip_case):
    """Run one exact lower-bank firmware/chip lifecycle on the real agents."""
    version, variant, stock_path, door_id, door_hook = firmware_case
    chip, agent_key, wants_amd, amd_device = chip_case
    label = (version, chip)
    patch_ids = [
        patch_id for patch_id in _FULL_STACK_PATCH_IDS[variant]
        if wants_amd or patch_id != "amd_flash"
    ]
    image = _build_from(stock_path, patch_ids)
    assert all(
        patch_ms41.is_applied(image, PATCHES[patch_id])
        for patch_id in patch_ids
    ), (label, "full-stack patch composition")
    assert patch_ms41.is_applied(image, PATCHES["amd_flash"]) == wants_amd
    payload = _softbsl_agent_payload(agent_key)

    emu = _emu(image)
    flash = (
        AmdFlashModel(device=amd_device, busy_reads=4)
        if wants_amd else FlashModel(busy_reads=4)
    )
    emu.mem.flash_model = flash
    Timer1(tick=1).attach(emu.peripherals)
    original_eeprom = bytes(
        (address * 17 + 3) & 0xFF for address in range(Eeprom24C04.SIZE))
    eeprom = Eeprom24C04(original_eeprom)
    eeprom.attach(emu.peripherals)

    # Bind the family-specific persistent door to the common stock EEPROM
    # commit and watchdog-reset spin before executing it.
    door = PATCHES[door_id]
    cave_edit = next(
        edit for edit in door["edits"]
        if edit["off"] == door["cave"]["base"])
    cave_data = bytes.fromhex(cave_edit["data"])
    commit_spin = bytes.fromhex("da00621a0dff")
    cave_offset = cave_data.index(commit_spin)
    commit_call = (door["cave"]["base"] ^ 0x4000) + cave_offset
    reset_spin = commit_call + 4
    assert _code_bytes(emu, commit_call, len(commit_spin)) == commit_spin

    emu.cpu.csp = 2
    emu.write_byte(0xE653, 0x2A)
    reset_count = emu.reset_count
    door_trace = set()
    _run_until_state(
        emu,
        door_hook,
        lambda: emu.reset_count > reset_count,
        max_steps=4_000_000,
        visited=door_trace,
    )
    assert (
        emu.last_reset_reason == "watchdog"
        and _SOFTBSL_COMMIT in door_trace
        and reset_spin in door_trace
        and bytes(eeprom.data) != original_eeprom
        and any(item[0] == "write" for item in eeprom.transactions)
    ), (label, "persistent door/EEPROM commit/watchdog reset")
    door_write_count = sum(
        item[0] == "write" for item in eeprom.transactions)

    visited = []
    emu.cpu.set_trace(lambda pc, _opcode: visited.append(pc))
    recovery = _run(
        emu,
        emu.cpu.pc,
        stop_at=(BOOT_EXIT, RECOVER_EXIT),
        max_steps=300000,
    )
    emu.cpu.set_trace(None)
    assert (
        recovery.final_pc == RECOVER_EXIT
        and recovery.exit_reason == "stop_at"
        and CAVE_CPU not in visited
        and emu.read_byte(_SOFTBSL_MARKER) == 1
    ), (label, "door-to-CalGuard recovery", recovery)

    # Route through the common recovery dispatcher and SA1 loader, including
    # both ACKs, payload CRC, and exact RAM readback.
    upload_crc = _crc16(payload)
    for address, value in (
        (0xE653, 0x5A),
        (0xE423, 0x9C),
        (0xE424, 0x9C),
        (0xE425, len(payload) >> 8),
        (0xE426, len(payload) & 0xFF),
        (0xE427, upload_crc >> 8),
        (0xE428, upload_crc & 0xFF),
    ):
        emu.write_byte(address, value)
    emu.asc0.rx_inject(payload)
    routed = _run(
        emu, _SOFTBSL_RECOVERY_DISPATCH,
        stop_at=(_SOFTBSL_LOADER,), max_steps=20)
    assert (
        routed.final_ip == _SOFTBSL_LOADER
        and routed.exit_reason == "stop_at"
    ), (label, "SA1 loader route", routed)
    loaded = _run(
        emu, _SOFTBSL_LOADER,
        stop_at=(_SOFTBSL_AGENT,), max_steps=300000)
    assert (
        loaded.final_ip == _SOFTBSL_AGENT
        and loaded.exit_reason == "stop_at"
        and bytes(emu.asc0.tx) == b"\x06\x06"
        and bytes(
            emu.read_byte(_SOFTBSL_AGENT + offset)
            for offset in range(len(payload))
        ) == payload
        and not emu.asc0.rx
    ), (label, "manifest-bound agent upload", loaded)

    emu.asc0.tx.clear()
    _run_until_state(
        emu, _SOFTBSL_AGENT,
        lambda: len(emu.asc0.tx) == 1, max_steps=100000)
    assert bytes(emu.asc0.tx) == b"\xA5", (label, "agent banner")
    backing = emu.mem.image
    assert bytes(backing) == image

    # CPU 0x0000 is protected SA1. CPU 0x2000 is the adjacent 8 KiB
    # writable sector/block on all three supported lower-bank geometries.
    command_count = len(flash.commands)
    assert _agent_exchange(emu, _agent_erase(0x0000), 1) == b"\x03"
    assert len(flash.commands) == command_count and bytes(backing) == image
    assert _agent_exchange(emu, _agent_erase(0x2000), 1) == b"\x01"
    assert _cpu_flash_bytes(backing, 0x2000, 0x2000) == b"\xFF" * 0x2000
    assert _cpu_flash_bytes(backing, 0x0000, 0x2000) == (
        _cpu_flash_bytes(image, 0x0000, 0x2000))
    assert _cpu_flash_bytes(backing, 0x4000, 0x4000) == (
        _cpu_flash_bytes(image, 0x4000, 0x4000))

    chunks = []
    for address in range(0x2000, 0x4000, 0x400):
        data = _cpu_flash_bytes(image, address, 0x400)
        if data != b"\xFF" * 0x400:
            chunks.append(_agent_chunk(address, data))
    assert _agent_exchange(
        emu, b"".join(chunks), len(chunks), max_steps=16_000_000
    ) == b"\x01" * len(chunks), (label, "SA2 program")
    expected = _cpu_flash_bytes(image, 0x2000, 32)
    readback = _agent_exchange(
        emu, _agent_read(0x2000, len(expected)), len(expected) + 2)
    assert readback[:-2] == expected
    assert int.from_bytes(readback[-2:], "big") == _crc16(
        _agent_address(0x2000) + expected)
    assert bytes(backing) == image, (label, "SA2 byte-exact restore")

    if wants_amd:
        assert (0x6000, 0x0030) in flash.commands
        assert flash.rejected == 0
    else:
        errors = flash.SR_ERASE_ERR | flash.SR_PROG_ERR | flash.SR_VPP_ERR
        assert (
            flash.ERASE_SETUP in flash.commands
            and flash.ERASE_CONFIRM in flash.commands
            and not flash.status & errors
            and flash.pending is None
        ), (label, "Intel CUI/WSM state")

    # Retain the deeper 32 KiB AMD-sector proof once, without multiplying it
    # across the 12-row compatibility matrix.
    if variant == "SS1v2" and chip == "29f400":
        assert _cpu_flash_bytes(image, 0xC000, 0x4000) == b"\xFF" * 0x4000
        assert _agent_exchange(emu, _agent_erase(0x8000), 1) == b"\x01"
        assert _cpu_flash_bytes(
            backing, 0x8000, 0x8000) == b"\xFF" * 0x8000
        sector_chunks = b"".join(
            _agent_chunk(
                address, _cpu_flash_bytes(image, address, 0x400))
            for address in range(0x8000, 0xC000, 0x400)
        )
        assert _agent_exchange(
            emu, sector_chunks, 16, max_steps=24_000_000
        ) == b"\x01" * 16
        read_address = 0xBFE0
        expected = _cpu_flash_bytes(image, read_address, 32)
        readback = _agent_exchange(
            emu, _agent_read(read_address, len(expected)), len(expected) + 2)
        assert readback[:-2] == expected
        assert int.from_bytes(readback[-2:], "big") == _crc16(
            _agent_address(read_address) + expected)
        assert bytes(backing) == image
        assert (0xC000, 0x0030) in flash.commands

    # R 9C 9C must call the same stock commit, persist marker zero in the
    # physical EEPROM, software-reset, and select normal CalGuard boot.
    assert emu.read_byte(_SOFTBSL_MARKER) == 1
    reset_count = emu.reset_count
    finalizer_trace = set()
    emu.asc0.rx_inject(b"R\x9C\x9C")
    _run_until_state(
        emu,
        emu.cpu.pc,
        lambda: emu.reset_count > reset_count,
        max_steps=4_000_000,
        visited=finalizer_trace,
    )
    final_lo, final_hi, final_value = _SOFTBSL_FINALIZER_EEPROM
    assert (
        emu.last_reset_reason == "software"
        and _SOFTBSL_COMMIT in finalizer_trace
        and bytes(eeprom.data[final_lo:final_hi]) == final_value
        and sum(item[0] == "write" for item in eeprom.transactions)
        > door_write_count
        and bytes(backing) == image
    ), (label, "agent EEPROM finalizer/software reset")

    visited = []
    emu.cpu.set_trace(lambda pc, _opcode: visited.append(pc))
    normal = _run(
        emu,
        emu.cpu.pc,
        stop_at=(BOOT_FALLBACK, RECOVER_EXIT),
        max_steps=300000,
    )
    emu.cpu.set_trace(None)
    assert (
        normal.final_pc == BOOT_FALLBACK
        and normal.exit_reason == "stop_at"
        and CAVE_CPU in visited
        and emu.read_byte(_SOFTBSL_MARKER) == 0
    ), (label, "agent finalize-to-normal boot", normal)


def verify_joined_softbsl_agent_cycle():
    """Run the exact 4-firmware x 3-lower-chip catalog matrix."""
    for firmware_case in _SOFTBSL_LIFECYCLE_FIRMWARE:
        for chip_case in _SOFTBSL_LIFECYCLE_CHIPS:
            _verify_joined_softbsl_agent_case(firmware_case, chip_case)


IGNITION_HOOKS = (0xD92A, 0xD98E)       # IP values while CSP=3
IGNITION_CAVE_CPU = 0x3DC70             # full CPU address used by trace
IGNITION_STOCK_REPLAY_CPU = 0x3DCDA     # cave's displaced ANDB P1L,RL1
IGNITION_CONTROL_HOOK = 0x755A          # IP while CSP=2
IGNITION_SINGLE_MASKS = bytes.fromhex("7ebd7bb76f9f")
IGNITION_PAIRED_MASKS = bytes.fromhex("76ad5bb66d9b")


def _seed_ignition_gate(emu, *, rpm, pins=0, request=False):
    """Seed only V7's inputs; P1L starts high/off like the native startup path."""
    emu.write_byte(0xFC3C, rpm)
    emu.write_byte(0xFD60, pins & 0xFF)
    emu.write_byte(0xFD61, (pins >> 8) & 0xFF)
    emu.write(0xFD5A, 0x0080 if request else 0)
    emu.write_byte(0xFF04, 0xFF)


def _run_ignition(
        image, *, rpm, pins=0, request=False, hook=IGNITION_HOOKS[0], mask=0xFE):
    """Execute one real CC6-ISR hook through both complete V7 return paths."""
    emu = _emu(image, dpp0=4)
    _seed_ignition_gate(emu, rpm=rpm, pins=pins, request=request)
    emu.reg.r[1] = 0x1200 | mask      # selected native clear mask in RL1
    emu.reg.r[4] = 0xA55A             # V7 must preserve the complete r4
    visited = []
    emu.cpu.set_trace(lambda pc, _opcode: visited.append(pc))
    emu.cpu.csp = 3
    sp0 = emu.state()["regs"]["sp"]
    res = _run(emu,
        hook,
        stop_at=(hook + 4,),
        max_steps=300,
    )
    replayed = IGNITION_STOCK_REPLAY_CPU in visited
    p1l = emu.read_byte(0xFF04)
    cut = not replayed and p1l == 0xFF
    stock = replayed and p1l == mask
    hook_cpu = 0x30000 | hook
    hygiene = (
        res.final_ip == hook + 4 and res.exit_reason == "stop_at"
        and hook_cpu in visited and IGNITION_CAVE_CPU in visited
        and res.regs["dpp"][0] == 4
        and emu.reg.r[4] == 0xA55A
        and emu.reg.r[1] == (0x1200 | mask)
        and res.regs["sp"] == sp0
    )
    return cut, stock, hygiene


def _verify_native_cc6_mask_matrix(cut_image, stock_image):
    """Cover both native mask tables and both recurring charge sites.

    ADB2 clears one cylinder output plus companion P1L.6/.7. ADC4 clears a
    paired cylinder set plus a companion bit. V7 intentionally suppresses the
    complete scheduled ANDB transaction, whatever mask the stock ISR selected.
    """
    sources = (
        ("primary single", 0xD91A, 0xD92A, 0x0000, IGNITION_SINGLE_MASKS),
        ("primary paired", 0xD91A, 0xD92A, 0x0100, IGNITION_PAIRED_MASKS),
        ("secondary single", 0xD986, 0xD98E, 0x0000, IGNITION_SINGLE_MASKS),
    )
    for source, entry, hook, fd5e, masks in sources:
        for index, mask in enumerate(masks):
            for wants_cut, image in ((True, cut_image), (False, stock_image)):
                emu = _emu(image, dpp0=4)
                _seed_ignition_gate(emu, rpm=0xC8)
                emu.write_byte(0xFA5F, index)
                emu.write(0xFD5E, fd5e)
                emu.reg.r[4] = 0xA55A
                visited = []
                emu.cpu.set_trace(lambda pc, _opcode: visited.append(pc))
                emu.cpu.csp = 3
                sp0 = emu.state()["regs"]["sp"]
                res = _run(emu, entry, stop_at=(hook + 4,), max_steps=300)
                replayed = IGNITION_STOCK_REPLAY_CPU in visited
                expected_p1l = 0xFF if wants_cut else mask
                assert (
                    res.final_ip == hook + 4 and res.exit_reason == "stop_at"
                    and (0x30000 | hook) in visited and IGNITION_CAVE_CPU in visited
                    and replayed == (not wants_cut)
                    and emu.read_byte(0xFF04) == expected_p1l
                    and (emu.reg.r[1] & 0xFF) == mask
                    and emu.reg.r[4] == 0xA55A
                    and res.regs["dpp"][0] == 4 and res.regs["sp"] == sp0
                ), (source, index, hex(mask), wants_cut, res, visited[-20:])


SIR_PIN_WORDS = {
    0x01: 0x0200,  # SIR selector 01: P1.12 / pin 80 / fd60.9
    0x02: 0x0100,  # SIR selector 02: P1.13 / pin 81 / fd60.8
    0x04: 0x0080,  # SIR selector 04: P1.14 / pin 82 / fd60.7
}
SIR_PIN_BYTES = {
    selector: (word & 0xFF, word >> 8)
    for selector, word in SIR_PIN_WORDS.items()
}


def verify_ignition_cut_v7(case_image=_case_image):
    # name, CUTSW, CUTRPM, RPM, pins, LC request, wants cut
    cases = [
        ("always above", 0x00, 0x7D, 0xC8, 0, False, True),
        ("always equal", 0x00, 0x7D, 0x7D, 0, False, True),
        ("always below", 0x00, 0x7D, 0x64, 0, False, False),
        ("off", 0xFF, 0x7D, 0xC8, 0, False, False),
        ("pin80 set", 0x01, 0x7D, 0xC8, SIR_PIN_WORDS[0x01], False, True),
        ("pin80 clear", 0x01, 0x7D, 0xC8, 0, False, False),
        ("pin81", 0x02, 0x7D, 0xC8, SIR_PIN_WORDS[0x02], False, True),
        ("pin82", 0x04, 0x7D, 0xC8, SIR_PIN_WORDS[0x04], False, True),
        ("launch request", 0xFF, 0x7D, 0x64, 0, True, True),
        ("no launch request", 0xFF, 0x7D, 0xC8, 0, False, False),
    ]
    for name, switch, limit, rpm, pins, request, want_cut in cases:
        image = case_image({"CUTSW": switch, "CUTRPM": limit})
        cut, stock, hygiene = _run_ignition(
            image, rpm=rpm, pins=pins, request=request)
        assert cut == want_cut and stock == (not want_cut) and hygiene, (
            name, cut, stock, hygiene)

    # Both byte-identical CC6 ISR sites must make the same cut/stock decision.
    for hook in IGNITION_HOOKS:
        cut_image = case_image({"CUTSW": 0x00, "CUTRPM": 0x7D})
        cut, stock, hygiene = _run_ignition(cut_image, rpm=0xC8, hook=hook)
        assert cut and not stock and hygiene, (hex(hook), cut, stock, hygiene)
        stock_image = case_image({"CUTSW": 0xFF, "CUTRPM": 0x7D})
        cut, stock, hygiene = _run_ignition(stock_image, rpm=0xC8, hook=hook)
        assert not cut and stock and hygiene, (hex(hook), cut, stock, hygiene)

    # Reach both hooks through every native single/paired mask selection. The
    # stock path must execute 0x65 and write P1L; the cut path must leave it high.
    native_cut_image = case_image({"CUTSW": 0x00, "CUTRPM": 0x7D})
    native_stock_image = case_image({"CUTSW": 0xFF, "CUTRPM": 0x7D})
    _verify_native_cc6_mask_matrix(native_cut_image, native_stock_image)


A_THRESHOLDS = {"LC_ARMSPEED": 0x05, "LC_MAXSPEED": 0x1E, "LC_MINTPS": 0x80}


def _run_launch_a(image, *, speed, tps, fd60, fd61, latch):
    emu = _emu(image)
    sp0 = emu.state()["regs"]["sp"]
    emu.write_byte(0xF19A, speed)
    emu.write_byte(0xE8D0, tps)
    emu.write_byte(0xFD60, fd60)
    emu.write_byte(0xFD61, fd61)
    emu.write(0xFD5A, 0x0040 if latch else 0)
    emu.cpu.csp = 3
    res = _run(emu, 0x9928, stop_at=(0x992C,), max_steps=500)
    latch_out = bool(emu.read(0xFD5A) & 0x40)
    spark = bool(emu.read(0xFD5A) & 0x80)
    hygiene = (
        res.final_ip == 0x992C
        and res.exit_reason == "stop_at"
        and res.regs["sp"] == sp0
        and res.regs["dpp"][0] == 5
        and res.regs["dpp"][1] == 5
    )
    return latch_out, spark, hygiene


def verify_launch_brain(case_image=_case_image):
    # name, LC_SW, polarity, speed, TPS, fd60, fd61, initial latch, wanted latch
    cases = [
        ("off", 0xFF, 0, 0, 0xC0, 0, 0, 1, 0),
        ("always", 0x00, 0, 0, 0xC0, 0, 0, 0, 1),
        ("pin80 arm", 0x01, 0, 0, 0xC0, *SIR_PIN_BYTES[0x01], 0, 1),
        ("pin80 hold zero", 0x01, 0, 0, 0xC0, 0, 0, 0, 0),
        ("pin80 hold one", 0x01, 0, 0, 0xC0, 0, 0, 1, 1),
        ("release speed", 0x01, 0, 0x28, 0xC0, *SIR_PIN_BYTES[0x01], 1, 0),
        ("release TPS", 0x01, 0, 0, 0x40, *SIR_PIN_BYTES[0x01], 1, 0),
        ("mid-shift no arm", 0x01, 0, 0x14, 0xC0, *SIR_PIN_BYTES[0x01], 0, 0),
        ("rollout hold", 0x01, 0, 0x14, 0xC0, 0, 0, 1, 1),
        ("active-low arm", 0x01, 1, 0, 0xC0, 0, 0, 0, 1),
        ("active-low high", 0x01, 1, 0, 0xC0, *SIR_PIN_BYTES[0x01], 0, 0),
        ("pin81", 0x02, 0, 0, 0xC0, *SIR_PIN_BYTES[0x02], 0, 1),
        ("pin82", 0x04, 0, 0, 0xC0, *SIR_PIN_BYTES[0x04], 0, 1),
        ("bad selector", 0x03, 0, 0, 0xC0, 0x80, 2, 1, 0),
    ]
    for name, switch, polarity, speed, tps, fd60, fd61, latch, want in cases:
        values = {"LC_SW": switch, "LC_CLUTCHPOL": polarity, **A_THRESHOLDS}
        image = case_image(values)
        got, spark, hygiene = _run_launch_a(
            image, speed=speed, tps=tps, fd60=fd60, fd61=fd61, latch=latch)
        assert got == bool(want) and not spark and hygiene, (name, got, spark, hygiene)


def verify_launch_fuel_soft_cave():
    # name, latch, cut type, max RPM, stock f014, fd30.4, result, continuation
    cases = [
        ("not armed / skip", 0, 0, 0x7D, 0xCB, 0, 0xCB, 0x07E8),
        ("not armed / clamp path", 0, 0, 0x7D, 0xCB, 1, 0xCB, 0x07D6),
        ("armed fuel clamp", 1, 0, 0x7D, 0xCB, 0, 0x7D, 0x07E8),
        ("armed fuel keep", 1, 0, 0xD0, 0xCB, 1, 0xCB, 0x07D6),
        ("armed ignition", 1, 1, 0x7D, 0xCB, 0, 0xCB, 0x07E8),
    ]
    for name, latch, cut_type, maxrpm, stock_limit, fd30_4, want, continuation in cases:
        image = _case_image({"LC_CUTTYPE": cut_type, "LC_MAXRPM": maxrpm})
        emu = _emu(image)
        sp0 = emu.state()["regs"]["sp"]
        emu.write(0xFD5A, 0x0040 if latch else 0)
        emu.write(0xFD30, 0x0010 if fd30_4 else 0)
        emu.write_byte(0xF014, stock_limit)
        emu.cpu.csp = 2
        res = _run(emu, 0x07D2, stop_at=(0x07D6, 0x07E8), max_steps=300)
        hygiene = (res.final_ip == continuation and res.exit_reason == "stop_at"
                   and res.regs["sp"] == sp0 and res.regs["dpp"][0] == 5)
        assert emu.read_byte(0xF014) == want and hygiene, (
            name, emu.read_byte(0xF014), want, res, hygiene)


def verify_launch_fuel_soft_cave_ms413():
    # MS41.3 has a different displaced continuation at the F014 clamp hook.
    cases = [
        ("not armed", 0, 0, 0x7D, 0xCB, 0xCB),
        ("armed fuel clamp", 1, 0, 0x7D, 0xCB, 0x7D),
        ("armed fuel keep", 1, 0, 0xD0, 0xCB, 0xCB),
        ("armed ignition", 1, 1, 0x7D, 0xCB, 0xCB),
    ]
    for name, latch, cut_type, maxrpm, stock_limit, want in cases:
        image = _case_image_413({"LC_CUTTYPE": cut_type, "LC_MAXRPM": maxrpm})
        emu = _emu(image)
        sp0 = emu.state()["regs"]["sp"]
        emu.write(0xFD5A, 0x0040 if latch else 0)
        emu.write_byte(0xF014, stock_limit)
        emu.cpu.csp = 2
        res = _run(emu, 0x07D2, stop_at=(0x07D6,), max_steps=300)
        hygiene = (
            res.final_ip == 0x07D6
            and res.exit_reason == "stop_at"
            and res.regs["sp"] == sp0
            and res.regs["dpp"][0] == 5
            and res.regs["dpp"][1] == 5
        )
        assert emu.read_byte(0xF014) == want and hygiene, (
            name, emu.read_byte(0xF014), want, res, hygiene)


def verify_launch_fuel_hard_comparator(case_image=_case_image):
    """Exercise both real DB87 CALL sites and their untouched stock branches.

    Current Launch uses LC_HARDRPM rather than a DB86/DB87-derived gap. It also clamps a
    configured hard threshold below soft, and treats 0xFF as an unconfigured
    soft+3 fallback with saturation. Persistent DB86/DB87 must remain unchanged.
    """
    sites = (
        # native MOVB start; branch outcome below threshold / at-or-above
        (0x0886, (0x0890, 0x08E0)),
        (0x0926, (0x0942, 0x0930)),
    )
    # name, latch, mode, soft, hard, rpm, expected branch. DB86 is deliberately
    # far from DB87 so any accidental return to V3's stock-gap calculation fails.
    cases = [
        ("configured below hard", 1, 0, 0x7D, 0x90, 0x8F, 0),
        ("configured at hard", 1, 0, 0x7D, 0x90, 0x90, 1),
        ("configured above hard", 1, 0, 0x7D, 0x90, 0x91, 1),
        ("below-soft clamp below", 1, 0, 0x7D, 0x70, 0x7C, 0),
        ("below-soft clamp at soft", 1, 0, 0x7D, 0x70, 0x7D, 1),
        ("FF fallback below", 1, 0, 0x7D, 0xFF, 0x7F, 0),
        ("FF fallback at soft+3", 1, 0, 0x7D, 0xFF, 0x80, 1),
        ("FF saturation below", 1, 0, 0xFE, 0xFF, 0xFE, 0),
        ("FF saturation at max", 1, 0, 0xFE, 0xFF, 0xFF, 1),
        ("not armed uses stock below", 0, 0, 0x7D, 0x90, 0xCD, 0),
        ("not armed uses stock at", 0, 0, 0x7D, 0x90, 0xCE, 1),
        ("ignition mode uses stock", 1, 1, 0x7D, 0x90, 0xCD, 0),
    ]
    for name, latch, cut_type, soft, hard, rpm, result_index in cases:
        image = case_image({
            "LC_CUTTYPE": cut_type,
            "LC_MAXRPM": soft,
            "LC_HARDRPM": hard,
        })
        for entry, outcomes in sites:
            emu = _emu(image)
            sp0 = emu.state()["regs"]["sp"]
            emu.write(0xFD5A, 0x0040 if latch else 0)
            emu.write_byte(0xFC3C, rpm)
            emu.write_byte(0xDB86, 0x10)
            emu.write_byte(0xDB87, 0xCE)
            emu.cpu.csp = 2
            res = _run(emu, entry, stop_at=outcomes, max_steps=300)
            assert (res.final_ip == outcomes[result_index]
                    and res.exit_reason == "stop_at"
                    and res.regs["sp"] == sp0
                    and res.regs["dpp"][0] == 5
                    and res.regs["dpp"][1] == 5
                    and emu.read_byte(0xDB86) == 0x10
                    and emu.read_byte(0xDB87) == 0xCE), (
                        name, entry, outcomes, res,
                        emu.read_byte(0xDB86), emu.read_byte(0xDB87))


def verify_composed_launch_and_ignition(case_image=_case_image):
    # name, CUTSW, CUTRPM, LC_SW, LC mode, LC RPM, actual RPM, wants spark cut
    cases = [
        ("LC only", 0xFF, 0xD7, 0x00, 0x01, 0x7D, 0x8C, True),
        ("ignition only", 0x00, 0xD7, 0xFF, 0x01, 0x7D, 0xE0, True),
        ("LC threshold", 0x00, 0xD7, 0x00, 0x01, 0x7D, 0x96, True),
        ("below both", 0x00, 0xD7, 0x00, 0x01, 0x7D, 0x50, False),
        ("ignition threshold", 0x00, 0xD7, 0xFF, 0x01, 0x7D, 0xE0, True),
    ]
    for name, cutsw, cutrpm, lc_sw, cut_type, lc_rpm, rpm, want in cases:
        image = case_image({
            "CUTSW": cutsw, "CUTRPM": cutrpm, "LC_SW": lc_sw,
            "LC_CUTTYPE": cut_type, "LC_MAXRPM": lc_rpm, **A_THRESHOLDS,
        })
        emu = _emu(image)
        emu.write_byte(0xFC3C, rpm)
        emu.write(0xFD5A, 0)
        emu.cpu.csp = 3
        res = _run(emu, 0x9928, stop_at=(0x992C,), max_steps=500)
        assert res.final_ip == 0x992C, (name, res)
        request = bool(emu.read(0xFD5A) & 0x80)

        cut, stock, hygiene = _run_ignition(
            image, rpm=rpm, request=request)
        assert cut == want and stock == (not want) and hygiene, (
            name, request, cut, stock, want, hygiene)


def _seed_older_feature_inputs(emu, layout, low, high):
    latched = low | (high << 8)
    input_latch = layout.get("input_latch")
    if input_latch is None:
        emu.write_byte(layout["input_bytes"][0], low)
        emu.write_byte(layout["input_bytes"][1], high)
        return

    assert latched & ~0x0380 == 0, hex(latched)
    raw_port = (
        ((latched & 0x0080) << 7)
        | ((latched & 0x0100) << 5)
        | ((latched & 0x0200) << 3)
    )
    emu.write(0xFF04, raw_port)
    emu.write(layout["input_bytes"][0], 0)
    emu.cpu.csp = 3
    res = _run(emu,
        input_latch[0], stop_at=(input_latch[1],), max_steps=10)
    assert (
        res.final_ip == input_latch[1]
        and res.exit_reason == "stop_at"
        and emu.read(layout["input_bytes"][0]) == latched
    ), (hex(raw_port), hex(latched), res)


def _service_foreground_watchdog(emu, variant):
    """Run the real T0-pending foreground path through SRVWDT on the same state."""
    pending, entry, exit_address, task, service = _WATCHDOG_LAYOUTS[variant]
    architectural = (emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp))
    resets = emu.reset_count
    emu.watchdog.value = 0xFE00
    emu.watchdog.prescaler_select = 1
    emu.write(pending, emu.read(pending) | 0x4000)
    visited = []
    emu.cpu.set_trace(lambda pc, _opcode: visited.append(pc))
    res = _run(emu, entry, stop_at=(exit_address,), max_steps=500)
    return (
        res.final_pc == exit_address
        and res.exit_reason == "stop_at"
        and task in visited
        and service in visited
        and emu.read(0xFFAE) == 0xF501
        and 0xF500 <= emu.watchdog.value < 0xF600
        and emu.reset_count == resets
        and not emu.read(pending) & 0x4000
        and (emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp)) == architectural
    )


def _verify_cc6_interrupt_entry(emu, variant):
    """Enter and return through the exact stock CC6 vector and ISR."""
    service = _WATCHDOG_LAYOUTS[variant][4]
    emu.reg.sp = 0xFC00
    emu.cpu.psw.unpack(0x0800)
    architectural = (emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp))
    emu.write_byte(0xFF84, 0x4C)  # CC6IE, ILVL 3, GLVL 0
    emu.interrupts.schedule_irq("CC6", after=1)
    emu.cpu.csp, emu.cpu.ip = service >> 16, service & 0xFFFF
    emu.cpu.step()
    assert emu.cpu.pc == 0x0058
    assert emu.mem.read_word_direct(0xFBFA) == (service + 4) & 0xFFFF
    assert emu.mem.read_word_direct(0xFBFC) == service >> 16
    assert emu.mem.read_word_direct(0xFBFE) == 0x0800
    emu.cpu.step()  # exact stock vector bytes: JMPS 00:2158
    assert emu.cpu.pc == 0x2158
    visited = []
    emu.cpu.set_trace(lambda pc, _opcode: visited.append(pc))
    res = _run(emu,
        emu.cpu.pc,
        stop_at=(service + 4,),
        max_steps=1000,
    )
    assert (
        res.final_pc == service + 4
        and res.exit_reason == "stop_at"
        and _CC6_IGNITION_HOOKS[variant] in visited
        and (
            emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp)
        ) == architectural
    ), (variant, "CC6 ISR return", res)


def _code_bytes(emu, address, length):
    return bytes(
        emu.mem.read_code_byte((address + index) & 0xFFFF, address >> 16)
        for index in range(length)
    )


def _native_ds2(emu, dispatcher, tx_arm, command, index):
    """Run native command handlers and TX PEC after the decoded RX fields."""
    emu.asc0.tx.clear()
    emu.write_byte(0xE653, command)
    emu.write_byte(0xE422, command)
    emu.write_byte(0xE423, index)
    _call_native(emu, dispatcher, max_steps=100000)
    _call_native(emu, tx_arm, max_steps=100000)
    transfers = 0
    while emu.read(0xFEC4) & 0xFF:
        emu.interrupts.request("ASC0_TX")
        assert emu.interrupts.pec.service_request(0xFF6C)
        transfers += 1
        assert transfers < 0x100
    frame = bytes(emu.asc0.tx)
    checksum_xor = 0
    for value in frame:
        checksum_xor ^= value
    assert checksum_xor == 0
    return frame


def _verify_self_test_descriptor_catalog(image, variant):
    """Bind the generic DTC names to what these exact programs install."""
    bases = (
        tuple(range(0xA582, 0xA8C3, 0x10))
        if variant == "1429861"
        else tuple(range(0xA5DC, 0xABAD, 0x10))
    )
    # Logical DPP2 with native DPP2=0 gives phys base-0x8000; flash file order
    # then XORs 0x4000, hence base ^ 0xC000 (this is not CSP:IP mapping).
    candidates = {
        base: image[base ^ 0xC000]
        for base in bases
        if image[base ^ 0xC000] in {48, 58, 100, 170}
    }
    expected = 0xA842 if variant == "1429861" else 0xA88C
    assert candidates == {expected: 100}, (
        variant, "exact self-test descriptor catalog", candidates)


def verify_watchdog_dtc100_ds2(image, variant):
    """Prove WDT -> native DTC100 -> DS2 read/clear on one exact full stack."""
    (boot_stop, dispatcher, tx_arm, evaluator,
     status_address, reason_address) = _DTC100_LAYOUTS[variant]
    _verify_self_test_descriptor_catalog(image, variant)
    emu = _load_emulator(
        image, force_variant=variant, silicon_reset=True)
    assert (
        emu.read_byte(status_address) == 0
        and emu.read(reason_address) == 0
        and emu.read(0xEA0C) == 0
        and emu.read_byte(0xEA18) == 0
    )
    assert _code_bytes(emu, dispatcher, 8) == bytes.fromhex(
        "88808890f3f853e6")
    assert _code_bytes(emu, tx_arm, 8) == bytes.fromhex(
        "6eb86eb74ed8e7f8")
    evaluator_signature = (
        "c2f4d2ebf6f438fd"
        if variant == "1429861" else "8890f09cc2f42aec"
    )
    assert _code_bytes(emu, evaluator, 8) == bytes.fromhex(
        evaluator_signature)
    assert _code_bytes(emu, boot_stop, 8) == bytes.fromhex(
        "e6b85400e6b71b00")

    emu.watchdog.enabled = True
    emu.watchdog.prescaler_select = 0
    emu.watchdog.value = 0xFFFF
    reset = _run(emu, 0x0434, stop_at=(0x04B0,), max_steps=100)
    assert (
        reset.final_pc == 0x04B0
        and reset.exit_reason == "stop_at"
        and emu.reset_count == 1
        and emu.last_reset_reason == "watchdog"
    ), (variant, reset)

    startup = _run(
        emu, emu.cpu.pc, stop_at=(boot_stop,), max_steps=400000)
    status = emu.read_byte(status_address)
    reason = emu.read(reason_address)
    assert (
        startup.final_pc == boot_stop
        and startup.exit_reason == "stop_at"
        and status & 0x60 == 0x60
        and reason == emu.read(0xEA0C) == 3
        and emu.read_byte(0xEA18) == 1
        and emu.read_byte(0xEA19) == 1
    ), (variant, startup, hex(status), hex(reason))

    frame = _native_ds2(emu, dispatcher, tx_arm, 0x04, 1)
    records = parse_ds2_dtc_response(frame[3:-1])
    assert (
        frame[:3] == b"\x12\x0F\xA0"
        and len(frame) == emu.read_byte(0xE521) == 15
        and len(records) == 1
        and records[0].code == 100
        and records[0].is_active
        and records[0].status_raw == status
        and int.from_bytes(records[0].raw_record[6:8], "little") == reason
        and emu.reset_count == 1
    ), (variant, frame.hex(" "), records)

    clear = _native_ds2(emu, dispatcher, tx_arm, 0x05, 0)
    assert clear == b"\x12\x04\xA0\xB6", (variant, clear.hex(" "))
    assert (
        emu.read_byte(status_address) == 0
        and emu.read(reason_address) == 0
        and emu.read(0xEA0C) == 0
        and emu.read_byte(0xEA18) == 0
        and emu.read_byte(0xEA19) == 0
    ), (variant, "native DTC clear RAM")
    empty = _native_ds2(emu, dispatcher, tx_arm, 0x04, 1)
    assert empty == b"\x12\x06\xA0\x00\x00\xB4", (
        variant, empty.hex(" "))

def _run_older_ignition(
        layout, image, *, rpm, pins=0, request=False, hook=None, mask=0xFE):
    hook = layout["ignition_hooks"][0] if hook is None else hook
    emu = _emu(image, dpp0=4)
    emu.write_byte(layout["rpm_address"], rpm)
    emu.write_byte(0xFD60, pins & 0xFF)
    emu.write_byte(0xFD61, (pins >> 8) & 0xFF)
    emu.write(0xFD5A, 0x0080 if request else 0)
    emu.write_byte(0xFF04, 0xFF)
    emu.reg.r[1] = 0x1200 | mask
    emu.reg.r[4] = 0xA55A
    visited = []
    emu.cpu.set_trace(lambda pc, _opcode: visited.append(pc))
    emu.cpu.csp = 3
    sp0 = emu.state()["regs"]["sp"]
    res = _run(emu, hook, stop_at=(hook + 4,), max_steps=300)
    replayed = layout["ignition_replay_cpu"] in visited
    hygiene = (
        res.final_ip == hook + 4 and res.exit_reason == "stop_at"
        and layout["ignition_cave_cpu"] in visited
        and res.regs["dpp"][0] == 4
        and emu.reg.r[4] == 0xA55A
        and emu.reg.r[1] == (0x1200 | mask)
        and res.regs["sp"] == sp0
    )
    return (
        not replayed and emu.read_byte(0xFF04) == 0xFF,
        replayed and emu.read_byte(0xFF04) == mask,
        hygiene,
    )


def _verify_older_ignition(layout):
    cases = [
        ("always above", 0x00, 0x7D, 0xC8, 0, False, True),
        ("always equal", 0x00, 0x7D, 0x7D, 0, False, True),
        ("always below", 0x00, 0x7D, 0x64, 0, False, False),
        ("off", 0xFF, 0x7D, 0xC8, 0, False, False),
        ("pin80", 0x01, 0x7D, 0xC8, SIR_PIN_WORDS[0x01], False, True),
        ("pin81", 0x02, 0x7D, 0xC8, SIR_PIN_WORDS[0x02], False, True),
        ("pin82", 0x04, 0x7D, 0xC8, SIR_PIN_WORDS[0x04], False, True),
        ("launch request", 0xFF, 0xD7, 0x64, 0, True, True),
    ]
    for name, switch, limit, rpm, pins, request, wants_cut in cases:
        image = _case_image_older(
            layout, {"CUTSW": switch, "CUTRPM": limit})
        cut, stock, hygiene = _run_older_ignition(
            layout, image, rpm=rpm, pins=pins, request=request)
        assert cut == wants_cut and stock == (not wants_cut) and hygiene, (
            name, cut, stock, hygiene)

    # Both recurring sites and all six native single-cylinder masks execute the
    # real descriptor CALLS and return with the stock/cut P1L result.
    cut_image = _case_image_older(layout, {"CUTSW": 0x00, "CUTRPM": 0x7D})
    stock_image = _case_image_older(layout, {"CUTSW": 0xFF, "CUTRPM": 0x7D})
    for hook in layout["ignition_hooks"]:
        for image, wants_cut in ((cut_image, True), (stock_image, False)):
            cut, stock, hygiene = _run_older_ignition(
                layout, image, rpm=0xC8, hook=hook)
            assert cut == wants_cut and stock == (not wants_cut) and hygiene
    for index, mask in enumerate(IGNITION_SINGLE_MASKS):
        for image, wants_cut in ((cut_image, True), (stock_image, False)):
            emu = _emu(image, dpp0=4)
            emu.write_byte(layout["rpm_address"], 0xC8)
            emu.write_byte(0xFA5F, index)
            emu.write(layout["paired_selector"], 0)
            emu.write(0xFD5A, 0)
            emu.write_byte(0xFF04, 0xFF)
            emu.cpu.csp = 3
            res = _run(emu,
                layout["ignition_entry"],
                stop_at=(layout["ignition_hooks"][0] + 4,),
                max_steps=300,
            )
            assert res.exit_reason == "stop_at"
            assert emu.read_byte(0xFF04) == (0xFF if wants_cut else mask), (
                index, hex(mask), wants_cut, res)


def _run_older_launch(
        layout, image, *, speed, tps, fd60=0, fd61=0, soft=0xCB,
        fd30_4=False, latch=False):
    emu = _emu(image)
    emu.write_byte(0xF19A, speed)
    emu.write_byte(0xE8D0, tps)
    emu.write_byte(layout["rpm_address"], 0xC8)
    emu.write_byte(0xFD60, fd60)
    emu.write_byte(0xFD61, fd61)
    emu.write(0xFD5A, 0x0040 if latch else 0)
    emu.write(0xFD30, 0x0010 if fd30_4 else 0)
    emu.write_byte(layout["soft_limit_address"], soft)
    emu.cpu.csp = 2
    sp0 = emu.state()["regs"]["sp"]
    res = _run(emu,
        layout["launch_hook"],
        stop_at=layout["launch_continuations"],
        max_steps=600,
    )
    return emu, res, sp0


def _verify_older_launch(layout):
    state_cases = [
        ("off", 0xFF, 0, 0, 0xC0, 0, 0, 1, False),
        ("always", 0x00, 0, 0, 0xC0, 0, 0, 0, True),
        ("pin80 arm", 0x01, 0, 0, 0xC0, *SIR_PIN_BYTES[0x01], 0, True),
        ("pin80 hold zero", 0x01, 0, 0, 0xC0, 0, 0, 0, False),
        ("pin80 hold one", 0x01, 0, 0, 0xC0, 0, 0, 1, True),
        ("pin81", 0x02, 0, 0, 0xC0, *SIR_PIN_BYTES[0x02], 0, True),
        ("pin82", 0x04, 0, 0, 0xC0, *SIR_PIN_BYTES[0x04], 0, True),
        ("release speed", 0x01, 0, 0x28, 0xC0, *SIR_PIN_BYTES[0x01], 1, False),
        ("release TPS", 0x01, 0, 0, 0x40, *SIR_PIN_BYTES[0x01], 1, False),
        ("mid-shift no arm", 0x01, 0, 0x14, 0xC0, *SIR_PIN_BYTES[0x01], 0, False),
        ("rollout hold", 0x01, 0, 0x14, 0xC0, 0, 0, 1, True),
        ("active-low", 0x01, 1, 0, 0xC0, 0, 0, 0, True),
    ]
    for (
        name, switch, polarity, speed, tps, fd60, fd61, initial_latch,
        wants_latch,
    ) in state_cases:
        image = _case_image_older(layout, {
            "LC_SW": switch, "LC_CUTTYPE": 1,
            "LC_CLUTCHPOL": polarity, **A_THRESHOLDS,
            "LC_MAXRPM": 0x7D,
        })
        emu, res, sp0 = _run_older_launch(
            layout, image, speed=speed, tps=tps, fd60=fd60, fd61=fd61,
            latch=initial_latch)
        assert (
            bool(emu.read(0xFD5A) & 0x40) == wants_latch
            and res.exit_reason == "stop_at"
            and res.regs["sp"] == sp0 and res.regs["dpp"][0] == 5
        ), (name, res, hex(emu.read(0xFD5A)))

    soft_cases = [
        ("off", 0xFF, 0, 0x7D, 0xCB, 0xCB),
        ("fuel clamp", 0x00, 0, 0x7D, 0xCB, 0x7D),
        ("fuel keep", 0x00, 0, 0xD0, 0xCB, 0xCB),
        ("ignition mode", 0x00, 1, 0x7D, 0xCB, 0xCB),
    ]
    for name, switch, cut_type, limit, stock_limit, wanted in soft_cases:
        image = _case_image_older(layout, {
            "LC_SW": switch, "LC_CUTTYPE": cut_type,
            "LC_CLUTCHPOL": 0, "LC_MAXRPM": limit, **A_THRESHOLDS,
        })
        emu, res, sp0 = _run_older_launch(
            layout, image, speed=0, tps=0xC0, soft=stock_limit)
        assert (
            emu.read_byte(layout["soft_limit_address"]) == wanted
            and res.exit_reason == "stop_at"
            and res.regs["sp"] == sp0 and res.regs["dpp"][0] == 5
        ), (name, res, emu.read_byte(layout["soft_limit_address"]))

    hard_cases = [
        ("below", 1, 0, 0x7D, 0x90, 0x8F, 0),
        ("at", 1, 0, 0x7D, 0x90, 0x90, 1),
        ("below-soft clamp", 1, 0, 0x7D, 0x70, 0x7C, 0),
        ("at-soft clamp", 1, 0, 0x7D, 0x70, 0x7D, 1),
        ("FF fallback below", 1, 0, 0x7D, 0xFF, 0x7F, 0),
        ("FF fallback at", 1, 0, 0x7D, 0xFF, 0x80, 1),
        ("stock below", 0, 0, 0x7D, 0x90, 0xCD, 0),
        ("stock at", 0, 0, 0x7D, 0x90, 0xCE, 1),
        ("ignition uses stock", 1, 1, 0x7D, 0x90, 0xCD, 0),
    ]
    for name, latch, cut_type, soft, hard, rpm, result_index in hard_cases:
        image = _case_image_older(layout, {
            "LC_CUTTYPE": cut_type, "LC_MAXRPM": soft, "LC_HARDRPM": hard,
        })
        for entry, outcomes in layout["hard_sites"]:
            emu = _emu(image)
            emu.write(0xFD5A, 0x0040 if latch else 0)
            emu.write_byte(layout["rpm_address"], rpm)
            emu.cpu.csp = 2
            sp0 = emu.state()["regs"]["sp"]
            res = _run(emu, entry, stop_at=outcomes, max_steps=300)
            assert (
                res.final_ip == outcomes[result_index]
                and res.exit_reason == "stop_at"
                and res.regs["sp"] == sp0 and res.regs["dpp"][0] == 5
            ), (name, entry, outcomes, res)

    # Ignition-mode launch request composes with this firmware's V7 hook.
    image = _case_image_older(layout, {
        "CUTSW": 0xFF, "CUTRPM": 0xD7,
        "LC_SW": 0x00, "LC_CUTTYPE": 1, "LC_CLUTCHPOL": 0,
        "LC_MAXRPM": 0x7D, **A_THRESHOLDS,
    })
    emu, res, _sp0 = _run_older_launch(
        layout, image, speed=0, tps=0xC0)
    request = bool(emu.read(0xFD5A) & 0x80)
    cut, stock, hygiene = _run_older_ignition(
        layout, image, rpm=0xC8, request=request)
    assert request and cut and not stock and hygiene, (res, request, cut)


def _verify_ms411_vanos(layout):
    cases = [
        ("fd14.4 below", 0x10, 0x7D, 0x64, 0),
        ("fd14.4 equal", 0x10, 0x7D, 0x7D, 1),
        ("fd14.5 below", 0x20, 0x7D, 0x64, 0),
        ("fd14.5 above", 0x20, 0x7D, 0xC8, 1),
        ("neither preserves engage", 0x00, 0xFF, 0x00, 1),
    ]
    for name, fd14, threshold, rpm, outcome_index in cases:
        image = _case_image_older(layout, {"VANOSRPM": threshold})
        emu = _emu(image, dpp0=4)
        emu.write(0xFD14, fd14)
        emu.write_byte(0xE9E2, rpm)
        emu.cpu.csp = 3
        res = _run(emu,
            layout["vanos_hook"],
            stop_at=layout["vanos_outcomes"],
            max_steps=100,
        )
        assert (
            res.final_ip == layout["vanos_outcomes"][outcome_index]
            and res.exit_reason == "stop_at"
            and res.regs["dpp"][0] == 4
        ), (name, res)


_FLASH_TARGET = 0x2200
_FLASH_TARGET_PHYS = 0x12200
_FLASH_TARGET_FILE = _FLASH_TARGET_PHYS ^ 0x4000


def _copy_driver(emu, start, end):
    body = bytes(emu.mem.image[start:end])
    for index, value in enumerate(body):
        emu.write_byte(0xE320 + index, value)
    return 0xE320 + len(body)


def _intel_driver(kind, *, from_ram):
    image = bytearray(STOCK_413_PATH.read_bytes())
    image[_FLASH_TARGET_FILE:_FLASH_TARGET_FILE + 2] = (
        0xFFFF if kind == "program" else 0).to_bytes(2, "little")
    emu = _load_emulator(
        bytes(image), seg0_from_flash=True, flash_writable=True,
        force_variant="SS1v2")
    emu.mem.flash_model = FlashModel(
        target=_FLASH_TARGET, busy_reads=2)
    Timer1(tick=1).attach(emu.peripherals)
    emu.write(0xE656, _FLASH_TARGET)
    emu.write(0xE744, 0xFFFF)
    emu.reg.set_word(0, 0xE600)
    if kind == "program":
        emu.write(0xE73C, 0xE800)
        emu.write(0xE73A, 2)
        emu.write(0xE800, 0xBEEF)
        start, end, flash_entry = 0x4230, 0x432C, 0x0230
    else:
        start, end, flash_entry = 0x432E, 0x4410, 0x032E
    if from_ram:
        stop = _copy_driver(emu, start, end)
        entry = 0xE320
    else:
        entry, stop = flash_entry, end ^ 0x4000
    result = _run(emu, entry, stop_at=stop, max_steps=400000)
    assert result.exit_reason == "stop_at" and emu.read_byte(0xE742) == 1
    emu.write(_FLASH_TARGET, 0x00FF)
    expected = 0xBEEF if kind == "program" else 0xFFFF
    assert emu.read(_FLASH_TARGET) == expected


def verify_intel_flash_mutation():
    """Run the stock Intel program/erase driver from flash and its RAM copy."""
    for kind in ("program", "erase"):
        for from_ram in (False, True):
            _intel_driver(kind, from_ram=from_ram)


def _amd_driver(device, kind):
    image = bytearray(FULL_IMAGE)
    image[_FLASH_TARGET_FILE:_FLASH_TARGET_FILE + 2] = (
        0xFFFF if kind == "program" else 0).to_bytes(2, "little")
    emu = _load_emulator(
        bytes(image), seg0_from_flash=True, flash_writable=True,
        force_variant="1406464")
    emu.mem.flash_model = AmdFlashModel(device=device, busy_reads=4)
    Timer1(tick=1).attach(emu.peripherals)
    emu.write(0xE656, _FLASH_TARGET)
    emu.write(0xE744, 0xFFFF)
    emu.reg.set_word(0, 0xE600)
    if kind == "program":
        emu.write(0xE73C, 0xE800)
        emu.write(0xE73A, 2)
        emu.write(0xE800, 0xBEEF)
        start, end = 0x4230, 0x4308
    else:
        start, end = 0x432E, 0x43C4
    stop = _copy_driver(emu, start, end) - 2  # AMD descriptor slice includes RETS.
    result = _run(emu, 0xE320, stop_at=stop, max_steps=400000)
    assert result.exit_reason == "stop_at" and emu.read_byte(0xE742) == 1
    expected = 0xBEEF if kind == "program" else 0xFFFF
    assert emu.read(_FLASH_TARGET) == expected


def verify_amd_flash_mutation():
    """Bind and execute the exact current AMD descriptor payload."""
    patch = PATCHES["amd_flash"]
    asm = TEST_DATA_ROOT.parent / "Decompilation" / "asm"
    program = bytes.fromhex(
        (asm / "program_amd_v5.hex").read_text(encoding="ascii").strip())
    erase = bytes.fromhex(
        (asm / "erase_amd.hex").read_text(encoding="ascii").strip())
    program_edit = bytes.fromhex(patch["edits"][0]["data"])
    erase_edit = bytes.fromhex(patch["edits"][1]["data"])
    assert program_edit == program[12:]
    assert erase_edit == erase[6:]
    assert hashlib.sha256(program).hexdigest() == (
        "456fde3b6f6cfaeaa77add3e9090bb56bf6d9a2fb37c61cc7fba60e193630b6c")
    assert hashlib.sha256(program_edit).hexdigest() == (
        "b2c462b5531eb57707109f65770c7208030edacb3fe71471f198cd4b41b39c31")
    assert hashlib.sha256(erase).hexdigest() == (
        "21b0cb2e7fc17bdf1fef238a312783f320cf8903ccdcbda2c618763cbbf5a460")
    assert hashlib.sha256(erase_edit).hexdigest() == (
        "e1c68088867bf4b0dfe50a57a89f21a507b9a78ab608f358bb387c3001cca93f")
    for device in ("am29f200bb", "am29f400bb"):
        for kind in ("program", "erase"):
            _amd_driver(device, kind)


ADMISSION_REGISTRY = {
    "alphan_failsafe": "features-ms413",
    "amd_flash": "amd-flash",
    "cal_guard": "cal-guard",
    "door_0x43": "loader-doors",
    "door_0x43_ms410": "loader-doors",
    "door_0x43_ms411": "loader-doors",
    "door_magic": "loader-doors",
    "door_magic_ms410": "loader-doors",
    "door_magic_ms411": "loader-doors",
    "ignition_cut_v7_ms410": "features-ms410",
    "ignition_cut_v7_ms411": "features-ms411",
    "ignition_cut_v7": "features-ms412",
    "launch_control_v5": "features-ms413",
    "launch_control_v4_ms410": "features-ms410",
    "launch_control_v4_ms411": "features-ms411",
    "launch_control_v4_ms412": "features-ms412",
    "softbsl_loader": "loader-doors",
    "vanos_minrpm_ms411": "features-ms411",
    "vanos_minrpm_v2_ms410": "features-ms410",
}


def _verify_registry():
    installable = {
        patch_id for patch_id, patch in PATCHES.items()
        if not patch.get("deprecated")
        and (patch.get("cave")
             or patch_id in {"amd_flash", "softbsl_loader"})
    }
    assert installable == set(ADMISSION_REGISTRY), (
        "patch admission registry drift",
        sorted(installable - set(ADMISSION_REGISTRY)),
        sorted(set(ADMISSION_REGISTRY) - installable),
    )
    assert None not in ADMISSION_REGISTRY.values()
    assert set(ADMISSION_REGISTRY.values()) <= set(GROUPS)


def _admission_fingerprint():
    digest = hashlib.sha256()

    def add(label, path):
        data = path.read_bytes()
        digest.update(label.encode("utf-8"))
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)

    for path in (
        Path(__file__),
        ROOT / "checksum.py",
        ROOT / "engines" / "patcher" / "patch_ms41.py",
        ROOT / "engines" / "patcher" / "cal_guard_exact.py",
    ):
        add(path.relative_to(ROOT).as_posix(), path)
    for path in sorted(
            (ROOT / "engines" / "patcher" / "patches").glob("*.json")):
        add(path.relative_to(ROOT).as_posix(), path)
    for path in sorted((EMU_ROOT / "ms41emu").rglob("*.py")):
        add("ms41emu/" + path.relative_to(EMU_ROOT / "ms41emu").as_posix(), path)
    for path in (
        EMU_ROOT / "tools" / "opcode_coverage.py",
        EMU_ROOT / "tests" / "diff.py",
        EMU_ROOT / "tests" / "oracle_fixture_hashes.json",
        EMU_ROOT / "oracle" / "run_oracle.py",
        EMU_ROOT / "oracle" / "EmuMs41.java",
        *manual_evidence_paths(),
    ):
        add(path.relative_to(EMU_ROOT).as_posix(), path)
    for path in sorted((EMU_ROOT / "tests" / "golden").glob("*.json")):
        add("tests/golden/" + path.name, path)
    for variant, path in (
        ("ms41.0", STOCK_410_PATH),
        ("ms41.1", STOCK_411_PATH),
        ("ms41.2", STOCK_PATH),
        ("ms41.3", STOCK_413_PATH),
    ):
        add(f"reference/{variant}.bin", path)
    asm = TEST_DATA_ROOT.parent / "Decompilation" / "asm"
    add("amd/program_amd_v5.hex", asm / "program_amd_v5.hex")
    add("amd/erase_amd.hex", asm / "erase_amd.hex")
    softbsl = ROOT / "engines" / "softbsl"
    for path in sorted((*softbsl.glob("*.hex"), *softbsl.glob("*manifest.json"))):
        add(path.relative_to(ROOT).as_posix(), path)
    return digest.hexdigest()


def _group_cal_guard():
    verify_calguard_compatibility()
    print("[PASS] cal_guard exact installed splice/cave, IDs and recovery")


def _group_loader():
    verify_loader_and_doors()
    verify_joined_softbsl_agent_cycle()
    print(
        "[PASS] 4-firmware x 3-chip doors/CalGuard recovery/loader/"
        "matched agents/EEPROM finalize/normal reboot")


def _group_features_412():
    checks = [
        ("ignition cut V7", verify_ignition_cut_v7),
        ("launch V4 state", verify_launch_brain),
        ("launch V4 soft limiter", verify_launch_fuel_soft_cave),
        ("launch V4 hard comparator", verify_launch_fuel_hard_comparator),
        ("launch V4 + ignition V7 composition", verify_composed_launch_and_ignition),
    ]
    for label, check in checks:
        check()
        print(f"[PASS] MS41.2 {label}")
    _verify_watchdog_liveness(
        STOCK_PATH, "ignition_cut_v7", "launch_control_v4_ms412")
    print("[PASS] MS41.2 stock/V7/V7+V4/full-stack boot-reset liveness")


def _group_features_413():
    checks = [
        ("AlphaN V3 fallback", verify_alphan_failsafe),
        ("ignition cut V7", lambda: verify_ignition_cut_v7(_case_image_413)),
        ("launch V5 state", lambda: verify_launch_brain(_case_image_413)),
        ("launch V5 soft limiter", verify_launch_fuel_soft_cave_ms413),
        ("launch V5 hard comparator",
         lambda: verify_launch_fuel_hard_comparator(_case_image_413)),
        ("launch V5 + ignition V7 composition",
         lambda: verify_composed_launch_and_ignition(_case_image_413)),
    ]
    for label, check in checks:
        check()
        print(f"[PASS] MS41.3 {label}")
    _verify_watchdog_liveness(
        STOCK_413_PATH, "ignition_cut_v7", "launch_control_v5")
    print("[PASS] MS41.3 stock/V7/V7+V5/full-stack boot-reset liveness")

def _verify_asc0_rx_interrupt(emu, variant):
    """Deliver one byte through ASC0 IR, vector, stock ISR wrapper, and RETI."""
    for address in (0xE000, 0xE002):
        emu.write(address, 0x00CC)
    original_s0ric = emu.read_byte(0xFF6E)
    original_psw = emu.cpu.psw.pack()
    emu.cpu.csp = 0
    emu.cpu.psw.IEN = True
    emu.cpu.psw.ILVL = 0
    emu.write_byte(0xFF6E, 0x4C)
    saved = (emu.reg.sp, emu.cpu.psw.pack())
    first_entry = len(emu.interrupts.entries)
    trace = []
    emu.cpu.set_trace(lambda pc, _opcode: trace.append(pc))
    emu.asc0.rx_inject(b"\x12")
    result = emu.run_from(0xE000, stop_at=0xE002, max_steps=2000)
    emu.cpu.set_trace(None)
    entries = emu.interrupts.entries[first_entry:]
    assert (
        result.exit_reason == "stop_at"
        and trace[:4] == [
            0xE000, 0x00AC, 0x21AC, _ASC0_RX_HANDLERS[variant]]
        and len(entries) == 1
        and entries[0][1:] == ("ASC0_RX", 3, 0, 0x00AC)
        and not emu.asc0.rx
        and (emu.reg.sp, emu.cpu.psw.pack()) == saved
    ), (variant, "ASC0 RX interrupt/RETI", result, trace[:8], entries)
    emu.write_byte(0xFF6E, original_s0ric)
    emu.cpu.psw.unpack(original_psw)


def _verify_full_stack_liveness(image, variant):
    """Cold/warm boot and exercise non-cut services in the full composition."""
    emu = _load_emulator(
        image, force_variant=variant, silicon_reset=True)
    boot_stop = _DTC100_LAYOUTS[variant][0]
    assert _code_bytes(emu, boot_stop, 8) == bytes.fromhex(
        "e6b85400e6b71b00")
    for boot_count in range(2):
        visited = []
        emu.cpu.set_trace(lambda pc, _opcode: visited.append(pc))
        result = _run(
            emu,
            emu.cpu.pc,
            stop_at=(boot_stop, RECOVER_EXIT),
            max_steps=400000,
        )
        assert (
            result.final_pc == boot_stop
            and result.exit_reason == "stop_at"
            and CAVE_CPU in visited
            and emu.reset_count == boot_count
        ), (variant, "full-stack boot", boot_count, result)
        assert _service_foreground_watchdog(emu, variant), (
            variant, "full-stack foreground watchdog", boot_count)
        _verify_cc6_interrupt_entry(emu, variant)
        if boot_count:
            continue
        _verify_asc0_rx_interrupt(emu, variant)
        emu.watchdog.value = 0xFFFF
        emu.watchdog.prescaler_select = 0
        emu.advance_oscillator(4)
        assert (
            emu.reset_count == 1
            and emu.last_reset_reason == "watchdog"
            and emu.cpu.pc == 0
            and emu.read(0xFFAE) == 0x0002
        ), (variant, "full-stack warm reset")


def _verify_watchdog_liveness(stock_path, ignition_id, launch_id):
    """Prove stock and approved patch compositions retain watchdog liveness."""
    variant = _REFERENCE_VARIANTS[stock_path.resolve()]
    images = {
        "stock": _bind_image(stock_path.read_bytes(), variant),
        "ignition V7": _build_from(stock_path, [ignition_id]),
        "ignition V7 + launch": _build_from(
            stock_path, [ignition_id, launch_id]),
    }
    full_ids = _FULL_STACK_PATCH_IDS[variant]
    full_image = _build_from(stock_path, list(full_ids))
    assert all(
        patch_ms41.is_applied(full_image, PATCHES[patch_id])
        for patch_id in full_ids
    ), (variant, "full-stack patch composition")
    verify_watchdog_dtc100_ds2(full_image, variant)
    _verify_full_stack_liveness(full_image, variant)
    for name, image in images.items():
        emu = _emu(image, dpp0=4)
        assert _service_foreground_watchdog(emu, variant), name
        irq_emu = _emu(image, dpp0=4)
        _verify_cc6_interrupt_entry(irq_emu, variant)
        irq_emu.watchdog.value = 0xFFFF
        irq_emu.watchdog.prescaler_select = 0
        irq_emu.advance_oscillator(4)
        assert (
            irq_emu.reset_count == 1
            and irq_emu.last_reset_reason == "watchdog"
            and irq_emu.cpu.pc == 0
            and irq_emu.read(0xFFAE) == 0x0002
        ), name

def _group_older(version):
    layout = OLDER_FEATURE_LAYOUTS[version]
    _verify_older_ignition(layout)
    _verify_older_launch(layout)
    _verify_watchdog_liveness(
        layout["stock_path"], layout["ignition_id"], layout["launch_id"])
    if version == "MS41.1":
        _verify_ms411_vanos(layout)
    suffix = (
        "/watchdog/full-stack-boot-reset"
        if version == "MS41.0"
        else "/VANOS/watchdog/full-stack-boot-reset"
    )
    print(f"[PASS] {version} ignition V7/launch V4{suffix}")

GROUPS = {
    "cal-guard": _group_cal_guard,
    "loader-doors": _group_loader,
    "intel-flash": verify_intel_flash_mutation,
    "amd-flash": verify_amd_flash_mutation,
    "features-ms410": lambda: _group_older("MS41.0"),
    "features-ms411": lambda: _group_older("MS41.1"),
    "features-ms412": _group_features_412,
    "features-ms413": _group_features_413,
}
assert tuple(GROUPS) == _GROUP_NAMES


def main(argv=()):
    if not EMU_ROOT.is_dir():
        raise SystemExit(f"ms41emu package not found: {EMU_ROOT}")
    if not STOCK_PATH.is_file():
        raise SystemExit(f"MS41.2 reference image not found: {STOCK_PATH}")
    if not STOCK_410_PATH.is_file():
        raise SystemExit(f"MS41.0 reference image not found: {STOCK_410_PATH}")
    if not STOCK_411_PATH.is_file():
        raise SystemExit(f"MS41.1 reference image not found: {STOCK_411_PATH}")
    if not STOCK_413_PATH.is_file():
        raise SystemExit(f"MS41.3 reference image not found: {STOCK_413_PATH}")

    args = _parse_args(argv)
    _verify_registry()
    _ADMISSION_EMULATORS.clear()
    if args.list:
        print("\n".join(GROUPS))
        return 0
    selected = args.group or list(GROUPS)
    for name in selected:
        GROUPS[name]()
        if name in {"intel-flash", "amd-flash"}:
            print(f"[PASS] {name} actual array mutation/readback")
    executed_opcodes = set().union(
        *(emu.cpu.executed_opcodes for emu in _ADMISSION_EMULATORS))
    assert executed_opcodes, "private admission executed no firmware instructions"
    require_trusted(executed_opcodes)
    print(f"[PASS] trusted opcode evidence ({len(executed_opcodes)} executed)")
    print(f"admission fingerprint: {_admission_fingerprint()}")
    if args.group:
        print(f"PRIVATE MS41 EMULATOR FOCUSED CHECK PASS ({len(selected)} groups)")
    else:
        print(f"PRIVATE MS41 EMULATOR ADMISSION PASS ({len(selected)} groups)")
    return 0


if __name__ == "__main__":
    main(sys.argv[1:])
