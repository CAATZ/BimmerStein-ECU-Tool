"""Execute both installed final-event angle paths through the native ISR return.

The original firmware is the oracle for tooth split, dwell, charge and release.
These fixtures exercise machine code, not physical ignition timing or scheduling.
"""
from __future__ import annotations

from engines.patcher.verify_launch_retard_state import (
    CUT_STATE_ADDR, CUT_RECOVERY_ADDR, CUT_TIMING_ADDR, CUT_SOURCE_ADDR,
)

from pathlib import Path

import checksum

from engines.patcher.verify_launch_retard_state import LAYOUTS as STATE_LAYOUTS


# Native ISR entry/context, final-angle hooks, inverse angle, dwell and offsets.
LAYOUTS = {
    "1429861": ("ms410", 0x5510, 0xFC3C, (0x5CD6, 0x5DAA), 0x32EC0,
                0xFB00, 0xFB02, 0xFB04, 0xFB26, 0xFD4E, 0xFAE2, 0xFADE),
    "1437806": ("ms411", 0x5592, 0xFCBC, (0x5D58, 0x5E2C), 0x3F7C0,
                0xFC56, 0xFC58, 0xFC5A, 0xFC7C, 0xFD5E, 0xFC38, 0xFC34),
    "1406464": ("ms412", 0x5592, 0xFB00, (0x5D58, 0x5E2C), 0x3E700,
                0xFC56, 0xFC58, 0xFC5A, 0xFC7C, 0xFD5E, 0xFC38, 0xFC34),
    "SS1v2": ("ms413", 0x5592, 0xFB00, (0x5D58, 0x5E2C), 0x3E700,
              0xFC56, 0xFC58, 0xFC5A, 0xFC7C, 0xFD5E, 0xFC38, 0xFC34),
}
_DISPLACED = bytes.fromhex("F00267F00F00")


def verify_launch_retard_artifacts(image, stock_image, variant):
    """Bind the verified event boundaries and installed helper artifacts."""
    family, entry, context, hooks, timing, *_ = LAYOUTS[variant]
    prefix = b"\xC6\x08" + context.to_bytes(2, "little") + bytes.fromhex("C6870000EC06EC07")
    assert stock_image[entry ^ 0x4000:(entry ^ 0x4000) + len(prefix)] == prefix
    assert image[entry ^ 0x4000:(entry ^ 0x4000) + len(prefix)] == prefix
    replacement = bytes((0xDA, timing >> 16, timing & 255, (timing >> 8) & 255, 0xCC, 0))
    for hook in hooks:
        assert stock_image[hook ^ 0x4000:(hook ^ 0x4000) + 6] == _DISPLACED
        assert image[hook ^ 0x4000:(hook ^ 0x4000) + 6] == replacement
    patcher = Path(__file__).parent
    for kind in ("timing", "qualification", "state", "state_entry"):
        revision = {"timing": "v18", "qualification": "v18", "state": "v16", "state_entry": "v14"}[kind]
        base = next(line.split()[1] for line in
                    (patcher / f"ignition_cut_{revision}_{family}_{kind}.asm").read_text().splitlines()
                    if line.startswith("base "))
        payload = bytes.fromhex((patcher / f"ignition_cut_{revision}_{family}_{kind}.hex").read_text())
        offset = int(base, 16) ^ 0x4000
        assert image[offset:offset + len(payload)] == payload, (variant, kind)


def run_timing(source, variant, load_emulator, *, ordinary=192, correction=0, cylinder=0, buffer=0,
        fdmode=0x0400, old=0xC0, fault=0, fault2=0, knock=None,
        armed=0x40, road_speed=0, throttle=192, fuel=0, input=0,
        rpm_value=125, marker=0xA0, timeline=False, owner=None, extra_bytes=(), return_emu=False):
    _, entry, context, hooks, _, inverse, dwell, factors, offsets, flags, crank, stage = LAYOUTS[variant]
    _, _, soft, rpm, hard, speed, latch, pins, pin82 = STATE_LAYOUTS[variant]
    emu = load_emulator(source, force_variant=variant)
    emu.reg.dpp[:] = [5, 5, 0, 3]
    for index in range(16):
        emu.reg.r[index] = 0x9000 + index * 23
    emu.reg.r[0] = 0xFA16
    emu.reg.mdl, emu.reg.mdh, emu.reg.mdc = 0x3456, 0x5678, 0
    # Native prologue selects its ISR bank; seed the captured tooth period.
    for address, value in ((context + 16, 1000), (context + 6, 0),
                           (context + 10, 0x3456), (0xFA4A, 255), (0xFA56, 255),
                           (flags, fdmode), (dwell, 4000), (0xFD12, fuel),
                           (0xFD30, fault), (0xFD32, fault2)):
        emu.mem.write_word_direct(address, value)
    for index in range(6):
        emu.mem.write_word_direct(factors + index * 2, 0x8000)
        emu.write_byte(offsets + index, correction & 255)
    for address, value in ((inverse, ordinary), (crank, 0), (stage, 0),
                           (0xFABC, 119), (0xFA80, cylinder), (0xFA5F, buffer),
                           (CUT_STATE_ADDR, marker), (CUT_RECOVERY_ADDR, 5), (CUT_TIMING_ADDR, old),
                           (latch, armed), (speed, road_speed), (0xE8D0, throttle),
                           (soft, 125), (rpm, rpm_value), (pins, input), (pin82, 0)):
        emu.write_byte(address, value)
    if hard >= 0xC000:
        emu.write_byte(hard, 206)
    for address in (0xE9D9, *range(0xE9CC, 0xE9D2)):
        emu.write_byte(address, 127 if address == knock else 128)
    emu.write_byte(CUT_SOURCE_ADDR, (1 if old in (0xC1, 0xC3) else 0) if owner is None else owner)
    for address, value in extra_bytes:
        emu.write_byte(address, value)
    architectural = (tuple(emu.reg.r), tuple(emu.reg.dpp), emu.reg.cp,
                     emu.reg.mdl, emu.reg.mdh, emu.reg.mdc)
    # Hardware interrupt frame: saved IP, CSP and PSW. RETI must consume it.
    emu.reg.sp = 0xFBFA
    for address, value in ((0xFBFA, 0x0234), (0xFBFC, 0), (0xFBFE, 0x0800)):
        emu.mem.write_word_direct(address, value)
    angles, transitions = [], []
    previous = tuple(emu.read(address) for address in range(0xFA46, 0xFA60, 2))
    def trace(pc, _opcode):
        nonlocal previous
        if pc in (hooks[0] + 6, hooks[1] + 6):
            angles.append((pc, emu.reg.r[2]))
        if timeline and entry <= pc < entry + 0xB00:
            current = tuple(emu.read(address) for address in range(0xFA46, 0xFA60, 2))
            if current != previous:
                transitions.append((pc, current))
                previous = current
    emu.cpu.set_trace(trace)
    result = emu.run_from(entry, stop_at=(0x234,), max_steps=2000)
    emu.cpu.set_trace(None)
    assert result.exit_reason == "stop_at" and result.final_pc == 0x234, (variant, result)
    assert len(angles) == 2, (variant, cylinder, buffer, angles)
    assert emu.reg.sp == 0xFC00 and emu.reg.psw.pack() == 0x0800
    assert (tuple(emu.reg.r), tuple(emu.reg.dpp), emu.reg.cp,
            emu.reg.mdl, emu.reg.mdh, emu.reg.mdc) == architectural, (variant, "native ISR ABI")
    # Both queued event banks, tooth/dwell data and native fuel ownership.
    outputs = (tuple(emu.read(address) for address in range(0xFA46, 0xFA60, 2)),
               emu.read(0xFD12), emu.read_byte(CUT_STATE_ADDR), emu.read_byte(latch),
               tuple(transitions))
    result = (tuple(angles), outputs, emu.read_byte(CUT_TIMING_ADDR))
    return (*result, emu) if return_emu else result


def verify_launch_retard(image, stock_image, variant, load_emulator, bind_image):
    """Check later-angle arbitration and unchanged downstream native events."""
    verify_launch_retard_artifacts(image, stock_image, variant)
    _, entry, context, hooks, _, inverse, dwell, factors, offsets, flags, crank, stage = LAYOUTS[variant]
    _, cal, soft, rpm, hard, speed, latch, pins, pin82 = STATE_LAYOUTS[variant]

    def configured(source, angle, **values):
        candidate = bytearray(source)
        settings = {cal: values.get("switch", 0), cal + 1: values.get("mode", 0),
                    cal + 2: 0, cal + 3: 125, cal + 5: 5, cal + 6: 100,
                    cal + 7: 128, cal + 13: angle}
        if hard < 0xC000:
            settings[hard] = 206
        for address, value in settings.items():
            candidate[(0x10000 + address) ^ 0x4000] = value
        candidate = bytes(checksum.correct_checksums(candidate)[0])
        assert all(checksum.checksum_status(candidate)[part] for part in ("boot", "program", "cal"))
        return bind_image(candidate, variant)

    def run(source, **values):
        return run_timing(source, variant, load_emulator, **values)

    count = 0
    disabled = configured(image, 255)
    original = configured(stock_image, 255)
    for ordinary in (80, 192, 232):
        for correction in (-8, 0, 8):
            for cylinder in range(6):
                values = dict(ordinary=ordinary, correction=correction, cylinder=cylinder)
                native, patched = run(original, **values), run(disabled, **values)
                assert patched[:2] == native[:2], (variant, "disabled parity", values)
                assert patched[2] == 0xC0
                count += 1

    for angle in (0, 27, 63):
        enabled = configured(image, angle)
        for cylinder in range(6):
            for buffer in (0, 1):
                for fdmode in (0x0400, 0x0500):
                    values = dict(cylinder=cylinder, buffer=buffer, fdmode=fdmode, timeline=True)
                    for ordinary, correction in ((80, 8), (248, -8), (0, -8), (255, 8), (255 - angle, 0)):
                        baseline = run(original, ordinary=ordinary, correction=correction, **values)
                        expected = max(255 - angle, baseline[0][0][1])
                        assert all(value == baseline[0][0][1] for _, value in baseline[0])
                        # Stock receives the arbitrated inverse angle; it owns every later event.
                        native = run(original, ordinary=expected, **values)
                        patched = run(enabled, ordinary=ordinary, correction=correction, **values)
                        assert all(value == expected for _, value in patched[0])
                        assert patched[:2] == native[:2], (variant, angle, ordinary, correction, values)
                        assert patched[2] == 0xC3
                        count += 1

    enabled = configured(image, 27)
    bypasses = [(f"input fallback {bit}", dict(fault=1 << bit), 0xC1) for bit in range(7)]
    bypasses += [(f"knock sensor {bit}", dict(fault2=1 << bit), 0xC1) for bit in (14, 15)]
    bypasses += [("disarmed", dict(armed=0), 0xC0),
                 ("speed release", dict(road_speed=5), 0xC0),
                 ("throttle release", dict(throttle=99), 0xC0),
                 ("fuel finished below entry", dict(rpm_value=124), 0xC0),
                 ("native priority", dict(marker=0xA8), 0xC0),
                 ("native hard reached before next limiter task", dict(rpm_value=206), 0xC0)]
    for name, values, expected in bypasses:
        native, patched = run(original, old=0xC3, **values), run(enabled, old=0xC3, **values)
        assert patched[:2] == native[:2], (variant, name)
        assert patched[2] == expected, (variant, name, hex(patched[2]))
        count += 1
    for address in (0xE9D9, *range(0xE9CC, 0xE9D2)):
        for ordinary, correction in ((80, 8), (248, -8)):
            baseline = run(original, ordinary=ordinary, correction=correction, knock=address)
            expected = max(228, baseline[0][0][1])
            native = run(original, ordinary=expected, knock=address)
            patched = run(enabled, ordinary=ordinary, correction=correction,
                          knock=address, return_emu=True)
            assert patched[:2] == native[:2] and patched[2] == 0xC3, (variant, address)
            assert all(patched[3].read_byte(cell) == (127 if cell == address else 128)
                       for cell in (0xE9D9, *range(0xE9CC, 0xE9D2)))
            count += 1
    for name, values in (("held fuel return", dict(old=0xC3, rpm_value=124, fuel=0x8000)),
                          ("protection cleared during fuel return", dict(old=0xC1, rpm_value=124, fuel=0x8000)),
                          ("unrelated fault retains the angle", dict(fault=0x0080, fault2=0x0200))):
        native = run(original, ordinary=228, **values)
        patched = run(enabled, ordinary=80, correction=8, **values)
        assert patched[:2] == native[:2] and patched[2] == 0xC3, (variant, name)
        count += 1
    for name, settings in (("invalid lower", dict(angle=64)), ("invalid upper", dict(angle=254)),
                           ("ignition mode", dict(angle=27, mode=1)),
                           ("switch off", dict(angle=27, switch=255))):
        candidate = configured(image, **settings)
        native, patched = run(original, old=0xC3), run(candidate, old=0xC3)
        assert patched[:2] == native[:2] and patched[2] == 0xC0, (variant, name)
        count += 1
    return count
