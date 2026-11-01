"""Offline donor selection and source-evidence regressions."""

from dataclasses import replace
from pathlib import Path
import csv
import re

import pytest

import transmission_conversion as tx
from ms42_ms43_transmission_profiles import (
    EGS_RELEASE_HISTORY, MS42_ZCS_DONORS, MS43_FA_DONORS, MS43_ZCS_DONORS,
)


def _fa(vehicle_type, date, sa=()):
    year, month = date
    return tx.FaV2(
        f"{month:02d}{year % 100:02d}", "E46_", vehicle_type, "354_", "N6TT",
        ("7511570", "0000000", "0000000"), tuple(sa), (), (),
    )


@pytest.mark.parametrize("family,egs,prefix,profile", [
    (family, egs, prefix, profile)
    for family, egs, profiles in (
        ("MS42", "GS8600", MS42_ZCS_DONORS),
        ("MS43", "GS8604", MS43_ZCS_DONORS),
    )
    for prefix, profile in profiles.items()
])
def test_every_zcs_profile_selects_its_exact_release_lineage(family, egs, prefix, profile):
    _vehicle_type, first, last, zb, _ = profile
    zcs = tx.Zcs(bytes.fromhex(prefix + "00A5"), b"\0" * 8, b"\x12" * 5)
    for date in (first, last):
        assert tx._e46_egs_admission(
            family, tx.OrderFormat.ZCS, zcs=zcs, production=date,
        ) == (egs, EGS_RELEASE_HISTORY[zb])
    assert tx.decode_zcs(tx.encode_zcs(zcs.with_automatic(True))) == zcs.with_automatic(True)
    assert zcs.with_automatic(True).with_automatic(False) == zcs


@pytest.mark.parametrize("vehicle_type,profile", MS43_FA_DONORS.items())
@pytest.mark.parametrize("short_axle", (False, True))
def test_every_ms43_profile_honors_sa204_and_its_production_window(
        vehicle_type, profile, short_axle):
    _type, first, last, standard, short = profile
    sa = ("204", "403") if short_axle else ("403",)
    expected = EGS_RELEASE_HISTORY[short if short_axle and short else standard]
    for date in (first, last):
        fa = _fa(vehicle_type, date, sa)
        assert tx._e46_egs_admission(
            "MS43", tx.OrderFormat.FA, fa=fa, production=date,
        ) == ("GS8604", expected)
        assert tx.decode_fa_v2(tx.encode_fa_v2(fa.with_automatic(True))).with_automatic(False) == fa


@pytest.mark.parametrize("short_axle", (False, True))
def test_ev52_does_not_admit_the_other_final_drive_release(short_axle):
    _, allowed = tx._e46_egs_admission(
        "MS43", tx.OrderFormat.FA,
        fa=_fa("EV52", (2002, 2), ("204",) if short_axle else ()),
        production=(2002, 2),
    )
    assert (7546094 in allowed) is short_axle
    assert (7546090 in allowed) is not short_axle
    assert 7508228 not in allowed  # MS42 GS8600, even if its body type resembles this car.


def test_existing_am51_and_ev51_donors_are_preserved_exactly():
    assert EGS_RELEASE_HISTORY[7504952] == frozenset((1423390, 1423487, 1423678, 7504952))
    assert EGS_RELEASE_HISTORY[7546090] == frozenset((
        7507497, 7510270, 7511859, 7512065, 7514302,
        7515453, 7516764, 7523484, 7529014, 7546090,
    ))


@pytest.mark.parametrize("vehicle_type", ("EV53", "AV51", "EW51", "DT61", "XX99"))
def test_unmapped_transmission_or_chassis_is_not_inferred(vehicle_type):
    with pytest.raises(ValueError, match=f"not reviewed for {vehicle_type}"):
        tx._e46_egs_admission(
            "MS43", tx.OrderFormat.FA, fa=_fa(vehicle_type, (2002, 2)),
            production=(2002, 2),
        )


def test_e46_type_in_a_different_chassis_is_rejected():
    with pytest.raises(ValueError, match="exact E46"):
        tx._e46_egs_admission(
            "MS43", tx.OrderFormat.FA,
            fa=replace(_fa("EV52", (2002, 2)), chassis="E53_"),
            production=(2002, 2),
        )


@pytest.mark.parametrize("production,fa_date", (
    ((2001, 8), (2002, 2)), ((2005, 3), (2002, 2)),
    ((2002, 2), (2001, 8)), ((2002, 2), (2005, 3)),
))
def test_fa_and_connected_dates_both_remain_within_type_window(production, fa_date):
    with pytest.raises(ValueError, match="outside the reviewed range"):
        tx._e46_egs_admission(
            "MS43", tx.OrderFormat.FA, fa=_fa("EV52", fa_date), production=production)


def test_new_zcs_profile_with_unobservable_sa204_is_not_guessed():
    zcs = tx.Zcs(bytes.fromhex("66520000"), b"\0" * 8, b"\0" * 5)
    with pytest.raises(ValueError, match="not reviewed for 6652"):
        tx._e46_egs_admission("MS42", tx.OrderFormat.ZCS, zcs=zcs, production=(1999, 1))


@pytest.mark.parametrize("prefix,profile", MS43_ZCS_DONORS.items())
def test_early_ms43_donors_do_not_cross_the_zcs_era(prefix, profile):
    _type, first, _last, _zb, _short = profile
    zcs = tx.Zcs(bytes.fromhex(prefix + "0000"), b"\0" * 8, b"\0" * 5)
    before = (first[0], first[1] - 1) if first[1] > 1 else (first[0] - 1, 12)
    for production in (before, (2001, 9)):
        with pytest.raises(ValueError, match="outside the reviewed range"):
            tx._e46_egs_admission(
                "MS43", tx.OrderFormat.ZCS, zcs=zcs, production=production)
    with pytest.raises(ValueError, match="not reviewed"):
        tx._e46_egs_admission(
            "MS42", tx.OrderFormat.ZCS, zcs=zcs, production=first)


@pytest.mark.parametrize("prefix", ("6421", "6461", "6561"))
def test_early_ms43_ambiguous_or_missing_donor_assignments_are_not_borrowed(prefix):
    zcs = tx.Zcs(bytes.fromhex(prefix + "0000"), b"\0" * 8, b"\0" * 5)
    with pytest.raises(ValueError, match="not reviewed"):
        tx._e46_egs_admission(
            "MS43", tx.OrderFormat.ZCS, zcs=zcs, production=(2001, 1))


@pytest.mark.parametrize("family,vehicle,production,zb", (
    ("MS42", "6633", (1999, 1), 7508228),
    ("MS43", "EV52", (2002, 2), 7546094),
    *(("MS43", prefix, profile[1], profile[3])
      for prefix, profile in MS43_ZCS_DONORS.items()),
))
@pytest.mark.parametrize("target", (tx.Transmission.MANUAL, tx.Transmission.AUTOMATIC))
def test_new_profiles_reach_existing_full_preflight_without_any_writes(
        monkeypatch, family, vehicle, production, zb, target):
    source = (tx.Transmission.AUTOMATIC if target is tx.Transmission.MANUAL
              else tx.Transmission.MANUAL)
    automatic = source is tx.Transmission.AUTOMATIC
    uses_zcs = family == "MS42" or production < (2001, 9)
    zcs = tx.Zcs(bytes.fromhex(vehicle + "00A5") if uses_zcs else b"\0" * 4,
                 (8 if automatic else 0).to_bytes(8, "big"), b"\x12" * 5)
    raw = tx.encode_zcs(zcs)
    if uses_zcs:
        state = tx.ClusterStoreState(
            "E46", b"cluster", 2, 0x1F, bytes(450), 104, 78, 2, 0, 2,
            0x1F, 0x20, 0xFF, 0x3F,
        )
    else:
        state = tx.ClusterStoreState(
            "E46", b"cluster", 7, 0x38, bytes(276), 368, 112, 0x18, 0, 8,
            0xB7, 0x38, 0xB6, 0x16E,
        )
    data = bytearray(state.data)
    data[tx._cluster_absolute_slice(state, state.zcs_offset, 20)] = raw
    data[state.transmission_offset - state.first_word * 2] = state.automatic_value if automatic else 0
    tx._cluster_checksum_image(state, data)
    state = replace(state, data=bytes(data))
    tx._validate_cluster_checksum(state)
    ews = tx.ZcsHolderState("EWS", b"ews", 81, raw)
    coding = tx.CodingState("EWS", b"ews", 81, bytes((int(automatic), 0, 0, 0, 0)))
    stability = tx.CodingState("MK20", b"dsc", 3, b"\0\0" + bytes((0x80 if automatic else 0,)) + bytes(9))
    fa = _fa(vehicle, production, ("204", "205", "403") if automatic else ("204", "403"))
    stream = tx.encode_fa_v2(fa)
    monkeypatch.setattr(tx, "read_e46_cluster_store", lambda _ds2: state)
    monkeypatch.setattr(tx, "read_ews_production_month", lambda _ds2: production)
    monkeypatch.setattr(tx, "read_ews_zcs", lambda _ds2: ews)
    monkeypatch.setattr(tx, "read_ews_transmission", lambda _ds2: coding)
    monkeypatch.setattr(tx, "read_e46_dme_transmission", lambda _ds2: source)
    monkeypatch.setattr(tx, "_read_e46_stability", lambda _ds2: stability)
    for name in ("akmb", "alsz"):
        monkeypatch.setattr(tx, f"read_{name}_fa", lambda _ds2, n=name: tx.FaHolderState(n, b"id", 1, b"", stream, b""))
    egs_family = "GS8600" if family == "MS42" else "GS8604"
    monkeypatch.setattr(tx, "_probe_egs", lambda _ds2: (egs_family, b"egs") if not automatic else None)
    monkeypatch.setattr(tx, "read_egs_aif_zb", lambda *_args, **_kwargs: (egs_family, b"egs", zb))
    monkeypatch.setattr(tx, "_positive", lambda *_args, **_kwargs: pytest.fail("preflight must not write"))
    session = tx._prepare_e46_swap(object(), b"dme", "7500255" if family == "MS42" else "7551615", family, target)
    assert session.ready and session.family == family and session.chassis == "E46"
    assert session.source is source and session.target is target
    assert tx.e46_cluster_transmission(replace(state, data=session.target_cluster)) is target
    assert tx.ews_starter_interlock_active(replace(coding, data=session.target_ews_coding)) is (not automatic)
    assert session.egs_zb == (zb if not automatic else None)
    if uses_zcs:
        expected = zcs.with_automatic(not automatic)
        assert tx.decode_zcs(session.target_zcs) == expected
        assert session.akmb_fa is None and session.alsz_fa is None
        assert session.target_fa is None
    else:
        assert tx.decode_fa_v2(session.target_fa) == fa.with_automatic(not automatic)


_DATEN = Path.home() / "ECU-reference-data/E46"


@pytest.mark.skipif(not _DATEN.is_dir(), reason="private BMW source evidence is not installed")
@pytest.mark.parametrize("family,filename,stem,profiles", (
    ("MS42", "E46ZST.000", "GD8600", MS42_ZCS_DONORS),
    ("MS43", "E46ZST.000", "GD8604", MS43_ZCS_DONORS),
    ("MS43", "E46AT.000", "GD8604", MS43_FA_DONORS),
))
def test_every_shipped_profile_matches_original_bmw_type_ats_and_history(
        family, filename, stem, profiles):
    lines = (_DATEN / "daten" / filename).read_text("latin1").splitlines()
    if filename == "E46ZST.000":
        automatic = next(line.split() for line in lines if line.startswith("0205 "))
        assert automatic[1:4] == ["00000000", "0000000000000008", "0000000000"]
    ats = list(csv.DictReader([
        line for line in (_DATEN / "data" / stem / f"{stem}.ATS").read_text("latin1").splitlines()
        if line and not line.startswith(";")
    ], delimiter=";"))
    edges = []
    for line in (_DATEN / "data" / stem / f"{stem}.HIS").read_text("latin1").splitlines():
        match = re.fullmatch(r"(\d{7}),(\d{7}),(?:00|01),(?:28|2H)", line)
        if match:
            edges.append(tuple(map(int, match.groups())))
    for key, (vehicle_type, first, last, standard, short) in profiles.items():
        uses_zcs = filename == "E46ZST.000"
        prefix = vehicle_type if uses_zcs else f"W {vehicle_type}"
        source = next(line for line in lines if line.startswith(prefix + " "))
        if uses_zcs:
            assert source.split()[1][:4] == key
        assert any(token.startswith("M52" if family == "MS42" else "M54")
                   for token in source.split("//")[0].split())
        dates = re.search(r"(\d{2})/(\d{2}) - (\d{2})/(\d{2})", source)
        m1, y1, m2, y2 = map(int, dates.groups())
        start = (1900 + y1 if y1 >= 80 else 2000 + y1, m1)
        end = (1900 + y2 if y2 >= 80 else 2000 + y2, m2)
        assert first == (max(start, (2001, 9)) if not uses_zcs else start)
        assert last == (min(end, (2001, 8)) if family == "MS43" and uses_zcs else end)
        if family == "MS43" and uses_zcs:
            assert all(not row["LA"] and row["SA"] == "205"
                       and int(row["ZB_NR"]) == standard
                       for row in ats if row["FZG"] == vehicle_type)
        for zb, sa in ((standard, "205"), (short, "204&205")):
            if zb is None:
                continue
            assert any(row["FZG"] == vehicle_type and row["SA"] == sa
                       and not row["LA"] and int(row["ZB_NR"]) == zb for row in ats)
            known = {zb}
            while True:
                previous = known | {old for old, new in edges if new in known}
                if previous == known:
                    break
                known = previous
            assert EGS_RELEASE_HISTORY[zb] == known
