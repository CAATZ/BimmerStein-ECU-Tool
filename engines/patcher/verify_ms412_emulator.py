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
_CUT_STATE_ADDR = 0xE847
_GROUP_NAMES = (
    "cal-guard", "loader-doors", "intel-flash", "amd-flash",
    "st9030-proxy",
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
    "1429861": 0xFD5A,
    "1437806": 0xFD5A,
    "1406464": 0xFD5A,
    "SS1v2": 0xFD5A,
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
        "ignition_cut_v9_ms410", "launch_control_v7_ms410",
    ),
    "1437806": (
        "amd_flash", "softbsl_loader", "cal_guard", "door_magic_ms411",
        "ignition_cut_v9_ms411", "launch_control_v7_ms411",
    ),
    "1406464": (
        "amd_flash", "softbsl_loader", "cal_guard", "door_magic",
        "ignition_cut_v9_ms412", "launch_control_v7_ms412",
    ),
    "SS1v2": (
        "amd_flash", "softbsl_loader", "cal_guard", "door_magic",
        "ignition_cut_v9", "launch_control_v7",
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
    "ignition_cut_v9_ms412", "launch_control_v7_ms412",
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
        ["alphan_failsafe", "ignition_cut_v9", "launch_control_v7"],
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
            "ignition_cut_v9_ms410",
            "launch_control_v7_ms410",
            "vanos_minrpm_v2_ms410",
        ),
        "ignition_id": "ignition_cut_v9_ms410",
        "launch_id": "launch_control_v7_ms410",
        "vanos_id": "vanos_minrpm_v2_ms410",
        "ignition_hooks": (0x26F8, 0x275C),
        "ignition_entry": 0x26E8,
        "ignition_cave_cpu": 0x32820,
        "ignition_replay_cpu": 0x3283A,
        "control_hook": 0x5840,
        "ipw_addresses": (0xECBC, 0xECBE),
        "rpm_address": 0xFAE6,
        "speed_address": 0xEDF4,
        "paired_selector": 0xFD4E,
        "input_bytes": (0xFD50, 0xFD51),
        "input_latch": (0x2364, 0x2370),
        "launch_latch": 0xFD5A,
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
            "ignition_cut_v9_ms411",
            "launch_control_v7_ms411",
            "vanos_minrpm_ms411",
        ),
        "ignition_id": "ignition_cut_v9_ms411",
        "launch_id": "launch_control_v7_ms411",
        "vanos_id": "vanos_minrpm_ms411",
        "ignition_hooks": (0xF466, 0xF4CA),
        "ignition_entry": 0xF456,
        "ignition_cave_cpu": 0x3F680,
        "ignition_replay_cpu": 0x3F69A,
        "control_hook": 0x7A5A,
        "ipw_addresses": (0xEF96, 0xEF98),
        "rpm_address": 0xFC3C,
        "speed_address": 0xF1BE,
        "paired_selector": 0xFD5E,
        "input_bytes": (0xFD60, 0xFD61),
        "launch_latch": 0xFD5A,
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
    for patch_id in ("ignition_cut_v9_ms412", "launch_control_v7_ms412"):
        cal_offsets.update(PATCHES[patch_id]["cave"]["cals"])
    for name, value in values.items():
        offset = cal_offsets[name]
        if name in {"CUT_IPW", "LC_IPW"}:
            image[offset:offset + 2] = value.to_bytes(2, "little")
        else:
            image[offset] = value & 0xFF
    image, _details = checksum.correct_checksums(image, correct_program=True)
    status = checksum.checksum_status(image)
    assert status["boot"] and status["program"] and status["cal"], status
    return _bind_image(image, "1406464")


def _case_image_413(values):
    """Set the MS41.3 patch controls and restore its active checksums."""
    image = bytearray(FULL_413_IMAGE)
    cal_offsets = {}
    for patch_id in ("ignition_cut_v9", "launch_control_v7"):
        cal_offsets.update(PATCHES[patch_id]["cave"]["cals"])
    for name, value in values.items():
        offset = cal_offsets[name]
        if name in {"CUT_IPW", "LC_IPW"}:
            image[offset:offset + 2] = value.to_bytes(2, "little")
        else:
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
        if name in {"CUT_IPW", "LC_IPW"}:
            image[offset:offset + 2] = value.to_bytes(2, "little")
        else:
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
            guard_emu, result, final, sp0 = _guard_route(
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
            res = _run(emu, _SOFTBSL_LOADER, stop_at=(_SOFTBSL_TX,), max_steps=100)
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
            res = _run(emu, _SOFTBSL_LOADER, stop_at=(0xD800,), max_steps=300000)
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
        "st9030_probe": (
            "st9030_agent.hex", 1944,
            "cd43358bde39c4e2a5dd00884b7775df1662802d08886df9a209027c32706ee2",
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


_ST9030_TARGET_SHA256 = (
    "73defe9dd870a36e3891e5be0e5305e39c01cb90604caa654ec2e912054264b4"
)
_ST9030_SLOTS = (
    (0x0102, 2),
    (0x0103, 2),
    (0x0105, 4),
    (0x0108, 5),
    (0x0109, 5),
    (0x010A, 12),
    (0x010E, 1),
)
_ST9030_GATE_REQUEST = b"gST90"
_ST9030_GATE_CHALLENGE = tuple(b"65772052030") + (0xA0,)
_ST9030_GATE_RESPONSE = b"72052030657"
_ST9030_TELEMETRY_REQUEST = b"tST0B"
_ST9030_TELEMETRY_TX = (0x010B, 0x0002, 0, 0, 0, 0x010E)
_ST9030_TELEMETRY_SLOTS = 15


def _st9030_target_image():
    """Compose and bind the exact owner-supplied post-install MS41.3 image."""
    ids = (
        "amd_flash", "softbsl_loader", "door_magic", "cal_guard",
        "alphan_failsafe",
    )
    image, _log = patch_ms41.build(
        STOCK_413_PATH.read_bytes(), ids, marker="B")
    image = bytes(image)
    assert hashlib.sha256(image).hexdigest() == _ST9030_TARGET_SHA256
    assert all(patch_ms41.is_applied(image, PATCHES[item]) for item in ids)
    status = checksum.checksum_status(image)
    assert (
        status["boot"] and status["program"] and status["cal"]
        and status["prog_disabled"]
    ), status
    return _bind_image(image, "SS1v2")


def _st9030_crc_frame(body):
    return body + _crc16(body).to_bytes(2, "big")


def _st9030_reply(emu, request, length, *, max_steps=4_000_000):
    start = len(emu.asc0.tx)
    emu.asc0.rx_inject(request)
    _run_until_state(
        emu,
        emu.cpu.pc,
        lambda: len(emu.asc0.tx) >= start + length,
        max_steps=max_steps,
    )
    reply = bytes(emu.asc0.tx[start:])
    assert len(reply) == length, (request[:2], len(reply), length)
    return reply


def _assert_st9030_frame(reply, body):
    assert reply[:-2] == body
    assert int.from_bytes(reply[-2:], "big") == _crc16(body)


def _st9030_gate_body(status, challenge=(), response=b"", acknowledgment=0):
    challenge = tuple(challenge)
    assert len(challenge) <= 12 and len(response) <= 11
    body = bytes((status,)) + b"".join(
        word.to_bytes(2, "big")
        for word in challenge + (0,) * (12 - len(challenge))
    )
    body += response.ljust(11, b"\x00")
    body += acknowledgment.to_bytes(2, "big")
    assert len(body) == 38
    return body


def _st9030_gate_start(emu):
    asc0_start = len(emu.asc0.tx)
    asc1_start = len(emu.asc1.tx_words)
    emu.asc0.rx_inject(_st9030_crc_frame(_ST9030_GATE_REQUEST))
    _run_until_state(
        emu,
        emu.cpu.pc,
        lambda: len(emu.asc1.tx_words) == asc1_start + 1,
        max_steps=100_000,
    )
    assert emu.asc1.tx_words[asc1_start:] == [0x010A]
    return asc0_start, asc1_start


def _st9030_gate_reply(emu, asc0_start, *, max_steps=4_000_000):
    _run_until_state(
        emu,
        emu.cpu.pc,
        lambda: len(emu.asc0.tx) >= asc0_start + 40,
        max_steps=max_steps,
    )
    reply = bytes(emu.asc0.tx[asc0_start:])
    assert len(reply) == 40
    return reply


def _st9030_telemetry_body(
        status, count=0, terminal_delta=0, words=(), timestamps=()):
    words = tuple(words)
    timestamps = tuple(timestamps)
    assert 0 <= count <= _ST9030_TELEMETRY_SLOTS
    assert len(words) <= count and len(timestamps) <= count
    body = bytes((status, count))
    body += terminal_delta.to_bytes(2, "big")
    body += b"".join(word.to_bytes(2, "big") for word in (
        words + (0,) * (_ST9030_TELEMETRY_SLOTS - len(words))))
    body += b"".join(timestamp.to_bytes(2, "big") for timestamp in (
        timestamps + (0,) * (
            _ST9030_TELEMETRY_SLOTS - len(timestamps))))
    assert len(body) == 64
    return body


def _st9030_telemetry_start(emu):
    asc0_start = len(emu.asc0.tx)
    asc1_start = len(emu.asc1.tx_words)
    emu.asc0.rx_inject(_st9030_crc_frame(_ST9030_TELEMETRY_REQUEST))
    _run_until_state(
        emu,
        emu.cpu.pc,
        lambda: len(emu.asc1.tx_words) == asc1_start + 6,
        max_steps=100_000,
    )
    assert tuple(emu.asc1.tx_words[asc1_start:]) == _ST9030_TELEMETRY_TX
    return asc0_start, asc1_start


def _st9030_telemetry_reply(emu, asc0_start, *, max_steps=4_000_000):
    _run_until_state(
        emu,
        emu.cpu.pc,
        lambda: len(emu.asc0.tx) >= asc0_start + 66,
        max_steps=max_steps,
    )
    reply = bytes(emu.asc0.tx[asc0_start:])
    assert len(reply) == 66
    return reply


def verify_st9030_proxy_agent():
    """Execute the frozen bounded proxy through the installed RAM loader."""
    image = _st9030_target_image()
    payload = _softbsl_agent_payload("st9030_probe")
    assert len(payload) <= 0x800

    emu = _emu(image)
    flash = AmdFlashModel(device="am29f200bb", busy_reads=4)
    emu.mem.flash_model = flash
    timer1 = Timer1(tick=1)
    timer1.attach(emu.peripherals)
    eeprom = Eeprom24C04(bytes([0x5A]) * Eeprom24C04.SIZE)
    eeprom.attach(emu.peripherals)
    original_flash = bytes(emu.mem.image)

    # Exercise the exact installed CRC-checked loader, not a direct RAM copy.
    upload_crc = _crc16(payload)
    for address, value in (
        (_SOFTBSL_MARKER, 1),
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
    assert routed.final_ip == _SOFTBSL_LOADER
    loaded = _run(
        emu, _SOFTBSL_LOADER,
        stop_at=(_SOFTBSL_AGENT,), max_steps=300_000)
    assert loaded.final_ip == _SOFTBSL_AGENT
    assert bytes(emu.asc0.tx) == b"\x06\x06"
    assert bytes(
        emu.read_byte(_SOFTBSL_AGENT + offset)
        for offset in range(len(payload))) == payload
    assert not emu.asc0.rx

    emu.asc0.tx.clear()
    _run_until_state(
        emu, _SOFTBSL_AGENT,
        lambda: len(emu.asc0.tx) == 1, max_steps=100_000)
    assert bytes(emu.asc0.tx) == b"\xA5"

    identify = _st9030_reply(emu, b"i", 6)
    _assert_st9030_frame(identify, bytes((5, 15, 7, 1)))
    snapshot = _st9030_reply(emu, b"s", 13)
    _assert_st9030_frame(snapshot, snapshot[:-2])
    assert snapshot[0] == 0

    for slot, (command, count) in enumerate(_ST9030_SLOTS):
        asc1_start = len(emu.asc1.tx_words)
        asc0_start = len(emu.asc0.tx)
        request = _st9030_crc_frame(bytes((ord("r"), slot)))
        emu.asc0.rx_inject(request)
        _run_until_state(
            emu,
            emu.cpu.pc,
            lambda: len(emu.asc1.tx_words) == asc1_start + 1,
            max_steps=100_000,
        )
        assert emu.asc1.tx_words[-1] == command
        words = tuple(
            ((slot + index) & 0xFF) | (0x100 if index & 1 else 0)
            for index in range(count)
        )
        emu.asc1.rx_inject(words)
        reply_length = 5 + 2 * count
        _run_until_state(
            emu,
            emu.cpu.pc,
            lambda: len(emu.asc0.tx) >= asc0_start + reply_length,
            max_steps=500_000,
        )
        reply = bytes(emu.asc0.tx[asc0_start:])
        body = bytes((0, slot, count)) + b"".join(
            word.to_bytes(2, "big") for word in words)
        _assert_st9030_frame(reply, body)

    # The exact payload must establish the documented ASC1 pin direction and
    # framing before every active replay: P3.8 TX high/output, P3.9 RX
    # high/input, S1BG=1, and 9-bit asynchronous/two-stop-bit S1CON=0x801C.
    assert emu.read(0xFFC4) & 0x0300 == 0x0300
    assert emu.read(0xFFC6) & 0x0300 == 0x0100
    assert emu.read(0xFEBC) == 0x0001
    assert emu.read(0xFFB8) == 0x801C

    # Request integrity fails before any ST9030 traffic.
    tx_count = len(emu.asc1.tx_words)
    bad_crc = bytearray(_st9030_crc_frame(_ST9030_GATE_REQUEST))
    bad_crc[-1] ^= 1
    reply = _st9030_reply(emu, bytes(bad_crc), 40)
    _assert_st9030_frame(reply, _st9030_gate_body(1))
    reply = _st9030_reply(
        emu, _st9030_crc_frame(b"gST91"), 40)
    _assert_st9030_frame(reply, _st9030_gate_body(2))
    assert len(emu.asc1.tx_words) == tx_count

    # A non-A0 challenge and any set ninth bit stop before 0x10C is sent.
    challenge_not_ready = _ST9030_GATE_CHALLENGE[:-1] + (0xA1,)
    # Stage only the leading command byte first. The real ASC0 has no FIFO:
    # the agent must capture the rest of the request before clearing its reply
    # transcript, or a high-baud burst can be discarded by the next rx() IR
    # clear. The old gate_clear-before-rx payload fails this exact-byte check.
    gate_wire = _st9030_crc_frame(_ST9030_GATE_REQUEST)
    emu.write_byte(0xE000, 0x5A)
    asc0_start = len(emu.asc0.tx)
    asc1_start = len(emu.asc1.tx_words)
    emu.asc0.rx_inject(gate_wire[:1])
    _run(emu, emu.cpu.pc, max_steps=2_000)
    assert emu.read_byte(0xE000) == 0x5A, (
        "ST9030 gate cleared its transcript before capturing the host frame")
    emu.asc0.rx_inject(gate_wire[1:])
    _run_until_state(
        emu,
        emu.cpu.pc,
        lambda: len(emu.asc1.tx_words) == asc1_start + 1,
        max_steps=100_000,
    )
    assert emu.asc1.tx_words[asc1_start:] == [0x010A]
    emu.asc1.rx_inject(challenge_not_ready)
    reply = _st9030_gate_reply(emu, asc0_start)
    _assert_st9030_frame(
        reply, _st9030_gate_body(7, challenge_not_ready))
    assert emu.asc1.tx_words[asc1_start:] == [0x010A]

    challenge_ninth = (
        (_ST9030_GATE_CHALLENGE[0] | 0x100),
        *_ST9030_GATE_CHALLENGE[1:],
    )
    asc0_start, asc1_start = _st9030_gate_start(emu)
    emu.asc1.rx_inject(challenge_ninth)
    reply = _st9030_gate_reply(emu, asc0_start)
    _assert_st9030_frame(
        reply, _st9030_gate_body(6, challenge_ninth))
    assert emu.asc1.tx_words[asc1_start:] == [0x010A]

    # Exact stock transcript: 10A receive, rotate-left-three, 10C header plus
    # eleven 8-bit words, then one 10E/A0.
    asc0_start, asc1_start = _st9030_gate_start(emu)
    emu.asc1.rx_inject(_ST9030_GATE_CHALLENGE)
    expected_tx = (
        0x010A, 0x010C, *_ST9030_GATE_RESPONSE, 0x010E)
    _run_until_state(
        emu,
        emu.cpu.pc,
        lambda: len(emu.asc1.tx_words) == asc1_start + len(expected_tx),
        max_steps=500_000,
    )
    assert tuple(emu.asc1.tx_words[asc1_start:]) == expected_tx
    emu.asc1.rx_inject((0xA0,))
    reply = _st9030_gate_reply(emu, asc0_start)
    _assert_st9030_frame(reply, _st9030_gate_body(
        0, _ST9030_GATE_CHALLENGE, _ST9030_GATE_RESPONSE, 0xA0))

    # A1 is surfaced as one-shot pending; the bounded agent never invents the
    # stock scheduler's retry timing.
    asc0_start, asc1_start = _st9030_gate_start(emu)
    emu.asc1.rx_inject(_ST9030_GATE_CHALLENGE)
    _run_until_state(
        emu,
        emu.cpu.pc,
        lambda: len(emu.asc1.tx_words) == asc1_start + len(expected_tx),
        max_steps=500_000,
    )
    emu.asc1.rx_inject((0xA1,))
    reply = _st9030_gate_reply(emu, asc0_start)
    _assert_st9030_frame(reply, _st9030_gate_body(
        0x0F, _ST9030_GATE_CHALLENGE, _ST9030_GATE_RESPONSE, 0xA1))
    assert tuple(emu.asc1.tx_words[asc1_start:]) == expected_tx

    # Both receive waits are finite and service the watchdog.
    emu.watchdog.enabled = True
    emu.watchdog.prescaler_select = 0
    emu.watchdog.reload = 0xC9
    emu.watchdog.value = 0xF000
    reset_count = emu.reset_count
    asc1_start = len(emu.asc1.tx_words)
    timeout = _st9030_reply(
        emu, _st9030_crc_frame(_ST9030_GATE_REQUEST), 40,
        max_steps=4_000_000)
    _assert_st9030_frame(timeout, _st9030_gate_body(4))
    assert emu.asc1.tx_words[asc1_start:] == [0x010A]
    assert emu.reset_count == reset_count

    emu.watchdog.value = 0xF000
    asc0_start, asc1_start = _st9030_gate_start(emu)
    emu.asc1.rx_inject(_ST9030_GATE_CHALLENGE)
    _run_until_state(
        emu,
        emu.cpu.pc,
        lambda: len(emu.asc1.tx_words) == asc1_start + len(expected_tx),
        max_steps=500_000,
    )
    timeout = _st9030_gate_reply(emu, asc0_start)
    _assert_st9030_frame(timeout, _st9030_gate_body(
        0x0C, _ST9030_GATE_CHALLENGE, _ST9030_GATE_RESPONSE))
    assert tuple(emu.asc1.tx_words[asc1_start:]) == expected_tx
    assert emu.reset_count == reset_count

    # Raw invalid requests remain bounded and never reach ASC1.
    tx_count = len(emu.asc1.tx_words)
    denied = _st9030_reply(
        emu, _st9030_crc_frame(b"r\x07"), 5)
    _assert_st9030_frame(denied, bytes((1, 7, 0)))
    bad_crc = _st9030_reply(emu, b"r\x00\x00\x00", 5)
    _assert_st9030_frame(bad_crc, bytes((2, 0, 0)))
    assert len(emu.asc1.tx_words) == tx_count

    # A silent ST9 produces a finite timeout, while the watchdog is serviced.
    timeout_start = len(emu.asc1.tx_words)
    timeout = _st9030_reply(
        emu, _st9030_crc_frame(b"r\x00"), 5,
        max_steps=2_000_000)
    _assert_st9030_frame(timeout, bytes((4, 0, 0)))
    assert len(emu.asc1.tx_words) == timeout_start + 1

    # Telemetry integrity failures clear the complete v5 transcript before any
    # ST9030 traffic, including the issued-attempt counter.
    telemetry_wire = _st9030_crc_frame(_ST9030_TELEMETRY_REQUEST)
    bad_crc = bytearray(telemetry_wire)
    bad_crc[-1] ^= 1
    tx_count = len(emu.asc1.tx_words)
    for request, status in (
        (bytes(bad_crc), 1),
        (_st9030_crc_frame(b"tST0C"), 2),
    ):
        for address in range(0xE000, 0xE060):
            emu.write_byte(address, 0xA5)
        emu.write(0xE404, 0xA5A5)
        reply = _st9030_reply(emu, request, 66)
        _assert_st9030_frame(reply, _st9030_telemetry_body(status))
        assert all(emu.read_byte(address) == 0 for address in range(
            0xE000, 0xE060))
        assert emu.read(0xE404) == 0
    assert len(emu.asc1.tx_words) == tx_count

    timer_low = emu.peripherals._readers[Timer1.T1]
    timer_high = emu.peripherals._readers[Timer1.T1 + 1]

    class StockPacedTimer:
        """Coherent FE52 schedule for exact 0x19-spaced observations."""

        def __init__(self):
            self.reads = 0
            self.latched = 0

        def read_low(self):
            self.latched = (self.reads // 3) * 0x19
            self.reads += 1
            return self.latched & 0xFF

        def read_high(self):
            return (self.latched >> 8) & 0xFF

    def install_timer(model):
        emu.peripherals.register_read(Timer1.T1, model.read_low)
        emu.peripherals.register_read(Timer1.T1 + 1, model.read_high)

    def restore_timer():
        emu.peripherals.register_read(Timer1.T1, timer_low)
        emu.peripherals.register_read(Timer1.T1 + 1, timer_high)

    def assert_telemetry_tx(asc1_start, attempts):
        actual = tuple(emu.asc1.tx_words[asc1_start:])
        expected = _ST9030_TELEMETRY_TX + (0x010E,) * (attempts - 1)
        assert actual == expected
        assert actual.count(0x010B) == 1
        assert actual.count(0x010E) == attempts
        assert 0x010D not in actual

    def telemetry_fields(reply):
        body = reply[:-2]
        words = tuple(int.from_bytes(body[offset:offset + 2], "big")
                      for offset in range(4, 34, 2))
        timestamps = tuple(int.from_bytes(
            body[offset:offset + 2], "big") for offset in range(34, 64, 2))
        return body[0], body[1], int.from_bytes(body[2:4], "big"), (
            words), timestamps

    def run_telemetry(responses, status):
        responses = tuple(responses)
        paced = StockPacedTimer()
        install_timer(paced)
        try:
            asc0_start, asc1_start = _st9030_telemetry_start(emu)
            for index, response in enumerate(responses, start=1):
                emu.asc1.rx_inject((response,))
                if index < len(responses):
                    _run_until_state(
                        emu,
                        emu.cpu.pc,
                        lambda index=index: len(emu.asc1.tx_words)
                        == asc1_start + 6 + index,
                        max_steps=100_000,
                    )
            reply = _st9030_telemetry_reply(emu, asc0_start)
        finally:
            restore_timer()
        timestamps = tuple(0x19 * index for index in range(
            1, len(responses) + 1))
        _assert_st9030_frame(reply, _st9030_telemetry_body(
            status, len(responses), timestamps[-1], responses, timestamps))
        assert paced.reads == 3 * len(responses) + 1
        assert_telemetry_tx(asc1_start, len(responses))
        return reply

    # The real ASC0 has no FIFO. Capture the complete request before clearing
    # reply scratch, then execute two paced A1 polls followed by A0.
    paced = StockPacedTimer()
    install_timer(paced)
    try:
        emu.write_byte(0xE000, 0x5A)
        asc0_start = len(emu.asc0.tx)
        asc1_start = len(emu.asc1.tx_words)
        emu.asc0.rx_inject(telemetry_wire[:1])
        _run(emu, emu.cpu.pc, max_steps=2_000)
        assert emu.read_byte(0xE000) == 0x5A, (
            "ST9030 telemetry cleared scratch before capturing the host frame")
        emu.asc0.rx_inject(telemetry_wire[1:])
        for index, response in enumerate((0xA1, 0xA1, 0xA0), start=1):
            _run_until_state(
                emu,
                emu.cpu.pc,
                lambda index=index: len(emu.asc1.tx_words)
                == asc1_start + 5 + index,
                max_steps=100_000,
            )
            emu.asc1.rx_inject((response,))
        reply = _st9030_telemetry_reply(emu, asc0_start)
    finally:
        restore_timer()
    _assert_st9030_frame(reply, _st9030_telemetry_body(
        0, 3, 0x4B, (0xA1, 0xA1, 0xA0), (0x19, 0x32, 0x4B)))
    assert_telemetry_tx(asc1_start, 3)

    # A blocking receive consumes the pacing interval.  The first post-RX
    # FE52 read already reports 0x19, so no second full delay is imposed.
    paced = StockPacedTimer()
    install_timer(paced)
    try:
        asc0_start, asc1_start = _st9030_telemetry_start(emu)
        assert paced.reads == 3
        emu.asc1.rx_inject((0xA0,))
        reply = _st9030_telemetry_reply(emu, asc0_start)
        assert paced.reads == 4
    finally:
        restore_timer()
    _assert_st9030_frame(
        reply, _st9030_telemetry_body(0, 1, 0x19, (0xA0,), (0x19,)))
    assert_telemetry_tx(asc1_start, 1)

    # A0, FF, an unexpected value, and a set ninth bit are classified at every
    # possible inspected attempt. Attempt 15 instead preserves the late raw
    # word and reports the conservative stock expiry at exactly 0x177.
    terminal_cases = (
        (0xA0, 0), (0xFF, 0x0A), (0x55, 0x0B), (0x1A0, 0x09),
    )
    for terminal, normal_status in terminal_cases:
        for attempt in range(1, _ST9030_TELEMETRY_SLOTS + 1):
            status = 0x0D if attempt == 15 else normal_status
            reply = run_telemetry(
                (0xA1,) * (attempt - 1) + (terminal,), status)
            assert telemetry_fields(reply)[0] != 0x0E

    # Fifteen A1 observations reach the exact stock boundary: the last raw
    # word is retained at 0x177, expiry wins, and attempt 16 is never issued.
    reply = run_telemetry((0xA1,) * 15, 0x0D)
    status, count, terminal, words, timestamps = telemetry_fields(reply)
    assert status == 0x0D and count == 15 and terminal == 0x177
    assert words == (0xA1,) * 15
    assert timestamps == tuple(0x19 * index for index in range(1, 16))

    # The ASC1 model accepts only 9-bit words. Override its receive SFRs
    # narrowly to prove that impossible upper bits are preserved and rejected.
    paced = StockPacedTimer()
    install_timer(paced)
    old_low = emu.peripherals._readers[emu.asc1.S1RBUF]
    old_high = emu.peripherals._readers[emu.asc1.S1RBUF + 1]
    old_ready = emu.peripherals._readers[emu.asc1.S1RIC]
    try:
        asc0_start, asc1_start = _st9030_telemetry_start(emu)
        emu.peripherals.register_read(emu.asc1.S1RBUF, lambda: 0)
        emu.peripherals.register_read(emu.asc1.S1RBUF + 1, lambda: 2)
        emu.peripherals.register_read(emu.asc1.S1RIC, lambda: 0x80)
        reply = _st9030_telemetry_reply(emu, asc0_start)
    finally:
        emu.peripherals.register_read(emu.asc1.S1RBUF, old_low)
        emu.peripherals.register_read(emu.asc1.S1RBUF + 1, old_high)
        emu.peripherals.register_read(emu.asc1.S1RIC, old_ready)
        restore_timer()
    _assert_st9030_frame(
        reply, _st9030_telemetry_body(9, 1, 0x19, (0x0200,), (0x19,)))
    assert_telemetry_tx(asc1_start, 1)

    # Frozen FE52 exhausts the independent pacing guard after preserving the
    # received word. The bounded loop continues servicing the watchdog.
    emu.watchdog.enabled = True
    emu.watchdog.prescaler_select = 0
    emu.watchdog.reload = 0xC9
    emu.peripherals.register_read(Timer1.T1, lambda: 0)
    emu.peripherals.register_read(Timer1.T1 + 1, lambda: 0)
    emu.watchdog.value = 0xF000
    reset_count = emu.reset_count
    try:
        asc0_start, asc1_start = _st9030_telemetry_start(emu)
        emu.asc1.rx_inject((0xA1,))
        reply = _st9030_telemetry_reply(emu, asc0_start)
    finally:
        restore_timer()
    _assert_st9030_frame(
        reply, _st9030_telemetry_body(0x0C, 1, 0, (0xA1,), (0,)))
    assert_telemetry_tx(asc1_start, 1)
    assert emu.reset_count == reset_count

    # Phase-specific transmit/receive/error exits are bounded. Issued poll
    # failures increment count while leaving that raw/timestamp slot zero.
    def telemetry_tx_timeout(allowed_words, status):
        paced = StockPacedTimer()
        install_timer(paced)
        asc0_start = len(emu.asc0.tx)
        asc1_start = len(emu.asc1.tx_words)
        ready_reader = emu.peripherals._readers[emu.asc1.S1TIC]
        limit = asc1_start + allowed_words
        emu.peripherals.register_read(
            emu.asc1.S1TIC,
            lambda: ready_reader() & (
                0x7F if len(emu.asc1.tx_words) > limit else 0xFF),
        )
        emu.watchdog.value = 0xF000
        reset_count = emu.reset_count
        try:
            emu.asc0.rx_inject(telemetry_wire)
            reply = _st9030_telemetry_reply(emu, asc0_start)
        finally:
            emu.peripherals.register_read(emu.asc1.S1TIC, ready_reader)
            restore_timer()
        expected_count = int(allowed_words >= 5)
        terminal = 0x19 if expected_count else 0
        _assert_st9030_frame(reply, _st9030_telemetry_body(
            status, expected_count, terminal))
        assert emu.reset_count == reset_count
        return tuple(emu.asc1.tx_words[asc1_start:])

    assert telemetry_tx_timeout(0, 3) == (0x010B,)
    assert telemetry_tx_timeout(1, 4) == (0x010B, 0x0002)
    assert telemetry_tx_timeout(5, 6) == _ST9030_TELEMETRY_TX

    # An ASC1 error during the initial 10B header remains distinct and never
    # increments the issued 10E count.
    paced = StockPacedTimer()
    install_timer(paced)
    old_error = emu.peripherals._readers.get(0xFF76)
    emu.peripherals.register_read(0xFF76, lambda: 0x80)
    asc0_start = len(emu.asc0.tx)
    asc1_start = len(emu.asc1.tx_words)
    try:
        emu.asc0.rx_inject(telemetry_wire)
        reply = _st9030_telemetry_reply(emu, asc0_start)
    finally:
        if old_error is None:
            emu.peripherals._readers.pop(0xFF76, None)
        else:
            emu.peripherals.register_read(0xFF76, old_error)
        restore_timer()
    _assert_st9030_frame(reply, _st9030_telemetry_body(5))
    assert tuple(emu.asc1.tx_words[asc1_start:]) == (0x010B,)

    paced = StockPacedTimer()
    install_timer(paced)
    emu.watchdog.value = 0xF000
    reset_count = emu.reset_count
    try:
        asc0_start, asc1_start = _st9030_telemetry_start(emu)
        reply = _st9030_telemetry_reply(emu, asc0_start)
    finally:
        restore_timer()
    _assert_st9030_frame(
        reply, _st9030_telemetry_body(7, 1, 0x19))
    assert_telemetry_tx(asc1_start, 1)
    assert emu.reset_count == reset_count

    # A third issued poll can time out while the first two completed A1
    # observations remain intact and its own raw/time slot stays zero.
    paced = StockPacedTimer()
    install_timer(paced)
    emu.watchdog.value = 0xF000
    reset_count = emu.reset_count
    try:
        asc0_start, asc1_start = _st9030_telemetry_start(emu)
        for index in (1, 2):
            emu.asc1.rx_inject((0xA1,))
            _run_until_state(
                emu,
                emu.cpu.pc,
                lambda index=index: len(emu.asc1.tx_words)
                == asc1_start + 6 + index,
                max_steps=100_000,
            )
        reply = _st9030_telemetry_reply(emu, asc0_start)
    finally:
        restore_timer()
    _assert_st9030_frame(reply, _st9030_telemetry_body(
        7, 3, 0x4B, (0xA1, 0xA1), (0x19, 0x32)))
    assert_telemetry_tx(asc1_start, 3)
    assert emu.reset_count == reset_count

    # Force the ASC1 error indication after the first poll is issued.
    paced = StockPacedTimer()
    install_timer(paced)
    old_error = emu.peripherals._readers.get(0xFF76)
    try:
        asc0_start, asc1_start = _st9030_telemetry_start(emu)
        emu.peripherals.register_read(0xFF76, lambda: 0x80)
        reply = _st9030_telemetry_reply(emu, asc0_start)
    finally:
        if old_error is None:
            emu.peripherals._readers.pop(0xFF76, None)
        else:
            emu.peripherals.register_read(0xFF76, old_error)
        restore_timer()
    _assert_st9030_frame(
        reply, _st9030_telemetry_body(8, 1, 0x19))
    assert_telemetry_tx(asc1_start, 1)
    assert 0x010D not in emu.asc1.tx_words

    # The proxy itself never touches flash or EEPROM. Cleanup is the reviewed
    # stock E740 finalizer and must then software-reset the C166.
    assert bytes(emu.mem.image) == original_flash
    assert flash.commands == []
    assert eeprom.transactions == []
    reset_count = emu.reset_count
    emu.asc0.rx_inject(b"q\xC3\x3C")
    _run_until_state(
        emu,
        emu.cpu.pc,
        lambda: emu.reset_count > reset_count,
        max_steps=4_000_000,
    )
    assert emu.last_reset_reason == "software"
    assert bytes(emu.mem.image) == original_flash
    assert flash.commands == []
    assert any(item[0] == "write" for item in eeprom.transactions)
    finalizer_writes = sum(
        item[0] == "write" for item in eeprom.transactions)

    # Software reset retains the already verified RAM payload. Re-enter it
    # once at normal E740=0 to execute the protected recovery alias too.
    emu.asc0.tx.clear()
    _run_until_state(
        emu, _SOFTBSL_AGENT,
        lambda: len(emu.asc0.tx) == 1, max_steps=100_000)
    assert bytes(emu.asc0.tx) == b"\xA5"
    reset_count = emu.reset_count
    emu.asc0.rx_inject(b"R\x9C\x9C")
    _run_until_state(
        emu,
        emu.cpu.pc,
        lambda: emu.reset_count > reset_count,
        max_steps=4_000_000,
    )
    assert emu.last_reset_reason == "software"
    assert bytes(emu.mem.image) == original_flash
    assert flash.commands == []
    assert sum(
        item[0] == "write" for item in eeprom.transactions
    ) == finalizer_writes
    print(
        "[PASS] exact bound image / installed loader / ST9030 fixed-slot "
        "/ stock-gate / telemetry ASC1 proxy / safe quit+recovery")


IGNITION_HOOKS = (0xD92A, 0xD98E)       # IP values while CSP=3
IGNITION_CAVE_CPU = 0x3DC70             # full CPU address used by trace
IGNITION_STOCK_REPLAY_CPU = 0x3DC8A     # cave's displaced ANDB P1L,RL1
IGNITION_CONTROL_HOOK = 0x755A          # IP while CSP=2
IGNITION_SINGLE_MASKS = bytes.fromhex("7ebd7bb76f9f")
IGNITION_PAIRED_MASKS = bytes.fromhex("76ad5bb66d9b")


def _execute_control(
        emu, *, rpm, pins=0, state=0xA0, hook=IGNITION_CONTROL_HOOK,
        rpm_address=0xFC3C, input_bytes=(0xFD60, 0xFD61),
        ipw_addresses=(0xEF7E, 0xEF80),
):
    """Run V9's late standalone-request/fixed-IPW hook once."""
    emu.write_byte(rpm_address, rpm)
    if pins is not None:
        emu.write_byte(input_bytes[0], pins & 0xFF)
        emu.write_byte(input_bytes[1], (pins >> 8) & 0xFF)
    emu.write_byte(_CUT_STATE_ADDR, state)
    emu.write(ipw_addresses[0], 0x2222)
    emu.write(ipw_addresses[1], 0x3333)
    emu.reg.r[5] = 0xA55A
    emu.reg.r[6] = 0x5AA5
    emu.cpu.csp = 2
    architectural = (emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp))
    res = _run(emu, hook, stop_at=(hook + 4,), max_steps=500)
    hygiene = (
        res.final_ip == hook + 4 and res.exit_reason == "stop_at"
        and (emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp)) == architectural
        and emu.reg.r[5] == 0xA55A and emu.reg.r[6] == 0x5AA5
    )
    return (
        emu.read_byte(_CUT_STATE_ADDR),
        (emu.read(ipw_addresses[0]), emu.read(ipw_addresses[1])),
        hygiene,
    )


def _run_control(image, **kwargs):
    return _execute_control(_emu(image, dpp0=4), **kwargs)


def _verify_control_calibrations(case_image, **control):
    """Run the shared hysteresis/IPW contract on one firmware layout."""
    image = case_image({
        "CUTSW": 0x00, "CUTRPM": 0x7D,
        "CUT_HYST": 0x0A, "CUT_IPW": 0xFFFF,
        "LC_HYST": 0x00, "LC_IPW": 0xFFFF,
    })
    emu = _emu(image, dpp0=4)
    state, _ipws, hygiene = _execute_control(
        emu, rpm=0x7D, state=0xA0, **control)
    assert state & 0x01 and hygiene
    state, _ipws, hygiene = _execute_control(
        emu, rpm=0x74, state=state, **control)
    assert state & 0x01 and hygiene
    state, _ipws, hygiene = _execute_control(
        emu, rpm=0x72, state=state, **control)
    assert not state & 0x01 and hygiene

    # Hysteresis at or above the limiter cannot wrap the release threshold to
    # zero and leave an active request stuck on.
    for hyst in (0x20, 0x21):
        image = case_image({
            "CUTSW": 0x00, "CUTRPM": 0x20,
            "CUT_HYST": hyst, "CUT_IPW": 0xFFFF,
            "LC_HYST": 0x00, "LC_IPW": 0xFFFF,
        })
        state, _ipws, hygiene = _execute_control(
            _emu(image, dpp0=4), rpm=0x10, state=0xA1, **control)
        assert not state & 0x01 and hygiene, (hyst, hex(state))

    # Each spark requester owns its fixed IPW, including literal zero. Launch
    # wins if both request, fuel cut alone never overrides, and FFFF keeps stock.
    for name, cutsw, rpm, state_in, cut_ipw, lc_ipw, expected in (
        ("standalone", 0x00, 0xC8, 0xA0, 0x1234, 0x5678, (0x1234, 0x1234)),
        ("launch", 0xFF, 0x64, 0xA2, 0x1234, 0x5678, (0x5678, 0x5678)),
        ("both launch priority", 0x00, 0xC8, 0xA2, 0x1234, 0x5678, (0x5678, 0x5678)),
        ("standalone zero", 0x00, 0xC8, 0xA0, 0x0000, 0x5678, (0x0000, 0x0000)),
        ("launch zero", 0xFF, 0x64, 0xA2, 0x1234, 0x0000, (0x0000, 0x0000)),
        ("fuel only", 0xFF, 0x64, 0xA4, 0x1234, 0x5678, (0x2222, 0x3333)),
        ("standalone stock", 0x00, 0xC8, 0xA0, 0xFFFF, 0x5678, (0x2222, 0x3333)),
        ("launch stock", 0xFF, 0x64, 0xA2, 0x1234, 0xFFFF, (0x2222, 0x3333)),
        ("both launch stock priority", 0x00, 0xC8, 0xA2, 0x1234, 0xFFFF, (0x2222, 0x3333)),
    ):
        image = case_image({
            "CUTSW": cutsw, "CUTRPM": 0x7D,
            "CUT_HYST": 0xFF, "CUT_IPW": cut_ipw,
            "LC_HYST": 0xFF, "LC_IPW": lc_ipw,
        })
        state, ipws, hygiene = _execute_control(
            _emu(image, dpp0=4), rpm=rpm, state=state_in, **control)
        assert ipws == expected and hygiene, (name, hex(state), ipws)


def _seed_ignition_gate(emu, *, rpm, state):
    """Seed V9's shared requests; P1L starts high/off like native startup."""
    emu.write_byte(0xFC3C, rpm)
    emu.write_byte(_CUT_STATE_ADDR, state)
    emu.write_byte(0xFF04, 0xFF)


def _run_ignition(
        image, *, rpm, state=0xA0, hook=IGNITION_HOOKS[0], mask=0xFE,
        emu=None):
    """Execute one real CC6-ISR hook through both complete V9 return paths."""
    emu = _emu(image, dpp0=4) if emu is None else emu
    _seed_ignition_gate(emu, rpm=rpm, state=state)
    emu.reg.r[1] = 0x1200 | mask      # selected native clear mask in RL1
    emu.reg.r[4] = 0xA55A             # V9 must preserve the complete r4
    visited = []
    emu.cpu.set_trace(lambda pc, _opcode: visited.append(pc))
    emu.cpu.csp = 3
    architectural = (emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp))
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
        and emu.reg.r[4] == 0xA55A
        and emu.reg.r[1] == (0x1200 | mask)
        and (emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp)) == architectural
    )
    hygiene = hygiene and _service_foreground_watchdog(
        emu, _bound_variant(image))
    return cut, stock, hygiene


def _verify_native_cc6_mask_matrix(cut_image, stock_image):
    """Cover both native mask tables and both recurring charge sites.

    ADB2 clears one cylinder output plus companion P1L.6/.7. ADC4 clears a
    paired cylinder set plus a companion bit. V9 intentionally suppresses the
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
                _seed_ignition_gate(
                    emu, rpm=0xC8, state=0xA1 if wants_cut else 0xA0)
                emu.write_byte(0xFA5F, index)
                emu.write(0xFD5E, fd5e)
                emu.reg.r[4] = 0xA55A
                visited = []
                emu.cpu.set_trace(lambda pc, _opcode: visited.append(pc))
                emu.cpu.csp = 3
                architectural = (emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp))
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
                    and (
                        emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp)
                    ) == architectural
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


def verify_ignition_cut_v9(case_image=_case_image):
    # name, CUTSW, CUTRPM, RPM, pins, LC request, wants cut
    cases = [
        ("always zero", 0x00, 0x00, 0x00, 0, False, True),
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
        image = case_image({
            "CUTSW": switch, "CUTRPM": limit,
            "CUT_HYST": 0xFF, "CUT_IPW": 0xFFFF,
        })
        state, _ipws, control_hygiene = _run_control(
            image, rpm=rpm, pins=pins, state=0xA2 if request else 0xA0)
        cut, stock, hygiene = _run_ignition(
            image, rpm=rpm, state=state)
        assert (
            cut == want_cut and stock == (not want_cut)
            and hygiene and control_hygiene
            and bool(state & 0x02) == request
        ), (name, hex(state), cut, stock, hygiene, control_hygiene)

    # Both byte-identical CC6 ISR sites must make the same cut/stock decision.
    for hook in IGNITION_HOOKS:
        cut_image = case_image({
            "CUTSW": 0x00, "CUTRPM": 0x7D,
            "CUT_HYST": 0xFF, "CUT_IPW": 0xFFFF,
        })
        cut, stock, hygiene = _run_ignition(
            cut_image, rpm=0xC8, state=0xA1, hook=hook)
        assert cut and not stock and hygiene, (hex(hook), cut, stock, hygiene)
        stock_image = case_image({
            "CUTSW": 0xFF, "CUTRPM": 0x7D,
            "CUT_HYST": 0xFF, "CUT_IPW": 0xFFFF,
        })
        cut, stock, hygiene = _run_ignition(
            stock_image, rpm=0xC8, state=0xA0, hook=hook)
        assert not cut and stock and hygiene, (hex(hook), cut, stock, hygiene)

    # Reach both hooks through every native single/paired mask selection. The
    # stock path must execute 0x65 and write P1L; the cut path must leave it high.
    native_cut_image = case_image({
        "CUTSW": 0x00, "CUTRPM": 0x7D,
        "CUT_HYST": 0xFF, "CUT_IPW": 0xFFFF,
    })
    native_stock_image = case_image({
        "CUTSW": 0xFF, "CUTRPM": 0x7D,
        "CUT_HYST": 0xFF, "CUT_IPW": 0xFFFF,
    })
    _verify_native_cc6_mask_matrix(native_cut_image, native_stock_image)

    _verify_control_calibrations(case_image)


A_THRESHOLDS = {"LC_ARMSPEED": 0x05, "LC_MAXSPEED": 0x1E, "LC_MINTPS": 0x80}


def _run_launch_a(
        image, *, speed, tps, fd60, fd61, latch, rpm=0, state=0xA0,
        limiter_active=False):
    emu = _emu(image)
    latch_address = _launch_latch(image)
    architectural = (emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp))
    emu.write_byte(0xF19A, speed)
    emu.write_byte(0xE8D0, tps)
    emu.write_byte(0xFC3C, rpm)
    emu.write_byte(0xFD60, fd60)
    emu.write_byte(0xFD61, fd61)
    emu.write_byte(0xFD13, 0x80 if limiter_active else 0)
    emu.write_byte(_CUT_STATE_ADDR, state)
    emu.write_byte(latch_address, 0x40 if latch else 0)
    emu.cpu.csp = 3
    res = _run(emu, 0x9928, stop_at=(0x992C,), max_steps=500)
    latch_out = bool(emu.read_byte(latch_address) & 0x40)
    state_out = emu.read_byte(_CUT_STATE_ADDR)
    hygiene = (
        res.final_ip == 0x992C
        and res.exit_reason == "stop_at"
        and (emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp)) == architectural
    )
    hygiene = hygiene and _service_foreground_watchdog(
        emu, _bound_variant(image))
    return latch_out, state_out, hygiene


def verify_launch_brain(case_image=_case_image):
    # name, LC_SW, polarity, speed, TPS, fd60, fd61, initial latch, wanted latch
    cases = [
        ("off", 0xFF, 0, 0, 0xC0, 0, 0, 1, 0),
        ("always", 0x00, 0, 0, 0xC0, 0, 0, 0, 1),
        ("pin80 arm", 0x01, 0, 0, 0xC0, *SIR_PIN_BYTES[0x01], 0, 1),
        ("pin80 hold zero", 0x01, 0, 0, 0xC0, 0, 0, 0, 0),
        ("pin80 hold one", 0x01, 0, 0, 0xC0, 0, 0, 1, 1),
        ("speed below arm", 0x01, 0, 0x04, 0xC0, *SIR_PIN_BYTES[0x01], 0, 1),
        ("speed at arm", 0x01, 0, 0x05, 0xC0, *SIR_PIN_BYTES[0x01], 0, 0),
        ("speed below max", 0x01, 0, 0x1D, 0xC0, 0, 0, 1, 1),
        ("speed at max", 0x01, 0, 0x1E, 0xC0, *SIR_PIN_BYTES[0x01], 1, 0),
        ("TPS below min", 0x01, 0, 0, 0x7F, *SIR_PIN_BYTES[0x01], 1, 0),
        ("TPS at min", 0x01, 0, 0, 0x80, 0, 0, 1, 1),
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
        got, state, hygiene = _run_launch_a(
            image, speed=speed, tps=tps, fd60=fd60, fd61=fd61, latch=latch)
        assert got == bool(want) and not state & 0x02 and hygiene, (
            name, got, hex(state), hygiene)

    # The launch requester has its own hysteresis and never touches E847.0.
    image = case_image({
        "LC_SW": 0x00, "LC_CUTTYPE": 1, "LC_MAXRPM": 0x7D,
        "CUT_HYST": 0x00, "LC_HYST": 0x0A, **A_THRESHOLDS,
    })
    latch, state, hygiene = _run_launch_a(
        image, speed=0, tps=0xC0, fd60=0, fd61=0, latch=True,
        rpm=0x7C, state=0xA1)
    assert latch and state & 0x03 == 0x01 and hygiene
    latch, state, hygiene = _run_launch_a(
        image, speed=0, tps=0xC0, fd60=0, fd61=0, latch=False,
        rpm=0x7D, state=0xA1)
    assert latch and state & 0x03 == 0x03 and hygiene
    latch, state, hygiene = _run_launch_a(
        image, speed=0, tps=0xC0, fd60=0, fd61=0, latch=latch,
        rpm=0x73, state=state)
    assert latch and state & 0x03 == 0x03 and hygiene
    latch, state, hygiene = _run_launch_a(
        image, speed=0, tps=0xC0, fd60=0, fd61=0, latch=latch,
        rpm=0x72, state=state)
    assert latch and state & 0x03 == 0x01 and hygiene

    # Release standalone first while Launch stays above its own release point,
    # then release Launch. Neither requester may clear the other's bit.
    state, _ipws, hygiene = _run_control(image, rpm=0xC0, state=0xA3)
    assert state & 0x03 == 0x02 and hygiene
    latch, state, hygiene = _run_launch_a(
        image, speed=0, tps=0xC0, fd60=0, fd61=0, latch=True,
        rpm=0xC0, state=state)
    assert latch and state & 0x03 == 0x02 and hygiene
    latch, state, hygiene = _run_launch_a(
        image, speed=0, tps=0xC0, fd60=0, fd61=0, latch=latch,
        rpm=0x72, state=state)
    assert latch and state & 0x03 == 0x00 and hygiene

    for hyst in (0x20, 0x21):
        edge_image = case_image({
            "LC_SW": 0x00, "LC_CUTTYPE": 1, "LC_MAXRPM": 0x20,
            "LC_HYST": hyst, **A_THRESHOLDS,
        })
        latch, state, hygiene = _run_launch_a(
            edge_image, speed=0, tps=0xC0, fd60=0, fd61=0, latch=True,
            rpm=0x10, state=0xA2)
        assert latch and not state & 0x02 and hygiene, (hyst, hex(state))

    # Fuel-cut state reflects the stock limiter's actual FD12.15 signal, not
    # merely an armed launch configuration, and is absent in ignition mode.
    image = case_image({
        "LC_SW": 0x00, "LC_CUTTYPE": 0, "LC_MAXRPM": 0x7D,
        **A_THRESHOLDS,
    })
    for limiter_active, expected in ((False, False), (True, True)):
        _latch, state, hygiene = _run_launch_a(
            image, speed=0, tps=0xC0, fd60=0, fd61=0, latch=False,
            rpm=0xC8, limiter_active=limiter_active)
        assert bool(state & 0x04) == expected and not state & 0x02 and hygiene
    image = case_image({
        "LC_SW": 0x00, "LC_CUTTYPE": 1, "LC_MAXRPM": 0x7D,
        **A_THRESHOLDS,
    })
    _latch, state, hygiene = _run_launch_a(
        image, speed=0, tps=0xC0, fd60=0, fd61=0, latch=False,
        rpm=0xC8, limiter_active=True)
    assert state & 0x02 and not state & 0x04 and hygiene


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
        architectural = (emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp))
        emu.write_byte(_launch_latch(image), 0x40 if latch else 0)
        emu.write(0xFD30, 0x0010 if fd30_4 else 0)
        emu.write_byte(0xF014, stock_limit)
        emu.cpu.csp = 2
        res = _run(emu, 0x07D2, stop_at=(0x07D6, 0x07E8), max_steps=300)
        watchdog = _service_foreground_watchdog(
            emu, _bound_variant(image))
        hygiene = (res.final_ip == continuation and res.exit_reason == "stop_at"
                   and (emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp))
                   == architectural and watchdog)
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
        architectural = (emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp))
        emu.write_byte(_launch_latch(image), 0x40 if latch else 0)
        emu.write_byte(0xF014, stock_limit)
        emu.cpu.csp = 2
        res = _run(emu, 0x07D2, stop_at=(0x07D6,), max_steps=300)
        watchdog = _service_foreground_watchdog(
            emu, _bound_variant(image))
        hygiene = (
            res.final_ip == 0x07D6
            and res.exit_reason == "stop_at"
            and (
                emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp)
            ) == architectural
            and watchdog
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
            architectural = (emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp))
            emu.write_byte(_launch_latch(image), 0x40 if latch else 0)
            emu.write_byte(0xFC3C, rpm)
            emu.write_byte(0xDB86, 0x10)
            emu.write_byte(0xDB87, 0xCE)
            emu.cpu.csp = 2
            res = _run(emu, entry, stop_at=outcomes, max_steps=300)
            watchdog = _service_foreground_watchdog(
                emu, _bound_variant(image))
            assert (res.final_ip == outcomes[result_index]
                    and res.exit_reason == "stop_at"
                    and (
                        emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp)
                    ) == architectural
                    and watchdog
                    and emu.read_byte(0xDB86) == 0x10
                    and emu.read_byte(0xDB87) == 0xCE), (
                        name, entry, outcomes, res,
                        emu.read_byte(0xDB86), emu.read_byte(0xDB87))


def _verify_stock_limiter_parity(
        stock_path, patched_image, *, soft_entry, soft_stops, soft_address,
        rpm_address, hard_sites, stock_hard_address):
    """Compare the disabled patch path with the canonical stock limiter bytes."""
    variant = _REFERENCE_VARIANTS[stock_path.resolve()]
    stock_image = _bind_image(stock_path.read_bytes(), variant)

    soft_results = []
    for image in (stock_image, patched_image):
        emu = _emu(image, dpp0=4)
        architectural = (emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp))
        emu.write_byte(_launch_latch(image), 0)
        emu.write(0xFD30, 0)
        emu.write_byte(_CUT_STATE_ADDR, 0xA0)
        emu.write_byte(soft_address, 0xCB)
        emu.cpu.csp = 2
        res = _run(emu, soft_entry, stop_at=soft_stops, max_steps=600)
        assert (
            res.exit_reason == "stop_at"
            and (
                emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp)
            ) == architectural
        ), (variant, "stock soft limiter", res)
        soft_results.append((res.final_pc, emu.read_byte(soft_address)))
    assert (
        soft_results[0] == soft_results[1]
        and soft_results[0][1] == 0xCB
    ), (variant, soft_results)

    for rpm, result_index in ((0xCD, 0), (0xCE, 1)):
        for entry, outcomes in hard_sites:
            results = []
            for image in (stock_image, patched_image):
                emu = _emu(image, dpp0=4)
                architectural = (
                    emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp))
                emu.write_byte(_launch_latch(image), 0)
                emu.write_byte(rpm_address, rpm)
                if stock_hard_address >= 0xC000:
                    emu.write_byte(stock_hard_address, 0xCE)
                else:
                    assert emu.read_byte(stock_hard_address) == 0xCE
                emu.cpu.csp = 2
                res = _run(emu, entry, stop_at=outcomes, max_steps=300)
                assert (
                    res.final_ip == outcomes[result_index]
                    and res.exit_reason == "stop_at"
                    and (
                        emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp)
                    ) == architectural
                ), (variant, "stock hard limiter", hex(entry), hex(rpm), res)
                results.append(res.final_pc)
            assert results[0] == results[1], (
                variant, hex(entry), hex(rpm), results)


def verify_stock_limiter_parity(
        case_image=_case_image, stock_path=STOCK_PATH,
        soft_stops=(0x07D6, 0x07E8)):
    image = case_image({
        "LC_SW": 0xFF, "LC_CUTTYPE": 0, "LC_MAXRPM": 0x7D,
        **A_THRESHOLDS,
    })
    _verify_stock_limiter_parity(
        stock_path,
        image,
        soft_entry=0x07D2,
        soft_stops=soft_stops,
        soft_address=0xF014,
        rpm_address=0xFC3C,
        hard_sites=(
            (0x0886, (0x0890, 0x08E0)),
            (0x0926, (0x0942, 0x0930)),
        ),
        stock_hard_address=0xDB87,
    )


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
            "CUT_HYST": 0xFF, "CUT_IPW": 0xFFFF,
            "LC_CUTTYPE": cut_type, "LC_MAXRPM": lc_rpm,
            "LC_HYST": 0xFF, "LC_IPW": 0xFFFF, **A_THRESHOLDS,
        })
        state, _ipws, control_hygiene = _run_control(
            image, rpm=rpm, state=0xA0)
        _latch, state, launch_hygiene = _run_launch_a(
            image, speed=0, tps=0xC0, fd60=0, fd61=0,
            latch=False, rpm=rpm, state=state)
        cut, stock, hygiene = _run_ignition(
            image, rpm=rpm, state=state)
        assert (
            cut == want and stock == (not want)
            and control_hygiene and launch_hygiene and hygiene
        ), (name, hex(state), cut, stock, want, hygiene)


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

    # Intentional cut without a watchdog reset is the causal inverse.
    cut_emu = _emu(image, dpp0=4)
    if variant in {"1406464", "SS1v2"}:
        cut, stock, hygiene = _run_ignition(
            image, rpm=0xC8, state=0xA1, emu=cut_emu)
    else:
        version = "MS41.0" if variant == "1429861" else "MS41.1"
        cut, stock, hygiene = _run_older_ignition(
            OLDER_FEATURE_LAYOUTS[version], image, rpm=0xC8,
            request=True, emu=cut_emu)
    assert cut and not stock and hygiene
    cut_emu.reg.r[12] = 0x206B
    _call_native(cut_emu, evaluator)
    assert (
        cut_emu.reset_count == 0
        and cut_emu.read(0xEA0C) == 0
        and not cut_emu.read_byte(status_address) & 0x60
        and cut_emu.read(reason_address) == 0
        and cut_emu.read_byte(0xEA18) == 0
    ), (variant, "intentional-cut inverse")


def _run_older_ignition(
        layout, image, *, rpm, pins=0, request=False, hook=None, mask=0xFE,
        emu=None):
    hook = layout["ignition_hooks"][0] if hook is None else hook
    emu = _emu(image, dpp0=4) if emu is None else emu
    _seed_older_feature_inputs(
        emu, layout, pins & 0xFF, (pins >> 8) & 0xFF)
    state, _ipws, control_hygiene = _execute_control(
        emu, rpm=rpm, pins=None, state=0xA2 if request else 0xA0,
        hook=layout["control_hook"], rpm_address=layout["rpm_address"],
        input_bytes=layout["input_bytes"],
        ipw_addresses=layout["ipw_addresses"],
    )
    emu.write_byte(_CUT_STATE_ADDR, state)
    emu.write_byte(0xFF04, 0xFF)
    emu.reg.r[1] = 0x1200 | mask
    emu.reg.r[4] = 0xA55A
    visited = []
    emu.cpu.set_trace(lambda pc, _opcode: visited.append(pc))
    emu.cpu.csp = 3
    architectural = (emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp))
    res = _run(emu, hook, stop_at=(hook + 4,), max_steps=300)
    replayed = layout["ignition_replay_cpu"] in visited
    hygiene = (
        res.final_ip == hook + 4 and res.exit_reason == "stop_at"
        and layout["ignition_cave_cpu"] in visited
        and emu.reg.r[4] == 0xA55A
        and emu.reg.r[1] == (0x1200 | mask)
        and (emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp)) == architectural
        and control_hygiene
    )
    hygiene = hygiene and _service_foreground_watchdog(
        emu, _bound_variant(image))
    return (
        not replayed and emu.read_byte(0xFF04) == 0xFF,
        replayed and emu.read_byte(0xFF04) == mask,
        hygiene,
    )


def _verify_older_ignition(layout):
    cases = [
        ("always zero", 0x00, 0x00, 0x00, 0, False, True),
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
            _state, _ipws, control_hygiene = _execute_control(
                emu, rpm=0xC8, state=0xA0,
                hook=layout["control_hook"],
                rpm_address=layout["rpm_address"],
                input_bytes=layout["input_bytes"],
                ipw_addresses=layout["ipw_addresses"],
            )
            emu.write_byte(0xFA5F, index)
            emu.write(layout["paired_selector"], 0)
            emu.write_byte(0xFF04, 0xFF)
            emu.cpu.csp = 3
            res = _run(emu,
                layout["ignition_entry"],
                stop_at=(layout["ignition_hooks"][0] + 4,),
                max_steps=300,
            )
            assert res.exit_reason == "stop_at" and control_hygiene
            assert emu.read_byte(0xFF04) == (0xFF if wants_cut else mask), (
                index, hex(mask), wants_cut, res)

    _verify_control_calibrations(
        lambda values: _case_image_older(layout, values),
        pins=None,
        hook=layout["control_hook"],
        rpm_address=layout["rpm_address"],
        input_bytes=layout["input_bytes"],
        ipw_addresses=layout["ipw_addresses"],
    )


def _run_older_launch(
        layout, image, *, speed, tps, fd60=0, fd61=0, soft=0xCB,
        fd30_4=False, latch=False, rpm=0xC8, state=0xA0,
        limiter_active=False):
    emu = _emu(image)
    emu.write_byte(layout["speed_address"], speed)
    emu.write_byte(
        0xF19A, 0x00 if speed >= A_THRESHOLDS["LC_MAXSPEED"] else 0xFF)
    emu.write_byte(0xE8D0, tps)
    emu.write_byte(layout["rpm_address"], rpm)
    _seed_older_feature_inputs(emu, layout, fd60, fd61)
    emu.write_byte(0xFD13, 0x80 if limiter_active else 0)
    emu.write_byte(_CUT_STATE_ADDR, state)
    emu.write_byte(layout["launch_latch"], 0x40 if latch else 0)
    emu.write(0xFD30, 0x0010 if fd30_4 else 0)
    emu.write_byte(layout["soft_limit_address"], soft)
    emu.cpu.csp = 2
    architectural = (emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp))
    res = _run(emu,
        layout["launch_hook"],
        stop_at=layout["launch_continuations"],
        max_steps=600,
    )
    assert (
        emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp)
    ) == architectural, (
        hex(layout["launch_hook"]), hex(state), architectural,
        (emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp)),
    )
    assert _service_foreground_watchdog(emu, _bound_variant(image))
    return emu, res, architectural[0]


def _verify_older_launch(layout):
    state_cases = [
        ("off", 0xFF, 0, 0, 0xC0, 0, 0, 1, False),
        ("always", 0x00, 0, 0, 0xC0, 0, 0, 0, True),
        ("pin80 arm", 0x01, 0, 0, 0xC0, *SIR_PIN_BYTES[0x01], 0, True),
        ("pin80 hold zero", 0x01, 0, 0, 0xC0, 0, 0, 0, False),
        ("pin80 hold one", 0x01, 0, 0, 0xC0, 0, 0, 1, True),
        ("pin81", 0x02, 0, 0, 0xC0, *SIR_PIN_BYTES[0x02], 0, True),
        ("pin82", 0x04, 0, 0, 0xC0, *SIR_PIN_BYTES[0x04], 0, True),
        ("speed below arm", 0x01, 0, 0x04, 0xC0, *SIR_PIN_BYTES[0x01], 0, True),
        ("speed at arm", 0x01, 0, 0x05, 0xC0, *SIR_PIN_BYTES[0x01], 0, False),
        ("speed below max", 0x01, 0, 0x1D, 0xC0, 0, 0, 1, True),
        ("speed at max", 0x01, 0, 0x1E, 0xC0, *SIR_PIN_BYTES[0x01], 1, False),
        ("TPS below min", 0x01, 0, 0, 0x7F, *SIR_PIN_BYTES[0x01], 1, False),
        ("TPS at min", 0x01, 0, 0, 0x80, 0, 0, 1, True),
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
            bool(emu.read_byte(layout["launch_latch"]) & 0x40) == wants_latch
            and res.exit_reason == "stop_at"
            and res.regs["sp"] == sp0 and res.regs["dpp"][0] == 5
        ), (name, res, hex(emu.read_byte(layout["launch_latch"])))

    for hyst in (0x20, 0x21):
        image = _case_image_older(layout, {
            "LC_SW": 0x00, "LC_CUTTYPE": 1,
            "LC_CLUTCHPOL": 0, "LC_MAXRPM": 0x20,
            "LC_HYST": hyst, **A_THRESHOLDS,
        })
        emu, res, sp0 = _run_older_launch(
            layout, image, speed=0, tps=0xC0, latch=True,
            rpm=0x10, state=0xA2)
        assert (
            not emu.read_byte(_CUT_STATE_ADDR) & 0x02
            and res.exit_reason == "stop_at"
            and res.regs["sp"] == sp0 and res.regs["dpp"][0] == 5
        ), (hyst, res, hex(emu.read_byte(_CUT_STATE_ADDR)))

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
            emu.write_byte(layout["launch_latch"], 0x40 if latch else 0)
            emu.write_byte(layout["rpm_address"], rpm)
            emu.cpu.csp = 2
            architectural = (emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp))
            res = _run(emu, entry, stop_at=outcomes, max_steps=300)
            watchdog = _service_foreground_watchdog(
                emu, _bound_variant(image))
            assert (
                res.final_ip == outcomes[result_index]
                and res.exit_reason == "stop_at"
                and (
                    emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp)
                ) == architectural
                and watchdog
            ), (name, entry, outcomes, res)

    parity_image = _case_image_older(layout, {
        "LC_SW": 0xFF, "LC_CUTTYPE": 0, "LC_CLUTCHPOL": 0,
        "LC_MAXRPM": 0x7D, **A_THRESHOLDS,
    })
    _verify_stock_limiter_parity(
        layout["stock_path"],
        parity_image,
        soft_entry=layout["launch_hook"],
        soft_stops=layout["launch_continuations"],
        soft_address=layout["soft_limit_address"],
        rpm_address=layout["rpm_address"],
        hard_sites=layout["hard_sites"],
        stock_hard_address=layout["stock_hard_address"],
    )

    # Ignition-mode launch request composes with this firmware's V9 hook.
    image = _case_image_older(layout, {
        "CUTSW": 0xFF, "CUTRPM": 0xD7,
        "LC_SW": 0x00, "LC_CUTTYPE": 1, "LC_CLUTCHPOL": 0,
        "LC_MAXRPM": 0x7D, **A_THRESHOLDS,
    })
    emu, res, _sp0 = _run_older_launch(
        layout, image, speed=0, tps=0xC0)
    request = bool(emu.read_byte(_CUT_STATE_ADDR) & 0x02)
    cut, stock, hygiene = _run_older_ignition(
        layout, image, rpm=0xC8, request=request)
    assert request and cut and not stock and hygiene, (res, request, cut)

    # Exercise the complete independent-request pipeline with deliberately
    # different Launch (4000 RPM) and standalone (6880 RPM) thresholds.
    for name, cutsw, lc_sw, rpm, expected_state, wants_cut in (
        ("launch only", 0xFF, 0x00, 0x8C, 0x02, True),
        ("standalone only", 0x00, 0xFF, 0xE0, 0x01, True),
        ("both", 0x00, 0x00, 0xE0, 0x03, True),
        ("between thresholds", 0x00, 0x00, 0x8C, 0x02, True),
        ("below both", 0x00, 0x00, 0x50, 0x00, False),
    ):
        image = _case_image_older(layout, {
            "CUTSW": cutsw, "CUTRPM": 0xD7,
            "LC_SW": lc_sw, "LC_CUTTYPE": 1, "LC_CLUTCHPOL": 0,
            "LC_MAXRPM": 0x7D, "CUT_HYST": 0xFF, "CUT_IPW": 0xFFFF,
            "LC_HYST": 0xFF, "LC_IPW": 0xFFFF, **A_THRESHOLDS,
        })
        state, _ipws, control_hygiene = _execute_control(
            _emu(image, dpp0=4), rpm=rpm, pins=None, state=0xA0,
            hook=layout["control_hook"], rpm_address=layout["rpm_address"],
            input_bytes=layout["input_bytes"],
            ipw_addresses=layout["ipw_addresses"],
        )
        emu, _res, _sp0 = _run_older_launch(
            layout, image, speed=0, tps=0xC0, rpm=rpm, state=state)
        state = emu.read_byte(_CUT_STATE_ADDR)
        cut, stock, hygiene = _run_older_ignition(
            layout, image, rpm=rpm, request=bool(state & 0x02))
        assert (
            state & 0x03 == expected_state
            and cut == wants_cut and stock == (not wants_cut)
            and control_hygiene and hygiene
        ), (name, hex(state), cut, stock)

    # Match the MS41.2/.3 launch hysteresis, coexistence, and release-order
    # matrix on the older layouts.
    image = _case_image_older(layout, {
        "CUTSW": 0x00, "CUTRPM": 0xD7,
        "LC_SW": 0x00, "LC_CUTTYPE": 1, "LC_CLUTCHPOL": 0,
        "LC_MAXRPM": 0x7D, "CUT_HYST": 0x00,
        "LC_HYST": 0x0A, **A_THRESHOLDS,
    })
    emu, _res, _sp0 = _run_older_launch(
        layout, image, speed=0, tps=0xC0, rpm=0x7C,
        state=0xA1, latch=True)
    assert emu.read_byte(_CUT_STATE_ADDR) & 0x03 == 0x01
    emu, _res, _sp0 = _run_older_launch(
        layout, image, speed=0, tps=0xC0, rpm=0x7D, state=0xA1)
    state = emu.read_byte(_CUT_STATE_ADDR)
    assert state & 0x03 == 0x03
    emu, _res, _sp0 = _run_older_launch(
        layout, image, speed=0, tps=0xC0, rpm=0x73,
        state=state, latch=True)
    state = emu.read_byte(_CUT_STATE_ADDR)
    assert state & 0x03 == 0x03
    emu, _res, _sp0 = _run_older_launch(
        layout, image, speed=0, tps=0xC0, rpm=0x72,
        state=state, latch=True)
    state = emu.read_byte(_CUT_STATE_ADDR)
    assert state & 0x03 == 0x01

    state, _ipws, hygiene = _execute_control(
        _emu(image, dpp0=4), rpm=0xC0, pins=None, state=0xA3,
        hook=layout["control_hook"], rpm_address=layout["rpm_address"],
        input_bytes=layout["input_bytes"],
        ipw_addresses=layout["ipw_addresses"],
    )
    assert state & 0x03 == 0x02 and hygiene
    emu, _res, _sp0 = _run_older_launch(
        layout, image, speed=0, tps=0xC0, rpm=0xC0,
        state=state, latch=True)
    state = emu.read_byte(_CUT_STATE_ADDR)
    assert state & 0x03 == 0x02
    emu, _res, _sp0 = _run_older_launch(
        layout, image, speed=0, tps=0xC0, rpm=0x72,
        state=state, latch=True)
    assert emu.read_byte(_CUT_STATE_ADDR) & 0x03 == 0x00

    # Actual launch fuel-cut state follows FD12.15 on every firmware family.
    image = _case_image_older(layout, {
        "LC_SW": 0x00, "LC_CUTTYPE": 0, "LC_CLUTCHPOL": 0,
        "LC_MAXRPM": 0x7D, **A_THRESHOLDS,
    })
    for limiter_active, expected in ((False, False), (True, True)):
        emu, _res, _sp0 = _run_older_launch(
            layout, image, speed=0, tps=0xC0,
            limiter_active=limiter_active)
        state = emu.read_byte(_CUT_STATE_ADDR)
        assert bool(state & 0x04) == expected and not state & 0x02
    image = _case_image_older(layout, {
        "LC_SW": 0x00, "LC_CUTTYPE": 1, "LC_CLUTCHPOL": 0,
        "LC_MAXRPM": 0x7D, **A_THRESHOLDS,
    })
    emu, _res, _sp0 = _run_older_launch(
        layout, image, speed=0, tps=0xC0, limiter_active=True)
    state = emu.read_byte(_CUT_STATE_ADDR)
    assert state & 0x02 and not state & 0x04

    # A zero-RPM bench calibration can expose the independent launch request;
    # disabling launch clears that request and the arm latch.
    image = _case_image_older(layout, {
        "CUTSW": 0xFF, "CUTRPM": 0x00,
        "LC_SW": 0x00, "LC_CUTTYPE": 1, "LC_CLUTCHPOL": 0,
        "LC_MAXRPM": 0x00, **A_THRESHOLDS,
    })
    emu, res, _sp0 = _run_older_launch(
        layout, image, speed=0, tps=0xC0, rpm=0, state=0)
    state = emu.read_byte(_CUT_STATE_ADDR)
    cut, stock, hygiene = _run_older_ignition(
        layout, image, rpm=0, request=bool(state & 0x02))
    assert (
        state & 0xF3 == 0xA2
        and emu.read_byte(layout["launch_latch"]) & 0x40
        and cut and not stock and hygiene
    ), (res, hex(state), cut, stock)

    image = _case_image_older(layout, {
        "LC_SW": 0xFF, "LC_CUTTYPE": 1, "LC_MAXRPM": 0x00,
        **A_THRESHOLDS,
    })
    emu, res, _sp0 = _run_older_launch(
        layout, image, speed=0, tps=0xC0, latch=True, rpm=0, state=state)
    assert (
        emu.read_byte(_CUT_STATE_ADDR) & 0xF3 == 0xA0
        and not emu.read_byte(layout["launch_latch"]) & 0x40
    ), (
        res, hex(emu.read_byte(_CUT_STATE_ADDR)),
        hex(emu.read_byte(layout["launch_latch"])),
    )


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


# Both firmware families dispatch cylinders in the order 1, 5, 3, 6, 2, 4.
COIL_DTC_DESCRIPTORS_410 = (
    (0xA6E2, 29), (0xA6F2, 31), (0xA702, 30),
    (0xA712, 3), (0xA722, 1), (0xA732, 2),
)
COIL_DTC_DESCRIPTORS_LATE = (
    (0xA72C, 29), (0xA73C, 31), (0xA74C, 30),
    (0xA75C, 3), (0xA76C, 1), (0xA77C, 2),
)
MISFIRE_DTC_DESCRIPTORS_LATE = (
    (0xAA0C, 238), (0xAA1C, 242), (0xAA2C, 240),
    (0xAA3C, 243), (0xAA4C, 239), (0xAA5C, 241),
)


CUT_GUARD_LAYOUTS = {
    "MS41.0": {
        "image": lambda: OLDER_FEATURE_LAYOUTS["MS41.0"]["image"],
        "stft": (0x28664, 0x32D3A, 0x28668, (0xED56, 0xED90)),
        "ltft": (0x28C40, (0xED56, 0xED90), 0x18, 0x2222),
        "additive": (0x28C6E, (0xED56, 0xED90), 0x10, 0x3333),
        "diagnostics": (
            # Shared roughness/misfire detector plus coil/resistor diagnostics.
            (0x2CC98, 0x2CF58, 0x2CC9C),
            (0x27D42, 0x27DFA, 0x27D46),
            (0x27984, 0x279C0, 0x27988),
        ),
        "dtc_descriptors": COIL_DTC_DESCRIPTORS_410,
        "dtc_table": (0xA582, 0xA8C2),
        "dtc_manager": (0x259E6, 0x25B0E),
        "dtc_maturation": (
            (0, 0xEAF6, 0xA6E2),
        ),
    },
    "MS41.1": {
        "image": lambda: OLDER_FEATURE_LAYOUTS["MS41.1"]["image"],
        "stft": (0x2CA5A, 0x3FBFA, 0x2CA5E, (0xF030, 0xF0EC)),
        "ltft": (0x2D382, (0xF030, 0xF0EC), 0x18, 0x2222),
        "additive": (0x2D3B0, (0xF030, 0xF0EC), 0x10, 0x3333),
        "diagnostics": (
            (0x36FB6, 0x372A6, 0x36FBA),
            (0x2C030, 0x2C132, 0x2C034),
            (0x2B42C, 0x2B48C, 0x2B430),
            (0x2B4EE, 0x2B54E, 0x2B4F2),
            (0x30FBE, 0x3114A, 0x30FC2),
            (0x37D50, 0x37DA6, 0x37D54),
            (0x37D7C, 0x37DA6, 0x37D80),
        ),
        "dtc_descriptors": (
            *COIL_DTC_DESCRIPTORS_LATE,
            *MISFIRE_DTC_DESCRIPTORS_LATE,
        ),
        "dtc_manager": (0x27CE6, 0x27EAA),
        "dtc_maturation": (
            (0, 0xEB22, 0xA72C),
            (4, 0xED4A, 0xAA0C),
        ),
    },
    "MS41.2": {
        "image": lambda: FULL_IMAGE,
        "stft": (0x2BF10, 0x3E1BA, 0x2BF14, (0xF018, 0xF0C4)),
        "ltft": (0x2C86E, (0xF018, 0xF0C4), 0x18, 0x2222),
        "additive": (0x2C89C, (0xF018, 0xF0C4), 0x10, 0x3333),
        "diagnostics": (
            (0x3553E, 0x3582E, 0x35542),
            (0x2B5A6, 0x2B6A8, 0x2B5AA),
            (0x2B05C, 0x2B0BC, 0x2B060),
            (0x2B11E, 0x2B17E, 0x2B122),
            (0x36248, 0x3629E, 0x3624C),
            (0x36274, 0x3629E, 0x36278),
            (0x3025C, 0x30330, 0x30260),
        ),
        "dtc_descriptors": (
            *COIL_DTC_DESCRIPTORS_LATE,
            *MISFIRE_DTC_DESCRIPTORS_LATE,
        ),
        "dtc_manager": (0x27956, 0x27B1A),
        "dtc_maturation": (
            (0, 0xEB22, 0xA72C),
            (6, 0xED4A, 0xAA0C),
        ),
    },
    "MS41.3": {
        "image": lambda: FULL_413_IMAGE,
        "stft": (0x2BF10, 0x3E1BA, 0x2BF14, (0xF018, 0xF0C4)),
        "ltft": (0x2C86E, (0xF018, 0xF0C4), 0x18, 0x2222),
        "additive": (0x2C89C, (0xF018, 0xF0C4), 0x10, 0x3333),
        "diagnostics": (
            (0x3553E, 0x3582E, 0x35542),
            (0x2B05C, 0x2B0BC, 0x2B060),
            (0x2B11E, 0x2B17E, 0x2B122),
            (0x36248, 0x3629E, 0x3624C),
            (0x36274, 0x3629E, 0x36278),
            (0x3025C, 0x30330, 0x30260),
        ),
        "dtc_descriptors": (
            *COIL_DTC_DESCRIPTORS_LATE,
            *MISFIRE_DTC_DESCRIPTORS_LATE,
        ),
        "dtc_manager": (0x27956, 0x27B1A),
        "dtc_maturation": (
            (0, 0xEB22, 0xA72C),
            (5, 0xED4A, 0xAA0C),
        ),
    },
}


def _guard_route(image, hook, state, stops, seeds=(), emu=None):
    emu = _emu(image, dpp0=4) if emu is None else emu
    emu.write_byte(_CUT_STATE_ADDR, state)
    for address, value in seeds:
        emu.write(address, value)
    emu.cpu.csp = hook >> 16
    sp0 = emu.reg.sp
    architectural = (emu.reg.cp, tuple(emu.reg.dpp))
    res = _run(emu,
        hook & 0xFFFF,
        stop_at=tuple(stop & 0xFFFF for stop in stops),
        max_steps=100,
    )
    assert (
        emu.reg.cp, tuple(emu.reg.dpp)
    ) == architectural, (
        hex(hook), hex(state), architectural,
        (emu.reg.cp, tuple(emu.reg.dpp)),
    )
    final = (emu.cpu.csp << 16) | res.final_ip
    return emu, res, final, sp0


def _verify_post_release_dtc_maturation(version, image, layout):
    """Cross-check real descriptors and prove the stock DTC engine re-arms."""
    descriptor_ids = {
        pointer: expected for pointer, expected in layout["dtc_descriptors"]
    }
    probe = _emu(image, dpp0=4)
    assert all(
        probe.read_byte(pointer) == expected
        for pointer, expected in descriptor_ids.items()
    ), (version, "DTC descriptor IDs")

    if "dtc_table" in layout:
        start, stop = layout["dtc_table"]
        stock_ids = {probe.read_byte(pointer)
                     for pointer in range(start, stop, 0x10)}
        assert not stock_ids.intersection(range(238, 244)), (
            version, "unexpected misfire DTC descriptor")

    manager, manager_rets = layout["dtc_manager"]
    for guard_index, record, descriptor in layout["dtc_maturation"]:
        hook, active_cleanup, stock_cont = layout["diagnostics"][guard_index]
        emu = _emu(image, dpp0=4)
        matured = None
        for cycle in range(2):
            before = (
                emu.read_byte(record),
                emu.read_byte(record + 1),
                emu.read(0xFD38),
            )
            emu, res, final, sp0 = _guard_route(
                image, hook, 0xA1, (active_cleanup, stock_cont), emu=emu)
            assert (
                final == active_cleanup
                and res.exit_reason == "stop_at"
                and res.regs["sp"] == sp0
                and (
                    emu.read_byte(record),
                    emu.read_byte(record + 1),
                    emu.read(0xFD38),
                ) == before
            ), (version, "DTC guard active", cycle, hex(hook), res)

            emu, res, final, sp0 = _guard_route(
                image, hook, 0xA0, (active_cleanup, stock_cont), emu=emu)
            assert final == stock_cont and res.exit_reason == "stop_at", (
                version, "DTC guard release", cycle, hex(hook), res, hex(final))

            # Run the exact stock continuation up to its shared cleanup. The
            # deterministic fixture then calls the native DTC manager directly
            # because the production monitor inputs are asynchronous.
            architectural = (emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp))
            visited = []
            emu.cpu.set_trace(lambda pc, _opcode: visited.append(pc))
            res = _run(emu,
                stock_cont & 0xFFFF,
                stop_at=(active_cleanup & 0xFFFF,),
                max_steps=500,
            )
            final = (emu.cpu.csp << 16) | res.final_ip
            assert (
                final == active_cleanup
                and res.exit_reason == "stop_at"
                and stock_cont in visited
                and (
                    emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp)
                ) == architectural
            ), (version, "stock diagnostic continuation", cycle, res)

            # A one-count increment and threshold exercise the native maturity
            # branch deterministically; production callers supply calibrated values.
            emu.write_byte(0xEA0E, 1)
            emu.reg.r[12] = record
            emu.reg.r[13] = descriptor
            emu.reg.r[14] = 1
            emu.reg.r[15] = 1
            emu.cpu.csp = manager >> 16
            res = _run(emu,
                manager & 0xFFFF,
                stop_at=(manager_rets & 0xFFFF,),
                max_steps=500,
            )
            final = (emu.cpu.csp << 16) | res.final_ip
            current = (
                emu.read_byte(record),
                emu.read_byte(record + 1),
                emu.read(0xFD38),
            )
            assert (
                descriptor in descriptor_ids
                and final == manager_rets
                and res.exit_reason == "stop_at"
                and res.regs["sp"] == sp0
                and current[0] & 0x60 == 0x60
                and current[1] == 1
                and current[2] & 0x60 == 0x60
                and (matured is None or current == matured)
            ), (
                version, "post-release DTC maturation", cycle,
                descriptor_ids.get(descriptor), res,
            )
            matured = current


def verify_cut_side_effect_guards(version):
    """Prove active suppression and immediate stock restoration via real splices."""
    layout = CUT_GUARD_LAYOUTS[version]
    image = layout["image"]()

    hook, active_rets, stock_cont, bank_bases = layout["stft"]
    stft_values = tuple(
        (base + 0x06, 0x1100 + index) for index, base in enumerate(bank_bases)
    )
    for state, expected in (
        (0x00, stock_cont),
        (0xA0, stock_cont),
        (0xA1, active_rets),
        (0xA2, active_rets),
        (0xA4, active_rets),
    ):
        emu, res, final, sp0 = _guard_route(
            image, hook, state, (active_rets, stock_cont), stft_values)
        assert final == expected and res.exit_reason == "stop_at", (
            version, "stft", hex(state), res, hex(final))
        assert all(
            emu.read(address) == value for address, value in stft_values
        ), (version, "stft banks", hex(state))
        if state & 0x07:
            assert res.regs["sp"] == sp0, (version, "stft stack", res)

    for name in ("ltft", "additive"):
        hook, bank_bases, displacement, stock_value = layout[name]
        for bank, base in enumerate(bank_bases):
            address = base + displacement
            initial = 0x1100 + bank
            for state, expected in (
                (0xA0, stock_value),
                (0xA1, initial),
                (0xA2, initial),
                (0xA4, initial),
            ):
                emu = _emu(image, dpp0=4)
                emu.write_byte(_CUT_STATE_ADDR, state)
                emu.write(address, initial)
                emu.reg.r[6 if name == "ltft" else 7] = base
                emu.reg.r[8 if name == "ltft" else 4] = stock_value
                emu.cpu.csp = hook >> 16
                architectural = (
                    emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp))
                res = _run(emu,
                    hook & 0xFFFF,
                    stop_at=((hook + 4) & 0xFFFF,),
                    max_steps=100,
                )
                assert (
                    res.final_ip == ((hook + 4) & 0xFFFF)
                    and emu.cpu.csp == (hook >> 16)
                    and res.exit_reason == "stop_at"
                    and (
                        emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp)
                    ) == architectural
                    and emu.read(address) == expected
                ), (
                    version, name, bank, hex(state), res,
                    hex(emu.read(address)),
                )

    for hook, active_cleanup, stock_cont in layout["diagnostics"]:
        for state in (0xA1, 0xA2, 0xA4):
            emu, res, final, sp0 = _guard_route(
                image, hook, state, (active_cleanup, stock_cont))
            assert (
                final == active_cleanup and res.exit_reason == "stop_at"
                and res.regs["sp"] == sp0
            ), (
                version, "diagnostic active", hex(hook), hex(state),
                res, hex(final),
            )

            # Dropping the request on the same emulator immediately restores
            # the byte-identical stock monitor continuation.
            emu.write_byte(_CUT_STATE_ADDR, 0xA0)
            emu.cpu.csp = hook >> 16
            architectural = (emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp))
            res = _run(emu,
                hook & 0xFFFF,
                stop_at=(stock_cont & 0xFFFF,),
                max_steps=100,
            )
            final = (emu.cpu.csp << 16) | res.final_ip
            assert (
                final == stock_cont and res.exit_reason == "stop_at"
                and (
                    emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp)
                ) == architectural
            ), (
                version, "diagnostic restored", hex(hook), hex(state),
                res, hex(final),
            )

    _verify_post_release_dtc_maturation(version, image, layout)


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
    "ignition_cut_v9": "features-ms413",
    "ignition_cut_v9_ms410": "features-ms410",
    "ignition_cut_v9_ms411": "features-ms411",
    "ignition_cut_v9_ms412": "features-ms412",
    "launch_control_v7": "features-ms413",
    "launch_control_v7_ms410": "features-ms410",
    "launch_control_v7_ms411": "features-ms411",
    "launch_control_v7_ms412": "features-ms412",
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
        ("ignition cut V9", verify_ignition_cut_v9),
        ("cut side effects", lambda: verify_cut_side_effect_guards("MS41.2")),
        ("launch V7 state", verify_launch_brain),
        ("launch V7 soft limiter", verify_launch_fuel_soft_cave),
        ("launch V7 hard comparator", verify_launch_fuel_hard_comparator),
        ("stock limiter parity", verify_stock_limiter_parity),
        ("launch + ignition composition", verify_composed_launch_and_ignition),
    ]
    for label, check in checks:
        check()
        print(f"[PASS] MS41.2 {label}")
    _verify_watchdog_liveness(
        STOCK_PATH, "ignition_cut_v9_ms412", "launch_control_v7_ms412")
    print("[PASS] MS41.2 stock/V9/V9+V7/full-stack boot-reset liveness")


def _group_features_413():
    checks = [
        ("AlphaN V3 fallback", verify_alphan_failsafe),
        ("ignition cut V9", lambda: verify_ignition_cut_v9(_case_image_413)),
        ("cut side effects", lambda: verify_cut_side_effect_guards("MS41.3")),
        ("launch V7 state", lambda: verify_launch_brain(_case_image_413)),
        ("launch V7 soft limiter", verify_launch_fuel_soft_cave_ms413),
        ("launch V7 hard comparator",
         lambda: verify_launch_fuel_hard_comparator(_case_image_413)),
        ("stock limiter parity",
         lambda: verify_stock_limiter_parity(
             _case_image_413, STOCK_413_PATH, (0x07D6,))),
        ("launch + ignition composition",
         lambda: verify_composed_launch_and_ignition(_case_image_413)),
    ]
    for label, check in checks:
        check()
        print(f"[PASS] MS41.3 {label}")
    _verify_watchdog_liveness(
        STOCK_413_PATH, "ignition_cut_v9", "launch_control_v7")
    print("[PASS] MS41.3 stock/V9/V9+V7/full-stack boot-reset liveness")


def _full_stack_calibrations(image, variant, values):
    tuned = bytearray(image)
    offsets = {}
    for patch_id in _FULL_STACK_PATCH_IDS[variant][-2:]:
        offsets.update(PATCHES[patch_id]["cave"]["cals"])
    for name, value in values.items():
        offset = offsets[name]
        if name in {"CUT_IPW", "LC_IPW"}:
            tuned[offset:offset + 2] = value.to_bytes(2, "little")
        else:
            tuned[offset] = value & 0xFF
    tuned, _details = checksum.correct_checksums(
        tuned, correct_program=(variant != "SS1v2"))
    status = checksum.checksum_status(tuned)
    assert status["boot"] and status["program"] and status["cal"], status
    return _bind_image(tuned, variant)


def _verify_full_stack_fueling_parity(stock_path, full_image, variant):
    """Compare one real scheduled fueling task with all patch switches off."""
    full_image = _full_stack_calibrations(full_image, variant, {
        "CUTSW": 0xFF,
        "LC_SW": 0xFF,
        "CUT_IPW": 0xFFFF,
        "LC_IPW": 0xFFFF,
    })
    stock_image = _bind_image(stock_path.read_bytes(), variant)
    task, hook, cave, rpm_address, outputs = _FUEL_TASK_LAYOUTS[variant]
    emulators = [
        _load_emulator(
            image, force_variant=variant, silicon_reset=True)
        for image in (stock_image, full_image)
    ]
    for emu in emulators:
        boot = _run(
            emu, emu.cpu.pc, stop_at=(BOOT_EXIT, RECOVER_EXIT),
            max_steps=200000)
        assert boot.final_pc == BOOT_EXIT and boot.exit_reason == "stop_at", (
            variant, "fueling parity boot", boot)
        assert _code_bytes(emu, task, 12) == bytes.fromhex(
            "886088708880889026f00800")

    # Start both exact binaries from one byte-identical post-boot RAM state,
    # then seed the ADC result mirrors and the minimum deterministic engine
    # state consumed by this native task.
    emulators[1].mem.ram[:] = emulators[0].mem.ram
    source, target = emulators[0].reg, emulators[1].reg
    target.dpp[:] = source.dpp
    target.csp = source.csp
    target.sp = source.sp
    target.stkov = source.stkov
    target.stkun = source.stkun
    target.cp = source.cp
    target.mdl = source.mdl
    target.mdh = source.mdh
    target.mdc = source.mdc
    target.psw.unpack(source.psw.pack())
    results = []
    paths = []
    prestates = []
    for emu in emulators:
        for channel in range(10):
            emu.write(
                0xFA94 + channel * 2,
                (channel << 12) | (0x100 + channel * 0x20),
            )
        emu.write_byte(rpm_address, 0x80)
        emu.write_byte(0xE8D0, 0x80)
        emu.write_byte(0xFC9D, 1)
        emu.write(0xF68E, 0x100)
        emu.write(0xE8E4, 0x1000)
        emu.write_byte(0xDC28, 0)
        prestates.append((
            emu.reg.csp,
            emu.reg.sp,
            emu.reg.stkov,
            emu.reg.stkun,
            emu.reg.cp,
            tuple(emu.reg.dpp),
            emu.reg.mdl,
            emu.reg.mdh,
            emu.reg.mdc,
            emu.reg.psw.pack(),
            tuple(emu.reg.r),
        ))
        visited = []
        emu.cpu.set_trace(lambda pc, _opcode: visited.append(pc))
        _call_native(emu, task, max_steps=500000)
        results.append(tuple(emu.read(address) for address in outputs))
        paths.append(set(visited))

    assert prestates[0] == prestates[1], (
        variant, "feature-disabled fueling architectural prestate")
    assert hook in paths[0] and cave not in paths[0], (
        variant, "stock scheduled fueling path")
    assert hook in paths[1] and cave in paths[1], (
        variant, "patched scheduled fueling path")
    assert results[0] == results[1] and all(results[0]), (
        variant, "feature-disabled scheduled fueling parity", results)
    assert emulators[1].read_byte(_CUT_STATE_ADDR) & 0x0F == 0, (
        variant, "feature switches disabled")


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


def _exercise_full_stack_events(emu, image, variant):
    """Bounded same-instance cut/IRQ/ASC0/ADC/PEC soak before warm reset."""
    if variant in {"1429861", "1437806"}:
        version = "MS41.0" if variant == "1429861" else "MS41.1"
        layout = OLDER_FEATURE_LAYOUTS[version]
        control = {
            "hook": layout["control_hook"],
            "rpm_address": layout["rpm_address"],
            "input_bytes": layout["input_bytes"],
            "ipw_addresses": layout["ipw_addresses"],
            "pins": None,
        }
    else:
        control = {"pins": None}

    state = 0xA0
    for rpm, active in ((0xC8, True), (0x72, False), (0xC8, True)):
        state, _ipws, hygiene = _execute_control(
            emu, rpm=rpm, state=state, **control)
        assert bool(state & 1) == active and hygiene, (
            variant, "cut/release/rearm", hex(rpm), hex(state))
        _verify_cc6_interrupt_entry(emu, variant)
        assert _service_foreground_watchdog(emu, variant), (
            variant, "soak foreground watchdog", hex(rpm))

    # A real RX interrupt/RETI plus the native response builder and TX PEC run
    # on this same state. Startup already initialized ASC0; no derived helper
    # entry is called here.
    _verify_asc0_rx_interrupt(emu, variant)
    _boot_stop, dispatcher, tx_arm, _eval, _status, _reason = (
        _DTC100_LAYOUTS[variant])
    assert _native_ds2(
        emu, dispatcher, tx_arm, 0x04, 1
    ) == b"\x12\x06\xA0\x00\x00\xB4"

    # ADC completion is injected, then the normal instruction-boundary event
    # service performs PEC7 into the stock ADC result mirror.
    emu.mem.write_word_direct(0xFECE, 0x0202)
    emu.mem.write_word_direct(0xFDFC, 0xFEA0)
    emu.mem.write_word_direct(0xFDFE, 0xFA94)
    emu.mem.write_byte_direct(0xFF98, 0x7F)
    emu.write(0xE010, 0x00CC)
    emu.write(0xE012, 0x00CC)
    emu.cpu.psw.IEN = True
    emu.cpu.psw.ILVL = 0
    assert emu.inject_adc(4, 0x2AA) == 0x42AA
    event = emu.run_from(0xE010, stop_at=0xE012, max_steps=20)
    assert (
        event.exit_reason == "stop_at"
        and emu.read(0xFA94) == 0x42AA
        and emu.read(0xFECE) == 0x0201
        and not emu.read_byte(0xFF98) & 0x80
    ), (variant, "ADC instruction-boundary PEC", event)


def _verify_full_stack_liveness(image, variant):
    """Cold/warm boot and soak the exact supported full composition."""
    image = _full_stack_calibrations(image, variant, {
        "CUTSW": 0x00,
        "CUTRPM": 0x7D,
        "CUT_HYST": 0x0A,
        "CUT_IPW": 0xFFFF,
        "LC_SW": 0xFF,
    })
    emu = _load_emulator(
        image, force_variant=variant, silicon_reset=True)
    boot_stop = _DTC100_LAYOUTS[variant][0]
    assert _code_bytes(emu, boot_stop, 8) == bytes.fromhex(
        "e6b85400e6b71b00")
    for boot_count in range(2):
        visited = []
        emu.cpu.set_trace(lambda pc, _opcode: visited.append(pc))
        res = _run(emu,
            emu.cpu.pc,
            stop_at=(boot_stop, RECOVER_EXIT),
            max_steps=400000,
        )
        assert (
            res.final_pc == boot_stop
            and res.exit_reason == "stop_at"
            and CAVE_CPU in visited
            and emu.reset_count == boot_count
        ), (variant, "full-stack boot", boot_count, res)
        assert _service_foreground_watchdog(emu, variant), (
            variant, "full-stack foreground watchdog", boot_count)
        if boot_count:
            # Startup owns E847 and must clear the interrupted cut request
            # before the first post-reset ignition interrupt can observe it.
            assert emu.read_byte(_CUT_STATE_ADDR) == 0, (
                variant, "warm-boot E847 clear")
            _verify_cc6_interrupt_entry(emu, variant)
            continue
        _exercise_full_stack_events(emu, image, variant)
        assert emu.read_byte(_CUT_STATE_ADDR) & 1, (
            variant, "reset while cut request active")
        emu.watchdog.value = 0xFFFF
        emu.watchdog.prescaler_select = 0
        emu.advance_oscillator(4)
        assert (
            emu.reset_count == 1
            and emu.last_reset_reason == "watchdog"
            and emu.cpu.pc == 0
            and emu.read(0xFFAE) == 0x0002
            and emu.read_byte(_CUT_STATE_ADDR) & 1
        ), (variant, "full-stack warm reset")


def _verify_watchdog_liveness(stock_path, ignition_id, launch_id):
    """Prove stock and current patch compositions still service the foreground WDT."""
    variant = _REFERENCE_VARIANTS[stock_path.resolve()]
    images = {
        "stock": _bind_image(stock_path.read_bytes(), variant),
        "ignition V9": _build_from(stock_path, [ignition_id]),
        "ignition V9 + launch V7": _build_from(
            stock_path, [ignition_id, launch_id]),
    }
    full_ids = _FULL_STACK_PATCH_IDS[variant]
    full_image = _build_from(stock_path, list(full_ids))
    assert all(
        patch_ms41.is_applied(full_image, PATCHES[patch_id])
        for patch_id in full_ids
    ), (variant, "full-stack patch composition")
    _verify_full_stack_fueling_parity(stock_path, full_image, variant)
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
    verify_cut_side_effect_guards(version)
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
    print(f"[PASS] {version} ignition/launch/side-effects{suffix}")


GROUPS = {
    "cal-guard": _group_cal_guard,
    "loader-doors": _group_loader,
    "intel-flash": verify_intel_flash_mutation,
    "amd-flash": verify_amd_flash_mutation,
    "st9030-proxy": verify_st9030_proxy_agent,
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
