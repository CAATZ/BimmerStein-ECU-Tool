"""Community options are atomic program/calibration changes, never tune patches."""

from pathlib import Path
from types import SimpleNamespace

import pytest

import ms42_ms43_config as stock
import ms42_ms43_config_patches as config
from ms42_ms43_image import checksum_status, correct_checksums, extract_calibration
from tests.test_ms42_ms43_image import synthetic_image


def patch_image(family):
    data = synthetic_image(family)
    software = "0110C6" if family == "MS42" else "430069"
    for patch in config.patches_for(family, software):
        for offset, original, _ in patch.entries:
            data[offset:offset + len(original)] = original
    return bytes(data)


@pytest.mark.parametrize("family", ("MS42", "MS43"))
@pytest.mark.parametrize("name", ("Immobilizer bypass", "Calibration checksum bypass"))
def test_enable_disable_preserves_every_unselected_byte_and_corrects_checksums(family, name):
    original = patch_image(family)
    patched = config.apply_config(original, {name: "Enabled"})
    definition = next(p for p in config.patches_for(family, "0110C6" if family == "MS42" else "430069") if p.name == name)
    expected = bytearray(original)
    for offset, _, value in definition.entries:
        expected[offset:offset + len(value)] = value
    assert patched == expected
    assert config.read_config(patched)[name] == "Enabled"
    assert config.apply_config(patched, {name: "Enabled"}) == patched
    assert config.apply_config(patched, {name: "Disabled"}) == original
    corrected, _ = correct_checksums(patched, family)
    status = checksum_status(corrected, family)
    assert all(status[key] for key in ("boot", "program", "cal"))
    assert config.read_config(corrected)[name] == "Enabled"
    assert patched[:0x10000] == original[:0x10000]


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_mixed_known_install_can_be_completed_but_unknown_code_is_not_overwritten(family):
    original = patch_image(family)
    patch = config.patches_for(family, "0110C6" if family == "MS42" else "430069")[0]
    offset, base, replacement = patch.entries[0]
    mixed = bytearray(original)
    mixed[offset:offset + len(base)] = replacement
    assert config.read_config(mixed)[patch.name].startswith("Mixed")
    assert config.apply_config(mixed, {patch.name: "Disabled"}) == original
    assert config.apply_config(mixed, {patch.name: "Enabled"}) == config.apply_config(original, {patch.name: "Enabled"})
    mixed[offset] ^= 0x55
    before = bytes(mixed)
    with pytest.raises(ValueError, match="unrecognized bytes"):
        config.apply_config(mixed, {patch.name: "Enabled"})
    assert bytes(mixed) == before


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_partial_and_unknown_revisions_cannot_receive_program_patches(family):
    full = patch_image(family)
    tune = extract_calibration(full)
    assert config.read_config(tune)["Immobilizer bypass"] == stock.FULL_IMAGE_REQUIRED
    with pytest.raises(ValueError, match="full ROM"):
        config.apply_config(tune, {"Immobilizer bypass": "Enabled"})
    assert not config.patches_for(family, "unknown")
    with pytest.raises(ValueError, match="Unknown configuration"):
        config.apply_config(full, {"Invented bypass": "Enabled"})


@pytest.mark.parametrize("family,extra_bytes", (("MS42", 18), ("MS43", 22)))
def test_live_state_reads_exact_patch_bytes_without_full_read_or_identity_query(family, extra_bytes):
    data = patch_image(family)
    software = "0110C6" if family == "MS42" else "430069"
    calls = []

    def read(address, size):
        calls.append((address, size))
        offset = address - 0x80000 if family == "MS43" and address >= 0x90000 else address
        return data[offset:offset + size]

    ecu = SimpleNamespace(family=family, read_memory=read)
    before = stock.read_live_config(ecu, software)
    stock_count = sum(size for _, size in calls)
    calls.clear()
    values = config.read_live_config(ecu, software)
    assert values == {**before, "Immobilizer bypass": "Disabled", "Calibration checksum bypass": "Disabled"}
    assert sum(size for _, size in calls) == stock_count + extra_bytes
    ecu.read_memory = lambda at, count: b""
    with pytest.raises(ValueError, match="Short"):
        config.read_live_config(ecu, software)


@pytest.mark.parametrize("family,stock_name,conflicting", (
    ("MS42", "Calibration CRC", "Enabled"),
    ("MS43", "Calibration monitor checksum", "Enabled"),
))
def test_stock_and_community_checksum_changes_cannot_conflict(family, stock_name, conflicting):
    source = config.apply_config(patch_image(family), {"Calibration checksum bypass": "Enabled"})
    # The original displayed stock value is not itself a requested edit.
    selections = config.read_config(source)
    selections["Calibration checksum bypass"] = "Disabled"
    assert config.read_config(config.apply_config(source, selections))["Calibration checksum bypass"] == "Disabled"
    source = bytearray(patch_image(family))
    patch = config.patches_for(family, "0110C6" if family == "MS42" else "430069")[1]
    offset, _, value = patch.entries[0]
    source[offset:offset + len(value)] = value
    with pytest.raises(ValueError, match="conflicts"):
        config.apply_config(source, {stock_name: conflicting, "Calibration checksum bypass": "Enabled"})


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_original_reference_matches_patch_admission(family):
    names = {"MS42": "Siemens_MS42_0110C6_E46_M52TUB28_EU3_LHD.bin",
             "MS43": "Siemens_MS43_MS430069_E46_M54B30_US.bin"}
    path = Path(__file__).resolve().parents[2] / "_shared" / f"REF_{family}" / names[family]
    if not path.is_file():
        pytest.skip("Private stock reference unavailable")
    original = path.read_bytes()
    selections = {"Immobilizer bypass": "Enabled", "Calibration checksum bypass": "Enabled"}
    patched, _ = correct_checksums(config.apply_config(original, selections), family)
    assert all(config.read_config(patched)[name] == value for name, value in selections.items())
