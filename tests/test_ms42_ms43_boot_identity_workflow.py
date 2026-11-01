"""Real boot-write backend with a strict fake wire; no serial-port access."""

from hashlib import sha256

import pytest

import ms42_ms43 as protocol
from ms42_ms43_identity import IDENTITY_GRAFT_RANGES, IDENTITY_OFFSET, IDENTITY_SIZE
from ms42_ms43_vin import AIF_ADDRESSES, AIF_SIZE, encode_vin
from tests.test_ms42_ms43 import Journal
from tests.test_ms42_ms43_agent import packet
from tests.test_ms42_ms43_agent_workflow import agent_workflow as agent_workflow, boot_target
from tests.test_ms42_ms43_ram_write import expected_array


def identity_bytes(image, family, *, live, history="full"):
    """Different donor/unit data without changing executable or HW compatibility."""
    start = AIF_ADDRESSES[family]
    image[start:start + AIF_SIZE] = b"\xFF" * AIF_SIZE
    if history == "full":
        for index in range(14):
            record = bytearray(46)
            record[:13] = encode_vin(f"WBAAM31010FG{(10000 if live else 20000) + index:05d}")
            record[14:17] = bytes((0x7B, 0x00, 0x00))
            image[start + index * 46:start + (index + 1) * 46] = record
    elif history == "interrupted":
        image[start + 2:start + 46] = b"\x26" * 44
    pairing = 0x3EDE if family == "MS42" else 0x3ED4
    image[pairing:0x3F20] = b"\xFF" * (0x3F20 - pairing)
    image[pairing:pairing + 6] = bytes.fromhex("CB ED 34 ED CB 12" if live else "87 A9 78 A9 87 56")
    if family == "MS43":
        image[pairing + 18:pairing + 24] = b"\xFE\xFE" * 3
    supplier = 0x3C40 if family == "MS42" else 0x3C36
    image[supplier:supplier + 10] = b"0000444444" if live else b"0000666666"
    image[0x3F80:0x3F88] = b"5WKLIVE1" if live else b"5WKDONR1"
    image[0x3F8C:0x3F94] = b"11112222" if live else b"99998888"
    image[0x3F98:0x3F9A] = b"23" if live else b"24"
    image[0x3F9C:0x3F9E] = b"12" if live else b"34"


def prepare(peer, history="full"):
    identity_bytes(peer.image, peer.family, live=True, history=history)
    peer.original = bytes(peer.image)
    target = bytearray(boot_target(peer))
    identity_bytes(target, peer.family, live=False)
    effective = bytearray(target)
    for start, end in IDENTITY_GRAFT_RANGES[peer.family]:
        effective[start:end] = peer.original[start:end]
    return bytes(target), bytes(effective)


def assert_written(peer, expected):
    actual = expected_array(peer, expected, "full")
    actual[:0x4000] = expected[:0x4000]
    actual[0x8000:0x10000] = expected[0x8000:0x10000]
    assert bytes(peer.image) == bytes(actual)
    assert peer.image[0x4000:0x8000] == peer.original[0x4000:0x8000]


@pytest.mark.parametrize("preserve", [None, False])
def test_boot_identity_is_live_by_default_and_donor_only_on_opt_out(agent_workflow, preserve):
    ecu, peer = agent_workflow
    target, effective = prepare(peer)
    journal = Journal()
    kwargs = {} if preserve is None else {"preserve_identity": preserve}
    with ecu.transfer():
        state = ecu.write_full(target, use_ram=True, use_agent=True, write_boot=True,
                               journal=journal, **kwargs)
    expected = effective if preserve is None else target
    assert_written(peer, expected)
    assert state.completed and state.target_sha256 == sha256(expected).hexdigest()
    grafts = [fields for event, fields in journal.events if event == "identity_grafted"]
    assert len(grafts) == int(preserve is None)
    first_mutation = next(i for i, (command, *_rest) in enumerate(peer.agent_requests)
                          if command in (ord("E"), ord("P")))
    reads = [(offset, count) for command, offset, count, _data in peer.agent_requests[:first_mutation]
             if command == ord("R") and offset in (0x3C00, 0x0250)]
    assert reads == ([(0x3C00, 1024)] + ([(0x0250, 4)] if peer.family == "MS43" else [])
                     if preserve is None else [])


@pytest.mark.parametrize("history", ["empty", "interrupted"])
def test_empty_or_interrupted_live_history_is_copied_raw(agent_workflow, history):
    ecu, peer = agent_workflow
    target, effective = prepare(peer, history)
    with ecu.transfer():
        state = ecu.write_full(target, use_ram=True, use_agent=True, write_boot=True, journal=Journal())
    assert_written(peer, effective)
    start = AIF_ADDRESSES[peer.family]
    assert peer.image[start:start + AIF_SIZE] == peer.original[start:start + AIF_SIZE]
    assert state.completed


def test_snapshot_and_effective_hash_exist_before_first_flash_mutation(agent_workflow, monkeypatch):
    ecu, peer = agent_workflow
    target, effective = prepare(peer)
    journal = Journal()
    original_request = peer.agent_request
    mutations = []

    def inspect_before_flash(command, offset, count, data):
        if command in (ord("E"), ord("P")):
            grafts = [fields for event, fields in journal.events if event == "identity_grafted"]
            assert len(grafts) == 1
            graft = grafts[0]
            assert graft["source_offset"] == IDENTITY_OFFSET
            assert bytes.fromhex(graft["source_data"]) == peer.original[IDENTITY_OFFSET:IDENTITY_OFFSET + IDENTITY_SIZE]
            assert graft["target_sha256"] == sha256(effective).hexdigest()
            assert graft["pairing_pointer"] == (peer.original[0x250:0x254].hex()
                                                if peer.family == "MS43" else None)
            assert ecu._pending_image == effective
            assert ecu.last_write.target_sha256 == graft["target_sha256"]
            mutations.append((command, offset))
        return original_request(command, offset, count, data)

    monkeypatch.setattr(peer, "agent_request", inspect_before_flash)
    with ecu.transfer():
        state = ecu.write_full(target, use_ram=True, use_agent=True, write_boot=True, journal=journal)
    assert mutations and state.completed
    first_journal_mutation = next(i for i, (event, _fields) in enumerate(journal.events)
                                  if event in ("erase_requested", "program_requested"))
    assert any(event == "identity_grafted" for event, _fields in journal.events[:first_journal_mutation])


def test_explicit_retry_retains_original_graft_after_sector_zero_is_lost(agent_workflow, monkeypatch):
    ecu, peer = agent_workflow
    target, effective = prepare(peer)
    journal = Journal()
    original_request = peer.agent_request
    fault = [True]

    def fail_sector_zero(command, offset, count, data):
        if fault[0] and command == ord("P") and offset < 0x4000:
            assert 0 in peer.erases
            return packet(command, offset, count, status=4)
        return original_request(command, offset, count, data)

    monkeypatch.setattr(peer, "agent_request", fail_sector_zero)
    with pytest.raises(protocol.FlashInterrupted):
        with ecu.transfer():
            ecu.write_full(target, use_ram=True, use_agent=True, write_boot=True, journal=journal)
    retained = ecu._pending_writer
    assert ecu.can_retry_write and retained.active
    assert ecu._pending_image == effective and retained._grafted_image == effective
    assert ecu.last_write.target_sha256 == sha256(effective).hexdigest()
    assert bytes(peer.image[:0x4000]) == b"\xFF" * 0x4000
    # A new graft from this partial write would lose the original VIN/ISN.
    peer.image[IDENTITY_OFFSET:IDENTITY_OFFSET + IDENTITY_SIZE] = b"\xA5" * IDENTITY_SIZE
    fault[0] = False
    retry = Journal()
    with ecu.transfer():
        state = ecu.retry_write(journal=retry)
    assert_written(peer, effective)
    assert retained._grafted_image == effective and state.completed
    assert state.target_sha256 == sha256(effective).hexdigest()
    assert sum(event == "identity_grafted" for event, _fields in journal.events) == 1
    assert not any(event == "identity_grafted" for event, _fields in retry.events)
    assert peer.agent_launches == 1
