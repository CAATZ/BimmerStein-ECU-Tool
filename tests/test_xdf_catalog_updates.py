import copy
import hashlib
import json
from xml.etree import ElementTree as ET

import pytest

from external_patch_library import ExternalPatchLibrary
from ms42_ms43_image import PROFILES, checksum_status, correct_checksums
import ms42_ms43_xdf_patches as patches
from tests.test_ms42_ms43_image import synthetic_image
from tests.test_ms42_ms43_xdf_patches import fixture, local_community_pairs


def updated_list(path, catalog, image, destination):
    tree = ET.parse(path)
    root = tree.getroot()
    root.find("XDFHEADER/fileversion").text = "next-version"
    root.find("XDFHEADER/author").text = "Updated author label"
    ids = {}
    for index, group in enumerate(root.findall("XDFPATCH")):
        old_id = hex(int(group.get("uniqueid"), 0))
        ids[old_id] = hex(0x90000 + index)
        group.set("uniqueid", ids[old_id])
        description = group.find("description")
        if description is None:
            description = ET.SubElement(group, "description")
        description.text = (description.text or "") + "\nUpdated release notes."
        for entry in group.findall("XDFPATCHENTRY"):
            entry.set("name", "Revised display label")
        group[:] = list(reversed(group))
    for field in root.findall("XDFTABLE"):
        if "CAN Bus" in field.findtext("title", ""):
            field.set("uniqueid", hex(int(field.get("uniqueid"), 0) + 0x100000))
            description = field.find("description")
            if description is None:
                description = ET.SubElement(field, "description")
            description.text = "Updated CAN display description."
    profile = PROFILES[catalog.family]
    occupied = [(entry.start, entry.start + len(entry.data))
                for group in catalog.groups for entry in group.entries]
    occupied += [(f["data"]["address"], f["data"]["end"])
                 for f in catalog.fields if "data" in f]
    address = next(at for at in range(profile.calibration_offset + 0x100,
                                     profile.calibration_offset + profile.partial_size - 0x40)
                   if not any(start <= at < end for start, end in occupied))
    extra = ET.SubElement(root, "XDFPATCH", uniqueid="0xF0000")
    ET.SubElement(extra, "title").text = "[PATCH] New independent calibration"
    ET.SubElement(extra, "XDFPATCHENTRY", address=hex(address), datasize="1",
                  basedata=image[address:address + 1].hex(), patchdata=bytes([image[address] ^ 1]).hex())
    # Move the entry display order without changing linked-axis IDs or their targets.
    header = root.find("XDFHEADER")
    root[:] = [header] + list(reversed([node for node in root if node is not header]))
    tree.write(destination, encoding="utf-8")
    return ids


def test_updated_lists_keep_dependencies_configuration_and_old_rollback(tmp_path):
    for path, bin_path, _, _ in local_community_pairs():
        image = bin_path.read_bytes()
        original = patches.load_catalog(path)
        directory = tmp_path / (original.family + original.edition)
        directory.mkdir()
        updated_path = directory / path.name
        ids = updated_list(path, original, image, updated_path)
        updated = patches.load_catalog(updated_path)
        assert updated.digest != original.digest
        if original.family == "MS43":
            assert updated.edition == original.edition
        assert updated.requires == {ids[child]: ids[parent] for child, parent in original.requires.items()}
        assert updated.cleanups == {ids[gid] for gid in original.cleanups}
        assert patches.catalog_matches(updated, image)
        original_fields = {f["title"]: f for f in patches.configuration(original, image)}
        updated_fields = {f["title"]: f for f in patches.configuration(updated, image)}
        for title, field in original_fields.items():
            current = updated_fields[title]
            assert current["read_only"] == field["read_only"], title
            if title in {"id_icl_toil_led__n", "id_icl_n_led__n", "c_t_max_knk_mil",
                         "lv_mil_knk_lv_1", "lv_mil_knk_lv_2"}:
                assert set(current["owners"]) == {ids[gid] for gid in field["owners"]}, title
        code = next(g.id for g in original.groups if g.title == "[PATCH] M Cluster LED Support")
        preset = next(g.id for g in original.groups if "M3 Cluster" in g.title and "Default Values" in g.title)
        selected = [ids[preset], ids[code]]
        field = updated_fields["id_icl_toil_led__n"]
        with pytest.raises(ValueError, match="Requires"):
            patches.build(updated, image, install=[ids[preset]])
        out, history = patches.build(updated, image, install=selected,
                                     changes=[dict(field_id=field["id"], value=113, group_id=ids[code])])
        assert all(checksum_status(out)[part] is True for part in ("boot", "program", "cal"))
        assert patches.validate_history(updated, out, json.loads(json.dumps(history)))
        assert patches.build(updated, out, remove=selected, history=history)[0] == image
        added, new_history = patches.build(updated, image, install=["0xf0000"])
        assert patches.build(updated, added, remove=["0xf0000"], history=new_history)[0] == image

        library = ExternalPatchLibrary(directory / "library")
        old_key = library.import_file(path)
        old_field = original_fields["id_icl_toil_led__n"]
        old_output = library.build(image, old_key, install=[preset, code],
                                   changes=[dict(field_id=old_field["id"], value=113, group_id=code)])
        old_receipt = copy.deepcopy(library.receipt(old_output))
        new_key = library.import_file(updated_path)
        assert library.preferred(old_output) == old_key
        assert library.receipt(old_output) == old_receipt
        with pytest.raises(ValueError, match="recorded catalog"):
            library.build(old_output, new_key, remove=selected)
        receiver = ExternalPatchLibrary(directory / "receiver")
        receiver.import_file(path)
        receiver.import_file(updated_path)
        assert receiver.import_receipt_bytes(old_output, json.dumps(old_receipt).encode()) == old_key
        assert receiver.build(old_output, old_key, remove=[preset, code]) == image


def test_updated_preset_defaults_keep_code_dependency(tmp_path):
    for path, bin_path, _, _ in local_community_pairs():
        original = patches.load_catalog(path)
        image = bin_path.read_bytes()
        destination = tmp_path / path.name
        ids = updated_list(path, original, image, destination)
        tree = ET.parse(destination)
        preset = next(g.id for g in original.groups if "M3 Cluster" in g.title and "Default Values" in g.title)
        code = original.requires[preset]
        node = next(g for g in tree.findall("XDFPATCH") if g.get("uniqueid") == ids[preset])
        entry = node.find("XDFPATCHENTRY")
        data = bytearray.fromhex(entry.get("patchdata"))
        data[0] ^= 1
        entry.set("patchdata", data.hex())
        tree.write(destination, encoding="utf-8")
        updated = patches.load_catalog(destination)
        assert updated.requires[ids[preset]] == ids[code]
        out, history = patches.build(updated, image, install=[ids[preset], ids[code]])
        assert out[int(entry.get("address"), 0)] == data[0]
        assert patches.build(updated, out, remove=[ids[preset], ids[code]], history=history)[0] == image

        changed_code = next(g for g in tree.findall("XDFPATCH") if g.get("uniqueid") == ids[code])
        instruction = changed_code.find("XDFPATCHENTRY")
        payload = bytearray.fromhex(instruction.get("patchdata"))
        payload[0] ^= 1
        instruction.set("patchdata", payload.hex())
        tree.write(destination, encoding="utf-8")
        unknown = patches.load_catalog(destination)
        assert ids[preset] not in unknown.requires
        assert unknown.notices
        row = next(r for r in patches.inspect(unknown, image) if r["id"] == ids[preset])
        assert row["compatibility_note"]


def test_relocated_setting_uses_its_new_patch_range(tmp_path):
    source = correct_checksums(synthetic_image("MS42"), "MS42")[0]
    old_address, new_address = 0x48111, 0x48121
    path = fixture(tmp_path, [("Example setting", [(old_address,
                   source[old_address:old_address + 1], b"\x00")])], fields=True)
    old_catalog = patches.load_catalog(path)
    old_output, old_receipt = patches.build(old_catalog, source, install=["0x1"],
                                           changes=[dict(field_id="field-0", value=True)])
    tree = ET.parse(path)
    group = tree.find("XDFPATCH")
    group.set("uniqueid", "0x1234")
    group.find("XDFPATCHENTRY").set("address", hex(new_address))
    group.find("XDFPATCHENTRY").set("basedata", source[new_address:new_address + 1].hex())
    for field in tree.findall("XDFFLAG"):
        field.find("EMBEDDEDDATA").set("mmedaddress", hex(new_address))
    newer = tmp_path / "newer.xdf"
    tree.write(newer)
    catalog = patches.load_catalog(newer)
    field = patches.configuration(catalog, source)[0]
    assert field["owners"] == ["0x1234"]
    output, receipt = patches.build(catalog, source, install=["0x1234"],
                                    changes=[dict(field_id=field["id"], value=True)])
    assert output[new_address] == 1 and output[old_address] == source[old_address]
    assert patches.build(catalog, output, remove=["0x1234"], history=receipt)[0] == source
    assert patches.build(old_catalog, old_output, remove=["0x1"], history=old_receipt)[0] == source


def test_metadata_updates_preserve_ms42_software_alias(tmp_path):
    path, bin_path, _, _ = local_community_pairs()[0]
    tree = ET.parse(path)
    tree.find("XDFHEADER/fileversion").text = "relabelled"
    for index, group in enumerate(tree.findall("XDFPATCH")):
        group.set("uniqueid", hex(0x90000 + index))
        description = group.find("description")
        if description is None:
            description = ET.SubElement(group, "description")
        description.text = "Revised description"
    destination = tmp_path / "relabelled.xdf"
    tree.write(destination)
    catalog = patches.load_catalog(destination)
    assert catalog.edition == "Community" and not catalog.notices
    # Admission-only fixture: both identity strings identify the existing C7 alias.
    image = bytearray(bin_path.read_bytes())
    profile = PROFILES["MS42"]
    for offset in (profile.sw_offset, profile.calibration_offset + 8):
        image[offset:offset + 6] = b"0110C7"
    assert patches.catalog_matches(catalog, image)


def test_mixed_category_keeps_unrelated_settings_independent(tmp_path):
    path, bin_path, _, _ = local_community_pairs()[0]
    image = bin_path.read_bytes()
    original = patches.load_catalog(path)
    destination = tmp_path / path.name
    ids = updated_list(path, original, image, destination)
    tree = ET.parse(destination)
    root = tree.getroot()
    cluster = next(g.id for g in original.groups if g.title == "[PATCH] M Cluster LED Support")
    cluster_node = next(g for g in root.findall("XDFPATCH") if g.get("uniqueid") == ids[cluster])
    category = cluster_node.find("CATEGORYMEM").get("category")
    extra = root.find("XDFPATCH[@uniqueid='0xF0000']")
    ET.SubElement(extra, "CATEGORYMEM", index="0", category=category)
    address = int(extra.find("XDFPATCHENTRY").get("address"), 0)
    occupied = [(e.start, e.start + len(e.data)) for g in original.groups for e in g.entries]
    occupied += [(f["data"]["address"], f["data"]["end"]) for f in original.fields if "data" in f]
    separate = next(at for at in range(address + 1, address + 100)
                    if not any(start <= at < end for start, end in occupied))
    for title, at in (("New patch setting", address), ("Independent setting", separate)):
        field = ET.SubElement(root, "XDFCONSTANT")
        ET.SubElement(field, "title").text = title
        ET.SubElement(field, "CATEGORYMEM", index="0", category=category)
        ET.SubElement(field, "EMBEDDEDDATA", mmedaddress=hex(at), mmedelementsizebits="8")
    tree.write(destination, encoding="utf-8")
    catalog = patches.load_catalog(destination)
    rows = {f["title"]: f for f in patches.configuration(catalog, image)}
    assert rows["New patch setting"]["owners"] == ["0xf0000"]
    assert rows["Independent setting"]["owners"] == []
    assert rows["Independent setting"]["ownership_note"]
    changes = [dict(field_id=rows[title]["id"], value=image[at] ^ 2)
               for title, at in (("New patch setting", address), ("Independent setting", separate))]
    installed, history = patches.build(catalog, image, install=[ids[cluster]])
    configured, history = patches.build(catalog, installed, changes=changes, history=history)
    assert all(op["group_id"] is None for op in history["operations"][-2:])
    removed, _ = patches.build(catalog, configured, remove=[ids[cluster]], history=history)
    assert removed == patches.build(catalog, image, changes=changes)[0]


def test_prior_data_only_receipt_survives_new_dependency_recognition(tmp_path):
    for path, bin_path, _, _ in local_community_pairs():
        source = bin_path.read_bytes()
        tree = ET.parse(path)
        preset = next(g for g in tree.findall("XDFPATCH")
                      if "M3 Cluster" in g.findtext("title", "") and "Default Values" in g.findtext("title", ""))
        description = preset.find("description")
        if description is None:
            description = ET.SubElement(preset, "description")
        description.text = (description.text or "") + "\nHistorical release note."
        tree.find("XDFHEADER/fileversion").text = "historical-update"
        updated_path = tmp_path / path.name
        tree.write(updated_path, encoding="utf-8")
        catalog = patches.load_catalog(updated_path)
        gid = hex(int(preset.get("uniqueid"), 0))
        assert gid in catalog.requires
        with pytest.raises(ValueError, match="Requires"):
            patches.build(catalog, source, install=[gid], allow_unbased=True)
        # The old importer lost this dependency after a description change and
        # wrote a version-1 installation record containing the actual input bytes.
        group = next(g for g in catalog.groups if g.id == gid)
        data = bytearray(source)
        spans = []
        for entry in group.entries:
            before = data[entry.start:entry.start + len(entry.data)]
            spans.append(dict(start=entry.start, before=before.hex(), after=entry.data.hex()))
            data[entry.start:entry.start + len(entry.data)] = entry.data
        output = correct_checksums(data, catalog.family)[0]
        source_hash = hashlib.sha256(source).hexdigest()
        receipt = dict(version=1, catalog_digest=catalog.digest, parent_sha256=source_hash,
                       source_sha256=source_hash, output_sha256=hashlib.sha256(output).hexdigest(),
                       operations=[dict(kind="install", group_id=gid, spans=spans)])
        receiver = ExternalPatchLibrary(tmp_path / (catalog.family + catalog.edition))
        receiver.import_file(updated_path)
        assert receiver.import_receipt_bytes(output, json.dumps(receipt).encode()) == catalog.digest
        checksum = next(g.id for g in catalog.groups if "Checksum" in g.title)
        extended = receiver.build(output, catalog.digest, install=[checksum])
        assert receiver.build(extended, catalog.digest, remove=[checksum]) == output
        assert receiver.build(output, catalog.digest, remove=[gid]) == source
