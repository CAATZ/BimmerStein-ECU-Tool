"""Focused native charge, staged injection and BMW 214 controls for the V20 pair."""

from . import verify_fixed_ipw as fuel
from . import verify_knock_guard as knock
from .v20_launch import LAYOUT
from .verify_v20_diagnostics import fault_memory
from .verify_v20_recovery import verify_released_fuel


def _calibrate(gate, family, image, values):
    suffix = "" if family == "MS41.3" else "_ms41" + family[-1]
    cals = {}
    for kind in ("ignition_cut", "launch_control"):
        cals.update(gate.PATCHES[kind + "_v20" + suffix]["cave"]["cals"])
    result = bytearray(image)
    for key, value in values.items():
        addr = cals[key]
        width = 2 if key.endswith("IPW") else 1
        result[addr : addr + width] = value.to_bytes(width, "little")
    result, _ = gate.checksum.correct_checksums(result, correct_program=True)
    return gate._bind_image(result, gate._bound_variant(image))


def _charge(gate, family, emu, image, rpm):
    """Publish the new speed with stock period conversion, then service genuine CC6."""
    variant = gate._bound_variant(image)
    mirror = gate._NATIVE_FUEL_LAYOUTS[variant][5]
    write = bytes.fromhex("f3fa1efff7fa") + mirror.to_bytes(2, "little")
    calc = bytes.fromhex("f2f574fae60780c3e606c901")
    assert image.count(write) == image.count(calc) == 1
    start, end = (image.index(calc) ^ 0x4000), ((image.index(write) + 8) ^ 0x4000)
    assert 0 < end - start < 68
    emu.write(0xFD02, emu.read(0xFD02) & ~8)
    emu.write(0xFA74, 30000000 // (rpm * 32))
    knock._run(emu, start, end)
    assert emu.read_byte(mirror) == rpm
    emu.write_byte(0xFA5F, 0)
    emu.write(fuel.LAYOUTS[family]["flags"], 0)
    emu.write_byte(0xFF04, 0xFF)
    gate._verify_cc6_interrupt_entry(emu, variant)
    emu.cpu.set_trace(None)
    return emu.read_byte(0xFF04) == 0xFF


def _crossings(gate, family, candidate, *, launch, hyst):
    variant = gate._bound_variant(candidate)
    speed = gate._NATIVE_FUEL_LAYOUTS[variant][2]
    rpm = gate._NATIVE_FUEL_LAYOUTS[variant][5]
    latch = gate._launch_latch(candidate)
    emu, ctx = gate._native_fuel_fixture(lambda _: candidate, {})
    emu.interrupts.enabled = True
    switch = 0xFD50 if family == "MS41.0" else 0xFD60
    emu.write(switch, 0x0200)
    emu.write_byte(latch, 0)
    emu.write_byte(rpm, 124)
    gate._call_native(emu, speed)
    gate._native_fuel_step(emu, ctx, 124)
    assert not emu.read_byte(0xD802) & 0x3F
    for current, wanted in ((124, False), (125, True), (124, bool(hyst)), (121, False)):
        assert _charge(gate, family, emu, candidate, current) == wanted, (family, launch, hyst, current)
        assert bool(emu.read_byte(0xD802) & 32) == wanted
        assert bool(emu.read_byte(0xD801) & (2 if launch else 1)) == wanted
    if launch:
        emu.write_byte(0xE8D0, 0)
        gate._call_native(emu, speed)
        gate._native_fuel_step(emu, ctx, 125)
    else:
        emu.write(switch, 0)
    assert not _charge(gate, family, emu, candidate, 130)


def verify_standalone(gate, family, image):
    for hyst in (0, 3):
        candidate = _calibrate(gate, family, image, dict(CUTSW=1, CUTRPM=125, CUT_HYST=hyst))
        _crossings(gate, family, candidate, launch=False, hyst=hyst)


def verify(gate, family, image):
    variant = gate._bound_variant(image)
    _, limiter, speed, delta, speed_value, rpm, *_ = gate._NATIVE_FUEL_LAYOUTS[variant]
    latch = gate._launch_latch(image)
    stage = 0xFADE if family == "MS41.0" else 0xFC34
    pending = 3 if family == "MS41.0" else 31
    base_values = dict(
        CUTSW=255,
        CUTRPM=125,
        CUT_HYST=0,
        LC_SW=0,
        LC_CUTTYPE=1,
        LC_MAXRPM=125,
        LC_HARDRPM=128,
        LC_HYST=0,
        LC_ARMSPEED=5,
        LC_MAXSPEED=30,
        LC_MINTPS=128,
        LC_FUEL_HYST_A=1,
        LC_FUEL_HYST_B=0,
    )

    def configured(**overrides):
        return _calibrate(gate, family, image, base_values | overrides)

    off = configured(LC_SW=255)
    control = fuel.LAYOUTS[family]["control"]
    orders = [(speed, control), (control, speed)]
    if family in ("MS41.0", "MS41.1"):
        orders.extend(((limiter, control), (control, limiter)))
    for order in orders:
        e = gate._emu(off, dpp0=4)
        for addr in (0xD802, 0xD803, 0xD804, 0xD80C, 0xD80D):
            e.write_byte(addr, 255)
        e.write_byte(0xD80D, 0xA5)
        e.write_byte(0xD801, 0)
        for entry in order:
            gate._call_native(e, entry)
            assert (
                e.read_byte(0xD801) & 0xF7,
                e.read(0xD802),
                e.read_byte(0xD804),
                e.read_byte(0xD80C),
                e.read_byte(0xD80D),
            ) == (0xA0, 0xC000, 0, 0xA0, 0), (family, order)

    verify_standalone(gate, family, configured(LC_SW=255))
    for hyst in (0, 3):
        _crossings(gate, family, configured(LC_HYST=hyst), launch=True, hyst=hyst)

    # Coexisting sources keep independent entry and release thresholds.
    paired = configured(CUTSW=1, CUTRPM=130, CUT_HYST=7, LC_MAXRPM=125, LC_HYST=3)
    e, ctx = gate._native_fuel_fixture(lambda _: paired, {})
    e.interrupts.enabled = True
    pin = 0xFD50 if family == "MS41.0" else 0xFD60
    e.write(pin, 0x200)
    gate._call_native(e, speed)
    gate._native_fuel_step(e, ctx, 120)
    for raw, bits in ((120, 0), (125, 2), (130, 3), (124, 3), (122, 2), (121, 0), (130, 3)):
        assert _charge(gate, family, e, paired, raw) == bool(bits)
        assert e.read_byte(0xD801) & 3 == bits and bool(e.read_byte(0xD802) & 32) == bool(bits)
        assert e.read_byte(0xFF04) == (255 if bits else gate.IGNITION_SINGLE_MASKS[0])
    # The speed task releases Launch before the next limiter or CC6 call.
    e.write_byte(0xE8D0, 0)
    gate._call_native(e, speed)
    assert _charge(gate, family, e, paired, 130) and e.read_byte(0xD801) & 3 == 1
    e.write(pin, 0)
    assert not _charge(gate, family, e, paired, 135) and e.read_byte(0xD801) & 3 == 0
    for extra in (dict(LC_SW=3), dict(LC_SW=1, LC_CLUTCHPOL=3), dict(LC_CUTTYPE=2)):
        disabled = configured(CUTSW=3, **extra)
        e, ctx = gate._native_fuel_fixture(lambda _: disabled, {})
        e.interrupts.enabled = True
        e.write(pin, 0x200)
        gate._call_native(e, speed)
        gate._native_fuel_step(e, ctx, 135)
        assert not _charge(gate, family, e, disabled, 135) and not e.read_byte(0xD801) & 7

    # A healthy speed pulse reaching the exit threshold must revoke Spark on
    # the same visit even if the setting was armed at zero road speed.
    moving_image = configured()
    e, ctx = gate._native_fuel_fixture(lambda _: moving_image, {})
    e.interrupts.enabled = True
    gate._call_native(e, speed)
    gate._native_fuel_step(e, ctx, 135)
    assert _charge(gate, family, e, moving_image, 135)
    divisor_address = {"1429861": 0xE2, "1437806": 0x17C, "1406464": 0x166, "SS1v2": 0x166}[variant]
    e.write(delta, (30 * e.read(divisor_address) + 3599) // 3600)
    gate._call_native(e, speed)
    assert e.read_byte(speed_value) == 30 and not e.read(0xFD30) & 16
    assert not e.read_byte(latch) & 64 and not _charge(gate, family, e, moving_image, 135)
    site = {"MS41.0": 0x2F2C2, "MS41.1": 0x3B488, "MS41.2": 0x399B2, "MS41.3": 0x399B2}[family]
    # At the displaced native speed-completion instruction all registers,
    # PSW, P1L and both stack/context registers retain the native ABI.
    for flags in (0, 0x081F):
        states = []
        for source in (gate._cut_reference(family), moving_image):
            m = gate._emu(source, dpp0=4)
            m.reg.dpp[:] = [4, 5, 0, 3]
            m.reg.psw.unpack(flags)
            m.reg.r[4], m.reg.r[5] = 0x1234, 0x5678
            m.write_byte(rpm, 135)
            m.write_byte(0xFF04, 0xC9)
            knock._run(m, site, site + 4)
            states.append(
                (
                    tuple(m.reg.r),
                    m.reg.sp,
                    m.reg.cp,
                    tuple(m.reg.dpp),
                    m.reg.psw.pack(),
                    m.read_byte(0xFF04),
                )
            )
        assert states[0] == states[1], (family, flags, "speed return")

    candidate = configured(LC_CUTTYPE=0)
    emu, ctx = gate._native_fuel_fixture(lambda _: candidate, {})
    emu.write_byte(rpm, 130)
    gate._call_native(emu, speed)
    gate._native_fuel_step(emu, ctx, 130)
    assert emu.read_byte(stage) == 1 and emu.read_byte(0xD801) & 4, (family, "first fuel stage")
    saved_cp = emu.reg.cp
    out = fuel._consumer(emu, fuel.LAYOUTS[family], family, stage=1)
    emu.reg.cp = saved_cp
    assert out.count(0xDEAD) == 1 and emu.read_byte(0xD802) & pending == pending
    gate._native_fuel_step(emu, ctx, 120)
    assert emu.read_byte(stage) == 0 and not emu.read_byte(0xD801) & 4
    assert emu.read_byte(0xD802) & pending == pending, (family, "short cut release lost")

    verify_released_fuel(gate, family, emu)

    # The live stage publisher and compare schedule exercise all six outputs.
    e, ctx = gate._native_fuel_fixture(lambda _: candidate, {})
    last = set()
    for n in range(1, 7):
        e.write_byte(rpm, 135)
        gate._call_native(e, speed)
        gate._native_fuel_step(e, ctx, 135)
        assert e.read_byte(stage) == n
        saved_cp = e.reg.cp
        out = fuel._consumer(e, fuel.LAYOUTS[family], family, stage=n)
        e.reg.cp = saved_cp
        absent = {i for i, value in enumerate(out) if value == 0xDEAD}
        assert len(absent) == n and last < absent, (family, n, out)
        last = absent

    # Disarming inside native RPM hysteresis can leave the Launch-created
    # count running. Replay the same RPM/throttle trajectory in original ROM.
    taper = configured(LC_CUTTYPE=0, LC_FUEL_HYST_A=2, LC_FUEL_HYST_B=2)
    native, ctx = gate._native_fuel_fixture(lambda _: taper, {}, original=True)
    native.write_byte(rpm, 135)
    gate._call_native(native, speed)
    native_soft = gate._native_fuel_step(native, ctx, 135)[0][3]
    for raw in (native_soft - 1, native_soft):
        active, context = gate._native_fuel_fixture(lambda _: taper, {})
        original, reference = gate._native_fuel_fixture(lambda _: taper, {}, original=True)
        for visit in range(10):
            if visit == 8:
                active.write_byte(0xE8D0, 0)
                original.write_byte(0xE8D0, 0)
            for machine in (active, original):
                machine.write_byte(rpm, raw)
                gate._call_native(machine, speed)
            output = gate._native_fuel_step(active, context, raw)[0][0]
            untouched = gate._native_fuel_step(original, reference, raw)[0][0]
            if visit >= 8:
                assert output > untouched and active.read_byte(0xD801) & 4, (
                    family,
                    raw,
                    output,
                    untouched,
                )
                saved_cp = active.reg.cp
                active.write_byte(0xD802, 0)
                written = fuel._consumer(active, fuel.LAYOUTS[family], family, stage=output)
                active.reg.cp = saved_cp
                assert written.count(0xDEAD) == output and active.read_byte(0xD802) & pending == pending
        publisher = LAYOUT[family][3] - 0x12
        alternate = context[3] - 2
        if raw == native_soft:
            # Genuine forced inhibition takes precedence even over retained six.
            active.write(0xFD14, active.read(0xFD14) | 0x20)
        else:
            # An independently stronger stage wins without changing its count.
            active.write_byte(alternate, 6)
        gate._call_native(active, publisher)
        assert active.read_byte(0xD80D) == 0xA5
        assert active.read_byte(stage) == 6 and not active.read_byte(0xD801) & 4
        active.write_byte(0xD802, 0)
        saved_cp = active.reg.cp
        assert fuel._consumer(active, fuel.LAYOUTS[family], family, stage=6).count(0xDEAD) == 6
        active.reg.cp = saved_cp
        assert active.read_byte(0xD802) == 0
        active.write(0xFD14, active.read(0xFD14) & ~0x20)
        active.write_byte(alternate, 0)
        gate._call_native(active, publisher)
        assert active.read_byte(stage) == output and active.read_byte(0xD801) & 4
        assert active.read_byte(0xD80D) == 0xA5
        saved_cp = active.reg.cp
        assert fuel._consumer(active, fuel.LAYOUTS[family], family, stage=output).count(0xDEAD) == output
        active.reg.cp = saved_cp
        assert active.read_byte(0xD802) & pending == pending
        assert gate._native_fuel_step(active, context, 120)[0][0] == 0
        assert active.read_byte(0xD80D) == 0
        assert not active.read_byte(0xD801) & 4
        verify_released_fuel(gate, family, active)

    # All timer outputs inhibited by native masks, native global inhibit, or
    # native stage alone. No additional Launch-induced omission can be claimed.
    for state, n, mask, inhibit in ((0xA4, 1, 63, False), (0xA4, 1, 0, True), (0xA0, 6, 0, False)):
        e = fuel._seed(gate, candidate, fuel.LAYOUTS[family], state=state)
        e.write_byte(0xD802, 0)
        out = fuel._consumer(e, fuel.LAYOUTS[family], family, stage=n, mask=mask, inhibit=inhibit)
        assert out.count(0xDEAD) == 6 and e.read_byte(0xD802) == 0

    for mode in (0, 1):
        candidate = configured(LC_CUTTYPE=mode)
        for load in (0, 100):
            e = gate._emu(candidate, dpp0=4)
            e.interrupts.enabled = True
            e.write_byte(0xD801, 0xA0)
            e.write_byte(0xE8D0, 192)
            e.write_byte(0xE8E5, load)
            e.write_byte(rpm, 135)
            e.write(0xFD06, 0x20)
            e.write(delta, 0)
            for visit in range(1, 9):
                e.write_byte(0xEA0E, 0)
                gate._call_native(e, speed)
                if load and visit >= 6:
                    assert e.read(0xFD30) & 16 and not e.read_byte(latch) & 64
                    assert not e.read_byte(0xD802) & 64
                gate._call_native(e, limiter)
                assert e.read_byte(speed_value) == 0
                fault = bool(e.read(0xFD30) & 0x10)
                assert fault == bool(load and visit >= 6)
                assert bool(e.read_byte(latch) & 0x40) != fault
                if mode:
                    assert _charge(gate, family, e, candidate, 135) != fault
                assert bool(e.read_byte(0xD801) & (2 if mode else 4)) != fault
            records = fault_memory(gate, e, variant)
            assert [(r.code, r.is_active) for r in records] == ([(214, True)] if load else [])
            if load:
                divisor = e.read(
                    {"1429861": 0xE2, "1437806": 0x17C, "1406464": 0x166, "SS1v2": 0x166}[variant]
                )

                def moving(raw_speed, throttle):
                    e.write_byte(0xEA0E, 0)
                    e.write_byte(0xE8D0, throttle)
                    e.write(delta, (raw_speed * divisor + 3599) // 3600)
                    gate._call_native(e, speed)
                    gate._call_native(e, limiter)
                    assert e.read_byte(speed_value) == raw_speed

                for _ in range(256):
                    moving(10, 192)
                    assert not e.read_byte(latch) & 64
                    if not e.read(0xFD30) & 16:
                        break
                assert not e.read(0xFD30) & 16, (family, "native 214 did not clear")
                moving(2, 0)
                assert not e.read_byte(latch) & 64
                moving(2, 192)
                assert e.read_byte(latch) & 64
                if mode:
                    assert _charge(gate, family, e, candidate, 135)
                else:
                    assert e.read_byte(stage) > 0 and e.read_byte(0xD801) & 4
