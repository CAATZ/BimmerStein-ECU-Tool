"""Native roughness and V18 state must survive each other's complete calls.

These are controlled native-function/control-splice sequences, not a claim of
arbitrary interrupt scheduling or physical ECU validation.
"""
from hashlib import sha256

from engines.patcher.verify_launch_retard_state import CUT_STATE_ADDR


# Exact source image, native entry, raw phase byte, retired collision pair.
_LAYOUTS = {
    "MS41.0": ("c61674c5812f5fe0a4a6f86e96311e9a8e540a905f9e7f681fdd045254249521",
               0x2B026, 0xFB49, "v15_ms410"),
    "MS41.1": ("6129b8e8884cd8321c94546e2beba4c8b4afcfca0d7749a341a82cea37f99aa3",
               0x2F8FC, 0xFC9F, "v14_ms411"),
    "MS41.2": ("2d3ab7db6fe0f9a1f4680339f416aeeef43871730cd468cfd990f6dc80c03208",
               0x2F6BC, 0xFC9F, "v14_ms412"),
    "MS41.3": ("89f31cbb70466ad39b23571e3c1c533251275182e8e2502c214a208e83f64487",
               0x2F6BC, 0xFC9F, "v14"),
}


def _bytes(emu, start, length):
    return bytes(emu.read_byte(address) for address in range(start, start + length))


def _seed_arrays(emu):
    for index in range(6):
        for base, value in ((0xE830, 0x1000 + index * 0x111), (0xE83C, 0x8000),
                            (0xE848, 0x4321), (0xE854, 0x5678)):
            emu.write(base + index * 2, value)


def _native_inputs(emu, version, phase, fault, valid):
    emu.write_byte(_LAYOUTS[version][2], phase)
    emu.write(0xFD30, fault)
    emu.write_byte(0xE861, valid)
    emu.write(0xFAF8, 3000)
    emu.write(0xFAE4 if version == "MS41.0" else 0xFC3A, 1200)
    emu.write_byte(0xE8E5, 80)
    emu.write_byte(0xE860, 128)
    for address, value in ((0xFB58, 3), (0xFB5A, 7), (0xFB5C, 4),
                           (0xFB5E, 11), (0xFD54, 0xACCF)):
        emu.write(address, value)
    for address, value in ((0xFB60, 1), (0xFB61, 2), (0xFB62, 4), (0xFB63, 0xA7)):
        emu.write_byte(address, value)
    emu.reg.r[9] = 0x1234  # The native fault branch retains incoming R9.


def _control(gate, emu, version):
    options = {}
    if version in gate.OLDER_FEATURE_LAYOUTS:
        layout = gate.OLDER_FEATURE_LAYOUTS[version]
        options = {key: layout[key] for key in ("rpm_address", "input_bytes", "ipw_addresses")}
        options["hook"] = layout["control_hook"]
    # Retain published state between calls; exercise real initialization too.
    result = gate._execute_control(emu, rpm=0x40, state=emu.read_byte(CUT_STATE_ADDR),
                                   prime_ignition=False, **options)
    assert result[2], (version, "control splice ABI")


def verify_patch_ram(gate, version):
    digest, entry, _, predecessor = _LAYOUTS[version]
    stock = gate._cut_reference(version)
    assert sha256(stock).hexdigest() == digest, (version, "exact native RAM owner")
    image = gate.CUT_GUARD_LAYOUTS[version]["image"]()
    # The complete roughness body through RETS is identical in the installed image.
    end = entry + 0x136
    assert bytes(stock[pc ^ 0x4000] for pc in range(end - 2, end)) == bytes.fromhex("db00")
    assert all(stock[pc ^ 0x4000] == image[pc ^ 0x4000] for pc in range(entry, end))
    cases = 0
    for fault, valid in ((0, 0x3F), (0, 0), (2, 0), (4, 0), (8, 0)):
        emu, reference = gate._emu(image, dpp0=4), gate._emu(stock, dpp0=4)
        for machine in (emu, reference):
            _seed_arrays(machine)
        for phase in range(6):
            before = _bytes(emu, 0xE830, 0x32)
            _control(gate, emu, version)
            assert _bytes(emu, 0xE830, 0x32) == before, (version, "patch changed native arrays", phase)
            published = _bytes(emu, CUT_STATE_ADDR, 12)
            assert published[0] & 0xF0 == 0xA0
            for machine in (emu, reference):
                _native_inputs(machine, version, phase, fault, valid)
                gate._call_native(machine, entry)
            assert _bytes(emu, CUT_STATE_ADDR, 12) == published, (
                version, "native changed V18 state", phase, fault)
            assert _bytes(emu, 0xE830, 0x32) == _bytes(reference, 0xE830, 0x32), (
                version, "native roughness differs from stock", phase, fault)
            cases += 1
        # Live cut/recovery/timing publications must also survive native writes.
        for phase in range(6):
            for offset, value in enumerate((0xA7, 0x1F, 0xC3, 3)):
                emu.write_byte(CUT_STATE_ADDR + offset, value)
            published = _bytes(emu, CUT_STATE_ADDR, 12)
            _native_inputs(emu, version, phase, fault, valid)
            gate._call_native(emu, entry)
            assert _bytes(emu, CUT_STATE_ADDR, 12) == published
            cases += 1

    # Exact retained predecessors must expose both directions of the collision.
    ids = ["ignition_cut_" + predecessor, "launch_control_" + predecessor]
    old, _ = gate.patch_ms41.build(stock, ids, allow_deprecated=True)
    old = gate._bind_image(old, gate._bound_variant(stock))
    for phase in (0, 4, 5):
        emu = gate._emu(old, dpp0=4)
        _seed_arrays(emu)
        for offset, value in enumerate((0xA0, 0x0F, 0xC3, 3)):
            emu.write_byte(0xE847 + offset, value)
        _native_inputs(emu, version, phase, 0, 0x3F)
        before = _bytes(emu, 0xE847, 4)
        gate._call_native(emu, entry)
        assert _bytes(emu, 0xE847, 4) != before, (version, "retired collision control", phase)
    emu = gate._emu(old, dpp0=4)
    _seed_arrays(emu)
    before = _bytes(emu, 0xE830, 0x32)
    _control(gate, emu, version)
    assert _bytes(emu, 0xE830, 0x32) != before, (version, "retired initialization collision")
    print(f"[PASS] {version} V18/native RAM coexistence ({cases} cases, 4 retired collision controls)")
