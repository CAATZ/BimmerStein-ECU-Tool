"""Exact current RPM-band timing, ownership and native diagnostic regressions."""
from __future__ import annotations

from engines.patcher.verify_launch_retard_state import (
    CUT_STATE_ADDR, CUT_RECOVERY_ADDR, CUT_TIMING_ADDR, CUT_SOURCE_ADDR,
)

from engines.patcher.verify_launch_retard import run_timing
from engines.patcher.verify_launch_retard_state import LAYOUTS


_FAMILIES = {
    "1429861": ("MS41.0", "_ms410"),
    "1437806": ("MS41.1", "_ms411"),
    "1406464": ("MS41.2", "_ms412"),
    "SS1v2": ("MS41.3", ""),
}


def verify_ignition_retard_band(gate, installed_image, stock_image, variant):
    """Use stock scheduling as the oracle; never replace the native ISR."""
    version, suffix = _FAMILIES[variant]
    revision = "v18"
    ignition_id, launch_id = "ignition_cut_" + revision + suffix, "launch_control_" + revision + suffix
    cals = dict(gate.PATCHES[ignition_id]["cave"]["cals"])
    cals.update(gate.PATCHES[launch_id]["cave"]["cals"])
    _, _, _, rpm_address, hard, _, latch, pins, pin82 = LAYOUTS[variant]

    def configured(source, strategy, **overrides):
        values = dict(CUTSW=0xFF, CUTRPM=125, CUT_HYST=10, CUT_IPW=0xFFFF,
                      CUT_RETARD_RANGE=6, CUT_IGN_ANGLE=0xFF,
                      LC_SW=0xFF, LC_CUTTYPE=1, LC_CLUTCHPOL=0, LC_MAXRPM=125,
                      LC_HARDRPM=128, LC_HYST=10, LC_IPW=0xFFFF,
                      LC_ARMSPEED=1, LC_MAXSPEED=5, LC_MINTPS=100,
                      LC_IGN_ANGLE=0xFF, LC_RETARD_RANGE=6, LC_CUT_IGN_ANGLE=0xFF)
        if strategy == "standalone":
            values.update(CUTSW=0, CUT_IGN_ANGLE=27)
        elif strategy == "launch":
            values.update(LC_SW=0, LC_CUT_IGN_ANGLE=27)
        values.update(overrides)
        image = bytearray(source)
        for name, value in values.items():
            offset = cals[name]
            image[offset:offset + (2 if name.endswith("IPW") else 1)] = value.to_bytes(
                2 if name.endswith("IPW") else 1, "little")
        if hard < 0xC000:
            image[(0x10000 + hard) ^ 0x4000] = 206
        return gate._bind_image(bytes(gate.checksum.correct_checksums(image)[0]), variant)

    count = 0
    for strategy, owner in (("standalone", 3), ("launch", 2)):
        image = configured(installed_image, strategy)
        original = configured(stock_image, "off")
        # Establish a real timing override through the native ISR, then invoke
        # the unmodified self-test evaluator on that same active machine.
        active = run_timing(image, variant, gate._load_emulator, rpm_value=121, return_emu=True)
        emu = active[3]
        assert all(angle == 228 for _, angle in active[0]) and active[2] == 0xC3
        assert emu.read_byte(CUT_SOURCE_ADDR) == owner and emu.read_byte(CUT_STATE_ADDR) == 0xA0
        _, _, _, evaluator, status, reason = gate._DTC100_LAYOUTS[variant]
        emu.reg.r[12] = 0x206B
        gate._call_native(emu, evaluator)
        assert (emu.reset_count == 0 and emu.read(0xEA0C) == 0
                and not emu.read_byte(status) & 0x60 and emu.read(reason) == 0
                and emu.read_byte(0xEA18) == 0), (variant, strategy, "active-band self-test inverse")
        assert emu.read_byte(CUT_TIMING_ADDR) == 0xC3 and emu.read_byte(CUT_SOURCE_ADDR) == owner
        count += 1
        for rpm, active in ((118, False), (119, True), (124, True), (125, False), (126, False)):
            for state in (0xA0, 0xA1, 0xA2, 0xA3):
                # A held spark-cut request and its hysteresis do not define the band.
                values = dict(rpm_value=rpm, marker=state, old=0xC3, owner=owner)
                native = run_timing(original, variant, gate._load_emulator,
                                    ordinary=228 if active else 192, **values)
                patched = run_timing(image, variant, gate._load_emulator, return_emu=True, **values)
                assert patched[:2] == native[:2], (variant, strategy, "band boundary", rpm, state)
                assert patched[2] == (0xC3 if active else 0xC0)
                assert patched[3].read_byte(CUT_SOURCE_ADDR) == (owner if active else 0)
                count += 1

        prefix = "CUT" if strategy == "standalone" else "LC"
        angle_key = "CUT_IGN_ANGLE" if strategy == "standalone" else "LC_CUT_IGN_ANGLE"
        for name, settings, runtime, active in (
            ("angle disabled", {angle_key: 0xFF}, {}, False),
            ("invalid angle", {angle_key: 64}, {}, False),
            ("zero band", {prefix + "_RETARD_RANGE": 0}, {}, False),
            ("switch off", {"CUTSW" if strategy == "standalone" else "LC_SW": 0xFF}, {}, False),
            ("zero hysteresis", {prefix + "_HYST": 0}, {}, True),
            ("legacy zero hysteresis", {prefix + "_HYST": 0xFF}, {}, True),
            ("wide hysteresis", {prefix + "_HYST": 124}, {}, True),
            ("critical input protection", {}, {"fault": 1}, False),
            ("knock sensor protection", {}, {"fault2": 0x8000}, False),
            ("global knock history", {}, {"knock": 0xE9D9}, True),
            ("cylinder knock history", {}, {"knock": 0xE9CF}, True),
            ("native priority", {}, {"marker": 0xA8}, False),
        ):
            candidate = configured(installed_image, strategy, **settings)
            values = dict(rpm_value=121, old=0xC3, owner=owner, **runtime)
            native = run_timing(original, variant, gate._load_emulator,
                                ordinary=228 if active else 192, **values)
            patched = run_timing(candidate, variant, gate._load_emulator, return_emu=True, **values)
            assert patched[:2] == native[:2], (variant, strategy, name)
            expected_state = 0xC3 if active else (0xC1 if any(key in runtime for key in ("fault", "fault2", "knock")) else 0xC0)
            assert patched[2] == expected_state, (variant, strategy, name, hex(patched[2]))
            count += 1

        for angle in (0, 27, 63):
            candidate = configured(installed_image, strategy, **{angle_key: angle})
            for cylinder in range(6):
                for buffer in (0, 1):
                    values = dict(rpm_value=121, cylinder=cylinder, buffer=buffer, timeline=True)
                    native = run_timing(original, variant, gate._load_emulator, ordinary=255 - angle, **values)
                    patched = run_timing(candidate, variant, gate._load_emulator, ordinary=80, **values)
                    assert patched[:2] == native[:2], (variant, strategy, angle, cylinder, buffer)
                    count += 1

        # Live switch and launch gates apply afresh; standalone is independent
        # of the launch latch, speed and throttle envelope.
        for values in (dict(armed=0), dict(road_speed=5), dict(throttle=99)):
            active = strategy == "standalone"
            native = run_timing(original, variant, gate._load_emulator,
                                ordinary=228 if active else 192, rpm_value=121, **values)
            patched = run_timing(image, variant, gate._load_emulator, rpm_value=121, **values)
            assert patched[:2] == native[:2] and patched[2] == (0xC3 if active else 0xC0)
            count += 1

        _verify_band_recovery(gate, version, image, variant, owner)

    # Shared controls must not let stale launch ownership suppress standalone,
    # and launch ignition has deterministic priority if both bands are eligible.
    for values, expected_owner, expected_angle in (
        ({"LC_SW": 0, "LC_CUT_IGN_ANGLE": 10}, 2, 245),
        ({"LC_SW": 0, "LC_CUTTYPE": 0, "LC_IGN_ANGLE": 10, "LC_HARDRPM": 120}, 1, 245),
        ({"LC_SW": 0xFF}, 3, 228),
    ):
        image = configured(installed_image, "standalone", **values)
        patched = run_timing(image, variant, gate._load_emulator, rpm_value=121, return_emu=True)
        assert all(angle == expected_angle for _, angle in patched[0])
        assert patched[3].read_byte(CUT_SOURCE_ADDR) == expected_owner
        count += 1

    standalone, _ = gate.patch_ms41.build(stock_image, [ignition_id])
    standalone = configured(gate._bind_image(standalone, variant), "standalone")
    # Deliberately enable launch calibrations without its program hooks. The
    # standalone installation must never call or infer an absent Launch patch.
    standalone = configured(standalone, "standalone", LC_SW=0, LC_CUTTYPE=0,
                            LC_IGN_ANGLE=10, LC_HARDRPM=120)
    patched = run_timing(standalone, variant, gate._load_emulator, rpm_value=121, return_emu=True)
    assert all(angle == 228 for _, angle in patched[0]) and patched[3].read_byte(CUT_SOURCE_ADDR) == 3
    _verify_band_recovery(gate, version, standalone, variant, 3)
    fuel = configured(installed_image, "off", LC_SW=0, LC_CUTTYPE=0, LC_IGN_ANGLE=27)
    _verify_fuel_task_retard_recovery(gate, fuel, variant)
    if variant == "1429861":
        # Reproduce V14's late owner overwrite at the current RAM ABI. The
        # exact historical descriptor still retains the original extra store.
        old_edit = next(edit for edit in gate.PATCHES["ignition_cut_v14_ms410"]["edits"]
                        if edit["off"] == 0x36A00)
        assert bytes.fromhex("e13af7fa48e8f7fa4ae8") in bytes.fromhex(old_edit["data"])
        broken = bytearray(fuel)
        control = bytes.fromhex(next(edit["data"] for edit in gate.PATCHES[ignition_id]["edits"]
                                     if edit["off"] == 0x36A00))
        writer = bytes.fromhex("e13af7fa") + CUT_RECOVERY_ADDR.to_bytes(2, "little")
        assert control.count(writer) == 1
        operand = 0x36A00 + control.index(writer) + 4
        broken[operand:operand + 2] = CUT_SOURCE_ADDR.to_bytes(2, "little")
        broken = gate._bind_image(bytes(gate.checksum.correct_checksums(broken)[0]), variant)
        try:
            _verify_fuel_task_retard_recovery(gate, broken, variant)
        except AssertionError as error:
            assert error.args == ((variant, "fueling task overwrote timing source"),)
        else:
            raise AssertionError("V14 late-owner defect must fail timing-source preservation")
    return count + 2


def _verify_fuel_task_retard_recovery(gate, image, variant):
    """The complete fueling task must not overwrite the timing ISR's owner."""
    result = run_timing(image, variant, gate._load_emulator,
                        rpm_value=125, marker=0xA4, fuel=0x8000, return_emu=True)
    emu = result[3]
    assert all(angle == 228 for _, angle in result[0])
    assert (emu.read_byte(CUT_TIMING_ADDR), emu.read_byte(CUT_SOURCE_ADDR)) == (0xC3, 1)
    # Minimum ADC/engine inputs used by the existing scheduled-fueling fixture.
    for channel in range(10):
        emu.write(0xFA94 + channel * 2, (channel << 12) | (0x100 + channel * 0x20))
    emu.write_byte(0xFC9D, 1)
    emu.write(0xF68E, 0x100)
    emu.write(0xE8E4, 0x1000)
    emu.write_byte(0xDC28, 0)
    task, hook, cave, rpm, _ = gate._FUEL_TASK_LAYOUTS[variant]
    visited = set()
    emu.cpu.set_trace(lambda pc, _opcode: visited.add(pc))
    gate._call_native(emu, task)
    emu.cpu.set_trace(None)
    assert hook in visited and cave in visited, (variant, "complete native fueling path")
    assert (emu.read_byte(CUT_TIMING_ADDR), emu.read_byte(CUT_SOURCE_ADDR)) == (0xC3, 1), (
        variant, "fueling task overwrote timing source")
    entry = LAYOUTS[variant][0]
    emu.write_byte(rpm, 124)
    gate._call_native(emu, entry)
    assert (emu.read_byte(CUT_TIMING_ADDR), emu.read_byte(CUT_SOURCE_ADDR), emu.reg.r[4] & 255) == (0xC3, 1, 27), (
        variant, "fuel-retard recovery survives fueling task")
    emu.write(0xFD12, emu.read(0xFD12) & ~0x8000)
    gate._call_native(emu, entry)
    assert (emu.read_byte(CUT_TIMING_ADDR), emu.read_byte(CUT_SOURCE_ADDR), emu.reg.r[4] & 255) == (0xC0, 0, 255), (
        variant, "fuel-retard episode releases with native fuel state")


def _verify_band_recovery(gate, version, image, variant, owner):
    """Actual retard without a cut still protects samples, then releases them."""
    _, _, _, rpm, _, _, _, _, _ = LAYOUTS[variant]
    for bank in gate.CUT_GUARD_LAYOUTS[version]["stft"][3]:
        result = run_timing(image, variant, gate._load_emulator, rpm_value=121, return_emu=True)
        emu = result[3]
        assert emu.read_byte(CUT_STATE_ADDR) == 0xA0 and emu.read_byte(CUT_SOURCE_ADDR) == owner
        assert emu.read_byte(CUT_RECOVERY_ADDR) & (3 if version == "MS41.0" else 0x1F)
        caller = gate._native_stft_callers(version)[0]
        emu.reg.r[0], emu.reg.r[8], emu.reg.r[9] = 0xE600, bank, 0x100
        emu.write(bank + 6, 0x9000)
        emu.write(bank + 12, 0x7000)
        emu.write(bank + 14, 0xA000)
        gate._native_stft_case(image, caller, bank, 0xA0, emu=emu)
        assert emu.read(bank + 6) == 0x9000, (version, owner, "band trim freeze", bank)
        # A read-only check must recognize a current band exit without changing
        # the previous publication; accepted samples wait for the timing owner.
        emu.write_byte(rpm, 118)
        entry = LAYOUTS[variant][0]
        gate._call_native(emu, entry + 10)
        assert emu.reg.r[4] & 255 == 0xC0 and emu.read_byte(CUT_TIMING_ADDR) == 0xC3
        assert emu.read_byte(CUT_SOURCE_ADDR) == owner
        gate._call_native(emu, entry)
        assert emu.read_byte(CUT_TIMING_ADDR) == 0xC0 and emu.read_byte(CUT_SOURCE_ADDR) == 0
        emu.reg.r[12], emu.reg.r[13] = 0x55, bank
        gate._call_native(emu, gate._CUT_MONITOR_LAYOUTS[version]["front"])
        gate._native_stft_case(image, caller, bank, 0xA0, emu=emu)
        assert emu.read(bank + 6) == 0x8F00, (version, owner, "band accepted-sample recovery", bank)
