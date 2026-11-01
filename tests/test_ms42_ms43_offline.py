"""Offline image/definition/catalog integration; no ECU transport."""

from pathlib import Path
import os
from hashlib import sha256

import pytest

import backup_manager
import bin_compare
import ms42_ms43_image as images
from definition_registry import DefinitionRegistry, DefinitionRegistryError
from ms42_ms43_identity import PAIRING_POINTERS
from ms42_ms43_offline import decode_identity
from ms42_ms43_vin import AIF_ADDRESSES, AIF_SIZE, encode_vin
from rom_analyzer import analyze
from tests.test_backup_manager import _mgr
from tests.test_ms42_ms43_image import synthetic_image


VIN = "WBAAV31030FU12345"


def identity_image(family, isn=0x1234, slot=0):
    data = synthetic_image(family)
    start = AIF_ADDRESSES[family]
    data[start:start + AIF_SIZE] = b"\xFF" * AIF_SIZE
    data[start:start + 13] = encode_vin(VIN)
    data[start + 13] = 0
    pointer, pairing = PAIRING_POINTERS[family]
    data[pointer:pointer + 4] = pairing.to_bytes(4, "little")
    data[pairing:pairing + 0x42] = b"\xFF" * 0x42
    value = isn ^ 0xFFFF
    for index, word in enumerate((value, value ^ 0x00FF, value ^ 0xFF00)):
        at = pairing + slot * 6 + index * 2
        data[at:at + 2] = word.to_bytes(2, "little")
    if family == "MS43":
        descriptor = (0xFEFE, 0xECEC, 0xC8C8)[slot]
        data[pairing + 0x12:pairing + 0x18] = descriptor.to_bytes(2, "little") * 3
    data[0x3F80:0x3F88] = b"12345678"
    return data


@pytest.mark.parametrize("family,slot", [("MS42", 0), ("MS43", 0), ("MS43", 1), ("MS43", 2)])
def test_native_identity_selection_and_redundant_word_recovery(family, slot):
    data = identity_image(family, slot=slot)
    original = bytes(data)
    info = decode_identity(data)
    assert (info["vin"], info["isn"], info["isn_status"], info["ecu_number"]) == (VIN, "1234", "Stored", "12345678")
    assert analyze(data).identity == info
    assert bytes(data) == original
    pairing = PAIRING_POINTERS[family][1] + slot * 6
    for corrupt in range(3):
        damaged = bytearray(data)
        damaged[pairing + corrupt * 2] ^= 0x12
        recovered = decode_identity(damaged)
        assert recovered["isn"] == "1234"
        assert any("redundant" in warning for warning in recovered["warnings"])
    data[pairing:pairing + 6] = bytes(6)
    assert decode_identity(data)["isn_status"] == "Invalid ISN data"


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_identity_empty_interrupted_unmapped_and_wrong_pointer(family):
    data = identity_image(family)
    start = AIF_ADDRESSES[family]
    data[start + 46:start + 48] = b"\xFF\xFF"
    data[start + 48:start + 50] = b"\x00\x00"
    info = decode_identity(data)
    assert info["vin"] == VIN and any("unfinished" in item for item in info["warnings"])
    pointer, pairing = PAIRING_POINTERS[family]
    data[pairing:pairing + 0x42] = b"\xFF" * 0x42
    assert decode_identity(data)["isn_status"] == "Not stored"
    data[pointer] ^= 1
    assert decode_identity(data)["isn_status"] == "Unavailable"
    partial = images.extract_calibration(data)
    assert decode_identity(partial)["vin"] is None
    data[images.PROFILES[family].sw_offset + 5] = ord("1")
    assert decode_identity(data)["vin"] is None


def test_ms43_descriptor_majority_and_invalid_selection():
    data = identity_image("MS43", slot=1)
    start = PAIRING_POINTERS["MS43"][1]
    data[start + 0x12:start + 0x14] = b"\xFF\xFF"
    assert decode_identity(data)["isn"] == "1234"
    data[start + 0x14:start + 0x16] = b"\xFE\xFE"
    assert decode_identity(data)["isn_status"] == "Invalid ISN data"


def xdf_file(tmp_path, family, *, partial=False, equation="X*2", flags="0x03"):
    profile = images.PROFILES[family]
    software = "0110C6" if family == "MS42" else "430069"
    path = tmp_path / f"{family}.xdf"
    path.write_text(f'''<XDFFORMAT><XDFHEADER><deftitle>{family} {software}</deftitle>
      <BASEOFFSET offset="{0 if partial else profile.calibration_offset}" subtract="0"/>
      <REGION size="{profile.partial_size if partial else images.FULL_SIZE}"/>
      <CATEGORY index="0" name="Test"/></XDFHEADER>
      <XDFCONSTANT><title>Scaled signed value</title><CATEGORYMEM category="1"/>
      <EMBEDDEDDATA mmedaddress="0x1000" mmedelementsizebits="16" mmedtypeflags="{flags}"/>
      <units>test units</units><decimalpl>1</decimalpl><MATH equation="{equation}"><VAR id="X"/></MATH>
      </XDFCONSTANT><XDFTABLE><title>Map</title><XDFAXIS id="z">
      <EMBEDDEDDATA mmedaddress="0x1100" mmedelementsizebits="8" mmedrowcount="3" mmedcolcount="2"/>
      </XDFAXIS></XDFTABLE></XDFFORMAT>''', encoding="utf-8")
    return path


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("partial_definition", [False, True])
def test_xdf_registry_full_and_partial_offsets_and_scaled_values(tmp_path, family, partial_definition):
    data = identity_image(family)
    base = images.PROFILES[family].calibration_offset
    data[base + 0x1000:base + 0x1002] = (-123).to_bytes(2, "little", signed=True)
    path = xdf_file(tmp_path, family, partial=partial_definition)
    registry = DefinitionRegistry(tmp_path / "registered")
    imported = registry.import_file(path)
    registry.set_active(imported.path.name)
    assert registry.names() == [path.name]
    for image in (data, images.extract_calibration(data)):
        result = analyze(image, registry.active_path())
        assert result.matched_label and result.n_scalars == result.n_maps == 1
        assert result.params[0] == ("Test", "Scaled signed value", "-246.0", "test units")
        assert result.params[1][2:] == ("2×3 map", f"0x{base + 0x1100:05X}")
    other = "MS42" if family == "MS43" else "MS43"
    assert analyze(synthetic_image(other), path).params == []
    mixed = bytearray(data)
    mixed[base + 8 + 5] = ord("1")
    mismatch = analyze(mixed, path)
    assert mismatch.params == [] and mismatch.matched_label is None
    assert any("software do not match" in warning for warning in mismatch.warnings)
    data[images.PROFILES[family].sw_offset + 5] = ord("1")
    assert analyze(data, path).params == []


@pytest.mark.parametrize("equation", ["__import__('os').system('echo bad')", "X**1000000", "BitWise(1000000000,X,4)", "1/0"])
def test_xdf_unsafe_or_unsupported_math_is_not_executed(tmp_path, equation):
    path = xdf_file(tmp_path, "MS43", equation=equation)
    result = analyze(identity_image("MS43"), path)
    assert result.n_scalars == 0 and result.n_maps == 1
    assert any("unsupported" in item for item in result.warnings)


def test_xdf_rejects_entities_and_unqualified_family(tmp_path):
    path = xdf_file(tmp_path, "MS42")
    path.write_text('<!DOCTYPE x [<!ENTITY bomb "x">]>' + path.read_text(), encoding="utf-8")
    registry = DefinitionRegistry(tmp_path / "registered")
    with pytest.raises(DefinitionRegistryError, match="declarations"):
        registry.import_file(path)
    path = xdf_file(tmp_path, "MS42")
    path.write_text(path.read_text().replace("MS42 0110C6", "Unknown"), encoding="utf-8")
    with pytest.raises(DefinitionRegistryError, match="revision"):
        registry.import_file(path)


def test_xdf_encoding_size_and_ambiguous_family_boundaries(tmp_path):
    registry = DefinitionRegistry(tmp_path / "registered")
    path = xdf_file(tmp_path, "MS43")
    text = path.read_text()
    path.write_bytes(text.encode("utf-16"))
    assert registry.import_file(path).copied
    path.write_bytes(('<!DOCTYPE x [<!ENTITY bomb "x">]>' + text).encode("utf-16"))
    with pytest.raises(DefinitionRegistryError, match="declarations"):
        registry.import_file(path)
    path.write_text(text.replace("MS43 430069", "MS42 MS43 430069"), encoding="utf-8")
    with pytest.raises(DefinitionRegistryError, match="revision"):
        registry.import_file(path)
    path.write_bytes(b" " * (16 * 1024 * 1024 + 1))
    with pytest.raises(DefinitionRegistryError, match="size limit"):
        registry.import_file(path)


@pytest.mark.parametrize("rows,columns", [(-3, -2), (0, 2), (3, -1), (1, 524289)])
def test_xdf_invalid_dimensions_are_omitted(tmp_path, rows, columns):
    path = xdf_file(tmp_path, "MS43")
    text = path.read_text().replace('mmedrowcount="3"', f'mmedrowcount="{rows}"')
    path.write_text(text.replace('mmedcolcount="2"', f'mmedcolcount="{columns}"'), encoding="utf-8")
    result = analyze(identity_image("MS43"), path)
    assert result.n_scalars == 1 and result.n_maps == 0
    assert any("unsupported layout" in warning for warning in result.warnings)


_REFERENCES = {
    "MS42": ("Siemens_MS42_0110C6_E46_M52TUB28_EU3_LHD.bin",
             "010d6bf17bcfe46a9f436ba8e11dff375466ee948aaaf10d43a1d8325757b2cc",
             "Siemens_MS42_0110C6_ENG_512K_v1.3.xdf", 1306, 1002),
    "MS43": ("Siemens_MS43_MS430069_E46_M54B30_US.bin",
             "eb687fdd8eee4a0671dbe1d3294b4c25788b6cfa60c29f15d3b77293981909da",
             "Siemens_MS43_MS430069_ENG_512K_v1.15.xdf", 2276, 1469),
}


def _original(family):
    root = Path(os.environ.get("MS41_TEST_DATA_ROOT", Path.home() / "ECU-reference-data"))
    path = root / ("REF_" + family) / _REFERENCES[family][0]
    if not path.is_file():
        pytest.skip("Exact private MS42/MS43 reference is not available")
    data = path.read_bytes()
    assert sha256(data).hexdigest() == _REFERENCES[family][1]
    return root, data


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_supplied_stock_xdf_inventory_and_partial_mapping(family):
    root, full = _original(family)
    definition = root.parent / "reference_inputs" / family / "inputs" / _REFERENCES[family][2]
    if not definition.is_file():
        pytest.skip("Private stock XDF is not available")
    complete = analyze(full, definition)
    partial = analyze(images.extract_calibration(full), definition,
                      checksum_context=full if family == "MS43" else None)
    assert (complete.n_scalars, complete.n_maps) == _REFERENCES[family][3:]
    assert complete.params == partial.params
    assert not any("omitted" in item for item in complete.warnings)
    assert complete.checksum == partial.checksum == "enabled"


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_offline_isn_matches_original_firmware_restore(family, monkeypatch):
    _root, stock = _original(family)
    monkeypatch.syspath_prepend(str(Path.home() / "ECU Emulator"))
    emulator = pytest.importorskip("ms41emu")
    from ms42_ms43_diagnostics import decode_isn, ISN_READS

    cls = getattr(emulator, family)
    start = PAIRING_POINTERS[family][1]
    records = []
    for slot in range(1 if family == "MS42" else 3):
        reference = identity_image(family, slot=slot)
        for corrupt in (None, 0, 1, 2):
            data = bytearray(stock)
            data[start:start + 0x42] = reference[start:start + 0x42]
            if corrupt is not None:
                data[start + slot * 6 + corrupt * 2] ^= 0x12
            records.append(data)
    for fill in (0, 255):
        data = bytearray(stock)
        data[start:start + 0x42] = bytes([fill]) * 0x42
        records.append(data)
    if family == "MS43":
        for words in ((0xFEFE, 0xECEC, 0xECEC), (0xFFFF, 0xFEFE, 0xECEC)):
            data = bytearray(records[4])
            data[start + 0x12:start + 0x18] = b"".join(word.to_bytes(2, "little") for word in words)
            records.append(data)
    for data in records:
        expected = decode_identity(data)
        native = cls.load(stock)
        native.mem.image[:] = data
        result = native.call(0x02951A if family == "MS42" else 0x0AECA0, max_steps=500)
        assert result.exit_reason == "stop_at", result
        _software, address, count = ISN_READS[family]
        actual = decode_isn(bytes(native.mem.read_byte_direct(address + index) for index in range(count)), family)
        assert actual == {key: expected[key] for key in ("isn", "isn_status")}
        assert bytes(native.mem.image) == data


def test_bin_vin_and_saved_checksum_context_survive_reload(tmp_path, monkeypatch):
    manager = _mgr(tmp_path, monkeypatch)
    full, _ = images.correct_checksums(identity_image("MS43"), "MS43")
    full_entry = manager.add_data(full, "full.bin")
    assert full_entry.vin == VIN
    partial = images.extract_calibration(full)
    context = full[0x6FDB8:0x6FDE0]
    entry = manager.add_data(partial, "partial.bin", checksum_context=context)
    assert entry.checksum_context == context.hex()
    manager.remove(full_entry)
    manager = backup_manager.BackupManager()
    assert manager.checksum_context(partial) == context
    assert analyze(partial, checksum_context=manager.checksum_context(partial)).checksum == "enabled"
    report = bin_compare.compare_entries(manager.entries[0], manager.entries[0], context_provider=manager.checksum_context)
    assert "cal=OK" in report
    assert "cal=OK" in bin_compare.compare_entries(manager.entries[0], manager.entries[0])
    assert bin_compare.support_metadata(manager.entries[0])["checksum"]["cal"] is True
    changed = bytearray(partial)
    changed[0x1100] ^= 1
    assert manager.checksum_context(changed) is None
    duplicate = manager.add_data(partial, "duplicate.bin", checksum_context=bytes(40))
    assert duplicate.checksum_context != entry.checksum_context
    assert manager.checksum_context(partial) is None
    assert "cal=N/A" in bin_compare.compare_entries(manager.entries[0], duplicate)
    assert "cal=N/A" in bin_compare.compare_entries(manager.entries[0], manager.entries[0], context_provider=manager.checksum_context)


def test_legacy_context_requires_identical_calibration_and_verified_full(tmp_path, monkeypatch):
    manager = _mgr(tmp_path, monkeypatch)
    full, _ = images.correct_checksums(identity_image("MS43"), "MS43")
    partial = images.extract_calibration(full)
    manager.add_data(partial, "partial.bin")
    assert manager.checksum_context(partial) is None
    full_entry = manager.add_data(full, "full.bin")
    assert manager.checksum_context(partial) == full[0x6FDB8:0x6FDE0]
    damaged = bytearray(full)
    damaged[0x12345] ^= 1
    Path(full_entry.path).write_bytes(damaged)
    assert manager.checksum_context(partial) is None
