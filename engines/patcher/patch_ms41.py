#!/usr/bin/env python3
"""patch_ms41.py - compose MS41 firmware patches onto a stock base, collision-safe.

A patch library lives in patchlib/patches/*.json. Each patch declares byte `edits`
(offset + `expect` stock bytes + `data`), a `target` base version, `recompute` flags,
and optional `cave` usage. This tool stacks any subset onto a stock bin and GUARANTEES
no two patches overlap; checksums are recomputed from the final image.

  python patchlib/patch_ms41.py list
  python patchlib/patch_ms41.py show alphan_failsafe
  python patchlib/patch_ms41.py apply --base stock.bin --out built.bin \
        --patch amd_flash --patch softbsl_loader --patch alphan_failsafe [--dry-run]

Add a patch = drop a new <id>.json into patchlib/patches/. A descriptor may use
``targets`` when the exact same edits support multiple firmware versions, plus
``recompute_by_target`` for checksum gates that differ between those versions.
"""
import argparse
import glob
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
# The repository root is two levels above this vendored patch engine.
FLASHER = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, FLASHER)
import checksum  # noqa: E402  (boot-CRC + cal recompute)

PATCH_DIR = os.path.join(HERE, "patches")
FULL = 262144

# base fingerprints: coarse "is this the right version" gate (edits' `expect` do the fine check)
FINGERPRINTS = {
    # MS41.3 has one program-side signature and two calibration-side markers. check_base handles
    # its (program AND (SS1 OR ABHISHEK)) rule explicitly below; requiring both calibration strings
    # would reject a valid custom tune that overwrote one of them.
    "MS41.3": [(0x39A9A, bytes.fromhex("9a116390"))],
    # MS41.0 = factory part 1429861. The part-number digit-runs live at 0x6025 (6 aligned copies);
    # MS41.3/.2 carry "1406464" at that same offset, so this cleanly discriminates the version.
    "MS41.0": [(0x6025, b"1429861")],
    # Factory MS41.1 reference (ECU-ID 1437806 / CAL-ID 60). Both markers are required so
    # alternate or hybrid .1 images remain fail-closed until their hook bytes are verified.
    "MS41.1": [(0x6025, b"1437806"), (0x1400E, b"60")],
    # Factory S52 firmware.  check_base additionally rejects the SS1v2 program/cal markers so
    # the common 1406464 part number cannot make an MS41.3 or hybrid image look like MS41.2.
    "MS41.2": [(0x6025, b"1406464"), (0x1400E, b"12")],
}


def patch_targets(patch):
    """Firmware versions accepted by a descriptor (legacy ``target`` remains supported)."""
    return tuple(patch.get("targets") or (patch["target"],))


def supports_target(patch, target):
    return target in patch_targets(patch)


def recompute_flags(patch, target):
    """Checksum work requested globally and specifically for the selected firmware target."""
    flags = set(patch.get("recompute", []))
    flags.update(patch.get("recompute_by_target", {}).get(target, []))
    if target == "MS41.3":
        flags.add("program")
    return flags


def load_patches():
    out = {}
    for p in sorted(glob.glob(os.path.join(PATCH_DIR, "*.json"))):
        d = json.load(open(p, encoding="utf-8"))
        out[d["id"]] = d
    return out


def _ranges(patch):
    """byte intervals (start, end_exclusive) a patch writes."""
    return [(e["off"], e["off"] + len(bytes.fromhex(e["data"]))) for e in patch["edits"]]


def _overlap(a, b):
    return not (a[1] <= b[0] or b[1] <= a[0])


def _xfer(data, i=0):
    """decode an inter-segment transfer at data[i]: FA jmps / DA calls -> (op, CPU target)."""
    if i + 4 <= len(data) and data[i] in (0xFA, 0xDA):
        return data[i], (data[i + 1] << 16) | (data[i + 3] << 8) | data[i + 2]
    return None, None


def validate_splices(patch):
    """Precisely validate every declared splice against its cave entry point.

    Legacy descriptors declare one ``splice_off``/``base`` pair. Multi-hook patches such
    as Launch Control V3/V4 use ``cave.splices`` so every hook is checked rather than silently
    validating only the first one.
    """
    errs = []
    cave = patch.get("cave")
    if not cave:
        return errs
    specs = cave.get("splices") or [
        {"off": cave["splice_off"], "base": cave["base"]}
    ]
    for spec in specs:
        splice_off = int(spec["off"])
        cave_base = int(spec["base"])
        spl = next((e for e in patch["edits"] if e["off"] == splice_off), None)
        if not spl:
            errs.append(f"{patch['id']}: cave splice 0x{splice_off:05X} has no matching edit")
            continue
        sdata = bytes.fromhex(spl["data"])
        op, cpu = _xfer(sdata)
        if op is None:
            # INTRA-segment splice: jmpa (EA) / calla (CA) keeps the splice segment.
            if len(sdata) >= 4 and sdata[0] in (0xEA, 0xCA):
                seg = (splice_off ^ 0x4000) >> 16
                cpu = (seg << 16) | (sdata[3] << 8) | sdata[2]
            else:
                errs.append(
                    f"{patch['id']}: splice @0x{splice_off:05X} is not a jmps/calls/jmpa/calla")
                continue
        f = cpu ^ 0x4000
        if f != cave_base:
            errs.append(f"{patch['id']}: splice @0x{splice_off:05X} -> file 0x{f:05X}, "
                        f"but cave entry is 0x{cave_base:05X}  (A14-XOR / addressing bug!)")
    return errs


def scan_cave_intraseg(patch):
    """WARN: intra-segment absolute transfers (jmpa=EA / calla=CA) inside a cave payload.
    A cave lives in some code segment; an intra-seg jump keeps that segment, so if it targets
    code in ANOTHER segment (e.g. the dispatcher epilogue), it lands in the wrong place. Caves
    that return cross-segment must use jmps/calls (inter-seg) or jmpr (relative). This is the
    door_magic bug (a verbatim-copied jmpa in a seg-3 cave targeting a seg-2 epilogue)."""
    warns = []
    cave = patch.get("cave")
    if not cave:
        return warns
    caveseg = (cave["base"] ^ 0x4000) >> 16
    for e in patch["edits"]:
        if e["off"] != cave["base"]:
            continue
        data = bytes.fromhex(e["data"])
        for i in range(max(0, len(data) - 3)):
            if data[i] in (0xEA, 0xCA):
                off16 = (data[i + 3] << 8) | data[i + 2]
                opn = "jmpa" if data[i] == 0xEA else "calla"
                warns.append(f"{patch['id']} @0x{e['off']+i:05X}: {opn} 0x{off16:04X} INTRA-seg "
                             f"(stays in seg {caveseg}; if the real target is in another segment, "
                             f"use jmps/calls). Verify - or a data byte.")
    return warns


def is_applied(data, patch):
    """True if every edit's post-patch bytes are already present in `data` (patch installed)."""
    for e in patch["edits"]:
        off = e["off"]; dat = bytes.fromhex(e["data"])
        if bytes(data[off:off + len(dat)]) != dat:
            return False
    return True


# File range of the SA1 / boot region (== DS2 addr 0x0000-0x1FFF via the XOR-0x4000 block
# swap; == PARAM1_FILE in the soft-BSL host). DS2 and the default soft-BSL flash leave it
# intact — it is written only by the hardware BSL or by soft-BSL with bootloader writes armed.
BOOT_FILE_LO = 0x4000
BOOT_FILE_HI = 0x6000


def needs_boot_write(patch):
    """True if any of the patch's edits land in the boot/SA1 region (file 0x4000-0x5FFF).
    Such a patch cannot be flashed by DS2 or by an un-armed soft-BSL write."""
    for e in patch["edits"]:
        lo = e["off"]; hi = lo + len(bytes.fromhex(e["data"]))
        if lo < BOOT_FILE_HI and hi > BOOT_FILE_LO:
            return True
    return False


def revert(data, patch):
    """Undo a single already-applied patch: restore each edit's pre-patch `expect` bytes.
    Raises PatchError if the patch isn't currently applied (nothing to safely undo — the
    `expect` bytes are only guaranteed correct as the patch's own immediate predecessor state).
    Recomputes the checksum classes declared by the descriptor. This matters on MS41.2, whose
    program checksum is enabled, and for SA1 hooks that change the always-on boot CRC."""
    if not is_applied(data, patch):
        raise PatchError(f"'{patch['id']}' is not currently applied to this base — nothing to remove")
    out = bytearray(data)
    for e in patch["edits"]:
        off = e["off"]; exp = bytes.fromhex(e["expect"])
        out[off:off + len(exp)] = exp
    matches = [version for version in FINGERPRINTS if check_base(out, version) is None]
    target = matches[0] if len(matches) == 1 else (patch_targets(patch)[0]
                                                   if len(patch_targets(patch)) == 1 else None)
    flags = recompute_flags(patch, target) if target else set()
    if flags:
        out, _details = checksum.correct_checksums(
            out, correct_program=("program" in flags))
    return bytes(out)


def check_base(data, target):
    if len(data) != FULL:
        return f"base is {len(data)} B, expected {FULL}"
    if target not in FINGERPRINTS:
        return f"unsupported patch target {target!r}"
    if target == "MS41.3":
        program_ok = bytes(data[0x39A9A:0x39A9E]) == bytes.fromhex("9a116390")
        cal_ok = (bytes(data[0x173BB:0x173BE]) == b"SS1"
                  or bytes(data[0x11F60:0x11F68]) == b"ABHISHEK")
        if not program_ok:
            return "exact MS41.3 program signature missing at 0x39A9A"
        if not cal_ok:
            return "neither MS41.3 calibration marker is present (SS1 @0x173BB / ABHISHEK @0x11F60)"
        return None
    for off, sig in FINGERPRINTS[target]:
        if data[off:off + len(sig)] != sig:
            return f"base fingerprint FAIL: {sig!r} not at 0x{off:05X} (not a {target} image?)"
    if target == "MS41.2":
        if bytes(data[0x39A9A:0x39A9E]) == bytes.fromhex("9a116390"):
            return "MS41.3 program signature is present; not a factory MS41.2 program"
        if (bytes(data[0x173BB:0x173BE]) == b"SS1"
                or bytes(data[0x11F60:0x11F68]) == b"ABHISHEK"):
            return "MS41.3 calibration marker is present; not a factory MS41.2 calibration"
    return None


def cmd_list(patches, args):
    print(f"MS41 patch library ({len(patches)} patches):\n")
    for pid, p in patches.items():
        tot = sum(len(bytes.fromhex(e["data"])) for e in p["edits"])
        cave = f"  cave@0x{p['cave']['base']:05X}" if p.get("cave") else ""
        rc = ",".join(p.get("recompute", [])) or "none"
        target_text = "/".join(patch_targets(p))
        retired = "  DEPRECATED - removal only" if p.get("deprecated") else ""
        print(f"  {pid:18} [{target_text}]  {len(p['edits'])} edits/{tot}B  "
              f"recompute={rc}{cave}{retired}")
        print(f"       {p['title']} - {p['description'][:96]}")
    # pairwise collisions across the WHOLE library (informational)
    ids = list(patches)
    clashes = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            ra, rb = _ranges(patches[ids[i]]), _ranges(patches[ids[j]])
            if any(_overlap(a, b) for a in ra for b in rb):
                clashes.append((ids[i], ids[j]))
    print("\ncollisions (cannot be stacked together):")
    if clashes:
        for a, b in clashes:
            print(f"  [!] {a}  X  {b}")
    else:
        print("  none")


def cmd_show(patches, args):
    p = patches.get(args.id)
    if not p:
        sys.exit(f"no such patch: {args.id}")
    print(json.dumps({k: v for k, v in p.items() if k != "edits"}, indent=2))
    print("edits:")
    for e in p["edits"]:
        n = len(bytes.fromhex(e["data"]))
        print(f"  0x{e['off']:05X}  {n:4} B   expect {e['expect'][:16]}{'..' if len(e['expect'])>16 else ''}"
              f"  ->  {e['data'][:16]}{'..' if len(e['data'])>16 else ''}")


def cmd_validate(patches, args):
    sel = args.patch or [
        pid for pid, patch in patches.items() if not patch.get("deprecated")
    ]
    print("jump-target validation (precise splice->cave check):")
    errs = []
    for pid in sel:
        p = patches.get(pid)
        if not p:
            sys.exit(f"no such patch: {pid}")
        e = validate_splices(p)
        errs += e
        cave = p.get("cave")
        if not cave:
            print(f"  -- {pid:18} (no cave / no splice to check)")
        elif e:
            for x in e:
                print(f"  X  {x}")
        else:
            print(f"  OK {pid:18} splice @0x{cave['splice_off']:05X} -> cave 0x{cave['base']:05X}")
    iwarns = []
    for pid in sel:
        iwarns += scan_cave_intraseg(patches[pid])
    if iwarns:
        print("\nintra-segment transfer warnings (verify - jmpa/calla in a cave):")
        for w in iwarns:
            print(f"  ! {w}")
    print("\n" + ("FAIL - addressing bug(s) above" if errs else "all splices resolve correctly"))
    sys.exit(1 if errs else 0)


class PatchError(Exception):
    """A patch selection could not be composed (bad base, collision, expect mismatch, etc.)."""


def build(base_data, patch_ids, patches=None, marker=None, *, allow_deprecated=False):
    """Compose the selected patches onto base_data (bytes); return (out_bytes, log_lines).

    Pure: no file I/O, no printing, no sys.exit. Raises PatchError on any rejection
    (unknown patch, unsupported target, bad base, unmet requires, conflict, byte collision,
    unresolved cave splice, or an expect-byte mismatch). Deprecated definitions are
    detection/removal fixtures and are rejected unless an internal test explicitly passes
    ``allow_deprecated=True``; that fixture-only escape also permits a deprecated
    descriptor whose historical defect is an invalid splice. marker (None/'B'/'T')
    sets the bank-ID byte @0x5FFC. Checksums are recomputed from the final image.
    """
    if patches is None:
        patches = load_patches()
    log = []

    for pid in patch_ids:
        if pid not in patches:
            raise PatchError(f"no such patch: {pid}")
    chosen = [patches[pid] for pid in patch_ids]
    deprecated = [p["id"] for p in chosen if p.get("deprecated")]
    if deprecated and not allow_deprecated:
        raise PatchError(
            "deprecated patch(es) are detection/removal-only and cannot be installed: "
            + ", ".join(deprecated))

    data = bytearray(base_data)
    if not chosen and marker is None:
        raise PatchError("no patches selected")
    # Identify the actual base first. Shared descriptors can then support .2 and .3 without
    # duplicating IDs, while a version-specific patch still fails closed on the wrong firmware.
    matches = [version for version in FINGERPRINTS
               if check_base(data, version) is None]
    if len(matches) != 1:
        raise PatchError("BASE REJECTED: could not identify exactly one supported firmware version")
    target = matches[0]
    unsupported = [p["id"] for p in chosen if not supports_target(p, target)]
    if unsupported:
        raise PatchError(
            f"BASE REJECTED: {target} is not supported by patch(es): {', '.join(unsupported)}")
    if (target == "MS41.3"
            and any(p["id"] == "cal_guard" for p in chosen)
            and bytes(data[0x173BB:0x173C0]) != b"SS1v2"):
        raise PatchError(
            "BASE REJECTED: CalGuard requires the strict SS1v2 calibration "
            "marker used by its runtime decision")

    err = check_base(data, target)
    if err:
        raise PatchError(f"BASE REJECTED: {err}")
    log.append(f"base OK: {target}, {len(data)} B")

    _all = patches
    for p in chosen:
        for req in p.get("requires", []):
            # satisfied if the dependency is in this selection OR already applied on the base
            reqp = _all.get(req)
            if req not in patch_ids and not (reqp and is_applied(data, reqp)):
                raise PatchError(f"'{p['id']}' requires '{req}' (add it to the selection, or install it first)")
        for con in p.get("conflicts", []):
            if con in patch_ids:
                raise PatchError(f"'{p['id']}' conflicts with '{con}'")

    # COLLISION GUARD (the core safety): no two selected patches may write the same byte
    for i in range(len(chosen)):
        for j in range(i + 1, len(chosen)):
            for a in _ranges(chosen[i]):
                for b in _ranges(chosen[j]):
                    if _overlap(a, b):
                        lo, hi = max(a[0], b[0]), min(a[1], b[1])
                        raise PatchError(
                            f"COLLISION {chosen[i]['id']} X {chosen[j]['id']}: "
                            f"overlap 0x{lo:05X}-0x{hi - 1:05X}")
    log.append("collision check: OK - no overlapping bytes")

    # JUMP-TARGET CHECK (precise A14-XOR splice->cave resolution)
    xerr = []
    for p in chosen:
        errors = validate_splices(p)
        if errors and allow_deprecated and p.get("deprecated"):
            log.extend("fixture warning: " + error for error in errors)
        else:
            xerr += errors
    if xerr:
        raise PatchError("JUMP-TARGET ERROR: " + "; ".join(xerr))
    log.append("jump-target check: OK - all cave splices resolve")
    for p in chosen:
        for w in scan_cave_intraseg(p):
            log.append("warn: " + w)

    # Verify every expect, then apply. An unchanged edit may already match its
    # current payload while another edit is upgraded. A descriptor may also
    # carry a precise ``upgrade_expect`` for a superseded revision. This is not a
    # broad bypass: only an exact listed prior payload may be replaced in place.
    recompute = {"program"} if target == "MS41.3" else set()
    for p in chosen:
        for e in p["edits"]:
            off = e["off"]
            exp = bytes.fromhex(e["expect"])
            cur = bytes(data[off:off + len(exp)])
            dat = bytes.fromhex(e["data"])
            if cur not in (exp, dat):
                upgrades = e.get("upgrade_expect", [])
                if isinstance(upgrades, str):
                    upgrades = [upgrades]
                upgrades = [bytes.fromhex(value) for value in upgrades]
                if cur not in upgrades:
                    raise PatchError(
                        f"{p['id']} @0x{off:05X}: expect {exp.hex()} but base has {cur.hex()} "
                        f"(wrong base, or already patched)")
                log.append(
                    f"{p['id']} @0x{off:05X}: exact prior revision detected; upgrading")
        for e in p["edits"]:
            off = e["off"]; dat = bytes.fromhex(e["data"])
            data[off:off + len(dat)] = dat
        recompute |= recompute_flags(p, target)
        log.append(f"applied {p['id']}: {len(p['edits'])} edits")

    if marker:
        half = 0x42 if marker == "B" else 0x54       # 'B' / 'T'
        data[0x5FFC:0x6000] = bytes([0xA5, 0x5A, half, half ^ 0xFF])
        recompute.add("boot_crc")
        log.append(f"set bank marker @0x5FFC -> {marker}")

    if recompute:
        corrected, details = checksum.correct_checksums(
            data, correct_program=("program" in recompute))
        data[:] = corrected                       # correct_checksums returns a COPY
        log.append(f"recomputed checksums: {','.join(sorted(recompute))}")
        log.extend("  " + d.replace("→", "->") for d in details)

    return bytes(data), log


def cmd_apply(patches, args):
    base_data = open(args.base, "rb").read()
    try:
        out, log = build(base_data, args.patch, patches, marker=getattr(args, "marker", None))
    except PatchError as e:
        sys.exit(str(e))
    for line in log:
        print(line)
    if args.dry_run:
        print("\n[dry-run] no file written. Selection verified + collision-free + checksums OK.")
        return
    open(args.out, "wb").write(out)
    print(f"\nwrote {args.out} ({len(out)} B)")
    print(f"  patches: {', '.join(args.patch)}")


def main():
    ap = argparse.ArgumentParser(description="compose MS41 firmware patches, collision-safe")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sp = sub.add_parser("show"); sp.add_argument("id")
    vp = sub.add_parser("validate"); vp.add_argument("--patch", action="append", default=[])
    ap_ = sub.add_parser("apply")
    ap_.add_argument("--base", required=True)
    ap_.add_argument("--out", default="patched.bin")
    ap_.add_argument("--patch", action="append", default=[], required=True)
    ap_.add_argument("--marker", choices=["B", "T"],
                     help="set the bank-ID marker @0x5FFC: B=bottom (default in the loader patch), "
                          "T=top/golden (for softbsl_host `flash --cross-bank`). Forces a boot-CRC recompute.")
    ap_.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    patches = load_patches()
    {"list": cmd_list, "show": cmd_show, "validate": cmd_validate,
     "apply": cmd_apply}[args.cmd](patches, args)


if __name__ == "__main__":
    main()
