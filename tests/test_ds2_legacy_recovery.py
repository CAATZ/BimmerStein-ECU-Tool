"""Regression coverage for shared slow DS2 ownership and recovery outcomes."""
from types import SimpleNamespace

import pytest
import ds2
from ds2_fast_contracts import decode_ds2_frame, encode_ds2_frame
from ds2_fast_plans import ds2_image_to_file_layout
from tests.test_ds2_program_readback import CalibrationPeer


class AckPeer(CalibrationPeer):
    def __init__(self, fault):
        super().__init__(-1)
        self.ack_fault = fault

    def program(self, operation, address, data):
        reply = super().program(operation, address, data)
        if operation == 2:
            payload = bytearray(decode_ds2_frame(reply).payload)
            if self.ack_fault == "short":
                payload = payload[:1]
            elif self.ack_fault == "cursor":
                payload[3] ^= 1
            elif self.ack_fault == "count":
                payload[4] = 0
            elif self.ack_fault == "amd_partial":
                self.memory[address:address + 2] = b"\xff\xff"
                payload[1:4] = (address + len(data) - 2).to_bytes(3, "big")
                payload[4] -= 2
            return encode_ds2_frame(0xFF if self.ack_fault == "outer_ff" else 0xA0, payload)
        return reply


@pytest.mark.parametrize("fault", ("short", "cursor", "count", "amd_partial", "outer_ff"))
def test_write_never_accepts_malformed_ack_without_readback(monkeypatch, fault):
    monkeypatch.setattr(ds2.time, "sleep", lambda _: None)
    peer = AckPeer(fault)
    client = ds2.DS2Interface(peer.port)
    client._ser = peer
    if fault in ("amd_partial", "outer_ff"):
        with pytest.raises(ds2.DS2Error):
            client._write_block(0x10000, b"\xa6" * 243)
    else:
        client._write_block(0x10000, b"\xa6" * 243)
    assert [r.command for r in peer.requests] == ([7] if fault == "outer_ff" else [7, 6])


class PhaseClient(ds2.DS2Interface):
    def __init__(self, *, failing_phase="tune", failures=1, preamble_failure=False):
        super().__init__("OFFLINE")
        self._ser = SimpleNamespace(is_open=True)
        self.events, self.failures = [], failures
        self.failing_phase, self.preamble_failure = failing_phase, preamble_failure

    def _prepare(self):
        self.events.append("prepare")
        if self.preamble_failure:
            raise ds2.DS2Error("prepare failed")

    def status(self):
        return b""

    def unlock_write(self, **kwargs):
        return b"\x00"

    def read_mem(self, address, count):
        return b"\x02" if address == 0xE658 else bytes(count)

    def _erase_sector(self, address, *, boundary_cb=None, **kwargs):
        if boundary_cb:
            boundary_cb()
        self.events.append(("erase", address))

    def _write_block(self, address, data, log_fn=None):
        self.events.append(("program", address))
        phase = "tune" if address >= 0x10000 and address < 0x20000 else "program"
        if phase == self.failing_phase and self.failures:
            self.failures -= 1
            raise ds2.DS2ProgramMismatch(address, data, bytes(data[:1]) + b"\xff" * (len(data) - 1))

    def verify_program_region(self, **kwargs):
        self.events.append("finalize")
        return True, 1


def target(full):
    tune = b"\xa6" * 16 + b"\xff" * (0x6000 - 16)
    if not full:
        return tune
    image = bytearray(b"\xff" * 0x40000)
    image[0x2000:0x2010] = b"\x17" * 16
    image[0x10000:0x16000] = tune
    return ds2_image_to_file_layout(image)


@pytest.mark.parametrize("full", (False, True))
def test_automatic_replay_repeats_only_failed_tune_and_finalizes(full):
    client = PhaseClient()
    method = client.write_full if full else client.write_partial
    method(target(full), boundary_cb=lambda: client.events.append("boundary"))
    erases = [event[1] for event in client.events if isinstance(event, tuple) and event[0] == "erase"]
    assert erases == ([0x2000, 0x10000, 0x10000] if full else [0x10000, 0x10000])
    assert client.events.count("boundary") == 1
    assert client.events[-1] == "finalize"
    assert client.write_recovery.completed and client.is_open
    assert not client.write_recovery.retry_supported


def test_failed_automatic_replay_retains_once_for_operator_retry():
    client = PhaseClient(failures=2)
    with pytest.raises(ds2.LegacyWriteRecoveryRequired) as caught:
        client.write_partial(target(False))
    recovery = caught.value.recovery
    assert recovery.is_open and recovery.retry_supported
    assert client.events.count(("erase", 0x10000)) == 2
    ds2.resume_legacy_recovery(recovery)
    assert client.events.count(("erase", 0x10000)) == 3
    assert recovery.completed and not recovery.retry_supported


def test_preparation_failure_does_not_claim_erase_or_recovery():
    client = PhaseClient(preamble_failure=True)
    with pytest.raises(ds2.DS2Error, match="prepare failed") as caught:
        client.write_partial(target(False), boundary_cb=lambda: client.events.append("boundary"))
    assert not isinstance(caught.value, ds2.LegacyWriteRecoveryRequired)
    assert client.events == ["prepare"] and client.write_recovery is None


def test_calibration_packets_do_not_cross_readback_pages(monkeypatch):
    client = PhaseClient(failures=0)
    writes = []
    monkeypatch.setattr(client, "_write_block", lambda address, data, **kw: writes.append((address, len(data))))
    client.write_partial(b"\xa6" * 0x6000)
    assert sum(count for _, count in writes) == 0x6000
    assert all(address // 0x4000 == (address + count - 1) // 0x4000 for address, count in writes)


@pytest.mark.parametrize("authorization,wrong_keys", ((b"\x01", b"\x00"), (b"\x02", b"\x02")))
def test_partial_fault_cannot_reerase_with_pending_key_or_lock(monkeypatch, authorization, wrong_keys):
    client = PhaseClient()
    original_read = client.read_mem
    monkeypatch.setattr(client, "read_mem", lambda address, count:
                        authorization if address == 0xE658 else
                        wrong_keys if address == 0xE74B else original_read(address, count))
    with pytest.raises(ds2.LegacyWriteRecoveryRequired) as caught:
        client.write_partial(target(False))
    recovery = caught.value.recovery
    assert recovery.is_open and recovery.power_cycle_required
    assert not recovery.retry_supported
    assert client.events.count(("erase", 0x10000)) == 1
    assert "finalize" not in client.events


def test_failed_finalizer_is_retained_without_another_erase(monkeypatch):
    client = PhaseClient(failures=0)
    monkeypatch.setattr(client, "verify_program_region", lambda **kw: (False, 9))
    with pytest.raises(ds2.LegacyWriteRecoveryRequired) as caught:
        client.write_partial(target(False))
    recovery = caught.value.recovery
    assert recovery.phase == "finalize" and recovery.is_open
    assert not recovery.completed and not recovery.retry_supported
    assert client.events.count(("erase", 0x10000)) == 1


def test_failed_boundary_journal_prevents_erase():
    client = PhaseClient(failures=0)
    def refuse_boundary():
        raise OSError("journal cannot be persisted")
    with pytest.raises(OSError, match="journal cannot be persisted"):
        client.write_partial(target(False), boundary_cb=refuse_boundary)
    assert client.write_recovery is None
    assert not any(isinstance(event, tuple) for event in client.events)
