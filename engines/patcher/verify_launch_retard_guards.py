"""Native diagnostic transactions for the optional launch fuel timing override.


Called by the private admission owner with its canonical emulator and fixtures.
This module neither opens a transport nor substitutes a diagnostic model.
"""

from engines.patcher.verify_launch_retard_state import (
    CUT_STATE_ADDR, CUT_RECOVERY_ADDR, CUT_TIMING_ADDR, CUT_SOURCE_ADDR,
)

_LAYOUTS = {
    "MS41.0": ("launch_control_v18_ms410", 0xED52, 0xFAE6, 0xEDF4, 0xFD80, 0x33040),
    "MS41.1": ("launch_control_v18_ms411", 0xF02C, 0xFC3C, 0xF1BE, 0xFDB6, 0x3FE40),
    "MS41.2": ("launch_control_v18_ms412", 0xF014, 0xFC3C, 0xF19A, 0xFDB6, 0x3E800),
    "MS41.3": ("launch_control_v18", 0xF014, 0xFC3C, 0xF19A, 0xFDB6, 0x3E800),
}


def _configured(gate, version, image):
    candidate = bytearray(image)
    cals = gate.PATCHES[_LAYOUTS[version][0]]["cave"]["cals"]
    # Hard below soft, fuel stage zero: timing can legitimately outlive cutting.
    values = dict(LC_SW=0, LC_CUTTYPE=0, LC_CLUTCHPOL=0, LC_MAXRPM=125,
                  LC_MAXSPEED=5, LC_MINTPS=0xA0, LC_HARDRPM=120,
                  LC_IGN_ANGLE=27)
    for name, value in values.items():
        candidate[cals[name]] = value
    candidate, _ = gate.checksum.correct_checksums(candidate)
    return gate._bind_image(candidate, gate._bound_variant(image))


def _seed(version, emu, *, marker=0xC3):
    _, soft, rpm, speed, latch, _ = _LAYOUTS[version]
    emu.write_byte(CUT_STATE_ADDR, 0xA0)
    emu.write_byte(CUT_RECOVERY_ADDR, 0)
    emu.write_byte(CUT_TIMING_ADDR, marker)
    emu.write_byte(CUT_SOURCE_ADDR, 1 if marker in (0xC1, 0xC3) else 0)
    emu.write_byte(latch, 0x40)
    emu.write_byte(soft, 125)
    emu.write_byte(rpm, 121)
    if version in ("MS41.2", "MS41.3"):
        emu.write_byte(0xDB87, 206)  # native published hard ceiling (RAM on these families)
    emu.write_byte(speed, 0)
    emu.write_byte(0xE8D0, 0xC0)
    emu.write(0xFD12, 0)
    emu.write(0xFD30, 0)
    emu.write(0xFD32, 0)
    emu.write_byte(0xE9D9, 0x80)
    for address in range(0xE9CC, 0xE9D2):
        emu.write_byte(address, 0x80)
    return emu


def verify_retard_guards(gate, version, installed_image):
    """Prove retard-only eligibility, complete native calls and normal recovery."""
    image = _configured(gate, version, installed_image)
    layout = gate.CUT_GUARD_LAYOUTS[version]
    banks = layout["stft"][3]
    monitors = gate._CUT_MONITOR_LAYOUTS[version]
    pending = 3 if version == "MS41.0" else 15
    control = {}
    if version in gate.OLDER_FEATURE_LAYOUTS:
        older = gate.OLDER_FEATURE_LAYOUTS[version]
        control = dict(hook=older["control_hook"], rpm_address=older["rpm_address"],
                       input_bytes=older["input_bytes"], ipw_addresses=older["ipw_addresses"])
    for initial, expected in ((0, 0xC0), (0xA0, 0xC3)):
        emu = _seed(version, gate._emu(image, dpp0=4))
        _, _, hygiene = gate._execute_control(emu, rpm=121, state=initial, **control)
        assert hygiene and emu.read_byte(CUT_TIMING_ADDR) == expected, (
            version, "launch timing state initializes once and survives regular control", initial)

    for index, bank in enumerate(banks):
        for caller in gate._native_stft_callers(version):
            reference = gate._native_stft_case(gate._cut_reference(version), caller, bank, 0xA0)
            emu = _seed(version, gate._emu(image, dpp0=4))
            emu.reg.r[:] = [0x1100 + register * 0x101 for register in range(16)]
            emu.reg.r[0], emu.reg.sp = 0xE600, 0xFC00
            emu.reg.r[8], emu.reg.r[9] = bank, 0x100
            emu.write(bank + 6, 0x9000)
            emu.write(bank + 12, 0x7000)
            emu.write(bank + 14, 0xA000)
            gate._native_stft_case(image, caller, bank, 0xA0, emu=emu)
            assert emu.read(bank + 6) == 0x9000 and emu.read_byte(CUT_RECOVERY_ADDR) == pending, (
                version, "retard-only STFT freeze", caller, bank)
            assert emu.read_byte(CUT_STATE_ADDR) == 0xA0 and emu.read_byte(CUT_TIMING_ADDR) == 0xC3
            emu.write_byte(_LAYOUTS[version][4], 0)
            gate._native_stft_case(image, caller, bank, 0xA0, emu=emu)
            assert emu.read(bank + 6) == 0x9000 and emu.read_byte(CUT_TIMING_ADDR) == 0xC3, (
                version, "diagnostic disarm recheck never publishes timing state")
            gate._call_native(emu, _LAYOUTS[version][5])
            assert emu.read_byte(CUT_TIMING_ADDR) == 0xC0, (version, "timing owner publishes disarm")
            emu.reg.r[12], emu.reg.r[13] = 0x55, bank
            gate._call_native(emu, monitors["front"])
            gate._native_stft_case(image, caller, bank, 0xA0, emu=emu)
            assert emu.read(bank + 6) == reference.read(bank + 6), (
                version, "retard STFT accepted-sample recovery", caller, bank)

        # The full native learning transaction must freeze coherently; its
        # genuine-fault fallback still resets adaptation through the stock path.
        emu = gate._seed_cut_learning(image, version, bank, 0xA0)
        _seed(version, emu)
        before = gate._trim_state(emu, bank)
        gate._call_native(emu, gate._CUT_LEARNING_LAYOUTS[version][0])
        assert gate._trim_state(emu, bank) == before, (version, "retard learning freeze", bank)
        emu.write(0xFD30, 2)
        gate._call_native(emu, gate._CUT_LEARNING_LAYOUTS[version][0])
        assert emu.read(bank + 0x10) == emu.read(bank + 0x18) == 0x8000, (
            version, "retard does not hide native learning fault reset", bank)

        for kind in ("front", "rear"):
            entry = monitors[kind]
            if entry is None:
                continue
            emu = _seed(version, gate._emu(image, dpp0=4))
            output = bank + (0x42 if kind == "rear" else 0x2B)
            own_bit = 1 << (index + (2 if kind == "rear" else 0))
            emu.write_byte(output, 0x17)
            emu.write_byte(bank + 0x41, 0x55)
            emu.reg.r[12] = bank if kind == "rear" else 0x55
            emu.reg.r[13] = bank
            gate._call_native(emu, entry)
            assert (emu.read_byte(output), emu.read_byte(CUT_RECOVERY_ADDR)) == (0x17, pending), (
                version, "retard rejects contaminated accepted sample", kind, bank)
            emu.write_byte(_LAYOUTS[version][4], 0)
            emu.reg.r[12] = bank if kind == "rear" else 0x55
            emu.reg.r[13] = bank
            gate._call_native(emu, entry)
            assert (emu.read_byte(output), emu.read_byte(CUT_RECOVERY_ADDR)) == (0x17, pending), (
                version, "sample publication waits for the timing owner to release C3", kind, bank)
            assert emu.read_byte(CUT_TIMING_ADDR) == 0xC3, (
                version, "fresh observation check leaves previous timing publication intact")
            gate._call_native(emu, _LAYOUTS[version][5])
            assert emu.read_byte(CUT_TIMING_ADDR) == 0xC0
            emu.reg.r[12] = bank if kind == "rear" else 0x55
            emu.reg.r[13] = bank
            gate._call_native(emu, entry)
            assert (emu.read_byte(output), emu.read_byte(CUT_RECOVERY_ADDR)) == (0x55, pending & ~own_bit), (
                version, "retard accepts fresh native sample after published release", kind, bank)

    # Electrical coil/resistor eligibility is unchanged even while the angle is
    # actually overridden. MS41.0 also retains this shared roughness monitor.
    hook, cleanup, continuation = layout["diagnostics"][0]
    emu = _seed(version, gate._emu(image, dpp0=4))
    _, result, final, sp = gate._guard_route(
        image, hook, 0xA0, (cleanup, continuation), emu=emu)
    assert final == continuation and result.exit_reason == "stop_at" and emu.reg.sp == sp, (
        version, "retard-only electrical coil diagnostics remain native", result)
    assert emu.read_byte(CUT_RECOVERY_ADDR) == 0

    # Off/unknown state cannot call an absent launch helper or forge a cut.
    # A held, previously vetoed episode cannot suppress observations before a
    # timing event actually publishes a qualified override.
    for marker in (0, 0xC0, 0xC2, 0xC4, 0xFF, 0xC1):
        emu = _seed(version, gate._emu(image, dpp0=4), marker=marker)
        emu.reg.r[12], emu.reg.r[13] = 0x55, banks[0]
        gate._call_native(emu, monitors["front"])
        assert emu.read_byte(CUT_RECOVERY_ADDR) == 0 and emu.read_byte(CUT_TIMING_ADDR) == marker, (
            version, "only a published actual override can suppress observations", marker)
        assert emu.read_byte(CUT_STATE_ADDR) == 0xA0
        if marker == 0xC1:
            gate._call_native(emu, _LAYOUTS[version][5])
            assert emu.read_byte(CUT_TIMING_ADDR) == 0xC3
            emu.reg.r[12], emu.reg.r[13] = 0x55, banks[0]
            gate._call_native(emu, monitors["front"])
            assert emu.read_byte(CUT_RECOVERY_ADDR) == pending

    # Faults release observation; retained history must not veto the V18 angle.
    protection_cases = [(0xFD30, 1, True), (0xFD32, 0x8000, True)]
    protection_cases += [(address, 0x7F, False)
                         for address in (0xE9D9, *range(0xE9CC, 0xE9D2))]
    for address, value, veto in protection_cases:
        emu = _seed(version, gate._emu(image, dpp0=4))
        if address in (0xFD30, 0xFD32):
            emu.write(address, value)
        else:
            emu.write_byte(address, value)
        emu.reg.r[0], emu.reg.r[8], emu.reg.r[9] = 0xE600, banks[0], 0x100
        emu.write(banks[0] + 6, 0x9000)
        emu.write(banks[0] + 12, 0x7000)
        emu.write(banks[0] + 14, 0xA000)
        gate._native_stft_case(image, gate._native_stft_callers(version)[0], banks[0], 0xA0, emu=emu)
        assert emu.read_byte(CUT_TIMING_ADDR) == 0xC3 and emu.read_byte(CUT_RECOVERY_ADDR) == (0 if veto else pending), (
            version, "only current faults release observation without publishing timing state", address)
        if not veto:
            assert emu.read_byte(address) == value and emu.read(banks[0] + 6) == 0x9000, (
                version, "retained knock history keeps retard-only observations frozen", address)
        gate._call_native(emu, _LAYOUTS[version][5])
        assert emu.read_byte(CUT_TIMING_ADDR) == (0xC1 if veto else 0xC3), (
            version, "timing owner suspends for faults and retains the history-constrained episode", address)

    if version != "MS41.0":
        _verify_misfire(gate, version, image)
        pending_entry = 0x3F89E if version == "MS41.1" else 0x3E7BA
        for marker in (0, 0xFF, 0xB0):
            emu = _seed(version, gate._emu(image, dpp0=4))
            emu.write_byte(CUT_STATE_ADDR, marker)
            emu.write_byte(CUT_RECOVERY_ADDR, 0xFF)
            gate._call_native(emu, pending_entry)
            assert emu.reg.r[4] == 0 and emu.read_byte(CUT_RECOVERY_ADDR) == 0xFF, (
                version, "unknown startup RAM cannot create a pending misfire episode", marker)
    _verify_short_episode(gate, version, image)
    _verify_sample_irq(gate, version, image)


def _timing_fixture(gate, version, image):
    """Seed the actual native timing ISR for foreground/interrupt interactions."""
    from engines.patcher.verify_launch_retard import LAYOUTS

    variant = gate._bound_variant(image)
    (_, isr, context, hooks, _, inverse, dwell, factors, offsets,
     flags, crank, stage) = LAYOUTS[variant]
    emu = _seed(version, gate._emu(image, dpp0=5), marker=0xC0)
    for address, value in ((inverse, 192), (crank, 0), (stage, 0),
                           (0xFABC, 119), (0xFA80, 0), (0xFA5F, 0)):
        emu.write_byte(address, value)
    for address, value in ((context + 16, 1000), (context + 6, 0),
                           (context + 10, 0x3456), (0xFA4A, 255),
                           (0xFA56, 255), (flags, 0x0400), (dwell, 4000)):
        emu.mem.write_word_direct(address, value)
    for index in range(6):
        emu.mem.write_word_direct(factors + index * 2, 0x8000)
        emu.write_byte(offsets + index, 0)
    return emu, isr, hooks


def _verify_sample_irq(gate, version, image):
    """Real T4 arbitration cannot lose freshness at sample check/clear/store."""
    recovery = {"MS41.0": 0x33000, "MS41.1": 0x3F700,
                "MS41.2": 0x3E500, "MS41.3": 0x3E500}[version]
    for kind in ("front", "rear"):
        entry = gate._CUT_MONITOR_LAYOUTS[version][kind]
        if entry is None:
            continue
        wrapper = recovery + (46 if kind == "rear" else 0)
        offset = wrapper ^ 0x4000
        assert image[offset:offset + 6] == bytes.fromhex("ec88be88ecf4")
        assert image[offset + 26:offset + 34] == (
            bytes.fromhex("65f8") + CUT_RECOVERY_ADDR.to_bytes(2, "little")
            + bytes.fromhex("fcf4fc88"))
        for bank in gate.CUT_GUARD_LAYOUTS[version]["stft"][3]:
            for delta in (0, 2, 4, 26, 32, 34):
                emu, isr, hooks = _timing_fixture(gate, version, image)
                emu.write_byte(bank + 0x41, 0x55)
                emu.reg.r[12], emu.reg.r[13] = bank if kind == "rear" else 0x55, bank
                emu.reg.psw.unpack(0x0800)
                emu.mem.write_word_direct(0xFF64, 0x40 | (7 << 2))  # T4IE, priority 7
                requested, entered, angles = False, False, []

                def trace(pc, _opcode):
                    nonlocal requested, entered
                    if pc == wrapper + delta and not requested:
                        requested = True
                        emu.interrupts.request("T4")
                    if pc == isr:
                        entered = True
                    if pc in (hooks[0] + 6, hooks[1] + 6):
                        angles.append(emu.reg.r[2])

                emu.cpu.set_trace(trace)
                gate._call_native(emu, entry)
                emu.cpu.set_trace(None)
                assert requested and entered and angles == [228, 228], (version, kind, delta, angles)
                assert len(emu.interrupts.entries) == 1 and emu.interrupts.entries[0][1] == "T4"
                assert emu.read_byte(CUT_RECOVERY_ADDR) == (3 if version == "MS41.0" else 0x1F), (
                    version, kind, delta, "accepted sample erased interrupt freshness")
                assert emu.reg.psw.IEN and emu.reg.sp == 0xFC00


def _verify_short_episode(gate, version, image):
    """A real override/release between foreground tasks must leave recovery due."""
    emu, isr, hooks = _timing_fixture(gate, version, image)

    def timing_event():
        emu.reg.sp = 0xFBFA
        for address, value in ((0xFBFA, 0x234), (0xFBFC, 0), (0xFBFE, 0x0800)):
            emu.mem.write_word_direct(address, value)
        angles = []
        def trace(pc, _opcode):
            if pc in (hooks[0] + 6, hooks[1] + 6):
                angles.append(emu.reg.r[2])
        emu.cpu.set_trace(trace)
        result = emu.run_from(isr, stop_at=0x234, max_steps=2500)
        emu.cpu.set_trace(None)
        assert result.final_pc == 0x234 and emu.reg.sp == 0xFC00
        return angles

    assert timing_event() == [228, 228] and emu.read_byte(CUT_TIMING_ADDR) == 0xC3
    pending = 3 if version == "MS41.0" else 0x1F
    assert emu.read_byte(CUT_RECOVERY_ADDR) == pending, (version, "actual timing marks recovery")
    emu.write_byte(0xE8D0, 99)
    assert timing_event() == [192, 192] and emu.read_byte(CUT_TIMING_ADDR) == 0xC0
    assert emu.read_byte(CUT_RECOVERY_ADDR) == pending

    bank = gate.CUT_GUARD_LAYOUTS[version]["stft"][3][0]
    caller = gate._native_stft_callers(version)[0]
    emu.reg.r[0], emu.reg.r[8], emu.reg.r[9] = 0xE600, bank, 0x100
    emu.write(bank + 6, 0x9000)
    emu.write(bank + 12, 0x7000)
    emu.write(bank + 14, 0xA000)
    gate._native_stft_case(image, caller, bank, 0xA0, emu=emu)
    assert emu.read(bank + 6) == 0x9000, (version, "short retard awaits fresh sample")
    emu.reg.r[12], emu.reg.r[13] = 0x55, bank
    gate._call_native(emu, gate._CUT_MONITOR_LAYOUTS[version]["front"])
    gate._native_stft_case(image, caller, bank, 0xA0, emu=emu)
    assert emu.read(bank + 6) == 0x8F00, (version, "fresh sample resumes native trim")

    if version == "MS41.0":
        return
    (entry, stop, baseline, thresholds, enable, warmup, opportunities,
     counts, mf_flags, shutdown) = gate._CUT_MISFIRE_LAYOUTS[version]
    recovery = 0xF560 if version == "MS41.1" else 0xF53A
    emu.reg.r[0], emu.reg.r[9] = 0xE600, 0
    for address, value in ((warmup, 0), (enable, 0x3F), (0xE8E5, 255),
                           (mf_flags, 0xA5), (shutdown, 0x12), (0xFC35, 0x12)):
        emu.write_byte(address, value)
    emu.write(baseline, 0)
    emu.write(opportunities, 7)
    for index in range(6):
        emu.write(baseline + 2 + 2 * index, 1000)
        emu.write(thresholds + 2 * index, 0)
        emu.write(counts + 2 * index, 10 + index)
    result = emu.run_from(entry, stop_at=stop, max_steps=3000)
    assert result.final_pc == stop
    assert emu.read(opportunities) == 7 and tuple(
        emu.read(counts + 2 * index) for index in range(6)) == tuple(range(10, 16)), (
            version, "short retard cannot enter delayed native fault counts")
    assert gate._ram_bytes(emu, recovery, 6) == bytes([2] * 6)
    assert not emu.read_byte(CUT_RECOVERY_ADDR) & 0x10


def _verify_misfire(gate, version, image):
    """Execute the delayed native cylinder counter, preserving prior evidence."""
    entry, stop, baseline, thresholds, enable, warmup, opportunities, counts, flags, shutdown = (
        gate._CUT_MISFIRE_LAYOUTS[version])
    for cylinder in range(6):
        emu = _seed(version, gate._emu(image, dpp0=4))
        emu.reg.r[0], emu.reg.r[9] = 0xE600, cylinder
        emu.write_byte(warmup, 0)
        emu.write_byte(enable, 0x3F)
        emu.write_byte(0xE8E5, 255)
        emu.write(baseline, 0)
        emu.write(opportunities, 7)
        emu.write_byte(flags, 0xA5)
        emu.write_byte(shutdown, 0x12)
        emu.write_byte(0xFC35, 0x12)
        for index in range(6):
            emu.write(baseline + 2 + 2 * index, 1000)
            emu.write(thresholds + 2 * index, 0)
            emu.write(counts + 2 * index, 10 + index)
        result = emu.run_from(entry, stop_at=stop, max_steps=3000)
        assert result.exit_reason == "stop_at" and result.final_pc == stop
        assert emu.read(opportunities) == 7 and tuple(
            emu.read(counts + 2 * index) for index in range(6)) == tuple(range(10, 16)), (
                version, "retard-only native misfire counters frozen", cylinder)
        assert (emu.read_byte(flags), emu.read_byte(shutdown), emu.read_byte(0xFC35)) == (0xA5, 0x12, 0x12)
        assert not emu.read_byte(enable) & (1 << cylinder)
        assert emu.read(thresholds + 2 * cylinder) == 0x8000
        # Use the same machine for all firing events: a short retard episode
        # must not resume the delayed counter before each cylinder recovers.
        recovery = 0xF560 if version == "MS41.1" else 0xF53A
        assert gate._ram_bytes(emu, recovery, 6) == bytes([2] * 6)
        emu.write_byte(_LAYOUTS[version][4], 0)
        gate._call_native(emu, _LAYOUTS[version][5])
        assert emu.read_byte(CUT_TIMING_ADDR) == 0xC0
        for event in range(24):
            emu.reg.r[9] = (cylinder + event + 1) % 6
            result = emu.run_from(entry, stop_at=stop, max_steps=3000)
            assert result.exit_reason == "stop_at" and result.final_pc == stop
            if event < 11:
                assert emu.read(opportunities) == 7 and tuple(
                    emu.read(counts + 2 * index) for index in range(6)) == tuple(range(10, 16)), (
                        version, "native retard misfire recovery remains pending", cylinder, event)
            assert (emu.read_byte(shutdown), emu.read_byte(0xFC35)) == (0x12, 0x12)
        assert gate._ram_bytes(emu, recovery, 6) == bytes(6)
        assert emu.read(opportunities) > 7 and sum(
            emu.read(counts + 2 * index) for index in range(6)) > sum(range(10, 16)), (
                version, "genuine native misfire detection resumes", cylinder)
