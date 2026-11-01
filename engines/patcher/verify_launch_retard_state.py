"""Exact installed-byte checks for the launch-only fixed-angle episode owner."""
from __future__ import annotations

from pathlib import Path

import checksum

CUT_STATE_ADDR = 0xD801
CUT_RECOVERY_ADDR = 0xD802
CUT_TIMING_ADDR = 0xD803
CUT_SOURCE_ADDR = 0xD804

LAYOUTS = {
    "1429861": (0x33040, 0x3020, 0xED52, 0xFAE6, 0x01D3, 0xEDF4, 0xFD80, 0xFD51, 0xFD50),
    "1437806": (0x3FE40, 0x3710, 0xF02C, 0xFC3C, 0x02DB, 0xF1BE, 0xFDB6, 0xFD61, 0xFD60),
    "1406464": (0x3E800, 0x352C, 0xF014, 0xFC3C, 0xDB87, 0xF19A, 0xFDB6, 0xFD61, 0xFD60),
    "SS1v2": (0x3E800, 0x47E0, 0xF014, 0xFC3C, 0xDB87, 0xF19A, 0xFDB6, 0xFD61, 0xFD60),
}


BODY_ENTRIES = {"1429861": 0x33200, "1437806": 0x06000, "1406464": 0x3EA00, "SS1v2": 0x3EA00}

def _qualification_pause_pc(image, variant):
    """Pause after live gates, before qualification/publication, for nesting."""
    family = {"1429861": "ms410", "1437806": "ms411", "1406464": "ms412", "SS1v2": "ms413"}[variant]
    patcher = Path(__file__).parent
    source = (patcher / f"ignition_cut_v18_{family}_qualification.asm").read_text()
    base = int(next(line.split()[1] for line in source.splitlines() if line.startswith("base ")), 16)
    call = bytes((0xDA, base >> 16, base & 255, (base >> 8) & 255))
    payload = bytes.fromhex((patcher / f"ignition_cut_v16_{family}_state.hex").read_text())
    assert image[BODY_ENTRIES[variant] ^ 0x4000:(BODY_ENTRIES[variant] ^ 0x4000) + len(payload)] == payload
    return BODY_ENTRIES[variant] + payload.index(call)


def verify_launch_retard_state(image, variant, load_emulator, bind_image):
    """Check lifetime, real input release, invalid settings and native priority."""
    entry, cal, soft, rpm_address, hard, speed, latch, pins, pin82 = LAYOUTS[variant]
    offset = entry ^ 0x4000
    for delta in (0, 10):
        target = BODY_ENTRIES[variant] + delta
        assert image[offset + delta:offset + delta + 4] == bytes((0xFA, target >> 16, target & 255, (target >> 8) & 255)), (
            variant, "UPDATE/CHECK trampolines and fixed CHECK entry")
    cases = [
        ("below first threshold", dict(rpm=124), 0xC0),
        ("at soft threshold", {}, 0xC3),
        ("above hard threshold", dict(rpm=129), 0xC3),
        ("hard below soft", dict(hard=120, rpm=120), 0xC3),
        ("below lower hard", dict(hard=120, rpm=119), 0xC0),
        ("automatic hard", dict(hard=255), 0xC3),
        ("automatic hard saturation", dict(soft=254, hard=255, native_hard=255, rpm=254), 0xC3),
        ("native hard priority", dict(hard=255, rpm=206), 0xC0),
        ("native hard priority while held", dict(old=0xC3, rpm=206), 0xC0),
        ("held through fuel return", dict(old=0xC3, rpm=124, fuel=0x8000), 0xC3),
        ("held fuel return with preemption", dict(old=0xC3, rpm=124, fuel=0x8000, preempt=True), 0xC3),
        ("B finished before A", dict(old=0xC3, rpm=124), 0xC0),
        ("zero stage while still at threshold", dict(old=0xC3), 0xC3),
        ("stock episode cannot start retard", dict(rpm=124, fuel=0x8000), 0xC0),
        ("protection temporarily suspends held episode", dict(old=0xC3, fault=1), 0xC1),
        ("protection cleared during held fuel return", dict(old=0xC1, rpm=124, fuel=0x8000), 0xC3),
        ("protection cleared after fuel returned", dict(old=0xC1, rpm=124), 0xC0),
        ("native priority", dict(marker=0xA8), 0xC0),
        ("invalid cut marker", dict(marker=0), 0xC0),
        ("uninitialized soft publication", dict(effective_soft=0), 0xC0),
        ("stale soft publication", dict(effective_soft=124, old=0xC3), 0xC0),
        ("native soft publication during entry", dict(effective_soft=203), 0xC3),
        ("native soft publication below entry", dict(effective_soft=203, rpm=124), 0xC0),
        ("native soft publication during recovery", dict(effective_soft=203, old=0xC3, rpm=124, fuel=0x8000), 0xC3),
        ("native soft reached during publication", dict(effective_soft=203, old=0xC3, rpm=203, fuel=0x8000), 0xC0),
        ("ignition mode", dict(mode=1, old=0xC3), 0xC0),
        ("invalid mode", dict(mode=255), 0xC0),
        ("not armed", dict(armed=0), 0xC0),
        ("max speed reached", dict(speed=5, old=0xC3), 0xC0),
        ("below throttle gate", dict(tps=99, old=0xC3), 0xC0),
        ("disabled", dict(angle=255, old=0xC3), 0xC0),
        ("zero degrees is enabled", dict(angle=63), 0xC3),
        ("latest supported angle", dict(angle=0), 0xC3),
        ("invalid angle low", dict(angle=64, old=0xC3), 0xC0),
        ("invalid angle high", dict(angle=254), 0xC0),
        ("switch off", dict(switch=255, old=0xC3), 0xC0),
        ("invalid switch", dict(switch=3), 0xC0),
        ("non-protective fault bit", dict(fault=0x80), 0xC3),
        ("unrelated second-bank fault", dict(fault2=0x0200), 0xC3),
        ("no accidental coil request", dict(marker=0xA1), 0xC3),
        ("read-only eligible", dict(check=True, old=0xC3, result=0xC3), 0xC3),
        ("read-only unpublished", dict(check=True, result=0xC0), 0xC0),
        ("read-only suspended", dict(check=True, old=0xC1, rpm=124, fuel=0x8000, result=0xC0), 0xC1),
        ("read-only protective veto", dict(check=True, old=0xC3, fault=1, result=0xC0), 0xC3),
        ("read-only unarmed", dict(check=True, old=0xC3, armed=0, result=0xC0), 0xC3),
        ("read-only fresh throttle release", dict(check=True, old=0xC3, tps=99, result=0xC0), 0xC3),
        ("read-only release preemption", dict(check=True, old=0xC3, rpm=124, fuel=0x8000, preempt_release=True, result=0xC0), 0xC0),
    ]
    cases += [(f"fallback bit {bit}", dict(fault=1 << bit), 0xC1) for bit in range(7)]
    cases += [(f"knock bank {bit}", dict(fault2=1 << bit), 0xC1) for bit in (14, 15)]
    cases += [(f"knock history {address:04X}", dict(knock=address), 0xC3)
              for address in (0xE9D9, *range(0xE9CC, 0xE9D2))]
    for switch, mask in ((1, 2), (2, 1), (4, 128)):
        for polarity in (0, 1):
            cases += [
                (f"switch {switch} polarity {polarity} asserted",
                 dict(switch=switch, polarity=polarity, input=0 if polarity else mask), 0xC3),
                (f"switch {switch} polarity {polarity} released",
                 dict(switch=switch, polarity=polarity, input=mask if polarity else 0, old=0xC3), 0xC0),
            ]

    for name, overrides, expected in cases:
        values = dict(soft=125, hard=128, native_hard=206, rpm=125, old=0xC0,
                      marker=0xA0, mode=0, angle=27, switch=0, polarity=0,
                      input=0, armed=0x40, speed=0, tps=192, fuel=0,
                      fault=0, fault2=0, knock=None)
        values.update(overrides)
        candidate = bytearray(image)
        for sa, value in ((cal, values["switch"]), (cal + 1, values["mode"]),
                          (cal + 2, values["polarity"]), (cal + 3, values["soft"]),
                          (cal + 5, 5), (cal + 6, 100), (cal + 7, values["hard"]),
                          (cal + 13, values["angle"])):
            candidate[(0x10000 + sa) ^ 0x4000] = value
        if hard < 0xC000:
            candidate[(0x10000 + hard) ^ 0x4000] = values["native_hard"]
        candidate = bytes(checksum.correct_checksums(candidate)[0])
        assert all(checksum.checksum_status(candidate)[key] for key in ("boot", "program", "cal"))
        candidate = bind_image(candidate, variant)
        emu = load_emulator(candidate, force_variant=variant)
        emu.reg.dpp[:] = [5, 5, 0, 3]
        for i in range(16):
            emu.reg.r[i] = 0x9000 + i * 23
        emu.reg.r[0] = 0xFA16
        emu.reg.psw.unpack(0x095F)
        emu.write_byte(CUT_STATE_ADDR, values["marker"])
        emu.write_byte(CUT_RECOVERY_ADDR, 0x05)
        emu.write_byte(CUT_TIMING_ADDR, values["old"])
        prior_owner = 1 if values["old"] in (0xC1, 0xC3) else 0
        emu.write_byte(CUT_SOURCE_ADDR, prior_owner)
        emu.write_byte(latch, values["armed"])
        emu.write_byte(speed, values["speed"])
        emu.write_byte(0xE8D0, values["tps"])
        emu.write_byte(soft, values.get("effective_soft", values["soft"]))
        emu.write_byte(rpm_address, values["rpm"])
        emu.write_byte(pins, values["input"] if values["switch"] in (1, 2) else 0)
        emu.write_byte(pin82, values["input"] if values["switch"] == 4 else 0)
        if hard >= 0xC000:
            emu.write_byte(hard, values["native_hard"])
        for address, value in ((0xFD12, values["fuel"]), (0xFD30, values["fault"]),
                               (0xFD32, values["fault2"])):
            emu.write(address, value)
        for address in (0xE9D9, *range(0xE9CC, 0xE9D2)):
            emu.write_byte(address, 127 if address == values["knock"] else 128)
        preserved = (tuple(emu.reg.r), tuple(emu.reg.dpp), emu.reg.cp, emu.reg.psw.pack())
        native = tuple(emu.read_byte(address) for address in
                       (CUT_STATE_ADDR, CUT_RECOVERY_ADDR, latch, soft - 1, soft, soft + 1, soft + 2))
        fuel = emu.read(0xFD12)
        emu.reg.sp = 0xFBFC
        emu.mem.write_word_direct(0xFBFC, 0xE000)
        emu.mem.write_word_direct(0xFBFE, 0)
        if values.get("preempt_release"):
            resume = _qualification_pause_pc(candidate, variant)
            interrupted = emu.run_from(entry + 10, stop_at=(resume,), max_steps=100)
            assert interrupted.exit_reason == "stop_at" and interrupted.final_pc == resume
            assert emu.read_byte(CUT_TIMING_ADDR) == 0xC3
            emu.write_byte(0xE8D0, 99)
            outer_sp = emu.reg.sp
            emu.cpu.push_word(3)
            emu.cpu.push_word(0xF000)
            nested = emu.run_from(entry, stop_at=(0x3F000,), max_steps=1000)
            assert nested.exit_reason == "stop_at" and nested.final_pc == 0x3F000
            assert emu.reg.sp == outer_sp and emu.read_byte(CUT_TIMING_ADDR) == 0xC0
            observed = []
            emu.cpu.set_trace(lambda _pc, _opcode: observed.append(emu.read_byte(CUT_TIMING_ADDR)))
            result = emu.run_from(resume, stop_at=(0xE000,), max_steps=1000)
            emu.cpu.set_trace(None)
            assert observed and set(observed) == {0xC0}, (variant, "read-only caller resurrected released state", observed)
        elif values.get("preempt"):
            observed = []
            emu.cpu.set_trace(lambda _pc, _opcode: observed.append(emu.read_byte(CUT_TIMING_ADDR)))
            resume = _qualification_pause_pc(candidate, variant)
            interrupted = emu.run_from(entry, stop_at=(resume,), max_steps=100)
            assert interrupted.exit_reason == "stop_at" and interrupted.final_pc == resume
            assert emu.read_byte(CUT_TIMING_ADDR) == 0xC3, (variant, "false episode release before preemption")
            outer_sp = emu.reg.sp
            emu.cpu.push_word(3)
            emu.cpu.push_word(0xF000)
            nested = emu.run_from(entry, stop_at=(0x3F000,), max_steps=1000)
            assert nested.exit_reason == "stop_at" and nested.final_pc == 0x3F000
            assert emu.reg.sp == outer_sp and emu.read_byte(CUT_TIMING_ADDR) == 0xC3
            result = emu.run_from(resume, stop_at=(0xE000,), max_steps=1000)
            emu.cpu.set_trace(None)
            assert observed and set(observed) <= {0xC1, 0xC3}, (variant, "episode lost during nested timing evaluation", observed)
        else:
            result = emu.run_from(entry + (10 if values.get("check") else 0), stop_at=(0xE000,), max_steps=1000)
        assert result.exit_reason == "stop_at" and result.final_pc == 0xE000, (variant, name, result)
        assert emu.reg.sp == 0xFC00
        registers = list(preserved[0])
        returned = values["result"] if values.get("check") else (values["angle"] if expected == 0xC3 else 0xFF)
        registers[4] = (registers[4] & 0xFF00) | returned
        preserved = (tuple(registers), *preserved[1:])
        expected_owner = (0 if values.get("preempt_release") else prior_owner) if values.get("check") else (1 if expected in (0xC1, 0xC3) else 0)
        assert emu.read_byte(CUT_SOURCE_ADDR) == expected_owner, (variant, name, "fuel episode owner")
        assert (tuple(emu.reg.r), tuple(emu.reg.dpp), emu.reg.cp, emu.reg.psw.pack()) == preserved, (variant, name, "ABI")
        assert emu.read_byte(CUT_TIMING_ADDR) == expected, (variant, name, hex(emu.read_byte(CUT_TIMING_ADDR)), hex(expected))
        assert tuple(emu.read_byte(address) for address in
                     (CUT_STATE_ADDR, CUT_RECOVERY_ADDR, latch, soft - 1, soft, soft + 1, soft + 2)) == native
        assert emu.read(0xFD12) == fuel
    return len(cases)


def verify_native_soft_publication(gate, version, installed_image):
    """Interrupt native soft publication with the full ignition ISR and RETI.

    The native task publishes its own soft threshold before launch lowers it.
    That temporary value must neither lose a held launch episode nor hide a
    native soft bound that has actually been reached.
    """
    from engines.patcher.verify_launch_retard import LAYOUTS as TIMING_LAYOUTS

    variant = gate._bound_variant(installed_image)
    state_entry, _, soft, rpm, *_ = LAYOUTS[variant]
    (_, isr, context, hooks, _, inverse, dwell, factors,
     offsets, flags, crank, _) = TIMING_LAYOUTS[variant]
    patch_id = {"MS41.0": "launch_control_v18_ms410", "MS41.1": "launch_control_v18_ms411",
                "MS41.2": "launch_control_v18_ms412", "MS41.3": "launch_control_v18"}[version]
    descriptor = gate.PATCHES[patch_id]
    publication_gap = {"1429861": 0x20726, "1437806": 0x207EC,
                       "1406464": 0x207E8, "SS1v2": 0x207E8}[variant]
    assert any(splice["off"] == publication_gap ^ 0x4000 for splice in descriptor["cave"]["splices"])

    def configured(values):
        image = bytearray(installed_image)
        for name, value in values.items():
            image[descriptor["cave"]["cals"][name]] = value
        return gate._bind_image(bytes(checksum.correct_checksums(image)[0]), variant)

    values = dict(LC_SW=0, LC_CUTTYPE=0, LC_MAXRPM=125, LC_HARDRPM=128,
                  LC_FUEL_HYST_A=6, LC_FUEL_HYST_B=2, LC_IGN_ANGLE=27,
                  LC_ARMSPEED=1, LC_MAXSPEED=5, LC_MINTPS=100)
    for interrupted_rpm, wanted_angle, wanted_state in ((124, 228, 0xC3), (203, 192, 0xC0)):
        emu, native = gate._native_fuel_fixture(configured, values)
        for address in (0xE9D9, *range(0xE9CC, 0xE9D2)):
            emu.write_byte(address, 128)
        emu.write(0xFD32, 0)
        for _ in range(8):
            gate._native_fuel_step(emu, native, 128)
        gate._call_native(emu, state_entry)
        assert emu.read_byte(CUT_TIMING_ADDR) == 0xC3
        gate._native_fuel_step(emu, native, 124)
        gate._call_native(emu, state_entry)
        assert emu.read_byte(CUT_TIMING_ADDR) == 0xC3 and emu.read(0xFD12) & 0x8000
        for address, value in ((context + 16, 1000), (context + 6, 0), (context + 10, 0x3456),
                               (0xFA4A, 255), (0xFA56, 255), (flags, 0x0400), (dwell, 4000)):
            emu.mem.write_word_direct(address, value)
        for address, value in ((inverse, 192), (crank, 0), (0xFABC, 119),
                               (0xFA80, 0), (0xFA5F, 0), (rpm, interrupted_rpm)):
            emu.write_byte(address, value)
        for index in range(6):
            emu.mem.write_word_direct(factors + index * 2, 0x8000)
            emu.write_byte(offsets + index, 0)
        emu.reg.r[0], emu.reg.sp = 0xFA16, 0xFBFC
        emu.reg.dpp[:] = [4, 5, 0, 3]
        emu.reg.psw.unpack(0x0800)
        emu.mem.write_word_direct(0xFBFC, 0xE000)
        emu.mem.write_word_direct(0xFBFE, 0)
        result = emu.run_from(native[1], stop_at=publication_gap, max_steps=1000)
        assert result.final_pc == publication_gap and emu.read_byte(soft) == 203
        assert emu.read_byte(CUT_TIMING_ADDR) == 0xC3 and emu.reg.psw.pack() & 0x0800
        preserved = (emu.reg.sp, emu.reg.cp, tuple(emu.reg.r), tuple(emu.reg.dpp), emu.reg.psw.pack())
        emu.cpu.push_word(emu.reg.psw.pack())
        emu.cpu.push_word(publication_gap >> 16)
        emu.cpu.push_word(publication_gap & 0xFFFF)
        angles = []

        def trace(pc, _opcode):
            if pc in (hooks[0] + 6, hooks[1] + 6):
                angles.append(emu.reg.r[2])

        emu.cpu.set_trace(trace)
        result = emu.run_from(isr, stop_at=publication_gap, max_steps=3000)
        emu.cpu.set_trace(None)
        assert result.final_pc == publication_gap and angles == [wanted_angle, wanted_angle], (
            variant, "interrupt during native soft publication", interrupted_rpm, angles)
        assert emu.read_byte(CUT_TIMING_ADDR) == wanted_state
        assert preserved == (emu.reg.sp, emu.reg.cp, tuple(emu.reg.r), tuple(emu.reg.dpp), emu.reg.psw.pack())
        result = emu.run_from(publication_gap, stop_at=0xE000, max_steps=3000)
        assert result.final_pc == 0xE000 and emu.read_byte(soft) == 125
        assert emu.read(0xFD12) & 0x8000
        gate._call_native(emu, state_entry)
        assert emu.read_byte(CUT_TIMING_ADDR) == wanted_state, (variant, "state after native publication")
    return 2
