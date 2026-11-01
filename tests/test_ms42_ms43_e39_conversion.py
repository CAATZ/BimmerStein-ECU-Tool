"""E39 order, donor, wire-level coding and durable recovery regressions."""

from dataclasses import replace
from pathlib import Path
import csv
import re

import pytest

from ds2 import DS2Timeout
import transmission_conversion as tx
import ms42_ms43_transmission as e39
from ms42_ms43_transmission_profiles import (
    E39_DONORS, E39_EGS_RELEASE_HISTORY, E39_GM_SUFFIXES, E39_US_MANUAL_PROFILES,
)
from transmission_swap_journal import SwapOperationJournal


def order(manual, automatic=False, short=False):
    profile = E39_DONORS[manual]
    sa = 0x04000480 | (0x0008000000000000 if short else 0)
    if automatic:
        sa |= 0x0001000000000000
    return tx.Zcs(bytes.fromhex(profile[4] if automatic else manual)
                  + E39_GM_SUFFIXES.get(manual, b"\0\0"),
                  sa.to_bytes(8, "big"), bytes.fromhex("0123456789"))


@pytest.mark.parametrize("manual,profile", E39_DONORS.items())
@pytest.mark.parametrize("short", (False, True))
def test_all_e39_type_pairs_preserve_unrelated_order_and_select_exact_donors(manual, profile, short):
    original = order(manual, short=short)
    automatic = e39.e39_target_order(original, profile[0], tx.Transmission.AUTOMATIC)
    assert automatic.gm[:2].hex().upper() == profile[4]
    assert automatic.gm[2:] == original.gm[2:]
    assert int.from_bytes(automatic.sa, "big") ^ int.from_bytes(original.sa, "big") == 0x0001000000000000
    assert automatic.vn == original.vn
    assert e39.e39_target_order(automatic, profile[0], tx.Transmission.MANUAL) == original
    assert tx.decode_zcs(tx.encode_zcs(automatic)) == automatic
    expected = profile[6] if short and profile[6] is not None else profile[5]
    assert e39.e39_egs_admission(original, profile[0]) == (
        "GS8600" if profile[0] == "MS42" else "GS8604", E39_EGS_RELEASE_HISTORY[expected])


@pytest.mark.parametrize("manual,short,asc_manual,asc_auto,dsc_manual,dsc_auto", (
    ("5A11", False, 0, 2, 19, 20),
    ("5A31", False, 0, 1, 18, 10),
    ("5A31", True, 0, 1, 18, 11),
    ("5A51", False, 0, 1, 12, 10),
    ("5A51", True, 0, 1, 12, 11),
    ("5F11", False, 1, 1, 19, 20),
    ("5F31", False, 2, 2, 18, 9),
    ("5F31", True, 1, 1, 18, 9),
    ("5F51", False, 1, 1, 12, 9),
    ("5F51", True, 1, 1, 12, 11),
))
def test_ncs_variant_choices_and_dsc_checksum_delta(
        manual, short, asc_manual, asc_auto, dsc_manual, dsc_auto):
    zcs = order(manual, short=short)
    family = E39_DONORS[manual][0]
    for mode, asc, dsc in ((tx.Transmission.MANUAL, asc_manual, dsc_manual),
                           (tx.Transmission.AUTOMATIC, asc_auto, dsc_auto)):
        state = tx.CodingState("ASC57", b"id", 9, b"\x3A")
        assert e39.e39_stability_target(state, zcs, family, mode) == bytes((0x3A | asc << 6,))
        state = tx.CodingState("DSC57", b"id", 10, bytes.fromhex("A5 F2 0B C2"))
        target = e39.e39_stability_target(state, zcs, family, mode)
        assert target[0] == 0xA5 and target[2:] == bytes((dsc, 0xC2))
        assert sum(target[1:]) & 255 == sum(state.data[1:]) & 255


class E39Bus:
    """Independent DS2 response model; no real transport is constructed."""

    def __init__(self, family="MS43", *, automatic=True, stability="DSC57", ike=False):
        self.family = family
        self.manual = "5A51" if family == "MS42" else "5F51"
        self.raw = bytearray(tx.encode_zcs(order(self.manual, automatic)))
        self.tail = bytes(range(12))
        self.ews = bytearray((int(automatic), 0xA2, 0x73, 0xB4, 0x85))
        self.memory = bytearray(256)
        self.memory[140:160] = self.raw
        self.memory[180] = (0xC0 if ike else 0x40) if automatic else 0
        self.memory[88] = 0x3F if automatic else 0x37
        fuel = (0x0A41 if family == "MS42" else 0x0A73) if automatic else 0x0947
        self.memory[232:234] = (0xA000 | fuel).to_bytes(2, "big")
        # Independent fixture encoding: NCS KENNUNG B [90,255,89].
        self.memory[89] = 0
        for value in self.memory[90:256]:
            self.memory[89] ^= value
        self.cluster_ci, self.cluster_di = (5, 1) if ike else (6, 0x32)
        self.stability_name = stability
        self.stability_ci = 9 if stability == "ASC57" else 10
        code = (1 if automatic else 0) if family == "MS42" else 1
        dsc = (10 if family == "MS42" else 9) if automatic else 12
        self.stability = bytearray((0x3A | code << 6,) if stability == "ASC57"
                                   else (0xA5, (-dsc - 0xC2) & 255, dsc, 0xC2))
        self.active_automatic = automatic
        self.egs_present = not automatic
        self.egs_zb = E39_DONORS[self.manual][5]
        self.frames = []
        self.writes = []
        self.fail_stability_once = False

    def identify(self):
        return (b"7500255" if self.family == "MS42" else b"7551615") + b" fixture"

    @staticmethod
    def response(address, payload=b""):
        return tx._frame(bytes((address, len(payload) + 4, 0xA0)) + payload)

    def send_frame(self, frame, *, resp_addr, **_kwargs):
        assert len(frame) == frame[1] and tx._xor(frame) == 0
        self.frames.append(bytes(frame))
        address, _, command = frame[:3]
        assert address == resp_addr
        if address == 0x32 and not self.egs_present:
            raise DS2Timeout("disconnected EGS fixture")
        if command == 0:
            if address == 0x32:
                payload = bytearray(42)
                payload[7:9] = b"28" if self.family == "MS42" else b"2H"
            else:
                ci, di, size = {0x44: (81, 0x80, 12),
                                0x80: (self.cluster_ci, self.cluster_di, 14),
                                0x56: (self.stability_ci, 0, 12)}[address]
                payload = bytearray(size)
                payload[5], payload[6] = (ci // 10 << 4) | ci % 10, di
            return self.response(address, payload)
        if address == 0x44 and command == 0x69:
            return self.response(address, self.raw[:16] if frame[3] == 4 else self.raw[16:] + self.tail)
        if address == 0x44 and command == 0x6A:
            self.writes.append((address, command))
            if frame[3] == 4:
                self.raw[:16] = frame[5:-1]
            else:
                self.raw[16:] = frame[5:9]
                assert frame[9:-1] == self.tail
        elif address == 0x44 and command == 8:
            return self.response(address, self.ews)
        elif address == 0x44 and command == 9:
            self.writes.append((address, command))
            self.ews[:] = frame[3:-1]
        elif address == 0x80 and command in (6, 7):
            assert frame[3:5] == b"\x03\0"
            start, count = 2 * int.from_bytes(frame[5:7], "big"), frame[7] * 2
            if command == 6:
                return self.response(address, self.memory[start:start + count])
            self.writes.append((address, command))
            assert len(frame[8:-1]) == count
            self.memory[start:start + count] = frame[8:-1]
        elif address == 0x56 and command == 8:
            assert frame[3:5] == bytes((0, len(self.stability)))
            return self.response(address, self.stability)
        elif address == 0x56 and command == 9:
            self.writes.append((address, command))
            assert frame[3] == 0
            self.stability[:] = frame[4:-1]
            if self.fail_stability_once:
                self.fail_stability_once = False
                raise DS2Timeout("lost stability write acknowledgement")
        elif address == 0x12 and command == 0x0B:
            assert frame[3] == 0x94
            return self.response(address, bytes((0x10 if self.active_automatic else 0,)))
        elif address == 0x12 and command == 0x43:
            assert frame == tx.post_coding_frame(self.family)
            self.writes.append((address, command))
            self.active_automatic = self.raw[:2].hex().upper() == E39_DONORS[self.manual][4]
        elif address == 0x32 and command == 0x0D:
            payload = bytearray(63)
            payload[60:63] = b"\0\x11\0"
            return self.response(address, payload)
        elif address == 0x32 and command == 6:
            assert frame[3] == 0
            location = int.from_bytes(frame[4:7], "big")
            payload = bytearray(46) if location == 0x1100 else bytearray(b"\xFF" * 46)
            if location == 0x1100:
                payload[27:30] = self.egs_zb.to_bytes(3, "big")
                payload[30] = 1
            return self.response(address, payload)
        else:
            pytest.fail(f"unexpected request {frame.hex()}")
        return self.response(address)


def _set_cluster_type(bus, ci):
    bus.cluster_ci = ci
    bus.cluster_di = 0x10 if ci in (11, 12) else 0x32
    if ci in (11, 12):
        bus.memory[180] = 0x1D | (0xC0 if bus.active_automatic else 0)
        bus.memory[89] = 0
        for value in bus.memory[90:256]:
            bus.memory[89] ^= value


def _us_automatic_bus(manual, ci, steptronic, stability="DSC57", *, selector=None):
    bus = E39Bus("MS43", stability=stability)
    _set_cluster_type(bus, ci)
    automatic, fuel = {
        "5E33": ("5E43", 0x0A41),
        "5F33": ("5F43", 0x0A0F),
        "5F53": ("5F63", 0x0A73),
    }[manual]
    sa = 0x0020041004024400 | (0x0001000000000000 if steptronic else 0)
    bus.raw[:] = tx.encode_zcs(tx.Zcs(
        bytes.fromhex(automatic + "0000"), sa.to_bytes(8, "big"), bytes.fromhex("0123456789")))
    bus.memory[140:160] = bus.raw
    bus.memory[180] = (0x1D | (0xC0 if steptronic else 0x40)) if ci == 12 else 0x40
    if selector is not None:
        bus.memory[180] = (bus.memory[180] & ~0xE0) | selector
    bus.memory[232:234] = (0xA000 | fuel).to_bytes(2, "big")
    bus.memory[89] = 0
    for value in bus.memory[90:256]:
        bus.memory[89] ^= value
    bus.stability[:] = b"\x7A" if stability == "ASC57" else bytes.fromhex("A5 33 0B C2")
    return bus


@pytest.mark.parametrize("manual", ("5E33", "5F33", "5F53"))
@pytest.mark.parametrize("ci", (6, 7, 12))
@pytest.mark.parametrize("steptronic", (False, True))
@pytest.mark.parametrize("stability", ("ASC57", "DSC57"))
def test_us_e39_manual_conversion_preserves_interlock_and_all_owners(
        tmp_path, manual, ci, steptronic, stability):
    bus = _us_automatic_bus(manual, ci, steptronic, stability)
    original_memory, original_ews = bytes(bus.memory), bytes(bus.ews)
    original_order = tx.decode_zcs(bytes(bus.raw))
    session = tx.prepare_connected_swap(bus, "manual")
    assert session.ready, session.reasons
    assert not bus.writes
    assert session.target_ews_coding == original_ews
    assert any("retains" in message for message in session.warnings)
    journal = SwapOperationJournal(tmp_path / "journal")
    tx.write_connected_modules(bus, session, archive_dir=tmp_path, journal=journal)
    saved = tx.load_connected_swap_archive(session.archive_path)
    assert saved.cluster == session.cluster
    assert saved.target_ews_coding == original_ews == bytes(bus.ews)
    target_order = tx.decode_zcs(bytes(bus.raw))
    assert target_order.gm == bytes.fromhex(manual + "0000")
    assert int.from_bytes(target_order.sa, "big") == int.from_bytes(original_order.sa, "big") & ~0x0001000000000000
    assert target_order.vn == original_order.vn
    assert bus.memory[140:160] == bus.raw
    assert bus.memory[180] == (0x1D if ci == 12 else 0)
    assert bus.memory[88] == 0x37
    fuel = {"5E33": 0x0992, "5F33": 0x092E, "5F53": 0x0947}[manual]
    assert bus.memory[232:234] == (0xA000 | fuel).to_bytes(2, "big")
    expected_stability = (b"\x7A" if stability == "ASC57" else
                          bytes.fromhex("A5 2C 12 C2") if manual != "5F53" else
                          bytes.fromhex("A5 32 0C C2"))
    assert bus.stability == expected_stability
    changed = {88, 89, 180, 232, 233, *range(140, 160)}
    assert all(before == after for index, (before, after) in enumerate(
        zip(original_memory, bus.memory)) if index not in changed)
    checksum = 0
    for value in bus.memory[90:256]:
        checksum ^= value
    assert bus.memory[89] == checksum
    assert tx.verify_connected_swap(bus, session, journal=journal)["completed"]


@pytest.mark.parametrize("manual", ("5E33", "5F33", "5F53"))
@pytest.mark.parametrize("ci", (6, 7, 12))
@pytest.mark.parametrize("steptronic", (False, True))
def test_us_e39_lost_ack_restores_original_order_selector_and_interlock(
        tmp_path, manual, ci, steptronic):
    bus = _us_automatic_bus(manual, ci, steptronic)
    session = tx.prepare_connected_swap(bus, "manual")
    assert session.ready, session.reasons
    original = (bytes(bus.raw), bytes(bus.ews), bytes(bus.memory), bytes(bus.stability))
    bus.fail_stability_once = True
    with pytest.raises(DS2Timeout, match="lost stability"):
        tx.write_connected_modules(bus, session, archive_dir=tmp_path)
    assert (bytes(bus.raw), bytes(bus.ews), bytes(bus.memory), bytes(bus.stability)) == original
    assert session.phase == "rolled_back"
    assert (0x12, 0x43) not in bus.writes


@pytest.mark.parametrize("manual", ("5E33", "5F33", "5F53"))
def test_us_reverse_automatic_conversion_is_not_inferred_from_manual_support(manual):
    profile = E39_US_MANUAL_PROFILES[manual]
    zcs = tx.Zcs(bytes.fromhex(manual + "0000"), bytes(8), bytes(5))
    with pytest.raises(ValueError, match="production-date"):
        e39.e39_target_order(zcs, profile[0], tx.Transmission.AUTOMATIC)
    with pytest.raises(ValueError, match="reverse automatic"):
        e39.e39_egs_admission(zcs, profile[0])
    bus = _us_automatic_bus(manual, 12, False)
    session = tx.prepare_connected_swap(bus, "automatic")
    assert not session.ready and not bus.writes
    assert any("production-date" in reason for reason in session.reasons)


@pytest.mark.parametrize("manual", ("5E33", "5F33", "5F53"))
@pytest.mark.parametrize("steptronic", (False, True))
@pytest.mark.parametrize("lost_ack", (False, True))
def test_us_c12_recognized_automatic_encodings_share_manual_target_and_restore(
        tmp_path, manual, steptronic, lost_ack):
    targets = []
    for selector in (0x40, 0x80, 0xC0):
        bus = _us_automatic_bus(manual, 12, steptronic, selector=selector)
        original = (bytes(bus.raw), bytes(bus.ews), bytes(bus.memory), bytes(bus.stability))
        session = tx.prepare_connected_swap(bus, "manual")
        assert session.ready, session.reasons
        targets.append((session.target_zcs, session.target_ews_coding,
                        session.target_cluster, session.target_stability))
        if lost_ack:
            bus.fail_stability_once = True
            with pytest.raises(DS2Timeout, match="lost stability"):
                tx.write_connected_modules(bus, session, archive_dir=tmp_path)
            assert (bytes(bus.raw), bytes(bus.ews), bytes(bus.memory), bytes(bus.stability)) == original
            assert session.phase == "rolled_back"
        else:
            tx.write_connected_modules(bus, session, archive_dir=tmp_path)
            assert bus.memory[180] == 0x1D
            assert tx.verify_connected_swap(bus, session)["completed"]
    assert targets[0] == targets[1] == targets[2]


@pytest.mark.parametrize("selector", (0x20, 0x60, 0xA0, 0xE0))
def test_us_c12_unknown_selector_keeps_existing_rejection(selector):
    bus = _us_automatic_bus("5F33", 12, False, selector=selector)
    session = tx.prepare_connected_swap(bus, "manual")
    assert not session.ready and not bus.writes
    assert any("transmission value" in reason for reason in session.reasons)


@pytest.mark.parametrize("ci,diagnostic", (
    (6, 0x30), (7, 0x31), (7, 0x32), (7, 0x40),
    (11, 0x05), (11, 0x09), (12, 0x10), (12, 0x14)))
def test_bmw_diagnostic_family_bounds_and_bcd_coding_index(ci, diagnostic):
    bus = E39Bus("MS42")
    _set_cluster_type(bus, ci)
    bus.cluster_di = diagnostic
    session = tx.prepare_connected_swap(bus, "manual")
    assert session.ready, session.reasons
    assert session.cluster.coding_index == ci
    assert session.cluster.ident[8:10] == bytes(((ci // 10 << 4) | ci % 10, diagnostic))
    assert session.cluster.profile == ("IKE" if ci in (11, 12) else "KMB")
    assert not bus.writes


@pytest.mark.parametrize("ci,diagnostic", (
    (6, 0x29), (7, 0x41), (7, 0x20), (7, 0x1E),
    (11, 0x04), (12, 0x15), (12, 0x0E), (12, 0x32)))
def test_mismatched_cluster_layout_or_family_is_rejected_before_read(ci, diagnostic):
    bus = E39Bus("MS42")
    _set_cluster_type(bus, ci)
    bus.cluster_di = diagnostic
    with pytest.raises(ValueError):
        e39.read_e39_cluster(bus)
    assert len(bus.frames) == 1 and not bus.writes


def test_kmbi_c11_does_not_claim_m54_service_values():
    bus = E39Bus("MS43")
    _set_cluster_type(bus, 11)
    session = tx.prepare_connected_swap(bus, "manual")
    assert not session.ready and not bus.writes
    assert any("no reviewed M54 service" in reason for reason in session.reasons)


@pytest.mark.parametrize("ci", (6, 7))
@pytest.mark.parametrize("diagnostic", (0x48, 0x53, 0x54, 0x64))
def test_bmw_e46_diagnostic_ranges_keep_existing_route(monkeypatch, ci, diagnostic):
    # D_0080 converts BCD before selecting KOMBI46 48..53 / KOMBI46R 54..64.
    bus = E39Bus("MS43")
    bus.cluster_ci, bus.cluster_di = ci, diagnostic
    marker = object()
    seen = []
    monkeypatch.setattr(tx, "_prepare_e46_swap", lambda *args, **kwargs:
                        seen.append(kwargs["cluster_identity"]) or marker)
    monkeypatch.setattr(tx, "read_ews_zcs", lambda *_:
                        pytest.fail("E46 routing must not enter E39 preflight"))
    assert tx.prepare_connected_swap(bus, "manual") is marker
    assert seen[0][9] == diagnostic
    assert not bus.writes


@pytest.mark.parametrize("family,ci", (
    ("MS42", 6), ("MS42", 7), ("MS42", 11), ("MS42", 12),
    ("MS43", 6), ("MS43", 7), ("MS43", 12)))
@pytest.mark.parametrize("automatic", (False, True))
@pytest.mark.parametrize("stability", ("ASC57", "DSC57"))
def test_complete_e39_read_plan_write_archive_and_final_verify(tmp_path, family, automatic, stability, ci):
    bus = E39Bus(family, automatic=automatic, stability=stability)
    _set_cluster_type(bus, ci)
    second_bank = bytearray((index * 17 + 3) & 255 for index in range(192))
    second_bank[1] = 0
    second_checksum = 0
    for value in second_bank:
        second_checksum ^= value
    second_bank[1] = second_checksum
    bus.memory.extend(second_bank)
    target = tx.Transmission.MANUAL if automatic else tx.Transmission.AUTOMATIC
    session = tx.prepare_connected_swap(bus, target)
    assert session.ready, session.reasons
    assert session.family == family and session.chassis == "E39" and not bus.writes
    journal = SwapOperationJournal(tmp_path / "journal")
    result = tx.write_connected_modules(bus, session, archive_dir=tmp_path, journal=journal)
    assert result["requires_key_cycle"] and session.phase == "awaiting_cycle"
    restored_plan = tx.load_connected_swap_archive(session.archive_path)
    assert restored_plan.stability == session.stability
    assert restored_plan.target_stability == session.target_stability
    assert bus.stability == session.target_stability
    assert bus.raw == session.target_zcs == bus.memory[140:160]
    if ci in (11, 12):
        assert bus.memory[180] == (0x1D if automatic else 0xDD)
        assert restored_plan.cluster.profile == "IKE"
        assert restored_plan.cluster.transmission_mask == 0xE0
    else:
        assert bus.memory[180] & 0xC0 == (0 if automatic else 0x40)
    assert bus.memory[88] == (0x37 if automatic else 0x3F)
    fuel = 0x0947 if automatic else 0x0A41 if family == "MS42" else 0x0A73
    assert bus.memory[232:234] == (0xA000 | fuel).to_bytes(2, "big")
    checksum = 0
    for value in bus.memory[90:256]:
        checksum ^= value
    assert bus.memory[89] == checksum
    assert bus.memory[256:448] == second_bank
    assert bus.tail == bytes(range(12)) and bus.ews[1:] == bytes((0xA2, 0x73, 0xB4, 0x85))
    assert tx.verify_connected_swap(bus, session, journal=journal)["completed"]
    assert journal.load(session.token).phase == "verified"


@pytest.mark.parametrize("family,ci", (("MS42", 6), ("MS43", 6), ("MS42", 11), ("MS43", 12)))
def test_lost_ack_restores_every_attempted_owner_including_dsc_checksum(tmp_path, family, ci):
    bus = E39Bus(family)
    _set_cluster_type(bus, ci)
    session = tx.prepare_connected_swap(bus, "manual")
    original = (bytes(bus.raw), bytes(bus.ews), bytes(bus.memory), bytes(bus.stability))
    bus.fail_stability_once = True
    with pytest.raises(DS2Timeout, match="lost stability"):
        tx.write_connected_modules(bus, session, archive_dir=tmp_path)
    assert (bytes(bus.raw), bytes(bus.ews), bytes(bus.memory), bytes(bus.stability)) == original
    assert session.phase == "rolled_back" and not session.written
    assert (0x12, 0x43) not in bus.writes


@pytest.mark.parametrize("owner", ("stability", "cluster"))
@pytest.mark.parametrize("ci", (6, 12))
def test_interrupted_payload_is_restored_then_verified_through_existing_journal(tmp_path, owner, ci):
    bus = E39Bus()
    _set_cluster_type(bus, ci)
    session = tx.prepare_connected_swap(bus, "manual")
    archive = tx.archive_connected_swap(session, tmp_path)
    journal = SwapOperationJournal(tmp_path / "journal")
    journal.create(session.token, plan={"token": session.token, "family": session.family,
                   "chassis": "E39", "source": "automatic", "target": "manual"}, archive_path=archive)
    journal.mark_write_intent(session.token, owner)
    if owner == "stability":
        bus.stability[2] = session.target_stability[2]
    else:
        bus.memory[232] = session.target_cluster[232 - 88]
    journal.mark_failed(session.token, "interrupted payload")
    recovered = tx.load_connected_swap_journal(journal.load(session.token))
    result = tx.recover_connected_modules(bus, recovered, action="original", journal=journal,
                                          journal_id=session.token + "-restore", supersedes=session.token)
    assert result["requires_key_cycle"]
    assert bus.stability == session.stability.data
    assert bus.memory[88:256] == session.cluster.data
    assert tx.verify_connected_original(bus, recovered, journal=journal,
                                         journal_id=session.token + "-restore")["restored"]


@pytest.mark.parametrize("drift", ("donor", "stability", "order", "variant"))
def test_incompatible_or_changed_e39_state_never_writes(drift, tmp_path):
    bus = E39Bus(automatic=False)
    if drift == "donor":
        bus.egs_zb = 7546090  # Valid E46 assembly, wrong E39 vehicle.
    elif drift == "stability":
        bus.stability[2] = 18  # 2.5-litre DSC variant, not this 3.0-litre.
    elif drift == "order":
        bus.memory[140] ^= 1
    else:
        bus.raw = bytearray(tx.encode_zcs(replace(order(bus.manual), gm=bytes.fromhex("5F510003"))))
    session = tx.prepare_connected_swap(bus, "automatic")
    assert not session.ready and not bus.writes


@pytest.mark.parametrize("diagnostic", (0x54, 0x64))
def test_existing_e46_fa_route_does_not_read_ews_legacy_zcs(monkeypatch, diagnostic):
    bus = E39Bus()
    bus.cluster_ci, bus.cluster_di = 7, diagnostic
    state = tx.ClusterStoreState(
        "E46", b"cluster", 7, 0x38, bytes(276), 368, 112, 0x18, 0, 8,
        0xB7, 0x38, 0xB6, 0x16E)
    data = bytearray(state.data)
    data[0] = 8
    tx._cluster_checksum_image(state, data)
    state = replace(state, data=bytes(data))
    fa = tx.FaV2("0202", "E46_", "EV51", "354_", "N6TT",
                 ("7511570", "0000000", "0000000"), ("205", "403"), (), ())
    stream = tx.encode_fa_v2(fa)
    seen_identities = []
    monkeypatch.setattr(tx, "read_e46_cluster_store",
                        lambda _ds2, *, identity: seen_identities.append(identity) or state)
    monkeypatch.setattr(tx, "read_ews_production_month", lambda _ds2: (2002, 2))
    monkeypatch.setattr(tx, "_read_ews_zcs_raw", lambda _ds2: pytest.fail("FA must not read legacy ZCS"))
    for name in ("akmb", "alsz"):
        monkeypatch.setattr(tx, f"read_{name}_fa", lambda _ds2, n=name:
                            tx.FaHolderState(n, b"id", 1, b"", stream, b""))
    monkeypatch.setattr(tx, "_read_e46_stability", lambda _ds2:
                        tx.CodingState("MK20", b"id", 3, b"\0\0\x80" + bytes(9)))
    session = tx.prepare_connected_swap(bus, "manual")
    assert session.ready and session.chassis == "E46", session.reasons
    assert len(seen_identities) == 1 and seen_identities[0][8] == 7
    assert sum(frame[:3] == b"\x80\x04\0" for frame in bus.frames) == 1
    assert not any(frame[0] == 0x44 and frame[2] == 0x69 for frame in bus.frames)
    assert not bus.writes


def test_early_ms43_wrong_donor_reports_vehicle_type_instead_of_key_error(monkeypatch):
    zcs = tx.Zcs(bytes.fromhex("6C210000"), bytes(8), bytes(5))
    ews = tx.ZcsHolderState("EWS", b"id", 81, tx.encode_zcs(zcs))
    monkeypatch.setattr(tx, "read_e46_cluster_store", lambda _ds2:
                        type("Cluster", (), {"coding_index": 2})())
    monkeypatch.setattr(tx, "cluster_zcs", lambda _state: zcs)
    monkeypatch.setattr(tx, "read_ews_production_month", lambda _ds2: (2001, 6))
    monkeypatch.setattr(tx, "read_ews_zcs", lambda _ds2: ews)
    monkeypatch.setattr(tx, "read_ews_transmission", lambda _ds2: tx.CodingState("EWS", b"id", 81, bytes(5)))
    monkeypatch.setattr(tx, "read_e46_dme_transmission", lambda _ds2: tx.Transmission.MANUAL)
    monkeypatch.setattr(tx, "e46_cluster_transmission", lambda _state: tx.Transmission.MANUAL)
    monkeypatch.setattr(tx, "_read_e46_stability", lambda _ds2: tx.CodingState("MK20", b"id", 3, bytes(12)))
    monkeypatch.setattr(tx, "_probe_egs", lambda _ds2: ("GS8604", b"id"))
    monkeypatch.setattr(tx, "read_egs_aif_zb", lambda *_args, **_kwargs: ("GS8604", b"id", 7529032))
    with pytest.raises(ValueError, match="7529032 is not reviewed for AT31"):
        tx._prepare_e46_swap(object(), b"dme", "7551615", "MS43", tx.Transmission.AUTOMATIC)


def test_e39_stale_stability_blocks_before_archive_and_write(tmp_path):
    bus = E39Bus()
    session = tx.prepare_connected_swap(bus, "manual")
    bus.stability[3] ^= 2
    with pytest.raises(RuntimeError, match="stability-control coding changed"):
        tx.write_connected_modules(bus, session, archive_dir=tmp_path)
    assert not bus.writes and not list(tmp_path.iterdir())


def test_e39_native_word_serialization_preserves_can_byte_and_uses_low_checksum_byte():
    bus = E39Bus()
    state = e39.read_e39_cluster(bus)
    assert state.data[:2] == bytes(bus.memory[88:90])
    assert state.checksum_byte_offset == 89
    assert state.data[0] == 0x3F
    checksum = 0
    for value in state.data[2:]:
        checksum ^= value
    assert state.data[1] == checksum and state.data[0] != checksum
    with pytest.raises(ValueError, match="checksum is invalid"):
        tx._validate_cluster_checksum(replace(state, checksum_byte_offset=None))


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_old_ike_profile_without_matching_sia_definitions_is_not_admitted(family):
    bus = E39Bus(family, ike=True)
    session = tx.prepare_connected_swap(bus, "manual")
    assert not session.ready and not bus.writes
    assert "coding index 05" in session.reasons[0]


@pytest.mark.parametrize("manual,values", (
    ("5A11", (0x08CA, 0x09AB)), ("5C11", (0x0960, 0x0A41)),
    ("5A31", (0x092E, 0x0A0F)), ("5C31", (0x0992, 0x0A73)),
    ("5A51", (0x0947, 0x0A41)), ("5C51", (0x0992, 0x0A73)),
    ("5F11", (0x08CA, 0x09AB)), ("5E11", (0x0947, 0x09DD)),
    ("5F31", (0x092E, 0x0A0F)), ("5E31", (0x0992, 0x0A41)),
    ("5F51", (0x0947, 0x0A73)), ("5E51", (0x09AB, 0x0AA5)),
))
def test_all_engine_body_service_thresholds_and_unrelated_cluster_bits(manual, values):
    family = E39_DONORS[manual][0]
    bus = E39Bus(family)
    state = e39.read_e39_cluster(bus)
    for automatic, expected in enumerate(values):
        mode = tx.Transmission.AUTOMATIC if automatic else tx.Transmission.MANUAL
        target = e39.e39_cluster_target(state, order(manual), family, mode)
        assert target[232 - 88:234 - 88] == (0xA000 | expected).to_bytes(2, "big")
        assert target[0] == (0x3F if automatic else 0x37)
        changed = {index + 88 for index, (old, new) in enumerate(zip(state.data, target)) if old != new}
        assert changed <= {88, 89, 180, 232, 233, *range(140, 160)}
        check = 0
        for value in target[2:]:
            check ^= value
        assert target[1] == check


_MARKET_PAIRS = (
    # Exact GM suffix, engine service thresholds, and DSC manual/automatic values.
    ("5A37", "0013", 0x092E, 0x0A0F, 18, 11),
    ("5A58", "0000", 0x0947, 0x0A41, 12, 10),
    ("5A59", "0003", 0x0947, 0x0A41, 12, 11),
    ("5E38", "0000", 0x092E, 0x0A0F, 18, 10),
    ("5F35", "0000", 0x092E, 0x0A0F, 18, 9),
    ("5F37", "0000", 0x092E, 0x0A0F, 18, 9),
    ("5F55", "0000", 0x0947, 0x0A73, 12, 9),
)


@pytest.mark.parametrize("manual,suffix,mt_fuel,at_fuel,mt_dsc,at_dsc", _MARKET_PAIRS)
@pytest.mark.parametrize("automatic", (False, True))
@pytest.mark.parametrize("lost_ack", (False, True))
def test_exact_market_conversion_archive_and_lost_ack_restore(
        tmp_path, manual, suffix, mt_fuel, at_fuel, mt_dsc, at_dsc, automatic, lost_ack):
    profile = E39_DONORS[manual]
    bus = E39Bus(profile[0], automatic=automatic)
    bus.manual = manual
    source_gm = bytes.fromhex((profile[4] if automatic else manual) + suffix)
    source_order = replace(order(manual, automatic), gm=source_gm)
    bus.raw[:] = tx.encode_zcs(source_order)
    bus.memory[140:160] = bus.raw
    bus.memory[232:234] = (0xA000 | (at_fuel if automatic else mt_fuel)).to_bytes(2, "big")
    bus.memory[89] = 0
    for value in bus.memory[90:256]:
        bus.memory[89] ^= value
    dsc = at_dsc if automatic else mt_dsc
    bus.stability[:] = bytes((0xA5, (-dsc - 0xC2) & 255, dsc, 0xC2))
    bus.egs_zb = profile[5]
    original = (bytes(bus.raw), bytes(bus.ews), bytes(bus.memory), bytes(bus.stability))
    session = tx.prepare_connected_swap(bus, "manual" if automatic else "automatic")
    assert session.ready, session.reasons
    assert not bus.writes
    expected_order = tx.decode_zcs(session.target_zcs)
    assert expected_order.gm.hex().upper() == (manual if automatic else profile[4]) + suffix
    assert expected_order.vn == source_order.vn
    assert int.from_bytes(expected_order.sa, "big") ^ int.from_bytes(source_order.sa, "big") == 0x0001000000000000
    assert session.target_cluster[232 - 88:234 - 88] == (0xA000 | (mt_fuel if automatic else at_fuel)).to_bytes(2, "big")
    assert session.target_stability[2] == (mt_dsc if automatic else at_dsc)
    journal = SwapOperationJournal(tmp_path / "journal")
    bus.fail_stability_once = lost_ack
    if lost_ack:
        with pytest.raises(DS2Timeout, match="lost stability"):
            tx.write_connected_modules(bus, session, archive_dir=tmp_path, journal=journal)
        assert (bytes(bus.raw), bytes(bus.ews), bytes(bus.memory), bytes(bus.stability)) == original
        assert session.phase == "rolled_back"
    else:
        tx.write_connected_modules(bus, session, archive_dir=tmp_path, journal=journal)
        reloaded = tx.load_connected_swap_archive(session.archive_path)
        assert reloaded.target_cluster == session.target_cluster
        assert reloaded.target_zcs == session.target_zcs
        assert tx.verify_connected_swap(bus, session, journal=journal)["completed"]
        assert bus.memory[88:256] == session.target_cluster
        assert bus.stability == session.target_stability


@pytest.mark.parametrize("manual,suffix", [(case[0], case[1]) for case in _MARKET_PAIRS])
def test_market_profile_does_not_accept_a_different_gm_suffix(manual, suffix):
    family = E39_DONORS[manual][0]
    with pytest.raises(ValueError, match="not mapped"):
        e39.e39_profile(replace(order(manual), gm=bytes.fromhex(manual + "0001")), family)
    if suffix != "0000":
        with pytest.raises(ValueError, match="not mapped"):
            e39.e39_profile(replace(order(manual), gm=bytes.fromhex(manual + "0000")), family)


_SOURCE = Path.home() / "ECU-reference-data"


@pytest.mark.skipif(not _SOURCE.is_dir(), reason="private BMW sources are not installed")
def test_every_e39_pair_matches_bmw_type_flags_ats_and_history():
    zst = (_SOURCE / "coding/E39ZST.000").read_text("latin1").splitlines()
    for manual, profile in E39_DONORS.items():
        family, engine, mt, at, auto, standard, short = profile
        for name, prefix, mode in ((mt, manual, "MAN"), (at, auto, "AUT")):
            row = next(line.split("//")[0].split() for line in zst if line.startswith(name + " "))
            assert row[1] == prefix + E39_GM_SUFFIXES.get(manual, b"\0\0").hex().upper()
            assert engine in row and mode in row
            assert ("TU" in row) is (family == "MS42")
            assert bool(int(row[2], 16) & 0x0001000000000000) is (mode == "AUT")
        stem = "GD8600" if family == "MS42" else "GD8604"
        folder = _SOURCE / "E46/data" / stem
        ats = list(csv.DictReader([line for line in (folder / (stem + ".ATS")).read_text("latin1").splitlines()
                                  if line and not line.startswith(";")], delimiter=";"))
        edges = [tuple(map(int, match.groups())) for line in (folder / (stem + ".HIS")).read_text("latin1").splitlines()
                 if (match := re.fullmatch(r"(\d{7}),(\d{7}),(?:00|01),(?:28|2H)", line))]
        for release, option in ((standard, ""), (short, "204")):
            if release is None:
                continue
            assert any(row["FZG"] == at and not row["LA"] and row["SA"] == option
                       and int(row["ZB_NR"]) == release for row in ats)
            known = {release}
            while (previous := known | {old for old, new in edges if new in known}) != known:
                known = previous
            assert E39_EGS_RELEASE_HISTORY[release] == known
