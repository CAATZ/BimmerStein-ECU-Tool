"""Install the four foreground knock hooks. Crank capture and ASC1 receive stay stock."""
from pathlib import Path


# All four assembled sources have the same instruction layout.
ENTRY_OFFSETS = {
    "dirty": 0, "timing_dirty": 54, "timing_release": 78, "initialize": 118,
    "active": 162, "request": 246, "ingress": 316, "prefilter": 522, "global_gate": 622,
}


def apply(cut, launch, family, stock):
    cave = cut["cave"]
    layout = cave["knock_layout"]
    entries = {name: layout["base"] + offset for name, offset in ENTRY_OFFSETS.items()}
    for name in ("dirty", "timing_dirty", "timing_release"):
        assert entries[name] == cave["knock_entries"][name]
    cave["knock_entries"] = entries
    stem = f"ignition_cut_v20_ms41{family[-1]}_knock.hex"
    payload = bytes.fromhex(Path(__file__).with_name(stem).read_text())
    entry = next(edit for edit in cut["edits"] if edit["off"] == layout["base"] ^ 0x4000)
    assert stock[entry["off"]:entry["off"] + len(payload)] == b"\xff" * len(payload)
    entry.update(data=payload.hex(), expect=(b"\xff" * len(payload)).hex(),
                 name="knock recovery from completed native 0102 replies")
    retired = {pc ^ 0x4000 for pc in (*layout["windows"], *layout["snapshots"])}
    for edit in cut["edits"]:
        if edit["off"] in retired:
            edit.update(data=edit["expect"], name="unchanged native crank capture")
    cave["splices"] = [splice for splice in cave["splices"] if splice["off"] not in retired]
    for name in ("request", "ingress", "prefilter", "global_gate"):
        address = entries[name]
        off = layout[name] ^ 0x4000
        splice = next(s for s in cave["splices"] if s["off"] == off)
        edit = next(e for e in cut["edits"] if e["off"] == off)
        op = 0xDA if name == "request" else 0xFA
        edit["data"] = bytes((op, address >> 16, address & 255, (address >> 8) & 255)).hex()
        splice["base"] = address ^ 0x4000
