"""Standard serial reaches the same agent without requesting an exact high baud."""

import pytest

import ms42_ms43_agent as agent
import ms42_ms43_image as images
import ms42_ms43_resident as resident
import transfer_rates
from tests.test_ms42_ms43 import Journal
from tests.test_ms42_ms43_agent_workflow import agent_workflow as agent_workflow
from tests.test_ms42_ms43_ram_write import expected_array, target_image


@pytest.mark.parametrize("compatibility", (False, True))
@pytest.mark.parametrize("mode", ("full", "tune"))
def test_standard_serial_reads_use_native_startup_baud(agent_workflow, monkeypatch, compatibility, mode):
    ecu, peer = agent_workflow
    peer.native_fast_capable = False
    monkeypatch.setattr(transfer_rates, "_high_baud", 125000 if compatibility else 187500)
    with ecu.read_transfer(use_agent=True) as reader:
        assert isinstance(reader, agent.AgentReader)
        assert reader.profile["baud"] == 9600
        data = getattr(reader, "read_" + mode)()
    assert data == (peer.original if mode == "full" else images.extract_calibration(peer.original))
    assert not any(command == 0x01 for phase, command, _ in peer.requests if phase == "loader")
    assert not peer.erases and not peer.programs
    assert ecu.rate_confirmed and ecu.baud == 9600 and peer.parity == "E"


@pytest.mark.parametrize("mode,boot", (("tune", False), ("full", False), ("full", True)))
def test_standard_serial_write_keeps_existing_image_plan(agent_workflow, mode, boot):
    ecu, peer = agent_workflow
    peer.native_fast_capable = False
    target = target_image(peer, mode)
    options = dict(use_ram=True, use_agent=True, verify=True, journal=Journal())
    if boot:
        options.update(write_boot=True, preserve_identity=True)
    with ecu.transfer():
        state = getattr(ecu, "write_" + mode)(target, **options)
    assert state.completed and state.verified
    assert peer.image == expected_array(peer, target, mode)
    assert not any(command == 0x01 for phase, command, _ in peer.requests if phase == "loader")
    assert ecu.rate_confirmed and ecu.baud == 9600 and peer.parity == "E"


def test_standard_serial_does_not_expand_older_resident_route(agent_workflow):
    ecu, peer = agent_workflow
    peer.native_fast_capable = False
    assert resident.select_reader(ecu) is None
    assert resident.select_writer(ecu) is None
    assert not peer.requests


def test_agent_requires_runtime_parity_control_before_probes(agent_workflow):
    ecu, peer = agent_workflow
    peer.native_fast_capable = False
    del peer.parity
    assert agent.select_reader(ecu) is None
    assert agent.select_writer(ecu) is None
    assert not peer.requests
