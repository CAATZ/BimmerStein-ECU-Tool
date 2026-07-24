import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ms41
import pytest
from tests.conftest import SYNTHETIC_IDENTITIES, ref


def _synthetic_ms410():
    data = bytearray(b"\xFF" * ms41.MS41ECU.FULL_ROM_SIZE)
    data[ms41.ECU_ID_ADDR:ms41.ECU_ID_ADDR + 7] = b"1429861"
    data[ms41.CALID_ADDR_256K:ms41.CALID_ADDR_256K + 8] = b"41000000"
    for address in ms41.FIRMWARE_COMPAT_PROGRAM_ADDRS:
        data[address:address + 4] = b"0641"
    for address in ms41.FIRMWARE_COMPAT_CAL_ADDRS:
        data[address:address + 4] = b"0641"
    for address in (0x14016, 0x14026, 0x14036):
        data[address:address + 4] = b"0600"
    return data


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
    assert r["program_compatibility_id"] == "0960"
    assert r["calibration_compatibility_id"] == "0960"
    assert set(r) == {
        "program", "cal", "hybrid", "program_compatibility_id",
        "calibration_compatibility_id", "ecu_id", "cal_id", "vin",
    }


def test_same_variant_different_compatibility_ids_are_rejected():
    data = _synthetic_ms410()
    for address in ms41.FIRMWARE_COMPAT_CAL_ADDRS:
        data[address:address + 4] = b"0659"

    assert ms41.MS41ECU.detect_program_variant(data) == "MS41.0"
    assert ms41.MS41ECU.detect_variant(data) == "MS41.0"
    assert "0641" in ms41.MS41ECU.check_hybrid(data)
    assert "0659" in ms41.MS41ECU.check_hybrid(data)


def test_calibration_family_headers_are_not_treated_as_repeated_full_ids():
    data = _synthetic_ms410()

    assert ms41.MS41ECU.read_calibration_compatibility_id(data) == "0641"
    assert ms41.MS41ECU.check_hybrid(data) is None


def test_conflicting_repeated_compatibility_ids_are_rejected():
    data = _synthetic_ms410()
    address = ms41.FIRMWARE_COMPAT_PROGRAM_ADDRS[-1]
    data[address:address + 4] = b"0659"

    assert ms41.MS41ECU.read_program_compatibility_id(data) is None
    assert "inconsistent repeated identifiers" in ms41.MS41ECU.check_hybrid(data)
