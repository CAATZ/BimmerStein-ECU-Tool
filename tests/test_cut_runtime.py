"""Installed Launch code must be identified independently from Ignition Cut."""

import pytest

from cut_runtime import (
    detect_alphan_failsafe_runtime, detect_cut_runtime, detect_launch_runtime,
    launch_runtime_probes,
)
from engines.patcher import patch_ms41
from tests.conftest import ref


@pytest.mark.parametrize("ecu_id,family,suffix", [
    ("1429861", "MS41.0", "_ms410"),
    ("1437806", "MS41.1", "_ms411"),
    ("1406464", "MS41.2", "_ms412"),
    ("SHINDE1", "MS41.3", ""),
])
@pytest.mark.parametrize("revision", ["V20", "V19", "V18", "V17", "V16", "V14"])
def test_launch_probe_requires_its_own_hooks_and_matching_state_layout(
        ecu_id, family, suffix, revision):
    image, _ = patch_ms41.build(
        ref(family), ["ignition_cut_" + revision.lower() + suffix],
        allow_deprecated=True)
    image = bytearray(image)

    class ImageDS2:
        def read_mem(self, address, length):
            assert address < 0x8000 or address >= 0x18000  # Program ROM, including low-segment code.
            return bytes(image[(address + index) ^ 0x4000] for index in range(length))

    ds2 = ImageDS2()
    expected = "V16" if revision in ("V16", "V17", "V18", "V19", "V20") else "legacy"
    assert detect_cut_runtime(ds2, ecu_id) == expected
    assert detect_launch_runtime(ds2, ecu_id) is None
    image, _ = patch_ms41.build(
        ref(family), ["ignition_cut_" + revision.lower() + suffix,
                      "launch_control_" + revision.lower() + suffix],
        allow_deprecated=True)
    image = bytearray(image)
    assert detect_cut_runtime(ds2, ecu_id) == expected
    assert detect_launch_runtime(ds2, ecu_id) == expected

    # Leave all calibration/state bytes intact and remove a real Launch hook.
    address, installed_hook = next(
        probes[0] for candidate, probes in launch_runtime_probes(ecu_id)
        if candidate == revision)
    for index in range(len(installed_hook)):
        image[(address + index) ^ 0x4000] = 0
    assert detect_cut_runtime(ds2, ecu_id) == expected
    assert detect_launch_runtime(ds2, ecu_id) is None


def test_launch_probe_unknown_id_and_short_read():
    class UnavailableDS2:
        def read_mem(self, address, length):
            return bytes(length - 1)

    assert detect_launch_runtime(UnavailableDS2(), "unknown") is None
    with pytest.raises(ValueError, match="short patch-program read"):
        detect_launch_runtime(UnavailableDS2(), "1429861")


@pytest.mark.parametrize("pid,revision", [("alphan_failsafe_v3", "V3"), ("alphan_failsafe", "V4")])
def test_alphan_probe_requires_complete_revision_on_shinde1(pid, revision):
    image = bytearray(ref("MS41.3"))

    class ImageDS2:
        def read_mem(self, address, length):
            assert 0 < length <= 247
            return bytes(image[(address + index) ^ 0x4000] for index in range(length))

    ds2 = ImageDS2()
    assert not detect_alphan_failsafe_runtime(ds2, "SHINDE1")
    installed, _ = patch_ms41.build(ref("MS41.3"), [pid], allow_deprecated=True)
    image = bytearray(installed)
    assert detect_alphan_failsafe_runtime(ds2, "SHINDE1") == revision
    assert not detect_alphan_failsafe_runtime(ds2, "1406464")
    patches = patch_ms41.load_patches()
    for edit in patches[pid]["edits"]:
        # Old short-prefix probes missed the tail of code bodies and the
        # retired V2 hook. Every edit contributes to exact identification.
        last = edit["off"] + len(bytes.fromhex(edit["data"])) - 1
        image[last] ^= 1
        assert detect_alphan_failsafe_runtime(ds2, "SHINDE1") is None
        image[last] ^= 1
    late = patches["alphan_failsafe_v2"]["cave"]["splice_off"]
    retired = next(e for e in patches["alphan_failsafe_v2"]["edits"] if e["off"] == late)
    image[late:late + 4] = bytes.fromhex(retired["data"])
    assert detect_alphan_failsafe_runtime(ds2, "SHINDE1") is None


@pytest.mark.parametrize("patch_id", ["alphan_failsafe_v1", "alphan_failsafe_v2"])
def test_alphan_probe_rejects_retired_publication_paths(patch_id):
    image, _ = patch_ms41.build(ref("MS41.3"), [patch_id], allow_deprecated=True)

    class ImageDS2:
        def read_mem(self, address, length):
            return bytes(image[(address + index) ^ 0x4000] for index in range(length))

    assert not detect_alphan_failsafe_runtime(ImageDS2(), "SHINDE1")
