"""Focused C166 acceptance for the MS41.3 automatic MAF failsafe."""
from engines.patcher.verify_v20_diagnostics import fault_memory


def _block(emu, start, stop):
    result = emu.run_from(start, stop_at=(stop,), max_steps=20_000)
    assert result.final_pc == stop and result.exit_reason == "stop_at", result


def _fixture(gate, image):
    emu = gate._emu(image, dpp0=4)
    emu.write(0xFD06, 0x28)  # Crank sync and periodic task enabled.
    emu.write(0xE902, 0x4000)
    emu.write_byte(0xF690, 105)
    emu.write(0xFAA2, 512)
    gate._call_native(emu, 0x25C66)  # TPS initialization and qualified working throttle.
    for _ in range(2):
        gate._call_native(emu, 0x22E90)
    gate._call_native(emu, 0x29E34)  # Stock MAF transfer selection.
    _block(emu, 0x360F0, 0x36120)
    assert (emu.read(0xFC50), emu.read_byte(0xE8D0), emu.read_byte(0xE900)) == (0x2AD6, 80, 128)
    return emu


def _sample(emu, adc):
    emu.write(0xFA9E, 0x4000 | adc)  # AN4 result, including its channel tag.
    cp = emu.reg.cp
    emu.reg.cp = 0xFAE8
    _block(emu, 0x3CB08, 0x3CB58)  # ISR lookup, accumulation and bank count.
    emu.reg.cp = cp


def _average(emu, period):
    emu.write(0xFA74, period)
    _block(emu, 0x34E58, 0x34EFA)  # Period division and measured airflow/load.
    return emu.read(0xFC52), emu.read(0xFC54)


def _acquire(emu, adc, period):
    _sample(emu, adc)
    return _average(emu, period)


def _load(emu):
    emu.reg.r[0], emu.reg.sp = 0xFA16, 0xFC00
    _block(emu, 0x34F68, 0x34FDA)


def _engine_load(emu):
    return tuple(emu.read(addr) for addr in (0xFC52, 0xFC54, 0xE80A, 0xE80C, 0xE8E4))


def _records(gate, emu, variant="SS1v2"):
    return [(r.code, r.is_active) for r in fault_memory(gate, emu, variant)]


def _fault_cycle(gate, reference, image, rail, period):
    original, candidate = (_fixture(gate, rom) for rom in (reference, image))
    observed = []
    for adc, digital, count, expected in (
        (512, False, 40, []),
        (rail, False, 90, [(8, True)]),
        (512, True, 40, [(8, True)]),
        (512, False, 50, [(8, False)]),
        (rail, False, 90, [(8, True)]),
        (512, False, 50, [(8, False)]),
    ):
        for emu in (original, candidate):
            emu.write(0xFF04, (emu.read(0xFF04) & ~0x0400) | (int(digital) << 10))
        for visit in range(count):
            was_active = bool(candidate.read(0xFD30) & 2)
            for emu in (original, candidate):
                _acquire(emu, adc, period)
                _load(emu)
                gate._call_native(emu, 0x2E5B2)
            assert candidate.read(0xE8E8) == original.read(0xE8E8), (adc, visit, "measured filter")
            active = bool(candidate.read(0xFD30) & 2)
            assert active == bool(original.read(0xFD30) & 2), (adc, visit, "MAF decision")
            if active != was_active:
                observed_records = fault_memory(gate, candidate, "SS1v2")
                stock_records = fault_memory(gate, original, "SS1v2")
                assert [(r.code, r.is_active) for r in observed_records] == [(8, active)]
                assert observed_records[0].status_raw == stock_records[0].status_raw
                if active and observed == [False]:
                    assert observed_records[0].status_raw == (0x72 if rail == 0 else 0x71)
            if digital:
                assert active, "the MAF digital input still signals a fault"
            if not observed or observed[-1] != active:
                observed.append(active)
            if adc == rail and visit == count - 1:
                assert was_active and candidate.read(0xE8E4) != candidate.read(0xE8E8)
                assert candidate.read(0xE80A) != original.read(0xE80A)
        assert _records(gate, candidate) == expected
    assert observed == [False, True, False, True, False]


def _repeat_and_stop(gate, reference, image, selector):
    original, candidate = (_fixture(gate, rom) for rom in (reference, image))
    candidate.write(0xFD22, selector)
    for emu in (original, candidate):
        emu.write(0xFD30, 2)
    for adc, period, stopped in ((512, 15625, False), (0, 15625, False), (512, 65535, True)):
        for emu in (original, candidate):
            if stopped:
                emu.write(0xFD02, emu.read(0xFD02) | 8)
            measured = _acquire(emu, adc, period)
        if stopped:
            assert measured == (0, 0) and candidate.read_byte(0xFC3C) == 0
        for fd12, fd08, fd14, f19a in ((0, 0, 0, 0), (0x200, 0x20, 0, 0), (0x800, 0, 0, 0),
                                        (0, 0, 4, 0), (0, 0, 4, 1), (0, 0, 0x10, 0), (0, 0, 2, 0)):
            # Unpatched SS1v2 overwrites FC52/54 while publishing legacy units.
            # Feed this oracle the same original acquisition on every repeated
            # foreground call. The V4 image must retain it without help.
            for addr, value in zip((0xFC52, 0xFC54), measured):
                original.write(addr, value)
            for emu in (original, candidate):
                emu.write(0xFD12, fd12)
                emu.write(0xFD08, fd08)
                emu.write(0xFD14, fd14)
                emu.write_byte(0xF19A, f19a)
            _load(original)
            _load(candidate)
            assert candidate.read(0xE8E8) == original.read(0xE8E8), (adc, period, measured)


def _sensor_faults(gate, image):
    emu = _fixture(gate, image)
    for adc, passes, faults in ((512, 40, []), (1023, 40, [(8, True), (12, True)]),
                               (512, 100, [(8, False), (12, False)])):
        emu.write(0xFAA2, adc)
        for _ in range(passes):
            gate._call_native(emu, 0x22E90)  # Native BMW 12 qualifier and working TPS.
            _acquire(emu, adc, 8333)
            _load(emu)
            gate._call_native(emu, 0x2E5B2)
        assert _records(gate, emu) == faults
        assert emu.read_byte(0xE8D0) == (64 if adc == 1023 else 80)


def _lost_sync(gate, image):
    emu = _fixture(gate, image)
    measured = _acquire(emu, 512, 15625)
    assert all(measured)
    emu.reg.r[9] = 0xF018  # Native context receiving the no-sync reset.
    _block(emu, 0x28100, 0x28118)
    _load(emu)
    assert _engine_load(emu)[:4] == (0, 0, 0, 0)
    assert _acquire(emu, 512, 15625) == measured
    _load(emu)
    assert (emu.read(0xE80A), emu.read(0xE80C)) == measured


def _sd_handoff(gate, image):
    for warmup in (40, 41):  # The native ADC handler alternates its two banks.
        emu = _fixture(gate, image)
        for _ in range(warmup):
            _acquire(emu, 512, 15625)
            _load(emu)
        saved = emu.read(0xE8E8)
        assert saved > 0 and not _records(gate, emu)
        emu.write(0xFD22, 0x200)
        _sample(emu, 0)  # This accumulation bank now contains a MAP sample.
        _load(emu)
        emu.write(0xFD22, 0)
        _sample(emu, 1023)  # A valid MAF sample is added to the SAME bank.
        if warmup == 41:
            _load(emu)  # Foreground may run before capture drains the mixed bank.
        _average(emu, 15625)
        _load(emu)
        assert emu.read(0xE8E8) == saved
        emu.write(0xFF04, emu.read(0xFF04) | 0x400)
        gate._call_native(emu, 0x2E5B2)
        assert [(r.code, r.status_raw) for r in fault_memory(gate, emu, "SS1v2")] == [(8, 0x74)]
        emu.write(0xFF04, emu.read(0xFF04) & ~0x400)
        for _ in range(35):  # No clean acquisition; do not advance analog recovery.
            _load(emu)
            gate._call_native(emu, 0x2E5B2)
            assert emu.read(0xE8E8) == saved and emu.read(0xFD30) & 2
        for _ in range(30):
            _acquire(emu, 512, 15625)
            _load(emu)
            gate._call_native(emu, 0x2E5B2)
        assert _records(gate, emu) == [(8, False)]


def _digital_during_sd_return(gate, image):
    emu = _fixture(gate, image)
    for selector, visits in ((0, 30), (0x200, 20)):
        emu.write(0xFD22, selector)
        for _ in range(visits):
            _acquire(emu, 512, 15625)
            _load(emu)
    assert emu.read(0xE8E8) >= 0xF000  # Produced by the native SD load task.
    emu.write(0xFD22, 0)
    for level, expected in ((0, []), (0x400, [(8, 0x74)]), (0, [(8, 0x74)])):
        emu.write(0xFF04, (emu.read(0xFF04) & ~0x400) | level)
        for visit in range(35):
            gate._call_native(emu, 0x2E5B2)  # No new MAF sample or foreground visit.
            if visit in (0, 34):
                records = fault_memory(gate, emu, "SS1v2")
                assert [(r.code, r.status_raw) for r in records] == expected, (level, visit, records)


def _modes(gate, reference, image):
    for selector in (0, 0x200, 0x400, 0x600):
        for fault in (0, 1, 2, 3):
            original, candidate = (_fixture(gate, rom) for rom in (reference, image))
            for emu in (original, candidate):
                emu.write(0xFD22, selector)
                emu.write(0xFD30, fault)
                _acquire(emu, 512, 15625)
                _load(emu)
            if selector & 0x200 or not fault & 2 or (selector == 0x400 and fault == 2):
                assert _engine_load(candidate) == _engine_load(original), (selector, fault)

            # Asymmetric current and filtered loads distinguish the two native
            # enrichment/enleanment routes even if a steady-state map is flat.
            for entry, dest, standard, alternate in (
                (0x38F54, 0xEFD5, 64, 128), (0x39052, 0xEFD6, 128, 64),
            ):
                for addr, value in ((0xFC52, 0x4000), (0xE8E4, 0x2000),
                                    (0xEFBA, 0x1000), (0xEFE0, 0x8000)):
                    candidate.write(addr, value)
                candidate.write_byte(0xEFBE, 1)
                candidate.write_byte(0xEFDE, 1)
                candidate.reg.r[12] = 0xEFB4
                gate._call_native(candidate, entry)
                uses_model = bool(selector & 0x400) or (selector == 0 and fault & 2)
                assert candidate.read_byte(dest) == (alternate if uses_model else standard), (selector, fault, entry)


def _dual_fault(gate, image, speeds):
    # The original MAF publisher and filter independently consume each modeled
    # sample below. Keep its publisher shift matched even on a modified tune.
    stock = _calibrated(gate, gate._cut_reference("MS41.3"), {0x173BF: image[0x173BF]})
    oracle, emu, lookup = _fixture(gate, stock), _fixture(gate, image), _fixture(gate, image)
    shift = image[0x173BF] & 3
    for machine in (oracle, emu):
        machine.write(0xFD30, 3)
        _block(machine, 0x22EB4, 0x22F10)
    assert emu.read_byte(0xE8D0) == image[0x14205]
    for speed in speeds:
        lookup.reg.r[12], lookup.reg.r[13] = 0x04BC, speed
        gate._call_native(lookup, 0x348AC)  # This curve's own six-point RPM axis.
        lookup.reg.r[12] = 0x04BE
        gate._call_native(lookup, 0x34A34)
        curve = lookup.reg.r[4] & 255
        for machine in (oracle, emu):
            machine.write(0xFC3A, speed * 32)
            machine.write_byte(0xFC3C, speed)
        _load(emu)
        # E900=128 gives unit temperature compensation. Promote before shifting
        # and keep native saturation before the final temperature division.
        expected = min(255 << 8, (emu.read_byte(0xE8EB) << 8) + ((curve << 8) >> shift))
        fc52, fc54, reduced, airflow, _ = _engine_load(emu)
        assert reduced == expected, (speed * 32, shift, curve, reduced, expected)
        assert (fc52, fc54, airflow) == (
            min(0xFFFF, expected << shift),
            (min(0xFFFF, expected << shift) * speed * 32) >> 16,
            (expected * speed * 32) >> 16,
        )
        oracle.write(0xFC52, expected)
        oracle.write(0xFC54, (expected * speed * 32) >> 16)
        _load(oracle)
        assert _engine_load(emu) == _engine_load(oracle), (speed, _engine_load(emu), _engine_load(oracle))
        if speed in (203, 225, 250):
            for machine in (emu, oracle):
                gate._call_native(machine, 0x3C8C4)
            assert (emu.read(0xEF78), emu.read_byte(0xE96E)) == (
                oracle.read(0xEF78), oracle.read_byte(0xE96E))


def _calibrated(gate, image, changes):
    data = bytearray(image)
    for offset, value in changes.items():
        data[offset] = value
    fixed, _ = gate.patch_ms41._correct_patch_checksums(data, {"program", "cal"})
    return gate._bind_image(fixed, "SS1v2")


def _temperature_bounds(gate, image):
    for shift, curve, iac, temperature in ((3, 221, 19, 80), (0, 253, 17, 216)):
        settings = {0x144BE + index: curve for index in range(6)}
        settings[0x173BF] = (image[0x173BF] & ~3) | shift
        tuned = _calibrated(gate, image, settings)
        emu, oracle = (_fixture(gate, tuned) for _ in range(2))
        for machine in (emu, oracle):
            machine.write(0xFC3A, 7200)
            machine.write_byte(0xFC3C, 225)
            machine.write_byte(0xE900, temperature)
        emu.write(0xFD30, 3)
        emu.reg.r[0], emu.reg.sp = 0xFA16, 0xFC00
        _block(emu, 0x34F68, 0x2E8B4)
        emu.write_byte(0xE8EB, iac)  # Isolate the downstream calibration arithmetic.
        _block(emu, 0x2E8B4, 0x34FDA)
        oracle.reg.r[12] = min(255 << 7, (iac << 7) + ((curve << 7) >> shift))
        _block(oracle, 0x2E8CC, 0x2E8EA)  # Original IAT division and airflow calculation.
        assert (emu.read(0xE80A), emu.read(0xE80C)) == (oracle.read(0xFC52), oracle.read(0xFC54))


def verify(gate):
    reference = gate._cut_reference("MS41.3")
    patch = gate.PATCHES["alphan_failsafe"]
    assert patch["version"] == "V4"
    for with_v20 in (False, True):
        ids = ["alphan_failsafe"]
        if with_v20:
            ids += ["ignition_cut_v20", "launch_control_v20"]
        image = gate._build_from(gate.STOCK_413_PATH, ids, marker=None)
        for rail, period in ((0, 15625), (1023, 8333)):
            _fault_cycle(gate, reference, image, rail, period)
        for selector in (0, 0x400):
            _repeat_and_stop(gate, reference, image, selector)
        _lost_sync(gate, image)
        _sensor_faults(gate, image)
        _sd_handoff(gate, image)
        _digital_during_sd_return(gate, image)
        _modes(gate, reference, image)
        _dual_fault(gate, image, range(256))
        if not with_v20:
            # Reproduce the high modeled-load recovery failure on the tuned
            # curve from the V3 audit, using an actual failed/restored input.
            cells = {offset: 48 for offset in (0x1005D, 0x1005E, 0x1006D, 0x1006E)}
            _fault_cycle(gate, _calibrated(gate, reference, cells),
                         _calibrated(gate, image, cells), 1023, 15625)
            assert image[0x144BC:0x144BE] == bytes.fromhex("d103") and image[0x143D1] == 6
            # Move only the legacy axis so reuse of expanded breakpoints cannot
            # pass. Exercise all four supported publisher shifts, including the
            # real quantization boundaries at 6496, 7200, 7488 and 8000 RPM.
            for shift in range(4):
                settings = {0x143D2 + i: bp for i, bp in enumerate((3, 21, 63, 113, 187, 242))}
                settings[0x173BF] = (image[0x173BF] & ~3) | shift
                _dual_fault(gate, _calibrated(gate, image, settings), range(256))
            _temperature_bounds(gate, image)
        gate._collect_executed_opcodes()
