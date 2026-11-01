"""Real host/worker wire ownership, with injected peers only."""

import pytest

from ds2 import DS2Error
import ms42_ms43 as protocol
import ms42_ms43_agent as agent
import ms42_ms43_image as images
import transfer_rates
from tests.test_ms42_ms43 import Journal
from tests.test_ms42_ms43_agent_workflow import agent_workflow as agent_workflow
from tests.test_ms42_ms43_ram_write import expected_array, target_image


def corrupt_fast_reads(peer, monkeypatch, limit):
    request = peer.agent_request
    rates = []

    def noisy(command, offset, count, data):
        reply = request(command, offset, count, data)
        if command == agent.READ:
            rates.append(peer.baudrate)
            if peer.baudrate > limit:
                return reply[:-1] + bytes((reply[-1] ^ 1,))
        return reply

    monkeypatch.setattr(peer, "agent_request", noisy)
    return rates


@pytest.mark.parametrize("limit", (125000, 9600))
@pytest.mark.parametrize("mode", ("full", "tune"))
def test_read_retries_lower_agent_rates_after_confirmed_reset(agent_workflow, monkeypatch, limit, mode):
    ecu, peer = agent_workflow
    monkeypatch.setattr(transfer_rates, "_high_baud", 187500)
    rates = corrupt_fast_reads(peer, monkeypatch, limit)
    logs = []
    with ecu.read_transfer(use_agent=True) as reader:
        result = getattr(reader, "read_" + mode)(log_fn=logs.append)
        assert reader.profile["baud"] == limit
    assert result == (peer.original if mode == "full" else images.extract_calibration(peer.original))
    assert list(dict.fromkeys(rates)) == [agent.PROFILES[ecu.family]["baud"], 125000, *([9600] if limit == 9600 else [])]
    assert not peer.erases and not peer.programs and ecu.rate_confirmed and ecu.baud == 9600
    assert peer.agent_launches == (3 if limit == 9600 else 2)
    assert any("retry" in text.lower() and f"{limit:,}" in text for text in logs)


def test_compatibility_read_falls_only_to_native_startup_rate(agent_workflow, monkeypatch):
    ecu, peer = agent_workflow
    monkeypatch.setattr(transfer_rates, "_high_baud", 125000)
    rates = corrupt_fast_reads(peer, monkeypatch, 9600)
    with ecu.read_transfer(use_agent=True) as reader:
        assert reader.read_tune() == images.extract_calibration(peer.original)
    assert list(dict.fromkeys(rates)) == [125000, 9600]


@pytest.mark.parametrize("failure", ("reset_ack", "restored_identity"))
def test_read_fallback_needs_confirmed_reset_and_same_identity(agent_workflow, monkeypatch, failure):
    ecu, peer = agent_workflow
    corrupt_fast_reads(peer, monkeypatch, 9600)
    if failure == "reset_ack":
        peer.agent_fault = failure
    else:
        peer.fault = failure
    with pytest.raises(DS2Error):
        with ecu.read_transfer(use_agent=True) as reader:
            reader.read_tune()
    assert peer.agent_launches == 1 and not ecu.rate_confirmed
    assert not peer.erases and not peer.programs


def test_write_can_downshift_only_before_first_flash_mutation(agent_workflow, monkeypatch):
    ecu, peer = agent_workflow
    monkeypatch.setattr(transfer_rates, "_high_baud", 187500)
    corrupt_fast_reads(peer, monkeypatch, 125000)
    target = target_image(peer, "tune")
    journal = Journal()
    with ecu.transfer():
        state = ecu.write_tune(target, use_ram=True, use_agent=True, verify=True, journal=journal)
    assert state.completed and state.verified
    assert peer.image == expected_array(peer, target, "tune") and peer.agent_launches == 2
    assert ecu.baud == 9600 and ecu.rate_confirmed


def test_definitive_flash_failure_keeps_original_rate_and_agent(agent_workflow, monkeypatch):
    ecu, peer = agent_workflow
    monkeypatch.setattr(transfer_rates, "_high_baud", 187500)
    peer.reject_next_program = True
    with pytest.raises(protocol.FlashInterrupted):
        with ecu.transfer():
            ecu.write_tune(target_image(peer, "tune"), use_ram=True, use_agent=True, journal=Journal())
    assert ecu.can_retry_write and peer.agent_launches == 1
    assert ecu.baud == agent.PROFILES[ecu.family]["baud"] and peer.mode == "agent"
    requests = len(peer.requests)
    assert not ecu._pending_writer._lower_baud()
    assert len(peer.requests) == requests
