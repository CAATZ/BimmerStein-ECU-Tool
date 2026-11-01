"""Native E39 word order, checksum publication and old-archive recovery."""

from dataclasses import replace
from functools import reduce
from operator import xor

import pytest

from ds2 import DS2Timeout
import ms42_ms43_transmission as e39
import transmission_conversion as tx
from transmission_swap_journal import SwapOperationJournal


class ClusterBus:
    def __init__(self, profile="KMB", ci=5, automatic=True):
        self.memory = bytearray((index * 13 + 5) & 255 for index in range(258))
        self.memory[88] = 0xA7 if profile == "KMB" else 0xFF
        self.memory[140:160] = tx.encode_zcs(tx.Zcs(bytes(4), bytes(8), bytes(5)))
        self.memory[180] = 0x2D | (0x40 if automatic else 0)
        self.memory[89] = reduce(xor, self.memory[90:256])
        payload = bytearray(14)
        payload[5] = ci
        payload[6] = 0x32 if profile == "KMB" else 1
        self.ident = self.response(payload)
        self.frames = []
        self.writes = []
        self.fail_word = None
        self.apply_before_failure = False

    @staticmethod
    def response(payload=b""):
        data = bytes((0x80, len(payload) + 4, 0xA0)) + payload
        return data + bytes((reduce(xor, data),))

    @staticmethod
    def identify():
        return b"1406464 connected identity"

    def send_frame(self, frame, **_kwargs):
        self.frames.append(frame)
        assert reduce(xor, frame) == 0 and frame[0] == 0x80
        if frame[2] == 0:
            return self.ident
        assert frame[3:5] == b"\x03\x00"
        word = int.from_bytes(frame[5:7], "big")
        count = frame[7]
        assert 1 <= count <= 8
        start, end = word * 2, (word + count) * 2
        if frame[2] == 6:
            return self.response(self.memory[start:end])
        assert frame[2] == 7 and len(frame[8:-1]) == count * 2
        failing = word == self.fail_word
        if not failing or self.apply_before_failure:
            self.memory[start:end] = frame[8:-1]
        self.writes.append((word, bytes(frame[8:-1])))
        if failing:
            self.fail_word = None
            raise DS2Timeout("simulated interruption")
        return self.response()


def _session(bus):
    state = tx.read_ms41_cluster_store(bus, "E39")
    target = tx.build_ms41_cluster_target(
        state, bytes(bus.memory[140:160]), tx.Transmission.MANUAL)
    return tx.ConnectedSwapSession(
        "0123456789abcdef0123456789abcdef", "ready", "Ready", (), (), (),
        tx.Transmission.AUTOMATIC, tx.Transmission.MANUAL, "MS41.2", "E39",
        dme_ident=bus.identify(), program="1406464", order_format=tx.OrderFormat.ZCS,
        cluster=state, target_cluster=target,
        eeprom_before=bytes.fromhex("AD A5 53 01"),
        eeprom_target=bytes.fromhex("AE A5 54 01"), eeprom_variant="MS41.2",
    )


@pytest.mark.parametrize("diagnostic", (0x30, 0x31, 0x32, 0x39, 0x40))
def test_cluster_diagnostic_family_uses_bcd_from_raw_response(diagnostic):
    bus = ClusterBus()
    raw = bytearray(bus.ident)
    raw[9] = diagnostic
    raw[-1] = reduce(xor, raw[:-1])
    bus.ident = bytes(raw)
    state = tx.read_ms41_cluster_store(bus, "E39")
    assert state.profile == "KMB"
    assert state.ident == bytes(raw)
    assert not bus.writes


@pytest.mark.parametrize("diagnostic", (0x20, 0x29, 0x41, 0x1E, 0x2A, 0xFF))
def test_unrelated_or_invalid_diagnostic_family_is_rejected_before_storage_read(diagnostic):
    bus = ClusterBus()
    raw = bytearray(bus.ident)
    raw[9] = diagnostic
    raw[-1] = reduce(xor, raw[:-1])
    bus.ident = bytes(raw)
    with pytest.raises(ValueError):
        tx.read_ms41_cluster_store(bus, "E39")
    assert len(bus.frames) == 1 and not bus.writes


@pytest.mark.parametrize("profile,ci", [("KMB", ci) for ci in range(3, 8)] + [("IKE", 5)])
@pytest.mark.parametrize("automatic", (False, True))
def test_native_checksum_read_target_write_and_exact_restore(profile, ci, automatic):
    bus = ClusterBus(profile, ci, automatic)
    if profile == "KMB" and ci == 7:
        bus.memory.extend((index * 17 + 3) & 255 for index in range(190))
        bus.memory[257] = 0
        bus.memory[257] = reduce(xor, bus.memory[256:448])
    original = bytes(bus.memory)
    state = tx.read_ms41_cluster_store(bus, "E39")
    assert state.data[:2] == original[88:90]
    assert original[88] != original[89]
    tx._validate_cluster_checksum(state)
    assert state.checksum_byte_offset == 89
    mode = tx.Transmission.MANUAL if automatic else tx.Transmission.AUTOMATIC
    target = tx.build_ms41_cluster_target(state, original[140:160], mode)
    expected = bytearray(original)
    expected[180] ^= 0x40
    expected[89] ^= 0x40
    assert target == expected[88:256]

    verified = tx.write_cluster_store(bus, state, target)
    assert verified.data == target and bus.memory == expected
    assert bus.writes == [(0x5A, bytes(expected[180:182])),
                          (0x2C, bytes(expected[88:90]))]
    bus.writes.clear()
    tx.restore_cluster_store(bus, state)
    assert bus.memory == original
    assert bus.writes[-1] == (0x2C, original[88:90])


@pytest.mark.parametrize("family", ("MS41", "MS42", "MS43"))
@pytest.mark.parametrize("changed_byte", (88, 89, 90, 91, 254, 255, 256, 257, 258, 447))
def test_c07_checksum_range_matches_bmw_coding_script(family, changed_byte):
    # A_KMB2.ipo Cod/NettoDat select words 0x2D..0x7F, destination byte89.
    bus = ClusterBus(ci=7)
    bus.memory.extend((index * 17 + 3) & 255 for index in range(190))
    original_checksum = bus.memory[89]
    bus.memory[changed_byte] ^= 0x80
    untouched_second_bank = bytes(bus.memory[256:448])
    state = (tx.read_ms41_cluster_store(bus, "E39") if family == "MS41"
             else e39.read_e39_cluster(bus, validate=False))
    target = bytearray(state.data)
    tx._cluster_checksum_image(state, target)

    expected = original_checksum ^ (0x80 if 90 <= changed_byte <= 255 else 0)
    assert target[1] == expected
    assert target[:1] + target[2:] == state.data[:1] + state.data[2:]
    assert bytes(bus.memory[256:448]) == untouched_second_bank
    reads = [frame for frame in bus.frames if frame[2] == 6]
    assert reads
    assert all(0x2C <= int.from_bytes(frame[5:7], "big")
               and int.from_bytes(frame[5:7], "big") + frame[7] <= 0x80
               for frame in reads)


@pytest.mark.parametrize("fail_word,applied", ((0x5A, True), (0x2C, False), (0x2C, True)))
def test_interrupted_write_restores_even_when_native_checksum_is_invalid(fail_word, applied):
    bus = ClusterBus()
    session = _session(bus)
    original = bytes(bus.memory)
    bus.fail_word, bus.apply_before_failure = fail_word, applied
    with pytest.raises(DS2Timeout, match="simulated interruption"):
        tx.write_cluster_store(bus, session.cluster, session.target_cluster)
    if fail_word == 0x5A or not applied:
        assert bus.memory[89] != reduce(xor, bus.memory[90:256])
    tx.restore_cluster_store(bus, session.cluster)
    assert bus.memory == original


def test_connected_write_archive_and_final_verification(monkeypatch, tmp_path):
    bus = ClusterBus()
    session = _session(bus)
    monkeypatch.setattr(tx, "_assert_egs_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tx, "read_ms41_runtime_transmission", lambda *_args: session.source)
    journal = SwapOperationJournal(tmp_path / "journal")
    tx.write_connected_modules(bus, session, archive_dir=tmp_path, journal=journal,
                               eeprom_current=session.eeprom_before)
    archived = tx.load_connected_swap_archive(session.archive_path)
    assert archived.cluster == session.cluster
    assert archived.target_cluster == session.target_cluster
    assert session.phase == "modules_written"
    tx.mark_eeprom_write_intent(session, journal)
    tx.mark_eeprom_written(session, session.eeprom_target, journal=journal)
    resumed = tx.load_connected_swap_journal(journal.load(session.token))
    assert resumed.phase == "awaiting_cycle"
    monkeypatch.setattr(tx, "read_ms41_runtime_transmission", lambda *_args: session.target)
    result = tx.verify_connected_swap(bus, resumed, eeprom_after=session.eeprom_target,
                                      journal=journal)
    assert result["completed"] and journal.load(session.token).phase == "verified"


@pytest.mark.parametrize("profile,ci", [("KMB", ci) for ci in range(3, 8)] + [("IKE", 5)])
def test_legacy_valid_target_remains_writable(tmp_path, profile, ci):
    bus = ClusterBus(profile, ci)
    session = _session(bus)
    session.cluster = replace(session.cluster, checksum_byte_offset=None)
    original = bytes(bus.memory)
    path = tx.archive_connected_swap(session, tmp_path)
    archive_bytes = path.read_bytes()
    restored = tx.load_connected_swap_archive(path)
    assert restored.cluster.checksum_byte_offset == 89
    assert restored.target_cluster == session.target_cluster
    tx.write_cluster_store(bus, restored.cluster, restored.target_cluster)
    tx.restore_cluster_store(bus, restored.cluster)
    assert bus.memory == original and path.read_bytes() == archive_bytes


@pytest.mark.parametrize("damage", ("old_checksum", "flags"))
@pytest.mark.parametrize("partial,phase", ((False, "failed"), (True, "failed"),
                                          (False, "awaiting_cycle")))
def test_legacy_archive_invalid_target_is_not_written_or_verified_but_original_restores(
        monkeypatch, tmp_path, damage, partial, phase):
    bus = ClusterBus()
    session = _session(bus)
    original = bytes(bus.memory)
    session.cluster = replace(session.cluster, checksum_byte_offset=None)
    broken = bytearray(session.target_cluster)
    if damage == "old_checksum":
        broken[0], broken[1] = broken[1], original[89]
    else:
        broken[0] ^= 1
    session.target_cluster = bytes(broken)
    path = tx.archive_connected_swap(session, tmp_path)
    archive_bytes = path.read_bytes()
    journal = SwapOperationJournal(tmp_path / "journal")
    journal.create(session.token, plan={
        "token": session.token, "family": session.family, "chassis": session.chassis,
        "source": session.source.value, "target": session.target.value,
    }, archive_path=path)
    journal.mark_write_intent(session.token, "cluster")
    if phase == "failed":
        journal.mark_failed(session.token, "old write interrupted")
    else:
        journal.mark_write_complete(session.token, "cluster")
        journal.mark_awaiting_cycle(session.token)
    recovered = tx.load_connected_swap_journal(journal.load(session.token))
    assert recovered.phase == "recovery"
    assert recovered.cluster.checksum_byte_offset == 89
    assert recovered.cluster.data == session.cluster.data
    assert recovered.target_cluster == session.target_cluster
    bus.frames.clear()

    with pytest.raises(ValueError, match="checksum|preserved flags"):
        tx.write_cluster_store(bus, recovered.cluster, recovered.target_cluster)
    with pytest.raises(ValueError, match="checksum|preserved flags"):
        tx.write_connected_modules(bus, tx.load_connected_swap_archive(path),
                                   archive_dir=tmp_path,
                                   eeprom_current=recovered.eeprom_before)
    with pytest.raises(ValueError, match="checksum|preserved flags"):
        tx.recover_connected_modules(bus, recovered, "target", journal=journal,
                                     journal_id="retry", supersedes=session.token,
                                     eeprom_current=recovered.eeprom_before)
    recovered.phase = "awaiting_cycle"
    with pytest.raises(ValueError, match="checksum|preserved flags"):
        tx.verify_connected_swap(bus, recovered, eeprom_after=recovered.eeprom_target,
                                  journal=journal)
    assert not bus.frames and not bus.writes
    assert journal.load(session.token).phase == phase

    # Retain the old target as evidence of what an interrupted write could contain.
    start = 2 if partial else 0
    bus.memory[88 + start:256] = broken[start:]
    monkeypatch.setattr(tx, "_assert_egs_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tx, "read_ms41_runtime_transmission", lambda *_args: session.source)
    tx.recover_connected_modules(bus, recovered, "original", journal=journal,
                                 journal_id="restore", supersedes=session.token,
                                 eeprom_current=recovered.eeprom_before)
    recovered = tx.load_connected_swap_journal(journal.load("restore"))
    assert recovered.phase == "rollback_awaiting_cycle"
    result = tx.verify_connected_original(bus, recovered, journal=journal,
                                          journal_id="restore",
                                          eeprom_after=recovered.eeprom_before)
    tx.settle_superseded_connected_swaps(journal, "restore")
    assert result["restored"] and bus.memory == original
    assert tx.recoverable_connected_swaps(journal) == ()
    assert path.read_bytes() == archive_bytes
