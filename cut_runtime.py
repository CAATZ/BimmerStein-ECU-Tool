"""Installed cut-patch RAM ABI and AlphaN load-publication probes.

These probes select an address layout, not full-ROM integrity. Native RAM
contents, including an A0-looking byte at E847, are not patch identities.
"""

from functools import lru_cache

from engines.patcher.patch_ms41 import load_patches


def cut_state_address(revision):
    return 0xD801 if revision in ("V16", "V17", "V18") else 0xE847


def cut_program_state_probe(patch_id):
    """A short code window containing the descriptor's marker operand."""
    patch = load_patches().get(patch_id)
    if patch is None:
        return None
    base = patch["cave"]["base"]
    body = next(bytes.fromhex(edit["data"]) for edit in patch["edits"]
                if edit["off"] == base)
    index = body.index(cut_state_address(patch["version"]).to_bytes(2, "little"))
    start = max(0, index - 2)
    return (base + start) ^ 0x4000, body[start:start + 16]


@lru_cache(maxsize=None)
def cut_runtime_probes(ecu_id):
    """Known revision profiles derived from the canonical patch descriptors."""
    suffix = {"1429861": "_ms410", "1437806": "_ms411",
              "1406464": "_ms412", "SHINDE1": ""}.get(ecu_id)
    if suffix is None:
        return ()
    patches = load_patches()
    profiles = []
    for revision in ("V18", "V17", "V16", "V15", "V14", "V13", "V12", "V11", "V10", "V9"):
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
            probes.append((splice["base"] ^ 0x4000, edits[splice["base"]][:16]))
        profiles.append((revision, tuple(dict.fromkeys(probes))))
    return tuple(profiles)


def detect_cut_runtime(ds2, ecu_id):
    """Return V16 (D801, including V17/V18) or legacy ABI, or None.

    Historical minor revisions can share these short prefixes. They are all
    labeled legacy rather than claiming an exact minor-revision identity.
    """
    return _detect_runtime(ds2, cut_runtime_probes(str(ecu_id)))


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
    for revision in ("V18", "V17", "V16", "V15", "V14", "V13", "V12", "V11", "V10", "V9", "V8", "V7"):
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
            probes.append((splice["base"] ^ 0x4000, edits[splice["base"]][:16]))
        probes.append(cut_program_state_probe(patch_id))
        profiles.append((revision, tuple(dict.fromkeys(probes))))
    return tuple(profiles)


def detect_launch_runtime(ds2, ecu_id):
    """Return Launch's V16/legacy ABI, or None; require matching cut ABI too.

    Callers must compare this result with detect_cut_runtime before exposing
    shared Launch channels. Neither probe establishes complete-ROM integrity.
    """
    return _detect_runtime(ds2, launch_runtime_probes(str(ecu_id)))


@lru_cache(maxsize=1)
def _alphan_failsafe_probes():
    patch = load_patches()["alphan_failsafe"]
    edits = {edit["off"]: bytes.fromhex(edit["data"])
             for edit in patch["edits"]}
    probes = []
    for splice in patch["cave"]["splices"]:
        probes.append((splice["off"] ^ 0x4000, edits[splice["off"]]))
        # V3 entry points share one descriptor payload rather than separate edits.
        base = splice["base"]
        body = next(data[base - offset:base - offset + 16]
                    for offset, data in edits.items()
                    if offset <= base < offset + len(data))
        probes.append((base ^ 0x4000, body))
    return tuple(probes)


def detect_alphan_failsafe_runtime(ds2, ecu_id):
    """Recognize current SS1v2 AlphaN V3 hooks before exposing its load mirrors."""
    if str(ecu_id) != "SHINDE1":
        return False
    return _detect_runtime(ds2, (("V3", _alphan_failsafe_probes()),)) is not None


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
            return "V16" if revision in ("V16", "V17", "V18") else "legacy"
    return None
