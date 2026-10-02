"""Exercise MS41.3 signature detection through the actual desktop connection route."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

gui = pytest.importorskip("gui", reason="PyQt5 not available")


@pytest.mark.parametrize("signature, expected", [
    (gui.SS1V2_PROG_SIG, "MS41.3"),
    (b"\xff" * len(gui.SS1V2_PROG_SIG), "MS41.2"),
    (None, None),
])
def test_connect_detects_program_family_without_calibration_markers(monkeypatch, signature, expected):
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(gui.MS41FlashGUI, "_refresh_ports", lambda self: None)
    window = gui.MS41FlashGUI()
    reads = []
    connected = []
    program_address = gui.SS1V2_PROG_SIG_ADDR ^ 0x4000

    class FakeDS2:
        def __init__(self, **kwargs):
            pass

        def open(self):
            pass

        def close(self):
            pass

        def identify(self):
            return b"1406464" + b"\x00" * 20

        def read_vin(self):
            return ""

        def read_mem(self, address, length):
            reads.append((address, length))
            if address == program_address:
                if signature is None:
                    raise RuntimeError("program signature unavailable")
                return signature
            if address == gui.ecu_info.CAL_ID_ADDR:
                return b"12000000"
            return b"\xff" * length

    monkeypatch.setattr(gui, "DS2Interface", FakeDS2)
    monkeypatch.setattr(window, "_start_session_log", lambda: None)
    monkeypatch.setattr(window, "_read_new_info_fields", lambda *args: {})
    monkeypatch.setattr(window, "_read_live_identity_source", lambda *args: None)
    monkeypatch.setattr(gui.softbsl_service, "calguard_recovery_ready", lambda *args: False)
    monkeypatch.setattr(window, "_on_connected", lambda *args: connected.append(args))
    monkeypatch.setattr(window, "_run_task", lambda task, on_success, on_failure:
                        on_success(task(lambda *args: None, lambda *args: None)))
    try:
        window.cb_port.clear()
        window.cb_port.addItem("OFFLINE_TEST")
        window._connect()
        assert connected[0][-1][1] == expected
        assert (program_address, len(gui.SS1V2_PROG_SIG)) in reads
        assert all(address != 0x15F60 for address, _ in reads)
    finally:
        window.close()
        app.processEvents()
