import json

import pytest

import transmission_conversion as tx
from ds2 import DS2Timeout


def _reply(data=b"", *, status=0xA0):
    body = bytes((0x32, len(data) + 4, status)) + bytes(data)
    return tx._frame(body)


def _ident(logical):
    data = bytearray(42)
    data[7:9] = logical.encode("ascii")
    if logical == "32":
        data[11] = ord("0")
    return _reply(data)


def _discovery(base):
    data = bytearray(63)
    data[60:63] = int(base).to_bytes(3, "big")
    return _reply(data)


def _record(count, zb=None, *, empty=False):
    data = bytearray(count)
    data[:3] = b"\xFF\xFF\xFF" if empty else b"AIF"
    if zb is not None:
        data[27:30] = int(zb).to_bytes(3, "big")
        data[30] = 1
    return _reply(data)


class _Frames:
    def __init__(self, exchanges):
        self.exchanges = list(exchanges)
        self.seen = []

    def send_frame(self, frame, *, resp_addr, timeout):
        frame = bytes(frame)
        self.seen.append(frame)
        assert resp_addr == 0x32
        assert timeout == 2.0
        expected, response = self.exchanges.pop(0)
        assert frame == expected
        return response


def test_gs8600_aif_reads_latest_programmed_slot_with_xor_frames():
    base = 0x120000
    zb = 7504952
    slot0 = bytes((0x32, 0x09, 0x06, 0x00, 0x12, 0x00, 0x00, 0x2E))
    slot1 = bytes((0x32, 0x09, 0x06, 0x00, 0x12, 0x00, 0x2E, 0x2E))
    ds2 = _Frames((
        (tx._frame(bytes((0x32, 0x04, 0x00))), _ident("28")),
        (tx._frame(bytes((0x32, 0x04, 0x0D))), _discovery(base)),
        (tx._frame(slot0), _record(0x2E, zb)),
        (tx._frame(slot1), _record(0x2E, empty=True)),
        (tx._frame(slot0), _record(0x2E, zb)),
    ))

    assert tx.read_egs_aif_zb(ds2) == ("GS8600", _ident("28"), zb)
    assert ds2.seen[1] == bytes.fromhex("32 04 0D 3B")
    assert not ds2.exchanges


def test_gs832_aif_uses_direct_selector_two_block():
    base = 0x010203
    zb = 1423262
    read = bytes((0x32, 0x09, 0x06, 0x02, 0x01, 0x02, 0x03, 0x21))
    ident = _ident("32")
    ds2 = _Frames((
        (tx._frame(bytes((0x32, 0x04, 0x00))), ident),
        (tx._frame(bytes((0x32, 0x04, 0x0D))), _discovery(base)),
        (tx._frame(read), _record(0x21, zb)),
    ))

    assert tx.read_egs_aif_zb(ds2) == ("GS832", ident, zb)
    assert not ds2.exchanges


def test_gs834_aif_scan_uses_selector_two():
    base = 0x020000
    zb = 1423438
    slot0 = bytes((0x32, 0x09, 0x06, 0x02, 0x02, 0x00, 0x00, 0x2E))
    slot1 = bytes((0x32, 0x09, 0x06, 0x02, 0x02, 0x00, 0x2E, 0x2E))
    ident = _ident("23")
    ds2 = _Frames((
        (tx._frame(bytes((0x32, 0x04, 0x00))), ident),
        (tx._frame(bytes((0x32, 0x04, 0x0D))), _discovery(base)),
        (tx._frame(slot0), _record(0x2E, zb)),
        (tx._frame(slot1), _record(0x2E, empty=True)),
        (tx._frame(slot0), _record(0x2E, zb)),
    ))

    assert tx.read_egs_aif_zb(ds2) == ("GS834", ident, zb)
    assert not ds2.exchanges


@pytest.mark.parametrize(("config", "expected"), (
    (0x00, tx.Transmission.MANUAL),
    (0x10, tx.Transmission.AUTOMATIC),
))
def test_ms42_ms43_active_dme_transmission_readback(config, expected):
    request = bytes.fromhex("12 05 0B 94 88")
    response = tx._frame(bytes((0x12, 0x05, 0xA0, config)))

    class Dme:
        def send_frame(self, frame, *, resp_addr, timeout):
            assert bytes(frame) == request
            assert resp_addr == 0x12 and timeout == 2.0
            return response

    assert tx.read_e46_dme_transmission(Dme()) is expected


def _zcs(sa=0):
    return tx.encode_zcs(tx.Zcs(
        bytes.fromhex("00000000"), sa.to_bytes(8, "big"),
        bytes.fromhex("0000000000"),
    ))


def test_exact_ms41_automatic_donor_admission_is_type_key_specific():
    e36 = tx.Ms41ZcsTarget(
        "E36", tx.Transmission.MANUAL, tx.Transmission.AUTOMATIC,
        "BF73", "BF83", _zcs(), False, False, False,
    )
    assert tx._ms41_egs_admission(e36) == (
        "GS834", frozenset((1423326, 1423432)))

    unsupported = tx.Ms41ZcsTarget(
        "E36", tx.Transmission.MANUAL, tx.Transmission.AUTOMATIC,
        "BF51", "BF61", _zcs(), False, False, False,
    )
    with pytest.raises(ValueError, match="not reviewed for BF61"):
        tx._ms41_egs_admission(unsupported)

    e39 = tx.Ms41ZcsTarget(
        "E39", tx.Transmission.MANUAL, tx.Transmission.AUTOMATIC,
        "DD31", "DD41", _zcs(tx._SA204), False, False, False,
    )
    assert tx._ms41_egs_admission(e39) == (
        "GS832", frozenset((1423118, 1423228)))


def test_e46_automatic_donor_admission_requires_exact_vehicle_identity():
    am51 = tx.Zcs(bytes.fromhex("66510000"), b"\0" * 8, b"\0" * 5)
    assert tx._e46_egs_admission(
        "MS42", tx.OrderFormat.ZCS, zcs=am51,
        production=(1999, 6),
    )[0] == "GS8600"

    ev51 = tx.FaV2(
        "0202", "E46_", "EV51", "354_", "N6TT",
        ("7511570", "7529014", "0000000"),
        ("403",), (), (),
    )
    assert tx._e46_egs_admission(
        "MS43", tx.OrderFormat.FA, fa=ev51,
        production=(2002, 2))[0] == "GS8604"
    with pytest.raises(ValueError, match="EV51 only"):
        tx._e46_egs_admission(
            "MS43", tx.OrderFormat.FA,
            fa=tx.FaV2(**{**ev51.__dict__, "vehicle_type": "AV51"}),
            production=(2002, 2),
        )


def test_e46_manual_direction_still_requires_exact_reviewed_vehicle(monkeypatch):
    zcs = tx.Zcs(bytes.fromhex("00000000"), (8).to_bytes(8, "big"), b"\0" * 5)
    raw = tx.encode_zcs(zcs)
    monkeypatch.setattr(
        tx, "read_e46_cluster_store",
        lambda _ds2: type("Cluster", (), {"coding_index": 2})(),
    )
    monkeypatch.setattr(tx, "read_ews_production_month", lambda _ds2: (1999, 1))
    monkeypatch.setattr(
        tx, "read_ews_zcs", lambda _ds2: tx.ZcsHolderState("EWS", b"id", 1, raw))
    monkeypatch.setattr(tx, "cluster_zcs", lambda _state: zcs)

    with pytest.raises(ValueError, match="AM51 only"):
        tx._prepare_e46_swap(
            object(), b"dme", "7500255", "MS42", tx.Transmission.MANUAL)


def test_late_ms43_fa_holder_timeout_never_falls_back_to_zcs(monkeypatch):
    monkeypatch.setattr(
        tx, "read_e46_cluster_store",
        lambda _ds2: type("Cluster", (), {"coding_index": 7})(),
    )
    monkeypatch.setattr(tx, "read_ews_production_month", lambda _ds2: (2002, 2))
    monkeypatch.setattr(
        tx, "read_akmb_fa",
        lambda _ds2: (_ for _ in ()).throw(DS2Timeout("missing AKMB")),
    )
    monkeypatch.setattr(
        tx, "read_ews_zcs",
        lambda _ds2: pytest.fail("late MS43 must not fall back to ZCS"),
    )

    with pytest.raises(DS2Timeout, match="missing AKMB"):
        tx._prepare_e46_swap(
            object(), b"dme", "7511570", "MS43", tx.Transmission.MANUAL)


@pytest.mark.parametrize("name", ("ASC5", "MK20"))
def test_coding_rollback_checks_identity_before_any_write(monkeypatch, name):
    state = tx.CodingState(name, b"reviewed", 1 if name == "ASC5" else 3, b"\0" * 12)
    changed = tx.CodingState(name, b"different", state.coding_index, state.data)
    if name == "ASC5":
        monkeypatch.setattr(tx, "_read_addressed_coding", lambda *_args, **_kwargs: changed)
    else:
        monkeypatch.setattr(tx, "read_mk20_transmission", lambda _ds2: changed)
    monkeypatch.setattr(
        tx, "_identify_c6",
        lambda *_args, **_kwargs: pytest.fail("identity mismatch must prevent coding entry"),
    )
    monkeypatch.setattr(
        tx, "_positive",
        lambda *_args, **_kwargs: pytest.fail("identity mismatch must prevent a write"),
    )

    with pytest.raises(RuntimeError, match="identity changed"):
        tx.restore_transmission_coding(object(), state)


def test_adding_automatic_option_keeps_fa_sa_words_canonical():
    fa = tx.FaV2(
        "0202", "E46_", "EV51", "354_", "N6TT",
        ("7511570", "7529014", "0000000"),
        ("522", "403"), (), (),
    )
    assert fa.with_automatic(True).sa == ("205", "403", "522")


def test_egs_identity_and_zb_are_archived_and_rechecked(monkeypatch, tmp_path):
    ident = _ident("28")
    session = tx.ConnectedSwapSession(
        "token", "ready", "ready", (), (), (),
        tx.Transmission.MANUAL, tx.Transmission.AUTOMATIC, "MS42", "E46",
        dme_ident=b"dme", egs_family="GS8600", egs_ident=ident,
        egs_zb=7504952,
    )
    path = tx.archive_connected_swap(session, tmp_path)
    assert json.loads(path.read_text(encoding="utf-8"))["egs"] == {
        "family": "GS8600", "ident": ident.hex().upper(), "aif_zb": 7504952,
    }

    monkeypatch.setattr(tx, "_probe_egs", lambda _ds2: ("GS8600", ident))
    monkeypatch.setattr(
        tx, "read_egs_aif_zb",
        lambda *_args, **_kwargs: ("GS8600", ident, 7504952),
    )
    tx._assert_egs_snapshot(object(), session)
    monkeypatch.setattr(
        tx, "read_egs_aif_zb",
        lambda *_args, **_kwargs: ("GS8600", ident, 7504953),
    )
    with pytest.raises(RuntimeError, match="assembly changed"):
        tx._assert_egs_snapshot(object(), session, final=True)
