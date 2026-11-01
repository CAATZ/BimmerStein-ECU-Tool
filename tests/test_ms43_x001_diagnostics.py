"""X001 identity reads use its actual consumers, not the removed ISN restore."""

from hashlib import sha256
from pathlib import Path

import pytest

import ms42_ms43_diagnostics as diagnostics
from ms42_ms43_offline import decode_identity
from tests.test_ms42_ms43 import reply
from tests.test_ms42_ms43_diagnostics import identity_ecu
from tests.test_ms42_ms43_offline import VIN, identity_image


X001_SHA256 = "997975e61fb237094216052c300964dffd97e2ab57a94ddea5d710216389e21d"


@pytest.fixture(scope="module")
def x001():
    path = Path.home() / "ECU-reference-data/Siemens_MS43_MS43X001_E46_M54B30_US.bin"
    if not path.is_file():
        pytest.skip("Private X001 reference is unavailable")
    image = path.read_bytes()
    assert sha256(image).hexdigest() == X001_SHA256
    return image


@pytest.fixture
def native_x001(x001, monkeypatch):
    monkeypatch.syspath_prepend(str(Path.home() / "ECU Emulator"))
    module = pytest.importorskip("ms41emu.ms43")
    monkeypatch.setattr(module.MS43, "IMAGE_SHA256", X001_SHA256)
    return module.MS43.load(x001)


def x001_ecu(image=None):
    ecu, link = identity_ecu("MS43")
    original = link.responder

    def respond(command, args):
        if command == 6:
            address, count = int.from_bytes(args[:4], "big"), args[4]
            assert address != 0xE2D0, "X001 must not read the removed runtime ISN mirror"
            if address == 0xEFFBA:
                return reply(b"43X001")
            if image is not None and (address in (0xEED46, 0xEED5E, 0x1C64)
                                      or 0xEFFCC <= address < 0xEFFF0
                                      or address == 0x70010):
                offset = address & 0x7FFFF
                return reply(image[offset:offset + count])
        return original(command, args)

    link.responder = respond
    return ecu, link


def test_x001_info_and_snapshot_keep_verified_identity_without_stale_isn():
    ecu, link = x001_ecu()
    info = diagnostics.read_identity(ecu)
    assert info["software_id"] == "43X001"
    assert info["ecu_number"] == "87654321"
    assert info["vin"] == "WBAAM31010FG12345"
    assert info["program_calibration_match"] == "References agree"
    assert info["flash_driver"] == "AMD-compatible word-program driver"
    assert info["transmission_mode"] == "Automatic"
    assert info["isn"] is None and info["isn_status"] == "Unavailable"
    assert info["warnings"] == ["ISN unavailable: 43X001 removes the original pairing handlers"]
    loaded = diagnostics.decode_snapshot(diagnostics.encode_snapshot(info), family="MS43")
    for field in ("software_id", "vin", "ecu_number", "isn", "isn_status",
                  "program_calibration_match", "flash_driver"):
        assert loaded[field] == info[field]
    assert all(command in (0, 6, 0x0B, 0x0D) for _, command, _ in link.calls)


@pytest.mark.parametrize("slot", (0, 1, 2))
def test_x001_offline_aif_and_number_do_not_claim_legacy_isn_is_active(slot):
    image = identity_image("MS43", slot=slot)
    image[0x6FFBA:0x6FFC0] = b"43X001"
    original = bytes(image)
    info = decode_identity(image)
    assert info["vin"] == VIN and info["ecu_number"] == "12345678"
    assert info["isn"] is None and info["isn_status"] == "Unavailable"
    assert info["warnings"] == ["ISN not decoded: 43X001 removes the original pairing handlers."]
    assert bytes(image) == original


def test_x001_identity_owners_and_descriptors_match_reviewed_original(x001):
    stock_path = (Path.home() / "ECU-reference-data/REF_MS43"
                  / "Siemens_MS43_MS430069_E46_M54B30_US.bin")
    if not stock_path.is_file():
        pytest.skip("Private original reference is unavailable")
    stock = stock_path.read_bytes()
    assert sha256(stock).hexdigest() == "eb687fdd8eee4a0671dbe1d3294b4c25788b6cfa60c29f15d3b77293981909da"
    # Compare individual consumers and descriptors, not merely shared boot code.
    for address, count in ((0x2126, 0x17A), (0x1653A, 0x4FC),
                           (0x26536, 0x66), (0x6ED46, 4), (0x6ED5E, 4), (0x1C64, 16)):
        assert x001[address:address + count] == stock[address:address + count]
    assert int.from_bytes(x001[0x6ED46:0x6ED4A], "little") == 0xEFFCC
    assert int.from_bytes(x001[0x6ED5E:0x6ED62], "little") == 0x70010
    # The legacy ISN restore entry is absent, despite its retained flash pointer.
    assert x001[0x2ECA0:0x2EF00] == b"\xFF" * 0x260


@pytest.mark.parametrize("automatic", (False, True))
def test_x001_native_0b94_publishes_transmission_bit(native_x001, automatic):
    emu = native_x001
    original = bytes(emu.mem.image)
    emu.write_byte(0xF6D0, 0x94)
    emu.write(0xFD9C, int(automatic))
    result = emu.call(0xA5C1E, max_steps=500)
    assert result.exit_reason == "stop_at"
    assert emu.read_byte(0xF74E) == 8
    assert emu.read_byte(0xF74F) == 0xA0
    assert bool(emu.read_byte(0xF750) & 0x10) is automatic
    assert bytes(emu.mem.image) == original


@pytest.mark.parametrize("change,expected", [
    ("none", "References agree"),
    ("one_copy", "References agree"),
    ("ignored_byte", "References agree"),
    ("reference_mismatch", "Reference mismatch"),
    ("no_pair", "Invalid program reference"),
])
def test_x001_reference_display_matches_native_validator(native_x001, change, expected):
    emu = native_x001
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
    # This boot owner returns with RET; use the verified near-call convention.
    emu.reg.set_word(12, 0)
    emu.reg.set_word(13, 0xFCC0)
    emu.cpu.push_word(0x7FFE)
    result = emu.run_from(0x2126, stop_at=0x7FFE, max_steps=2500)
    assert result.exit_reason == "stop_at"
    native_status = emu.reg.get_word(4) & 0xFF
    assert bool(native_status & 2) is (change == "no_pair")
    assert bool(native_status & 0x10) is (change == "reference_mismatch")
    ecu, _ = x001_ecu(original)
    info = diagnostics.read_identity(ecu)
    assert info["program_calibration_match"] == expected
    assert info["flash_driver"] == "AMD-compatible word-program driver"
    assert bytes(emu.mem.image) == original
