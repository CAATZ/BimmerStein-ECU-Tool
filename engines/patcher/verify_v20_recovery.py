"""Execute native fuel-trim, misfire and six-segment adaptation consumers for V20."""


def _trim(gate, family, image):
    caller = gate._native_stft_callers(family)[0]
    for bank in gate.CUT_GUARD_LAYOUTS[family]["stft"][3]:
        for marker, pending, blocked in ((0xA0, 0, False), (0xA1, 0, False),
                                         (0xA2, 0x40, False), (0xA0, 0x20, True),
                                         (0xA4, 0, True)):
            emu = gate._emu(image, dpp0=4)
            emu.reg.r[0], emu.reg.r[8], emu.reg.r[9] = 0xE600, bank, 0x100
            emu.write(bank + 6, 0x9000)
            emu.write(bank + 12, 0x7000)
            emu.write(bank + 14, 0xA000)
            emu.write_byte(0xD802, pending)
            gate._native_stft_case(image, caller, bank, marker, emu=emu)
            assert (emu.read(bank + 6) == 0x9000) == blocked, (family, bank, marker, pending)
            assert emu.read_byte(0xD802) & pending == pending


def _delayed_count(gate, family, image):
    entry, stop, _, _, _, _, opportunities, counts, _, shutdown = gate._CUT_MISFIRE_LAYOUTS[family]
    emu = gate._native_misfire_counts(family, image, 0xA0, 0, return_emu=True)

    def visit(cylinder):
        emu.reg.r[9] = cylinder
        result = emu.run_from(entry, stop_at=stop, max_steps=3000)
        assert result.exit_reason == "stop_at" and result.final_pc == stop, result

    # Requester authorization cannot turn unknown private RAM into recovery.
    emu.write_byte(0xD802, 0x40)
    for index in range(6):
        emu.write_byte(0xD80E + index, 0xFF)
    initial = emu.read(opportunities)
    visit(0)
    assert emu.read(opportunities) == initial + 1
    evidence = (emu.read(opportunities), [emu.read(counts + 2*i) for i in range(6)])
    # The actual event has ended before the misfire task sees the request.
    emu.write_byte(0xD802, 0x5F)
    visit(0)
    assert emu.read_byte(0xD802) == 0xCF
    assert gate._ram_bytes(emu, 0xD80E, 6) == bytes([2] * 6)
    native_counts = 0xF560 if family == "MS41.1" else 0xF53A
    assert gate._ram_bytes(emu, native_counts, 6) == bytes(6)
    for n in range(12):
        visit((n + 1) % 6)
        assert (emu.read(opportunities), [emu.read(counts + 2*i) for i in range(6)]) == evidence
    assert gate._ram_bytes(emu, 0xD80E, 6) == bytes(6)
    for n in range(12):
        visit((n + 1) % 6)
    assert emu.read(opportunities) > evidence[0] and sum(
        emu.read(counts + 2*i) for i in range(6)) > sum(evidence[1])
    assert emu.read_byte(shutdown) == 0x12 and emu.read_byte(0xFC35) == 0x12
    for invalid in (6, 255):
        emu.reg.r[9] = invalid
        neighbor = 0xD80E + invalid
        emu.write_byte(neighbor, 0xDA)
        result = emu.run_from(entry, stop_at=entry + 4, max_steps=100)
        assert result.exit_reason == "stop_at" and emu.read_byte(neighbor) == 0xDA


def _read_faults(gate, image, emu):
    boot, dispatcher, tx, *_ = gate._DTC100_LAYOUTS[gate._bound_variant(image)]
    result = emu.run_from(boot - 4, stop_at=boot, max_steps=8)
    assert result.exit_reason == "stop_at"
    frame = gate._native_ds2(emu, dispatcher, tx, 4, 1)
    return [(r.code, r.is_active) for r in gate.parse_ds2_dtc_response(frame[3:-1])], frame


def verify_released_fuel(gate, family, emu):
    """Feed the native misfire monitor the Launch injector case's live event state."""
    if family == "MS41.0":
        return
    entry, stop, base, threshold, enable, warm, opp, counts, _, _ = gate._CUT_MISFIRE_LAYOUTS[family]
    assert not emu.read_byte(0xD801) & 7 and emu.read_byte(0xD802) & 0x10
    emu.reg.r[0], emu.reg.sp = 0xFA40, 0xFBF0
    emu.write_byte(enable, 63)
    emu.write_byte(warm, 0)
    emu.write_byte(0xE8E5, 255)
    emu.write_byte(0xFC3C, 200)
    emu.write(0xFD60, 0)
    emu.write(base, 0)
    emu.write(opp, 7)
    for i in range(6):
        emu.write(base + 2 + 2*i, 1000)
        emu.write(threshold + 2*i, 0)
        emu.write(counts + 2*i, 10 + i)
    for n in range(13):
        emu.reg.r[9] = n % 6
        result = emu.run_from(entry, stop_at=stop, max_steps=3000)
        assert result.exit_reason == "stop_at" and result.final_pc == stop
        assert emu.read(opp) == 7 and [emu.read(counts + 2*i) for i in range(6)] == list(range(10, 16))
    assert not emu.read_byte(0xD802) & 0x10 and gate._ram_bytes(emu, 0xD80E, 6) == bytes(6)
    for n in range(12):
        emu.reg.r[9] = (n + 1) % 6
        result = emu.run_from(entry, stop_at=stop, max_steps=3000)
        assert result.exit_reason == "stop_at"
    assert emu.read(opp) > 7 and sum(emu.read(counts + 2*i) for i in range(6)) > sum(range(10,16))


def _misfire_fault(gate, family, image, exceeded):
    """Supply the cylinder-1 completed misfire score to its factory DTC qualifier."""
    start, stop, score, threshold = ((0x3191C, 0x3197E, 0xF54E, 0x72) if family == "MS41.1"
                                     else (0x30D5E, 0x30DA4, 0xF528, 0x70))
    emu = gate._emu(image, dpp0=4)
    assert emu.read_byte(0xAA0C) == 238
    emu.reg.r[0], emu.reg.sp = 0xFA40, 0xFBF0
    emu.write(0xFD52, 0x400)
    emu.write(score, emu.read(threshold) + int(exceeded))
    for _ in range(25):
        emu.reg.r[9] = 1  # Another selected cylinder leaves only the score comparison.
        emu.write_byte(0xEA0E, 0)
        result = emu.run_from(start, stop_at=stop, max_steps=2500)
        assert result.exit_reason == "stop_at" and result.final_pc == stop
    return _read_faults(gate, image, emu)


def _segments(gate, family, image, *, native_shutdown=False, intervention=False, recovered=False):
    """Run the entire factory task through its fault manager and native DS2 read."""
    displacement = 0xA7C if family == "MS41.1" else 0
    ram_offset = 0x26 if displacement else 0
    emu = gate._emu(image, dpp0=4)
    emu.write_byte(0xFC3C, 72 if displacement else 61)
    emu.write_byte(0xD801, 0xA0)
    emu.write_byte(0xD803, 0xC0)
    emu.write_byte(0xD805, 0xA7)
    emu.write(0xFD14, 0x20)
    emu.write_byte(0xE8E5, 0xC0)
    for n in range(7):
        emu.write(0xFC42 + 2*n, 2000 if n in (0, 6) else 2160)
    if recovered:
        emu.write_byte(0xD802, 0x10)
        for n in range(18):
            emu.write_byte(0xFC9F, n % 6)
            gate._call_native(emu, 0x301AA + displacement)
        assert not emu.read_byte(0xD802) & 0x10 and gate._ram_bytes(emu, 0xD80E, 6) == bytes(6)
    emu.write(0xFD60, 63 if native_shutdown else 0)
    for n in range(420):
        # Last-charge release on each of six phases, and active periods in
        # between. OEM cylinder qualifications must come only from FD60.
        if intervention:
            released = (n // 12) % 6 == n % 12
            emu.write_byte(0xD802, (emu.read_byte(0xD802) & 0xCF) | (0x10 if released else 0x30))
        emu.write_byte(0xFC9F, n % 6)
        gate._call_native(emu, 0x301AA + displacement)
        if intervention:
            assert emu.read_byte(0xF55C + ram_offset) == 0
    learned = [emu.read(0xF50E + ram_offset + 2*i) for i in range(6)]
    records, frame = _read_faults(gate, image, emu)
    return learned, records, frame


def verify(gate, family, image):
    _trim(gate, family, image)
    if family == "MS41.0":
        return  # This exact catalog has no BMW 244 or six-cylinder misfire learner.
    _delayed_count(gate, family, image)
    ordinary = _segments(gate, family, image)
    assert ordinary[:2] == ([0] * 6, [])
    assert _segments(gate, family, image, intervention=True) == ordinary
    genuine = _segments(gate, family, image, native_shutdown=True)
    assert genuine[0][0] == 0 and all(genuine[0][1:]) and genuine[1] == [(244, True)]
    stock = gate._cut_reference(family)
    assert genuine == _segments(gate, family, stock, native_shutdown=True)
    assert _segments(gate, family, image, native_shutdown=True, recovered=True)[1:] == genuine[1:]
    for exceeded in (False, True):
        actual = _misfire_fault(gate, family, image, exceeded)
        assert actual == _misfire_fault(gate, family, stock, exceeded)
        assert actual[0] == ([(238, True)] if exceeded else [])
