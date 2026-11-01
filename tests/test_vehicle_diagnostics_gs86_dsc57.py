import pytest

from ds2 import DS2Error
from vehicle_diagnostics import clear_module_faults, read_module_faults


class ScriptedDS2:
    def __init__(self, address, *payloads):
        self.address = address
        self.payloads = iter(payloads)
        self.calls = []

    def send_frame(self, frame, resp_addr, timeout):
        assert resp_addr == self.address
        self.calls.append(bytes(frame))
        payload = next(self.payloads)
        reply = bytes((resp_addr, len(payload) + 4, 0xA0)) + payload
        checksum = 0
        for byte in reply:
            checksum ^= byte
        return reply + bytes((checksum,))


@pytest.mark.parametrize("profile", ["egs_gs8600", "egs_gs8604"])
def test_gs86_decodes_its_own_flags_and_mileage_snapshots(profile):
    record = bytes.fromhex("02 F7 01 12 01 02 03 12 34 04 05 06 23 45 07 08 09 34 56")
    ds2 = ScriptedDS2(0x32, b"\x01" + record)
    [fault] = read_module_faults(ds2, profile)
    assert ds2.calls == [bytes.fromhex("32 05 04 01 32")]
    assert fault.raw_record == record
    assert fault.description == "Pressure regulator EDS 2"
    assert fault.current
    assert fault.frequency == 1
    assert fault.conditions[:6] == (
        "Value too high", "Fault lamp requested", "Intermittent",
        "Substitute / failsafe function active", "Currently present",
        "Logistics counter: 18",
    )
    assert fault.conditions[6].startswith("Snapshot 1: mileage 46600 km;")
    assert fault.conditions[6].endswith("Battery voltage: 0.24 V; raw values 01 02 03")
    assert len(fault.conditions) == 9  # Three snapshots even when frequency is one.
    assert fault.environment_raw == record[4:]
    assert not any("hours" in text for text in fault.conditions)


def test_gs8604_specific_fault_is_not_claimed_for_gs8600():
    record = bytes.fromhex("41 08 01 00") + bytes(15)
    for profile, expected in (
        ("egs_gs8600", "Unknown fault location 0x41"),
        ("egs_gs8604", "Gear 4 monitoring (ENP)"),
    ):
        [fault] = read_module_faults(ScriptedDS2(0x32, b"\x01" + record), profile)
        assert fault.description == expected
        assert fault.conditions[0] == "Value too low"


@pytest.mark.parametrize("profile", ["egs_gs8600", "egs_gs8604"])
def test_gs86_five_record_limit_preserves_reported_count(profile):
    records = [bytes((code, 0x20, 1, 0)) + bytes(15) for code in range(1, 6)]
    ds2 = ScriptedDS2(0x32, b"\x06" + b"".join(records))
    faults = read_module_faults(ds2, profile)
    assert [fault.code for fault in faults] == [1, 2, 3, 4, 5]
    assert all(fault.reported_total == 6 for fault in faults)
    assert len(ds2.calls) == 1


@pytest.mark.parametrize("payload", [b"", b"\x01", b"\x01" + bytes(18)])
def test_gs86_rejects_truncated_records(payload):
    with pytest.raises(DS2Error):
        read_module_faults(ScriptedDS2(0x32, payload), "egs_gs8600")


def test_dsc57_status_bits_are_snapshot_states_not_fault_current_bits():
    ds2 = ScriptedDS2(0x56, b"", bytes.fromhex("01 04 93 07 01 20"))
    [fault] = read_module_faults(ds2, "dsc57")
    assert ds2.calls == [bytes.fromhex("56 04 00 52"), bytes.fromhex("56 05 04 01 56")]
    assert fault.description == "Left-rear wheel-speed sensor plausibility"
    assert fault.frequency == 7
    assert fault.speed_kmh == 18.0
    assert not fault.current
    assert fault.unknown_status_bits == 0
    assert fault.conditions == (
        "ASC passive", "ABS regulation inactive", "Brake-light switch not pressed",
        "ASC regulation active", "ACB active", "HBA active", "ECD inactive", "HDC inactive",
    )


@pytest.mark.parametrize("frequency,count", [(49, 3), (50, 2)])
def test_dsc57_brake_switch_record_consumes_the_documented_slots(frequency, count):
    payload = bytes((count, 0x19, 0, frequency, 0, 0, 0x59, 0, 4, 0, 32))
    faults = read_module_faults(ScriptedDS2(0x56, b"", payload), "dsc57")
    assert [fault.code for fault in faults] == [0x19, 0x59]
    assert faults[0].speed_kmh is None  # No scaling row for this fault in BMW's table.
    assert faults[1].speed_kmh == 2.0
    assert all(fault.reported_total == count for fault in faults)


def test_dsc57_unmapped_fields_remain_raw():
    [fault] = read_module_faults(
        ScriptedDS2(0x56, b"", bytes.fromhex("01 FE FF 01 12 34")), "dsc57")
    assert fault.description == "Unknown fault location 0xFE"
    assert fault.conditions == ()
    assert fault.speed_kmh is None
    assert fault.unknown_status_bits == 255
    assert fault.environment_raw == b"\x12\x34"


@pytest.mark.parametrize("payload", [b"", b"\x01", b"\x01" + bytes(4), b"\x02" + bytes(5)])
def test_dsc57_rejects_truncated_records(payload):
    with pytest.raises(DS2Error):
        read_module_faults(ScriptedDS2(0x56, b"", payload), "dsc57")


@pytest.mark.parametrize("profile,address,payloads,frames", [
    ("egs_gs8600", 0x32, [b"\x00"], ["32 05 04 01 32"]),
    ("egs_gs8604", 0x32, [b"\x00"], ["32 05 04 01 32"]),
    ("dsc57", 0x56, [b"", b"\x00"], ["56 04 00 52", "56 05 04 01 56"]),
])
def test_new_profiles_empty_fault_memory(profile, address, payloads, frames):
    ds2 = ScriptedDS2(address, *payloads)
    assert read_module_faults(ds2, profile) == []
    assert ds2.calls == [bytes.fromhex(frame) for frame in frames]


@pytest.mark.parametrize("profile,address,payloads,frames", [
    ("egs_gs8600", 0x32, [b""], ["32 04 05 33"]),
    ("egs_gs8604", 0x32, [b""], ["32 04 05 33"]),
    ("dsc57", 0x56, [b"", b""], ["56 04 00 52", "56 04 05 57"]),
])
def test_new_profiles_clear_uses_existing_one_attempt_contract(profile, address, payloads, frames):
    ds2 = ScriptedDS2(address, *payloads)
    clear_module_faults(ds2, profile)
    assert ds2.calls == [bytes.fromhex(frame) for frame in frames]


@pytest.mark.parametrize("profile", ["egs_gs8600", "egs_gs8604", "dsc57"])
def test_busy_is_not_retried(profile):
    class BusyDS2:
        calls = 0

        def send_frame(self, frame, resp_addr, timeout):
            self.calls += 1
            return bytes((resp_addr, 4, 0xA1, 0))

    for action in (read_module_faults, clear_module_faults):
        ds2 = BusyDS2()
        with pytest.raises(DS2Error, match="0xA1"):
            action(ds2, profile)
        assert ds2.calls == 1
