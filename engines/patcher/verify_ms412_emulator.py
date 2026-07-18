#!/usr/bin/env python3
"""Execute the current MS41.2/MS41.3 patch set in canonical ms41emu.

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
from ms41emu import Emulator  # noqa: E402
from ms41emu.gate import verify_cal_guard  # noqa: E402


STOCK_PATH = _find_reference(TEST_DATA_ROOT, "MS41.2")
STOCK_413_PATH = _find_reference(TEST_DATA_ROOT, "MS41.3")
PATCHES = patch_ms41.load_patches()
LATEST = [
    "amd_flash", "softbsl_loader", "cal_guard", "door_magic",
    "ignition_cut_v7", "launch_control_v4_ms412",
]


def _build(ids):
    stock = STOCK_PATH.read_bytes()
    image, _log = patch_ms41.build(stock, ids, marker="B")
    status = checksum.checksum_status(image)
    assert status["boot"] and status["program"] and status["cal"], status
    return image


FULL_IMAGE = _build(LATEST)
BOOTSTRAP_IMAGE = _build(["amd_flash", "softbsl_loader", "door_0x43"])


def _build_413():
    stock = STOCK_413_PATH.read_bytes()
    image, _log = patch_ms41.build(
        stock, ["ignition_cut_v7", "launch_control_v4"], marker="B")
    status = checksum.checksum_status(image)
    assert status["boot"] and status["cal"] and status["prog_disabled"], status
    return image


FULL_413_IMAGE = _build_413()


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
    for patch_id in ("ignition_cut_v7", "launch_control_v4"):
        cal_offsets.update(PATCHES[patch_id]["cave"]["cals"])
    for name, value in values.items():
        image[cal_offsets[name]] = value & 0xFF
    image, _details = checksum.correct_checksums(image)
    status = checksum.checksum_status(image)
    assert status["boot"] and status["cal"] and status["prog_disabled"], status
    return bytes(image)


def _emu(image, dpp0=5):
    emu = Emulator.load(image)
    emu.reg.dpp[0] = dpp0
    return emu


def _crc16(data, init=0xFFFF):
    crc = init
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return crc & 0xFFFF


def verify_loader_and_doors():
    # Execute the real SA1 dispatcher call slot. File 0x55A0 maps to CPU 0x15A0;
    # its relocated operand must enter the new main at CPU 0x1D92.
    emu = _emu(FULL_IMAGE)
    res = emu.run_from(0x15A0, stop_at=(0x1D92,), max_steps=10)
    assert res.final_pc == 0x1D92 and res.exit_reason == "stop_at", res

    # Relocated loader: ordinary traffic must fall through to the stock handler.
    emu = _emu(FULL_IMAGE)
    emu.write_byte(0xE653, 0x00)
    res = emu.run_from(0x1D92, stop_at=(0x0A44,), max_steps=100)
    assert res.final_pc == 0x0A44 and res.exit_reason == "stop_at", res

    # A valid 0x5A/0x9C9C header reaches the relocated TX helper at CPU 0x1FD8.
    emu = _emu(FULL_IMAGE)
    emu.write_byte(0xE653, 0x5A)
    emu.write_byte(0xE423, 0x9C)
    emu.write_byte(0xE424, 0x9C)
    res = emu.run_from(0x1D92, stop_at=(0x1FD8,), max_steps=100)
    assert res.final_pc == 0x1FD8 and res.exit_reason == "stop_at", res

    # Execute the relocated CRC helper against both matching and bad headers.
    payload = b"MS41.2 relocated loader"
    expected = _crc16(payload)
    for supplied, want_ip, want_rl4 in (
        # Stop on the RETS after the result byte has been written.  The two
        # result MOVB instructions start at 0x1C6E/0x1C72 respectively.
        (expected, 0x1C70, 0), (expected ^ 1, 0x1C74, 1),
    ):
        emu = _emu(FULL_IMAGE)
        for index, value in enumerate(payload):
            emu.write_byte(0xD800 + index, value)
        emu.reg.r[5] = 0xD800 + len(payload)
        emu.write_byte(0xE427, supplied >> 8)
        emu.write_byte(0xE428, supplied & 0xFF)
        res = emu.run_from(0x1C32, stop_at=(0x1C70, 0x1C74), max_steps=10000)
        assert res.final_pc == want_ip and (emu.reg.r[4] & 0xFF) == want_rl4, (
            supplied, res, emu.reg.r[4])

    # Persistent 0x2A door: passthrough and matched reset/commit paths.
    emu = _emu(FULL_IMAGE)
    emu.cpu.csp = 2
    emu.write_byte(0xE653, 0x00)
    res = emu.run_from(0x3386, stop_at=(0x385E,), max_steps=100)
    assert res.final_pc == 0x385E, res

    emu = _emu(FULL_IMAGE)
    emu.cpu.csp = 2
    emu.write_byte(0xE653, 0x2A)
    res = emu.run_from(0x3386, stop_at=(0x1A62,), max_steps=100)
    assert res.final_pc == 0x1A62 and emu.read(0xE740) == 1, (
        res, emu.read(0xE740))

    # Disposable 0x43 bootstrap door: passthrough and valid-header helper jump.
    emu = _emu(BOOTSTRAP_IMAGE)
    emu.cpu.csp = 2
    emu.write_byte(0xE653, 0x00)
    res = emu.run_from(0x3354, stop_at=(0x51CC,), max_steps=100)
    assert res.final_pc == 0x51CC, res

    emu = _emu(BOOTSTRAP_IMAGE)
    emu.cpu.csp = 2
    emu.write_byte(0xE653, 0x43)
    emu.write_byte(0xE423, 0x9C)
    emu.write_byte(0xE424, 0x9C)
    res = emu.run_from(0x3354, stop_at=(0xDBEC,), max_steps=100)
    assert res.final_pc == 0xDBEC and emu.cpu.csp == 3, res


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
    hygiene = (res.final_pc == 0x992C and res.exit_reason == "stop_at"
               and res.regs["sp"] == sp0 and res.regs["dpp"][0] == 5)
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
        hygiene = (res.final_pc == 0x07D6 and res.exit_reason == "stop_at"
                   and res.regs["sp"] == sp0 and res.regs["dpp"][0] == 5)
        assert emu.read_byte(0xF014) == want and hygiene, (
            name, emu.read_byte(0xF014), want, res, hygiene)


def verify_launch_fuel_hard_comparator(case_image=_case_image):
    """Exercise both real DB87 CALL sites and their untouched stock branches.

    V4 uses LC_HARDRPM rather than a DB86/DB87-derived gap. It also clamps a
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


def main():
    if not EMU_ROOT.is_dir():
        raise SystemExit(f"ms41emu package not found: {EMU_ROOT}")
    if not STOCK_PATH.is_file():
        raise SystemExit(f"MS41.2 reference image not found: {STOCK_PATH}")
    if not STOCK_413_PATH.is_file():
        raise SystemExit(f"MS41.3 reference image not found: {STOCK_413_PATH}")

    guard = PATCHES["cal_guard"]
    guard_bytes = bytes.fromhex(next(
        edit["data"] for edit in guard["edits"] if edit["off"] == guard["cave"]["base"]))
    assert verify_cal_guard(FULL_IMAGE, cave=guard_bytes) == "BOOT"

    checks = [
        ("relocated loader + command doors", verify_loader_and_doors),
        ("ignition cut V7 native CC6 final-stage reachability", verify_ignition_cut_v7),
        ("launch V4 state machine", verify_launch_brain),
        ("launch V4 MS41.2 soft limiter / branch cave", verify_launch_fuel_soft_cave),
        ("launch V4 independent hard limiter comparator", verify_launch_fuel_hard_comparator),
        ("launch V4 + ignition V7 composition", verify_composed_launch_and_ignition),
    ]
    print("[PASS] cal_guard: consistent MS41.2 -> BOOT")
    for label, check in checks:
        check()
        print(f"[PASS] {label}")
    print(f"\nMS41.2 EMULATOR GATE PASS ({len(checks) + 1} groups)")

    checks_413 = [
        ("ignition cut V7 native CC6 final-stage reachability",
         lambda: verify_ignition_cut_v7(_case_image_413)),
        ("launch V4 state machine", lambda: verify_launch_brain(_case_image_413)),
        ("launch V4 MS41.3 soft limiter / continuation",
         verify_launch_fuel_soft_cave_ms413),
        ("launch V4 independent hard limiter comparator",
         lambda: verify_launch_fuel_hard_comparator(_case_image_413)),
        ("launch V4 + ignition V7 composition",
         lambda: verify_composed_launch_and_ignition(_case_image_413)),
    ]
    for label, check in checks_413:
        check()
        print(f"[PASS] MS41.3 {label}")
    print(f"\nMS41.3 EMULATOR GATE PASS ({len(checks_413)} groups)")


if __name__ == "__main__":
    main()
