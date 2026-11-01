"""Paired configuration metadata through the unchanged full writer and retry.

Injected serial peer verifies host orchestration; native finalization is covered
by the canonical test_c167_agent_finalize factory-exception differential cases.
"""

import pytest

import ms42_ms43 as protocol
import ms42_ms43_config as config
import ms42_ms43_image as images
from tests.test_ms42_ms43 import Journal
from tests.test_ms42_ms43_agent_workflow import agent_workflow as agent_workflow, packet
from tests.test_ms42_ms43_ram_write import expected_array, target_image


@pytest.mark.parametrize("interrupted", [False, True])
@pytest.mark.parametrize("disabled", [False, True])
def test_purge_configuration_uses_existing_tune_writer_and_retained_retry(
        agent_workflow, monkeypatch, interrupted, disabled):
    ecu, peer = agent_workflow
    software = "0110C6" if peer.family == "MS42" else "430069"
    names = ("EVAP purge operation", "Purge-valve electrical diagnosis", "Automatic purge-flow diagnosis",
             "Closed-loop lambda feedback")
    selections = {
        feature.name: "Disabled" if disabled else feature.options[0][0]
        for feature in config.features_for(peer.family, software) if feature.name in names
    }
    if peer.family == "MS42":
        selections["Purge-control lambda feedback"] = "Bank 2"
    full = config.apply_config(target_image(peer, "full"), selections)
    target = images.extract_calibration(images.correct_checksums(full, peer.family)[0])
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
                ecu.write_tune(target, use_ram=True, use_agent=True, verify=True, journal=Journal())
        assert ecu.can_retry_write and ecu._pending_image == target
        assert not peer.agent_finalizations and not peer.restored
        with ecu.transfer():
            state = ecu.retry_write(journal=Journal())
    else:
        with ecu.transfer():
            state = ecu.write_tune(target, use_ram=True, use_agent=True, verify=True, journal=Journal())
    assert state.completed and state.finalized and state.verified
    assert peer.image == expected_array(peer, target, "tune")
    assert {name: config.read_config(peer.image)[name] for name in selections} == selections
    assert peer.image[:peer.layout.calibration_offset] == peer.original[:peer.layout.calibration_offset]
    assert not ecu.can_retry_write and peer.restored and peer.agent_finalizations == 1


@pytest.mark.parametrize("agent_workflow", ["MS42"], indirect=True)
@pytest.mark.parametrize("label", ["Enabled", "Disabled"])
@pytest.mark.parametrize("interrupted", [False, True])
def test_ms42_crc_pair_survives_full_write_finalization_and_retained_retry(
        agent_workflow, monkeypatch, label, interrupted):
    ecu, peer = agent_workflow
    if label == "Enabled":
        peer.original = config.apply_config(peer.original, {"Calibration CRC": "Disabled"})
        peer.image[:] = peer.original
    target = config.apply_config(target_image(peer, "full"), {"Calibration CRC": label})
    target = images.correct_checksums(target, "MS42")[0]
    original = peer.agent_request
    failures = []

    def request(command, offset, count, data):
        if interrupted and not failures and command == ord("P") and offset <= 0x5002C < offset + count:
            original(command, offset, count, data)
            failures.append(offset)
            # A command may have changed flash before reporting a failure.
            return packet(command, offset, count, status=4)
        return original(command, offset, count, data)

    monkeypatch.setattr(peer, "agent_request", request)
    if interrupted:
        with pytest.raises(protocol.FlashInterrupted):
            with ecu.transfer():
                ecu.write_full(target, use_ram=True, use_agent=True, verify=True, journal=Journal())
        assert ecu._pending_image == target and ecu.can_retry_write
        assert not ecu.last_write.completed and not peer.agent_finalizations and not peer.restored
        assert peer.image[0x5002C] == target[0x5002C]
        with ecu.transfer():
            state = ecu.retry_write(journal=Journal())
    else:
        with ecu.transfer():
            state = ecu.write_full(target, use_ram=True, use_agent=True, verify=True, journal=Journal())
    assert state.completed and state.finalized and state.verified
    assert config.read_config(peer.image)["Calibration CRC"] == label
    assert peer.image == expected_array(peer, target, "full")
    assert peer.image[:0x10000] == peer.original[:0x10000]
    assert not ecu.can_retry_write and peer.restored and peer.agent_finalizations == 1
