#!/usr/bin/env python3
"""Execute the current MS41.0-MS41.3 patch set in canonical ms41emu.

This is a behavioural gate for the port, not a replacement for on-car/HIL tests.
It composes the real JSON descriptors through ``patch_ms41.build``, keeps every
case checksummed, and executes the resulting C166 machine code.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]


def _required_directory(variable: str) -> Path:
    value = os.environ.get(variable, "").strip()
    if not value:
        raise SystemExit(f"set {variable} to run the private emulator gate")
    path = Path(value).expanduser()
    if not path.is_dir():
        raise SystemExit(f"{variable} does not identify an available directory")
    return path


def _find_reference(root: Path, variant: str) -> Path:
    marker = f"ref_{variant.lower()}"
    candidates = []
    for path in root.rglob("*.bin"):
        if not path.is_file() or path.stat().st_size != 0x40000:
            continue
        parts = (root.name.lower(),) + tuple(
            part.lower() for part in path.relative_to(root).parts
        )
        name = path.name.lower()
        if marker not in parts or "full" not in name:
            continue
        if variant == "MS41.3" and ("stock" not in name or "cksum" in name):
            continue
        candidates.append(path)
    if not candidates:
        raise SystemExit(
            f"{variant} full reference image was not found under MS41_TEST_DATA_ROOT"
        )
    return sorted(candidates)[0]


EMU_ROOT = _required_directory("MS41EMU_ROOT")
TEST_DATA_ROOT = _required_directory("MS41_TEST_DATA_ROOT")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(EMU_ROOT))

import checksum  # noqa: E402
from engines.patcher import patch_ms41  # noqa: E402
from engines.patcher.cal_guard_exact import (  # noqa: E402
    BOOT_EXIT,
    RECOVER_EXIT,
    assemble as assemble_cal_guard,
)
from ms41emu import Emulator  # noqa: E402
from ms41emu.gate import verify_cal_guard  # noqa: E402


STOCK_410_PATH = _find_reference(TEST_DATA_ROOT, "MS41.0")
STOCK_411_PATH = _find_reference(TEST_DATA_ROOT, "MS41.1")
STOCK_PATH = _find_reference(TEST_DATA_ROOT, "MS41.2")
STOCK_413_PATH = _find_reference(TEST_DATA_ROOT, "MS41.3")
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
    return image


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


def _build_413():
    stock = STOCK_413_PATH.read_bytes()
    image, _log = patch_ms41.build(
        stock, ["ignition_cut_v7", "launch_control_v5"], marker="B")
    status = checksum.checksum_status(image)
    assert status["boot"] and status["program"] and status["cal"], status
    assert status["prog_disabled"], status
    return image


FULL_413_IMAGE = _build_413()


def verify_calguard_compatibility():
    """Execute the registered fast compatibility gate on every stock family."""
    guard = PATCHES["cal_guard"]
    guard_bytes = bytes.fromhex(next(
        edit["data"] for edit in guard["edits"]
        if edit["off"] == guard["cave"]["base"]
    ))
    assert guard_bytes == assemble_cal_guard()

    references = {
        "MS41.0": STOCK_410_PATH,
        "MS41.1": STOCK_411_PATH,
        "MS41.2": STOCK_PATH,
        "MS41.3": STOCK_413_PATH,
    }
    for version, path in references.items():
        assert verify_cal_guard(
            path.read_bytes(), cave=guard_bytes) == "BOOT", version

    # Same broad generation is insufficient: ID41 calibration with ID59
    # program (or vice versa) must remain in the stock flash listener.
    id41_to_id59 = bytearray(STOCK_410_PATH.read_bytes())
    assert id41_to_id59[0x6007:0x600B] == b"0641"
    id41_to_id59[0x1400C:0x14010] = b"0659"
    assert verify_cal_guard(
        id41_to_id59, cave=guard_bytes) == "RECOVER"

    # The strict SS1v2 identity still takes precedence over legacy suffixes.
    strict_mismatch = bytearray(STOCK_413_PATH.read_bytes())
    assert strict_mismatch[0x173BB:0x173C0] == b"SS1v2"
    assert strict_mismatch[0x6007:0x600B] != b"0641"
    strict_mismatch[0x6007:0x600B] = b"0641"
    assert verify_cal_guard(
        strict_mismatch, cave=guard_bytes) == "RECOVER"

    # E740=1 is intentionally the existing stock flash-listener branch.
    assert verify_cal_guard(
        STOCK_PATH.read_bytes(), e740=1, cave=guard_bytes) == "RECOVER"


def verify_brickguard_integrity():
    """Execute the registered full integrity guard and representative faults."""
    guard = PATCHES["brick_guard"]
    entry = guard["cave"]["main_cpu"]

    def run(image, e740=3):
        emu = Emulator.load(bytes(image))
        emu.reg.dpp[0] = 4
        emu.reg.dpp[1] = 5
        emu.reg.dpp[2] = 0
        emu.write_byte(0xE740, e740)
        return emu.run_from(
            entry,
            stop_at=(BOOT_EXIT, RECOVER_EXIT),
            max_steps=25_000_000,
        )

    references = {
        "MS41.0": STOCK_410_PATH,
        "MS41.1": STOCK_411_PATH,
        "MS41.2": STOCK_PATH,
        "MS41.3": STOCK_413_PATH,
    }
    for version, path in references.items():
        image, _log = patch_ms41.build(
            path.read_bytes(), ["softbsl_loader", "brick_guard"])
        assert run(image).final_pc == BOOT_EXIT, version

        listener = run(image, e740=1)
        assert listener.final_pc == RECOVER_EXIT and listener.steps == 3

        for label, offset in (
            ("boot", 0x4000),
            ("program-low", 0x0100),
            ("program-upper", 0x20000),
            ("calibration", 0x14100),
        ):
            corrupt = bytearray(image)
            corrupt[offset] ^= 1
            assert run(corrupt).final_pc == RECOVER_EXIT, (version, label)

        survivor = bytearray(b"\xFF" * len(image))
        survivor[0x4000:0x6000] = image[0x4000:0x6000]
        assert run(survivor).final_pc == RECOVER_EXIT, version


OLDER_FEATURE_LAYOUTS = {
    "MS41.0": {
        "stock_path": STOCK_410_PATH,
        "patch_ids": (
            "ignition_cut_v7_ms410",
            "launch_control_v4_ms410",
            "vanos_minrpm_ms410",
        ),
        "ignition_id": "ignition_cut_v7_ms410",
        "launch_id": "launch_control_v4_ms410",
        "vanos_id": "vanos_minrpm_ms410",
        "ignition_hooks": (0x26F8, 0x275C),
        "ignition_entry": 0x26E8,
        "ignition_cave_cpu": 0x32820,
        "ignition_replay_cpu": 0x3288A,
        "rpm_address": 0xFAE6,
        "paired_selector": 0xFD4E,
        "launch_hook": 0x0710,
        "launch_continuations": (0x0714, 0x0726),
        "soft_limit_address": 0xED52,
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
        "rpm_address": 0xFC3C,
        "paired_selector": 0xFD5E,
        "launch_hook": 0x07D6,
        "launch_continuations": (0x07DA, 0x07EC),
        "soft_limit_address": 0xF02C,
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
        image[cal_offsets[name]] = value & 0xFF
    image, _details = checksum.correct_checksums(image, correct_program=True)
    status = checksum.checksum_status(image)
    assert status["boot"] and status["program"] and status["cal"], status
    return bytes(image)


def _case_image_413(values):
    """Set the MS41.3 patch controls and restore its active checksums."""
    image = bytearray(FULL_413_IMAGE)
    cal_offsets = {}
    for patch_id in ("ignition_cut_v7", "launch_control_v5"):
        cal_offsets.update(PATCHES[patch_id]["cave"]["cals"])
    for name, value in values.items():
        image[cal_offsets[name]] = value & 0xFF
    image, _details = checksum.correct_checksums(image)
    status = checksum.checksum_status(image)
    assert status["boot"] and status["program"] and status["cal"], status
    assert status["prog_disabled"], status
    return bytes(image)


def _case_image_older(layout, values):
    image = bytearray(layout["image"])
    cal_offsets = {}
    for patch_id in layout["patch_ids"]:
        cal_offsets.update(PATCHES[patch_id].get("cave", {}).get("cals", {}))
    for name, value in values.items():
        image[cal_offsets[name]] = value & 0xFF
    image, _details = checksum.correct_checksums(image, correct_program=True)
    status = checksum.checksum_status(image)
    assert status["boot"] and status["program"] and status["cal"], status
    return bytes(image)


def _emu(image, dpp0=5):
    emu = Emulator.load(image)
    emu.reg.dpp[0] = dpp0
    # Function-level calls skip stock startup, which normally establishes the
    # calibration flash pages as DPP0=4 and DPP1=5.
    emu.reg.dpp[1] = 5
    return emu


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
        res = emu.run_from(0x15A0, stop_at=(0x1D92,), max_steps=10)
        assert res.final_pc == 0x1D92 and res.exit_reason == "stop_at", (version, res)

        emu = _emu(image)
        emu.write_byte(0xE653, 0x00)
        res = emu.run_from(0x1D92, stop_at=(0x0A44,), max_steps=100)
        assert res.final_pc == 0x0A44 and res.exit_reason == "stop_at", (version, res)

        emu = _emu(image)
        emu.write_byte(0xE653, 0x5A)
        emu.write_byte(0xE423, 0x9C)
        emu.write_byte(0xE424, 0x9C)
        res = emu.run_from(0x1D92, stop_at=(0x1FD8,), max_steps=100)
        assert res.final_pc == 0x1FD8 and res.exit_reason == "stop_at", (version, res)

        payload = f"{version} relocated loader".encode()
        expected = _crc16(payload)
        for supplied, want_rl4 in (
            (expected, 0), (expected ^ 1, 1),
        ):
            # Test-only CALLS trampoline. The production entry returns with
            # RETS and the wrapper has no spare trampoline tail.
            test_image = bytearray(image)
            test_image[0x4100:0x4104] = bytes.fromhex("da001204")
            emu = _emu(bytes(test_image))
            for index, value in enumerate(payload):
                emu.write_byte(0xD800 + index, value)
            emu.reg.r[5] = 0xD800 + len(payload)
            emu.write_byte(0xE427, supplied >> 8)
            emu.write_byte(0xE428, supplied & 0xFF)
            res = emu.run_from(
                0x0100, stop_at=(0x0104,), max_steps=10000)
            assert res.final_pc == 0x0104 and (emu.reg.r[4] & 0xFF) == want_rl4, (
                version, supplied, res, emu.reg.r[4])

        # Persistent 0x2A door: stock NAK passthrough and matched commit paths.
        emu = _emu(image)
        emu.cpu.csp = 2
        emu.write_byte(0xE653, 0x00)
        res = emu.run_from(persistent_hook, stop_at=(nak_handler,), max_steps=100)
        assert res.final_pc == nak_handler, (version, res)

        emu = _emu(image)
        emu.cpu.csp = 2
        emu.write_byte(0xE653, 0x2A)
        res = emu.run_from(persistent_hook, stop_at=(0x1A62,), max_steps=100)
        assert res.final_pc == 0x1A62 and emu.read(0xE740) == 1, (
            version, res, emu.read(0xE740))

        # Disposable 0x43 door: stock clear-adapts passthrough and RAM-agent upload.
        emu = _emu(bootstrap)
        emu.cpu.csp = 2
        emu.write_byte(0xE653, 0x00)
        res = emu.run_from(
            bootstrap_hook, stop_at=(clear_handler,), max_steps=100)
        assert res.final_pc == clear_handler, (version, res)

        emu = _emu(bootstrap)
        emu.cpu.csp = 2
        emu.write_byte(0xE653, 0x43)
        emu.write_byte(0xE423, 0x9C)
        emu.write_byte(0xE424, 0x9C)
        res = emu.run_from(
            bootstrap_hook, stop_at=(bootstrap_tx,), max_steps=100)
        assert res.final_pc == bootstrap_tx and emu.cpu.csp == 3, (version, res)


IGNITION_HOOKS = (0xD92A, 0xD98E)       # IP values while CSP=3
IGNITION_CAVE_CPU = 0x3DC70             # full CPU address used by trace
IGNITION_STOCK_REPLAY_CPU = 0x3DCDA     # cave's displaced ANDB P1L,RL1
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
    res = emu.run_from(
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
        res.final_pc == hook + 4 and res.exit_reason == "stop_at"
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
                res = emu.run_from(entry, stop_at=(hook + 4,), max_steps=300)
                replayed = IGNITION_STOCK_REPLAY_CPU in visited
                expected_p1l = 0xFF if wants_cut else mask
                assert (
                    res.final_pc == hook + 4 and res.exit_reason == "stop_at"
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
    res = emu.run_from(0x9928, stop_at=(0x992C,), max_steps=500)
    latch_out = bool(emu.read(0xFD5A) & 0x40)
    spark = bool(emu.read(0xFD5A) & 0x80)
    hygiene = (
        res.final_pc == 0x992C
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
        res = emu.run_from(0x07D2, stop_at=(0x07D6, 0x07E8), max_steps=300)
        hygiene = (res.final_pc == continuation and res.exit_reason == "stop_at"
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
        res = emu.run_from(0x07D2, stop_at=(0x07D6,), max_steps=300)
        hygiene = (
            res.final_pc == 0x07D6
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
            res = emu.run_from(entry, stop_at=outcomes, max_steps=300)
            assert (res.final_pc == outcomes[result_index]
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
        res = emu.run_from(0x9928, stop_at=(0x992C,), max_steps=500)
        assert res.final_pc == 0x992C, (name, res)
        request = bool(emu.read(0xFD5A) & 0x80)

        cut, stock, hygiene = _run_ignition(
            image, rpm=rpm, request=request)
        assert cut == want and stock == (not want) and hygiene, (
            name, request, cut, stock, want, hygiene)


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
    res = emu.run_from(hook, stop_at=(hook + 4,), max_steps=300)
    replayed = layout["ignition_replay_cpu"] in visited
    hygiene = (
        res.final_pc == hook + 4 and res.exit_reason == "stop_at"
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
            res = emu.run_from(
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
    res = emu.run_from(
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
            res = emu.run_from(entry, stop_at=outcomes, max_steps=300)
            assert (
                res.final_pc == outcomes[result_index]
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
        res = emu.run_from(
            layout["vanos_hook"],
            stop_at=layout["vanos_outcomes"],
            max_steps=100,
        )
        assert (
            res.final_pc == layout["vanos_outcomes"][outcome_index]
            and res.exit_reason == "stop_at"
            and res.regs["dpp"][0] == 4
        ), (name, res)


def main():
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

    verify_calguard_compatibility()
    verify_brickguard_integrity()

    checks = [
        ("relocated loader + command doors", verify_loader_and_doors),
        ("ignition cut V7 native CC6 final-stage reachability", verify_ignition_cut_v7),
        ("launch V4 state machine", verify_launch_brain),
        ("launch V4 MS41.2 soft limiter / branch cave", verify_launch_fuel_soft_cave),
        ("launch V4 independent hard limiter comparator", verify_launch_fuel_hard_comparator),
        ("launch V4 + ignition V7 composition", verify_composed_launch_and_ignition),
    ]
    print("[PASS] cal_guard: exact IDs, strict SS1v2, mismatch and E740 recovery")
    print("[PASS] brick_guard: boot/program/cal faults and E740 recovery")
    for label, check in checks:
        check()
        print(f"[PASS] {label}")
    print(f"\nMS41.2 EMULATOR GATE PASS ({len(checks) + 2} groups)")

    checks_413 = [
        ("ignition cut V7 native CC6 final-stage reachability",
         lambda: verify_ignition_cut_v7(_case_image_413)),
        ("launch V5 state machine", lambda: verify_launch_brain(_case_image_413)),
        ("launch V5 MS41.3 soft limiter / continuation",
         verify_launch_fuel_soft_cave_ms413),
        ("launch V5 independent hard limiter comparator",
         lambda: verify_launch_fuel_hard_comparator(_case_image_413)),
        ("launch V5 + ignition V7 composition",
         lambda: verify_composed_launch_and_ignition(_case_image_413)),
    ]
    for label, check in checks_413:
        check()
        print(f"[PASS] MS41.3 {label}")
    print(f"\nMS41.3 EMULATOR GATE PASS ({len(checks_413)} groups)")

    for version, layout in OLDER_FEATURE_LAYOUTS.items():
        _verify_older_ignition(layout)
        print(f"[PASS] {version} ignition cut V7 final-stage hooks")
        _verify_older_launch(layout)
        print(f"[PASS] {version} launch V4 native staged limiter + V7 composition")
        if version == "MS41.1":
            _verify_ms411_vanos(layout)
            print("[PASS] MS41.1 VANOS minimum-RPM retrofit")
        print(f"\n{version} FEATURE PORT EMULATOR GATE PASS")


if __name__ == "__main__":
    main()
