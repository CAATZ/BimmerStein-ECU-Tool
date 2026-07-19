"""Process-local native-fast reentry state keyed by serial port.

The ECU owns the real readiness state.  This registry only records that this
process completed (or recovered from) a native-fast selector session on a port,
so the next native-fast operation knows it must observe the shared firmware
completion latch before entering again.
"""

from __future__ import annotations

import threading


_lock = threading.RLock()
_pending_ports: set[str] = set()


def _key(port: str) -> str:
    value = str(port).strip().casefold()
    if not value:
        raise ValueError("serial port must not be empty")
    return value


def reentry_required(port: str) -> bool:
    with _lock:
        return _key(port) in _pending_ports


def mark_reentry_required(port: str) -> None:
    with _lock:
        _pending_ports.add(_key(port))


def clear_reentry_required(port: str) -> None:
    with _lock:
        _pending_ports.discard(_key(port))


def _reset_for_tests() -> None:
    with _lock:
        _pending_ports.clear()
