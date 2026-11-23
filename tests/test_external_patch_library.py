import json
from xml.etree import ElementTree as ET

import pytest

from bin_compare import analyze_image
from external_patch_library import ExternalPatchLibrary
from ms42_ms43_image import correct_checksums
from tests.test_ms42_ms43_image import synthetic_image
from tests.test_ms42_ms43_xdf_patches import fixture


def test_equivalent_edits_from_two_views_share_the_recorded_undo(tmp_path):
    lib = ExternalPatchLibrary(tmp_path / "library")
    source = correct_checksums(synthetic_image("MS42"), "MS42")[0]
    xdf = fixture(tmp_path, [("Example", [(0x48170, b"a", b"b")])])
    doc = ET.parse(xdf)
    for title, equation in [("Display A", "X"), ("Display B", "X*2")]:
        field = ET.SubElement(doc.getroot(), "XDFCONSTANT")
        ET.SubElement(field, "title").text = title
        ET.SubElement(field, "EMBEDDEDDATA", mmedaddress="0x48130", mmedelementsizebits="8")
        ET.SubElement(ET.SubElement(field, "MATH", equation=equation), "VAR", id="X")
    doc.write(xdf)
    cid = lib.import_file(xdf)
    first = lib.build(source, cid, changes=[dict(field_id="field-0", value=32)])
    second = lib.build(source, cid, changes=[dict(field_id="field-1", value=64)])
    assert first == second and first[0x48130] == 32
    assert lib.previous(second) == source
    assert lib.receipt(second)["operations"][0]["change"]["field_id"] == "field-0"


def test_catalog_updates_keep_receipt_and_exact_parent(tmp_path, monkeypatch):
    monkeypatch.setenv("BIMMERSTEIN_DATA_DIR", str(tmp_path))
    lib = ExternalPatchLibrary()
    source = bytearray(correct_checksums(synthetic_image("MS42"), "MS42")[0])
    source[0x48130] ^= 1  # Inside the synthetic calibration checksum range.
    at = 0x48170
    xdf = fixture(tmp_path, [("Cluster lighting", [(at, bytes(source[at:at+1]), b"B")])])
    uid = lib.import_file(xdf)
    assert lib.import_file(xdf) == uid
    output = lib.build(source, uid, install=["0x1"])
    assert lib.previous(output) == source
    alternate = lib.import_bytes(xdf.name, xdf.read_bytes() + b"\n")
    assert alternate != uid and lib.preferred(output) == uid
    assert len({c["label"] for c in lib.catalogs(output)}) == 2
    info = analyze_image(output)
    assert [p["title"] for p in info["installed_patches"]] == ["Cluster lighting"]
    assert info["xdf_matches"] == []

    changed = bytearray(output)
    changed[at] ^= 1
    with pytest.raises(ValueError, match="Owned bytes"):
        lib.import_receipt_bytes(changed, json.dumps(lib.receipt(output)).encode(), allow_edited=True)
    assert lib.receipt(changed) is None
    corrected = correct_checksums(source, "MS42")[0]
    assert lib.build(output, uid, remove=["0x1"]) == corrected
    lib.select(corrected, alternate)
    assert lib.preferred(corrected) == alternate
    assert analyze_image(corrected)["installed_patches"] == []

    # Both inputs yield the same output. The receipt can still undo the patch,
    # but the saved file alone cannot distinguish the two possible parent BINs.
    assert corrected != source and lib.build(corrected, uid, install=["0x1"]) == output
    assert lib.previous(output) is None
    assert lib.build(output, uid, remove=["0x1"]) == corrected
