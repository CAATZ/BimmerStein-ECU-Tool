import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # headless, in case PyQt is imported
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest

gui = pytest.importorskip("gui", reason="PyQt5 not available")
import ms41

# _program_is_ms41_3 is a staticmethod — call it directly, with NO QApplication/widget
# construction (constructing MS41FlashGUI repeatedly under the offscreen platform is flaky).
_detect = gui.MS41FlashGUI._program_is_ms41_3

# file 0x39A9A (program-region SS1v2 sig) is read at DS2 0x3DA9A; the cal-resident ABHISHEK
# marker (file 0x11F60) is read at DS2 0x15F60 — inside the 24 KB tune, so a tune wipes it.
_PROG_DS2_ADDR = ms41.SS1V2_PROG_SIG_ADDR ^ 0x4000
_CAL_ABHISHEK_ADDR = 0x15F60


class _FakeDS2:
    def __init__(self, mem):
        self.mem = mem
        self.reads = []

    def read_mem(self, addr, n):
        self.reads.append(addr)
        return self.mem.get(addr, b"\xff" * n)[:n]


def test_ms41_3_detected_from_program_sig_when_cal_marker_is_wiped():
    # A tuned MS41.3 ECU: the ABHISHEK cal marker is gone (overwritten by the custom tune),
    # but the program-region SS1v2 signature is intact. Detection must key off the program
    # signature, so this ECU is still MS41.3.
    ds2 = _FakeDS2({
        _CAL_ABHISHEK_ADDR: b"\xff" * 8,            # cal marker wiped by the tune
        _PROG_DS2_ADDR: ms41.SS1V2_PROG_SIG,        # program signature present
    })
    assert _detect(ds2) is True
    assert _PROG_DS2_ADDR in ds2.reads              # it reads the PROGRAM region, not the cal


def test_program_sig_absent_is_not_ms41_3():
    assert _detect(_FakeDS2({})) is False           # all 0xFF (factory MS41.2)


def test_program_sig_read_failure_is_not_ms41_3():
    class Boom:
        def read_mem(self, a, n):
            raise RuntimeError("no response")

    assert _detect(Boom()) is False                 # fail-safe
