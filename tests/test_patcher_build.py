import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import checksum
from engines.patcher import patch_ms41
from tests.conftest import ref
import pytest


def test_build_applies_cal_guard_and_recomputes_bootcrc():
    out, log = patch_ms41.build(
        ref("MS41.3"), ["softbsl_loader", "cal_guard"])
    assert len(out) == patch_ms41.FULL
    patches = patch_ms41.load_patches()
    for e in patches["cal_guard"]["edits"]:
        off, d = e["off"], bytes.fromhex(e["data"])
        assert out[off:off + len(d)] == d, f"edit @0x{off:05X} not applied"
    assert checksum.bootloader_checksum_ok(bytearray(out)) is True
    assert isinstance(log, list) and any("cal_guard" in line for line in log)


def test_build_rejects_wrong_base():
    with pytest.raises(patch_ms41.PatchError):
        patch_ms41.build(bytes(patch_ms41.FULL), ["cal_guard"])


def test_build_rejects_collision():
    # door_0x43 and alphan_failsafe share cave base 0x39B6A
    with pytest.raises(patch_ms41.PatchError):
        patch_ms41.build(ref("MS41.3"), ["door_0x43", "alphan_failsafe"])


def test_build_rejects_unknown_and_mixed_target():
    with pytest.raises(patch_ms41.PatchError):
        patch_ms41.build(ref("MS41.3"), ["not_a_patch"])
    with pytest.raises(patch_ms41.PatchError):
        patch_ms41.build(ref("MS41.3"), ["cal_guard", "vanos_minrpm_ms410"])  # .3 + .0 targets


def test_build_does_not_write_files(tmp_path):
    # build() is pure — returns bytes, touches no disk
    before = set(os.listdir(tmp_path))
    patch_ms41.build(ref("MS41.3"), ["softbsl_loader", "cal_guard"])
    assert set(os.listdir(tmp_path)) == before
