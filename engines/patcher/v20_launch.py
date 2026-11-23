"""Install V20's charge-time RPM check and the native fuel publication hooks."""
from pathlib import Path


HERE = Path(__file__).parent
# New cave, existing CC6 cave, late fuel control, native fuel-stage writer.
LAYOUT = {
    "MS41.0": (0x33E00, 0x32820, 0x32A00, 0x2510E),
    "MS41.1": (0x07000, 0x3F680, 0x3F8C0, 0x272B2),
    "MS41.2": (0x3F800, 0x3DC70, 0x3E600, 0x26DB2),
    "MS41.3": (0x3F800, 0x3DC70, 0x3E600, 0x26DB2),
}


def _jump(cpu):
    return bytes((0xFA, cpu >> 16, cpu & 255, (cpu >> 8) & 255))


def _edit(desc, cpu):
    return next(e for e in desc["edits"] if e["off"] == (cpu ^ 0x4000))


def _hook(desc, stock, cpu, target, preimage):
    offset = cpu ^ 0x4000
    assert stock[offset:offset + len(preimage)] == preimage, (desc["id"], hex(cpu))
    data = _jump(target) + b"\xcc\x00" * ((len(preimage)-4)//2)
    desc["edits"].append({"off": offset, "expect": preimage.hex(), "data": data.hex()})
    desc["cave"]["splices"].append({"off": offset, "base": target ^ 0x4000, "op": "jmps"})


def apply(cut, launch, family, stock):
    base, old_gate, control, stage_site = LAYOUT[family]
    name = "ms41" + family[-1]
    for desc, filename, cpu in (
        (cut, f"ignition_cut_v20_{name}_charge", base),
        (launch, f"launch_control_v20_{name}_fuel", base + 0x100),
    ):
        data = bytes.fromhex((HERE / (filename + ".hex")).read_text())
        offset = cpu ^ 0x4000
        assert len(data) <= 256 and stock[offset:offset + len(data)] == b"\xff"*len(data)
        desc["edits"].append({"name": filename, "off": offset, "expect": (b"\xff"*len(data)).hex(), "data": data.hex()})

    old = _edit(cut,old_gate)
    body = bytes.fromhex(old["data"])
    assert body[:4] == bytes.fromhex("ecf4 f3f8")
    body = _jump(base) + body[4:]  # Preserve other entries in the same V19 cave.
    dirty = cut["cave"]["knock_entries"]["dirty"]
    dirty_call = bytes((0xDA, dirty >> 16, dirty & 255, (dirty >> 8) & 255))
    if family == "MS41.3":
        init = body.index(dirty_call, 0x40)
        # RL5 is zero here. Clear the latent Launch stage before publishing
        # D801's initialized marker, consistent with the other cold entries.
        body = body[:init] + bytes.fromhex("f7fa0dd8") + body[init+4:]
    old["data"] = body.hex()
    # Reach the charge owner directly from both native CC6 paths. A second
    # far jump would extend every permitted charge and every omitted charge.
    for splice in cut["cave"]["splices"][:2]:
        assert splice["base"] == old_gate ^ 0x4000 and splice["op"] == "calls"
        hook = _edit(cut, splice["off"] ^ 0x4000)
        hook["data"] = (b"\xda" + _jump(base)[1:]).hex()
        splice["base"] = base ^ 0x4000

    # A requested mode alone cannot dirty a sample. Preserve all legacy entry
    # offsets in the control cave, including SS1v2's separate CC6 request.
    old = _edit(cut,control)
    body = bytes.fromhex(old["data"])
    start = body.index(bytes.fromhex("f3fa01d869a72d"))
    end = body.index(bytes.fromhex("02d8"),start)+2
    assert bytes.fromhex("75fa") in body[start:end] or bytes.fromhex("f7fa") in body[start:end]
    body = body[:start] + b"\xcc\x00"*((end-start)//2) + body[end:]
    if family != "MS41.3":
        # The native fuel finalizer keeps its original call and return ABI.
        # All standalone decisions now live in the charge cave.
        foreground = bytes.fromhex((HERE / f"ignition_cut_v20_{name}_foreground.hex").read_text())
        body = foreground.ljust(len(body), b"\xff")
    old["data"] = body.hex()

    cfg = launch["cave"]
    requester = cfg["base"] ^ 0x4000
    old = _edit(launch,requester)
    data = bytes.fromhex((HERE / f"launch_control_v20_{name}_cave_a.hex").read_text())
    length = len(bytes.fromhex(old["data"]))
    assert len(data) <= length
    old["data"] = data.ljust(length,b"\xff").hex()
    if family in ("MS41.2","MS41.3"):
        # The former site preceded the very speed fault that must disarm Launch.
        first = cfg["splice_off"]
        assert first == (0x39928 ^ 0x4000)
        anchor = _edit(launch,0x39928)
        anchor["data"] = anchor["expect"]
        cfg["splices"] = [s for s in cfg["splices"] if s["off"] != first]
        _hook(launch,stock,0x399B2,requester,bytes.fromhex("f3f83cfc"))
        cfg["splice_off"] = 0x399B2 ^ 0x4000
    else:
        speed_exit = 0x2F2C2 if family=="MS41.0" else 0x3B488
        rpm = 0xFAE6 if family=="MS41.0" else 0xFC3C
        _hook(launch,stock,speed_exit,requester+2,b"\xf3\xf8"+rpm.to_bytes(2,"little"))

    stage = 0xFADE if family=="MS41.0" else 0xFC34
    store = b"\xf7\xf8" + stage.to_bytes(2,"little")
    clear = b"\xf7\x8e" + stage.to_bytes(2,"little")
    owner = base + 0x100
    for delta, entry, original in ((0,2,store),(0x1C,0,store),(0x26,2,store),(0x2C,4,clear)):
        _hook(launch,stock,stage_site+delta,owner+entry,original)
    injector = 0x5960 if family=="MS41.0" else 0x59E2
    _hook(launch,stock,injector,owner+6,bytes.fromhex("7124 f7f204fd"))
    cut["cave"]["charge_event"] = base
    cfg["fuel_stage_owner"] = owner
