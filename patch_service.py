"""Typed service layer between application workflows and the internal patch module.

Filters the patch library to a base image's detected version, computes byte
collisions for a selection (so the UI can gray out incompatible patches), and
delegates the actual compose to engines.patcher.patch_ms41.build. Pure logic —
no Qt, no file I/O.
"""
from ms41 import MS41ECU
from engines.patcher import patch_ms41

PatchError = patch_ms41.PatchError

# This bootstrap is an internal Soft-BSL installation detail, not a general-purpose
# firmware patch. Keep its definition available to the installer while omitting it
# from the Patches-tab catalogue, where baking it into a BIN can cause collisions.
PATCH_TAB_HIDDEN_IDS = frozenset({
    "door_0x43", "door_0x43_ms410", "door_0x43_ms411",
})


def definitions():
    """Return the internal patch definitions used by every application workflow."""
    return patch_ms41.load_patches()


def is_applied(image, patch):
    return patch_ms41.is_applied(bytes(image), patch)


def base_version(data):
    """The program-side version string a base should be patched as (e.g. 'MS41.3'), or None."""
    return MS41ECU.resolve_version(bytes(data))["program"]


def _installed_patch_state(data, all_patches=None):
    """Return (version, installed ids, shadowed predecessor ids) for one image."""
    data = bytes(data)
    ver = base_version(data)
    all_patches = all_patches or patch_ms41.load_patches()
    installed_ids = {
        pid for pid, patch in all_patches.items()
        if patch_ms41.supports_target(patch, ver)
        and patch_ms41.is_applied(data, patch)
    }

    # A successor may retain every signature byte of its predecessor. Treat
    # those descriptor matches as one effective installed revision.
    shadowed_ids = set()
    for pid in installed_ids:
        supersedes = all_patches[pid].get("supersedes")
        superseded_ids = (
            [supersedes] if isinstance(supersedes, str) else list(supersedes or [])
        )
        shadowed_ids.update(lid for lid in superseded_ids if lid in installed_ids)
    return ver, installed_ids, shadowed_ids


def installed_dependents(data, patch_id):
    """Effective installed patches that directly require ``patch_id``."""
    all_patches = patch_ms41.load_patches()
    _ver, installed_ids, shadowed_ids = _installed_patch_state(data, all_patches)
    effective_ids = installed_ids - shadowed_ids
    return sorted(
        pid for pid in effective_ids
        if pid != patch_id and patch_id in all_patches[pid].get("requires", [])
    )


def available_patches(data):
    """Selectable patches whose target matches the base's program version, each with a
    health badge. Patches flagged "deprecated" (e.g. a superseded patch version) are never
    offered for selection, but if such a predecessor is already present in `data`, the patch
    that supersedes it carries a `legacy` list (one {id, label} entry per predecessor that is
    currently installed) so the UI can warn the user and offer to remove that old/broken
    revision instead of silently hiding it. `supersedes` may be a single id or a list."""
    all_patches = patch_ms41.load_patches()
    ver, installed_ids, shadowed_ids = _installed_patch_state(data, all_patches)
    effective_ids = installed_ids - shadowed_ids

    out = []
    for pid, p in sorted(all_patches.items()):
        if pid in PATCH_TAB_HIDDEN_IDS:
            continue
        installed = pid in installed_ids
        required_by = sorted(
            other_id for other_id in effective_ids
            if other_id != pid and pid in all_patches[other_id].get("requires", [])
        )
        if p.get("deprecated"):
            # Deprecated patches are hidden UNLESS one is actually installed on this base - then surface a
            # remove-only row so a stray/old install can be reverted directly from the tab (no need to first
            # select its successor). Not selectable/installable; the UI shows only its "Remove" control.
            if not installed or pid in shadowed_ids:
                continue
            out.append({
                "id": pid, "title": p.get("title", ""), "description": p.get("description", ""),
                "user_description": p.get("user_description") or p.get("description", ""),
                "target": ver, "status": p.get("status", "DEPRECATED"),
                "tested": p.get("tested"),
                "requires": [], "conflicts": p.get("conflicts", []),
                "ok": False, "badge": "DEPRECATED - installed; remove recommended",
                "installed": True, "needs_boot": patch_ms41.needs_boot_write(p),
                "deprecated": True, "removable": not required_by,
                "required_by": required_by, "legacy": [],
            })
            continue
        if not patch_ms41.supports_target(p, ver):
            continue
        errs = patch_ms41.validate_splices(p)
        entry = {
            "id": pid,
            "title": p.get("title", ""),
            "description": p.get("description", ""),
            "user_description": p.get("user_description") or p.get("description", ""),
            "target": ver,
            "status": p.get("status", ""),
            "tested": p.get("tested"),
            "requires": p.get("requires", []),
            "conflicts": p.get("conflicts", []),
            "ok": len(errs) == 0,
            "badge": "OK" if not errs else "; ".join(errs),
            "installed": installed,
            "needs_boot": patch_ms41.needs_boot_write(p),
            "deprecated": False, "removable": installed and not required_by,
            "required_by": required_by,
        }
        # A superseding patch (e.g. ignition_cut_v7) can replace one or more predecessors.
        # Flag every predecessor ACTUALLY present on this base so the UI can offer to remove
        # it — an old/broken revision must be reverted before the new one will apply.
        sup = p.get("supersedes")
        sup_ids = [sup] if isinstance(sup, str) else list(sup or [])
        entry["legacy"] = [
            {"id": lid, "label": all_patches[lid].get("label", lid)}
            for lid in sup_ids
            if lid in installed_ids and lid not in shadowed_ids
        ]
        out.append(entry)
    return out


def collisions(selected_ids):
    """Return the set of OTHER patch ids whose byte-edits overlap the current selection."""
    patches = patch_ms41.load_patches()
    sel_ranges = []
    for pid in selected_ids:
        if pid in patches:
            sel_ranges += patch_ms41._ranges(patches[pid])
    blocked = set()
    for pid, p in patches.items():
        if pid in selected_ids:
            continue
        if any(patch_ms41._overlap(a, b) for a in sel_ranges for b in patch_ms41._ranges(p)):
            blocked.add(pid)
    return blocked


def revert_patch(base_data, patch_id):
    """Undo one already-applied patch on base_data, restoring its pre-patch stock bytes.
    Returns the new bytes. Raises PatchError (via patch_ms41.revert) if patch_id is unknown
    or not currently applied."""
    patches = patch_ms41.load_patches()
    p = patches.get(patch_id)
    if p is None:
        raise patch_ms41.PatchError(f"no such patch: {patch_id}")
    dependents = installed_dependents(base_data, patch_id)
    if dependents:
        joined = ", ".join(dependents)
        raise patch_ms41.PatchError(
            f"cannot remove '{patch_id}': installed patch(es) {joined} require it; "
            "remove the dependent patch(es) first"
        )
    return patch_ms41.revert(bytes(base_data), p)


def build_image(base_data, selected_ids, marker=None):
    """Compose the selected patches onto base_data. Returns (bytes, log_lines).
    Raises patch_ms41.PatchError on any rejection (bad base, collision, expect mismatch...)."""
    return patch_ms41.build(bytes(base_data), list(selected_ids), marker=marker)


# SA1 / boot window (file offsets) — the region DS2 and un-armed soft-BSL never write.
SA1_LO  = patch_ms41.BOOT_FILE_LO      # 0x4000
SA1_HI  = patch_ms41.BOOT_FILE_HI      # 0x6000
SA1_LEN = SA1_HI - SA1_LO              # 0x2000


def boot_write_patches_in(image):
    """IDs of patches present in `image` that write the boot/SA1 region (file 0x4000-0x5FFF).
    Those bytes are not written by DS2 or an un-armed soft-BSL flash."""
    patches = patch_ms41.load_patches()
    return sorted(pid for pid, p in patches.items()
                  if patch_ms41.needs_boot_write(p) and patch_ms41.is_applied(image, p))


def boot_patch_read_ranges(image):
    """Minimal merged FILE-offset ranges needed to prove the image's applied SA1 patches.

    Unlike the legacy gate, this does not request the complete 8 KB SA1 window. It includes
    only each applied patch edit's intersection with file 0x4000..0x5FFF. Overlapping and
    directly-adjacent edits are merged; unrelated descriptor/identity bytes are never read.
    """
    patches = patch_ms41.load_patches()
    ranges = []
    for patch_id in boot_write_patches_in(image):
        for edit in patches[patch_id]["edits"]:
            lo = max(int(edit["off"]), SA1_LO)
            hi = min(int(edit["off"]) + len(bytes.fromhex(edit["data"])), SA1_HI)
            if lo < hi:
                ranges.append((lo, hi))
    merged = []
    for lo, hi in sorted(ranges):
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def sa1_window(evidence):
    """Normalize ECU evidence to the SA1 byte window (file 0x4000..0x5FFF, len 0x2000): accepts
    a full 256 KB ROM read (slices it), an already-sliced 0x2000 SA1 buffer, or None. Returns
    None if the input can't supply the whole window (the caller then fails safe)."""
    if evidence is None:
        return None
    b = bytes(evidence)
    if len(b) == SA1_LEN:
        return b
    if len(b) >= SA1_HI:
        return b[SA1_LO:SA1_HI]
    return None


def _sa1_edits_present(patch, ecu_sa1):
    """True iff every SA1-region byte this patch writes is already present in `ecu_sa1`. Edit
    bytes OUTSIDE [0x4000, 0x6000) are ignored — those are delivered by the normal DS2 / soft-BSL
    write; only the SA1 portion is at risk of being dropped. Scoping to the patch's own edit
    bytes is what keeps per-unit descriptor/coding drift (serial, VIN, boot-CRC) from tripping
    the gate — that drift is real but lies outside every boot patch's edits."""
    for e in patch["edits"]:
        off = e["off"]
        dat = bytes.fromhex(e["data"])
        a = max(off, SA1_LO)
        b = min(off + len(dat), SA1_HI)
        if a >= b:
            continue                                   # this edit doesn't touch SA1
        if dat[a - off:b - off] != ecu_sa1[a - SA1_LO:b - SA1_LO]:
            return False
    return True


def _sparse_bytes(reads, lo, hi):
    """Return [lo,hi) from sparse ``(file_offset, bytes)`` reads, or None if uncovered."""
    for start, data in reads:
        data = bytes(data)
        if start <= lo and hi <= start + len(data):
            return data[lo - start:hi - start]
    return None


def missing_boot_patches_sparse(image, reads):
    """Sparse equivalent of :func:`missing_boot_patches` for the live pre-erase gate.

    ``reads`` contains ``(file_offset, bytes)`` entries produced from
    :func:`boot_patch_read_ranges`. Missing coverage fails safe and reports the patch missing.
    """
    patches = patch_ms41.load_patches()
    missing = []
    for patch_id in boot_write_patches_in(image):
        present = True
        for edit in patches[patch_id]["edits"]:
            off = int(edit["off"])
            expected = bytes.fromhex(edit["data"])
            lo = max(off, SA1_LO)
            hi = min(off + len(expected), SA1_HI)
            if lo >= hi:
                continue
            actual = _sparse_bytes(reads, lo, hi)
            if actual != expected[lo - off:hi - off]:
                present = False
                break
        if not present:
            missing.append(patch_id)
    return missing


def missing_boot_patches(image, ecu_evidence):
    """Boot/SA1 patch ids carried by `image` whose SA1 bytes the connected ECU does NOT already
    have — i.e. the bytes a DS2 / un-armed soft-BSL flash would silently drop. `ecu_evidence` is
    the ECU's live SA1 window (file 0x4000..0x5FFF), a full ROM read (auto-sliced), or None.
    None means no usable evidence → every boot patch is reported missing (conservative fail-safe:
    better a false block than a silent partial patch). Only the patch's own SA1 edit bytes are
    compared, so variant conversions and per-unit identity drift never register as missing."""
    win = sa1_window(ecu_evidence)
    patches = patch_ms41.load_patches()
    return [pid for pid in boot_write_patches_in(image)
            if win is None or not _sa1_edits_present(patches[pid], win)]
