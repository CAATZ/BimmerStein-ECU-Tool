"""Bounded SS1v2 CEL/limiter admission using exact installed instructions.

Samples, task arrivals and external replies are supplied. These checks prove
serialized software transactions, not physical knock, lamp cadence or a car.
"""
from hashlib import sha256

from engines.patcher import verify_fixed_ipw as fuel
from engines.patcher.verify_knock_guard import (
    HISTORY, STATE, _bytes, _call as _knock_call, _native, _run, _seed,
)
from engines.patcher.verify_launch_retard import run_timing


_STOCK_SHA = "89f31cbb70466ad39b23571e3c1c533251275182e8e2502c214a208e83f64487"
_PROGRAM = ((0x20648, 0x20670), (0x29B60, 0x29B8A), (0x20570, 0x20606),
            (0x20670, 0x2072A), (0x2EB0A, 0x2EB32))
_CALS = ((0x33E1, 1), (0x33E2, 1), (0x33E3, 1), (0x33E4, 1),
         (0x33E5, 1), (0x361C, 0x1A))
_DEFAULT = dict(CUTSW=255, CUTRPM=215, CUT_HYST=0, CUT_IPW=65535,
                CUT_RETARD_RANGE=6, CUT_IGN_ANGLE=255,
                LC_SW=0, LC_CUTTYPE=1, LC_CLUTCHPOL=0, LC_MAXRPM=125,
                LC_ARMSPEED=5, LC_MAXSPEED=30, LC_MINTPS=128,
                LC_HARDRPM=206, LC_HYST=0, LC_IPW=65535,
                LC_IGN_ANGLE=255, LC_RETARD_RANGE=0, LC_CUT_IGN_ANGLE=255)
_SHARED = {0xFD22: 0x859A, 0xE804: 0x5A03, 0xE98C: 0x80C7,
           0xF694: 0xA996, 0xFFC4: 0xA5E7, 0xFD06: 0x0540}


def _call(emu, entry):
    before = (emu.reg.r[0], emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp))
    result = _knock_call(emu, entry)
    assert (emu.reg.r[0], emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp)) == before
    return result


def _configured(gate, image, **values):
    # Only calibrate a copy of the supplied image; never rebuild away a defect.
    return gate._full_stack_calibrations(image, "SS1v2", dict(_DEFAULT, **values))


def _alarm(emu):
    emu.write_byte(0xF694, 200)  # warm: isolate the knock alarm
    emu.write_byte(0xE804, 0)
    emu.write(0xFD22, 0)
    emu.write(0xFFC4, 2)
    emu.write(0xFD06, 0)
    emu.reg.r[12] = 0x1B
    _call(emu, 0x29B60)
    flashes = not bool(emu.read(0xFFC4) & 2)
    assert bool(emu.read(0xFD06) & 0x2000) == flashes
    return flashes


def _snapshot(emu):
    return {address: emu.read(address) for address in _SHARED}


def _shared_seed(emu, lamp):
    for address, value in _SHARED.items():
        emu.write(address, value)
    emu.write(0xFD06, _SHARED[0xFD06] | (0x2000 if lamp else 0))
    emu.write(0xFFC4, (_SHARED[0xFFC4] & ~2) | (0 if lamp else 2))
    return _snapshot(emu)


def _alarm_cases(gate, image):
    candidate = bytearray(image)
    for sa, value in ((0x33E1, 100), (0x33E2, 120), (0x33E3, 188), (0x33E5, 220)):
        candidate[(0x10000 + sa) ^ 0x4000] = value
    candidate = gate._bind_image(gate.checksum.correct_checksums(candidate)[0], "SS1v2")
    cases = ((150, 128, 200, 0, 0, False), (100, 128, 187, 0, 0, False),
             (100, 128, 188, 0, 0, True), (220, 128, 30, 0, 0, True),
             (150, 99, 30, 0, 0, True), (150, 128, 30, 128, 0, True),
             (150, 128, 30, 0, 1, True))
    for ect, knock, rpm, flags, countdown, toggles in cases:
        for marker in (0xA0, 0xA1):
            emu = gate._emu(candidate, dpp0=4)
            for address, value in ((0xF694, ect), (0xE98D, knock), (0xFC3C, rpm),
                                   (0xE804, countdown), (0xD801, marker), (0xEA18, 0), (0xEA19, 0)):
                emu.write_byte(address, value)
            emu.write(0xFD22, flags)
            emu.write(0xFFC4, 2)
            emu.write(0xFD06, 0)
            emu.reg.r[12] = 0x1B
            _call(emu, 0x29B60)
            assert emu.read(0xFFC4) & 2 == (0 if toggles else 2)
            assert bool(emu.read(0xFD06) & 0x2000) == toggles
            assert not emu.read(0xFD22) & 0x80
            assert emu.read_byte(0xEA18) == emu.read_byte(0xEA19) == 0
            assert emu.read_byte(0xD801) == marker
            if toggles and not countdown:
                emu.write(0xFD22, flags)
                emu.reg.r[12] = 0x1B
                _call(emu, 0x29B60)
                assert emu.read(0xFFC4) & 2 and not emu.read(0xFD06) & 0x2000
    return len(cases) * 2


def _knock_case(gate, image, layout, retained, sample, mode, *, legacy=False):
    emu = _seed(gate, image, layout, sample=sample)
    for address in (*range(0xE9CC, 0xE9D2), *range(0xD840, 0xD9C0)):
        emu.write_byte(address, retained)
    emu.write_byte(0xE98D, 0x55)
    if mode != "ordinary":
        emu.reg.r[1] = 0x127E
        emu.write_byte(0xFF04, 255)
        abi = (tuple(emu.reg.r), emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp))
        _run(emu, 0x3D92A, 0x3D92E)
        assert emu.read_byte(0xD801) & 1 and emu.read_byte(0xFF04) == 255
        assert (tuple(emu.reg.r), emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp)) == abi
        if mode == "pending_recovery":
            emu.write_byte(0xFC3C, 92)
            _run(emu, 0x3D92A, 0x3D92E)
            assert emu.read_byte(0xD801) & 1 == 0
        if mode == "native_sensor_fault":
            emu.write(0xFD32, 0x4000)
    before = _bytes(emu, HISTORY)
    visits = set()
    emu.cpu.set_trace(lambda pc, op: visits.add(pc))
    _native(emu, layout)
    emu.cpu.set_trace(None)
    frozen = not legacy and mode in ("actual_cut", "pending_recovery")
    assert (_bytes(emu, HISTORY) != before) != frozen, "CEL cut knock history"
    assert (layout["fault"] in visits) == (mode == "native_sensor_fault")
    corrections = _bytes(emu, range(0xE9CC, 0xE9D2))
    emu.write(0xFD1C, emu.read(0xFD1C) | 4)
    _call(emu, layout["global_gate"] - 0x134)
    value = emu.read_byte(0xE98D)
    assert value == sum(corrections) // 6
    assert _alarm(emu) == (value < 128)
    if frozen:
        assert value == retained
    return emu


def _timing_case(gate, image, layout, retained, *, legacy=False):
    emu = _seed(gate, image, layout, sample=255)
    preserved = tuple((address, retained) for address in range(0xE9CC, 0xE9D2))
    result = run_timing(image, "SS1v2", lambda *args, **kwargs: emu,
                        ordinary=192, rpm_value=121, extra_bytes=preserved, return_emu=True)
    assert result[3] is emu and result[2] == (0xC1 if legacy and retained < 128 else 0xC3)
    if not legacy:
        assert emu.read_byte(0xD805) == 0xE7
    assert emu.read_byte(0xE98D) == 112
    # Supply the next foreground context without repairing history/provenance.
    emu.reg.dpp[:] = [4, 5, 0, 3]
    emu.write(layout["sync"], 0)
    before = _bytes(emu, HISTORY)
    _native(emu, layout)
    assert (_bytes(emu, HISTORY) != before) == legacy
    _call(emu, layout["global_gate"] - 0x134)
    value = emu.read_byte(0xE98D)
    assert _alarm(emu) == (value < 128)
    assert value == (125 if legacy else retained)


def _recover(emu, layout):
    def reply(channel):
        _run(emu, layout["request"], layout["request"] + 0x1C)
        for byte in (255, channel):
            emu.asc1.rx_inject([byte])
            emu.interrupts.request("ASC1_RX")
            assert emu.interrupts.pec.service_request(0xFF74)
        pc = layout["snapshots"][channel]
        _run(emu, pc, pc + 14)
        _run(emu, layout["ingress"], layout["ingress"] + 20)
    reply(0)
    for phase in range(6):
        pc = layout["windows"][phase % 2]
        emu.write_byte(0xFA80, phase)
        emu.write(0xFA90, 0x140A)
        for tick in (10, 20):
            emu.reg.r[0] = tick
            _run(emu, pc, pc + 18)
    for channel in range(6):
        reply(channel)
    assert _bytes(emu, STATE) == bytes((0xA7, 63, 0, 1, 1, 1, 63))
    emu.reg.r[0] = 0xFA40
    before = _bytes(emu, HISTORY)
    _native(emu, layout)
    assert _bytes(emu, HISTORY) != before
    _call(emu, layout["global_gate"] - 0x134)
    assert emu.read_byte(0xE98D) < 128 and _alarm(emu)


def _fuel_cases(gate, image):
    p = fuel.LAYOUTS["MS41.3"]
    protected = (0xE98D, *range(0xE9CC, 0xE9D2), 0xFD22, 0xFD23, 0xE804, 0xF694)
    for ipw, status in ((1000, 0xA1), (65534, 0xA2), (65535, 0xA0)):
        candidate = _configured(gate, image, CUTSW=0, CUTRPM=93, CUT_IPW=ipw, LC_SW=255)
        for knock in (112, 128):
            emu = fuel._seed(gate, candidate, p)
            emu.write_byte(p["rpm"], 160)
            emu.write_byte(0xE8D0, 128)
            emu.write_byte(0xFC9D, 137)
            emu.write(0xE8E4, 4096)
            for channel in range(10):
                emu.write(0xFA94 + 2*channel, (channel << 12) | (0x100 + channel*32))
            for address in (0xE98D, *range(0xE9CC, 0xE9D2)):
                emu.write_byte(address, knock)
            emu.write_byte(0xF694, 200)
            emu.write_byte(0xE804, 0)
            emu.write(0xFD22, 0)
            before = _bytes(emu, protected)
            fuel._call(emu, p["task"])
            assert emu.read_byte(0xD80C) == status and _bytes(emu, protected) == before
            assert _alarm(emu) == (knock < 128)
    return 6


def _shared_cases(gate, image, stock, *, event_owned=False):
    image = _configured(gate, image)
    p = fuel.LAYOUTS["MS41.3"]
    cases = 0
    for lamp in (False, True):
        emu = gate._emu(image, dpp0=4)
        before = _shared_seed(emu, lamp)
        emu.write(0xF198, 0)
        emu.write_byte(0xFC3C, 125)
        emu.write_byte(0xE8D0, 192)
        gate._call_native(emu, 0x3989A)
        gate._call_native(emu, 0x20780)
        before[0xFD22] &= 0x7FFF  # displaced native SIR-live clear
        if event_owned:
            assert emu.read_byte(0xD802) & 0x40 and not emu.read_byte(0xD801) & 2
        else:
            assert emu.read_byte(0xD801) & 2
        for entry, hook, paired, masks in (
                (0x3D91A, 0x3D92A, 0, gate.IGNITION_SINGLE_MASKS),
                (0x3D91A, 0x3D92A, 0x100, gate.IGNITION_PAIRED_MASKS),
                (0x3D986, 0x3D98E, 0, gate.IGNITION_SINGLE_MASKS)):
            for index, mask in enumerate(masks):
                emu.write_byte(0xFA5F, index)
                emu.write(0xFD5E, (emu.read(0xFD5E) & ~0x100) | paired)
                emu.write_byte(0xFF04, 255)
                emu.reg.r[4] = 0xA55A
                arch = (emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp))
                _run(emu, entry, hook + 4)
                assert emu.read_byte(0xFF04) == 255 and emu.reg.r[1] & 255 == mask
                assert emu.read_byte(0xD801) & 2
                assert emu.reg.r[4] == 0xA55A and arch == (emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp))
        assert _snapshot(emu) == before
        learning = gate._seed_cut_learning(image, "MS41.3", 0xF018, 0xA2)
        if event_owned:
            learning.write_byte(0xD802, 0x20)
        before = _shared_seed(learning, lamp)
        trims = gate._trim_state(learning, 0xF018)
        gate._call_native(learning, gate._CUT_LEARNING_LAYOUTS["MS41.3"][0])
        assert gate._trim_state(learning, 0xF018) == trims and _snapshot(learning) == before
        cases += 2

    for ipw in (65535, 2000, 60000):
        candidate = _configured(gate, image, LC_IPW=ipw)
        outcomes = []
        for lamp in (False, True):
            emu = fuel._seed(gate, candidate, p, state=0xA2)
            before = _shared_seed(emu, lamp)
            fuel._run(emu, 0x2755A, 0x275EA)
            assert _snapshot(emu) == before
            outcomes.append((fuel._values(emu, p), emu.read_byte(0xD80C)))
        assert outcomes[0] == outcomes[1]
        cases += 1

    for fault in (0, 1):
        candidate = _configured(gate, image, LC_RETARD_RANGE=6, LC_CUT_IGN_ANGLE=27)
        outcomes, fuel_outcomes = [], []
        for lamp in (False, True):
            def load(*args, **kwargs):
                emu = gate._load_emulator(*args, **kwargs)
                _shared_seed(emu, lamp)
                return emu
            result = run_timing(candidate, "SS1v2", load, rpm_value=121, fault=fault, return_emu=True)
            emu = result[3]
            expected = dict(_SHARED)
            expected[0xFD06] |= 0x2000 if lamp else 0
            expected[0xFFC4] = (expected[0xFFC4] & ~2) | (0 if lamp else 2)
            assert _snapshot(emu) == expected
            outcomes.append(result[:3])
            emu.write(0xFD22, 0x80)  # native custom sensor modes off; alarm retained
            for channel in range(10):
                emu.write(0xFA94 + channel*2, (channel << 12) | (0x100 + channel*32))
            emu.write_byte(0xFC9D, 1)
            emu.write(0xF68E, 0x100)
            emu.write(0xE8E4, 0x1000)
            emu.write_byte(0xDC28, 0)
            before = _snapshot(emu)
            gate._call_native(emu, 0x271F4)
            assert _snapshot(emu) == before
            fuel_outcomes.append((fuel._values(emu, p), _bytes(emu, range(0xD801, 0xD80D))))
        assert outcomes[0] == outcomes[1] and fuel_outcomes[0] == fuel_outcomes[1]
        cases += 2

    for entry, stop in ((0x433A, 0x434C), (0x5160, 0x5172)):
        for phase in (1, 2, 3):
            for lamp in (False, True):
                outcomes = []
                for candidate in (stock, image):
                    emu = gate._emu(candidate, dpp0=4)
                    before = _shared_seed(emu, lamp)
                    emu.write_byte(0xFA90, 1)
                    emu.write_byte(0xFA91, 2)
                    emu.reg.r[0] = phase
                    emu.write_byte(0xD801, 0xA2)
                    arch = (emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp))
                    _run(emu, entry, stop)
                    assert arch == (emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp))
                    after = _snapshot(emu)
                    for address in before:
                        mask = 0xFFEF if address == 0xFFC4 else 0xFFFF
                        assert before[address] & mask == after[address] & mask, "CEL window shared-state"
                    outcomes.append((emu.read(0xFFC4), emu.reg.psw.pack()))
                assert outcomes[0] == outcomes[1], "CEL window shared-state"
                cases += 1

    for lamp in (False, True):
        emu = gate._emu(image, dpp0=4)
        before = _shared_seed(emu, lamp)
        for address, value in ((0xD801, 0xA3), (0xD802, 0x1F), (0xD803, 0xC3), (0xD804, 2)):
            emu.write_byte(address, value)
        patch_state = _bytes(emu, range(0xD801, 0xD80D))
        emu.reg.r[12] = 0x1B
        _call(emu, 0x29B60)
        assert emu.read(0xFD22) == before[0xFD22] & ~0x80
        assert emu.read(0xFD06) == before[0xFD06] ^ 0x2000
        assert emu.read(0xFFC4) == before[0xFFC4] ^ 2 and emu.read(0xE804) == 0x5A02
        assert _bytes(emu, range(0xD801, 0xD80D)) == patch_state
        cases += 1

    for previous, high, countdown in ((0, True, 4), (1, False, 2)):
        emu = gate._emu(image, dpp0=4)
        for address, value in ((0xFD22, 0x20), (0xFD5A, previous),
                               (0xFD60, 0x200 if high else 0), (0xFFC4, 2), (0xFD06, 0)):
            emu.write(address, value)
        for address, value in ((0xD801, 0xA3), (0xE98D, 128), (0xF694, 150), (0xFC3C, 125)):
            emu.write_byte(address, value)
        _run(emu, 0x2EB4E, 0x2EB66)
        assert bool(emu.read(0xFD22) & 0x40) == high
        _call(emu, 0x2EB0A)
        assert emu.read_byte(0xE804) == countdown
        for index, remaining in enumerate(range(countdown - 1, -1, -1)):
            emu.reg.r[12] = 0x1B
            _call(emu, 0x29B60)
            assert emu.read_byte(0xE804) == remaining
            assert bool(emu.read(0xFD06) & 0x2000) == (index % 2 == 0)
            assert emu.read_byte(0xD801) == 0xA3
        cases += 1
    return cases


def verify_cel_interaction(gate, installed_image):
    """Verify the supplied exact SS1v2 V19 pair; return bounded case counts."""
    assert len(installed_image) == 0x40000 and gate._bound_variant(installed_image) == "SS1v2"
    stock = gate._cut_reference("MS41.3")
    assert sha256(stock).hexdigest() == _STOCK_SHA
    for patch_id in ("ignition_cut_v19", "launch_control_v19"):
        patch = gate.PATCHES[patch_id]
        splice = patch["cave"]["splice_off"]
        entry = next(edit for edit in patch["edits"] if edit["off"] == splice)
        assert installed_image[splice:splice + 4] == bytes.fromhex(entry["data"])[:4], patch_id
    protected = [range(start, end) for start, end in _PROGRAM]
    protected += [range(0x10000 + sa, 0x10000 + sa + width) for sa, width in _CALS]
    for region in protected:
        assert all(installed_image[pc ^ 0x4000] == stock[pc ^ 0x4000] for pc in region), (
            "CEL protected region", hex(region.start))
    summation = bytes.fromhex("c2f4cce9c2f5cde90045c2f5cee90045c2f5cfe90045c2f5d0e90045c2f5d1e90045e065f6f40efe5b55f2f40efef7f88de9")
    assert installed_image[0x2E1E6:0x2E218] == stock[0x2E1E6:0x2E218] == summation
    layout = gate.PATCHES["ignition_cut_v19"]["cave"]["knock_layout"]
    summary = dict(static_regions=len(protected), alarm=_alarm_cases(gate, installed_image),
                   knock=0, timing=0, recovery=0, fuel=0, shared_state=0, negative_controls=0)
    image = _configured(gate, installed_image, CUTSW=0, CUTRPM=93, LC_SW=255)
    for retained in (112, 128):
        for sample in (0, 255):
            for mode in ("ordinary", "actual_cut", "pending_recovery", "native_sensor_fault"):
                _knock_case(gate, image, layout, retained, sample, mode)
                summary["knock"] += 1
    pending = _knock_case(gate, image, layout, 128, 255, "pending_recovery")
    _recover(pending, layout)
    summary["recovery"] = 1
    timing = _configured(gate, installed_image, CUTSW=0, CUTRPM=125, CUT_IGN_ANGLE=27, LC_SW=255)
    for retained in (112, 128):
        _timing_case(gate, timing, layout, retained)
        summary["timing"] += 1
    summary["fuel"] = _fuel_cases(gate, installed_image)
    summary["shared_state"] = _shared_cases(gate, installed_image, stock)

    # Exact V16 bytes witness the previously unguarded observation path.
    legacy, _ = gate.patch_ms41.build(stock, ["ignition_cut_v16", "launch_control_v16"], allow_deprecated=True)
    legacy = gate._bind_image(legacy, "SS1v2")
    old_cut = _configured(gate, legacy, CUTSW=0, CUTRPM=93, LC_SW=255)
    emu = _knock_case(gate, old_cut, layout, 128, 255, "actual_cut", legacy=True)
    assert emu.read_byte(0xE98D) == 123
    old_timing = _configured(gate, legacy, CUTSW=0, CUTRPM=125, CUT_IGN_ANGLE=27, LC_SW=255)
    _timing_case(gate, old_timing, layout, 128, legacy=True)
    summary["negative_controls"] = 2
    summary["cases"] = sum(count for key, count in summary.items() if key != "static_regions")
    return summary
