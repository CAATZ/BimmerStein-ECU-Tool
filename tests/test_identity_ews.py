import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import identity


def test_ews_frames_for_synthetic_isn():
    f = identity.ews_frames("1234")
    assert f["read"] == bytes([0x12, 0x04, 0x00, 0x16])
    assert f["write"] == bytes([0x44, 0x06, 0x61, 0x04, 0xD2, 0xF5])
    assert f["hex_value"] == 0x4D2
    assert f["truncated"] is False


def test_ews_frames_reject_bad_isn():
    import pytest
    with pytest.raises(ValueError):
        identity.ews_frames("12")      # not 4 digits


def test_ews_frames_truncation_flag():
    f = identity.ews_frames("9999")    # proven lookup behavior wraps to the low 12 bits
    assert f["truncated"] is True
    assert f["wrapped"] is True
    assert f["hex_value"] == 0x70F


def test_ews_frames_matches_wrapped_synthetic_example():
    f = identity.ews_frames("6789")
    assert f["hex_value"] == 0xA85
    assert f["write"] == bytes([0x44, 0x06, 0x61, 0x0A, 0x85, 0xAC])
    assert f["wrapped"] is True
