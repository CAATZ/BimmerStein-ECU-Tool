"""Production virginize/recovery sequencing over the offline RAM-agent wire peer."""

import json

import pytest

from ds2 import DS2Error
from ms42_ms43 import FlashInterrupted
from ms42_ms43_image import checksum_status, image_identity
from ms42_ms43_vin import AIF_SIZE, PAIRING_STATUS
from ms42_ms43_vin_write import PAIRING_LAYOUT, virginize, write_vin
from tests.test_ms42_ms43 import Journal, reply
from tests.test_ms42_ms43_agent import packet
from tests.test_ms42_ms43_agent_workflow import agent_workflow as agent_workflow
from tests.test_ms42_ms43_compatibility_baud import compatibility as compatibility, assert_handoff
from tests.test_ms42_ms43_vin_workflow import vin_workflow as vin_workflow, seed_history


@pytest.fixture
def virgin_workflow(vin_workflow, monkeypatch):
    ecu, peer, address = vin_workflow
    seed_history(peer, address, "full")
    start, count, _ = PAIRING_LAYOUT[ecu.family]
    status_address = status_for(ecu, peer)
    peer.image[start:start + count] = bytes(range(count))
    stock = peer.stock

    def respond(command, args):
        assert command not in (0x6C, 0x6D), "Virginizing must never start/acknowledge EWS alignment"
        if command == 0x06 and int.from_bytes(args[:4], "big") == status_address:
            assert peer.restored and args[4] == 2
            unpaired = peer.image[start:start + count] == b"\xFF" * count
            return reply(b"\xFF\xFF" if unpaired else b"\xAA\x55")
        return stock(command, args)

    monkeypatch.setattr(peer, "stock", respond)
    return ecu, peer, address


def status_for(ecu, peer):
    software = image_identity(peer.original, ecu.family)["software_id"]
    return PAIRING_STATUS[ecu.family][software]


def target_image(ecu, original, address, *, clear_vin=True):
    target = bytearray(original)
    if clear_vin:
        target[address:address + AIF_SIZE] = b"\xFF" * AIF_SIZE
    start, count, _ = PAIRING_LAYOUT[ecu.family]
    target[start:start + count] = b"\xFF" * count
    return bytes(target)


@pytest.mark.parametrize("clear_vin", (False, True))
def test_virginize_changes_only_selected_regions(virgin_workflow, clear_vin):
    ecu, peer, address = virgin_workflow
    original, journal, progress = bytes(peer.image), Journal(), []
    expected = target_image(ecu, original, address, clear_vin=clear_vin)
    result = virginize(ecu, clear_vin=clear_vin, journal=journal,
                       progress_cb=lambda *args: progress.append(args))
    assert result["virginized"] and not result["already_virginized"]
    assert result["vin_cleared"] is clear_vin
    if clear_vin:
        assert result["vin"] is result["native_vin"] is None and result["programming_count"] == 0
        assert result["aif_raw"] == b"\xFF" * AIF_SIZE
    else:
        assert result["vin"] == result["native_vin"] == "WBAAM31010FG00013"
        assert result["programming_count"] == 14
        assert result["aif_raw"] == original[address:address + AIF_SIZE]
    assert bytes(peer.image) == expected
    assert checksum_status(original, ecu.family) == checksum_status(expected, ecu.family)
    assert peer.erases == [0] and not peer.agent_finalizations and not peer.finalizations
    assert peer.restored and ecu.rate_confirmed and ecu.baud == 9600
    assert ecu.last_write.completed and ecu.last_write.verified and not ecu.can_retry_write
    assert ecu.last_write.operation == "virginize"
    backup = next(fields for event, fields in journal.events if event == "virginize_backup")
    assert bytes.fromhex(backup["original_hex"]) == original[:0x4000]
    assert bytes.fromhex(backup["target_hex"]) == expected[:0x4000]
    assert backup["clear_vin"] is clear_vin
    assert progress[-1] == (1, 1, "ECU virginized and verified")
    status_address = status_for(ecu, peer)
    assert sum(command == 6 and int.from_bytes(args[:4], "big") == status_address
               for wire, command, args in peer.requests if wire == "ds2") == 1


@pytest.mark.parametrize("fault", ("erase_rejected", "erase_ack", "program_partial", "program_ack", "last_ack"))
@pytest.mark.parametrize("clear_vin", (False, True))
def test_interrupted_virginize_retains_original_target_and_requires_explicit_retry(
        virgin_workflow, monkeypatch, fault, clear_vin):
    ecu, peer, address = virgin_workflow
    expected = target_image(ecu, bytes(peer.image), address, clear_vin=clear_vin)
    request, fired = peer.agent_request, []

    def inject(command, offset, count, data):
        match = (command == ord("E") if fault.startswith("erase") else
                 command == ord("P") and (offset == 0x3C00 if fault == "last_ack" else True))
        if match and not fired:
            fired.append(True)
            if fault.endswith("ack"):
                request(command, offset, count, data)
                return b""
            if fault == "program_partial":
                peer.image[offset:offset + 2] = data[:2]
            return packet(command, offset, count, status=4)
        return request(command, offset, count, data)

    monkeypatch.setattr(peer, "agent_request", inject)
    with pytest.raises(FlashInterrupted):
        virginize(ecu, clear_vin=clear_vin, journal=Journal())
    assert fired and ecu.can_retry_write and peer.mode == "agent" and not peer.restored
    before = len(peer.agent_requests)
    for operation in (lambda: ecu.identify(), lambda: ecu.write_tune(b""),
                      lambda: virginize(ecu, journal=Journal()),
                      lambda: write_vin(ecu, "WBAAM31010FG54321", journal=Journal())):
        with pytest.raises(DS2Error, match="incomplete identity write|incomplete write"):
            operation()
    assert len(peer.agent_requests) == before
    result = ecu.retry_write(journal=Journal())
    assert result["virginized"] and bytes(peer.image) == expected
    assert result["vin_cleared"] is clear_vin
    assert peer.erases == [0] and peer.agent_launches == 1
    assert not peer.agent_finalizations and not peer.finalizations
    retry = peer.agent_requests[before:]
    assert retry[0][0] == ord("I") and retry[1][0] == ord("R")
    if fault == "last_ack":
        assert not any(command in (ord("E"), ord("P")) for command, *_ in retry)


@pytest.mark.parametrize("clear_vin", (False, True))
def test_already_virginized_does_not_erase_or_program(virgin_workflow, clear_vin):
    ecu, peer, address = virgin_workflow
    peer.image[:] = target_image(ecu, bytes(peer.image), address, clear_vin=clear_vin)
    result = virginize(ecu, clear_vin=clear_vin, journal=Journal())
    assert result["virginized"] and result["already_virginized"]
    assert not peer.erases and not peer.programs and not peer.agent_finalizations
    assert ecu.last_write.bytes_written == 0 and peer.restored


def test_durable_backup_precedes_erase(virgin_workflow, tmp_path):
    ecu, peer, address = virgin_workflow
    original = bytes(peer.image)
    virginize(ecu, clear_vin=True, backup_dir=tmp_path)
    records = [json.loads(line) for line in next(tmp_path.glob("virginize-*.jsonl")).read_text().splitlines()]
    backup = next(i for i, record in enumerate(records) if record["event"] == "virginize_backup")
    erase = next(i for i, record in enumerate(records) if record["event"] == "erase_requested")
    assert backup < erase
    assert bytes.fromhex(records[backup]["fields"]["original_hex"]) == original[:0x4000]
    assert bytes.fromhex(records[backup]["fields"]["target_hex"]) == target_image(ecu, original, address)[:0x4000]
    assert records[-1]["fields"]["outcome"] == "success"


def test_backup_failure_leaves_flash_untouched(virgin_workflow):
    ecu, peer, _ = virgin_workflow
    original = bytes(peer.image)

    class FailedBackup(Journal):
        def append(self, event, **fields):
            if event == "virginize_backup":
                raise OSError("disk full")
            return super().append(event, **fields)

    with pytest.raises(FlashInterrupted, match="disk full"):
        virginize(ecu, journal=FailedBackup())
    assert bytes(peer.image) == original and not peer.erases and not peer.programs
    assert peer.restored and not ecu.can_retry_write


@pytest.mark.parametrize("fault", ("reset_ack", "paired_status", "journal_finish"))
def test_completed_write_never_replays_or_aligns_after_followup_failure(virgin_workflow, monkeypatch, fault):
    ecu, peer, address = virgin_workflow
    expected = target_image(ecu, bytes(peer.image), address)
    peer.agent_fault = fault
    stock = peer.stock

    def respond(command, args):
        if (fault == "paired_status" and command == 6
                and int.from_bytes(args[:4], "big") == status_for(ecu, peer)):
            return reply(b"\xAA\x55")
        return stock(command, args)

    monkeypatch.setattr(peer, "stock", respond)

    class FailedFinish(Journal):
        def finish(self, outcome, **fields):
            if fault == "journal_finish":
                raise OSError("completion journal unavailable")
            return super().finish(outcome, **fields)

    with pytest.raises(DS2Error) as error:
        virginize(ecu, clear_vin=True, journal=FailedFinish())
    assert error.value.programming_completed
    assert bytes(peer.image) == expected and ecu.last_write.completed and ecu.last_write.verified
    assert not ecu.can_retry_write and ecu._pending_vin is None
    assert not peer.agent_finalizations and not peer.finalizations


def test_changed_connection_cannot_resume_virginize(virgin_workflow):
    ecu, peer, _ = virgin_workflow
    peer.agent_fault = "boot_program_ack"
    with pytest.raises(FlashInterrupted):
        virginize(ecu, journal=Journal())
    ecu.transport._ser = object()
    assert not ecu.can_retry_write
    with pytest.raises(DS2Error, match="original open RAM-agent"):
        ecu.retry_write(journal=Journal())


@pytest.mark.parametrize("field", ("aif", "pairing", "software"))
def test_changed_native_layout_is_rejected_before_flash_requests(virgin_workflow, monkeypatch, field):
    ecu, peer, _ = virgin_workflow
    if field == "pairing":
        pointer = 0x3C04 if ecu.family == "MS42" else 0x0250
        peer.image[pointer] ^= 2
    elif field == "software":
        peer.unsupported = "software"
    else:
        stock = peer.stock

        def respond(command, args):
            frame = stock(command, args)
            if command == 0x0D:
                payload = bytearray(frame[3:-1])
                payload[62] ^= 2
                return reply(payload)
            return frame

        monkeypatch.setattr(peer, "stock", respond)
    original = bytes(peer.image)
    with pytest.raises(DS2Error, match="verified firmware layout|supported RAM-agent"):
        virginize(ecu, journal=Journal())
    assert bytes(peer.image) == original and not peer.erases and not peer.programs
    assert not ecu.can_retry_write


@pytest.mark.parametrize("fault", ("authorization_timeout_seed", "upload_ack"))
def test_entry_failure_preserves_existing_readiness_policy(virgin_workflow, fault):
    ecu, peer, _ = virgin_workflow
    peer.fault = fault
    with pytest.raises(FlashInterrupted):
        virginize(ecu, journal=Journal())
    assert not peer.erases and not peer.programs and not ecu.can_retry_write
    assert ecu.baud_uncertain is (fault != "authorization_timeout_seed")


def test_virginize_uses_selected_compatibility_rate(virgin_workflow, compatibility):
    ecu, peer, _ = virgin_workflow
    virginize(ecu, journal=Journal())
    assert_handoff(peer, compatibility)


def test_default_preserves_vin_and_history(virgin_workflow):
    ecu, peer, address = virgin_workflow
    expected = target_image(ecu, bytes(peer.image), address, clear_vin=False)
    result = virginize(ecu, journal=Journal())
    assert not result["vin_cleared"] and bytes(peer.image) == expected
    assert result["vin"] == "WBAAM31010FG00013" and result["programming_count"] == 14


def test_pairing_only_can_preserve_malformed_history_without_blocking_reset(virgin_workflow):
    ecu, peer, address = virgin_workflow
    peer.image[address:address + 46] = b"\xFF" * 46
    expected = target_image(ecu, bytes(peer.image), address, clear_vin=False)
    result = virginize(ecu, journal=Journal())
    assert result["virginized"] and not result["vin_cleared"]
    assert bytes(peer.image) == expected and result["aif_raw"] == expected[address:address + AIF_SIZE]
    assert "preserved but could not be decoded" in result["aif_warnings"][0]
    assert ecu.last_write.completed and not ecu.can_retry_write


@pytest.mark.parametrize("value", (None, "false", 1))
def test_clear_vin_rejects_ambiguous_options_before_any_request(virgin_workflow, value):
    ecu, peer, _ = virgin_workflow
    with pytest.raises(ValueError, match="True or False"):
        virginize(ecu, clear_vin=value, journal=Journal())
    assert not peer.requests
