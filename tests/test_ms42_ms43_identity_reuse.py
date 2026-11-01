"""Identity selection and write workflows use injected transports only."""

import pytest

import ms42_ms43 as protocol
import ms42_ms43_agent as agent
import ms42_ms43_resident as resident
from tests.test_ms42_ms43 import Journal, make_write
from tests.test_ms42_ms43_agent_workflow import agent_workflow as agent_workflow
from tests.test_ms42_ms43_ram_write import target_image, write_workflow as write_workflow


def assert_single_preflight_identity(ecu, peer):
    first_authorization = next(index for index, request in enumerate(peer.requests)
                               if request[:2] == ("ds2", 0x90))
    shift = 0x80000 if ecu.family == "MS43" else 0
    for offset, size in ((ecu.profile.hw_offset, 8), (ecu.profile.sw_offset, 6)):
        request = ("ds2", 6, (offset + shift).to_bytes(4, "big") + bytes((size,)))
        assert peer.requests[:first_authorization].count(request) == 1


@pytest.mark.parametrize("mode", ("tune", "full"))
def test_agent_write_reads_identity_once_before_handoff(agent_workflow, mode):
    ecu, peer = agent_workflow
    with ecu.transfer():
        state = getattr(ecu, "write_" + mode)(target_image(peer, mode), use_ram=True,
                                             use_agent=True, journal=Journal())
    assert state.completed and peer.restored
    assert_single_preflight_identity(ecu, peer)
    # The independent post-reset identity check remains mandatory.
    reset = next(index for index, request in enumerate(peer.requests)
                 if request[:2] == ("agent", ord("X")))
    assert [command for _wire, command, _args in peer.requests[reset + 1:]] == [0, 6, 6]


@pytest.mark.parametrize("mode", ("tune", "full"))
def test_resident_write_reads_identity_once_before_handoff(write_workflow, mode):
    ecu, peer = write_workflow
    with ecu.transfer():
        state = getattr(ecu, "write_" + mode)(target_image(peer, mode), use_ram=True,
                                             journal=Journal())
    assert state.completed and peer.restored
    assert_single_preflight_identity(ecu, peer)


@pytest.mark.parametrize("family", ("MS42", "MS43"))
@pytest.mark.parametrize("module", (agent, resident))
def test_writer_selection_keeps_direct_callers_and_independent_snapshot(family, module):
    ecu, _image, _events = make_write(family)
    ecu.transport.native_fast_capable = True
    ecu.transport._ser.parity = "E"
    ecu._resident_signature_matches = True
    identity, calls = ecu._identity_fields(), []
    ecu._identity_fields = lambda: calls.append(True) or dict(identity)
    direct = module.select_writer(ecu)
    assert direct.identity == identity and len(calls) == 1
    reused = module.select_writer(ecu, identity=identity)
    assert reused.identity == identity and len(calls) == 1
    identity["software_id"] = "999999"
    assert reused.identity == direct.identity


@pytest.mark.parametrize("family", ("MS42", "MS43"))
@pytest.mark.parametrize("module", (agent, resident))
def test_reused_identity_preserves_software_and_small_fingerprint_fallbacks(family, module):
    ecu, _image, _events = make_write(family)
    ecu.transport.native_fast_capable = True
    ecu.transport._ser.parity = "E"
    identity = ecu._identity_fields()
    ecu._identity_fields = lambda: pytest.fail("Preflight identity must not be read again")
    reads = []
    ecu.read_memory = lambda address, length: reads.append((address, length)) or bytes(length)
    unknown = dict(identity, software_id="999999")
    assert module.select_writer(ecu, identity=unknown) is None
    assert reads == [] and ecu._resident_signature_matches is None
    assert module.select_writer(ecu, identity=identity) is None
    assert tuple(reads) == resident.PROFILES[family]["signature_spans"]
    assert sum(length for _address, length in reads) == (120 if family == "MS42" else 108)
    assert module.select_writer(ecu, identity=identity) is None
    assert tuple(reads) == resident.PROFILES[family]["signature_spans"]


def test_active_write_retry_keeps_original_identity_binding(agent_workflow, monkeypatch):
    ecu, peer = agent_workflow
    peer.drop_next_program = True
    original_request = peer.agent_request

    def lose_readback_after_program(command, offset, count, data):
        response = original_request(command, offset, count, data)
        return b"" if peer.lost_program and command == ord("R") else response

    # Prevent automatic reconciliation so this still exercises an explicit retained retry.
    monkeypatch.setattr(peer, "agent_request", lose_readback_after_program)
    with pytest.raises(protocol.FlashInterrupted):
        with ecu.transfer():
            ecu.write_tune(target_image(peer, "tune"), use_ram=True, use_agent=True,
                           journal=Journal())
    assert peer.lost_program and peer.programs.count(peer.lost_program) == 1
    retained, live = ecu._pending_writer, dict(ecu._pending_live)
    original, reads = ecu._identity_fields, []

    def read_after_reset():
        assert peer.restored, "An active retry must use its retained identity"
        reads.append(True)
        return original()

    monkeypatch.setattr(ecu, "_identity_fields", read_after_reset)
    monkeypatch.setattr(peer, "agent_request", original_request)
    state = ecu.retry_write(journal=Journal())
    assert state.completed and reads == [True]
    assert retained.identity == live and not retained.active
