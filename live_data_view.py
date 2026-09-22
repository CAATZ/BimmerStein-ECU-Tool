"""Native Qt live and saved-log views; acquisition stays in live_data.py."""

from bisect import bisect_left
import math
from pathlib import Path

from PyQt5.QtCore import Qt, QRectF, QPointF
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QScrollArea, QSlider, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from live_log import MAX_LOG_BYTES, display_specs, parse_log, sample_value


COLORS = ("#70c7ec", "#efbd65", "#93d58e", "#da9aea", "#f68b8b", "#75d8cb")
MAX_LIVE_SAMPLES = 5_000


def nearest_sample(times, timestamp):
    if not times:
        return 0
    index = bisect_left(times, timestamp)
    if index == len(times) or (index and timestamp - times[index - 1] <= times[index] - timestamp):
        index -= 1
    return index


def plot_segments(times, values, start, stop, width):
    """Bound paint work by pixels while preserving peaks and missing-data gaps."""
    step = max(1, math.ceil((stop - start) / max(1, width)))
    segment = []
    bucket = []
    def flush():
        if bucket:
            indices = sorted({bucket[0], bucket[-1],
                              min(bucket, key=values.__getitem__),
                              max(bucket, key=values.__getitem__)})
            segment.extend((times[i], values[i]) for i in indices)
            bucket.clear()
    for index in range(start, stop):
        if values[index] is None:
            flush()
            if segment:
                yield segment
                segment = []
        else:
            bucket.append(index)
            if len(bucket) >= step:
                flush()
    flush()
    if segment:
        yield segment


class _HistoryPlot(QWidget):
    def __init__(self, view):
        super().__init__()
        self.view = view
        self.setMinimumHeight(260)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("Live Data history graph")
        self.setToolTip("Wheel: zoom time. Drag: sample cursor. Shift-drag: pan time.")
        self._drag = None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#1e1e1e"))
        view = self.view
        names = view.selected_channels()
        if not view.times or not names:
            painter.setPen(self.palette().windowText().color())
            painter.drawText(self.rect(), Qt.AlignCenter,
                             "Select channels to graph" if view.times else "No samples yet")
            return
        left, right = view.time_range()
        start = max(0, bisect_left(view.times, left) - 1)
        stop = min(len(view.times), bisect_left(view.times, right) + 1)
        chart_width = max(1, self.width() - 88)
        row_height = max(120, self.height() // len(names))
        for order, name in enumerate(names):
            series = view.series[name]
            color = QColor(COLORS[order % len(COLORS)])
            top = order * row_height
            rect = QRectF(65, top + 28, chart_width, row_height - 52)
            numeric = [v for v in series["values"][start:stop] if v is not None]
            low, high = (min(numeric), max(numeric)) if numeric else (0, 1)
            margin = (high - low) * .05 if high > low else max(1, abs(low) * .05)
            low, high = low - margin, high + margin
            painter.setPen(QColor("#444444"))
            painter.drawRect(rect)
            painter.drawLine(QPointF(rect.left(), rect.center().y()),
                             QPointF(rect.right(), rect.center().y()))
            painter.setPen(self.palette().windowText().color())
            painter.drawText(QRectF(8, top + 4, self.width() - 16, 20),
                             Qt.AlignLeft, f"{name}  {view.sample_text(name)} {series['unit']}")
            painter.drawText(QRectF(0, rect.top(), 59, 18), Qt.AlignRight, f"{high:.4g}")
            painter.drawText(QRectF(0, rect.bottom() - 18, 59, 18), Qt.AlignRight, f"{low:.4g}")
            painter.drawText(QRectF(rect.left(), rect.bottom() + 3, 100, 18),
                             Qt.AlignLeft, f"{left:.2f} s")
            painter.drawText(QRectF(rect.right() - 100, rect.bottom() + 3, 100, 18),
                             Qt.AlignRight, f"{right:.2f} s")
            painter.save()
            painter.setClipRect(rect)
            painter.setPen(QPen(color, 1.5))
            for segment in plot_segments(view.times, series["values"], start, stop, int(chart_width)):
                path = QPainterPath()
                for index, (timestamp, value) in enumerate(segment):
                    point = QPointF(rect.left() + (timestamp - left) / (right - left) * rect.width(),
                                    rect.bottom() - (value - low) / (high - low) * rect.height())
                    if index == 0:
                        path.moveTo(point)
                    else:
                        path.lineTo(point)
                if len(segment) == 1:
                    painter.drawEllipse(point, 2, 2)
                else:
                    painter.drawPath(path)
            cursor_time = view.times[view.cursor]
            x = rect.left() + (cursor_time - left) / (right - left) * rect.width()
            painter.setPen(QPen(QColor("#eeeeee"), 1, Qt.DashLine))
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            painter.restore()

    def _time_at(self, x):
        left, right = self.view.time_range()
        fraction = max(0., min(1., (x - 65) / max(1, self.width() - 88)))
        return left + fraction * (right - left)

    def wheelEvent(self, event):
        if not self.view.times:
            return
        self.view.zoom(.75 if event.angleDelta().y() > 0 else 1.333,
                       self._time_at(event.pos().x()))
        event.accept()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or not self.view.times:
            return
        self._drag = (event.pos().x(), self.view.time_range())
        self.view.follow.setChecked(False)
        if not event.modifiers() & Qt.ShiftModifier:
            self.view.set_cursor(nearest_sample(self.view.times, self._time_at(event.pos().x())))

    def mouseMoveEvent(self, event):
        if self._drag is None:
            return
        if event.modifiers() & Qt.ShiftModifier:
            x, (left, right) = self._drag
            offset = (x - event.pos().x()) * (right - left) / max(1, self.width() - 88)
            self.view.set_time_range(left + offset, right + offset)
        else:
            self.view.set_cursor(nearest_sample(self.view.times, self._time_at(event.pos().x())))

    def mouseReleaseEvent(self, event):
        self._drag = None

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Left, Qt.Key_Right):
            self.view.follow.setChecked(False)
            self.view.set_cursor(self.view.cursor + (1 if event.key() == Qt.Key_Right else -1))
        else:
            super().keyPressEvent(event)


class _Gauges(QWidget):
    def __init__(self, view):
        super().__init__()
        self.view = view
        self.setAccessibleName("Live Data gauges and digital values")

    def resizeEvent(self, event):
        self.update_geometry()
        super().resizeEvent(event)

    def update_geometry(self):
        columns = max(1, self.width() // 240)
        self.setMinimumHeight(max(180, math.ceil(len(self.view.selected_channels()) / columns) * 185))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        names = self.view.selected_channels()
        columns = max(1, self.width() // 240)
        width = self.width() / columns
        for index, name in enumerate(names):
            x, y = (index % columns) * width, (index // columns) * 185
            rect = QRectF(x + 6, y + 6, width - 12, 173)
            painter.setBrush(QColor("#242424"))
            painter.setPen(QColor("#444444"))
            painter.drawRoundedRect(rect, 5, 5)
            painter.setFont(self.font())
            painter.setPen(self.palette().windowText().color())
            painter.drawText(rect.adjusted(10, 6, -10, -125),
                             Qt.AlignCenter | Qt.TextWordWrap, name)
            spec = self.view.specs.get(name, {})
            series = self.view.series[name]
            value = series["values"][self.view.cursor] if self.view.times else None
            color = QColor(COLORS[index % len(COLORS)])
            if spec.get("kind") == "dial":
                dial = QRectF(rect.center().x() - 53, y + 50, 106, 106)
                painter.setPen(QPen(QColor("#555555"), 6))
                painter.drawArc(dial, -30 * 16, 240 * 16)
                low, high = spec["minimum"], spec["maximum"]
                if value is not None:
                    fraction = max(0., min(1., (value - low) / (high - low)))
                    angle = math.radians(210 - 240 * fraction)
                    painter.setPen(QPen(color, 3))
                    painter.drawLine(dial.center(), dial.center() + QPointF(
                        math.cos(angle) * 43, -math.sin(angle) * 43))
                painter.setPen(QColor("#bbbbbb"))
                painter.drawText(QRectF(x + 12, y + 135, width - 24, 20),
                                 Qt.AlignLeft, f"{low:g}")
                painter.drawText(QRectF(x + 12, y + 135, width - 24, 20),
                                 Qt.AlignRight, f"{high:g}")
                value_rect = QRectF(x + 15, y + 152, width - 30, 23)
            else:
                value_rect = rect.adjusted(12, 60, -12, -23)
            font = QFont(self.font())
            font.setFamily("Consolas")
            font.setPointSizeF(max(font.pointSizeF(), 12))
            painter.setFont(font)
            painter.setPen(color)
            painter.drawText(value_rect, Qt.AlignCenter | Qt.TextWordWrap,
                             f"{self.view.sample_text(name)} {series['unit']}")


class LiveDataView(QWidget):
    """Display exact poll cycles or a validated CSV with the same controls."""
    def __init__(self, parent=None, *, definition_path=None, live=True):
        super().__init__(parent)
        self.definition_path = definition_path
        self.live = live
        self.times = []
        self.series = {}
        self.specs = {}
        self.cursor = 0
        self._range = None
        self.dropped = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        self.follow = QCheckBox("Follow latest", self)
        self.follow.setChecked(live)
        self.follow.setVisible(live)
        self.follow.toggled.connect(self._follow_changed)
        toolbar.addWidget(self.follow)
        for text, callback in (("Zoom +", lambda: self.zoom(.5)),
                               ("Zoom −", lambda: self.zoom(2)), ("Fit", self.fit)):
            button = QPushButton(text)
            button.clicked.connect(callback)
            toolbar.addWidget(button)
        toolbar.addStretch()
        self.position = QLabel("No samples")
        toolbar.addWidget(self.position)
        layout.addLayout(toolbar)
        splitter = QSplitter()
        channels = QWidget()
        channels_layout = QVBoxLayout(channels)
        channels_layout.setContentsMargins(0, 0, 0, 0)
        channels_layout.addWidget(QLabel("Graph / gauge channels"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find channel…")
        self.search.textChanged.connect(self._filter_channels)
        channels_layout.addWidget(self.search)
        self.channels = QListWidget()
        self.channels.setMinimumWidth(160)
        self.channels.setAccessibleName("Channels shown in graphs and gauges")
        self.channels.itemChanged.connect(self._selection_changed)
        channels_layout.addWidget(self.channels)
        buttons = QHBoxLayout()
        for text, state in (("All", Qt.Checked), ("None", Qt.Unchecked)):
            button = QPushButton(text)
            button.clicked.connect(lambda checked=False, value=state: self._select_all(value))
            buttons.addWidget(button)
        channels_layout.addLayout(buttons)
        splitter.addWidget(channels)
        self.tabs = QTabWidget()
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Parameter", "Value", "Unit"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.tabs.addTab(self.table, "Values")
        self.plot = _HistoryPlot(self)
        self.gauges = _Gauges(self)
        for widget, title in ((self.plot, "Graphs"), (self.gauges, "Gauges / digital")):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(widget)
            self.tabs.addTab(scroll, title)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([200, 700])
        layout.addWidget(splitter, 1)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setAccessibleName("Sample cursor")
        self.slider.valueChanged.connect(self._slider_changed)
        layout.addWidget(self.slider)
        self.note = QLabel("Live history keeps 5,000 samples; CSV retains every recorded sample."
                           if live else "Wheel: zoom time · Drag: cursor · Shift-drag: pan · Arrows: one sample")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

    def selected_channels(self):
        return [self.channels.item(i).text() for i in range(self.channels.count())
                if self.channels.item(i).checkState() == Qt.Checked]

    def set_channels(self, rows):
        selected = set(self.selected_channels())
        had_channels = self.channels.count() > 0
        self.channels.blockSignals(True)
        self.channels.clear()
        self.series = {name: {"name": name, "unit": unit, "values": [], "labels": []}
                       for name, unit in rows}
        self.specs = display_specs(rows, self.definition_path)
        self.table.setRowCount(len(rows))
        self.table_rows = {}
        for index, (name, unit) in enumerate(rows):
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if (name in selected if had_channels else index < 3)
                               else Qt.Unchecked)
            self.channels.addItem(item)
            self.table_rows[name] = index
            for column, text in enumerate((name, "—", unit)):
                cell = QTableWidgetItem(text)
                if column == 1:
                    font = QFont(self.font())
                    font.setFamily("Consolas")
                    cell.setFont(font)
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(index, column, cell)
        self.channels.blockSignals(False)
        self._filter_channels(self.search.text())
        self._selection_changed()

    def clear(self, rows=None):
        self.times = []
        self.cursor = 0
        self._range = None
        self.dropped = 0
        self.set_channels(rows if rows is not None else
                          [(name, item["unit"]) for name, item in self.series.items()])
        self.follow.setChecked(self.live)
        self._refresh()

    def load(self, payload):
        self.clear([(item["name"], item["unit"]) for item in payload["series"]])
        self.times = list(payload["times"])
        self.series = {item["name"]: item for item in payload["series"]}
        self.specs = payload["display_specs"]
        self.cursor = 0
        self.fit()
        self._refresh()

    def append_samples(self, rows, samples, dropped=0):
        if not samples:
            return
        if list(rows) != [(name, item["unit"]) for name, item in self.series.items()]:
            self.clear(list(rows))
        cursor_time = self.times[self.cursor] if self.times else 0
        self.dropped += dropped
        for _sequence, elapsed, values in samples:
            self.times.append(elapsed)
            for item, raw in zip(self.series.values(), values):
                value, label = sample_value(raw)
                item["values"].append(value)
                item["labels"].append(label)
        # ponytail: bounded in-memory history; use the complete CSV for longer sessions.
        remove = max(0, len(self.times) - MAX_LIVE_SAMPLES)
        if remove:
            del self.times[:remove]
            for item in self.series.values():
                del item["values"][:remove]
                del item["labels"][:remove]
        if self.follow.isChecked():
            self.cursor = len(self.times) - 1
            self._range = (max(self.times[0], self.times[-1] - 30), self.times[-1])
        else:
            self.cursor = nearest_sample(self.times, cursor_time)
        self._refresh()

    def sample_text(self, name):
        if not self.times:
            return "—"
        item = self.series[name]
        value, label = item["values"][self.cursor], item["labels"][self.cursor]
        return label if label is not None else (f"{value:.6g}" if value is not None else "—")

    def time_range(self):
        left, right = self._range or ((self.times[0], self.times[-1]) if self.times else (0, 1))
        return left, max(right, left + .001)

    def set_time_range(self, left, right):
        if not self.times:
            return
        span = min(max(.001, right - left), max(.001, self.times[-1] - self.times[0]))
        left = max(self.times[0], min(left, self.times[-1] - span))
        self._range = (left, left + span)
        self.plot.update()

    def zoom(self, factor, center=None):
        if not self.times:
            return
        self.follow.setChecked(False)
        left, right = self.time_range()
        center = self.times[self.cursor] if center is None else center
        center = max(left, min(right, center))
        self.set_time_range(center - (center - left) * factor,
                            center + (right - center) * factor)

    def fit(self):
        self.follow.setChecked(False)
        self._range = None
        self.plot.update()

    def set_cursor(self, index):
        self.cursor = max(0, min(index, len(self.times) - 1))
        self._refresh()

    def _slider_changed(self, value):
        self.follow.setChecked(False)
        self.set_cursor(value)

    def _follow_changed(self, checked):
        if checked and self.times:
            self._range = (max(self.times[0], self.times[-1] - 30), self.times[-1])
            self.set_cursor(len(self.times) - 1)

    def _filter_channels(self, text):
        for index in range(self.channels.count()):
            item = self.channels.item(index)
            item.setHidden(text.casefold() not in item.text().casefold())

    def _select_all(self, state):
        self.channels.blockSignals(True)
        for index in range(self.channels.count()):
            self.channels.item(index).setCheckState(state)
        self.channels.blockSignals(False)
        self._selection_changed()

    def _selection_changed(self, *_args):
        self.plot.setMinimumHeight(max(260, len(self.selected_channels()) * 120))
        self.plot.update()
        self.gauges.update_geometry()

    def _refresh(self):
        self.slider.blockSignals(True)
        self.slider.setRange(0, max(0, len(self.times) - 1))
        self.slider.setValue(self.cursor)
        self.slider.blockSignals(False)
        self.slider.setEnabled(bool(self.times))
        for name, index in getattr(self, "table_rows", {}).items():
            value = self.sample_text(name)
            self.table.item(index, 1).setText(value)
            self.table.item(index, 1).setToolTip(value)
        text = (f"{self.times[self.cursor]:.3f} s · Sample {self.cursor + 1:,} / {len(self.times):,}"
                if self.times else "No samples")
        if self.dropped:
            text += f" · {self.dropped:,} display samples missed"
        self.position.setText(text)
        self.plot.update()
        self.gauges.update()


def show_log(parent, path, definition_path=None):
    """Open a saved CSV without connecting to an ECU; errors stay in the UI."""
    try:
        if definition_path is None:
            from logger_definition_registry import LoggerDefinitionRegistry
            definition_path = LoggerDefinitionRegistry().active_path()
        with open(path, "rb") as stream:
            payload = parse_log(stream.read(MAX_LOG_BYTES + 1), definition_path)
        dialog = QDialog(parent)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.setWindowTitle(f"Live Data Log — {Path(path).name}")
        dialog.resize(1120, 780)
        dialog.setMinimumSize(600, 450)
        layout = QVBoxLayout(dialog)
        view = LiveDataView(dialog, definition_path=definition_path, live=False)
        view.load(payload)
        layout.addWidget(view)
        close = QPushButton("Close")
        close.clicked.connect(dialog.close)
        layout.addWidget(close, alignment=Qt.AlignRight)
        dialog.show()
        return dialog
    except (OSError, ValueError, RuntimeError) as error:
        QMessageBox.warning(parent, "Cannot Open Live Data Log", str(error))
        return None
