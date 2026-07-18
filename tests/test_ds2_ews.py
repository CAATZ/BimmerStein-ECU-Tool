import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ds2


def test_isn_from_identify_takes_last_four_digits():
    synthetic_isn = "1234"
    payload = b"SYNTHETIC DS2 IDENTIFICATION".ljust(38, b"0") + synthetic_isn.encode()
    assert len(payload) == 42
    assert ds2._isn_from_identify(payload) == synthetic_isn


def test_isn_from_identify_short_payload():
    assert ds2._isn_from_identify(b"78") == "78"        # fewer than 4 → whatever is there
    assert ds2._isn_from_identify(b"") == ""
