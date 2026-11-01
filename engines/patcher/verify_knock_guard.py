"""Execute V18 knock handoffs and complete native transactions in ms41emu.

No transport, hardware, alternative CPU model, or private audit-file dependency.
The software boundary does not prove the age of an opaque ST9030 measurement.
"""

STATE = tuple(range(0xD805, 0xD80C))
HISTORY = tuple(range(0xE9A8, 0xE9B4)) + tuple(range(0xE9BA, 0xE9D2)) + tuple(range(0xD840, 0xD9C0))


def _bytes(emu, addresses):
    return bytes(emu.read_byte(address) for address in addresses)


def _run(emu, start, stop, limit=12000):
    emu.watchdog.service()
    result = emu.run_from(start, stop_at=stop, max_steps=limit)
    assert result.exit_reason == "stop_at" and result.final_pc == stop, (hex(start), result)
    return result


def _call(emu, entry):
    # Explicit full far-return frame; no helper resets caller GPRs or R0.
    saved_sp = emu.reg.sp
    emu.reg.sp = 0xFB70
    emu.write(0xFB70, 0xE000)
    emu.write(0xFB72, 0)
    result = _run(emu, entry, 0xE000)
    assert emu.reg.sp == 0xFB74
    emu.reg.sp = saved_sp
    return result


def _seed(gate, image, layout, phase=0, sample=64):
    emu = gate._emu(image, dpp0=4)
    emu.interrupts.enabled = False
    emu.reg.dpp[:] = [4, 5, 0, 3]
    emu.reg.cp, emu.reg.sp = 0xFC00, 0xFB80
    for address in (0xFF42, 0xFF46, 0xFF48, 0xFF50):
        emu.write(address, 0)
    for address in (0xFD0A, 0xFD12, 0xFD14, 0xFD1C, 0xFD24, 0xFD2A,
                    0xFD2C, 0xFD30, 0xFD32, 0xFD34, layout["enabled"],
                    layout["sync"], 0xEA0C):
        emu.write(address, 0)
    emu.write(layout["enabled"], 0x1000)
    values = {layout["rpm"]:93, layout["coolant"]:200, layout["derivative"]:0,
              layout["phase"]:phase, 0xE8E5:80, 0xE9B6:93, 0xE9B7:80,
              0xE9B8:0, 0xE9DE:0, 0xE9DF:0, 0xE9D8:0, 0xE9D9:128,
              0xE9D2:128, 0xE98D:112, 0xE987:128, 0xD801:0xA0,
              0xD803:0xC0, 0xD805:0xA7, 0xD806:63, 0xD807:0,
              0xD808:1, 0xD809:1, 0xD80A:1, 0xD80B:63}
    for address, value in values.items():
        emu.write_byte(address, value)
    emu.write(0xE9DC, 0x4000)
    for channel in range(6):
        emu.write(0xE9A8 + 2*channel, 0x2000)
        emu.write(0xE9C0 + 2*channel, 0x7000)
        emu.write_byte(0xE9CC + channel, 112)
        emu.write_byte(0xE9BA + channel, 0)
        emu.write_byte(layout["sample"] + channel, sample)
        emu.write_byte(0xDC2E + channel, 0)
    for address in range(0xD840, 0xD9C0):
        emu.write_byte(address, 112)
    for index in range(16):
        emu.reg.r[index] = 0x3141 + index*0x217
    emu.reg.r[0] = 0xFA40
    emu.reg.psw.unpack(0x0800)
    return emu


def _native(emu, layout):
    initial = (emu.reg.r[0], emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp))
    result = _run(emu, layout["caller"], layout["caller"] + 4)
    assert (emu.reg.r[0], emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp)) == initial
    return result


def _preemption(gate, version, image, layout, entries):
    """Canonical IRQ arbitration with a declared RAM test trampoline.

    Only the test vector/trampoline is synthetic; dirty and all admitted native
    bodies are the exact installed instructions. No CPU behavior is replaced.
    """
    candidate = bytearray(image)
    candidate[0x40A0:0x40A4] = bytes.fromhex("FA0000E1")  # ADC -> RAM E100
    candidate = gate._bind_image(candidate, gate._bound_variant(image))

    def seed():
        emu = _seed(gate, candidate, layout, sample=255)
        address = entries["dirty"]
        stub = bytes([0xDA, address >> 16, address & 255, (address >> 8) & 255, 0xFB, 0x88])
        for index, byte in enumerate(stub):
            emu.write_byte(0xE100 + index, byte)
        emu.write(0xFF98, 0x44)  # ADC enabled, priority 1
        emu.interrupts.enabled = True
        return emu

    # Each possible early boundary, including pending IRQ inside the masked
    # publication, must end with no stale channel revalidated after dirty.
    probe = seed()
    probe.write_byte(0xD80B, 0)
    probe.write(layout["raw"] + 2, 0x02FF)
    steps = _run(probe, layout["ingress"], layout["ingress"] + 20).steps
    count = 0
    for boundary in range(1, steps + 1):
        emu = seed()
        emu.write_byte(0xD80B, 0)
        emu.write(layout["raw"] + 2, 0x02FF)
        emu.interrupts.schedule_irq("ADC", after=boundary)
        _run(emu, layout["ingress"], layout["ingress"] + 20)
        assert len(emu.interrupts.entries) == 1, (version, "dirty IRQ delivered", boundary)
        assert emu.read_byte(0xD80B) == 0, (version, "no stale publication after dirty", boundary)
        assert emu.reg.psw.IEN and emu.reg.psw.ILVL == 0
        count += 1

    # A cut after the prefilter admission cannot alter the stable native sample
    # bank. Finish that already-admitted clean transaction; the next is frozen.
    reference = _seed(gate, image, layout, sample=255)
    _native(reference, layout)
    emu = seed()
    pending = [True]
    def interrupt_after_admission(pc, opcode):
        if pending[0] and pc == layout["prefilter"] + 4:
            pending[0] = False
            emu.interrupts.schedule_irq("ADC", after=1)
    emu.cpu.set_trace(interrupt_after_admission)
    _native(emu, layout)
    assert not pending[0] and len(emu.interrupts.entries) == 1
    assert _bytes(emu, HISTORY) == _bytes(reference, HISTORY), (version, "clean admitted transaction")
    assert emu.read_byte(0xD80B) == 0
    before = _bytes(emu, HISTORY)
    _native(emu, layout)
    assert _bytes(emu, HISTORY) == before, (version, "next transaction blocked")
    return count + 1


def verify_knock_guard(gate, version, installed_image):
    """Return a focused all-path case count, raising on any failed assertion."""
    suffix = "" if version == "MS41.3" else "_" + version.lower().replace(".", "")
    descriptor = gate.PATCHES["ignition_cut_v18" + suffix]
    entries = descriptor["cave"]["knock_entries"]
    layout = descriptor["cave"]["knock_layout"]
    reference = gate._cut_reference(version)
    cases = 0

    # A valid ordinary transaction executes the complete native owner unchanged.
    # Negative controls execute the same native sample with an active cut marker.
    native_changes = 0
    learned_changes = 0
    for phase in range(6):
        for sample in (0, 255):
            stock = _seed(gate, reference, layout, phase, sample)
            current = _seed(gate, installed_image, layout, phase, sample)
            before = _bytes(stock, HISTORY)
            learned = _bytes(stock, range(0xD840, 0xD9C0))
            stock.write_byte(0xD801, 0xA1)  # native/V16 has no knock cut gate
            _native(stock, layout)
            _native(current, layout)
            assert _bytes(current, HISTORY) == _bytes(stock, HISTORY), (version, phase, sample, "native histories")
            assert list(current.reg.r) == list(stock.reg.r), (version, phase, sample, "native GPR")
            assert current.read(0xFD1C) == stock.read(0xFD1C)
            native_changes += _bytes(stock, HISTORY) != before
            learned_changes += _bytes(stock, range(0xD840, 0xD9C0)) != learned
            for mode in (0xA1, 0xA2, 0xA4, 0xA0):
                blocked = _seed(gate, installed_image, layout, phase, sample)
                blocked.write_byte(0xD801, mode)
                blocked.write_byte(0xD803, 0xC3 if mode == 0xA0 else 0xC0)
                if mode == 0xA0:
                    blocked.write_byte(0xD805, 0xE7)  # actual timing episode
                retained = _bytes(blocked, HISTORY)
                _native(blocked, layout)
                assert _bytes(blocked, HISTORY) == retained, (version, phase, mode, "freeze complete observation/learning")
                assert blocked.read(0xFD1C) & 0x400 == 0
                assert blocked.read_byte(layout["sample"] + 6 + (phase+3)%6) == 15
                assert blocked.read_byte(0xD80B) == 0
                cases += 1
            # Eligibility which has never changed an event must retain the
            # complete ordinary detector/reference/learning transaction.
            eligible = _seed(gate, installed_image, layout, phase, sample)
            eligible.write_byte(0xD803, 0xC3)
            _native(eligible, layout)
            assert _bytes(eligible, HISTORY) == _bytes(stock, HISTORY), (version, "no-op C3 native histories")
            assert list(eligible.reg.r) == list(stock.reg.r), (version, "no-op C3 native GPR")
            assert eligible.read_byte(0xD805) == 0xA7 and eligible.read_byte(0xD80B) == 63
            cases += 1
            cases += 1
    assert native_changes == 12, (version, "native negative control must change state", native_changes)
    assert learned_changes >= 6, (version, "negative control must update learned cells", learned_changes)

    # Complete ingress ABI matches native for all admitted channels and invalid
    # selectors, regardless of data sign or the caller's interrupt-enable bit.
    for channel in (0, 1, 2, 3, 4, 5, 6, 12, 255):
        for value in (0, 255):
            for ien in (0, 0x800):
                pair = [_seed(gate, image, layout) for image in (reference, installed_image)]
                for emu in pair:
                    emu.write(layout["raw"] + 2, (channel << 8) | value)
                    emu.reg.psw.unpack(ien | 0x15)
                    _run(emu, layout["ingress"], layout["ingress"] + 20)
                stock, current = pair
                assert list(current.reg.r) == list(stock.reg.r), (version, channel, value, "ingress GPR")
                assert current.reg.psw.pack() == stock.reg.psw.pack(), (version, channel, value, "ingress PSW")
                assert _bytes(current, range(layout["sample"], layout["sample"]+6)) == _bytes(stock, range(layout["sample"], layout["sample"]+6))
                assert current.read_byte(layout["telemetry"]) == channel
                cases += 1

    # Existing native sensor/synchronization fault-retard path remains executable.
    # Its learned grid is retained, its correction may become more conservative.
    for address, bit in ((0xFD32,14),(0xFD32,15),(layout["sync"],8),
                         (layout["sync"],10),(0xEA0C,12)):
        for actual_timing in (False, True):
            stock = _seed(gate, reference, layout, sample=255)
            current = _seed(gate, installed_image, layout, sample=255)
            for emu in (stock, current):
                emu.write(address, emu.read(address) | (1 << bit))
            current.write_byte(0xD801, 0xA0 if actual_timing else 0xA1)
            current.write_byte(0xD803, 0xC3 if actual_timing else 0xC0)
            current.write_byte(0xD805, 0xE7 if actual_timing else 0xA7)
            visits = []
            current.cpu.set_trace(lambda pc, op: visits.append(pc))
            _native(stock, layout)
            _native(current, layout)
            assert layout["fault"] in visits, (version, address, bit, "native fault fallback reached")
            assert _bytes(current, range(0xE9C0, 0xE9D2)) == _bytes(stock, range(0xE9C0, 0xE9D2))
            assert _bytes(current, range(0xD840, 0xD9C0)) == bytes([112])*384
            assert current.read(address) & (1 << bit)
            cases += 1

    emu = _seed(gate, installed_image, layout)
    registers, psw = list(emu.reg.r), emu.reg.psw.pack()
    _call(emu, entries["dirty"])
    assert list(emu.reg.r) == registers and emu.reg.psw.pack() == psw
    assert _bytes(emu, STATE) == b"\xa7\0\0\0\0\0\0"

    def request():
        # Execute the actual native PEC destination/source/count/header setup.
        _run(emu, layout["request"], layout["request"] + 0x1C)
        assert emu.read(0xFDE2) == layout["raw"]
        assert emu.read(0xFDE0) == 0xFEBA
        assert emu.read(0xFEC0) == 0x0302

    def reply(channel, value=0x55, snapshot=0):
        # Declared two-byte external reply delivered by the canonical PEC owner.
        for byte in (value, channel):
            emu.asc1.rx_inject([byte])
            emu.interrupts.request("ASC1_RX")
            assert emu.interrupts.pec.service_request(0xFF74)
        pc = layout["snapshots"][snapshot]
        _run(emu, pc, pc + 14)
        assert emu.read_byte(layout["raw"] + 1) == 12
        _run(emu, layout["ingress"], layout["ingress"] + 20)

    def windows():
        for phase in range(6):
            pc = layout["windows"][phase % 2]
            emu.write_byte(0xFA80, phase)
            emu.write(0xFA90, 0x140A)
            for tick in (10, 20):
                emu.reg.r[0] = tick
                _run(emu, pc, pc + 18)

    # A window cycle before draining cannot qualify a request or retained sample.
    windows()
    assert emu.read_byte(0xD806) == 0
    request(); reply(0)
    assert emu.read_byte(0xD808) == 1 and emu.read_byte(0xD80B) == 0
    windows()
    assert emu.read_byte(0xD806) == 63
    for channel in range(6):
        request()
        assert emu.read_byte(0xD809) == 1
        # Old raw index invalidated before the native receiver is armed.
        assert emu.read_byte(layout["raw"] + 1) == 12
        reply(channel, snapshot=channel)
        assert emu.read_byte(0xD80B) == (1 << (channel+1))-1, (version, channel, _bytes(emu, STATE).hex(), hex(emu.read(layout["raw"]+2)), list(emu.reg.r))
    for index in range(6, 12):
        request(); reply(index % 6, snapshot=index)
        assert emu.read_byte(0xD80B) == 63
    for channel in (6, 12, 255):
        request(); reply(channel)
        assert emu.read_byte(0xD80A) == 0 and emu.read_byte(0xD80B) == 63
    # A cut after request issue rejects its delayed result, even after release.
    request(); _call(emu, entries["dirty"]); reply(5, snapshot=11)
    assert emu.read_byte(0xD80B) == 0 and emu.read_byte(0xD80A) == 0
    cases += 10

    # Global adaptive counter/learned-byte transaction blocks as a whole.
    for mask in (0, 1, 31, 63):
        emu = _seed(gate, installed_image, layout)
        emu.write_byte(0xD80B, mask)
        emu.write(0xFD1C, 0x0400)
        stops = (layout["global_gate"]+4, layout["global_skip"])
        result = emu.run_from(layout["global_gate"], stop_at=stops, max_steps=1000)
        assert result.exit_reason == "stop_at"
        assert result.final_pc == stops[mask != 63], (version, "global freshness", mask, result)
        cases += 1
    # Execute the complete scheduled global owner through RETS. Its ordinary
    # path changes E9D9/counters; the guarded path retains them while native
    # diagnostics after the learning block still run.
    for mask, request_bits, timing, marker in (
        (63, 0, 0xC0, 0xA7), (0, 0, 0xC0, 0xA7),
        (31, 0, 0xC0, 0xA7), (63, 1, 0xC0, 0xA7),
        (63, 0, 0xC3, 0xA7), (63, 0, 0xC3, 0xE7),
    ):
        emu = _seed(gate, installed_image, layout)
        emu.write(0xFD1C, 0x0404)
        emu.write(0xFD2C, 0x0200)
        emu.write_byte(0xE9D9, 64)
        emu.write_byte(0xD80B, mask)
        emu.write_byte(0xD801, 0xA0 | request_bits)
        emu.write_byte(0xD803, timing)
        emu.write_byte(0xD805, marker)
        _call(emu, layout["global_gate"] - 0x134)
        ready = mask == 63 and request_bits == 0 and not (timing == 0xC3 and marker == 0xE7)
        assert emu.read_byte(0xE9D9) == 64 + ready
        assert bool(emu.read(0xE9D4) or emu.read(0xE9D6)) == ready
        assert emu.read_byte(0xDA88) == 64 + ready  # downstream native mirror
        cases += 1
    cases += _preemption(gate, version, installed_image, layout, entries)
    from engines.patcher.verify_knock_timing_episode import verify_knock_timing_episode
    episode = verify_knock_timing_episode(gate, version, installed_image, layout, entries)
    cases += episode["cases"]
    return {"cases": cases, "native_negative_controls": native_changes,
            "native_learned_cell_changes": learned_changes,
            "timing_episode": episode,
            "scope": "C166 handoff and serialized native transactions; external U3 result age/electrical timing unverified"}
