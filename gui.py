"""
gui.py — PyQt5 graphical interface for BimmerStein ECU Tool.

Tabs:
  1. Flash       — Read/Write full ROM (256KB) or tune region (24KB)
  2. DTC         — Read, display, and clear Diagnostic Trouble Codes
  3. ECU Info    — Firmware ID, part numbers, chip identification

Connection bar is shared across all tabs.
"""

import sys
import os
import tempfile
import threading
import datetime
import time
import traceback

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QTextEdit,
    QProgressBar, QFileDialog, QGroupBox, QGridLayout,
    QMessageBox, QTabWidget, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView,
    QCheckBox, QSpinBox,
    QLineEdit, QInputDialog, QDialog, QScrollArea
)
from PyQt5.QtCore import Qt, QThread, QObject, pyqtSignal, QTimer, QUrl
from PyQt5.QtGui import (
    QFont, QColor, QTextCursor, QBrush, QDesktopServices, QIcon, QPalette,
)

from ms41 import MS41ECU, SS1V2_PROG_SIG, SS1V2_PROG_SIG_ADDR
from ds2 import DS2Interface, DS2Error
from checksum import verify_checksum, correct_checksums, disable_checksum
from dtc import format_dtc_table, parse_ds2_dtc_response, DS2DTCRecord
import ecu_info
from live_data import (LiveDataPoller, PROFILE_DISPLAY_NAMES,
                       TELEGRAM_PARAM_NAMES, display_rows)
from rom_analyzer import analyze as analyze_rom
from backup_manager import BackupManager, BACKUP_DIR
from port_owner import PortOwner, PortBusyError
import patch_service
import identity
import ds2_fast_read
import ds2_native_fast_service
from ds2_write_authorization import AUTHORIZATION_STATE_ADDRESS
import softbsl_service
import softbsl_install
import bsl_service
from app_paths import mutable_path
from definition_registry import (
    DefinitionConflictError,
    DefinitionRegistry,
    DefinitionRegistryError,
)

APP_ICON_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "assets", "bimmerstein_ecu_tool.png")
IDENTITY_BACKUP_SOURCE = "Identity / EWS"
IDENTITY_RECOVERY_DIR = os.path.join(BACKUP_DIR, "recovery")
LOG_DIR = str(mutable_path("logs"))
VERIFY_OFF_MESSAGE = (
    "Read-back verification skipped (Verify off). ECU-side finalization completed."
)


class StockWriteNotStarted(RuntimeError):
    """A stock DS2 write was stopped before any erase/program command."""


def configure_application(app):
    """Apply the shared application identity and dark Fusion theme."""
    app.setApplicationName("BimmerStein ECU Tool")
    if os.path.exists(APP_ICON_PATH):
        app.setWindowIcon(QIcon(APP_ICON_PATH))

    app.setStyle("Fusion")
    dark = QPalette()
    dark.setColor(QPalette.Window,          QColor("#2b2b2b"))
    dark.setColor(QPalette.WindowText,      QColor("#d4d4d4"))
    dark.setColor(QPalette.Base,            QColor("#1e1e1e"))
    dark.setColor(QPalette.AlternateBase,   QColor("#2a2a2a"))
    dark.setColor(QPalette.Text,            QColor("#d4d4d4"))
    dark.setColor(QPalette.Button,          QColor("#3a3a3a"))
    dark.setColor(QPalette.ButtonText,      QColor("#d4d4d4"))
    dark.setColor(QPalette.Highlight,       QColor("#2a6099"))
    dark.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    dark.setColor(QPalette.ToolTipBase,     QColor("#1e1e1e"))
    dark.setColor(QPalette.ToolTipText,     QColor("#d4d4d4"))
    dark.setColor(QPalette.Link,            QColor("#7ec8e3"))

    # Fusion derives disabled colors from the active palette. Set them
    # explicitly so disabled controls remain legible on the dark background.
    dark.setColor(QPalette.Disabled, QPalette.Window,     QColor("#2b2b2b"))
    dark.setColor(QPalette.Disabled, QPalette.Base,       QColor("#252525"))
    dark.setColor(QPalette.Disabled, QPalette.Button,     QColor("#333333"))
    dark.setColor(QPalette.Disabled, QPalette.Text,       QColor("#888888"))
    dark.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#888888"))
    dark.setColor(QPalette.Disabled, QPalette.WindowText, QColor("#888888"))
    app.setPalette(dark)


def install_exception_handler():
    """Log uncaught GUI-thread exceptions and show a concise release dialog."""
    def _handle(exception_type, exception, exception_traceback):
        if issubclass(exception_type, KeyboardInterrupt):
            sys.__excepthook__(exception_type, exception, exception_traceback)
            return

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(LOG_DIR, f"crash_{timestamp}.txt")
        saved = False
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            with open(path, "w", encoding="utf-8") as crash_log:
                crash_log.write("BimmerStein ECU Tool — Unexpected error\n")
                crash_log.write(
                    f"Time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                )
                traceback.print_exception(
                    exception_type,
                    exception,
                    exception_traceback,
                    file=crash_log,
                )
            saved = True
        except Exception:
            sys.__excepthook__(exception_type, exception, exception_traceback)

        detail = f"\n\nTechnical details were saved to:\n{path}" if saved else ""
        QMessageBox.critical(
            None,
            "Unexpected Application Error",
            "BimmerStein ECU Tool encountered an unexpected error. "
            "The active operation was stopped." + detail,
        )

    sys.excepthook = _handle

# Shared QGroupBox chrome used by every tab's section boxes, so titles/borders/fonts
# stay identical across tabs instead of drifting per hand-copied literal.
_SECTION_GB = (
    "QGroupBox { color:#aaa; font-weight:bold; border:1px solid #444; "
    "border-radius:4px; margin-top:6px; padding-top:6px; } "
    "QGroupBox::title { subcontrol-origin:margin; left:10px; padding:0 4px; }"
)

_ANALYZER_TABLE_STYLE = """
    QTableWidget { background:#1e1e1e; color:#d4d4d4;
                   gridline-color:#333; border:1px solid #444; }
    QTableWidget::item:alternate { background:#252525; }
    QTableWidget::item:selected { background:#2a6099; color:#fff; }
    QHeaderView::section { background:#2a2a2a; color:#aaa;
                           border:1px solid #444; padding:4px; font-weight:bold; }
    QTableWidget QTableCornerButton::section { background:#2a2a2a; border:1px solid #444; }
"""


def _create_analyzer_table() -> QTableWidget:
    table = QTableWidget(0, 4)
    table.setHorizontalHeaderLabels(["Category", "Parameter", "Value", "Unit / Info"])
    table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
    table.horizontalHeader().setDefaultSectionSize(110)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setAlternatingRowColors(True)
    table.setStyleSheet(_ANALYZER_TABLE_STYLE)
    table.setFont(QFont("Courier New", 9))
    table.verticalHeader().setVisible(False)
    return table


def _filter_analyzer_params(params, filter_text: str, scalars_only: bool):
    filter_text = filter_text.strip().lower()
    rows = []
    for category, name, value, unit in params:
        is_map = unit.startswith("0x") or "map" in unit.lower()
        if scalars_only and is_map:
            continue
        if filter_text and filter_text not in name.lower() and filter_text not in category.lower():
            continue
        rows.append((category, name, value, unit))
    return rows


def _populate_analyzer_table(table: QTableWidget, rows) -> None:
    sorting_enabled = table.isSortingEnabled()
    table.setSortingEnabled(False)
    table.setRowCount(0)
    for category, name, value, unit in rows:
        row = table.rowCount()
        table.insertRow(row)
        category_item = QTableWidgetItem(category)
        category_item.setForeground(QBrush(QColor("#888")))
        name_item = QTableWidgetItem(name)
        name_item.setForeground(QBrush(QColor("#d4d4d4")))
        value_item = QTableWidgetItem(value)
        value_item.setForeground(QBrush(QColor("#7ec8e3")))
        value_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        unit_item = QTableWidgetItem(unit)
        unit_item.setForeground(QBrush(QColor("#888")))
        table.setItem(row, 0, category_item)
        table.setItem(row, 1, name_item)
        table.setItem(row, 2, value_item)
        table.setItem(row, 3, unit_item)
    table.setSortingEnabled(sorting_enabled)


class AnalyzerParametersWindow(QDialog):
    """Modeless, synchronized view of the current ROM Analyzer parameters."""

    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ROM Analyzer Parameters")
        self.setModal(False)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.resize(1100, 680)
        self._params = []
        self._counts = (0, 0)

        layout = QVBoxLayout(self)
        self.context_label = QLabel("No ROM is loaded.")
        self.context_label.setWordWrap(True)
        self.context_label.setStyleSheet("color:#aaa; padding:2px;")
        layout.addWidget(self.context_label)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filter:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("type to filter by name or category...")
        self.filter_edit.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.filter_edit, 1)
        self.scalars_only = QCheckBox("Scalars only")
        self.scalars_only.stateChanged.connect(self._apply_filter)
        filter_row.addWidget(self.scalars_only)
        self.count_label = QLabel("")
        self.count_label.setStyleSheet("color:#888;")
        filter_row.addWidget(self.count_label)
        layout.addLayout(filter_row)

        self.table = _create_analyzer_table()
        self.table.setColumnWidth(0, 180)
        self.table.setColumnWidth(2, 120)
        self.table.setColumnWidth(3, 140)
        self.table.setSortingEnabled(True)
        layout.addWidget(self.table, 1)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        close_row.addWidget(close_button)
        layout.addLayout(close_row)

    def set_analysis(self, params, counts, source_name: str, definition_name: str) -> None:
        self._params = list(params)
        self._counts = counts
        self.context_label.setText(
            f"File: {source_name or 'Loaded data'}    |    "
            f"Definition: {definition_name or 'None selected'}"
        )
        self._apply_filter()

    def _apply_filter(self):
        rows = _filter_analyzer_params(
            self._params,
            self.filter_edit.text(),
            self.scalars_only.isChecked(),
        )
        _populate_analyzer_table(self.table, rows)
        scalar_count, map_count = self._counts
        self.count_label.setText(
            f"showing {len(rows)} of {len(self._params)}  "
            f"({scalar_count} scalars, {map_count} maps)"
        )

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Worker thread
# ---------------------------------------------------------------------------

class WorkerThread(QThread):
    log_signal      = pyqtSignal(str, str)    # (message, level)
    progress_signal = pyqtSignal(int, int, str)
    done_signal     = pyqtSignal(bool, object) # (success, result_or_exception)

    def __init__(self, task_fn, parent=None):
        super().__init__(parent)
        self.task_fn = task_fn

    def run(self):
        try:
            result = self.task_fn(
                log_fn      = lambda msg, lvl="info": self.log_signal.emit(msg, lvl),
                progress_fn = lambda d, t, l="": self.progress_signal.emit(d, t, l),
            )
            self.done_signal.emit(True, result)
        except Exception as e:
            # Preserve typed recovery exceptions across the worker boundary.
            # Ordinary failures are still rendered with str() by the GUI.
            self.done_signal.emit(False, e)


class _GuiPrompt(QObject):
    """A blocking prompt usable from a worker thread. The soft-BSL class calls
    prompt(msg) at each physical step (key-cycle / A17 flip); this shows a modal
    on the MAIN thread (via a queued signal) and blocks the worker until the
    operator clicks OK."""
    _ask = pyqtSignal(str)
    _retry = pyqtSignal(str)
    _phase1_reentry = pyqtSignal(str, str)

    def __init__(self, parent):
        super().__init__(parent)
        self._widget = parent
        self._evt = threading.Event()
        self._retry_answer = False
        self._ask.connect(self._show)          # queued → runs on the main thread
        self._retry.connect(self._show_retry)  # queued → runs on the main thread
        self._phase1_reentry.connect(self._show_phase1_reentry)

    def _show(self, msg):
        text = str(msg).strip()
        lowered = text.lower()
        if "a17" in lowered:
            title = "A17 Switch Required"
        elif "key-cycle" in lowered or "ignition" in lowered:
            title = "Ignition Cycle Required"
        else:
            title = "Soft-BSL Action Required"
        QMessageBox.information(self._widget, title, text)
        self._evt.set()

    def _show_retry(self, msg):
        answer = QMessageBox.warning(
            self._widget,
            "ECU Not Ready After Ignition Cycle",
            str(msg).strip(),
            QMessageBox.Retry | QMessageBox.Cancel,
            QMessageBox.Retry,
        )
        self._retry_answer = answer == QMessageBox.Retry
        self._evt.set()

    def _show_phase1_reentry(self, port, msg):
        """Release COM for an ignition cycle, then offer one explicit retry."""
        self._retry_answer = False
        try:
            self._widget._prepare_softbsl_phase1_reentry_prompt(str(port))
            answer = QMessageBox.warning(
                self._widget,
                "Soft-BSL Installation Needs an Ignition Cycle",
                str(msg).strip(),
                QMessageBox.Retry | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if answer == QMessageBox.Retry:
                self._retry_answer = bool(
                    self._widget._reacquire_softbsl_port_after_phase1_reentry(
                        str(port)
                    )
                )
        finally:
            self._evt.set()

    def __call__(self, msg=""):
        self._evt.clear()
        self._ask.emit(str(msg))
        self._evt.wait()                        # worker blocks until the modal is dismissed
        return ""

    def retry_cancel(self, msg=""):
        """Ask whether a failed physical-step verification should be retried."""
        self._retry_answer = False
        self._evt.clear()
        self._retry.emit(str(msg))
        self._evt.wait()
        return self._retry_answer

    def phase1_reentry_retry_cancel(self, port, msg=""):
        """Run the install-only marker recovery prompt on the Qt main thread."""
        self._retry_answer = False
        self._evt.clear()
        self._phase1_reentry.emit(str(port), str(msg))
        self._evt.wait()
        return self._retry_answer


class _GuiConfirm(QObject):
    """Blocking Yes/No confirmation callable from a worker thread."""
    _ask = pyqtSignal(str)

    def __init__(self, parent):
        super().__init__(parent)
        self._widget = parent
        self._evt = threading.Event()
        self._accepted = False
        self._ask.connect(self._show)

    def _show(self, msg):
        self._accepted = (
            QMessageBox.question(
                self._widget, "Reinstall Soft-BSL?", str(msg),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            == QMessageBox.Yes
        )
        self._evt.set()

    def __call__(self, msg=""):
        self._accepted = False
        self._evt.clear()
        self._ask.emit(str(msg))
        self._evt.wait()
        return self._accepted


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

STATUS_COLOURS = {
    "Active":             QColor("#f47171"),
    "Confirmed":          QColor("#e8c46a"),
    "Pending":            QColor("#6ab4e8"),
    "Failed since clear": QColor("#cc88ff"),
    "Not completed":      QColor("#888888"),
}

SYSTEM_COLOURS = {
    "Ignition":         "#ff9966",
    "Lambda / Fueling": "#66ccff",
    "Knock Control":    "#ffcc44",
    "VANOS":            "#99ff99",
    "Emissions":        "#cc99ff",
    "ECU Internal":     "#ff6666",
    "Immobiliser":      "#ff4444",
    "Power Supply":     "#ffaa44",
}


# ---------------------------------------------------------------------------
# ECU identify response parser
# ---------------------------------------------------------------------------

def _populate_ecu_info(ident: bytes, labels: dict) -> tuple:
    """Parse the DS2 identify response and fill the ECU ID / Detected Variant labels.

    ECU ID is the first 7 ASCII bytes of the identify() response, e.g. "1437806"
    or "SHINDE1". Variant is determined from the ECU ID (authoritative), with the
    ID prefix (ID60, ID12, etc.) derived from the variant. The remaining identify()
    bytes (HW/SW/coding fields) aren't independently confirmed field-for-field, so
    they're not decoded here — the exact bytes are available via the "Show Raw
    Identification Response" button instead.

    Returns (ecu_id, variant) — e.g. ('1437806', 'MS41.1').
    Both values are None if the relevant field couldn't be parsed.
    """
    # ECU ID → (variant label, display ID prefix)
    _ECU_VARIANT_MAP = {
        "1437806": ("MS41.1", "60"),
        "1438068": ("MS41.1", "60"),
        "1429861": ("MS41.0", "41"),
        "1432401": ("MS41.0", "41"),
        "1429373": ("MS41.0", "41"),
        "1438137": ("MS41.0", "41"),
        "1406464": ("MS41.2", "12"),
        "SHINDE1": ("MS41.3", "SS"),
    }

    ecu_id  = None
    variant = None
    try:
        def _ascii(b: bytes) -> str:
            return "".join(chr(x) if 32 <= x < 127 else "?" for x in b).strip()

        if len(ident) >= 7:
            ecu_id = _ascii(ident[0:7])
            labels["ECU ID"].setText(ecu_id)

        # Variant from ECU ID (authoritative — CAL ID prefix varies by firmware rev)
        if ecu_id and ecu_id in _ECU_VARIANT_MAP:
            variant, id_prefix = _ECU_VARIANT_MAP[ecu_id]
            labels["Detected Variant"].setText(f"{variant}  (ID{id_prefix})")

    except Exception:
        pass
    return ecu_id, variant


class _BootGateBlock:
    """Sentinel a write task returns when the boot/SA1 gate trips during the worker's
    pre-erase SA1 read. It is returned so on_success renders the gate as a clean
    cancellation rather than an operation failure (no erase happened)."""
    def __init__(self, ids, reason=None):
        self.ids = list(ids)
        self.reason = reason


# ---------------------------------------------------------------------------
# Main Window
# ---------------------------------------------------------------------------

class MS41FlashGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BimmerStein ECU Tool")
        if os.path.exists(APP_ICON_PATH):
            self.setWindowIcon(QIcon(APP_ICON_PATH))
        self.setMinimumSize(980, 700)
        self._ds2                 = None
        self._worker              = None
        self._dtcs                = []
        self._ecu_variant         = None
        self._ecu_program_variant = None   # confirmed from full ROM read; resolves MS41.2/MS41.3 ambiguity
        self._softbsl_last_is_ms41_3 = False   # backward-compatible sticky used by older UI tests
        self._softbsl_last_version = None      # consistent MS41.2/.3 target retained across port handoff
        self._ecu_id              = None
        self._ecu_cal_id          = None
        self._ecu_vin             = None
        self._session_backup_read = False
        self._last_full_read      = None   # bytes of the last full ROM read THIS connection;
                                            # reused for boot gates and identity grafting
        self._ecu_identity_source = None   # small live DS2 snapshot: serial/ISN + packed VIN
        self._task_busy           = False
        self._connection_echo     = True
        self._connection_port     = None   # plain-string snapshot; safe for worker-thread handoffs
        self._d2xx_checked        = False
        self._log_file            = None
        self._poller:  LiveDataPoller | None = None
        self._live_log_basename    = ""
        self._live_timer           = QTimer()
        self._live_timer.setInterval(200)
        self._live_timer.timeout.connect(self._refresh_live_display)
        self._backup_mgr           = BackupManager()
        self._port_owner           = PortOwner()   # single-owner mutex for the one serial port
        self._native_write_recovery = None  # retained D2XX/session after a post-erase failure
        self._softbsl_write_recovery = None # retained Soft-BSL RAM agent after a post-erase failure
        self._softbsl_install_recovery = None # retained Phase-1/2 installer transport
        # A completed stock write leaves volatile authorization active until a
        # real OFF -> 10 s -> ON cycle.  Gate a subsequent write on RAM state so
        # it cannot reuse that stale authorization and enter flash programming.
        self._post_write_cycle_pending = False
        self._identity_boot_data = None  # current 16 KB file-order BOOT identity/descriptor cache
        self._identity_sector_data = None # complete live erase sector: BOTTOM SA1 8 KB or TOP SA7 64 KB
        self._identity_sector_off = None  # file offset owning _identity_sector_data
        self._identity_cache_key = None  # connection fingerprint that owns the BOOT cache
        self._identity_cache_source = ""
        self._identity_cache_time = None
        self._identity_isn   = None   # fresh live 4-digit DME ISN (EWS workflow only)
        self._identity_isn_key = None # connection fingerprint that owns the live ISN
        self._last_full_read_key = None
        self._ecu_softbsl_marker = None
        self._ecu_softbsl_hook_present = False
        self._softbsl_image = None
        self._softbsl_xbank_base = None
        self._softbsl_xbank_base_source = ""
        self._softbsl_xbank_base_origin = None
        self._softbsl_xbank_identity_source = None
        self._softbsl_xbank_patch_ids = []
        self._last_ident_raw = b""   # raw identify() bytes, for the "Show Raw Response" button
        self._definition_registry = DefinitionRegistry()
        self._analyzer_loaded_data = None
        self._analyzer_loaded_path = ""
        self._analyzer_parameters_window = None
        self._build_ui()
        self._refresh_ports()

    # -------------------------------------------------------------------
    # UI Construction
    # -------------------------------------------------------------------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(6)
        root.setContentsMargins(10, 10, 10, 10)

        # ── Connection bar ──────────────────────────────────────────────
        conn_group = QGroupBox("ECU Connection  (BMW DS2 — 9600 8E2, K-Line or direct tap)")
        conn_lay = QVBoxLayout(conn_group)
        conn_controls = QHBoxLayout()

        conn_controls.addWidget(QLabel("Port:"))
        self.cb_port = QComboBox()
        self.cb_port.setMinimumWidth(140)
        self.cb_port.currentTextChanged.connect(self._on_port_selection_changed)
        conn_controls.addWidget(self.cb_port)

        btn_refresh = QPushButton("⟳")
        btn_refresh.setFixedWidth(30)
        btn_refresh.setToolTip("Refresh port list")
        btn_refresh.clicked.connect(self._refresh_ports)
        conn_controls.addWidget(btn_refresh)

        self.btn_connect = QPushButton("Connect")
        self.btn_connect.setCheckable(True)
        self.btn_connect.clicked.connect(self._on_connect_toggle)
        conn_controls.addWidget(self.btn_connect)

        self.chk_direct_tap = QCheckBox("Direct tap (no echo)")
        self.chk_direct_tap.setToolTip(
            "Full-duplex direct tap on the CPU's ASC0 pins (TxD0=P3.10, RxD0=P3.11) instead of the\n"
            "single-wire K-Line.  The K-Line echoes our TX back; a direct tap does not — so check\n"
            "this when wired straight onto the ECU (same tap as the BSL unbricker).  Leave unchecked\n"
            "for a normal K-Line / OBD-II adapter.  Set it before connecting.")
        conn_controls.addWidget(self.chk_direct_tap)

        self.lbl_intended_use = QLabel(
            "OFF-ROAD, COMPETITION, RESEARCH, AND BENCH USE ONLY"
        )
        self.lbl_intended_use.setAlignment(Qt.AlignCenter)
        self.lbl_intended_use.setStyleSheet(
            "color:#e8c46a; font-size:10px; font-weight:bold; padding:2px 4px;"
        )
        self.lbl_intended_use.setToolTip(
            "Do not use this software to modify a vehicle operated on public roads. "
            "The user is responsible for compliance with applicable emissions, safety, "
            "registration, and other laws."
        )
        conn_controls.addWidget(self.lbl_intended_use, 1)

        self.lbl_status = QLabel("● Disconnected")
        self.lbl_status.setStyleSheet("color:#999; font-weight:bold;")
        conn_controls.addWidget(self.lbl_status)
        self.lbl_variant = QLabel("")
        self.lbl_variant.setStyleSheet("color:#7ec8e3; font-weight:bold; padding-left:10px;")
        conn_controls.addWidget(self.lbl_variant)
        conn_lay.addLayout(conn_controls)
        root.addWidget(conn_group)

        # ── Log pane (shared) ───────────────────────────────────────────
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Courier New", 9))
        self.log_view.setStyleSheet(
            "background:#1a1a1a; color:#d4d4d4; border:1px solid #444;"
        )
        # FIXED (not just max) height: with only a max-height, Qt's layout would shrink this
        # box below its natural size to satisfy whichever tab's page currently needs more room
        # (tabs have differing minimum content height) — that's what made the log box (and
        # everything below it, including the tab bar) visibly jump between tabs.
        self.log_view.setFixedHeight(120)
        btn_clear_log = QPushButton("Clear Log")
        btn_clear_log.setFixedHeight(22)
        btn_clear_log.clicked.connect(self.log_view.clear)
        log_frame = QGroupBox("Log")
        log_vlay  = QVBoxLayout(log_frame)
        log_vlay.addWidget(self.log_view)
        log_vlay.addWidget(btn_clear_log)
        root.addWidget(log_frame)

        # ── Tab widget ──────────────────────────────────────────────────
        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(True)
        self.tabs.tabBar().setUsesScrollButtons(True)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.tabBar().setElideMode(Qt.ElideNone)
        self.tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #444;
                background: #2b2b2b;
            }
            QTabBar {
                font-size: 11px;
                font-weight: bold;
            }
            QTabBar::tab {
                padding: 8px 24px;
                min-width: 120px;
                min-height: 28px;
                max-height: 28px;
                color: #bbb;
                background: #2a2a2a;
                border: 1px solid #444;
                border-bottom: none;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background: #2a6099;
                color: white;
                border-color: #2a6099;
            }
            QTabBar::tab:hover:!selected {
                background: #3a3a3a;
                color: #ddd;
            }
        """)
        root.addWidget(self.tabs, 1)

        self._d2xx_ok = False   # resolved against the selected COM port after the UI is built
        # Tab order is workflow-grouped: core read/write → diagnostics → advanced read/write &
        # storage → editing → offline analysis → advanced/recovery last. (Display order = call order.)
        self._build_flash_tab()        # Flash
        self._build_info_tab()         # ECU Info
        self._build_dtc_tab()          # DTC Codes
        self._build_live_data_tab()    # Live Data
        self._build_partial_tab()      # Partial / Full
        self._build_backups_tab()      # Bins
        self._build_patches_tab()      # Patches
        self._build_config_tab()       # ECU Config
        self._build_identity_tab()     # Identity / EWS
        self._build_analyzer_tab()     # ROM Analyzer
        self._build_softbsl_tab()      # Soft-BSL
        self._build_bsl_tab()          # BSL-Unbricker

        # ── Progress bar (shared) ───────────────────────────────────────
        self.progress_bar   = QProgressBar()
        self.progress_label = QLabel("")
        self.progress_bar.setVisible(False)
        self.progress_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        root.addWidget(self.progress_bar)
        root.addWidget(self.progress_label)

        self._set_ecu_buttons_enabled(False)
        self._update_transfer_mode()   # sets the initial disabled-tooltip before any connect

    # ── Flash tab ───────────────────────────────────────────────────────

    def _flash_chip_label_text(self, chip_sig: bytes) -> str:
        """Flash-chip note text for the Flash tab. Generic (mentions all supported chips)
        until a live chip signature is known; specific once one is read at connect."""
        base = ("electrically erasable in-circuit. The ECU boot ROM handles sector erase "
                "before each write. A backup is recommended and remains optional.")
        if chip_sig:
            label = ecu_info.decode_flash_chip(chip_sig)
            if not label.startswith("Unknown"):
                return f"Flash chip: <b>{label}</b> — {base}"
        return f"Flash chip: <b>Intel 28F200 / AMD 29F200 / AMD 29F400</b> — {base}"

    def _build_flash_tab(self):
        tab = QWidget()
        lay = QVBoxLayout(tab)

        note = QLabel(self._flash_chip_label_text(b""))
        note.setWordWrap(True)
        note.setStyleSheet("color:#aaa; padding:4px;")
        lay.addWidget(note)
        self._flash_chip_note = note

        # Transfer-path indicator (auto: soft-BSL agent when installed + D2XX, else DS2). Sits at the
        # top of the tab next to the chip note — it's read-only status, not an option.
        self.lbl_transfer_mode = QLabel("Transfer: —")
        self.lbl_transfer_mode.setStyleSheet("color:#aaa; padding:0 4px 4px 4px;")
        lay.addWidget(self.lbl_transfer_mode)

        grid = QGridLayout()
        self.btn_read_full = self._op_btn(
            "📥  Read Full ROM  (256 KB)", "#1e5080",
            lambda: self._on_read("full")
        )
        self.btn_read_tune = self._op_btn(
            "📥  Read Tune Region  (24 KB)", "#1e5080",
            lambda: self._on_read("tune")
        )
        self.btn_write_full = self._op_btn(
            "📤  Write Full ROM  (256 KB)", "#7a1f1f",
            lambda: self._on_write("full")
        )
        self.btn_write_tune = self._op_btn(
            "📤  Write Tune Region  (24 KB)", "#7a1f1f",
            lambda: self._on_write("tune")
        )
        self.btn_check_file = self._op_btn(
            "🔍  Verify Checksums  (offline)", "#3d3d3d",
            self._on_check_file
        )
        self.btn_fix_file = self._op_btn(
            "🔧  Correct Checksums  (offline)", "#3d3d3d",
            self._on_fix_file
        )
        self.btn_fix_file.setToolTip(
            "Correct the boot, program, and calibration checksums enforced by the selected "
            "MS41 variant. On MS41.3, boot and calibration checksums are corrected while "
            "the stock-disabled program checksum remains unchanged. Only checksum bytes change."
        )
        self.btn_disable_cksum = self._op_btn(
            "🚫  Disable Program Checksum Verification  (offline)", "#3d3d3d",
            self._on_disable_cksum_file
        )
        self.btn_disable_cksum.setToolTip(
            "Set the full-ROM program-checksum switch at 0x605C to 0xFF. This disables "
            "program-checksum verification only; boot and calibration checks remain unchanged. "
            "Only this one byte changes."
        )
        grid.addWidget(self.btn_read_full,     0, 0)
        grid.addWidget(self.btn_read_tune,     0, 1)
        grid.addWidget(self.btn_write_full,    1, 0)
        grid.addWidget(self.btn_write_tune,    1, 1)
        grid.addWidget(self.btn_check_file,    0, 2)
        grid.addWidget(self.btn_fix_file,      1, 2)
        grid.addWidget(self.btn_disable_cksum, 2, 2)
        lay.addLayout(grid)

        # ── Checksum + verify row ─────────────────────────────────────────
        opt_row = QHBoxLayout()
        self.chk_correct_cksum = QCheckBox("Correct checksums before write")
        self.chk_correct_cksum.setChecked(True)
        self.chk_correct_cksum.setStyleSheet("color:#aaa; padding:4px;")
        self.chk_correct_cksum.setToolTip(
            "Recompute all MS41 checksums on the image before flashing so the ECU's "
            "start-up verification passes (recommended). No-op for an unmodified "
            "backup. For MS41.3, boot and calibration checksums are corrected while "
            "the disabled program checksum is left unchanged.")
        opt_row.addWidget(self.chk_correct_cksum)

        opt_row.addStretch()
        lay.addLayout(opt_row)

        # ── Boot-region write row (requires Soft-BSL) ──────────────────────
        boot_row = QHBoxLayout()
        self.chk_bootloader_write = QCheckBox(
            "Allow boot/parameter-region writes (Full ROM only; brick-class)")
        self.chk_bootloader_write.setStyleSheet("color:#e8c46a; padding:4px;")
        self.chk_bootloader_write.setEnabled(False)
        self.chk_bootloader_write.toggled.connect(self._update_boot_identity_checkbox_state)
        boot_row.addWidget(self.chk_bootloader_write)

        self.chk_boot_preserve_identity = QCheckBox(
            "Preserve ECU VIN / ISN when writing boot region")
        self.chk_boot_preserve_identity.setChecked(True)
        self.chk_boot_preserve_identity.setEnabled(False)
        self.chk_boot_preserve_identity.setStyleSheet("color:#aaa; padding:4px;")
        self.chk_boot_preserve_identity.setToolTip(
            "When a brick-class boot/parameter write is armed, graft the connected ECU's "
            "serial (including its four-digit EWS ISN) and VIN onto the selected image. "
            "DS2 and ordinary Soft-BSL writes preserve this region automatically.")
        boot_row.addWidget(self.chk_boot_preserve_identity)
        boot_row.addStretch()
        lay.addLayout(boot_row)

        # ── Verify + Calibration row ──────────────────────────────────────
        extra_row = QHBoxLayout()
        self.chk_backup_before_write = QCheckBox("Back up before write (single read)")
        self.chk_backup_before_write.setStyleSheet("color:#aaa; padding:4px;")
        self.chk_backup_before_write.setToolTip(
            "Optional. Read the selected tune or full ROM once and save it in Bins "
            "before writing. The flash does not require a backup.")
        extra_row.addWidget(self.chk_backup_before_write)
        self.chk_verify = QCheckBox("Verify flash after write  (reads back and compares byte-for-byte)")
        self.chk_verify.setStyleSheet("color:#aaa; padding:4px;")
        extra_row.addWidget(self.chk_verify)
        extra_row.addStretch()
        self.btn_reset_adapt = self._op_btn(
            "Reset Adaptations", "#4a3a00", self._on_reset_adaptations
        )
        self.btn_reset_adapt.setToolTip(
            "Clear learned fuel trim and idle speed adaptations from EEPROM.\n"
            "Recommended after any fueling change."
        )
        self.btn_reset_adapt.setMaximumWidth(200)
        extra_row.addWidget(self.btn_reset_adapt)
        self.btn_native_recovery = self._op_btn(
            "Retry Flash Recovery", "#9b1c1c", self._start_native_flash_recovery
        )
        self.btn_native_recovery.setToolTip(
            "Continue the retained native-fast or Soft-BSL session after a mid-flash failure. "
            "Keep ignition ON and do not disconnect the adapter.")
        self.btn_native_recovery.setVisible(False)
        self.btn_native_recovery.setEnabled(False)
        extra_row.addWidget(self.btn_native_recovery)
        lay.addLayout(extra_row)
        lay.addStretch()

        self.tabs.addTab(tab, "  Flash  ")

    # ── DTC tab ─────────────────────────────────────────────────────────

    def _build_dtc_tab(self):
        tab = QWidget()
        lay = QVBoxLayout(tab)

        # Button bar
        btn_bar = QHBoxLayout()
        self.btn_read_dtc = self._op_btn(
            "🔎  Read DTCs", "#1e5080", self._on_read_dtc
        )
        self.btn_read_dtc.setMaximumWidth(180)
        self.btn_clear_dtc = self._op_btn(
            "🗑  Clear DTCs", "#7a1f1f", self._on_clear_dtc
        )
        self.btn_clear_dtc.setMaximumWidth(180)
        self.btn_export_dtc = self._op_btn(
            "💾  Export to Text", "#3d3d3d", self._on_export_dtc
        )
        self.btn_export_dtc.setMaximumWidth(180)
        self.lbl_dtc_count = QLabel("No data")
        self.lbl_dtc_count.setStyleSheet("color:#aaa; padding:4px;")

        btn_bar.addWidget(self.btn_read_dtc)
        btn_bar.addWidget(self.btn_clear_dtc)
        btn_bar.addWidget(self.btn_export_dtc)
        btn_bar.addWidget(self.lbl_dtc_count)
        btn_bar.addStretch()
        lay.addLayout(btn_bar)

        # DTC table
        self.dtc_table = QTableWidget(0, 5)
        self.dtc_table.setHorizontalHeaderLabels(
            ["BMW Code", "SAE Code", "System", "Status", "Description"]
        )
        self.dtc_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.dtc_table.horizontalHeader().setDefaultSectionSize(110)
        self.dtc_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.dtc_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.dtc_table.setAlternatingRowColors(True)
        self.dtc_table.setStyleSheet("""
            QTableWidget {
                background: #1e1e1e;
                color: #d4d4d4;
                gridline-color: #333;
                border: 1px solid #444;
            }
            QTableWidget::item:alternate { background: #252525; }
            QHeaderView::section {
                background: #2a2a2a;
                color: #aaa;
                border: 1px solid #444;
                padding: 4px;
                font-weight: bold;
            }
            QTableWidget::item:selected { background: #2a6099; color: #fff; }
            QTableWidget QTableCornerButton::section { background: #2a2a2a; border: 1px solid #444; }
        """)
        self.dtc_table.setFont(QFont("Courier New", 9))
        vh = self.dtc_table.verticalHeader()
        vh.setVisible(True)
        p = vh.palette()
        p.setColor(p.Button,     __import__('PyQt5.QtGui', fromlist=['QColor']).QColor("#2a2a2a"))
        p.setColor(p.ButtonText, __import__('PyQt5.QtGui', fromlist=['QColor']).QColor("#666"))
        p.setColor(p.Window,     __import__('PyQt5.QtGui', fromlist=['QColor']).QColor("#2a2a2a"))
        p.setColor(p.WindowText, __import__('PyQt5.QtGui', fromlist=['QColor']).QColor("#666"))
        vh.setPalette(p)
        lay.addWidget(self.dtc_table, 1)

        # Detail panel
        detail_group = QGroupBox("Selected DTC — Detail")
        detail_group.setStyleSheet("""
            QGroupBox {
                color: #aaa;
                font-weight: bold;
                border: 1px solid #444;
                border-radius: 4px;
                margin-top: 6px;
                padding-top: 4px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
        """)
        detail_lay = QVBoxLayout(detail_group)
        detail_lay.setContentsMargins(6, 6, 6, 6)
        self.dtc_detail = QTextEdit()
        self.dtc_detail.setReadOnly(True)
        self.dtc_detail.setMaximumHeight(130)
        self.dtc_detail.setStyleSheet(
            "background:#1a1a1a; color:#d4d4d4; border:none; padding:4px;"
        )
        detail_lay.addWidget(self.dtc_detail)
        lay.addWidget(detail_group)

        self.dtc_table.itemSelectionChanged.connect(self._on_dtc_selected)
        self.tabs.addTab(tab, "  DTC Codes  ")

    # ── ECU Info tab ────────────────────────────────────────────────────

    def _build_info_tab(self):
        tab = QWidget()
        lay = QVBoxLayout(tab)

        self.btn_info = self._op_btn(
            "📋  Read ECU Firmware Info", "#1e5080", self._on_read_info
        )
        self.btn_info.setMaximumWidth(240)
        lay.addWidget(self.btn_info)

        self.info_grid = QGridLayout()
        self.info_grid.setColumnStretch(1, 1)
        self._info_labels = {}
        fields = [
            "ECU ID", "CAL ID", "Detected Variant", "Firmware Version",
            "VIN", "ISN", "Flash Chip", "Transmission",
        ]
        for row, f in enumerate(fields):
            lbl_key = QLabel(f"{f}:")
            lbl_key.setStyleSheet("font-weight:bold; color:#aaa; min-width:160px;")
            lbl_val = QLabel("—")
            lbl_val.setWordWrap(True)
            lbl_val.setTextFormat(Qt.RichText)
            lbl_val.setStyleSheet("color:#e0e0e0;")
            self.info_grid.addWidget(lbl_key, row, 0, Qt.AlignTop)
            self.info_grid.addWidget(lbl_val, row, 1, Qt.AlignTop)
            self._info_labels[f] = lbl_val

        info_inner = QWidget()
        info_inner.setLayout(self.info_grid)
        lay.addWidget(info_inner)

        self.btn_show_raw_ident = QPushButton("Show Raw Identification Response")
        self.btn_show_raw_ident.setMaximumWidth(260)
        self.btn_show_raw_ident.clicked.connect(self._on_show_raw_ident)
        lay.addWidget(self.btn_show_raw_ident)

        self.raw_ident_view = QTextEdit()
        self.raw_ident_view.setReadOnly(True)
        self.raw_ident_view.setFont(QFont("Courier New", 9))
        self.raw_ident_view.setMaximumHeight(60)
        self.raw_ident_view.setPlaceholderText(
            "Exact bytes as received from the DS2 identification command (0x00) — "
            "shown as-is, with no assumed field meaning."
        )
        self.raw_ident_view.hide()
        lay.addWidget(self.raw_ident_view)

        lay.addStretch()
        self.tabs.addTab(tab, "  ECU Info  ")

    def _on_show_raw_ident(self):
        self.raw_ident_view.setPlainText(self._last_ident_raw.hex(" ").upper())
        self.raw_ident_view.show()

    # -------------------------------------------------------------------
    # Connection
    # -------------------------------------------------------------------

    @staticmethod
    def _populate_port_combo(combo, ports):
        """Refresh one independent COM selector while preserving its selection."""
        previous = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        for port in ports:
            combo.addItem(port)
        if not ports:
            combo.addItem("(no ports found)")
        elif previous in ports:
            combo.setCurrentText(previous)
        combo.blockSignals(False)
        return previous, combo.currentText()

    def _refresh_ports(self):
        ports = DS2Interface.list_ports()
        previous, current = self._populate_port_combo(self.cb_port, ports)
        if current != previous:
            self._on_port_selection_changed(self.cb_port.currentText())
        if hasattr(self, "cb_bsl_port"):
            previous_bsl, current_bsl = self._populate_port_combo(self.cb_bsl_port, ports)
            if current_bsl != previous_bsl:
                self._invalidate_bsl_plan()

    def _on_port_selection_changed(self, port):
        if self._ds2 is None:
            self._d2xx_checked = False
            self._d2xx_ok = False
            self._update_d2xx_warning()
            if hasattr(self, "lbl_transfer_mode"):
                self._update_transfer_mode()

    def _update_d2xx_warning(self):
        if hasattr(self, "_d2xx_warn"):
            if not self._d2xx_checked:
                self._d2xx_warn.setText(
                    "D2XX fast-transfer availability will be confirmed when this adapter connects.")
                self._d2xx_warn.setStyleSheet("color:#888;")
                self._d2xx_warn.setVisible(True)
            elif not self._d2xx_ok:
                self._d2xx_warn.setText(
                    "⚠ D2XX could not open this FTDI adapter. Native fast DS2 is unavailable; "
                    "stock-ECU transfers will use the reliable 9600-baud path.")
                self._d2xx_warn.setStyleSheet("color:#e8c46a;")
                self._d2xx_warn.setVisible(True)
            else:
                self._d2xx_warn.setVisible(False)

    def _on_connect_toggle(self, checked):
        if checked:
            self._connect()
        else:
            self._disconnect()

    def _connect(self):
        port = self.cb_port.currentText()
        if not port or port.startswith("("):
            QMessageBox.warning(self, "No Serial Port", "Select a valid serial port.")
            self.btn_connect.setChecked(False)
            return
        try:
            self._port_owner.acquire("flasher")
        except PortBusyError as e:
            QMessageBox.warning(self, "Port Busy",
                                f"The serial port is held by '{e.holder}'. "
                                f"Finish that operation before connecting.")
            self.btn_connect.setChecked(False)
            return
        self._log(f"Opening {port} for BMW DS2 (9600 8E2, no init)…")
        direct_tap = self.chk_direct_tap.isChecked()
        self._connection_echo = not direct_tap
        self._connection_port = port
        if direct_tap:
            self._log("Direct-tap mode: full-duplex, no K-Line echo expected.")

        def task(log_fn, progress_fn):
            self._ds2 = DS2Interface(port=port, baud=9600, verbose=False, echo=not direct_tap)
            self._ds2.open()
            transport = getattr(self._ds2, "transport_name", None) or "serial"
            log_fn(f"Port {port} open via {transport} (DS2 9600 8E2)")
            log_fn("Identifying ECU (DS2 0x00)…")
            ident = self._ds2.identify()
            cal_id = ""
            try:
                raw = self._ds2.read_mem(0x1000E, 2)  # cal_base+0x0E per RomRaider defs
                cal_id = "".join(chr(b) if 32 <= b < 127 else "?" for b in raw).strip()
            except Exception:
                pass
            vin = ""
            try:
                vin = self._ds2.read_vin()
            except Exception:
                pass
            # Detect MS41.3 from the PROGRAM-region SS1v2 signature (survives a tune/cal
            # reflash), NOT the cal-resident ABHISHEK marker a custom tune wipes.
            prog_is_ms41_3 = self._program_is_ms41_3(self._ds2)
            # Soft-BSL bank marker + flash chip signature — read-only, no agent, safe on a
            # normally-running ECU. Used to decide whether the Fast checkbox can be offered
            # and to render an accurate chip label.
            softbsl_marker_raw = b""
            try:
                softbsl_marker_raw = self._ds2.read_mem(ecu_info.BANK_MARKER_ADDR, ecu_info.BANK_MARKER_LEN)
            except Exception:
                pass
            softbsl_hook_present = False
            softbsl_hook_check_failed = False
            if ecu_info.decode_bank_marker(softbsl_marker_raw):
                try:
                    softbsl_hook_present = self._live_patch_present(
                        self._ds2, "door_magic")
                except Exception as error:
                    softbsl_hook_check_failed = True
                    log_fn(
                        "Soft-BSL loader marker is present, but the normal-mode 0x2A "
                        f"hook could not be confirmed ({error}). Automatic transfers "
                        "will use DS2.", "warn")
                if not softbsl_hook_present and not softbsl_hook_check_failed:
                    log_fn(
                        "Soft-BSL loader marker is present without the complete 0x2A "
                        "program hook. Automatic transfers will use DS2.", "warn")
            chip_sig_for_fast = b""
            try:
                chip_sig_for_fast = self._ds2.read_mem(ecu_info.DRV_SIG_ADDR, ecu_info.DRV_SIG_LEN)
            except Exception:
                pass
            new_fields = self._read_new_info_fields(self._ds2, log_fn)
            identity_source = self._read_live_identity_source(self._ds2, log_fn)
            return (ident, cal_id, vin, prog_is_ms41_3, new_fields,
                    softbsl_marker_raw, chip_sig_for_fast, identity_source,
                    softbsl_hook_present)

        self._run_task(
            task,
            on_success=lambda r: self._on_connected(*r),
            on_failure=lambda e: self._on_connect_failed(e),
        )

    def _disconnect(self):
        if getattr(self, "_ds2", None):
            try: self._ds2.close()
            except Exception: pass
            self._ds2 = None
        self._port_owner.release("flasher")
        self._connection_port = None
        # Remember the MS41.3 verdict BEFORE clearing it — a soft-BSL op (install / fast R-W) frees
        # the DS2 port by disconnecting first, and must not then mis-read this ECU as non-MS41.3.
        self._softbsl_last_version = self._ecu_patch_version()
        self._softbsl_last_is_ms41_3 = self._ecu_is_ms41_3()
        self._ecu_variant         = None
        self._ecu_program_variant = None
        self._ecu_id              = None
        self._ecu_cal_id          = None
        self._ecu_vin             = None
        self._ecu_softbsl_marker  = None
        self._ecu_softbsl_hook_present = False
        self._ecu_chip_sig        = b""
        self._flash_chip_note.setText(self._flash_chip_label_text(b""))
        self._update_transfer_mode()
        self._session_backup_read = False
        self._last_full_read      = None   # clear cached boot evidence / identity source
        self._last_full_read_key  = None
        if hasattr(self, "_config_live_values_read"):
            self._reset_live_config_state()
        self._ecu_identity_source = None
        self._identity_boot_data = None
        self._identity_sector_data = None
        self._identity_sector_off = None
        self._identity_cache_key = None
        self._identity_cache_source = ""
        self._identity_cache_time = None
        self._identity_isn = None
        self._identity_isn_key = None
        if hasattr(self, "id_vin_current"):
            self._clear_identity_tab_state()
        self._on_live_stop()
        self._end_session_log()
        self.lbl_status.setText("● Disconnected")
        self.lbl_status.setStyleSheet("color:#999; font-weight:bold;")
        self.lbl_variant.setText("")
        self.btn_connect.setText("Connect")
        self.btn_connect.setChecked(False)
        self._set_ecu_buttons_enabled(False)
        self._update_softbsl_install_options()
        self._log("Disconnected", "warn")

    @staticmethod
    def _program_is_ms41_3(ds2) -> bool:
        """True if the connected ECU's PROGRAM half carries the exact MS41.3 SS1v2 signature
        (file 0x39A9A, read at DS2 0x3DA9A). It lives in the program region, so it survives a
        tune/cal reflash — unlike the cal-resident ABHISHEK marker (file 0x11F60, inside the
        24 KB tune), which a custom tune wipes, making a tuned MS41.3 ECU look like MS41.2.
        This is the same signature the full-ROM resolver uses. Read-only 4 bytes, safe on a
        running ECU; any read failure returns False (fail-safe)."""
        try:
            sig = ds2.read_mem(SS1V2_PROG_SIG_ADDR ^ 0x4000, len(SS1V2_PROG_SIG))
        except Exception:
            return False
        return bytes(sig) == SS1V2_PROG_SIG

    @staticmethod
    def _live_patch_present(ds2, patch_id: str) -> bool:
        """Confirm every descriptor edit directly from the normally-running ECU.

        Patch descriptors use full-file offsets. Normal DS2 program reads use the
        ECU's block-swapped address space, so each edit is read at ``off ^ 0x4000``.
        A short or failed read is an error; callers fail safe to the stock DS2 route.
        """
        patch = patch_service.definitions().get(patch_id)
        if patch is None:
            raise ValueError(f"unknown patch descriptor: {patch_id}")
        for edit in patch["edits"]:
            expected = bytes.fromhex(edit["data"])
            address = int(edit["off"]) ^ 0x4000
            actual = bytes(ds2.read_mem(address, len(expected)))
            if len(actual) != len(expected):
                raise ValueError(
                    f"short {patch_id} read at 0x{address:05X}: "
                    f"expected {len(expected)}, received {len(actual)}")
            if actual != expected:
                return False
        return True

    @staticmethod
    def _read_live_identity_source(ds2, log_fn):
        """Read only the live per-unit identity bytes needed for a base-image graft.

        These two small stock-DS2 reads replace the old requirement for a complete
        256 KB ROM read. File 0x5CE0/0x5D07 map to DS2 0x1CE0/0x1D07.
        """
        try:
            serial_block = ds2.read_mem(0x1CE0, 15)  # marker + gap + 9-digit serial + NUL
            packed_vin = ds2.read_mem(0x1D07, identity.VIN_LEN)
            if len(serial_block) != 15 or len(packed_vin) != identity.VIN_LEN:
                raise ValueError("short identity read")
            source = bytearray(b"\xFF" * 0x6100)
            source[identity.MARK_1585_OFF:identity.MARK_1585_OFF + 15] = serial_block
            source[identity.VIN_OFF:identity.VIN_OFF + identity.VIN_LEN] = packed_vin
            info = identity.decode_identity(bytes(source))
            if not info.serial or not info.isn4:
                raise ValueError("live serial/ISN is invalid")
            return bytes(source)
        except Exception as error:
            log_fn(f"Live VIN/ISN preservation snapshot failed: {error}", "warn")
            return None

    def _on_connected(self, ident=b"", cal_id="", vin="", prog_is_ms41_3=False, new_fields=None,
                     softbsl_marker_raw=b"", chip_sig=b"", identity_source=None,
                     softbsl_hook_present=False):
        self._reset_live_config_state()
        self._d2xx_checked = True
        self._d2xx_ok = bool(self._ds2 and getattr(self._ds2, "uses_d2xx", False))
        self._update_d2xx_warning()
        self._ecu_identity_source = bytes(identity_source) if identity_source else None
        self._ecu_softbsl_marker = ecu_info.decode_bank_marker(softbsl_marker_raw)
        self._ecu_softbsl_hook_present = bool(softbsl_hook_present)
        self._ecu_chip_sig = chip_sig
        self._flash_chip_note.setText(self._flash_chip_label_text(chip_sig))
        self._update_transfer_mode()
        self._start_session_log()
        self.lbl_status.setText("● Connected (DS2)")
        self.lbl_status.setStyleSheet("color:#5f5; font-weight:bold;")
        self.btn_connect.setText("Disconnect")
        self._set_ds2_buttons_enabled()
        self._log("Connected via DS2 — identify OK.", "ok")
        try:
            parsed_id, parsed_variant = _populate_ecu_info(ident, self._info_labels)
            if parsed_id:
                self._ecu_id = parsed_id
                self._log(f"ECU ID: {parsed_id}", "ok")
            if cal_id:
                self._ecu_cal_id = cal_id
                self._info_labels["CAL ID"].setText(cal_id)
            # MS41.3 program marker — resolves the MS41.2 / MS41.3 ambiguity that
            # ECU ID alone can't handle (some MS41.3 ECUs still report "1406464").
            if prog_is_ms41_3:
                self._ecu_program_variant = "MS41.3"
                parsed_variant = "MS41.3"
                self._log("MS41.3 program firmware detected (SS1v2 program signature).", "ok")
            if parsed_variant:
                self._ecu_variant = parsed_variant
                self._log(f"Variant: {parsed_variant}  |  CAL ID: {cal_id or '?'}", "ok")
        except Exception:
            pass
        if vin:
            self._ecu_vin = vin
            self._info_labels["VIN"].setText(vin)
            self._log(f"VIN: {vin}", "ok")
        else:
            self._info_labels["VIN"].setText("Not programmed in ECU")
        self._last_ident_raw = ident
        for key, val in (new_fields or {}).items():
            self._info_labels[key].setText(val)
        self._update_softbsl_install_options()
        self._update_config_buttons()

    def _set_ds2_buttons_enabled(self):
        """DS2 mode: reads + partial tune write enabled; full write held off."""
        for b in (self.btn_read_dtc, self.btn_clear_dtc, self.btn_export_dtc,
                  self.btn_info, self.btn_read_tune, self.btn_read_full,
                  self.btn_id_read_ecu, self.btn_softbsl_install):
            b.setEnabled(True)
        self.btn_id_read_flash_ecu.setEnabled(self._fast_read_available())
        self.btn_id_read_flash_ecu.setToolTip(
            "Requires the installed Soft-BSL loader and its normal-mode 0x2A hook. "
            "Reads the 16 KB BOOT identity window "
            "on BOTTOM, or the complete 64 KB fused SA7 sector on 29F400 TOP, with automatic "
            "high-to-low baud fallback. DS2 is not used for BOOT access.")
        # Config-tab buttons follow the file/ECU mode rules, not a blanket enable.
        self._update_config_buttons()
        self.btn_read_tune.setToolTip(
            "Read the 24 KB calibration/tune partition. Uses Soft-BSL when installed; "
            "otherwise native DS2 enters 187,500 directly through D2XX and falls back "
            "to normal DS2 only after confirmed low recovery. Saves automatically to Bins, "
            "then offers an additional copy elsewhere.")
        self.btn_read_full.setToolTip(
            "Read the complete mapped ROM once into a 256 KB image. Uses "
            "Soft-BSL when installed; otherwise native DS2 enters 187,500 directly "
            "through D2XX with confirmed-low fallback. Saves automatically to Bins, "
            "then offers an additional copy elsewhere.")
        self.btn_write_tune.setEnabled(True)
        self.btn_write_tune.setToolTip(
            "Write the 24 KB calibration/tune partition using the active transfer path.\n"
            "The app automatically selects Soft-BSL, native-fast DS2, or normal DS2 and "
            "verifies when enabled.\nA backup is recommended and optional. Keep ignition ON "
            "and engine OFF throughout.")
        self.btn_write_full.setEnabled(True)
        self.btn_write_full.setToolTip(
            "Write the full 256 KB ROM using the active transfer path.\n"
            "The boot/parameter region is preserved unless its brick-class option is armed.\n"
            "A backup is recommended and optional. Keep ignition ON and engine OFF throughout.")
        self.btn_reset_adapt.setEnabled(True)
        self.btn_reset_adapt.setToolTip(
            "Clear ECU learned adaptations over DS2 (command 0x43).\n"
            "Choose which adaptation to clear in the next dialog.\n"
            "The ECU will re-learn on the next drive cycle."
        )
        # Connected and idle when this runs → backup ops are available.
        self._set_backup_buttons_enabled(True)
        # Enable live data via DS2 RAM reads or the registered-address batch command.
        self._set_live_buttons_enabled(True)
        self._update_telegram_checkbox_state()
        self._update_identity_write_state()
        if hasattr(self, "btn_ews_send"):
            self.btn_ews_send.setEnabled(bool(
                self._identity_isn
                and self._identity_isn_key == self._identity_connection_key()
                and not self._task_busy))

    def _fast_read_available(self):
        """Whether the complete normal-mode Soft-BSL entry path is usable.

        The boot marker alone is insufficient: command 0x2A can reach the loader
        only when the exact ``door_magic`` program hook is also present. High baud
        additionally requires the adapter to have opened through D2XX.
        """
        return bool(
            getattr(self, "_ecu_softbsl_marker", None)
            and getattr(self, "_ecu_softbsl_hook_present", False)
            and getattr(self, "_d2xx_ok", False)
        )

    def _native_fast_ds2_available(self):
        """The stock-ECU native fast path requires the selected adapter to be D2XX."""
        return bool(getattr(self, "_d2xx_ok", False))

    def _auto_transfer_route(self):
        """Return the preferred route without conflating Soft-BSL and native DS2."""
        if self._fast_read_available():
            return "softbsl"
        if self._native_fast_ds2_available():
            return "native_ds2"
        return "legacy_ds2"

    def _update_transfer_mode(self):
        """Describe the automatic Soft-BSL/native-fast/legacy transfer route."""
        marker = getattr(self, "_ecu_softbsl_marker", None)
        hook_present = getattr(self, "_ecu_softbsl_hook_present", False)
        if self._fast_read_available():
            self.lbl_transfer_mode.setText("Transfer: Soft-BSL RAM agent, high baud (fast, auto)")
            self.lbl_transfer_mode.setStyleSheet("color:#9ece6a; padding:4px;")
            self.lbl_transfer_mode.setToolTip(
                "This ECU has the Soft-BSL loader and complete normal-mode 0x2A hook, "
                "and this adapter opened through D2XX, so reads/writes use the RAM "
                "agent at high baud. Falls back to a lower baud automatically if the "
                "link is noisy.")
        elif marker and not hook_present and self._native_fast_ds2_available():
            self.lbl_transfer_mode.setText(
                "Transfer: Native DS2 187,500 — Soft-BSL hook not detected")
            self.lbl_transfer_mode.setStyleSheet("color:#e8c46a; padding:4px;")
            self.lbl_transfer_mode.setToolTip(
                "The Soft-BSL loader marker exists, but the complete normal-mode 0x2A "
                "program hook was not confirmed. Soft-BSL entry is unavailable, so "
                "reads and writes use native-fast DS2 with normal 9600 fallback.")
        elif self._native_fast_ds2_available():
            self.lbl_transfer_mode.setText(
                "Transfer: Native DS2 187,500 (fast, direct; 9600 fallback)")
            self.lbl_transfer_mode.setStyleSheet("color:#9ece6a; padding:4px;")
            self.lbl_transfer_mode.setToolTip(
                "The stock ECU is entered directly from normal DS2 using selector 0x01. "
                "The host requests the ECU-exact 187,500-baud tier and validates "
                "communication before transfer. No 19,200 tier is used.")
        elif marker and not hook_present:
            self.lbl_transfer_mode.setText(
                "Transfer: DS2 9600 — Soft-BSL hook not detected")
            self.lbl_transfer_mode.setStyleSheet("color:#e8c46a; padding:4px;")
            self.lbl_transfer_mode.setToolTip(
                "The Soft-BSL loader marker exists, but the complete normal-mode 0x2A "
                "program hook was not confirmed. Soft-BSL entry is unavailable, and "
                "D2XX native-fast DS2 is unavailable, so transfers use DS2 at 9600 baud.")
        elif marker:
            self.lbl_transfer_mode.setText("Transfer: DS2 9600 (slow) — D2XX unavailable")
            self.lbl_transfer_mode.setStyleSheet("color:#e8c46a; padding:4px;")
            self.lbl_transfer_mode.setToolTip(
                "The Soft-BSL loader is present, but D2XX could not open the selected adapter. "
                "High baud requires D2XX, so this connection uses DS2 at 9600 baud.")
        else:
            self.lbl_transfer_mode.setText("Transfer: DS2 9600 (slow) — D2XX unavailable")
            self.lbl_transfer_mode.setStyleSheet("color:#aaa; padding:4px;")
            self.lbl_transfer_mode.setToolTip(
                "The selected adapter did not open through D2XX, so native fast DS2 is unavailable.")
        if hasattr(self, "btn_id_read_flash_ecu"):
            self.btn_id_read_flash_ecu.setEnabled(
                self._ds2 is not None and self._fast_read_available() and not self._task_busy)
        self._update_bootloader_checkbox_state()

    def _update_bootloader_checkbox_state(self):
        """The boot/parameter region is protected against the resident DS2 driver
        (ds2.py:979) — there is no plain-DS2 fallback, so this checkbox only makes sense
        when the complete Soft-BSL path is available (loader + 0x2A hook + D2XX)."""
        if self._fast_read_available():
            self.chk_bootloader_write.setEnabled(True)
            if getattr(self, "_ecu_softbsl_marker", None) == "T":
                tip = ("Writes the complete fused TOP SA7 sector (file 0x0000-0xFFFF) through "
                       "the RAM agent. If interrupted, select BOTTOM and recover over Soft-BSL.")
            else:
                tip = ("Writes BOTTOM file 0x4000-0x5FFF through the Soft-BSL agent. If interrupted, "
                       "recover over Soft-BSL when possible; hardware BSL remains the backstop.")
            self.chk_bootloader_write.setToolTip(tip)
        else:
            self.chk_bootloader_write.setEnabled(False)
            self.chk_bootloader_write.setChecked(False)
            self.chk_bootloader_write.setToolTip(
                "Requires Soft-BSL (loader + normal-mode 0x2A hook + D2XX); "
                "DS2 cannot write this region.")
        self._update_boot_identity_checkbox_state()

    def _update_boot_identity_checkbox_state(self):
        """Enable identity grafting only for an armed boot/parameter write.

        The option remains checked while disabled so boot writes always return to the safe
        default. DS2 and ordinary Soft-BSL writes preserve these ECU bytes in place.
        """
        checkbox = getattr(self, "chk_boot_preserve_identity", None)
        boot = getattr(self, "chk_bootloader_write", None)
        if checkbox is not None:
            checkbox.setEnabled(bool(boot and boot.isEnabled() and boot.isChecked()))

    def _identity_graft_source(self):
        """Return the best current ECU identity snapshot and its decoded fields.

        A cached full read is preferred, but connection setup already captures the only bytes
        graft_identity() needs, so a slow 256 KB read is not required for a conversion.
        """
        cached = getattr(self, "_last_full_read", None)
        source = (bytes(cached)
                  if cached is not None and len(cached) == identity.FULL_ROM_SIZE
                  else getattr(self, "_ecu_identity_source", None))
        if not source:
            return None, None
        source = bytes(source)
        info = identity.decode_identity(source)
        if not info.serial or not info.isn4:
            return None, info
        return source, info

    @staticmethod
    def _conversion_warning_policy(ecu_variant, file_variant, write_boot):
        """Return (dialog title, risk text, involves_ms410) for a full-ROM conversion.

        MS41.1/.2/.3 are mutually supported. A conversion in either direction involving
        MS41.0 is allowed, but remains untested: preserving the source boot window carries an
        explicit compatibility/brick warning, while replacing it carries the normal brick-class
        boot-write warning plus an untested-conversion warning.
        """
        involves_ms410 = "MS41.0" in (ecu_variant, file_variant)
        if involves_ms410 and write_boot:
            return (
                "Untested MS41.0 Conversion — Boot Write",
                "MS41.0 and the other MS41 variants use compatible hardware, and this "
                "operation includes the target ROM's boot/parameter region. However, this exact "
                "conversion has not yet been validated on an ECU. The boot write is brick-class "
                "and a failure may require hardware BSL recovery.",
                True,
            )
        if involves_ms410:
            return (
                "Untested MS41.0 Conversion — Brick Risk",
                "This conversion has not yet been validated while preserving the connected "
                "ECU's existing boot/parameter region. That boot code may be incompatible with "
                "the target program. The ECU may not boot and may require hardware BSL recovery.",
                True,
            )
        return (
            "Confirm Variant Conversion",
            "MS41.1/MS41.2/MS41.3 full-ROM conversion is supported on the selected "
            "transfer path.",
            False,
        )

    def _bootloader_write_file_warning(self, data: bytes):
        """None if the file being flashed carries both persistent Soft-BSL entry components;
        otherwise a warning string (informational — the caller still allows proceeding)."""
        missing_ids = self._softbsl_missing_after_full_write(
            data, write_bootloader=True)
        labels = {
            "softbsl_loader": "the Soft-BSL loader",
            "door_magic": "the 0x2A dispatcher door",
        }
        missing = [labels[patch_id] for patch_id in missing_ids]
        if not missing:
            return None
        return (f"This file does not contain {' and '.join(missing)}. After a successful write, "
                "Soft-BSL entry may no longer be available; an interrupted boot-region write "
                "may require hardware BSL recovery.")

    @staticmethod
    def _softbsl_missing_after_full_write(data: bytes, *, write_bootloader: bool):
        """Persistent Soft-BSL components absent from the effective post-write image.

        Every full write replaces program-high, so the normal-mode ``door_magic``
        entry must exist in the selected image. A simple full write preserves SA1
        and therefore preserves the connected ECU's already-working loader. When
        BOOT/SA1 is explicitly written, the selected image must carry the current
        supported loader as well.
        """
        patches = patch_service.definitions()
        missing = []
        if write_bootloader:
            loader = patches.get("softbsl_loader")
            if loader is None or not patch_service.is_applied(data, loader):
                missing.append("softbsl_loader")
        hook = patches.get("door_magic")
        if hook is None or not patch_service.is_applied(data, hook):
            missing.append("door_magic")
        return tuple(missing)

    def _boot_region_flash_block(self, image, ecu_evidence):
        """None if flashing `image` over a path that won't write the boot/SA1 region is safe;
        otherwise an explanatory string. `ecu_evidence` is the ECU's SA1 window (file
        0x4000-0x5FFF), a full ROM read (auto-sliced), or None. DS2 (and un-armed soft-BSL)
        leave file 0x4000-0x5FFF intact, so an SA1 patch the ECU doesn't already have would be
        silently dropped — a partially-applied patch."""
        missing = patch_service.missing_boot_patches(bytes(image), ecu_evidence)
        if not missing:
            return None
        return self._boot_block_message(missing, no_evidence=ecu_evidence is None)

    @staticmethod
    def _boot_block_message(missing, no_evidence, reason=None):
        names = ", ".join(missing)
        msg = (f"This image includes a patch that writes the ECU's boot/parameter region "
               f"(file 0x4000–0x5FFF):\n\n  {names}\n\n"
               "That region is not written by DS2, nor by Soft-BSL unless boot-region "
               "writes are armed — so flashing here would leave those bytes off the ECU and the "
               "patch only partially applied.\n\n"
               "To apply it, enable boot/parameter-region writes on the Flash tab, or use the "
               "hardware BSL recovery tab.")
        if reason:
            msg += f"\n\n(The ECU's boot region {reason}, so its contents could not be confirmed.)"
        elif no_evidence:
            msg += ("\n\n(The ECU's boot region could not be read to confirm it already has these "
                    "bytes.)")
        return msg


    def _on_connect_failed(self, msg):
        # Clean up any partial state (port may have been opened before the error)
        if getattr(self, "_ds2", None):
            try: self._ds2.close()
            except Exception: pass
            self._ds2 = None
        self._port_owner.release("flasher")
        self._connection_port = None
        self.btn_connect.setChecked(False)
        self.btn_connect.setText("Connect")
        self._log(f"Connection failed: {msg}", "error")
        QMessageBox.critical(self, "Connection Failed", str(msg))

    # -------------------------------------------------------------------
    # ECU Info
    # -------------------------------------------------------------------

    def _read_new_info_fields(self, ds2, log_fn) -> dict:
        """Read the 4 new/fixed ECU Info fields over DS2 (read-only, safe on
        a normally-running ECU) and decode them via ecu_info.py. Each read is
        independently wrapped so one failure (e.g. an older MS41 variant
        missing a field) never blocks the others."""
        isn4_live = ""
        try:
            isn4_live = ds2.read_isn()
        except Exception as e:
            log_fn(f"Live 4-digit ISN read failed: {e}", "warn")

        fw_raw = b""
        try:
            fw_raw = ds2.read_mem(ecu_info.FW_VERSION_ADDR, ecu_info.FW_VERSION_LEN)
        except Exception as e:
            log_fn(f"Firmware version read failed: {e}", "warn")

        isn_block = b""
        try:
            isn_block = ds2.read_mem(ecu_info.ISN_BLOCK_ADDR, ecu_info.ISN_BLOCK_LEN)
        except Exception as e:
            log_fn(f"ISN block read failed: {e}", "warn")

        chip_sig = b""
        try:
            chip_sig = ds2.read_mem(ecu_info.DRV_SIG_ADDR, ecu_info.DRV_SIG_LEN)
        except Exception as e:
            log_fn(f"Flash chip signature read failed: {e}", "warn")

        trans_raw = b""
        try:
            trans_raw = ds2.read_mem(ecu_info.TRANS_FLAG_ADDR, 1)
        except Exception as e:
            log_fn(f"Transmission flag read failed: {e}", "warn")

        return ecu_info.format_new_fields(fw_raw, isn_block, isn4_live, chip_sig, trans_raw)

    def _on_read_info(self):
        if not self._ds2: return

        def task(log_fn, progress_fn):
            log_fn("Reading ECU identification (DS2)…")
            ident = self._ds2.identify()
            cal_id = ""
            try:
                raw = self._ds2.read_mem(0x1000E, 2)
                cal_id = "".join(chr(b) if 32 <= b < 127 else "?" for b in raw).strip()
                log_fn(f"CAL ID: {cal_id}")
            except Exception as e:
                log_fn(f"CAL ID read failed: {e}", "warn")
            vin = ""
            try:
                vin = self._ds2.read_vin()
            except Exception as e:
                log_fn(f"VIN read failed: {e}", "warn")
            new_fields = self._read_new_info_fields(self._ds2, log_fn)
            return ident, cal_id, vin, new_fields

        def on_done(result):
            ident, cal_id, vin, new_fields = result
            self._info_labels["VIN"].setText(vin if vin else "Not programmed in ECU")
            if cal_id:
                self._info_labels["CAL ID"].setText(cal_id)
            parsed_id, parsed_variant = _populate_ecu_info(ident, self._info_labels)
            if parsed_id:
                self._ecu_id = parsed_id
            if parsed_variant:
                self._ecu_variant = parsed_variant
            self._last_ident_raw = ident
            for key, val in new_fields.items():
                self._info_labels[key].setText(val)
            self._log("ECU identify (DS2) read OK", "ok")

        self._run_task(task, on_success=on_done)

    # -------------------------------------------------------------------
    # Flash read / write
    # -------------------------------------------------------------------

    def _offer_additional_read_copy(self, data, entry, label, dialog_title="Read Complete"):
        """Offer an optional external copy after Bins owns the authoritative read."""
        save_copy = QMessageBox.question(
            self, dialog_title,
            f"Saved {label} automatically to Bins:\n{entry.filename}\n\n"
            f"Backup folder:\n{BACKUP_DIR}\n\n"
            "Would you like to save an additional copy elsewhere?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if save_copy != QMessageBox.Yes:
            return
        copy_path, _ = QFileDialog.getSaveFileName(
            self, f"Save Additional {label} Copy", entry.filename,
            "Binary Files (*.bin);;All Files (*)")
        if not copy_path:
            return
        try:
            with open(copy_path, "wb") as handle:
                handle.write(data)
        except OSError as error:
            QMessageBox.warning(
                self, "Copy Failed",
                f"The Bins copy is safe, but the additional copy could not be saved:\n{error}")
            return
        self._log(f"Additional {label} copy saved: {copy_path}", "ok")

    def _on_read(self, mode: str):
        if not self._ds2:
            QMessageBox.warning(self, "Not Connected", "Connect to the ECU first.")
            return
        is_full = (mode == "full")
        label = "Full ROM (256 KB)" if is_full else "Tune (24 KB)"
        expected_size = MS41ECU.FULL_ROM_SIZE if is_full else MS41ECU.TUNE_SIZE

        transfer_route = self._auto_transfer_route()
        chip_family = self._fast_chip_family() if transfer_route == "softbsl" else None

        def task(log_fn, progress_fn):
            if is_full:
                if transfer_route == "softbsl":
                    log_fn("Using the Soft-BSL RAM agent with automatic baud fallback.", "ok")
                    data = self._run_via_softbsl(
                        lambda port, pf, lf: softbsl_service.read_image(port, "full", "high", pf, lf,
                                                                        chip_family=chip_family),
                        log_fn, progress_fn)
                elif transfer_route == "native_ds2":
                    log_fn(
                        "Using stock native DS2: direct 9600 → 187500 with whole-read "
                        "9600 fallback.", "ok")
                    data = self._native_fast_read_with_fallback(
                        "full", log_fn, progress_fn)
                else:
                    log_fn("Reading full 256 KB ROM over DS2 (block-swapped layout)…")
                    data = self._ds2_read("full", progress_fn, log_fn)
            else:
                if transfer_route == "softbsl":
                    log_fn("Using the Soft-BSL RAM agent with automatic baud fallback.", "ok")
                    data = self._run_via_softbsl(
                        lambda port, pf, lf: softbsl_service.read_image(port, "tune", "high", pf, lf,
                                                                        chip_family=chip_family),
                        log_fn, progress_fn)
                elif transfer_route == "native_ds2":
                    log_fn(
                        "Using stock native DS2: direct 9600 → 187500 with whole-read "
                        "9600 fallback.", "ok")
                    data = self._native_fast_read_with_fallback(
                        "tune", log_fn, progress_fn)
                else:
                    log_fn("Reading 24 KB calibration partition (DS2 @0x10000)…")
                    data = self._ds2_read("tune", progress_fn, log_fn)
            data = bytes(data)
            if len(data) != expected_size:
                raise RuntimeError(
                    f"{label} read returned {len(data):,} bytes; expected {expected_size:,}")
            ok, details = verify_checksum(bytearray(data))
            for d in details:
                level = (
                    "debug"
                    if d.startswith("Boot/program checksums are outside the partial")
                    else "info"
                )
                log_fn(d, level)
            log_fn(f"Read and validated {len(data):,} bytes; saving automatically to Bins.")
            return data

        def on_read_success(data):
            try:
                if is_full:
                    entry = self._record_full_ecu_read(data, source="ECU read")
                else:
                    entry = self._backup_save_bytes(
                        bytearray(data), "tune", source="ECU read")
                    self._refresh_backup_table()
                    self._session_backup_read = True
            except Exception as error:
                self._log(f"{label} automatic Bins save failed: {error}", "error")
                QMessageBox.critical(
                    self, "Automatic Save Failed",
                    f"The {label} was read, but could not be saved to Bins:\n{error}")
                return
            self._log(f"{label} read complete: {entry.path}", "ok")
            self._offer_additional_read_copy(data, entry, label)

        self._run_task(task, on_success=on_read_success)

    def _on_reset_adaptations(self):
        # DS2 path
        if self._ds2:
            from PyQt5.QtWidgets import QInputDialog
            choices = [
                "All adaptations",
                "Idle adaptation",
                "Knock adaptation",
                "Lambda / fuel trim adaptation",
                "Throttle adaptation",
            ]
            choice, ok = QInputDialog.getItem(
                self, "Clear Adaptations",
                "Select which adaptation to clear:", choices, 0, False
            )
            if not ok: return

            sub_map = {
                "All adaptations":                self._ds2.ADAPT_ALL,
                "Idle adaptation":                self._ds2.ADAPT_IDLE,
                "Knock adaptation":               self._ds2.ADAPT_KNOCK,
                "Lambda / fuel trim adaptation":  self._ds2.ADAPT_LAMBDA,
                "Throttle adaptation":            self._ds2.ADAPT_THROTTLE,
            }
            sub1, sub2 = sub_map[choice]

            ans = QMessageBox.question(
                self, "Confirm Clear Adaptations",
                f"Clear '{choice}' from ECU memory?\n\n"
                "The ECU will re-learn on the next drive cycle.\n\nProceed?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if ans != QMessageBox.Yes: return

            def task(log_fn, progress_fn):
                log_fn(f"Clearing '{choice}' (DS2 0x43 sub={sub1:02X} {sub2:02X})…")
                self._ds2.clear_adaptations(sub1, sub2)
                return f"'{choice}' cleared — ECU will re-learn on next drive cycle."

            self._run_task(task)
            return

        QMessageBox.information(self, "Not Connected",
            "Connect to the ECU over DS2 first.")

    def _on_write(self, mode: str):
        label   = "Full ROM (256KB)" if mode == "full" else "Tune (24KB)"
        path, _ = QFileDialog.getOpenFileName(
            self, f"Open {label} for writing", "", "Binary Files (*.bin);;All Files (*)"
        )
        if not path: return

        expected = MS41ECU.FULL_ROM_SIZE if mode == "full" else MS41ECU.TUNE_SIZE
        size     = os.path.getsize(path)
        if size != expected:
            QMessageBox.critical(self, "Size Mismatch",
                f"Expected {expected:,} bytes, got {size:,} bytes.\n\n"
                f"Ensure you are loading a "
                f"{'full 256 KB ROM' if mode == 'full' else '24 KB tune region'} file.")
            return

        with open(path, "rb") as f:
            data = bytearray(f.read())

        # File sanity — reject blank/erased or zeroed images
        ff_ratio   = data.count(0xFF) / len(data)
        zero_ratio = data.count(0x00) / len(data)
        if ff_ratio > 0.95:
            QMessageBox.critical(self, "Invalid File",
                f"This file is {ff_ratio*100:.0f}% 0xFF bytes — looks like a blank "
                f"erased chip.\n\nFlash aborted.")
            return
        if zero_ratio > 0.95:
            QMessageBox.critical(self, "Invalid File",
                f"This file is {zero_ratio*100:.0f}% zero bytes and is not a valid ROM.\n\n"
                f"Flash aborted.")
            return

        if self._ds2 is not None:
            if mode == "full":
                self._ds2_write_full(data, os.path.basename(path))
            else:
                self._ds2_write_tune(data, os.path.basename(path))
        else:
            QMessageBox.information(self, "Not Connected",
                "Connect to the ECU over DS2 first.")

    def _ds2_verify_after_write(self, kind: str, image, log_fn, progress_fn):
        """Read the just-written image back over DS2 and compare byte-for-byte.

        Raises DS2Error on any mismatch (→ surfaces via the task's on_failure).

        kind='tune' compares the full 24 KB (that IS the written region).

        kind='full' compares ONLY the DS2 regions the write actually touches —
        program-low 0x2000-0x6000, tune 0x10000-0x16000, program-high
        0x20000-0x40000 — exactly as the factory tool does.  The boot block and
        the gaps (0x6000-0xFFFF, 0x16000-0x1FFFF) are deliberately left as-is by
        the write, so comparing them here would raise spurious failures.
        """
        log_fn("Verifying — reading back and comparing byte-for-byte…")
        if kind == "full":
            rb = self._ds2.read_full(progress_cb=progress_fn, log_fn=log_fn)
            if len(rb) != len(image):
                raise DS2Error(
                    f"Verify FAILED: read back {len(rb)} bytes, expected {len(image)}")
            # Compare in DS2 (chip) coordinates over the written windows only.
            BLK = 0x4000
            def _to_ds2(buf):
                out = bytearray(len(buf))
                for blk in range(len(buf) // BLK):
                    out[(blk ^ 1) * BLK:((blk ^ 1) + 1) * BLK] = buf[blk * BLK:(blk + 1) * BLK]
                return out
            exp, got = _to_ds2(image), _to_ds2(rb)
            windows = ((0x002000, 0x006000), (0x010000, 0x016000), (0x020000, 0x040000))
            diffs = [a for lo, hi in windows for a in range(lo, hi) if exp[a] != got[a]]
            if diffs:
                f = diffs[0]
                raise DS2Error(
                    f"Verify FAILED: {len(diffs)} byte(s) differ in the written regions — "
                    f"first at DS2 0x{f:06X} (wrote 0x{exp[f]:02X}, read 0x{got[f]:02X}). "
                    f"Re-flash before cycling ignition.")
            log_fn("Verify OK — program + tune regions read back byte-for-byte.", "ok")
            return

        rb = self._ds2.read_partial(progress_cb=progress_fn, log_fn=log_fn)
        if len(rb) != len(image):
            raise DS2Error(
                f"Verify FAILED: read back {len(rb)} bytes, expected {len(image)}")
        diffs = [i for i in range(len(image)) if image[i] != rb[i]]
        if diffs:
            f = diffs[0]
            raise DS2Error(
                f"Verify FAILED: {len(diffs)} byte(s) differ after write — first at "
                f"0x{f:05X} (wrote 0x{image[f]:02X}, read 0x{rb[f]:02X}). Re-flash before "
                f"cycling ignition.")
        log_fn(f"Verify OK — {len(image)//1024} KB read back byte-for-byte.", "ok")

    def _show_flash_complete(self, title: str, message: str):
        """Shared post-success instruction for every Flash-tab write route."""
        QMessageBox.information(
            self,
            title,
            f"{message}\n\n"
            "Turn ignition OFF.\n"
            "Wait at least 10 seconds.\n"
            "Turn ignition ON.",
        )

    def _finish_flash_success(self, title: str, message: str):
        """Persist the terminal success result before opening the modal instructions."""
        self._post_write_cycle_pending = True
        self._log(message, "ok")
        self._show_flash_complete(title, message)

    def _ds2_write_tune(self, data: bytearray, filename: str):
        """DS2 path for writing the 24 KB tune partition (write_partial)."""
        # Optionally correct calibration checksums before flashing.
        image = bytearray(data)
        if self.chk_correct_cksum.isChecked():
            image, cdet = correct_checksums(image, correct_program=False)
            for d in cdet:
                self._log(d)

        # Verify checksums on what we're about to write.
        ok, cs_details = verify_checksum(image)
        for d in cs_details:
            self._log(d)
        if not ok:
            ans = QMessageBox.warning(self, "Checksum Not Valid",
                "The tune file checksum is not valid after correction.\n\n"
                "Flashing a tune with a bad checksum may prevent the ECU from booting.\n\n"
                "Proceed anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ans != QMessageBox.Yes:
                self._log("DS2 write cancelled — checksum not valid.", "warn")
                return

        # ── Variant / CAL-ID guard ───────────────────────────────────────────
        # A partial cal from the WRONG MS41 variant can brick the ECU. This guard
        # was missing before and an MS41.1 cal written to an MS41.2 ECU bricked it.
        from ms41 import MS41ECU
        file_variant = MS41ECU.detect_variant(image)      # cal-region identity of the tune file
        file_calid   = MS41ECU.read_calid(image)
        # Prefer the program-confirmed variant (set after a full ROM read); fall back
        # to the connect-time identify response, which can't distinguish MS41.2 from
        # MS41.3 on ECUs that report ECU ID "1406464" instead of "SHINDE1".
        ecu_variant  = (getattr(self, "_ecu_program_variant", None)
                        or getattr(self, "_ecu_variant", None))
        ecu_calid    = getattr(self, "_ecu_cal_id", None)

        confirmed_mismatch = None
        uncertain_warn     = None

        if file_variant and ecu_variant:
            if file_variant != ecu_variant:
                # Special case: ECU shows MS41.2 but could actually be MS41.3 firmware
                # if the ECU has not been full-read yet (ECU ID "1406464" is shared).
                if (file_variant == "MS41.3" and ecu_variant == "MS41.2"
                        and not getattr(self, "_ecu_program_variant", None)):
                    uncertain_warn = (
                        f"The tune is MS41.3 but the connected ECU identifies as {ecu_variant}.\n\n"
                        "If this ECU was already converted to MS41.3 firmware, the "
                        "identification may be ambiguous (ECU IDs 1406464 / SHINDE1 "
                        "both map to this range).\n\n"
                        "Perform a full ROM read first to confirm the ECU's program "
                        "variant, then retry.  Proceeding without confirmation risks "
                        "a brick if the ECU is truly MS41.2.")
                else:
                    confirmed_mismatch = (
                        f"Connected ECU: {ecu_variant}  —  Tune file: {file_variant}.\n\n"
                        f"Writing a {file_variant} calibration to a {ecu_variant} ECU "
                        f"will brick it.")
        elif file_calid and ecu_calid and file_calid[:2] != ecu_calid[:2]:
            confirmed_mismatch = (
                f"CAL ID family mismatch — ECU CAL ID '{ecu_calid}' vs tune '{file_calid}'.\n\n"
                "Writing a calibration from the wrong ID family will brick the ECU.")
        elif not ecu_variant:
            uncertain_warn = ("Connected ECU variant could not be determined — "
                              "cannot confirm this tune belongs to the connected ECU.")

        if confirmed_mismatch:
            QMessageBox.critical(self, "Tune / ECU Mismatch — Flash Blocked",
                f"{confirmed_mismatch}\n\n"
                "A calibration from the wrong MS41 variant can leave the ECU unbootable "
                "and require recovery.\n\n"
                "Flash blocked. Load a tune file that matches the connected ECU.")
            self._log(f"Tune write BLOCKED — {confirmed_mismatch.splitlines()[0]}", "error")
            return

        if uncertain_warn:
            ans = QMessageBox.warning(self, "ECU Variant Uncertain",
                f"{uncertain_warn}\n\nProceed anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ans != QMessageBox.Yes:
                self._log("Tune write cancelled — ECU variant uncertain.", "warn")
                return

        transfer_route = self._auto_transfer_route()
        fast_route = transfer_route == "softbsl"
        native_route = transfer_route == "native_ds2"
        verify_write = self.chk_verify.isChecked()
        backup_before_write = self.chk_backup_before_write.isChecked()
        chip_family = self._fast_chip_family() if fast_route else None
        if fast_route:
            transport = "Soft-BSL RAM agent (fast baud with automatic fallback)"
        elif native_route:
            transport = "Native DS2 at 187,500 baud (direct; pre-erase 9600 fallback)"
        else:
            transport = "DS2 at 9600 baud"
        ans = QMessageBox.question(
            self, "Confirm Calibration Write",
            f"Writing the 24 KB calibration/tune partition.\n\n"
            f"File : {filename}\n\n"
            f"Transport: {transport}\n"
            f"Pre-write backup: {'single read' if backup_before_write else 'disabled'}\n"
            f"Read-back verification: {'enabled' if verify_write else 'disabled'}\n\n"
            f"• This will ERASE the existing tune sector, then write the new data.\n"
            f"• Keep ignition ON throughout. Engine must be OFF.\n"
            f"• Do NOT disconnect the adapter or cut power during write.\n"
            f"• If interrupted, the ECU may require re-flashing to recover.\n\n"
            f"Proceed?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return

        image_bytes = bytes(image)
        backup_entry_box = [None]
        self._invalidate_current_full_read("calibration write started")

        def task(log_fn, progress_fn):
            if backup_before_write:
                log_fn("Optional backup selected: reading the current 24 KB tune once…")
                backup_data = self._read_image_auto("tune", log_fn, progress_fn)
                backup_entry_box[0] = self._backup_save_bytes(
                    bytearray(backup_data), "tune", source="ECU read (pre-write)")
                log_fn(f"Pre-write backup saved: {backup_entry_box[0].filename}", "ok")
            log_fn("Starting partial (24 KB) write sequence…")
            if fast_route:
                log_fn("Using the Soft-BSL RAM agent with automatic baud fallback.", "ok")
                # A 24 KB tune partial goes through the soft-BSL PARTIAL writer (agent counterpart of
                # write_partial: erase the cal block + write 24 KB to the running bank). It must NOT go
                # through run_flash/flash_image, which require a full 256 KB image with a bank marker
                # (the "no valid bank-ID marker @0x5FFC" error a partial otherwise triggers).
                self._run_via_softbsl(
                    lambda port, pf, lf: softbsl_service.write_tune(
                        port, image_bytes, lf, baud="high",
                        progress_cb=pf, do_verify=verify_write, chip_family=chip_family),
                    log_fn, progress_fn)
            elif native_route:
                log_fn(
                    "Using stock native DS2 with direct 187500 entry and a short "
                    "high-rate stability check.", "ok")
                self._native_fast_write_with_fallback(
                    "tune",
                    image_bytes,
                    self._fast_chip_family(),
                    log_fn,
                    progress_fn,
                    verify_write=verify_write,
                )
            else:
                self._ds2_write("tune", image_bytes, progress_fn, log_fn)
                if verify_write:
                    self._ds2_verify_after_write("tune", image_bytes, log_fn, progress_fn)
            if verify_write:
                return "Calibration write completed and read-back verification passed."
            return f"Calibration write completed. {VERIFY_OFF_MESSAGE}"

        def on_success(msg):
            if backup_entry_box[0] is not None:
                self._session_backup_read = True
                self._refresh_backup_table()
            self._finish_flash_success("Calibration Write Complete", msg)

        def on_failure(error_msg):
            if backup_entry_box[0] is not None:
                self._session_backup_read = True
                self._refresh_backup_table()
            if isinstance(error_msg, StockWriteNotStarted):
                self._log(f"Calibration write not started: {error_msg}", "warn")
                QMessageBox.warning(self, "Calibration Write Not Started", str(error_msg))
                return
            self._log(f"Calibration write failed: {error_msg}", "error")
            if self._offer_active_flash_recovery(
                    f"The calibration write failed after erase began:\n{error_msg}"):
                return
            QMessageBox.critical(self, "Calibration Write Failed",
                f"The calibration write failed:\n{error_msg}\n\n"
                "If the write was interrupted mid-way, the ECU may have a partially "
                "erased tune sector. Re-flash before cycling ignition.")

        self._run_task(task, on_success=on_success, on_failure=on_failure)

    def _ds2_write_full(self, data: bytearray, filename: str, *,
                        require_boot_write=False, preserve_boot_identity=None,
                        archived_prewrite_image=None, on_write_success=None):
        """Validate and route a full 256 KB ROM write.

        ``require_boot_write`` is used by workflows whose intended edit lives inside file
        0x4000-0x5FFF (currently Identity/EWS). It never weakens the normal boot-write gates:
        Soft-BSL/D2XX is required and the file warning, typed acknowledgement, and final
        confirmation still run. ``preserve_boot_identity=False`` deliberately writes the ROM
        file's identity instead of grafting the currently connected ECU identity over the edit.
        ``archived_prewrite_image`` is an already-catalogued live full read; when supplied,
        the optional pre-write backup read is not repeated. ``on_write_success`` is called on
        the GUI thread only after the full writer reports success.
        """
        from ms41 import MS41ECU

        if archived_prewrite_image is not None:
            archived_prewrite_image = bytes(archived_prewrite_image)
            if len(archived_prewrite_image) != MS41ECU.FULL_ROM_SIZE:
                raise ValueError(
                    "archived_prewrite_image must be an archived 256 KB full ROM")

        # Correct every checksum the stock target enforces.  MS41.3's program
        # algorithm is not confirmed, so leave that field alone; its stock gate
        # at file 0x605C normally disables the program check.
        image = bytearray(data)
        variant = MS41ECU.detect_variant(image)
        if self.chk_correct_cksum.isChecked():
            do_prog = not (variant == "MS41.3")
            image, cdet = correct_checksums(image, correct_program=do_prog)
            for d in cdet:
                self._log(d)
            if not do_prog:
                if image[0x605C] == 0xFF:
                    self._log(
                        "MS41.3: boot and calibration checksums corrected; program "
                        "checksum left unchanged because the stock program check is disabled.",
                        "ok",
                    )
                else:
                    self._log(
                        "MS41.3: program checksum left unchanged, but its disable gate "
                        "is not 0xFF; checksum validation must pass or be explicitly overridden.",
                        "warn",
                    )

        # Verify checksums on the final image
        ok, cs_details = verify_checksum(image)
        for d in cs_details:
            self._log(d)
        if not ok:
            ans = QMessageBox.warning(self, "Checksum Not Valid",
                "The ROM image checksum is not valid after correction.\n\n"
                "Flashing a ROM with a bad checksum may prevent the ECU from booting.\n\n"
                "Proceed anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ans != QMessageBox.Yes:
                self._log("DS2 full write cancelled — checksum not valid.", "warn")
                return

        # ── Hybrid ROM check — HARD BLOCK, no override ──────────────────────────
        # Detect ROMs assembled from program and calibration of different variants
        # (e.g. MS41.1 program + MS41.3 cal).  These will brick the ECU.
        hybrid_err = MS41ECU.check_hybrid(image)
        if hybrid_err:
            QMessageBox.critical(self, "Hybrid ROM — Flash Blocked",
                "This ROM image contains mismatched program and calibration data:\n\n"
                f"  {hybrid_err}\n\n"
                "Flashing a hybrid ROM will brick the ECU.  The flash has been blocked.\n\n"
                "Use a ROM where program and calibration were prepared from the "
                "same MS41 variant, or a factory stock image.")
            self._log(f"Full write BLOCKED — hybrid ROM detected: {hybrid_err}", "error")
            return

        # Resolve the transfer/boot policy before the conversion warning. DS2 and ordinary
        # Soft-BSL writes preserve file 0x4000-0x5FFF (including serial/ISN/VIN); only the
        # explicitly armed Soft-BSL path overwrites it.
        transfer_route = self._auto_transfer_route()
        fast_route = transfer_route == "softbsl"
        native_route = transfer_route == "native_ds2"
        verify_write = self.chk_verify.isChecked()
        backup_before_write = self.chk_backup_before_write.isChecked()
        if archived_prewrite_image is not None:
            backup_description = "reused full read (already archived in Bins)"
        elif backup_before_write:
            backup_description = "single read"
        else:
            backup_description = "disabled"
        boot_checkbox_requested = bool(
            getattr(self, "chk_bootloader_write", None) is not None
            and self.chk_bootloader_write.isChecked())
        if require_boot_write and not fast_route:
            QMessageBox.critical(
                self, "Soft-BSL Boot Write Required",
                "This image changes VIN/ISN data inside the ECU's boot/parameter region "
                "(file 0x4000-0x5FFF). DS2 cannot write that region.\n\n"
                "Install Soft-BSL and reconnect with an FTDI D2XX-capable adapter, then retry. "
                "Hardware BSL remains the recovery alternative.")
            self._log("Full write blocked — the requested Identity/EWS edit requires an armed "
                      "Soft-BSL boot-region write.", "error")
            return
        will_write_boot = fast_route and (require_boot_write or boot_checkbox_requested)
        target_half = softbsl_service.marker(image) or "B"
        connected_chip_family = self._fast_chip_family()
        try:
            softbsl_service.validate_flash_image_family(
                image, connected_chip_family, write_bootloader=will_write_boot)
        except softbsl_service.FlashFamilyMismatchError as error:
            QMessageBox.critical(self, "Flash-Chip Family Mismatch", str(error))
            self._log(f"Full write blocked — {error}", "error")
            return

        preserve_requested = (
            self.chk_boot_preserve_identity.isChecked()
            if preserve_boot_identity is None else bool(preserve_boot_identity))
        preserve_boot_identity = bool(
            will_write_boot
            and preserve_requested)
        identity_source = None
        identity_info = None
        if preserve_boot_identity:
            identity_source, identity_info = self._identity_graft_source()
            if identity_source is None:
                QMessageBox.critical(
                    self, "VIN / ISN Preservation Unavailable",
                    "Preserve ECU VIN / ISN is checked, but no valid live identity snapshot "
                    "is available. Reconnect and retry so the ECU's serial/ISN and VIN can be "
                    "read, or explicitly uncheck identity preservation if you intend to write "
                    "the identity contained in the selected ROM file.")
                self._log("Full write blocked — boot-region identity preservation was requested "
                          "but no valid ECU identity snapshot is available.", "error")
                return

        # A consistent full ROM may intentionally target a different ECU variant. This is a
        # conversion, not a hybrid image; keep it opt-in, but do not block it.
        file_variant = MS41ECU.detect_variant(image)
        ecu_variant = (getattr(self, "_ecu_program_variant", None)
                       or getattr(self, "_ecu_variant", None))
        if file_variant is None:
            ans = QMessageBox.warning(self, "Unrecognised ROM",
                "This file is not recognised as a valid MS41 ROM.\n\n"
                "Flashing an invalid ROM can leave the ECU unbootable and require "
                "hardware-BSL recovery.\n\nProceed?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ans != QMessageBox.Yes:
                return
        elif ecu_variant and file_variant != ecu_variant:
            if will_write_boot:
                if preserve_boot_identity:
                    identity_note = (
                        "The connected ECU's serial/ISN and VIN were grafted onto the target "
                        "image and will be preserved.")
                else:
                    identity_note = (
                        "VIN/ISN preservation is DISABLED. The serial/ISN/VIN contained in the "
                        "selected ROM file will be written.")
                boot_note = "The target boot/parameter region will also be overwritten."
            else:
                identity_note = (
                    "The ECU's existing serial/ISN and VIN are in the preserved boot/parameter "
                    "region and will remain untouched; no full read is required.")
                boot_note = "The ECU's existing boot/parameter region will be preserved."

            title, risk_note, ms410 = self._conversion_warning_policy(
                ecu_variant, file_variant, will_write_boot)
            ans = QMessageBox.warning(self, title,
                f"Connected ECU : {ecu_variant}\n"
                f"ROM file      : {file_variant}\n\n"
                f"This will convert the ECU from {ecu_variant} to {file_variant}. "
                "A full ROM flash rewrites both the program code and the calibration, "
                "so the ECU will boot as a different variant after the write.\n\n"
                f"{boot_note}\n{identity_note}\n\n"
                f"{risk_note}\n\n"
                "Proceed with the conversion?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ans != QMessageBox.Yes:
                self._log(f"Full write cancelled — conversion {ecu_variant} → "
                          f"{file_variant} not confirmed.", "warn")
                return
            self._log(f"Variant conversion confirmed: {ecu_variant} → {file_variant}." +
                      (" MS41.0 path is untested." if ms410 else ""),
                      "warn" if ms410 else "ok")

        if preserve_boot_identity:
            image = identity.graft_identity(image, identity_source)
            self._log(f"Identity grafted from the connected ECU "
                      f"(serial {identity_info.serial or '?'}).", "ok")

        # ── Boot / SA1 patch gate ────────────────────────────────────────────
        # DS2 (and un-armed soft-BSL) leave file 0x4000-0x5FFF (SA1/boot) intact. If this image
        # carries an SA1 patch the ECU doesn't already have, those bytes would be silently
        # dropped — a partially-applied patch. Confirm against the ECU's live SA1 region, NOT a
        # 256 KB full read: use a cached full read for free when we have one, else read only the
        # exact SA1 patch-edit ranges in the write worker before any erase. Building is never gated.
        boot_ids = patch_service.boot_write_patches_in(image)    # pure, ~0 cost, usually []
        gate_needs_live_read = False
        if boot_ids and not will_write_boot:
            cached_full = getattr(self, "_last_full_read", None)
            if cached_full is not None:                          # authoritative, free (a slice)
                blk = self._boot_region_flash_block(image, cached_full)
                if blk:
                    QMessageBox.critical(self, "Boot-Region Patch — Flash Blocked", blk)
                    self._log("Full write blocked — image changes boot-region bytes the ECU lacks "
                              "(from the cached full read).", "error")
                    return
                # else: the cache confirms the ECU already has them → allow
            else:
                gate_needs_live_read = True   # defer sparse SA1 patch reads to the worker (pre-erase)

        if will_write_boot:
            warning = self._bootloader_write_file_warning(image)
            if warning and QMessageBox.warning(
                    self, "Boot-Region Write — File Check",
                    f"{warning}\n\nProceed anyway?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                self._log("Full ROM write cancelled — boot-region file check declined.", "warn")
                return
            recovery_text = (
                "If interrupted, select the intact BOTTOM bank and recover over Soft-BSL."
                if target_half == "T" else
                "If interrupted, recover over Soft-BSL when the loader remains reachable; "
                "hardware BSL is the backstop.")
            text, ok = QInputDialog.getText(
                self, "BRICK-CLASS — Boot-Region Write",
                "This will overwrite the ECU's boot/parameter region via the RAM-resident Soft-BSL "
                f"agent. {recovery_text}\n\nType  WRITE BOOT  to proceed:")
            if not ok or text.strip() != "WRITE BOOT":
                self._log("Full ROM write cancelled — boot-region confirmation declined.", "warn")
                return

        chip_family = connected_chip_family if fast_route else None
        if fast_route:
            transport = "Soft-BSL RAM agent (fast baud with automatic fallback)"
        elif native_route:
            transport = "Native DS2 at 187,500 baud (direct; pre-erase 9600 fallback)"
        else:
            transport = "DS2 at 9600 baud"
        boot_action = ("will be overwritten (brick-class)" if will_write_boot else
                       "will be preserved")
        boot_region = ("TOP fused SA7 (file 0x0000-0xFFFF)"
                       if target_half == "T" else
                       "BOTTOM SA1 (file 0x4000-0x5FFF)")
        identity_action = (
            "connected ECU VIN/ISN grafted"
            if preserve_boot_identity else
            "ROM-file VIN/ISN will be written"
            if will_write_boot else
            "connected ECU VIN/ISN preserved in place")
        ans = QMessageBox.question(
            self, "Confirm Full ROM Write",
            f"Writing a full 256 KB ROM image.\n\n"
            f"File    : {filename}\n"
            f"Variant : {file_variant or 'Unknown'}\n\n"
            f"Transport: {transport}\n"
            f"Pre-write backup: {backup_description}\n"
            f"Read-back verification: {'enabled' if verify_write else 'disabled'}\n"
            f"Boot/parameter region - {boot_region}: {boot_action}.\n\n"
            f"Identity: {identity_action}.\n\n"
            f"• Program and calibration sectors will be erased and rewritten.\n"
            f"• Keep ignition ON throughout. Engine must be OFF.\n"
            f"• Do NOT disconnect the adapter or cut power during write.\n"
            "\n"
            f"Proceed?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return

        image_bytes = bytes(image)
        softbsl_missing_after_write = (
            self._softbsl_missing_after_full_write(
                image_bytes, write_bootloader=will_write_boot)
            if fast_route else ()
        )
        backup_entry_box = [None]
        self._invalidate_current_full_read("full ROM write started")

        def task(log_fn, progress_fn):
            if archived_prewrite_image is not None:
                log_fn(
                    "Using the unmodified full ECU read already archived in Bins as the "
                    "pre-write recovery image; no duplicate backup read is needed.", "ok")
            elif backup_before_write:
                log_fn("Optional backup selected: reading the current full ROM once…")
                backup_data = self._read_image_auto("full", log_fn, progress_fn)
                backup_entry_box[0] = self._backup_save_bytes(
                    bytearray(backup_data), "full", source="ECU read (pre-write)")
                log_fn(f"Pre-write backup saved: {backup_entry_box[0].filename}", "ok")
            if gate_needs_live_read:
                ranges = patch_service.boot_patch_read_ranges(image_bytes)
                total = sum(hi - lo for lo, hi in ranges)
                log_fn(f"Boot-region gate: reading {total} required patch bytes "
                       f"across {len(ranges)} sparse range(s) before erase…")
                try:
                    # SA1 file 0x4000-0x5FFF maps linearly to DS2 0x0000-0x1FFF via XOR 0x4000.
                    # read_memory_range handles DS2's 247-byte frame cap. This runs before the
                    # soft-BSL port handoff and reads no unrelated identity/descriptor bytes.
                    sparse = [(lo, self._ds2.read_memory_range(lo ^ 0x4000, hi - lo))
                              for lo, hi in ranges]
                except Exception as e:
                    return _BootGateBlock(boot_ids, reason=f"could not be read ({e})")  # fail-safe: no erase
                missing = patch_service.missing_boot_patches_sparse(image_bytes, sparse)
                if missing:
                    return _BootGateBlock(missing)     # abort BEFORE any erase
            log_fn("Starting full ROM write sequence…")
            if fast_route:
                log_fn("Using the Soft-BSL RAM agent with automatic baud fallback.", "ok")
                self._run_via_softbsl(
                    lambda port, pf, lf: softbsl_service.run_flash(
                        port, image_bytes, "full", self._softbsl_prompt, lf, baud="high",
                        progress_cb=pf, do_verify=verify_write,
                        write_bootloader=will_write_boot, chip_family=chip_family),
                    log_fn, progress_fn,
                    restore_after_success=not softbsl_missing_after_write)
            elif native_route:
                log_fn(
                    "Using stock native DS2 with direct 187500 entry and a short "
                    "high-rate stability check.", "ok")
                self._native_fast_write_with_fallback(
                    "full",
                    image_bytes,
                    connected_chip_family,
                    log_fn,
                    progress_fn,
                    verify_write=verify_write,
                )
            else:
                self._ds2_write("full", image_bytes, progress_fn, log_fn)
                if verify_write:
                    self._ds2_verify_after_write("full", image_bytes, log_fn, progress_fn)
            if verify_write:
                return "Full ROM write completed and read-back verification passed."
            return f"Full ROM write completed. {VERIFY_OFF_MESSAGE}"

        def on_success(msg):
            if backup_entry_box[0] is not None:
                self._session_backup_read = True
                self._refresh_backup_table()
            if isinstance(msg, _BootGateBlock):
                # reason set = the SA1 read failed (fail-safe block); reason None = the read
                # succeeded and the bytes are genuinely absent, so no "couldn't read" caveat.
                QMessageBox.critical(self, "Boot-Region Patch — Flash Blocked",
                                     self._boot_block_message(msg.ids, no_evidence=False, reason=msg.reason))
                self._log("Full write blocked — required boot-region bytes are absent (confirmed by "
                          "a live sparse read); nothing was erased or written.", "error")
                return
            self._finish_flash_success("Full ROM Write Complete", msg)
            if on_write_success is not None:
                on_write_success()
            if softbsl_missing_after_write:
                missing_text = ", ".join(softbsl_missing_after_write)
                self._log(
                    "The written image no longer contains a complete Soft-BSL entry "
                    f"path ({missing_text}). Disconnected so the next connection "
                    "detects the ECU's actual transfer route.", "warn")
            if native_route or softbsl_missing_after_write:
                # Stock full writes remain at high rate. A Soft-BSL write whose
                # effective target loses the loader or normal-mode hook is also
                # left disconnected so Connect performs fresh route detection.
                self._disconnect()

        def on_failure(error_msg):
            if backup_entry_box[0] is not None:
                self._session_backup_read = True
                self._refresh_backup_table()
            if isinstance(error_msg, StockWriteNotStarted):
                self._log(f"Full ROM write not started: {error_msg}", "warn")
                QMessageBox.warning(self, "Full ROM Write Not Started", str(error_msg))
                return
            self._log(f"Full ROM write failed: {error_msg}", "error")
            if self._offer_active_flash_recovery(
                    f"The full write failed after erase began:\n{error_msg}"):
                return
            QMessageBox.critical(self, "Full ROM Write Failed",
                f"The full ROM write failed:\n{error_msg}\n\n"
                "If write was interrupted mid-way the ECU may be in a partially "
                "erased state. Review the log and recover or re-flash before cycling ignition.")

        self._run_task(task, on_success=on_success, on_failure=on_failure)

    # ── Live Data tab ────────────────────────────────────────────────────

    def _build_live_data_tab(self):
        tab = QWidget()
        lay = QVBoxLayout(tab)

        # Controls bar
        ctrl = QHBoxLayout()
        self.btn_live_start = self._op_btn("Start Polling", "#1e5080", self._on_live_start)
        self.btn_live_start.setMaximumWidth(150)
        self.btn_live_stop  = self._op_btn("Stop",          "#7a1f1f", self._on_live_stop)
        self.btn_live_stop.setMaximumWidth(100)
        self.btn_live_stop.setEnabled(False)

        ctrl.addWidget(QLabel("Interval:"))
        self.spin_interval = QSpinBox()
        self.spin_interval.setRange(100, 5000)
        self.spin_interval.setSingleStep(100)
        self.spin_interval.setValue(100)
        self.spin_interval.setSuffix(" ms")
        self.spin_interval.setFixedWidth(90)
        self.spin_interval.setToolTip(
            "Minimum start-to-start poll period. Acquisition time is included.\n"
            "\n"
            "Telegram (batch) mode: one response containing all displayed values.\n"
            "  100 ms requests the fastest practical rate.\n"
            "\n"
            "Standard (RAM reads) mode: several grouped read_mem calls per cycle.\n"
            "  Acquisition time becomes the effective interval when it exceeds this setting.\n"
            "\n"
            "For data logging use Telegram mode; Standard is a fallback."
        )
        ctrl.addWidget(self.spin_interval)

        self.chk_live_log = QCheckBox("Log to CSV")
        self.chk_live_log.setChecked(True)
        self.chk_live_log.setStyleSheet("color:#aaa;")
        self.chk_live_log.setToolTip(
            "Write a CSV log to the logs/ folder.\n"
            "Time column is elapsed seconds — compatible with MegaLog Viewer HD."
        )
        ctrl.addWidget(self.chk_live_log)

        self.chk_telegram = QCheckBox("Fast Telegram Mode")
        self.chk_telegram.setChecked(True)
        self.chk_telegram.setStyleSheet("color:#f0c060;")
        self.chk_telegram.setToolTip(
            "Register ECU RAM addresses with DS2 0x0B/0x01, then poll them together.\n"
            "All 24 ECU slots are used. Two state bytes decode closed/part/full load,\n"
            "deceleration fuel cut, and engine-start states. MS41.3 automatically\n"
            "switches to actual/target AFR when its wideband feature is enabled.\n"
            "Requires a connected ECU with a known variant."
        )
        self.chk_telegram.setEnabled(False)  # enabled once variant is known
        ctrl.addWidget(self.chk_telegram)

        ctrl.addWidget(self.btn_live_start)
        ctrl.addWidget(self.btn_live_stop)
        ctrl.addStretch()

        self.lbl_live_status = QLabel("Not polling")
        self.lbl_live_status.setStyleSheet("color:#888; font-style:italic;")
        ctrl.addWidget(self.lbl_live_status)
        lay.addLayout(ctrl)

        # Telegram mode info bar (hidden by default)
        self.lbl_telegram_note = QLabel(
            "  Telegram mode active — DS2 registered-address batch using the connected ECU's "
            "address family. All 24 slots are used for core values, operating states, and "
            "analog inputs. On MS41.3, enabled wideband support selects actual AFR, target AFR, "
            "and the configured wideband input automatically."
        )
        self.lbl_telegram_note.setStyleSheet(
            "background:#3a2e00; color:#f0c060; border:1px solid #806000; "
            "padding:4px; font-size:10px;"
        )
        self.lbl_telegram_note.setWordWrap(True)
        self.lbl_telegram_note.setVisible(False)
        lay.addWidget(self.lbl_telegram_note)

        # Parameter table — union of standard + telegram-only parameters
        rows_def = display_rows()
        self.live_table = QTableWidget(len(rows_def), 3)
        self.live_table.setHorizontalHeaderLabels(["Parameter", "Value", "Unit"])
        self.live_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.live_table.horizontalHeader().setDefaultSectionSize(100)
        self.live_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.live_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.live_table.setAlternatingRowColors(True)
        self.live_table.setStyleSheet("""
            QTableWidget { background:#1e1e1e; color:#d4d4d4;
                           gridline-color:#333; border:1px solid #444; }
            QTableWidget::item:alternate { background:#252525; }
            QHeaderView::section { background:#2a2a2a; color:#aaa;
                                   border:1px solid #444; padding:4px; font-weight:bold; }
        """)
        self.live_table.setFont(QFont("Courier New", 10))
        self._live_rows = {}
        for row, (pname, punit) in enumerate(rows_def):
            name_item = QTableWidgetItem(pname)
            name_item.setForeground(QBrush(QColor("#aaa")))
            val_item  = QTableWidgetItem("—")
            val_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            val_item.setForeground(QBrush(QColor("#7ec8e3")))
            val_item.setFont(QFont("Courier New", 11))
            unit_item = QTableWidgetItem(punit)
            unit_item.setForeground(QBrush(QColor("#888")))
            self.live_table.setItem(row, 0, name_item)
            self.live_table.setItem(row, 1, val_item)
            self.live_table.setItem(row, 2, unit_item)
            self._live_rows[pname] = row
            if pname in PROFILE_DISPLAY_NAMES:
                self.live_table.setRowHidden(row, True)
        lay.addWidget(self.live_table, 1)

        self.tabs.addTab(tab, "  Live Data  ")
        self._set_live_buttons_enabled(False)

    def _on_live_start(self):
        if not self._ds2: return
        interval     = self.spin_interval.value() / 1000.0
        use_telegram = self.chk_telegram.isChecked()
        log_path     = None
        if self.chk_live_log.isChecked():
            os.makedirs(LOG_DIR, exist_ok=True)
            ts       = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            if self._ds2 is not None:
                mode_tag = "ds2_telegram" if use_telegram else "ds2_standard"
            else:
                mode_tag = "telegram" if use_telegram else "live"
            log_path = os.path.join(LOG_DIR, f"{mode_tag}_{ts}.csv")
        self._poller = LiveDataPoller(interval=interval, use_telegram=use_telegram,
                                      ecu_id=self._ecu_id,
                                      ecu_variant=(self._ecu_program_variant or self._ecu_variant),
                                      ds2=self._ds2)
        self._poller.start(log_path=log_path)
        self._live_timer.start()
        self.btn_live_start.setEnabled(False)
        self.btn_live_stop.setEnabled(True)
        self.chk_telegram.setEnabled(False)
        if self._ds2 is not None:
            mode_label = "DS2 Telegram (0x0B batch)" if use_telegram else "DS2 Standard (cmd 0x06)"
        elif use_telegram:
            mode_label = "Telegram (fast)"
        else:
            mode_label = "DS2 Standard (cmd 0x06)"
        self._live_log_basename = os.path.basename(log_path) if log_path else ""
        status = f"{mode_label}  —  polling every {self.spin_interval.value()} ms"
        if log_path:
            status += f"  —  {self._live_log_basename}  [0 rows]"
        self.lbl_live_status.setText(status)
        self.lbl_live_status.setStyleSheet("color:#5f5; font-style:normal;")
        self.lbl_telegram_note.setVisible(use_telegram)
        self._log(
            f"Live data polling started  ({mode_label}, {self.spin_interval.value()} ms)", "ok"
        )

    def _on_live_stop(self):
        was_polling = self._poller is not None
        self._live_timer.stop()
        rows = self._poller.csv_rows if self._poller else 0
        if self._poller:
            self._poller.stop()
            self._poller = None
        self.btn_live_start.setEnabled(True)
        self.btn_live_stop.setEnabled(False)
        self.lbl_telegram_note.setVisible(False)
        self._update_telegram_checkbox_state()
        stop_msg = f"Stopped  —  {rows} rows logged to {self._live_log_basename}" if self._live_log_basename and rows else "Stopped"
        self._live_log_basename = ""
        self.lbl_live_status.setText(stop_msg)
        self.lbl_live_status.setStyleSheet("color:#888; font-style:italic;")
        if was_polling:
            self._log("Live data polling stopped", "info")

    def _refresh_live_display(self):
        if not self._poller:
            return
        values        = self._poller.latest_values()
        telegram_mode = getattr(self._poller, "_use_telegram", False)
        active_profile_names = self._poller.active_profile_names
        for name in PROFILE_DISPLAY_NAMES:
            row = self._live_rows.get(name)
            if row is not None:
                self.live_table.setRowHidden(row, name not in active_profile_names)
        for name, (val_str, unit) in values.items():
            row = self._live_rows.get(name)
            if row is None:
                continue
            item = self.live_table.item(row, 1)
            if item:
                item.setText(val_str)
                item.setToolTip(val_str)
                if val_str == "ERR":
                    colour = "#f47171"
                elif telegram_mode and name not in TELEGRAM_PARAM_NAMES:
                    colour = "#555555"   # dimmed — not covered by telegram
                else:
                    colour = "#7ec8e3"
                item.setForeground(QBrush(QColor(colour)))
        for err in self._poller.pop_errors():
            self._log(f"Live data: {err}", "warn")
        rows = self._poller.csv_rows
        rate = self._poller.sample_rate
        if rows > 0:
            cur = self.lbl_live_status.text()
            # Append or update the row counter suffix without rebuilding the whole string
            base = cur.split("  —  ")[0] if "  —  " in cur else cur
            # Find the file label part that was set at start
            file_part = getattr(self, "_live_log_basename", "")
            file_info = f"  —  {file_part}  [{rows} rows]" if file_part else ""
            self.lbl_live_status.setText(f"{base}  —  {rate:.1f} samples/s" + file_info)

    def _update_telegram_checkbox_state(self):
        """Telegram (batch) mode is available whenever connected over DS2."""
        connected = self._ds2 is not None
        self.chk_telegram.setEnabled(connected)
        if connected:
            self.chk_telegram.setToolTip(
                "DS2 Telegram mode: batch read via cmd 0x0B/0x01 (MS41 RomRaider style).\n"
                "All RAM addresses sent in a single request — faster, fewer round-trips.\n"
                "Uncheck for Standard mode: individual cmd 0x06 block reads — slower\n"
                "but more reliable if the batch request causes issues.\n"
                "(Both modes use the same RAM addresses and scaling.)"
            )

    def _set_live_buttons_enabled(self, enabled: bool):
        self.btn_live_start.setEnabled(enabled)
        self.btn_live_stop.setEnabled(False)

    # ── ROM Analyzer tab ─────────────────────────────────────────────────

    def _build_analyzer_tab(self):
        tab = QWidget()
        lay = QVBoxLayout(tab)

        btn_bar = QHBoxLayout()
        btn_load = self._op_btn("Load ROM / Tune File…", "#3d3d3d", self._on_analyze_file)
        btn_load.setMaximumWidth(220)
        btn_bar.addWidget(btn_load)
        btn_bar.addSpacing(16)
        btn_bar.addWidget(QLabel("Definition:"))
        self.cb_analyzer_definition = QComboBox()
        self.cb_analyzer_definition.setMinimumWidth(220)
        self.cb_analyzer_definition.setToolTip(
            "Registered definitions are copied to your BimmerStein user-data folder."
        )
        self.cb_analyzer_definition.currentIndexChanged.connect(
            self._on_analyzer_definition_changed
        )
        btn_bar.addWidget(self.cb_analyzer_definition, 1)
        self.btn_analyzer_load_definition = QPushButton("Load Definition...")
        self.btn_analyzer_load_definition.clicked.connect(self._on_load_definition)
        btn_bar.addWidget(self.btn_analyzer_load_definition)
        self.btn_analyzer_delete_definition = QPushButton("Delete")
        self.btn_analyzer_delete_definition.clicked.connect(self._on_delete_definition)
        btn_bar.addWidget(self.btn_analyzer_delete_definition)
        lay.addLayout(btn_bar)

        _gb_style = """
            QGroupBox { color:#aaa; font-weight:bold; border:1px solid #444;
                        border-radius:4px; margin-top:6px; padding-top:4px; }
            QGroupBox::title { subcontrol-origin:margin; left:10px; padding:0 4px; }
        """
        # Summary group
        summary_group = QGroupBox("File Summary")
        summary_group.setStyleSheet(_gb_style)
        summary_grid  = QGridLayout(summary_group)
        summary_grid.setColumnStretch(1, 1)
        self._analyzer_labels = {}
        fields = [
            ("File Type",     "file_type"),
            ("Variant",       "variant"),
            ("ECU ID",        "ecu_id"),
            ("CAL ID",        "cal_id"),
            ("VIN",           "vin"),
            ("ISN",           "isn"),
            ("Matched Definition", "matched"),
            ("Checksum Check","checksum"),
        ]
        for row, (label, key) in enumerate(fields):
            lk = QLabel(f"{label}:")
            lk.setStyleSheet("font-weight:bold; color:#aaa; min-width:140px;")
            lv = QLabel("—")
            lv.setStyleSheet("color:#e0e0e0;")
            lv.setWordWrap(True)
            lv.setTextFormat(Qt.RichText)
            summary_grid.addWidget(lk, row, 0, Qt.AlignTop)
            summary_grid.addWidget(lv, row, 1, Qt.AlignTop)
            self._analyzer_labels[key] = lv
        lay.addWidget(summary_group)

        # Checksum detail
        cs_detail_label = QLabel("Checksum detail:")
        cs_detail_label.setStyleSheet("color:#888; font-size:9pt; padding-top:2px;")
        lay.addWidget(cs_detail_label)
        self.analyzer_cs_detail = QTextEdit()
        self.analyzer_cs_detail.setReadOnly(True)
        self.analyzer_cs_detail.setMaximumHeight(70)
        self.analyzer_cs_detail.setFont(QFont("Courier New", 9))
        self.analyzer_cs_detail.setStyleSheet(
            "background:#1a1a1a; color:#aaa; border:1px solid #444; padding:2px;"
        )
        lay.addWidget(self.analyzer_cs_detail)

        # Parameters supplied by the selected calibration definition.
        scalar_group = QGroupBox("Parameters")
        scalar_group.setStyleSheet(_gb_style)
        scalar_lay   = QVBoxLayout(scalar_group)

        filt_row = QHBoxLayout()
        filt_lbl = QLabel("Filter:")
        filt_lbl.setStyleSheet("color:#aaa;")
        filt_row.addWidget(filt_lbl)
        self.analyzer_filter = QLineEdit()
        self.analyzer_filter.setPlaceholderText("type to filter by name or category…")
        self.analyzer_filter.textChanged.connect(self._apply_analyzer_filter)
        filt_row.addWidget(self.analyzer_filter)
        self.chk_scalars_only = QCheckBox("Scalars only")
        self.chk_scalars_only.setStyleSheet("color:#aaa;")
        self.chk_scalars_only.stateChanged.connect(self._apply_analyzer_filter)
        filt_row.addWidget(self.chk_scalars_only)
        self.lbl_param_count = QLabel("")
        self.lbl_param_count.setStyleSheet("color:#888;")
        filt_row.addWidget(self.lbl_param_count)
        self.btn_analyzer_parameters_window = QPushButton("Open in Window...")
        self.btn_analyzer_parameters_window.setEnabled(False)
        self.btn_analyzer_parameters_window.clicked.connect(
            self._open_analyzer_parameters_window
        )
        filt_row.addWidget(self.btn_analyzer_parameters_window)
        scalar_lay.addLayout(filt_row)

        self.scalar_table = _create_analyzer_table()
        self._analyzer_params = []   # full unfiltered list
        scalar_lay.addWidget(self.scalar_table)

        # Warnings box
        self.analyzer_warns_group = QGroupBox("⚠  Warnings")
        self.analyzer_warns_group.setStyleSheet(
            _gb_style.replace("color:#aaa", "color:#e8c46a")
              .replace("border:1px solid #444", "border:1px solid #806000")
        )
        warns_lay = QVBoxLayout(self.analyzer_warns_group)
        warns_lay.setContentsMargins(4, 4, 4, 4)
        self.analyzer_warns = QTextEdit()
        self.analyzer_warns.setReadOnly(True)
        self.analyzer_warns.setMaximumHeight(60)
        self.analyzer_warns.setFont(QFont("Courier New", 9))
        self.analyzer_warns.setStyleSheet(
            "background:#1a1a1a; color:#e8c46a; border:none;"
        )
        warns_lay.addWidget(self.analyzer_warns)
        self.analyzer_warns_group.setVisible(False)
        scalar_lay.addWidget(self.analyzer_warns_group)
        lay.addWidget(scalar_group, 1)

        self.tabs.addTab(tab, "  ROM Analyzer  ")
        self._refresh_analyzer_definitions()

    def _refresh_analyzer_definitions(self, selected_name=None):
        names = self._definition_registry.names()
        active = selected_name if selected_name in names else self._definition_registry.active_name()
        self.cb_analyzer_definition.blockSignals(True)
        try:
            self.cb_analyzer_definition.clear()
            self.cb_analyzer_definition.addItem("No definition selected", None)
            for name in names:
                self.cb_analyzer_definition.addItem(name, name)
            index = self.cb_analyzer_definition.findData(active)
            self.cb_analyzer_definition.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self.cb_analyzer_definition.blockSignals(False)
        self.btn_analyzer_delete_definition.setEnabled(active is not None)

    def _on_analyzer_definition_changed(self):
        name = self.cb_analyzer_definition.currentData()
        try:
            self._definition_registry.set_active(name)
        except DefinitionRegistryError as exc:
            QMessageBox.critical(self, "Definition Selection Failed", str(exc))
            self._refresh_analyzer_definitions()
            return
        self.btn_analyzer_delete_definition.setEnabled(name is not None)
        self._log(
            f"ROM Analyzer definition: {name or 'none selected'}",
            "ok" if name else "warn",
        )
        self._reanalyze_loaded_file()

    def _on_load_definition(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select MS41 Calibration Definition",
            "",
            "XML Definition Files (*.xml);;All Files (*)",
        )
        if not path:
            return
        try:
            try:
                result = self._definition_registry.import_file(path)
            except DefinitionConflictError as conflict:
                answer = QMessageBox.question(
                    self,
                    "Replace Registered Definition?",
                    f"A different definition named '{conflict.destination.name}' is already "
                    "registered. Replace the registered copy?\n\n"
                    "The original source file will not be changed.",
                    QMessageBox.Yes | QMessageBox.Cancel,
                    QMessageBox.Cancel,
                )
                if answer != QMessageBox.Yes:
                    return
                result = self._definition_registry.import_file(path, replace=True)
            self._definition_registry.set_active(result.path.name)
        except DefinitionRegistryError as exc:
            QMessageBox.critical(self, "Definition Could Not Be Loaded", str(exc))
            return

        self._refresh_analyzer_definitions(result.path.name)
        detail = (
            "The identical registered copy was selected."
            if result.identical
            else "The definition was copied into the application registry."
        )
        self._log(f"Definition ready: {result.path.name}. {detail}", "ok")
        self._reanalyze_loaded_file()

    def _on_delete_definition(self):
        name = self.cb_analyzer_definition.currentData()
        if not name:
            return
        answer = QMessageBox.question(
            self,
            "Delete Registered Definition?",
            f"Delete '{name}' from BimmerStein ECU Tool?\n\n"
            "Only the registered copy will be deleted. The original source file is untouched.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self._definition_registry.delete(name)
        except DefinitionRegistryError as exc:
            QMessageBox.critical(self, "Definition Could Not Be Deleted", str(exc))
            return
        self._refresh_analyzer_definitions()
        self._log(f"Deleted registered definition: {name}", "ok")
        self._reanalyze_loaded_file()

    def _reanalyze_loaded_file(self):
        if self._analyzer_loaded_data is not None:
            self._show_analysis(self._analyzer_loaded_data, self._analyzer_loaded_path)

    def _open_analyzer_parameters_window(self):
        if self._analyzer_parameters_window is None:
            window = AnalyzerParametersWindow(self)
            window.closed.connect(self._on_analyzer_parameters_window_destroyed)
            self._analyzer_parameters_window = window
        self._sync_analyzer_parameters_window()
        self._analyzer_parameters_window.show()
        self._analyzer_parameters_window.raise_()
        self._analyzer_parameters_window.activateWindow()

    def _on_analyzer_parameters_window_destroyed(self, *_args):
        self._analyzer_parameters_window = None

    def _sync_analyzer_parameters_window(self):
        window = self._analyzer_parameters_window
        if window is None:
            return
        source_name = os.path.basename(self._analyzer_loaded_path) or "Loaded data"
        definition_name = self.cb_analyzer_definition.currentData() or "None selected"
        window.set_analysis(
            getattr(self, "_analyzer_params", []),
            getattr(self, "_analyzer_counts", (0, 0)),
            source_name,
            definition_name,
        )

    def _on_analyze_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select ROM or Tune File", "", "Binary Files (*.bin);;All Files (*)"
        )
        if not path:
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as exc:
            QMessageBox.critical(self, "File Could Not Be Loaded", str(exc))
            return
        self._show_analysis(data, path)

    def _show_analysis(self, data: bytes, path: str = ""):
        self._analyzer_loaded_data = bytes(data)
        self._analyzer_loaded_path = path
        result = analyze_rom(data, self._definition_registry.active_path())

        self._analyzer_labels["file_type"].setText(result.file_type)
        self._analyzer_labels["variant"].setText(
            result.variant if result.variant else "Unknown / Not MS41"
        )
        self._analyzer_labels["ecu_id"].setText(result.ecu_id or "Not found")
        cal_display = f"{result.cal_id[:2]}  ({result.cal_id})" if result.cal_id else "Not found"
        self._analyzer_labels["cal_id"].setText(cal_display)
        # Offline VIN decode (full ROMs carry it at 0x5D07; tunes don't)
        from ms41 import MS41ECU
        vin = MS41ECU.vin_from_image(data)
        self._analyzer_labels["vin"].setText(
            vin if vin else ("N/A (tune file)" if "24 KB" in result.file_type
                             else "Not programmed / not found"))
        # Offline ISN decode (full ROMs carry it at file 0x5CE5; tunes don't)
        id_info = identity.decode_identity(bytes(data))
        if "24 KB" in result.file_type:
            isn_text = "N/A (tune file)"
        elif id_info.serial:
            isn_text = ecu_info.format_full_isn_html(id_info.serial, id_info.isn4 or "")
        else:
            isn_text = "Not programmed / not found"
        self._analyzer_labels["isn"].setText(isn_text)
        self._analyzer_labels["matched"].setText(result.matched_label or "No match")
        # Checksum verification state (enabled = stock, disabled = tune workaround)
        state_map = {
            "enabled":  ("ENABLED (stock)",  "#5f5"),
            "disabled": ("DISABLED",          "#e8c46a"),
            "unknown":  ("UNKNOWN",           "#f47171"),
            "n/a":      ("N/A (tune file)",   "#888"),
        }
        if "24 KB" in result.file_type:
            # Partial: report the calibration checksum-table status (verified algorithm
            # for all variants incl. MS41.3 — confirmed against a corrected MS41.3 read).
            from checksum import verify_checksum as _vc
            cal_ok, _ = _vc(bytearray(data))
            cs_text  = "CAL OK" if cal_ok else "CAL MISMATCH — correct before write"
            cs_color = "#5f5" if cal_ok else "#f47171"
        elif result.variant == "MS41.3":
            # MS41.3 enforces boot and calibration checksums; its stock program
            # checksum gate is disabled.
            cs_text = (
                "boot/calibration checks enforced · program check "
                f"{result.checksum.upper()}"
            )
            if result.checksum == "disabled":
                cs_text += " (stock MS41.3 policy)"
                cs_color = "#e8c46a"
            else:
                cs_color = "#f47171"
        else:
            cs_text, cs_color = state_map.get(result.checksum, (result.checksum, "#888"))
        self._analyzer_labels["checksum"].setText(cs_text)
        self._analyzer_labels["checksum"].setStyleSheet(
            f"color:{cs_color}; font-weight:bold;"
        )
        self.analyzer_cs_detail.setPlainText("\n".join(result.cs_details))

        # Store full param list and render (with current filter)
        self._analyzer_params = result.params
        self._analyzer_counts = (result.n_scalars, result.n_maps)
        self.btn_analyzer_parameters_window.setEnabled(True)
        self._apply_analyzer_filter()
        self._sync_analyzer_parameters_window()

        if result.warnings:
            self.analyzer_warns.setPlainText("\n".join(result.warnings))
            self.analyzer_warns_group.setVisible(True)
        else:
            self.analyzer_warns_group.setVisible(False)

        self._log(
            f"Analyzed: {os.path.basename(path)} — {result.file_type}, "
            f"{result.matched_label or 'no def'}, "
            f"{result.n_scalars} scalars + {result.n_maps} maps",
            "ok" if result.matched_label else "warn"
        )

    def _apply_analyzer_filter(self):
        params = getattr(self, "_analyzer_params", [])
        rows = _filter_analyzer_params(
            params,
            self.analyzer_filter.text(),
            self.chk_scalars_only.isChecked(),
        )
        _populate_analyzer_table(self.scalar_table, rows)
        ns, nm = getattr(self, "_analyzer_counts", (0, 0))
        self.lbl_param_count.setText(f"showing {len(rows)} of {len(params)}  ({ns} scalars, {nm} maps)")

    # ── ECU Config tab ───────────────────────────────────────────────────

    def _build_config_tab(self):
        import ecu_config
        self._ecu_config_mod = ecu_config
        tab = QWidget()
        tab_lay = QVBoxLayout(tab)
        tab_lay.setContentsMargins(0, 0, 0, 0)
        self._config_scroll = QScrollArea()
        self._config_scroll.setWidgetResizable(True)
        self._config_scroll.setFrameShape(QScrollArea.NoFrame)
        self._config_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        config_body = QWidget()
        lay = QVBoxLayout(config_body)
        self._config_scroll.setWidget(config_body)
        tab_lay.addWidget(self._config_scroll)

        note = QLabel(
            "Enable/disable ECU features via the calibration <b>Control Bits</b> "
            "(Byte 4–8).<br>Load a full ROM or a 24 KB partial, change the options, "
            "then <b>Apply &amp; Save</b>. Only the relevant bits change; checksums "
            "are recomputed on save. Full ROMs also expose supported program-region "
            "switches. Bit meanings come from the matching RomRaider CAL-ID definition."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#aaa; padding:6px;")
        lay.addWidget(note)

        # ── FILE operations row (active only when a file is loaded) ───────────
        file_row = QHBoxLayout()
        file_tag = QLabel("FILE")
        file_tag.setStyleSheet("color:#7ec8e3; font-weight:bold; min-width:38px;")
        file_tag.setToolTip("Edit the feature flags of a ROM/partial file on disk, then Apply & Save.")
        self.btn_config_load = self._op_btn("Load ROM / Partial…", "#1e5080", self._on_config_load)
        self.btn_config_load.setMaximumWidth(180)
        self.btn_config_close = self._op_btn("Close File", "#3d3d3d", self._on_config_close)
        self.btn_config_close.setMaximumWidth(110)
        self.btn_config_close.setEnabled(False)
        self.btn_config_close.setToolTip("Close the loaded file and switch back to ECU operations.")
        self.btn_config_copy_ecu = self._op_btn("Copy flags from ECU", "#3d3d3d", self._on_config_copy_from_ecu)
        self.btn_config_copy_ecu.setMaximumWidth(180)
        self.btn_config_copy_ecu.setEnabled(False)
        self.btn_config_copy_ecu.setToolTip(
            "Read the connected ECU's current feature flags into the dropdowns,\n"
            "keeping the loaded file as the save target. Copies flags only — not maps.")
        self.btn_config_save = self._op_btn("Apply & Save…", "#3d6b35", self._on_config_save)
        self.btn_config_save.setMaximumWidth(150)
        self.btn_config_save.setEnabled(False)
        file_row.addWidget(file_tag)
        file_row.addWidget(self.btn_config_load)
        file_row.addWidget(self.btn_config_close)
        file_row.addWidget(self.btn_config_copy_ecu)
        file_row.addWidget(self.btn_config_save)
        file_row.addStretch()
        self.lbl_config_file = QLabel("No file loaded")
        self.lbl_config_file.setStyleSheet("color:#888; font-style:italic;")
        file_row.addWidget(self.lbl_config_file)
        lay.addLayout(file_row)

        # ── ECU operations row (active only when NO file is loaded) ───────────
        ecu_row = QHBoxLayout()
        ecu_tag = QLabel("ECU")
        ecu_tag.setStyleSheet("color:#c8a85f; font-weight:bold; min-width:38px;")
        ecu_tag.setToolTip("Read or write the connected ECU's feature flags directly.")
        self.btn_config_read_ecu = self._op_btn("Read from ECU", "#1e5080", self._on_config_read_ecu)
        self.btn_config_read_ecu.setMaximumWidth(160)
        self.btn_config_read_ecu.setEnabled(False)
        self.btn_config_read_ecu.setToolTip(
            "Read control bytes 4–8 and supported program switches directly from the ECU.\n"
            "A matching current-session full read remains a fallback if a small program-byte\n"
            "read fails; a full ROM read is otherwise deferred until a program change is written.")
        self.btn_config_get_file = self._op_btn("Get flags from file…", "#3d3d3d", self._on_config_get_from_file)
        self.btn_config_get_file.setMaximumWidth(180)
        self.btn_config_get_file.setEnabled(False)
        self.btn_config_get_file.setToolTip(
            "Load the feature flags from a ROM/partial file into the dropdowns,\n"
            "then Write to ECU. Copies flags only — not maps or the full tune.")
        self.btn_config_write_ecu = self._op_btn("Write to ECU", "#7a1f1f", self._on_config_write_ecu)
        self.btn_config_write_ecu.setMaximumWidth(150)
        self.btn_config_write_ecu.setEnabled(False)
        self.btn_config_write_ecu.setToolTip(
            "Apply the selected configuration to the connected ECU. Calibration-only\n"
            "changes use a 24 KB partial write. If a program-region setting changed,\n"
            "the app reuses a current-session full backup or reads and archives the\n"
            "full 256 KB ROM, patches it, then routes it through the guarded full writer.")
        ecu_row.addWidget(ecu_tag)
        ecu_row.addWidget(self.btn_config_read_ecu)
        ecu_row.addWidget(self.btn_config_get_file)
        ecu_row.addWidget(self.btn_config_write_ecu)
        ecu_row.addStretch()
        lay.addLayout(ecu_row)

        self._config_combos = {}
        self._config_section_groups = {}
        feature_label_width = max(
            QLabel(feat.name + ":").sizeHint().width()
            for feat in ecu_config.FEATURES
        ) + 8
        combo_probe = QComboBox()
        combo_probe.ensurePolished()
        config_combo_height = max(
            20, combo_probe.fontMetrics().lineSpacing() + 6)

        def _add_feature_group(title, features):
            group = QGroupBox(title)
            group.setStyleSheet(_SECTION_GB)
            grid = QGridLayout(group)
            grid.setContentsMargins(8, 10, 8, 6)
            grid.setVerticalSpacing(2)
            grid.setColumnMinimumWidth(0, feature_label_width)
            grid.setColumnStretch(1, 1)
            for row, feat in enumerate(features):
                lk = QLabel(feat.name + ":")
                lk.setFixedWidth(feature_label_width)
                lk.setStyleSheet("font-weight:bold; color:#cfcfcf;")
                lk.setToolTip(feat.note)
                cb = QComboBox()
                options = feat.options_for(None, None)
                if options:
                    cb.addItems([label for label, _ in options])
                elif feat.is_profile_specific:
                    cb.addItem("(firmware profile required)")
                cb.setEnabled(False)
                cb.setMinimumWidth(200)
                cb.setFixedHeight(config_combo_height)
                cb.setToolTip(feat.note)
                grid.addWidget(lk, row, 0, Qt.AlignLeft)
                grid.addWidget(cb, row, 1, Qt.AlignLeft | Qt.AlignVCenter)
                self._config_combos[feat.name] = cb
            self._config_section_groups[title] = group
            lay.addWidget(group)

        _add_feature_group(
            "Calibration Region — Partial & Full ROM / ECU Read & Write",
            [f for f in ecu_config.FEATURES if not f.is_program_feature],
        )
        _add_feature_group(
            "Program Region — Full ROM write only",
            [f for f in ecu_config.FEATURES if f.is_program_feature],
        )

        self.chk_config_fix = QCheckBox("Recompute checksums on save (recommended)")
        self.chk_config_fix.setChecked(True)
        self.chk_config_fix.setStyleSheet("color:#aaa; padding:4px;")
        lay.addWidget(self.chk_config_fix)

        tnote = QLabel(
            "Transmission selection changes Byte 5 bits 0–6 while preserving the "
            "independent knock-detection setting in bit 7. For ID12/ID60 experimental "
            "O2 disable, first select O2 Feedback Program Gate = Feedback Disabled; "
            "this exposes Oxygen Sensors = Disabled (Experimental). Either change "
            "alone does not disable feedback."
        )
        tnote.setWordWrap(True)
        tnote.setStyleSheet("color:#777; padding:6px; font-size:10px;")
        lay.addWidget(tnote)
        lay.addStretch()

        self._config_data = None
        self._config_path = None
        self._config_profile = None
        self._config_program_variant = None
        self._config_target_size = None
        self._config_o2_gate_present = False
        self._config_o2_pre_experimental_choice = None
        self._config_live_values_read = False
        self._config_live_baseline = {}
        self._config_live_program_placeholder = "(full ROM read on write)"
        self._config_combos[
            "O2 Feedback Program Gate (Experimental)"
        ].currentTextChanged.connect(self._on_config_o2_program_gate_changed)
        config_body.setMinimumHeight(config_body.sizeHint().height())
        self._config_tab_index = self.tabs.addTab(tab, "  ECU Config  ")
        self._update_config_buttons()

    def _on_config_load(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load full ROM or 24 KB partial", "", "Binary Files (*.bin);;All Files (*)"
        )
        if not path: return
        self._load_config_from_path(path)

    def _live_control_bit_profile(self):
        """Exact CAL-ID family for the connected ECU's control-bit definitions."""
        return self._ecu_config_mod.control_bit_profile_from_calid(
            getattr(self, "_ecu_cal_id", None))

    def _live_config_program_variant(self):
        """Best live program-family verdict available without reading the full ROM."""
        return (getattr(self, "_ecu_program_variant", None)
                or getattr(self, "_ecu_variant", None))

    def _current_full_read_for_connection(self):
        """Return the exact current-session full read owned by this connection."""
        cached = getattr(self, "_last_full_read", None)
        current_key = self._identity_connection_key()
        if (current_key is None
                or cached is None or len(cached) != MS41ECU.FULL_ROM_SIZE
                or getattr(self, "_last_full_read_key", None)
                != current_key):
            return None
        return bytes(cached)

    def _invalidate_current_full_read(self, reason=None):
        """Stop treating the archived full read as an exact image of the live ECU."""
        if getattr(self, "_last_full_read", None) is not None and reason:
            self._log(f"Current-session full-read cache invalidated: {reason}", "debug")
        self._last_full_read = None
        self._last_full_read_key = None

    def _reset_live_config_state(self):
        """Forget live dropdown baselines that belong to a previous ECU/read."""
        self._config_live_values_read = False
        self._config_live_baseline = {}

    def _config_program_baseline(self):
        """Snapshot only program-region selections used to choose the write route."""
        return {
            feat.name: self._config_combos[feat.name].currentText()
            for feat in self._ecu_config_mod.FEATURES
            if feat.is_program_feature and feat.name in self._config_combos
        }

    def _config_live_program_changes(self):
        """Return program controls whose selected value differs from the live baseline."""
        if not getattr(self, "_config_live_values_read", False):
            return {}
        baseline = getattr(self, "_config_live_baseline", {})
        placeholder = getattr(
            self, "_config_live_program_placeholder", "(full ROM read on write)")
        changed = {}
        for name, original in baseline.items():
            current = self._config_combos[name].currentText()
            if current != original and current != placeholder:
                changed[name] = (original, current)
        return changed

    def _set_config_program_placeholder(self, name):
        """Expose a writable program control whose current value needs a later full read."""
        cb = self._config_combos[name]
        if not cb.isEnabled():
            return False
        placeholder = self._config_live_program_placeholder
        cb.blockSignals(True)
        try:
            if cb.findText(placeholder) < 0:
                cb.insertItem(0, placeholder)
            cb.setCurrentText(placeholder)
            cb.setEnabled(True)
        finally:
            cb.blockSignals(False)
        return True

    def _configure_config_combos(self, profile, target_size=None, preserve=True,
                                 program_variant=None,
                                 program_gate_present=False):
        """Rebuild CAL-ID-specific choices for the current file/live target."""
        import ecu_config as _ec

        gate_feature = next(
            feat for feat in _ec.FEATURES
            if feat.name == "O2 Feedback Program Gate (Experimental)"
        )
        gate_supported = gate_feature.addr(
            0, target_size, profile, program_variant) is not None
        program_gate_present = bool(program_gate_present and gate_supported)

        for feat in _ec.FEATURES:
            cb = self._config_combos.get(feat.name)
            if cb is None:
                continue
            previous = cb.currentText() if preserve else ""
            cb.blockSignals(True)
            cb.clear()

            options = feat.options_for(
                profile, target_size, program_gate_present)
            full_file_required = (feat.full_file_only
                                  and target_size != MS41ECU.FULL_ROM_SIZE)
            not_in_partial = (feat.abs_addr is not None
                              and target_size == MS41ECU.TUNE_SIZE)
            program_mismatch = (
                feat.profile_abs_addrs is not None
                and target_size == MS41ECU.FULL_ROM_SIZE
                and bool(options)
                and feat.addr(0, target_size, profile, program_variant) is None
            )
            if full_file_required:
                cb.addItem("(full ROM file required)")
                cb.setEnabled(False)
            elif not_in_partial:
                cb.addItem("(not in partial)")
                cb.setEnabled(False)
            elif feat.is_profile_specific and not options:
                if profile is None:
                    cb.addItem("(CAL ID required)")
                else:
                    cb.addItem(f"(not available for {profile})")
                cb.setEnabled(False)
            elif program_mismatch:
                cb.addItem("(program/CAL mismatch)")
                cb.setEnabled(False)
            else:
                cb.addItems([label for label, _ in options])
                if previous and cb.findText(previous) >= 0:
                    cb.setCurrentText(previous)
                cb.setEnabled(True)
            cb.blockSignals(False)

        self._config_profile = profile
        self._config_program_variant = program_variant
        self._config_target_size = target_size
        self._config_o2_gate_present = program_gate_present
        self._config_o2_pre_experimental_choice = (
            self._config_combos["Oxygen Sensors"].currentText()
            if program_gate_present else None
        )

    def _on_config_o2_program_gate_changed(self, text):
        """Expose the experimental CAL choice only while its program gate is selected."""
        import ecu_config as _ec

        profile = getattr(self, "_config_profile", None)
        target_size = getattr(self, "_config_target_size", None)
        program_variant = getattr(self, "_config_program_variant", None)
        gate_feature = next(
            feat for feat in _ec.FEATURES
            if feat.name == "O2 Feedback Program Gate (Experimental)"
        )
        gate_supported = gate_feature.addr(
            0, target_size, profile, program_variant) is not None
        present = bool(gate_supported and text == "Feedback Disabled")

        oxygen = self._config_combos.get("Oxygen Sensors")
        if oxygen is None:
            return
        experimental = "Disabled (Experimental)"
        custom_disabled = "Custom (0x04)"
        existing = oxygen.findText(experimental)
        was_present = getattr(self, "_config_o2_gate_present", False)

        oxygen.blockSignals(True)
        try:
            if present:
                if not was_present:
                    self._config_o2_pre_experimental_choice = oxygen.currentText()
                if existing < 0:
                    oxygen.addItem(experimental)
                if oxygen.currentText() == custom_disabled:
                    oxygen.setCurrentText(experimental)
                    custom_index = oxygen.findText(custom_disabled)
                    if custom_index >= 0:
                        oxygen.removeItem(custom_index)
            elif existing >= 0:
                if oxygen.currentText() == experimental:
                    fallback = self._config_o2_pre_experimental_choice
                    if not fallback:
                        fallback = custom_disabled
                    if oxygen.findText(fallback) < 0:
                        oxygen.addItem(fallback)
                    oxygen.setCurrentText(fallback)
                oxygen.removeItem(oxygen.findText(experimental))
        finally:
            oxygen.blockSignals(False)

        self._config_o2_gate_present = present

    @staticmethod
    def _config_transfer_details(skipped):
        if not skipped:
            return ""
        return "\n\nNot copied:\n" + "\n".join(f"  - {line}" for line in skipped)

    def _load_config_from_path(self, path):
        """Load a ROM/partial file into the editor and enter FILE mode.

        Shared by the Load button and the Backups tab's "Edit Config" button.
        """
        with open(path, "rb") as f:
            data = bytearray(f.read())
        profile = self._ecu_config_mod.detect_control_bit_profile(data)
        cfg = self._ecu_config_mod.read_config(data, profile=profile)
        if cfg is None:
            QMessageBox.critical(self, "Unsupported File",
                "Control Bits are in the calibration region — load a 256 KB full ROM "
                "or a 24 KB partial.")
            return
        self._config_data = data
        self._config_path = path
        # FILE mode replaces the live editor state. Require another Read from ECU
        # after the file is closed before program-region live edits are offered.
        self._reset_live_config_state()
        program_variant = (MS41ECU.detect_program_variant(data)
                           if len(data) == MS41ECU.FULL_ROM_SIZE else None)
        program_gate_present = (
            self._ecu_config_mod.experimental_o2_program_gate_present(
                data, profile, program_variant)
        )
        self._configure_config_combos(
            profile, len(data), preserve=False,
            program_variant=program_variant,
            program_gate_present=program_gate_present)
        for name, cb in self._config_combos.items():
            if name not in cfg:
                # Not present in this image, or profile-specific and unsafe to decode.
                continue
            cur = cfg.get(name, "")
            idx = cb.findText(cur)
            if idx >= 0:
                cb.setCurrentIndex(idx)
            else:
                # current value isn't one of the known options — add it so nothing is lost
                cb.addItem(cur)
                cb.setCurrentText(cur)
        if program_gate_present:
            oxygen_text = self._config_combos["Oxygen Sensors"].currentText()
            self._config_o2_pre_experimental_choice = (
                "Custom (0x04)"
                if oxygen_text == "Disabled (Experimental)" else oxygen_text
            )
        kind = "Full ROM" if len(data) == MS41ECU.FULL_ROM_SIZE else "Partial"
        profile_label = profile or "Unknown CAL-ID family"
        self.lbl_config_file.setText(
            f"{os.path.basename(path)}  ({profile_label}, {kind})")
        self.lbl_config_file.setStyleSheet("color:#7ec8e3; font-style:normal;")
        self._update_config_buttons()
        self._log(
            f"Config loaded: {os.path.basename(path)} ({profile_label}, {kind})", "ok")

    def _on_config_close(self):
        """Close the loaded file and return to ECU mode."""
        if self._config_data is None:
            return
        name = os.path.basename(self._config_path) if self._config_path else "file"
        self._config_data = None
        self._config_path = None
        # Return to the connected ECU's profile. Oxygen editing stays locked if
        # its running firmware has not been established.
        self._configure_config_combos(
            self._live_control_bit_profile(), target_size=None, preserve=False,
            program_variant=self._live_config_program_variant())
        self.lbl_config_file.setText("No file loaded")
        self.lbl_config_file.setStyleSheet("color:#888; font-style:italic;")
        self._update_config_buttons()
        self._log(f"Closed config file: {name} — ECU operations re-enabled.", "ok")

    def _update_config_buttons(self):
        """Single source of truth for the ECU Config tab's button states.

        FILE operations are live only when a file is loaded; ECU operations only
        when no file is loaded (the ECU is then the unambiguous target).  Both
        are gated by connection / task-busy state.
        """
        busy        = getattr(self, "_task_busy", False)
        file_loaded = self._config_data is not None
        connected   = getattr(self, "_ds2", None) is not None
        ok = not busy
        if not file_loaded and hasattr(self, "_config_combos"):
            live_profile = self._live_control_bit_profile()
            live_program_variant = self._live_config_program_variant()
            live_target_size = (
                MS41ECU.FULL_ROM_SIZE
                if getattr(self, "_config_live_values_read", False) else None)
            if (live_profile != getattr(self, "_config_profile", None)
                    or live_program_variant != getattr(
                        self, "_config_program_variant", None)
                    or getattr(self, "_config_target_size", None) != live_target_size):
                self._configure_config_combos(
                    live_profile, target_size=live_target_size, preserve=True,
                    program_variant=live_program_variant)
        # Load is always available (when idle); it's the entry into FILE mode.
        self.btn_config_load.setEnabled(ok)
        # FILE operations — editing/saving a file works offline (no ECU needed).
        self.btn_config_close.setEnabled(ok and file_loaded)
        self.btn_config_save.setEnabled(ok and file_loaded)
        self.btn_config_copy_ecu.setEnabled(ok and file_loaded and connected)
        # ECU operations — only when no file is loaded and we're connected.
        self.btn_config_read_ecu.setEnabled(ok and (not file_loaded) and connected)
        self.btn_config_get_file.setEnabled(ok and (not file_loaded) and connected)
        self.btn_config_write_ecu.setEnabled(ok and (not file_loaded) and connected)

    def _set_config_combo(self, name, label, profile=None, allow_profile_custom=False):
        """Set one config dropdown to `label`, adding it if it's a custom value.
        Returns True if the combo exists."""
        import ecu_config as _ec

        cb = self._config_combos.get(name)
        if cb is None:
            return False
        feat = next((item for item in _ec.FEATURES if item.name == name), None)
        if feat is None:
            return False
        if profile is None:
            profile = getattr(self, "_config_profile", None)
        target_size = getattr(self, "_config_target_size", None)
        program_variant = getattr(self, "_config_program_variant", None)
        valid_labels = [item_label for item_label, _
                        in feat.options_for(
                            profile, target_size,
                            getattr(self, "_config_o2_gate_present", False))]
        if (feat.is_profile_specific and label not in valid_labels
                and not (allow_profile_custom and label.startswith("Custom (0x"))):
            return False
        if feat.full_file_only and target_size != MS41ECU.FULL_ROM_SIZE:
            return False
        if feat.abs_addr is not None and target_size == MS41ECU.TUNE_SIZE:
            return False
        if (feat.profile_abs_addrs is not None
                and feat.addr(0, target_size, profile, program_variant) is None):
            return False
        if cb.findText(label) < 0:
            cb.addItem(label)
        cb.setCurrentText(label)
        cb.setEnabled(True)
        idx = cb.findText("(not in partial)")
        if idx >= 0:
            cb.removeItem(idx)
        idx = cb.findText("(firmware profile required)")
        if idx >= 0:
            cb.removeItem(idx)
        for placeholder in ("(CAL ID required)", "(full ROM file required)",
                            "(program/CAL mismatch)"):
            idx = cb.findText(placeholder)
            if idx >= 0:
                cb.removeItem(idx)
        return True

    def _ecu_read_config_task(self):
        """Return a task that reads calibration and supported program config bytes.

        Result is (raw_cal_bytes, prog_crc_byte_or_None, o2_gate_byte_or_None).
        Shared by Read from ECU and Copy flags from ECU.
        """
        CAL_DS2_BASE = 0x10000   # 24 KB calibration region start (= PARTIAL_DS2_ADDR)
        profile = self._live_control_bit_profile()
        program_variant = self._live_config_program_variant()
        gate_feature = next(
            feat for feat in self._ecu_config_mod.FEATURES
            if feat.name == "O2 Feedback Program Gate (Experimental)"
        )
        gate_file_addr = gate_feature.addr(
            0, MS41ECU.FULL_ROM_SIZE, profile, program_variant)

        def task(log_fn, progress_fn):
            log_fn("Reading ECU control bytes 4–8 (DS2 0x10004–0x10008)…")
            raw = self._ds2.read_mem(CAL_DS2_BASE + 4, 5)   # bytes 4,5,6,7,8
            log_fn(f"Control bytes: {raw.hex(' ').upper()}")
            # Program CRC switch: file 0x605C = DS2 0x205C (file block 1 → DS2 block 0)
            prog_crc_byte = None
            try:
                pb = self._ds2.read_mem(0x205C, 1)
                prog_crc_byte = pb[0]
                log_fn(f"Program CRC byte (DS2 0x205C): 0x{prog_crc_byte:02X}")
            except Exception:
                pass
            o2_gate_byte = None
            if gate_file_addr is not None:
                gate_ds2_addr = gate_file_addr ^ 0x4000
                try:
                    pb = self._ds2.read_mem(gate_ds2_addr, 1)
                    o2_gate_byte = pb[0]
                    log_fn(
                        f"O2 feedback program gate (file 0x{gate_file_addr:05X}, "
                        f"DS2 0x{gate_ds2_addr:05X}): 0x{o2_gate_byte:02X}")
                except Exception as error:
                    log_fn(
                        f"O2 feedback program gate could not be read directly: {error}",
                        "warn")
            return raw, prog_crc_byte, o2_gate_byte

        return task

    def _apply_ecu_config_result(self, result, source_profile=None, target_profile=None,
                                 source_target_size=None,
                                 source_program_gate_present=False):
        """Populate the dropdowns from an _ecu_read_config_task result.
        Returns (updated_lines, skipped_lines). Profile-specific values are copied
        by semantic label, never by raw control bits."""
        import ecu_config
        if source_profile is None:
            source_profile = self._live_control_bit_profile()
        if target_profile is None:
            target_profile = getattr(self, "_config_profile", None)
        raw = result[0]
        prog_crc_byte = result[1] if len(result) > 1 else None
        o2_gate_byte = result[2] if len(result) > 2 else None
        if o2_gate_byte is not None:
            source_target_size = MS41ECU.FULL_ROM_SIZE
            source_program_gate_present = o2_gate_byte == 0x11
        byte_vals = {4 + i: raw[i] for i in range(len(raw))}   # byte4..byte8
        updated = []
        skipped = []

        # Apply the live program gate before decoding Byte 6. Its value changes
        # whether ID12/ID60 0x04 is a true disabled state or merely an unknown mode.
        gate_feature = next(
            feat for feat in ecu_config.FEATURES
            if feat.name == "O2 Feedback Program Gate (Experimental)"
        )
        if o2_gate_byte is not None:
            gate_label = gate_feature.current(
                o2_gate_byte, source_profile, source_target_size,
                source_program_gate_present)
            if self._set_config_combo(
                    gate_feature.name,
                    gate_label,
                    target_profile,
                    allow_profile_custom=(source_profile == target_profile)):
                gate_addr = gate_feature.addr(
                    0, MS41ECU.FULL_ROM_SIZE, source_profile,
                    self._live_config_program_variant())
                where = (f"0x{gate_addr:05X}" if gate_addr is not None
                         else "program gate")
                updated.append(
                    f"  {gate_feature.name}: {gate_label}  "
                    f"({where} = 0x{o2_gate_byte:02X})")

        for feat in ecu_config.FEATURES:
            if feat.is_program_feature:
                if feat.name == gate_feature.name:
                    continue
                # Program CRC lives in the program region — read separately above.
                if feat.name == "Program CRC Check" and prog_crc_byte is not None:
                    label = feat.current(
                        prog_crc_byte, source_profile, source_target_size,
                        source_program_gate_present)
                    if self._set_config_combo(feat.name, label, target_profile):
                        updated.append(
                            f"  {feat.name}: {label}  (0x605C = 0x{prog_crc_byte:02X})")
                continue
            bval = byte_vals.get(feat.byte)
            if bval is None:
                continue
            if feat.is_profile_specific:
                source_options = feat.options_for(
                    source_profile, source_target_size,
                    source_program_gate_present)
                target_options = feat.options_for(
                    target_profile, getattr(self, "_config_target_size", None),
                    getattr(self, "_config_o2_gate_present", False))
                if not source_options:
                    skipped.append(
                        f"{feat.name}: source firmware profile is unknown")
                    continue
                label = feat.current(
                    bval, source_profile, source_target_size,
                    source_program_gate_present)
                if not target_options:
                    skipped.append(
                        f"{feat.name}: target firmware profile is unknown")
                    continue
                if (label not in [option_label for option_label, _ in target_options]
                        and not (source_profile == target_profile
                                 and label.startswith("Custom (0x"))):
                    skipped.append(
                        f"{feat.name}: {label} has no {target_profile} equivalent")
                    continue
            else:
                label = feat.current(
                    bval, source_profile, source_target_size,
                    source_program_gate_present)
            if self._set_config_combo(
                    feat.name,
                    label,
                    target_profile,
                    allow_profile_custom=(source_profile == target_profile)):
                updated.append(f"  {feat.name}: {label}  (Byte {feat.byte} = 0x{bval:02X})")
        return updated, skipped

    def _apply_live_config_read(self, result):
        """Enter live-edit mode after the small control-byte read completes."""
        profile = self._live_control_bit_profile()
        cached_full = self._current_full_read_for_connection()
        live_gate_byte = result[2] if len(result) > 2 else None
        source_gate_present = False

        if cached_full is not None:
            program_variant = (MS41ECU.detect_program_variant(cached_full)
                               or self._live_config_program_variant())
            source_gate_present = (
                self._ecu_config_mod.experimental_o2_program_gate_present(
                    cached_full, profile, program_variant))
            self._configure_config_combos(
                profile, target_size=MS41ECU.FULL_ROM_SIZE, preserve=False,
                program_variant=program_variant,
                program_gate_present=source_gate_present)
            cached_cfg = self._ecu_config_mod.read_config(
                cached_full, profile=profile) or {}
            for name, label in cached_cfg.items():
                self._set_config_combo(
                    name, label, profile,
                    allow_profile_custom=True)
            source_note = "matching current-session full read"
        else:
            program_variant = self._live_config_program_variant()
            if live_gate_byte is not None:
                source_gate_present = live_gate_byte == 0x11
            self._configure_config_combos(
                profile, target_size=MS41ECU.FULL_ROM_SIZE, preserve=False,
                program_variant=program_variant,
                program_gate_present=source_gate_present)
            source_note = "live control/program bytes; full ROM deferred until required"

        # The direct byte is newer evidence than a cached image if both exist.
        if live_gate_byte is not None:
            source_gate_present = live_gate_byte == 0x11

        updated, skipped = self._apply_ecu_config_result(
            result,
            source_profile=profile,
            target_profile=profile,
            source_target_size=MS41ECU.FULL_ROM_SIZE,
            source_program_gate_present=source_gate_present,
        )

        # Preserve the guarded full-read fallback if this optional small read is
        # unavailable and no current-connection full image can supply the value.
        if cached_full is None and live_gate_byte is None:
            self._set_config_program_placeholder(
                "O2 Feedback Program Gate (Experimental)")
        if cached_full is None:
            if result[1] is None:
                self._set_config_program_placeholder("Program CRC Check")

        self._config_live_values_read = True
        self._config_live_baseline = self._config_program_baseline()
        self.lbl_config_file.setText(f"Live ECU values ({source_note})")
        self.lbl_config_file.setStyleSheet("color:#c8a85f; font-style:normal;")
        self._log("Control bytes read from ECU:", "ok")
        for line in updated:
            self._log(line)
        for line in skipped:
            self._log(f"  Not decoded: {line}", "warn")
        if live_gate_byte is not None:
            self._log(
                "O2 feedback program gate populated from its live DS2 program byte.",
                "ok")
        elif cached_full is not None:
            self._log(
                "Program-region values populated from this connection's archived full read.",
                "ok")
        else:
            self._log(
                "Program-region editing is available. A changed program setting will "
                "trigger and archive a full ECU read before flashing.", "warn")

    def _on_config_read_ecu(self):
        """ECU mode: read the live control bytes into the dropdowns (ECU is the target)."""
        if not self._ds2:
            return
        self._run_task(
            self._ecu_read_config_task(), on_success=self._apply_live_config_read)

    def _on_config_copy_from_ecu(self):
        """FILE mode: copy the ECU's live feature flags into the dropdowns, keeping
        the loaded file as the save target."""
        if not self._ds2 or self._config_data is None:
            return

        def on_success(result):
            source_profile = self._live_control_bit_profile()
            target_profile = self._config_profile
            updated, skipped = self._apply_ecu_config_result(
                result,
                source_profile=source_profile,
                target_profile=target_profile,
            )
            self._log("Copied feature flags from ECU into the loaded file's editor:", "ok")
            for line in updated:
                self._log(line)
            for line in skipped:
                self._log(f"  Not copied: {line}", "warn")
            message = (
                f"Imported {len(updated)} feature flag(s) from the connected ECU.\n\n"
                "These overwrite the dropdowns only — maps and the rest of the file are "
                "untouched. Use Apply & Save to write the edited file to disk."
                + self._config_transfer_details(skipped))
            show = QMessageBox.warning if skipped else QMessageBox.information
            show(self, "Flags Copied from ECU", message)

        self._run_task(self._ecu_read_config_task(), on_success=on_success)

    def _on_config_get_from_file(self):
        """ECU mode: load a file's feature flags into the dropdowns (ECU stays the
        write target).  Copies the on/off flags only — never maps or the full tune."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Get feature flags from ROM / partial file", "",
            "Binary Files (*.bin);;All Files (*)")
        if not path:
            return
        with open(path, "rb") as f:
            data = bytearray(f.read())
        source_profile = self._ecu_config_mod.detect_control_bit_profile(data)
        target_profile = self._live_control_bit_profile()
        cfg = self._ecu_config_mod.read_config(data, profile=source_profile)
        if cfg is None:
            QMessageBox.critical(self, "Unsupported File",
                "Control Bits are in the calibration region — pick a 256 KB full ROM "
                "or a 24 KB partial.")
            return
        live_full_mode = getattr(self, "_config_live_values_read", False)
        target_size = MS41ECU.FULL_ROM_SIZE if live_full_mode else None
        imported_gate_disabled = (
            cfg.get("O2 Feedback Program Gate (Experimental)")
            == "Feedback Disabled")
        self._configure_config_combos(
            target_profile, target_size=target_size, preserve=True,
            program_variant=self._live_config_program_variant(),
            program_gate_present=(live_full_mode and imported_gate_disabled))
        import ecu_config as _ec
        updated = []
        skipped = []
        for feat in _ec.FEATURES:
            # Program flags become writable only after Read from ECU established
            # the live baseline and enabled the guarded full-ROM route.
            if feat.is_program_feature and not live_full_mode:
                continue
            label = cfg.get(feat.name)
            if label is None:
                if feat.is_profile_specific:
                    skipped.append(
                        f"{feat.name}: source firmware profile is unknown")
                continue
            if feat.is_profile_specific:
                target_labels = [item_label for item_label, _
                                 in feat.options_for(
                                     target_profile, target_size,
                                     imported_gate_disabled)]
                if not target_labels:
                    skipped.append(
                        f"{feat.name}: target firmware profile is unknown")
                    continue
                if label not in target_labels:
                    skipped.append(
                        f"{feat.name}: {label} has no {target_profile} equivalent")
                    continue
            if self._set_config_combo(feat.name, label, target_profile):
                updated.append(f"  {feat.name}: {label}")
        self.lbl_config_file.setText(
            f"Flags from {os.path.basename(path)}  →  Write to ECU")
        self.lbl_config_file.setStyleSheet("color:#c8a85f; font-style:normal;")
        self._log(f"Imported feature flags from file: {os.path.basename(path)}", "ok")
        for line in updated:
            self._log(line)
        for line in skipped:
            self._log(f"  Not copied: {line}", "warn")
        message = (
            f"Imported {len(updated)} feature flag(s) from:\n{os.path.basename(path)}\n\n"
            "This copies the on/off flags ONLY — not maps or file contents. Use "
            "Write to ECU to apply them to the connected ECU. Program-region changes "
            "use the guarded full-ROM path after Read from ECU."
            + self._config_transfer_details(skipped))
        show = QMessageBox.warning if skipped else QMessageBox.information
        show(self, "Flags Loaded from File", message)

    def _on_config_save(self):
        if self._config_data is None: return
        changes = {name: cb.currentText() for name, cb in self._config_combos.items()}
        patched, log = self._ecu_config_mod.apply_config(
            self._config_data, changes, profile=self._config_profile)
        for l in log:
            self._log(l)
        # Recompute checksums (variant-gated, like the rest of the tool)
        if self.chk_config_fix.isChecked():
            variant = MS41ECU.detect_variant(patched)
            do_program = not (variant == "MS41.3" and len(patched) == MS41ECU.FULL_ROM_SIZE)
            patched, cdet = correct_checksums(patched, correct_program=do_program)
            for d in cdet:
                self._log(d)
            if not do_program:
                if patched[0x605C] == 0xFF:
                    self._log(
                        "MS41.3: boot and calibration checksums corrected; program checksum "
                        "left unchanged because stock program verification is disabled.",
                        "ok",
                    )
                else:
                    self._log(
                        "MS41.3: boot and calibration checksums corrected; program checksum "
                        "left unchanged, but program verification is enabled in this image.",
                        "warn",
                    )
        stem = os.path.splitext(os.path.basename(self._config_path))[0]
        out, _ = QFileDialog.getSaveFileName(
            self, "Save configured image", f"{stem}_config.bin",
            "Binary Files (*.bin);;All Files (*)"
        )
        if not out: return
        with open(out, "wb") as f:
            f.write(patched)
        self._log(f"Configured image saved → {os.path.basename(out)}", "ok")
        QMessageBox.information(self, "Configured Image Saved",
            f"Saved configured image:\n{os.path.basename(out)}")

    def _on_config_write_ecu(self):
        """Apply live config by partial write, or by full read/patch/write if needed."""
        if self._ds2 is None:
            QMessageBox.information(self, "Not Connected",
                "Connect to the ECU over DS2 first.")
            return

        program_changes = self._config_live_program_changes()
        needs_full = bool(program_changes)
        if (self._config_combos["Oxygen Sensors"].currentText()
                == "Disabled (Experimental)"):
            # This calibration choice is only valid together with a full-ROM
            # program gate, even when that gate was already disabled at read time.
            needs_full = True

        if needs_full:
            requested_changes = {
                name: cb.currentText()
                for name, cb in self._config_combos.items()
            }
            cached_full = self._current_full_read_for_connection()
            if cached_full is not None:
                self._log(
                    "Config program edit: reusing this connection's archived full read; "
                    "no additional ECU read is required.", "ok")
                self._config_write_apply_full(
                    bytearray(cached_full),
                    requested_changes=requested_changes)
                return

            change_names = list(program_changes)
            if not change_names:
                change_names = ["Oxygen Sensors = Disabled (Experimental)"]
            details = "\n".join(f"  • {name}" for name in change_names)
            if QMessageBox.warning(
                    self, "Full ROM Read Required",
                    "This configuration changes the program region and cannot be "
                    "written as a 24 KB calibration-only update.\n\n"
                    f"Full-write trigger:\n{details}\n\n"
                    "BimmerStein ECU Tool will:\n"
                    "  1. Read the ECU's current full 256 KB ROM.\n"
                    "  2. Archive that unmodified image in Bins.\n"
                    "  3. Patch only the selected configuration fields.\n"
                    "  4. Pass the result to the normal guarded full-ROM writer.\n\n"
                    "Continue with the full read?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No) != QMessageBox.Yes:
                self._log("Config full-ROM read cancelled.", "warn")
                return

            def full_read_task(log_fn, progress_fn):
                log_fn("Reading current full 256 KB ROM for program config edit…")
                return bytes(self._read_image_auto("full", log_fn, progress_fn))

            def full_read_success(data):
                try:
                    entry = self._record_full_ecu_read(
                        data, source="ECU read (pre-config-full-write)")
                except Exception as error:
                    self._log(
                        f"Config full write aborted — the source ROM could not be archived: "
                        f"{error}", "error")
                    QMessageBox.critical(
                        self, "Full ROM Archive Failed",
                        "The ECU read completed, but its unmodified full image could not "
                        f"be archived in Bins:\n\n{error}\n\n"
                        "Nothing was patched or written.")
                    return
                self._log(
                    f"Unmodified pre-write full ROM archived: {entry.filename}", "ok")
                # Let the read worker restore the GUI's idle state before the full
                # writer starts its own guarded worker/port handoff.
                source_image = bytes(data)
                QTimer.singleShot(
                    0, lambda: self._config_write_apply_full(
                        bytearray(source_image),
                        requested_changes=requested_changes))

            self._run_task(full_read_task, on_success=full_read_success)
            return

        def task(log_fn, progress_fn):
            log_fn("Reading current 24 KB tune from ECU (for config edit)…")
            return self._read_image_auto("tune", log_fn, progress_fn)

        self._run_task(task, on_success=lambda d: self._config_write_apply(bytearray(d)))

    def _config_write_apply_full(self, full_rom, requested_changes=None):
        """Patch an archived live full read and hand it to the guarded full writer."""
        import ecu_config

        if len(full_rom) != MS41ECU.FULL_ROM_SIZE:
            QMessageBox.critical(
                self, "ECU Config Read Failed",
                f"Expected a 256 KB full ROM, got {len(full_rom):,} bytes.")
            return

        profile = self._live_control_bit_profile()
        source_profile = ecu_config.detect_control_bit_profile(full_rom)
        program_variant = MS41ECU.detect_program_variant(full_rom)
        live_program_variant = self._live_config_program_variant()
        mismatches = []
        if profile and source_profile and profile != source_profile:
            mismatches.append(
                f"CAL-ID control profile: ECU {profile}, full read {source_profile}")
        if (live_program_variant and program_variant
                and live_program_variant != program_variant):
            mismatches.append(
                f"program variant: ECU {live_program_variant}, full read {program_variant}")
        if mismatches:
            detail = "\n".join(f"  • {line}" for line in mismatches)
            QMessageBox.critical(
                self, "Live Full ROM Mismatch — Write Blocked",
                "The full image selected as the live configuration base does not match "
                f"the connected ECU:\n\n{detail}\n\nNothing was written.")
            self._log(
                "Config full write blocked — the full-read identity does not match the "
                "connected ECU.", "error")
            return

        changes = (dict(requested_changes) if requested_changes is not None else {
            name: cb.currentText() for name, cb in self._config_combos.items()
        })
        patched, change_log = ecu_config.apply_config(
            full_rom, changes, profile=profile)
        if patched == full_rom:
            QMessageBox.information(
                self, "No Changes",
                "The selected configuration already matches the full ECU image — "
                "nothing to write.")
            return

        actual_changes = [line for line in change_log if line != "No changes."]
        preview = "\n".join(f"  • {line}" for line in actual_changes)
        if QMessageBox.warning(
                self, "Confirm Full-ROM Config Patch",
                "These configuration changes will be applied to the ECU's archived "
                f"full read:\n\n{preview}\n\n"
                "This requires a full ROM flash. The normal checksum, flash-family, "
                "boot-region, backup, transport, and verification safeguards will run "
                "next. Continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No) != QMessageBox.Yes:
            self._log("Config full write cancelled after preview.", "warn")
            return

        if self.chk_config_fix.isChecked():
            variant = MS41ECU.detect_variant(patched)
            correct_program = not (variant == "MS41.3")
            patched, checksum_log = correct_checksums(
                patched, correct_program=correct_program)
            for line in checksum_log:
                self._log(line)

        archived_source = bytes(full_rom)

        def update_program_baseline():
            self._config_live_values_read = True
            self._config_live_baseline = {
                feat.name: changes[feat.name]
                for feat in self._ecu_config_mod.FEATURES
                if feat.is_program_feature and feat.name in changes
            }
            self._log(
                "Live program-configuration baseline updated after successful full write.",
                "ok")

        self._ds2_write_full(
            bytearray(patched), "live_ECU_config_full.bin",
            archived_prewrite_image=archived_source,
            on_write_success=update_program_baseline)

    def _config_write_apply(self, partial):
        """Build the patched tune, preview the diff, then partial-write it back."""
        import ecu_config
        if len(partial) != MS41ECU.TUNE_SIZE:
            QMessageBox.critical(self, "ECU Config Read Failed",
                f"Expected a 24 KB tune from the ECU, got {len(partial):,} bytes.")
            return

        profile = self._live_control_bit_profile()
        changes = {name: cb.currentText() for name, cb in self._config_combos.items()}
        patched, _log = ecu_config.apply_config(partial, changes, profile=profile)
        diff = self._config_diff(partial, patched, profile=profile)
        if not diff:
            QMessageBox.information(self, "No Changes",
                "The selected control bits already match the ECU — nothing to write.")
            return

        # Config bytes are covered by the calibration checksum — recompute it.
        patched, cdet = correct_checksums(patched, correct_program=False)
        for d in cdet:
            self._log(d)

        # Optional pre-write backup of the tune we just read (already in memory).
        ans = QMessageBox.question(self, "Back Up First?",
            "Back up the ECU's current tune before writing this change?",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.Yes)
        if ans == QMessageBox.Cancel:
            self._log("Config write cancelled.", "warn")
            return
        if ans == QMessageBox.Yes:
            try:
                entry = self._backup_save_bytes(partial, "tune",
                                                source="ECU read (pre-config-write)")
                self._refresh_backup_table()
                self._log(f"Pre-write backup saved: {entry.filename}", "ok")
            except Exception as e:
                if QMessageBox.question(self, "Backup Failed",
                    f"Could not save a backup:\n{e}\n\nWrite anyway?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                    return

        lines = "".join(
            f"  • {f}: {ol} → {nl}   (Byte {byte}: 0x{old:02X} → 0x{new:02X})\n"
            for (f, byte, old, new, ol, nl) in diff)
        if QMessageBox.warning(self, "Confirm Config Write",
            "These control-bit changes will be flashed to the ECU:\n\n" + lines +
            "\nThis ERASES and rewrites the 24 KB tune sector.\n"
            "Ignition ON, engine OFF. Do not cut power during the write.\n\nProceed?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            self._log("Config write cancelled.", "warn")
            return

        image = bytes(patched)

        # The archived full image remains a restore point, but this calibration
        # write means it is no longer an exact source for a later full-ROM edit.
        self._invalidate_current_full_read("calibration configuration write started")

        verify_write = self.chk_verify.isChecked()

        def task(log_fn, progress_fn):
            log_fn("Writing config-modified tune to ECU…")
            self._write_tune_auto(
                image,
                log_fn,
                progress_fn,
                verify_write=verify_write,
            )
            return "Config written to ECU — cycle ignition to apply."

        def on_success(msg):
            self._finish_flash_success("Config Written", msg)

        def on_failure(err):
            if isinstance(err, StockWriteNotStarted):
                self._log(f"Config write not started: {err}", "warn")
                QMessageBox.warning(self, "ECU Config Write Not Started", str(err))
                return
            self._log(f"Config write FAILED: {err}", "error")
            if self._offer_active_flash_recovery(
                    f"The config/tune write failed after erase began:\n{err}"):
                return
            QMessageBox.critical(self, "ECU Config Write Failed",
                f"Config write failed:\n{err}\n\nIf interrupted mid-write, re-flash "
                f"the tune before cycling ignition.")

        self._run_task(task, on_success=on_success, on_failure=on_failure)

    def _config_diff(self, orig, patched, profile=None):
        """(feature, byte, old, new, old_label, new_label) for each changed control bit.

        24 KB partial → cal base is 0, so the control byte index == feature.byte.
        """
        import ecu_config
        out = []
        for feat in ecu_config.FEATURES:
            if feat.is_program_feature:
                continue
            a = feat.byte
            if a >= len(orig) or a >= len(patched):
                continue
            old, new = orig[a], patched[a]
            ol = feat.current(old, profile, len(orig))
            nl = feat.current(new, profile, len(patched))
            if ol != nl:
                out.append((feat.name, feat.byte, old, new, ol, nl))
        return out

    # ── Partial / Full tab ───────────────────────────────────────────────

    def _build_partial_tab(self):
        tab = QWidget()
        lay = QVBoxLayout(tab)

        note = QLabel(
            "Convert between a <b>full 256 KB ROM</b> and a <b>24 KB partial</b> "
            "(the ECU's CPU/DS2-order tune partition, DS2 0x10000–0x15FFF).<br>"
            "• <b>Extract</b> pulls the 24 KB calibration partial out of a full read — "
            "useful for editing in RomRaider.<br>"
            "• <b>Merge</b> injects an edited 24 KB partial back into a full ROM and "
            "recomputes all checksums, producing a flash-ready full image."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#aaa; padding:6px;")
        lay.addWidget(note)

        row = QHBoxLayout()
        btn_extract = self._op_btn("📤  Extract Partial from Full…", "#1e5080",
                                   self._on_extract_partial)
        btn_extract.setMinimumWidth(260)
        btn_merge = self._op_btn("📥  Merge Partial into Full…", "#3d6b35",
                                 self._on_merge_partial)
        btn_merge.setMinimumWidth(260)
        row.addWidget(btn_extract)
        row.addWidget(btn_merge)
        row.addStretch()
        lay.addLayout(row)

        self.chk_merge_fix = QCheckBox("Correct checksums when merging (recommended)")
        self.chk_merge_fix.setChecked(True)
        self.chk_merge_fix.setStyleSheet("color:#aaa; padding:4px;")
        lay.addWidget(self.chk_merge_fix)

        info = QLabel(
            "The partial is the ECU's CPU/DS2-order tune partition (DS2 0x10000–0x15FFF), "
            "descrambled from the full ROM — byte-identical to a live tune read, so it edits "
            "in RomRaider and writes back to the ECU. (file = CPU XOR 0x4000 per 16 KB, so it "
            "is NOT a plain file slice — the two 16 KB halves are swapped.)\n"
            "Merging scatters an edited partial back into the donor full ROM, leaves the rest "
            "unchanged, then fixes the calibration / program / boot checksums.\n"
            "These are offline file operations — no ECU connection required."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#777; padding:6px; font-size:10px;")
        lay.addWidget(info)
        lay.addStretch()

        self.tabs.addTab(tab, "  Partial / Full  ")

    def _on_extract_partial(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select FULL 256 KB ROM", "", "Binary Files (*.bin);;All Files (*)"
        )
        if not path: return
        with open(path, "rb") as f:
            data = f.read()
        if len(data) != MS41ECU.FULL_ROM_SIZE:
            QMessageBox.critical(self, "Wrong Size",
                f"Expected a 256 KB full ROM, got {len(data):,} bytes.")
            return
        # CPU/DS2-order descramble (NOT a file slice) — matches ds2.read_partial and
        # RomRaider; a plain data[0x14000:0x1A000] drops the extended AlphaN + SS1v2 high-cal.
        partial = MS41ECU.tune_from_full(data)
        variant = MS41ECU.detect_variant(data) or "Unknown"
        calid   = MS41ECU.read_calid(data) or "????"
        stem    = os.path.splitext(os.path.basename(path))[0]
        out, _  = QFileDialog.getSaveFileName(
            self, "Save 24 KB Partial", f"{stem}_partial.bin",
            "Binary Files (*.bin);;All Files (*)"
        )
        if not out: return
        with open(out, "wb") as f:
            f.write(partial)
        self.tabs.setCurrentIndex(0)  # bring log/flash into view
        self._log(f"Extracted 24 KB partial ({variant}, CAL {calid}) "
                  f"→ {os.path.basename(out)}", "ok")
        QMessageBox.information(self, "Partial Extracted",
            f"Saved 24 KB partial ({variant}, CAL ID {calid}).\n\n"
            f"CPU/DS2-order tune partition (DS2 0x10000–0x15FFF), descrambled from the "
            f"full ROM — identical to a live ECU tune read.")

    def _on_merge_partial(self):
        full_path, _ = QFileDialog.getOpenFileName(
            self, "Select donor FULL 256 KB ROM", "", "Binary Files (*.bin);;All Files (*)"
        )
        if not full_path: return
        with open(full_path, "rb") as f:
            full = bytearray(f.read())
        if len(full) != MS41ECU.FULL_ROM_SIZE:
            QMessageBox.critical(self, "Wrong Size",
                f"Donor must be a 256 KB full ROM, got {len(full):,} bytes.")
            return
        part_path, _ = QFileDialog.getOpenFileName(
            self, "Select 24 KB PARTIAL to merge in", "", "Binary Files (*.bin);;All Files (*)"
        )
        if not part_path: return
        with open(part_path, "rb") as f:
            partial = f.read()
        if len(partial) != MS41ECU.TUNE_SIZE:
            QMessageBox.critical(self, "Wrong Size",
                f"Partial must be 24 KB ({MS41ECU.TUNE_SIZE:,} bytes), got {len(partial):,}.")
            return

        # CAL ID consistency check (the partial's CAL ID is at its offset 0x0E)
        full_cal = MS41ECU.read_calid(full)
        part_cal = MS41ECU.read_calid(partial)
        if full_cal and part_cal and full_cal != part_cal:
            ans = QMessageBox.warning(self, "CAL ID Mismatch",
                f"Donor full CAL ID : {full_cal}\nPartial CAL ID    : {part_cal}\n\n"
                f"These calibrations differ. Merging mismatched calibrations can "
                f"produce an unbootable ROM.\n\nMerge anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ans != QMessageBox.Yes:
                self._log("Merge cancelled — CAL ID mismatch.", "warn")
                return

        # inverse descramble: the partial is CPU/DS2-order, scatter it back to file order
        merged = MS41ECU.tune_into_full(full, partial)

        if self.chk_merge_fix.isChecked():
            # MS41.3 enforces boot and calibration checksums while its stock
            # program-checksum gate is disabled.
            ms413 = MS41ECU.detect_variant(merged) == "MS41.3"
            merged, cdet = correct_checksums(merged, correct_program=not ms413)
            for d in cdet:
                self._log(d)
            if ms413:
                if merged[0x605C] == 0xFF:
                    self._log(
                        "MS41.3: calibration and boot checksums corrected; program checksum "
                        "left unchanged because stock program verification is disabled.",
                        "ok",
                    )
                else:
                    self._log(
                        "MS41.3: calibration and boot checksums corrected; program checksum "
                        "left unchanged, but program verification is enabled in this image.",
                        "warn",
                    )

        stem   = os.path.splitext(os.path.basename(full_path))[0]
        out, _ = QFileDialog.getSaveFileName(
            self, "Save merged FULL ROM", f"{stem}_merged.bin",
            "Binary Files (*.bin);;All Files (*)"
        )
        if not out: return
        with open(out, "wb") as f:
            f.write(merged)
        ok, _ = verify_checksum(merged)
        self.tabs.setCurrentIndex(0)
        self._log(f"Merged partial into full → {os.path.basename(out)} "
                  f"(checksums {'OK' if ok else 'NOT verified'})", "ok" if ok else "warn")
        QMessageBox.information(self, "Merge Complete",
            f"Saved merged full ROM:\n{os.path.basename(out)}\n\n"
            f"Checksums: {'valid' if ok else 'NOT fully valid — review log'}.")

    # ── Identity / EWS tab ───────────────────────────────────────────────
    def _build_identity_tab(self):
        tab = QWidget()
        lay = QVBoxLayout(tab)

        # VIN editing is deliberately independent from EWS alignment. Nothing
        # read by this group can arm the EWS write button.
        vin_group = QGroupBox("VIN Editing")
        vin_group.setStyleSheet(_SECTION_GB)
        vin_lay = QVBoxLayout(vin_group)
        read_row = QHBoxLayout()
        self.btn_id_read_flash_ecu = self._op_btn(
            "Read BOOT Identity", "#1e5080", self._on_identity_read_flash_ecu)
        self.btn_id_read_flash_ecu.setMaximumWidth(210)
        self.btn_id_read_flash_ecu.setEnabled(False)
        self.btn_id_read_flash_ecu.setToolTip(
            "Requires Soft-BSL. Reads 16 KB on BOTTOM or the complete fused 64 KB SA7 sector "
            "on 29F400 TOP, with automatic baud fallback. A full 256 KB backup can be created "
            "first and reused without a second ECU read.")
        read_row.addWidget(self.btn_id_read_flash_ecu)
        self.lbl_identity_cache = QLabel("No BOOT identity data cached")
        self.lbl_identity_cache.setStyleSheet("color:#888; padding-left:8px;")
        read_row.addWidget(self.lbl_identity_cache)
        read_row.addStretch()
        vin_lay.addLayout(read_row)

        id_group = QGroupBox("BOOT Identity Data")
        id_group.setStyleSheet(_SECTION_GB)
        grid = QGridLayout(id_group)
        grid.setColumnStretch(1, 1)
        self._id_labels = {}
        for row, (label, key) in enumerate([
                ("Source", "source"), ("Part #", "part"),
                ("DME Serial", "serial"), ("ISN (display only)", "isn"),
                ("VIN", "vin")]):
            lk = QLabel(f"{label}:"); lk.setStyleSheet("font-weight:bold; color:#aaa; min-width:120px;")
            lv = QLabel("—"); lv.setStyleSheet("color:#e0e0e0;"); lv.setFont(QFont("Courier New", 10))
            grid.addWidget(lk, row, 0); grid.addWidget(lv, row, 1)
            self._id_labels[key] = lv
        strings_label = QLabel("Boot strings:")
        strings_label.setStyleSheet("font-weight:bold; color:#aaa;")
        self.id_boot_strings = QTextEdit()
        self.id_boot_strings.setReadOnly(True)
        self.id_boot_strings.setMaximumHeight(72)
        self.id_boot_strings.setFont(QFont("Courier New", 9))
        self.id_boot_strings.setStyleSheet(
            "background:#1a1a1a; color:#aaa; border:1px solid #444; padding:2px;")
        grid.addWidget(strings_label, 5, 0)
        grid.addWidget(self.id_boot_strings, 5, 1)
        vin_lay.addWidget(id_group)

        edit_row = QHBoxLayout()
        edit_row.addWidget(QLabel("Current VIN:"))
        self.id_vin_current = QLineEdit()
        self.id_vin_current.setReadOnly(True)
        self.id_vin_current.setPlaceholderText("Read BOOT Identity first")
        self.id_vin_current.setFont(QFont("Courier New", 10))
        edit_row.addWidget(self.id_vin_current)
        edit_row.addWidget(QLabel("New VIN:"))
        self.id_vin_custom = QLineEdit()
        self.id_vin_custom.setPlaceholderText("17-character VIN")
        self.id_vin_custom.setMaxLength(17)
        self.id_vin_custom.setFont(QFont("Courier New", 10))
        self.id_vin_custom.textChanged.connect(self._update_identity_write_state)
        edit_row.addWidget(self.id_vin_custom)
        self.btn_id_vin_apply = self._op_btn(
            "Write VIN to ECU", "#7a2d2d", self._on_identity_vin_apply)
        self.btn_id_vin_apply.setEnabled(False)
        self.btn_id_vin_apply.setMaximumWidth(190)
        edit_row.addWidget(self.btn_id_vin_apply)
        vin_lay.addLayout(edit_row)
        self.lbl_vin_validation = QLabel("Read BOOT Identity to begin.")
        self.lbl_vin_validation.setStyleSheet("color:#888;")
        vin_lay.addWidget(self.lbl_vin_validation)
        lay.addWidget(vin_group)

        # EWS alignment owns its own fresh live ISN state.
        ews_group = QGroupBox(
            "EWS3 Alignment  (write the DME's 4-digit ISN to the immobilizer, module 0x44)")
        ews_group.setStyleSheet(_SECTION_GB)
        ews_lay = QVBoxLayout(ews_group)
        ews_read_row = QHBoxLayout()
        self.btn_id_read_ecu = self._op_btn(
            "Read ISN", "#2a5d3a", self._on_identity_read_ecu)
        self.btn_id_read_ecu.setMaximumWidth(150)
        self.btn_id_read_ecu.setEnabled(False)
        self.btn_id_read_ecu.setToolTip(
            "Read only the authoritative four-digit DME ISN. This fresh live value is the "
            "only source allowed to arm Send to EWS.")
        ews_read_row.addWidget(self.btn_id_read_ecu)
        ews_read_row.addWidget(QLabel("DME ISN:"))
        self.id_ews_isn = QLineEdit()
        self.id_ews_isn.setReadOnly(True)
        self.id_ews_isn.setPlaceholderText("Not read")
        self.id_ews_isn.setAlignment(Qt.AlignCenter)
        self.id_ews_isn.setFixedWidth(110)
        self.id_ews_isn.setFont(QFont("Courier New", 12, QFont.Bold))
        self.id_ews_isn.setStyleSheet(
            "background:#1a1a1a; color:#7ec8e3; border:1px solid #444; padding:3px;")
        ews_read_row.addWidget(self.id_ews_isn)
        ews_read_row.addStretch()
        ews_lay.addLayout(ews_read_row)
        ews_lay.addWidget(QLabel("EWS Alignment log:"))
        self.id_ews_frames = QTextEdit()
        self.id_ews_frames.setReadOnly(True)
        self.id_ews_frames.setMaximumHeight(72)
        self.id_ews_frames.setFont(QFont("Courier New", 9))
        self.id_ews_frames.setStyleSheet("background:#1a1a1a; color:#aaa; border:1px solid #444; padding:2px;")
        self.id_ews_frames.setPlainText("Read ISN from the connected DME to begin.")
        ews_lay.addWidget(self.id_ews_frames)
        self.btn_ews_send = self._op_btn("Send to EWS (0x44)…", "#7a2d2d", self._on_ews_send)
        self.btn_ews_send.setEnabled(False)
        ews_lay.addWidget(self.btn_ews_send)
        lay.addWidget(ews_group)
        lay.addStretch()
        self.tabs.addTab(tab, "  VIN / EWS  ")

    def _show_identity(self, data, source):
        """Show identity and retain the complete erase sector needed for this bank.

        This never populates the independent EWS alignment state.
        """
        data = bytes(data)
        half = getattr(self, "_ecu_softbsl_marker", None)
        if len(data) == identity.FULL_ROM_SIZE:
            boot_data = data[
                identity.BOOT_DATA_OFF:identity.BOOT_DATA_OFF + identity.BOOT_DATA_SIZE]
            if half == "T":
                sector_off = identity.TOP_IDENTITY_SECTOR_OFF
                sector_data = data[sector_off:sector_off + identity.TOP_IDENTITY_SECTOR_SIZE]
            else:
                sector_off = identity.IDENTITY_SECTOR_OFF
                sector_data = data[sector_off:sector_off + identity.IDENTITY_SECTOR_SIZE]
        elif len(data) == identity.TOP_IDENTITY_SECTOR_SIZE and half == "T":
            sector_off = identity.TOP_IDENTITY_SECTOR_OFF
            sector_data = data
            boot_data = data[
                identity.BOOT_DATA_OFF:identity.BOOT_DATA_OFF + identity.BOOT_DATA_SIZE]
        elif len(data) == identity.BOOT_DATA_SIZE:
            boot_data = data
            sector_off = identity.IDENTITY_SECTOR_OFF
            sector_data = data[:identity.IDENTITY_SECTOR_SIZE]
        else:
            raise ValueError(
                f"identity data must be {identity.BOOT_DATA_SIZE}, "
                f"{identity.TOP_IDENTITY_SECTOR_SIZE}, or {identity.FULL_ROM_SIZE} bytes, "
                f"got {len(data)}")
        self._cache_boot_identity(boot_data, source, sector_data, sector_off)

    def _identity_connection_key(self, serial=None):
        """Fingerprint the live ECU/bank that is allowed to own cached critical data."""
        if self._ds2 is None or not self._connection_port:
            return None
        if not serial:
            try:
                source = getattr(self, "_ecu_identity_source", None)
                serial = identity.decode_identity(source).serial if source else None
            except Exception:
                serial = None
        return (
            self._connection_port,
            self._ecu_id or "",
            serial or "",
            getattr(self, "_ecu_softbsl_marker", None) or "",
            bytes(getattr(self, "_ecu_chip_sig", b"") or b""),
        )

    def _cache_boot_identity(self, boot_data, source, sector_data, sector_off):
        boot_data = bytes(boot_data)
        sector_data = bytes(sector_data)
        info = identity.decode_boot_identity(boot_data)
        if info.serial and not getattr(self, "_ecu_identity_source", None):
            # Connection setup normally captured this already. If that sparse
            # read failed, the freshly-read BOOT window is an equally authoritative
            # source and keeps the ownership fingerprint stable.
            self._ecu_identity_source = bytes(identity.boot_data_image(boot_data))
        self._identity_boot_data = boot_data
        self._identity_sector_data = sector_data
        self._identity_sector_off = int(sector_off)
        self._identity_cache_key = self._identity_connection_key(info.serial)
        self._identity_cache_source = str(source)
        self._identity_cache_time = datetime.datetime.now()
        self._id_labels["source"].setText(source)
        self._id_labels["part"].setText(info.part or "—")
        self._id_labels["serial"].setText(info.serial or "—")
        self._id_labels["isn"].setText(info.isn4 or "—")
        self._id_labels["vin"].setText(info.vin or "—")
        self.id_vin_current.setText(info.vin or "")
        self.id_vin_custom.clear()
        strings = identity.boot_strings(boot_data)
        self.id_boot_strings.setPlainText(
            "\n".join(f"0x{offset:05X}: {text}" for offset, text in strings)
            if strings else "No conservative printable strings found.")
        stamp = self._identity_cache_time.strftime("%H:%M:%S")
        self.lbl_identity_cache.setText(
            f"Cached from current ECU • {len(sector_data) // 1024} KB erase sector • {stamp}")
        self.lbl_identity_cache.setStyleSheet("color:#9ece6a; padding-left:8px;")
        if info.notes:
            self._log("BOOT identity layout notes: " + "; ".join(info.notes), "warn")
        self._update_identity_write_state()

    def _clear_identity_tab_state(self):
        self._identity_boot_data = None
        self._identity_sector_data = None
        self._identity_sector_off = None
        for label in getattr(self, "_id_labels", {}).values():
            label.setText("—")
        self.id_boot_strings.clear()
        self.id_vin_current.clear()
        self.id_vin_custom.clear()
        self.lbl_identity_cache.setText("No BOOT identity data cached")
        self.lbl_identity_cache.setStyleSheet("color:#888; padding-left:8px;")
        self.lbl_vin_validation.setText("Read BOOT Identity to begin.")
        self.lbl_vin_validation.setStyleSheet("color:#888;")
        self.btn_id_vin_apply.setEnabled(False)
        self.id_ews_isn.clear()
        self.id_ews_frames.setPlainText("Read ISN from the connected DME to begin.")
        self.btn_ews_send.setEnabled(False)

    def _update_identity_write_state(self, *_args):
        if not hasattr(self, "btn_id_vin_apply"):
            return
        enabled = False
        message = "Read BOOT Identity to begin."
        colour = "#888"
        current_key = self._identity_connection_key()
        if not self._identity_boot_data:
            pass
        elif not self._identity_sector_data or self._identity_sector_off is None:
            message = "The complete identity erase sector is not cached. Read BOOT Identity again."
            colour = "#f47171"
        elif not self._identity_cache_key or self._identity_cache_key != current_key:
            message = "Cached BOOT data does not belong to the current ECU connection. Read it again."
            colour = "#f47171"
        else:
            candidate = self.id_vin_custom.text().strip().upper()
            current = self.id_vin_current.text().strip().upper()
            try:
                identity.encode_vin(candidate)
                valid = True
            except ValueError:
                valid = False
            if not candidate:
                message = "Enter the new 17-character VIN."
            elif not valid:
                message = "VIN must contain 17 valid VIN characters (I, O and Q are not allowed)."
                colour = "#e8c46a"
            elif candidate == current:
                message = "The new VIN matches the current VIN; nothing will be written."
            elif (getattr(self, "_ecu_softbsl_marker", None) == "T"
                  and self._fast_chip_family() != "amd"):
                message = "TOP VIN writes require the AMD/29F400 64 KB SA7 geometry."
                colour = "#f47171"
            elif self._fast_chip_family() not in ("amd", "intel"):
                message = "Flash-chip family is unknown; a sector write cannot be armed safely."
                colour = "#f47171"
            elif not self._fast_read_available():
                message = "Writing VIN requires the Soft-BSL loader and an active FTDI D2XX connection."
                colour = "#e8c46a"
            elif self._task_busy:
                message = "Another ECU operation is in progress."
            else:
                enabled = True
                if getattr(self, "_ecu_softbsl_marker", None) == "T":
                    message = "Ready: the cached 64 KB fused TOP SA7 sector will be rewritten and verified."
                else:
                    message = "Ready: only the cached 8 KB BOTTOM SA1 sector will be rewritten and verified."
                colour = "#9ece6a"
        self.btn_id_vin_apply.setEnabled(enabled)
        self.lbl_vin_validation.setText(message)
        self.lbl_vin_validation.setStyleSheet(f"color:{colour};")

    def _set_isn(self, isn4, connection_key=None):
        """Set only the fresh live EWS ISN state; BOOT data never calls this."""
        if isn4 and isn4.isdigit() and len(isn4) == 4:
            self._identity_isn = isn4
            self._identity_isn_key = connection_key or self._identity_connection_key()
            self.id_ews_isn.setText(isn4)
            self.id_ews_frames.setPlainText(
                "ISN read from DME. Ready to send the validated EWS3 encoding to EWS.")
            self.btn_ews_send.setEnabled(
                self._ds2 is not None
                and self._identity_isn_key == self._identity_connection_key())
        else:
            self._identity_isn = None
            self._identity_isn_key = None
            self.id_ews_isn.clear()
            self.id_ews_frames.setPlainText("Read ISN from the connected DME to begin.")
            self.btn_ews_send.setEnabled(False)

    def _ask_identity_backup_choice(self):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Create a Full FLASH Backup?")
        box.setText(
            "Before reading the BOOT identity data, would you like to create a complete "
            "256 KB FLASH backup?")
        box.setInformativeText(
            "A full backup is recommended and will be saved in Bins. If you continue without "
            "one, Soft-BSL reads only the identity data needed for this bank: 16 KB on BOTTOM "
            "or the complete 64 KB fused SA7 erase sector on 29F400 TOP.")
        backup_btn = box.addButton("Create Full Backup", QMessageBox.AcceptRole)
        continue_btn = box.addButton("Continue Without Full Backup", QMessageBox.ActionRole)
        cancel_btn = box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(backup_btn)
        box.exec_()
        clicked = box.clickedButton()
        if clicked is backup_btn:
            return "backup"
        if clicked is continue_btn:
            return "partial"
        if clicked is cancel_btn:
            return None
        return None

    def _on_identity_read_flash_ecu(self):
        """Read/cache BOOT identity data, optionally creating one full backup first."""
        if not self._ds2:
            QMessageBox.warning(self, "Not Connected", "Connect to the ECU first.")
            return
        if not self._fast_read_available():
            QMessageBox.critical(
                self, "Soft-BSL Required",
                "BOOT identity access requires an installed Soft-BSL loader and an active FTDI "
                "D2XX connection. DS2 is not used for this VIN workflow.")
            return

        current_key = self._identity_connection_key()
        cached_full = getattr(self, "_last_full_read", None)
        if (cached_full is not None
                and len(cached_full) == identity.FULL_ROM_SIZE
                and getattr(self, "_last_full_read_key", None) == current_key):
            self._show_identity(cached_full, "current-session full backup")
            self._log(
                "BOOT identity reused from the current connection's archived full backup; "
                "no additional ECU read was needed.", "ok")
            return

        choice = self._ask_identity_backup_choice()
        if choice is None:
            return

        def task(log_fn, progress_fn):
            half = getattr(self, "_ecu_softbsl_marker", None)
            chip_family = self._fast_chip_family()
            if choice == "backup":
                log_fn("Reading the complete 256 KB FLASH backup before VIN editing…")
                data = bytes(self._read_image_auto("full", log_fn, progress_fn))
                if len(data) != identity.FULL_ROM_SIZE:
                    raise ValueError(
                        f"full FLASH read returned {len(data):,} bytes; expected "
                        f"{identity.FULL_ROM_SIZE:,}")
                ok, details = verify_checksum(bytearray(data))
                for detail in details:
                    log_fn(detail)
                if not ok:
                    log_fn("Full backup read completed, but one or more ROM checksums are invalid.", "warn")
                return "backup", data

            expected = (identity.TOP_IDENTITY_SECTOR_SIZE
                        if half == "T" else identity.BOOT_DATA_SIZE)
            label = ("complete 64 KB fused TOP SA7 sector"
                     if half == "T" else "16 KB BOOT identity window")
            log_fn(
                f"Fast (Soft-BSL) read - {label}, high baud with automatic fallback.", "ok")
            data = self._run_via_softbsl(
                lambda port, pf, lf: softbsl_service.read_identity_data(
                    port, "high", pf, lf, chip_family=chip_family, half=half),
                log_fn, progress_fn)
            if len(data) != expected:
                raise ValueError(
                    f"BOOT identity read returned {len(data):,} bytes; expected {expected:,}")
            return "partial", bytes(data)

        def on_done(result):
            mode, data = result
            if mode == "backup":
                try:
                    entry = self._record_full_ecu_read(
                        data, source="ECU read (VIN editor)")
                except Exception as error:
                    self._log(f"VIN-editor full backup archive failed: {error}", "error")
                    QMessageBox.critical(
                        self, "Full Backup Could Not Be Saved",
                        f"The complete FLASH was read, but it could not be persisted in Bins:\n"
                        f"{error}\n\nBOOT identity loading was cancelled so the operation is not "
                        "presented as backed up. Retry and choose whether to continue without one.")
                    return
                self._show_identity(data, entry.filename)
                self._log(
                    f"Full FLASH backup archived as {entry.filename}; BOOT identity borrowed from it.",
                    "ok")
                QMessageBox.information(
                    self, "Full Backup and BOOT Read Complete",
                    f"The complete 256 KB FLASH was archived in Bins as:\n{entry.filename}\n\n"
                    "BOOT identity data was loaded from that backup without a second ECU read.")
            else:
                self._show_identity(data, "live ECU • cached only")
                self._log(
                    "BOOT identity/descriptor window cached for VIN editing; no catalogue backup was created.",
                    "ok")

        self._run_task(task, on_success=on_done)

    def _on_identity_read_ecu(self):
        if not self._ds2:
            QMessageBox.warning(self, "Not Connected", "Connect to the ECU first.")
            return
        connection_key = self._identity_connection_key()

        def task(log_fn, progress_fn):
            log_fn("Reading the authoritative four-digit DME ISN for EWS alignment…")
            return self._ds2.read_isn()

        def on_done(isn):
            self._set_isn(isn, connection_key)
            self._log(f"EWS workflow: fresh DME ISN {isn} read for the current connection.", "ok")

        self._run_task(task, on_success=on_done)

    def _on_identity_vin_apply(self):
        if not self._identity_boot_data or not self._ds2:
            return
        cache_key = self._identity_cache_key
        if not cache_key or cache_key != self._identity_connection_key():
            QMessageBox.critical(
                self, "BOOT Cache Does Not Match",
                "The cached BOOT data does not belong to the current ECU connection. "
                "Read BOOT Identity again before writing.")
            self._update_identity_write_state()
            return
        half = getattr(self, "_ecu_softbsl_marker", None)
        chip_family = self._fast_chip_family()
        if chip_family not in ("amd", "intel") or not self._fast_read_available():
            QMessageBox.critical(
                self, "Safe Sector Writer Unavailable",
                "Writing VIN requires a detected AMD/Intel flash family, the installed Soft-BSL "
                "loader, and an active FTDI D2XX connection. Plain DS2 cannot erase this sector.")
            return
        if half == "T" and chip_family != "amd":
            QMessageBox.critical(
                self, "TOP Geometry Mismatch",
                "The TOP-bank VIN workflow requires AMD/29F400 coarse 64 KB SA7 geometry.")
            return

        new_vin = self.id_vin_custom.text().strip().upper()
        current_vin = self.id_vin_current.text().strip().upper()
        try:
            target_boot = bytes(identity.set_boot_vin(self._identity_boot_data, new_vin))
        except ValueError as error:
            QMessageBox.warning(self, "Invalid VIN", str(error))
            return
        if new_vin == current_vin:
            return

        original_sector = bytes(self._identity_sector_data or b"")
        sector_off = self._identity_sector_off
        expected_sector_size = (identity.TOP_IDENTITY_SECTOR_SIZE
                                if half == "T" else identity.IDENTITY_SECTOR_SIZE)
        expected_sector_off = (identity.TOP_IDENTITY_SECTOR_OFF
                               if half == "T" else identity.IDENTITY_SECTOR_OFF)
        if len(original_sector) != expected_sector_size or sector_off != expected_sector_off:
            QMessageBox.critical(
                self, "Incomplete BOOT Cache",
                "The cached erase sector does not match the active bank geometry. "
                "Read BOOT Identity again before writing.")
            return
        target_sector = bytearray(original_sector)
        boot_rel = identity.VIN_OFF - identity.BOOT_DATA_OFF
        vin_lo = identity.VIN_OFF - sector_off
        vin_hi = vin_lo + identity.VIN_LEN
        target_sector[vin_lo:vin_hi] = target_boot[boot_rel:boot_rel + identity.VIN_LEN]
        target_sector = bytes(target_sector)
        changed = [index for index, (old, new) in enumerate(zip(original_sector, target_sector))
                   if old != new]
        if not changed or any(index < vin_lo or index >= vin_hi for index in changed):
            QMessageBox.critical(
                self, "VIN Write Construction Blocked",
                "The proposed sector image contains an unexpected change outside the packed VIN field. "
                "Nothing was written.")
            return

        hardware_note = (
            "\n\nIntel 28F200: this erase/program operation requires the correct Intel agent "
            "and the ECU's 12 V programming conditions."
            if chip_family == "intel" else "")
        sector_label = "64 KB fused TOP SA7" if half == "T" else "8 KB BOTTOM SA1"
        recovery_route = (
            "Switch to the intact BOTTOM bank and recover over Soft-BSL."
            if half == "T" else
            "Recover over Soft-BSL if the loader remains reachable; hardware BSL is the backstop.")
        typed, accepted = QInputDialog.getText(
            self, "BRICK-CLASS — Write VIN",
            f"Current VIN: {current_vin or '<not programmed>'}\n"
            f"New VIN:     {new_vin}\n\n"
            "Flash cannot update only 13 bytes. This operation will erase and rewrite the complete "
            f"{sector_label} erase sector, preserving every cached byte except the VIN.\n\n"
            "Immediately before erase, the tool will reread the live sector, require an exact cache "
            f"match, save a {expected_sector_size // 1024} KB recovery snapshot, and verify the complete "
            "sector afterward.\n\n"
            "Loss of ECU power or communication can leave this bank unbootable. "
            f"{recovery_route}{hardware_note}\n\nType  WRITE VIN  to proceed:")
        if not accepted or typed.strip() != "WRITE VIN":
            self._log("VIN write cancelled at the brick-class confirmation.", "warn")
            return

        self._last_identity_recovery_path = None

        def task(log_fn, progress_fn):
            log_fn(
                f"Preflight: rereading the live {expected_sector_size // 1024} KB identity "
                "erase sector through Soft-BSL before any erase…")
            live_sector = self._run_via_softbsl(
                lambda port, pf, lf: softbsl_service.read_identity_sector(
                    port, "high", pf, lf, chip_family=chip_family, half=half),
                log_fn, progress_fn)
            if bytes(live_sector) != original_sector:
                mismatches = [i for i, (cached, live) in enumerate(zip(original_sector, live_sector))
                              if cached != live]
                first = mismatches[0] if mismatches else 0
                raise RuntimeError(
                    f"live identity sector no longer matches the cache "
                    f"(first difference at file 0x{sector_off + first:05X}); "
                    "nothing was erased. Read BOOT Identity again.")

            os.makedirs(IDENTITY_RECOVERY_DIR, exist_ok=True)
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            ecu_name = "".join(ch for ch in (self._ecu_id or "ecu") if ch.isalnum()) or "ecu"
            sector_tag = "sa7_top" if half == "T" else "sa1_bottom"
            recovery_path = os.path.join(
                IDENTITY_RECOVERY_DIR, f"ms41_{ecu_name}_{sector_tag}_pre_vin_{timestamp}.bin")
            with open(recovery_path, "wb") as recovery_file:
                recovery_file.write(live_sector)
                recovery_file.flush()
                os.fsync(recovery_file.fileno())
            self._last_identity_recovery_path = recovery_path
            log_fn(
                f"Pre-erase {expected_sector_size // 1024} KB recovery snapshot persisted: "
                f"{recovery_path}", "ok")

            log_fn(
                f"Starting {sector_label} VIN write ({len(changed)} packed-VIN byte(s) differ; "
                f"{expected_sector_size // 1024} KB erase sector preserved/rebuilt).", "warn")
            self._run_via_softbsl(
                lambda port, pf, lf: softbsl_service.write_identity_sector(
                    port, target_sector, self._softbsl_prompt, lf,
                    baud="high", progress_cb=pf, chip_family=chip_family, half=half),
                log_fn, progress_fn)
            log_fn(
                f"Independent verify: rereading every byte of the {expected_sector_size // 1024} KB "
                "identity sector through Soft-BSL…")
            readback = self._run_via_softbsl(
                lambda port, pf, lf: softbsl_service.read_identity_sector(
                    port, "high", pf, lf, chip_family=chip_family, half=half),
                log_fn, progress_fn)
            if bytes(readback) != target_sector:
                mismatches = [i for i, (wanted, got) in enumerate(zip(target_sector, readback))
                              if wanted != got]
                first = mismatches[0] if mismatches else 0
                raise RuntimeError(
                    f"full-sector verification mismatch at file "
                    f"0x{sector_off + first:05X}; do not rely on the ECU until "
                    f"the sector is recovered, and retain recovery snapshot {recovery_path}")
            decoded = identity.decode_boot_identity(target_boot)
            if decoded.vin != new_vin:
                raise RuntimeError(
                    f"sector bytes verified, but VIN decoded as {decoded.vin!r} instead of {new_vin!r}")
            log_fn(
                f"Complete {expected_sector_size // 1024} KB read-back verification passed; "
                "the new VIN decodes correctly.", "ok")
            return target_boot, target_sector, recovery_path

        def on_done(result):
            written_boot, written_sector, recovery_path = result
            self._ecu_vin = new_vin
            if "VIN" in self._info_labels:
                self._info_labels["VIN"].setText(new_vin)
            try:
                source = getattr(self, "_ecu_identity_source", None)
                if source and len(source) >= identity.VIN_OFF + identity.VIN_LEN:
                    self._ecu_identity_source = bytes(identity.set_vin(source, new_vin))
                else:
                    self._ecu_identity_source = bytes(identity.boot_data_image(written_boot))
            except Exception:
                pass
            # The archived full backup remains a valid restore point, but it is no
            # longer an exact image of the live ECU and must not be reused as current data.
            self._last_full_read = None
            self._last_full_read_key = None
            self._show_identity(
                written_sector if half == "T" else written_boot,
                "live ECU • verified after VIN write")
            self._log(f"VIN write completed and verified: {current_vin or 'blank'} → {new_vin}", "ok")
            QMessageBox.information(
                self, "VIN Write Complete",
                f"VIN written and independently verified:\n\n{new_vin}\n\n"
                f"The complete {expected_sector_size // 1024} KB sector matched byte-for-byte.\n"
                f"Recovery snapshot retained at:\n{recovery_path}")

        def on_failure(error_msg):
            snapshot = getattr(self, "_last_identity_recovery_path", None)
            extra = f"\n\nRecovery snapshot:\n{snapshot}" if snapshot else ""
            self._log(f"VIN sector write failed: {error_msg}", "error")
            if self._offer_active_flash_recovery(
                    f"The identity-sector write failed after erase began:\n{error_msg}{extra}"):
                return
            QMessageBox.critical(
                self, "VIN Write Failed",
                f"{error_msg}{extra}\n\nReview the operation log. If erase began, do not rely on the ECU "
                "until the identity sector has been recovered and verified.")

        self._run_task(task, on_success=on_done, on_failure=on_failure)

    def _on_ews_send(self):
        isn = self._identity_isn
        connection_key = self._identity_connection_key()
        if not (self._ds2 and isn and isn.isdigit() and len(isn) == 4
                and self._identity_isn_key == connection_key):
            QMessageBox.warning(
                self, "EWS Align",
                "Connect and use Read ISN for this ECU connection before sending to EWS.")
            self._set_isn(None)
            return
        f = identity.ews_frames(isn)
        ok = QMessageBox.warning(
            self, "Write ISN to EWS?",
            f"This writes the ISN to the EWS3 immobilizer (module 0x44) — a LIVE immobilizer change.\n\n"
            f"DME ISN: {isn}\n"
            f"Validated EWS3 encoded value: 0x{f['hex_value']:03X}\n\n"
            "The DME ISN will be reread immediately before the write and must still match. "
            "Success is reported only after a checksum-valid EWS acknowledgement.\n\n"
            "After it succeeds, turn the ignition OFF for 15 seconds.\n\nProceed?",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        if ok != QMessageBox.Yes:
            return
        write_frame = f["write"]

        def task(log_fn, progress_fn):
            fresh_isn = self._ds2.read_isn()
            if fresh_isn != isn:
                raise RuntimeError(
                    f"DME ISN changed from {isn} to {fresh_isn}; EWS write blocked before transmission")
            log_fn(f"DME ISN recheck passed: {fresh_isn}", "ok")
            log_fn(f"EWS write: {write_frame.hex(' ').upper()}")
            response = bytes(self._ds2.send_frame(write_frame, resp_addr=0x44))
            # the legacy utility's response parser removes the checksum and accepts
            # exactly three remaining bytes, i.e. a four-byte complete DS2 frame.
            if len(response) != 4:
                raise DS2Error(
                    f"unexpected EWS acknowledgement length {len(response)} "
                    f"(expected 4): {response.hex(' ').upper()}")
            return response

        def on_done(resp):
            encoded = f["hex_value"]
            self.id_ews_frames.setPlainText(
                f"DME ISN: {isn}\n"
                f"EWS encoded value: 0x{encoded:03X}\n"
                f"Validated acknowledgement: {bytes(resp).hex(' ').upper()}")
            self._log(f"EWS exact acknowledgement: {bytes(resp).hex(' ').upper()}", "ok")
            QMessageBox.information(
                self, "EWS Aligned",
                "EWS returned the expected checksum-valid acknowledgement. "
                "Turn the ignition OFF for 15 seconds.")
            self._identity_isn = None
            self._identity_isn_key = None
            self.id_ews_isn.clear()
            self.btn_ews_send.setEnabled(False)

        def on_failure(error_msg):
            self._set_isn(None)
            self._log(f"EWS alignment failed: {error_msg}", "error")
            QMessageBox.critical(self, "EWS Alignment Failed", str(error_msg))

        self._run_task(task, on_success=on_done, on_failure=on_failure)

    # ── Soft-BSL tab ─────────────────────────────────────────────────────
    def _build_softbsl_tab(self):
        tab = QWidget()
        lay = QVBoxLayout(tab)
        self._softbsl_prompt = _GuiPrompt(self)   # main-thread-owned blocking prompt
        self._softbsl_confirm = _GuiConfirm(self)

        warn = QLabel("⚠ Soft-BSL runs a RAM agent over K-line. Installation and ordinary fast operations "
                      "reuse the selected serial port automatically. A cross-bank write changes the "
                      "inactive bank's boot region and is therefore brick-class.")
        warn.setWordWrap(True); warn.setStyleSheet("color:#e8c46a;")
        lay.addWidget(warn)

        order = QLabel("① Install Soft-BSL once. After installation, the Flash tab automatically uses "
                       "the agent for ordinary reads and writes. ② Use the cross-bank section only to "
                       "prepare the inactive TOP half of a dual-bank 29F400.")
        order.setWordWrap(True); order.setStyleSheet("color:#888; font-style:italic;")
        lay.addWidget(order)

        self._d2xx_warn = QLabel()
        self._d2xx_warn.setWordWrap(True)
        self._update_d2xx_warning()
        lay.addWidget(self._d2xx_warn)

        # ── ① install soft-BSL loader (one-click, in-process service) ──
        inst = QGroupBox("①  Install Soft-BSL  (prepare image → one ignition cycle → write → verify)")
        inst.setStyleSheet(_SECTION_GB)
        ig = QVBoxLayout(inst)
        inst_help = QLabel(
            "By default, reads the connected ECU and prepares the required Soft-BSL image in memory. "
            "You cycle the ignition once when prompted.\n"
            "• Use base .bin skips the slow full-ROM read; VIN/ISN can still be preserved from the "
            "identity data captured at connection.\n"
            "• Calibration is preserved when the connected ECU and patch base are the same consistent MS41.2 or MS41.3 version.\n"
            "• Cross-version conversion replaces the calibration and requires explicit full-write confirmation.")
        inst_help.setWordWrap(True); inst_help.setStyleSheet("color:#888;")
        ig.addWidget(inst_help)
        r3 = QHBoxLayout()
        self.chk_install_calguard = QCheckBox("Add cal_guard no-brick version gate (recommended)")
        self.chk_install_calguard.setChecked(True)
        r3.addWidget(self.chk_install_calguard)
        self.chk_install_force_base = QCheckBox("Use base .bin (skip ECU read)")
        self.chk_install_force_base.setToolTip(
            "Choose a full, consistent MS41.2 or MS41.3 ROM and use it directly as the patch base instead of "
            "reading 256 KB from the connected ECU. Intended for fresh/rebuild workflows.")
        r3.addWidget(self.chk_install_force_base)
        r3.addStretch()
        ig.addLayout(r3)
        r4 = QHBoxLayout()
        self.chk_install_preserve_identity = QCheckBox("Preserve VIN / ISN")
        self.chk_install_preserve_identity.setChecked(True)
        self.chk_install_preserve_identity.setToolTip(
            "When a base .bin is used, read only the connected ECU's serial/ISN and packed VIN "
            "and graft them into the base before composing the install images.")
        r4.addWidget(self.chk_install_preserve_identity)
        self.chk_install_preserve_cal = QCheckBox("Preserve calibration (matching MS41.2/MS41.3)")
        self.chk_install_preserve_cal.setChecked(True)
        self.chk_install_preserve_cal.setEnabled(False)
        self.chk_install_preserve_cal.setToolTip(
            "Available for an already-consistent MS41.2 or MS41.3 ECU. Unchecking performs a full "
            "brick-class write and replaces the calibration from the selected/composed base.")
        r4.addWidget(self.chk_install_preserve_cal)
        self.btn_softbsl_install = self._op_btn("Install Soft-BSL…", "#7a2d2d", self._on_softbsl_install)
        self.btn_softbsl_install.setEnabled(False)
        r4.addWidget(self.btn_softbsl_install); r4.addStretch()
        ig.addLayout(r4)
        lay.addWidget(inst)

        # ── ② cross-bank golden TOP (soft-BSL-ONLY; ordinary agent read/write is auto on the Flash tab) ──
        flash_gb = QGroupBox("②  Cross-bank golden TOP  (Soft-BSL installed; brick-class 29F400 top-half write)")
        flash_gb.setStyleSheet(_SECTION_GB)
        fg_lay = QVBoxLayout(flash_gb)
        note2 = QLabel("Builds the golden TOP with the same persistent Soft-BSL components as regular "
                       "installation, then writes the complete coarse-sector TOP half of a dual-bank "
                       "29F400. Load a consistent "
                       "MS41.2 or MS41.3 base, or read the existing TOP through the RAM agent while connected. The live "
                       "write remains brick-class and recoverable only from the intact BOTTOM.")
        note2.setWordWrap(True); note2.setStyleSheet("color:#888;")
        fg_lay.addWidget(note2)

        opts = QHBoxLayout()
        self.chk_xbank_calguard = QCheckBox("Add cal_guard no-brick version gate (recommended)")
        self.chk_xbank_calguard.setChecked(True)
        self.chk_xbank_calguard.stateChanged.connect(self._on_softbsl_xbank_options_changed)
        opts.addWidget(self.chk_xbank_calguard)
        self.chk_xbank_preserve_identity = QCheckBox("Preserve VIN / ISN for a file base")
        self.chk_xbank_preserve_identity.setChecked(True)
        self.chk_xbank_preserve_identity.setToolTip(
            "For a selected file, graft the connected ECU's serial/ISN and packed VIN before composing. "
            "A base read from TOP already preserves its own identity inherently.")
        self.chk_xbank_preserve_identity.stateChanged.connect(self._on_softbsl_xbank_options_changed)
        opts.addWidget(self.chk_xbank_preserve_identity)
        opts.addStretch()
        fg_lay.addLayout(opts)

        top = QHBoxLayout()
        self.btn_softbsl_xbank_load = self._op_btn(
            "Load MS41.2 / .3 Base…", "#3d3d3d", self._on_softbsl_load)
        self.btn_softbsl_xbank_load.setMaximumWidth(210)
        top.addWidget(self.btn_softbsl_xbank_load)
        self.btn_softbsl_xbank_read = self._op_btn(
            "Read TOP Base + Compose…", "#3d3d3d", self._on_softbsl_read_top_base)
        self.btn_softbsl_xbank_read.setMaximumWidth(220)
        self.btn_softbsl_xbank_read.setEnabled(False)
        top.addWidget(self.btn_softbsl_xbank_read)
        top.addWidget(QLabel("Bank marker:"))
        self._softbsl_marker_lbl = QLabel("—"); self._softbsl_marker_lbl.setFont(QFont("Courier New", 10))
        top.addWidget(self._softbsl_marker_lbl)
        top.addStretch()
        fg_lay.addLayout(top)

        self._softbsl_preview = QTextEdit(); self._softbsl_preview.setReadOnly(True)
        self._softbsl_preview.setFont(QFont("Courier New", 9))
        self._softbsl_preview.setStyleSheet("background:#1a1a1a; color:#aaa; border:1px solid #444; padding:2px;")
        fg_lay.addWidget(self._softbsl_preview)

        btns = QHBoxLayout()
        self.btn_softbsl_xbank = self._op_btn("Cross-bank golden TOP…", "#7a2d2d", self._on_softbsl_cross_bank)
        self.btn_softbsl_xbank.setEnabled(False)
        btns.addWidget(self.btn_softbsl_xbank); btns.addStretch()
        fg_lay.addLayout(btns)
        lay.addWidget(flash_gb)

        self.tabs.addTab(tab, "  Soft-BSL  ")

    def _show_softbsl_image(self, data, source, compose_log=None):
        """Load a golden-bank candidate and show the one operation this panel performs."""
        self._softbsl_image = bytes(data)
        m = softbsl_service.marker(self._softbsl_image)
        self._softbsl_marker_lbl.setText(f"{m or '—'}   ({source})")
        text = softbsl_service.crossbank_plan(self._softbsl_image)
        if compose_log:
            text = ("=== PREPARED IMAGE ===\n"
                    + "\n".join(str(line) for line in compose_log)
                    + "\n\n" + text)
        self._softbsl_preview.setPlainText(text)
        self._update_softbsl_crossbank_button()

    def _update_softbsl_crossbank_button(self):
        """Arm the brick-class operation only for a valid TOP image and a free DS2 session."""
        image = getattr(self, "_softbsl_image", None)
        marker = softbsl_service.marker(image) if image else None
        image_family = ecu_info.image_chip_family(image) if image else None
        valid_size = image is not None and len(image) == identity.FULL_ROM_SIZE
        disconnected = getattr(self, "_ds2", None) is None
        idle = not getattr(self, "_task_busy", False)
        amd_driver = self._fast_chip_family() == "amd"
        enabled = (valid_size and marker == "T" and image_family == "amd"
                   and amd_driver and disconnected and idle)
        self.btn_softbsl_xbank.setEnabled(enabled)

        if not valid_size:
            reason = "Load a complete 256 KB golden-TOP image first."
        elif marker != "T":
            reason = "Cross-bank requires a golden-TOP image with bank marker 'T'."
        elif image_family != "amd":
            reason = "The golden-TOP image must carry the AMD/JEDEC 29F flash driver."
        elif not amd_driver:
            reason = "The last connected ECU must report the AMD/JEDEC 29F driver family."
        elif not disconnected:
            reason = "Disconnect the active DS2 session before the cross-bank A17 operation."
        elif not idle:
            reason = "Wait for the current operation to finish."
        else:
            reason = ("Writes the 29F400 TOP half from the intact BOTTOM bank; "
                      "requires the A17 switch and typed confirmation.")
        self.btn_softbsl_xbank.setToolTip(reason)

        connected = getattr(self, "_ds2", None) is not None
        bottom_loader = getattr(self, "_ecu_softbsl_marker", None) == "B"
        can_read_top = connected and bottom_loader and amd_driver and idle
        self.btn_softbsl_xbank_read.setEnabled(can_read_top)
        self.btn_softbsl_xbank_load.setEnabled(idle)
        self.chk_xbank_calguard.setEnabled(idle)
        self.chk_xbank_preserve_identity.setEnabled(idle)
        if not connected:
            read_reason = "Connect to the working BOTTOM bank first."
        elif not bottom_loader:
            read_reason = "The connected working bank must report the Soft-BSL B marker."
        elif not amd_driver:
            read_reason = "Cross-bank TOP access is only valid for the AMD-driver 29F400 layout."
        elif not idle:
            read_reason = "Wait for the current operation to finish."
        else:
            read_reason = "Enter the RAM agent from BOTTOM, flip A17, and CRC-read the full TOP base."
        self.btn_softbsl_xbank_read.setToolTip(read_reason)

    def _clear_softbsl_crossbank_target(self, message=""):
        self._softbsl_image = None
        self._softbsl_xbank_patch_ids = []
        self._softbsl_marker_lbl.setText("—")
        self._softbsl_preview.setPlainText(message)
        self._update_softbsl_crossbank_button()

    def _compose_softbsl_crossbank_base(self):
        """Validate and compose the retained file/TOP base into a persistent T image."""
        if self._softbsl_xbank_base is None:
            return False
        raw_base = bytes(self._softbsl_xbank_base)
        resolved = MS41ECU.resolve_version(raw_base)
        supported = ("MS41.2", "MS41.3")
        if (resolved["hybrid"] or resolved["program"] not in supported
                or resolved["cal"] != resolved["program"]):
            self._clear_softbsl_crossbank_target(
                "Base rejected: a consistent MS41.2 or MS41.3 image is required.")
            QMessageBox.warning(
                self, "Golden TOP Base Rejected",
                "The base must be a complete, internally-consistent MS41.2 or MS41.3 image.\n\n"
                f"Program: {resolved['program'] or 'unknown'}\n"
                f"Calibration: {resolved['cal'] or 'unknown'}\n"
                f"Hybrid: {resolved['hybrid'] or 'no'}")
            return False

        base = raw_base
        identity_note = "identity preserved from the TOP read"
        if self._softbsl_xbank_base_origin == "file":
            if self.chk_xbank_preserve_identity.isChecked():
                source = (self._softbsl_xbank_identity_source
                          or getattr(self, "_ecu_identity_source", None))
                if not source:
                    self._clear_softbsl_crossbank_target(
                        "Identity preservation requested, but no live ECU identity snapshot is available.")
                    QMessageBox.critical(
                        self, "VIN / ISN Unavailable",
                        "Connect to the working BOTTOM bank and reload/recompose the file base so its "
                        "serial/ISN and VIN can be grafted, or explicitly uncheck identity preservation.")
                    return False
                self._softbsl_xbank_identity_source = bytes(source)
                base = identity.graft_identity(base, source)
                info = identity.decode_identity(source)
                identity_note = f"grafted serial {info.serial or '—'} / VIN {info.vin or '—'}"
            else:
                identity_note = "WARNING: file identity retained (VIN/ISN not grafted)"
        try:
            image, patch_ids, build_log = softbsl_install.compose_persistent_target(
                base, self.chk_xbank_calguard.isChecked(), marker="T", chip="29f400")
        except softbsl_install.SoftBSLInstallError as error:
            self._clear_softbsl_crossbank_target(f"Patch compose failed: {error}")
            QMessageBox.critical(self, "Golden TOP Compose Failed", str(error))
            return False
        self._softbsl_xbank_patch_ids = list(patch_ids)
        log = [f"base: {self._softbsl_xbank_base_source}",
               f"target patches: {', '.join(patch_ids)}",
               identity_note,
               *build_log]
        self._show_softbsl_image(
            image, f"{self._softbsl_xbank_base_source} → persistent patches", log)
        return True

    def _set_softbsl_crossbank_base(self, data, source, origin):
        self._softbsl_xbank_base = bytes(data)
        self._softbsl_xbank_base_source = str(source)
        self._softbsl_xbank_base_origin = origin
        self._softbsl_xbank_identity_source = (
            bytes(self._ecu_identity_source)
            if origin == "file" and getattr(self, "_ecu_identity_source", None) else None)
        return self._compose_softbsl_crossbank_base()

    def _on_softbsl_xbank_options_changed(self, _state=None):
        if self._softbsl_xbank_base is not None:
            self._compose_softbsl_crossbank_base()

    def _on_softbsl_load(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load full MS41.2 or MS41.3 base for golden TOP", "", "Binary (*.bin);;All Files (*)")
        if not path:
            return
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError as error:
            QMessageBox.critical(self, "Golden TOP Base Read Failed", str(error))
            return
        if len(data) != 262144:
            QMessageBox.warning(
                self,
                "Bad Base",
                f"Expected a 256 KB (262,144-byte) full image; received {len(data):,} bytes.",
            )
            return
        self._set_softbsl_crossbank_base(data, os.path.basename(path), "file")

    def _on_softbsl_read_top_base(self):
        if (self._ds2 is None or getattr(self, "_ecu_softbsl_marker", None) != "B"
                or self._fast_chip_family() != "amd"):
            QMessageBox.warning(
                self, "TOP Read Unavailable",
                "Connect to the Soft-BSL working BOTTOM bank of the AMD-driver 29F400 first.")
            return
        if QMessageBox.warning(
                self, "Read Golden TOP Base",
                "This is a read-only cross-bank operation. The RAM agent will enter from the intact "
                "BOTTOM bank, then prompt you to flip A17 to UPPER for the full 256 KB read and back "
                "to LOWER before reset.\n\nContinue?",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
            return
        baud = "high" if self._d2xx_ok else "low"
        prompt = self._softbsl_prompt

        def task(log_fn, progress_fn):
            return self._run_via_softbsl(
                lambda port, pf, lf: softbsl_service.read_cross_bank_image(
                    port, prompt, lf, baud=baud, progress_cb=pf),
                log_fn, progress_fn)

        def on_done(data):
            if len(data) != identity.FULL_ROM_SIZE:
                self._clear_softbsl_crossbank_target("TOP read returned an invalid image length.")
                QMessageBox.critical(
                    self, "TOP Read Failed",
                    f"TOP read returned {len(data):,} bytes; expected a 256 KB "
                    f"({identity.FULL_ROM_SIZE:,}-byte) image.")
                return
            if self._set_softbsl_crossbank_base(data, "ECU TOP read (Soft-BSL)", "top"):
                self._log("Golden TOP base read and patch composition complete.", "ok")
            else:
                self._log("TOP read completed, but the image was rejected by the MS41.2/.3 patch gates.", "error")

        self._run_task(task, on_success=on_done)

    def _acquire_softbsl_port(self, allow_handoff=False):
        """Take the port for a Soft-BSL operation.

        Install may hand an active app DS2 session to the in-process agent
        without clearing connection metadata or the cached full ROM.
        """
        port = self.cb_port.currentText()
        if not port or port.startswith("("):
            QMessageBox.warning(self, "No Serial Port", "Select a serial port in the connection bar.")
            return None
        self._softbsl_handoff_port = None
        if allow_handoff and self._port_owner.owner == "flasher":
            if self._ds2 is not None:
                try:
                    self._ds2.close()
                except Exception:
                    pass
                self._ds2 = None
            self._port_owner.release("flasher")
            self._softbsl_handoff_port = port
        try:
            self._port_owner.acquire("softbsl")
        except PortBusyError as e:
            QMessageBox.warning(self, "Port Busy",
                                f"The port is held by '{e.holder}'. Disconnect the DS2 session first.")
            return None
        return port

    def _release_softbsl_port(self, port, *, restore_ds2=True):
        """Release Soft-BSL and optionally restore a handed-off DS2 session."""
        self._port_owner.release("softbsl")
        if getattr(self, "_softbsl_handoff_port", None) == port:
            self._softbsl_handoff_port = None
            if restore_ds2:
                self._port_owner.acquire("flasher")
                self._reopen_ds2_with_retry(port, self._log)

    def _prepare_softbsl_phase1_reentry_prompt(self, port):
        """Disconnect and release COM before the install-only ignition prompt."""
        self._softbsl_handoff_port = None
        try:
            if (
                self._ds2 is not None
                or self._connection_port is not None
                or self.btn_connect.isChecked()
            ):
                signals_were_blocked = self.btn_connect.blockSignals(True)
                try:
                    self._disconnect()
                finally:
                    self.btn_connect.blockSignals(signals_were_blocked)
        finally:
            # The worker reaches this callback only after cmd_deploy_splice has
            # unwound, closing its probe and the native service's D2XX handle.
            self._port_owner.release("softbsl")

    def _reacquire_softbsl_port_after_phase1_reentry(self, port):
        """Retake logical ownership without reopening ordinary 9600-baud DS2."""
        try:
            self._port_owner.acquire("softbsl")
        except PortBusyError as error:
            QMessageBox.warning(
                self,
                "Soft-BSL Installation Cancelled",
                f"The serial port could not be reacquired because it is held by "
                f"'{error.holder}'. The installation will stop without writing the ECU.",
            )
            return False
        return True

    def _fast_chip_family(self):
        """The detected flash driver family ('amd'/'intel'/None) for picking the soft-BSL agent —
        an Intel 28F200 needs agent_28f.hex, not the AMD agent. From the connect-time chip probe."""
        return ecu_info.chip_family(getattr(self, "_ecu_chip_sig", b"") or b"")

    def _active_write_recovery(self):
        """Return (kind, recovery) for the one retained post-erase session, if any."""
        install = getattr(self, "_softbsl_install_recovery", None)
        if install is not None and install.is_open:
            return "Soft-BSL installer", install
        soft = getattr(self, "_softbsl_write_recovery", None)
        if soft is not None and soft.is_open:
            return "Soft-BSL RAM-agent", soft
        native = getattr(self, "_native_write_recovery", None)
        if native is not None and native.is_open:
            return "native-fast", native
        return None, None

    def _offer_active_flash_recovery(self, failure_summary: str) -> bool:
        """Offer an immediate retained retry, or leave the live session pending.

        The timer defers an immediate retry until the failed worker's completion callback has
        returned. Starting a second worker inside that callback would let the first worker's
        final button-state restore race with the recovery worker.
        """
        recovery_kind, recovery = self._active_write_recovery()
        if recovery is None:
            return False
        self.btn_native_recovery.setVisible(True)
        self.btn_native_recovery.setEnabled(True)
        answer = QMessageBox.warning(
            self,
            "FLASH INCOMPLETE — KEEP IGNITION ON",
            f"{failure_summary}\n\n"
            "DO NOT TURN IGNITION OFF or disconnect the adapter.\n"
            f"The ECU and {recovery_kind} session are still live.\n\n"
            "Choose Retry to re-erase and re-flash the same corrected target now. "
            "Choose Cancel only to leave recovery pending; the session stays open and "
            "Retry Flash Recovery remains available.",
            QMessageBox.Retry | QMessageBox.Cancel,
            QMessageBox.Retry,
        )
        if answer == QMessageBox.Retry:
            QTimer.singleShot(0, lambda: self._start_native_flash_recovery(confirmed=True))
        return True

    def _run_via_softbsl(
            self, op_fn, log_fn, progress_fn, *, restore_after_success=True):
        """Switch the shared app transport from framed DS2 to RAM-agent mode,
        then normally reopen framed DS2 after ordinary completion. A caller that
        knows its completed target removes the persistent Soft-BSL entry path may
        leave the app disconnected so the next connection performs fresh route
        detection. Pre-erase failures still restore DS2, while post-erase failures
        retain the RAM-agent handle and port ownership for in-place recovery."""
        # This method runs inside WorkerThread. Never read a QWidget here: the
        # selected port is snapshotted on the GUI thread when the DS2 session opens.
        if (self._softbsl_write_recovery is not None
                and self._softbsl_write_recovery.is_open):
            raise RuntimeError(
                "A Soft-BSL flash recovery session is already active. Retry that recovery "
                "before starting another operation."
            )
        port = self._connection_port
        if not port:
            raise RuntimeError("No connected serial port is available for the Soft-BSL handoff.")
        if self._ds2 is not None:
            try:
                self._ds2.close()
            except Exception:
                pass
            self._ds2 = None
        self._port_owner.release("flasher")
        self._port_owner.acquire("softbsl")
        hold_for_recovery = False
        completed = False
        try:
            result = op_fn(port, progress_fn, log_fn)
            completed = True
            return result
        except softbsl_service.SoftBSLWriteRecoveryRequired as error:
            # Do not close/reopen/downshift here: the agent is already RAM-resident at the
            # active baud and the erased target must be re-flashed before ignition is cycled.
            hold_for_recovery = True
            self._softbsl_write_recovery = error.recovery
            raise
        finally:
            if not hold_for_recovery:
                self._port_owner.release("softbsl")
                if restore_after_success or not completed:
                    self._port_owner.acquire("flasher")
                    self._reopen_ds2_with_retry(port, log_fn)

    def _run_via_native_fast_ds2(self, op_fn, log_fn, progress_fn):
        """Hand the connected K-Line adapter to the stock native-fast session."""
        port = self._connection_port
        if not port:
            raise RuntimeError("No connected serial port is available for native fast DS2.")
        if self._ds2 is not None:
            try:
                self._ds2.close()
            except Exception:
                pass
            self._ds2 = None
        self._port_owner.release("flasher")
        self._port_owner.acquire("native_fast_ds2")
        try:
            return op_fn(port, progress_fn, log_fn)
        finally:
            self._port_owner.release("native_fast_ds2")
            self._port_owner.acquire("flasher")
            progress_fn(0, 0, "Reopening normal DS2 at 9600")
            self._reopen_ds2_with_retry(port, log_fn)

    def _native_fast_read_with_fallback(self, which, log_fn, progress_fn):
        """Try direct 187500 native DS2, then restart wholly at normal DS2 if safe."""
        def event_cb(event, fields):
            # All transport events remain in the durable native-fast journal.
            # Mirror only state changes that help the operator decide what to do.
            if event == "automatic_read_recovery":
                level = "warn" if fields.get("recovered") else "error"
                log_fn(
                    "Native DS2 read recovery "
                    + ("confirmed normal low state." if fields.get("recovered")
                       else "could not confirm normal low state."),
                    level)
            elif event == "native_fast_read_reentry_wait_started":
                log_fn(
                    "Waiting for the ECU native-fast completion latch "
                    f"(E72E={fields.get('e72e')}, "
                    f"E659=0x{fields.get('e659', 0):02X})."
                )
            elif event == "native_fast_read_reentry_ready":
                log_fn(
                    "Native-fast selector rearmed after "
                    f"{fields.get('elapsed_s', 0):.2f} seconds "
                    f"(E72E={fields.get('e72e')}, "
                    f"E659=0x{fields.get('e659', 0):02X}).",
                    "ok",
                )
            elif event == "native_fast_read_reentry_blocked":
                log_fn(
                    "Native-fast selector entry blocked before rate change: "
                    f"{fields.get('reason', 'unknown state')} "
                    f"(E72E={fields.get('e72e')}, "
                    f"E659=0x{fields.get('e659', 0):02X}).",
                    "warn",
                )

        try:
            result = self._run_via_native_fast_ds2(
                lambda port, pf, _lf: (
                    ds2_fast_read.read_full_d2xx(
                        port, progress_cb=pf, event_cb=event_cb,
                        echo=self._connection_echo)
                    if which == "full"
                    else ds2_fast_read.read_partial_d2xx(
                        port, progress_cb=pf, event_cb=event_cb,
                        echo=self._connection_echo)
                ),
                log_fn,
                progress_fn,
            )
            return bytes(result.file_image if which == "full" else result.data)
        except ds2_fast_read.NativeFastReadReentryNotReady:
            # This is an ECU-side stale/rearming state, not a transport
            # capability failure.  Reopening normal DS2 is safe, but silently
            # falling back would hide the required ignition cycle.
            raise
        except Exception as error:
            # The read-only native session performs bounded high/low recovery.
            # Only a successfully reopened normal DS2 session authorizes a
            # whole-operation fallback; an unknown physical rate fails closed.
            if self._ds2 is None:
                raise RuntimeError(
                    f"Native fast DS2 failed and normal low state was not confirmed: {error}"
                ) from error
            log_fn(
                f"Native fast DS2 failed before completion ({error}). "
                "Restarting the entire read at normal DS2 9600.",
                "warn",
            )
            return bytes(self._ds2_read(which, progress_fn, log_fn))

    def _run_via_native_fast_write(
        self,
        op_fn,
        log_fn,
        progress_fn,
        *,
        reopen_after_success: bool,
    ):
        """Run a native destructive state machine with recovery-aware ownership."""
        port = self._connection_port
        if not port:
            raise RuntimeError("No connected serial port is available for native fast DS2.")
        if self._ds2 is not None:
            try:
                self._ds2.close()
            except Exception:
                pass
            self._ds2 = None
        self._port_owner.release("flasher")
        self._port_owner.acquire("native_fast_ds2")
        hold_for_recovery = False
        succeeded = False
        try:
            result = op_fn(port, progress_fn, log_fn)
            succeeded = True
            return result
        except ds2_native_fast_service.NativeWriteRecoveryRequired as error:
            # Deliberately retain both D2XX and the port-owner claim.  Closing,
            # reopening, downshifting, or cycling here would discard the stock
            # ECU's recoverable RAM listener.
            hold_for_recovery = True
            self._native_write_recovery = error.recovery
            raise
        finally:
            if not hold_for_recovery:
                self._port_owner.release("native_fast_ds2")
                self._port_owner.acquire("flasher")
                if not succeeded or reopen_after_success:
                    progress_fn(0, 0, "Reopening normal DS2 at 9600")
                    self._reopen_ds2_with_retry(port, log_fn)

    def _native_fast_write_with_fallback(
        self,
        which,
        image_bytes,
        connected_family,
        log_fn,
        progress_fn,
        *,
        verify_write,
    ):
        """Try native high rate, then restart at 9600 only if no erase began."""
        recovery_kind, recovery = self._active_write_recovery()
        if recovery is not None:
            raise RuntimeError(
                f"A {recovery_kind} flash recovery session is already active. Retry that "
                "recovery before starting another write."
            )
        self._require_previous_write_cycle(log_fn)
        reopen_after_success = which == "tune"

        def operation(port, pf, _lf):
            if which == "tune":
                return ds2_native_fast_service.write_partial_d2xx(
                    port,
                    bytes(image_bytes),
                    verify_write=verify_write,
                    progress_cb=pf,
                )
            return ds2_native_fast_service.write_full_d2xx(
                port,
                bytes(image_bytes),
                connected_family=connected_family,
                verify_write=verify_write,
                progress_cb=pf,
            )

        try:
            return self._run_via_native_fast_write(
                operation,
                log_fn,
                progress_fn,
                reopen_after_success=reopen_after_success,
            )
        except ds2_native_fast_service.NativeWriteRecoveryRequired:
            raise
        except ds2_native_fast_service.NativeFastPreEraseFailure as error:
            if error.reentry_not_ready:
                raise StockWriteNotStarted(
                    f"{error}. Normal DS2 at 9600 was restored and nothing was "
                    "erased. Turn ignition OFF, wait at least 10 seconds, turn "
                    "ignition ON, then retry."
                ) from error
            if error.seed_unavailable:
                raise StockWriteNotStarted(
                    "The ECU remained safely locked and did not make a write seed "
                    "available. Normal DS2 at 9600 was restored and nothing was erased. "
                    "A slow-write fallback would repeat the same authorization request, "
                    "so it was not attempted. Turn ignition OFF, wait at least 10 seconds, "
                    "turn ignition ON, then retry."
                ) from error
            if not error.safe_legacy_fallback or self._ds2 is None:
                raise RuntimeError(
                    f"Native fast write failed before erase, but normal low state "
                    f"was not confirmed: {error}"
                ) from error
            log_fn(
                f"Native fast write could not establish a reliable high-rate session "
                f"before erase ({error}). Restarting the complete write at DS2 9600.",
                "warn",
            )
            self._ds2_write(which, bytes(image_bytes), progress_fn, log_fn)
            if verify_write:
                self._ds2_verify_after_write(which, bytes(image_bytes), log_fn, progress_fn)
            return None
        except Exception as error:
            # Transport setup failed before a session could erase anything.  The
            # ownership wrapper has already reopened and identified normal DS2;
            # that confirmed low-rate link is the authority for a full restart.
            if self._ds2 is None:
                raise
            log_fn(
                f"Native fast transport setup failed before erase ({error}). "
                "Restarting the complete write at DS2 9600.",
                "warn",
            )
            self._ds2_write(which, bytes(image_bytes), progress_fn, log_fn)
            if verify_write:
                self._ds2_verify_after_write(which, bytes(image_bytes), log_fn, progress_fn)
            return None

    def _start_softbsl_install_recovery(self, confirmed=False):
        """Resume the interrupted installer through its exact retained transport."""
        recovery = self._softbsl_install_recovery
        if recovery is None or not recovery.is_open:
            QMessageBox.warning(
                self,
                "Installer Recovery Unavailable",
                "No live Soft-BSL installer recovery session is available.",
            )
            return
        phase_name = (
            "temporary program-only bootstrap"
            if recovery.phase == "bootstrap"
            else "persistent boot/program target"
        )
        if not confirmed and QMessageBox.warning(
            self,
            "Retry Soft-BSL Installation",
            "The ECU must remain powered and the adapter must stay connected.\n\n"
            f"The {phase_name} will be re-erased and re-flashed through the same "
            "retained session. After it succeeds, the installer will continue its "
            "remaining phase(s) and final validation.\n\nProceed?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) != QMessageBox.Yes:
            return

        def task(log_fn, progress_fn):
            log_fn(
                "Resuming retained Soft-BSL installation. DO NOT TURN IGNITION OFF.",
                "warn",
            )
            try:
                return softbsl_install.resume_install(
                    recovery,
                    log_fn,
                    progress_cb=progress_fn,
                )
            except softbsl_install.SoftBSLInstallRecoveryRequired as error:
                self._softbsl_install_recovery = error.recovery
                raise

        def on_success(_result):
            self._softbsl_install_recovery = None
            self.btn_native_recovery.setVisible(False)
            self.btn_native_recovery.setEnabled(False)
            self._on_softbsl_install_success(recovery.port)

        def on_failure(error):
            current = self._softbsl_install_recovery
            still_live = current is not None and current.is_open
            self.btn_native_recovery.setVisible(still_live)
            self.btn_native_recovery.setEnabled(still_live)
            self._log(f"Soft-BSL installer recovery failed: {error}", "error")
            if still_live:
                QMessageBox.critical(
                    self,
                    "INSTALL RECOVERY INCOMPLETE - KEEP IGNITION ON",
                    f"Recovery failed again:\n{error}\n\n"
                    "DO NOT TURN IGNITION OFF or disconnect the adapter. The same "
                    "installer recovery session remains open and can be retried.",
                )
                return
            self._softbsl_install_recovery = None
            self._release_softbsl_port(recovery.port)
            QMessageBox.critical(
                self,
                "Soft-BSL Installation Recovery Failed",
                f"{error}\n\nThe retained session is no longer open. Inspect the ECU "
                "connection/state before starting another operation.",
            )

        self._run_task(task, on_success=on_success, on_failure=on_failure)

    def _start_softbsl_flash_recovery(self, confirmed=False):
        """Resume the same target through the retained Soft-BSL RAM-agent session."""
        recovery = self._softbsl_write_recovery
        if recovery is None or not recovery.is_open:
            QMessageBox.warning(
                self,
                "Soft-BSL Recovery Unavailable",
                "No live Soft-BSL RAM-agent recovery session is available.",
            )
            return
        verify_note = (
            "Read-back verification will run because Verify was selected."
            if recovery.do_verify
            else "Read-back verification is disabled, matching the original write request."
        )
        target_name = "24 KB calibration" if recovery.operation == "tune" else "full ROM image"
        if not confirmed and QMessageBox.warning(
            self,
            "Retry Soft-BSL Flash Recovery",
            "The ECU must still be powered and the adapter must remain connected.\n\n"
            f"The same prepared {target_name} will be re-erased and re-flashed "
            "through the retained RAM agent at its current baud.\n\n"
            f"{verify_note}\n\nProceed?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) != QMessageBox.Yes:
            return

        def task(log_fn, progress_fn):
            log_fn(
                "Resuming retained Soft-BSL recovery. DO NOT TURN IGNITION OFF.",
                "warn",
            )
            try:
                result = softbsl_service.resume_write_recovery(
                    recovery,
                    progress_cb=progress_fn,
                    log=log_fn,
                )
            except softbsl_service.SoftBSLWriteRecoveryRequired:
                raise
            except Exception:
                # Finalization may have reset the ECU even if confirmation failed.  If the service
                # closed the retained handle, release ownership and attempt a normal DS2 reopen.
                if not recovery.is_open:
                    self._port_owner.release("softbsl")
                    self._port_owner.acquire("flasher")
                    self._reopen_ds2_with_retry(recovery.port, log_fn)
                raise
            self._port_owner.release("softbsl")
            self._port_owner.acquire("flasher")
            self._reopen_ds2_with_retry(recovery.port, log_fn)
            return result

        def on_success(_result):
            self._softbsl_write_recovery = None
            self.btn_native_recovery.setVisible(False)
            self.btn_native_recovery.setEnabled(False)
            message = (
                "Soft-BSL flash recovery completed and read-back verification passed."
                if recovery.do_verify
                else f"Soft-BSL flash recovery completed. {VERIFY_OFF_MESSAGE}"
            )
            self._finish_flash_success("Flash Recovery Complete", message)

        def on_failure(error_msg):
            still_live = recovery.is_open
            if not still_live:
                self._softbsl_write_recovery = None
            self._log(f"Soft-BSL flash recovery failed: {error_msg}", "error")
            self.btn_native_recovery.setVisible(still_live)
            self.btn_native_recovery.setEnabled(still_live)
            if still_live:
                QMessageBox.critical(
                    self,
                    "RECOVERY INCOMPLETE — KEEP IGNITION ON",
                    f"Recovery failed again:\n{error_msg}\n\n"
                    "DO NOT TURN IGNITION OFF or disconnect the adapter. The RAM-agent "
                    "session remains open and can be retried.",
                )
            else:
                QMessageBox.critical(
                    self,
                    "Soft-BSL Finalization Unconfirmed",
                    f"The recovery write returned an error after the retained session closed:\n"
                    f"{error_msg}\n\nInspect the ECU connection/state before another operation.",
                )

        self._run_task(task, on_success=on_success, on_failure=on_failure)

    def _start_native_flash_recovery(self, confirmed=False):
        """Resume the same corrected target on the retained D2XX session."""
        if (self._softbsl_install_recovery is not None
                and self._softbsl_install_recovery.is_open):
            return self._start_softbsl_install_recovery(confirmed=confirmed)
        if (self._softbsl_write_recovery is not None
                and self._softbsl_write_recovery.is_open):
            return self._start_softbsl_flash_recovery(confirmed=confirmed)
        recovery = self._native_write_recovery
        if recovery is None or not recovery.is_open:
            QMessageBox.warning(
                self,
                "Native Recovery Unavailable",
                "No live native-fast recovery session is available.",
            )
            return
        verify_note = (
            "Read-back verification will run because Verify was selected."
            if getattr(recovery.session, "verify_write", False)
            else "Verify remains off for this recovery attempt."
        )
        if not confirmed and QMessageBox.warning(
            self,
            "Retry Native Flash Recovery",
            "The ECU must still be powered and the adapter must remain connected.\n\n"
            "The same prepared target will be re-erased and re-flashed "
            "using the retained high-rate session.\n\n"
            f"{verify_note}\n\nProceed?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) != QMessageBox.Yes:
            return

        def task(log_fn, progress_fn):
            log_fn(
                "Resuming retained native-fast recovery. DO NOT TURN IGNITION OFF.",
                "warn",
            )
            result = ds2_native_fast_service.resume_recovery(
                recovery,
                progress_cb=progress_fn,
            )
            self._port_owner.release("native_fast_ds2")
            self._port_owner.acquire("flasher")
            if result.final_link.name.lower() == "low":
                self._reopen_ds2_with_retry(recovery.port, log_fn)
            return result

        def on_success(result):
            self._native_write_recovery = None
            self.btn_native_recovery.setVisible(False)
            self.btn_native_recovery.setEnabled(False)
            message = (
                "Native flash recovery completed and read-back verification passed."
                if getattr(result, "verified", False)
                else f"Native flash recovery completed. {VERIFY_OFF_MESSAGE}"
            )
            self._finish_flash_success("Flash Recovery Complete", message)
            if result.final_link.name.lower() == "high":
                self._disconnect()

        def on_failure(error_msg):
            self._log(f"Native flash recovery failed: {error_msg}", "error")
            self.btn_native_recovery.setVisible(True)
            self.btn_native_recovery.setEnabled(True)
            QMessageBox.critical(
                self,
                "RECOVERY INCOMPLETE — KEEP IGNITION ON",
                f"Recovery failed again:\n{error_msg}\n\n"
                "DO NOT TURN IGNITION OFF or disconnect the adapter. The recovery "
                "session remains open and can be retried.",
            )

        self._run_task(task, on_success=on_success, on_failure=on_failure)

    def _mark_ds2_reconnect_failed(self):
        """Expose a failed handoff restore as a real, reusable disconnection.

        Preserve cached ECU/file evidence for a same-ECU reconnect, but never
        leave the logical ``flasher`` owner or green connection UI behind when
        no DS2 transport exists.
        """
        self._ds2 = None
        self._port_owner.release("flasher")
        self._connection_port = None
        self._d2xx_checked = False
        self._d2xx_ok = False
        self._update_d2xx_warning()
        self.lbl_status.setText("● Disconnected")
        self.lbl_status.setStyleSheet("color:#999; font-weight:bold;")
        self.lbl_variant.setText("")
        self.btn_connect.setText("Connect")
        self.btn_connect.setChecked(False)
        self._set_ecu_buttons_enabled(False)
        self._update_softbsl_install_options()

    def _reopen_ds2_with_retry(self, port, log_fn, attempts=12, delay=0.3):
        """Reopen the plain DS2 connection after a Fast op. The soft-BSL recovery WDT-reboots the
        ECU, so for the first ~1-2 s the port may open while the ECU is still booting and not yet
        answering — confirm with an identify() and retry until it responds (these early retries are
        expected, not errors, so they aren't logged per-attempt). self._ds2 stays None if the ECU
        never comes back."""
        last_err = None
        for attempt in range(1, attempts + 1):
            try:
                self._ds2 = DS2Interface(
                    port=port, baud=9600, verbose=False, echo=self._connection_echo)
                self._ds2.open()
                self._ds2.identify()          # confirm the ECU is answering, not just that the port opened
                self._d2xx_checked = True
                self._d2xx_ok = bool(getattr(self._ds2, "uses_d2xx", False))
                if attempt > 1:
                    log_fn(f"ECU back up after the Fast operation (attempt {attempt}).", "ok")
                return True
            except Exception as e:
                last_err = e
                try:
                    if self._ds2:
                        self._ds2.close()
                except Exception:
                    pass
                self._ds2 = None
                time.sleep(delay)
        log_fn(f"Could not reconnect to {port} after the Fast operation ({last_err}). "
              f"Key-cycle the ECU and press Connect.", "error")
        self._mark_ds2_reconnect_failed()
        return False

    def _on_softbsl_cross_bank(self):
        if not self._softbsl_image:
            QMessageBox.information(
                self, "Prepare Golden TOP First",
                "Load a consistent MS41.2 or MS41.3 base, or connect to the Soft-BSL BOTTOM bank and use "
                "Read TOP Base + Compose. The patch engine must produce the T-marked target "
                "before the brick-class write can be armed.")
            return
        if (len(self._softbsl_image) != identity.FULL_ROM_SIZE
                or softbsl_service.marker(self._softbsl_image) != "T"):
            QMessageBox.warning(
                self, "Not a Golden TOP Image",
                "Cross-bank requires a complete 256 KB golden-TOP image with bank marker 'T'. "
                "The loaded image cannot be used for this operation.")
            return
        chip_family = self._fast_chip_family()
        try:
            softbsl_service.validate_flash_image_family(
                self._softbsl_image, chip_family, write_bootloader=True)
        except softbsl_service.FlashFamilyMismatchError as error:
            QMessageBox.critical(self, "Flash-Chip Family Mismatch", str(error))
            self._log(f"Cross-bank write blocked — {error}", "error")
            return
        port = self._acquire_softbsl_port()
        if not port:
            return
        text, ok = QInputDialog.getText(
            self, "BRICK-CLASS — Cross-bank golden TOP write",
            "This writes all four coarse sectors on the physical 29F400 TOP half, including the "
            "FUSED SA7 boot sector. The AMD driver signature cannot distinguish a 29F200 from a "
            "29F400; proceed only on confirmed dual-bank hardware with the A17 switch.\n\n"
            f"Base: {self._softbsl_xbank_base_source or 'prepared image'}\n"
            f"Persistent patches: {', '.join(self._softbsl_xbank_patch_ids) or 'pre-composed'}\n\n"
            "You will be prompted to flip A17. Recoverable only if booted from the intact bottom.\n\n"
            "Type  FLASH TOP  to proceed:")
        if not ok or text.strip() != "FLASH TOP":
            self._port_owner.release("softbsl")
            return
        image = self._softbsl_image
        prompt = self._softbsl_prompt
        def task(log_fn, progress_fn):
            softbsl_service.run_cross_bank(
                port, image, prompt, log_fn, chip_family=chip_family)
        self._run_task(task,
                       on_success=lambda _r: (self._port_owner.release("softbsl"),
                                              self._log("Cross-bank TOP write complete and verified; "
                                                        "A17 returned to LOWER and the ECU reset", "ok")),
                       on_failure=self._on_softbsl_crossbank_failure)

    def _on_softbsl_crossbank_failure(self, error):
        """Make the required safe A17 recovery state impossible to miss in the log."""
        self._port_owner.release("softbsl")
        self._log(f"Cross-bank failed: {error}", "error")
        QMessageBox.critical(
            self, "Cross-bank Write Stopped",
            "The golden-TOP operation did not complete.\n\n"
            "Before resetting or key-cycling, make sure the A17 switch is back in the LOWER "
            "position so the ECU boots from the intact working bank. The TOP bank must not be "
            f"trusted until it is written and verified successfully.\n\nDetails: {error}")

    def _ecu_is_ms41_3(self):
        """Best-effort: is the connected ECU running MS41.3? From the connect-time program/variant
        detection (ABHISHEK / SS1v2 markers). The install engine re-checks this authoritatively and
        refuses a mismatch, so a wrong guess here is safe — it only picks which dialog path runs."""
        return (getattr(self, "_ecu_program_variant", None) == "MS41.3"
                or getattr(self, "_ecu_variant", None) == "MS41.3")

    def _ecu_patch_version(self):
        """Best-effort consistent patch target for the connected ECU."""
        program = getattr(self, "_ecu_program_variant", None)
        calibration = getattr(self, "_ecu_variant", None)
        if program in ("MS41.2", "MS41.3") and calibration in (None, program):
            return program
        if program is None and calibration in ("MS41.2", "MS41.3"):
            return calibration
        return None

    def _target_patch_version(self):
        return self._ecu_patch_version() or getattr(self, "_softbsl_last_version", None)

    def _update_softbsl_install_options(self):
        """Expose cal preservation for a live, consistent MS41.2 or MS41.3 ECU."""
        if self._ds2 is None:
            self.chk_install_preserve_cal.setChecked(True)
            self.chk_install_preserve_cal.setEnabled(False)
        elif self._ecu_patch_version() in ("MS41.2", "MS41.3"):
            self.chk_install_preserve_cal.setEnabled(True)
        else:
            self.chk_install_preserve_cal.setChecked(False)
            self.chk_install_preserve_cal.setEnabled(False)

    def _graft_softbsl_target(self, target_path):
        """Graft this ECU's serial/ISN/VIN into a selected full base image.

        Prefer a cached full ROM when present; otherwise use the two-field identity
        snapshot captured during connection. No 256 KB ECU read is required.
        """
        source, info = self._identity_graft_source()
        if not source:
            return target_path, None
        with open(target_path, "rb") as f:
            target_bytes = f.read()
        if len(target_bytes) != identity.FULL_ROM_SIZE:
            return target_path, None
        grafted = identity.graft_identity(target_bytes, source)
        fd, out_path = tempfile.mkstemp(suffix="_grafted.bin", prefix="softbsl_target_")
        with os.fdopen(fd, "wb") as f:
            f.write(grafted)
        return out_path, info

    def _select_softbsl_base_file(self):
        """Pick and validate a complete, internally-consistent MS41.2 or MS41.3 base."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a full MS41.2 or MS41.3 base .bin", "",
            "Binary (*.bin);;All files (*)")
        if not path:
            return None
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError as error:
            QMessageBox.critical(self, "Base Image Read Failed", f"Could not read the selected image:\n{error}")
            return None
        if len(data) != identity.FULL_ROM_SIZE:
            QMessageBox.warning(
                self, "Invalid Base Image",
                "The base must be a 256 KB full MS41.2 or MS41.3 image "
                f"({identity.FULL_ROM_SIZE:,} bytes); received {len(data):,} bytes.")
            return None
        program_variant = MS41ECU.detect_program_variant(data)
        cal_variant = MS41ECU.detect_variant(data)
        if (program_variant not in ("MS41.2", "MS41.3")
                or cal_variant != program_variant):
            QMessageBox.warning(
                self, "Invalid Base Image",
                f"The selected base is not a consistent MS41.2 or MS41.3 image "
                f"(program={program_variant or 'unknown'}, cal={cal_variant or 'unknown'}).")
            return None
        return path

    def _on_softbsl_install(self):
        port = self._acquire_softbsl_port(allow_handoff=True)
        if not port:
            return
        prompt = self._softbsl_prompt
        with_calguard = self.chk_install_calguard.isChecked()
        force_base = self.chk_install_force_base.isChecked()
        preserve_identity = self.chk_install_preserve_identity.isChecked()
        live_version = self._target_patch_version()
        target_version = live_version
        forced_base_path = None
        if force_base:
            forced_base_path = self._select_softbsl_base_file()
            if not forced_base_path:
                self._release_softbsl_port(port)
                return
            try:
                with open(forced_base_path, "rb") as selected_base:
                    target_version = MS41ECU.resolve_version(selected_base.read())["program"]
            except OSError as error:
                self._release_softbsl_port(port)
                QMessageBox.critical(
                    self, "Base Image Read Failed",
                    f"Could not reopen the selected base image:\n{error}")
                return
            if preserve_identity:
                try:
                    forced_base_path, forced_info = self._graft_softbsl_target(forced_base_path)
                except Exception as error:
                    self._release_softbsl_port(port)
                    QMessageBox.critical(self, "Identity Preservation Failed",
                                         f"Could not preserve the connected ECU's VIN/ISN:\n{error}")
                    return
                if not forced_info:
                    self._release_softbsl_port(port)
                    QMessageBox.critical(
                        self, "Identity Data Unavailable",
                        "Preserve VIN / ISN is checked, but no valid live identity snapshot is "
                        "available. Reconnect and retry, or explicitly uncheck identity preservation.")
                    return
        preserve_cal = (self.chk_install_preserve_cal.isChecked()
                        and live_version == target_version
                        and target_version in ("MS41.2", "MS41.3"))
        reinstall_preconfirmed = False
        installed_marker = getattr(self, "_ecu_softbsl_marker", None)
        if installed_marker:
            if QMessageBox.question(
                    self, "Confirm Soft-BSL Reinstallation",
                    f"This ECU already reports a Soft-BSL installation (bank marker "
                    f"{installed_marker!r}).\n\nDo you want to continue and reinstall it? "
                    f"This will rewrite the boot and program regions. Continue only if you "
                    f"intentionally want to repair or replace the existing installation.",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                self._release_softbsl_port(port)
                return
            reinstall_preconfirmed = True

        if (target_version in ("MS41.2", "MS41.3")
                and live_version == target_version):
            # Reuse this session's full read when available. It is already tied
            # to the connected ECU and avoids another ~5 minute stock-DS2 read.
            cached_base = (None if force_base else
                           bytes(self._last_full_read)
                           if self._last_full_read
                           and len(self._last_full_read) == identity.FULL_ROM_SIZE else None)
            base_note = ("uses the selected base .bin without an ECU full read"
                         if force_base else
                         "reuses this session's cached full read"
                         if cached_base is not None else
                         "reads this ECU once as the patch base")
            identity_note = ("The connected ECU's VIN and ISN will be preserved."
                             if (preserve_identity or not force_base) else
                             "VIN/ISN preservation is disabled; identity data from the base may be written.")
            if QMessageBox.warning(
                    self, "Confirm Soft-BSL Installation",
                    f"Port: {port}\nSource: {base_note}.\n\n"
                    f"Calibration: {'preserved from this ' + target_version + ' ECU' if preserve_cal else 'replaced from the base image'}.\n"
                    f"Identity: {identity_note}\n\n"
                    "Installation requires one ignition cycle and writes the ECU's boot/parameter "
                    "and program regions. Keep stable power connected. An interrupted boot-region "
                    "write may require hardware BSL recovery.\n\nContinue?",
                    QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
                self._release_softbsl_port(port)
                return
            base_path = forced_base_path if force_base else cached_base
            allow_convert = False
        else:
            # Unsupported/unknown live firmware, or an explicitly selected cross-version base:
            # conversion is a FULL write that replaces the calibration.
            variant = (self._ecu_program_variant or self._ecu_variant
                       or "an unsupported/unknown MS41 variant")
            conversion_target = target_version or "MS41.3"
            target_label = target_version or "the selected MS41.2/MS41.3 target"
            conversion_title, conversion_risk, involves_ms410 = self._conversion_warning_policy(
                variant, conversion_target, True)
            if not involves_ms410:
                conversion_title = f"Confirm {target_label} Conversion"
            if QMessageBox.warning(
                    self, conversion_title,
                    f"This ECU appears to be {variant}; the Soft-BSL target is {target_label}.\n\n"
                    f"Conversion erases and replaces the current calibration "
                    "and writes the target boot/parameter region.\n\n"
                    f"{conversion_risk}\n\n"
                    f"{'Continue with the selected base image?' if force_base else 'Continue and pick a consistent MS41.2 or MS41.3 base image?'}",
                    QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
                self._release_softbsl_port(port)
                return
            path = forced_base_path if force_base else self._select_softbsl_base_file()
            if not path:
                self._release_softbsl_port(port)
                return
            if force_base:
                base_path, allow_convert = path, True
            elif preserve_identity:
                # Preserve THIS ECU's per-unit identity by grafting it onto the base before it is composed,
                # so the convert keeps the ISN/VIN and EWS stays paired. Needs a cached full read of this ECU.
                # Guarded: any I/O failure here must release the port (unlike an early return, this call runs
                # after acquire()), or the port stays wedged as 'softbsl' until restart.
                try:
                    base_path, info = self._graft_softbsl_target(path)
                except Exception as e:
                    self._release_softbsl_port(port)
                    QMessageBox.critical(self, "Base Image Preparation Failed",
                                         f"Could not prepare the selected base image:\n{e}")
                    return
                allow_convert = True
                if info:
                    if QMessageBox.question(
                            self, "Confirm Identity Preservation",
                            f"The connected ECU's identity will be carried onto the base:\n"
                            f"  Serial : {info.serial or '—'}\n  ISN    : {info.isn4 or '—'}\n"
                            f"  VIN    : {info.vin or '—'}\n\nProceed with the conversion?",
                            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
                        self._release_softbsl_port(port)
                        return
                else:
                    self._release_softbsl_port(port)
                    QMessageBox.critical(
                        self, "Identity Data Unavailable",
                        "Preserve VIN / ISN is checked, but no valid live identity snapshot is "
                        "available. Reconnect and retry, or explicitly uncheck identity preservation.")
                    return
            else:
                base_path, allow_convert = path, True
                if QMessageBox.warning(
                        self, "Confirm Identity Replacement",
                        "Preserve VIN / ISN is unchecked. The selected base image's serial/ISN/VIN "
                        "will be written and EWS may require re-alignment. Continue?",
                        QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
                    self._release_softbsl_port(port)
                    return

        def task(log_fn, progress_fn):
            install_baud = "high" if self._d2xx_ok else "low"
            reinstall_confirm = ((lambda _message: True)
                                 if reinstall_preconfirmed else self._softbsl_confirm)
            try:
                rc = softbsl_install.install_compose(
                    port,
                    base_path,
                    with_calguard,
                    allow_convert,
                    prompt,
                    log_fn,
                    baud=install_baud,
                    progress_cb=progress_fn,
                    confirm_reinstall=reinstall_confirm,
                    preserve_cal=preserve_cal,
                )
            except softbsl_install.SoftBSLInstallRecoveryRequired as error:
                # Keep the installer-owned port claim and exact live transport.
                # The completion callback must not reopen DS2 after erase.
                self._softbsl_install_recovery = error.recovery
                raise
            if rc != 0:
                raise RuntimeError(f"installation returned status {rc}")
        self._run_task(task,
                       on_success=lambda _r: self._on_softbsl_install_success(port),
                       on_failure=lambda error: self._on_softbsl_install_failure(port, error))

    def _on_softbsl_install_success(self, port):
        """Leave the app disconnected after a verified persistent installation."""
        self._log("Soft-BSL installation completed and verified.", "ok")
        self._release_softbsl_port(port, restore_ds2=False)
        # The Connect toggle remains checked during the install handoff. Block
        # its toggled(False) signal so this explicit teardown runs exactly once.
        signals_were_blocked = self.btn_connect.blockSignals(True)
        try:
            self._disconnect()
        finally:
            self.btn_connect.blockSignals(signals_were_blocked)
        QMessageBox.information(
            self, "Soft-BSL Installed",
            "Soft-BSL was installed and verified successfully.\n\n"
            "The ECU connection was closed intentionally. Press Connect to identify the new "
            "installation; supported reads and writes will then use Soft-BSL automatically.")

    def _on_softbsl_install_failure(self, port, error):
        """Retain post-erase sessions; release the port for ordinary failures."""
        if isinstance(error, softbsl_install.SoftBSLInstallCancelled):
            self._release_softbsl_port(port)
            self._log(f"Soft-BSL installation paused safely: {error}", "warn")
            if getattr(error, "phase", None) == "pre_phase1":
                QMessageBox.information(
                    self,
                    "Soft-BSL Installation Cancelled",
                    "The installation was cancelled before the temporary Phase 1 write. "
                    "No challenge, selector, erase, or flash command was sent, and nothing "
                    "was erased.\n\nThe ECU connection remains closed. Press Connect when "
                    "you are ready to identify the ECU again.",
                )
                return
            QMessageBox.information(
                self,
                "Soft-BSL Installation Paused",
                "The temporary Phase 1 entry path was written, but Phase 2 was not started. "
                "No persistent-image erase occurred.\n\n"
                "Complete the ignition OFF → wait approximately 10 seconds → ignition ON "
                "cycle, reconnect if necessary, and run Install Soft-BSL again.",
            )
            return
        if isinstance(error, softbsl_install.SoftBSLInstallRecoveryRequired):
            self._softbsl_install_recovery = error.recovery
            self._log(f"Soft-BSL installation incomplete: {error}", "error")
            if self._offer_active_flash_recovery(str(error)):
                return
        self._release_softbsl_port(port)
        self._log(f"Soft-BSL installation failed: {error}", "error")
        QMessageBox.critical(
            self, "Soft-BSL Installation Failed",
            f"{error}\n\nReview the log before retrying. If the failure occurred while writing "
            "the boot/parameter region and the ECU no longer responds, use the hardware BSL "
            "recovery tab.")

    # ── BSL-Unbricker tab (in-process hardware-BSL recovery) ──

    def _bsl_region_choices(self):
        chip, half = self.cb_bsl_chip.currentData(), self.cb_bsl_half.currentText()
        return bsl_service.flash_regions(chip, half)

    def _build_bsl_tab(self):
        tab = QWidget()
        lay = QVBoxLayout(tab)

        warn = QLabel("⚠ BSL-Unbricker drives the CPU's built-in silicon bootstrap loader over a "
                       "dedicated DIRECT full-duplex serial tap (NOT K-line, no echo) — for a "
                       "bricked/unbootable ECU. DTR pulses RSTIN#; NMI# must be held low and ALE "
                       "sampled high at reset. Disconnect the DS2 session first.")
        warn.setWordWrap(True); warn.setStyleSheet("color:#e8c46a;")
        lay.addWidget(warn)

        order = QLabel("Last resort only — use this when the ECU no longer responds over DS2 or "
                       "Soft-BSL. Select the chip/half below, then Preview before "
                       "arming a real write.")
        order.setWordWrap(True); order.setStyleSheet("color:#888; font-style:italic;")
        lay.addWidget(order)

        transport = QHBoxLayout()
        transport.addWidget(QLabel("BSL Port:"))
        self.cb_bsl_port = QComboBox()
        self.cb_bsl_port.setMinimumWidth(140)
        self.cb_bsl_port.setToolTip(
            "Dedicated FT232 COM port wired directly to ASC0 TxD0/RxD0. Hardware BSL is full-duplex "
            "and never uses K-Line echo handling.")
        transport.addWidget(self.cb_bsl_port)
        self.btn_bsl_refresh = QPushButton("⟳ Refresh")
        self.btn_bsl_refresh.setMinimumWidth(84)
        self.btn_bsl_refresh.setToolTip("Refresh both serial-port lists")
        self.btn_bsl_refresh.clicked.connect(self._refresh_ports)
        transport.addWidget(self.btn_bsl_refresh)
        transport.addWidget(QLabel("Baud:"))
        self.cb_bsl_baud = QComboBox()
        self.cb_bsl_baud.addItem("Low (9,600)", 9600)
        self.cb_bsl_baud.addItem("Mid (19,200)", 19200)
        self.cb_bsl_baud.addItem("High (38,400)", 38400)
        self.cb_bsl_baud.setCurrentIndex(2)
        self.cb_bsl_baud.setToolTip(
            "High (38,400) is the preferred rate. Use Mid or Low as a manual fallback "
            "when the adapter or direct-tap wiring is unstable.")
        transport.addWidget(self.cb_bsl_baud)
        self.lbl_bsl_transport_mode = QLabel("Direct ASC0 | 8N1 | no echo | DTR reset")
        self.lbl_bsl_transport_mode.setStyleSheet(
            "color:#7ec8e3; font-weight:bold; padding-left:8px;")
        self.lbl_bsl_transport_mode.setToolTip(
            "Hardware BSL uses a separate full-duplex ASC0 tap. DTR pulses the active-low ECU reset "
            "before each BSL synchronization; ALE-high and NMI#-low remain physical entry straps.")
        transport.addWidget(self.lbl_bsl_transport_mode)
        transport.addStretch()
        lay.addLayout(transport)

        top = QHBoxLayout()
        top.addWidget(QLabel("Chip:"))
        self.cb_bsl_chip = QComboBox()
        self.cb_bsl_chip.addItem("Auto-detect", "auto")
        self.cb_bsl_chip.addItem("Intel 28F200", "28f200")
        self.cb_bsl_chip.addItem("AMD 29F200", "29f200")
        self.cb_bsl_chip.addItem("AMD 29F400", "29f400")
        top.addWidget(self.cb_bsl_chip)
        top.addWidget(QLabel("Half (29F400 only):"))
        self.cb_bsl_half = QComboBox(); self.cb_bsl_half.addItems(["upper", "lower"])
        top.addWidget(self.cb_bsl_half)
        top.addStretch()
        lay.addLayout(top)

        # ── non-destructive reads ──
        rg = QGroupBox("Read flash  (non-destructive hardware-BSL dump)")
        rg.setStyleSheet(_SECTION_GB)
        rg.setMaximumHeight(96)
        rl = QHBoxLayout(rg)
        read_note = QLabel(
            "Full: visible 256 KB bank → standard file order; unmapped window filled with 0xFF.\n"
            "Tune: CPU/DS2 0x10000–0x15FFF → standard 24 KB file. "
            "Both save automatically to Bins; an optional extra copy is offered.")
        read_note.setWordWrap(True)
        read_note.setStyleSheet("color:#888;")
        rl.addWidget(read_note, 1)
        self.btn_bsl_read_full = self._op_btn(
            "Read Full Flash (256 KB)…", "#2a5d3a", self._on_bsl_read_full)
        self.btn_bsl_read_full.setToolTip(
            "Read the complete visible 256 KB flash bank through the direct ASC0 hardware-BSL "
            "tap. The image is converted to standard file order and saved automatically to Bins; "
            "you can then save an additional copy elsewhere.")
        self.btn_bsl_read_tune = self._op_btn(
            "Read Tune (24 KB)…", "#2a5d3a", self._on_bsl_read_tune)
        self.btn_bsl_read_tune.setToolTip(
            "Read CPU/DS2 addresses 0x10000–0x15FFF as the standard 24 KB calibration partial "
            "and save it automatically to Bins; you can then save an additional copy elsewhere.")
        rl.addWidget(self.btn_bsl_read_full)
        rl.addWidget(self.btn_bsl_read_tune)
        lay.addWidget(rg)

        # ── flash (preview -> frozen plan -> execute) ──
        fg = QGroupBox("Flash  (erase + program + verify a region from a reference)")
        fg.setStyleSheet(_SECTION_GB)
        fl = QVBoxLayout(fg)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Region:"))
        self.cb_bsl_region = QComboBox(); self.cb_bsl_region.addItems(self._bsl_region_choices())
        r1.addWidget(self.cb_bsl_region)
        self.cb_bsl_chip.currentTextChanged.connect(self._on_bsl_geometry_changed)
        self.cb_bsl_half.currentTextChanged.connect(self._on_bsl_geometry_changed)
        self.cb_bsl_region.currentTextChanged.connect(self._invalidate_bsl_plan)
        r1.addStretch()
        fl.addLayout(r1)
        r2 = QHBoxLayout()
        self.btn_bsl_ref = self._op_btn("Reference image…", "#3d3d3d", self._on_bsl_pick_ref)
        r2.addWidget(self.btn_bsl_ref)
        self._bsl_ref = None
        self._bsl_ref_lbl = QLabel("(none)"); r2.addWidget(self._bsl_ref_lbl, 1)
        fl.addLayout(r2)
        r3 = QHBoxLayout()
        self.chk_bsl_fix_cksum = QCheckBox("Fix checksums before flashing")
        self.chk_bsl_force = QCheckBox("Force (override cross-variant / bad-checksum guard — brick risk)")
        self.chk_bsl_fix_cksum.toggled.connect(self._invalidate_bsl_plan)
        self.chk_bsl_force.toggled.connect(self._invalidate_bsl_plan)
        r3.addWidget(self.chk_bsl_fix_cksum); r3.addWidget(self.chk_bsl_force); r3.addStretch()
        fl.addLayout(r3)

        self._bsl_preview = QTextEdit(); self._bsl_preview.setReadOnly(True)
        self._bsl_preview.setFont(QFont("Courier New", 9))
        self._bsl_preview.setStyleSheet("background:#1a1a1a; color:#aaa; border:1px solid #444; padding:2px;")
        fl.addWidget(self._bsl_preview)

        r4 = QHBoxLayout()
        self.btn_bsl_dryrun = self._op_btn("Review Flash Plan…", "#3d3d3d", self._on_bsl_dry_run)
        self.btn_bsl_arm = self._op_btn("Confirm and Flash…", "#7a2d2d", self._on_bsl_arm)
        self.btn_bsl_arm.setEnabled(False)
        self._bsl_plan = None
        r4.addWidget(self.btn_bsl_dryrun); r4.addWidget(self.btn_bsl_arm); r4.addStretch()
        fl.addLayout(r4)
        lay.addWidget(fg)

        # ── diagnostics (collapsed by default) ──
        diag = QGroupBox("Diagnostics (Advanced)")
        self.bsl_diag_group = diag
        diag.setStyleSheet(_SECTION_GB)
        diag.setCheckable(True); diag.setChecked(False)
        diag_lay = QVBoxLayout(diag)
        diag_inner = QWidget(); diag_inner.setVisible(False)
        diag.toggled.connect(diag_inner.setVisible)
        diag_lay.addWidget(diag_inner)
        dg = QHBoxLayout(diag_inner)
        self.btn_bsl_sync = self._op_btn("Sync", "#3d3d3d", self._on_bsl_sync)
        self.btn_bsl_id = self._op_btn("Chip ID", "#3d3d3d", self._on_bsl_id)
        self.btn_bsl_businfo = self._op_btn("Bus Info", "#3d3d3d", self._on_bsl_businfo)
        self.btn_bsl_alias = self._op_btn("Verify Alias", "#3d3d3d", self._on_bsl_verify_alias)
        for button in (self.btn_bsl_sync, self.btn_bsl_id,
                       self.btn_bsl_businfo, self.btn_bsl_alias):
            dg.addWidget(button)
        self.btn_bsl_vpp = self._op_btn("VPP On", "#7a2d2d", self._on_bsl_vpp_on)
        self.btn_bsl_vpp.setEnabled(False)
        dg.addWidget(self.btn_bsl_vpp)
        dg.addStretch()
        lay.addWidget(diag)

        self.cb_bsl_port.currentTextChanged.connect(self._invalidate_bsl_plan)
        self.cb_bsl_baud.currentIndexChanged.connect(self._invalidate_bsl_plan)
        self._on_bsl_geometry_changed()

        self._bsl_tab_index = self.tabs.addTab(tab, "  BSL-Unbricker  ")

    def _acquire_bsl_port(self):
        port = self.cb_bsl_port.currentText()
        if not port or port.startswith("("):
            QMessageBox.warning(
                self, "No BSL Serial Port",
                "Select the dedicated direct-tap COM port in the BSL-Unbricker tab.")
            return None
        try:
            self._port_owner.acquire("bsl")
        except PortBusyError as e:
            QMessageBox.warning(self, "Port Busy",
                                f"The port is held by '{e.holder}'. Disconnect the DS2 session first.")
            return None
        return port

    def _bsl_chip_half(self):
        return self.cb_bsl_chip.currentData(), self.cb_bsl_half.currentText()

    def _bsl_baud(self):
        return int(self.cb_bsl_baud.currentData())

    def _invalidate_bsl_plan(self, *_args):
        self._bsl_plan = None
        if hasattr(self, "btn_bsl_arm"):
            self.btn_bsl_arm.setEnabled(False)

    def _on_bsl_geometry_changed(self, *_args):
        current = self.cb_bsl_region.currentText()
        self.cb_bsl_region.blockSignals(True)
        self.cb_bsl_region.clear()
        choices = self._bsl_region_choices()
        self.cb_bsl_region.addItems(choices)
        if current in choices:
            self.cb_bsl_region.setCurrentText(current)
        self.cb_bsl_region.blockSignals(False)
        self.cb_bsl_half.setEnabled(self.cb_bsl_chip.currentData() == "29f400")
        self._update_bsl_vpp_control(not getattr(self, "_task_busy", False))
        self._invalidate_bsl_plan()

    def _update_bsl_vpp_control(self, controls_enabled=True):
        """Explain and enforce that manual VPP control is Intel-28F200-only."""
        chip = self.cb_bsl_chip.currentData()
        self.btn_bsl_vpp.setEnabled(bool(controls_enabled) and chip == "28f200")
        if chip == "28f200":
            self.btn_bsl_vpp.setText("VPP On")
            tip = (
                "Temporarily drive P2.6 to enable the external 12 V VPP/RP# rail for an Intel "
                "28F200 voltage check. The next BSL command or ECU reset turns it off.")
        elif chip == "auto":
            self.btn_bsl_vpp.setText("VPP On (select 28F200)")
            tip = (
                "Disabled while Chip is Auto-detect. Select Intel 28F200 to test its external "
                "12 V VPP/RP# rail; AMD/JEDEC flash parts are single-supply.")
        else:
            self.btn_bsl_vpp.setText("VPP N/A (AMD)")
            tip = (
                "Not applicable to AMD/JEDEC 29F200/29F400 flash. These chips are single-supply "
                "and do not use the Intel 12 V VPP control.")
        self.btn_bsl_vpp.setToolTip(tip)

    def _set_bsl_controls_enabled(self, enabled):
        if not hasattr(self, "btn_bsl_dryrun"):
            return
        for widget in (self.cb_bsl_port, self.btn_bsl_refresh, self.cb_bsl_baud,
                       self.cb_bsl_chip, self.cb_bsl_region, self.chk_bsl_fix_cksum,
                       self.chk_bsl_force, self.btn_bsl_ref, self.btn_bsl_dryrun,
                       self.btn_bsl_sync, self.btn_bsl_id, self.btn_bsl_businfo,
                       self.btn_bsl_alias, self.btn_bsl_read_full,
                       self.btn_bsl_read_tune):
            widget.setEnabled(enabled)
        self.cb_bsl_half.setEnabled(enabled and self.cb_bsl_chip.currentData() == "29f400")
        self._update_bsl_vpp_control(enabled)
        self.btn_bsl_arm.setEnabled(enabled and self._bsl_plan is not None)

    def _on_bsl_pick_ref(self):
        path, _ = QFileDialog.getOpenFileName(self, "Reference Image", "", "Binary (*.bin);;All Files (*)")
        if path:
            self._bsl_ref = path
            self._bsl_ref_lbl.setText(os.path.basename(path))
            self._invalidate_bsl_plan()

    def _on_bsl_dry_run(self):
        if not self._bsl_ref:
            QMessageBox.warning(
                self, "Reference Image Required",
                "Select a 24 KB calibration or 256 KB full reference image before previewing the plan.")
            return
        port = self.cb_bsl_port.currentText()
        if not port or port.startswith("("):
            QMessageBox.warning(
                self, "No BSL Serial Port",
                "Select the dedicated direct-tap COM port in this tab. Preview records that port "
                "but does not open it.")
            return
        chip, half = self._bsl_chip_half()
        if chip == "auto":
            QMessageBox.warning(
                self, "Select Flash Chip",
                "Auto-detect is safe for Chip ID only. Select Intel 28F200, AMD 29F200, "
                "or AMD 29F400 before reviewing a flash plan because their erase "
                "geometries differ.")
            return
        region = self.cb_bsl_region.currentText()
        baud = self._bsl_baud()
        fix_cksum, force = self.chk_bsl_fix_cksum.isChecked(), self.chk_bsl_force.isChecked()
        ref = self._bsl_ref
        self._bsl_preview.clear()
        lines = []
        try:
            plan = bsl_service.create_flash_plan(
                port, region, ref, chip, half, fix_cksum, force,
                baud=baud, reset_line="dtr")
        except Exception as error:
            QMessageBox.warning(self, "Invalid BSL Plan", str(error))
            return
        rc = bsl_service.flash_dry_run(plan, lines.append)
        preview = "\n".join(lines)
        for old, new in (
            ("DRY RUN", "PLAN REVIEW COMPLETE"),
            ("Dry-run", "Plan review"),
            ("dry-run", "plan review"),
            ("--arm", "Confirm and Flash"),
            ("--fix-checksums", "'Fix checksums before flashing'"),
            ("--force", "'Force' override"),
            ("--half lower", "Lower half"),
            ("--half upper", "Upper half"),
            ("--cpu-order", "CPU-order output"),
            ("`dump --file-order`", "a standard file-order dump"),
        ):
            preview = preview.replace(old, new)
        self._bsl_preview.setPlainText(preview)
        self._bsl_plan = plan if rc == 0 else None
        self.btn_bsl_arm.setEnabled(self._bsl_plan is not None)
        if rc != 0:
            self._log(f"BSL flash-plan review failed (exit {rc})", "error")

    def _on_bsl_arm(self):
        plan = self._bsl_plan
        if plan is None:
            QMessageBox.warning(self, "Preview Required", "Preview this exact flash plan first.")
            return
        port = self._acquire_bsl_port()
        if not port:
            return
        if port != plan.port:
            self._port_owner.release("bsl")
            self._invalidate_bsl_plan()
            QMessageBox.warning(self, "Plan Changed", "The selected port changed. Preview again.")
            return
        chip, region = plan.chip, plan.region
        chip_label = {
            "28f200": "Intel 28F200",
            "29f200": "AMD 29F200",
            "29f400": "AMD 29F400",
        }.get(chip, chip)
        force_note = ("\n\nFORCE IS ENABLED: checksum and variant safety guards may be overridden."
                      if plan.force else "")
        text, ok = QInputDialog.getText(
            self, "BRICK-CLASS — Hardware BSL Flash",
            f"This ERASES + PROGRAMS + VERIFIES region '{region}' on {chip_label} over the direct "
            f"ASC0 hardware-BSL link using {plan.port} at {plan.baud:,} baud, no echo, with DTR reset. "
            f"A failed/interrupted write can leave the ECU unbootable by anything "
            f"but this same tool.{force_note}\n\nType  FLASH {region.upper()}  to proceed:")
        if not ok or text.strip() != f"FLASH {region.upper()}":
            self._port_owner.release("bsl")
            return
        def task(log_fn, progress_fn):
            rc = bsl_service.flash_arm(plan, log_fn, progress_fn)
            if rc != 0:
                raise RuntimeError(f"flash failed with code {rc}")
        self._run_task(task,
                       on_success=lambda _r: (self._port_owner.release("bsl"),
                                              self._invalidate_bsl_plan(),
                                              self._log(f"BSL flash of '{region}' complete", "ok"),
                                              QMessageBox.information(
                                                  self, "BSL Flash Complete",
                                                  f"Region '{region}' was erased, programmed, and verified.\n\n"
                                                  "Reset the ECU and remove the BSL entry straps before normal use.")),
                       on_failure=lambda e: (self._port_owner.release("bsl"),
                                             self._log(f"BSL flash failed: {e}", "error"),
                                             QMessageBox.critical(
                                                 self, "BSL Flash Failed",
                                                 f"{e}\n\nKeep the BSL entry wiring available. Do not assume the written region is valid; "
                                                 "review the log and retry recovery as needed.")))

    def _run_bsl_diag(self, fn, *extra_args, label):
        port = self._acquire_bsl_port()
        if not port:
            return
        chip, half = self._bsl_chip_half()
        baud = self._bsl_baud()
        def task(log_fn, progress_fn):
            log_fn(f"Hardware BSL on {port}: {baud:,} baud, direct ASC0/no echo, DTR reset.")
            rc = fn(
                port, *extra_args, chip, half, log_fn,
                baud=baud, reset_line="dtr", progress=progress_fn)
            if rc != 0:
                raise RuntimeError(f"{label} exited with code {rc}")
        self._run_task(task,
                       on_success=lambda _r: (self._port_owner.release("bsl"), self._log(f"{label} complete")),
                       on_failure=lambda e: (self._port_owner.release("bsl"),
                                             self._log(f"{label} failed: {e}", "error")))

    def _on_bsl_sync(self):
        self._run_bsl_diag(bsl_service.sync, label="BSL sync")

    def _on_bsl_id(self):
        self._run_bsl_diag(bsl_service.chip_id, label="BSL chip ID")

    def _on_bsl_businfo(self):
        self._run_bsl_diag(bsl_service.businfo, label="BSL bus info")

    def _on_bsl_verify_alias(self):
        self._run_bsl_diag(bsl_service.verify_alias, label="BSL verify-alias")

    def _on_bsl_vpp_on(self):
        if QMessageBox.warning(
                self, "Enable Intel Programming Voltage",
                "Enable the Intel 28F200 programming-voltage output (P2.6) so you can measure "
                "approximately 12 V at VPP? This control is not used for AMD/JEDEC flash. The "
                "next hardware-BSL command or ECU reset turns it off.",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
            return
        self._run_bsl_diag(bsl_service.vpp_on, label="BSL VPP-on")

    def _on_bsl_read_full(self):
        self._on_bsl_dump("full")

    def _on_bsl_read_tune(self):
        self._on_bsl_dump("tune")

    def _on_bsl_dump(self, mode):
        """Read a full or tune image through hardware BSL, save it, and catalogue it."""
        if mode not in ("full", "tune"):
            raise ValueError(f"unsupported BSL dump mode: {mode}")
        size = MS41ECU.FULL_ROM_SIZE if mode == "full" else MS41ECU.TUNE_SIZE
        kind = "full 256 KB flash" if mode == "full" else "24 KB tune"
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        suggested = f"ms41_bsl_{mode}_{stamp}.bin"
        port = self._acquire_bsl_port()
        if not port:
            return
        try:
            fd, path = tempfile.mkstemp(prefix=f"bimmerstein_bsl_{mode}_", suffix=".bin")
            os.close(fd)
        except OSError as error:
            self._port_owner.release("bsl")
            QMessageBox.critical(
                self, "BSL Read Failed",
                f"Could not prepare temporary storage for the {kind}:\n{error}")
            return
        chip, half = self._bsl_chip_half()
        baud = self._bsl_baud()
        read_fn = bsl_service.dump_full if mode == "full" else bsl_service.dump_tune

        def remove_temporary_output():
            try:
                os.remove(path)
            except OSError:
                pass

        def task(log_fn, progress_fn):
            log_fn(
                f"Reading {kind} through hardware BSL on {port}: {baud:,} baud, "
                "direct ASC0/no echo, DTR reset.")
            rc = read_fn(
                port, path, chip, half, log_fn, progress_fn,
                baud=baud, reset_line="dtr")
            if rc != 0:
                raise RuntimeError(f"hardware-BSL {mode} read exited with code {rc}")
            with open(path, "rb") as handle:
                data = handle.read()
            if len(data) != size:
                raise RuntimeError(
                    f"hardware-BSL {mode} read saved {len(data):,} bytes; expected {size:,}")
            return data

        def on_success(data):
            self._port_owner.release("bsl")
            try:
                entry = self._backup_mgr.add_data(
                    data, suggested, source="BSL-Unbricker read",
                    notes=(f"Hardware BSL direct ASC0 {mode} read at {baud:,} baud; "
                           f"chip={chip}, half={half}"))
            except Exception as error:
                remove_temporary_output()
                self._log(f"BSL read automatic Bins save failed: {error}", "error")
                QMessageBox.critical(
                    self, "Automatic Save Failed",
                    f"The {kind} was read, but could not be saved to Bins:\n{error}")
                return
            remove_temporary_output()
            self._refresh_backup_table()
            self._log(
                f"BSL {mode} read complete: {entry.path}", "ok")
            copy_label = (
                "BSL Full Flash (256 KB)" if mode == "full" else "BSL Tune (24 KB)")
            self._offer_additional_read_copy(
                data, entry, copy_label, dialog_title="BSL Read Complete")

        def on_failure(error):
            self._port_owner.release("bsl")
            remove_temporary_output()
            self._log(f"BSL {mode} read failed: {error}", "error")
            QMessageBox.critical(
                self, "BSL Read Failed",
                f"The {kind} could not be read completely:\n{error}\n\n"
                "No incomplete image was added to Bins.")

        self._run_task(task, on_success=on_success, on_failure=on_failure)

    # ── Patches tab ──────────────────────────────────────────────────────

    def _build_patches_tab(self):
        tab = QWidget()
        lay = QVBoxLayout(tab)
        self._patch_base = None
        self._patch_base_source = ""
        self._patch_checkboxes = {}
        self._patch_rows = {}
        self._patch_installed_ids = set()
        self._patch_entries = {}
        self._patch_dependency_sync = False

        bar = QHBoxLayout()
        btn_load = QPushButton("Load Base .bin…")
        btn_load.clicked.connect(self._on_patches_load_base)
        bar.addWidget(btn_load)
        btn_read = QPushButton("Read Base from ECU")
        btn_read.setToolTip(
            "Reads the full 256 KB ROM through the automatic transfer path: Soft-BSL, "
            "native-fast DS2, or normal DS2. The ECU is returned to normal communication "
            "mode after the read.")
        btn_read.clicked.connect(self._on_patches_read_ecu)
        bar.addWidget(btn_read)
        bar.addStretch()
        lay.addLayout(bar)

        self.lbl_patch_base = QLabel("No base loaded.")
        self.lbl_patch_base.setStyleSheet("color:#aaa;")
        self.lbl_patch_base.setWordWrap(True)
        lay.addWidget(self.lbl_patch_base)

        self._patch_group = QGroupBox("Patches")
        self._patch_group.setStyleSheet(
            "QGroupBox{color:#aaa;font-weight:bold;border:1px solid #444;border-radius:4px;"
            "margin-top:6px;padding-top:8px;} QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 4px;}")
        self._patch_group_lay = QVBoxLayout(self._patch_group)
        self._patch_placeholder = QLabel("Load a base image to see the patches that apply to it.")
        self._patch_placeholder.setStyleSheet("color:#888;")
        self._patch_group_lay.addWidget(self._patch_placeholder)
        lay.addWidget(self._patch_group)

        build_bar = QHBoxLayout()
        self.btn_patches_build = QPushButton("Build Patched Image  (→ Bins)")
        self.btn_patches_build.setEnabled(False)
        self.btn_patches_build.clicked.connect(self._on_patches_build)
        build_bar.addWidget(self.btn_patches_build)
        build_bar.addStretch()
        lay.addLayout(build_bar)

        self.patches_log = QTextEdit()
        self.patches_log.setReadOnly(True)
        self.patches_log.setMaximumHeight(130)
        self.patches_log.setFont(QFont("Courier New", 9))
        self.patches_log.setStyleSheet("background:#1a1a1a;color:#aaa;border:1px solid #444;padding:2px;")
        lay.addWidget(self.patches_log)
        lay.addStretch()

        self.tabs.addTab(tab, "  Patches  ")

    def _set_patch_base(self, data, source):
        data = bytes(data)
        r = MS41ECU.resolve_version(data)
        self._patch_base = data
        self._patch_base_source = source
        txt = f"Base: {source}  —  program {r['program'] or '?'} / cal {r['cal'] or '?'}"
        if r["hybrid"]:
            txt += f"   ⚠ HYBRID: {r['hybrid']}"
        self.lbl_patch_base.setText(txt)
        self._refresh_patch_list()

    def _on_patch_remove(self, patch_id):
        """Revert one already-applied patch from the loaded base (restores its stock bytes),
        so it can be re-patched with something else (e.g. ignition_cut V1-V4 -> V5)."""
        if self._patch_base is None:
            return
        title = patch_service.definitions().get(patch_id, {}).get("title", patch_id)
        if QMessageBox.question(
                self, "Remove Patch",
                f"Remove '{patch_id}' from the loaded base?\n\n{title}\n\n"
                "This restores the original stock bytes at every offset this patch touched. "
                "It edits the in-memory base only — use Build Patched Image to archive it, or "
                "Flash to ECU from the Backups tab once you've re-applied whatever you want.",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            new_data = patch_service.revert_patch(self._patch_base, patch_id)
        except patch_service.PatchError as e:
            QMessageBox.critical(self, "Remove Failed", str(e))
            self.patches_log.append(f"REMOVE FAILED: {e}")
            return
        self.patches_log.append(f"Removed {patch_id} — restored stock bytes.")
        self._set_patch_base(new_data, f"{self._patch_base_source} (patch removed: {patch_id})")

    @staticmethod
    def _badge(text, bg, fg):
        b = QLabel(text)
        b.setStyleSheet(f"background:{bg}; color:{fg}; border-radius:8px; "
                        f"padding:1px 7px; font-size:9px; font-weight:bold;")
        return b

    def _refresh_patch_list(self):
        for row in self._patch_rows.values():
            row.setParent(None)
        self._patch_rows = {}
        self._patch_checkboxes = {}
        self._patch_installed_ids = set()
        self._patch_entries = {}
        avail = patch_service.available_patches(self._patch_base) if self._patch_base else []
        if not avail:
            self._patch_placeholder.setText("No patches match this base's version.")
            self._patch_placeholder.setVisible(True)
            self.btn_patches_build.setEnabled(False)
            return
        self._patch_placeholder.setVisible(False)
        self._patch_entries = {patch["id"]: patch for patch in avail}
        definitions = patch_service.definitions()
        for p in avail:
            row = QWidget()
            rlay = QHBoxLayout(row)
            rlay.setContentsMargins(0, 2, 0, 2)

            cb = QCheckBox(f"{p['id']}  —  {p['title']}")
            cb.setProperty("blocked_by_legacy", bool(p.get("legacy")))
            cb.setStyleSheet("QCheckBox{font-weight:normal;color:#d4d4d4;}")
            user_tip = p.get("user_description") or p["description"]
            required_names = []
            for required_id in p.get("requires", []):
                required_title = definitions.get(required_id, {}).get("title", required_id)
                required_names.append(required_title.split(" - ", 1)[0])
            if required_names:
                user_tip += (
                    "\n\nRequired patch: " + ", ".join(required_names)
                    + ". Selecting this patch automatically selects available requirements."
                )
            cb.setToolTip(user_tip)
            cb.setChecked(p["installed"])
            rlay.addWidget(cb)

            if p.get("tested") is False:
                untested_badge = self._badge(
                    "UNTESTED", "#5a4a1a", "#e8c46a")
                untested_badge.setToolTip(
                    p["status"] or "This patch has not been validated on a vehicle.")
                rlay.addWidget(untested_badge)
            elif p["status"]:
                rlay.addWidget(self._badge(p["status"], "#2a2a2a", "#aaa"))
            for required_id, required_name in zip(
                    p.get("requires", []), required_names):
                requirement = self._badge(
                    f"REQUIRES {required_name.upper()}", "#24384d", "#8fc7ff")
                requirement.setToolTip(
                    f"{p['title']} requires {required_name}. Selecting this patch also "
                    "selects that requirement when it is available. Conflicting selections "
                    "are never removed automatically."
                )
                rlay.addWidget(requirement)
            if p.get("needs_boot"):
                bb = self._badge("BOOT REGION · Soft-BSL", "#3a2a55", "#c9a6ff")
                bb.setToolTip("Writes the boot/parameter region (file 0x4000–0x5FFF). Enable "
                              "boot-region writes on the Flash tab, or use hardware BSL recovery; "
                              "plain DS2 cannot deliver these bytes.")
                rlay.addWidget(bb)
            if p["installed"]:
                rlay.addWidget(self._badge("✓ INSTALLED", "#1e4d2b", "#9ece6a"))
                btn_rm = QPushButton("✕ Remove")
                btn_rm.setStyleSheet(
                    "QPushButton{background:#3d2020;color:#f0a0a0;border:1px solid #5a1a1a;"
                    "border-radius:3px;padding:1px 8px;font-size:9px;} "
                    "QPushButton:hover{background:#5a1a1a;}")
                btn_rm.clicked.connect(lambda _=False, pid=p["id"]: self._on_patch_remove(pid))
                required_by = p.get("required_by", [])
                if required_by:
                    dependent_names = [
                        definitions.get(pid, {}).get("title", pid)
                        .split(" - ", 1)[0].split(" / ", 1)[0]
                        for pid in required_by
                    ]
                    joined = ", ".join(dependent_names)
                    rlay.addWidget(self._badge(
                        f"REQUIRED BY {joined.upper()}", "#4d3524", "#ffc07a"))
                    btn_rm.setEnabled(False)
                    btn_rm.setToolTip(
                        f"Cannot remove {p['title']} while installed patch(es) {joined} "
                        "still require it. Remove the dependent patch first."
                    )
                rlay.addWidget(btn_rm)
            for leg in p.get("legacy", []):
                rlay.addWidget(self._badge(f"⚠ {leg['id'].upper()} ({leg['label']}) INSTALLED", "#5a1a1a", "#f47171"))
                btn_rm_legacy = QPushButton(f"✕ Remove {leg['id']} ({leg['label']})")
                btn_rm_legacy.setStyleSheet(
                    "QPushButton{background:#3d2020;color:#f0a0a0;border:1px solid #5a1a1a;"
                    "border-radius:3px;padding:1px 8px;font-size:9px;} "
                    "QPushButton:hover{background:#5a1a1a;}")
                btn_rm_legacy.clicked.connect(lambda _=False, pid=leg["id"]: self._on_patch_remove(pid))
                rlay.addWidget(btn_rm_legacy)
            if not p["ok"]:
                rlay.addWidget(self._badge(f"⚠ {p['badge']}", "#5a1a1a", "#f47171"))
            rlay.addStretch()

            if p["installed"]:
                self._patch_installed_ids.add(p["id"])
                cb.setEnabled(False)
                cb.setToolTip(user_tip + "\n\nAlready present in this base image.")
            elif p.get("legacy"):
                cb.setEnabled(False)
                legacy_ids = ", ".join(f"{leg['id']} ({leg['label']})" for leg in p["legacy"])
                cb.setToolTip(user_tip + f"\n\nThis base already has a superseded patch applied "
                                         f"({legacy_ids}). Remove the deprecated/unsafe revision "
                                         "above, or build from a clean base, before applying this version.")
            cb.stateChanged.connect(self._on_patch_selection_changed)
            self._patch_group_lay.addWidget(row)
            self._patch_rows[p["id"]] = row
            self._patch_checkboxes[p["id"]] = cb
        self._on_patch_selection_changed()

    def _on_patch_selection_changed(self):
        # already-installed patches are checked+disabled (status display only) and must not
        # be re-applied, since their bytes no longer match the pre-patch `expect` values.
        if self._patch_dependency_sync:
            return

        # Expand selected patches to their available requirements. This only adds
        # requirements; it never clears a conflicting user selection.
        self._patch_dependency_sync = True
        try:
            while True:
                selected = [
                    pid for pid, cb in self._patch_checkboxes.items()
                    if cb.isChecked() and pid not in self._patch_installed_ids
                ]
                blocked = patch_service.collisions(selected)
                changed = False
                for pid in selected:
                    for required_id in self._patch_entries.get(pid, {}).get("requires", []):
                        if required_id in self._patch_installed_ids:
                            continue
                        required_cb = self._patch_checkboxes.get(required_id)
                        if (required_cb is not None and not required_cb.isChecked()
                                and not required_cb.property("blocked_by_legacy")
                                and required_id not in blocked):
                            required_cb.setChecked(True)
                            changed = True
                if not changed:
                    break
        finally:
            self._patch_dependency_sync = False

        selected = [pid for pid, cb in self._patch_checkboxes.items()
                    if cb.isChecked() and pid not in self._patch_installed_ids]
        blocked = patch_service.collisions(selected)
        for pid, cb in self._patch_checkboxes.items():
            if pid in self._patch_installed_ids:
                continue
            if cb.property("blocked_by_legacy"):
                cb.setEnabled(False)
                continue
            cb.setEnabled(pid in selected or pid not in blocked)

        selected_or_installed = set(selected) | self._patch_installed_ids
        missing = {
            required_id
            for pid in selected
            for required_id in self._patch_entries.get(pid, {}).get("requires", [])
            if required_id not in selected_or_installed
        }
        self.btn_patches_build.setEnabled(
            bool(selected) and self._patch_base is not None and not missing)
        if missing:
            definitions = patch_service.definitions()
            names = [
                definitions.get(pid, {}).get("title", pid).split(" - ", 1)[0]
                for pid in sorted(missing)
            ]
            self.btn_patches_build.setToolTip(
                "Required patch unavailable or blocked by a conflict: " + ", ".join(names))
        else:
            self.btn_patches_build.setToolTip(
                "Build and archive the selected patches. Available required patches are "
                "selected automatically.")

    def _on_patches_load_base(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load Base .bin", "", "BIN files (*.bin);;All files (*)")
        if not path:
            return
        try:
            data = open(path, "rb").read()
        except Exception as e:
            QMessageBox.critical(self, "Patch Base Read Failed", str(e))
            return
        if len(data) != 262144:
            QMessageBox.warning(
                self,
                "Not a Full ROM",
                f"Expected a 256 KB (262,144-byte) full ROM; received {len(data):,} bytes.",
            )
            return
        self._set_patch_base(data, os.path.basename(path))
        self.patches_log.append(f"Loaded base: {os.path.basename(path)}")

    def _read_base_from_ecu(self, log_fn, progress_fn):
        """Read a full patch base through the same route used by the Flash tab."""
        route = self._auto_transfer_route()
        data = self._read_image_auto("full", log_fn, progress_fn)
        labels = {
            "softbsl": "ECU read (Soft-BSL fast)",
            "native_ds2": "ECU read (native DS2 fast)",
            "legacy_ds2": "ECU read (DS2)",
        }
        return bytes(data), labels[route]

    def _on_patches_read_ecu(self):
        if not self._ds2:
            QMessageBox.information(self, "Not Connected", "Connect to the ECU first (Connect button).")
            return

        def task(log_fn, progress_fn):
            return self._read_base_from_ecu(log_fn, progress_fn)

        def on_done(result):
            data, source = result
            self._set_patch_base(data, source)
            self.patches_log.append(f"Read {len(data)} bytes from ECU ({source}).")

        self._run_task(task, on_success=on_done)

    def _on_patches_build(self):
        # Already-installed patches show up checked (status display only, see
        # _refresh_patch_list) — they must NOT be sent to build_image, since their bytes are
        # no longer stock and would fail the expect-byte check. Only newly-checked patches
        # get applied; whatever's already baked into the loaded base passes through untouched.
        selected = [pid for pid, cb in self._patch_checkboxes.items()
                    if cb.isChecked() and pid not in self._patch_installed_ids]
        if not selected or not self._patch_base:
            return
        loaded = patch_service.definitions()
        untested = [
            pid for pid in selected
            if loaded.get(pid, {}).get("tested") is False
        ]
        if untested:
            untested_names = [
                loaded.get(pid, {}).get("title", pid) for pid in untested
            ]
            if QMessageBox.warning(
                    self, "Untested Patch",
                    "These patches are marked untested:\n"
                    + "\n".join(f"  • {name}" for name in untested_names)
                    + "\n\n"
                    "Emulator verification does not replace vehicle testing. Continue?",
                    QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return
        try:
            out, buildlog = patch_service.build_image(self._patch_base, selected)
        except patch_service.PatchError as e:
            QMessageBox.critical(self, "Build Failed", str(e))
            self.patches_log.append(f"BUILD FAILED: {e}")
            return

        # Auto-archive the built image to the Bins catalogue (traceable, and no path to pick). add_data
        # derives the variant / CAL ID / ECU ID / VIN / checksum straight from the patched image.
        ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        name = "ms41_patched_" + "_".join(selected) + f"_{ts}.bin"
        try:
            entry = self._backup_mgr.add_data(
                out, name, source="patched: " + "+".join(selected),
                notes="Patched build: " + ", ".join(selected))
        except Exception as e:
            QMessageBox.critical(self, "Build Failed", f"Built the image but could not archive it: {e}")
            self.patches_log.append(f"ARCHIVE FAILED: {e}")
            return
        self._refresh_backup_table()
        self.patches_log.append(f"Built + archived to Bins: {entry.filename}  ({', '.join(selected)})")
        for line in buildlog:
            self.patches_log.append("  " + line)

        # Offer to flash it straight to the connected ECU (same auto soft-BSL/DS2 routing + variant /
        # identity guards as any full write on the Flash tab).
        if self._ds2 is not None:
            if QMessageBox.question(
                    self, "Flash Patched Image?",
                    f"Built and archived to Bins:\n  {entry.filename}\n\nPatches: {', '.join(selected)}\n\n"
                    "Flash it to the connected ECU now? The automatic transfer path uses "
                    "Soft-BSL, native-fast DS2, or normal DS2 as available.",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
                self._ds2_write_full(bytearray(out), entry.filename)
        else:
            QMessageBox.information(
                self, "Build Complete",
                f"Built and archived to Bins:\n  {entry.filename}\n\nPatches: {', '.join(selected)}\n\n"
                f"Connect to an ECU to flash it (Bins → Flash to ECU), or open the file via "
                f"Bins → Open Folder.")

    # ── Backups tab ──────────────────────────────────────────────────────

    def _build_backups_tab(self):
        tab = QWidget()
        lay = QVBoxLayout(tab)

        btn_bar = QHBoxLayout()
        self.btn_backup_ecu = self._op_btn("Back Up From ECU…", "#1e5080", self._on_backup_from_ecu)
        self.btn_backup_ecu.setMaximumWidth(180)
        self.btn_backup_ecu.setEnabled(False)
        self.btn_backup_ecu.setToolTip(
            "Read the connected ECU through the automatic transfer path (Soft-BSL, "
            "native-fast DS2, or normal DS2) and add the full ROM or 24 KB tune to "
            "this catalogue with ECU ID, VIN, and CAL ID metadata.")
        btn_add = self._op_btn("Add Backup…",       "#3d3d3d",  self._on_backup_add)
        btn_add.setMaximumWidth(140)
        self.btn_backup_flash = self._op_btn("Flash to ECU",    "#7a1f1f",  self._on_backup_flash)
        self.btn_backup_flash.setMaximumWidth(150)
        self.btn_backup_open_bsl = self._op_btn(
            "Open in BSL-Unbricker", "#3d3d3d", self._on_backup_open_in_bsl)
        self.btn_backup_open_bsl.setMaximumWidth(190)
        self.btn_backup_open_bsl.setToolTip(
            "Load the selected Bin as the BSL reference image and open the BSL-Unbricker tab. "
            "This only prepares the tab; it does not open hardware or flash anything.")
        self.btn_backup_config = self._op_btn("Edit Config",   "#3d3d3d",  self._on_backup_edit_config)
        self.btn_backup_config.setMaximumWidth(130)
        self.btn_backup_config.setToolTip(
            "Open this backup in the ECU Config tab (FILE mode) to view/edit its "
            "feature flags, then Apply & Save.")
        self.btn_backup_notes = self._op_btn("Edit Notes",      "#3d3d3d",  self._on_backup_notes)
        self.btn_backup_notes.setMaximumWidth(130)
        self.btn_backup_del   = self._op_btn("Delete",          "#5a1a1a",  self._on_backup_delete)
        self.btn_backup_del.setMaximumWidth(100)
        btn_open = self._op_btn("Open Folder",      "#3d3d3d",  self._on_backup_open_folder)
        btn_open.setMaximumWidth(130)
        btn_bar.addWidget(self.btn_backup_ecu)
        btn_bar.addWidget(btn_add)
        btn_bar.addWidget(self.btn_backup_flash)
        btn_bar.addWidget(self.btn_backup_open_bsl)
        btn_bar.addWidget(self.btn_backup_config)
        btn_bar.addWidget(self.btn_backup_notes)
        btn_bar.addWidget(self.btn_backup_del)
        btn_bar.addStretch()
        btn_bar.addWidget(btn_open)
        lay.addLayout(btn_bar)

        # Search bar
        search_bar = QHBoxLayout()
        self._backup_search = QLineEdit()
        self._backup_search.setPlaceholderText("Search by notes, filename, variant, type…")
        self._backup_search.setStyleSheet(
            "QLineEdit { background:#2a2a2a; color:#d4d4d4; border:1px solid #444;"
            " border-radius:3px; padding:4px 8px; }"
            "QLineEdit:focus { border:1px solid #2a6099; }"
        )
        self._backup_search.setClearButtonEnabled(True)
        self._backup_search.textChanged.connect(self._on_backup_search)
        search_bar.addWidget(self._backup_search)
        lay.addLayout(search_bar)

        self.backup_table = QTableWidget(0, 7)
        self.backup_table.setHorizontalHeaderLabels(
            ["Date", "Filename", "Type", "Variant", "Source", "Checksum", "Notes"]
        )
        self.backup_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        self.backup_table.horizontalHeader().setDefaultSectionSize(120)
        self.backup_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.backup_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.backup_table.setAlternatingRowColors(True)
        self.backup_table.setStyleSheet("""
            QTableWidget { background:#1e1e1e; color:#d4d4d4;
                           gridline-color:#333; border:1px solid #444; }
            QTableWidget::item:alternate { background:#252525; }
            QHeaderView::section { background:#2a2a2a; color:#aaa;
                                   border:1px solid #444; padding:4px; font-weight:bold; }
            QTableWidget::item:selected { background:#2a6099; }
        """)
        self.backup_table.setFont(QFont("Courier New", 9))
        lay.addWidget(self.backup_table, 1)
        self.backup_table.selectionModel().selectionChanged.connect(
            lambda: self._set_backup_buttons_enabled(getattr(self, "_ds2", None) is not None)
        )

        self.tabs.addTab(tab, "  Bins  ")
        self._refresh_backup_table()
        self._set_backup_buttons_enabled(False)

    def _refresh_backup_table(self):
        self._backup_mgr.refresh()
        self.backup_table.setRowCount(0)
        for entry in self._backup_mgr.entries:
            row = self.backup_table.rowCount()
            self.backup_table.insertRow(row)
            cs_text  = "OK"    if entry.cs_ok else "INVALID"
            cs_color = "#5f5"  if entry.cs_ok else "#f47171"
            # Live ECU pulls green, patched builds amber, imported files grey (the Source column
            # doubles as the bin's "kind").
            src = entry.source or "imported"
            if src.startswith("ECU"):
                src_color = "#9ece6a"
            elif src.startswith("patched"):
                src_color = "#e8c46a"
            elif src == IDENTITY_BACKUP_SOURCE:
                src_color = "#c099ff"
            else:
                src_color = "#888"
            items = [
                (entry.display_date, "#aaa"),
                (entry.filename,     "#d4d4d4"),
                (entry.file_type,    "#aaa"),
                (entry.variant,      "#7ec8e3"),
                (src,                src_color),
                (cs_text,            cs_color),
                (entry.notes,        "#888"),
            ]
            for col, (text, colour) in enumerate(items):
                item = QTableWidgetItem(text)
                item.setForeground(QBrush(QColor(colour)))
                self.backup_table.setItem(row, col, item)
        self.backup_table.resizeRowsToContents()
        self.backup_table.clearSelection()
        self.backup_table.setCurrentIndex(self.backup_table.model().index(-1, -1))
        # Re-evaluate button states (row count just changed).  Refreshes only
        # happen while idle, so connection state is the interactivity gate.
        self._set_backup_buttons_enabled(getattr(self, "_ds2", None) is not None)
        self._on_backup_search(self._backup_search.text())

    def _on_backup_search(self, text: str):
        # Columns searched: Filename(1), Type(2), Variant(3), Source(4), Notes(6)
        SEARCH_COLS = (1, 2, 3, 4, 6)
        query = text.strip().lower()
        for row in range(self.backup_table.rowCount()):
            if not query:
                self.backup_table.setRowHidden(row, False)
                continue
            match = any(
                query in (self.backup_table.item(row, col).text().lower()
                          if self.backup_table.item(row, col) else "")
                for col in SEARCH_COLS
            )
            self.backup_table.setRowHidden(row, not match)

    def _selected_backup(self):
        rows = self.backup_table.selectedIndexes()
        row  = rows[0].row() if rows else -1
        entries = self._backup_mgr.entries
        if 0 <= row < len(entries):
            return entries[row]
        return None

    def _on_backup_add(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select ROM or Tune File to Back Up",
            "", "Binary Files (*.bin);;All Files (*)"
        )
        if not path: return
        notes, ok = QInputDialog.getText(
            self, "Backup Notes",
            "Optional notes for this backup (ECU mileage, date, description):"
        )
        if not ok: notes = ""
        try:
            entry = self._backup_mgr.add(path, notes=notes)
            self._refresh_backup_table()
            self._log(
                f"Backup added: {entry.filename} "
                f"({entry.file_type}, {entry.variant}, "
                f"checksum {'OK' if entry.cs_ok else 'INVALID'})",
                "ok"
            )
        except Exception as e:
            QMessageBox.critical(self, "Backup Failed", str(e))

    def _on_backup_from_ecu(self):
        """Read the connected ECU (full ROM or 24 KB tune) and add it to the catalogue."""
        if self._ds2 is None:
            QMessageBox.information(self, "Not Connected",
                "Connect to the ECU over DS2 first.")
            return
        choice, ok = QInputDialog.getItem(
            self, "Back Up From ECU", "Read and catalogue:",
            ["Tune Region (24 KB)", "Full ROM (256 KB)"],
            0, False)
        if not ok:
            return
        mode = "full" if choice.startswith("Full") else "tune"

        def task(log_fn, progress_fn):
            log_fn(f"Reading {'full 256 KB ROM' if mode == 'full' else '24 KB tune'} "
                   f"from ECU for backup…")
            return self._read_image_auto(mode, log_fn, progress_fn)

        def on_success(data):
            entry = self._backup_save_bytes(bytearray(data), mode, source="ECU read")
            self._refresh_backup_table()
            self._log(f"ECU backup saved: {entry.filename}  ({entry.file_type}, "
                      f"{entry.variant}, checksum {'OK' if entry.cs_ok else 'INVALID'})", "ok")
            QMessageBox.information(self, "Backup Saved",
                f"Saved {entry.file_type} backup:\n{entry.filename}")

        self._run_task(task, on_success=on_success)

    def _backup_save_bytes(self, data, mode: str, source: str):
        """Save an in-memory ECU image to the backup catalogue with a derived name."""
        eid = self._ecu_id or ""
        vin = self._ecu_vin or ""
        typ = "full" if mode == "full" else "partial"
        ts  = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        parts = ["ms41", eid or "ecu", typ] + ([vin] if vin else []) + [ts]
        name = "_".join(parts) + ".bin"
        return self._backup_mgr.add_data(bytes(data), name, source=source,
                                         ecu_id=eid, vin=vin)

    def _record_full_ecu_read(self, data, source="ECU read"):
        """Cache and catalogue one authoritative full ECU read from any GUI workflow."""
        data = bytes(data)
        if len(data) != MS41ECU.FULL_ROM_SIZE:
            raise ValueError(
                f"full ECU read must be {MS41ECU.FULL_ROM_SIZE:,} bytes "
                f"(got {len(data):,})")
        # Persist first. A requested backup is not considered available until the
        # bytes and catalogue entry have both been written successfully.
        entry = self._backup_save_bytes(bytearray(data), "full", source=source)
        program_variant = MS41ECU.detect_program_variant(data)
        if program_variant:
            self._ecu_program_variant = program_variant
            if self._ecu_variant != program_variant:
                self._ecu_variant = program_variant
                self._log(f"Program variant confirmed from full read: {program_variant}", "ok")
        identity_info = identity.decode_identity(data)
        if identity_info.serial and not self._ecu_identity_source:
            self._ecu_identity_source = data[:identity.BOOT_DATA_OFF + identity.BOOT_DATA_SIZE]
        self._session_backup_read = True
        self._last_full_read = data
        self._last_full_read_key = self._identity_connection_key(
            identity_info.serial)
        self._refresh_backup_table()
        return entry

    def _on_backup_flash(self):
        entry = self._selected_backup()
        if not entry:
            return
        if self._ds2 is None:
            QMessageBox.information(self, "Not Connected", "Connect to the ECU first.")
            return
        mode = "full" if entry.file_type == "Full ROM" else "tune"
        expected = MS41ECU.FULL_ROM_SIZE if mode == "full" else MS41ECU.TUNE_SIZE
        with open(entry.path, "rb") as f:
            data = bytearray(f.read())
        if len(data) != expected:
            QMessageBox.critical(self, "Size Error",
                f"Backup file size mismatch: {len(data):,} vs {expected:,} bytes.")
            return
        # Sanity check (backup file could have been corrupted or replaced externally)
        ff_ratio   = data.count(0xFF) / len(data)
        zero_ratio = data.count(0x00) / len(data)
        if ff_ratio > 0.95:
            QMessageBox.critical(self, "Invalid Backup",
                f"Backup is {ff_ratio*100:.0f}% 0xFF — appears to be a blank erased chip.\n"
                f"Flash aborted.")
            return
        if zero_ratio > 0.95:
            QMessageBox.critical(self, "Invalid Backup",
                f"Backup is {zero_ratio*100:.0f}% zero bytes and is not a valid ROM.\n"
                f"Flash aborted.")
            return
        # Route through the DS2 write path (with its variant/CAL-ID guard,
        # confirmation, retry and optional verify).
        if mode == "full":
            identity_edit = entry.source == IDENTITY_BACKUP_SOURCE
            self._ds2_write_full(
                data, os.path.basename(entry.path),
                require_boot_write=identity_edit,
                preserve_boot_identity=False if identity_edit else None)
        else:
            self._ds2_write_tune(data, os.path.basename(entry.path))

    def _on_backup_open_in_bsl(self):
        """Load the selected catalogue file as a reference without starting hardware BSL."""
        entry = self._selected_backup()
        if not entry:
            return
        path = os.path.abspath(entry.path)
        if not os.path.isfile(path):
            QMessageBox.critical(
                self, "File Missing", f"Selected Bin file was not found:\n{path}")
            return
        try:
            size = os.path.getsize(path)
        except OSError as error:
            QMessageBox.critical(
                self, "File Unavailable", f"Could not inspect the selected Bin:\n{error}")
            return
        if size not in (MS41ECU.TUNE_SIZE, MS41ECU.FULL_ROM_SIZE):
            QMessageBox.critical(
                self, "Unsupported BSL Reference",
                f"Selected Bin is {size:,} bytes. Use a 24,576-byte tune or "
                "262,144-byte full ROM.")
            return

        self._bsl_ref = path
        self._bsl_ref_lbl.setText(os.path.basename(path))
        if size == MS41ECU.TUNE_SIZE:
            tune_index = self.cb_bsl_region.findText("tune")
            if tune_index >= 0:
                self.cb_bsl_region.setCurrentIndex(tune_index)
        self._invalidate_bsl_plan()
        self.tabs.setCurrentIndex(self._bsl_tab_index)
        self._log(f"BSL reference loaded from Bins: {os.path.basename(path)}")

    def _on_backup_edit_config(self):
        """Load the selected backup into the ECU Config tab (FILE mode) and switch to it."""
        entry = self._selected_backup()
        if not entry:
            return
        if not os.path.exists(entry.path):
            QMessageBox.critical(self, "File Missing",
                f"Backup file not found:\n{entry.path}")
            return
        self._load_config_from_path(entry.path)
        # Only jump to the Config tab if the load succeeded (FILE mode is active).
        if self._config_data is not None:
            self.tabs.setCurrentIndex(self._config_tab_index)

    def _on_backup_notes(self):
        entry = self._selected_backup()
        if not entry: return
        notes, ok = QInputDialog.getText(
            self, "Edit Notes", "Notes:", text=entry.notes
        )
        if ok:
            self._backup_mgr.update_notes(entry, notes)
            self._refresh_backup_table()

    def _on_backup_delete(self):
        entry = self._selected_backup()
        if not entry: return
        ans = QMessageBox.question(
            self, "Confirm Delete",
            f"Delete backup file:\n{entry.filename}\n\nThis cannot be undone.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ans == QMessageBox.Yes:
            self._backup_mgr.remove(entry)
            self._refresh_backup_table()
            self._log(f"Backup deleted: {entry.filename}", "warn")

    def _on_backup_open_folder(self):
        folder = os.path.abspath("backups")
        os.makedirs(folder, exist_ok=True)
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(folder)):
            QMessageBox.warning(self, "Open Folder Failed", f"Could not open:\n{folder}")

    def _set_backup_buttons_enabled(self, enabled: bool=True):
        # State is recomputed from busy/connection/selection rather than the
        # `enabled` arg (kept for call-site compatibility): local file operations
        # must stay available while disconnected.
        busy           = getattr(self, "_task_busy", False)
        connected      = getattr(self, "_ds2", None) is not None
        has_selection  = len(self.backup_table.selectedIndexes()) > 0
        idle           = not busy
        # ECU operations — need a live DS2 connection.
        self.btn_backup_ecu.setEnabled(idle and connected)
        self.btn_backup_flash.setEnabled(idle and connected and has_selection)
        # Local file operations — work offline, just need a selected row.
        self.btn_backup_open_bsl.setEnabled(idle and has_selection)
        self.btn_backup_config.setEnabled(idle and has_selection)
        self.btn_backup_notes.setEnabled(idle and has_selection)
        self.btn_backup_del.setEnabled(idle and has_selection)

    def _on_check_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select ROM / Tune file", "", "Binary Files (*.bin);;All Files (*)"
        )
        if not path: return
        with open(path, "rb") as f:
            data = bytearray(f.read())
        ok, details = verify_checksum(data)
        self._log(f"--- Verify checksums: {os.path.basename(path)} ---")
        for d in details:
            self._log(d, "ok" if ok else "warn")
        self._log("Result: ALL VALID" if ok else "Result: MISMATCH — use Correct Checksums",
                  "ok" if ok else "error")

    def _on_fix_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select 256 KB ROM or 24 KB partial to correct checksums", "",
            "Binary Files (*.bin);;All Files (*)"
        )
        if not path: return
        with open(path, "rb") as f:
            data = bytearray(f.read())
        if len(data) not in (MS41ECU.FULL_ROM_SIZE, MS41ECU.TUNE_SIZE):
            QMessageBox.warning(self, "Wrong Size",
                "Checksum correction needs a 256 KB full ROM or a 24 KB partial.")
            return
        # MS41.3 enforces boot and calibration checksums while its stock
        # program-checksum gate is disabled.
        is_ms413_full = (MS41ECU.detect_variant(data) == "MS41.3"
                         and len(data) == MS41ECU.FULL_ROM_SIZE)
        patched, details = correct_checksums(data, correct_program=not is_ms413_full)
        if is_ms413_full:
            if patched[0x605C] == 0xFF:
                details.append(
                    "MS41.3: boot and calibration checksums corrected; program checksum "
                    "left unchanged because stock program verification is disabled."
                )
            else:
                details.append(
                    "MS41.3: boot and calibration checksums corrected; program checksum "
                    "left unchanged, but program verification is enabled in this image."
                )
        out, _ = QFileDialog.getSaveFileName(
            self, "Save checksum-corrected image",
            path.replace(".bin", "_cksum.bin"),
            "Binary Files (*.bin);;All Files (*)"
        )
        if not out: return
        with open(out, "wb") as f:
            f.write(patched)
        self._log(f"--- Correct checksums: {os.path.basename(path)} ---")
        for d in details: self._log(d)
        self._log(f"Saved: {out}", "ok")

    def _on_disable_cksum_file(self):
        """Disable the full-ROM program-checksum gate (0x605C → 0xFF)."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Select full 256 KB ROM to disable program checksum verification", "",
            "Binary Files (*.bin);;All Files (*)"
        )
        if not path: return
        with open(path, "rb") as f:
            data = bytearray(f.read())
        if len(data) != MS41ECU.FULL_ROM_SIZE:
            QMessageBox.warning(self, "Full ROM Required",
                "Disabling program-checksum verification needs a full 256 KB ROM — "
                "the switch at 0x605C is not present in a 24 KB partial.")
            return
        out_data, details = disable_checksum(data)
        self._log(f"--- Disable program checksum verification: {os.path.basename(path)} ---")
        for d in details: self._log(d)
        if out_data == data:
            QMessageBox.information(self, "Already Disabled",
                "Program-checksum verification is already disabled in this image "
                "(0x605C = 0xFF). Nothing to change.")
            return
        out, _ = QFileDialog.getSaveFileName(
            self, "Save ROM with program checksum verification disabled",
            path.replace(".bin", "_nocksum.bin"),
            "Binary Files (*.bin);;All Files (*)"
        )
        if not out: return
        with open(out, "wb") as f:
            f.write(out_data)
        self._log(f"Saved (program checksum verification disabled): {out}", "ok")
        QMessageBox.information(self, "Program Checksum Verification Disabled",
            f"Saved with program-checksum verification disabled:\n{os.path.basename(out)}\n\n"
            "Boot and calibration checksum verification remain unchanged.")

    # -------------------------------------------------------------------
    # DTC
    # -------------------------------------------------------------------

    def _on_read_dtc(self):
        if not self._ds2: return

        def task(log_fn, progress_fn):
            log_fn("Requesting DTCs (DS2 0x04)…")
            raw = self._ds2.read_dtc()
            log_fn(f"DS2 DTC response: {len(raw)} bytes")
            dtcs = parse_ds2_dtc_response(raw)
            log_fn(f"Decoded {len(dtcs)} unique DTC(s)")
            return dtcs

        def on_success(dtcs):
            self._dtcs = dtcs
            self._populate_dtc_table(dtcs)
            active  = sum(1 for d in dtcs if d.is_active)
            stored  = len(dtcs) - active
            if not dtcs:
                self.lbl_dtc_count.setText("✓  No DTCs stored")
                self.lbl_dtc_count.setStyleSheet("color:#5f5; padding:4px; font-weight:bold;")
            else:
                parts = []
                if active: parts.append(f"{active} active")
                if stored: parts.append(f"{stored} stored")
                self.lbl_dtc_count.setText(f"⚠  {len(dtcs)} DTC(s): {', '.join(parts)}")
                self.lbl_dtc_count.setStyleSheet("color:#e8c46a; padding:4px; font-weight:bold;")
            self.dtc_detail.clear()
            self._log(f"DTC read complete: {len(dtcs)} code(s) found.", "ok")

        self._run_task(task, on_success=on_success)

    def _on_clear_dtc(self):
        if not self._ds2: return
        ans = QMessageBox.question(
            self, "Confirm Clear DTCs",
            "This will erase all stored DTCs from the ECU.\n\nProceed?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ans != QMessageBox.Yes: return

        def task(log_fn, progress_fn):
            log_fn("Clearing DTCs (DS2 0x05)…")
            self._ds2.clear_dtc()
            return "DTCs cleared"

        def on_success(msg):
            self._dtcs = []
            self._populate_dtc_table([])
            self.lbl_dtc_count.setText("✓  DTCs cleared")
            self.lbl_dtc_count.setStyleSheet("color:#5f5; padding:4px; font-weight:bold;")
            self.dtc_detail.clear()
            self._log(msg, "ok")

        self._run_task(task, on_success=on_success)

    def _on_export_dtc(self):
        if not self._dtcs:
            QMessageBox.information(self, "No Data", "Read DTCs first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export DTCs", "dtc_report.txt", "Text Files (*.txt);;All Files (*)"
        )
        if not path: return
        text = format_dtc_table(self._dtcs)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        self._log(f"DTC report saved → {path}", "ok")

    def _populate_dtc_table(self, dtcs: list):
        self.dtc_table.setRowCount(0)
        for dtc in dtcs:
            row = self.dtc_table.rowCount()
            self.dtc_table.insertRow(row)

            items = [
                dtc.code_hex,
                dtc.sae_code,
                dtc.system,
                dtc.status_text,
                dtc.description,
            ]
            for col, text in enumerate(items):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)

                # Colour the status cell by status
                if col == 3:
                    for keyword, colour in STATUS_COLOURS.items():
                        if keyword in text:
                            item.setForeground(QBrush(colour))
                            break

                # Colour the system cell
                if col == 2:
                    colour = SYSTEM_COLOURS.get(dtc.system)
                    if colour:
                        item.setForeground(QBrush(QColor(colour)))

                # Highlight active faults in red on BMW code
                if col == 0 and dtc.is_active:
                    item.setForeground(QBrush(QColor("#f47171")))

                self.dtc_table.setItem(row, col, item)

        self.dtc_table.resizeRowsToContents()

    def _on_dtc_selected(self):
        rows = self.dtc_table.selectedItems()
        if not rows: return
        row = self.dtc_table.currentRow()
        if row < 0 or row >= len(self._dtcs): return
        d = self._dtcs[row]

        def kv(label, value, val_color="#e0e0e0"):
            return (
                f'<tr>'
                f'<td style="color:#888; padding:1px 10px 1px 0; white-space:nowrap;">{label}</td>'
                f'<td style="color:{val_color}; padding:1px 0;">{value}</td>'
                f'</tr>'
            )

        def flag(yes, yes_label="Yes", no_label="No"):
            return (f'<span style="color:#5f5;">{yes_label}</span>' if yes
                    else f'<span style="color:#888;">{no_label}</span>')

        if isinstance(d, DS2DTCRecord):
            active_color = "#f47171" if d.is_active else "#5f5"
            rows_html = (
                kv("DS2 Code", f'<b style="color:#7ec8e3;">{d.code}</b>  <span style="color:#888;">({d.sae_code})</span>')
                + kv("System", d.system, "#c8a85f")
                + kv("Status", f'{d.status_text}  <span style="color:#555;">raw=0x{d.status_raw:02X}</span>')
                + kv("Active", flag(d.is_active), active_color)
                + kv("Raw", f'<span style="font-family:\'Courier New\',monospace; font-size:9pt; color:#888;">{d.raw_record.hex(" ").upper()}</span>')
                + kv("Description", f'<b>{d.description}</b>')
            )
        else:
            active_color = "#f47171" if d.is_active else "#5f5"
            rows_html = (
                kv("BMW Code", f'<b style="color:#7ec8e3;">{d.code_hex}</b>')
                + kv("SAE Code", d.sae_code, "#aaa")
                + kv("System", d.system, "#c8a85f")
                + kv("Status", f'{d.status_text}  <span style="color:#555;">raw=0x{d.status:02X}</span>')
                + kv("Flags",
                     f'Active: {flag(d.is_active)} &nbsp;&nbsp; '
                     f'Confirmed: {flag(d.is_confirmed)} &nbsp;&nbsp; '
                     f'Pending: {flag(d.is_pending)}')
                + kv("Description", f'<b>{d.description}</b>')
            )

        html = (
            '<html><body style="font-family:\'Courier New\',monospace; font-size:9pt; '
            'background:#1a1a1a; color:#d4d4d4; margin:0; padding:0;">'
            f'<table style="border-spacing:0; padding:2px;">{rows_html}</table>'
            '</body></html>'
        )
        self.dtc_detail.setHtml(html)

    # -------------------------------------------------------------------
    # Shared task runner
    # -------------------------------------------------------------------

    def _ds2_read(self, which: str, progress_fn, log_fn):
        """Read 'full' or 'tune' over DS2 (9600)."""
        fn = self._ds2.read_full if which == "full" else self._ds2.read_partial
        return fn(progress_cb=progress_fn, log_fn=log_fn)

    def _ds2_write(self, which: str, image_bytes, progress_fn, log_fn):
        """Write 'full' or 'tune' over DS2 (9600)."""
        self._require_previous_write_cycle(log_fn)
        fn = self._ds2.write_full if which == "full" else self._ds2.write_partial
        fn(image_bytes, progress_cb=progress_fn, log_fn=log_fn)

    def _require_previous_write_cycle(self, log_fn) -> None:
        """Prove a requested post-write power cycle before another stock write.

        A successful write can leave E658=2 even after the partial-write path
        has returned to normal DS2 at 9600.  Reusing that authorization for a
        brand-new operation is unsafe: live testing reached programming and
        then failed in the next finalizer.  This guard runs before ownership is
        handed to either native-fast or conventional DS2 write code.
        """
        if not self._post_write_cycle_pending:
            return
        if self._ds2 is None:
            raise StockWriteNotStarted(
                "A previous write still requires ignition OFF for at least 10 seconds, "
                "then ON. Reconnect after that cycle before starting another write."
            )
        try:
            state_raw = self._ds2.read_mem(AUTHORIZATION_STATE_ADDRESS, 1)
        except Exception as error:
            raise StockWriteNotStarted(
                "Could not confirm that the required post-write ignition cycle completed. "
                "Turn ignition OFF for at least 10 seconds, turn it ON, reconnect, and retry. "
                "Nothing was erased by this attempt."
            ) from error
        if len(state_raw) != 1:
            raise StockWriteNotStarted(
                "Post-write authorization check returned an invalid response. Nothing was "
                "erased; complete the OFF / 10 seconds / ON cycle and reconnect."
            )
        state = state_raw[0]
        if state != 0:
            raise StockWriteNotStarted(
                "The previous write authorization is still active "
                f"(E658={state}). Nothing was erased by this attempt. Turn ignition OFF, "
                "wait at least 10 seconds, turn ignition ON, then retry."
            )
        self._post_write_cycle_pending = False
        log_fn("Required post-write ignition cycle confirmed (E658=0).", "ok")

    def _read_image_auto(self, which: str, log_fn, progress_fn):
        """Use Soft-BSL, otherwise native fast DS2, otherwise normal DS2."""
        route = self._auto_transfer_route()
        if route == "softbsl":
            log_fn("Fast (Soft-BSL) read — RAM agent, high baud (auto).", "ok")
            fam = self._fast_chip_family()
            return self._run_via_softbsl(
                lambda port, pf, lf: softbsl_service.read_image(port, which, "high", pf, lf,
                                                                chip_family=fam),
                log_fn, progress_fn)
        if route == "native_ds2":
            log_fn(
                "Fast native DS2 read — direct 9600 → 187500, normal DS2 fallback.",
                "ok",
            )
            return self._native_fast_read_with_fallback(which, log_fn, progress_fn)
        log_fn(f"Reading {'full 256 KB ROM' if which == 'full' else '24 KB tune'} over DS2 (9600)…")
        return self._ds2_read(which, progress_fn, log_fn)

    def _write_tune_auto(
        self,
        image_bytes,
        log_fn,
        progress_fn,
        *,
        verify_write,
    ):
        """Write a tune through the same automatic route as the Flash tab."""
        route = self._auto_transfer_route()
        if route == "softbsl":
            log_fn("Fast (Soft-BSL) write — RAM agent, high baud (auto).", "ok")
            fam = self._fast_chip_family()
            self._run_via_softbsl(
                lambda port, pf, lf: softbsl_service.write_tune(
                    port, bytes(image_bytes), lf, baud="high",
                    progress_cb=pf, do_verify=verify_write, chip_family=fam),
                log_fn, progress_fn)
            if not verify_write:
                log_fn(VERIFY_OFF_MESSAGE, "warn")
        elif route == "native_ds2":
            self._native_fast_write_with_fallback(
                "tune",
                bytes(image_bytes),
                self._fast_chip_family(),
                log_fn,
                progress_fn,
                verify_write=verify_write,
            )
        else:
            self._ds2_write("tune", bytes(image_bytes), progress_fn, log_fn)
            if verify_write:
                self._ds2_verify_after_write("tune", bytes(image_bytes), log_fn, progress_fn)

    def _run_task(self, task_fn, on_success=None, on_failure=None):
        # A live-data poller owns the same DS2 link from a background thread.
        # Stop and join it before any ECU task so authorization recovery can
        # provide a genuinely silent bus interval and no request races a port
        # handoff or baud transition.
        if self._poller is not None:
            self._on_live_stop()
        self._task_busy = True
        self._set_all_buttons_enabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self._worker = WorkerThread(task_fn)
        self._worker.log_signal.connect(self._log)
        self._worker.progress_signal.connect(self._on_progress)

        def _done(success, result):
            self._task_busy = False
            self.progress_bar.setVisible(False)
            self.progress_label.setText("")
            try:
                # Completion callbacks may release a Soft-BSL port handoff and reopen DS2.
                # Run them BEFORE deriving final button state; otherwise `_ds2 is None` at
                # the old restore point and every ECU tab remains falsely disconnected.
                if success:
                    if on_success:
                        on_success(result)
                    elif isinstance(result, str):
                        self._log(result, "ok")
                else:
                    if isinstance(result, StockWriteNotStarted):
                        self._log(f"Write not started: {result}", "warn")
                    else:
                        self._log(f"ERROR: {result}", "error")
                    if on_failure:
                        on_failure(result)
                    else:
                        QMessageBox.critical(self, "Operation Failed", str(result))
            finally:
                self._set_all_buttons_enabled(True)

        self._worker.done_signal.connect(_done)
        self._worker.start()

    def _on_progress(self, done: int, total: int, label: str):
        if total == 0:
            # Finalization, baud return, and reconnect are active protocol
            # phases, but they do not transfer another byte range.  Preserve
            # the completed bar and keep the current work visible by label.
            self.progress_label.setText(label)
            return
        if total > 0:
            self.progress_bar.setValue(int(done * 100 / total))
            if done == 0 and total == 1:
                # Native-fast phase transitions are status-only updates.  They
                # deliberately reset the completed base-read bar without
                # pretending that authorization or erase settling are bytes.
                self.progress_label.setText(label)
            else:
                self.progress_label.setText(
                    f"{label}  {done//1024}/{total//1024} KB"
                )

    # -------------------------------------------------------------------
    # Logging
    # -------------------------------------------------------------------

    def _log(self, msg: str, level: str = "info"):
        text = str(msg)
        level = str(level).lower()
        # DEBUG is the machine-facing tier: retain it for beta reports while
        # keeping the compact operator log focused on actionable information.
        if self._log_file:
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            try:
                self._log_file.write(f"[{ts}] [{level.upper():5s}] {text}\n")
                self._log_file.flush()
            except Exception:
                pass
        if level == "debug":
            return
        colours = {
            "info":  "#d4d4d4",
            "ok":    "#6adf6a",
            "warn":  "#e8c46a",
            "error": "#f47171",
            "debug": "#888888",
        }
        colour  = colours.get(level, "#d4d4d4")
        escaped = text.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        self.log_view.append(f'<span style="color:{colour};">{escaped}</span>')
        self.log_view.moveCursor(QTextCursor.End)

    def _start_session_log(self):
        os.makedirs(LOG_DIR, exist_ok=True)
        ts       = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path     = os.path.join(LOG_DIR, f"session_{ts}.txt")
        try:
            self._log_file = open(path, "w", encoding="utf-8")
            header = (
                f"BimmerStein ECU Tool — Session log\n"
                f"Started : {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"DEBUG entries are retained here but hidden from the in-app log window.\n"
                f"{'=' * 60}\n\n"
            )
            self._log_file.write(header)
            self._log_file.flush()
        except Exception:
            self._log_file = None

    def _end_session_log(self):
        if self._log_file:
            try:
                self._log_file.write(
                    f"\n{'=' * 60}\n"
                    f"Session ended : {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                )
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None

    # -------------------------------------------------------------------
    # Button state management
    # -------------------------------------------------------------------

    def _set_ecu_buttons_enabled(self, enabled: bool):
        ecu_btns = [
            self.btn_read_full, self.btn_read_tune,
            self.btn_write_full, self.btn_write_tune,
            self.btn_read_dtc, self.btn_clear_dtc, self.btn_export_dtc,
            self.btn_info, self.btn_reset_adapt,
            self.btn_id_read_flash_ecu, self.btn_id_read_ecu,
            self.btn_softbsl_install,
        ]
        for btn in ecu_btns:
            btn.setEnabled(enabled)
        if not enabled:
            if hasattr(self, "btn_id_vin_apply"):
                self.btn_id_vin_apply.setEnabled(False)
            if hasattr(self, "btn_ews_send"):
                self.btn_ews_send.setEnabled(False)
        self._set_live_buttons_enabled(enabled)
        self._set_backup_buttons_enabled(enabled)
        # Config-tab buttons depend on file/ECU mode + connection, not `enabled`
        # alone (file editing works offline), so recompute from current state.
        self._update_config_buttons()
        self._update_softbsl_crossbank_button()

    def _set_all_buttons_enabled(self, enabled: bool):
        if enabled and self._ds2 is not None:
            self._set_ds2_buttons_enabled()          # DS2 connected: enable ECU ops
        else:
            self._set_ecu_buttons_enabled(False)     # disconnected: disable ECU ops
        for w in [self.btn_check_file, self.btn_fix_file, self.btn_disable_cksum,
                  self.btn_connect]:
            w.setEnabled(enabled)
        # Direct Tap controls transport construction (echo suppression) and cannot be changed
        # on an already-open DS2 session. Keep the selected value, but lock it with the port.
        connection_editable = enabled and self._ds2 is None
        self.cb_port.setEnabled(connection_editable)
        self.chk_direct_tap.setEnabled(connection_editable)
        self._set_bsl_controls_enabled(enabled)
        self._update_softbsl_crossbank_button()
        self._update_transfer_mode()
        _recovery_kind, active_recovery = self._active_write_recovery()
        recovery_available = bool(enabled and active_recovery is not None)
        self.btn_native_recovery.setVisible(recovery_available)
        self.btn_native_recovery.setEnabled(recovery_available)
        if recovery_available:
            self.btn_connect.setEnabled(False)
            self.cb_port.setEnabled(False)
            self.chk_direct_tap.setEnabled(False)

    def closeEvent(self, event):
        # Don't silently close over a running read/write/flash — a half-written flash can need
        # re-flashing to recover. Let the user abort the close instead.
        recovery_kind, recovery = self._active_write_recovery()
        if getattr(self, "_task_busy", False):
            if recovery is not None:
                QMessageBox.warning(
                    self,
                    "Flash Recovery In Progress",
                    f"The {recovery_kind} recovery retry is still running. Wait for it "
                    "to return before retrying again or abandoning the retained session.",
                )
                event.ignore()
                return
            if QMessageBox.warning(
                    self, "Operation In Progress",
                    "A read / write / flash is still running. Closing now can interrupt it and leave "
                    "the ECU in an intermediate state (a half-written flash may need re-flashing).\n\n"
                    "Close anyway?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                event.ignore()
                return
        if recovery is not None:
            answer = QMessageBox.warning(
                self,
                "Flash Recovery Active",
                f"A post-erase {recovery_kind} flash recovery session is still active.\n\n"
                "If ignition is still ON, do not close the application; use Retry Flash "
                "Recovery first.\n\n"
                "If you have already turned ignition OFF for at least 10 seconds, recovered "
                "the ECU by another method, or otherwise know the retained session is no "
                "longer usable, choose Close to abandon it and exit.",
                QMessageBox.Close | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if answer != QMessageBox.Close:
                event.ignore()
                return
            try:
                recovery.close_after_confirmed_power_cycle()
            except Exception as error:
                self._log(
                    f"Retained {recovery_kind} session close reported: {error}",
                    "warn",
                )
            for attribute in (
                "_softbsl_install_recovery",
                "_softbsl_write_recovery",
                "_native_write_recovery",
            ):
                if getattr(self, attribute, None) is recovery:
                    setattr(self, attribute, None)
            for owner in ("native_fast_ds2", "softbsl"):
                try:
                    self._port_owner.release(owner)
                except Exception:
                    pass
            self.btn_native_recovery.setVisible(False)
            self.btn_native_recovery.setEnabled(False)
            self._log(
                f"Abandoned the retained {recovery_kind} session after explicit close "
                "confirmation.",
                "warn",
            )
        self._on_live_stop()
        self._disconnect()                       # closes the DS2 handle + releases the 'flasher' owner
        # Defensively release the soft-BSL owner too: a Fast op normally releases it in its own finally,
        # but a mid-flight close might not have reached that, which would leave the port marked busy.
        try:
            self._port_owner.release("softbsl")
        except Exception:
            pass
        self._end_session_log()
        super().closeEvent(event)

    # -------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------

    def _op_btn(self, label: str, colour: str, slot) -> QPushButton:
        btn = QPushButton(label)
        btn.setMinimumHeight(40)
        btn.setStyleSheet(
            f"QPushButton {{ background:{colour}; color:white; border-radius:4px; "
            f"font-weight:bold; padding:4px 10px; }}"
            f"QPushButton:hover {{ background:{colour}cc; }}"
            f"QPushButton:disabled {{ background:#333; color:#888; }}"
        )
        btn.clicked.connect(slot)
        return btn


if __name__ == "__main__":
    from PyQt5.QtWidgets import QApplication
    app = QApplication(sys.argv)
    configure_application(app)
    install_exception_handler()
    w   = MS41FlashGUI()
    w.show()
    sys.exit(app.exec_())
