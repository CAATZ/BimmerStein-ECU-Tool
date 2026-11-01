"""Storage reuses confirmed rate fallback only before its first mutation."""

import pytest

from ds2 import DS2Error
from ms42_ms43 import FlashInterrupted
import ms42_ms43_storage as storage
import transfer_rates
from tests.test_ms42_ms43 import Journal
from tests.test_ms42_ms43_agent_workflow import agent_workflow as agent_workflow
from tests.test_ms42_ms43_baud_recovery import corrupt_fast_reads


@pytest.mark.parametrize("limit", [125000, 9600])
@pytest.mark.parametrize("restore", [False, True])
def test_storage_reads_downshift_only_after_confirmed_reset_and_identity(
        agent_workflow, monkeypatch, limit, restore):
    ecu, peer = agent_workflow
    monkeypatch.setattr(transfer_rates, "_high_baud", 187500)
    rates = corrupt_fast_reads(peer, monkeypatch, limit)
    data = bytes(peer.image[0x4000:0x8000])
    if restore:
        snapshot = storage.make_snapshot(ecu.family, "retained", data, software_id=storage.SOFTWARE[ecu.family])
        assert storage.restore_storage(ecu, snapshot, journal=Journal())["verified"]
    else:
        assert storage.read_storage(ecu, "retained")["data"] == data
    assert list(dict.fromkeys(rates))[-1] == limit
    assert peer.agent_launches == (2 if limit == 125000 else 3)
    assert not peer.programs and not peer.erases and ecu.rate_confirmed and ecu.baud == 9600


@pytest.mark.parametrize("fault", ["reset_ack", "restored_identity"])
def test_failed_read_fallback_stops_without_another_agent_request(agent_workflow, monkeypatch, fault):
    ecu, peer = agent_workflow
    corrupt_fast_reads(peer, monkeypatch, 9600)
    if fault == "reset_ack":
        peer.agent_fault = fault
    else:
        peer.fault = fault
    with pytest.raises(DS2Error):
        storage.read_storage(ecu, "retained")
    assert peer.agent_launches == 1 and not ecu.rate_confirmed
    assert not peer.programs and not peer.erases
    assert sum(command == ord("X") for command, *_ in peer.agent_requests) == 1


def test_verification_transport_failure_after_mutation_never_downshifts(agent_workflow, monkeypatch):
    ecu, peer = agent_workflow
    target = bytearray(peer.image[0x4000:0x8000])
    target[0] ^= 1
    snapshot = storage.make_snapshot(ecu.family, "retained", target, software_id=storage.SOFTWARE[ecu.family])
    def progress(done, total, label):
        if done > 0:
            peer.agent_fault = "read_crc"
    with pytest.raises(FlashInterrupted):
        storage.restore_storage(ecu, snapshot, journal=Journal(), progress_cb=progress)
    assert ecu.can_retry_write and peer.agent_launches == 1 and not peer.restored
    peer.agent_fault = None
    assert ecu.retry_write(journal=Journal())["verified"]
    assert bytes(peer.image[0x4000:0x8000]) == target and peer.agent_launches == 1
