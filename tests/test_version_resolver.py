import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ms41
import pytest
from tests.conftest import SYNTHETIC_IDENTITIES, ref


@pytest.mark.parametrize("variant", ["MS41.0", "MS41.1", "MS41.2", "MS41.3"])
def test_canonical_full_rom_resolver_identifies_all_supported_variants(variant):
    resolved = ms41.MS41ECU.resolve_version(ref(variant))
    assert resolved["program"] == variant
    assert resolved["cal"] == variant
    assert resolved["hybrid"] is None


def test_ms41_1_is_not_mislabelled_as_3():
    """The bug: has_ss1v2_program counts >=64 non-FF at 0x39A9A, but MS41.1
    fills that range too (with e6fc7c1b..., not 9a116390...). The exact
    signature must classify a .1 ROM as MS41.1, never MS41.3."""
    data = ref("MS41.1")
    assert ms41.MS41ECU.has_ss1v2_program_sig(data) is False
    r = ms41.MS41ECU.resolve_version(data)
    assert r["program"] == "MS41.1"


def test_ms41_3_signature_and_resolve():
    data = ref("MS41.3")
    assert ms41.MS41ECU.has_ss1v2_program_sig(data) is True
    r = ms41.MS41ECU.resolve_version(data)
    assert r["program"] == "MS41.3"
    assert r["cal"] == "MS41.3"
    assert r["hybrid"] is None


def test_either_calibration_marker_resolves_ms413_but_program_gate_stays_independent():
    original = ref("MS41.3")

    ss1_only = bytearray(original)
    ss1_only[ms41.MS41_3_CREDIT_MARKER_256K:
             ms41.MS41_3_CREDIT_MARKER_256K + 8] = b"\xFF" * 8
    assert ms41.MS41ECU.resolve_version(bytes(ss1_only))["cal"] == "MS41.3"

    credit_only = bytearray(original)
    credit_only[ms41.MS41_3_MARKER_256K:
                ms41.MS41_3_MARKER_256K + 5] = b"\xFF" * 5
    resolved = ms41.MS41ECU.resolve_version(bytes(credit_only))
    assert resolved["cal"] == "MS41.3"
    assert resolved["program"] == "MS41.3"
    assert resolved["hybrid"] is None

    hybrid = bytearray(original)
    hybrid[ms41.SS1V2_PROG_SIG_ADDR:
           ms41.SS1V2_PROG_SIG_ADDR + len(ms41.SS1V2_PROG_SIG)] = b"\xFF" * 4
    assert ms41.MS41ECU.resolve_version(bytes(hybrid))["hybrid"] is not None


def test_resolve_reports_all_fields():
    data = ref("MS41.1")
    r = ms41.MS41ECU.resolve_version(data)
    assert r["ecu_id"] == "1437806"
    assert r["vin"] == SYNTHETIC_IDENTITIES["MS41.1"][1]
    assert set(r) == {"program", "cal", "hybrid", "ecu_id", "cal_id", "vin"}
