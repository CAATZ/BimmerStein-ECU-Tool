"""Storage reads choose a route before handoff, without touching real ports."""

from types import SimpleNamespace

import pytest

from ds2 import DS2Error
from ms42_ms43 import BaudTransitionError
import ms42_ms43_storage as storage
from tests.test_ms42_ms43_agent_workflow import agent_workflow as agent_workflow
from tests.test_ms42_ms43_snapshot_baud import (
    RETAINED, bulk_reads, snapshot_peer as snapshot_peer,
)


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("capable,slow,rate", [
    (True, False, 125000), (False, False, 9600), (True, True, 9600),
])
def test_stock_route_reads_complete_backup_at_selected_rate(
        snapshot_peer, monkeypatch, family, capable, slow, rate):
    ecu, peer = snapshot_peer(family, capable=capable)
    selections, progress, logs = [], [], []

    def select(*args, **kwargs):
        selections.append(kwargs["identity"])
        return None

    monkeypatch.setattr(storage, "select_reader", select)
    result = storage.read_storage(ecu, "retained", force_slow=slow,
                                  progress_cb=lambda *args: progress.append(args), log_fn=logs.append)
    assert result["data"] == RETAINED and result["family"] == family
    assert result["software_id"] == storage.SOFTWARE[family]
    assert result["hardware_id"]
    assert len(selections) == (0 if slow else 1)
    assert len(bulk_reads(peer)) == 134
    assert all(baud == rate for baud, _ in bulk_reads(peer))
    assert progress[-1][:2] == (16384, 16384)
    assert ecu.baud == peer.baudrate == peer.expected_baud == 9600 and ecu.rate_confirmed
    assert any("stock DS2" in line for line in logs)
    assert all(command in (6, 0x91) for _, command, _ in peer.requests)


def test_supported_ram_reader_stays_first_choice(agent_workflow):
    ecu, peer = agent_workflow
    result = storage.read_storage(ecu, "retained")
    assert result["data"] == bytes(peer.image[0x4000:0x8000])
    reads = [(offset, count) for command, offset, count, _ in peer.agent_requests if command == ord("R")]
    assert reads == [(offset, 1024) for offset in range(0x4000, 0x8000, 1024)]
    assert peer.agent_launches == 1 and peer.restored and ecu.rate_confirmed
    assert not peer.programs and not peer.erases
    assert not any(mode == "ds2" and command == 6 and 0x4000 <= int.from_bytes(args[:4], "big") < 0x8000
                   for mode, command, args in peer.requests)


def test_real_signature_rejection_uses_stock_without_handoff(agent_workflow):
    ecu, peer = agent_workflow
    ecu._resident_signature_matches = False
    result = storage.read_storage(ecu, "retained")
    assert result["data"] == bytes(peer.image[0x4000:0x8000])
    assert not peer.agent_launches and not peer.programs and not peer.erases
    assert all(mode == "ds2" and command in (6, 0x91) for mode, command, _ in peer.requests)
    assert ecu.baud == 9600 and ecu.rate_confirmed


def test_unknown_software_keeps_actual_identity_in_stock_backup(snapshot_peer, monkeypatch):
    ecu, peer = snapshot_peer("MS43")
    identity = {"hardware_id": "11143053", "software_id": "430066"}
    monkeypatch.setattr(ecu, "_identity_fields", lambda: dict(identity))
    result = storage.read_storage(ecu, "retained")
    assert result["data"] == RETAINED and result["software_id"] == "430066"
    assert result["hardware_id"] == identity["hardware_id"]
    assert len(bulk_reads(peer)) == 134


def test_handoff_failure_does_not_switch_back_to_stock_read(agent_workflow):
    ecu, peer = agent_workflow
    peer.agent_fault = "upload_ready_missing"
    with pytest.raises(DS2Error):
        storage.read_storage(ecu, "retained")
    assert not ecu.rate_confirmed
    assert not any(mode == "ds2" and command == 6 and 0x4000 <= int.from_bytes(args[:4], "big") < 0x8000
                   for mode, command, args in peer.requests)
    assert not peer.erases and not peer.programs


def test_eeprom_has_no_stock_fallback(snapshot_peer, monkeypatch):
    import ms42_ms43_eeprom

    ecu, peer = snapshot_peer("MS43")
    monkeypatch.setattr(ms42_ms43_eeprom, "select_eeprom", lambda *a, **kw: None)
    with pytest.raises(DS2Error, match="RAM-agent"):
        storage.read_storage(ecu, "eeprom")
    assert not bulk_reads(peer)


def test_force_ds2_eeprom_is_rejected_before_io(snapshot_peer):
    ecu, peer = snapshot_peer("MS43")
    with pytest.raises(ValueError, match="RAM agent"):
        storage.read_storage(ecu, "eeprom", force_slow=True)
    assert peer.requests == []


@pytest.mark.parametrize("pending", ["_pending_image", "_pending_vin", "_pending_storage"])
def test_pending_write_prevents_any_read_route(snapshot_peer, pending):
    ecu, peer = snapshot_peer("MS43")
    setattr(ecu, pending, object())
    with pytest.raises(DS2Error, match="incomplete"):
        storage.read_storage(ecu, "retained", force_slow=True)
    assert peer.requests == []


@pytest.mark.parametrize("fault", ["enter", "restore"])
def test_stock_baud_failure_is_not_replaced_by_ram_attempt(snapshot_peer, monkeypatch, fault):
    ecu, peer = snapshot_peer("MS43")
    peer.fault = fault
    monkeypatch.setattr(storage, "select_reader", lambda *a, **kw: None)
    with pytest.raises(BaudTransitionError):
        storage.read_storage(ecu, "retained")
    assert not ecu.rate_confirmed
    assert len(bulk_reads(peer)) == (0 if fault == "enter" else 134)


def test_incomplete_stock_backup_is_not_accepted(snapshot_peer, monkeypatch):
    ecu, _ = snapshot_peer("MS43")
    monkeypatch.setattr(ecu, "_identity_fields", lambda: {"hardware_id": "11143053", "software_id": "430069"})
    monkeypatch.setattr(ecu, "read_memory", lambda *a, **kw: b"\xFF" * 12)
    with pytest.raises(ValueError, match="16,384|16384"):
        storage.read_storage(ecu, "retained", force_slow=True)


def test_ms41_never_enters_this_storage_route():
    with pytest.raises(ValueError, match="Unsupported"):
        storage.read_storage(SimpleNamespace(family="MS41"), "retained", force_slow=True)
