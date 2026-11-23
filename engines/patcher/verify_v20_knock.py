"""Execute V20 0102 handoffs, CC15 captures and knock-fault fallback on the native CPU."""
from . import verify_knock_guard as k


def _rom(image, pc, length):
    return bytes(image[(pc + n) ^ 0x4000] for n in range(length))


def _capture(gate, image, layout, phase, window, *, high=False, second=None, reply=None, charge=None):
    """Compare the actual CC15 vector, port transitions and optional ASC1 PEC preemption."""
    emu = k._seed(gate, image, layout)
    raw = layout["raw"]
    pc = 0x7C
    for _ in range(2):
        jump = _rom(image, pc, 4)
        assert jump[0] == 0xFA
        pc = jump[1] << 16 | int.from_bytes(jump[2:], "little")
    context = _rom(image, pc, 4)
    assert context[:2] == bytes.fromhex("c608")
    bank = int.from_bytes(context[2:], "little")
    emu.reg.sp = 0xFC00
    emu.reg.stkov, emu.reg.stkun = 0xFB64, 0xFC00
    emu.traps.enable_vectoring()
    for off, value in ((0, phase), (2, 0xE700), (4, 0xE704), (6, 0xE708)):
        emu.write(bank + off, value)
    for addr, value in ((0xFF58, 0xA000), (0xFF96, 0x6A), (0xFF50, 0x4340),
                        (0xFD00, 0), (layout["sync"], 0), (0xFA90, window), (raw, 0x0253)):
        emu.write(addr, value)
    emu.write(0xFFC4, (emu.read(0xFFC4) & ~16) | (16 if high else 0))
    for ptr in range(0xE000, 0xE600, 2):
        emu.write(ptr, 0x00CC)
    emu.write(0xE600, 0x00FA)
    emu.write(0xE602, 0xE000)
    if reply is not None:
        prior = layout["snapshots"][-1]
        k._run(emu, prior, prior + 14)
        k._run(emu, layout["request"], layout["request"] + 0x1C)
        emu.asc1.rx_inject([0x56])
        emu.interrupts.request("ASC1_RX")
        assert emu.interrupts.pec.service_request(0xFF74)
    if charge:
        family, paired, rpm, marker, pin82 = charge
        emu.write(0xFF84, 0x71)  # CC6, level 12; CC15 remains on native level 10.
        emu.write_byte(0xFA5F, 0)
        emu.write_byte(layout["rpm"], rpm)
        emu.write_byte(0xD801, (0xA3 if pin82 else 0xA2) if paired else 0xA1)
        if pin82:
            emu.write(0xFD50 if family == "MS41.0" else 0xFD60, 0x80)
        emu.write_byte(0xD802, 0x40 if paired else 0)
        emu.write_byte(0xFD80 if family == "MS41.0" else 0xFDB6, 0x40)
        emu.write_byte(0xD805, marker)
        emu.write_byte(0xD803, 0xC3 if marker == 0xE7 else 0xC0)
    cc6_requested = False
    emu.interrupts.enable()
    emu.cpu.csp, emu.cpu.ip = 0, 0xE000
    before = (tuple(emu.reg.r), emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp), emu.reg.psw.pack())
    assert emu.inject_edge("CC15", rising=False)
    start = emu.cpu.state_times
    port = high
    changes, acknowledgments = [], []
    end = 650 + (second or 0)
    while emu.cpu.state_times - start < end:
        clock = emu.cpu.state_times - start
        if charge and not cc6_requested and emu.cpu.pc == layout["snapshots"][0]:
            emu.interrupts.schedule_irq("CC6", after=1)
            cc6_requested = True
        if second is not None and clock >= second:
            assert emu.inject_edge("CC15", rising=False)
            second = None
        if reply is not None and clock >= reply:
            emu.asc1.rx_inject([3])
            emu.interrupts.request("ASC1_RX")
            reply = None
        if emu.cpu.pc == layout["windows"][0] + 80:
            acknowledgments.append(clock)
        emu.cpu.step()
        current = bool(emu.read(0xFFC4) & 16)
        if current != port:
            changes.append((emu.cpu.state_times - start, current))
            port = current
    assert (tuple(emu.reg.r), emu.reg.sp, emu.reg.cp, tuple(emu.reg.dpp), emu.reg.psw.pack()) == before
    if charge:
        assert cc6_requested and sum(event[1] == "CC6" for event in emu.interrupts.entries) == 1
        assert emu.read_byte(0xD802) & 0x20 and emu.read(0xD806) == 0x0300
        assert emu.read(0xD80A) == 0
    return (sum(entry[1] == "CC15" for entry in emu.interrupts.entries), changes, acknowledgments,
            emu.read(raw), emu.read(raw + 2))


def _recovery(gate, image, layout, entries):
    e = k._seed(gate, image, layout)
    raw, snap = layout["raw"], layout["snapshots"][0]

    def issue():
        k._run(e, layout["request"], layout["request"] + 0x1C)
        assert e.read(0xFDE2) == raw and e.read(0xFEC0) == 0x302

    def pec(*values):
        for value in values:
            e.asc1.rx_inject([value])
            e.interrupts.request("ASC1_RX")
            assert e.interrupts.pec.service_request(0xFF74)

    def snapshot():
        k._run(e, snap, snap + 14)
        assert e.read_byte(raw + 1) == 12

    def ingress(phase):
        e.write_byte(layout["phase"], phase)
        k._run(e, layout["ingress"], layout["ingress"] + 20)

    # Cold state uses the stock detector until the first verified intervention.
    for addr in range(0xD805, 0xD80C):
        e.write_byte(addr, 0)
    k._native(e, layout)
    assert e.read_byte(0xD80B) == 63
    # A first omitted charge followed by a permitted one before Task04 still
    # invalidates every retained channel on first use.
    e.write_byte(0xD805, 0)
    e.write(0xD806, 0x0300)
    e.write(0xD80A, 0)
    k._native(e, layout)
    assert e.read_byte(0xD805) == 0xA7 and e.read_byte(0xD80B) == 0
    k._call(e, entries["dirty"])
    assert k._bytes(e, k.STATE) == bytes.fromhex("a7 00 00 00 00 00 00")

    e.write(raw, 0x0255)
    issue()
    assert e.read(raw) == 0x0255
    snapshot()
    ingress(3)
    assert e.read_byte(0xD806) == 0   # Old unread reply.
    pec(0x55, 2)
    snapshot()
    issue()
    ingress(3)
    assert e.read_byte(0xD806) == 0   # Repeated snapshot from before issue.
    pec(0x56)
    snapshot()
    ingress(3)
    assert e.read_byte(0xD806) == 0   # First byte only.
    pec(2)
    k._call(e, entries["dirty"])
    snapshot()
    ingress(3)
    assert e.read_byte(0xD806) == 0   # Old completion after intervention.

    # A full post-cut cohort drains in-flight measurements. A second cohort
    # of phase-matched completed pairs repopulates the six retained channels.
    for cycle in (0, 1):
        for channel in range(6):
            issue()
            pec(0x60 + channel, channel)
            snapshot()
            ingress((channel + 1) % 6)
            expected = (2 << channel) - 1
            assert e.read_byte(0xD806) == (63 if cycle else expected)
            assert e.read_byte(0xD80B) == (expected if cycle else 0)
            ingress((channel + 1) % 6)
            assert e.read_byte(0xD80B) == (expected if cycle else 0)
            if cycle and channel == 1:
                # A different captured value cannot reuse the last credited ticket.
                e.write(raw + 2, 0x017F)
                ingress(2)
                assert e.read_byte(0xD80B) == 1
                issue()
                pec(0x61, 1)
                snapshot()
                ingress(2)
                assert e.read_byte(0xD80B) == 3
    for response, phase in (((0x66, 12), 0), ((0x67, 2), 1)):
        k._call(e, entries["dirty"])
        issue()
        pec(*response)
        snapshot()
        ingress(phase)
        assert e.read_byte(0xD806) == 0


def _publication_abi(gate, family, image, stock, layout):
    def registers(e):
        return tuple(e.reg.r), e.reg.psw.pack(), e.reg.sp, e.reg.cp, tuple(e.reg.dpp)

    for mask in (0, 63):
        for ien in (0, 0x800):
            for channel in (0, 5, 12, 255):
                emus = [k._seed(gate, src, layout) for src in (stock, image)]
                for e in emus:
                    e.reg.psw.unpack(ien | 0x1F)
                    e.write_byte(0xD80B, mask)
                    e.write(layout["raw"] + 2, channel << 8 | 0xAB)
                    k._run(e, layout["ingress"], layout["ingress"] + 20)
                assert registers(emus[0]) == registers(emus[1]), (family, mask, ien, channel, "ingress", registers(emus[0]), registers(emus[1]))
                assert emus[1].read_byte(layout["telemetry"]) == channel
                for e in emus:
                    e.reg.psw.unpack(ien | 0x1F)
                    k._run(e, layout["request"], layout["request"] + 4)
                assert registers(emus[0]) == registers(emus[1]), (family, mask, ien, channel, "request")


def verify(gate, family, image):
    suffix = "" if family == "MS41.3" else "_ms41" + family[-1]
    cave = gate.PATCHES["ignition_cut_v20" + suffix]["cave"]
    layout, entries = cave["knock_layout"], cave["knock_entries"]
    stock = gate._cut_reference(family)
    _recovery(gate, image, layout, entries)
    _publication_abi(gate, family, image, stock, layout)
    # Calibrated windows previously delayed by the CC15 splices on the exact references.
    phase_word = {"MS41.0": 0x2A26, "MS41.1": 0x0275, "MS41.2": 0x0476, "MS41.3": 0x0476}[family]
    checks = [((phase_word >> 8) - 1, phase_word, {"high": True}),
              (1, 0x0902, {"second": 249}), (1, 0x0902, {"second": 1501})]
    if family != "MS41.1":
        checks.append(((phase_word & 255) - 1, phase_word, {}))
    for clock in (11, 91, 231):
        checks.append((1, 0x0902, {"reply": clock}))
    for phase, word, options in checks:
        expected = _capture(gate, stock, layout, phase, word, **options)
        observed = _capture(gate, image, layout, phase, word, **options)
        assert expected == observed, (family, options, observed, expected)
        if "second" in options:
            assert observed[0] == 2
        if options.get("reply") == 91:
            assert observed[-2] >> 8 == 3
    from .verify_launch_v20 import _calibrate
    pair = gate.patch_ms41.is_applied(image, gate.PATCHES["launch_control_v20" + suffix])
    calibrated = _calibrate(gate, family, image, dict(CUTSW=255 if pair else 0,
                            CUTRPM=125, CUT_HYST=0, LC_SW=0, LC_CUTTYPE=1, LC_MAXRPM=125, LC_HYST=0))
    for marker in (0, 0xA7, 0xE7):
        for separation, rpm in ((335 if pair else 333, 125), (1471, 255), (1500, 250)):
            observed = _capture(gate, calibrated, layout, 1, 0x1402,
                                second=separation, charge=(family, pair, rpm, marker, False))
            assert observed[0] == 2, (family, pair, marker, separation, observed)
    # Full switch decode and both hysteresis release comparisons on the pair.
    # Raw 254 is immediately below the maximum configurable raw 255 on-threshold.
    stress = _calibrate(gate, family, image, dict(CUTSW=4, CUTRPM=255, CUT_HYST=3,
                        LC_SW=0, LC_CUTTYPE=1, LC_MAXRPM=255, LC_HYST=3))
    for marker in (0, 0xA7, 0xE7):
        observed = _capture(gate, stress, layout, 1, 0x1402,
                            second=1477, charge=(family, pair, 254, marker, True))
        assert observed[0] == 2, (family, pair, marker, observed)
    for addr, mask in ((0xFD32, 0x4000), (0xFD32, 0x8000), (layout["sync"], 0x100),
                       (layout["sync"], 0x400), (0xEA0C, 0x1000)):
        expected, guarded = (k._seed(gate, src, layout, sample=255) for src in (stock, image))
        guarded.write_byte(0xD802, 0x20)
        for emu in (expected, guarded):
            emu.write(addr, emu.read(addr) | mask)
            k._native(emu, layout)
        assert k._bytes(guarded, range(0xE9C0,0xE9D2)) == k._bytes(expected, range(0xE9C0,0xE9D2))
        assert guarded.read(addr) & mask and guarded.read_byte(0xD80B) == 0
    return {"capture_histories": len(checks), "native_fault_controls": 5, "recovery_channels": 6}
