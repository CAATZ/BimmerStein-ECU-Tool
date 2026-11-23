"""Offline composition of user-supplied MS42/MS43 XDF patch lists."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field as dataclass_field
from functools import lru_cache
from hashlib import sha256
import re
from xml.etree import ElementTree as ET

from ms42_ms43_image import FULL_SIZE, PROFILES, checksum_status, correct_checksums, extract_calibration, image_identity
from ms42_ms43_xdf import XDFDefinitions, parse_xdf, xdf_bytes


MS42 = "8de9c6e65996f8c492c24c711d7b0f3ba07c83a3c6df9edaf3da5e8d3a091e18"
MS43 = "0bdfc536e658364475300db1cfeddd22ef372649f69656ca2f77f47cd0881425"
MS43_LX = "7b50afac451e4a9ff0b476765a5ecd5e67eac5929f589324236e5028997bc5f4"
EDITIONS = {MS42: "Community", MS43: "Standard", MS43_LX: "Extended Load"}
# Authorship and revision labels may change independently of the XDF's behavior.
FUNCTIONAL_PROFILES = {
    "40bbcdbfa12d9d2b506e68acb22c15cfb3d4c5f564943bd5b397cb0799dcf9d4": MS42,
    "b7ff85b8abd5454621bb7608c427032bb9728612eeeda7e35aa2531b3499c00d": MS43,
    "b77a82ceceb849f8c22f6f8711e2e95a6a4b0a2611d7e2f7435aa96fd68496b8": MS43_LX,
}
# Three executable windows differ on the two 430069 reference programs and are
# outside every published patch entry and the complete calibration region.
EDITION_SIGNATURE = {
    "Standard": "357ffc8248fe033dc2c77a954f11277d6537d955f55091efba4bf1f89ad42e07",
    "Extended Load": "61edf16167935320e5e379a56350eb0e13c6515b4634cf1c0703a605b4a066a5",
}
MS42_REQUIRES = {"0x556f": "0x3104", "0x1cd5": "0x3104", "0x2547": "0x3104",
                 "0x6674": "0x32f2", "0x4b19": "0x2075", "0x7e0f": "0x5070"}
MS43_REQUIRES = {"0x5bfa": "0x4775", "0x2ffe": "0x4775", "0x24ef": "0x691e",
                 "0x1160": "0x5cf3", "0x2288": "0x2d8", "0x6067": "0x2729"}
# Binary contracts from the three reviewed lists, independent of XML IDs and labels.
# Calibration-only presets retain their contract when their default values change.
KNOWN_GROUPS = {
    "MS42": {
        "0220620b71139285916c2b0b", "0f4002fd4ae519785e49086b", "12ce165dbe5df9cffcdb7012",
        "2076b23733acf55c6b03a4fa", "2ecde0610b8f44a27ec26504", "58935d1e0f322db20d317dba",
        "5cb2864eaec2bf75d7fdea19", "5cff5cf5989c7b09dfd8b6ff", "7f0c7afb7cef2009ab05a9c0",
        "8d39e2f8d83e0bf3fa7842c4", "a5bb07a747ad3ff785ea7944", "aab7cd5f26630271452a1ea4",
        "b4243792026121bd48e0939e", "bf6c10af2c365ca7da2c8353", "c0078affae6f80e5209b08cc",
        "c22c903f5d71b5db130a4eae", "c518953ac3cc8a127d7e4324", "cd6dc2b005650b0a4acae9f6",
        "d271089de496eb387006ae8a", "d6b54b111457c44022265016", "eb43fab19e1875fca041e857",
    },
    "MS43": {
        "0a3965a4a40af6a8d53f9b9d", "0be0e560c6fa45fb4bba3b35", "10b4d98c2c15cf103f187a82",
        "37072704351aa8adf2faffe2", "3c5ca0939f5ac302999b8382", "5090d50eb25c0fbfc6af6a59",
        "54902dddeb745f222c21f7cb", "594415dc2e3f03327869200e", "61b53631fda10fa5c5e7f0a5",
        "68dd2d1a42285d8ea4ed3631", "74eb567de02cad8c5db315a6", "7abe1d83337f5cdd14ae9f72",
        "80d522c06b48dbbc76782ab7", "87f48abcee3c3b71b15bd3df", "8dc501e35de5486c4ccefb60",
        "97dd920df7634ac13ff79e1b", "a11c220132929dd4f8c185b9", "a1aa0e52040906495972fe7e",
        "ab9a12b7f0ef440ab774f8c1", "b0be34051e538c434609c328", "caeed8d82a41237f236a53a7",
        "d80a16322594e06081d96703", "d98ac87176c84007bf8bd856", "dd7026b6d02cbef1873cc2ca",
        "e5343d0f1b0979fc04a41a39", "e968b53bc14a4a1a324a6873", "ee8224fbe0d9f1cdf5b3adfa",
        "ef20b0da457b234c18071cdb", "f71a996699f95d41bbe1106d", "f7b408eb30b859ae339a45cc",
    },
}
RELATION_SIGNATURES = {
    "MS42": {
        "2076b23733acf55c6b03a4fa": "2ecde0610b8f44a27ec26504",
        "12ce165dbe5df9cffcdb7012": "2ecde0610b8f44a27ec26504",
        "0f4002fd4ae519785e49086b": "aab7cd5f26630271452a1ea4",
        "c0078affae6f80e5209b08cc": "5cff5cf5989c7b09dfd8b6ff",
        "cd6dc2b005650b0a4acae9f6": "8d39e2f8d83e0bf3fa7842c4",
    },
    "MS43": {
        "e968b53bc14a4a1a324a6873": "80d522c06b48dbbc76782ab7",
        "8dc501e35de5486c4ccefb60": "80d522c06b48dbbc76782ab7",
        "d98ac87176c84007bf8bd856": "d80a16322594e06081d96703",
        "87f48abcee3c3b71b15bd3df": "f7b408eb30b859ae339a45cc",
        "7abe1d83337f5cdd14ae9f72": "ee8224fbe0d9f1cdf5b3adfa",
        "dd7026b6d02cbef1873cc2ca": "37072704351aa8adf2faffe2",
    },
}
_CLEANUP_IDS = {"0x1fec", "0x406d"}
CLEANUP_SIGNATURES = {"54902dddeb745f222c21f7cb", "594415dc2e3f03327869200e"}
# Retain the former XML matching only when validating existing rollback records.
LEGACY_RELATIONS = {
    "0x556f": "f396a40eb690dbf58507e195", "0x1cd5": "29ae2a5e3ec42c1f9e0b3b13",
    "0x2547": "68a3e987606edff3a25c84b3", "0x6674": "a085af41adfbcb715d6d850b",
    "0x4b19": "2ab70f594c2ee20ea5d8aa50", "0x7e0f": "d77dbc4298a17ddb3b5e1fc9",
    "0x5bfa": "8dead987f9740fce5f572f65", "0x2ffe": "c853862515a4d1630bbd6a65",
    "0x24ef": "7a972629a628a5d35890b73e", "0x1160": "8f568592a2f2da626f2b0e96",
    "0x2288": "e2ea003ad2c4ffd04f00e6c9", "0x6067": "dbc0fd764918c8aacb99b491",
}


def _hash(data):
    return sha256(data).hexdigest()


@dataclass(frozen=True)
class Entry:
    name: str
    start: int
    data: bytes
    original: bytes | None


@dataclass(frozen=True)
class PatchGroup:
    id: str
    title: str
    description: str
    categories: tuple
    entries: tuple
    restriction: str
    read_only: bool = False


@dataclass(frozen=True)
class Catalog:
    title: str
    version: str
    author: str
    family: str
    software: str
    digest: str
    profile: str
    edition: str
    groups: tuple
    fields: tuple
    requires: dict
    cleanups: frozenset
    reviewed_groups: frozenset = frozenset()
    unknown_relations: frozenset = frozenset()
    notices: tuple = ()
    legacy_requires: dict = dataclass_field(default_factory=dict)


def _group(node, group_id, defs):
    title = node.findtext("title") or group_id
    try:
        categories = tuple(defs.categories.get(int(c.get("category"), 0), "Uncategorised")
                           for c in node.findall("CATEGORYMEM") if c.get("category"))
    except ValueError:
        return PatchGroup(group_id, title, node.findtext("description", ""), (), (), "Invalid XDF category")
    entries = []
    reason = ""
    try:
        if set(node.attrib) - {"uniqueid", "flags"} or int(node.get("flags", "0"), 0) != 0:
            raise ValueError("unsupported patch group attributes")
        if any(c.tag not in {"title", "description", "CATEGORYMEM", "XDFPATCHENTRY"} for c in node):
            raise ValueError("unsupported patch element")
        for it in node.findall("XDFPATCHENTRY"):
            if set(it.attrib) - {"name", "address", "datasize", "patchdata", "basedata"} or len(it):
                raise ValueError("unsupported patch entry format")
            start = int(it.attrib["address"], 0) + defs.base
            size = int(it.attrib["datasize"], 0)
            data = bytes.fromhex(it.attrib["patchdata"])
            original = bytes.fromhex(it.attrib["basedata"]) if "basedata" in it.attrib else None
            if size <= 0 or len(data) != size or (original is not None and len(original) != size):
                raise ValueError("patch byte string disagrees with declared size")
            if not 0 <= start < start + size <= defs.size:
                raise ValueError("patch entry outside declared BIN region")
            if any(start < e.start + len(e.data) and e.start < start + size for e in entries):
                raise ValueError("overlapping entries inside one group")
            entries.append(Entry(it.get("name", ""), start, data, original))
        if not entries:
            raise ValueError("empty patch group")
    except (KeyError, ValueError) as exc:
        reason = str(exc)
        entries = []
    return PatchGroup(group_id, title, node.findtext("description", ""), categories, tuple(entries), reason)


def _group_signature(group, family):
    profile = PROFILES[family]
    calibration = all(profile.calibration_offset <= e.start and
                      e.start + len(e.data) <= profile.calibration_offset + profile.partial_size
                      for e in group.entries)
    content = sorted((e.start, len(e.data) if calibration else e.data.hex(),
                      e.original.hex() if e.original is not None else None) for e in group.entries)
    return _hash(repr(content).encode())[:24]


def _updated_relationships(defs, groups):
    signatures = {g.id: _group_signature(g, defs.family) for g in groups if not g.restriction}
    known = KNOWN_GROUPS[defs.family] if defs.software in ("0110C6", "0110C7", "430069") else set()
    reviewed = {gid for gid, signature in signatures.items() if signature in known}
    requires = {}
    unresolved = set(signatures) - reviewed
    for gid in reviewed:
        parent = RELATION_SIGNATURES[defs.family].get(signatures[gid])
        if parent is None:
            continue
        matches = [i for i, signature in signatures.items() if signature == parent and i != gid]
        if len(matches) == 1:
            requires[gid] = matches[0]
        else:
            unresolved.add(gid)
    cleanups = {gid for gid in reviewed if defs.family == "MS43" and signatures[gid] in CLEANUP_SIGNATURES}
    return requires, cleanups, reviewed, unresolved


def _legacy_relationships(defs, profile, groups, nodes):
    candidates = MS42_REQUIRES if defs.family == "MS42" else MS43_REQUIRES
    if profile in EDITIONS:
        ids = {g.id for g in groups}
        return {child: parent for child, parent in candidates.items()
                if child in ids and parent in ids}
    if profile or defs.base or defs.software not in ("0110C6", "430069"):
        return {}
    indexed = {g.id: node for g, node in zip(groups, nodes) if not g.restriction}
    requires = {}
    for child, parent in candidates.items():
        if parent in indexed and child in indexed:
            pair = ET.Element("relationship")
            pair.extend([indexed[parent], indexed[child]])
            canonical = ET.canonicalize(ET.tostring(pair, encoding="unicode"), strip_text=True)
            if _hash(" ".join(canonical.split()).encode())[:24] == LEGACY_RELATIONS[child]:
                requires[child] = parent
    return requires


def _functional_profile(root):
    normalized = deepcopy(root)
    header = normalized.find("XDFHEADER")
    for node in list(header):
        if node.tag in ("fileversion", "author"):
            header.remove(node)
    for node in normalized.iter():
        if node.text and not node.text.strip() and len(node):
            node.text = None
        if node.tag == "description" and node.text:
            node.text = re.sub(r"[\r\n]+", "\n", node.text)
        if node.tail and not node.tail.strip():
            node.tail = None
    canonical = ET.canonicalize(ET.tostring(normalized, encoding="unicode"))
    return FUNCTIONAL_PROFILES.get(_hash(canonical.encode()), "")


def _edited_ms43_edition(defs, fields, groups):
    if defs.base != 0:
        return None
    scaler = [f["data"] for f in fields if f.get("data", {}).get("address") == 0x70c3e]
    if len(scaler) != 1:
        return None
    data = scaler[0]
    if (data.get("address"), data.get("width"), data.get("units"), data.get("intercept")) != (0x70c3e, 1, "mg/stk", 0):
        return None
    found = {_group_signature(g, defs.family) for g in groups if not g.restriction}
    normal = {"74eb567de02cad8c5db315a6", "caeed8d82a41237f236a53a7"}
    extended = {"68dd2d1a42285d8ea4ed3631", "37072704351aa8adf2faffe2"}
    if data.get("slope") == 5.4470588 and normal <= found and not extended & found:
        return "Standard"
    if data.get("slope") == 10.8941176 and extended <= found and not normal & found:
        return "Extended Load"
    return None


@lru_cache(maxsize=6)
def _catalog(raw):
    root = parse_xdf(raw)
    defs = XDFDefinitions(root, allow_patch_only=True)
    nodes = root.findall("XDFPATCH")
    if not nodes:
        raise ValueError("This XDF contains no patch groups")
    if defs.size != FULL_SIZE:
        raise ValueError("Patch lists must describe a full 512 KiB image")
    header = root.find("XDFHEADER")
    digest = _hash(raw)
    profile = digest
    if digest not in EDITIONS and "COMMUNITY PATCHLIST" in defs.title.upper():
        profile = _functional_profile(root)
    edition = EDITIONS.get(profile, "Imported")
    ids = []
    for node in nodes:
        try:
            ids.append(hex(int(node.get("uniqueid", ""), 0)))
        except ValueError:
            ids.append("")
    counts = Counter(ids)
    groups = tuple(_group(node, uid if uid and counts[uid] == 1 else f"group-{i}", defs)
                   for i, (node, uid) in enumerate(zip(nodes, ids)))
    from ms42_ms43_xdf_fields import build_fields
    fields = tuple(build_fields(defs)) if defs.entries or defs.flags else ()
    reviewed = MS42_REQUIRES if profile == MS42 else MS43_REQUIRES if profile in (MS43, MS43_LX) else {}
    requires = {child: parent for child, parent in reviewed.items() if child in counts and parent in counts}
    cleanups = set(_CLEANUP_IDS) if profile in (MS43, MS43_LX) else set()
    reviewed_groups = {g.id for g in groups if not g.restriction}
    unresolved = set()
    if profile not in EDITIONS:
        requires, cleanups, reviewed_groups, unresolved = _updated_relationships(defs, groups)
        if defs.family == "MS43" and defs.software == "430069":
            identified = _edited_ms43_edition(defs, fields, groups)
            if identified or not profile:
                edition = identified or "Unverified MS43 community edition"
        elif defs.family == "MS42" and defs.software == "0110C6" and not unresolved:
            if {_group_signature(g, defs.family) for g in groups} == KNOWN_GROUPS["MS42"]:
                edition = "Community"
    notices = (("Some patch relationships are unknown. Select those patches to review their compatibility notes.",)
               if unresolved else ())
    legacy_requires = _legacy_relationships(defs, profile, groups, nodes)
    return Catalog(defs.title, header.findtext("fileversion", ""), header.findtext("author", ""),
                   defs.family, defs.software, digest, profile, edition, groups, fields, requires, frozenset(cleanups),
                   frozenset(reviewed_groups), frozenset(unresolved), notices, legacy_requires)


def load_catalog(path):
    return _catalog(xdf_bytes(path))


def _check_image(catalog, image):
    extract_calibration(image)
    identity = image_identity(image)
    revisions = {catalog.software}
    if catalog.family == "MS42" and catalog.software == "0110C6" and catalog.edition == "Community":
        revisions.add("0110C7")
    if identity["family"] != catalog.family or identity["software_id"].upper() not in revisions:
        raise ValueError("Patch list and BIN family/software do not match")
    if catalog.edition == "Unverified MS43 community edition":
        raise ValueError("The MS43 catalog edition is unclear: its settings and MAF options must identify Standard or Extended Load")
    if catalog.edition in EDITION_SIGNATURE:
        actual = _hash(b"".join(image[a:a + 32] for a in (0x16c80, 0x3b500, 0x3bb40)))
        if actual != EDITION_SIGNATURE[catalog.edition]:
            raise ValueError(f"BIN program does not match the {catalog.edition} MS43 edition")


def catalog_match_reason(catalog, image):
    try:
        _check_image(catalog, image)
        return ""
    except ValueError as exc:
        return str(exc)


def catalog_matches(catalog, image):
    return not catalog_match_reason(catalog, image)


def _cleanup(catalog, gid):
    return gid in catalog.cleanups


def _slice(data, entry):
    return data[entry.start:entry.start + len(entry.data)]


def _matches(group, image):
    return bool(group.entries) and all(_slice(image, e) == e.data for e in group.entries)


def _group_map(catalog):
    return {g.id: g for g in catalog.groups}


def _span(at, before, after, mask=None):
    row = {"start": at, "before": bytes(before).hex(), "after": bytes(after).hex()}
    if mask is not None:
        row["mask"] = bytes(mask).hex()
    return row


def _parts(s):
    a = s["start"]
    b, v = bytes.fromhex(s["before"]), bytes.fromhex(s["after"])
    m = bytes.fromhex(s["mask"]) if "mask" in s else b"\xff" * len(v)
    if type(a) is not int or not 0 <= a < a + len(v) <= FULL_SIZE or len(b) != len(v) or len(m) != len(v):
        raise ValueError("Invalid rollback span")
    return a, b, v, m


def _reverse(data, span):
    a, b, v, mask = _parts(span)
    actual = data[a:a + len(v)]
    if any((x ^ y) & m for x, y, m in zip(actual, v, mask)):
        raise ValueError(f"Owned bytes changed at 0x{a:X}; rollback would overwrite an edit")
    data[a:a + len(v)] = bytes((x & ~m) | (y & m) for x, y, m in zip(actual, b, mask))


def _relation(catalog, parent, child):
    return catalog.requires.get(child) == parent


def _overlap(group, other):
    return any(e.start < p.start + len(p.data) and p.start < e.start + len(e.data)
               for e in group.entries for p in other.entries)


def _present(catalog, group, image, active):
    if group.id in active or _matches(group, image):
        return True
    # A verified ordered extension can replace part of its prerequisite's marker.
    children = [g for g in catalog.groups if _relation(catalog, group.id, g.id) and _matches(g, image)]
    if not children or not group.entries:
        return False
    for e in group.entries:
        expected = bytearray(e.data)
        for child in children:
            for c in child.entries:
                lo, hi = max(c.start, e.start), min(c.start + len(c.data), e.start + len(e.data))
                if lo < hi:
                    expected[lo-e.start:hi-e.start] = c.data[lo-c.start:hi-c.start]
        if _slice(image, e) != expected:
            return False
    return True


def _field_owners(catalog, field, *, legacy_receipt=False):
    data = field.get("data")
    if not data or field["read_only"]:
        return []
    owners = [g.id for g in catalog.groups if not g.restriction and not _cleanup(catalog, g.id) and any(
        e.start < data["end"] and data["address"] < e.start + len(e.data) for e in g.entries)]
    # MS42 beta items share a browsing category across independent features.
    # Older receipts used that category to name the installed owner.
    beta_overlap = bool(owners) and catalog.profile == MS42 and field["category"] == "Beta Testing - Use With Caution"
    if catalog.profile in EDITIONS and (not beta_overlap or legacy_receipt):
        owners.extend(g.id for g in catalog.groups if not g.restriction
                      and field["category"] in g.categories and not _cleanup(catalog, g.id))
        # The two MAF views share storage and categories but use different physical units.
        if field["category"] == "MAF Sensor Hack":
            owners = [i for i in owners if any(g.id == i and
                      any(s in field["title"] and s in g.title for s in ("2048kg/h", "4096kg/h"))
                      for g in catalog.groups)]
    elif catalog.profile not in EDITIONS:
        # A category can contain unrelated features. Associate it only with one reviewed family.
        for category in field["categories"]:
            candidates = [g.id for g in catalog.groups if not g.restriction
                          and category in g.categories and not _cleanup(catalog, g.id)]
            if not set(candidates) <= catalog.reviewed_groups - catalog.unknown_relations:
                continue
            roots = {catalog.requires.get(i, i) for i in candidates}
            if len(roots) == 1 and all(catalog.requires.get(i, i) in roots for i in owners):
                owners.extend(candidates)
    parents = [catalog.requires[i] for i in owners if i in catalog.requires]
    return list(dict.fromkeys(owners + parents))


def _covers_cell(group, start, size):
    return any(e.start <= start and start + size <= e.start + len(e.data) for e in group.entries)


def configuration(catalog, image, history=None):
    from ms42_ms43_xdf_fields import field_summary, read_field
    _check_image(catalog, image)
    rows = []
    cells = 0
    for field in catalog.fields:
        cells += field["rows"] * field["columns"] + field["rows"] + field["columns"]
        if cells > 100_000:
            raise ValueError("Patch list exceeds the 100,000-cell configuration preview limit")
        row = (read_field(field, image) if "data" in field else
               dict(field_summary(field), values=[], axes={}))
        owners = _field_owners(catalog, field)
        note = ("No patch association was identified. This setting is saved independently."
                if not owners and not row["read_only"] else "")
        row.update(field_id=field["id"], owners=owners, ownership_note=note)
        rows.append(row)
    return rows


def _change_span(catalog, data, change):
    from ms42_ms43_xdf_fields import field_write_mask, write_field
    field = next((f for f in catalog.fields if f["id"] == change["field_id"]), None)
    if field is None:
        raise ValueError("Unknown configuration field")
    axis = change.get("axis", "z")
    if axis not in ("x", "y", "z"):
        raise ValueError("Unknown configuration axis")
    old = bytes(data)
    at, after = write_field(field, data, change["value"], row=change.get("row", 0),
                            column=change.get("column", 0), axis=axis)
    profile = PROFILES[catalog.family]
    if (not field.get("can_view") and
            not profile.calibration_offset <= at < at + len(after) <= profile.calibration_offset + profile.partial_size):
        raise ValueError("This setting lies outside the writable calibration")
    mask = None
    layout = field["data"] if axis == "z" else field["axes"][axis]["data"]
    write_mask = field_write_mask(field, axis, change.get("row", 0), layout)
    if write_mask:
        mask = write_mask.to_bytes(len(after), layout["endian"])
    return field, _span(at, old[at:at + len(after)], after, mask)


def _active(operations):
    return {op["group_id"] for op in operations if op["kind"] == "install"}


def _track_preconfiguration(saved, operation):
    standalone = operation["kind"] == "configure" and operation["group_id"] is None
    if not standalone and not saved:
        return
    for span in operation["spans"]:
        start, before, after, masks = _parts(span)
        for i, (old, new, mask) in enumerate(zip(before, after, masks), start):
            bits, baseline = saved.get(i, (0, 0))
            if standalone:
                saved[i] = bits | mask, (baseline & bits) | (old & mask & ~bits)
            elif bits & (old ^ new) & mask:
                remaining = bits & ~((old ^ new) & mask)
                if remaining:
                    saved[i] = remaining, baseline & remaining
                else:
                    del saved[i]


def _preconfiguration(operations):
    saved = {}
    for op in operations:
        _track_preconfiguration(saved, op)
    return saved


def _original_history(catalog, image, history, exact=False):
    """Authenticate the shape and owned bytes against the selected catalog before undo."""
    data = bytearray(image)
    if history is None:
        return data, []
    if not isinstance(history, dict) or history.get("version") != 1 or history.get("catalog_digest") != catalog.digest:
        raise ValueError("Rollback history belongs to a different catalog or format")
    for key in ("output_sha256", "source_sha256", "parent_sha256"):
        if not isinstance(history.get(key), str) or not re.fullmatch("[0-9a-f]{64}", history[key]):
            raise ValueError("Invalid rollback history digest")
    if exact and history["output_sha256"] != _hash(image):
        raise ValueError("Rollback history does not match this output BIN")
    ops = history.get("operations")
    if not isinstance(ops, list) or len(ops) > 8192:
        raise ValueError("Invalid rollback operation list")
    groups = _group_map(catalog)
    installed = set()
    try:
        for op in ops:
            owner = op["group_id"]
            if owner is not None and (owner not in groups or groups[owner].restriction):
                raise ValueError("Unknown or unavailable rollback owner")
            if op["kind"] == "install":
                if owner is None or owner in installed or len(op["spans"]) != len(groups[owner].entries):
                    raise ValueError("Invalid recorded installation")
                installed.add(owner)
                for e, s in zip(groups[owner].entries, op["spans"]):
                    at, before, after, mask = _parts(s)
                    if at != e.start or after != e.data or "mask" in s:
                        raise ValueError("Receipt contains bytes outside its declared patch")
            elif op["kind"] != "configure" or len(op["spans"]) != 1:
                raise ValueError("Invalid rollback operation")
        for op in reversed(ops):
            if op["kind"] == "configure":
                s, = op["spans"]
                _reverse(data, s)
                # Re-encode through the same bounded field and mask used by the UI.
                test = data.copy()
                field, expected = _change_span(catalog, test, op["change"])
                at, before, after, mask = _parts(s)
                ea, eb, ev, em = _parts(expected)
                owner = op["group_id"]
                if (at != ea or mask != em or any((v ^ e) & m for v, e, m in zip(after, ev, mask))
                        or owner is not None and (owner not in _field_owners(catalog, field, legacy_receipt=True)
                           or field.get("can_view") and not _covers_cell(groups[owner], at, len(after)))):
                    raise ValueError("Receipt configuration differs from its declared field")
            else:
                for s in reversed(op["spans"]):
                    _reverse(data, s)
        # Validate the preconditions in chronological order as well: a portable
        # receipt cannot invent mixed originals or manufacture a prerequisite.
        replay = data.copy()
        preceding = set()
        saved = {}
        for op in ops:
            owner = op["group_id"]
            if op["kind"] == "install":
                group = groups[owner]
                if "requires" in op and op["requires"] != catalog.requires.get(owner):
                    raise ValueError("Recorded prerequisite differs from its patch")
                requirements = catalog.requires if "requires" in op else catalog.legacy_requires
                _, reason = _blocker(catalog, group, replay, preceding, requirements=requirements)
                reason = reason or _guard_error(catalog, group, replay, preceding, saved)
                if reason:
                    raise ValueError(f"Invalid rollback installation: {reason}")
                preceding.add(owner)
            elif owner is not None:
                if not _present(catalog, groups[owner], replay, preceding):
                    raise ValueError("Rollback configuration owner was not installed")
                preceding.add(owner)
            _track_preconfiguration(saved, op)
            for s in op["spans"]:
                at, _, after, mask = _parts(s)
                prior = replay[at:at + len(after)]
                replay[at:at + len(after)] = bytes((a & ~m) | (v & m) for a, v, m in zip(prior, after, mask))
    except (TypeError, KeyError, IndexError, AttributeError, OverflowError) as exc:
        raise ValueError("Malformed rollback history") from exc
    return data, ops


def validate_history(catalog, image, history):
    _check_image(catalog, image)
    if history is None:
        raise ValueError("No rollback history supplied")
    _original_history(catalog, image, history, exact=True)
    return True


def _blocker(catalog, group, image, active, *, requirements=None):
    req = (catalog.requires if requirements is None else requirements).get(group.id)
    groups = _group_map(catalog)
    if req and not _present(catalog, groups[req], image, active):
        return "requires", f"Requires {groups[req].title}"
    for other in catalog.groups:
        if (other.id == group.id or other.restriction or _cleanup(catalog, other.id)
                or _relation(catalog, other.id, group.id)
                or (other.id not in active and not _matches(other, image))):
            continue
        for e in group.entries:
            for p in other.entries:
                lo, hi = max(e.start, p.start), min(e.start + len(e.data), p.start + len(p.data))
                if lo < hi and e.data[lo-e.start:hi-e.start] != p.data[lo-p.start:hi-p.start]:
                    return "conflict", f"Overlaps {other.title} at 0x{lo:X}"
    return "", ""


def _guard_error(catalog, group, image, active, saved):
    shared = {}
    for other in catalog.groups:
        if other.id in active and other.id != group.id:
            for e in other.entries:
                shared.update(enumerate(e.data, e.start))
    for e in group.entries:
        if e.original is None and not saved:
            continue
        for j, (actual, target) in enumerate(zip(_slice(image, e), e.data)):
            i = e.start + j
            bits, baseline = saved.get(i, (0, 0))
            if (actual ^ target) & bits:
                return f"Preset would overwrite a separately saved setting at 0x{i:X}. Use the BIN from before the edit to select the preset and edit the setting together."
            if e.original is None:
                continue
            original = e.original[j]
            before_edit = (actual & ~bits) | (baseline & bits)
            if actual != original and before_edit != original and not (actual == target == shared.get(i)):
                return f"Original bytes differ at 0x{i:X}"
    return ""


def _external_removable(catalog, group, image, active, saved):
    return (bool(group.entries) and not _cleanup(catalog, group.id) and _matches(group, image)
            and all(e.original is not None for e in group.entries)
            and not any((e.data[i - e.start] ^ e.original[i - e.start]) & mask
                        for i, (mask, _) in saved.items() for e in group.entries
                        if e.start <= i < e.start + len(e.data))
            and not any(g.id != group.id and _relation(catalog, group.id, g.id)
                        and _present(catalog, g, image, active) for g in catalog.groups)
            and not any(g.id != group.id and _overlap(group, g)
                        and (g.id in active or _matches(g, image)) and not _cleanup(catalog, g.id)
                        and not _relation(catalog, g.id, group.id) for g in catalog.groups))


def inspect(catalog, image, history=None):
    _check_image(catalog, image)
    original, ops = _original_history(catalog, image, history)
    active = _active(ops)
    saved = _preconfiguration(ops)
    configurable = {gid for f in catalog.fields for gid in _field_owners(catalog, f)}
    edited = {o["group_id"] for o in ops if o["kind"] == "configure" and o["group_id"]}
    rows = []
    for g in catalog.groups:
        needs_original = any(e.original is None for e in g.entries)
        reason = g.restriction
        removable = False
        if reason:
            state = "unavailable"
        elif g.id in active:
            state = "configured" if g.id in edited else "applied"
            removable = True
        elif g.id in edited and _matches(g, original):
            state = "configured"
            removable = _external_removable(catalog, g, original, active, saved)
        elif _present(catalog, g, image, active):
            state = "matching"
            reason = "Result bytes already present"
            removable = _external_removable(catalog, g, image, active, saved)
        else:
            state, reason = _blocker(catalog, g, image, active)
            if not state:
                reason = _guard_error(catalog, g, image, active, saved)
                state = "conflict" if reason else "needs_original" if needs_original else "ready"
                if state == "needs_original":
                    reason = "Some original bytes are unspecified; review the affected ranges"
        compatibility_note = ""
        if g.id in catalog.unknown_relations:
            compatibility_note = ("The required companion patch could not be identified in this list."
                                  if g.id in catalog.reviewed_groups else
                                  "Dependency relationships are unknown for this new or changed patch. Review its XDF description.")
        rows.append(dict(id=g.id, title=g.title, description=g.description, categories=list(g.categories),
                         compatibility_note=compatibility_note,
                         state=state, reason=reason, read_only=g.read_only, installable=state in ("ready", "needs_original"),
                         removable=removable, needs_original=needs_original, has_settings=g.id in configurable,
                         rollback_kind=("recorded" if g.id in active else "xdf_baseline") if removable else None,
                         unguarded=[{"start": e.start, "size": len(e.data)} for e in g.entries if e.original is None]))
    return rows


def _install(catalog, group, data, ops, allow_unbased, reference, saved):
    if group.restriction:
        raise ValueError(group.restriction)
    active = _active(ops)
    if group.id in active or _matches(group, data):
        raise ValueError(f"{group.title}: result already present")
    _, reason = _blocker(catalog, group, data, active)
    reason = reason or _guard_error(catalog, group, data, active, saved)
    if reason:
        raise ValueError(f"{group.title}: {reason}")
    spans = []
    for e in group.entries:
        before = _slice(data, e)
        if e.original is None:
            reference_matches = reference is not None and _slice(reference, e) == before
            if not reference_matches and not allow_unbased:
                raise ValueError(f"{group.title}: no original for 0x{e.start:X}; confirm replacement of existing bytes")
        spans.append(_span(e.start, before, e.data))
    for e in group.entries:
        data[e.start:e.start+len(e.data)] = e.data
    ops.append(dict(kind="install", group_id=group.id, spans=spans, requires=catalog.requires.get(group.id)))


def _replay_kept(original, ops, removed):
    """Rebase identical shared writes on the inverse image, retaining later owners."""
    retained = []
    for op in ops:
        if op["group_id"] in removed:
            continue
        spans = []
        for s in op["spans"]:
            at, _, after, mask = _parts(s)
            before = original[at:at + len(after)]
            replacement = bytes((a & ~m) | (z & m) for a, z, m in zip(before, after, mask))
            spans.append(_span(at, before, replacement, mask if "mask" in s else None))
            original[at:at+len(after)] = replacement
        retained.append(dict(op, spans=spans))
    return retained


def _check_pending_aliases(seen, span):
    at, _, data, mask = _parts(span)
    for i, (v, m) in enumerate(zip(data, mask), at):
        existing, bits = seen.get(i, (0, 0))
        if (existing ^ v) & bits & m:
            raise ValueError(f"Conflicting settings share storage at 0x{i:X}")
        seen[i] = (existing & ~m) | (v & m), bits | m


def build(catalog, image, *, install=(), remove=(), changes=(), history=None,
          allow_unbased=False, reference=None):
    """Build a new checked full BIN and its JSON-serializable active rollback journal."""
    _check_image(catalog, image)
    if reference is not None:
        _check_image(catalog, reference)
        if image_identity(reference)["software_id"] != image_identity(image)["software_id"]:
            raise ValueError("Reference software differs from the patch input")
    original, old_ops = _original_history(catalog, image, history)
    groups = _group_map(catalog)
    installs, removes = list(dict.fromkeys(install)), set(remove)
    if set(installs) & removes:
        raise ValueError("Choose install or remove for each patch")
    if any(i not in groups for i in (*installs, *removes)):
        raise ValueError("Unknown patch group")
    if not installs and not removes and not changes:
        raise ValueError("Select a patch or a configuration change")
    active = _active(old_ops)
    for gid in removes:
        if groups[gid].restriction:
            raise ValueError(groups[gid].restriction)
        for child in catalog.groups:
            if (child.id not in removes and _relation(catalog, gid, child.id)
                    and _present(catalog, child, image, active)):
                raise ValueError(f"Remove {child.title} with its prerequisite")
    data = original
    ops = _replay_kept(data, old_ops, removes)
    saved = _preconfiguration(ops)
    # A previously unseen installation may be returned only to a complete, evidenced XDF baseline.
    remaining = removes - active
    while remaining:
        gid = next((i for i in remaining if not any(_relation(catalog, i, child) for child in remaining)), None)
        if gid is None:
            raise ValueError("Cyclic patch prerequisites")
        g = groups[gid]
        if not _external_removable(catalog, g, data, _active(ops), saved):
            raise ValueError(f"{g.title}: existing installation has no recoverable original")
        for e in g.entries:
            data[e.start:e.start+len(e.data)] = e.original
        remaining.remove(gid)
    pending = list(installs)
    while pending:
        item = next((g for g in pending if catalog.requires.get(g) not in pending), None)
        if item is None:
            raise ValueError("Cyclic patch prerequisites")
        _install(catalog, groups[item], data, ops, allow_unbased, reference, saved)
        pending.remove(item)
    seen = {}
    active = _active(ops)
    preconfig = bytes(data)
    owners_present = active | {op["group_id"] for op in ops if op["group_id"] is not None}
    for change in changes:
        if not isinstance(change, dict) or not {"field_id", "value"} <= change.keys():
            raise ValueError("Invalid patch setting change")
        field, span = _change_span(catalog, data, change)
        owners = _field_owners(catalog, field)
        owner = change.get("group_id")
        if owner is not None and owner not in owners:
            raise ValueError("Selected patch does not own this setting")
        at, _, word, _ = _parts(span)
        covering = [gid for gid in owners if _covers_cell(groups[gid], at, len(word))]
        if field.get("can_view"):
            # A cluster patch can own one message within a larger CAN object table.
            owners = covering
            if owner not in owners:
                owner = None
        physical = next((gid for gid in covering if gid in active), None)
        if owner is not None and not _present(catalog, groups[owner], preconfig, owners_present):
            owner = None
        if owner is None or physical is not None and _relation(catalog, owner, physical):
            owner = physical or next((gid for gid in owners if _present(catalog, groups[gid], preconfig, owners_present)), None)
        _check_pending_aliases(seen, span)
        if span["before"] == span["after"]:
            continue
        cell = {k: change[k] for k in ("field_id", "row", "column", "value", "axis") if k in change}
        ops.append(dict(kind="configure", group_id=owner, change=cell, spans=[span]))
    corrected, _ = correct_checksums(data, catalog.family)
    _check_image(catalog, corrected)
    status = checksum_status(corrected, catalog.family)
    if any(status.get(k) is not True for k in ("boot", "program", "cal")):
        raise ValueError("Built BIN failed family checksum verification")
    parent_hash = _hash(image)
    receipt = dict(version=1, catalog_digest=catalog.digest, parent_sha256=parent_hash,
                   source_sha256=history["source_sha256"] if history else parent_hash,
                   output_sha256=_hash(corrected), operations=ops)
    # Includes a second check of final owned spans after checksum calculation.
    _original_history(catalog, corrected, receipt, exact=True)
    return corrected, receipt
