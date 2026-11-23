"""Exercise the native crank-code qualifiers, DS2 fault memory and ignition event queue."""
from . import verify_knock_guard as knock
from .verify_launch_retard import LAYOUTS, run_timing


# Monitor entry, BMW 83 qualifier and continuation, sync counter, fault record.
NATIVE = {
    "MS41.0": (0x2B9EC, 0x21EAA, 0x21F12, 0xFB4B, 0xEA2E),
    "MS41.1": (0x353A8, 0x22B32, 0x22BBE, 0xFCA1, 0xEA32),
    "MS41.2": (0x33870, 0x22C10, 0x22C9C, 0xFCA1, 0xEA32),
    "MS41.3": (0x33870, 0x22C10, 0x22C9C, 0xFCA1, 0xEA32),
}


def code(image, pc, length):
    return bytes(image[(pc+i) ^ 0x4000] for i in range(length))


def fault_memory(gate, emu, variant):
    boot, dispatcher, tx, *_ = gate._DTC100_LAYOUTS[variant]
    # Run the stock fault-list setup before asking the native DS2 dispatcher.
    knock._run(emu, boot - 4, boot)
    frame = gate._native_ds2(emu, dispatcher, tx, 4, 1)
    return gate.parse_ds2_dtc_response(frame[3:-1])


def verify_crank_sensor(gate, family, image, layout):
    _, start, stop, counter, record = NATIVE[family]
    variant = gate._bound_variant(image)
    for flags, errors, expected in ((0, 0, 0), (0x400, 0, 0x74),
                                    (0x1400, 0, 0x78), (0, 255, 0x74)):
        emu = gate._emu(image, dpp0=4)
        emu.reg.r[0], emu.reg.sp = 0xFA40, 0xFBF0
        emu.write_byte(layout["telemetry"] - 1, 1)
        emu.write_byte(layout["telemetry"], 12)
        emu.write(layout["sync"], flags)
        emu.write_byte(counter, errors)
        for _ in range(8):
            emu.write_byte(0xEA0E, 0)
            knock._run(emu, start, stop)
        assert emu.read_byte(record) == expected, (family, flags, errors)
        records = fault_memory(gate, emu, variant)
        assert [r.code for r in records] == ([83] if expected else []), (family, flags, errors, records)


def mailbox(gate, family, image, original, layout, mode, *, pending=None):
    variant = gate._bound_variant(image)
    emu = knock._seed(gate, image, layout)
    for addr in range(0xD801, 0xD814):
        emu.write_byte(addr, 0)  # Reset state, no prior combustion intervention.
    if pending is not None:
        # No clean cylinder observations have arrived since the last omitted charge.
        emu.write_byte(0xD801, 0xA0)
        emu.write_byte(0xD803, 0xC0)
        emu.write_byte(0xD805, 0xA7)
        emu.write_byte(0xD802, pending)
    _, _, _, _, _, reason = gate._DTC100_LAYOUTS[variant]
    raw = layout["raw"]
    request = layout["request"]
    assert code(original, request+28, 2) == bytes.fromhex("ea00")
    dispatch_end = (request & 0xF0000) | int.from_bytes(code(original, request+30, 2), "little")
    rx_complete = 0x2FFB8 if family == "MS41.0" else dispatch_end + 8
    assert code(original, rx_complete, 8) == bytes.fromhex("8a0105706eba7fbb")
    queue = [pc + code(original, pc, 0x160).index(bytes.fromhex("0f6e7fbb"))
             for pc in layout["snapshots"]]

    def issue(site):
        knock._run(emu, site, site+4)
        assert emu.read(0xFDDC) & 1
        knock._run(emu, request-0x1E, dispatch_end)
        assert emu.read(0xFDE2) == raw and emu.read(0xFEC0) == 0x302

    def receive(*data):
        for value in data:
            emu.asc1.rx_inject([value])
            emu.interrupts.request("ASC1_RX")
            assert emu.interrupts.pec.service_request(0xFF74)
        if not emu.read(0xFEC0) & 0xFF:
            knock._run(emu, rx_complete, rx_complete+8)

    early = mode not in ("late", "split")
    if early:
        emu.write_byte(raw+1, 12)
    else:
        issue(queue[4])
        receive(0x50, 5)
    if mode == "timeout":
        timeout = 0xFD50 if family == "MS41.0" else 0xFD60
        emu.write(timeout, emu.read(timeout) | 0x8000)
    for index, pc in enumerate(layout["snapshots"]):
        phase = emu.read_byte(layout["phase"])
        assert phase == index % 6
        old = (phase-1) % 6
        assert emu.read_byte(raw+1) == (12 if early else old)
        issue(queue[(index//6)*6 + (index-1)%6])
        channel = phase if mode == "wrong phase" else old
        if early:
            receive(0x60+old, channel)
        elif mode == "split":
            receive(0x60+old)
        # Run the native crank phase load, sample snapshot and retirement.
        pre = code(original, pc-32, 32)
        phase_load = bytes((0xE1, ((index+1)%6)*16+12, 0xF7, 0xFC, 0x80, 0xFA))
        assert pre.count(phase_load) == 1
        knock._run(emu, pc-32+pre.index(phase_load), pc+14)
        ingress = layout["ingress"]
        publish = bytes.fromhex("f3f480faf7f4") + layout["phase"].to_bytes(2, "little")
        end = ingress + code(original, ingress, 0x90).index(publish) + 8
        knock._run(emu, ingress-8, end)
        assert emu.read_byte(layout["telemetry"]) == channel, (family, mode, index)
        gate._call_native(emu, NATIVE[family][0])
        if mode == "split":
            receive(phase)
        elif not early:
            receive(0x60+old, phase)
    expected = 0x1000 if mode in ("timeout", "wrong phase") else 0
    assert (emu.read(0xEA0C), emu.read(reason)) == (expected, expected)
    records = fault_memory(gate, emu, variant)
    assert [r.code for r in records] == ([100] if expected else [])
    if expected:
        assert records[0].is_active and int.from_bytes(records[0].raw_record[6:8], "little") == expected
    if mode == "late" and pending is None:
        knock._native(emu, layout)
        offsets = LAYOUTS[variant][8]
        bank = tuple((addr, emu.read_byte(addr)) for addr in range(offsets, offsets+6))
        timing, output, _ = run_timing(image, variant, gate._load_emulator, ordinary=192, cylinder=2,
                                       buffer=0, armed=0, owner=0, extra_bytes=bank)
        return bank, tuple(angle for _, angle in timing), tuple(output[0])


def verify(gate, family, image):
    original = gate._cut_reference(family)
    suffix = "" if family == "MS41.3" else "_ms41" + family[-1]
    layout = gate.PATCHES["ignition_cut_v20"+suffix]["cave"]["knock_layout"]
    verify_crank_sensor(gate, family, image, layout)
    for mode in ("on time", "split", "late", "wrong phase", "timeout"):
        for pending in (0, 0x3F):  # Released and actively skipping charges.
            mailbox(gate, family, image, original, layout, mode, pending=pending)
        result = mailbox(gate, family, image, original, layout, mode)
        if mode == "late":
            assert result == mailbox(gate, family, original, original, layout, mode), (family, mode, result)


def verify_launch_observations(gate, family, image):
    """Take both banks from the first real injector omission to their own fresh samples."""
    from . import verify_fixed_ipw as fuel
    from .verify_launch_v20 import _calibrate

    variant = gate._bound_variant(image)
    speed_task = gate._NATIVE_FUEL_LAYOUTS[variant][2]
    banks = gate.CUT_GUARD_LAYOUTS[family]["stft"][3]
    caller = gate._native_stft_callers(family)[0]

    def trim(emu, bank):
        emu.reg.r[0], emu.reg.r[8], emu.reg.r[9] = 0xE600, bank, 0x100
        for offset, value in ((6, 0x9000), (12, 0x7000), (14, 0xA000)):
            emu.write(bank + offset, value)
        gate._native_stft_case(image, caller, bank, emu.read_byte(0xD801), emu=emu)
        return emu.read(bank + 6)

    for mode in (0, 1):
        settings = {**gate.A_THRESHOLDS, "LC_SW": 0, "LC_CUTTYPE": mode, "LC_MAXRPM": 125,
                    "LC_HARDRPM": 128, "LC_HYST": 0, "LC_FUEL_HYST_A": 1, "LC_FUEL_HYST_B": 0,
                    "LC_IGN_ANGLE": 255, "LC_CUT_IGN_ANGLE": 255, "CUTSW": 255}
        emu, ctx = gate._native_fuel_fixture(lambda values: _calibrate(gate, family, image, values), settings)
        emu.write_byte(0xD805, 0xA7)
        emu.write_byte(0xD806, 63)
        emu.write_byte(0xD80B, 63)
        emu.write_byte(ctx[2], 140)
        gate._call_native(emu, speed_task)
        stage = gate._native_fuel_step(emu, ctx, 140)[0][0]
        assert stage == int(mode == 0)
        assert [trim(emu, bank) for bank in banks] == [0x9000 if stage else 0x8F00] * 2
        cp = emu.reg.cp
        outputs = fuel._consumer(emu, fuel.LAYOUTS[family], family, stage=stage)
        emu.reg.cp = cp
        assert outputs.count(0xDEAD) == stage
        if mode:
            assert emu.read_byte(0xD80B) == 63 and not emu.read_byte(0xD802) & 3
            continue
        assert emu.read_byte(0xD80B) == 0
        assert gate._native_fuel_step(emu, ctx, 100)[0][0] == 0
        assert [trim(emu, bank) for bank in banks] == [0x9000] * 2
        for index, bank in enumerate(banks):
            emu.reg.r[12], emu.reg.r[13] = 0x55, bank
            gate._call_native(emu, gate._CUT_MONITOR_LAYOUTS[family]["front"])
            assert emu.read_byte(0xD802) & 3 == (2 if index == 0 else 0)
            assert trim(emu, bank) == 0x8F00
