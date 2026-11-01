"""Application write workflow over a strict byte-pipe fake; never real hardware.

Native opcode/flash/timeout proof is in the canonical C167 emulator tests.
This peer tests production orchestration, packet shapes, retained failures and
the exact-image plan without substituting fake success for native evidence.
"""
from hashlib import sha256
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import ds2
import ds2_fast_safety
import ms42_ms43 as protocol
import ms42_ms43_image as images
from tests.test_ms42_ms43 import Journal
from tests.test_ms42_ms43_ram_workflow import Peer, REFERENCES, response
from tests.test_ms42_ms43_desktop import connect as desktop_connect
from tests.test_ms42_ms43_desktop import window as window, worker_runner as worker_runner


SECTORS = ((0, 0x4000), (0x4000, 0x6000), (0x6000, 0x8000), (0x8000, 0x10000),
           *((start, start + 0x10000) for start in range(0x10000, 0x80000, 0x10000)))
CONTRACTS = {
    "MS42": dict(program=(8, 9, 10, 4, 5, 6), calibration=7,
                 program_marker=0x50000, calibration_marker=0x4FFF0,
                 primary=0x4FFD6, secondary=0x7FFE6,
                 descriptors=(0x502D6, 0x502DE, 0x502E2)),
    "MS43": dict(program=(8, 9, 4, 5, 6, 7), calibration=10,
                 program_marker=0x6FFF0, calibration_marker=0x7FFF0,
                 primary=0x7FFD0, secondary=0x4FFE6,
                 descriptors=(0x6ED46, 0x6ED3A, 0x6ED3E)),
}
# Every ID case uses the same modeled 512 KiB x16 bottom-boot contract.
# These are host-policy cases, not qualification of physical chips or layouts.
COMPATIBLE_MS43_FLASH_IDS = (
    pytest.param(1, 0x22AB, id="amd"),
    pytest.param(0x20, 0xD6, id="st"),
    pytest.param(4, 0x22AB, id="fujitsu"),
    pytest.param(0xC2, 0x22AB, id="macronix"),
    pytest.param(0x7E, 0x1234, id="unlisted-modeled-chip"),
)


class WritePeer(Peer):
    """Fixed x16 bottom-boot effects and acknowledgements, not a CPU emulator."""

    def __init__(self, family, image, **kwargs):
        super().__init__(family, bytearray(image), **kwargs)
        self.original = bytes(image)
        self.contract = CONTRACTS[family]
        self.erases = []
        self.programs = []
        self.lost_program = None
        self.drop_next_program = False
        self.reject_next_program = False
        self.finalizations = []
        self.validation_busy = 1
        self.validation_mask = 0
        self.finalize_result = 1
        self.missing_marker = None
        self.corrupt_verify = False
        self.markers_at_reset = None
        self.writer_stage = bytearray(0x800)
        self.writer_jump = None
        self.write_overlay = None
        self.writer_installs = []
        self.flash_manufacturer, self.flash_device = 1, 0x22AB
        self.writer_fault = None
        self.bmw_authorized = False
        self.fc_statuses = []

    def loader(self, command, args):
        assert command != 0x75, "Command75 destroys the resident sector"
        if command != self.write_overlay:
            self.write_overlay = None
        if self.family == "MS43":
            setup = self.writer_setup(command, args)
            if setup is not None:
                return setup
        if command == 0x79 and self.reset_staged:
            self.markers_at_reset = tuple(bytes(self.image[self.contract[key]:self.contract[key] + 4])
                                          for key in ("program_marker", "calibration_marker"))
        if command == 0x71 and self.corrupt_verify and self.erases and not self.reset_staged:
            address = int.from_bytes(args[:3], "big") - 0x80000
            count = int.from_bytes(args[3:], "big") * 2
            mismatch = self.layout.calibration_offset + 0x100
            if address <= mismatch < address + count:
                original = self.image[mismatch]
                self.image[mismatch] ^= 1
                try:
                    return super().loader(command, args)
                finally:
                    self.image[mismatch] = original
        if command == 0x73:
            if self.family == "MS42":
                assert len(args) == 1 and args[0] < 11
                sector = args[0]
            else:
                assert self.write_overlay == command, "MS43 erase requires the adapted RAM overlay"
                assert len(args) == 4 and args[0] == 2
                address = int.from_bytes(args[1:], "big")
                sector = next(index for index, bounds in enumerate(SECTORS)
                              if bounds[0] == address)
            assert sector >= 4, "Boot/resident sectors must remain untouched"
            start, end = SECTORS[sector]
            self.image[start:end] = b"\xff" * (end - start)
            self.erases.append(sector)
            return response(stock=False)
        if command in (0x74, 0x7A):
            if self.family == "MS42":
                assert command == 0x74
                address, words = int.from_bytes(args[:3], "big"), args[3:]
                assert len(words) % 2 == 0
                data = b"".join(words[index:index + 2][::-1] for index in range(0, len(words), 2))
            else:
                assert self.write_overlay == command, "MS43 program requires the adapted RAM overlay"
                assert command == 0x7A and args[0] == 2
                address, data = int.from_bytes(args[1:4], "big"), args[4:]
            assert address >= 0x10000 and address % 2 == 0
            assert 2 <= len(data) <= 248 and len(data) % 2 == 0
            assert address + len(data) <= 0x80000
            if self.reject_next_program:
                self.reject_next_program = False
                return response(stock=False, status=0xD2)
            old = self.image[address:address + len(data)]
            assert all((new & ~prior) == 0 for prior, new in zip(old, data)), "Programming needs erase"
            self.image[address:address + len(data)] = data
            self.programs.append((address, bytes(data)))
            if self.drop_next_program and self.erases:
                self.drop_next_program = False
                self.lost_program = (address, bytes(data))
                return b""  # The commit occurred; the host must not replay it.
            return response(stock=False)
        if command == 2 and self.identities >= 2:
            assert not args
            return response(self.profile["identity"], stock=False)
        reply = super().loader(command, args)
        if command == 0x79 and self.mode == "ds2":
            self.bmw_authorized = False
        return reply

    def writer_setup(self, command, args):
        """Validate staging/verification/cache orchestration; native tests execute it."""
        if command == 0x78 and args != bytes.fromhex("0000B748B7B7"):
            offset, data = int.from_bytes(args[:2], "big"), args[2:]
            assert not offset & 1 and not len(data) & 1 and 2 <= len(data) <= 250
            if offset == 0:
                assert data == bytes.fromhex("EA0000E4")
                self.writer_jump = data
            else:
                assert 0x400 <= offset < 0x800 and offset + len(data) <= 0x800
                self.writer_stage[offset:offset + len(data)] = data
            return response(stock=False)
        if command == 0x71 and not self.reset_staged:
            address, count = int.from_bytes(args[:3], "big"), int.from_bytes(args[3:], "big") * 2
            if 0xE400 <= address < 0xE800:
                data = self.writer_stage[address - 0xE000:address - 0xE000 + count]
                if self.writer_fault == "readback":
                    data = bytes((data[0] ^ 1,)) + data[1:]
            elif address == 0xF902 and self.writer_jump:
                assert count == 4
                data = self.writer_jump
            else:
                return None
            return response(b"".join(data[i:i + 2][::-1] for i in range(0, count, 2)), stock=False)
        if command == 0x79 and self.writer_jump and not self.reset_staged:
            from ms43_resident_writer import bootstrap

            assert not args
            installed = next(candidate for candidate in (0x73, 0x7A)
                             if self.writer_stage[0x400:0x400 + len(bootstrap(candidate))] == bootstrap(candidate))
            self.writer_installs.append(installed)
            self.write_overlay = installed
            if self.writer_fault == "checksum":
                return response(stock=False, status=0xB6)
            return response(self.flash_manufacturer.to_bytes(2, "little")
                            + self.flash_device.to_bytes(2, "little"), stock=False)
        return None

    def stock(self, command, args):
        if command == 0x0A:
            assert not args and self.restored
            if self.validation_busy:
                self.validation_busy -= 1
                return response(status=0xA1)
            return response(bytes((self.validation_mask,)))
        if command == 0x90 and args.startswith(b"BMW"):
            assert len(args) == 4
            if protocol.time.monotonic() < self.ready_at:
                return response(status=0xA1)
            # Both families retain BMW authorization across host reconnects.
            self.bmw_authorized = True
            return response(b"\x00")
        if command == 0x90 and args == b"\xFC\x00\x00\x00" and self.bmw_authorized:
            self.bmw_authorized = False
            self.ready_at = protocol.time.monotonic() + self.authorization_delay
            self.fc_statuses.append(0xA2)
            return response(status=0xA2)
        if command == 0x07 and args[0] != 0:
            operation = args[0]
            assert operation in (2, 6, 0x0F)
            address = int.from_bytes(args[1:4], "big")
            assert args[4] == len(args[5:])
            if operation == 0x0F:
                self.finalizations.append(address)
                if self.erases and self.restored and self.finalize_result == 1:
                    # Explicit protocol outcome fixture; native validation and
                    # marker publication are separately executed in C167 tests.
                    if set(self.contract["program"]).issubset(self.erases):
                        start = self.contract["program_marker"]
                        self.image[start:start + 4] = bytes.fromhex("90482442")
                    if self.contract["calibration"] in self.erases:
                        start = self.contract["calibration_marker"]
                        self.image[start:start + 4] = bytes.fromhex("42244890")
                    if self.missing_marker:
                        start = self.contract[self.missing_marker + "_marker"]
                        self.image[start:start + 4] = b"\xff" * 4
            return response(bytes(5) + bytes((self.finalize_result,)))
        reply = super().stock(command, args)
        if command == 0x90 and args == b"\xFC\x00\x00\x00":
            self.fc_statuses.append(reply[2] if reply else None)
        return reply


@pytest.fixture(params=("MS42", "MS43"))
def write_workflow(request, monkeypatch):
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
        peer = WritePeer(family, image, **kwargs)
        peers.append(peer)
        return peer

    link = ds2.DS2Interface("FAKE_RAM_WORKFLOW_ONLY", serial_factory=factory, two_stop=False)
    link.open()
    assert len(peers) == 1
    return protocol.MS42MS43ECU(link, family), peers[0]


def target_image(peer, mode):
    target = bytearray(peer.original)
    target[peer.layout.calibration_offset + 0x100] ^= 1
    corrected = images.correct_checksums(bytes(target), peer.family)[0]
    if mode == "tune":
        start = peer.layout.calibration_offset
        return corrected[start:start + peer.layout.partial_size]
    return corrected


def expected_array(peer, target, mode):
    contract, profile = peer.contract, peer.layout
    expected = bytearray(peer.original)
    if mode == "full":
        for sector in contract["program"]:
            start, end = SECTORS[sector]
            expected[start:end] = b"\xff" * (end - start)
        for _address, source, length in profile.program_write_regions:
            expected[source:source + length] = target[source:source + length]
        backup = bytearray()
        for pointer, count in zip(contract["descriptors"], (12, 7, 7)):
            source = int.from_bytes(peer.original[pointer:pointer + 4], "little") & 0x7FFFF
            backup.extend(peer.original[source:source + count])
        expected[contract["secondary"]:contract["secondary"] + 26] = backup
        marker = contract["program_marker"]
        expected[marker:marker + 4] = bytes.fromhex("90482442")
    start, end = SECTORS[contract["calibration"]]
    expected[start:end] = b"\xff" * (end - start)
    source = profile.calibration_offset if mode == "full" else 0
    expected[profile.calibration_offset:profile.calibration_offset + profile.calibration_write_length] = (
        target[source:source + profile.calibration_write_length])
    marker = contract["calibration_marker"]
    expected[marker:marker + 4] = bytes.fromhex("42244890")
    return expected


@pytest.mark.parametrize("mode", ("tune", "full"))
def test_complete_ram_write_verifies_restores_and_uses_native_finalization(write_workflow, mode):
    ecu, peer = write_workflow
    target = target_image(peer, mode)
    progress, journal = [], Journal()
    with ecu.transfer():
        state = getattr(ecu, "write_" + mode)(target, use_ram=True, verify=True,
            journal=journal, progress_cb=lambda *event: progress.append(event))
    assert state.completed and state.finalized and state.verified
    assert ecu._pending_image is None and ecu._pending_writer is None
    assert peer.image == expected_array(peer, target, mode)
    assert peer.image[:0x10000] == peer.original[:0x10000]
    expected_sectors = (*peer.contract["program"], peer.contract["calibration"]) if mode == "full" else (
        peer.contract["calibration"],)
    assert peer.erases == list(expected_sectors)
    assert peer.restored and ecu.rate_confirmed and ecu.transport._ser is peer
    assert peer.markers_at_reset[1] == b"\xff" * 4
    assert peer.markers_at_reset[0] == (b"\xff" * 4 if mode == "full" else bytes.fromhex("90482442"))
    assert (peer.baudrate, peer.parity, peer.stopbits) == (9600, "E", 1)
    assert all(command != 0x75 for _mode, command, _args in peer.requests)
    assert not any(wire == "ds2" and command == 7 and args[0] in (2, 6)
                   for wire, command, args in peer.requests)
    assert peer.finalizations
    reset_index = next(index for index, request in enumerate(peer.requests) if request[:2] == ("resident", 0x79))
    assert any(index > reset_index and request[:2] == ("ds2", 0x0A)
               for index, request in enumerate(peer.requests))
    assert journal.closed and journal.events[-1][1]["outcome"] == "success"
    assert progress


@pytest.mark.parametrize("write_workflow", ["MS43"], indirect=True)
@pytest.mark.parametrize("mode", ("tune", "full"))
@pytest.mark.parametrize("next_operation", ("full_read_after_reconnect", "write"))
def test_ms43_completed_write_can_enter_ram_again_on_same_ecu(
        write_workflow, mode, next_operation):
    ecu, peer = write_workflow
    target = target_image(peer, mode)
    with ecu.transfer():
        state = getattr(ecu, "write_" + mode)(target, use_ram=True, verify=True, journal=Journal())
    assert state.completed and state.finalized and peer.bmw_authorized
    expected = bytes(expected_array(peer, target, mode))
    assert bytes(peer.image) == expected
    count, fc_count = len(peer.requests), len(peer.fc_statuses)
    erases, programs = len(peer.erases), len(peer.programs)
    # Supplied timing models the native nine-service reset countdown after A2.
    # Reconnecting the host does not reset the ECU's authorization state.
    peer.authorization_delay = 9.0
    started = protocol.time.monotonic()
    if next_operation == "full_read_after_reconnect":
        ecu = protocol.MS42MS43ECU(ecu.transport, "MS43")
        assert ecu._resident_signature_matches is None and peer.bmw_authorized
        with ecu.read_transfer() as reader:
            assert reader.read_source == "ECU RAM loader read"
            assert reader.read_full() == expected
        assert (len(peer.erases), len(peer.programs)) == (erases, programs)
        assert not peer.bmw_authorized
    else:
        assert ecu._resident_signature_matches is True
        with ecu.transfer():
            state = getattr(ecu, "write_" + mode)(target, use_ram=True, verify=True, journal=Journal())
        assert state.completed and state.verified and state.finalized
        assert len(peer.erases) > erases and len(peer.programs) > programs
        assert peer.bmw_authorized
    assert bytes(peer.image) == expected
    statuses = peer.fc_statuses[fc_count:]
    assert statuses == [0xA2, 0xA0]
    later_requests = peer.requests[count:]
    fc = [index for index, (mode, command, args) in enumerate(later_requests)
          if mode == "ds2" and command == 0x90 and args == b"\xFC\0\0\0"]
    assert len(fc) == 2
    assert any(mode == "ds2" and command == 0x06 and args == bytes.fromhex("0000F6B001")
               for mode, command, args in later_requests[fc[0] + 1:fc[1]])
    assert protocol.time.monotonic() - started >= 9.0
    subsequent = peer.requests[count:]
    assert subsequent.count(("ds2", 0x90, peer.profile["key"])) == 1
    assert subsequent.count(("ds2", 0x53, b"\xFC")) == 1
    probes = [args for wire, command, args in subsequent
              if wire == "ds2" and command == 6 and int.from_bytes(args[:4], "big") < 0xC000]
    expected_probes = ([address.to_bytes(4, "big") + bytes((length,))
                        for address, length in peer.profile["signature_spans"]]
                       if next_operation == "full_read_after_reconnect" else [])
    assert probes == expected_probes
    assert ecu._pending_image is None and ecu._pending_writer is None
    assert ecu.rate_confirmed and ecu.transport._ser is peer and peer.mode == "ds2"
    assert (peer.baudrate, peer.parity, peer.stopbits) == (9600, "E", 1)


@pytest.mark.parametrize("write_workflow", ["MS43"], indirect=True)
@pytest.mark.parametrize("mode", ("tune", "full"))
@pytest.mark.parametrize("manufacturer,device", COMPATIBLE_MS43_FLASH_IDS)
def test_ms43_compatible_contract_write_logs_ids_verifies_backups_and_finalizes(
        write_workflow, mode, manufacturer, device):
    ecu, peer = write_workflow
    peer.flash_manufacturer, peer.flash_device = manufacturer, device
    target, journal = target_image(peer, mode), Journal()
    with ecu.transfer():
        state = getattr(ecu, "write_" + mode)(target, use_ram=True, verify=True, journal=journal)
    assert state.completed and state.verified and state.finalized
    assert peer.image == expected_array(peer, target, mode)
    assert peer.image[:0x10000] == peer.original[:0x10000]
    sectors = (*peer.contract["program"], peer.contract["calibration"]) if mode == "full" else (
        peer.contract["calibration"],)
    assert peer.erases == list(sectors)
    if mode == "full":
        for key in ("primary", "secondary"):
            assert any(address == peer.contract[key] and len(data) == 26
                       for address, data in peer.programs)
    identified = [fields for event, fields in journal.events if event == "resident_flash_identified"]
    assert [entry["command"] for entry in identified] == peer.writer_installs
    assert all((entry["manufacturer"], entry["device"]) == (manufacturer, device) for entry in identified)
    assert 0 < peer.writer_installs.count(0x7A) < len(peer.programs)
    assert peer.finalizations == [peer.layout.calibration_offset]
    assert peer.restored and ecu.rate_confirmed and ecu.transport._ser is peer
    assert (peer.baudrate, peer.parity, peer.stopbits) == (9600, "E", 1)
    assert ecu._pending_image is None and ecu._pending_writer is None
    assert journal.closed and journal.events[-1][1]["outcome"] == "success"
    assert not any(wire == "ds2" and command == 7 and args[0] in (2, 6)
                   for wire, command, args in peer.requests)
    assert all(command != 0x75 for _wire, command, _args in peer.requests)


@pytest.mark.parametrize("mode", ("tune", "full"))
def test_lost_ram_program_reply_stops_then_explicit_retry_restarts_same_plan(write_workflow, mode):
    ecu, peer = write_workflow
    target = target_image(peer, mode)
    peer.drop_next_program = True
    journal = Journal()
    with pytest.raises(protocol.FlashInterrupted):
        with ecu.transfer():
            getattr(ecu, "write_" + mode)(target, use_ram=True, verify=True, journal=journal)
    assert peer.lost_program and peer.mode == "resident" and not peer.restored
    address, data = peer.lost_program
    assert peer.image[address:address + len(data)] == data
    assert peer.programs.count(peer.lost_program) == 1
    assert ecu._pending_image is not None and ecu._pending_writer.active
    retained = ecu._pending_writer
    assert journal.events[-1][1]["outcome"] == "commit_unknown"
    request_count = len(peer.requests)
    with pytest.raises(protocol.DS2Error, match="incomplete|retained"):
        getattr(ecu, "write_" + mode)(target, use_ram=True, journal=Journal())
    assert len(peer.requests) == request_count
    state = ecu.retry_write(journal=Journal())
    assert peer.requests[request_count][:2] == ("resident", 2)
    assert not retained.active and state.completed and state.verified
    assert peer.image == expected_array(peer, target, mode)
    assert ecu._pending_image is None and peer.restored


def test_force_slow_retains_existing_ds2_write_workflow(write_workflow):
    ecu, peer = write_workflow
    with ecu.transfer(force_slow=True):
        state = ecu.write_tune(target_image(peer, "tune"), use_ram=False, journal=Journal())
    assert state.completed
    assert all(mode == "ds2" for mode, _command, _args in peer.requests)
    assert not peer.erases and not peer.programs
    assert (peer.baudrate, peer.parity, peer.stopbits) == (9600, "E", 1)


@pytest.mark.parametrize("mode", ("tune", "full"))
def test_finalization_rejection_retains_original_identity_for_explicit_retry(write_workflow, mode):
    ecu, peer = write_workflow
    target = target_image(peer, mode)
    peer.finalize_result = 0
    with pytest.raises(protocol.FlashInterrupted):
        with ecu.transfer():
            getattr(ecu, "write_" + mode)(target, use_ram=True, verify=True, journal=Journal())
    assert not ecu.last_write.completed and not ecu.last_write.finalized
    assert peer.restored and peer.finalizations
    assert ecu._pending_image is not None and ecu._pending_writer is not None
    retained = ecu._pending_writer
    assert not retained.active and ecu.can_retry_write
    metadata = retained.metadata
    peer.finalize_result = 1
    count = len(peer.requests)
    with ecu.transfer():
        state = ecu.retry_write(journal=Journal())
    assert state.completed and state.finalized and state.verified
    assert retained.metadata == metadata
    assert peer.image == expected_array(peer, target, mode)
    assert ("ds2", 0x90, b"\xfc\x00\x00\x00") in peer.requests[count:]
    assert all(command != 0x75 for _wire_mode, command, _args in peer.requests)


def test_changed_service_signature_uses_existing_ds2_write_route(write_workflow):
    ecu, peer = write_workflow
    peer.unsupported = "boot"
    with ecu.transfer():
        state = ecu.write_tune(target_image(peer, "tune"), use_ram=True, journal=Journal())
    assert state.completed
    assert not peer.erases and not peer.programs
    assert all(mode == "ds2" for mode, _command, _args in peer.requests)


@pytest.mark.parametrize("mask", (1, 2, 4))
def test_native_validation_failure_never_requests_marker_finalization(write_workflow, mask):
    ecu, peer = write_workflow
    peer.validation_mask = mask
    with pytest.raises(protocol.FlashInterrupted):
        with ecu.transfer():
            ecu.write_full(target_image(peer, "full"), use_ram=True, journal=Journal())
    assert not peer.finalizations
    assert ecu.can_retry_write and not ecu.last_write.completed
    assert not ecu._pending_writer.active and ecu._pending_writer.metadata is not None


@pytest.mark.parametrize("marker", ("program", "calibration"))
def test_native_success_detail_without_both_flash_markers_is_not_complete(write_workflow, marker):
    ecu, peer = write_workflow
    peer.missing_marker = marker
    journal = Journal()
    with pytest.raises(protocol.FlashInterrupted):
        with ecu.transfer():
            ecu.write_full(target_image(peer, "full"), use_ram=True, journal=journal)
    assert peer.finalizations and peer.finalize_result == 1
    assert not ecu.last_write.completed and not ecu.last_write.finalized
    assert ecu.can_retry_write and ecu._pending_writer.metadata is not None
    assert journal.events[-1][1]["outcome"] == "commit_unknown"


def test_identity_snapshot_accepts_absent_descriptor_without_reading_end_of_rom(write_workflow):
    from ms42_ms43_resident import ResidentWriter

    ecu, peer = write_workflow
    # Native descriptor sentinel means an absent seven-byte identity component.
    descriptor = peer.contract["descriptors"][1]
    peer.image[descriptor:descriptor + 4] = b"\xff" * 4
    writer = ResidentWriter(ecu)
    writer.enter()
    count = len(peer.requests)
    writer._snapshot_identity(full=True)
    primary, make_primary, cal_valid = writer.metadata
    assert make_primary and cal_valid
    assert primary[12:19] == b"\xff" * 7
    for command_mode, command, args in peer.requests[count:]:
        assert command_mode == "resident" and command == 0x71
        address = int.from_bytes(args[:3], "big")
        assert address < 0xFFFFE
    assert not peer.erases and not peer.programs


def test_ram_readback_mismatch_does_not_reset_or_finalize(write_workflow):
    ecu, peer = write_workflow
    peer.corrupt_verify = True
    with pytest.raises(protocol.FlashInterrupted, match="Readback differs"):
        with ecu.transfer():
            ecu.write_tune(target_image(peer, "tune"), use_ram=True, verify=True, journal=Journal())
    assert peer.mode == "resident" and not peer.restored and not peer.finalizations
    assert not ecu.last_write.completed and not ecu.last_write.verified
    assert ecu._pending_writer.active and ecu._pending_image is not None
    assert ("resident", 0x78, bytes.fromhex("0000B748B7B7")) not in peer.requests


def test_retained_ram_write_cannot_continue_on_replaced_transport(write_workflow):
    ecu, peer = write_workflow
    peer.drop_next_program = True
    with pytest.raises(protocol.FlashInterrupted):
        with ecu.transfer():
            ecu.write_tune(target_image(peer, "tune"), use_ram=True, journal=Journal())
    count = len(peer.requests)
    ecu.transport._ser = SimpleNamespace(is_open=True)
    with pytest.raises(protocol.DS2Error, match="original retained connection"):
        ecu.retry_write(journal=Journal())
    assert len(peer.requests) == count


@pytest.mark.parametrize("fault,safe_to_restart", [
    ("authorization_timeout_seed", True), ("authorization_timeout_key", False),
    ("key_ack", False),
])
def test_ram_write_entry_failure_preserves_only_unconsumed_challenge(write_workflow, fault, safe_to_restart):
    ecu, peer = write_workflow
    peer.fault = fault
    if safe_to_restart:
        journal = Journal()
        with ecu.transfer():
            state = ecu.write_tune(target_image(peer, "tune"), use_ram=True, journal=journal)
        assert state.completed and ecu.rate_confirmed and not ecu.can_retry_write
        assert any(event == "route_fallback" for event, _fields in journal.events)
        assert any(mode == "ds2" and command == 7 and args[0] == 6
                   for mode, command, args in peer.requests)
        assert not any(mode == "resident" for mode, _command, _args in peer.requests)
        return
    with pytest.raises(protocol.FlashInterrupted):
        with ecu.transfer():
            ecu.write_tune(target_image(peer, "tune"), use_ram=True, journal=Journal())
    assert not peer.erases and not peer.programs
    assert ecu._pending_image is None and not ecu.can_retry_write
    assert ecu.rate_confirmed is safe_to_restart
    if not safe_to_restart:
        count = len(peer.requests)
        with pytest.raises(protocol.BaudTransitionError):
            ecu.read_voltage()
        assert len(peer.requests) == count


@pytest.mark.parametrize("stage", ("finalize_accepted", "finish"))
def test_ram_completed_write_is_not_replayed_after_journal_error(write_workflow, stage):
    ecu, peer = write_workflow

    class FailedJournal(Journal):
        def append(self, event, **fields):
            if event == stage:
                raise OSError("completion journal unavailable")
            return super().append(event, **fields)

        def finish(self, outcome, **fields):
            if stage == "finish" and outcome == "success":
                raise OSError("completion journal unavailable")
            return super().finish(outcome, **fields)

    with pytest.raises(protocol.ProgrammingCompletedError):
        with ecu.transfer():
            ecu.write_tune(target_image(peer, "tune"), use_ram=True, verify=True, journal=FailedJournal())
    assert ecu.last_write.completed and ecu.last_write.finalized
    assert ecu._pending_image is None and ecu._pending_writer is None
    assert not ecu.can_retry_write and peer.finalizations


def test_actual_retained_ram_backend_enables_only_desktop_retry(
        write_workflow, window, worker_runner, monkeypatch):
    from PyQt5.QtWidgets import QMessageBox

    ecu, peer = write_workflow
    peer.drop_next_program = True
    with pytest.raises(protocol.FlashInterrupted):
        with ecu.transfer():
            ecu.write_tune(target_image(peer, "tune"), use_ram=True, journal=Journal())
    assert ecu.baud_uncertain and ecu.can_retry_write
    desktop_connect(window, ecu.family)
    window._ds2 = ecu
    window._set_all_buttons_enabled(True)
    window._update_diag_actions()
    window._update_coding_actions()
    window._set_backup_buttons_enabled()
    assert window.btn_native_recovery.isEnabled()
    assert not window.btn_native_recovery.isHidden()
    for button in (window.btn_read_full, window.btn_write_full, window.btn_info,
                   window.btn_read_dtc, window.btn_read_module_coding, window.btn_backup_ecu):
        assert not button.isEnabled(), button.text()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    monkeypatch.setattr(window, "_show_flash_complete", lambda *args: None)
    monkeypatch.setattr(ds2_fast_safety, "OperationJournal", lambda *args, **kwargs: Journal())
    window._ms42_ms43_retry()
    assert worker_runner.results[0][0] is True
    assert ecu.last_write.completed and not ecu.can_retry_write
    assert peer.restored


@pytest.mark.parametrize("mode", ("tune", "full"))
def test_desktop_file_write_runs_complete_real_worker_and_backend(
        write_workflow, window, worker_runner, monkeypatch, tmp_path, mode):
    from PyQt5.QtWidgets import QFileDialog, QMessageBox
    import ms42_ms43_agent
    import ms42_ms43_resident

    # Keep this peer's regression on the existing resident writer; the custom
    # agent has separate complete GUI/worker/backend workflow coverage.
    monkeypatch.setattr(ms42_ms43_agent, "select_writer", ms42_ms43_resident.select_writer)

    ecu, peer = write_workflow
    target = target_image(peer, mode)
    path = tmp_path / "target.bin"
    path.write_bytes(target)
    desktop_connect(window, ecu.family)
    window._ds2 = ecu
    window.chk_backup_before_write.setChecked(False)
    window.chk_verify.setChecked(True)
    window.chk_force_slow_ds2.setChecked(False)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    monkeypatch.setattr(window, "_prewrite_battery_notice", lambda: "12.8 V")
    monkeypatch.setattr(ds2_fast_safety, "OperationJournal", lambda *args, **kwargs: Journal())
    completed = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: completed.append(args[1:]))
    window._on_write(mode)
    assert worker_runner.results == [(True, None)]
    assert ecu.last_write.completed and ecu.last_write.finalized and ecu.last_write.verified
    assert peer.image == expected_array(peer, target, mode)
    assert peer.restored and not ecu.baud_uncertain and not ecu.can_retry_write
    assert completed[0][0] == "Write Complete"
    assert "Turn ignition OFF" in completed[0][1]
    assert any("RAM write validated" in message for message, _level in worker_runner.logs)
    assert any(label == f"{ecu.family} RAM write" for _done, _total, label in worker_runner.progress)
    window._set_all_buttons_enabled(True)
    assert window.btn_read_full.isEnabled() and window.btn_write_full.isEnabled()
    assert not window.btn_native_recovery.isEnabled()


@pytest.mark.parametrize("fault", ("reset_ack", "reset_execute", "reconnect"))
def test_ram_reset_ambiguity_disables_retry_and_further_wire_operations(write_workflow, fault):
    ecu, peer = write_workflow
    peer.fault = fault
    with pytest.raises(protocol.FlashInterrupted):
        with ecu.transfer():
            ecu.write_tune(target_image(peer, "tune"), use_ram=True, journal=Journal())
    assert ecu._pending_image is not None and not ecu.can_retry_write
    assert ecu._pending_writer is not None and not ecu._pending_writer.active
    count = len(peer.requests)
    with pytest.raises(protocol.BaudTransitionError):
        ecu.retry_write(journal=Journal())
    assert len(peer.requests) == count
