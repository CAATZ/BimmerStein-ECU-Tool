import itertools

import pytest

from checksum import correct_checksums, verify_checksum
from ms41 import (
    CODING_FAMILY_CAL_ADDRS,
    CODING_FAMILY_FILE_ADDR,
    CODING_FAMILY_PARTIAL_ADDRS,
    CODING_FAMILY_PROGRAM_ADDRS,
    MS41ECU,
)
from tests.conftest import ref


def _assert_family(image, family):
    for address in CODING_FAMILY_PROGRAM_ADDRS:
        assert bytes(image[address:address + 3]) == family
    for address in CODING_FAMILY_CAL_ADDRS:
        assert image[address] == family[2]


def test_coding_family_graft_updates_only_the_seven_compatibility_fields():
    target = bytearray(b"\xA5" * MS41ECU.FULL_ROM_SIZE)
    grafted = MS41ECU.graft_coding_family(target, b"606")

    _assert_family(grafted, b"606")
    changed = {
        index
        for index, (before, after) in enumerate(zip(target, grafted))
        if before != after
    }
    expected = set(CODING_FAMILY_CAL_ADDRS)
    for address in CODING_FAMILY_PROGRAM_ADDRS:
        expected.update(range(address, address + 3))
    assert changed == expected


def test_partial_coding_family_graft_updates_only_four_calibration_headers():
    target = bytearray(b"\xA5" * MS41ECU.TUNE_SIZE)
    grafted = MS41ECU.graft_coding_family(target, b"606")

    assert all(grafted[address] == ord("6") for address in CODING_FAMILY_PARTIAL_ADDRS)
    changed = {
        index
        for index, (before, after) in enumerate(zip(target, grafted))
        if before != after
    }
    assert changed == set(CODING_FAMILY_PARTIAL_ADDRS)


@pytest.mark.parametrize(
    "source_variant,target_variant",
    [
        pair
        for pair in itertools.permutations(
            ("MS41.0", "MS41.1", "MS41.2", "MS41.3"), 2
        )
    ],
)
def test_every_supported_variant_conversion_grafts_and_rechecksums(
    source_variant, target_variant
):
    source = ref(source_variant)
    target = ref(target_variant)
    family = source[
        CODING_FAMILY_FILE_ADDR:CODING_FAMILY_FILE_ADDR + 3
    ]

    grafted = MS41ECU.graft_coding_family(target, family)
    corrected, _details = correct_checksums(
        grafted,
        correct_program=target_variant != "MS41.3",
    )

    _assert_family(corrected, family)
    assert MS41ECU.detect_program_variant(corrected) == target_variant
    assert MS41ECU.detect_variant(corrected) == target_variant
    assert (
        MS41ECU.read_program_compatibility_id(corrected)
        == MS41ECU.read_calibration_compatibility_id(corrected)
    )
    assert MS41ECU.check_hybrid(corrected) is None
    assert verify_checksum(corrected)[0]


@pytest.mark.parametrize(
    "boot_variant,target_variant",
    list(
        itertools.product(
            ("MS41.0", "MS41.1", "MS41.2", "MS41.3"),
            repeat=2,
        )
    ),
)
def test_every_boot_and_partial_variant_pair_grafts_and_rechecksums(
    boot_variant, target_variant
):
    boot = ref(boot_variant)
    target = MS41ECU.tune_from_full(ref(target_variant))
    family = boot[CODING_FAMILY_FILE_ADDR:CODING_FAMILY_FILE_ADDR + 3]

    grafted = MS41ECU.graft_coding_family(target, family)
    corrected, _details = correct_checksums(grafted, correct_program=False)

    assert all(
        corrected[address] == family[2]
        for address in CODING_FAMILY_PARTIAL_ADDRS
    )
    assert MS41ECU.detect_variant(corrected) == target_variant
    assert verify_checksum(corrected)[0]


@pytest.mark.parametrize("family", (b"", b"60", b"60A", b"6060"))
def test_coding_family_graft_rejects_invalid_boot_values(family):
    with pytest.raises(ValueError, match="three ASCII digits"):
        MS41ECU.graft_coding_family(
            b"\xFF" * MS41ECU.FULL_ROM_SIZE,
            family,
        )
