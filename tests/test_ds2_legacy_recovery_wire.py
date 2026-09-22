"""Wire-level regressions for the retained slow DS2 owner."""
import pytest

import ds2
from tests.test_ds2_program_readback import CalibrationPeer


@pytest.mark.parametrize("fault", ("double_late", "six_byte_impostor"))
def test_one_byte_readback_cannot_accept_a_six_byte_program_ack(monkeypatch, fault):
    monkeypatch.setattr(ds2.time, "sleep", lambda _seconds: None)
    address = 0x10000
    peer = CalibrationPeer(address, fault=fault, fragment=1)
    client = ds2.DS2Interface(peer.port)
    client._ser = peer
    # The requested byte equals the ACK's first payload byte. The full READ_MEM
    # reply length must be validated before extracting this one-byte slice.
    with pytest.raises(ds2.DS2Error, match="completion unknown"):
        client._write_block(address, b"\x02")
    assert [request.command for request in peer.requests] == [7, 6]
    assert peer.memory[address] == 2
    assert peer.is_open and peer.baudrate == 9600


def test_ten_byte_a2_during_readback_blocks_retained_reerase(monkeypatch):
    monkeypatch.setattr(ds2.time, "sleep", lambda _seconds: None)
    peer = CalibrationPeer(0x1370E, fault="read_nak", fragment=1)
    peer.read_memory = lambda address, count: bytes(6)
    client = ds2.DS2Interface(peer.port)
    client._ser = peer
    with pytest.raises(ds2.DS2Error) as caught:
        client._write_block(peer.address, b"A" * 243)
    assert caught.value.status == 0xA2
    recovery = ds2.LegacyWriteRecovery(
        client, b"A" * 0x6000, "tune", "tune",
        error=caught.value, destructive_started=True)
    assert recovery.power_cycle_required and not recovery.retry_supported
    assert [request.command for request in peer.requests] == [7, 6]
    assert peer.is_open and peer.baudrate == 9600
