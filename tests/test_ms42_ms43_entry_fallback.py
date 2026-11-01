"""Failed RAM entry falls back only after the original DS2 session is confirmed."""

import pytest

import ds2
import ms42_ms43 as protocol
import ms42_ms43_storage as storage
from tests.test_ms42_ms43 import Journal
from tests.test_ms42_ms43_agent_workflow import agent_workflow as agent_workflow
from tests.test_ms42_ms43_ram_workflow import response
from tests.test_ms42_ms43_ram_write import SECTORS, target_image


def inject_entry_failure(peer, monkeypatch, fault):
    stock = peer.stock

    def respond(command, args):
        if command == 0x0B and args[:1] == b"\x01" and fault == "preflight":
            return response(status=0xB0)
        if command == 0x90 and args == b"\xFC\0\0\0" and fault == "challenge_rejected":
            return response(status=0xA2)
        return stock(command, args)

    if fault == "bridge_readback":
        peer.fault = "upload_readback"
    elif fault == "challenge_busy":
        peer.fault = "authorization_timeout_seed"
    elif fault == "resident_upload":
        peer.agent_fault = "upload_readback"
    monkeypatch.setattr(peer, "stock", respond)


def model_stock_writes(peer, monkeypatch):
    """Apply the byte-pipe peer's stock writes so readback checks real array changes."""
    stock = peer.stock

    def respond(command, args):
        if command == 0x90 and args.startswith(b"BMW") and peer.authorized:
            # Native FC-authorized state rejects this first request and re-arms readiness.
            peer.authorized = peer.seed_sent = False
            peer.ready_at = protocol.time.monotonic() + 1.0
            return response(status=0xA2)
        if command == 0x07 and args[0] in (2, 6):
            assert peer.bmw_authorized and peer.mode == "ds2"
            operation, address = args[0], int.from_bytes(args[1:4], "big") & 0x7FFFF
            assert args[4] == len(args[5:])
            if operation == 6:
                sectors = ((peer.contract["calibration"],) if address == peer.layout.calibration_offset
                           else peer.contract["program"])
                for sector in sectors:
                    assert sector >= 4
                    start, end = SECTORS[sector]
                    peer.image[start:end] = b"\xff" * (end - start)
                    peer.erases.append(sector)
            else:
                data = args[5:]
                assert 0 < len(data) <= 118 and address >= 0x10000
                assert all(new & ~old == 0 for new, old in zip(data, peer.image[address:address + len(data)]))
                peer.image[address:address + len(data)] = data
                peer.programs.append((address, data))
        return stock(command, args)

    monkeypatch.setattr(peer, "stock", respond)


@pytest.mark.parametrize("fault,mode", [
    ("preflight", "full"), ("bridge_readback", "tune"),
    ("challenge_busy", "tune"), ("challenge_rejected", "tune"),
    ("resident_upload", "tune"),
])
def test_confirmed_entry_failure_returns_stock_reader(agent_workflow, monkeypatch, fault, mode):
    ecu, peer = agent_workflow
    inject_entry_failure(peer, monkeypatch, fault)
    logs = []
    with ecu.read_transfer(use_agent=True, log_fn=logs.append) as reader:
        assert reader is ecu
        actual = getattr(reader, "read_" + mode)(log_fn=logs.append)
    if mode == "full":
        expected = bytearray(b"\xff" * 0x80000)
        for _address, offset, size in ecu.profile.read_regions:
            expected[offset:offset + size] = peer.original[offset:offset + size]
        expected[0x3F00:0x3F64] = b"\xff" * 100
        assert actual == bytes(expected)
        assert any("masked" in line for line in logs)
    else:
        start = ecu.profile.calibration_offset
        assert actual == peer.original[start:start + ecu.profile.partial_size]
    assert any("DS2" in line and "fallback" in line.lower() for line in logs)
    assert peer.mode == "ds2" and ecu.baud == 9600 and ecu.rate_confirmed
    assert not peer.agent_launches and not peer.erases and not peer.programs
    assert not peer.selected_measurements


@pytest.mark.parametrize("mode,fault", [
    ("tune", "preflight"), ("full", "preflight"),
    ("tune", "bridge_readback"), ("tune", "resident_upload"),
])
def test_entry_fallback_writes_and_verifies_original_target(agent_workflow, monkeypatch, mode, fault):
    ecu, peer = agent_workflow
    inject_entry_failure(peer, monkeypatch, fault)
    model_stock_writes(peer, monkeypatch)
    target, journal, logs = target_image(peer, mode), Journal(), []
    with ecu.transfer():
        state = getattr(ecu, "write_" + mode)(
            target, use_ram=True, use_agent=True, verify=True, journal=journal, log_fn=logs.append)
    assert state.completed and state.verified and state.finalized
    regions = list(ecu.profile.program_write_regions) if mode == "full" else []
    regions.append((ecu.profile.calibration_offset,
                    ecu.profile.calibration_offset if mode == "full" else 0,
                    ecu.profile.calibration_write_length))
    for address, source, size in regions:
        offset = address & 0x7FFFF
        assert peer.image[offset:offset + size] == target[source:source + size]
    assert peer.image[:0x10000] == peer.original[:0x10000]
    assert peer.erases and peer.programs and not peer.agent_launches
    assert all(len(data) <= 118 for _, data in peer.programs)
    assert not ecu.can_retry_write and ecu.baud == 9600 and ecu.rate_confirmed
    assert any(event == "route_fallback" for event, _ in journal.events)
    assert journal.events[-1][1]["outcome"] == "success"
    bmw_requests = [args for mode, command, args in peer.requests
                    if mode == "ds2" and command == 0x90 and args.startswith(b"BMW")]
    assert len(bmw_requests) == (2 if fault == "bridge_readback" else 1)


@pytest.mark.parametrize("fault", ("challenge", "key_ack", "handoff", "resident_switch_ack"))
def test_uncertain_entry_never_sends_stock_fallback(agent_workflow, fault):
    ecu, peer = agent_workflow
    peer.fault = fault
    with pytest.raises(ds2.DS2Error):
        with ecu.read_transfer(use_agent=True):
            pytest.fail("Unconfirmed entry must not expose a reader")
    assert not ecu.rate_confirmed and not peer.restored
    assert not peer.erases and not peer.programs
    assert not any(mode == "ds2" and command == 0x90 and args.startswith(b"BMW")
                   for mode, command, args in peer.requests)


@pytest.mark.parametrize("phase", ("upload", "readback"))
def test_incomplete_agent_upload_reply_cannot_start_resident_reset(agent_workflow, monkeypatch, phase):
    ecu, peer = agent_workflow
    loader = peer.loader

    def respond(command, args):
        reply = loader(command, args)
        if command == (0x78 if phase == "upload" else 0x71):
            return reply[:-1]
        return reply

    monkeypatch.setattr(peer, "loader", respond)
    with pytest.raises(ds2.DS2Error):
        with ecu.read_transfer(use_agent=True):
            pytest.fail("An incomplete response cannot establish a reset boundary")
    assert not ecu.rate_confirmed and peer.mode == "resident"
    assert not peer.restored and not peer.reset_staged
    assert not peer.agent_launches and not peer.erases and not peer.programs


def test_requested_boot_write_is_never_downgraded(agent_workflow, monkeypatch):
    ecu, peer = agent_workflow
    inject_entry_failure(peer, monkeypatch, "preflight")
    with pytest.raises(protocol.FlashInterrupted):
        with ecu.transfer():
            ecu.write_full(target_image(peer, "full"), use_ram=True, use_agent=True,
                           write_boot=True, journal=Journal())
    assert not peer.erases and not peer.programs and bytes(peer.image) == peer.original
    assert not any(mode == "ds2" and command == 0x90 and args.startswith(b"BMW")
                   for mode, command, args in peer.requests)


def test_cancelled_entry_does_not_start_fallback(agent_workflow, monkeypatch):
    ecu, peer = agent_workflow
    stock, cancelled = peer.stock, []

    def check():
        if cancelled:
            raise RuntimeError("cancelled")

    def respond(command, args):
        reply = stock(command, args)
        if command == 0x0B and args[:1] == b"\x01":
            cancelled.append(True)
        return reply

    ecu.operation_check = check
    monkeypatch.setattr(peer, "stock", respond)
    with pytest.raises(ds2.DS2Error, match="cancelled"):
        with ecu.read_transfer(use_agent=True):
            pytest.fail("Cancelled entry must not expose a reader")
    assert peer.requests[-1][1] == 0x0B
    assert not peer.erases and not peer.programs and not peer.agent_launches


def test_entry_fallback_refuses_changed_identity(agent_workflow, monkeypatch):
    ecu, peer = agent_workflow
    inject_entry_failure(peer, monkeypatch, "preflight")
    stock = peer.stock
    failed = []

    def respond(command, args):
        if command == 0x0B and args[:1] == b"\x01":
            failed.append(True)
        if (failed and command == 6
                and int.from_bytes(args[:4], "big") & 0x7FFFF == peer.layout.sw_offset):
            return response(b"999999")
        return stock(command, args)

    monkeypatch.setattr(peer, "stock", respond)
    with pytest.raises(ds2.DS2Error, match="identity"):
        with ecu.read_transfer(use_agent=True):
            pytest.fail("Changed identity must not expose a reader")
    assert not ecu.rate_confirmed and not peer.erases and not peer.programs


def test_retained_storage_entry_failure_uses_stock_read(agent_workflow, monkeypatch):
    ecu, peer = agent_workflow
    inject_entry_failure(peer, monkeypatch, "preflight")
    logs = []
    backup = storage.read_storage(ecu, "retained", log_fn=logs.append)
    assert backup["data"] == peer.original[0x4000:0x8000]
    assert any("DS2" in line and "fallback" in line.lower() for line in logs)
    assert ecu.rate_confirmed and ecu.baud == 9600
    assert not peer.erases and not peer.programs and not peer.agent_launches


@pytest.mark.parametrize("agent_workflow", ["MS43"], indirect=True)
def test_eeprom_entry_failure_has_no_stock_equivalent(agent_workflow, monkeypatch):
    ecu, peer = agent_workflow
    inject_entry_failure(peer, monkeypatch, "preflight")
    with pytest.raises(ds2.DS2Error):
        storage.read_storage(ecu, "eeprom")
    assert not any(mode == "ds2" and command == 6 and int.from_bytes(args[:4], "big") == 0x4000
                   for mode, command, args in peer.requests)
    assert not peer.erases and not peer.programs and not peer.agent_launches
