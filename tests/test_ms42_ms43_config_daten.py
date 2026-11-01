"""Bounded native configuration checks using supplied BMW program packages.

Sparse package fixtures are analysis input only, never complete flash images.
The native instructions run with supplied application register and input state.
"""

from hashlib import sha256
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

import ms42_ms43_config as config
from ms42_ms43_image import PROFILES, extract_calibration
from tests.test_ms42_ms43_image import synthetic_image


ROOT = Path(__file__).resolve().parents[1]
DATEN = ROOT / "tests/reference_data/packages"
PROGRAMS = {
    "0110CA": ("MDS42", "7526753A.0PA", "266c74a1b1b3fb1547c964c1fbd73bdb4a79385b43c11408504bb099483cb1a2"),
    "430037": ("MDS43", "7511570A.0PA", "dd64d18d2e0bb898dba95abbac82edca358e1311a48c72fd9b5b0ade640010c5"),
    "430064": ("MDS43", "7528050A.0PA", "18464c777122df8560a82b95207254254de37227f21d04b85941dfd6a2a18195"),
    "430065": ("MDS43", "4482751A.0PA", "48ef9c17be2c0becb98f1783262d58a6202a0b24a16b0eca923628b9bd10c56d"),
    "430066": ("MDS43", "7545150A.0PA", "d75e04dd6e6bf77b100f6cdf3d10b298f23c5207c9d90973b0fa7007932cf972"),
    "430066-later": ("MDS43", "7572342A.0PA", "91181c383c39b4ad98986e9b0b834f700a3f52d955d8dc87238c6b256395d2d4"),
}
CALIBRATIONS = {
    "0110CA": ("P7526756.0DA", "39f7fd6e0927f0a445a1616e879aa3f9fb31501dcb1686c12586f16f9fc90445"),
    "430037": ("O7509402.0DA", "70e5ceb3094bf793cb8dea74cb4c8e4dd9b74e9b0ad5a76f6f70ecc7eecab981"),
    "430064": ("O7532587.0DA", "557021fcb24533e37d182d6c33bfba32b54de7d2b8963b51afa3253bfe49e3ef"),
    "430065": ("O4485524.0DA", "a252206b19e53a8098964b97538afa55d18e34b5ce15b10758df400a99d44e8a"),
    "430066": ("O4834242.0DA", "579089afe66f6d0aae8ec363301ee8f449ff9117d5aa7ecb8854f8b962f68231"),
}
# Entry, lambda-configuration state byte, MIL output word / first bit.
MIL = {
    "430037": (0x9D1D0, 0x80D5, 0xFD1C, 3),
    "430055": (0x9D664, 0x80F6, 0xFD1C, 1),
    "430064": (0x9D67C, 0x80F6, 0xFD1C, 1),
    "430065": (0x9DCF8, 0x80F6, 0xFD1C, 2),
    "430066": (0x9DD58, 0x80F6, 0xFD1A, 14),
}


@pytest.fixture(scope="module")
def packages():
    parser_path = DATEN / "export_daten.py"
    if not parser_path.is_file():
        pytest.skip("private BMW DATEN references unavailable")
    spec = importlib.util.spec_from_file_location("config_daten_packages", parser_path)
    parser = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parser)
    result = {}
    for key, (folder, filename, digest) in PROGRAMS.items():
        software = key.split("-")[0]
        calibration, cal_digest = CALIBRATIONS[software]
        memory = {}
        for name, expected in ((filename, digest), (calibration, cal_digest)):
            path = DATEN / "packages" / folder / name
            if not path.is_file():
                pytest.skip(f"private configuration reference unavailable: {name}")
            assert sha256(path.read_bytes()).hexdigest() == expected
            supplied, _ = parser.parse_package(path)
            memory.update(supplied)
        family = folder.replace("MDS", "MS")
        image = bytearray(b"\xff" * 0x80000)
        for address, byte in memory.items():
            image[address & 0x7FFFF] = byte
        profile = PROFILES[family]
        assert image[profile.sw_offset:profile.sw_offset + 6] == software.encode()
        assert image[profile.calibration_offset + 8:profile.calibration_offset + 14] == software.encode()
        assert all(profile.calibration_offset + f.byte in memory
                   for f in config.features_for(family, software))
        result[key] = SimpleNamespace(image=bytes(image), memory=memory, family=family)
    full55 = (ROOT.parent / "_shared/REF_MS43/430055.bin")
    if full55.is_file():
        image = full55.read_bytes()
        assert sha256(image).hexdigest() == "820bce7aea24933673fce0b6dc4b24b4f672b4e6dbd43476dcecc5826feec1f8"
        assert image[0x6FFBA:0x6FFC0] == image[0x70008:0x7000E] == b"430055"
        result["430055"] = SimpleNamespace(image=image, family="MS43",
            memory={at + 0x80000: byte for at, byte in enumerate(image)})
    return result


@pytest.fixture
def native(monkeypatch):
    root = Path.home() / "ECU Emulator"
    if not (root / "ms41emu").is_dir():
        pytest.skip("canonical C167 emulator unavailable")
    monkeypatch.syspath_prepend(str(root))
    from ms41emu.ms42 import MS42
    from ms41emu.ms43 import MS43
    return {"MS42": MS42, "MS43": MS43}


def test_ca_call_free_variant_block_and_brake_handler_match_c6(packages):
    path = ROOT.parent / "_shared/REF_MS42/Siemens_MS42_0110C6_E46_M52TUB28_EU3_LHD.bin"
    if not path.is_file():
        pytest.skip("private MS42 reference unavailable")
    original = path.read_bytes()
    assert sha256(original).hexdigest() == "010d6bf17bcfe46a9f436ba8e11dff375466ee948aaaf10d43a1d8325757b2cc"
    for first, last in ((0x13440, 0x13496), (0x2A3E4, 0x2A41C)):
        assert all(at in packages["0110CA"].memory for at in range(first, last))
        assert packages["0110CA"].image[first:last] == original[first:last]


@pytest.mark.parametrize("offset,entry,word,bit", [
    (0x28A, 0x13466, 0xFD04, 0), (0x288, 0x13472, 0xFD04, 1),
    (0x28E, 0x1347E, 0xFD04, 2), (0x28F, 0x1348A, 0xFD06, 1),
])
@pytest.mark.parametrize("value", [0, 1])
def test_ca_equipment_settings_execute_original_branches(packages, native, offset, entry, word, bit, value):
    feature = next(f for f in config.features_for("MS42", "0110CA") if f.byte == offset)
    image = config.apply_config(packages["0110CA"].image, {feature.name: feature.options[value][0]})
    emu = native["MS42"](image, experimental=True)
    emu.write(word, 0xA55A)
    result = emu.run_from(entry, stop_at=(entry + 12,), max_steps=5)
    assert result.exit_reason == "stop_at"
    assert emu.read(word) == (0xA55A & ~(1 << bit)) | (value << bit)


@pytest.mark.parametrize("mode", [0, 1, 2, 3])
def test_ca_mil_modes_execute_original_branches(packages, native, mode):
    feature = next(f for f in config.features_for("MS42", "0110CA") if f.byte == 0x28C)
    image = config.apply_config(packages["0110CA"].image, {feature.name: feature.options[mode][0]})
    emu = native["MS42"](image, experimental=True)
    emu.write(0xFD04, 0x1234)
    emu.write(0xFD06, 0xA55A)
    result = emu.run_from(0x13440, stop_at=(0x13466,), max_steps=15)
    assert result.exit_reason == "stop_at"
    assert emu.read(0xFD04) == 0x1234 | ((mode in (2, 3)) << 15)
    assert emu.read(0xFD06) == 0xA55A | (mode in (0, 3))


@pytest.mark.parametrize("key", [key for key in PROGRAMS if key.startswith("43")] + ["430055"])
def test_earlier_mil_function_changes_only_reviewed_addresses(packages, key):
    if key not in packages:
        pytest.skip("private MS43 430055 image unavailable")
    software = key.split("-")[0]
    entry, state, word, bit = MIL[software]
    normal, no_monitor = (f.byte for f in config.features_for("MS43", software)
                          if f.name.startswith("MIL without") or f.name == "Malfunction indicator lamp")
    # Complete, call-free routine: selection, four-way dispatch and output bits.
    expected = bytearray.fromhex(
        "f3f8f68049843d03f3f8220b0d02f3f8210b49802d0549822d0649832d070d09"
        "ee0dff0ddb00ef0dfe0ddb00ef0dff0ddb00ee0dfe0ddb00")
    expected[2:4] = state.to_bytes(2, "little")
    expected[10:12] = no_monitor.to_bytes(2, "little")
    expected[16:18] = normal.to_bytes(2, "little")
    for at, selected_bit, enabled in ((32, bit, False), (34, bit + 1, True),
                                     (38, bit, True), (40, bit + 1, False),
                                     (44, bit, True), (46, bit + 1, True),
                                     (50, bit, False), (52, bit + 1, False)):
        expected[at:at + 2] = bytes(((selected_bit << 4) | (15 if enabled else 14),
                                    (word - 0xFD00) // 2))
    memory = packages[key].memory
    assert all(at in memory for at in range(entry, entry + len(expected)))
    assert bytes(memory[at] for at in range(entry, entry + len(expected))) == expected
    callers = {"430037": (0x9D040, 0xA4B64), "430055": (0x9D4D4, 0xA3988),
               "430064": (0x9D4EC, 0xA3AB8),
               "430065": (0x9DB68, 0xA474A), "430066": (0x9DBC8, 0xA474A)}
    for caller in callers[software]:
        assert bytes(memory[at] for at in range(caller, caller + 4)) == bytes(
            (0xDA, entry >> 16, entry & 0xFF, (entry >> 8) & 0xFF))


@pytest.mark.parametrize("key", [key for key in PROGRAMS if key.startswith("43")] + ["430055"])
@pytest.mark.parametrize("lambda_state", [3, 4])
@pytest.mark.parametrize("mode", [0, 1, 2, 3])
def test_earlier_mil_modes_execute_original_bytes(packages, native, key, lambda_state, mode):
    if key not in packages:
        pytest.skip("private MS43 430055 image unavailable")
    software = key.split("-")[0]
    entry, state, word, bit = MIL[software]
    features = config.features_for("MS43", software)
    primary = next(f for f in features if f.name == "Malfunction indicator lamp")
    secondary = next(f for f in features if f.name.startswith("MIL without"))
    selected = secondary if lambda_state == 4 else primary
    other = primary if lambda_state == 4 else secondary
    image = config.apply_config(packages[key].image, {
        selected.name: selected.options[mode][0], other.name: other.options[(mode + 1) % 4][0],
    })
    emu = native["MS43"](image, experimental=True)
    emu.write_byte(state, lambda_state)
    mask = 3 << bit
    emu.write(word, 0xA55A)
    result = emu.run_from(entry, stop_at=tuple(entry + at for at in (36, 42, 48, 54)), max_steps=20)
    assert result.exit_reason == "stop_at"
    assert emu.read(word) == (0xA55A & ~mask) | ((2, 0, 1, 3)[mode] << bit)


@pytest.mark.parametrize("key,entry", [("430064", 0x9514A), ("430065", 0x951AC),
                                        ("430066", 0x951AC), ("430066-later", 0x951AC)])
@pytest.mark.parametrize("enabled", [False, True])
def test_earlier_obd_distance_reporting_executes_original_bytes(packages, native, key, entry, enabled):
    software = key.split("-")[0]
    feature = next(f for f in config.features_for("MS43", software) if f.name == "OBD distance with MIL reporting")
    expected = bytearray.fromhex(
        "f3f81f0b49813d05e7fa8000f7fa05e60d02f78e05e6f78e06e6f78e07e6"
        "e118f7f808e6e1a8f7f80ce6db00")
    expected[2:4] = feature.byte.to_bytes(2, "little")
    assert bytes(packages[key].memory[at] for at in range(entry, entry + len(expected))) == expected
    image = config.apply_config(packages[key].image, {feature.name: "Enabled" if enabled else "Disabled"})
    emu = native["MS43"](image, experimental=True)
    result = emu.run_from(entry, stop_at=(entry + 42,), max_steps=20)
    assert result.exit_reason == "stop_at"
    assert emu.mem.read_byte(0xE605) == (0x80 if enabled else 0)
    assert emu.mem.read_byte(0xE608) == 1


@pytest.mark.parametrize("key", ["430066", "430066-later"])
def test_066_brake_function_changes_only_calibration_and_clutch_bit(packages, key):
    path = ROOT.parent / "_shared/REF_MS43/Siemens_MS43_MS430069_E46_M54B30_US.bin"
    if not path.is_file():
        pytest.skip("private MS43 reference unavailable")
    original = path.read_bytes()
    assert sha256(original).hexdigest() == "eb687fdd8eee4a0671dbe1d3294b4c25788b6cfa60c29f15d3b77293981909da"
    expected = bytearray(original[0x3051E:0x30560])
    expected[2] = 0x1F
    expected[0x23] = 0x46
    assert bytes(packages[key].memory[at] for at in range(0xB051E, 0xB0560)) == expected


@pytest.mark.parametrize("key,entry,flag", [("0110CA", 0x2A3E4, 0xFD36),
                                          ("430066", 0xB051E, 0xFDAE),
                                          ("430066-later", 0xB051E, 0xFDAE)])
@pytest.mark.parametrize("polarity", [0, 1])
def test_added_brake_configuration_is_loaded_by_original_firmware(packages, native, key, entry, flag, polarity):
    software = key.split("-")[0]
    source = packages[key]
    feature = next(f for f in config.features_for(source.family, software) if f.name == "Brake test switch polarity")
    image = config.apply_config(source.image, {feature.name: feature.options[polarity][0]})
    emu = native[source.family](image, experimental=True)
    result = emu.run_from(entry, stop_at=(entry + 8,), max_steps=3)
    assert result.exit_reason == "stop_at" and emu.read(flag) == polarity


@pytest.mark.parametrize("key", ["430055", "430066", "430066-later"])
@pytest.mark.parametrize("offset,entry,bit", [
    (0xB25, 0x9DBCC, 2), (0xB26, 0x9DBD8, 1),
    (0xB2F, 0x9DBE4, 4), (0xB20, 0x9DBF0, 5),
])
@pytest.mark.parametrize("value", [0, 1])
def test_earlier_variant_blocks_keep_native_behavior(packages, native, key, offset, entry, bit, value):
    if key not in packages:
        pytest.skip("private MS43 430055 image unavailable")
    if key == "430055":
        offset, entry, bit = {0xB25: (0xABD, 0x9D4D8, 5), 0xB26: (0xABE, 0x9D4E4, 4),
                             0xB2F: (0xAC7, 0x9D4F0, 7), 0xB20: (0xAB8, 0x9D4FC, 8)}[offset]
    source = packages[key]
    expected = bytes.fromhex("f3f800003d02000e0d01000e")
    expected = bytearray(expected)
    expected[2:4] = offset.to_bytes(2, "little")
    expected[6] = (bit << 4) | 14
    expected[10] = (bit << 4) | 15
    assert bytes(source.memory[at] for at in range(entry, entry + 12)) == expected
    feature = next(f for f in config.features_for("MS43", key.split("-")[0]) if f.byte == offset)
    image = config.apply_config(source.image, {feature.name: feature.options[value][0]})
    emu = native["MS43"](image, experimental=True)
    emu.write(0xFD1C, 0xA55A)
    result = emu.run_from(entry, stop_at=(entry + 12,), max_steps=5)
    assert result.exit_reason == "stop_at"
    assert emu.read(0xFD1C) == (0xA55A & ~(1 << bit)) | (value << bit)


@pytest.mark.parametrize("family,software,count,spans", [
    ("MS42", "0110CA", 25, [(0x4816D, 1), (0x481EF, 96), (0x48287, 28), (0x48319, 4), (0x483AA, 37)]),
    ("MS43", "430037", 35, [(0x709AE, 1), (0x70A37, 20), (0x70ACE, 1), (0x70B61, 119), (0x70BF8, 3), (0x70CD0, 28)]),
    ("MS43", "430064", 44, [(0x70A3B, 1), (0x70ACA, 21), (0x70B6C, 1), (0x70BFF, 8), (0x70C84, 36), (0x70D8B, 31)]),
    ("MS43", "430065", 46, [(0x70A8A, 1), (0x70B1C, 21), (0x70BC0, 1), (0x70C53, 8), (0x70CDD, 37), (0x70DF2, 33)]),
    ("MS43", "430066", 47, [(0x70A8C, 1), (0x70B1E, 21), (0x70BC2, 1), (0x70C55, 8), (0x70CDE, 37), (0x70DF3, 52)]),
    ("MS43", "430055", 42, [(0x70A27, 1), (0x70AB6, 21), (0x70B54, 1), (0x70BE2, 8), (0x70C66, 36), (0x70D68, 29)]),
])
@pytest.mark.parametrize("partial", [False, True])
def test_revision_editing_roundtrip_preservation_and_small_reads(family, software, count, spans, partial):
    import ms42_ms43_config_patches as editor

    profile = PROFILES[family]
    image = synthetic_image(family)
    image[profile.sw_offset:profile.sw_offset + 6] = software.encode()
    image[profile.calibration_offset + 8:profile.calibration_offset + 14] = software.encode()
    image = extract_calibration(image) if partial else bytes(image)
    base = 0 if partial else profile.calibration_offset
    features = config.features_for(family, software)
    assert len(features) == count
    assert all(feature in editor.features_for(family, software) for feature in features if not feature.full_file_only)
    for feature in features:
        if feature.full_file_only:
            continue  # Coordinated CRC uses real owner-qualified images in its native suite.
        for label, value in feature.options:
            expected = bytearray(image)
            expected[base + feature.byte] = value
            changed = editor.apply_config(image, {feature.name: label})
            assert changed == expected
            assert editor.read_config(changed)[feature.name] == label
    calls = []
    ecu = SimpleNamespace(family=family, read_memory=lambda at, n: calls.append((at, n)) or bytes(n))
    assert len(config.read_live_config(ecu, software)) == count
    assert calls == spans
