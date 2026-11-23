"""Install the V20 observation and crank-history guards on a cloned descriptor."""
from pathlib import Path


PATCHER = Path(__file__).parent
# New guard, previous cut-active entry and native segment/misfire code displacement.
LAYOUT = {
    "MS41.0": ("ms410", 0x33500, 0x32D20, None),
    "MS41.1": ("ms411", 0x3FE50, 0x3FBE0, 0xA7C),
    "MS41.2": ("ms412", 0x3E3E8, 0x3E1A0, 0),
    "MS41.3": ("ms412", 0x3E3E8, 0x3E1A0, 0),
}


def _jump(target):
    return bytes((0xFA, target >> 16, target & 255, (target >> 8) & 255))


def apply(cut, launch, family, stock):
    family_source, base, old_guard, displacement = LAYOUT[family]
    path = PATCHER / f"ignition_cut_v20_{family_source}_recovery.hex"
    data = bytes.fromhex(path.read_text())
    start = base ^ 0x4000
    assert stock[start:start + len(data)] == b"\xff" * len(data)
    cut["edits"].append({"name": "Committed combustion and private recovery", "off": start,
                         "expect": (b"\xff" * len(data)).hex(), "data": data.hex()})

    guard = next(edit for edit in cut["edits"] if edit["off"] == old_guard ^ 0x4000)
    body = bytes.fromhex(guard["data"])
    assert body[:4] == bytes.fromhex("f3 f8 01 d8")
    body = _jump(base) + body[4:]
    if family == "MS41.0":
        # The historical bank pending stores replaced the entire D802 byte.
        # Preserve the ISR-owned actual-charge flag during a timing episode.
        stores = bytes.fromhex("f7 f8 02 d8")
        assert body.count(stores) == 2
        body = body.replace(stores, bytes.fromhex("75 f8 02 d8"))
    guard["data"] = body.hex()
    if displacement is None:
        return

    # Repoint the two existing native hooks and install the learner gate
    # after native fault-reset priority, before its six-segment qualification.
    for cpu, offset in ((0x3021E, 4), (0x30468, 8), (0x304F2, 12)):
        site = (cpu + displacement) ^ 0x4000
        target = base + offset
        if cpu == 0x304F2:
            expected = bytes.fromhex("f3 f8 82 f5" if displacement else "f3 f8 5c f5")
            assert stock[site:site + 4] == expected
            cut["edits"].append({"off": site, "expect": expected.hex(), "data": _jump(target).hex()})
            cut["cave"]["splices"].append({"off": site, "base": target ^ 0x4000, "op": "jmps"})
        else:
            edit = next(e for e in cut["edits"] if e["off"] == site)
            assert bytes.fromhex(edit["expect"]) == stock[site:site + 4]
            splice = next(s for s in cut["cave"]["splices"] if s["off"] == site)
            assert bytes.fromhex(edit["data"]) == _jump(splice["base"] ^ 0x4000)
            edit["data"] = _jump(target).hex()
            splice["base"] = target ^ 0x4000
