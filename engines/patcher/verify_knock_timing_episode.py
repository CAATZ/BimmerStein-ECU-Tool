"""Exact V18 actual-intervention episode and V17 negative-control witnesses.

Eligibility without an actual scheduled-angle change remains ordinary native
operation. After any real change, later no-op events cannot release protection
until eligibility ends and native observation recovery completes.
"""
from engines.patcher.verify_launch_retard import run_timing
from engines.patcher.verify_knock_guard import _bytes, _call, _native, _run, _seed, HISTORY, STATE


def _configured(gate, image, variant, suffix, angle):
    cals = dict(gate.PATCHES["ignition_cut_v18" + suffix]["cave"]["cals"])
    cals.update(gate.PATCHES["launch_control_v18" + suffix]["cave"]["cals"])
    values = dict(CUTSW=0, CUTRPM=125, CUT_HYST=0, CUT_IPW=65535,
                  CUT_RETARD_RANGE=6, CUT_IGN_ANGLE=angle, LC_SW=255,
                  LC_IGN_ANGLE=255, LC_CUT_IGN_ANGLE=255)
    candidate = bytearray(image)
    for name, value in values.items():
        width = 2 if name.endswith("IPW") else 1
        candidate[cals[name]:cals[name]+width] = value.to_bytes(width, "little")
    return gate._bind_image(bytes(gate.checksum.correct_checksums(candidate)[0]), variant)


def _episode_preemption(gate, version, image, layout, entries):
    """A timing IRQ cannot lose its new E7 to a stale foreground release."""
    candidate = bytearray(image)
    candidate[0x40A0:0x40A4] = bytes.fromhex("FA0000E1")
    candidate = gate._bind_image(candidate, gate._bound_variant(image))

    def seed():
        emu = _seed(gate, candidate, layout)
        # The RAM IRQ fixture publishes C3 then calls the exact installed
        # actual-change entry, as the native timing helper does.
        address = entries["timing_dirty"]
        stub = bytes.fromhex("ECF4E7F8C300F7F803D8FCF4") + bytes(
            (0xDA, address >> 16, address & 255, (address >> 8) & 255, 0xFB, 0x88))
        for index, byte in enumerate(stub):
            emu.write_byte(0xE100+index, byte)
        emu.write(0xFF98, 0x44)
        emu.interrupts.enabled = True
        emu.write_byte(0xD805, 0xE7)
        emu.write_byte(0xD803, 0xC0)  # old episode is about to release
        return emu

    cases = 0
    for owner in ("active", "timing_release"):
        probe = seed()
        steps = _call(probe, entries[owner]).steps
        for boundary in range(1, steps + 1):
            emu = seed()
            emu.interrupts.schedule_irq("ADC", after=boundary)
            _call(emu, entries[owner])
            # An IRQ scheduled at the terminal return needs one real instruction
            # boundary to arbitrate; execute a second ordinary active check.
            if not emu.interrupts.entries:
                _call(emu, entries["active"])
            assert len(emu.interrupts.entries) == 1, (version, owner, "episode IRQ delivered", boundary)
            assert emu.read_byte(0xD803) == 0xC3
            assert _bytes(emu, STATE) == b"\xe7\0\0\0\0\0\0", (version, owner, "episode IRQ not lost", boundary)
            assert emu.reg.psw.IEN and emu.reg.psw.ILVL == 0
            cases += 1
    return cases


def verify_knock_timing_episode(gate, version, image, layout, entries):
    variant = gate._bound_variant(image)
    suffix = "" if version == "MS41.3" else "_" + version.lower().replace(".", "")
    clean = bytes((0xA7, 63, 0, 1, 1, 1, 63))
    dirty = b"\xa7\0\0\0\0\0\0"
    armed = b"\xe7\0\0\0\0\0\0"
    native_image = _configured(gate, gate._cut_reference(version), variant, suffix, 255)
    cases = 0
    # Both final-event hooks, every native channel, both scheduling banks, and
    # lower/equal/later arbitration at each extreme and an interior target.
    for angle in (0, 27, 63):
        candidate = _configured(gate, image, variant, suffix, angle)
        target = 255-angle
        for ordinary in sorted({target-1, target, min(target+1, 255)}):
            for channel in range(6):
                for bank in (0, 1):
                    values = dict(rpm_value=121, cylinder=channel, buffer=bank,
                                  extra_bytes=tuple(zip(STATE, clean)), timeline=True)
                    native = run_timing(native_image, variant, gate._load_emulator,
                                        ordinary=max(ordinary, target), **values)
                    current = run_timing(candidate, variant, gate._load_emulator,
                                         ordinary=ordinary, return_emu=True, **values)
                    assert current[:2] == native[:2], (version, angle, ordinary, channel, bank, "native event outputs")
                    assert current[2] == 0xC3
                    expected = armed if ordinary < target else clean
                    assert _bytes(current[3], STATE) == expected, (version, "actual intervention", angle, ordinary)
                    cases += 1
    candidate = _configured(gate, image, variant, suffix, 27)
    # Pending actual timing or a preceding cut cannot be erased by a no-op.
    for state in (dirty, armed):
        current = run_timing(candidate, variant, gate._load_emulator, ordinary=240,
                             rpm_value=121, extra_bytes=tuple(zip(STATE, state)), return_emu=True)
        assert current[2] == 0xC3 and _bytes(current[3], STATE) == state
        cases += 1

    # The native timing owner records a brief release even if the foreground
    # knock task never runs between the two eligibility episodes. Fault veto
    # and simultaneous cut requests must not hide that release.
    for runtime in (dict(rpm_value=118), dict(rpm_value=121, fault=1),
                    dict(rpm_value=121, fault2=0x4000),
                    dict(rpm_value=118, marker=0xA1)):
        released = run_timing(candidate, variant, gate._load_emulator, ordinary=240,
            old=0xC3, owner=3, extra_bytes=tuple(zip(STATE, armed)), return_emu=True, **runtime)
        assert released[2] != 0xC3 and _bytes(released[3], STATE) == dirty, (version, "event owner releases latch", runtime)
        next_episode = run_timing(candidate, variant, gate._load_emulator, ordinary=240,
            rpm_value=121, extra_bytes=tuple(zip(STATE, _bytes(released[3], STATE))), return_emu=True)
        assert next_episode[2] == 0xC3 and _bytes(next_episode[3], STATE) == dirty
        cases += 1

    # Historical V17 must fail the new clean no-op invariant, with its exact
    # descriptor bytes rather than a hand-mutated surrogate implementation.
    old, _ = gate.patch_ms41.build(gate._cut_reference(version),
        ["ignition_cut_v17"+suffix, "launch_control_v17"+suffix], allow_deprecated=True)
    old = _configured(gate, old, variant, suffix, 27)
    baseline = run_timing(old, variant, gate._load_emulator, ordinary=240, rpm_value=121,
                          extra_bytes=tuple(zip(STATE, clean)), return_emu=True)
    assert baseline[2] == 0xC3 and _bytes(baseline[3], STATE) == dirty
    cases += 1

    emu = _seed(gate, image, layout, sample=255)
    _call(emu, entries["timing_dirty"])
    assert _bytes(emu, STATE) == armed
    emu.write_byte(0xD803, 0xC3)
    for _ in range(3):
        before = _bytes(emu, HISTORY)
        _native(emu, layout)
        assert _bytes(emu, HISTORY) == before and _bytes(emu, STATE) == armed
    # Ordinary cut invalidation preserves the actual-timing episode latch.
    _call(emu, entries["dirty"])
    assert _bytes(emu, STATE) == armed
    emu.write_byte(0xD803, 0xC0)
    _call(emu, entries["active"])
    assert _bytes(emu, STATE) == dirty
    before = _bytes(emu, HISTORY)
    _native(emu, layout)
    assert _bytes(emu, HISTORY) == before, (version, "release retains pending contamination")
    cases += 1

    # Initialization/reinitialization after published C0 never preserves an
    # old E7 episode, including the actual control initializer's dirty call.
    emu.write_byte(0xD805, 0xE7)
    _call(emu, entries["dirty"])
    assert _bytes(emu, STATE) == dirty
    for marker in (0, 0xFF, 0xA6, 0xB7):
        uninitialized = _seed(gate, image, layout, sample=255)
        uninitialized.write_byte(0xD805, marker)
        retained = _bytes(uninitialized, HISTORY)
        _native(uninitialized, layout)
        assert _bytes(uninitialized, HISTORY) == retained
        assert _bytes(uninitialized, STATE) == dirty
        cases += 1

    # A newer published C3 must defeat a stale owner's release attempt.
    emu.write_byte(0xD803, 0xC3)
    _call(emu, entries["timing_dirty"])
    _call(emu, entries["timing_release"])
    assert _bytes(emu, STATE) == armed
    emu.write_byte(0xD803, 0xC0)
    _call(emu, entries["timing_release"])
    assert _bytes(emu, STATE) == dirty
    cases += 1

    # Complete the existing native provenance sequence after episode release.
    # A subsequent no-op-only C3 episode must allow the pending recovery to
    # progress; it may not bless old samples immediately or restart the drain.
    emu.write_byte(0xD803, 0xC3)
    def request_reply(channel):
        _run(emu, layout["request"], layout["request"]+0x1C)
        for byte in (255, channel):
            emu.asc1.rx_inject([byte])
            emu.interrupts.request("ASC1_RX")
            assert emu.interrupts.pec.service_request(0xFF74)
        pc = layout["snapshots"][channel]
        _run(emu, pc, pc+14)
        _run(emu, layout["ingress"], layout["ingress"]+20)
    request_reply(0)
    for phase in range(6):
        pc = layout["windows"][phase % 2]
        emu.write_byte(0xFA80, phase)
        emu.write(0xFA90, 0x140A)
        for tick in (10, 20):
            emu.reg.r[0] = tick
            _run(emu, pc, pc+18)
    for channel in range(6):
        request_reply(channel)
    assert _bytes(emu, STATE) == clean
    emu.reg.r[0] = 0xFA40
    before = _bytes(emu, HISTORY)
    _native(emu, layout)
    assert _bytes(emu, HISTORY) != before
    cases += 1
    irq_cases = _episode_preemption(gate, version, image, layout, entries)
    return {"cases": cases+irq_cases, "episode_irq_boundaries": irq_cases,
            "v17_negative_controls": 1,
            "scope": "Never-modified eligibility stays native; any actual timing intervention remains latched through episode release and native recovery"}
