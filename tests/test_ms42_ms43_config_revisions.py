"""Additional maps require matching native consumers, not a similar version label."""

import importlib.util
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

import ms42_ms43_config as config
from ms42_ms43_image import PROFILES, extract_calibration
from tests.test_ms42_ms43_image import synthetic_image


ROOT = Path(__file__).resolve().parents[1]
DATEN = ROOT / "tests/reference_data/packages"
C7_OWNERS = (
    (0x01116E, 0x0119AA), (0x0133FA, 0x0134AC), (0x013716, 0x014122),
    (0x0149BA, 0x0149E4), (0x015084, 0x015756), (0x01D5B0, 0x01D6DE),
    (0x02A3E4, 0x02A41C), (0x03B79E, 0x03B878), (0x03B878, 0x03B8FE),
    (0x03B8FE, 0x03B980), (0x03D114, 0x03D340), (0x03DCC8, 0x03DD48),
    (0x03DD9A, 0x03DE7C), (0x072664, 0x072D58), (0x0731F6, 0x073222),
    (0x074AC0, 0x074BF8),
    (0x030200, 0x0314B6), (0x03357C, 0x0338D2), (0x075722, 0x07573C),
)
X001_OWNERS = (
    (0x092A84, 0x092BC4), (0x0951AC, 0x0951D8), (0x09D342, 0x09D872),
    (0x09DD58, 0x09DD90), (0x0A4620, 0x0A46D4), (0x0A51D0, 0x0A586A),
    (0x0A97F6, 0x0A9A00), (0x0B051E, 0x0B0560), (0x0BC5BA, 0x0BD328),
    (0x0BF716, 0x0BF8E6), (0x0C403C, 0x0C41B4), (0x0D61CC, 0x0D622C),
    (0x0D622C, 0x0D6262), (0x0DAF3C, 0x0DB310), (0x0DFB80, 0x0DFD7C),
    (0x0D9630, 0x0DA982), (0x0DC182, 0x0DC4E4),
    (0x0928AC, 0x0928CE),
)


@pytest.fixture(scope="module")
def private_sources():
    paths = {
        "c6": ROOT.parent / "_shared/REF_MS42/Siemens_MS42_0110C6_E46_M52TUB28_EU3_LHD.bin",
        "stock": ROOT.parent / "_shared/REF_MS43/Siemens_MS43_MS430069_E46_M54B30_US.bin",
        "x001": Path.home() / "ECU-reference-data/Siemens_MS43_MS43X001_E46_M54B30_US.bin",
        "xdf": Path.home() / "ECU-reference-data/Siemens_MS43_MS43X001_512K.xdf",
        "c7": DATEN / "packages/MDS42/7505859A.0PA",
        "c7cal": DATEN / "packages/MDS42/P7513731.0DA",
    }
    if not all(path.is_file() for path in paths.values()) or not (DATEN / "export_daten.py").is_file():
        pytest.skip("private exact firmware/definition/DATEN references unavailable")
    hashes = {
        "c6": "010d6bf17bcfe46a9f436ba8e11dff375466ee948aaaf10d43a1d8325757b2cc",
        "stock": "eb687fdd8eee4a0671dbe1d3294b4c25788b6cfa60c29f15d3b77293981909da",
        "x001": "997975e61fb237094216052c300964dffd97e2ab57a94ddea5d710216389e21d",
        "xdf": "d4311e9dff6258887370880db8ffebc517fb0f24614d32cd940ab35ed12e302e",
        "c7": "d6a727e169360426d78b253e5d86f4bbc629667ee0f470f9e530d9c72a0d6b0e",
        "c7cal": "e7caca6915e7f12d24567581521681cedd6c7712a77ae562bffc7bfaf2dbe948",
    }
    sources = {name: path.read_bytes() for name, path in paths.items()}
    assert {name: sha256(data).hexdigest() for name, data in sources.items()} == hashes
    spec = importlib.util.spec_from_file_location("config_daten_evidence", DATEN / "export_daten.py")
    parser = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parser)
    sources["c7_memory"], _ = parser.parse_package(paths["c7"])
    sources["c7_cal_memory"], _ = parser.parse_package(paths["c7cal"])
    return sources


def test_c7_all_reviewed_consumers_are_supplied_and_identical(private_sources):
    memory = private_sources["c7_memory"]
    for start, end in C7_OWNERS:
        assert all(address in memory for address in range(start, end))
        assert bytes(memory[address] for address in range(start, end)) == private_sources["c6"][start:end]
    assert bytes(memory[address] for address in range(0x50036, 0x5003C)) == b"0110C7"
    calibration = private_sources["c7_cal_memory"]
    assert bytes(calibration[address] for address in range(0x48008, 0x4800E)) == b"0110C7"
    for feature in config.features_for("MS42", "0110C7"):
        assert 0x48000 + feature.byte in calibration
    assert [feature.name for feature in config.features_for("MS42", "0110C7") if feature.full_file_only] == ["Calibration CRC"]


def test_x001_selected_complete_consumers_and_definitions_match(private_sources):
    for start, end in X001_OWNERS:
        start -= 0x80000
        end -= 0x80000
        assert private_sources["x001"][start:end] == private_sources["stock"][start:end]
    definitions = {
        int(item.find("EMBEDDEDDATA").get("mmedaddress"), 16): item
        for item in ET.fromstring(private_sources["xdf"]).findall("XDFCONSTANT")
    }
    assert private_sources["x001"][0x6FFBA:0x6FFC0] == b"43X001"
    assert private_sources["x001"][0x70008:0x7000E] == b"43X001"
    for feature in config.features_for("MS43", "43X001"):
        definition = definitions[feature.byte]
        assert definition.find("EMBEDDEDDATA").get("mmedelementsizebits") == "8"
    # Its relocated initializer is native-qualified separately; changed modes stay custom.
    assert private_sources["x001"][0x1DB3E:0x1DD58] != private_sources["stock"][0x1DB3E:0x1DD58]
    feature = next(f for f in config.features_for("MS43", "43X001") if f.byte == 0xB19)
    assert [value for _, value in feature.options] == [0, 1, 2, 3]


@pytest.mark.parametrize("family,software,count", [("MS42", "0110C7", 25), ("MS43", "43X001", 45)])
@pytest.mark.parametrize("partial", [False, True])
def test_added_revision_options_roundtrip_and_preserve_other_bytes(family, software, count, partial):
    data = synthetic_image(family)
    profile = PROFILES[family]
    data[profile.sw_offset:profile.sw_offset + 6] = software.encode()
    data[profile.calibration_offset + 8:profile.calibration_offset + 14] = software.encode()
    data = extract_calibration(data) if partial else bytes(data)
    base = 0 if partial else profile.calibration_offset
    features = config.features_for(family, software)
    assert len(features) == count
    for feature in features:
        if feature.full_file_only:
            continue  # The CRC owner and coordinated edit have their own complete-image checks.
        for label, value in feature.options:
            edited = config.apply_config(data, {feature.name: label}, family, software)
            expected = bytearray(data)
            expected[base + feature.byte] = value
            assert edited == expected
            assert config.read_config(edited)[feature.name] == label


@pytest.mark.parametrize("family,software,spans", [
    ("MS42", "0110C7", [(0x4816D, 1), (0x481EF, 96), (0x48287, 28), (0x48319, 4), (0x483AA, 37)]),
    ("MS43", "43X001", [(0x70A83, 1), (0x70B16, 21), (0x70BBB, 1), (0x70C4E, 8), (0x70CD7, 38), (0x70DEF, 34)]),
])
def test_added_revision_live_reads_stay_compact(family, software, spans):
    calls = []
    ecu = SimpleNamespace(family=family, read_memory=lambda at, count: calls.append((at, count)) or bytes(count))
    assert len(config.read_live_config(ecu, software)) == len(config.features_for(family, software))
    assert calls == spans


@pytest.mark.parametrize("family,software", [("MS42", "0110CB"), ("MS43", "430067"), ("MS43", "43X002")])
def test_other_revisions_are_not_assumed_equivalent(family, software):
    assert not config.features_for(family, software)


@pytest.mark.parametrize("value", [0, 1])
def test_c7_original_flap_branch_runs_with_packaged_code(private_sources, monkeypatch, value):
    emulator_root = Path.home() / "ECU Emulator"
    if not (emulator_root / "ms41emu").is_dir():
        pytest.skip("canonical C167 emulator unavailable")
    monkeypatch.syspath_prepend(str(emulator_root))
    from ms41emu.ms42 import MS42

    # Sparse package bytes are a bounded execution fixture, never a flash image.
    image = bytearray(b"\xff" * 0x80000)
    for address, byte in private_sources["c7_memory"].items():
        image[address] = byte
    for address, byte in private_sources["c7_cal_memory"].items():
        image[address] = byte
    emu = MS42(image, experimental=True)
    emu.mem.image[:] = config.apply_config(image, {
        "Exhaust flap actuation: below minimum speed": "On" if value else "Off",
    })
    emu.mem.image[0x483FC] = 10
    emu.write_byte(0xC442, 0)
    emu.write(0xFD78, 0)
    emu.write(0xFDA8, 0x4000 if not value else 0)
    emu.write(0xFDA6, 0x4000 if not value else 0)
    result = emu.run_from(0x1D68A, stop_at=(0x1D6A2, 0x1D6AC, 0x1D6DC), max_steps=20)
    assert result.exit_reason == "stop_at"
    assert bool(emu.read(0xFDA8) & 0x4000) == bool(value)
    assert bool(emu.read(0xFDA6) & 0x4000) == bool(value)


@pytest.mark.parametrize("value,expected", [(0, 0x92B68), (1, 0x92B4A), (2, 0x92B18)])
def test_x001_original_pedal_brake_branch(private_sources, monkeypatch, value, expected):
    emulator_root = Path.home() / "ECU Emulator"
    if not (emulator_root / "ms41emu").is_dir():
        pytest.skip("canonical C167 emulator unavailable")
    monkeypatch.syspath_prepend(str(emulator_root))
    from ms41emu.ms43 import MS43

    emu = MS43(private_sources["x001"], experimental=True)
    feature = next(f for f in config.features_for("MS43", "43X001") if f.byte == 0xE05)
    emu.mem.image[:] = config.apply_config(private_sources["x001"], {
        feature.name: dict((value, label) for label, value in feature.options)[value],
    })
    emu.write_byte(0xFB46, 255)
    result = emu.run_from(0x92B00, stop_at=(0x92B68, 0x92B4A, 0x92B18), max_steps=20)
    assert result.exit_reason == "stop_at" and emu.cpu.pc == expected


@pytest.mark.parametrize("original,entry,offset,bit", [
    (0x9DBCC, 0x9DB46, 0xB1D, 2), (0x9DBD8, 0x9DB52, 0xB1E, 1),
    (0x9DBE4, 0x9DB5E, 0xB27, 4), (0x9DBF0, 0x9DB6A, 0xB18, 5),
])
@pytest.mark.parametrize("value", [0, 1])
def test_x001_relocated_variant_blocks_keep_native_behavior(
        private_sources, monkeypatch, original, entry, offset, bit, value):
    emulator_root = Path.home() / "ECU Emulator"
    if not (emulator_root / "ms41emu").is_dir():
        pytest.skip("canonical C167 emulator unavailable")
    monkeypatch.syspath_prepend(str(emulator_root))
    from ms41emu.ms43 import MS43

    assert private_sources["x001"][entry - 0x80000:entry - 0x80000 + 12] == private_sources["stock"][original - 0x80000:original - 0x80000 + 12]
    feature = next(f for f in config.features_for("MS43", "43X001") if f.byte == offset)
    image = config.apply_config(private_sources["x001"], {feature.name: feature.options[value][0]})
    emu = MS43(image, experimental=True)
    emu.write(0xFD1C, 0xA55A)
    result = emu.run_from(entry, stop_at=(entry + 12,), max_steps=5)
    assert result.exit_reason == "stop_at"
    assert emu.read(0xFD1C) == (0xA55A & ~(1 << bit)) | (value << bit)
