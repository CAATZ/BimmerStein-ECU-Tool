"""GUI worker -> real service -> strict fake wire workflows, without hardware.

This byte-pipe fixture validates host sequencing and flash effects. It does not
stand in for executing the generated C167 bytes or checking physical timing.
"""
from hashlib import sha256
import os
from pathlib import Path

import pytest

import ds2
import ds2_fast_safety
import ms42_ms43 as protocol
import ms42_ms43_agent as agent
import ms42_ms43_image as images
from tests.test_ms42_ms43 import Journal
from tests.test_ms42_ms43_agent import crc16, packet
from tests.test_ms42_ms43_desktop import connect as desktop_connect
from tests.test_ms42_ms43_desktop import window as window, worker_runner as worker_runner
from tests.test_ms42_ms43_ram_workflow import REFERENCES, response
from tests.test_ms42_ms43_ram_write import WritePeer, SECTORS, expected_array, target_image


class AgentPeer(WritePeer):
    """Independent framing, upload state, and fixed bottom-boot array effects."""

    def __init__(self, family, image, **kwargs):
        super().__init__(family, image, **kwargs)
        self.staged_agent = bytearray(0x800)
        self.uploaded = set()
        self.checked = set()
        self.upload_readbacks = []
        self.upload_corrupt_offset = None
        self.verifier_launches = 0
        self.agent_launches = 0
        self.agent_requests = []
        self.agent_fault = None
        self.agent_finalize_status = 0
        self.agent_finalizations = 0
        self.boot_enabled = False
        self.boot_at_reset = None

    def loader(self, command, args):
        if (command == 0x78 and args == bytes.fromhex("0000B748B7B7")) or self.reset_staged:
            return super().loader(command, args)
        if command == 0x78:
            offset = int.from_bytes(args[:2], "big")
            data = args[2:]
            assert not offset & 1 and not len(data) & 1
            assert 2 <= len(data) <= 250 and offset + len(data) <= 0x800
            self.staged_agent[offset:offset + len(data)] = data
            self.uploaded.update(range(offset, offset + len(data)))
            if self.upload_corrupt_offset is not None and offset <= self.upload_corrupt_offset < offset + len(data):
                self.staged_agent[self.upload_corrupt_offset] ^= 1
            return response(stock=False)
        if command == 0x71:
            address = int.from_bytes(args[:3], "big")
            count = int.from_bytes(args[3:], "big") * 2
            shadow = 0xF602 if self.family == "MS42" else 0xF902
            if shadow <= address < shadow + 0x96:
                offset = address - shadow
                assert offset + count <= 0x96
            elif 0xE096 <= address < 0xE800:
                offset = address - 0xE000
                assert offset + count <= 0x800
            else:
                return super().loader(command, args)
            assert 2 <= count <= 252 and not count & 1
            self.checked.update(range(offset, offset + count))
            self.upload_readbacks.append((offset, count))
            data = bytes(self.staged_agent[offset:offset + count])
            if self.agent_fault == "upload_readback":
                data = bytes((data[0] ^ 1,)) + data[1:]
            return response(b"".join(data[i:i + 2][::-1] for i in range(0, count, 2)), stock=False)
        if command == 0x79:
            from engines.ms42_ms43.payload import UPLOAD_READY, UPLOAD_REJECTED, VERIFY_PREFIX_SIZE, payload

            assert not args
            code = bytes(payload(self.family))
            assert self.uploaded == set(range(len(code)))
            assert self.checked == set(range(VERIFY_PREFIX_SIZE))
            assert bytes(self.staged_agent[:VERIFY_PREFIX_SIZE]) == code[:VERIFY_PREFIX_SIZE]
            self.verifier_launches += 1
            if crc16(self.staged_agent[VERIFY_PREFIX_SIZE:len(code)]) != crc16(code[VERIFY_PREFIX_SIZE:]):
                self.mode = "upload_failed"
                return bytes((UPLOAD_REJECTED,))
            assert bytes(self.staged_agent[:len(code)]) == code
            self.mode = "agent"
            self.agent_launches += 1
            if self.agent_fault == "upload_ready_missing":
                return b""
            if self.agent_fault == "upload_ready_wrong":
                return b"\x00"
            return bytes((UPLOAD_READY,))
        return super().loader(command, args)

    def write(self, request):
        if self.mode != "agent":
            return super().write(request)
        assert (self.baudrate, self.parity, self.stopbits) == self.expected_format
        request = bytes(request)
        assert crc16(request[:-2]) == int.from_bytes(request[-2:], "big")
        command, offset, count = request[0], int.from_bytes(request[1:4], "big"), int.from_bytes(request[4:6], "big")
        data = request[6:-2]
        assert len(data) == (count if command == ord("P") else 0)
        self.agent_requests.append((command, offset, count, data))
        self.requests.append(("agent", command, request[1:-2]))
        output = self.agent_request(command, offset, count, data)
        self.pending = request + output
        return len(request)

    def agent_request(self, command, offset, count, data):
        payload = b""
        if command == ord("I"):
            assert offset in (0, 1) and count == 4 and not data
            self.boot_enabled = bool(offset)
            payload = b"BS42" if self.family == "MS42" else b"BS43"
            if self.agent_fault == "identity":
                payload = b"NOPE"
        elif command in (ord("R"), ord("P")):
            assert not offset & 1 and not count & 1 and 2 <= count <= 1024
            assert 0 <= offset < offset + count <= 0x80000
            if command == ord("R"):
                payload = bytes(self.image[offset:offset + count])
                if self.corrupt_verify and self.erases:
                    mismatch = self.layout.calibration_offset + 0x100
                    if offset <= mismatch < offset + count:
                        payload = bytearray(payload)
                        payload[mismatch - offset] ^= 1
                        payload = bytes(payload)
                if self.agent_fault == "boot_readback" and 3 in self.erases and 0x8000 <= offset < 0x10000:
                    payload = bytes((payload[0] ^ 1,)) + payload[1:]
            else:
                assert self.boot_enabled or offset >= 0x10000
                if self.reject_next_program:
                    self.reject_next_program = False
                    return packet(command, offset, count, status=4)
                previous = self.image[offset:offset + count]
                assert all((new & ~old) == 0 for old, new in zip(previous, data)), "Programming needs erase"
                self.image[offset:offset + count] = data
                self.programs.append((offset, bytes(data)))
                if self.drop_next_program and self.erases:
                    self.drop_next_program = False
                    self.lost_program = (offset, bytes(data))
                    return b""  # Flash changed; no implicit retransmission is allowed.
                if self.agent_fault == "boot_program_ack" and offset < 0x10000:
                    self.agent_fault = None
                    self.lost_program = (offset, bytes(data))
                    return b""
        elif command == ord("E"):
            assert not count and not data
            index = next(i for i, bounds in enumerate(SECTORS) if bounds[0] == offset)
            assert self.boot_enabled or index >= 4
            start, end = SECTORS[index]
            self.image[start:end] = b"\xff" * (end - start)
            self.erases.append(index)
        elif command == ord("F"):
            assert offset == count == 0 and not data
            self.agent_finalizations += 1
            if self.agent_finalize_status:
                return packet(command, offset, count, status=self.agent_finalize_status)
            # Explicit peer outcome; native tests execute validation and commits.
            for key, expected in (("calibration_marker", "42244890"),
                                  ("program_marker", "90482442")):
                start = self.contract[key]
                self.image[start:start + 4] = bytes.fromhex(expected)
            if self.missing_marker:
                start = self.contract[self.missing_marker + "_marker"]
                self.image[start:start + 4] = b"\xff" * 4
        else:
            assert command == ord("X") and offset == count == 0 and not data
            self.markers_at_reset = tuple(bytes(self.image[self.contract[key]:self.contract[key] + 4])
                                          for key in ("program_marker", "calibration_marker"))
            self.boot_at_reset = bytes(self.image[:0x10000])
            self.mode, self.restored = "ds2", True
            self.expected_format = (9600, "E", 1)
            self.ram = None
            self.seed_sent = self.authorized = self.bmw_authorized = False
            self.resident_baud_selected = False
            self.ready_at = protocol.time.monotonic() + self.authorization_delay
            self.busy_payloads.clear()
            self.uploaded.clear()
            self.checked.clear()
        reply = packet(command, offset, count, payload, status=0)
        if self.agent_fault == "reset_ack" and command == ord("X"):
            return b""  # Reset occurred, but the host cannot know that from silence.
        if self.agent_fault == "finalize_ack" and command == ord("F"):
            return b""
        if self.agent_fault == "finalize_crc" and command == ord("F"):
            reply = reply[:-1] + bytes((reply[-1] ^ 1,))
        if self.agent_fault == "read_crc" and command == ord("R"):
            reply = reply[:-1] + bytes((reply[-1] ^ 1,))
        return reply


@pytest.fixture(params=("MS42", "MS43"))
def agent_workflow(request, monkeypatch):
    family = request.param
    root = Path(os.environ.get("MS41_TEST_DATA_ROOT", Path(__file__).resolve().parents[2] / "_shared"))
    name, digest = REFERENCES[family]
    path = root / ("REF_" + family) / name
    if not path.is_file():
        pytest.skip("private exact MS42/MS43 reference image unavailable")
    image = path.read_bytes()
    assert sha256(image).hexdigest() == digest
    clock = [0.0]
    monkeypatch.setattr(protocol.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(protocol.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    monkeypatch.setattr(ds2, "_import_d2xx_serial", lambda: pytest.fail("No real driver allowed"))
    if ds2.serial is not None:
        monkeypatch.setattr(ds2.serial, "Serial", lambda **_: pytest.fail("No real port allowed"))
    peer = AgentPeer(family, image, port="FAKE_RAM_WORKFLOW_ONLY", two_stop=False, baudrate=9600)
    link = ds2.DS2Interface("FAKE_CUSTOM_AGENT_ONLY", two_stop=False)
    link._ser = peer  # No open/enumeration call, including when the GUI fixture is active.
    link.transport_name = "injected"
    return protocol.MS42MS43ECU(link, family), peer


@pytest.mark.parametrize("authorization", ("ram", "bmw"))
@pytest.mark.parametrize("operation", ("read", "write"))
def test_agent_transfer_from_existing_authorization(agent_workflow, authorization, operation):
    ecu, peer = agent_workflow
    if authorization == "ram":
        peer.authorized = peer.seed_sent = True
    else:
        ecu.unlock()
        peer.authorization_delay = 0.4

    if operation == "read":
        with ecu.read_transfer(use_agent=True) as reader:
            assert reader.read_full() == peer.original
        assert not peer.erases and not peer.programs
    else:
        target = target_image(peer, "tune")
        with ecu.transfer():
            state = ecu.write_tune(target, use_ram=True, use_agent=True, verify=True, journal=Journal())
        assert state.completed and state.finalized and state.verified
        assert peer.image == expected_array(peer, target, "tune")

    fc = b"\xFC\x00\x00\x00"
    authorization_requests = [args for mode, command, args in peer.requests
                              if mode == "ds2" and command == 0x90 and not args.startswith(b"BMW")]
    assert authorization_requests == ([fc] if authorization == "ram" else [fc, fc, peer.profile["key"]])
    assert sum(mode == "ds2" and command == 0x0B for mode, command, _ in peer.requests) == 3
    assert peer.agent_launches == 1 and peer.restored and ecu.rate_confirmed


@pytest.mark.parametrize("payload", (b"", b"\x00\x00", b"\xDE\xAD\xBE\xEF"))
def test_invalid_authorized_reply_stops_before_upload(agent_workflow, monkeypatch, payload):
    ecu, peer = agent_workflow
    stock = peer.stock

    def respond(command, args):
        if command == 0x90:
            return response(payload)
        return stock(command, args)

    monkeypatch.setattr(peer, "stock", respond)
    with pytest.raises(ds2.DS2Error, match="authorization record"):
        with ecu.read_transfer(use_agent=True):
            pytest.fail("Invalid authorization must not start a read")
    assert sum(command == 0x90 for mode, command, args in peer.requests if mode == "ds2") == 1
    assert peer.ram is None and peer.agent_launches == 0 and not peer.erases and not peer.programs


@pytest.mark.parametrize("operation", ("read_full", "read_tune", "write_full", "write_tune", "write_boot"))
def test_individual_record_supports_complete_agent_transfers(agent_workflow, operation):
    ecu, peer = agent_workflow
    # Use synthetic credentials rather than publishing a physical ECU's data.
    record = bytes.fromhex("DE AD BE EF 11 22 33 44")
    peer.image[0x3F5C:0x3F64] = record
    peer.original = bytes(peer.image)
    assert ecu.read_memory(0x3F5C, 8) == b"\xFF" * 8
    if operation.startswith("read"):
        with ecu.read_transfer(use_agent=True) as reader:
            result = getattr(reader, operation)()
        assert result == (peer.original if operation == "read_full" else images.extract_calibration(peer.original))
        assert not peer.erases and not peer.programs
    else:
        mode = "tune" if operation == "write_tune" else "full"
        target = bytearray(boot_target(peer) if operation == "write_boot" else target_image(peer, mode))
        if operation == "write_boot":
            target[0x3C4A:0x3C50] = bytes(6)
        journal = Journal()
        with ecu.transfer():
            state = getattr(ecu, "write_" + mode)(bytes(target), use_ram=True, use_agent=True,
                                                 verify=True, journal=journal,
                                                 **({"write_boot": True} if operation == "write_boot" else {}))
        expected = expected_array(peer, target, mode)
        if operation == "write_boot":
            expected[:0x4000], expected[0x8000:0x10000] = target[:0x4000], target[0x8000:0x10000]
            expected[0x3C4A:0x3C50] = peer.original[0x3C4A:0x3C50]
            assert any(event == "identity_grafted" for event, _ in journal.events)
            assert [sector for sector in peer.erases if sector < 4] == [3, 0]
        else:
            assert all(sector >= 4 for sector in peer.erases)
        assert peer.image == expected
        assert state.completed and state.finalized and state.verified
    assert ecu._resident_signature_matches is (None if operation == "write_boot" else True)
    authorization = [(command, args) for mode, command, args in peer.requests
                     if mode == "ds2" and command in (0x0B, 0x90)]
    assert [command for command, _ in authorization] == [0x0B, 0x0B, 0x0B, 0x90, 0x90]
    assert authorization[1:] == [(0x0B, b"\x00"), (0x0B, b"\xFF"),
                                 (0x90, b"\xFC\x00\x00\x00"), (0x90, record[4:])]
    assert peer.measurement_reads == [tuple(range(0x3F5C, 0x3F64))]
    assert not peer.selected_measurements and peer.agent_launches == 1 and peer.restored


@pytest.mark.parametrize("agent_workflow", ("MS42",), indirect=True)
@pytest.mark.parametrize("fault", ("setup_reject", "setup_payload", "setup_lost", "setup_ff_payload",
                                  "read_reject", "read_short", "read_mismatch", "read_ff",
                                  "cleanup_reject", "cleanup_payload", "cleanup_ff"))
def test_record_failure_uses_stock_only_after_confirmed_cleanup(agent_workflow, monkeypatch, fault):
    ecu, peer = agent_workflow
    peer.image[0x3F5C:0x3F64] = bytes.fromhex("DE AD BE EF 11 22 33 44")
    original, stock = bytes(peer.image), peer.stock

    def respond(command, args):
        reply = stock(command, args)
        if command == 0x0B:
            if args[0] == 1 and fault.startswith("setup_"):
                if fault == "setup_ff_payload":
                    return response(b"\x00", status=0xFF)
                return (b"" if fault == "setup_lost" else response(b"\x00") if fault == "setup_payload"
                        else response(status=0xB0))
            if args == b"\x00" and fault.startswith("read_"):
                if fault == "read_ff":
                    return response(status=0xFF)
                return (response(status=0xA2) if fault == "read_reject" else response(bytes(8))
                        if fault == "read_mismatch" else response(bytes(7)))
            if args == b"\xFF" and fault.startswith("cleanup_"):
                if fault == "cleanup_ff":
                    return response(status=0xFF)
                return response(b"\x00") if fault == "cleanup_payload" else response(status=0xB0)
        return reply

    monkeypatch.setattr(peer, "stock", respond)
    if fault.startswith("cleanup_") or fault == "read_mismatch":
        with pytest.raises(ds2.DS2Error):
            with ecu.read_transfer(use_agent=True):
                pytest.fail("Uncertain cleanup or a pending challenge must stop")
        assert not ecu.rate_confirmed
    else:
        with ecu.read_transfer(use_agent=True) as reader:
            assert reader is ecu
            assert reader.read_tune() == images.extract_calibration(original)
        assert ecu.rate_confirmed
    measurement_requests = [request for request in peer.requests if request[:2] == ("ds2", 0x0B)]
    assert measurement_requests[-1] == ("ds2", 0x0B, b"\xFF")
    assert sum(mode == "ds2" and command == 0x90 for mode, command, _ in peer.requests) == (
        1 if fault == "read_mismatch" else 0)
    assert not peer.selected_measurements and peer.ram is None
    assert peer.agent_launches == 0 and not peer.erases and not peer.programs
    assert bytes(peer.image) == original
    assert not any(command == 0x90 and args != b"\xFC\x00\x00\x00"
                   for mode, command, args in peer.requests if mode == "ds2")


@pytest.mark.parametrize("known_pair", (True, False))
def test_boot_only_measurement_rejection_preserves_known_entry(agent_workflow, monkeypatch, known_pair):
    ecu, peer = agent_workflow
    if not known_pair:
        peer.image[0x3F5C:0x3F64] = bytes.fromhex("DE AD BE EF 11 22 33 44")
        peer.original = bytes(peer.image)
    stock = peer.stock

    def respond(command, args):
        return response(status=0xFF) if command == 0x0B else stock(command, args)

    monkeypatch.setattr(peer, "stock", respond)
    if known_pair:
        with ecu.read_transfer(use_agent=True) as reader:
            assert reader.read_full() == peer.original
        assert peer.restored
    else:
        with pytest.raises(ds2.DS2Error, match="Unexpected RAM authorization challenge"):
            with ecu.read_transfer(use_agent=True):
                pytest.fail("An unavailable measurement service cannot supply an unknown key")
        assert sum(mode == "ds2" and command == 0x90 for mode, command, _ in peer.requests) == 1
        assert peer.ram is None
    assert sum(mode == "ds2" and command == 0x0B for mode, command, _ in peer.requests) == 1
    assert not peer.erases and not peer.programs


@pytest.mark.parametrize("mode", ("full", "tune"))
def test_complete_agent_read_restores_stock_diagnostics(agent_workflow, mode):
    ecu, peer = agent_workflow
    progress = []
    with ecu.read_transfer(use_agent=True) as reader:
        assert isinstance(reader, agent.AgentReader)
        result = getattr(reader, "read_" + mode)(progress_cb=lambda *args: progress.append(args))
    expected = peer.original if mode == "full" else images.extract_calibration(peer.original)
    assert result == expected
    requests = [(offset, count) for command, offset, count, _data in peer.agent_requests if command == ord("R")]
    start = 0 if mode == "full" else peer.layout.calibration_offset
    assert requests == [(start + offset, min(1024, len(expected) - offset)) for offset in range(0, len(expected), 1024)]
    assert progress[-1][:2] == (len(expected), len(expected))
    assert peer.agent_launches == 1 and peer.restored and not ecu.baud_uncertain
    assert peer.upload_readbacks == [(0, 150)]
    assert ecu.baud == 9600 and peer.parity == "E"
    assert not peer.erases and not peer.programs


@pytest.mark.parametrize("mode", ("full", "tune"))
def test_complete_agent_write_preserves_boot_and_finalizes_before_reset(agent_workflow, mode):
    ecu, peer = agent_workflow
    target, journal = target_image(peer, mode), Journal()
    with ecu.transfer():
        state = getattr(ecu, "write_" + mode)(target, use_ram=True, use_agent=True, verify=True, journal=journal)
    assert state.completed and state.finalized and state.verified
    assert peer.upload_readbacks == [(0, 150)]
    assert peer.image == expected_array(peer, target, mode)
    assert peer.image[:0x10000] == peer.original[:0x10000]
    assert peer.agent_finalizations == 1 and not peer.finalizations and peer.restored and not ecu.can_retry_write
    assert any(len(data) > 248 for _offset, data in peer.programs)
    assert all(len(data) <= 1024 for _offset, data in peer.programs)
    assert all(sector >= 4 for sector in peer.erases)
    assert peer.agent_requests[0][:3] == (ord("I"), 0, 4)
    assert journal.events[-1][1]["outcome"] == "success"
    assert all(command != 0x75 for _mode, command, _args in peer.requests)


@pytest.mark.parametrize("fault", ("identity", "upload_ready_missing", "upload_ready_wrong"))
@pytest.mark.parametrize("operation", ("read", "write"))
def test_agent_entry_failure_sends_no_erase_or_program(agent_workflow, fault, operation):
    ecu, peer = agent_workflow
    peer.agent_fault = fault
    with pytest.raises(ds2.DS2Error):
        if operation == "read":
            with ecu.read_transfer(use_agent=True):
                pytest.fail("An unverified agent cannot be exposed to the caller")
        else:
            with ecu.transfer():
                ecu.write_tune(target_image(peer, "tune"), use_ram=True, use_agent=True, journal=Journal())
    assert not peer.erases and not peer.programs
    assert not any(command in (ord("E"), ord("P")) for command, *_ in peer.agent_requests)
    assert peer.agent_launches == 1
    if fault != "identity":
        assert not peer.agent_requests
    assert not peer.restored
    assert bytes(peer.image) == peer.original
    assert not ecu.can_retry_write


@pytest.mark.parametrize("position", ("first", "middle", "last"))
@pytest.mark.parametrize("operation", ("read", "write"))
def test_corrupt_upload_stops_before_agent_body_or_flash(agent_workflow, position, operation):
    from engines.ms42_ms43.payload import VERIFY_PREFIX_SIZE, payload

    ecu, peer = agent_workflow
    size = len(payload(ecu.family))
    peer.upload_corrupt_offset = {"first": VERIFY_PREFIX_SIZE, "middle": size // 2, "last": size - 1}[position]
    with pytest.raises(ds2.DS2Error, match="upload CRC failed"):
        if operation == "read":
            with ecu.read_transfer(use_agent=True):
                pytest.fail("A corrupted agent cannot be exposed to the caller")
        else:
            with ecu.transfer():
                ecu.write_tune(target_image(peer, "tune"), use_ram=True, use_agent=True, journal=Journal())
    assert peer.verifier_launches == 1 and peer.agent_launches == 0
    assert peer.upload_readbacks == [(0, 150)]
    assert not peer.agent_requests and not peer.erases and not peer.programs and not peer.restored
    assert bytes(peer.image) == peer.original
    assert not ecu.can_retry_write


def test_lost_program_ack_is_confirmed_by_readback_without_replaying(agent_workflow):
    ecu, peer = agent_workflow
    peer.drop_next_program = True
    journal = Journal()
    with ecu.transfer():
        state = ecu.write_tune(target_image(peer, "tune"), use_ram=True, use_agent=True, journal=journal)
    assert peer.lost_program and peer.programs.count(peer.lost_program) == 1
    assert state.completed and state.finalized and not ecu.can_retry_write
    assert peer.agent_launches == 1 and peer.restored
    assert not peer.finalizations and peer.agent_finalizations == 1
    reconciled = [fields for event, fields in journal.events if event == "program_confirmed_by_readback"]
    assert reconciled == [{"address": peer.lost_program[0], "length": len(peer.lost_program[1])}]
    assert journal.events[-1][1]["outcome"] == "success"
    assert peer.image == expected_array(peer, target_image(peer, "tune"), "tune")


def test_verify_failure_does_not_finalize_or_reset(agent_workflow):
    ecu, peer = agent_workflow
    peer.corrupt_verify = True
    with pytest.raises(protocol.FlashInterrupted, match="Readback differs"):
        with ecu.transfer():
            ecu.write_tune(target_image(peer, "tune"), use_ram=True, use_agent=True, verify=True, journal=Journal())
    assert not ecu.last_write.completed and not ecu.last_write.verified
    assert peer.mode == "agent" and not peer.finalizations and not peer.agent_finalizations
    assert not any(command == ord("X") for command, *_ in peer.agent_requests)


@pytest.mark.parametrize("mode", ("full", "tune"))
def test_gui_read_runs_actual_worker_backend_and_archives_checksum(
        agent_workflow, window, worker_runner, monkeypatch, mode):
    from PyQt5.QtWidgets import QMessageBox

    ecu, peer = agent_workflow
    desktop_connect(window, ecu.family)
    window._ds2 = ecu
    window.chk_force_slow_ds2.setChecked(False)
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Yes)
    monkeypatch.setattr(window, "_offer_additional_read_copy", lambda *_: None)
    window._on_read(mode)
    assert len(worker_runner.results) == 1 and worker_runner.results[0][0]
    expected = peer.original if mode == "full" else images.extract_calibration(peer.original)
    assert worker_runner.results[0][1] == expected
    assert peer.agent_launches == 1 and peer.restored
    entry, = window._backup_mgr.entries
    assert entry.source == "ECU RAM agent read"
    assert entry.cs_ok is True
    assert Path(entry.path).read_bytes() == expected


@pytest.mark.parametrize("mode", ("full", "tune"))
def test_gui_write_runs_actual_worker_and_agent_preserving_default_boot(
        agent_workflow, window, worker_runner, monkeypatch, mode):
    from PyQt5.QtWidgets import QMessageBox

    ecu, peer = agent_workflow
    target = target_image(peer, mode)
    desktop_connect(window, ecu.family)
    window._ds2 = ecu
    window.chk_force_slow_ds2.setChecked(False)
    window.chk_bootloader_write.setChecked(False)
    window.chk_backup_before_write.setChecked(False)
    window.chk_verify.setChecked(True)
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Yes)
    monkeypatch.setattr(window, "_prewrite_battery_notice", lambda: "12.8 V")
    monkeypatch.setattr(ds2_fast_safety, "OperationJournal", lambda *_args, **_kwargs: Journal())
    completed = []
    monkeypatch.setattr(window, "_show_flash_complete", lambda *args: completed.append(args))
    window._ms42_ms43_write(target, "offline.bin", mode)
    assert worker_runner.results == [(True, None)]
    assert ecu.last_write.completed and ecu.last_write.verified and ecu.last_write.finalized
    assert peer.image == expected_array(peer, target, mode)
    assert peer.agent_requests[0][:3] == (ord("I"), 0, 4)
    assert peer.restored and len(completed) == 1


@pytest.mark.parametrize("use_ram,use_agent", [(False, False), (True, False), (False, True)])
def test_boot_option_cannot_silently_fall_back_to_existing_write_routes(agent_workflow, use_ram, use_agent):
    ecu, peer = agent_workflow
    with pytest.raises((ValueError, ds2.DS2Error)):
        ecu.write_full(target_image(peer, "full"), use_ram=use_ram, use_agent=use_agent,
                       write_boot=True, journal=Journal())
    assert not peer.erases and not peer.programs and not peer.agent_launches


def test_unmatched_service_does_not_fall_back_for_boot_write(agent_workflow):
    ecu, peer = agent_workflow
    peer.unsupported = "boot"
    with pytest.raises((ValueError, ds2.DS2Error)):
        ecu.write_full(target_image(peer, "full"), use_ram=True, use_agent=True,
                       write_boot=True, journal=Journal())
    assert not peer.erases and not peer.programs and not peer.agent_launches


def boot_target(peer):
    target = bytearray(target_image(peer, "full"))
    # Both executable boot sectors change, outside the compact native ABI check.
    # The file also differs in retained sectors, which must never be copied.
    for offset in (0x3000, 0x9000, 0x4500, 0x6500):
        target[offset] ^= 1
    return images.correct_checksums(bytes(target), peer.family)[0]


@pytest.mark.parametrize("verify", (False, True))
def test_explicit_boot_update_verifies_code_preserves_retained_sectors_and_finalizes(agent_workflow, verify):
    ecu, peer = agent_workflow
    target, journal = boot_target(peer), Journal()
    with ecu.transfer():
        state = ecu.write_full(target, use_ram=True, use_agent=True, write_boot=True,
                               verify=verify, journal=journal)
    expected = expected_array(peer, target, "full")
    expected[:0x4000] = target[:0x4000]
    expected[0x8000:0x10000] = target[0x8000:0x10000]
    assert peer.image == expected
    assert peer.image[0x4000:0x8000] == peer.original[0x4000:0x8000]
    assert [sector for sector in peer.erases if sector < 4] == [3, 0]
    assert peer.agent_requests[0][:3] == (ord("I"), 1, 4)
    assert peer.boot_at_reset == bytes(expected[:0x10000])
    assert ("boot_verified", {"sectors": [3, 0], "length": 0xC000}) in journal.events
    assert state.completed and state.finalized and state.verified is verify
    assert peer.restored and peer.agent_finalizations == 1 and not peer.finalizations


def test_identical_boot_code_is_not_erased_even_when_option_selected(agent_workflow):
    ecu, peer = agent_workflow
    with ecu.transfer():
        state = ecu.write_full(target_image(peer, "full"), use_ram=True, use_agent=True,
                               write_boot=True, journal=Journal())
    assert state.completed and all(sector >= 4 for sector in peer.erases)
    assert peer.boot_at_reset == peer.original[:0x10000]


def test_boot_verification_is_mandatory_when_regular_verify_is_off(agent_workflow):
    ecu, peer = agent_workflow
    peer.agent_fault = "boot_readback"
    with pytest.raises(protocol.FlashInterrupted, match="Boot readback differs"):
        with ecu.transfer():
            ecu.write_full(boot_target(peer), use_ram=True, use_agent=True, write_boot=True,
                           verify=False, journal=Journal())
    assert not ecu.last_write.completed and ecu.can_retry_write
    assert peer.mode == "agent" and not peer.restored and not peer.finalizations and not peer.agent_finalizations
    assert [sector for sector in peer.erases if sector < 4] == [3]
    assert not any(command == ord("X") for command, *_ in peer.agent_requests)


@pytest.mark.parametrize("fault", ("checksum", "service_abi"))
def test_invalid_boot_target_never_launches_agent_or_erases(agent_workflow, fault):
    ecu, peer = agent_workflow
    target = bytearray(boot_target(peer))
    if fault == "checksum":
        target[0x3C24] ^= 1
    else:
        target[peer.profile["signature_spans"][0][0]] ^= 1
        target = images.correct_checksums(bytes(target), peer.family)[0]
    with pytest.raises((ValueError, ds2.DS2Error)):
        ecu.write_full(target, use_ram=True, use_agent=True, write_boot=True,
                       correct_checksum=False, journal=Journal())
    assert not peer.agent_launches and not peer.erases and not peer.programs


def test_gui_boot_option_reaches_real_worker_and_preserves_retained_data(
        agent_workflow, window, worker_runner, monkeypatch):
    from PyQt5.QtWidgets import QInputDialog, QMessageBox

    ecu, peer = agent_workflow
    target = boot_target(peer)
    desktop_connect(window, ecu.family)
    window._ds2 = ecu
    window.chk_force_slow_ds2.setChecked(False)
    window.chk_bootloader_write.setChecked(True)
    window.chk_backup_before_write.setChecked(False)
    window.chk_verify.setChecked(False)
    monkeypatch.setattr(QInputDialog, "getText", lambda *_: ("WRITE BOOT", True))
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Yes)
    monkeypatch.setattr(window, "_prewrite_battery_notice", lambda: "12.8 V")
    monkeypatch.setattr(ds2_fast_safety, "OperationJournal", lambda *_args, **_kwargs: Journal())
    monkeypatch.setattr(window, "_show_flash_complete", lambda *_: None)
    window._ms42_ms43_write(target, "offline-boot.bin", "full")
    assert worker_runner.results == [(True, None)]
    assert ecu.last_write.completed and peer.agent_requests[0][:3] == (ord("I"), 1, 4)
    assert peer.image[:0x4000] == target[:0x4000]
    assert peer.image[0x8000:0x10000] == target[0x8000:0x10000]
    assert peer.image[0x4000:0x8000] == peer.original[0x4000:0x8000]
    assert [sector for sector in peer.erases if sector < 4] == [3, 0]


def test_lost_reset_ack_prevents_retry_and_further_commands(agent_workflow):
    ecu, peer = agent_workflow
    peer.agent_fault = "reset_ack"
    with pytest.raises(protocol.ProgrammingCompletedError):
        with ecu.transfer():
            ecu.write_tune(target_image(peer, "tune"), use_ram=True, use_agent=True, journal=Journal())
    assert ecu._pending_image is ecu._pending_writer is None
    assert ecu.baud_uncertain and not ecu.can_retry_write
    assert ecu.last_write.completed and ecu.last_write.finalized
    assert not peer.finalizations and peer.agent_finalizations == 1
    request_count = len(peer.requests)
    with pytest.raises(protocol.DS2Error, match="No incomplete write"):
        ecu.retry_write(journal=Journal())
    with pytest.raises(protocol.BaudTransitionError):
        ecu.read_voltage()
    assert len(peer.requests) == request_count


def test_boot_retry_preserves_explicit_permission_and_immutable_target(agent_workflow, monkeypatch):
    ecu, peer = agent_workflow
    target = boot_target(peer)
    peer.agent_fault = "boot_program_ack"
    original_request = peer.agent_request

    def lose_readback_after_program(command, offset, count, data):
        response = original_request(command, offset, count, data)
        return b"" if peer.lost_program and command == ord("R") else response

    # A lost program reply alone is recoverable; persistent read loss keeps the commit unknown.
    monkeypatch.setattr(peer, "agent_request", lose_readback_after_program)
    with pytest.raises(protocol.FlashInterrupted):
        with ecu.transfer():
            ecu.write_full(target, use_ram=True, use_agent=True, write_boot=True, journal=Journal())
    retained = ecu._pending_writer
    assert retained.active and retained.write_boot and ecu._pending_write_boot
    assert ecu._pending_image == target and ecu.can_retry_write
    assert peer.lost_program and peer.lost_program[0] < 0x10000
    assert peer.programs.count(peer.lost_program) == 1
    assert not peer.restored and not peer.finalizations and not peer.agent_finalizations
    monkeypatch.setattr(peer, "agent_request", original_request)
    with ecu.transfer():
        state = ecu.retry_write(journal=Journal())
    assert state.completed and state.finalized and peer.agent_launches == 1
    identities = [(offset, count) for command, offset, count, _data in peer.agent_requests if command == ord("I")]
    assert identities == [(1, 4), (1, 4)]
    assert peer.image[:0x4000] == target[:0x4000]
    assert peer.image[0x8000:0x10000] == target[0x8000:0x10000]
    assert peer.image[0x4000:0x8000] == peer.original[0x4000:0x8000]


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_ms41_identity_preference_survives_other_family_controls(window, family):
    window.chk_boot_preserve_identity.setChecked(False)
    desktop_connect(window, family)
    assert window.chk_boot_preserve_identity.isChecked()
    assert not window.chk_boot_preserve_identity.isEnabled()
    # Ordinary route/control refreshes must not replace the saved MS41 choice.
    window._ms42_ms43_boot_controls()
    window._ds2 = None
    window._restore_ms41_labels()
    assert not window.chk_boot_preserve_identity.isChecked()
    assert not hasattr(window, "_ms41_boot_identity_choice")
