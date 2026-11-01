import pytest

from ds2 import DS2Error, DS2Timeout
from vehicle_diagnostics import clear_module_faults, read_module_faults, scan_modules


class ScriptedMK60:
    def __init__(self, *payloads):
        self.payloads = iter(payloads)
        self.calls = []

    def send_frame(self, frame, resp_addr, timeout):
        raise DS2Timeout("other modules absent")

    def send_bmw_fast(self, body, target, timeout):
        assert target == 0x29
        assert timeout == 2.0
        self.calls.append(bytes(body))
        payload = next(self.payloads)
        reply = b"\xB8\xF1\x29" + bytes((len(payload),)) + payload
        checksum = 0
        for byte in reply:
            checksum ^= byte
        return reply + bytes((checksum,))


def test_mk60_discovery_uses_verified_identity_and_exposes_a_selectable_module():
    ds2 = ScriptedMK60(b"\x5A\x80" + bytes(10))
    results = scan_modules(ds2, pause=0)
    [found] = [result for result in results if result.responded]
    assert found.profile.key == "dsc_mk60"
    assert found.profile.name == "DSC MK60"
    assert found.status_text == "Responded"
    assert ds2.calls == [bytes.fromhex("B8 29 F1 02 1A 80")]


def test_mk60_discovery_preserves_negative_status_without_a_ds2_interpretation():
    [found] = [result for result in scan_modules(
        ScriptedMK60(b"\x7F\x1A\x21"), pause=0) if result.responded]
    assert found.status_text == "Responded — request rejected (0x21)"


def test_mk60_faults_keep_the_full_16_bit_code_and_status():
    ds2 = ScriptedMK60(bytes.fromhex("58 02 5D 90 E1 5E 15 24"), b"\x57\x00", b"\x57\x00")
    faults = read_module_faults(ds2, "dsc_mk60")
    assert ds2.calls == [bytes.fromhex(frame) for frame in (
        "B8 29 F1 04 18 02 FF FF", "B8 29 F1 03 17 5D 90", "B8 29 F1 03 17 5E 15")]
    assert [fault.code_hex for fault in faults] == ["0x5D90", "0x5E15"]
    assert faults[0].description == "Left-front wheel-speed sensor: electrical fault"
    assert faults[0].raw_record == bytes.fromhex("5D 90 E1")
    assert faults[0].current
    assert faults[0].conditions[:3] == (
        "Signal or value above threshold", "Test conditions met", "Warning lamp requested")
    assert faults[1].description == "CAN timeout, transmission control unit"
    assert faults[1].state_text == "Stored"
    assert not faults[1].current
    assert all(fault.reported_total == 2 for fault in faults)


def test_mk60_conflicting_bmw_status_description_is_not_guessed():
    [fault] = read_module_faults(
        ScriptedMK60(bytes.fromhex("58 01 5D F4 50"), b"\x57\x00"), "dsc_mk60")
    assert fault.state_text == "Presence uncertain"
    assert not fault.current
    assert fault.conditions[1] == "Test conditions not yet met"
    assert any("conflicting" in condition for condition in fault.conditions)


def test_mk60_unknown_fault_and_symptom_remain_visible():
    [fault] = read_module_faults(
        ScriptedMK60(bytes.fromhex("58 01 12 34 0F"), b"\x57\x00"), "dsc_mk60")
    assert fault.description == "Unknown fault location 0x1234"
    assert fault.conditions[0] == "Unknown symptom 0xF"
    assert fault.unknown_status_bits == 15


def test_mk60_empty_fault_memory():
    assert read_module_faults(ScriptedMK60(b"\x58\x00"), "dsc_mk60") == []


@pytest.mark.parametrize("payload", [b"\x58", b"\x58\x01", b"\x58\x01\x5D\x90", b"\x58\x00\x00"])
def test_mk60_rejects_incomplete_or_extra_fault_records(payload):
    with pytest.raises(DS2Error):
        read_module_faults(ScriptedMK60(payload), "dsc_mk60")


def test_mk60_clear_enters_the_bmw_session_before_clearing_once():
    ds2 = ScriptedMK60(b"\x50\x87", b"\x54")
    reply = clear_module_faults(ds2, "dsc_mk60")
    assert reply[4:-1] == b"\x54"
    assert ds2.calls == [bytes.fromhex("B8 29 F1 02 10 87"), bytes.fromhex("B8 29 F1 03 14 FF FF")]


@pytest.mark.parametrize("payload", [b"\x7F\x10\x21", b"\x50\x85", b"\x58"])
def test_mk60_clear_does_not_proceed_after_session_rejection(payload):
    ds2 = ScriptedMK60(payload)
    with pytest.raises(DS2Error):
        clear_module_faults(ds2, "dsc_mk60")
    assert len(ds2.calls) == 1


def test_mk60_clear_rejection_is_not_repeated():
    ds2 = ScriptedMK60(b"\x50\x87", b"\x7F\x14\x21")
    with pytest.raises(DS2Error, match="status 0x21"):
        clear_module_faults(ds2, "dsc_mk60")
    assert len(ds2.calls) == 2
