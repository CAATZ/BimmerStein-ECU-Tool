"""Application preference for adapter-facing high-speed ECU transfers.

Transport sessions snapshot this value when they start, including for recovery.
Each frontend owns persistence and permits changes only while idle.
"""

STANDARD_HIGH_BAUD = 187_500
COMPATIBILITY_HIGH_BAUD = 125_000

_high_baud = STANDARD_HIGH_BAUD


def set_compatibility_baud(enabled: bool) -> None:
    global _high_baud
    _high_baud = COMPATIBILITY_HIGH_BAUD if enabled else STANDARD_HIGH_BAUD


def get_high_baud() -> int:
    return _high_baud
