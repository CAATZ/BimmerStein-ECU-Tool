"""Native octane-learning admission, request and factor owners, with supplied inputs.

These execute exact program bytes in the canonical emulator. They do not run an
engine scheduler or replace knock detection with an adaptation-temperature limit.
"""

import pytest

import ms42_ms43_config as config
from ms42_ms43_image import extract_calibration
from tests.test_ms42_ms43_config_daten import native as native, packages as packages
from tests.test_ms42_ms43_config_revisions import private_sources as private_sources
from tests.test_ms42_ms43_config_vanos_knock_native import byte, write


FEATURE = "Octane adaptation learning"
# Calibration offset, temperature owner, qualifier owner, factor-update owner.
OWNERS = {
    "0110C6": (0x3AA, 0x2F368, 0x2FA40, 0x2F892),
    "0110C7": (0x3AA, 0x2F368, 0x2FA40, 0x2F892),
    "0110CA": (0x3AA, 0x2F368, 0x2FA40, 0x2F892),
    "430037": (0xBD7, 0xB9418, 0xB9B56, 0xB997A),
    "430055": (0xC66, 0xB8726, 0xB8F1E, 0xB8D42),
    "430064": (0xC84, 0xB8FD0, 0xB97C8, 0xB95EC),
    "430065": (0xCDD, 0xB836E, 0xB8B66, 0xB898A),
    "430066": (0xCDE, 0xB632A, 0xB6B22, 0xB6946),
    "430066-later": (0xCDE, 0xB632A, 0xB6B22, 0xB6946),
    "430069": (0xCD7, 0xB632A, 0xB6B22, 0xB6946),
    "43X001": (0xCD7, 0xB632A, 0xB6B22, 0xB6946),
}


@pytest.fixture(scope="module")
def images(packages, private_sources):
    result = {key: source.image for key, source in packages.items()}
    result.update({"0110C6": private_sources["c6"], "430069": private_sources["stock"],
                   "43X001": private_sources["x001"]})
    c7 = bytearray(b"\xff" * 0x80000)
    for name in ("c7_memory", "c7_cal_memory"):
        for address, value in private_sources[name].items():
            c7[address] = value
    result["0110C7"] = bytes(c7)
    return result


def source_for(images, key):
    if key not in images:
        pytest.skip(f"private exact {key} image unavailable")
    family = "MS42" if key.startswith("01") else "MS43"
    software = key.split("-")[0]
    feature = next(item for item in config.features_for(family, software) if item.name == FEATURE)
    assert feature.byte == OWNERS[key][0]
    return images[key], family, feature


def physical(emu, logical):
    # Resolve data operands through the actual target's application DPP context.
    return ((emu.reg.dpp[logical >> 14] & 0x3FF) << 14) | (logical & 0x3FFF)


@pytest.mark.parametrize("key", OWNERS)
@pytest.mark.parametrize("partial", (False, True))
def test_octane_setting_preserves_custom_values_and_roundtrips(images, key, partial):
    image, family, feature = source_for(images, key)
    data = extract_calibration(image) if partial else image
    base = 0 if partial else (0x48000 if family == "MS42" else 0x70000)
    reference, reference_raw = feature.options[0]
    assert data[base + feature.byte] == reference_raw
    disabled = config.apply_config(data, {FEATURE: "Disabled"})
    assert disabled[base + feature.byte] == 255
    assert {at for at, (a, b) in enumerate(zip(data, disabled)) if a != b} == {base + feature.byte}
    assert config.apply_config(disabled, {FEATURE: reference}) == data
    custom = bytearray(data)
    custom[base + feature.byte] = 180
    assert config.apply_config(bytes(custom), {}) == bytes(custom)


@pytest.mark.parametrize("key", OWNERS)
@pytest.mark.parametrize("disabled", (False, True))
def test_octane_admission_all_coolant_bytes_including_equal_boundary(images, native, key, disabled):
    image, family, feature = source_for(images, key)
    if disabled:
        image = config.apply_config(image, {FEATURE: "Disabled"})
    emu = native[family](image, experimental=True)
    entry = OWNERS[key][1]
    code = image[(entry & 0x7FFFF):(entry & 0x7FFFF) + 16]
    assert code[:2] == b"\xf3\xf8" and code[4:6] == b"\x43\xf8"
    assert int.from_bytes(code[6:8], "little") == feature.byte
    assert code[8:11] == b"\xed\x02\xbe" and code[12:15] == b"\x0d\x01\xbf"
    coolant = physical(emu, int.from_bytes(code[2:4], "little"))
    flag = 0xFD00 + 2 * code[11]
    threshold = 255 if disabled else feature.options[0][1]
    for current in range(256):
        for previous in (0, 0xFFFF):
            write(emu, coolant, current)
            write(emu, flag, previous, 2)
            result = emu.run_from(entry, stop_at=(entry + 16,), max_steps=8)
            assert result.exit_reason == "stop_at", result
            expected = (previous & ~0x800) | ((current > threshold) << 11)
            assert emu.read(flag) == expected, (key, current, threshold, previous)
    assert bytes(emu.mem.image) == image


@pytest.mark.parametrize("key", OWNERS)
@pytest.mark.parametrize("mean", (0, 255))
def test_octane_disable_blocks_both_learning_directions_and_reference_restores(images, native, key, mean):
    original, family, feature = source_for(images, key)
    disabled = config.apply_config(original, {FEATURE: "Disabled"})
    restored = config.apply_config(disabled, {FEATURE: feature.options[0][0]})
    values = []
    for image in (original, disabled, restored):
        emu = native[family](image, experimental=True)
        _, admission, qualifier, factor = OWNERS[key]
        bias = 0 if family == "MS42" else 0x80000
        code = image[admission - bias:admission - bias + 16]
        write(emu, physical(emu, int.from_bytes(code[2:4], "little")), 255)
        result = emu.run_from(admission, stop_at=(admission + 16,), max_steps=8)
        assert result.exit_reason == "stop_at", result
        # The qualification owner receives the mean correction from its six-slot producer.
        if family == "MS42":
            write(emu, 0xC462, mean)
            emu.reg.set_word(4, mean)
            end, factor_end, factor_word = qualifier + 0x7E, factor + 0x6A, 0xFA98
        else:
            mean_operand = int.from_bytes(image[qualifier - bias + 10:qualifier - bias + 12], "little")
            write(emu, physical(emu, mean_operand), mean)
            factor_word = {"430037": 0xE2E4, "430055": 0xE302}.get(key, 0xE304)
            end, factor_end = qualifier + 0x82, factor + 0x7A
        result = emu.run_from(qualifier, stop_at=(end,), max_steps=80)
        assert result.exit_reason == "stop_at", result
        write(emu, factor_word, 0x8000, 2)
        result = emu.run_from(factor, stop_at=(factor_end,), max_steps=1500)
        assert result.exit_reason == "stop_at", result
        values.append(emu.read(factor_word))
        assert bytes(emu.mem.image) == image
    assert values[0] != 0x8000, (key, mean, values)
    assert values[1] == 0x8000, (key, mean, values)
    assert values[2] == values[0]


@pytest.mark.parametrize("key,entry,sample,detected,publication,stop,correction,base,output", [
    ("0110C6", 0x2F0DC, 0xE561, 0x2F0E6, 0x2F5FA, 0x2F624, 0xC464, 0xFA61, 0xE53D),
    ("430069", 0xB60B6, 0xFC39, 0xB60C0, 0xB65B0, 0xB65DA, 0x403A2, 0xE2B5, 0xFBAB),
])
def test_octane_learning_disable_preserves_knock_detection_and_correction(
        images, native, key, entry, sample, detected, publication, stop, correction, base, output):
    image, family, _ = source_for(images, key)
    image = config.apply_config(image, {FEATURE: "Disabled"})
    emu = native[family](image, experimental=True)
    emu.reg.set_word(6, 255)
    emu.reg.set_word(8, 0)
    write(emu, sample, 255)
    result = emu.run_from(entry, stop_at=(detected,), max_steps=20)
    assert result.exit_reason == "stop_at", result
    write(emu, correction, 116)
    write(emu, base, 100)
    result = emu.run_from(publication, stop_at=(stop,), max_steps=100)
    assert result.exit_reason == "stop_at", result
    assert byte(emu, output) == 167  # Inverted base100 plus retained -12-count correction.
    assert bytes(emu.mem.image) == image
