import pytest

from ds2 import DS2Error, DS2Timeout
from dtc import format_dtc_table
from vehicle_diagnostics import (
    FAULT_PROFILES,
    MODULE_PROFILES,
    PROFILE_BY_FAULT_KEY,
    clear_module_faults,
    read_module_faults,
    scan_modules,
)


def _response(address, status, payload=b""):
    frame = bytes((address, len(payload) + 4, status)) + bytes(payload)
    checksum = 0
    for byte in frame:
        checksum ^= byte
    return frame + bytes((checksum,))


def test_curated_module_scan_uses_exact_ident_frames_and_preserves_status():
    calls = []

    class FakeDS2:
        def send_frame(self, frame, resp_addr, timeout):
            calls.append((bytes(frame), resp_addr, timeout))
            if resp_addr == 0x12:
                return _response(resp_addr, 0xA0, b"MS41")
            if resp_addr == 0x44:
                return _response(resp_addr, 0xA1)
            raise DS2Timeout("simulated absent module")

    results = scan_modules(FakeDS2(), pause=0)

    assert [call[0].hex(" ") for call in calls] == [
        "12 04 00 16", "32 04 00 36", "44 04 00 40",
        "56 04 00 52", "5b 04 00 5f", "a6 04 00 a2",
    ]
    assert [call[1] for call in calls] == [p.address for p in MODULE_PROFILES]
    assert all(call[2] == 2.0 for call in calls)
    assert results[0].status_text == "Responded"
    assert results[2].status_text == "Responded — busy"
    assert results[1].status_text == "No response"


class ScriptedDS2:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def send_frame(self, frame, resp_addr, timeout):
        self.calls.append((bytes(frame), resp_addr, timeout))
        return self.responses.pop(0)


def _call_frames(ds2):
    return [frame.hex(" ") for frame, _, _ in ds2.calls]


def test_fault_profiles_are_explicit_and_grouped_by_scanned_module():
    assert [profile.key for profile in FAULT_PROFILES] == [
        "egs_gs832", "egs_gs855", "ews", "ihka",
        "asc5", "dsc5", "abs_mk20", "asc_mk20",
        "fgr", "fgr2", "gr2", "fgr2_5",
    ]
    assert PROFILE_BY_FAULT_KEY["asc5"].module_key == "abs"
    assert PROFILE_BY_FAULT_KEY["ihka"].module_key == "ihka"
    assert PROFILE_BY_FAULT_KEY["fgr2_5"].module_key == "fgr"
    assert "dme" not in PROFILE_BY_FAULT_KEY


def test_asc5_uses_wake_and_exact_read_frame_and_decodes_snapshot():
    ds2 = ScriptedDS2(
        _response(0x56, 0xA0),
        bytes.fromhex("56 0A A0 01 04 B3 07 01 20 6C"),
    )

    faults = read_module_faults(ds2, "asc5")

    assert _call_frames(ds2) == ["56 04 00 52", "56 05 04 01 56"]
    fault = faults[0]
    assert fault.code_hex == "0x04"
    assert fault.description == "Left-rear wheel-speed sensor"
    assert fault.frequency == 7
    assert fault.speed_kmh == 18.0
    assert fault.status == 0xB3
    assert fault.unknown_status_bits == 0x03
    assert fault.environment_raw == b"\x01\x20"
    assert fault.conditions == (
        "ASC not passive",
        "ABS regulation active",
        "Brake-light switch not pressed",
        "ASC regulation active",
    )
    assert fault.raw_record == bytes.fromhex("04 B3 07 01 20")


def test_abs_mk20_decodes_three_fixed_slots_and_skips_empty_slots():
    ds2 = ScriptedDS2(
        _response(0x56, 0xA0),
        bytes.fromhex("56 0D A0 11 03 A5 00 00 00 00 00 00 4C"),
    )

    faults = read_module_faults(ds2, "abs_mk20")

    assert len(faults) == 1
    assert faults[0].frequency == 3
    assert faults[0].speed_kmh == 50.0
    assert faults[0].conditions == (
        "ABS regulation active",
        "Brake-light switch not pressed",
        "Undervoltage detected",
    )


def test_asc_mk20_has_its_own_inverted_frequency_and_unknown_bit():
    ds2 = ScriptedDS2(
        _response(0x56, 0xA0),
        bytes.fromhex("56 0D A0 91 FB E6 00 00 00 00 00 00 77"),
    )

    fault = read_module_faults(ds2, "asc_mk20")[0]

    assert fault.description == "Internal CAN-controller fault"
    assert fault.frequency == 4
    assert fault.speed_kmh == 60.0
    assert fault.unknown_status_bits == 0x80
    assert fault.conditions == (
        "Regulation active", "Brake-light switch pressed")


def test_fgr_generic_and_explicit_profiles_share_exact_wire_layout():
    response = bytes.fromhex("A6 09 A0 02 01 03 13 05 19")
    generic_ds2 = ScriptedDS2(response)
    explicit_ds2 = ScriptedDS2(response)

    generic = read_module_faults(generic_ds2, "fgr")
    explicit = read_module_faults(explicit_ds2, "fgr2")

    assert _call_frames(generic_ds2) == ["a6 05 04 01 a6"]
    assert [(fault.code, fault.frequency) for fault in generic] == [
        (0x01, 3), (0x13, 5)]
    assert generic[0].description == "Watchdog system fault"
    assert explicit[0].description == "Watchdog system fault"
    assert explicit[1].description == "P+ voltage outside valid range"


def test_ews_reads_count_then_records_and_humanizes_key_faults():
    ds2 = ScriptedDS2(
        bytes.fromhex("44 05 A0 02 E3"),
        bytes.fromhex("44 09 A0 02 02 25 1E 03 D5"),
    )

    faults = read_module_faults(ds2, "ews")

    assert _call_frames(ds2) == ["44 05 04 00 45", "44 05 04 01 44"]
    assert faults[0].description == "Key 1: incorrect random code"
    assert faults[0].status_text == "Static · frequency 5"
    assert faults[0].is_active
    assert faults[1].description == "Engine ECU random code lost"
    assert faults[1].status_text == "Intermittent / sporadic · frequency 3"
    assert faults[1].raw_record == bytes.fromhex("1E 03")


def test_ews_does_not_invent_keys_above_the_ten_key_limit():
    ds2 = ScriptedDS2(
        _response(0x44, 0xA0, b"\x01"),
        _response(0x44, 0xA0, b"\x01\xA2\x01"),
    )

    assert read_module_faults(ds2, "ews")[0].description == (
        "Immobilizer fault 0xA2")


def test_egs_decodes_generic_qualifiers_and_preserves_raw_snapshots():
    ds2 = ScriptedDS2(bytes.fromhex(
        "32 18 A0 01 01 A2 02 01 00 6E AD 01 23 "
        "00 69 A3 01 00 00 00 00 00 00 01"))

    fault = read_module_faults(ds2, "egs_gs832")[0]

    assert _call_frames(ds2) == ["32 05 04 01 32"]
    assert fault.description == "Transmission fault 0x01"
    assert fault.is_active
    assert fault.status_text == "Current · frequency 2"
    assert fault.environment_raw == bytes.fromhex(
        "00 6E AD 01 23 00 69 A3 01 00 00 00 00 00 00")
    assert fault.conditions == (
        "Short circuit to battery positive",
        "Intermittent",
        "Currently present",
        "CARB count: 1",
        "Snapshot 1: values 00 6E AD; operating hours 291",
        "Snapshot 2: values 00 69 A3; operating hours 256",
    )


def test_egs_preserves_reported_count_while_decoding_factory_cap_of_five():
    records = b"".join(bytes((code,)) + bytes(18) for code in range(1, 6))
    faults = read_module_faults(
        ScriptedDS2(_response(0x32, 0xA0, b"\x06" + records)),
        "egs_gs855",
    )

    assert len(faults) == 5
    assert all(fault.reported_total == 6 for fault in faults)


@pytest.mark.parametrize(
    ("profile_key", "responses"),
    [
        ("ews", (bytes.fromhex("44 05 A0 02 E3"),
                 bytes.fromhex("44 07 A0 02 02 25 C6"))),
        ("egs_gs832", (bytes.fromhex("32 05 A0 01 96"),)),
    ],
)
def test_counted_module_records_reject_truncated_payloads(profile_key, responses):
    with pytest.raises(DS2Error, match="payload length"):
        read_module_faults(ScriptedDS2(*responses), profile_key)


@pytest.mark.parametrize(
    ("profile_key", "response"),
    [
        ("ews", bytes.fromhex("44 05 A0 00 E1")),
        ("egs_gs855", bytes.fromhex("32 05 A0 00 97")),
    ],
)
def test_counted_module_no_fault_replies(profile_key, response):
    assert read_module_faults(ScriptedDS2(response), profile_key) == []


def test_ihka_reads_only_needed_pages_and_preserves_flags_and_opaque_bytes():
    records = [
        bytes((0x21, 0xD1, 5)) + bytes(range(8)),
        bytes((0x22, 0x04, 2)) + b"abcdefgh",
        bytes((0x23, 0x28, 1)) + b"ABCDEFGH",
    ]
    ds2 = ScriptedDS2(
        _response(0x5B, 0xA0, bytes((3,)) + records[0] + records[1]),
        _response(0x5B, 0xA0, bytes((3,)) + records[2]),
    )

    faults = read_module_faults(ds2, "ihka")

    assert _call_frames(ds2) == ["5b 05 04 01 5b", "5b 05 04 03 59"]
    assert [fault.code for fault in faults] == [0x21, 0x22, 0x23]
    assert faults[0].is_active
    assert faults[0].unknown_status_bits == 0x10
    assert faults[0].environment_raw == bytes(range(8))
    assert faults[0].conditions == (
        "Short circuit to battery positive", "Current", "Intermittent")
    assert faults[2].unknown_status_bits == 0x20
    assert faults[2].description == "Unknown fault location 0x23"


def test_ihka_rejects_a_fault_count_that_changes_between_pages():
    record = bytes(11)
    ds2 = ScriptedDS2(
        _response(0x5B, 0xA0, bytes((3,)) + record + record),
        _response(0x5B, 0xA0, bytes((2,))),
    )

    with pytest.raises(DS2Error, match="count changed"):
        read_module_faults(ds2, "ihka")


@pytest.mark.parametrize(
    ("profile_key", "calls"),
    [
        ("egs_gs832", ["32 04 05 33"]),
        ("ews", ["44 04 05 45"]),
        ("ihka", ["5b 04 05 5a"]),
        ("dsc5", ["56 04 00 52", "56 04 05 57"]),
        ("fgr2_5", ["a6 04 05 a7"]),
    ],
)
def test_clear_uses_exact_frames(profile_key, calls):
    address = PROFILE_BY_FAULT_KEY[profile_key].address
    ds2 = ScriptedDS2(*[_response(address, 0xA0) for _ in calls])

    response = clear_module_faults(ds2, profile_key)

    assert _call_frames(ds2) == calls
    assert response[2] == 0xA0


def test_busy_is_an_error_and_is_not_retried():
    ds2 = ScriptedDS2(_response(0xA6, 0xA1))

    with pytest.raises(DS2Error, match="status 0xA1"):
        read_module_faults(ds2, "fgr2")

    assert _call_frames(ds2) == ["a6 05 04 01 a6"]


def test_module_fault_is_compatible_with_existing_text_export():
    ds2 = ScriptedDS2(bytes.fromhex("A6 07 A0 01 01 03 02"))

    text = format_dtc_table(read_module_faults(ds2, "fgr2"))

    assert "0x01" in text
    assert "Cruise Control FGR2" in text
    assert "Watchdog system fault" in text
    assert "01 03" in text
