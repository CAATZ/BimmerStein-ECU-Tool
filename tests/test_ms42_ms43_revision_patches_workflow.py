"""Qualified revision settings use the existing full writer and retained retry."""

import pytest

from tests.test_ms42_ms43_transfer_revision_matrix import workflow as workflow
from tests import test_ms42_ms43_config_patches_workflow as existing


@pytest.mark.parametrize("label", ("Enabled", "Disabled"))
@pytest.mark.parametrize("interrupted", (False, True))
def test_revision_options_use_the_existing_full_write_and_retry(workflow, monkeypatch, label, interrupted):
    existing.test_community_options_full_write_and_retry(workflow, monkeypatch, label, interrupted)
