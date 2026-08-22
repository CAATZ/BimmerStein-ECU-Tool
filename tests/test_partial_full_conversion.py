"""Regression tests for MS41ECU.tune_from_full() / tune_into_full() -- the
Partial/Full tab's Extract/Merge logic.

History: an earlier version of this extraction used a plain file slice
(full[0x14000:0x1A000]), which silently drops the tune partition's last 8 KB
and pads it with 0xFF, because the ECU's tune partition is CPU/DS2-order
(descrambled via file = CPU XOR 0x4000 per 16 KB block) -- NOT a contiguous
region in the file-order full ROM. These tests pin the CORRECT (descrambled)
behavior against REAL matched full+partial reads from actual ECUs (not just
synthetic bytes), across all 4 MS41 variants, so this can't silently regress
back to the naive slice.
"""
import os, sys
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ms41 import MS41ECU
from tests.conftest import ref, ref_partial


def test_merge_rejects_a_non_full_base():
    with pytest.raises(ValueError, match="262144 B full ROM"):
        MS41ECU.tune_into_full(bytes(MS41ECU.TUNE_SIZE), bytes(MS41ECU.TUNE_SIZE))

# "MS41.3" (REF_MS41.3/stock_*) is deliberately excluded here: that pair shows
# ~20 small scattered byte diffs from genuine live ECU-state drift during a
# multi-minute full read on a bench ECU mid patch-testing, not an extraction
# bug -- see the note in conftest.py. "MS41.3clean" is a verified-clean pair.
_VARIANTS = ["MS41.0", "MS41.1", "MS41.1b", "MS41.3clean", "MS41.2or3"]


def test_tune_from_full_matches_the_real_partial_read_from_the_same_ecu():
    for variant in _VARIANTS:
        full = ref(variant)
        real_partial = ref_partial(variant)
        extracted = MS41ECU.tune_from_full(full)
        assert extracted == real_partial, (
            f"{variant}: extracted partial does not byte-match the real "
            f"factory-tool partial read from the same ECU"
        )


def test_tune_into_full_round_trips_to_the_original_full_image():
    for variant in _VARIANTS:
        full = ref(variant)
        partial = MS41ECU.tune_from_full(full)
        merged = MS41ECU.tune_into_full(full, partial)
        assert bytes(merged) == full, (
            f"{variant}: extract-then-merge did not reproduce the original "
            f"full ROM byte-for-byte"
        )


def test_naive_contiguous_slice_would_have_been_wrong():
    """Documents WHY the descramble exists: the naive full[0x14000:0x1A000]
    slice that caused the historical bug does NOT match the real partition
    for any variant -- it drops the last 8 KB block-swap."""
    for variant in _VARIANTS:
        full = ref(variant)
        real_partial = ref_partial(variant)
        naive_slice = full[0x14000:0x14000 + MS41ECU.TUNE_SIZE]
        assert naive_slice != real_partial, (
            f"{variant}: expected the naive contiguous slice to mismatch "
            f"the real partial (that mismatch is exactly the historical bug "
            f"this descramble fixes) -- if this now matches, the reference "
            f"data or the law changed and this test needs review"
        )
