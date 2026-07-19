"""Shared stock-MS41 write-authorization policy.

The working write captures and live authorization tests use challenge 0x1E.
The stock handler dispatches command 0x90 by E658 state. Production entry uses
a bounded challenge policy: an empty contextual A1 is retried only after RAM
proves E658 remains zero and E74B is unchanged. A ten-second zero-traffic delay
remains only as the bounded compatibility fallback after a clean contextual
A1. This is never a blind seed or key retry.
"""

CAPTURED_INITIAL_CHALLENGE = 0x1E
MAX_INITIAL_SEED_ATTEMPTS = 2
INITIAL_SEED_RETRY_DELAY = 10.0
AUTHORIZATION_STATE_ADDRESS = 0xE658
WRONG_KEY_COUNTER_ADDRESS = 0xE74B
FLASH_MODE_MARKER_ADDRESS = 0xE740
NATIVE_FAST_REENTRY_TIMER_ADDRESS = 0xE72E
NATIVE_FAST_REENTRY_TIMER_INITIAL_VALUE = 1000
NATIVE_FAST_REENTRY_LATCH_ADDRESS = 0xE659
NATIVE_FAST_REENTRY_LATCH_READY_VALUE = 0xCC
NATIVE_FAST_REENTRY_POLL_INTERVAL = 1.0
NATIVE_FAST_REENTRY_TIMEOUT = 20.0

# Live MS41.3 trials proved that a second native-fast operation must not start
# until the previous operation's common E72E/E659 completion path has rearmed.
# This is deliberately scoped by ds2_native_fast_reentry.py; it is not an
# initial write-authorization prerequisite.
