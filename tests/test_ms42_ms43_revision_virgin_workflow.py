"""Virginizing uses application status addresses independently of boot profiles."""

import pytest

from tests import test_ms42_ms43_virgin_workflow as transactions
from tests.test_ms42_ms43_revision_vin_workflow import revision_vin as revision_vin


@pytest.fixture
def revision_virgin(revision_vin, monkeypatch):
    return transactions.virgin_workflow.__wrapped__(revision_vin, monkeypatch)


@pytest.mark.parametrize("clear_vin", (False, True))
def test_selected_ranges_and_exact_native_status_address(revision_virgin, clear_vin):
    transactions.test_virginize_changes_only_selected_regions(revision_virgin, clear_vin)


@pytest.mark.parametrize("clear_vin", (False, True))
@pytest.mark.parametrize("fault", ("erase_rejected", "erase_ack", "program_partial", "program_ack", "last_ack"))
def test_interrupted_write_requires_explicit_retry(revision_virgin, monkeypatch, fault, clear_vin):
    transactions.test_interrupted_virginize_retains_original_target_and_requires_explicit_retry(
        revision_virgin, monkeypatch, fault, clear_vin)


@pytest.mark.parametrize("clear_vin", (False, True))
def test_already_erased_data_is_never_reprogrammed(revision_virgin, clear_vin):
    transactions.test_already_virginized_does_not_erase_or_program(revision_virgin, clear_vin)


def test_backup_failure_is_nondestructive(revision_virgin):
    transactions.test_backup_failure_leaves_flash_untouched(revision_virgin)


@pytest.mark.parametrize("fault", ("reset_ack", "paired_status", "journal_finish"))
def test_completion_failure_never_replays_or_aligns(revision_virgin, monkeypatch, fault):
    transactions.test_completed_write_never_replays_or_aligns_after_followup_failure(
        revision_virgin, monkeypatch, fault)
