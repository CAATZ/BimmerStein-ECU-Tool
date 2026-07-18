from pathlib import Path

import pytest

import ds2_native_fast_service as service
from ds2_fast_contracts import FastOperation


class FakeJournal:
    def __init__(self, path: Path, operation: FastOperation):
        self.path = path
        self.operation = operation.value
        self.operation_id = "00000000-0000-0000-0000-000000000001"
        self.closed = False
        self.outcome = None
        self.fields = None

    def event_callback(self, event, fields):
        assert not self.closed

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


def test_partial_service_uses_slim_session_without_backup_and_passes_verify(
    monkeypatch, tmp_path
):
    journal = FakeJournal(tmp_path / "partial-slim.jsonl", FastOperation.PARTIAL_WRITE)
    transport = FakeTransport()
    captured = {}
    sentinel = object()
    monkeypatch.setattr(service, "_new_journal", lambda port, operation: journal)
    monkeypatch.setattr(
        service.NativeFastPartialWriteTransport,
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

    monkeypatch.setattr(service, "SlimNativeFastPartialWriteSession", SlimSession)

    result = service.write_partial_d2xx("COM1", b"target", verify_write=True)

    assert result is sentinel
    assert captured["target"] == b"target"
    assert captured["kwargs"]["verify_write"] is True
    assert not transport.is_open


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
    )

    assert result is sentinel
    assert captured["kwargs"]["connected_family"] == "amd"
    assert captured["kwargs"]["verify_write"] is False
    assert not transport.is_open


def test_program_only_service_uses_program_only_session(
    monkeypatch, tmp_path
):
    journal = FakeJournal(tmp_path / "program-only.jsonl", FastOperation.FULL_WRITE)
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

        def execute_program_only(self):
            return sentinel

    monkeypatch.setattr(service, "SlimNativeFastFullWriteSession", SlimSession)

    result = service.write_program_d2xx(
        "COM1",
        b"target",
        connected_family="amd",
        verify_write=False,
    )

    assert result is sentinel
    assert captured["target"] == b"target"
    assert captured["kwargs"]["connected_family"] == "amd"
    assert captured["kwargs"]["verify_write"] is False
    assert not transport.is_open

def test_failed_retained_recovery_keeps_transport_and_seals_new_journal(
    monkeypatch, tmp_path
):
    journal = FakeJournal(tmp_path / "recovery.jsonl", FastOperation.PARTIAL_WRITE)
    transport = FakeTransport()

    class FailingSession:
        progress_cb = None
        journal = None

        def recover_in_place(self):
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
    assert transport.is_open
    assert journal.closed
    assert journal.outcome == "commit_unknown"
    assert journal.fields["transport_retained"] is True


def test_successful_retained_recovery_closes_transport(monkeypatch, tmp_path):
    journal = FakeJournal(tmp_path / "recovery-ok.jsonl", FastOperation.PARTIAL_WRITE)
    transport = FakeTransport()
    sentinel = object()

    class SuccessfulSession:
        progress_cb = None
        journal = None

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
