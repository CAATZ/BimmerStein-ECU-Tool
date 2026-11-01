"""Existing VIN transaction on exact applications and independently known boots."""

import pytest

from tests import test_ms42_ms43_vin_workflow as transactions
from tests.test_ms42_ms43_transfer_revision_matrix import COMBINATIONS, workflow


@pytest.fixture(params=COMBINATIONS + [("430055", "430055"), ("430055", "430069"),
                                     ("430069", "430055")],
                ids=lambda item: f"program-{item[0]}-boot-{item[1]}")
def revision_vin(request, monkeypatch):
    base = workflow.__wrapped__(request, monkeypatch)
    return transactions.vin_workflow.__wrapped__(base, monkeypatch)


@pytest.mark.parametrize("mode", ("append", "full", "dirty"))
def test_same_transaction_and_whole_array_preservation(revision_vin, mode):
    transactions.test_vin_write_preserves_whole_array_and_uses_no_finalizer(revision_vin, mode)


@pytest.mark.parametrize("mode", ("append", "full"))
@pytest.mark.parametrize("fault", ("body_ack", "body_partial", "commit_ack", "commit_partial"))
def test_explicit_retry_preserves_committed_and_partial_writes(revision_vin, monkeypatch, mode, fault):
    transactions.test_interrupted_write_keeps_target_and_explicit_retry_verifies_before_programming(
        revision_vin, monkeypatch, mode, fault)


@pytest.mark.parametrize("committed", (False, True))
def test_history_erase_failure_and_lost_acknowledgment(revision_vin, monkeypatch, committed):
    transactions.test_full_history_erase_failure_or_lost_reply_can_resume(
        revision_vin, monkeypatch, committed)


def test_failed_backup_sends_no_write(revision_vin):
    transactions.test_backup_failure_cannot_send_flash_commands(revision_vin)


@pytest.mark.parametrize("fault", ("reset_ack", "journal_finish"))
def test_completion_error_cannot_replay_write(revision_vin, fault):
    transactions.test_verified_write_is_never_replayed_after_completion_error(revision_vin, fault)
