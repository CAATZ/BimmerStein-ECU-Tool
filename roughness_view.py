"""Compact native Qt roughness display; acquisition stays with the DS2 owner."""

from bisect import bisect_left
import math

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QGridLayout, QLabel, QStackedWidget, QVBoxLayout, QWidget

from live_data_view import COLORS, plot_segments
from live_statistics import BoxSummary, box_summary


class _RoughnessChart(QWidget):
    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.setMinimumHeight(max(190, self.fontMetrics().lineSpacing() * 12))
        self.setAccessibleName("Cylinder roughness chart")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#1e1e1e"))
        if self.view.mode == "box":
            self._paint_boxes(painter)
        else:
            self._paint_trend(painter)

    def _paint_boxes(self, painter):
        view = self.view
        metrics = self.fontMetrics()
        line = metrics.lineSpacing()
        left = metrics.horizontalAdvance("Cylinder 6") + 18
        current_width = metrics.horizontalAdvance("History only") + 16
        count_width = metrics.horizontalAdvance("3 (collecting)") + 14
        chart = QRectF(left, line + 12,
                       max(1, self.width() - left - current_width - count_width - 12),
                       max(1, self.height() - line * 2 - 24))
        current_x = chart.right() + 8
        count_x = current_x + current_width
        painter.setPen(QColor("#aaaaaa"))
        painter.drawText(QRectF(left, 2, chart.width(), line), Qt.AlignLeft,
                         "Last 30 s of samples · Signed roughness / unitless")
        painter.drawText(QRectF(current_x, 2, current_width, line), Qt.AlignCenter, "Current")
        painter.drawText(QRectF(count_x, 2, count_width, line), Qt.AlignCenter, "Samples")
        for fraction in (0, .25, .5, .75, 1):
            x = chart.left() + fraction * chart.width()
            painter.setPen(QPen(QColor("#777777" if fraction == .5 else "#333333"),
                                1.5 if fraction == .5 else 1))
            painter.drawLine(QPointF(x, chart.top()), QPointF(x, chart.bottom()))
            painter.setPen(QColor("#aaaaaa"))
            text = f"{view.scale_max * (2 * fraction - 1):g}"
            width = metrics.horizontalAdvance(text) + 8
            painter.drawText(QRectF(x - width / 2, chart.bottom() + 5, width, line),
                             Qt.AlignCenter, text)
        row_height = chart.height() / 6
        half_box = min(line * .6, row_height * .25)

        def x_at(value):
            return chart.left() + view._value_fraction(value) * chart.width()

        for index, summary in enumerate(view.box_summaries):
            y = chart.top() + (index + .5) * row_height
            color = QColor(COLORS[index])
            current = view.current_values[index]
            painter.setPen(color)
            painter.drawText(QRectF(4, y - line / 2, left - 12, line),
                             Qt.AlignRight | Qt.AlignVCenter, f"Cylinder {index + 1}")
            current_text = str(current) if current is not None else "History only" if summary.count else "—"
            count_text = f"{summary.count} (collecting)" if 0 < summary.count < 4 else str(summary.count)
            painter.drawText(QRectF(current_x, y - line / 2, current_width, line),
                             Qt.AlignCenter, current_text)
            painter.drawText(QRectF(count_x, y - line / 2, count_width, line),
                             Qt.AlignCenter, count_text)
            if summary.count >= 4:
                painter.setPen(QPen(color, 1.5))
                painter.drawLine(QPointF(x_at(summary.whisker_low), y),
                                 QPointF(x_at(summary.whisker_high), y))
                for value in (summary.whisker_low, summary.whisker_high):
                    x = x_at(value)
                    painter.drawLine(QPointF(x, y - half_box * .7), QPointF(x, y + half_box * .7))
                fill = QColor(color)
                fill.setAlpha(80)
                painter.setBrush(fill)
                painter.drawRect(QRectF(x_at(summary.q1), y - half_box,
                                        x_at(summary.q3) - x_at(summary.q1), half_box * 2))
                painter.setBrush(Qt.NoBrush)
                painter.drawLine(QPointF(x_at(summary.median), y - half_box),
                                 QPointF(x_at(summary.median), y + half_box))
                for value in summary.outliers:
                    painter.drawEllipse(QPointF(x_at(value), y), 2, 2)
            elif not summary.count:
                painter.setPen(QColor("#888888"))
                painter.drawText(QRectF(chart.left(), y - line / 2, chart.width(), line),
                                 Qt.AlignCenter, "No samples")
            if current is not None:
                x = x_at(current)
                diamond = QPainterPath(QPointF(x, y - 4))
                for point in (QPointF(x + 4, y), QPointF(x, y + 4), QPointF(x - 4, y)):
                    diamond.lineTo(point)
                diamond.closeSubpath()
                painter.setPen(QPen(QColor("#1e1e1e"), 1))
                painter.setBrush(QColor("#f2f2f2"))
                painter.drawPath(diamond)
                painter.setBrush(Qt.NoBrush)

    def _paint_trend(self, painter):
        view = self.view
        metrics = self.fontMetrics()
        line = metrics.lineSpacing()
        left, right = view._chart_margins()
        chart = QRectF(left, line + 8, max(1, self.width() - left - right),
                       max(1, self.height() - line * 2 - 20))
        painter.setPen(QColor("#aaaaaa"))
        painter.drawText(QRectF(left, 2, chart.width(), line), Qt.AlignLeft,
                         "Signed roughness / unitless")
        for fraction in (0, .5, 1):
            y = chart.bottom() - fraction * chart.height()
            painter.setPen(QPen(QColor("#777777" if fraction == .5 else "#444444"),
                                1.5 if fraction == .5 else 1))
            painter.drawLine(QPointF(chart.left(), y), QPointF(chart.right(), y))
            painter.setPen(QColor("#aaaaaa"))
            painter.drawText(QRectF(0, y - line / 2, left - 8, line),
                             Qt.AlignRight | Qt.AlignVCenter,
                             f"{view.scale_max * (2 * fraction - 1):g}")
        painter.save()
        painter.setClipRect(chart.adjusted(-1, -1, 1, 1))
        if view.times:
            end = max(30., view.times[-1])
            start = max(0., end - 30.)
            span = end - start
            for index, values in enumerate(view.history):
                painter.setPen(QPen(QColor(COLORS[index]), 1.5))
                for segment in plot_segments(view.times, values, 0, len(view.times), int(chart.width())):
                    path = QPainterPath()
                    for point_index, (timestamp, value) in enumerate(segment):
                        point = QPointF(chart.left() + (timestamp - start) / span * chart.width(),
                                        chart.bottom() - view._value_fraction(value) * chart.height())
                        if point_index == 0:
                            path.moveTo(point)
                        else:
                            path.lineTo(point)
                    if len(segment) == 1:
                        painter.drawEllipse(point, 2, 2)
                    else:
                        painter.drawPath(path)
        painter.restore()
        if view.times:
            painter.setPen(QColor("#aaaaaa"))
            painter.drawText(QRectF(chart.left(), chart.bottom() + 5, chart.width(), line),
                             Qt.AlignLeft, f"{start:g} s")
            painter.drawText(QRectF(chart.left(), chart.bottom() + 5, chart.width(), line),
                             Qt.AlignRight, f"{end:g} s")
        if not view.times:
            painter.setPen(QColor("#aaaaaa"))
            painter.drawText(chart, Qt.AlignCenter, "No samples yet")


class RoughnessView(QWidget):
    """Six signed values on one expanding symmetric scale with 30 seconds of history."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mode = "box"
        self.times = []
        self.history = [[] for _ in range(6)]
        self.current_values = (None,) * 6
        self.scale_max = 1
        self.box_summaries = tuple(BoxSummary(0) for _ in range(6))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.chart = _RoughnessChart(self)
        layout.addWidget(self.chart, 1)
        self.footer = QStackedWidget()
        legend = QLabel(
            "Box: Q1–Q3 · line: median · whiskers: actual samples within 1.5×IQR\n"
            "Dots: outliers · white diamond: current value · fewer than 4 samples: collecting")
        legend.setStyleSheet("color:#aaaaaa;")
        self.footer.addWidget(legend)
        values_page = QWidget()
        self._labels = QGridLayout(values_page)
        self._labels.setHorizontalSpacing(0)
        self._labels.setVerticalSpacing(2)
        self.value_labels = []
        for index, color in enumerate(COLORS):
            name = QLabel(f"Cylinder {index + 1}")
            value = QLabel("—")
            value.setAccessibleName(f"Cylinder {index + 1} roughness")
            for row, label in enumerate((name, value)):
                label.setAlignment(Qt.AlignCenter)
                label.setStyleSheet(f"color:{color};")
                self._labels.addWidget(label, row, index)
            self._labels.setColumnStretch(index, 1)
            self.value_labels.append(value)
        self.footer.addWidget(values_page)
        layout.addWidget(self.footer)
        self._refresh()

    def _chart_margins(self):
        metrics = self.fontMetrics()
        return max(metrics.horizontalAdvance(f"{-self.scale_max:g}"),
                   metrics.horizontalAdvance(f"{-self.scale_max / 2:g}")) + 12, 12

    def _value_fraction(self, value):
        return (value + self.scale_max) / (2 * self.scale_max)

    def _refresh(self):
        self.box_summaries = tuple(box_summary(values) for values in self.history)
        left, right = self._chart_margins()
        self._labels.setContentsMargins(left, 0, right, 0)
        for label, value in zip(self.value_labels, self.current_values):
            label.setText("—" if value is None else str(value))
        descriptions = []
        for index, (value, summary) in enumerate(zip(self.current_values, self.box_summaries)):
            current = str(value) if value is not None else "unavailable; history only" if summary.count else "unavailable"
            middle = f"{summary.median:g}" if summary.median is not None else "unavailable"
            descriptions.append(
                f"Cylinder {index + 1}: current {current}; median {middle}; "
                f"{summary.count} valid samples" + ("; collecting" if summary.count < 4 else ""))
        self.chart.setAccessibleDescription("Signed roughness, unitless. " + ". ".join(descriptions))
        self.chart.update()

    def set_mode(self, mode):
        if mode not in ("box", "trend"):
            raise ValueError("roughness display mode must be box or trend")
        self.mode = mode
        self.footer.setCurrentIndex(0 if mode == "box" else 1)
        self.chart.update()

    def reset(self):
        self.times.clear()
        for values in self.history:
            values.clear()
        self.current_values = (None,) * 6
        self.scale_max = 1
        self._refresh()

    def add_sample(self, timestamp_seconds, values):
        timestamp = float(timestamp_seconds)
        values = tuple(values)
        if not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError("sample time must be finite and nonnegative")
        if len(values) != 6 or any(value is not None and type(value) is not int for value in values):
            raise ValueError("roughness samples require six integers or None")
        if self.times and timestamp <= self.times[-1]:
            return
        peak = max((abs(value) for value in values if value is not None), default=0)
        if peak > self.scale_max:
            magnitude = 10 ** math.floor(math.log10(peak))
            self.scale_max = next(step * magnitude for step in (1, 2, 5, 10)
                                  if step * magnitude >= peak)
        self.times.append(timestamp)
        for history, value in zip(self.history, values):
            history.append(value)
        first = bisect_left(self.times, timestamp - 30.)
        del self.times[:first]
        for history in self.history:
            del history[:first]
        self.current_values = values
        self._refresh()

    def set_unavailable(self):
        if self.times and any(value is not None for value in self.current_values):
            # Preserve the last valid point, then break the line before a resumed sample.
            self.times.append(self.times[-1])
            for history in self.history:
                history.append(None)
        self.current_values = (None,) * 6
        self._refresh()
