import pytest

from live_statistics import BoxSummary, box_summary


def test_signed_inclusive_quartiles_and_actual_sample_whiskers():
    result = box_summary([100, -1, 2, -100, 0, -2, 1])
    assert result == BoxSummary(7, -1.5, 0, 1.5, -2, 2, (-100, 100))


def test_floating_samples_exclude_missing_and_nonfinite_without_losing_zero():
    result = box_summary(iter([None, float("nan"), -1, 0, float("inf"),
                               .5, 1.5, float("-inf")]))
    assert result == BoxSummary(4, -.25, .25, .75, -1, 1.5)


def test_flat_fractional_history_has_no_outliers():
    assert box_summary([.125] * 6) == BoxSummary(6, .125, .125, .125, .125, .125)


@pytest.mark.parametrize("values,count,middle", [
    ([], 0, None), ([None, float("nan"), float("inf")], 0, None),
    ([-1], 1, -1), ([-1, 0], 2, -.5), ([None, -1, 0, 1], 3, 0),
])
def test_short_history_retains_median_without_fabricating_a_box(values, count, middle):
    assert box_summary(values) == BoxSummary(count, median=middle)
