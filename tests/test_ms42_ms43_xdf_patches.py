import copy
import json
import os
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from ms42_ms43_image import checksum_status, correct_checksums
from ms42_ms43_xdf import load_definitions
import ms42_ms43_xdf_patches as patches
from tests.test_ms42_ms43_image import synthetic_image


def fixture(tmp_path, groups, *, base=0, fields=False):
    root = ET.Element("XDFFORMAT", version="1.70")
    hdr = ET.SubElement(root, "XDFHEADER")
    ET.SubElement(hdr, "deftitle").text = "MS42 0110C6 test"
    ET.SubElement(hdr, "BASEOFFSET", offset=str(abs(base)), subtract=str(int(base < 0)))
    ET.SubElement(hdr, "REGION", size="0x80000", startaddress="0")
    for number, (name, entries) in enumerate(groups, 1):
        group = ET.SubElement(root, "XDFPATCH", uniqueid=hex(number))
        ET.SubElement(group, "title").text = name
        for address, old, new in entries:
            attrs = dict(address=hex(address-base), datasize=hex(len(new)), patchdata=new.hex())
            if old is not None:
                attrs["basedata"] = old.hex()
            ET.SubElement(group, "XDFPATCHENTRY", attrs)
    if fields:
        for n in range(2):
            flag = ET.SubElement(root, "XDFFLAG", uniqueid=hex(11+n))
            ET.SubElement(flag, "title").text = f"Flag {n}"
            ET.SubElement(flag, "EMBEDDEDDATA", mmedaddress=hex(0x48111-base), mmedelementsizebits="8")
            ET.SubElement(flag, "mask").text = str(1 << n)
    p = tmp_path / "test.xdf"
    p.write_bytes(ET.tostring(root))
    return p


@pytest.fixture
def source():
    return correct_checksums(synthetic_image("MS42"), "MS42")[0]


@pytest.mark.parametrize("base", [-16, 32])
def test_patch_only_source_and_group_local_invalidity(tmp_path, source, base):
    p = fixture(tmp_path, [("One", [(0x48110, source[0x48110:0x48111], b"\x10")]),
                           ("Bad", [(0x80000, b"x", b"y")])], base=base)
    with pytest.raises(ValueError, match="no constants"):
        load_definitions(p)
    c = patches.load_catalog(p)
    rows = patches.inspect(c, source)
    assert [(r["id"], r["state"]) for r in rows] == [("0x1", "ready"), ("0x2", "unavailable")]
    assert not any(r["read_only"] for r in rows)
    with pytest.raises(ValueError, match="outside"):
        patches.build(c, source, install=["0x1", "0x2"])
    out, h = patches.build(c, source, install=["0x1"])
    assert out[0x48110] == 16 and source != out
    assert patches.validate_history(c, out, json.loads(json.dumps(h)))
    assert patches.build(c, out, remove=["0x1"], history=h)[0] == source
    assert patches.build(c, out, remove=["0x1"])[0] == source
    with pytest.raises(ValueError):
        patches.build(c, source[0x48000:0x50000], install=["0x1"])


def test_patch_names_do_not_disable_valid_entries_or_settings(tmp_path, source):
    at = 0x48111
    path = fixture(tmp_path, [
        ("Immobilizer Bypass DTC Fix", [(at, source[at:at+1], b"\x00")]),
        ("Checksum Bypass", [(at+1, source[at+1:at+2], b"\x02")]),
        ("Clear Immobilizer Data", [(0x80000, b"x", b"y")]),
    ], fields=True)
    tree = ET.parse(path)
    tree.find("XDFFLAG/title").text = "EWS configuration flag"
    tree.write(path)
    catalog = patches.load_catalog(path)
    rows = patches.inspect(catalog, source)
    assert [r["state"] for r in rows] == ["ready", "ready", "unavailable"]
    assert not any(r["read_only"] for r in rows)
    assert "outside" in rows[2]["reason"]
    with pytest.raises(ValueError, match="outside"):
        patches.build(catalog, source, install=["0x3"])
    out, history = patches.build(catalog, source, install=["0x1", "0x2"],
                                 changes=[dict(field_id="field-0", value=True)])
    assert out[at:at+2] == b"\x01\x02"
    assert [r["state"] for r in patches.inspect(catalog, out, history)][:2] == ["configured", "applied"]
    assert patches.validate_history(catalog, out, json.loads(json.dumps(history)))
    assert patches.build(catalog, out, remove=["0x1", "0x2"], history=history)[0] == source


def test_overlapping_selection_rollback_and_unrelated_edits(tmp_path, source):
    at = 0x48110
    c = patches.load_catalog(fixture(tmp_path, [
        ("Partial overlay", [(at, source[at:at+2], b"AB")]),
        ("Compatible shared byte", [(at+1, source[at+1:at+3], b"BC")]),
        ("Incompatible variant", [(at+1, source[at+1:at+2], b"D")]),
    ]))
    with pytest.raises(ValueError, match="Overlaps"):
        patches.build(c, source, install=["0x1", "0x3"])
    out, h = patches.build(c, source, install=["0x1", "0x2"])
    later = bytearray(out)
    later[at+12] ^= 1
    next_bin, h2 = patches.build(c, later, remove=["0x1"], history=h)
    assert next_bin[at:at+3] == source[at:at+1] + b"BC"
    assert next_bin[at+12] == later[at+12]
    restored, _ = patches.build(c, next_bin, remove=["0x2"], history=h2)
    later[at:at+3] = source[at:at+3]
    assert restored == correct_checksums(later, "MS42")[0]
    damaged = bytearray(out)
    damaged[at+1] ^= 1
    with pytest.raises(ValueError, match="Owned bytes changed"):
        patches.build(c, damaged, remove=["0x1"], history=h)


def test_actual_unguarded_bytes_confirmation_and_reference(tmp_path, source):
    at = 0x48111
    c = patches.load_catalog(fixture(tmp_path, [("Calibration preset", [(at, None, b"AB")]),
                                                 ("Program addition", [(0x11050, None, b"CD")])]))
    assert patches.inspect(c, source)[0]["needs_original"]
    for gid in ("0x1", "0x2"):
        with pytest.raises(ValueError, match="confirm replacement"):
            patches.build(c, source, install=[gid])
    out, h = patches.build(c, source, install=["0x1"], allow_unbased=True)
    assert patches.inspect(c, out)[0]["removable"] is False
    assert h["operations"][0]["spans"][0]["before"] == source[at:at+2].hex()
    assert patches.build(c, out, remove=["0x1"], history=h)[0] == source
    out, history = patches.build(c, source, install=["0x2"], allow_unbased=True)
    assert patches.build(c, source, install=["0x2"], reference=source)[0] == out
    assert patches.validate_history(c, out, json.loads(json.dumps(history)))
    assert patches.build(c, out, remove=["0x2"], history=history)[0] == source
    forged = copy.deepcopy(history)
    forged["operations"][0]["spans"][0]["start"] += 256
    with pytest.raises(ValueError, match="declared patch"):
        patches.validate_history(c, out, forged)


def test_settings_masks_alias_conflict_and_untrusted_receipt(tmp_path, source):
    at = 0x48111
    c = patches.load_catalog(fixture(tmp_path, [("Calibration", [(at, source[at:at+1], b"\x00")])], fields=True))
    first, second = patches.configuration(c, source)
    changes = [dict(field_id=row["id"], value=True) for row in (first, second)]
    out, h = patches.build(c, source, install=["0x1"], changes=changes)
    assert out[at] == 3 and patches.inspect(c, out, h)[0]["state"] == "configured"
    assert patches.build(c, out, remove=["0x1"], history=h)[0] == source
    with pytest.raises(ValueError, match="Conflicting settings"):
        patches.build(c, source, changes=[changes[0], dict(changes[0], value=False)])
    for n in (0, 1):
        corrupt = copy.deepcopy(h)
        corrupt["operations"][n]["spans"][0]["start"] += 256
        with pytest.raises(ValueError):
            patches.validate_history(c, out, corrupt)
    wrong = dict(h, output_sha256="0" * 64)
    with pytest.raises(ValueError, match="does not match"):
        patches.validate_history(c, out, wrong)


def local_community_pairs():
    """Private local inputs stay outside the distribution."""
    if not os.environ.get("XDF_PATCH_FIXTURES"):
        pytest.skip("Set XDF_PATCH_FIXTURES to the local community fixture directory")
    fixtures = Path(os.environ["XDF_PATCH_FIXTURES"])
    pairs = [
        (fixtures / "Siemens_MS42_0110C6_Community_Patchlist_v1.7.1.xdf",
         fixtures / "Siemens_MS42_0110C6_E46_M52TUB28_EU3_LHD.bin", 22, 31),
        (fixtures / "Siemens_MS43_MS430069_Community_Patchlist_v2.9.2.xdf",
         fixtures / "Siemens_MS43_MS430069_E46_M54B30_US.bin", 27, 46),
        (fixtures / "Siemens_MS43_MS430069_Community_Patchlist_v2.9.2_Load_Extend.xdf",
         fixtures / "Siemens_MS43_MS430069_LX_E46_M54B30_EU4_LHD.bin", 28, 55),
    ]
    if not all(x.is_file() and b.is_file() for x, b, _, _ in pairs):
        pytest.skip("Three local community author lists and matching reference BINs are required")
    return pairs


def test_local_immobilizer_and_checksum_groups_apply_and_rollback(tmp_path):
    from external_patch_library import ExternalPatchLibrary

    pairs = local_community_pairs()
    assert sum(count for _, _, count, _ in pairs) == 77
    for path, bin_path, count, _ in pairs:
        image = bin_path.read_bytes()
        catalog = patches.load_catalog(path)
        assert all(not group.restriction for group in catalog.groups)
        rows = patches.inspect(catalog, image)
        assert len(rows) == count and all(r["state"] != "unavailable" for r in rows)
        groups = [g for g in catalog.groups if "Immobilizer" in g.title or "Checksum" in g.title]
        assert len(groups) == 4
        for group in groups:
            source = image
            if "Clear Immobilizer Data" in group.title and patches._matches(group, source):
                # The supplied MS43 files are already clear. Exercise nonempty data on a copy.
                populated = bytearray(source)
                populated[group.entries[0].start] ^= 1
                source = correct_checksums(populated, catalog.family)[0]
            directory = tmp_path / catalog.edition / group.id
            library = ExternalPatchLibrary(directory)
            key = library.import_file(path)
            out = library.build(source, key, install=[group.id], allow_unbased=True)
            assert all(out[e.start:e.start+len(e.data)] == e.data for e in group.entries)
            status = checksum_status(out)
            assert all(status[k] is True for k in ("boot", "program", "cal"))
            reopened = ExternalPatchLibrary(directory)
            history = reopened.receipt(out)
            row = next(r for r in patches.inspect(catalog, out, history) if r["id"] == group.id)
            assert row["state"] == "applied" and row["removable"]
            assert reopened.previous(out) == source
            receiver = ExternalPatchLibrary(directory / "receiver")
            receiver.import_file(path)
            assert receiver.import_receipt_bytes(out, json.dumps(history).encode()) == key
            assert receiver.build(out, key, remove=[group.id]) == source
        # MS42's full and calibration checksum patches share compatible writes.
        selected = [g.id for g in groups if not patches._matches(g, image)]
        out, history = patches.build(catalog, image, install=selected, allow_unbased=True)
        assert patches.build(catalog, out, remove=selected, history=history)[0] == image


def test_local_can_initializer_edits_and_id_alias_roundtrip(tmp_path):
    from external_patch_library import ExternalPatchLibrary
    for path, bin_path, _, _ in local_community_pairs():
        image = bin_path.read_bytes()
        catalog = patches.load_catalog(path)
        library = ExternalPatchLibrary(tmp_path / catalog.edition)
        key = library.import_file(path)
        initial = {f["title"]: f for f in patches.configuration(catalog, image)}
        raw = initial["CAN Bus Initialization Table"]
        ids = initial["CAN Bus ARBID Table"]
        assert not raw["read_only"] and not ids["read_only"]
        assert raw["read_only_rows"] == [15]
        assert sum(f["read_only"] for f in initial.values()) == 9
        base = 0x52000 if catalog.family == "MS42" else 0x6C494
        column = 14
        at = base + column * 16 + 2
        original_word = int.from_bytes(image[at:at+2], "big")
        old_id = original_word >> 5
        assert ids["values"][0][column] == old_id and original_word & 31
        raw_change = dict(field_id=raw["id"], row=7, column=0, value=image[base+7] ^ 1)
        id_change = dict(field_id=ids["id"], column=column, value=old_id ^ 1)
        out = library.build(image, key, changes=[raw_change, id_change])
        checksum = checksum_status(out)
        assert all(checksum[k] is True for k in ("boot", "program", "cal"))
        word = ((old_id ^ 1) << 5) | (original_word & 31)
        assert int.from_bytes(out[at:at+2], "big") == word
        reopened = {f["id"]: f for f in patches.configuration(catalog, out)}
        assert reopened[ids["id"]]["values"][0][column] == old_id ^ 1
        assert reopened[raw["id"]]["values"][2][column] == word >> 8
        assert reopened[raw["id"]]["values"][3][column] == word & 255
        assert out[base+7] == image[base+7] ^ 1
        receipt = library.receipt(out)
        assert all(op["group_id"] is None for op in receipt["operations"])
        assert receipt["operations"][1]["spans"][0]["mask"] == "ffe0"
        assert patches.validate_history(catalog, out, json.loads(json.dumps(receipt)))
        assert library.previous(out) == image
        # The five low bits are outside the ID edit, including when replaying a portable record.
        changed = bytearray(out)
        changed[at+1] ^= 1
        recovered, _ = patches._original_history(catalog, changed, receipt)
        assert recovered[at+1] == image[at+1] ^ 1
        # A modified external field can never extend the reviewed register-image window.
        tree = ET.parse(path)
        mem = tree.find("XDFTABLE[title='CAN Bus ARBID Table']/XDFAXIS[@id='z']/EMBEDDEDDATA")
        mem.set("mmedaddress", hex(base))
        shifted = tmp_path / "shifted.xdf"
        tree.write(shifted, encoding="utf-8")
        changed_catalog = patches.load_catalog(shifted)
        modified = next(f for f in patches.configuration(changed_catalog, image) if f["title"] == ids["title"])
        assert modified["read_only"]
        if catalog.family == "MS42":
            code = next(g.id for g in catalog.groups if g.title == "[PATCH] M Cluster LED Support")
            installed, history = patches.build(catalog, image, install=[code])
            edited, history = patches.build(catalog, installed, changes=[raw_change], history=history)
            assert history["operations"][-1]["group_id"] is None
            restored, _ = patches.build(catalog, edited, remove=[code], history=history)
            assert restored == patches.build(catalog, image, changes=[raw_change])[0]


def test_ms42_knock_settings_keep_real_owners_and_old_receipts(tmp_path):
    from external_patch_library import ExternalPatchLibrary
    path, bin_path, _, _ = local_community_pairs()[0]
    image = bin_path.read_bytes()
    catalog = patches.load_catalog(path)
    rows = [f for f in patches.configuration(catalog, image) if f["category"].startswith("Beta Testing")]
    assert {f["title"] for f in rows} == {"c_t_max_knk_mil", "lv_mil_knk_lv_1", "lv_mil_knk_lv_2"}
    mil = {g.id for g in catalog.groups if g.title.endswith("MIL Knock Indicator")}
    assert len(mil) == 2 and all(set(f["owners"]) == mil for f in rows)
    cluster = next(g.id for g in catalog.groups if g.title == "[PATCH] M Cluster LED Support")
    installed, history = patches.build(catalog, image, install=[cluster])
    flag = next(f for f in rows if f["kind"] == "flag")
    edit = dict(field_id=flag["id"], value=not flag["values"][0][0])
    with pytest.raises(ValueError, match="does not own"):
        patches.build(catalog, installed, changes=[dict(edit, group_id=cluster)], history=history)
    out, history = patches.build(catalog, installed, changes=[edit], history=history)
    assert history["operations"][-1]["group_id"] is None
    assert patches.build(catalog, out, history=history, remove=[cluster])[0] == patches.build(catalog, image, changes=[edit])[0]
    # Earlier versions could assign this exact edit to an already-installed cluster.
    old = copy.deepcopy(history)
    old["operations"][-1]["group_id"] = cluster
    library = ExternalPatchLibrary(tmp_path / "receiver")
    key = library.import_file(path)
    assert library.import_receipt_bytes(out, json.dumps(old).encode()) == key
    assert library.build(out, key, remove=[cluster]) == image


def test_local_community_lists_roundtrip_and_edition(tmp_path):
    pairs = local_community_pairs()
    for path, bin_path, count, nfields in pairs:
        image = bin_path.read_bytes()
        c = patches.load_catalog(path)
        assert len(patches.inspect(c, image)) == count
        fields = patches.configuration(c, image)
        assert len(fields) == nfields and all(f["values"] for f in fields)
        code = next(g.id for g in c.groups if g.title == "[PATCH] M Cluster LED Support")
        preset = next(g.id for g in c.groups if "M3 Cluster" in g.title and "Default Values" in g.title)
        speed = next(f for f in fields if f["title"] == "id_icl_toil_led__n")
        cell = dict(field_id=speed["id"], value=113, group_id=code)
        preconfigured, history = patches.build(c, image, changes=[cell])
        assert history["operations"][-1]["group_id"] is None
        assert next(f for f in patches.configuration(c, preconfigured)
                    if f["id"] == speed["id"])["values"][0][0] == 113
        out, h = patches.build(c, image, install=[preset, code], changes=[cell])
        assert len(out) == len(image)
        state = {g["id"]: g["state"] for g in patches.inspect(c, out, h)}
        assert state[code] == "applied" and state[preset] == "configured"
        assert next(f for f in patches.configuration(c, out) if f["id"] == speed["id"])["values"][0][0] == 113
        with pytest.raises(ValueError, match="prerequisite"):
            patches.build(c, out, remove=[code], history=h)
        assert patches.build(c, out, remove=[preset, code], history=h)[0] == image
        reordered = tmp_path / "same-content.xdf"
        reordered.write_bytes(path.read_bytes() + b"\n")
        normalized = patches.load_catalog(reordered)
        assert normalized.edition == c.edition and normalized.digest != c.digest
        assert normalized.profile == c.profile
        tree = ET.parse(path)
        tree.find("XDFHEADER/fileversion").text = "updated"
        tree.find("XDFHEADER/author").text = "Review copy"
        tree.write(reordered, encoding="utf-8")
        edited = patches.load_catalog(reordered)
        assert edited.digest != c.digest and edited.profile == c.profile
        assert (edited.version, edited.author) == ("updated", "Review copy")
        assert edited.edition == c.edition and edited.requires == c.requires
        assert edited.cleanups == c.cleanups and patches.catalog_matches(edited, image)
        assert [r["owners"] for r in patches.configuration(edited, image)] == [r["owners"] for r in fields]
        assert patches.build(edited, image, install=[preset, code], changes=[cell])[0] == out
        group = next(g for g in tree.findall("XDFPATCH") if g.get("uniqueid").lower() == preset)
        entry = group.find("XDFPATCHENTRY")
        payload = bytes.fromhex(entry.get("patchdata"))
        entry.set("patchdata", (bytes([payload[0] ^ 1]) + payload[1:]).hex())
        tree.write(reordered, encoding="utf-8")
        changed = patches.load_catalog(reordered)
        assert changed.profile != c.profile
        assert changed.requires == c.requires
        if c.family == "MS43":
            other_path = pairs[2 if c.edition == "Standard" else 1][1]
            assert not patches.catalog_matches(normalized, other_path.read_bytes())
            assert not patches.catalog_matches(edited, other_path.read_bytes())
            scaler = next(e for e in tree.findall("XDFCONSTANT") if e.findtext("title") == "c_maf_fl_accin")
            scaler.find("MATH").set("equation", "3*X")
            tree.write(reordered, encoding="utf-8")
            assert "edition" in patches.catalog_match_reason(patches.load_catalog(reordered), image)
        else:
            extension = next(g.id for g in c.groups if "OLM Boost" in g.title)
            extra, h = patches.build(c, image, install=[extension, code])
            assert patches.build(c, extra, remove=[code, extension], history=h)[0] == image
