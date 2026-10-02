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
    "cal-guard", "loader-doors", "intel-flash", "amd-flash", "top-ds2",
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
from ms41emu import Emulator  # noqa: E402
from ms41emu.peripherals import (  # noqa: E402
    AmdFlashModel,
    Eeprom24C04,
    FlashModel,
    Timer1,
)
from ms41emu.references import resolve_reference  # noqa: E402
from ms41emu.top_ds2_guard import exercise_top_ds2_guard  # noqa: E402
from tools.opcode_coverage import (  # noqa: E402
    manual_evidence_paths,
    require_trusted,
)


# Current boot-resident V6 entry and native continuation boundaries.
CAVE_CPU = 0x0668
BOOT_EXIT = BOOT_FALLBACK = 0x0942
RECOVER_EXIT = 0x094A


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
        0x271F4, 0x2755A, 0x3E600, 0xFC3C,
        (0xEF7E, 0xEF80, 0xFC20, 0xFC22),
    ),
    "SS1v2": (
        0x271F4, 0x2755A, 0x3E600, 0xFC3C,
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
        "ignition_cut_v10_ms410", "launch_control_v11_ms410",
    ),
    "1437806": (
        "amd_flash", "softbsl_loader", "cal_guard", "door_magic_ms411",
        "ignition_cut_v10_ms411", "launch_control_v11_ms411",
    ),
    "1406464": (
        "amd_flash", "softbsl_loader", "cal_guard", "door_magic",
        "ignition_cut_v10_ms412", "launch_control_v11_ms412",
    ),
    "SS1v2": (
        "amd_flash", "softbsl_loader", "cal_guard", "door_magic",
        "ignition_cut_v11", "launch_control_v11",
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
    "ignition_cut_v10_ms412", "launch_control_v11_ms412",
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
        ["alphan_failsafe", "ignition_cut_v11", "launch_control_v11"],
        marker="B",
    )
    status = checksum.checksum_status(image)
    assert status["boot"] and status["program"] and status["cal"], status
    assert status["prog_disabled"], status
    return _bind_image(image, "SS1v2")


FULL_413_IMAGE = _build_413()


def verify_calguard_compatibility():
    """Execute packaged V6 from silicon reset, including independent recovery."""
    from engines.softbsl.softbsl_host import load_stage_payload

    stage = load_stage_payload()
    for path, variant in _REFERENCE_VARIANTS.items():
        source = path.read_bytes()
        for chip, bank in (("intel", "B"), ("amd", "B"), ("amd", "T")):
            ids = ["softbsl_loader", "cal_guard"]
            if chip == "amd":
                ids.append("amd_flash")
            if bank == "T":
                ids.append("top_ds2_guard")
            image, _ = patch_ms41.build(source, ids, marker=bank)
            target = int.from_bytes(image[0x633A:0x633E], "little")
            normal = []
            for candidate in (source, image):
                emu = _load_emulator(candidate, force_variant=variant, silicon_reset=True)
                Timer1(tick=16).attach(emu.peripherals)
                Eeprom24C04(bytes([255]) * 512).attach(emu.peripherals)
                emu.adc.set_inputs([1023] * 10)
                timing = {}
                def observe(pc, _opcode):
                    if pc == 0x0684 and "open" not in timing:
                        timing["open"] = emu.cpu.state_times
                    if pc == 0x09A4 and "exit" not in timing:
                        timing["exit"] = emu.cpu.state_times
                if candidate is image:
                    emu.cpu.set_trace(observe)
                result = emu.run_from(0, stop_at=(target, RECOVER_EXIT), max_steps=350000)
                assert result.exit_reason == "stop_at" and result.final_pc == target
                assert not emu.reset_count and emu.read_byte(0xE740) == 0
                if candidate is image:
                    assert timing["exit"] - timing["open"] == 147462, timing
                normal.append(emu)
            old, new = normal
            assert old.reg.snapshot() == new.reg.snapshot()
            assert old.reg.psw.pack() == new.reg.psw.pack()
            differences = [0xC000 + i for i, (a, b) in enumerate(
                zip(old.mem.ram[:0x3E00], new.mem.ram[:0x3E00])) if a != b]
            assert all(0xFB64 <= address < new.reg.sp for address in differences), differences

            mismatch = bytearray(image)
            mismatch[0x1400C] ^= 1
            mismatch = bytes(checksum.correct_checksums(mismatch)[0])
            erased = bytearray(b"\xff" * len(image))
            erased[0x4000:0x6000] = image[0x4000:0x6000]
            partial = bytearray(image)
            partial[0x6007:0x600B] = b"BAD!"
            partial[0x633A:0x634E] = b"\xac\xde\x03\x00" * 5
            cases = [("token", image, True, False),
                     ("mismatch", mismatch, False, False),
                     ("erased", bytes(erased), False, False),
                     ("partial", bytes(partial), False, False),
                     ("bad_crc", mismatch, False, True)]
            for label, candidate, token, bad_crc in cases:
                emu = _load_emulator(candidate, force_variant=variant, silicon_reset=True)
                Timer1(tick=16).attach(emu.peripherals)
                emu.adc.set_inputs([1023] * 10)
                emu.write_byte(0xE743, 2)  # Retained recovery mode must be reinitialized.
                sent_token = sent_frame = sent_payload = False
                crc = _crc16(stage) ^ int(bad_crc)
                body = b"\x12\x0a\x5a\x9c\x9c" + len(stage).to_bytes(2, "big") + crc.to_bytes(2, "big")
                frame_check = 0
                for byte in body:
                    frame_check ^= byte
                frame = body + bytes((frame_check,))
                def deliver(pc, _opcode):
                    nonlocal sent_token, sent_frame, sent_payload
                    assert pc < 0x2000, (variant, label, hex(pc))
                    if token and pc == 0x0684 and not sent_token:
                        emu.asc0.rx_inject(b"\x5a\x9c\x9c")
                        sent_token = True
                    if (not sent_frame and pc == 0x160E and emu.read(0xE730) >= 31
                            and not emu.asc0.rx and not emu.read(0xFDDE) & 0x100):
                        emu.asc0.rx_inject(frame)
                        sent_frame = True
                    if pc == 0x0412 and not sent_payload:
                        emu.asc0.rx_inject(stage)
                        sent_payload = True
                emu.cpu.set_trace(deliver)
                # Both flash families service the watchdog for 60000 startup iterations.
                result = emu.run_from(0, stop_at=(0xD800,),
                                      max_steps=340000)
                assert sent_frame and sent_payload and not emu.reset_count
                if bad_crc:
                    assert result.exit_reason == "max_steps" and bytes(emu.asc0.tx) == b"\x06\x15"
                else:
                    assert result.exit_reason == "stop_at" and result.final_pc == 0xD800
                    assert bytes(emu.asc0.tx) == (b"\x06" * (3 if token else 2))
                    assert bytes(emu.read_byte(0xD800 + i) for i in range(len(stage))) == stage
                assert emu.read_byte(0xE740) == 1 and emu.read_byte(0xE743) == 2
            print(f"[PASS] {variant} {chip}/{bank}: V6 normal state/window, token, mismatch, missing program, CRC rejection", flush=True)


OLDER_FEATURE_LAYOUTS = {
    "MS41.0": {
        "stock_path": STOCK_410_PATH,
        "patch_ids": (
            "ignition_cut_v10_ms410",
            "launch_control_v11_ms410",
            "vanos_minrpm_v2_ms410",
        ),
        "ignition_id": "ignition_cut_v10_ms410",
        "launch_id": "launch_control_v11_ms410",
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
        "launch_latch": 0xFD80,
        "launch_hook": 0x0726,
        "launch_continuations": (0x072E, 0x0864),
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
            "ignition_cut_v10_ms411",
            "launch_control_v11_ms411",
            "vanos_minrpm_ms411",
        ),
        "ignition_id": "ignition_cut_v10_ms411",
        "launch_id": "launch_control_v11_ms411",
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
        "launch_latch": 0xFDB6,
        "launch_hook": 0x07EC,
        "launch_continuations": (0x07F4, 0x092A),
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
    for patch_id in ("ignition_cut_v10_ms412", "launch_control_v11_ms412"):
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
    for patch_id in ("ignition_cut_v11", "launch_control_v11"):
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
    """Call one stock function through RETS with its full far-return frame."""
    emu.reg.r[0] = 0xFA16
    emu.reg.sp = 0xFBFC
    emu.reg.dpp[:] = [4, 5, 0, 3]
    emu.mem.write_word_direct(0xFBFC, 0xE000)
    emu.mem.write_word_direct(0xFBFE, 0)
    architectural = (emu.reg.cp, tuple(emu.reg.dpp))
    # Only 00:E000 is the return sentinel; launch code can execute 03:E000.
    start = entry if entry > 0xFFFF else (emu.cpu.csp << 16) | entry
    res = emu.run_from(start, stop_at=(0xE000,), max_steps=max_steps)
    assert (
        res.final_pc == 0xE000
        and res.exit_reason == "stop_at"
        and emu.reg.sp == 0xFC00
        and emu.reg.r[0] == 0xFA16
        and (emu.reg.cp, tuple(emu.reg.dpp)) == architectural
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
        for supplied, want_rl4 in (
            (expected, 0), (expected ^ 1, 1),
        ):
            emu = _emu(image)
            for index, value in enumerate(payload):
                emu.write_byte(0xD800 + index, value)
            emu.reg.r[5] = 0xD800 + len(payload)
            emu.write_byte(0xE427, supplied >> 8)
            emu.write_byte(0xE428, supplied & 0xFF)
            emu.write(0xD700, 0x00DA)  # CALLS CRC, then require its actual RETS.
            emu.write(0xD702, 0x1C32)
            res = _run(emu, 0xD700, stop_at=(0xD704,), max_steps=10000)
            assert res.exit_reason == "stop_at" and res.final_ip == 0xD704 and (emu.reg.r[4] & 0xFF) == want_rl4, (
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
            "agent.hex", 1416,
            "59543efcda316e670e3290444e9acd8bf99e48dbc85946aa17129ddcb89f72b9",
        ),
        "intel_28f200": (
            "agent_28f.hex", 1382,
            "8096c69eac3d26ccb1daa1d81e3fab7be6b073c2cb6f1b1f7491abee19f4a668",
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
        max_steps=480000,
    )
    emu.cpu.set_trace(None)
    assert (
        recovery.final_pc == RECOVER_EXIT
        and recovery.exit_reason == "stop_at"
        and CAVE_CPU in visited
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
        max_steps=480000,
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
    "44718b7af778dc4fc8432f329417b06abc3860621dc1f99d78c69825c25fd130"
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
    """Bind the captured image, then execute its current loader/guard upgrade."""
    historical = dict(PATCHES)
    historical["door_magic"] = dict(
        historical["door_magic"], requires=["softbsl_loader_v11"])
    captured, _log = patch_ms41.build(
        STOCK_413_PATH.read_bytes(),
        ("amd_flash_v3", "softbsl_loader_v11", "door_magic", "cal_guard_v5",
         "alphan_failsafe"),
        patches=historical, marker="B", allow_deprecated=True)
    assert hashlib.sha256(captured).hexdigest() == _ST9030_TARGET_SHA256
    ids = (
        "amd_flash", "softbsl_loader", "door_magic", "cal_guard",
        "alphan_failsafe",
    )
    image, _log = patch_ms41.build(captured, ids, marker="B")
    fresh, _log = patch_ms41.build(STOCK_413_PATH.read_bytes(), ids, marker="B")
    assert image == fresh
    image = bytes(image)
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
    """Run V10's late standalone-request/fixed-IPW hook once."""
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
    """Seed V10's shared requests; P1L starts high/off like native startup."""
    emu.write_byte(0xFC3C, rpm)
    emu.write_byte(_CUT_STATE_ADDR, state)
    emu.write_byte(0xFF04, 0xFF)


def _run_ignition(
        image, *, rpm, state=0xA0, hook=IGNITION_HOOKS[0], mask=0xFE,
        emu=None):
    """Execute one real CC6-ISR hook through both complete V10 return paths."""
    emu = _emu(image, dpp0=4) if emu is None else emu
    _seed_ignition_gate(emu, rpm=rpm, state=state)
    emu.reg.r[1] = 0x1200 | mask      # selected native clear mask in RL1
    emu.reg.r[4] = 0xA55A             # V10 must preserve the complete r4
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
    paired cylinder set plus a companion bit. V10 intentionally suppresses the
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


def verify_ignition_cut_v10(case_image=_case_image):
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


def verify_launch_always_gates(case_image=_case_image, layout=None):
    """Always supplies an active input, but obeys the normal launch envelope."""
    for cut_type in (0, 1):
        image = case_image({
            "LC_SW": 0, "LC_CUTTYPE": cut_type, "LC_CLUTCHPOL": 1,
            "LC_MAXRPM": 0x7D, "LC_HYST": 0, **A_THRESHOLDS,
        })
        emu = _emu(image, dpp0=4)
        latch_address = _launch_latch(image)
        emu.write_byte(latch_address, 0)
        emu.write_byte(_CUT_STATE_ADDR, 0xA1)
        for name, speed, tps, want in (
            ("low TPS cannot arm", 0, 0x7F, False),
            ("TPS equality arms", 0, 0x80, True),
            ("rollout retains latch", 0x1D, 0xC0, True),
            ("VMAX equality releases", 0x1E, 0xC0, False),
            ("remaining at VMAX cannot rearm", 0x1E, 0xC0, False),
            ("return below VMAX cannot rearm", 0x1D, 0xC0, False),
            ("arm-speed equality cannot rearm", 5, 0xC0, False),
            ("below arm speed rearms", 4, 0xC0, True),
            ("TPS drop releases", 4, 0x7F, False),
            ("TPS recovery during rollout cannot rearm", 5, 0x80, False),
            ("TPS recovery below arm speed rearms", 4, 0x80, True),
        ):
            if layout is None:
                # Execute the stock speed producer, installed launch hook and
                # complete RETS. No direct write supplies the launch VSS byte.
                emu.write(0xF198, (speed * emu.read(0x0166) + 3599) // 3600)
                emu.write_byte(0xE8D0, tps)
                emu.write_byte(0xFC3C, 0xC8)
                emu.write(0xFD60, 0x0380)  # inactive for selected low polarity
                emu.write_byte(0xFD13, 0x80)
                _call_native(emu, 0x3989A)
                assert emu.read_byte(0xF19A) == emu.read_byte(0xDA63) == speed
            else:
                emu, result, _sp = _run_older_launch(
                    layout, image, speed=speed, tps=tps, fd60=0x80, fd61=3,
                    rpm=0xC8, limiter_active=True, emu=emu,
                    latch=bool(emu.read_byte(latch_address) & 0x40),
                    state=emu.read_byte(_CUT_STATE_ADDR))
                assert result.exit_reason == "stop_at"
                assert emu.read_byte(layout["soft_limit_address"]) == (
                    0x7D if want and cut_type == 0 else 0xCB)
            state = emu.read_byte(_CUT_STATE_ADDR)
            request = (0x04 if cut_type == 0 else 0x02) if want else 0
            # Older hooks also execute V11's post-cap selector. The first
            # unarmed call transfers this deliberately active native episode
            # to stock A/B, and every later call must retain that priority.
            # Modern VSS calls execute only the launch requester here.
            priority = 0x08 if layout is not None else 0
            assert emu.read(0xFD12) & 0x8000
            assert state & 0x08 == priority, (
                _bound_variant(image), cut_type, name, hex(state))
            assert (
                bool(emu.read_byte(latch_address) & 0x40) == want
                and state == 0xA1 | priority | request
            ), (_bound_variant(image), cut_type, name, hex(state))


def _verify_launch_soft_dispatch(case_image):
    cases = (
        ("unarmed", 0, 0, 125, 120, 203),
        ("fuel inherit", 1, 0, 125, 255, 125),
        ("fuel lower hard keeps soft", 1, 0, 125, 120, 125),
        ("fuel equal hard", 1, 0, 125, 125, 125),
        ("fuel higher hard", 1, 0, 125, 128, 125),
        ("native soft cap", 1, 0, 208, 255, 203),
        ("ignition mode", 1, 1, 125, 120, 203),
    )
    for name, armed, mode, soft, hard, expected in cases:
        image = case_image({"LC_CUTTYPE": mode, "LC_MAXRPM": soft, "LC_HARDRPM": hard})
        emu = _emu(image)
        architectural = (emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp))
        emu.write_byte(_CUT_STATE_ADDR, 0xA0)
        emu.write_byte(_launch_latch(image), 0x40 if armed else 0)
        emu.write_byte(0xF014, 203)
        emu.write_byte(0xDB87, 206)
        emu.cpu.csp = 2
        result = _run(emu, 0x07E8, stop_at=(0x07F0, 0x0926), max_steps=400)
        assert result.exit_reason == "stop_at" and result.final_ip == 0x0926
        assert (emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp)) == architectural
        assert emu.read_byte(0xF014) == expected, (name, result)
        assert _service_foreground_watchdog(emu, _bound_variant(image))


def verify_launch_fuel_soft_cave():
    _verify_launch_soft_dispatch(_case_image)


def verify_launch_fuel_soft_cave_ms413():
    _verify_launch_soft_dispatch(_case_image_413)


_LAUNCH_HARD_CASES = (
    # name, latch, mode, soft, hard, RPM, below/at-or-above effective hard
    ("configured below hard", 1, 0, 0x7D, 0x90, 0x8F, 0),
    ("configured at hard", 1, 0, 0x7D, 0x90, 0x90, 1),
    ("configured above hard", 1, 0, 0x7D, 0x90, 0x91, 1),
    ("below-soft below hard", 1, 0, 0x7D, 0x70, 0x6F, 0),
    ("below-soft at hard", 1, 0, 0x7D, 0x70, 0x70, 1),
    ("below-soft above hard", 1, 0, 0x7D, 0x70, 0x7C, 1),
    ("FF fallback below", 1, 0, 0x7D, 0xFF, 0x7F, 0),
    ("FF fallback at soft+3", 1, 0, 0x7D, 0xFF, 0x80, 1),
    ("explicit high below native", 1, 0, 0xD8, 0xE0, 0xCD, 0),
    ("explicit high at native", 1, 0, 0xD8, 0xE0, 0xCE, 1),
    ("explicit high above native", 1, 0, 0xD8, 0xE0, 0xD0, 1),
    ("auto high below native", 1, 0, 0xD8, 0xFF, 0xCD, 0),
    ("auto high at native", 1, 0, 0xD8, 0xFF, 0xCE, 1),
    ("FF saturation below native", 1, 0, 0xFE, 0xFF, 0xCD, 0),
    ("FF saturation at native", 1, 0, 0xFE, 0xFF, 0xCE, 1),
    ("FF saturation above native", 1, 0, 0xFE, 0xFF, 0xFE, 1),
    ("not armed uses stock below", 0, 0, 0x7D, 0x90, 0xCD, 0),
    ("not armed uses stock at", 0, 0, 0x7D, 0x90, 0xCE, 1),
    ("ignition mode uses stock below", 1, 1, 0x7D, 0x90, 0xCD, 0),
    ("ignition mode uses stock at", 1, 1, 0x7D, 0x90, 0xCE, 1),
)


def verify_launch_fuel_hard_comparator(case_image=_case_image):
    """Exercise both real DB87 CALL sites and their untouched stock branches.

    Launch V11 preserves the native hard ceiling for explicit LC_HARDRPM
    or its saturated soft+3 fallback. Persistent DB86/DB87 remain unchanged.
    """
    sites = (
        # native MOVB start; branch outcome below threshold / at-or-above
        (0x0886, (0x0890, 0x08E0)),
        (0x0926, (0x0942, 0x0930)),
    )
    # DB86 deliberately differs from DB87 to reject the obsolete stock-gap rule.
    for name, latch, cut_type, soft, hard, rpm, result_index in _LAUNCH_HARD_CASES:
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


def verify_launch_native_hard_ceiling(case_image=_case_image, layout=None):
    """Keep native hard escalation through the complete limiter and RETS."""
    for name, armed, mode, soft, hard, rpm, hard_taken in (
        ("lower launch below", True, 0, 0x7D, 0x90, 0x8F, False),
        ("lower launch at", True, 0, 0x7D, 0x90, 0x90, True),
        ("explicit high below native", True, 0, 0xD8, 0xE0, 0xCD, False),
        ("explicit high at native", True, 0, 0xD8, 0xE0, 0xCE, True),
        ("auto high at native", True, 0, 0xD8, 0xFF, 0xCE, True),
        ("auto saturated at native", True, 0, 0xFE, 0xFF, 0xCE, True),
        ("disarmed retains native", False, 0, 0xD8, 0xE0, 0xCE, True),
        ("ignition retains native", True, 1, 0x7D, 0x90, 0xCE, True),
    ):
        image = case_image({
            "LC_SW": 0 if armed else 0xFF, "LC_CUTTYPE": mode,
            "LC_MAXRPM": soft, "LC_HARDRPM": hard, **A_THRESHOLDS,
        })
        variant = _bound_variant(image)
        emu = _emu(image, dpp0=4)
        soft_address = layout["soft_limit_address"] if layout else 0xF014
        rpm_address = layout["rpm_address"] if layout else 0xFC3C
        hard_address = layout["stock_hard_address"] if layout else 0xDB87
        entry = {"1429861": 0x206BE, "1437806": 0x20784}.get(variant, 0x20780)
        outcomes = layout["hard_sites"][0][1] if layout else (0x0890, 0x08E0)
        assert _code_bytes(emu, entry, 8) == bytes.fromhex("8a2202909a2214a0")
        emu.write_byte(_launch_latch(image), 0x40 if armed else 0)
        emu.write_byte(_CUT_STATE_ADDR, 0xA0)
        emu.write_byte(rpm_address, rpm)
        emu.write_byte(layout["speed_address"] if layout else 0xF19A, 0)
        emu.write_byte(0xE8D0, 0xC0)
        emu.write(0xFD06, 0x20)
        emu.write(0xFD12, 0x8000)
        emu.write_byte(soft_address - 1, 1)  # existing native limiter stage
        emu.write_byte(soft_address + 1, 5)  # stage countdown
        emu.write_byte(soft_address + 2, 5)  # countdown reload
        if layout is None:
            emu.write_byte(0xDB86, 0xCB)
            emu.write_byte(0xDB87, 0xCE)
        assert emu.read_byte(hard_address) == 0xCE
        visited = []
        emu.cpu.set_trace(lambda pc, _opcode: visited.append(pc))
        _call_native(emu, entry)
        emu.cpu.set_trace(None)
        assert 0x20000 + outcomes[int(hard_taken)] in visited, (variant, name)
        assert 0x20000 + outcomes[int(not hard_taken)] not in visited, (variant, name)
        stage = emu.read_byte(0xFADE if variant == "1429861" else 0xFC34)
        assert stage == (2 if hard_taken else 1), (variant, name, stage)
        assert emu.read_byte(soft_address) == (
            min(0xCB, soft) if armed and mode == 0 else 0xCB
        ), (variant, name, "native soft threshold changed")
        assert emu.read_byte(hard_address) == 0xCE


_NATIVE_FUEL_LAYOUTS = {
    "1429861": (STOCK_410_PATH, 0x206BE, 0x2F210, 0xEDF2, 0xEDF4,
                0xFAE6, 0xED52, 0x1CA, 0x1D3, 0x1D2, 0x1D1,
                0x1CC, 0x1CE, 0x20732, 0x20754),
    "1437806": (STOCK_411_PATH, 0x20784, 0x3B3B2, 0xF1BC, 0xF1BE,
                0xFC3C, 0xF02C, 0x2D2, 0x2DB, 0x2DA, 0x2D9,
                0x2D4, 0x2D6, 0x207F8, 0x2081A),
    "1406464": (STOCK_PATH, 0x20780, 0x3989A, 0xF198, 0xF19A,
                0xFC3C, 0xF014, 0xDB86, 0xDB87, 0x2BA, 0x2B9,
                0x2B4, 0x2B6, 0x207F4, 0x20816),
    "SS1v2": (STOCK_413_PATH, 0x20780, 0x3989A, 0xF198, 0xF19A,
              0xFC3C, 0xF014, 0xDB86, 0xDB87, 0x2BA, 0x2B9,
              0x2B4, 0x2B6, 0x207F4, 0x20816),
}


def _native_fuel_fixture(case_image, values, *, native_soft=0xCB,
                         native_hard=0xCE, stock_a=1, stock_b=0,
                         original=False, fault_soft=None, fault_cap=None):
    """Use original machine code as the oracle, with explicit input calibrations."""
    patched = case_image(values)
    variant = _bound_variant(patched)
    (path, entry, speed_entry, delta, speed, rpm, soft, stock_soft,
     stock_hard, cal_a, cal_b, fault_cal, cap_cal, load_a, load_b) = _NATIVE_FUEL_LAYOUTS[variant]
    image = bytearray(path.read_bytes() if original else patched)
    cals = {cal_a: stock_a, cal_b: stock_b}
    if stock_soft < 0xC000:
        cals[stock_soft] = native_soft
        cals[stock_hard] = native_hard
    if fault_soft is not None:
        cals[fault_cal] = fault_soft
    if fault_cap is not None:
        cals[cap_cal] = fault_cap
    for address, value in cals.items():
        image[(0x10000 + address) ^ 0x4000] = value
    image, _ = checksum.correct_checksums(image)
    image = _bind_image(image, variant)
    emu = _emu(image, dpp0=4)
    emu.write_byte(_CUT_STATE_ADDR, 0xA0)
    emu.write_byte(0xE8D0, 0xC0)
    emu.write(delta, 0)
    emu.write(0xFD06, 0x20)
    _call_native(emu, speed_entry)
    assert emu.read_byte(speed) == 0
    if stock_soft >= 0xC000:
        emu.write_byte(stock_soft, native_soft)
        emu.write_byte(stock_hard, native_hard)
    emu.write_byte(_launch_latch(image), 0 if original else 0x40)
    emu.write_byte(_CUT_STATE_ADDR, 0xA0)
    return emu, (variant, entry, rpm, soft, load_a + 4, load_b + 4)


def _native_fuel_step(emu, context, rpm):
    variant, entry, rpm_address, soft, a_return, b_return = context
    emu.write_byte(rpm_address, rpm)
    selected = {}
    def trace(pc, _opcode):
        if pc == a_return:
            selected["A"] = emu.reg.r[13]
        elif pc == b_return:
            selected["B"] = emu.reg.r[13]
    emu.cpu.set_trace(trace)
    _call_native(emu, entry)
    emu.cpu.set_trace(None)
    stage = emu.read_byte(0xFADE if variant == "1429861" else 0xFC34)
    assert stage == emu.read_byte(soft - 1), (variant, stage)
    state = (stage, bool(emu.read(0xFD12) & 0x8000),
             emu.read(0xFD44), emu.read_byte(soft),
             emu.read_byte(soft + 1), emu.read_byte(soft + 2))
    return state, selected


def verify_launch_native_fuel_recovery(case_image=_case_image, layout=None):
    """Compare V11 against ORIGINAL native code, including crossed S/H.

    Each controller receives matched effective S/H and recovery values. Stock
    thresholds in the patched image remain higher unless a priority tie/lower
    bound is the case under test. These are input fixtures, not a producer test.
    """
    cases = (
        ("inherit lower hard", 125, 120, 255, 255, 203),
        ("inherit narrow lower hard", 125, 124, 255, 255, 203),
        ("inherit equal hard", 125, 125, 255, 255, 203),
        ("inherit higher hard", 125, 128, 255, 255, 203),
        ("custom both", 125, 128, 6, 2, 203),
        ("inherit B", 125, 128, 3, 255, 203),
        ("inherit A", 125, 128, 255, 2, 203),
        ("custom zero", 125, 128, 0, 0, 203),
        ("subtract underflow", 10, 12, 20, 20, 203),
        ("stock soft tie", 125, 128, 6, 2, 125),
        ("stock soft lower", 125, 128, 6, 2, 120),
    )
    for name, soft, hard, custom_a, custom_b, native_soft in cases:
        values = {"LC_SW": 0, "LC_CUTTYPE": 0, "LC_MAXRPM": soft,
                  "LC_HARDRPM": hard, "LC_FUEL_HYST_A": custom_a,
                  "LC_FUEL_HYST_B": custom_b, **A_THRESHOLDS}
        effective_soft = min(soft, native_soft)
        priority = native_soft <= soft
        selected_a = 1 if priority or custom_a == 255 else custom_a
        selected_b = 0 if priority or custom_b == 255 else custom_b
        emu, context = _native_fuel_fixture(case_image, values, native_soft=native_soft)
        control, control_context = _native_fuel_fixture(
            case_image, values, native_soft=effective_soft, native_hard=hard,
            stock_a=selected_a, stock_b=selected_b, original=True)
        sequence = list(range(max(0, min(effective_soft, hard) - 1), max(effective_soft, hard) + 2))
        sequence = [rpm for rpm in sequence for _ in range(2)]
        sequence += [hard] * 8
        sequence += [rpm for rpm in range(max(effective_soft, hard) + 1,
                     max(-1, min(effective_soft, hard) - max(selected_a, selected_b) - 3), -1)
                     for _ in range(2)]
        for rpm in sequence:
            actual, selected = _native_fuel_step(emu, context, rpm)
            expected, native_selected = _native_fuel_step(control, control_context, rpm)
            assert actual == expected, (context[0], name, rpm, actual, expected)
            assert selected == native_selected, (context[0], name, rpm, selected, native_selected)
            assert bool(emu.read_byte(_CUT_STATE_ADDR) & 8) == priority, (context[0], name, rpm)


def verify_launch_hysteresis_priority(case_image=_case_image, layout=None):
    """One shared cut history; priority stays stock through an active episode."""
    values = {"LC_SW": 0, "LC_CUTTYPE": 0, "LC_MAXRPM": 125,
              "LC_HARDRPM": 128, "LC_FUEL_HYST_A": 6,
              "LC_FUEL_HYST_B": 2, **A_THRESHOLDS}
    # A crossed native hard can be reached before native soft. Falling below
    # it must not silently switch A/B back during the shared active episode.
    emu, ctx = _native_fuel_fixture(case_image, values, native_hard=130)
    for rpm, priority in ((125, False), (128, False), (130, True), (129, True), (125, True)):
        state, selected = _native_fuel_step(emu, ctx, rpm)
        assert bool(emu.read_byte(_CUT_STATE_ADDR) & 8) == priority, (ctx[0], rpm)
        assert selected == ({"A": 1, "B": 0} if priority else
                            ({"A": 6, "B": 2} if selected else {})), (ctx[0], rpm, selected)
    # TPS release transfers the still-active shared state to stock. Re-arm
    # before its next native evaluation cannot discard stock priority.
    emu, ctx = _native_fuel_fixture(
        case_image, values, native_soft=130, native_hard=140, stock_a=3)
    for _ in range(7):
        _native_fuel_step(emu, ctx, 128)
    assert not emu.read_byte(_CUT_STATE_ADDR) & 8
    emu.write_byte(0xE8D0, 0)
    if layout is None:
        emu.write_byte(0xFDB6, 0)
    state, selected = _native_fuel_step(emu, ctx, 128)
    assert state[1] and emu.read_byte(_CUT_STATE_ADDR) & 8, (ctx[0], state)
    emu.write_byte(0xE8D0, 0xC0)
    emu.write_byte(0xFD80 if layout and ctx[0] == "1429861" else 0xFDB6, 0x40)
    state, selected = _native_fuel_step(emu, ctx, 125)
    assert selected == {"A": 3, "B": 0} and state[1], (ctx[0], selected, state)
    _native_fuel_step(emu, ctx, 0)  # native recovery clears the shared limiter
    state, selected = _native_fuel_step(emu, ctx, 0)
    assert not state[1] and not emu.read_byte(_CUT_STATE_ADDR) & 8, (ctx[0], state)
    _native_fuel_step(emu, ctx, 125)
    state, selected = _native_fuel_step(emu, ctx, 125)
    assert selected == {"A": 6, "B": 2} and not emu.read_byte(_CUT_STATE_ADDR) & 8

    # The native fault soft producer, and each stock speed-fault cap, run
    # before the selector. Both must take priority over custom launch offsets.
    for fault, cap in ((True, False), (False, True)):
        emu, ctx = _native_fuel_fixture(case_image, values, fault_soft=110, fault_cap=112)
        if cap and ctx[0] == "SS1v2":
            continue  # SS1v2 replaced this stock speed-fault cap with SIR logic.
        emu.write(0xFD30, 3 if fault else 0x10)
        state, _ = _native_fuel_step(emu, ctx, 125)
        assert state[3] == (110 if fault else 112), (ctx[0], fault, state)
        assert emu.read_byte(_CUT_STATE_ADDR) & 8
        _, selected = _native_fuel_step(emu, ctx, 125)
        assert selected == {"A": 1, "B": 0}, (ctx[0], fault, selected)


def verify_launch_hysteresis_abi(case_image=_case_image, layout=None):
    """Match displaced native MOVBZ, including PSW and all unaffected GPRs."""
    for marker, armed, mode, a, b in (
        (0xA0, True, 0, 6, 2), (0xA0, True, 0, 0, 0),
        (0xA0, True, 0, 254, 254), (0xA0, True, 0, 255, 255),
        (0xA8, True, 0, 6, 2), (0, True, 0, 6, 2),
        (0xA0, False, 0, 6, 2), (0xA0, True, 1, 6, 2),
    ):
        custom = marker == 0xA0 and armed and mode == 0
        wanted_a = a if custom and a != 255 else 1
        wanted_b = b if custom and b != 255 else 0
        values = {"LC_SW": 0, "LC_CUTTYPE": mode, "LC_MAXRPM": 125,
                  "LC_HARDRPM": 128, "LC_FUEL_HYST_A": a,
                  "LC_FUEL_HYST_B": b, **A_THRESHOLDS}
        for index in (4, 5):
            for flags in (False, True):
                actual, context = _native_fuel_fixture(case_image, values)
                native, native_context = _native_fuel_fixture(
                    case_image, values, original=True,
                    stock_a=wanted_a, stock_b=wanted_b)
                results = []
                for emu, ctx in ((actual, context), (native, native_context)):
                    emu.write_byte(_CUT_STATE_ADDR, marker)
                    emu.write_byte(0xFD80 if ctx[0] == "1429861" else 0xFDB6,
                                   0x40 if armed else 0)
                    for reg in range(1, 16):
                        emu.reg.r[reg] = 0x1100 + reg
                    for flag in ("C", "V", "N", "Z", "E"):
                        setattr(emu.reg.psw, flag, flags)
                    emu.cpu.csp = 2
                    result = _run(emu, ctx[index] - 4,
                                  stop_at=(ctx[index],), max_steps=300)
                    assert result.exit_reason == "stop_at" and result.final_pc == ctx[index]
                    results.append((tuple(emu.reg.r), emu.reg.psw.pack(),
                                    emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp)))
                assert results[0] == results[1], (context[0], marker, armed, mode, a, b, index, flags, results)

    image = case_image({"CUTSW": 255, "LC_SW": 255})
    for hook in (layout["ignition_hooks"] if layout else IGNITION_HOOKS):
        if layout:
            cut, stock, hygiene = _run_older_ignition(
                layout, image, rpm=200, hook=hook, state_in=0xA8)
        else:
            cut, stock, hygiene = _run_ignition(
                image, rpm=200, hook=hook, state=0xA8)
        assert not cut and stock and hygiene, ("priority-only ignition", hook)

    # Invoke the selector directly: older Cave A intentionally initializes an
    # invalid marker before normal dispatch, so it is not this negative fixture.
    emu, context = _native_fuel_fixture(case_image, values | {"LC_CUTTYPE": 0})
    selector, stops = {
        "1429861": (0x328A0, (0x2072E, 0x20864)),
        "1437806": (0x3FB40, (0x207F4, 0x2092A)),
    }.get(context[0], (0x3DD00, (0x207F0, 0x20926)))
    emu.write_byte(_CUT_STATE_ADDR, 0x17)
    emu.write_byte(context[3], 203)
    architecture = (emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp))
    result = _run(emu, selector, stop_at=stops, max_steps=300)
    assert result.exit_reason == "stop_at" and result.final_pc == stops[1]
    assert emu.read_byte(context[3]) == 203 and emu.read_byte(_CUT_STATE_ADDR) == 0x17
    assert (emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp)) == architecture


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
        soft_stops=(0x07F0, 0x0926)):
    image = case_image({
        "LC_SW": 0xFF, "LC_CUTTYPE": 0, "LC_MAXRPM": 0x7D,
        **A_THRESHOLDS,
    })
    _verify_stock_limiter_parity(
        stock_path,
        image,
        soft_entry=0x07E8,
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
    """Exercise the model's forced WDT reset and native DTC100 read/clear."""
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
    has_wait = patch_ms41.is_applied(image, patch_ms41.startup_wait_definition(PATCHES))
    reset = _run(emu, 0x0434, stop_at=(0x04B0,),
                 max_steps=180100 if has_wait else 100)
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
    # This is the current reset/bus model's regression value, not a physical
    # watchdog signature. CPU 043E reads A330 before DPP2 initialization: reset
    # DPP2=2 sees FFFF, whereas post-prologue DPP2=0 sees 2000. Startup filters
    # that accumulated value to 0003 without taking a RAM-failure branch.
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
        emu=None, state_in=None):
    hook = layout["ignition_hooks"][0] if hook is None else hook
    emu = _emu(image, dpp0=4) if emu is None else emu
    _seed_older_feature_inputs(
        emu, layout, pins & 0xFF, (pins >> 8) & 0xFF)
    state, _ipws, control_hygiene = _execute_control(
        emu, rpm=rpm, pins=None,
        state=(0xA2 if request else 0xA0) if state_in is None else state_in,
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
        limiter_active=False, emu=None):
    emu = _emu(image) if emu is None else emu
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
    verify_launch_always_gates(
        lambda values: _case_image_older(layout, values), layout)
    verify_launch_native_hard_ceiling(
        lambda values: _case_image_older(layout, values), layout)
    verify_launch_native_fuel_recovery(
        lambda values: _case_image_older(layout, values), layout)
    verify_launch_hysteresis_priority(
        lambda values: _case_image_older(layout, values), layout)
    verify_launch_hysteresis_abi(
        lambda values: _case_image_older(layout, values), layout)
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
        ("off", 0xFF, 0, 0x7D, 0x78, 0xCB, 0xCB),
        ("fuel clamp", 0x00, 0, 0x7D, 0xFF, 0xCB, 0x7D),
        ("fuel lower hard keeps soft", 0x00, 0, 0x7D, 0x78, 0xCB, 0x7D),
        ("fuel equal hard", 0x00, 0, 0x7D, 0x7D, 0xCB, 0x7D),
        ("fuel higher hard", 0x00, 0, 0x7D, 0x80, 0xCB, 0x7D),
        ("fuel keep", 0x00, 0, 0xD0, 0xFF, 0xCB, 0xCB),
        ("ignition mode", 0x00, 1, 0x7D, 0x78, 0xCB, 0xCB),
    ]
    for name, switch, cut_type, limit, hard, stock_limit, wanted in soft_cases:
        image = _case_image_older(layout, {
            "LC_SW": switch, "LC_CUTTYPE": cut_type,
            "LC_CLUTCHPOL": 0, "LC_MAXRPM": limit, "LC_HARDRPM": hard,
            **A_THRESHOLDS,
        })
        emu, res, sp0 = _run_older_launch(
            layout, image, speed=0, tps=0xC0, soft=stock_limit)
        assert (
            emu.read_byte(layout["soft_limit_address"]) == wanted
            and res.exit_reason == "stop_at"
            and res.regs["sp"] == sp0 and res.regs["dpp"][0] == 5
        ), (name, res, emu.read_byte(layout["soft_limit_address"]))

    for name, latch, cut_type, soft, hard, rpm, result_index in _LAUNCH_HARD_CASES:
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

    # Ignition-mode launch request composes with this firmware's V10 hook.
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
        "stft": (0x28664, 0x32D78, 0x28668, (0xED56, 0xED90)),
        "ltft": (0x28C40, (0xED56, 0xED90), 0x18, 0x2222),
        "additive": (0x28C6E, (0xED56, 0xED90), 0x10, 0x3333),
        "diagnostics": (
            # Shared roughness/misfire detector plus coil/resistor diagnostics.
            (0x2CC98, 0x2CF58, 0x2CC9C),
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
        "stft": (0x2CA5A, 0x3FC60, 0x2CA5E, (0xF030, 0xF0EC)),
        "ltft": (0x2D382, (0xF030, 0xF0EC), 0x18, 0x2222),
        "additive": (0x2D3B0, (0xF030, 0xF0EC), 0x10, 0x3333),
        "diagnostics": (
            (0x36FB6, 0x372A6, 0x36FBA),
            (0x2B42C, 0x2B48C, 0x2B430),
            (0x2B4EE, 0x2B54E, 0x2B4F2),
        ),
        "dtc_descriptors": (
            *COIL_DTC_DESCRIPTORS_LATE,
            *MISFIRE_DTC_DESCRIPTORS_LATE,
        ),
        "dtc_manager": (0x27CE6, 0x27EAA),
        "dtc_maturation": (
            (0, 0xEB22, 0xA72C),
        ),
    },
    "MS41.2": {
        "image": lambda: FULL_IMAGE,
        "stft": (0x2BF10, 0x3E220, 0x2BF14, (0xF018, 0xF0C4)),
        "ltft": (0x2C86E, (0xF018, 0xF0C4), 0x18, 0x2222),
        "additive": (0x2C89C, (0xF018, 0xF0C4), 0x10, 0x3333),
        "diagnostics": (
            (0x3553E, 0x3582E, 0x35542),
            (0x2B05C, 0x2B0BC, 0x2B060),
            (0x2B11E, 0x2B17E, 0x2B122),
        ),
        "dtc_descriptors": (
            *COIL_DTC_DESCRIPTORS_LATE,
            *MISFIRE_DTC_DESCRIPTORS_LATE,
        ),
        "dtc_manager": (0x27956, 0x27B1A),
        "dtc_maturation": (
            (0, 0xEB22, 0xA72C),
        ),
    },
    "MS41.3": {
        "image": lambda: FULL_413_IMAGE,
        "stft": (0x2BF10, 0x3E220, 0x2BF14, (0xF018, 0xF0C4)),
        "ltft": (0x2C86E, (0xF018, 0xF0C4), 0x18, 0x2222),
        "additive": (0x2C89C, (0xF018, 0xF0C4), 0x10, 0x3333),
        "diagnostics": (
            (0x3553E, 0x3582E, 0x35542),
            (0x2B05C, 0x2B0BC, 0x2B060),
            (0x2B11E, 0x2B17E, 0x2B122),
        ),
        "dtc_descriptors": (
            *COIL_DTC_DESCRIPTORS_LATE,
            *MISFIRE_DTC_DESCRIPTORS_LATE,
        ),
        "dtc_manager": (0x27956, 0x27B1A),
        "dtc_maturation": (
            (0, 0xEB22, 0xA72C),
        ),
    },
}


_CUT_LEARNING_LAYOUTS = {
    # Native adaptation, qualification inputs, RPM mirror, STFT neutralizer.
    "MS41.0": (0x28952, 0xFAFE, 0xF0F0, 0xF0EC, 0xFAE4, 0x28486),
    "MS41.1": (0x2D068, 0xFC54, 0xF68A, 0xF686, 0xFC3A, 0x2C87C),
    "MS41.2": (0x2C53C, 0xFC54, 0xF694, 0xF690, 0xFC3A, 0x2BD32),
    "MS41.3": (0x2C53C, 0xFC54, 0xF694, 0xF690, 0xFC3A, 0x2BD32),
}
_CUT_TRIM_FIELDS = (0x06, 0x08, 0x10, 0x18, 0x1E, 0x20)


def _cut_reference(version):
    variant = _bound_variant(CUT_GUARD_LAYOUTS[version]["image"]())
    path = next(path for path, name in _REFERENCE_VARIANTS.items()
                if name == variant)
    return _bind_image(path.read_bytes(), variant)


def _legacy_cut_image(version):
    """Recreate exact, retired V9 only as a deliberately defective control."""
    suffix = "" if version == "MS41.3" else "_ms41" + version[-1]
    patch_id = "ignition_cut_v9" + suffix
    stock = _cut_reference(version)
    ids = [*PATCHES[patch_id].get("requires", ()), patch_id]
    image, _log = patch_ms41.build(stock, ids, allow_deprecated=True)
    return _bind_image(image, _bound_variant(stock))


def _native_stft_callers(version):
    stock = _cut_reference(version)
    entry = CUT_GUARD_LAYOUTS[version]["stft"][0]
    call = bytes((0xDA, entry >> 16, entry & 255, (entry >> 8) & 255))
    callers = tuple(offset ^ 0x4000 for offset in range(len(stock))
                    if stock.startswith(call, offset))
    assert len(callers) == 8, (version, "exact native STFT CALLS", callers)
    return callers


def _native_stft_case(image, caller, bank, state, *, emu=None):
    """Execute native argument setup, CALLS, full STFT body and its RETS."""
    if emu is None:
        emu = _emu(image, dpp0=4)
        emu.reg.r[:] = [0x1100 + index * 0x101 for index in range(16)]
        emu.reg.r[0] = 0xE600
        emu.reg.sp = 0xFC00
        emu.reg.r[8] = bank
        emu.reg.r[9] = 0x100
        emu.write(bank + 6, 0x9000)
        emu.write(bank + 12, 0x7000)
        emu.write(bank + 14, 0xA000)
    setup = _code_bytes(emu, caller - 6, 6)
    assert setup in (bytes.fromhex("f0c9e01df0e8"),
                     bytes.fromhex("f0c9e00df0e8")), setup.hex()
    architectural = (
        emu.reg.sp, emu.reg.r[0], emu.reg.cp, tuple(emu.reg.dpp),
        tuple(emu.reg.r[index] for index in (1, 2, 3, 8, 9, 10, 11)),
        emu.reg.psw.pack() & ~0x1F,
    )
    emu.write_byte(_CUT_STATE_ADDR, state)
    result = emu.run_from(
        caller - 6, stop_at=(caller + 4, bank, 0x10000 | bank), max_steps=1000)
    actual = (
        emu.reg.sp, emu.reg.r[0], emu.reg.cp, tuple(emu.reg.dpp),
        tuple(emu.reg.r[index] for index in (1, 2, 3, 8, 9, 10, 11)),
        emu.reg.psw.pack() & ~0x1F,
    )
    assert (result.exit_reason == "stop_at" and result.final_pc == caller + 4
            and actual == architectural), (
        "native STFT return/ABI", hex(caller), hex(bank), hex(state),
        result, architectural, actual)
    return emu


def verify_native_stft_returns(version, image=None):
    """Both banks and every real add/sub caller must satisfy the stock ABI."""
    image = CUT_GUARD_LAYOUTS[version]["image"]() if image is None else image
    stock = _cut_reference(version)
    for bank in CUT_GUARD_LAYOUTS[version]["stft"][3]:
        for caller in _native_stft_callers(version):
            reference = _native_stft_case(stock, caller, bank, 0xA0)
            for state in (0, 0xA0, 0xA8):
                emu = _native_stft_case(image, caller, bank, state)
                assert (tuple(emu.reg.r), emu.read(bank + 6)) == (
                    tuple(reference.reg.r), reference.read(bank + 6)), (
                    version, "inactive STFT stock equivalence", hex(caller), state)
            for state in (0xA1, 0xA2, 0xA4):
                emu = _native_stft_case(image, caller, bank, state)
                assert emu.read(bank + 6) == 0x9000, (
                    version, "active STFT", hex(caller), state)
                _native_stft_case(image, caller, bank, 0xA0, emu=emu)
                assert emu.read(bank + 6) == 0x9000, (
                    version, "STFT awaits accepted post-cut sample", hex(caller), state)
                emu.reg.r[12], emu.reg.r[13] = 0x55, bank
                _call_native(emu, _CUT_MONITOR_LAYOUTS[version]["front"])
                _native_stft_case(image, caller, bank, 0xA0, emu=emu)
                assert emu.read(bank + 6) == reference.read(bank + 6), (
                    version, "native STFT release", hex(caller), state)


def _trim_state(emu, bank):
    return tuple(emu.read(bank + offset) for offset in _CUT_TRIM_FIELDS) + (
        emu.read_byte(bank + 0x34),)


def _seed_cut_learning(image, version, bank, state, *, qualifier=0xFE, additive=False):
    emu = _emu(image, dpp0=4)
    _entry, gate_word, gate_high, gate_low, rpm, _neutralizer = (
        _CUT_LEARNING_LAYOUTS[version])
    for offset in (6, 8, 0x1E, 0x20):
        emu.write(bank + offset, 0x9000)
    emu.write(bank + 0x18, 0x8100)
    emu.write(bank + 0x10, 0x8200)
    emu.reg.r[12] = bank
    emu.write_byte(_CUT_STATE_ADDR, state)
    emu.write(0xFD10, 0x2600)
    emu.write(0xFD54, 0x1000)
    emu.write(gate_word, 0)
    emu.write_byte(gate_high, 0xFF)
    emu.write_byte(gate_low, 0)
    emu.write_byte(bank + 0x34, qualifier)
    emu.write(rpm, 0 if additive else 0x3000)
    emu.write_byte(0xE8E5, 0 if additive else 0xC0)
    return emu


def _assert_cut_learning_frozen(version, image, bank, state, qualifier, *, additive=False):
    emu = _seed_cut_learning(image, version, bank, state, qualifier=qualifier, additive=additive)
    before = _trim_state(emu, bank)
    _call_native(emu, _CUT_LEARNING_LAYOUTS[version][0])
    assert _trim_state(emu, bank) == before, (
        version, "coherent trim/history/qualification freeze", hex(bank),
        hex(state), before, _trim_state(emu, bank))
    return emu


def verify_native_cut_learning(version):
    """Test the complete adaptation transaction, alternate STFT and fallback."""
    image = CUT_GUARD_LAYOUTS[version]["image"]()
    stock = _cut_reference(version)
    entry, *_inputs, neutralizer = _CUT_LEARNING_LAYOUTS[version]
    for bank in CUT_GUARD_LAYOUTS[version]["stft"][3]:
        reference = _seed_cut_learning(stock, version, bank, 0xA0)
        before = _trim_state(reference, bank)
        _call_native(reference, entry)
        assert _trim_state(reference, bank) != before, (version, "live learning fixture")
        for state in (0, 0xA0, 0xA8):
            emu = _seed_cut_learning(image, version, bank, state)
            _call_native(emu, entry)
            assert _trim_state(emu, bank) == _trim_state(reference, bank), (
                version, "inactive native learning stock equivalence", hex(bank), state)
        index = CUT_GUARD_LAYOUTS[version]["stft"][3].index(bank)
        for pending in (1 << index, 1 << (1 - index)):
            emu = _seed_cut_learning(image, version, bank, 0xA0)
            emu.write_byte(0xE848, pending)
            before = _trim_state(emu, bank)
            _call_native(emu, entry)
            assert _trim_state(emu, bank) == (
                before if pending & (1 << index) else _trim_state(reference, bank)), (
                version, "bank-specific learning recovery", hex(bank), pending)
        for state in (0xA1, 0xA2, 0xA4):
            for qualifier in (0, 0xFE):
                _assert_cut_learning_frozen(version, image, bank, state, qualifier)
            # Preserve the native genuine fault/fallback reset independently of
            # cut-induced learning eligibility, rather than hiding every path.
            emu = _seed_cut_learning(image, version, bank, state)
            emu.write(0xFD30, 2)
            _call_native(emu, entry)
            assert emu.read(bank + 0x10) == emu.read(bank + 0x18) == 0x8000, (
                version, "native fault trim reset", hex(bank), state)
        for state in (0, 0xA0, 0xA8, 0xA1, 0xA2, 0xA4):
            emu = _emu(image, dpp0=4)
            emu.write_byte(_CUT_STATE_ADDR, state)
            emu.write(bank + 6, 0x9000)
            emu.write(bank + 10, 0x100)
            emu.write(0xFD0E, 0x200)
            emu.reg.r[12] = bank
            _call_native(emu, neutralizer)
            expected = 0x9000 if state in (0xA1, 0xA2, 0xA4) else 0x8F00
            assert emu.read(bank + 6) == expected, (
                version, "alternate native STFT path", hex(bank), state)
        reference = _seed_cut_learning(stock, version, bank, 0xA0, additive=True)
        visited = []
        reference.cpu.set_trace(lambda pc, _opcode: visited.append(pc))
        _call_native(reference, entry)
        assert CUT_GUARD_LAYOUTS[version]["additive"][0] in visited
        assert reference.read(bank + 0x10) != 0x8200, (version, "live additive fixture")
        for state in (0, 0xA0, 0xA8):
            emu = _seed_cut_learning(image, version, bank, state, additive=True)
            _call_native(emu, entry)
            assert _trim_state(emu, bank) == _trim_state(reference, bank), (
                version, "native additive stock equivalence", hex(bank), state)
        for state in (0xA1, 0xA2, 0xA4):
            _assert_cut_learning_frozen(version, image, bank, state, 0xFE, additive=True)


def verify_cut_v9_negative_controls(version):
    """The historical defects must fail the same admission invariants."""
    image = _legacy_cut_image(version)
    bank = CUT_GUARD_LAYOUTS[version]["stft"][3][0]
    caller = _native_stft_callers(version)[0]
    for label, check in (
        ("wrong return stack", lambda: _native_stft_case(image, caller, bank, 0xA0)),
        ("paired learning transfer", lambda: _assert_cut_learning_frozen(
            version, image, bank, 0xA1, 0xFE)),
        ("learning qualification", lambda: _assert_cut_learning_frozen(
            version, image, bank, 0xA1, 0)),
        ("additive transaction", lambda: _assert_cut_learning_frozen(
            version, image, bank, 0xA1, 0xFE, additive=True)),
    ):
        try:
            check()
        except AssertionError:
            pass
        else:
            raise AssertionError((version, "V9 negative control unexpectedly passed", label))


def _assert_ms413_wbo_caller(emu):
    """Execute the real scheduler CALLS and complete RETS without reseeding ABI state."""
    assert _code_bytes(emu, 0x38C0, 4) == bytes.fromhex("da02a0b5")
    architectural = (emu.reg.sp, emu.reg.r[0], emu.reg.cp, tuple(emu.reg.dpp))
    p3, dp3 = emu.read(0xFFC4), emu.read(0xFFC6)
    visited = set()
    emu.cpu.set_trace(lambda pc, _opcode: visited.add(pc))
    result = emu.run_from(0x38C0, stop_at=0x38C4, max_steps=20000)
    emu.cpu.set_trace(None)
    assert (result.exit_reason == "stop_at" and result.final_pc == 0x38C4
            and {0x38C0, 0x2B5A0, 0x2B5A4, 0x2B5A8} <= visited
            and (emu.reg.sp, emu.reg.r[0], emu.reg.cp, tuple(emu.reg.dpp))
            == architectural), ("native WBO caller return/ABI", result)
    assert emu.read(0xFFC6) == dp3, ("native WBO caller DP3 preservation", dp3)
    assert emu.read(0xFFC4) == p3, (
        "native WBO caller P3 preservation", hex(p3), hex(emu.read(0xFFC4)))


def verify_ms413_wbo_scheduler():
    """Admit the SS1v2 WBO caller and reject V10's in-instruction lambda splice."""
    stock = _cut_reference("MS41.3")
    assert hashlib.sha256(stock).hexdigest() == (
        "89f31cbb70466ad39b23571e3c1c533251275182e8e2502c214a208e83f64487")
    # The retired V10 is an exact negative fixture, never an installable choice.
    legacy, _log = patch_ms41.build(
        stock, ["ignition_cut_v10"], marker="B", allow_deprecated=True)
    clean = _build_from(STOCK_413_PATH, ["ignition_cut_v11"])
    upgraded, _log = patch_ms41.build(legacy, ["ignition_cut_v11"], marker="B")
    assert upgraded == clean, "V10-to-V11 must produce the clean V11 image"
    images = {
        "canonical SS1v2": stock,
        "clean V11": clean,
        "V10 to V11": upgraded,
        "full V11 composition": _build_from(
            STOCK_413_PATH, list(_FULL_STACK_PATCH_IDS["SS1v2"])),
        "historical V10": legacy,
    }
    for name, image in images.items():
        for cutsw in ((None,) if name == "canonical SS1v2" else (0xFF, 0)):
            candidate = (image if cutsw is None else _full_stack_calibrations(
                image, "SS1v2", {"CUTSW": cutsw}))
            emu = _load_emulator(candidate, force_variant="SS1v2", silicon_reset=True)
            boot = emu.run_from(0, stop_at=0x2FA24,
                                max_steps=530000 if name == "full V11 composition" else 350000)
            assert (boot.exit_reason == "stop_at" and boot.final_pc == 0x2FA24
                    and emu.reset_count == 0 and emu.read(0xFFC4) & 0x0400), (
                name, "natural reset initialization and enabled TXD0 latch", boot)
            if name == "historical V10":
                emu.write_byte(_CUT_STATE_ADDR, 0xA0 if cutsw == 0xFF else 0xA1)
                emu.write_byte(0xFC3C, 0x7D)
                try:
                    _assert_ms413_wbo_caller(emu)
                except AssertionError as error:
                    assert error.args[0][0] == "native WBO caller P3 preservation", error
                else:
                    raise AssertionError("historical V10 unexpectedly preserved P3")
                continue
            # First call preserves the real reset-initialized registers and RAM.
            # Subsequent cases vary only RPM/cut inputs; this is caller coverage,
            # not proof that the natural foreground scheduler reaches the call.
            _assert_ms413_wbo_caller(emu)
            for state in (0, 0xA0, 0xA8, 0xA1, 0xA2, 0xA4):
                for rpm in (0, 0x7D):
                    emu.write_byte(_CUT_STATE_ADDR, state)
                    emu.write_byte(0xFC3C, rpm)
                    _assert_ms413_wbo_caller(emu)


_CUT_MISFIRE_LAYOUTS = {
    # Eligibility, after counting, samples/baseline, threshold, enable, warmup,
    # opportunities, cylinder counts, instantaneous flags, fuel-shutdown mask.
    "MS41.1": (0x30C9A, 0x30F56, 0xF4E4, 0xF4F2, 0xF580, 0xF504,
               0xF524, 0xF528, 0xF581, 0xF56A),
    "MS41.2": (0x3021E, 0x304DA, 0xF4BE, 0xF4CC, 0xF55A, 0xF4DE,
               0xF4FE, 0xF502, 0xF55B, 0xF544),
    "MS41.3": (0x3021E, 0x304DA, 0xF4BE, 0xF4CC, 0xF55A, 0xF4DE,
               0xF4FE, 0xF502, 0xF55B, 0xF544),
}


def _native_misfire_counts(version, image, state, cylinder, *, frozen=False, return_emu=False):
    (entry, stop, baseline, thresholds, enable, warmup, opportunities,
     counts, flags, shutdown) = _CUT_MISFIRE_LAYOUTS[version]
    emu = _emu(image, dpp0=4)
    emu.reg.r[0] = 0xE600
    emu.reg.r[9] = cylinder
    emu.write_byte(_CUT_STATE_ADDR, state)
    emu.write_byte(0xFC3C, 200)
    emu.write_byte(0xE8E5, 255)
    emu.write_byte(warmup, 0)
    emu.write_byte(enable, 0x3F)
    emu.write(baseline, 0)
    emu.write(opportunities, 7)
    emu.write_byte(flags, 0xA5)
    emu.write_byte(shutdown, 0x12)
    emu.write_byte(0xFC35, 0x12)
    for index in range(6):
        emu.write(baseline + 2 + 2 * index, 1000)
        emu.write(thresholds + 2 * index, 0)
        emu.write(counts + 2 * index, 10 + index)

    def evidence():
        return (emu.read(opportunities),
                tuple(emu.read(counts + 2 * index) for index in range(6)),
                emu.read_byte(flags), emu.read_byte(shutdown), emu.read_byte(0xFC35))

    before = evidence()
    architectural = (emu.reg.sp, emu.reg.r[0], emu.reg.cp, tuple(emu.reg.dpp))
    result = emu.run_from(entry, stop_at=stop, max_steps=3000)
    assert (result.exit_reason == "stop_at" and result.final_pc == stop
            and (emu.reg.sp, emu.reg.r[0], emu.reg.cp, tuple(emu.reg.dpp))
            == architectural), (version, "native misfire stage ABI", result)
    if frozen:
        assert evidence() == before, (
            version, "misfire counting during cut", cylinder, hex(state), before, evidence())
        assert not emu.read_byte(enable) & (1 << cylinder), (
            version, "selected misfire eligibility", cylinder)
        assert emu.read(thresholds + 2 * cylinder) == 0x8000, (
            version, "selected misfire threshold", cylinder)
    return emu if return_emu else evidence()


def verify_native_misfire_counts(version):
    """Cover the delayed-cylinder counter stage, not eligibility alone."""
    if version == "MS41.0":
        # This family has the shared roughness/coil detector; its exact DTC
        # table and native cleanup are exercised by the existing guard tests.
        return
    image = CUT_GUARD_LAYOUTS[version]["image"]()
    stock = _cut_reference(version)
    for cylinder in range(6):
        reference = _native_misfire_counts(version, stock, 0xA0, cylinder)
        assert reference[0] == 8 and sum(reference[1]) == sum(range(10, 16)) + 1, (
            version, "live misfire counter fixture", cylinder, reference)
        for state in (0, 0xA0, 0xA8):
            assert _native_misfire_counts(version, image, state, cylinder) == reference, (
                version, "inactive misfire stock equivalence", cylinder, state)
        for state in (0xA1, 0xA2, 0xA4):
            _native_misfire_counts(version, image, state, cylinder, frozen=True)
    try:
        _native_misfire_counts(version, _legacy_cut_image(version), 0xA1, 0, frozen=True)
    except AssertionError:
        pass
    else:
        raise AssertionError((version, "V9 counter negative control unexpectedly passed"))

    # Keep the same machine across a one-event cut and a complete cylinder
    # sequence. Counting observes a delayed cylinder, so eligibility alone is
    # insufficient on the first release pass. Reuse the native two-observation
    # per-cylinder recovery, without clearing preexisting count/shutdown data.
    entry, stop, *_inputs, opportunities, counts, flags, shutdown = (
        _CUT_MISFIRE_LAYOUTS[version])
    recovery = 0xF560 if version == "MS41.1" else 0xF53A
    for active_events in (1, 6):
        for first in range(6):
            for state in (0xA1, 0xA2, 0xA4):
                emu = _native_misfire_counts(
                    version, image, state, first, frozen=True, return_emu=True)

                def evidence():
                    return (emu.read(opportunities),
                            tuple(emu.read(counts + 2 * i) for i in range(6)),
                            emu.read_byte(flags), emu.read_byte(shutdown), emu.read_byte(0xFC35))

                initial = evidence()
                abi = (emu.reg.sp, emu.reg.r[0], emu.reg.cp, tuple(emu.reg.dpp))
                assert _ram_bytes(emu, recovery, 6) == bytes([2] * 6)
                for event in range(1, active_events + 24):
                    active = event < active_events
                    emu.write_byte(_CUT_STATE_ADDR, state if active else 0xA0)
                    emu.reg.r[9] = (first + event) % 6
                    result = emu.run_from(entry, stop_at=stop, max_steps=3000)
                    assert (result.exit_reason == "stop_at" and result.final_pc == stop
                            and (emu.reg.sp, emu.reg.r[0], emu.reg.cp, tuple(emu.reg.dpp)) == abi)
                    actual = evidence()
                    if active:
                        assert actual == initial and _ram_bytes(emu, recovery, 6) == bytes([2] * 6)
                    elif event - active_events < 12:
                        assert actual[:2] == initial[:2], (
                            version, "premature delayed-cylinder counting", first, event, state)
                    assert actual[3:] == initial[3:], (version, "native shutdown evidence", event)
                assert _ram_bytes(emu, recovery, 6) == bytes(6)
                assert actual[0] > initial[0] and sum(actual[1]) > sum(initial[1]), (
                    version, "genuine misfire detection resumes after native recovery", first, state)


_CUT_MONITOR_LAYOUTS = {
    "MS41.0": dict(front=0x2793C, rear=None, mixture=0x28808,
                   mixture_records=(0xEA9C, 0xEAA6)),
    "MS41.1": dict(front=0x2B3E4, rear=0x2B49C, mixture=0x2CBFE,
                   mixture_records=(0xEAB6, 0xEAC2), combined=0x2CE3A,
                   combined_count=0x98, combined_cal=0x196,
                   adc=0x37CEE, adc_banks=0xDBAA, adc_inputs=(0xF707, 0xF708),
                   catalyst=0x294C0, selected=0xDBDA, count_cal=0x6A, fail_cal=0xF4),
    "MS41.2": dict(front=0x2B014, rear=0x2B0CC, mixture=0x2C0B4,
                   mixture_records=(0xEAB6, 0xEAC2), combined=0x2C30A,
                   combined_count=0xA6, combined_cal=0x180,
                   adc=0x361E6, adc_banks=0xF176, adc_inputs=(0xF711, 0xF712),
                   catalyst=0x291DC, selected=0xF596, count_cal=0x68, fail_cal=0xE0),
}
_CUT_MONITOR_LAYOUTS["MS41.3"] = _CUT_MONITOR_LAYOUTS["MS41.2"]


def _ram_bytes(emu, address, size):
    return bytes(emu.read_byte(address + offset) for offset in range(size))


def verify_native_cut_observation(version):
    """A full O2 scheduler pass must hold envelopes, filters and timers."""
    entry, runflag, bankcount = {
        "MS41.0": (0x279D0, 0xFD4C, 0xEDD0),
        "MS41.1": (0x2B560, 0xFD5C, 0xDBAA),
        "MS41.2": (0x2B190, 0xFD5C, 0xF176),
        "MS41.3": (0x2B190, 0xFD5C, 0xF176),
    }[version]
    image = CUT_GUARD_LAYOUTS[version]["image"]()
    stock = _cut_reference(version)
    banks = CUT_GUARD_LAYOUTS[version]["stft"][3]
    for index, bank in enumerate(banks):
        def fixture(candidate, state, pending):
            emu = _emu(candidate, dpp0=4)
            emu.write_byte(_CUT_STATE_ADDR, state)
            emu.write_byte(0xE848, pending)
            emu.write(runflag, 0x8000)
            emu.write(0xFD46, 0x400 if index == 0 else 0)
            emu.write_byte(bankcount, 2)
            for seed_bank in banks:
                for offset in range(0, 0x34, 2):
                    emu.write(seed_bank + offset, 0x3333)
                for offset, value in ((2, 0x8001), (4, 0), (6, 0x8000),
                                      (0xC, 0x7000), (0xE, 0x9000)):
                    emu.write(seed_bank + offset, value)
            return emu

        for state, pending in ((0xA1, 0), (0xA2, 0), (0xA4, 0),
                               (0xA0, 1 << index), (0xA0, 0), (0, 15)):
            emu = fixture(image, state, pending)
            before = _ram_bytes(emu, bank, 0x34)
            result = _call_native(emu, entry)
            assert result.steps > 90, (version, "full selected-bank scheduler pass", result)
            if state in (0xA1, 0xA2, 0xA4):
                assert all(emu.read_byte(bank + offset) == value
                           for offset, value in enumerate(before) if offset != 0x2B), (
                    version, "whole native O2 observation freeze", index, state, pending, result)
            else:
                # The scheduler acquires O2 before the observation body. An
                # accepted post-cut sample clears this bank's pending bit and
                # legitimately re-enables native processing in the same pass.
                assert not emu.read_byte(0xE848) & (1 << index)
                reference = fixture(stock, state, pending)
                _call_native(reference, entry)
                assert _ram_bytes(emu, bank, 0x34) == _ram_bytes(reference, bank, 0x34), (
                    version, "whole observation accepted-sample release", index, state, pending)

        # The fault reset is downstream of both observation and lambda
        # qualification. A direct adaptation call alone missed these bypasses.
        for state in (0xA1, 0xA2, 0xA4):
            results = []
            for candidate in (stock, image):
                emu = _emu(candidate, dpp0=4)
                emu.write(runflag, 0x8000)
                emu.write_byte(bankcount, 2)
                emu.write(0xFD46, 0x402 if index == 0 else 2)
                emu.write(0xFD30, 2)
                emu.write_byte(_CUT_STATE_ADDR, state)
                for offset, value in ((2, 0x8921 if version == "MS41.3" else 0x8121),
                                      (4, 1), (6, 0x8000),
                                      (0xC, 0x7000), (0xE, 0x9000),
                                      (0x10, 0x8200), (0x18, 0x8100)):
                    emu.write(bank + offset, value)
                emu.write_byte(bank + 0x2B, 0x55)
                emu.write_byte(0xFAE6 if version == "MS41.0" else 0xFC3C, 0xFF)
                emu.write_byte(0xE9F0, 0x55)
                emu.write_byte(0xE9F1, 0x55)
                visited = set()
                emu.cpu.set_trace(lambda pc, _opcode: visited.add(pc))
                _call_native(emu, entry)
                assert _CUT_LEARNING_LAYOUTS[version][0] in visited, (
                    version, "fault fixture reaches actual learning through whole caller", index, state)
                results.append((emu.read(bank + 0x10), emu.read(bank + 0x18)))
            assert results[0] == results[1], (
                version, "whole scheduler native fault fallback", index, state, results)
            assert results[1] == (0x8000, 0x8000), (
                version, "live whole scheduler fault reset fixture", index, state)


def verify_cut_sample_freshness(version):
    """Only an accepted native sample may release that bank's pending bit."""
    image = CUT_GUARD_LAYOUTS[version]["image"]()
    layout = _CUT_MONITOR_LAYOUTS[version]
    pending = 3 if version == "MS41.0" else 15
    for index, bank in enumerate(CUT_GUARD_LAYOUTS[version]["stft"][3]):
        for kind in ("front", "rear"):
            entry = layout[kind]
            if entry is None:
                continue
            own_bit = 1 << (index + (2 if kind == "rear" else 0))
            output = bank + (0x42 if kind == "rear" else 0x2B)
            emu = _emu(image, dpp0=4)
            emu.write_byte(0xE848, pending)
            emu.write_byte(output, 0x17)
            emu.write(0xFD46, 0)
            emu.write_byte(bank + 0x41, 0x55)
            for state in (0xA1, 0xA0):
                emu.reg.r[12] = bank if kind == "rear" else 0x55
                emu.reg.r[13] = bank
                emu.write_byte(_CUT_STATE_ADDR, state)
                _call_native(emu, entry)
                assert (emu.read_byte(output), emu.read_byte(0xE848)) == (
                    (0x17, pending) if state == 0xA1
                    else (0x55, pending & ~own_bit)), (
                    version, kind, "native sample freshness", index, hex(state),
                    emu.read_byte(output), emu.read_byte(0xE848))


def verify_native_cut_monitors(version):
    """Execute complete mixture and U3-monitor callers through real cleanup."""
    image = CUT_GUARD_LAYOUTS[version]["image"]()
    stock = _cut_reference(version)
    layout = _CUT_MONITOR_LAYOUTS[version]
    for index, bank in enumerate(CUT_GUARD_LAYOUTS[version]["stft"][3]):
        monitors = [(layout["mixture"], layout["mixture_records"][index])]
        if "combined" in layout:
            monitors.append((layout["combined"], (0xEE2E, 0xEE3A)[index]))
        for entry, record in monitors:
            def fixture(candidate, state, pending):
                emu = _emu(candidate, dpp0=4)
                emu.write_byte(_CUT_STATE_ADDR, state)
                emu.write_byte(0xE848, pending)
                emu.write(0xFD46, (0x100 if version != "MS41.0" else 0)
                          | (0x400 if index else 0))
                emu.write(0xFD0E, 2)
                for offset, value in ((6, 0xFFFF), (0x18, 0x8000),
                                      (0xC, 0x7000), (0xE, 0x9000)):
                    emu.write(bank + offset, value)
                emu.write_byte(bank + 0x32, 1)
                if "combined" in layout:
                    emu.write(bank + layout["combined_count"], emu.read(layout["combined_cal"]))
                return emu

            def transaction(emu):
                for _ in range(2):
                    emu.reg.r[12] = bank
                    _call_native(emu, entry)
                return (_ram_bytes(emu, record, 10 if version == "MS41.0" else 12),
                        emu.read_byte(bank + 0x32), emu.read_byte(0xEA0E),
                        emu.read_byte(0xEA18), emu.read(0xFD46),
                        emu.read(bank + layout["combined_count"]) if "combined" in layout else 0)

            reference = fixture(stock, 0xA0, 0)
            expected = transaction(reference)
            assert expected[0][0] & 0x60 == 0x60, (
                version, "live native mixture monitor fixture", hex(entry), expected)
            own, other = 1 << index, 1 << (1 - index)
            for state, pending, frozen in ((0, 15, False), (0xA0, 0, False),
                                           (0xA0, other, False), (0xA0, own, True),
                                           (0xA1, 0, True), (0xA2, 0, True), (0xA4, 0, True)):
                emu = fixture(image, state, pending)
                count_before = emu.read(bank + layout["combined_count"]) if "combined" in layout else 0
                actual = transaction(emu)
                if frozen:
                    assert (actual[0] == bytes(len(actual[0])) and actual[1] == 1
                            and actual[2:4] == (0, 0) and actual[5] == count_before), (
                        version, "whole native mixture monitor freeze", hex(entry),
                        index, hex(state), pending, actual)
                else:
                    assert actual == expected, (
                        version, "released native mixture stock equivalence", hex(entry),
                        index, hex(state), pending, actual, expected)

    if "adc" in layout:
        def adc_result(candidate, state):
            emu = _emu(candidate, dpp0=4)
            emu.write_byte(_CUT_STATE_ADDR, state)
            emu.write_byte(layout["adc_banks"], 2)
            for address in layout["adc_inputs"]:
                emu.write_byte(address, 0xFF)
            _call_native(emu, layout["adc"])
            return (emu.read_byte(0xEA0E), _ram_bytes(emu, 0xEE46, 24),
                    emu.read_byte(0xEA18))

        reference = adc_result(stock, 0xA0)
        assert reference[0] == 0 and reference[1][0] & 1 and reference[1][12] & 1
        # Default calibration may have zero maturation increment. Assert the
        # actual native bad-input path and complete records, not an invented DTC.
        for state in (0, 0xA0, 0xA8, 0xA1, 0xA2, 0xA4):
            assert adc_result(image, state) == reference, (
                version, "225/226 native fault and scratch preservation", state)
        assert adc_result(_legacy_cut_image(version), 0xA1)[0] == 1, (
            version, "V9 EA0E leak negative control")


def verify_native_cut_catalyst(version):
    """Discard incomplete cut samples while preserving completed fault evidence."""
    layout = _CUT_MONITOR_LAYOUTS[version]
    if "catalyst" not in layout:
        return
    image = CUT_GUARD_LAYOUTS[version]["image"]()
    stock = _cut_reference(version)
    for index, bank in enumerate(CUT_GUARD_LAYOUTS[version]["stft"][3]):
        own, other = (5, 10) if index == 0 else (10, 5)
        record = (0xECDE, 0xECEA)[index]

        def fixture(candidate, state, pending, complete):
            emu = _emu(candidate, dpp0=4)
            emu.reg.r[9] = 0x1234
            emu.write_byte(_CUT_STATE_ADDR, state)
            emu.write_byte(0xE848, pending)
            emu.write(layout["selected"], bank)
            emu.write(0xFD10, 4)
            emu.write(bank + 2, 0x8000)
            for offset in range(0x5E, 0x70, 2):
                emu.write(bank + offset, 3)
            if complete:
                emu.write(bank + 0x6E, emu.read(layout["count_cal"]) + 1)
                emu.write(bank + 0x6C, emu.read(layout["fail_cal"]) + (complete == "bad"))
            return emu

        for state, pending, complete in (
            (0xA1, 0, None), (0xA2, 0, None), (0xA4, 0, None),
            (0xA0, own, None), (0xA0, other, None), (0xA0, 0, None),
            (0xA1, 0, "bad"), (0xA0, own, "bad"), (0xA1, 0, "good"),
        ):
            emu = fixture(image, state, pending, complete)
            blocked = state in (0xA1, 0xA2, 0xA4) or pending == own
            _call_native(emu, layout["catalyst"])
            assert emu.reg.r[9] == 0x1234, (version, "native catalyst R9")
            if blocked and not complete:
                assert emu.read_byte(bank + 0x40) == 0 and all(
                    emu.read(bank + offset) == 0 for offset in range(0x5E, 0x70, 2)), (
                    version, "incomplete catalyst window", index, state, pending)
                assert _ram_bytes(emu, record, 12) == bytes(12)
            else:
                reference = fixture(stock, state, pending, complete)
                if complete:
                    reference.write(0xFD10, 0)  # Evaluate the completed history only.
                _call_native(reference, layout["catalyst"])
                assert _ram_bytes(emu, record, 12) == _ram_bytes(reference, record, 12), (
                    version, "native catalyst diagnostic evidence", index, state, complete)
                if complete == "bad":
                    assert emu.read_byte(record) == 0x71
                if not complete:
                    assert _ram_bytes(emu, bank, 0xAC) == _ram_bytes(reference, bank, 0xAC)
                    assert emu.read(0xFD10) == reference.read(0xFD10)

        emu = fixture(image, 0xA1, 0, None)
        for offset, value in ((0, 0x71), (1, 7), (2, 1), (10, 0x20)):
            emu.write_byte(record + offset, value)
        prior = _ram_bytes(emu, record, 12)
        _call_native(emu, layout["catalyst"])
        assert _ram_bytes(emu, record, 12) == prior, (
            version, "preserve preexisting catalyst fault", index)


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
    """Admission requires native transactions, complete returns and release."""
    layout = CUT_GUARD_LAYOUTS[version]
    image = layout["image"]()
    verify_native_stft_returns(version)
    verify_native_cut_learning(version)
    verify_native_misfire_counts(version)
    verify_cut_sample_freshness(version)
    verify_native_cut_monitors(version)
    verify_native_cut_catalyst(version)
    verify_native_cut_observation(version)
    verify_cut_v9_negative_controls(version)
    # Retain the established coil/O2 splice and real descriptor/DTC-manager
    # checks, alongside the complete transaction tests above.
    for hook, active_cleanup, stock_cont in layout["diagnostics"]:
        emu, res, final, sp0 = _guard_route(
            image, hook, 0xA8, (active_cleanup, stock_cont))
        assert final == stock_cont and res.exit_reason == "stop_at" and emu.reg.sp == sp0
        for state in (0xA1, 0xA2, 0xA4):
            emu, res, final, sp0 = _guard_route(
                image, hook, state, (active_cleanup, stock_cont))
            assert (final == active_cleanup and res.exit_reason == "stop_at"
                    and emu.reg.sp == sp0), (
                version, "diagnostic active", hex(hook), hex(state), res)
            emu, res, final, sp0 = _guard_route(
                image, hook, 0xA0, (active_cleanup, stock_cont), emu=emu)
            assert (final == stock_cont and res.exit_reason == "stop_at"
                    and emu.reg.sp == sp0), (
                version, "diagnostic restored", hex(hook), res)
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
        bytes(image), flash_writable=True, force_variant="SS1v2")
    # The actual post-prologue SYSCON keeps segmentation enabled. Artificial
    # flash-to-SFR preloading sets SGTDIS and redirects this target to 0x2200.
    assert emu.mem.translate(_FLASH_TARGET) == _FLASH_TARGET_PHYS
    emu.mem.flash_model = FlashModel(
        target=_FLASH_TARGET, busy_reads=2)
    Timer1(tick=1).attach(emu.peripherals)
    emu.write(0xE656, _FLASH_TARGET)
    emu.write(0xE744, 0xFFFF)
    emu.reg.set_word(0, 0xE600)
    emu.reg.sp = 0xFBFC
    emu.mem.write_word_direct(0xFBFC, 0xE000)
    emu.mem.write_word_direct(0xFBFE, 0)
    if kind == "program":
        emu.write(0xE73C, 0xE800)
        emu.write(0xE73A, 2)
        emu.write(0xE800, 0xBEEF)
        start, end, flash_entry = 0x4230, 0x432C, 0x0230
    else:
        start, end, flash_entry = 0x432E, 0x4410, 0x032E
    if from_ram:
        _copy_driver(emu, start, end + 2)  # Include the native terminal RETS.
        entry = 0xE320
    else:
        # Negative control: execute the exact flash-resident prefix only up to
        # starting the WSM. Its next instruction fetch must see CUI busy status,
        # not the image opcode; continuing it cannot prove native-driver success.
        emu.cpu.ip = flash_entry
        for _ in range(400000):
            emu.cpu.step()
            if emu.mem.flash_model.pending is not None:
                break
        assert emu.mem.flash_model.pending is not None
        assert emu.mem.flash_model.mode == "status"
        pc = emu.cpu.pc
        native_word = bytes(emu.mem.image[pc ^ 0x4000:(pc ^ 0x4000) + 2])
        fetched_word = bytes(
            emu.mem.read_code_byte(emu.cpu.ip + index, emu.cpu.csp)
            for index in range(2))
        assert fetched_word == b"\x00\x00" and fetched_word != native_word
        assert emu.read_byte(0xE742) != 1
        assert emu.mem.image[_FLASH_TARGET_FILE:_FLASH_TARGET_FILE + 2] == (
            image[_FLASH_TARGET_FILE:_FLASH_TARGET_FILE + 2])
        return
    result = _run(emu, entry, stop_at=0xE000, max_steps=400000)
    assert result.exit_reason == "stop_at" and emu.cpu.pc == 0xE000
    assert emu.reg.sp == 0xFC00 and emu.reg.get_word(0) == 0xE600
    assert emu.read_byte(0xE742) == 1
    emu.write(_FLASH_TARGET, 0x00FF)
    expected = 0xBEEF if kind == "program" else 0xFFFF
    assert emu.read(_FLASH_TARGET) == expected
    assert emu.mem.image[_FLASH_TARGET_FILE:_FLASH_TARGET_FILE + 2] == (
        expected.to_bytes(2, "little"))


def verify_intel_flash_mutation():
    """Prove real RAM-driver returns and reject flash-resident CUI execution."""
    _verify_startup_wait(
        patch_ms41.startup_wait_definition(PATCHES), ["softbsl_loader", "cal_guard"])
    for kind in ("program", "erase"):
        for from_ram in (False, True):
            _intel_driver(kind, from_ram=from_ram)


def _amd_driver(device, kind):
    image = bytearray(FULL_IMAGE)
    image[_FLASH_TARGET_FILE:_FLASH_TARGET_FILE + 2] = (
        0xFFFF if kind == "program" else 0).to_bytes(2, "little")
    emu = _load_emulator(
        bytes(image), flash_writable=True, force_variant="1406464")
    assert emu.mem.translate(_FLASH_TARGET) == _FLASH_TARGET_PHYS
    emu.mem.flash_model = AmdFlashModel(device=device, busy_reads=4)
    Timer1(tick=1).attach(emu.peripherals)
    emu.write(0xE656, _FLASH_TARGET)
    emu.write(0xE744, 0xFFFF)
    emu.reg.set_word(0, 0xE600)
    emu.reg.sp = 0xFBFC
    emu.mem.write_word_direct(0xFBFC, 0xE000)
    emu.mem.write_word_direct(0xFBFE, 0)
    if kind == "program":
        emu.write(0xE73C, 0xE800)
        emu.write(0xE73A, 2)
        emu.write(0xE800, 0xBEEF)
        start, end = 0x4230, 0x4308
    else:
        start, end = 0x432E, 0x43C4
    _copy_driver(emu, start, end)  # AMD descriptor slice includes RETS.
    result = _run(emu, 0xE320, stop_at=0xE000, max_steps=400000)
    assert result.exit_reason == "stop_at" and emu.cpu.pc == 0xE000
    assert emu.reg.sp == 0xFC00 and emu.reg.get_word(0) == 0xE600
    assert emu.read_byte(0xE742) == 1
    expected = 0xBEEF if kind == "program" else 0xFFFF
    assert emu.read(_FLASH_TARGET) == expected
    assert emu.mem.image[_FLASH_TARGET_FILE:_FLASH_TARGET_FILE + 2] == (
        expected.to_bytes(2, "little"))


def _verify_startup_wait(patch, selected_ids):
    """Execute CalGuard's shared wait and compare native startup state."""
    edits = {edit["off"]: edit for edit in patch["edits"]}
    assert bytes.fromhex(edits[0x4460]["data"]) == bytes.fromhex("e6f060ea")
    assert bytes.fromhex(edits[0x4484]["data"]) == bytes.fromhex("a758a7a728013dfc")
    for path, variant in _REFERENCE_VARIANTS.items():
        source = path.read_bytes()
        image, _ = patch_ms41.build(source, selected_ids, marker="B")
        old, new = [_load_emulator(candidate, force_variant=variant, silicon_reset=True)
                    for candidate in (source, image)]
        for emu in (old, new):
            result = emu.run_from(0, stop_at=(0x048C,), max_steps=190000)
            assert result.exit_reason == "stop_at" and not emu.reset_count
        assert new.cpu.state_times - old.cpu.state_times == 479948
        assert old.reg.snapshot() == new.reg.snapshot()
        assert old.reg.psw.pack() == new.reg.psw.pack()
        assert old.mem.ram == new.mem.ram
        assert not new.asc0.tx
        print(f"[PASS] {patch['id']} {variant}: nominal 40 ms wait, watchdog, native state preserved",
              flush=True)


def verify_amd_flash_mutation():
    """Bind and execute the exact current AMD descriptor payload."""
    patch = PATCHES["amd_flash"]
    asm = TEST_DATA_ROOT.parent / "Decompilation" / "asm"
    program = bytes.fromhex(
        (ROOT / "engines" / "patcher" / "program_amd_v6.hex").read_text(encoding="ascii").strip())
    erase = bytes.fromhex(
        (asm / "erase_amd.hex").read_text(encoding="ascii").strip())
    program_edit = bytes.fromhex(patch["edits"][0]["data"])
    erase_edit = bytes.fromhex(patch["edits"][1]["data"])
    assert program_edit == program[12:]
    assert erase_edit == erase[6:]
    assert hashlib.sha256(program).hexdigest() == (
        "c935b0c2a3c09e6f7668d1342f55435f1cbfa11ef6b6a06afc8fccbb6901680f")
    assert hashlib.sha256(program_edit).hexdigest() == (
        "57cf955e150ba14eb398830fcbf61920fbca90df8fc7d379cc388a7ec2b3630a")
    assert hashlib.sha256(erase).hexdigest() == (
        "21b0cb2e7fc17bdf1fef238a312783f320cf8903ccdcbda2c618763cbbf5a460")
    assert hashlib.sha256(erase_edit).hexdigest() == (
        "e1c68088867bf4b0dfe50a57a89f21a507b9a78ab608f358bb387c3001cca93f")
    _verify_startup_wait(
        patch_ms41.startup_wait_definition(PATCHES),
        ["amd_flash", "softbsl_loader", "cal_guard"])
    for device in ("am29f200bb", "am29f400bb"):
        for kind in ("program", "erase"):
            _amd_driver(device, kind)


def verify_top_ds2_guard():
    """Execute the final shared-builder TOP images on all supported references."""
    from engines.softbsl.softbsl_host import compose_persistent_image

    payload = bytes.fromhex(
        (ROOT / "engines" / "patcher" / "top_ds2_guard.hex").read_text())
    payload_edit = next(edit for edit in PATCHES["top_ds2_guard"]["edits"]
                        if edit["off"] == 0x4F22)
    assert payload == bytes.fromhex(payload_edit["data"])
    for path, variant in _REFERENCE_VARIANTS.items():
        image, ids, _log = compose_persistent_image(
            path.read_bytes(), "29f400", with_calguard=True, marker="T")
        assert "top_ds2_guard" in ids
        status = checksum.checksum_status(image)
        assert all(status[key] for key in ("boot", "program", "cal")), status
        _bind_image(image, variant)
        for fast in ((False,) if variant == "1429861" else (False, True)):
            result = exercise_top_ds2_guard(
                image, variant, load_emulator=_load_emulator, fast=fast)
            print(f"[PASS] TOP DS2 {variant} fast={fast}: {result}")


ADMISSION_REGISTRY = {
    "alphan_failsafe": "features-ms413",
    "amd_flash": "amd-flash",
    "top_ds2_guard": "top-ds2",
    "cal_guard": "cal-guard",
    "door_0x43": "loader-doors",
    "door_0x43_ms410": "loader-doors",
    "door_0x43_ms411": "loader-doors",
    "door_magic": "loader-doors",
    "door_magic_ms410": "loader-doors",
    "door_magic_ms411": "loader-doors",
    "ignition_cut_v11": "features-ms413",
    "ignition_cut_v10_ms410": "features-ms410",
    "ignition_cut_v10_ms411": "features-ms411",
    "ignition_cut_v10_ms412": "features-ms412",
    "launch_control_v11": "features-ms413",
    "launch_control_v11_ms410": "features-ms410",
    "launch_control_v11_ms411": "features-ms411",
    "launch_control_v11_ms412": "features-ms412",
    "softbsl_loader": "loader-doors",
    "vanos_minrpm_ms411": "features-ms411",
    "vanos_minrpm_v2_ms410": "features-ms410",
}


def _verify_registry():
    installable = {
        patch_id for patch_id, patch in PATCHES.items()
        if not patch.get("deprecated")
        and (patch.get("cave")
             or patch_id in {"amd_flash", "softbsl_loader", "top_ds2_guard"})
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
        ROOT / "engines" / "patcher" / "top_ds2_guard.asm",
        ROOT / "engines" / "patcher" / "top_ds2_guard.hex",
        ROOT / "engines" / "softbsl" / "softbsl_host.py",
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
        EMU_ROOT / "oracle" / "remint_oracle.py",
        EMU_ROOT / "oracle" / "EmuMs41.java",
        *manual_evidence_paths(),
    ):
        add(path.relative_to(EMU_ROOT).as_posix(), path)
    for path in sorted((EMU_ROOT / "oracle" / "sab80c166" / "data" / "languages").iterdir()):
        if path.suffix in (".sinc", ".slaspec", ".cspec", ".pspec", ".ldefs"):
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
    add("amd/program_amd_v6.asm", ROOT / "engines" / "patcher" / "program_amd_v6.asm")
    add("amd/program_amd_v6.hex", ROOT / "engines" / "patcher" / "program_amd_v6.hex")
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
        ("ignition cut V10", verify_ignition_cut_v10),
        ("cut side effects", lambda: verify_cut_side_effect_guards("MS41.2")),
        ("launch V11 state", verify_launch_brain),
        ("launch V11 Always speed/TPS envelope", verify_launch_always_gates),
        ("launch V11 soft limiter", verify_launch_fuel_soft_cave),
        ("launch V11 hard comparator", verify_launch_fuel_hard_comparator),
        ("launch native hard ceiling", verify_launch_native_hard_ceiling),
        ("launch native fuel recovery", verify_launch_native_fuel_recovery),
        ("launch hysteresis priority", verify_launch_hysteresis_priority),
        ("launch hysteresis ABI", verify_launch_hysteresis_abi),
        ("stock limiter parity", verify_stock_limiter_parity),
        ("launch + ignition composition", verify_composed_launch_and_ignition),
    ]
    for label, check in checks:
        check()
        print(f"[PASS] MS41.2 {label}")
    _verify_watchdog_liveness(
        STOCK_PATH, "ignition_cut_v10_ms412", "launch_control_v11_ms412")
    print("[PASS] MS41.2 stock/V10/V10+V11/full-stack boot-reset liveness")


def _group_features_413():
    checks = [
        ("AlphaN V3 fallback", verify_alphan_failsafe),
        ("ignition cut V11", lambda: verify_ignition_cut_v10(_case_image_413)),
        ("native WBO caller preserves P3", verify_ms413_wbo_scheduler),
        ("cut side effects", lambda: verify_cut_side_effect_guards("MS41.3")),
        ("launch V11 state", lambda: verify_launch_brain(_case_image_413)),
        ("launch V11 Always speed/TPS envelope",
         lambda: verify_launch_always_gates(_case_image_413)),
        ("launch V11 soft limiter", verify_launch_fuel_soft_cave_ms413),
        ("launch V11 hard comparator",
         lambda: verify_launch_fuel_hard_comparator(_case_image_413)),
        ("launch native hard ceiling",
         lambda: verify_launch_native_hard_ceiling(_case_image_413)),
        ("launch native fuel recovery",
         lambda: verify_launch_native_fuel_recovery(_case_image_413)),
        ("launch hysteresis priority",
         lambda: verify_launch_hysteresis_priority(_case_image_413)),
        ("launch hysteresis ABI",
         lambda: verify_launch_hysteresis_abi(_case_image_413)),
        ("stock limiter parity",
         lambda: verify_stock_limiter_parity(
             _case_image_413, STOCK_413_PATH, (0x07F0, 0x0926))),
        ("launch + ignition composition",
         lambda: verify_composed_launch_and_ignition(_case_image_413)),
    ]
    for label, check in checks:
        check()
        print(f"[PASS] MS41.3 {label}")
    _verify_watchdog_liveness(
        STOCK_413_PATH, "ignition_cut_v11", "launch_control_v11")
    print("[PASS] MS41.3 stock/V11/V11+V11/full-stack boot-reset liveness")


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
            max_steps=380000 if emu is emulators[1] else 200000)
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
            max_steps=580000,
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
    ignition_version = PATCHES[ignition_id]["version"]
    images = {
        "stock": _bind_image(stock_path.read_bytes(), variant),
        f"ignition {ignition_version}": _build_from(stock_path, [ignition_id]),
        f"ignition {ignition_version} + launch V11": _build_from(
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


def verify_ms410_limiter_diagnostics(layout):
    """Execute the stock limiter-adjacent DTC paths across every cut mode."""
    variant = "1429861"
    stock_image = _bind_image(layout["stock_path"].read_bytes(), variant)
    v9_image = _case_image_older(layout, {
        "CUTSW": 0x00, "CUTRPM": 0x7D,
        "CUT_HYST": 0xFF, "CUT_IPW": 0xFFFF,
    })
    launch_image = _case_image_older(layout, {
        "CUTSW": 0xFF, "CUTRPM": 0xD7,
        "CUT_HYST": 0xFF, "CUT_IPW": 0xFFFF,
        "LC_SW": 0x00, "LC_CUTTYPE": 1, "LC_CLUTCHPOL": 0,
        "LC_MAXRPM": 0x7D, "LC_ARMSPEED": 5, "LC_MAXSPEED": 0x1E,
        "LC_MINTPS": 0x80, "LC_HARDRPM": 0xCE,
        "LC_HYST": 0xFF, "LC_IPW": 0xFFFF,
    })

    def booted(image, stop=0x0924):
        emu = _load_emulator(
            image, force_variant=variant, silicon_reset=True)
        result = _run(
            emu, 0, stop_at=(stop,),
            max_steps=400_000 if stop == 0x2B388 else 100_000)
        assert result.final_pc == stop and result.exit_reason == "stop_at"
        emu.watchdog.enabled = False
        return emu

    def run_t0(emu):
        emu.interrupts.schedule_irq("T0", after=1)
        visited = []
        emu.cpu.set_trace(lambda pc, _opcode: visited.append(pc))
        result = _run(emu, 0xE000, stop_at=(0xE008,), max_steps=1_000)
        emu.cpu.set_trace(None)
        assert (
            result.final_pc == 0xE008 and result.exit_reason == "stop_at"
            and 0x0080 in visited and 0x2180 in visited
            and 0x322EC in visited
        ), (result, visited[:12])

    def dispatch_task_33(emu):
        emu.reg.r[12] = 0x33
        _call_native(emu, 0x208A0, max_steps=5_000)
        visited = []
        emu.cpu.set_trace(lambda pc, _opcode: visited.append(pc))
        result = _run(
            emu, 0x20D20, stop_at=(0x20D50,), max_steps=5_000)
        emu.cpu.set_trace(None)
        assert (
            result.final_pc == 0x20D50
            and result.exit_reason == "stop_at"
            and 0x403E in visited
            and 0x2BA6E in visited
            and 0x2B9EC in visited
        ), (result, visited[-20:])

    def seed_segment_monitor(emu, *, previous, current, fd50):
        emu.write_byte(0xFD08, 0x10)
        emu.write_byte(0xE655, 0x08)
        emu.write(0xFD14, 0)
        emu.write_byte(0xF0B8, previous)
        emu.write_byte(0xF0B9, current)
        emu.write(0xFD50, fd50)
        emu.write(0xEBD2, 0)
        emu.write(0xEBD8, 0)
        emu.write(0xEA0C, 0)
        emu.write_byte(0xEA18, 0)
        emu.write_byte(0xEA19, 0)
        emu.write(0xFD34, 0)
        emu.write(0xFD38, 0)
        emu.write_byte(0xFAC6, 0)
        emu.write_byte(0xFAC7, 0)

    def make_mode(name):
        if name in {"normal", "stock fuel"}:
            emu = _emu(stock_image, dpp0=4)
            if name == "stock fuel":
                emu.write_byte(0xFAE6, 0xD0)
                emu.write(0xFD44, 0)
                emu.write(0xFD30, 0)
                emu.write(0xFD34, 0)
                emu.write_byte(0xED50, 0)
                emu.write_byte(0xED51, 0)
                emu.write_byte(0xED53, 0)
                emu.write_byte(0xED54, 1)
                _call_native(emu, 0x206BE)
                assert (
                    emu.read_byte(0xFADE) == 6
                    and emu.read(0xFD12) & 0x8000
                )
            return emu

        if name == "V10":
            emu = _emu(v9_image, dpp0=4)
            cut, replayed, hygiene = _run_older_ignition(
                layout, v9_image, rpm=0xC8, emu=emu)
            assert cut and not replayed and hygiene
            assert emu.read_byte(_CUT_STATE_ADDR) == 0xA1
            return emu

        emu, result, _stack = _run_older_launch(
            layout, launch_image, speed=0, tps=0xC0, rpm=0xC8)
        assert (
            result.exit_reason == "stop_at"
            and emu.read_byte(layout["launch_latch"]) & 0x40
        )
        cut, replayed, hygiene = _run_older_ignition(
            layout, launch_image, rpm=0xC8, request=True, emu=emu)
        assert cut and not replayed and hygiene
        assert emu.read_byte(_CUT_STATE_ADDR) == 0xA2
        return emu

    # Real T0 vector and ISR: first force an in-flight command-0x103 timeout,
    # then exercise the healthy clear/periodic-task path on the same ECU state.
    emu = booted(stock_image)
    for address in range(0xE000, 0xE010, 2):
        emu.write(address, 0x00CC)
    emu.cpu.psw.IEN = True
    emu.cpu.psw.ILVL = 0
    emu.write_byte(0xFF9C, 0x49)
    emu.write(0xFDDC, 0)
    emu.write(0xFD02, 0x0080)
    emu.write(0xFD50, 0)
    run_t0(emu)
    assert (
        emu.read(0xFD50) & 0x8000
        and emu.read(0xFD02) == 0x0100
        and emu.read(0xFDDC) == 0x0010
    ), (hex(emu.read(0xFD50)), hex(emu.read(0xFD02)), hex(emu.read(0xFDDC)))

    emu.write(0xFDDC, 0)
    emu.write(0xFD02, 0)
    emu.write(0xFD50, 0x8000)
    emu.write_byte(0xFAC0, 1)
    emu.write(0xFB52, 0x1234)
    emu.write(0xFE50, 0x1000)
    run_t0(emu)
    assert emu.read(0xFD50) == 0x6000, hex(emu.read(0xFD50))

    visited = []
    emu.cpu.set_trace(lambda pc, _opcode: visited.append(pc))
    foreground = _run(
        emu, 0x20CAE, stop_at=(0x20CB8,), max_steps=2_000)
    emu.cpu.set_trace(None)
    assert (
        foreground.final_pc == 0x20CB8
        and foreground.exit_reason == "stop_at"
        and 0x3248 in visited and 0x208A0 in visited
        and emu.read_byte(0xFAC6) == 1
        and emu.read_byte(0xE8A5) == 0x33
        and emu.read(0xFD50) == 0
    ), (foreground, visited[-20:])
    emu.write_byte(0xFD08, emu.read_byte(0xFD08) | 0x10)
    emu.write_byte(0xE655, emu.read_byte(0xE655) | 0x08)
    emu.write_byte(0xF0B8, 1)
    emu.write_byte(0xF0B9, 0)
    visited = []
    emu.cpu.set_trace(lambda pc, _opcode: visited.append(pc))
    queued = _run(emu, 0x20D20, stop_at=(0x20D50,), max_steps=5_000)
    emu.cpu.set_trace(None)
    assert (
        queued.final_pc == 0x20D50 and queued.exit_reason == "stop_at"
        and 0x403E in visited and 0x2BA6E in visited
        and 0x2B9EC in visited
        and emu.read_byte(0xFAC7) == 1
        and emu.read_byte(0xEBD3) == 0
        and emu.read(0xEA0C) == 0
    ), (queued, visited[-20:])

    expected = {
        "healthy": (0, 0, 0, 0),
        "bad delta": (20, 0x1000, 0x78, 0x1000),
        "FD50.15": (20, 0x1000, 0x78, 0x1000),
    }
    scenarios = {
        "healthy": (1, 0, 0),
        "bad delta": (0, 0, 0),
        "FD50.15": (1, 0, 0x8000),
    }
    for mode in ("normal", "stock fuel", "V10", "launch"):
        for scenario, (previous, current, fd50) in scenarios.items():
            emu = make_mode(mode)
            seed_segment_monitor(
                emu, previous=previous, current=current, fd50=fd50)
            for _ in range(10):
                dispatch_task_33(emu)
            got = (
                emu.read_byte(0xEBD3), emu.read(0xEA0C),
                emu.read_byte(0xEBD2), emu.read(0xEBD8),
            )
            assert got == expected[scenario], (mode, scenario, got)
            assert (
                emu.read_byte(0xFAC6) == 10
                and emu.read_byte(0xFAC7) == 10
            )

    emu = booted(stock_image, 0x2B388)
    seed_segment_monitor(emu, previous=1, current=0, fd50=0x8000)
    for _ in range(10):
        dispatch_task_33(emu)
    frame = _native_ds2(emu, 0x22428, 0x227E4, 0x04, 1)
    records = parse_ds2_dtc_response(frame[3:-1])
    assert (
        len(records) == 1
        and records[0].code == 100
        and records[0].is_active
        and int.from_bytes(
            records[0].raw_record[6:8], "little") == 0x1000
    ), (frame.hex(" "), records)

    def evaluate_dtc214(emu):
        result = _run(
            emu, 0x2F210, stop_at=(0x25B10, 0x2F48A),
            max_steps=100_000)
        called_manager = result.final_pc == 0x25B10
        if called_manager:
            result = _run(
                emu, 0x25B10, stop_at=(0x2F48A,), max_steps=100_000)
        assert result.final_pc == 0x2F48A and result.exit_reason == "stop_at"
        return called_manager

    for mode in ("normal", "stock fuel", "V10", "launch"):
        emu = make_mode(mode)
        limiter_state = (
            emu.read_byte(0xFADE), emu.read_byte(_CUT_STATE_ADDR))
        emu.reg.dpp[:] = [4, 5, 0, 3]
        emu.write(0xFD06, emu.read(0xFD06) | 0x20)
        qualifying_rpm = max(75, emu.read_byte(0xFAE6))
        emu.write_byte(0xFAE6, qualifying_rpm)
        emu.write_byte(0xE8E5, 46)
        emu.write(0xEDF2, 0)
        counters = []
        manager_calls = []
        for _ in range(6):
            manager_calls.append(evaluate_dtc214(emu))
            counters.append(emu.read(0xEDF6))
        assert counters == [1, 2, 3, 4, 5, 5], (
            mode, counters, emu.read(0xEDF4), emu.read_byte(0xFAE6),
            emu.read_byte(0xE8E5), hex(emu.read(0xFD06)))
        assert manager_calls == [False] * 5 + [True], (mode, manager_calls)
        assert bytes(
            emu.read_byte(0xEA42 + index) for index in range(8)
        ) == bytes((0x78, 0, 1, 0x28, qualifying_rpm, 46, 0, 0))
        assert emu.read(0xFD30) & 0x0010
        final_limiter_state = (
            emu.read_byte(0xFADE), emu.read_byte(_CUT_STATE_ADDR))
        if mode == "stock fuel":
            assert limiter_state[0] and final_limiter_state[0], (
                mode, limiter_state, final_limiter_state)
        else:
            assert limiter_state == final_limiter_state, (
                mode, limiter_state, final_limiter_state)

    emu = booted(stock_image, 0x2B388)
    emu.write(0xFD06, emu.read(0xFD06) | 0x20)
    emu.write_byte(0xFAE6, 75)
    emu.write_byte(0xE8E5, 46)
    emu.write(0xEDF2, 0)
    for _ in range(6):
        evaluate_dtc214(emu)
    frame = _native_ds2(emu, 0x22428, 0x227E4, 0x04, 1)
    records = parse_ds2_dtc_response(frame[3:-1])
    assert frame == bytes.fromhex(
        "12 0F A0 01 D6 78 01 28 4B 2E 00 00 00 00 5E"
    ), frame.hex(" ")
    assert (
        len(records) == 1
        and records[0].code == 214
        and records[0].is_active
    ), records
    emu.write(0xEDF2, 3)
    assert evaluate_dtc214(emu)
    assert (
        emu.read(0xEDF6) == 0
        and emu.read_byte(0xEA42) == 0xB8
        and not emu.read(0xFD30) & 0x0010
    )


def _group_older(version):
    layout = OLDER_FEATURE_LAYOUTS[version]
    _verify_older_ignition(layout)
    verify_cut_side_effect_guards(version)
    _verify_older_launch(layout)
    _verify_watchdog_liveness(
        layout["stock_path"], layout["ignition_id"], layout["launch_id"])
    if version == "MS41.0":
        verify_ms410_limiter_diagnostics(layout)
    if version == "MS41.1":
        _verify_ms411_vanos(layout)
    suffix = (
        "/watchdog/full-stack-boot-reset/limiter-diagnostics"
        if version == "MS41.0"
        else "/VANOS/watchdog/full-stack-boot-reset"
    )
    print(f"[PASS] {version} ignition/launch/side-effects{suffix}")


GROUPS = {
    "cal-guard": _group_cal_guard,
    "loader-doors": _group_loader,
    "intel-flash": verify_intel_flash_mutation,
    "amd-flash": verify_amd_flash_mutation,
    "top-ds2": verify_top_ds2_guard,
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
