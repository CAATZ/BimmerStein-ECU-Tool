"""Community configuration survives the existing full-write and retained retry."""

import pytest

import ms42_ms43 as protocol
import ms42_ms43_config_patches as config
import ms42_ms43_image as images
from tests.test_ms42_ms43 import Journal
from tests.test_ms42_ms43_agent_workflow import agent_workflow as agent_workflow, packet
from tests.test_ms42_ms43_ram_write import expected_array, target_image


@pytest.mark.parametrize("label", ("Enabled", "Disabled"))
@pytest.mark.parametrize("interrupted", (False, True))
def test_community_options_full_write_and_retry(agent_workflow, monkeypatch, label, interrupted):
    ecu, peer = agent_workflow
    options = {"Immobilizer bypass": label, "Calibration checksum bypass": label}
    if label == "Disabled":
        peer.original = config.apply_config(peer.original, dict.fromkeys(options, "Enabled"))
        peer.image[:] = peer.original
    target = config.apply_config(target_image(peer, "full"), options)
    target = images.correct_checksums(target, ecu.family)[0]
    original = peer.agent_request
    failures = []

    def request(command, offset, count, data):
        if interrupted and not failures and command == ord("P"):
            original(command, offset, count, data)
            failures.append(offset)
            return packet(command, offset, count, status=4)
        return original(command, offset, count, data)

    monkeypatch.setattr(peer, "agent_request", request)
    if interrupted:
        with pytest.raises(protocol.FlashInterrupted):
            with ecu.transfer():
                ecu.write_full(target, use_ram=True, use_agent=True, verify=True, journal=Journal())
        assert ecu.can_retry_write and ecu._pending_image == target and not peer.restored
        with ecu.transfer():
            state = ecu.retry_write(journal=Journal())
    else:
        with ecu.transfer():
            state = ecu.write_full(target, use_ram=True, use_agent=True, verify=True, journal=Journal())
    assert state.completed and state.finalized and state.verified
    assert peer.image == expected_array(peer, target, "full")
    assert peer.image[:0x10000] == peer.original[:0x10000]
    assert all(config.read_config(peer.image)[name] == label for name in options)
    assert not ecu.can_retry_write and peer.restored
