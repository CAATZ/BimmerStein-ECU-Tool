"""Shared stock-MS41 write-authorization policy.

The working write captures and live authorization tests use challenge 0x1E.
The stock handler dispatches command 0x90 by E658 state. A challenge may be
retried only after RAM proves E658 is still zero and E74B did not increase;
otherwise another BMW payload could be consumed as a wrong key.
"""

CAPTURED_INITIAL_CHALLENGE = 0x1E
MAX_INITIAL_SEED_ATTEMPTS = 45
INITIAL_SEED_RETRY_DELAY = 0.20
AUTHORIZATION_STATE_ADDRESS = 0xE658
WRONG_KEY_COUNTER_ADDRESS = 0xE74B
