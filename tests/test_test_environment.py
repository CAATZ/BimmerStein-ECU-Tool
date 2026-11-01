"""Exercise the test bootstrap in a fresh process, independent of import order."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap


def _run_harness(tmp_path, source, count):
    root = Path(__file__).resolve().parents[1]
    (tmp_path / "conftest.py").write_bytes((root / "tests/conftest.py").read_bytes())
    (tmp_path / "test_lifetime.py").write_text(textwrap.dedent(source), encoding="utf-8")
    environment = dict(os.environ, PYTHONPATH=str(root), QT_QPA_PLATFORM="offscreen")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "--rootdir", str(tmp_path), "test_lifetime.py"],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{count} passed" in result.stdout


def test_gui_bootstrap_isolates_data_sets_dpi_and_disposes_closed_windows(tmp_path):
    _run_harness(tmp_path, '''\
        import os
        from pathlib import Path
        import tempfile
        from PyQt5 import sip
        from PyQt5.QtCore import QCoreApplication, QSettings, Qt
        from PyQt5.QtGui import QGuiApplication
        from PyQt5.QtWidgets import QWidget

        retained = []

        def test_create_and_close(qapp):
            assert QCoreApplication.testAttribute(Qt.AA_EnableHighDpiScaling)
            assert QCoreApplication.testAttribute(Qt.AA_UseHighDpiPixmaps)
            assert QGuiApplication.highDpiScaleFactorRoundingPolicy() == (
                Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
            data = Path(os.environ["BIMMERSTEIN_DATA_DIR"])
            assert data.parent.parent == Path.cwd() / ".tmp"
            assert Path(tempfile.gettempdir()) == data.parent / "temp"
            assert os.environ["TEMP"] == os.environ["TMP"] == tempfile.gettempdir()
            import gui
            for preferences in (gui.MS41FlashGUI._live_settings(),
                                gui._PanelSplitter._settings(None)):
                assert preferences.format() == QSettings.IniFormat
                assert Path(preferences.fileName()) == data / "preferences.ini"
                preferences.setValue("test/value", 42)
                preferences.sync()
                assert preferences.status() == QSettings.NoError
            assert (data / "preferences.ini").is_file()
            explicit_file = Path.cwd() / "explicit.ini"
            explicit = QSettings(str(explicit_file), QSettings.IniFormat)
            assert Path(explicit.fileName()) == explicit_file
            window = QWidget()
            retained.append(window)
            window.close()
            assert not sip.isdeleted(window)

        def test_previous_window_was_deleted(qapp):
            assert sip.isdeleted(retained[0])
            assert not qapp.topLevelWidgets()
        ''', 2)


def test_nongui_checks_do_not_load_the_desktop(tmp_path):
    _run_harness(tmp_path, '''\
        import sys

        def test_without_widgets():
            assert "gui" not in sys.modules
        ''', 1)
