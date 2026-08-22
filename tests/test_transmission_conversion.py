from dataclasses import replace
from functools import reduce
from hashlib import sha256

import pytest

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


def test_ms41_dynamic_mode_blocks_without_softbsl_and_never_uses_fixed_fallback():
    plan = plan_ms41_conversion(_ms41_request(softbsl=False))
    assert plan.status is PlanStatus.ACTION_REQUIRED
    assert not plan.can_write
    assert any("Installed Soft-BSL is required" in reason for reason in plan.reasons)
    assert any("will not be substituted" in warning for warning in plan.warnings)

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
    assert any("will not use another fixed selector as a fallback" in reason
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
