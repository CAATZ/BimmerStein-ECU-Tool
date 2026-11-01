"""Complete host read workflow over an injected peer; no real ports are opened.

The peer validates wire framing and allowed operations. It does not emulate CPU
execution or establish physical timing; separate C167 tests cover native code.
"""

from hashlib import sha256
import os
from pathlib import Path

import pytest

import ds2
import ms42_ms43 as protocol
import ms42_ms43_image as images
import ms42_ms43_resident as resident
from checksum import _crc
from ds2 import DS2Error, DS2Interface, _xor


REFERENCES = {
    "MS42": ("Siemens_MS42_0110C6_E46_M52TUB28_EU3_LHD.bin",
             "010d6bf17bcfe46a9f436ba8e11dff375466ee948aaaf10d43a1d8325757b2cc"),
    "MS43": ("Siemens_MS43_MS430069_E46_M54B30_US.bin",
             "eb687fdd8eee4a0671dbe1d3294b4c25788b6cfa60c29f15d3b77293981909da"),
}


def response(data=b"", *, stock=True, status=0xA0):
    header = bytes((0x12, len(data) + 4, status)) if stock else bytes((len(data) + 3, 1, status))
    body = header + data
    return body + bytes((_xor(body),))


class Peer:
    """Wire-level fake with independent format/state and no flash-write model."""

    native_fast_capable = True
    transport_name = "injected"
    is_open = True

    def __init__(self, family, image, **kwargs):
        assert kwargs["port"] == "FAKE_RAM_WORKFLOW_ONLY"
        assert kwargs["two_stop"] is False
        self.family, self.image = family, image
        self.layout, self.profile = images.PROFILES[family], resident.PROFILES[family]
        self.baudrate, self.parity, self.stopbits = kwargs["baudrate"], "E", 1
        self.expected_format = (9600, "E", 1)
        self.mode = "ds2"
        self.pending = b""
        self.requests = []
        self.ram = None
        self.authorized = False
        self.seed_sent = False
        self.reset_staged = False
        self.reset_checked = False
        self.restored = False
        self.identities = 0
        self.initial_probes = 0
        self.resident_baud_selected = False
        self.fault = None
        self.unsupported = None
        self.busy_payloads = set()
        self.authorization_delay = 0.0
        self.ready_at = 0.0
        self.selected_measurements = ()
        self.measurement_reads = []

    def __setattr__(self, name, value):
        fault = getattr(self, "fault", None)
        if (name == "parity" and value == "N" and fault == "host_8n1"
                or name == "baudrate" and fault == "host_fast_baud"
                and getattr(self, "mode", None) == "resident" and value != 9600
                or name == "parity" and value == "E" and fault == "host_restore" and self.restored):
            raise OSError("host format change failed")
        object.__setattr__(self, name, value)

    def reset_input_buffer(self):
        self.pending = b""

    def flush(self):
        pass

    def read(self, count):
        result, self.pending = self.pending[:count], self.pending[count:]
        return result

    def write(self, message):
        assert (self.baudrate, self.parity, self.stopbits) == self.expected_format
        assert _xor(message) == 0
        if self.mode == "ds2":
            assert message[0] == 0x12 and len(message) == message[1]
            command, args = message[2], message[3:-1]
            self.requests.append(("ds2", command, args))
            output = self.stock(command, args)
        else:
            assert message[1] == 1 and len(message) == message[0] + 1
            command, args = message[2], message[3:-1]
            self.requests.append(("resident", command, args))
            output = self.loader(command, args)
        self.pending = bytes(message) + output  # K-line echo precedes the response.
        return len(message)

    def stock(self, command, args):
        assert command in (0x00, 0x06, 0x07, 0x0B, 0x90, 0x91, 0x53), "Unexpected DS2 operation"
        if command == 0x0B:
            # FC retains the authorization dispatcher until its key is answered.
            if self.seed_sent and not self.authorized:
                self.seed_sent = False
                self.ready_at = protocol.time.monotonic() + 10
                return response(status=0xA2)
            if args[0] == 1:
                assert args[1] == 8 and len(args) == 42
                entries = [args[offset:offset + 5] for offset in range(2, len(args), 5)]
                assert all(entry[0] == 0 for entry in entries)
                self.selected_measurements = tuple(int.from_bytes(entry[1:], "big") for entry in entries)
                assert self.selected_measurements == tuple(range(0x3F5C, 0x3F64))
                return response()
            if args == b"\x00":
                assert self.selected_measurements
                self.measurement_reads.append(self.selected_measurements)
                return response(bytes(self.image[address] for address in self.selected_measurements))
            assert args == b"\xFF"
            self.selected_measurements = ()
            return response()
        if command == 0x91:
            baud = next(rate for rate, payload in protocol.BAUD_PAYLOADS.items() if payload == args)
            self.expected_format = (baud, "E", 1)
            return b"" if self.fault == "stock_switch_ack" else response()
        if command == 0x06:
            assert len(args) == 5
            address, count = int.from_bytes(args[:4], "big"), args[4]
            assert 1 <= count <= 123
            if address == {"MS42": 0xF736, "MS43": 0xF6B0}[self.family]:
                assert count == 1
                busy = (protocol.time.monotonic() < self.ready_at
                        or str(self.fault).startswith("authorization_timeout_"))
                return response(bytes((1 if busy else 4,)))
            if self.ram is not None and address == self.profile["ram"]:
                data = self.ram[:count]
                if self.fault == "upload_readback":
                    data = bytes((data[0] ^ 1,)) + data[1:]
            else:
                offset = address & 0x7FFFF
                data = bytes(0xFF if 0x3F00 <= index < 0x3F64 else self.image[index]
                             for index in range(offset, offset + count))
                if offset == self.layout.sw_offset and (
                        self.unsupported == "software" or self.restored and self.fault == "restored_identity"):
                    data = b"999999"
                if offset < 0xC000 and self.unsupported == "boot":
                    data = bytes((data[0] ^ 1,)) + data[1:]
                if self.restored and self.fault == "restored_identity_read":
                    return b""
            assert len(data) == count
            return response(data)
        if command == 0x90:
            phase = "seed" if args == b"\xFC\x00\x00\x00" else "key"
            if self.fault == "authorization_malformed":
                return response(b"\x00", status=0xA1)
            if self.fault == "authorization_lost_reply":
                return b""
            if (self.fault == "authorization_timeout_" + phase
                    or protocol.time.monotonic() < self.ready_at):
                return response(status=0xA1)
            if self.fault == "authorization_busy" and args not in self.busy_payloads:
                self.busy_payloads.add(args)
                return response(status=0xA1)
            if args == b"\xFC\x00\x00\x00":
                if self.authorized:
                    return response(b"\x00")
                assert not self.seed_sent
                self.seed_sent = True
                self.identities = self.initial_probes = 0
                challenge = bytes(self.image[0x3F5C:0x3F60])
                if self.fault == "challenge":
                    challenge = bytes(4)
                return response(challenge)
            assert self.seed_sent and args == bytes(self.image[0x3F60:0x3F64])
            assert not self.authorized
            self.authorized = True
            return b"" if self.fault == "key_ack" else response(b"\x00")
        if command == 0x07:
            assert self.authorized
            assert args[0] == 0, "Erase/program/marker operations are forbidden"
            assert int.from_bytes(args[1:4], "big") == self.profile["ram"]
            assert args[4] == len(args[5:]) == 24
            header, jump = args[5:25], args[25:]
            start = self.profile["ram"] + 20
            assert jump == self.profile["jump"]
            assert header == (_crc(jump, 0x544D).to_bytes(2, "little") + bytes(2)
                              + start.to_bytes(4, "little") + (start + 3).to_bytes(4, "little")
                              + self.layout.hw_prefix.encode("ascii") + b"MT")
            assert self.ram is None
            self.ram = args[5:]
            return b"" if self.fault == "upload_ack" else response(bytes(5) + b"\x01")
        if command == 0x53:
            assert self.ram and args == b"\xFC"
            if self.fault == "handoff":
                raise OSError("ambiguous handoff write")
            self.mode = "resident"
            self.expected_format = (9600, "N", 1)
            return b""  # Deliberately no ECU response to the execution handoff.
        assert command == 0x00 and not args
        assert not self.seed_sent or self.authorized, "Identification cannot interrupt a pending key"
        return b"" if self.fault == "reconnect" else response(bytes(42))

    def loader(self, command, args):
        assert command in (0x01, 0x02, 0x71, 0x78, 0x79), "Resident flash command forbidden"
        if command == 0x02:
            assert not args
            if not self.resident_baud_selected:
                self.initial_probes += 1
                if self.fault == "startup_silent" or self.fault == "startup_late" and self.initial_probes == 1:
                    return b""
                if self.fault == "startup_partial":
                    return response(self.profile["identity"], stock=False)[:-1]
            elif self.fault == "postbaud_silent":
                return b""
            self.identities += 1
            bad = self.fault == ("entry_identity" if self.identities == 1 else "baud_identity")
            return response(bytes(12) if bad else self.profile["identity"], stock=False)
        if command == 0x01:
            assert self.identities == 1 and not self.resident_baud_selected
            rates = {"MS42": {bytes.fromhex("00 41 1B 00 00"): 166667,
                              bytes.fromhex("00 30 D4 00 00"): 125000},
                     "MS43": {bytes.fromhex("00 09 3E 00 00"): 187500,
                              bytes.fromhex("00 06 29 00 00"): 125000}}
            assert args in rates[self.family]
            baud = rates[self.family][args]
            self.expected_format = (baud, "N", 1)
            self.resident_baud_selected = True
            return b"" if self.fault == "resident_switch_ack" else response(stock=False)
        if command == 0x71:
            assert self.identities == 2 and len(args) == 5
            address, count = int.from_bytes(args[:3], "big"), int.from_bytes(args[3:], "big") * 2
            if self.reset_staged:
                assert address == (0xF602 if self.family == "MS42" else 0xF902) and count == 4
                self.reset_checked = True
                data = bytes.fromhex("B7 48 B7 B7") if self.fault != "reset_payload" else bytes(4)
            else:
                assert 0x80000 <= address < 0x100000 and not address & 1
                assert 2 <= count <= 252 and address + count <= 0x100000
                if self.fault == "read":
                    return b""
                data = self.image[address - 0x80000:address - 0x80000 + count]
            words = bytearray(count)
            words[0::2], words[1::2] = data[1::2], data[0::2]
            return response(bytes(words), stock=False)
        if command == 0x78:
            assert args == bytes.fromhex("00 00 B7 48 B7 B7")
            self.reset_staged = True
            return b"" if self.fault == "reset_ack" else response(stock=False)
        assert command == 0x79 and not args and self.reset_staged and self.reset_checked
        if self.fault == "reset_execute":
            raise OSError("ambiguous SRST execution")
        self.mode = "ds2"
        self.restored = True
        self.expected_format = (9600, "E", 1)
        self.ram = None
        self.seed_sent = self.authorized = False
        self.reset_staged = self.reset_checked = False
        self.resident_baud_selected = False
        self.ready_at = protocol.time.monotonic() + self.authorization_delay
        self.busy_payloads.clear()
        return b""  # SRST execution also has no ECU acknowledgement.


@pytest.fixture(params=("MS42", "MS43"))
def workflow(request, monkeypatch):
    family = request.param
    root = Path(os.environ.get("MS41_TEST_DATA_ROOT", Path(__file__).resolve().parents[2] / "_shared"))
    name, digest = REFERENCES[family]
    path = root / ("REF_" + family) / name
    if not path.is_file():
        pytest.skip("private exact MS42/MS43 reference image unavailable")
    image = path.read_bytes()
    assert sha256(image).hexdigest() == digest
    now = [0.0]
    monkeypatch.setattr(protocol.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(protocol.time, "sleep", lambda delay: now.__setitem__(0, now[0] + delay))
    monkeypatch.setattr(ds2, "_import_d2xx_serial", lambda: pytest.fail("No real driver allowed"))
    if ds2.serial is not None:
        monkeypatch.setattr(ds2.serial, "Serial", lambda **_: pytest.fail("No real port allowed"))
    peers = []
    def factory(**kwargs):
        peer = Peer(family, image, **kwargs)
        peers.append(peer)
        return peer
    link = DS2Interface("FAKE_RAM_WORKFLOW_ONLY", serial_factory=factory, two_stop=False)
    link.open()
    assert len(peers) == 1
    return protocol.MS42MS43ECU(link, family), peers[0]


@pytest.mark.parametrize("mode", ("full", "tune"))
def test_complete_ram_read_and_normal_session_restoration(workflow, mode):
    ecu, peer = workflow
    progress, logs = [], []
    with ecu.read_transfer(log_fn=logs.append) as reader:
        assert isinstance(reader, resident.ResidentReader)
        assert reader.read_source == "ECU RAM loader read"
        data = getattr(reader, "read_" + mode)(progress_cb=lambda *args: progress.append(args))
    expected = (peer.image if mode == "full" else
                peer.image[ecu.profile.calibration_offset:ecu.profile.calibration_offset + ecu.profile.partial_size])
    assert data == expected
    assert progress[-1][:2] == (len(expected), len(expected))
    assert peer.restored and ecu.rate_confirmed and ecu.is_open
    assert ecu.transport._ser is peer
    assert (peer.baudrate, peer.parity, peer.stopbits, ecu.transport._BITS_PER_CHAR) == (9600, "E", 1, 11)
    assert peer.requests.count(("ds2", 0x90, b"\xFC\x00\x00\x00")) == 1
    assert [command for mode, command, _ in peer.requests if mode == "resident" and command != 0x71] == [
        0x02, 0x01, 0x02, 0x78, 0x79]
    assert len([1 for mode, command, _ in peer.requests if mode == "resident" and command == 0x71]) == (
        len(data) + 251) // 252 + 1  # The final read checks the staged SRST bytes.
    assert any(f"{peer.profile['baud']:,} baud" in text for text in logs)
    assert any("normal diagnostics restored" in text for text in logs)


@pytest.mark.parametrize("workflow", ["MS43"], indirect=True)
@pytest.mark.parametrize("marker", [b"\xa5" * 6, b"\x5a" * 6])
@pytest.mark.parametrize("mode", ["full", "tune"])
def test_ms43_mutable_mode_markers_keep_fast_reads_and_original_bytes(workflow, marker, mode):
    ecu, peer = workflow
    image = bytearray(peer.image)
    image[0x3EEC:0x3EF2] = marker
    peer.image = bytes(image)
    with ecu.read_transfer() as reader:
        assert isinstance(reader, resident.ResidentReader)
        data = getattr(reader, "read_" + mode)()
    expected = peer.image if mode == "full" else peer.image[0x70000:0x80000]
    assert data == expected
    assert peer.restored and ecu.rate_confirmed and ecu.baud == 9600


def test_compatibility_probe_is_small_and_cached_per_connection(workflow):
    ecu, peer = workflow
    assert isinstance(resident.select_reader(ecu), resident.ResidentReader)
    first = [args for mode, command, args in peer.requests
             if mode == "ds2" and command == 0x06 and int.from_bytes(args[:4], "big") < 0xC000]
    assert 0 < sum(args[4] for args in first) <= 128
    assert len(first) <= 3
    peer.requests.clear()
    assert isinstance(resident.select_reader(ecu), resident.ResidentReader)
    assert all(command == 0x06 and int.from_bytes(args[:4], "big") >= 0xC000
               for mode, command, args in peer.requests)
    # A cached service signature must never bypass a changed software identity.
    peer.unsupported = "software"
    assert resident.select_reader(ecu) is None


@pytest.mark.parametrize("span", [0, 1, 2])
@pytest.mark.parametrize("end", [False, True])
def test_changed_compatibility_signature_keeps_stock_reads(workflow, span, end):
    ecu, peer = workflow
    address, length = ({"MS42": ((0x8044, 48), (0xB800, 16), (0xB900, 56)),
                        "MS43": ((0x20E8, 40), (0x8004, 4), (0xBF10, 64))}[ecu.family][span])
    image = bytearray(peer.image)
    image[address + (length - 1 if end else 0)] ^= 1
    peer.image = bytes(image)
    with ecu.read_transfer() as reader:
        assert reader is ecu
        reader.read_tune()
    assert not peer.authorized and peer.ram is None
    assert all(mode == "ds2" and command in (0x06, 0x91) for mode, command, _ in peer.requests)


def test_failed_probe_is_not_cached_and_close_invalidates_cache(workflow):
    ecu, peer = workflow
    read_memory = ecu.read_memory
    def interrupted(*_):
        raise DS2Error("Probe interrupted")
    ecu.read_memory = interrupted
    with pytest.raises(DS2Error, match="Probe interrupted"):
        resident.select_reader(ecu)
    assert ecu._resident_signature_matches is None
    ecu.read_memory = read_memory
    assert isinstance(resident.select_reader(ecu), resident.ResidentReader)
    assert ecu._resident_signature_matches is True
    peer.close = lambda: None
    ecu.close()
    assert ecu._resident_signature_matches is None


@pytest.mark.parametrize("reason", ("software", "boot", "adapter", "slow"))
def test_unavailable_or_forced_slow_reader_stays_in_stock_diagnostics(workflow, reason):
    ecu, peer = workflow
    peer.unsupported = reason
    peer.native_fast_capable = reason != "adapter"
    with ecu.read_transfer(force_slow=reason == "slow") as reader:
        assert reader is ecu
        data = reader.read_tune()
    start = ecu.profile.calibration_offset
    assert data == peer.image[start:start + ecu.profile.partial_size]
    assert all(mode == "ds2" and command in (0x06, 0x91) for mode, command, _ in peer.requests)
    assert peer.ram is None and not peer.authorized
    assert ecu.rate_confirmed and ecu.baud == 9600
    if reason in ("adapter", "slow"):
        assert all(command == 0x06 for _, command, _ in peer.requests)
        assert all(start <= int.from_bytes(args[:4], "big") < start + ecu.profile.partial_size
                   for _, _, args in peer.requests)


def test_incomplete_write_rejects_read_before_any_transport_io(workflow):
    ecu, peer = workflow
    ecu._pending_image = b"retained write image"
    with pytest.raises(DS2Error, match="incomplete write"):
        with ecu.read_transfer():
            pytest.fail("Read context must not yield")
    assert peer.requests == []


def test_fc_authorization_waits_on_explicit_busy_without_changing_payloads(workflow):
    ecu, peer = workflow
    peer.fault = "authorization_busy"
    with ecu.read_transfer() as reader:
        reader.read_tune()
    assert [(command, args) for mode, command, args in peer.requests if mode == "ds2" and command == 0x90] == [
        (0x90, b"\xFC\x00\x00\x00"), (0x90, b"\xFC\x00\x00\x00"),
        (0x90, peer.profile["key"]), (0x90, peer.profile["key"])]
    assert ecu.rate_confirmed


def test_repeated_reads_wait_for_readiness_again_after_ram_reset(workflow):
    ecu, peer = workflow
    # Supplied clock reflects MS42's ten-second and MS43's nine-service startup;
    # native firmware tests separately establish each predicate and its owner.
    peer.authorization_delay = 10.0 if ecu.family == "MS42" else 9.0
    peer.ready_at = protocol.time.monotonic() + peer.authorization_delay
    logs = []
    for mode in ("tune", "full"):
        started = protocol.time.monotonic()
        with ecu.read_transfer(log_fn=logs.append) as reader:
            assert isinstance(reader, resident.ResidentReader)
            data = getattr(reader, "read_" + mode)()
        expected = (peer.image if mode == "full" else
                    peer.image[ecu.profile.calibration_offset:
                               ecu.profile.calibration_offset + ecu.profile.partial_size])
        assert data == expected
        assert protocol.time.monotonic() - started >= peer.authorization_delay - 0.3
        assert ecu.rate_confirmed and ecu.baud == 9600
    assert peer.requests.count(("ds2", 0x90, peer.profile["key"])) == 2
    assert peer.requests.count(("ds2", 0x53, b"\xFC")) == 2
    assert any("wait" in message.lower() for message in logs)
    probes = [args for mode, command, args in peer.requests
              if mode == "ds2" and command == 0x06 and int.from_bytes(args[:4], "big") < 0xC000]
    assert len(probes) == 3 and sum(args[4] for args in probes) <= 128


@pytest.mark.parametrize("phase", ["seed", "key"])
def test_authorization_timeout_preserves_only_unchanged_challenge_phase(workflow, phase):
    ecu, peer = workflow
    peer.fault = "authorization_timeout_" + phase
    started = protocol.time.monotonic()
    if phase == "seed":
        with ecu.read_transfer() as reader:
            assert reader is ecu
            elapsed = protocol.time.monotonic() - started
            assert reader.read_tune() == images.extract_calibration(peer.image)
    else:
        with pytest.raises(DS2Error, match="A1") as error:
            with ecu.read_transfer():
                pytest.fail("A pending key must not be replaced by stock commands")
        elapsed = protocol.time.monotonic() - started
    assert 15 <= elapsed <= 15.7
    assert ecu.baud == 9600 and ecu.is_open
    assert peer.ram is None and not peer.authorized
    assert all(mode == "ds2" and command not in (0x07, 0x53)
               for mode, command, _ in peer.requests)
    if phase == "seed":
        assert ecu.rate_confirmed
        assert ecu.read_memory(0x20, 4) == peer.image[0x20:0x24]
    else:
        assert isinstance(error.value.__cause__, protocol.AuthorizationBusyError)
        assert "ignition off" in str(error.value).lower()
        assert not ecu.rate_confirmed and peer.seed_sent
        requests = list(peer.requests)
        with pytest.raises(DS2Error, match="unconfirmed"):
            with ecu.read_transfer():
                pytest.fail("A pending key must not be replaced by a fresh challenge")
        assert peer.requests == requests


@pytest.mark.parametrize("fault,initial_probes,baud_commands", [
    ("startup_late", 2, 1), ("startup_silent", 3, 0),
    ("startup_partial", 1, 0), ("postbaud_silent", 1, 1),
])
def test_only_empty_initial_identity_reply_can_retry_without_replaying_entry(
        workflow, fault, initial_probes, baud_commands):
    ecu, peer = workflow
    peer.fault = fault
    if fault == "startup_late":
        with ecu.read_transfer() as reader:
            data = reader.read_tune()
        assert len(data) == ecu.profile.partial_size
        assert ecu.rate_confirmed and peer.restored
    else:
        with pytest.raises(DS2Error, match="Incomplete resident" if fault == "startup_partial" else "No resident"):
            with ecu.read_transfer():
                pytest.fail("Unconfirmed resident identity must not yield a reader")
        assert not ecu.rate_confirmed
        assert peer.requests[-1] == ("resident", 0x02, b"")
        assert not any(mode == "resident" and command in (0x71, 0x78, 0x79)
                       for mode, command, _ in peer.requests)
    assert peer.initial_probes == initial_probes
    assert sum(mode == "resident" and command == 0x02 for mode, command, _ in peer.requests) == (
        initial_probes + baud_commands)
    assert sum(mode == "resident" and command == 0x01 for mode, command, _ in peer.requests) == baud_commands
    assert [args for mode, command, args in peer.requests if mode == "ds2" and command == 0x90] == [
        b"\xFC\x00\x00\x00", peer.profile["key"]]
    assert sum(mode == "ds2" and command == 0x07 for mode, command, _ in peer.requests) == 1
    assert peer.requests.count(("ds2", 0x53, b"\xFC")) == 1
    assert [args for mode, command, args in peer.requests if mode == "ds2" and command == 0x91] == [
        protocol.BAUD_PAYLOADS[125000], protocol.BAUD_PAYLOADS[9600]]


@pytest.mark.parametrize("fault,last", (
    ("stock_switch_ack", ("ds2", 0x91)),
    ("authorization_malformed", ("ds2", 0x90)),
    ("authorization_lost_reply", ("ds2", 0x90)),
    ("challenge", ("ds2", 0x90)),
    ("key_ack", ("ds2", 0x90)),
    ("upload_ack", ("ds2", 0x07)),
    ("handoff", ("ds2", 0x53)),
    ("host_8n1", ("ds2", 0x53)),
    ("entry_identity", ("resident", 0x02)),
    ("resident_switch_ack", ("resident", 0x01)),
    ("host_fast_baud", ("resident", 0x01)),
    ("baud_identity", ("resident", 0x02)),
    ("read", ("resident", 0x71)),
    ("reset_ack", ("resident", 0x78)),
    ("reset_payload", ("resident", 0x71)),
    ("reset_execute", ("resident", 0x79)),
    ("host_restore", ("resident", 0x79)),
    ("reconnect", ("ds2", 0x00)),
    ("restored_identity", ("ds2", 0x06)),
    ("restored_identity_read", ("ds2", 0x06)),
))
def test_failure_at_each_boundary_stops_workflow_and_requires_reconnect(workflow, fault, last):
    ecu, peer = workflow
    peer.fault = fault
    with pytest.raises(DS2Error):
        with ecu.read_transfer() as reader:
            reader.read_tune()
    assert not ecu.rate_confirmed and ecu.is_open
    assert peer.requests[-1][:2] == last
    # Only idempotent normal-identity polls/reads may repeat during restoration.
    if fault not in ("reconnect", "restored_identity", "restored_identity_read", "upload_readback"):
        assert peer.requests.count(peer.requests[-1]) == (2 if fault == "baud_identity" else 1)
    if fault == "reconnect":
        assert 1 < peer.requests.count(("ds2", 0x00, b"")) <= 26
    before = list(peer.requests)
    with pytest.raises(DS2Error, match="unconfirmed"):
        ecu.read_voltage()
    assert peer.requests == before


def test_verified_ram_upload_readback_mismatch_returns_to_stock(workflow):
    ecu, peer = workflow
    peer.fault = "upload_readback"
    with ecu.read_transfer() as reader:
        assert reader is ecu
        assert reader.read_tune() == images.extract_calibration(peer.image)
    assert ecu.rate_confirmed and ecu.baud == 9600 and peer.mode == "ds2"
    assert not any(mode == "ds2" and command == 0x53 for mode, command, _ in peer.requests)


def test_cancel_after_first_resident_chunk_does_not_send_reset_or_retry(workflow):
    ecu, peer = workflow
    cancelled = False
    def check():
        if cancelled:
            raise RuntimeError("cancelled")
    def progress(*_):
        nonlocal cancelled
        cancelled = True
    ecu.operation_check = check
    with pytest.raises(DS2Error, match="cancelled"):
        with ecu.read_transfer() as reader:
            reader.read_full(progress_cb=progress)
    assert peer.requests[-1][:2] == ("resident", 0x71)
    assert sum(mode == "resident" and command == 0x71 for mode, command, _ in peer.requests) == 1
    assert not ecu.rate_confirmed and not peer.reset_staged
