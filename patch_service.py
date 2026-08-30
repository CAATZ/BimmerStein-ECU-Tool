"""Typed service layer between application workflows and the internal patch module.

Filters the patch library to a base image's detected version, computes byte
collisions for a selection (so the UI can gray out incompatible patches), and
delegates the actual compose to engines.patcher.patch_ms41.build. Pure logic —
no Qt, no file I/O.
"""
import hashlib
import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import checksum
from ms41 import MS41ECU
from engines.patcher import patch_ms41

PatchError = patch_ms41.PatchError

# This bootstrap is an internal Soft-BSL installation detail, not a general-purpose
# firmware patch. Keep its definition available to the installer while omitting it
# from the Patches-tab catalogue, where baking it into a BIN can cause collisions.
PATCH_TAB_HIDDEN_IDS = frozenset({
    "door_0x43", "door_0x43_ms410", "door_0x43_ms411",
})


_SWITCH_INPUT_CHOICES = (
    ("off", "Off", 0xFF),
    ("always", "Always on (RPM limiter)", 0x00),
    ("pin80", "Switch - Pin 80", 0x01),
    ("pin81", "Switch - Pin 81", 0x02),
    ("pin82", "Switch - Pin 82", 0x04),
)

# Only patch-owned, user-facing controls belong here. Descriptor ``cave.cals``
# also contains internal markers; keeping this allow-list separate prevents a
# UI or caller from turning every symbolic address into a write primitive.
_EDITABLE_PARAMETER_FAMILIES = {
    "ignition_cut_v7": (
        {
            "id": "CUTSW", "label": "Switch input", "kind": "choice",
            "codec": "choice", "choices": _SWITCH_INPUT_CHOICES,
            "description": "Arms the standalone ignition-cut limiter.",
        },
        {
            "id": "CUTRPM", "label": "RPM limit", "kind": "number",
            "codec": "rpm_u8", "units": "RPM", "minimum": "0",
            "maximum": "8160", "step": "32", "decimals": 0,
            "description": "Spark-cut threshold, stored in 32 RPM steps.",
        },
    ),
    "launch_control_v4": (
        {
            "id": "LC_SW", "label": "Switch / mode", "kind": "choice",
            "codec": "choice", "choices": _SWITCH_INPUT_CHOICES,
            "description": "Arms Launch Control from the selected input.",
        },
        {
            "id": "LC_CUTTYPE", "label": "Cut type", "kind": "choice",
            "codec": "choice", "choices": (
                ("fuel", "Fuel cut (stock injector limiter)", 0x00),
                ("ignition", "Ignition cut (shared V7 engine)", 0x01),
            ),
            "description": "Selects the launch limiter strategy.",
        },
        {
            "id": "LC_CLUTCHPOL", "label": "Clutch polarity", "kind": "choice",
            "codec": "choice", "choices": (
                ("active_high", "Active-high (depressed = 5V)", 0x00),
                ("active_low", "Active-low (depressed = 0V)", 0x01),
            ),
            "description": "Electrical sense of the clutch or toggle input.",
        },
        {
            "id": "LC_MAXRPM", "label": "Soft cut RPM", "kind": "number",
            "codec": "rpm_u8", "units": "RPM", "minimum": "0",
            "maximum": "8160", "step": "32", "decimals": 0,
            "description": "Launch limiter threshold, stored in 32 RPM steps.",
        },
        {
            "id": "LC_ARMSPEED", "label": "Arm speed", "kind": "number",
            "codec": "u8", "units": "km/h", "minimum": "0",
            "maximum": "255", "step": "1", "decimals": 0,
            "description": "A cleared launch latch can arm below this speed.",
        },
        {
            "id": "LC_MAXSPEED", "label": "Maximum speed", "kind": "number",
            "codec": "u8", "units": "km/h", "minimum": "0",
            "maximum": "255", "step": "1", "decimals": 0,
            "description": "Releases the launch latch at this speed.",
        },
        {
            "id": "LC_MINTPS", "label": "Minimum throttle", "kind": "number",
            "codec": "tps_u8", "units": "%", "minimum": "0",
            "maximum": "119.85", "step": "0.47", "decimals": 2,
            "description": "Clears the launch latch below this throttle value.",
        },
        {
            "id": "LC_HARDRPM", "label": "Hard cut RPM", "kind": "number",
            "codec": "rpm_reserved_u8", "units": "RPM", "minimum": "0",
            "maximum": "8128", "step": "32", "decimals": 0,
            "specials": (("@auto", "Automatic: soft cut + 96 RPM", 0xFF),),
            "description": "Upper fuel-cut threshold; ignition mode ignores it.",
        },
    ),
    "vanos_minrpm": (
        {
            "id": "VANOSRPM", "label": "Minimum RPM (closed throttle)",
            "kind": "number", "codec": "rpm_reserved_u8", "units": "RPM",
            "minimum": "0", "maximum": "8128", "step": "32", "decimals": 0,
            "specials": (("@stock", "Stock behavior", 0xFF),),
            "description": "Minimum RPM for closed-throttle VANOS engagement.",
        },
    ),
}
_EDITABLE_PARAMETER_FAMILIES["launch_control_v5"] = (
    _EDITABLE_PARAMETER_FAMILIES["launch_control_v4"]
)


def definitions():
    """Return the internal patch definitions used by every application workflow."""
    return patch_ms41.load_patches()


def is_applied(image, patch):
    return patch_ms41.is_applied(bytes(image), patch)


def base_version(data):
    """The exactly fingerprinted patch base version, or None."""
    data = bytes(data)
    if MS41ECU.check_hybrid(data):
        return None
    matches = [
        version for version in patch_ms41.FINGERPRINTS
        if patch_ms41.check_base(data, version) is None
    ]
    return matches[0] if len(matches) == 1 else None


def _parameter_family(patch):
    return str(patch.get("family_id") or patch.get("id") or "")


def _descriptor_token(patch):
    payload = json.dumps(
        {
            "patch": patch,
            "parameters": _EDITABLE_PARAMETER_FAMILIES.get(
                _parameter_family(patch), ()),
        },
        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _parameter_width(spec):
    return 2 if spec["codec"] == "ipw_u16le" else 1


def _is_calibration_offset(offset):
    storage_address = (int(offset) ^ 0x4000) - MS41ECU.TUNE_DS2_BASE
    return 0 <= storage_address < MS41ECU.TUNE_SIZE


def _parameter_location(patch, spec):
    cals = patch.get("cave", {}).get("cals", {})
    parameter_id = spec["id"]
    if parameter_id not in cals:
        raise PatchError(
            f"patch '{patch.get('id')}' does not declare calibration {parameter_id}"
        )
    offset = int(cals[parameter_id])
    width = _parameter_width(spec)
    if not all(_is_calibration_offset(index) for index in range(offset, offset + width)):
        raise PatchError(
            f"patch parameter {parameter_id} is outside the calibration partition"
        )
    parameter_range = (offset, offset + width)
    if any(patch_ms41._overlap(parameter_range, edit_range)
           for edit_range in patch_ms41._ranges(patch)):
        raise PatchError(
            f"patch parameter {parameter_id} overlaps executable patch bytes"
        )
    return offset, width


def _raw_parameter_value(image, patch, spec):
    offset, width = _parameter_location(patch, spec)
    raw = int.from_bytes(image[offset:offset + width], "little")
    return raw, offset, width


def _special_for_raw(spec, raw):
    return next(
        ((token, label) for token, label, value in spec.get("specials", ())
         if value == raw),
        None,
    )


def _format_decimal(value, decimals):
    if decimals == 0:
        return str(int(value))
    return f"{value:.{decimals}f}"


def _decode_parameter(spec, raw):
    if spec["kind"] == "choice":
        for token, label, value in spec["choices"]:
            if value == raw:
                return token, label
        raw_hex = f"{raw:0{_parameter_width(spec) * 2}X}"
        return f"@raw:{raw_hex}", f"Unknown (0x{raw_hex})"

    special = _special_for_raw(spec, raw)
    if special:
        return special

    codec = spec["codec"]
    if codec.startswith("rpm_"):
        value = Decimal(raw * 32)
    elif codec == "u8":
        value = Decimal(raw)
    elif codec == "tps_u8":
        value = Decimal(raw) * Decimal("0.47")
    elif codec == "ipw_u16le":
        value = Decimal(raw) * Decimal("0.00534")
    else:
        raise PatchError(f"unsupported patch parameter codec: {codec}")
    text = _format_decimal(value, int(spec.get("decimals", 0)))
    display = f"{text} {spec.get('units', '')}".strip()
    return text, display


def _decimal_value(value, parameter_id):
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise PatchError(f"{parameter_id} needs a numeric value") from None
    if not parsed.is_finite():
        raise PatchError(f"{parameter_id} needs a finite numeric value")
    return parsed


def _rounded_raw(value, scale):
    return int((value / scale).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _encode_parameter(spec, value):
    token = str(value).strip()
    if spec["kind"] == "choice":
        for choice_token, _label, raw in spec["choices"]:
            if token == choice_token:
                return raw
        raise PatchError(f"invalid choice for {spec['id']}: {token}")

    for special_token, _label, raw in spec.get("specials", ()):
        if token == special_token:
            return raw
    if token.startswith("@"):
        raise PatchError(f"invalid special value for {spec['id']}: {token}")

    number = _decimal_value(token, spec["id"])
    minimum = Decimal(spec["minimum"])
    maximum = Decimal(spec["maximum"])
    if not minimum <= number <= maximum:
        raise PatchError(
            f"{spec['id']} must be between {minimum} and {maximum} {spec.get('units', '')}".strip()
        )

    codec = spec["codec"]
    if codec.startswith("rpm_"):
        raw = _rounded_raw(number, Decimal(32))
        if number != Decimal(raw * 32):
            raise PatchError(f"{spec['id']} must use 32 RPM steps")
    elif codec == "u8":
        raw = _rounded_raw(number, Decimal(1))
        if number != Decimal(raw):
            raise PatchError(f"{spec['id']} must be a whole number")
    elif codec == "tps_u8":
        raw = _rounded_raw(number, Decimal("0.47"))
    elif codec == "ipw_u16le":
        raw = _rounded_raw(number, Decimal("0.00534"))
    else:
        raise PatchError(f"unsupported patch parameter codec: {codec}")

    max_raw = (1 << (_parameter_width(spec) * 8)) - 1
    reserved = {raw_value for _token, _label, raw_value in spec.get("specials", ())}
    if not 0 <= raw <= max_raw or raw in reserved:
        raise PatchError(f"{spec['id']} is not representable without a sentinel value")
    return raw


def _public_parameter(image, patch, spec):
    raw, _offset, width = _raw_parameter_value(image, patch, spec)
    current, current_display = _decode_parameter(spec, raw)
    return {
        "id": spec["id"],
        "label": spec["label"],
        "description": spec.get("description", ""),
        "kind": spec["kind"],
        "units": spec.get("units", ""),
        "minimum": spec.get("minimum"),
        "maximum": spec.get("maximum"),
        "step": spec.get("step"),
        "decimals": int(spec.get("decimals", 0)),
        "current": current,
        "current_display": current_display,
        "raw_hex": raw.to_bytes(width, "little").hex().upper(),
        "choices": [
            {"value": token, "label": label}
            for token, label, _raw in spec.get("choices", ())
        ],
        "specials": [
            {"value": token, "label": label}
            for token, label, _raw in spec.get("specials", ())
        ],
    }


def editable_parameters(data):
    """Return controls for exact, current patches installed in a 256 KiB ROM.

    Addresses and codecs remain private to this service. Consumers receive only
    stable patch/parameter ids and semantic values.
    """
    data = bytes(data)
    if len(data) != MS41ECU.FULL_ROM_SIZE:
        raise PatchError("patch parameters require an exact 256 KiB full ROM")
    if base_version(data) is None:
        raise PatchError("patch parameters require one exact, non-hybrid ROM base")

    all_patches = patch_ms41.load_patches()
    available = {entry["id"]: entry for entry in available_patches(data)}
    checksum_ok, _checksum_details = checksum.verify_checksum(bytearray(data))
    result = []
    for patch_id, entry in available.items():
        patch = all_patches[patch_id]
        specs = _EDITABLE_PARAMETER_FAMILIES.get(_parameter_family(patch))
        if not specs or not entry.get("installed") or entry.get("deprecated"):
            continue
        parameters = [_public_parameter(data, patch, spec) for spec in specs]
        result.append({
            "patch_id": patch_id,
            "title": patch.get("title", patch_id),
            "version": patch.get("version", ""),
            "descriptor_token": _descriptor_token(patch),
            "editable": bool(entry.get("ok") and checksum_ok),
            "blocked_reason": (
                "" if entry.get("ok") and checksum_ok
                else entry.get("badge") if not entry.get("ok")
                else "Source checksum verification failed."
            ),
            "parameters": parameters,
        })
    return result


def _validate_parameter_relationships(image, patch, specs):
    if _parameter_family(patch) not in {"launch_control_v4", "launch_control_v5"}:
        return
    by_id = {spec["id"]: spec for spec in specs}

    def raw(parameter_id):
        return _raw_parameter_value(image, patch, by_id[parameter_id])[0]

    if raw("LC_SW") == 0xFF:
        return
    if raw("LC_MAXSPEED") <= raw("LC_ARMSPEED"):
        raise PatchError("LC_MAXSPEED must be greater than LC_ARMSPEED")
    if raw("LC_CUTTYPE") == 0x00 and raw("LC_HARDRPM") != 0xFF:
        if raw("LC_HARDRPM") < raw("LC_MAXRPM"):
            raise PatchError("LC_HARDRPM must be at or above LC_MAXRPM")


def apply_parameter_changes(
        base_data, patch_id, changes, *, expected_sha256=None,
        expected_descriptor_token=None):
    """Apply declared semantic parameter changes to one exact installed patch.

    The result may differ only at the requested parameter bytes and calibration
    checksum stores. Returns ``(image, report)`` and never mutates ``base_data``.
    """
    source = bytes(base_data)
    if len(source) != MS41ECU.FULL_ROM_SIZE:
        raise PatchError("patch parameters require an exact 256 KiB full ROM")
    source_sha = hashlib.sha256(source).hexdigest()
    if expected_sha256 and expected_sha256.lower() != source_sha:
        raise PatchError("the source ROM changed after its parameters were opened")
    if not isinstance(changes, dict) or not changes:
        raise PatchError("choose at least one patch parameter change")
    if not all(isinstance(key, str) and isinstance(value, str)
               for key, value in changes.items()):
        raise PatchError("patch parameter changes must use string ids and values")

    all_patches = patch_ms41.load_patches()
    patch = all_patches.get(str(patch_id))
    if patch is None:
        raise PatchError(f"no such patch: {patch_id}")
    descriptor_token = _descriptor_token(patch)
    if expected_descriptor_token and expected_descriptor_token != descriptor_token:
        raise PatchError("the patch definition changed after its parameters were opened")

    groups = {group["patch_id"]: group for group in editable_parameters(source)}
    group = groups.get(str(patch_id))
    if group is None:
        raise PatchError(f"patch '{patch_id}' is not an editable current installation")
    if not group["editable"]:
        raise PatchError(group["blocked_reason"] or "patch parameters are not editable")

    specs = _EDITABLE_PARAMETER_FAMILIES[_parameter_family(patch)]
    by_id = {spec["id"]: spec for spec in specs}
    unknown = sorted(set(changes) - set(by_id))
    if unknown:
        raise PatchError("unknown patch parameter(s): " + ", ".join(unknown))

    checksum_ok, checksum_details = checksum.verify_checksum(bytearray(source))
    if not checksum_ok:
        raise PatchError("source checksum verification failed: " + "; ".join(checksum_details))

    working = bytearray(source)
    authorized_parameter_bytes = set()
    expected_parameter_bytes = {}
    report_changes = []
    for parameter_id, requested in changes.items():
        spec = by_id[parameter_id]
        old_raw, offset, width = _raw_parameter_value(source, patch, spec)
        new_raw = _encode_parameter(spec, requested)
        if new_raw == old_raw:
            continue
        working[offset:offset + width] = new_raw.to_bytes(width, "little")
        authorized_parameter_bytes.update(range(offset, offset + width))
        expected_parameter_bytes[parameter_id] = (offset, width, new_raw)
        _old_value, old_display = _decode_parameter(spec, old_raw)
        _new_value, new_display = _decode_parameter(spec, new_raw)
        report_changes.append({
            "parameter_id": parameter_id,
            "label": spec["label"],
            "before": old_display,
            "after": new_display,
            "before_raw": old_raw.to_bytes(width, "little").hex().upper(),
            "after_raw": new_raw.to_bytes(width, "little").hex().upper(),
        })
    if not report_changes:
        raise PatchError("the selected patch parameters are already set")

    _validate_parameter_relationships(working, patch, specs)
    corrected, correction_details = checksum.correct_checksums(
        working, correct_program=False)
    corrected = bytes(corrected)
    for parameter_id, (offset, width, expected_raw) in expected_parameter_bytes.items():
        actual_raw = int.from_bytes(corrected[offset:offset + width], "little")
        if actual_raw != expected_raw:
            raise PatchError(
                f"patch parameter {parameter_id} overlaps checksum storage"
            )
    checksum_bytes = {
        index for index, (before, after) in enumerate(zip(working, corrected))
        if before != after
    }
    if any(not _is_calibration_offset(index) for index in checksum_bytes):
        raise PatchError("parameter editing attempted a non-calibration checksum change")
    final_diff = {
        index for index, (before, after) in enumerate(zip(source, corrected))
        if before != after
    }
    allowed_diff = authorized_parameter_bytes | checksum_bytes
    if not final_diff <= allowed_diff:
        raise PatchError("parameter editing changed bytes outside its declared scope")

    final_ok, final_details = checksum.verify_checksum(bytearray(corrected))
    if not final_ok:
        raise PatchError("result checksum verification failed: " + "; ".join(final_details))
    if base_version(corrected) != base_version(source):
        raise PatchError("parameter editing changed the ROM family fingerprint")
    if not patch_ms41.is_applied(corrected, patch):
        raise PatchError("parameter editing damaged the installed patch signature")

    return corrected, {
        "patch_id": patch_id,
        "title": patch.get("title", patch_id),
        "source_sha256": source_sha,
        "result_sha256": hashlib.sha256(corrected).hexdigest(),
        "descriptor_token": descriptor_token,
        "changes": report_changes,
        "checksum_details": correction_details,
        "changed_byte_count": len(final_diff),
    }


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


def _installed_revision_satisfies(all_patches, requirement_id, installed_id):
    """Whether an installed revision belongs to a required current patch's lineage."""
    pending = [requirement_id]
    seen = set()
    while pending:
        candidate = pending.pop()
        if candidate == installed_id:
            return True
        if candidate in seen:
            continue
        seen.add(candidate)
        supersedes = all_patches.get(candidate, {}).get("supersedes")
        pending.extend(
            [supersedes] if isinstance(supersedes, str) else supersedes or []
        )
    return False


def _dependent_ids(all_patches, effective_ids, patch_id):
    return sorted(
        candidate_id for candidate_id in effective_ids
        if candidate_id != patch_id
        and any(
            _installed_revision_satisfies(
                all_patches, required_id, patch_id)
            for required_id in all_patches[candidate_id].get("requires", [])
        )
    )


def installed_dependents(data, patch_id):
    """Effective installed patches that directly require ``patch_id``."""
    all_patches = patch_ms41.load_patches()
    _ver, installed_ids, shadowed_ids = _installed_patch_state(data, all_patches)
    effective_ids = installed_ids - shadowed_ids
    return _dependent_ids(all_patches, effective_ids, patch_id)


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
        required_by = _dependent_ids(all_patches, effective_ids, pid)
        if p.get("deprecated"):
            # Deprecated patches are hidden UNLESS one is actually installed on this base - then surface a
            # remove-only row so a stray/old install can be reverted directly from the tab (no need to first
            # select its successor). Not selectable/installable; the UI shows only its "Remove" control.
            if not installed or pid in shadowed_ids:
                continue
            out.append({
                "id": pid, "title": p.get("title", ""), "description": p.get("description", ""),
                "user_description": p.get("user_description") or p.get("description", ""),
                "target": ver, "version": p.get("version", ""),
                "status": p.get("status", "DEPRECATED"),
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
        missing_requirements = [
            required_id for required_id in p.get("requires", [])
            if installed and required_id not in effective_ids
        ]
        if missing_requirements:
            errs.append(
                "MISSING REQUIRED PATCH: " + ", ".join(missing_requirements))
        entry = {
            "id": pid,
            "title": p.get("title", ""),
            "description": p.get("description", ""),
            "user_description": p.get("user_description") or p.get("description", ""),
            "target": ver,
            "version": p.get("version", ""),
            "status": p.get("status", ""),
            "tested": p.get("tested", False),
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
        entry["legacy"] = []
        for lid in sup_ids:
            if lid not in installed_ids or lid in shadowed_ids:
                continue
            legacy = {"id": lid, "label": all_patches[lid].get("label", lid)}
            legacy_required_by = _dependent_ids(
                all_patches, effective_ids, lid)
            if legacy_required_by:
                legacy["required_by"] = legacy_required_by
            entry["legacy"].append(legacy)
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
    base_data = bytes(base_data)
    selected_ids = list(selected_ids)
    all_patches = patch_ms41.load_patches()
    _ver, installed_ids, shadowed_ids = _installed_patch_state(
        base_data, all_patches)
    effective_after_build = (installed_ids - shadowed_ids) | set(selected_ids)
    missing = {
        patch_id: [
            required_id
            for required_id in all_patches[patch_id].get("requires", [])
            if required_id not in effective_after_build
        ]
        for patch_id in installed_ids - shadowed_ids
    }
    missing = {
        patch_id: required_ids
        for patch_id, required_ids in missing.items()
        if required_ids
    }
    if missing:
        details = "; ".join(
            f"{patch_id} requires {', '.join(required_ids)}"
            for patch_id, required_ids in sorted(missing.items())
        )
        raise PatchError(
            "installed patch dependency is incomplete; select the required "
            f"patch or remove the dependent patch first: {details}")
    return patch_ms41.build(
        base_data, selected_ids, marker=marker)


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
