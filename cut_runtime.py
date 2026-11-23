"""Installed cut-patch RAM ABI and AlphaN load-publication probes.

These probes select an address layout, not full-ROM integrity. Native RAM
contents, including an A0-looking byte at E847, are not patch identities.
"""

from functools import lru_cache

from engines.patcher.patch_ms41 import load_patches


def cut_state_address(revision):
    return 0xD801 if revision in ("V16", "V17", "V18", "V19", "V20") else 0xE847


def cut_program_state_probe(patch_id):
    """A short code window containing the descriptor's marker operand."""
    patch = load_patches().get(patch_id)
    if patch is None:
        return None
    cave = patch["cave"]
    base = cave["charge_event"] ^ 0x4000 if "charge_event" in cave else cave["base"]
    body = next(bytes.fromhex(edit["data"]) for edit in patch["edits"]
                if edit["off"] == base)
    index = body.index(cut_state_address(patch["version"]).to_bytes(2, "little"))
    start = max(0, index - 2)
    return (base + start) ^ 0x4000, body[start:start + 16]


def _code_prefix(edits, offset):
    # V20's common charge/fuel wrappers have several entry points in one edit.
    for start, data in edits.items():
        if start <= offset < start + len(data):
            index = offset - start
            return data[index:index + 16]
    raise ValueError(f"patch code entry {offset:#x} has no descriptor payload")


@lru_cache(maxsize=None)
def cut_runtime_probes(ecu_id):
    """Known revision profiles derived from the canonical patch descriptors."""
    suffix = {"1429861": "_ms410", "1437806": "_ms411",
              "1406464": "_ms412", "SHINDE1": ""}.get(ecu_id)
    if suffix is None:
        return ()
    patches = load_patches()
    profiles = []
    for revision in ("V20", "V19", "V18", "V17", "V16", "V15", "V14", "V13", "V12", "V11", "V10", "V9"):
        patch = patches.get("ignition_cut_" + revision.lower() + suffix)
        if patch is None:
            continue
        edits = {edit["off"]: bytes.fromhex(edit["data"])
                 for edit in patch["edits"]}
        cave = patch["cave"]
        splices = cave.get("splices") or [
            {"off": cave["splice_off"], "base": cave["base"]}]
        probes = []
        # Both charge hooks, the request owner, and their code prefixes. The
        # gate prefix includes the state-address operand, distinguishing V16.
        for splice in splices[:3]:
            probes.append((splice["off"] ^ 0x4000, edits[splice["off"]]))
            probes.append((splice["base"] ^ 0x4000, _code_prefix(edits, splice["base"])))
        if "charge_event" in cave:
            probes.append(cut_program_state_probe(patch["id"]))
        profiles.append((revision, tuple(dict.fromkeys(probes))))
    return tuple(profiles)


def detect_cut_runtime(ds2, ecu_id):
    """Return V16 (D801, including V17–V20) or legacy ABI, or None.

    Historical minor revisions can share these short prefixes. They are all
    labeled legacy rather than claiming an exact minor-revision identity.
    """
    return _cut_abi(_detect_runtime(ds2, cut_runtime_probes(str(ecu_id))))


def _cut_abi(revision):
    if revision is None:
        return None
    return "V16" if revision in ("V16", "V17", "V18", "V19", "V20") else "legacy"


@lru_cache(maxsize=None)
def launch_runtime_probes(ecu_id):
    """Launch activation hooks and code, independent of the shared cut owner.

    Checking the marker operand also separates relocated V16 state from the
    historical E847 layout. Calibration values and RAM contents are not proof
    that a Launch patch is installed.
    """
    suffix = {"1429861": "_ms410", "1437806": "_ms411",
              "1406464": "_ms412", "SHINDE1": ""}.get(ecu_id)
    if suffix is None:
        return ()
    patches = load_patches()
    profiles = []
    for revision in ("V20", "V19", "V18", "V17", "V16", "V15", "V14", "V13", "V12", "V11", "V10", "V9", "V8", "V7"):
        patch_id = "launch_control_" + revision.lower() + suffix
        patch = patches.get(patch_id)
        if patch is None:
            continue
        edits = {edit["off"]: bytes.fromhex(edit["data"])
                 for edit in patch["edits"]}
        cave = patch["cave"]
        splices = cave.get("splices") or [
            {"off": cave["splice_off"], "base": cave["base"]}]
        probes = []
        for splice in splices:
            probes.append((splice["off"] ^ 0x4000, edits[splice["off"]]))
            probes.append((splice["base"] ^ 0x4000, _code_prefix(edits, splice["base"])))
        probes.append(cut_program_state_probe(patch_id))
        profiles.append((revision, tuple(dict.fromkeys(probes))))
    return tuple(profiles)


def detect_launch_runtime(ds2, ecu_id):
    """Return Launch's V16/legacy ABI, or None; require matching cut ABI too.

    Callers must compare this result with detect_cut_runtime before exposing
    shared Launch channels. Neither probe establishes complete-ROM integrity.
    """
    return _cut_abi(_detect_runtime(ds2, launch_runtime_probes(str(ecu_id))))


@lru_cache(maxsize=1)
def _alphan_failsafe_probes():
    patches = load_patches()
    profiles = []
    for pid in ("alphan_failsafe", "alphan_failsafe_v3"):
        patch = patches[pid]
        probes = []
        # Try short, modified hooks first to avoid reading entire caves on
        # a stock ECU. Read every descriptor byte before admitting a revision.
        edits = sorted(patch["edits"], key=lambda e: (e["expect"] == e["data"], len(e["data"])))
        for edit in (*edits, *patch.get("match_anchors", ())):
            offset = edit["off"]
            payload = bytes.fromhex(edit["data"])
            while payload:
                # READ_MEM accepts at most 247 bytes. CPU/file order flips
                # at each 16 KiB boundary, even within a descriptor edit.
                size = min(len(payload), 0xF7, 0x4000 - (offset & 0x3FFF))
                probes.append((offset ^ 0x4000, payload[:size]))
                offset += size
                payload = payload[size:]
        profiles.append((patch["version"], tuple(probes)))
    return tuple(profiles)


def detect_alphan_failsafe_runtime(ds2, ecu_id):
    """Return the complete V4 or V3 revision, or None for unknown/mixed code."""
    if str(ecu_id) != "SHINDE1":
        return None
    return _detect_runtime(ds2, _alphan_failsafe_probes())


def _detect_runtime(ds2, profiles):
    reads = {}
    for revision, probes in profiles:
        for address, expected in probes:
            key = address, len(expected)
            if key not in reads:
                raw = bytes(ds2.read_mem(*key))
                if len(raw) != len(expected):
                    raise ValueError(f"short patch-program read {len(raw)}/{len(expected)}")
                reads[key] = raw
            if reads[key] != expected:
                break
        else:
            return revision
    return None
