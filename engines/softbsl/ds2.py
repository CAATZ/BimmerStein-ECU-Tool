"""Compatibility import for standalone Soft-BSL callers.

The application and recovery workflows share the canonical top-level :mod:`ds2`
transport. Keeping this module name avoids breaking older scripts without maintaining
a second, drifting DS2 implementation.
"""
from ds2 import *  # noqa: F401,F403
from ds2 import _progress_bar, _xor  # noqa: F401
