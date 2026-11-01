"""Shared finite-sample statistics for live diagnostic displays."""

from dataclasses import dataclass
import math
from statistics import median, quantiles


@dataclass(frozen=True)
class BoxSummary:
    count: int
    q1: float | None = None
    median: float | None = None
    q3: float | None = None
    whisker_low: float | None = None
    whisker_high: float | None = None
    outliers: tuple[float, ...] = ()


def box_summary(values):
    """Inclusive quartiles and Tukey whiskers, excluding missing/nonfinite values."""
    samples = sorted(value for value in values
                     if value is not None and math.isfinite(value))
    count = len(samples)
    if count < 4:
        return BoxSummary(count, median=median(samples) if samples else None)
    q1, _q2, q3 = quantiles(samples, n=4, method="inclusive")
    fence = 1.5 * (q3 - q1)
    inside = [value for value in samples if q1 - fence <= value <= q3 + fence]
    outside = tuple(value for value in samples if value < q1 - fence or value > q3 + fence)
    return BoxSummary(count, q1, median(samples), q3, inside[0], inside[-1], outside)
