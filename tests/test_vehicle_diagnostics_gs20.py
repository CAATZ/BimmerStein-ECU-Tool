"""GS20 fault jobs use the established DS2 route with their own BMW semantics."""

from hashlib import sha256
from pathlib import Path

import pytest

from ds2 import DS2Error
from vehicle_diagnostics import (
    PROFILE_BY_FAULT_KEY, _GS20, _GS20_QUALIFIERS,
    clear_module_faults, read_module_faults,
)


class ReplyDS2:
    def __init__(self, payload=b"", *, status=0xA0, raw=None):
        self.payload, self.status, self.raw = payload, status, raw
        self.calls = []

    def send_frame(self, frame, resp_addr, timeout):
        assert resp_addr == 0x32 and timeout == 2.0
        self.calls.append(bytes(frame))
        if self.raw is not None:
            return self.raw
        reply = bytes((0x32, len(self.payload) + 4, self.status)) + self.payload
        checksum = 0
        for value in reply:
            checksum ^= value
        return reply + bytes((checksum,))


def record(code=0x01, flags=0xF0, frequency=1):
    return bytes((code, flags, frequency, 18)) + bytes.fromhex(
        "01 02 03 12 34 04 05 06 23 45 07 08 09 34 56")


def test_read_decodes_gs20_flags_and_all_three_mileage_snapshots():
    raw = record(flags=0xFA)
    link = ReplyDS2(b"\x01" + raw)
    [fault] = read_module_faults(link, "egs_gs20")
    assert link.calls == [bytes.fromhex("32 05 04 01 32")]
    assert fault.description == "Pressure regulator EDS 1"
    assert fault.raw_record == raw and fault.environment_raw == raw[4:]
    assert fault.status == 0xFA and fault.frequency == 1
    assert fault.current and fault.state_text == "Current"
    assert fault.conditions[:6] == (
        "Stuck closed", "Fault present after start", "Intermittent",
        "Substitute / failsafe function active", "Currently present",
        "Logistics counter: 18",
    )
    assert len(fault.conditions) == 9
    for index, (mileage, voltage, raw_values) in enumerate((
        (46600, "0.3", "01 02 03"), (90290, "0.6", "04 05 06"),
        (133980, "0.9", "07 08 09"),
    )):
        snapshot = fault.conditions[6 + index]
        assert snapshot.startswith(f"Snapshot {index + 1}: mileage {mileage} km;")
        assert f"Battery voltage: {voltage} V" in snapshot
        assert snapshot.endswith(f"raw values {raw_values}")
    assert fault.unknown_status_bits == 0


@pytest.mark.parametrize("qualifier", range(16))
def test_status_nibble_uses_the_executing_job_text_lookup(qualifier):
    [fault] = read_module_faults(ReplyDS2(b"\x01" + record(flags=qualifier)), "egs_gs20")
    assert fault.conditions[0] == _GS20_QUALIFIERS.get(qualifier, f"Unknown qualifier 0x{qualifier:X}")
    assert fault.unknown_status_bits == (qualifier if qualifier not in _GS20_QUALIFIERS else 0)
    assert not fault.current
    assert fault.state_text == "Stored"
    assert {7, 8, 9}.isdisjoint(_GS20_QUALIFIERS)  # The job does not use FARTMATRIX's remap.


@pytest.mark.parametrize("flags,state,current", [(0, "Stored", False), (0x20, "Intermittent", False),
                                                (0x80, "Current", True), (0xA0, "Current", True)])
def test_status_presence_and_intermittence(flags, state, current):
    [fault] = read_module_faults(ReplyDS2(b"\x01" + record(flags=flags)), "egs_gs20")
    assert (fault.state_text, fault.current) == (state, current)


@pytest.mark.parametrize("count", (0, 1, 5, 6, 255))
def test_read_count_limit_needs_no_paging(count):
    link = ReplyDS2(bytes((count,)) + b"".join(record(code=i + 1) for i in range(min(count, 5))))
    faults = read_module_faults(link, "egs_gs20")
    assert len(faults) == min(count, 5)
    assert all(fault.reported_total == count for fault in faults)
    assert len(link.calls) == 1


@pytest.mark.parametrize("payload", (b"", b"\x01", b"\x01" + bytes(18), b"\x06" + record() * 4))
def test_empty_or_truncated_records_are_rejected(payload):
    link = ReplyDS2(payload)
    with pytest.raises(DS2Error):
        read_module_faults(link, "egs_gs20")
    assert len(link.calls) == 1


@pytest.mark.parametrize("raw", (b"", b"\x32", b"\x32\x04", b"\x32\x04\xA0"))
def test_short_replies_are_rejected(raw):
    with pytest.raises(DS2Error, match="response length"):
        read_module_faults(ReplyDS2(raw=raw), "egs_gs20")


@pytest.mark.parametrize("code", (0xFE, 0xFF))
def test_unknown_faults_keep_raw_environment_without_invented_units(code):
    raw = record(code=code, flags=0x27, frequency=255)
    [fault] = read_module_faults(ReplyDS2(b"\x01" + raw), "egs_gs20")
    assert fault.description.startswith("Unknown fault location")
    assert fault.raw_record == raw and fault.environment_raw == raw[4:]
    assert fault.frequency == 255  # The job reads the entire byte; its 0..31 comment is not a mask.
    assert not any("Snapshot" in text for text in fault.conditions)


def test_clear_uses_only_the_existing_ds2_clear_request():
    link = ReplyDS2()
    response = clear_module_faults(link, "egs_gs20")
    assert response[2] == 0xA0
    assert link.calls == [bytes.fromhex("32 04 05 33")]
    profile = PROFILE_BY_FAULT_KEY["egs_gs20"]
    assert profile.module_key == "egs" and not profile.wake_frame


@pytest.mark.parametrize("status", (0xA1, 0xA2, 0xB0, 0xB1, 0xB2, 0xFF, 0))
@pytest.mark.parametrize("operation", (read_module_faults, clear_module_faults))
def test_rejected_read_or_clear_is_not_replayed(status, operation):
    link = ReplyDS2(status=status)
    with pytest.raises(DS2Error, match=f"status 0x{status:02X}"):
        operation(link, "egs_gs20")
    assert len(link.calls) == 1


def test_codebook_and_qualifiers_match_the_exact_bmw_source(monkeypatch):
    source = Path.home() / "ECU-reference-data/diagnostics/gs20.prg"
    decoder = Path(__file__).resolve().parents[1] / "tests/reference_tools/diagnostic_decoder"
    if not source.is_file() or not (decoder / "decoderPrg.py").is_file():
        pytest.skip("private exact GS20 BMW source unavailable")
    assert sha256(source.read_bytes()).hexdigest() == "d1d0baa26e043fcb2a4f13a06c4df840f655380c7e1519157355cd3bcada1e50"
    monkeypatch.syspath_prepend(str(decoder))
    from decoderPrg import parse_prg

    tables = {table.name: table for table in parse_prg(str(source)).tables}
    assert set(_GS20) == {int(row.values[0], 16) for row in tables["FORTTEXTE"].rows}
    direct_nibbles = {int(row.values[0], 16) for row in tables["FARTTEXTE"].rows
                      if int(row.values[0], 16) < 16}
    assert set(_GS20_QUALIFIERS) == direct_nibbles
    assert len(_GS20) == 42
