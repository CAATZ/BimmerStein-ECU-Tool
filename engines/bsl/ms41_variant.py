"""Compatibility import for older BSL callers.

Variant resolution is owned by the application's canonical :mod:`ms41` module so the
recovery path cannot drift from the Flash, Soft-BSL, and patch safety gates.
"""
from ms41 import *  # noqa: F401,F403
