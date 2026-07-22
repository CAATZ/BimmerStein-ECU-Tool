"""Production-facing orchestration for native fast DS2 on stock MS41 ECUs."""

from __future__ import annotations

import datetime as _datetime
from dataclasses import dataclass
from pathlib import Path

from ds2_fast_contracts import FastOperation, LinkRate
from ds2_fast_full_write import (
    NativeFastFullWriteTransport,
)
from ds2_fast_partial_write import (
    InitialWriteSeedUnavailable,
    NativeFastWriteReentryNotReady,
    NativeFastPartialWriteTransport,
)
from ds2_fast_slim_write import (
    SlimNativeFastFullWriteSession,
    SlimNativeFastPartialWriteSession,
)
from ds2_fast_safety import OperationJournal
from ds2_native_fast_reentry import (
    clear_reentry_required,
    mark_reentry_required,
    reentry_required,
)
from app_paths import mutable_path


NATIVE_JOURNAL_DIR = mutable_path("backups", "native_fast", "journals")


class NativeFastServiceError(RuntimeError):
    pass


class NativeFastPreEraseFailure(NativeFastServiceError):
    """A fast attempt failed before erase; fallback is allowed only when flagged."""

    def __init__(self, cause: Exception, *, safe_legacy_fallback: bool):
        self.cause = cause
        self.safe_legacy_fallback = bool(safe_legacy_fallback)
        self.seed_unavailable = isinstance(cause, InitialWriteSeedUnavailable)
        self.reentry_not_ready = isinstance(cause, NativeFastWriteReentryNotReady)
        super().__init__(str(cause))


@dataclass
class NativeWriteRecovery:
    """Live post-erase context that must remain powered and open for recovery."""

    port: str
    transport: object
    session: object
    target: bytes
    journal_path: Path
    error: Exception

    @property
    def is_open(self) -> bool:
        return bool(getattr(self.transport, "is_open", False))

    @property
    def retry_supported(self) -> bool:
        """Whether the retained ECU handler is still qualified for replay."""
        value = getattr(self.session, "can_recover_in_place", None)
        return True if value is None else bool(value)

    def close_after_confirmed_power_cycle(self) -> None:
        """Release only after the operator has physically cycled/recovered the ECU."""
        self.transport.close()


class NativeWriteRecoveryRequired(NativeFastServiceError):
    """A destructive operation failed and its D2XX session is deliberately held."""

    def __init__(self, recovery: NativeWriteRecovery):
        self.recovery = recovery
        super().__init__(
            f"{recovery.error}. FLASH INCOMPLETE — DO NOT TURN IGNITION OFF; "
            "the native fast recovery session is still open"
        )


def _stamp() -> str:
    return _datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def _safe_port(port: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in str(port))


def _progress_adapter(progress_cb):
    if progress_cb is None:
        return None
    return lambda phase, current, total: progress_cb(current, total, phase)


def _new_journal(port: str, operation: FastOperation) -> OperationJournal:
    NATIVE_JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    path = NATIVE_JOURNAL_DIR / (
        f"{operation.value}-{_safe_port(port)}-{_stamp()}.jsonl"
    )
    return OperationJournal(
        path,
        operation=operation,
        metadata={"port": str(port), "transport": "d2xx", "protocol": "native_fast_ds2"},
    )


def _event_sink(journal: OperationJournal, observer=None):
    """Always journal transport events; mirror diagnostics without affecting I/O."""
    def callback(event, fields):
        journal.event_callback(event, fields)
        if observer is not None:
            try:
                observer(event, dict(fields))
            except Exception:
                # External diagnostic rendering must never alter flash state.
                pass
    return callback


def _finish_setup_failure(
    journal: OperationJournal,
    error: Exception,
    *,
    phase: str,
) -> None:
    """Seal a journal created before a transport/session setup failure."""
    if journal.closed:
        return
    try:
        journal.finish(
            "failed",
            phase=phase,
            error=f"{type(error).__name__}: {error}",
            destructive_started=False,
        )
    except Exception:
        # Preserve the setup exception.  A journal I/O failure must not hide the
        # reason a destructive session was never armed.
        pass


def write_partial_d2xx(
    port: str,
    target_tune: bytes,
    *,
    verify_write: bool = False,
    progress_cb=None,
    event_cb=None,
):
    """Run the slim partial writer and retain D2XX after a post-erase failure."""
    journal = _new_journal(port, FastOperation.PARTIAL_WRITE)
    try:
        transport = NativeFastPartialWriteTransport.open_d2xx(
            port, event_cb=_event_sink(journal, event_cb)
        )
    except Exception as error:
        _finish_setup_failure(journal, error, phase="transport_open")
        raise
    pending = reentry_required(port)
    try:
        session = SlimNativeFastPartialWriteSession(
            transport,
            bytes(target_tune),
            journal,
            verify_write=verify_write,
            reentry_required=pending,
            reentry_ready_cb=lambda: clear_reentry_required(port),
            progress_cb=_progress_adapter(progress_cb),
        )
    except Exception as error:
        transport.close()
        _finish_setup_failure(journal, error, phase="session_setup")
        raise NativeFastPreEraseFailure(
            error, safe_legacy_fallback=False
        ) from error
    try:
        result = session.execute()
    except Exception as error:
        if session.destructive_started:
            raise NativeWriteRecoveryRequired(
                NativeWriteRecovery(
                    port=str(port),
                    transport=transport,
                    session=session,
                    target=bytes(target_tune),
                    journal_path=journal.path,
                    error=error,
                )
            ) from error
        if (
            bool(getattr(session, "fast_write_armed", False))
            and getattr(session, "link", None) is LinkRate.LOW
        ):
            mark_reentry_required(port)
        transport.close()
        raise NativeFastPreEraseFailure(
            error, safe_legacy_fallback=session.safe_legacy_fallback
        ) from error
    else:
        if (
            bool(getattr(session, "fast_write_armed", False))
            and getattr(session, "link", None) is LinkRate.LOW
        ):
            mark_reentry_required(port)
        transport.close()
        return result


def write_full_d2xx(
    port: str,
    target_file_image: bytes,
    *,
    connected_family: str,
    verify_write: bool = False,
    variant_conversion: bool = False,
    progress_cb=None,
    event_cb=None,
):
    """Run the slim full writer, staying high on success and retaining failures."""
    journal = _new_journal(port, FastOperation.FULL_WRITE)
    try:
        transport = NativeFastFullWriteTransport.open_d2xx(
            port, event_cb=_event_sink(journal, event_cb)
        )
    except Exception as error:
        _finish_setup_failure(journal, error, phase="transport_open")
        raise
    pending = reentry_required(port)
    try:
        session = SlimNativeFastFullWriteSession(
            transport,
            bytes(target_file_image),
            journal,
            connected_family=connected_family,
            verify_write=verify_write,
            variant_conversion=variant_conversion,
            reentry_required=pending,
            reentry_ready_cb=lambda: clear_reentry_required(port),
            progress_cb=_progress_adapter(progress_cb),
        )
    except Exception as error:
        transport.close()
        _finish_setup_failure(journal, error, phase="session_setup")
        raise NativeFastPreEraseFailure(
            error, safe_legacy_fallback=False
        ) from error
    try:
        result = session.execute()
    except Exception as error:
        if session.destructive_started:
            raise NativeWriteRecoveryRequired(
                NativeWriteRecovery(
                    port=str(port),
                    transport=transport,
                    session=session,
                    target=bytes(target_file_image),
                    journal_path=journal.path,
                    error=error,
                )
            ) from error
        if (
            bool(getattr(session, "fast_write_armed", False))
            and getattr(session, "link", None) is LinkRate.LOW
        ):
            mark_reentry_required(port)
        transport.close()
        raise NativeFastPreEraseFailure(
            error, safe_legacy_fallback=session.safe_legacy_fallback
        ) from error
    else:
        # A successful full write intentionally leaves the ECU at 187500, but
        # retaining the host handle is unnecessary; the UI instructs a cycle.
        transport.close()
        return result


def write_program_d2xx(
    port: str,
    target_file_image: bytes,
    *,
    connected_family: str,
    verify_write: bool = False,
    progress_cb=None,
    event_cb=None,
):
    """Deploy only the program array with the native fast DS2 contract.

    This is the stock-DS2 bootstrap operation used before the temporary 0x43
    door is armed.  It intentionally does not erase or program the tune sector;
    success intentionally leaves the ECU at high-rate flash-listen, after which
    the installer requests its required manual key-cycle.
    """
    journal = _new_journal(port, FastOperation.FULL_WRITE)
    try:
        transport = NativeFastFullWriteTransport.open_d2xx(
            port, event_cb=_event_sink(journal, event_cb)
        )
    except Exception as error:
        # No transport means no request reached the ECU; a caller may safely
        # retry the exact operation through its legacy low-rate path.
        _finish_setup_failure(journal, error, phase="transport_open")
        raise NativeFastPreEraseFailure(error, safe_legacy_fallback=True) from error
    pending = reentry_required(port)
    try:
        session = SlimNativeFastFullWriteSession(
            transport,
            bytes(target_file_image),
            journal,
            connected_family=connected_family,
            verify_write=verify_write,
            reentry_required=pending,
            reentry_ready_cb=lambda: clear_reentry_required(port),
            progress_cb=_progress_adapter(progress_cb),
        )
    except Exception as error:
        transport.close()
        _finish_setup_failure(journal, error, phase="session_setup")
        raise NativeFastPreEraseFailure(error, safe_legacy_fallback=False) from error
    try:
        result = session.execute_program_only()
    except Exception as error:
        if session.destructive_started:
            raise NativeWriteRecoveryRequired(
                NativeWriteRecovery(
                    port=str(port),
                    transport=transport,
                    session=session,
                    target=bytes(target_file_image),
                    journal_path=journal.path,
                    error=error,
                )
            ) from error
        if (
            bool(getattr(session, "fast_write_armed", False))
            and getattr(session, "link", None) is LinkRate.LOW
        ):
            mark_reentry_required(port)
        transport.close()
        raise NativeFastPreEraseFailure(
            error, safe_legacy_fallback=session.safe_legacy_fallback
        ) from error
    else:
        transport.close()
        return result


def resume_recovery(recovery: NativeWriteRecovery, *, progress_cb=None):
    """Continue a held post-erase operation without cycling or reopening COM."""
    if not recovery.is_open:
        raise NativeFastServiceError("the retained native recovery transport is closed")
    if not recovery.retry_supported:
        raise NativeFastServiceError(
            "the write failed during or after finalization; same-session replay is "
            "disabled because the retained ECU handler is no longer in a qualified "
            "write state"
        )
    operation = (
        FastOperation.FULL_WRITE
        if isinstance(recovery.session, SlimNativeFastFullWriteSession)
        else FastOperation.PARTIAL_WRITE
    )
    journal = _new_journal(recovery.port, operation)
    recovery.session.journal = journal
    recovery.session.progress_cb = _progress_adapter(progress_cb)
    recovery.journal_path = journal.path
    try:
        result = recovery.session.recover_in_place()
    except Exception as error:
        if not journal.closed:
            try:
                journal.finish(
                    "commit_unknown",
                    phase="retained_recovery",
                    error=f"{type(error).__name__}: {error}",
                    destructive_started=True,
                    transport_retained=True,
                )
            except Exception:
                pass
        recovery.error = error
        raise NativeWriteRecoveryRequired(recovery) from error
    recovery.transport.close()
    return result
