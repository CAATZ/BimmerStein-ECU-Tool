from pathlib import Path

import pytest

import ds2_native_fast_service as service
import ds2_native_fast_reentry as native_reentry
from ds2_fast_contracts import (
    CommitUnknownError,
    FastOperation,
    FlashOperation,
    FlashRequest,
    LinkRate,
    SessionState,
)
from ds2_fast_partial_write import PartialWriteCancelled


@pytest.fixture(autouse=True)
def _clean_native_reentry_registry():
    native_reentry._reset_for_tests()
    yield
    native_reentry._reset_for_tests()


class FakeJournal:
    def __init__(self, path: Path, operation: FastOperation):
        self.path = path
        self.operation = operation.value
        self.operation_id = "00000000-0000-0000-0000-000000000001"
        self.closed = False
        self.outcome = None
        self.fields = None
        self.events = []

    def event_callback(self, event, fields):
        assert not self.closed
        self.events.append((event, dict(fields)))

    def finish(self, outcome, **fields):
        assert not self.closed
        self.outcome = outcome
        self.fields = fields
        self.closed = True


class FakeTransport:
    def __init__(self):
        self.is_open = True
        self.event_cb = None

    def close(self):
        self.is_open = False


def test_service_journal_factory_keeps_directory_override_compatibility(
    monkeypatch, tmp_path
):
    journal_dir = tmp_path / "compat-journals"
    monkeypatch.setattr(service, "NATIVE_JOURNAL_DIR", journal_dir)

    journal = service._new_journal("COM/1", FastOperation.PARTIAL_READ)
    journal.finish("aborted", destructive_started=False)

    assert journal.path.parent == journal_dir
    assert journal.path.name.startswith("partial_read-COM_1-")


def test_partial_service_uses_slim_session_without_backup_and_passes_verify(
    monkeypatch, tmp_path
):
    journal = FakeJournal(tmp_path / "partial-slim.jsonl", FastOperation.PARTIAL_WRITE)
    transport = FakeTransport()
    captured = {}
    sentinel = object()
    monkeypatch.setattr(service, "_new_journal", lambda port, operation: journal)
    injected_factory = object()

    def open_transport(port, event_cb=None, serial_factory=None):
        captured["event_cb"] = event_cb
        captured["serial_factory"] = serial_factory
        return transport

    monkeypatch.setattr(
        service.NativeFastPartialWriteTransport,
        "open_d2xx",
        open_transport,
    )

    class SlimSession:
        destructive_started = False
        safe_legacy_fallback = False

        def __init__(self, transport_arg, target, journal_arg, **kwargs):
            captured.update(
                transport=transport_arg, target=target, journal=journal_arg, kwargs=kwargs)

        def execute(self):
            captured["event_cb"](
                "write_flash_mode_marker_observed",
                {"label": "write_authorization_initial", "e740": 0},
            )
            return sentinel

    monkeypatch.setattr(service, "SlimNativeFastPartialWriteSession", SlimSession)

    observed = []
    result = service.write_partial_d2xx(
        "COM1",
        b"target",
        verify_write=True,
        expected_ecu_id="1406464",
        expected_program_compatibility_id="0660",
        expected_coding_family="606",
        expected_program_signature_hex="a5a5a5a5",
        expected_driver_signature_hex="e00e0d58f04ec084",
        event_cb=lambda event, fields: observed.append((event, dict(fields))),
        serial_factory=injected_factory,
    )

    assert result is sentinel
    assert captured["target"] == b"target"
    assert captured["kwargs"]["verify_write"] is True
    assert captured["kwargs"]["expected_ecu_id"] == "1406464"
    assert captured["kwargs"]["expected_program_compatibility_id"] == "0660"
    assert captured["kwargs"]["expected_coding_family"] == "606"
    assert captured["kwargs"]["expected_program_signature_hex"] == "a5a5a5a5"
    assert captured["kwargs"]["expected_driver_signature_hex"] == "e00e0d58f04ec084"
    assert captured["serial_factory"] is injected_factory
    assert captured["kwargs"]["reentry_required"] is False
    assert journal.events == observed == [
        (
            "write_flash_mode_marker_observed",
            {"label": "write_authorization_initial", "e740": 0},
        )
    ]
    assert not transport.is_open


def test_partial_service_consumes_pending_same_port_reentry(monkeypatch, tmp_path):
    journal = FakeJournal(tmp_path / "partial-reentry.jsonl", FastOperation.PARTIAL_WRITE)
    transport = FakeTransport()
    captured = {}
    sentinel = object()
    native_reentry.mark_reentry_required("COM1")
    monkeypatch.setattr(service, "_new_journal", lambda port, operation: journal)
    monkeypatch.setattr(
        service.NativeFastPartialWriteTransport,
        "open_d2xx",
        lambda port, event_cb=None: transport,
    )

    class SlimSession:
        destructive_started = False
        safe_legacy_fallback = False
        fast_write_armed = False

        def __init__(self, _transport, _target, _journal, **kwargs):
            captured.update(kwargs)

        def execute(self):
            assert captured["reentry_required"] is True
            captured["reentry_ready_cb"]()
            return sentinel

    monkeypatch.setattr(service, "SlimNativeFastPartialWriteSession", SlimSession)

    assert service.write_partial_d2xx("com1", b"target") is sentinel
    assert not native_reentry.reentry_required("COM1")
    assert not transport.is_open


def test_eeprom_record_service_passes_identity_and_factory_without_retry_owner(
    monkeypatch,
    tmp_path,
):
    journal = FakeJournal(tmp_path / "eeprom-record.jsonl", FastOperation.PARTIAL_WRITE)
    transport = FakeTransport()
    captured = {}
    sentinel = object()
    injected_factory = object()
    identity = b"I" * 42
    monkeypatch.setattr(service, "_new_journal", lambda port, operation: journal)

    def open_transport(port, **kwargs):
        captured["open"] = kwargs
        return transport

    monkeypatch.setattr(
        service.NativeFastPartialWriteTransport,
        "open_d2xx",
        open_transport,
    )

    class EepromSession:
        fast_write_armed = True
        link = LinkRate.LOW

        def __init__(self, transport_arg, target, journal_arg, **kwargs):
            captured.update(
                transport=transport_arg,
                target=target,
                journal=journal_arg,
                kwargs=kwargs,
            )

        def execute_eeprom_record(
            self,
            variant,
            expected_record,
            target_record,
            *,
            expected_identity=None,
        ):
            captured.update(
                variant=variant,
                expected_record=expected_record,
                target_record=target_record,
                expected_identity=expected_identity,
            )
            journal.finish("success", retry_policy="never")
            return sentinel

    monkeypatch.setattr(service, "SlimNativeFastPartialWriteSession", EepromSession)

    result = service.write_eeprom_record_d2xx(
        "COM1",
        "MS41.3",
        b"\x01\x00\x02\x00",
        b"\x02\x00\x03\x00",
        expected_identity=identity,
        serial_factory=injected_factory,
    )

    assert result is sentinel
    assert captured["open"]["serial_factory"] is injected_factory
    assert captured["target"] == b"\xFF" * (24 * 1024)
    assert captured["variant"] == "MS41.3"
    assert captured["expected_identity"] == identity
    assert transport.is_open is False
    assert native_reentry.reentry_required("COM1")


def test_eeprom_service_propagates_commit_unknown_and_closes_without_replay(
    monkeypatch,
    tmp_path,
):
    journal = FakeJournal(tmp_path / "eeprom-unknown.jsonl", FastOperation.PARTIAL_WRITE)
    transport = FakeTransport()
    error = CommitUnknownError(
        FlashRequest(
            FlashOperation.EEPROM_WRITE,
            0x1CA,
            b"\x02\x00\x03\x00",
        ),
        "no ECU response",
    )
    monkeypatch.setattr(service, "_new_journal", lambda port, operation: journal)
    monkeypatch.setattr(
        service.NativeFastPartialWriteTransport,
        "open_d2xx",
        lambda port, **kwargs: transport,
    )

    class CommitUnknownSession:
        fast_write_armed = True
        link = LinkRate.LOW

        def __init__(self, *_args, **_kwargs):
            pass

        def execute_eeprom_record(self, *_args, **_kwargs):
            journal.finish(
                "commit_unknown",
                commit_unknown=True,
                retry_allowed=False,
            )
            raise error

    monkeypatch.setattr(
        service,
        "SlimNativeFastPartialWriteSession",
        CommitUnknownSession,
    )

    with pytest.raises(CommitUnknownError) as caught:
        service.write_eeprom_record_d2xx(
            "COM1",
            "MS41.2",
            b"\x01\x00\x02\x00",
            b"\x02\x00\x03\x00",
        )

    assert caught.value is error
    assert caught.value.retry_allowed is False
    assert transport.is_open is False
    assert native_reentry.reentry_required("COM1")


def test_write_entry_qualification_returns_only_after_proven_low_cleanup(
    monkeypatch, tmp_path
):
    journal = FakeJournal(
        tmp_path / "write-entry-qualification.jsonl",
        FastOperation.PARTIAL_WRITE,
    )
    transport = FakeTransport()
    captured = {}
    monkeypatch.setattr(service, "_new_journal", lambda port, operation: journal)

    def open_transport(port, **kwargs):
        captured["open"] = kwargs
        return transport

    monkeypatch.setattr(
        service.NativeFastPartialWriteTransport,
        "open_d2xx",
        open_transport,
    )

    class QualificationSession:
        destructive_started = False
        cleanup_attempted = True
        safe_legacy_fallback = True
        link = LinkRate.LOW
        state = SessionState.LOW_READY
        identity = b"SHINDE1" + b" " * 35
        write_authorized = True
        authorization_may_be_active = True
        authorization_state_requires_cycle = False
        fast_write_armed = True

        def __init__(self, _transport, target, _journal, **kwargs):
            captured["target"] = target
            captured["session"] = kwargs

        def execute(self):
            assert captured["session"]["cancel_cb"]() is True
            journal.finish(
                "aborted",
                destructive_started=False,
                safe_legacy_fallback=True,
            )
            raise PartialWriteCancelled("cancelled at before_tune_erase")

    monkeypatch.setattr(
        service, "SlimNativeFastPartialWriteSession", QualificationSession
    )

    result = service.qualify_partial_write_entry_d2xx(
        "COM1",
        expected_ecu_id="SHINDE1",
        serial_factory=object(),
    )

    assert captured["open"]["flash_enabled"] is False
    assert captured["target"] == b"\xFF" * (24 * 1024)
    assert captured["session"]["expected_ecu_id"] == "SHINDE1"
    assert result.identity[:7] == b"SHINDE1"
    assert result.final_link is LinkRate.LOW
    assert result.final_state is SessionState.LOW_READY
    assert result.cleanup_confirmed is True
    assert result.destructive_started is False
    assert result.power_cycle_required is True
    assert transport.is_open is False


def test_write_entry_qualification_failure_after_authorization_requires_cycle(
    monkeypatch, tmp_path
):
    journal = FakeJournal(
        tmp_path / "write-entry-failure.jsonl",
        FastOperation.PARTIAL_WRITE,
    )
    transport = FakeTransport()
    monkeypatch.setattr(service, "_new_journal", lambda port, operation: journal)
    monkeypatch.setattr(
        service.NativeFastPartialWriteTransport,
        "open_d2xx",
        lambda port, **kwargs: transport,
    )

    class FailedQualificationSession:
        destructive_started = False
        cleanup_attempted = False
        safe_legacy_fallback = False
        link = LinkRate.UNKNOWN
        state = SessionState.POWER_CYCLE_REQUIRED
        write_authorized = True
        authorization_may_be_active = True
        authorization_state_requires_cycle = False
        fast_write_armed = False

        def __init__(self, *_args, **_kwargs):
            pass

        def execute(self):
            journal.finish("failed", destructive_started=False)
            raise RuntimeError("cleanup identity unavailable")

    monkeypatch.setattr(
        service,
        "SlimNativeFastPartialWriteSession",
        FailedQualificationSession,
    )

    with pytest.raises(service.NativeFastPreEraseFailure) as caught:
        service.qualify_partial_write_entry_d2xx(
            "COM1", expected_ecu_id="SHINDE1"
        )

    assert caught.value.power_cycle_required is True
    assert transport.is_open is False


def test_transport_open_failure_still_seals_journal(monkeypatch, tmp_path):
    journal = FakeJournal(tmp_path / "full.jsonl", FastOperation.FULL_WRITE)
    monkeypatch.setattr(service, "_new_journal", lambda port, operation: journal)

    def fail_open(*args, **kwargs):
        raise OSError("D2XX unavailable")

    monkeypatch.setattr(service.NativeFastFullWriteTransport, "open_d2xx", fail_open)

    with pytest.raises(OSError, match="D2XX unavailable"):
        service.write_full_d2xx(
            "COM1",
            b"target",
            connected_family="amd",
        )

    assert journal.closed
    assert journal.outcome == "failed"
    assert journal.fields["phase"] == "transport_open"


def test_full_service_uses_slim_session_and_verify_is_optional(monkeypatch, tmp_path):
    journal = FakeJournal(tmp_path / "full-slim.jsonl", FastOperation.FULL_WRITE)
    transport = FakeTransport()
    captured = {}
    sentinel = object()
    monkeypatch.setattr(service, "_new_journal", lambda port, operation: journal)
    monkeypatch.setattr(
        service.NativeFastFullWriteTransport,
        "open_d2xx",
        lambda port, event_cb=None: transport,
    )

    class SlimSession:
        destructive_started = False
        safe_legacy_fallback = False

        def __init__(self, transport_arg, target, journal_arg, **kwargs):
            captured.update(
                transport=transport_arg, target=target, journal=journal_arg, kwargs=kwargs)

        def execute(self):
            return sentinel

    monkeypatch.setattr(service, "SlimNativeFastFullWriteSession", SlimSession)

    result = service.write_full_d2xx(
        "COM1",
        b"target",
        connected_family="amd",
        verify_write=False,
        variant_conversion=True,
        expected_ecu_id="SHINDE1",
        expected_program_compatibility_id="0912",
        expected_coding_family="909",
        expected_program_signature_hex="01020304",
        expected_driver_signature_hex="e00e0d58f04ec084",
    )

    assert result is sentinel
    assert captured["kwargs"]["connected_family"] == "amd"
    assert captured["kwargs"]["verify_write"] is False
    assert captured["kwargs"]["variant_conversion"] is True
    assert captured["kwargs"]["expected_ecu_id"] == "SHINDE1"
    assert captured["kwargs"]["expected_driver_signature_hex"] == "e00e0d58f04ec084"
    assert not transport.is_open


def test_program_only_service_uses_program_only_session(
    monkeypatch, tmp_path
):
    journal = FakeJournal(tmp_path / "program-only.jsonl", FastOperation.FULL_WRITE)
    transport = FakeTransport()
    captured = {}
    sentinel = object()
    native_reentry.mark_reentry_required("COM1")
    monkeypatch.setattr(service, "_new_journal", lambda port, operation: journal)

    def open_transport(port, event_cb=None):
        captured["event_cb"] = event_cb
        return transport

    monkeypatch.setattr(
        service.NativeFastFullWriteTransport,
        "open_d2xx",
        open_transport,
    )

    class SlimSession:
        destructive_started = False
        safe_legacy_fallback = False

        def __init__(self, transport_arg, target, journal_arg, **kwargs):
            captured.update(
                transport=transport_arg, target=target, journal=journal_arg, kwargs=kwargs)

        def execute_program_only(self):
            assert captured["kwargs"]["reentry_required"] is True
            captured["kwargs"]["reentry_ready_cb"]()
            captured["event_cb"](
                "d2xx_queue_status",
                {"phase": "before_initial_write_seed_attempt_1", "rx_bytes": 0},
            )
            return sentinel

    monkeypatch.setattr(service, "SlimNativeFastFullWriteSession", SlimSession)

    observed = []
    result = service.write_program_d2xx(
        "COM1",
        b"target",
        connected_family="amd",
        verify_write=False,
        initial_identity_attempts=3,
        event_cb=lambda event, fields: observed.append((event, dict(fields))),
    )

    assert result is sentinel
    assert captured["target"] == b"target"
    assert captured["kwargs"]["connected_family"] == "amd"
    assert captured["kwargs"]["verify_write"] is False
    assert captured["kwargs"]["initial_identity_attempts"] == 3
    assert not native_reentry.reentry_required("COM1")
    assert journal.events == observed == [
        (
            "d2xx_queue_status",
            {"phase": "before_initial_write_seed_attempt_1", "rx_bytes": 0},
        )
    ]
    assert not transport.is_open


def test_program_only_transport_open_failure_is_not_fallback_safe(
    monkeypatch, tmp_path
):
    journal = FakeJournal(
        tmp_path / "program-open-failure.jsonl", FastOperation.FULL_WRITE
    )
    monkeypatch.setattr(service, "_new_journal", lambda port, operation: journal)
    monkeypatch.setattr(
        service.NativeFastFullWriteTransport,
        "open_d2xx",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            OSError("D2XX unavailable")
        ),
    )

    with pytest.raises(service.NativeFastPreEraseFailure) as caught:
        service.write_program_d2xx(
            "COM1",
            b"target",
            connected_family="amd",
        )

    assert caught.value.safe_legacy_fallback is False
    assert journal.closed
    assert journal.outcome == "failed"
    assert journal.fields["phase"] == "transport_open"


def test_program_only_reentry_timeout_closes_transport_and_preserves_gate(
    monkeypatch, tmp_path
):
    journal = FakeJournal(tmp_path / "program-reentry-timeout.jsonl", FastOperation.FULL_WRITE)
    transport = FakeTransport()
    captured = {}
    native_reentry.mark_reentry_required("COM1")
    monkeypatch.setattr(service, "_new_journal", lambda port, operation: journal)
    monkeypatch.setattr(
        service.NativeFastFullWriteTransport,
        "open_d2xx",
        lambda port, event_cb=None: transport,
    )

    class ReentryTimeoutSession:
        destructive_started = False
        safe_legacy_fallback = False
        fast_write_armed = False

        def __init__(self, _transport, _target, _journal, **kwargs):
            captured.update(kwargs)

        def execute_program_only(self):
            assert captured["reentry_required"] is True
            raise service.NativeFastWriteReentryNotReady(
                "E659 did not reach 0xCC; no challenge, selector, or flash command was sent"
            )

    monkeypatch.setattr(
        service, "SlimNativeFastFullWriteSession", ReentryTimeoutSession
    )

    with pytest.raises(service.NativeFastPreEraseFailure) as caught:
        service.write_program_d2xx(
            "COM1", b"prepared bootstrap", connected_family="amd"
        )

    assert caught.value.reentry_not_ready is True
    assert caught.value.safe_legacy_fallback is False
    assert transport.is_open is False
    assert native_reentry.reentry_required("COM1") is True


def test_program_only_preerase_power_cycle_state_is_preserved(
    monkeypatch, tmp_path
):
    journal = FakeJournal(
        tmp_path / "program-power-cycle.jsonl", FastOperation.FULL_WRITE
    )
    transport = FakeTransport()
    monkeypatch.setattr(service, "_new_journal", lambda port, operation: journal)
    monkeypatch.setattr(
        service.NativeFastFullWriteTransport,
        "open_d2xx",
        lambda port, event_cb=None: transport,
    )

    class KeyRejectedSession:
        destructive_started = False
        safe_legacy_fallback = False
        fast_write_armed = False
        state = SessionState.POWER_CYCLE_REQUIRED

        def __init__(self, *_args, **_kwargs):
            pass

        def execute_program_only(self):
            raise RuntimeError(
                "write key acknowledgement: status 0xA2, expected 0xA0"
            )

    monkeypatch.setattr(
        service, "SlimNativeFastFullWriteSession", KeyRejectedSession
    )

    with pytest.raises(service.NativeFastPreEraseFailure) as caught:
        service.write_program_d2xx(
            "COM1", b"prepared bootstrap", connected_family="intel"
        )

    assert caught.value.power_cycle_required is True
    assert caught.value.safe_legacy_fallback is False
    assert transport.is_open is False


def test_failed_retained_recovery_keeps_transport_and_seals_new_journal(
    monkeypatch, tmp_path
):
    journal = FakeJournal(tmp_path / "recovery.jsonl", FastOperation.PARTIAL_WRITE)
    transport = FakeTransport()

    class FailingSession:
        progress_cb = None
        journal = None
        can_recover_in_place = True
        recovery_calls = 0

        def recover_in_place(self):
            self.recovery_calls += 1
            self.can_recover_in_place = False
            raise RuntimeError("link still noisy")

    session = FailingSession()
    recovery = service.NativeWriteRecovery(
        port="COM1",
        transport=transport,
        session=session,
        target=b"immutable",
        journal_path=tmp_path / "old.jsonl",
        error=RuntimeError("first failure"),
    )
    monkeypatch.setattr(service, "_new_journal", lambda port, operation: journal)

    with pytest.raises(service.NativeWriteRecoveryRequired) as raised:
        service.resume_recovery(recovery)

    assert raised.value.recovery is recovery
    assert "automatic retained replay is disabled" in str(raised.value)
    assert "KEEP ECU POWER ON" in str(raised.value)
    assert recovery.retry_supported is False
    assert recovery.power_cycle_required is False
    assert transport.is_open
    assert journal.closed
    assert journal.outcome == "commit_unknown"
    assert journal.fields["transport_retained"] is True
    with pytest.raises(service.NativeFastServiceError, match="same-session replay"):
        service.resume_recovery(recovery)
    assert session.recovery_calls == 1


def test_recovery_without_explicit_capability_fails_closed_before_opening_journal(
    monkeypatch, tmp_path
):
    transport = FakeTransport()

    class UnknownSession:
        def recover_in_place(self):
            raise AssertionError("unqualified recovery must not be attempted")

    recovery = service.NativeWriteRecovery(
        port="COM1",
        transport=transport,
        session=UnknownSession(),
        target=b"immutable",
        journal_path=tmp_path / "old.jsonl",
        error=RuntimeError("first failure"),
    )
    monkeypatch.setattr(
        service,
        "_new_journal",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("recovery journal must not be opened")
        ),
    )

    assert recovery.retry_supported is False
    with pytest.raises(service.NativeFastServiceError, match="same-session replay"):
        service.resume_recovery(recovery)
    assert transport.is_open is True


def test_a2_refusal_exposes_only_controlled_power_cycle_handoff(tmp_path):
    class A2RefusedSession:
        can_recover_in_place = False
        state = SessionState.POWER_CYCLE_REQUIRED

    recovery = service.NativeWriteRecovery(
        port="COM1",
        transport=FakeTransport(),
        session=A2RefusedSession(),
        target=b"immutable",
        journal_path=tmp_path / "a2.jsonl",
        error=RuntimeError("erase returned A2"),
    )
    error = service.NativeWriteRecoveryRequired(recovery)

    assert recovery.retry_supported is False
    assert recovery.power_cycle_required is True
    assert "controlled recovery power-cycle" in str(error)


def test_finalizer_failure_recovery_refuses_replay_before_opening_journal(
    monkeypatch, tmp_path
):
    transport = FakeTransport()

    class FinalizerFailedSession:
        can_recover_in_place = False

        def recover_in_place(self):
            raise AssertionError("destructive replay must not be attempted")

    recovery = service.NativeWriteRecovery(
        port="COM1",
        transport=transport,
        session=FinalizerFailedSession(),
        target=b"immutable",
        journal_path=tmp_path / "old.jsonl",
        error=RuntimeError("finalizer rejected the image"),
    )
    monkeypatch.setattr(
        service,
        "_new_journal",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("recovery journal must not be opened")
        ),
    )

    with pytest.raises(service.NativeFastServiceError, match="same-session replay"):
        service.resume_recovery(recovery)

    assert recovery.retry_supported is False
    assert transport.is_open is True


def test_successful_retained_recovery_closes_transport(monkeypatch, tmp_path):
    journal = FakeJournal(tmp_path / "recovery-ok.jsonl", FastOperation.PARTIAL_WRITE)
    transport = FakeTransport()
    sentinel = object()

    class SuccessfulSession:
        progress_cb = None
        journal = None
        can_recover_in_place = True

        def recover_in_place(self):
            self.journal.finish("power_cycle_required", recovery=True)
            return sentinel

    session = SuccessfulSession()
    recovery = service.NativeWriteRecovery(
        port="COM1",
        transport=transport,
        session=session,
        target=b"immutable",
        journal_path=tmp_path / "old.jsonl",
        error=RuntimeError("first failure"),
    )
    monkeypatch.setattr(service, "_new_journal", lambda port, operation: journal)

    result = service.resume_recovery(recovery)

    assert result is sentinel
    assert not transport.is_open
    assert journal.outcome == "power_cycle_required"
