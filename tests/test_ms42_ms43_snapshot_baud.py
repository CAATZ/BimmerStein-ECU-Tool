"""Snapshot reads use real family/DS2 framing over an in-memory serial peer."""

import pytest

import ds2
from ds2 import DS2Error, DS2Interface
from ms42_ms43 import BAUD_PAYLOADS, BaudTransitionError, MS42MS43ECU
import ms42_ms43_diagnostics as diagnostics
from tests.test_ms42_ms43 import reply
from tests.test_ms42_ms43_diagnostics import IDENT, RETAINED, identity_ecu
from tests.test_ms42_ms43_transport import Serial


class SnapshotSerial(Serial):
    def __init__(self, family, capable, **kwargs):
        super().__init__(**kwargs)
        self.baudrate = self.expected_baud = kwargs["baudrate"]
        self.native_fast_capable = capable
        self.responder = identity_ecu(family)[1].responder
        self.requests = []
        self.fault = None

    def write(self, frame):
        assert self.baudrate == self.expected_baud
        assert ds2._xor(frame) == 0
        command, args = frame[2], frame[3:-1]
        self.requests.append((self.baudrate, command, args))
        if command == 0x91:
            self.expected_baud = next(rate for rate, payload in BAUD_PAYLOADS.items() if payload == args)
            failed = self.fault == ("enter" if self.expected_baud == 125000 else "restore")
            response = b"" if failed else reply()
        elif command == 0x06 and 0x4000 <= int.from_bytes(args[:4], "big") < 0x8000:
            if self.fault == "timeout":
                response = b""
            elif self.fault == "declined":
                response = reply(status=0xA2)
            else:
                response = self.responder(command, args)
        else:
            response = self.responder(command, args)
        self.pending = bytes(frame) + response
        return len(frame)


@pytest.fixture
def snapshot_peer(monkeypatch):
    monkeypatch.setattr(ds2.time, "sleep", lambda _: None)

    def create(family, *, capable=True):
        transport = DS2Interface("FAKE_ONLY")
        transport._ser = SnapshotSerial(family, capable, baudrate=9600)
        transport.transport_name = "fake"
        return MS42MS43ECU(transport, family, raw_ident=IDENT), transport._ser

    return create


def bulk_reads(peer):
    return [(baud, args) for baud, command, args in peer.requests
            if command == 0x06 and 0x4000 <= int.from_bytes(args[:4], "big") < 0x8000]


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("capable,slow,rate", [(True, False, 125000), (True, True, 9600), (False, False, 9600)])
def test_snapshot_escalates_only_bulk_read_and_restores_diagnostics(snapshot_peer, family, capable, slow, rate):
    ecu, peer = snapshot_peer(family, capable=capable)
    progress, rates = [], []
    ecu.transfer_rate_cb = lambda *args: rates.append(args)
    info = diagnostics.read_snapshot(ecu, force_slow=slow, progress_fn=lambda *args: progress.append(args))

    assert info["retained_raw"] == RETAINED
    assert info["retained_address"] == 0x4000 and info["warnings"] == []
    assert len(info["aif_raw"]) == 644
    reads = bulk_reads(peer)
    assert len(reads) == 134 and {baud for baud, _ in reads} == {rate}
    assert sum(args[4] for _, args in reads) == len(RETAINED)
    first_bulk = next(i for i, (_, command, args) in enumerate(peer.requests)
                      if command == 0x06 and int.from_bytes(args[:4], "big") == 0x4000)
    assert all(baud == 9600 for baud, _, _ in peer.requests[:first_bulk])
    assert all(command in (0, 6, 0x0B, 0x0D, 0x91) for _, command, _ in peer.requests)
    assert [args for _, command, args in peer.requests if command == 0x91] == (
        [BAUD_PAYLOADS[125000], BAUD_PAYLOADS[9600]] if rate == 125000 else [])
    assert rates[-1] == (9600, "DS2 connection")
    assert ecu.baud == peer.baudrate == peer.expected_baud == 9600 and ecu.rate_confirmed
    assert progress[0] == (0, 16385) and progress[-1] == (16385, 16385)
    assert all(a[0] <= b[0] for a, b in zip(progress, progress[1:]))
    assert diagnostics.decode_snapshot(diagnostics.encode_snapshot(info))["retained_raw"] == RETAINED


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("fault,count", [("declined", 1), ("timeout", 5)])
def test_optional_read_failure_keeps_identity_and_restores_rate(snapshot_peer, family, fault, count):
    ecu, peer = snapshot_peer(family)
    peer.fault = fault
    info = diagnostics.read_snapshot(ecu)
    assert info["vin"] == "WBAAM31010FG12345" and "retained_raw" not in info
    assert len(info["warnings"]) == 1 and "Raw retained flash unavailable" in info["warnings"][0]
    assert len(bulk_reads(peer)) == count
    assert ecu.baud == peer.baudrate == peer.expected_baud == 9600 and ecu.rate_confirmed


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("fault", ["enter", "restore"])
def test_baud_transition_failure_is_not_hidden_as_optional_storage_warning(snapshot_peer, family, fault):
    ecu, peer = snapshot_peer(family)
    peer.fault = fault
    with pytest.raises(BaudTransitionError):
        diagnostics.read_snapshot(ecu)
    assert not ecu.rate_confirmed
    assert len(bulk_reads(peer)) == (0 if fault == "enter" else 134)
    assert peer.requests[-1][1] == 0x91


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_cancellation_stops_without_success_or_more_commands(snapshot_peer, family):
    ecu, peer = snapshot_peer(family)
    progress = []

    def check():
        if bulk_reads(peer):
            raise DS2Error("Operation cancelled")

    ecu.operation_check = check
    with pytest.raises(DS2Error, match="cancelled"):
        diagnostics.read_snapshot(ecu, progress_fn=lambda *args: progress.append(args))
    assert len(bulk_reads(peer)) == 1
    assert peer.requests[-1][1] == 6
    assert progress == [(0, 16385)]
