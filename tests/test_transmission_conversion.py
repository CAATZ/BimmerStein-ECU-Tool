from dataclasses import replace
from functools import reduce
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest
import transmission_conversion as tx
from ds2 import DS2Timeout
from transmission_swap_journal import SwapOperationJournal

from transmission_conversion import (
    AUTOMATIC_OPTION,
    ConversionRequest,
    MS41ConversionRequest,
    MS41Selector,
    ModuleState,
    OrderCopy,
    OrderFormat,
    PlanStatus,
    Transmission,
    ZcsCounterpart,
    change_transmission_option,
    expected_order_format,
    plan_e46_conversion,
    plan_ms41_conversion,
    post_coding_frame,
)


def _digest(label):
    return sha256(label.encode("ascii")).digest()


def _module_state(name, transmission, *, writable=True):
    return ModuleState(
        reachable=True,
        profile=f"{name}.exact",
        writer_available=writable,
        reader_available=True,
        profile_exact=True,
        observed_transmission=transmission,
        transmission_exact=True,
        presence_exact=True,
    )


def _egs(target):
    if target is Transmission.MANUAL:
        return ModuleState(reachable=False, presence_exact=True)
    return _module_state("EGS", Transmission.AUTOMATIC, writable=False)


def _modules(order_format, transmission, *, writable=True):
    names = (("DME", "EWS", "KMB", "DSC") if order_format is OrderFormat.ZCS
             else ("DME", "EWS", "AKMB", "ALSZ", "DSC"))
    return {
        name: _module_state(name, transmission, writable=writable)
        for name in names
    }


def _copies(order_format, options, *, writable=True, checksum=True,
            codec="exact.v1", digest=None):
    holders = ("EWS", "KMB") if order_format is OrderFormat.ZCS else ("AKMB", "ALSZ")
    digest = digest or _digest(f"{order_format.value}:canonical")
    return {
        holder: OrderCopy(
            order_format=order_format,
            options=frozenset(options),
            codec=codec,
            checksum_valid=checksum,
            writer_available=writable,
            canonical_digest=digest,
            reader_available=True,
            codec_exact=True,
        )
        for holder in holders
    }


def _request(family="MS42", target=Transmission.MANUAL, *, year=2000, month=1,
             order_format=OrderFormat.ZCS, options=(AUTOMATIC_OPTION, "$403"),
             writable=True, egs=None, mechanical=True, chassis="E46"):
    current = (Transmission.AUTOMATIC if AUTOMATIC_OPTION in options
               else Transmission.MANUAL)
    return ConversionRequest(
        dme_family=family,
        target=target,
        production_year=year,
        production_month=month,
        reported_order_format=order_format,
        order_copies=_copies(order_format, options, writable=writable),
        modules=_modules(order_format, current, writable=writable),
        egs=_egs(target) if egs is None else egs,
        mechanical_swap_confirmed=mechanical,
        chassis=chassis,
    )


def _ms41_request(chassis="E39", family="MS41.0", *,
                  target=Transmission.MANUAL, selector=MS41Selector.DYNAMIC,
                  eeprom_transmission=Transmission.AUTOMATIC,
                  softbsl=True, writable=True):
    source = (Transmission.AUTOMATIC if target is Transmission.MANUAL
              else Transmission.MANUAL)
    names = (("DME", "EWS", "ASC_DSC", "IKE") if chassis == "E39"
             else ("DME", "EWS", "ASC_DSC"))
    modules = {
        name: _module_state(name, source, writable=writable)
        for name in names
    }
    source_digest = _digest(f"{chassis}:{family}:{source.value}")
    source_zcs = OrderCopy(
        order_format=OrderFormat.ZCS,
        options=frozenset((AUTOMATIC_OPTION, "$403") if source is Transmission.AUTOMATIC
                          else ("$403",)),
        codec="ms41.zcs.exact",
        checksum_valid=True,
        canonical_digest=source_digest,
        reader_available=True,
        codec_exact=True,
    )
    counterpart = ZcsCounterpart(
        target=target,
        gm="GM-EXACT",
        sa="SA-EXACT",
        vn="VN-EXACT",
        chassis=chassis,
        dme_family=family,
        source_digest=source_digest,
        source_transmission=source,
        relationship_reviewed=True,
        profile_exact=True,
        checksum_valid=True,
        writer_available=writable,
    )
    return MS41ConversionRequest(
        chassis=chassis,
        dme_family=family,
        target=target,
        counterpart=counterpart,
        source_zcs=source_zcs,
        modules=modules,
        selector=selector,
        eeprom_transmission=eeprom_transmission,
        eeprom_checksum_valid=True,
        eeprom_writer_available=writable,
        softbsl_installed=softbsl,
        egs=_egs(target),
        mechanical_swap_confirmed=True,
    )


def test_exact_e46_order_era_classification():
    assert expected_order_format("MS42") is OrderFormat.ZCS
    assert expected_order_format("MS43", 2001, 8) is OrderFormat.ZCS
    assert expected_order_format("MS43", 2001, 9) is OrderFormat.FA
    assert expected_order_format("MS43", 2002, 5) is OrderFormat.FA
    assert expected_order_format("MS43") is None
    with pytest.raises(ValueError, match="production month"):
        expected_order_format("MS43", 2001, 13)


def test_automatic_option_transform_preserves_every_other_option():
    original = frozenset(("$403", "$522"))
    automatic = change_transmission_option(original, Transmission.AUTOMATIC)
    assert automatic == original | {AUTOMATIC_OPTION}
    assert change_transmission_option(automatic, Transmission.MANUAL) == original


def test_only_hash_pinned_post_coding_frames_are_exposed():
    ms42 = post_coding_frame("MS42")
    ms43 = post_coding_frame("MS43")
    assert ms42 == bytes.fromhex("12 06 43 FF FF 57")
    assert ms43 == bytes.fromhex("12 06 43 00 01 56")
    assert ms42[-1] == reduce(lambda left, right: left ^ right, ms42[:-1])
    assert ms43[-1] == reduce(lambda left, right: left ^ right, ms43[:-1])
    with pytest.raises(ValueError, match="unsupported DME"):
        post_coding_frame("MS41")


def test_ready_ms42_plan_is_humanized_and_warns_about_full_adaptation_clear():
    plan = plan_e46_conversion(_request())
    assert plan.status is PlanStatus.READY
    assert plan.can_write
    assert AUTOMATIC_OPTION not in plan.updated_options
    assert "Automatic transmission ($205)" in plan.changes[0]
    assert "all MS42 engine adaptations" in plan.changes[-1]
    assert "will relearn" in plan.warnings[0]


def test_ms43_pu01_fa_plan_uses_variant_only_reset():
    request = _request(
        "MS43", Transmission.MANUAL, year=2002, month=5,
        order_format=OrderFormat.FA,
    )
    plan = plan_e46_conversion(request)
    assert plan.status is PlanStatus.READY
    assert plan.expected_order_format is OrderFormat.FA
    assert plan.post_coding_frame == bytes.fromhex("12 06 43 00 01 56")
    assert plan.warnings == ()
    assert "only the MS43 learned transmission variant" in plan.changes[-1]


def test_identity_mismatch_and_unknown_profile_are_unsupported_before_writes():
    mismatch = plan_e46_conversion(_request(
        "MS43", year=2002, month=5, order_format=OrderFormat.ZCS,
    ))
    assert mismatch.status is PlanStatus.UNSUPPORTED
    assert mismatch.changes == ()

    request = _request()
    modules = dict(request.modules)
    modules["DSC"] = replace(modules["DSC"], profile_exact=False)
    unknown = plan_e46_conversion(replace(request, modules=modules))
    assert unknown.status is PlanStatus.UNSUPPORTED
    assert not unknown.can_write
    assert any("coding version is not exactly supported" in reason
               for reason in unknown.reasons)


def test_missing_writer_bad_checksum_and_wrong_egs_require_action():
    no_writer = plan_e46_conversion(_request(writable=False))
    assert no_writer.status is PlanStatus.ACTION_REQUIRED
    assert not no_writer.can_write
    assert any("writer is not available" in reason for reason in no_writer.reasons)

    request = _request(
        target=Transmission.AUTOMATIC,
        options=("$403",),
        egs=ModuleState(reachable=False, presence_exact=True),
    )
    bad_copies = _copies(OrderFormat.ZCS, ("$403",), checksum=False)
    blocked = plan_e46_conversion(replace(request, order_copies=bad_copies))
    assert blocked.status is PlanStatus.ACTION_REQUIRED
    assert any("checksum is invalid" in reason for reason in blocked.reasons)
    assert any("requires a compatible" in reason for reason in blocked.reasons)


def test_already_configured_never_claims_a_write():
    plan = plan_e46_conversion(_request(options=("$403",), writable=False))
    assert plan.status is PlanStatus.READY
    assert plan.title == "Already configured for a manual gearbox"
    assert plan.changes == ()
    assert not plan.can_write


def test_e46_requires_explicit_chassis_and_matching_full_order_identity():
    assert plan_e46_conversion(replace(_request(), chassis=None)).status is PlanStatus.UNSUPPORTED
    assert plan_e46_conversion(replace(_request(), chassis="E39")).status is PlanStatus.UNSUPPORTED

    request = _request()
    copies = dict(request.order_copies)
    copies["KMB"] = replace(copies["KMB"], canonical_digest=_digest("different-full-zcs"))
    plan = plan_e46_conversion(replace(request, order_copies=copies))
    assert plan.status is PlanStatus.UNSUPPORTED
    assert any("different complete ZCS identities" in reason for reason in plan.reasons)


def test_e46_already_configured_requires_exact_readers_profiles_and_states():
    request = _request(options=("$403",), writable=False)

    copies = dict(request.order_copies)
    copies["EWS"] = replace(copies["EWS"], reader_available=False)
    no_reader = plan_e46_conversion(replace(request, order_copies=copies))
    assert no_reader.status is PlanStatus.ACTION_REQUIRED

    copies = dict(request.order_copies)
    copies["EWS"] = replace(copies["EWS"], codec="looks.exact", codec_exact=False)
    label_only = plan_e46_conversion(replace(request, order_copies=copies))
    assert label_only.status is PlanStatus.UNSUPPORTED

    modules = dict(request.modules)
    modules["KMB"] = replace(modules["KMB"], observed_transmission=Transmission.AUTOMATIC)
    disagreement = plan_e46_conversion(replace(request, modules=modules))
    assert disagreement.status is PlanStatus.UNSUPPORTED
    assert any("vehicle order reports manual" in reason for reason in disagreement.reasons)


def test_e46_automatic_requires_an_exact_observed_compatible_egs():
    request = _request(target=Transmission.AUTOMATIC, options=("$403",))
    assert plan_e46_conversion(request).status is PlanStatus.READY

    boolean_presence = plan_e46_conversion(replace(request, egs=True))
    assert boolean_presence.status is PlanStatus.ACTION_REQUIRED

    label_only = _module_state("EGS", Transmission.AUTOMATIC, writable=False)
    label_only = replace(label_only, profile_exact=False)
    unsupported = plan_e46_conversion(replace(request, egs=label_only))
    assert unsupported.status is PlanStatus.UNSUPPORTED

    wrong_kind = replace(_egs(Transmission.AUTOMATIC),
                         observed_transmission=Transmission.MANUAL)
    incompatible = plan_e46_conversion(replace(request, egs=wrong_kind))
    assert incompatible.status is PlanStatus.UNSUPPORTED


def test_e39_ms41_dynamic_plan_requires_and_uses_exact_existing_owners():
    plan = plan_ms41_conversion(_ms41_request())
    assert plan.status is PlanStatus.READY
    assert plan.can_write
    assert "GM/SA/VN counterpart" in plan.changes[0]
    assert "0x196" in plan.changes[1]
    assert "preserving bits 2-15" in plan.changes[1]
    assert plan.verification_address == 0xFD4C
    assert plan.verification_mask == 0x80


def test_e36_ms41_never_claims_complete_k_line_readiness():
    plan = plan_ms41_conversion(_ms41_request(chassis="E36"))
    assert plan.status is PlanStatus.ACTION_REQUIRED
    assert not plan.can_write
    assert plan.title == "External ADS step required"
    assert any("Concept-1 cluster" in reason and "ADS" in reason
               for reason in plan.reasons)


@pytest.mark.parametrize(
    "source", (Transmission.MANUAL, Transmission.AUTOMATIC),
)
def test_connected_ms41_e36_reports_ordinary_cluster_boundary(monkeypatch, source):
    ews = tx.ZcsHolderState("EWS", b"ews-ident", 2, b"z" * 20)
    counterpart = tx.Ms41ZcsTarget(
        "E36", source, Transmission.AUTOMATIC,
        "BF51", "BF61", b"a" * 20, False, False, False,
    )
    monkeypatch.setattr(tx, "read_ews_zcs", lambda _ds2: ews)
    monkeypatch.setattr(tx, "_classify_ms41_chassis", lambda _raw: "E36")
    monkeypatch.setattr(tx, "derive_ms41_zcs_target", lambda *_args: counterpart)
    monkeypatch.setattr(
        tx, "read_ews_transmission",
        lambda _ds2: tx.CodingState("EWS", ews.ident, ews.coding_index, b"\x00" * 5),
    )
    def unavailable_cluster(*_args):
        raise DS2Timeout("no Compact cluster response")
    monkeypatch.setattr(
        tx, "read_ms41_cluster_store", unavailable_cluster,
    )

    result = tx._prepare_ms41_swap(
        object(), b"dme-ident", "MS41.2", "MS41.2",
        Transmission.AUTOMATIC, None,
    )

    assert not result.ready
    assert result.title == "E36 requires ADS/L-line access"
    assert result.chassis == "E36"
    assert "No coding was changed" in result.reasons[0]


def test_connected_ms41_e36_compact_k_line_path_reaches_all_preflight_gates(
        monkeypatch):
    from engines.softbsl import eeprom_ram

    calls = []
    zcs = tx.Zcs(b"\x01\x02\x03\x04", b"\x00" * 8, b"\x05" * 5)
    raw = tx.encode_zcs(zcs)
    ews = tx.ZcsHolderState("EWS", b"ews-ident", 2, raw)
    ews_coding = tx.CodingState("EWS", ews.ident, 2, b"\x01" + b"\x00" * 4)
    counterpart = tx.Ms41ZcsTarget(
        "E36", Transmission.MANUAL, Transmission.MANUAL,
        "BF51", "BF51", raw, False, False, False,
    )
    cluster = SimpleNamespace()
    stability = tx.CodingState("ASC5", b"asc-ident", 1, b"\x00")
    eeprom = bytes.fromhex("AE A5 54 01")

    monkeypatch.setattr(tx, "read_ews_zcs", lambda _ds2: calls.append("ews") or ews)
    monkeypatch.setattr(tx, "_classify_ms41_chassis", lambda _raw: "E36")
    monkeypatch.setattr(tx, "derive_ms41_zcs_target", lambda *_args: counterpart)
    monkeypatch.setattr(
        tx, "read_ews_transmission",
        lambda _ds2: calls.append("ews_coding") or ews_coding,
    )
    monkeypatch.setattr(
        tx, "read_ms41_cluster_store",
        lambda *_args: calls.append("cluster") or cluster,
    )
    monkeypatch.setattr(tx, "cluster_zcs", lambda _cluster: zcs)
    monkeypatch.setattr(
        tx, "cluster_transmission",
        lambda _cluster: calls.append("cluster_mode") or Transmission.MANUAL,
    )
    monkeypatch.setattr(
        tx, "_read_ms41_stability",
        lambda *_args: calls.append("stability") or stability,
    )
    monkeypatch.setattr(
        tx, "ms41_selector",
        lambda _ds2: calls.append("selector") or MS41Selector.DYNAMIC,
    )
    monkeypatch.setattr(
        eeprom_ram, "decode_transmission_record",
        lambda *_args: calls.append("eeprom") or {"check_ok": True, "mode": "manual"},
    )
    monkeypatch.setattr(
        tx, "read_ms41_runtime_transmission",
        lambda *_args: calls.append("runtime") or Transmission.MANUAL,
    )
    monkeypatch.setattr(
        eeprom_ram, "make_transmission_record_from_record",
        lambda record, *_args: calls.append("target") or bytes(record),
    )
    monkeypatch.setattr(tx, "_probe_egs", lambda _ds2: calls.append("egs") or None)

    result = tx._prepare_ms41_swap(
        object(), b"dme-ident", "1406464", "MS41.2",
        Transmission.MANUAL, eeprom,
    )

    assert not result.ready
    assert result.title == "Already configured for a manual transmission"
    assert calls == [
        "ews", "ews_coding", "cluster", "cluster_mode", "stability",
        "selector", "eeprom", "runtime", "target", "egs",
    ]


def test_connected_ms41_same_target_finishes_every_read_only_gate(monkeypatch):
    from engines.softbsl import eeprom_ram

    calls = []
    zcs = tx.Zcs(b"\x01\x02\x03\x04", b"\x00" * 8, b"\x05" * 5)
    raw = tx.encode_zcs(zcs)
    ews = tx.ZcsHolderState("EWS", b"ews-ident", 81, raw)
    ews_coding = tx.CodingState("EWS", ews.ident, 81, b"\x00" * 5)
    target = tx.Ms41ZcsTarget(
        "E39", Transmission.MANUAL, Transmission.MANUAL,
        "DD51", "DD51", raw, False, False, False,
    )
    cluster = SimpleNamespace()
    stability = tx.CodingState("ASC5", b"asc-ident", 6, b"\x00")
    eeprom = bytes.fromhex("AE A5 54 01")

    monkeypatch.setattr(
        tx, "read_ews_zcs", lambda _ds2: calls.append("ews_zcs") or ews)
    monkeypatch.setattr(tx, "_classify_ms41_chassis", lambda _raw: "E39")
    monkeypatch.setattr(
        tx, "read_ews_production_month",
        lambda _ds2: calls.append("production") or (1999, 1),
    )
    monkeypatch.setattr(tx, "derive_ms41_zcs_target", lambda *_args: target)
    monkeypatch.setattr(
        tx, "read_ews_transmission",
        lambda _ds2: calls.append("ews_coding") or ews_coding,
    )
    monkeypatch.setattr(
        tx, "read_ms41_cluster_store",
        lambda *_args: calls.append("cluster") or cluster,
    )
    monkeypatch.setattr(tx, "cluster_zcs", lambda _cluster: zcs)
    monkeypatch.setattr(
        tx, "cluster_transmission",
        lambda _cluster: calls.append("cluster_mode") or Transmission.MANUAL,
    )
    monkeypatch.setattr(
        tx, "_read_ms41_stability",
        lambda *_args: calls.append("stability") or stability,
    )
    monkeypatch.setattr(
        tx, "ms41_selector",
        lambda _ds2: calls.append("selector") or MS41Selector.DYNAMIC,
    )
    monkeypatch.setattr(
        eeprom_ram, "validate_write_image",
        lambda image, _family: calls.append("eeprom_validate") or bytes(image),
    )
    monkeypatch.setattr(
        eeprom_ram, "detect_layouts",
        lambda _image: calls.append("eeprom_layout") or ("MS41.2",),
    )
    monkeypatch.setattr(
        eeprom_ram, "decode_transmission_record",
        lambda *_args: calls.append("eeprom_record")
        or {"check_ok": True, "mode": "manual"},
    )
    monkeypatch.setattr(
        tx, "read_ms41_runtime_transmission",
        lambda *_args: calls.append("dme_runtime") or Transmission.MANUAL,
    )
    monkeypatch.setattr(
        eeprom_ram, "make_transmission_record_from_record",
        lambda record, *_args: calls.append("eeprom_target") or bytes(record),
    )
    monkeypatch.setattr(
        tx, "_probe_egs", lambda _ds2: calls.append("egs") or None)

    result = tx._prepare_ms41_swap(
        object(), b"dme-ident", "1406464", "MS41.2",
        Transmission.MANUAL, eeprom,
    )

    assert not result.ready
    assert result.title == "Already configured for a manual transmission"
    assert calls == [
        "ews_zcs", "production", "ews_coding", "cluster", "cluster_mode",
        "stability", "selector", "eeprom_record", "dme_runtime",
        "eeprom_target", "egs",
    ]


def test_connected_e46_same_target_finishes_every_read_only_gate(monkeypatch):
    calls = []
    zcs = tx.Zcs(b"\x01\x02\x03\x04", b"\x00" * 8, b"\x05" * 5)
    raw = tx.encode_zcs(zcs)
    cluster = SimpleNamespace(coding_index=1)
    ews = tx.ZcsHolderState("EWS", b"ews-ident", 81, raw)
    ews_coding = tx.CodingState("EWS", ews.ident, 81, b"\x00" * 5)
    stability = tx.CodingState("MK20", b"mk20-ident", 3, b"\x00" * 12)

    monkeypatch.setattr(
        tx, "read_e46_cluster_store",
        lambda _ds2: calls.append("cluster") or cluster,
    )
    monkeypatch.setattr(
        tx, "read_ews_production_month",
        lambda _ds2: calls.append("production") or (2000, 1),
    )
    monkeypatch.setattr(
        tx, "read_ews_zcs", lambda _ds2: calls.append("ews_zcs") or ews)
    monkeypatch.setattr(tx, "cluster_zcs", lambda _cluster: zcs)
    monkeypatch.setattr(
        tx, "_e46_egs_admission",
        lambda *_args, **_kwargs: calls.append("egs_admission")
        or ("GS8600", frozenset()),
    )
    monkeypatch.setattr(
        tx, "read_ews_transmission",
        lambda _ds2: calls.append("ews_coding") or ews_coding,
    )
    monkeypatch.setattr(
        tx, "read_e46_dme_transmission",
        lambda _ds2: calls.append("dme") or Transmission.MANUAL,
    )
    monkeypatch.setattr(
        tx, "e46_cluster_transmission",
        lambda _cluster: calls.append("cluster_mode") or Transmission.MANUAL,
    )
    monkeypatch.setattr(
        tx, "_read_e46_stability",
        lambda _ds2: calls.append("stability") or stability,
    )
    monkeypatch.setattr(
        tx, "_stability_transmission",
        lambda _stability: calls.append("stability_mode") or Transmission.MANUAL,
    )
    monkeypatch.setattr(
        tx, "_probe_egs", lambda _ds2: calls.append("egs") or None)

    result = tx._prepare_e46_swap(
        object(), b"dme-ident", "7500255", "MS42", Transmission.MANUAL,
    )

    assert not result.ready
    assert result.title == "Already configured for a manual transmission"
    assert calls == [
        "cluster", "production", "ews_zcs", "egs_admission", "ews_coding",
        "dme", "cluster_mode", "stability", "stability_mode", "egs",
    ]


@pytest.mark.parametrize(
    ("family", "address"),
    (("MS41.0", 0xFD4C), ("MS41.1", 0xFD5C),
     ("MS41.2", 0xFD5C), ("MS41.3", 0xFD5C)),
)
def test_ms41_runtime_verification_metadata_is_family_specific(family, address):
    request = _ms41_request(
        family=family, selector=MS41Selector.MANUAL_ONLY,
        eeprom_transmission=None, softbsl=False,
    )
    plan = plan_ms41_conversion(request)
    assert plan.status is PlanStatus.READY
    assert plan.verification_address == address
    assert plan.verification_mask == 0x80
    assert f"0x{address:04X} bit 7" in plan.changes[-1]


def test_ms41_dynamic_mode_uses_stock_kline_without_softbsl():
    plan = plan_ms41_conversion(_ms41_request(softbsl=False))
    assert plan.status is PlanStatus.READY
    assert plan.can_write
    assert not any("Soft-BSL" in reason for reason in plan.reasons)
    assert any("checked transmission record" in warning for warning in plan.warnings)

    unread = plan_ms41_conversion(replace(
        _ms41_request(), eeprom_transmission=None,
    ))
    assert unread.status is PlanStatus.ACTION_REQUIRED
    assert any("Read the current transmission value" in reason
               for reason in unread.reasons)
    assert not any("EEPROM record at" in change for change in unread.changes)


def test_ms41_fixed_selector_conflict_is_unsupported_not_silently_changed():
    request = _ms41_request(selector=MS41Selector.AUTOMATIC_ONLY)
    plan = plan_ms41_conversion(request)
    assert plan.status is PlanStatus.UNSUPPORTED
    assert not plan.can_write
    assert any("Set its Transmission option to AT/MT" in reason
               for reason in plan.reasons)


def test_e39_ms41_unknown_counterpart_or_module_profile_blocks_before_writes():
    request = _ms41_request()
    no_counterpart = plan_ms41_conversion(replace(request, counterpart=None))
    assert no_counterpart.status is PlanStatus.UNSUPPORTED
    assert not no_counterpart.can_write

    modules = dict(request.modules)
    modules["IKE"] = replace(modules["IKE"], profile_exact=False)
    unknown_ike = plan_ms41_conversion(replace(request, modules=modules))
    assert unknown_ike.status is PlanStatus.UNSUPPORTED
    assert any("instrument cluster" in reason for reason in unknown_ike.reasons)


def test_ms41_counterpart_is_bound_to_source_chassis_family_and_review():
    request = _ms41_request()

    cases = (
        replace(request.counterpart, source_digest=_digest("another-source")),
        replace(request.counterpart, chassis="E36"),
        replace(request.counterpart, dme_family="MS41.1"),
        replace(request.counterpart, relationship_reviewed=False),
    )
    for counterpart in cases:
        plan = plan_ms41_conversion(replace(request, counterpart=counterpart))
        assert plan.status is PlanStatus.UNSUPPORTED
        assert not plan.can_write


def test_ms41_requires_exact_connected_zcs_and_consistent_module_states():
    request = _ms41_request()

    label_only = replace(request.source_zcs, codec="looks.exact", codec_exact=False)
    no_exact_source = plan_ms41_conversion(replace(request, source_zcs=label_only))
    assert no_exact_source.status is PlanStatus.UNSUPPORTED

    modules = dict(request.modules)
    modules["IKE"] = replace(
        modules["IKE"], observed_transmission=Transmission.MANUAL,
    )
    disagreement = plan_ms41_conversion(replace(request, modules=modules))
    assert disagreement.status is PlanStatus.UNSUPPORTED
    assert any("vehicle order reports automatic" in reason
               for reason in disagreement.reasons)

    modules = dict(request.modules)
    modules["IKE"] = replace(modules["IKE"], transmission_exact=False)
    unread = plan_ms41_conversion(replace(request, modules=modules))
    assert unread.status is PlanStatus.ACTION_REQUIRED


def test_built_in_zcs_counterpart_round_trips_without_touching_unrelated_data():
    source = tx.Zcs(
        bytes.fromhex("531100A5"),
        bytes.fromhex("0000000000000000"),
        bytes.fromhex("1122334455"),
    )
    automatic = tx.derive_ms41_zcs_target(
        tx.encode_zcs(source), "E39", Transmission.AUTOMATIC, (1998, 6))
    decoded = tx.decode_zcs(automatic.raw)
    assert automatic.source is Transmission.MANUAL
    assert decoded.gm.hex().upper().startswith("5321")
    assert decoded.gm[2:] == source.gm[2:]
    assert decoded.vn == source.vn

    manual = tx.derive_ms41_zcs_target(
        automatic.raw, "E39", Transmission.MANUAL, (1998, 6))
    restored = tx.decode_zcs(manual.raw)
    assert restored.gm == source.gm
    assert restored.vn == source.vn


def test_built_in_fa_codec_changes_only_automatic_option():
    source = tx.FaV2(
        "0202", "E46_", "EV51", "354_", "N6TT",
        ("7511570", "7529014", "0000000"),
        ("403", "522"), ("S167",), ("H001",),
    )
    encoded = tx.encode_fa_v2(source)
    assert tx.decode_fa_v2(encoded) == source

    automatic = source.with_automatic(True)
    decoded = tx.decode_fa_v2(tx.encode_fa_v2(automatic))
    assert decoded.automatic
    assert decoded.with_automatic(False) == source


@pytest.mark.parametrize(
    ("coding_index", "length", "automatic_bit"),
    ((1, 4, 0), (2, 5, 0), (81, 5, 1)),
)
def test_ews_starter_interlock_uses_exact_profile_and_changes_only_one_bit(
        coding_index, length, automatic_bit):
    data = bytearray((0xA4, 0x0A, 0x01, 0x0A, 0x0A)[:length])
    data[0] = (data[0] & ~1) | automatic_bit
    calls = []

    def response(payload):
        body = bytes((0x44, len(payload) + 4, 0xA0)) + bytes(payload)
        return body + bytes((tx._xor(body),))

    class Ds2:
        def send_frame(self, frame, *, resp_addr, timeout):
            nonlocal data
            frame = bytes(frame)
            calls.append(frame)
            assert resp_addr == 0x44 and timeout == 2.0
            assert tx._xor(frame) == 0
            if frame[2] == 0x00:
                payload = bytearray(12)
                payload[5] = ((coding_index // 10) << 4) | (coding_index % 10)
                payload[6] = 0x81 if coding_index == 81 else coding_index
                return response(payload)
            if frame[2] == 0x08:
                return response(data)
            if frame[2] == 0x09:
                data = bytearray(frame[3:-1])
                return response(b"")
            raise AssertionError(frame.hex(" "))

    ds2 = Ds2()
    before = tx.read_ews_transmission(ds2)
    assert tx.ews_starter_interlock_active(before)
    target = tx.build_ews_transmission_target(before, Transmission.MANUAL)
    assert len(target) == length
    assert target[1:] == before.data[1:]
    assert (target[0] ^ before.data[0]) == 1

    after = tx.write_ews_transmission(ds2, before, target)
    assert not tx.ews_starter_interlock_active(after)
    write = next(frame for frame in calls if frame[2] == 0x09)
    assert write == tx._frame(bytes((0x44, length + 4, 0x09)) + target)


def test_cluster_storage_publishes_checksum_word_last(monkeypatch):
    state = tx.ClusterStoreState(
        "E39", b"identity", 5, 0, b"\x10\x20\x30\x40",
        0, 2, 0xC0, 0, 0x40, 0, 1, 1,
    )
    target = b"\xAA\x20\x31\x40"
    writes = []
    monkeypatch.setattr(
        tx, "_write_words",
        lambda _ds2, start, before, after: writes.append(
            (start, bytes(before), bytes(after))),
    )

    tx._write_cluster_target_words(object(), state, state.data, target)

    assert writes == [
        (0, state.data, b"\x10\x20\x31\x40"),
        (0, b"\x10\x20\x31\x40", target),
    ]


def test_e36_compact_cluster_uses_second_checksum_byte_then_resets(monkeypatch):
    data = bytearray(0xA8)
    data[2:] = bytes((index * 7) & 0xFF for index in range(0xA6))
    state = tx.ClusterStoreState(
        "E36", b"identity", 2, 0x6C, bytes(data),
        268, 308, 0xC0, 0, 0x40, 0x6C, 0x6D, 0xBF, 0xD9, "KMB",
    )
    target = bytearray(state.data)
    target[10] ^= 1
    tx._cluster_checksum_image(state, target)
    assert target[0] == state.data[0]
    assert target[1] == tx._xor(target[2:])

    writes = []
    resets = []
    waits = []
    monkeypatch.setattr(
        tx, "_write_words",
        lambda _ds2, start, before, after: writes.append(
            (start, bytes(before), bytes(after))),
    )
    monkeypatch.setattr(
        tx, "_positive",
        lambda _ds2, frame, address: resets.append((bytes(frame), address)),
    )
    monkeypatch.setattr(tx.time, "sleep", waits.append)

    tx._write_cluster_target_words(object(), state, state.data, bytes(target))

    assert len(writes) == 2
    assert writes[0][2][:2] == state.data[:2]
    assert writes[1][2] == bytes(target)
    assert resets == [(bytes.fromhex("80 04 12"), 0x80)]
    assert waits == [5.0]


def test_module_transaction_rolls_back_in_reverse_order(monkeypatch, tmp_path):
    ews = tx.ZcsHolderState("EWS", b"ews", 1, b"z" * 20)
    cluster = tx.ClusterStoreState(
        "E39", b"cluster", 5, 0, b"\x00\x00",
        0, 0, 0xC0, 0, 0x40, None, None, None,
    )
    session = tx.ConnectedSwapSession(
        "token", "ready", "ready", (), (), (),
        Transmission.AUTOMATIC, Transmission.MANUAL, "MS41.0", "E39",
        dme_ident=b"dme", ews_zcs=ews, cluster=cluster,
        target_zcs=b"m" * 20, target_cluster=b"\x01\x00",
        eeprom_before=b"\x00" * 512, eeprom_target=b"\x01" * 512,
    )
    events = []
    monkeypatch.setattr(tx, "_assert_session_fresh", lambda *_args: None)
    monkeypatch.setattr(
        tx, "archive_connected_swap",
        lambda current, _directory: setattr(current, "archive_path", "archive"),
    )
    monkeypatch.setattr(
        tx, "write_ews_zcs", lambda *_args: events.append("write ews"))
    monkeypatch.setattr(
        tx, "write_cluster_store",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("injected")),
    )
    monkeypatch.setattr(
        tx, "restore_cluster_store",
        lambda *_args: events.append("restore cluster"),
    )
    monkeypatch.setattr(
        tx, "restore_ews_zcs", lambda *_args: events.append("restore ews"))

    with pytest.raises(RuntimeError, match="injected"):
        tx.write_connected_modules(
            object(), session, archive_dir=tmp_path,
            eeprom_current=session.eeprom_before,
        )
    assert events == ["write ews", "restore cluster", "restore ews"]
    assert session.phase == "rolled_back"
    assert session.written == []


def test_ms41_stale_eeprom_refuses_before_archive_or_module_write(
        monkeypatch, tmp_path):
    session = tx.ConnectedSwapSession(
        "token", "ready", "ready", (), (), (),
        Transmission.AUTOMATIC, Transmission.MANUAL, "MS41.2", "E39",
        dme_ident=b"dme", ews_zcs=tx.ZcsHolderState(
            "EWS", b"ews", 81, b"z" * 20),
        target_zcs=b"m" * 20, eeprom_before=b"\x00" * 512,
        eeprom_target=b"\x01" * 512,
    )
    monkeypatch.setattr(tx, "_assert_session_fresh", lambda *_args: None)
    monkeypatch.setattr(
        tx, "archive_connected_swap",
        lambda *_args: pytest.fail("stale EEPROM must stop before archiving"),
    )
    monkeypatch.setattr(
        tx, "_write_connected_owner",
        lambda *_args: pytest.fail("stale EEPROM must stop before module writes"),
    )

    with pytest.raises(RuntimeError, match="transmission record changed"):
        tx.write_connected_modules(
            object(), session, archive_dir=tmp_path,
            eeprom_current=b"\x02" * 512,
        )


def test_mk60_write_enters_coding_session_before_verified_update():
    data = bytearray(15)
    data[0] = 1
    calls = []

    def response(payload):
        body = bytes((0xB8, 0xF1, 0x29, len(payload))) + bytes(payload)
        return body + bytes((tx._xor(body),))

    class FakeDs2:
        def send_bmw_fast(self, body, *, target, timeout):
            body = bytes(body)
            calls.append(body)
            assert target == 0x29 and timeout == 2.0
            if body == bytes.fromhex("B8 29 F1 02 1A 80"):
                payload = bytearray(10)
                payload[:2] = b"\x5A\x80"
                payload[9] = 0x01
                return response(payload)
            if body == bytes.fromhex("B8 29 F1 03 22 30 00"):
                return response(b"\x62\x30\x00" + bytes(data))
            if body == bytes.fromhex("B8 29 F1 02 10 87"):
                return response(b"\x50\x87")
            if body.startswith(bytes.fromhex("B8 29 F1 12 2E 30 00")):
                data[:] = body[7:]
                return response(b"\x6E\x30\x00")
            raise AssertionError(body.hex(" "))

    ds2 = FakeDs2()
    before = tx.read_mk60_transmission(ds2)
    after = tx.write_mk60_transmission(
        ds2, before, Transmission.AUTOMATIC)

    session_index = calls.index(bytes.fromhex("B8 29 F1 02 10 87"))
    write_index = next(
        index for index, body in enumerate(calls)
        if body.startswith(bytes.fromhex("B8 29 F1 12 2E 30 00")))
    assert session_index < write_index
    assert tx.mk60_transmission(after) is Transmission.AUTOMATIC
    assert after.data[0] == (tx._xor(after.data[1:]) + 1) & 0xFF


def test_connected_swap_archive_round_trips_complete_recovery_plan(
        monkeypatch, tmp_path):
    before = bytes.fromhex("AD A5 53 01")
    target = bytes.fromhex("AE A5 54 01")
    session = tx.ConnectedSwapSession(
        "0123456789abcdef0123456789abcdef", "ready", "Ready", (), (), (),
        Transmission.AUTOMATIC, Transmission.MANUAL, "MS41.2", "E39",
        dme_ident=b"1406464 connected identity", program="1406464",
        order_format=OrderFormat.ZCS, selector=MS41Selector.DYNAMIC,
        eeprom_before=before, eeprom_target=target, eeprom_variant="MS41.2",
    )

    published = []
    durable_write = tx.write_new_file_durably

    def capture_publication(path, data):
        published.append((path, bytes(data)))
        return durable_write(path, data)

    monkeypatch.setattr(tx, "write_new_file_durably", capture_publication)
    path = tx.archive_connected_swap(session, tmp_path)
    restored = tx.load_connected_swap_archive(path)

    assert restored == session
    assert restored is not session
    assert restored.archive_path == str(path.resolve())
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema"] == 2
    assert payload["eeprom_record"] == before.hex().upper()
    assert published == [(path, path.read_bytes())]
    assert published[0][1].endswith(b"\n")


def test_connected_swap_archive_rejects_unknown_record_type(tmp_path):
    before = bytes(range(256)) * 2
    session = tx.ConnectedSwapSession(
        "0123456789abcdef0123456789abcdef", "ready", "Ready", (), (), (),
        Transmission.AUTOMATIC, Transmission.MANUAL, "MS41.2", "E39",
        dme_ident=b"1406464 connected identity", program="1406464",
        eeprom_before=before, eeprom_target=before, eeprom_variant="MS41.2",
    )
    path = tx.archive_connected_swap(session, tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["session"]["$type"] = "Anything"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="unknown recovery record type"):
        tx.load_connected_swap_archive(path)


def _e46_connected_session():
    stability = tx.CodingState("MK20", b"stability", 3, b"\x00" * 12)
    target_stability = bytearray(stability.data)
    target_stability[2] = 0x80
    return tx.ConnectedSwapSession(
        "0123456789abcdef0123456789abcdef", "ready", "Ready", (), (), (),
        Transmission.MANUAL, Transmission.AUTOMATIC, "MS42", "E46",
        dme_ident=b"7500255 connected identity", program="7500255",
        order_format=OrderFormat.ZCS, stability=stability,
        target_stability=bytes(target_stability),
    )


def test_connected_write_journal_reaches_awaiting_cycle(monkeypatch, tmp_path):
    session = _e46_connected_session()
    journal = SwapOperationJournal(tmp_path / "journal")
    monkeypatch.setattr(tx, "_assert_session_fresh", lambda *_args: None)
    monkeypatch.setattr(tx, "_write_connected_owner", lambda *_args: None)

    class Ds2:
        @staticmethod
        def send_frame(*_args, **_kwargs):
            return b"\x12\x04\xA0\x00"

    result = tx.write_connected_modules(
        Ds2(), session, archive_dir=tmp_path, journal=journal)
    record = journal.load(session.token)

    assert result["requires_key_cycle"] is True
    assert record.phase == "awaiting_cycle"
    assert [(write.owner, write.complete) for write in record.writes] == [
        ("stability", True), ("dme_post_coding", True),
    ]


def test_interrupted_recovery_can_restore_original_and_verify(
        monkeypatch, tmp_path):
    session = _e46_connected_session()
    archive = tx.archive_connected_swap(session, tmp_path)
    journal = SwapOperationJournal(tmp_path / "journal")
    journal.create(
        session.token,
        plan={
            "token": session.token, "family": session.family,
            "chassis": session.chassis, "source": session.source.value,
            "target": session.target.value,
        },
        archive_path=archive,
    )
    journal.mark_write_intent(session.token, "stability")
    journal.mark_failed(session.token, "simulated interruption")
    recovered = tx.load_connected_swap_journal(journal.load(session.token))
    events = []
    monkeypatch.setattr(tx, "_assert_egs_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        tx, "_restore_connected_owner",
        lambda _ds2, _session, name: events.append(f"restore {name}"),
    )
    monkeypatch.setattr(tx, "read_e46_dme_transmission", lambda _ds2: session.source)
    monkeypatch.setattr(tx, "_verify_egs", lambda *_args: None)
    monkeypatch.setattr(tx, "read_mk20_transmission", lambda _ds2: session.stability)

    class Ds2:
        @staticmethod
        def identify():
            return session.dme_ident

        @staticmethod
        def send_frame(*_args, **_kwargs):
            return b"\x12\x04\xA0\x00"

    recovery_id = f"{session.token}-restore"
    result = tx.recover_connected_modules(
        Ds2(), recovered, "original", journal=journal,
        journal_id=recovery_id, supersedes=session.token,
    )
    verified = tx.verify_connected_original(
        Ds2(), recovered, journal=journal, journal_id=recovery_id)
    tx.settle_superseded_connected_swaps(journal, recovery_id)

    assert events == ["restore stability"]
    assert result["requires_key_cycle"] is True
    assert verified["restored"] is True
    assert journal.load(recovery_id).phase == "verified"
    assert tx.recoverable_connected_swaps(journal) == ()


def test_recovery_preflights_all_owners_and_never_restores_untouched(
        monkeypatch, tmp_path):
    session = _e46_connected_session()
    session.ews_coding = tx.CodingState("EWS", b"ews", 81, b"\x01\x0A\x01\x0A\x0A")
    session.target_ews_coding = b"\x00\x0A\x01\x0A\x0A"
    archive = tx.archive_connected_swap(session, tmp_path)
    journal = SwapOperationJournal(tmp_path / "journal")
    journal.create(
        session.token,
        plan={
            "token": session.token, "family": session.family,
            "chassis": session.chassis, "source": session.source.value,
            "target": session.target.value,
        },
        archive_path=archive,
    )
    journal.mark_write_intent(session.token, "stability")
    journal.mark_failed(session.token, "interrupted stability write")
    recovered = tx.load_connected_swap_journal(journal.load(session.token))
    events = []
    monkeypatch.setattr(tx, "_assert_egs_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        tx, "_read_connected_owner_image",
        lambda _ds2, current, name: tx._connected_owner_before(current, name),
    )
    monkeypatch.setattr(
        tx, "_restore_connected_owner",
        lambda _ds2, _session, name: events.append(name),
    )
    monkeypatch.setattr(tx, "read_e46_dme_transmission", lambda _ds2: session.source)

    class Ds2:
        @staticmethod
        def identify():
            return session.dme_ident

        @staticmethod
        def send_frame(*_args, **_kwargs):
            return b"\x12\x04\xA0\x00"

    tx.recover_connected_modules(
        Ds2(), recovered, "original", journal=journal,
        journal_id=f"{session.token}-restore", supersedes=session.token,
    )

    assert events == ["stability"]


def test_recovery_changed_untouched_owner_refuses_before_new_journal_or_write(
        monkeypatch, tmp_path):
    session = _e46_connected_session()
    session.ews_coding = tx.CodingState("EWS", b"ews", 81, b"\x01\x0A\x01\x0A\x0A")
    session.target_ews_coding = b"\x00\x0A\x01\x0A\x0A"
    archive = tx.archive_connected_swap(session, tmp_path)
    journal = SwapOperationJournal(tmp_path / "journal")
    journal.create(
        session.token,
        plan={
            "token": session.token, "family": session.family,
            "chassis": session.chassis, "source": session.source.value,
            "target": session.target.value,
        },
        archive_path=archive,
    )
    journal.mark_write_intent(session.token, "stability")
    journal.mark_failed(session.token, "interrupted stability write")
    recovered = tx.load_connected_swap_journal(journal.load(session.token))
    monkeypatch.setattr(tx, "_assert_egs_snapshot", lambda *_args, **_kwargs: None)

    def read_owner(_ds2, current, name):
        before = tx._connected_owner_before(current, name)
        return b"changed" if name == "ews_coding" else before

    monkeypatch.setattr(tx, "_read_connected_owner_image", read_owner)
    monkeypatch.setattr(
        tx, "_restore_connected_owner",
        lambda *_args: pytest.fail("global preflight must finish before writes"),
    )

    class Ds2:
        @staticmethod
        def identify():
            return session.dme_ident

    recovery_id = f"{session.token}-restore"
    with pytest.raises(RuntimeError, match="ews_coding changed"):
        tx.recover_connected_modules(
            Ds2(), recovered, "original", journal=journal,
            journal_id=recovery_id, supersedes=session.token,
        )
    assert not journal.record_path(recovery_id).exists()


def test_ms41_recovery_rejects_transmission_record_drift_before_module_write(
        monkeypatch, tmp_path):
    before = bytes(range(256)) * 2
    target = bytearray(before)
    target[0x1CA:0x1CE] = b"\x02\x00\x03\x00"
    stability = tx.CodingState("ASC5", b"asc", 6, b"\x10")
    session = tx.ConnectedSwapSession(
        "0123456789abcdef0123456789abcdef", "ready", "Ready", (), (), (),
        Transmission.AUTOMATIC, Transmission.MANUAL, "MS41.2", "E39",
        dme_ident=b"1406464 connected identity", program="1406464",
        stability=stability, target_stability=b"\x00",
        eeprom_before=before, eeprom_target=bytes(target),
        eeprom_variant="MS41.2",
    )
    archive = tx.archive_connected_swap(session, tmp_path)
    journal = SwapOperationJournal(tmp_path / "journal")
    journal.create(
        session.token,
        plan={
            "token": session.token, "family": session.family,
            "chassis": session.chassis, "source": session.source.value,
            "target": session.target.value,
        },
        archive_path=archive,
    )
    journal.mark_write_intent(session.token, "stability")
    journal.mark_failed(session.token, "interrupted module write")
    recovered = tx.load_connected_swap_journal(journal.load(session.token))
    monkeypatch.setattr(tx, "_assert_egs_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        tx, "_read_connected_owner_image",
        lambda _ds2, current, name: tx._connected_owner_before(current, name),
    )
    monkeypatch.setattr(
        tx, "read_ms41_runtime_transmission", lambda *_args: session.source,
    )
    monkeypatch.setattr(
        tx, "_restore_connected_owner",
        lambda *_args: pytest.fail("EEPROM preflight must finish before writes"),
    )

    class Ds2:
        @staticmethod
        def identify():
            return session.dme_ident

    changed = bytearray(before)
    changed[0x1CA] ^= 1
    recovery_id = f"{session.token}-restore"
    with pytest.raises(RuntimeError, match="transmission record changed"):
        tx.recover_connected_modules(
            Ds2(), recovered, "original", journal=journal,
            journal_id=recovery_id, supersedes=session.token,
            eeprom_current=changed,
        )
    assert not journal.record_path(recovery_id).exists()


def test_ms41_zero_write_crash_closes_as_verified_noop(monkeypatch, tmp_path):
    before = bytes(range(256)) * 2
    stability = tx.CodingState("ASC5", b"asc", 6, b"\x10")
    session = tx.ConnectedSwapSession(
        "0123456789abcdef0123456789abcdef", "ready", "Ready", (), (), (),
        Transmission.AUTOMATIC, Transmission.MANUAL, "MS41.2", "E39",
        dme_ident=b"1406464 connected identity", program="1406464",
        stability=stability, target_stability=b"\x00",
        eeprom_before=before, eeprom_target=before,
        eeprom_variant="MS41.2",
    )
    archive = tx.archive_connected_swap(session, tmp_path)
    journal = SwapOperationJournal(tmp_path / "journal")
    journal.create(
        session.token,
        plan={
            "token": session.token, "family": session.family,
            "chassis": session.chassis, "source": session.source.value,
            "target": session.target.value,
        },
        archive_path=archive,
    )
    recovered = tx.load_connected_swap_journal(journal.load(session.token))
    monkeypatch.setattr(tx, "_assert_egs_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        tx, "_read_connected_owner_image",
        lambda _ds2, current, name: tx._connected_owner_before(current, name),
    )
    monkeypatch.setattr(
        tx, "read_ms41_runtime_transmission", lambda *_args: session.source,
    )
    monkeypatch.setattr(
        tx, "_restore_connected_owner",
        lambda *_args: pytest.fail("no owner write was attempted before the crash"),
    )

    class Ds2:
        @staticmethod
        def identify():
            return session.dme_ident

    recovery_id = f"{session.token}-restore"
    result = tx.recover_connected_modules(
        Ds2(), recovered, "original", journal=journal,
        journal_id=recovery_id, supersedes=session.token,
        eeprom_current=before,
    )

    assert result["completed"] is True
    assert result["restored"] is True
    assert journal.load(session.token).phase == "restored"
    assert not journal.record_path(recovery_id).exists()


def test_destructive_entry_refuses_stale_constructed_e36_session_before_archive(
        monkeypatch, tmp_path):
    session = tx.ConnectedSwapSession(
        "token", "ready", "Ready", (), (), (),
        Transmission.AUTOMATIC, Transmission.MANUAL, "MS41.2", "E36",
    )
    monkeypatch.setattr(
        tx, "archive_connected_swap",
        lambda *_args: pytest.fail("stale session must not be archived"),
    )
    ds2 = SimpleNamespace(identify=lambda: b"different-dme")
    with pytest.raises(RuntimeError, match="identity changed"):
        tx.write_connected_modules(
            ds2, session, archive_dir=tmp_path,
            eeprom_current=b"\x00" * 512,
        )
