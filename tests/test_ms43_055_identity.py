"""430055 identity uses its actual restore and reference-validation owners."""

import pytest

import ms42_ms43_diagnostics as diagnostics
from ms42_ms43_offline import decode_identity
from tests.test_ms42_ms43_config_daten import packages as packages, native as native
from tests.test_ms42_ms43_config_revisions import private_sources as private_sources
from tests.test_ms42_ms43_diagnostics import identity_ecu
from tests.test_ms42_ms43_offline import identity_image
from tests.test_ms43_055_live_native import native_ecu, read


@pytest.fixture
def emu(packages, native):
    return native["MS43"](packages["430055"].image, experimental=True)


def test_055_identity_owners_only_relocate_the_declared_ram_fields(packages, private_sources):
    image, base = packages["430055"].image, private_sources["stock"]
    for start, end in ((0, 0x3C00), (0x6ED46, 0x6ED62)):
        assert image[start:end] == base[start:end]
    expected = bytearray(base[0x2ECA0:0x2EF00])
    fields = {0xDA: 0xD8, 0xD0: 0xCE, 0xEA: 0xE8, 0xE8: 0xE6, 0xE6: 0xE4,
              0x78: 0x6E, 0x79: 0x6F, 0x7A: 0x70}
    positions = (0x5C, 0x90, 0x9A, 0x9E, 0xBC, 0xDC, 0x108, 0x10C, 0x110,
                 0x114, 0x11E, 0x13A, 0x13E, 0x144, 0x160, 0x164, 0x16A,
                 0x184, 0x1A0, 0x1BA, 0x1BE, 0x1C2, 0x1C8, 0x1CC, 0x1D2,
                 0x226, 0x22A, 0x230, 0x234, 0x238, 0x23C, 0x242, 0x24C)
    for offset in positions:
        assert expected[offset + 1] in (0xE2, 0xE3)
        expected[offset] = fields[expected[offset]]
    assert image[0x32F74:0x331D4] == expected
    assert int.from_bytes(image[0x250:0x254], "little") == 0x3ED4
    assert int.from_bytes(image[0x6ED46:0x6ED4A], "little") == 0xEFFCC
    assert int.from_bytes(image[0x6ED5E:0x6ED62], "little") == 0x70010


@pytest.mark.parametrize("slot", range(3))
@pytest.mark.parametrize("corrupt", (None, 0, 1, 2))
def test_055_offline_isn_matches_native_restore_and_word_recovery(emu, slot, corrupt):
    reference = identity_image("MS43", slot=slot)
    emu.mem.image[0x3ED4:0x3F16] = reference[0x3ED4:0x3F16]
    if corrupt is not None:
        emu.mem.image[0x3ED4 + slot * 6 + corrupt * 2] ^= 0x12
    before = bytes(emu.mem.image)
    expected = decode_identity(before)
    result = emu.call(0xB2F74, max_steps=500)
    assert result.exit_reason == "stop_at"
    actual = diagnostics.decode_isn(read(emu, 0xE2CE, 28), "MS43")
    assert actual == {key: expected[key] for key in ("isn", "isn_status")}
    assert bytes(emu.mem.image) == before


@pytest.mark.parametrize("fill,descriptors", ((0, None), (255, None),
                                             (255, (0xFEFE, 0xECEC, 0xECEC)),
                                             (255, (0xFFFF, 0xFEFE, 0xECEC))))
def test_055_erased_invalid_and_disagreeing_identity_records(emu, fill, descriptors):
    emu.mem.image[0x3ED4:0x3F16] = bytes((fill,)) * 0x42
    if descriptors is not None:
        reference = identity_image("MS43", slot=1)
        emu.mem.image[0x3ED4:0x3F16] = reference[0x3ED4:0x3F16]
        emu.mem.image[0x3EE6:0x3EEC] = b"".join(v.to_bytes(2, "little") for v in descriptors)
    expected = decode_identity(bytes(emu.mem.image))
    result = emu.call(0xB2F74, max_steps=500)
    assert result.exit_reason == "stop_at"
    assert diagnostics.decode_isn(read(emu, 0xE2CE, 28), "MS43") == {
        key: expected[key] for key in ("isn", "isn_status")}


def test_055_whole_pairing_clear_uses_its_actual_status_word(emu):
    from ms42_ms43_vin import PAIRING_STATUS
    from ms42_ms43_vin_write import PAIRING_LAYOUT

    start, count, _ = PAIRING_LAYOUT["MS43"]
    emu.mem.image[start:start + count] = b"\xff" * count
    target = bytes(emu.mem.image)
    assert emu.call(0xB2F74, max_steps=500).exit_reason == "stop_at"
    assert PAIRING_STATUS["MS43"]["430055"] == 0xE2CE
    assert read(emu, 0xE2CE, 2) == b"\xff\xff"
    assert bytes(emu.mem.image) == target


@pytest.mark.parametrize("change,expected", (("none", "References agree"),
                                           ("one_copy", "References agree"),
                                           ("ignored_byte", "References agree"),
                                           ("reference_mismatch", "Reference mismatch"),
                                           ("no_pair", "Invalid program reference")))
def test_055_info_uses_native_memory_and_the_original_reference_rule(emu, change, expected):
    image = emu.mem.image
    if change == "one_copy":
        image[0x6FFD7] ^= 1
    elif change in ("ignored_byte", "reference_mismatch"):
        index = 6 if change == "ignored_byte" else 8
        for offset in (0x70010, 0x70020, 0x70030):
            image[offset + index] ^= 1
    elif change == "no_pair":
        image[0x6FFD7] ^= 1
        image[0x6FFE3] ^= 2
    original = bytes(image)
    emu.reg.set_word(12, 0)
    emu.reg.set_word(13, 0xFCC0)
    emu.cpu.push_word(0x7FFE)
    result = emu.run_from(0x2126, stop_at=0x7FFE, max_steps=2500)
    assert result.exit_reason == "stop_at"
    status = emu.reg.get_word(4) & 255
    assert bool(status & 2) == (change == "no_pair")
    assert bool(status & 0x10) == (change == "reference_mismatch")
    assert emu.call(0xB2F74, max_steps=500).exit_reason == "stop_at"
    ecu, _ = identity_ecu("MS43")
    canned = ecu.transport.responder
    measured = native_ecu(emu).transport.responder
    ecu.transport.responder = lambda command, args: (canned(command, args) if command in (0, 0x0D)
                                                     else measured(command, args))
    info = diagnostics.read_identity(ecu)
    assert info["software_id"] == "430055"
    assert info["program_calibration_match"] == expected
    assert info["flash_driver"] == "AMD-compatible word-program driver"
    assert info["isn"] == decode_identity(original)["isn"]
    addresses = [int.from_bytes(args[:4], "big") for _, command, args in ecu.transport.calls if command == 6]
    assert 0xE2CE in addresses and 0xE2D0 not in addresses
    assert bytes(emu.mem.image) == original
