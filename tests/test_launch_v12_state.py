"""Run the installed launch-retard episode owner on all four exact families."""
import os
from pathlib import Path
import sys

import pytest

from engines.patcher import patch_ms41
from engines.patcher.verify_launch_retard_state import verify_launch_retard_state
from tests.conftest import ref


@pytest.mark.parametrize("family,variant,suffix", [
    ("MS41.0", "1429861", "_ms410"),
    ("MS41.1", "1437806", "_ms411"),
    ("MS41.2", "1406464", "_ms412"),
    ("MS41.3", "SS1v2", ""),
])
def test_installed_launch_retard_state(family, variant, suffix):
    root = os.environ.get("MS41EMU_ROOT")
    if root:
        sys.path.insert(0, str(Path(root)))
    Emulator = pytest.importorskip("ms41emu").Emulator
    patch_id = "launch_control_v18" + suffix
    patch = patch_ms41.load_patches()[patch_id]
    image, _ = patch_ms41.build(ref(family), [*patch["requires"], patch["id"]])
    assert verify_launch_retard_state(image, variant, Emulator.load,
                                     lambda value, _variant: value) == 75
