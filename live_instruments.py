"""Native Qt instrument cards; the owning view supplies cached sample statistics."""

import math

from PyQt5.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QComboBox, QWidget


_MODES = {"gauge": "Gauge", "digital": "Digital", "box": "Box plot"}


def _number(value):
    return "—" if value is None else f"{value:g}"


class LiveInstruments(QWidget):
    """Equal-footprint cards with persistent, keyboard-accessible display controls."""

    # Saved levels 0–2 keep their existing dimensions.
    MIN_SIZE = -1
    MAX_SIZE = 2
    size_changed = pyqtSignal(int)

    def __init__(self, view, colors):
        super().__init__()
        self.view = view
        self.colors = tuple(colors)
        self.size_level = 0
        self.presentation_controls = {}
        self.card_rects = {}
        self.setAccessibleName("Live Data instruments")
        self.update_geometry()

    def _dimensions(self, level=None):
        line = self.fontMetrics().lineSpacing()
        level = self.size_level if level is None else level
        width, height = ((12, 11), (14, 14), (18, 17), (23, 20))[level + 1]
        return line * width, line * height

    def set_size(self, level):
        if level not in range(self.MIN_SIZE, self.MAX_SIZE + 1):
            raise ValueError("Instrument size must be -1, 0, 1 or 2")
        if level != self.size_level:
            self.size_level = level
            self.update_geometry()
            self.size_changed.emit(level)

    def fit_panel(self):
        viewport = self.parentWidget() or self
        count = len(self.view.selected_channels())
        for level in range(self.MAX_SIZE, self.MIN_SIZE - 1, -1):
            width, height = self._dimensions(level)
            columns = max(1, viewport.width() // width)
            if math.ceil(count / columns) * height <= viewport.height() or level == self.MIN_SIZE:
                self.set_size(level)
                return

    def resizeEvent(self, event):
        self.update_geometry()
        super().resizeEvent(event)

    def _presentation_changed(self, name):
        self.view.set_presentation(name, self.presentation_controls[name].currentData())

    def update_geometry(self):
        names = self.view.selected_channels()
        for name in set(self.presentation_controls) - set(names):
            control = self.presentation_controls.pop(name)
            control.hide()
            control.deleteLater()
        for name in names:
            control = self.presentation_controls.get(name)
            if control is None:
                control = QComboBox(self)
                control.setAccessibleName(f"{name}: Display as")
                control.setToolTip(
                    f"{name}\n\nChoose this channel's display. Min/max cover capture or reset at the latest sample;\n"
                    "in history they cover retained samples through the cursor, after the last reset.\n"
                    "Box plot: valid samples from the last 30 seconds through the cursor. "
                    "Box = Q1–Q3; line = median; whiskers = samples within 1.5×IQR; "
                    "dots = outliers; white diamond = current value.")
                control.currentIndexChanged.connect(
                    lambda _index, channel=name: self._presentation_changed(channel))
                self.presentation_controls[name] = control
            options = tuple(self.view.presentation_options(name))
            control.blockSignals(True)
            if tuple(control.itemData(i) for i in range(control.count())) != options:
                control.clear()
                for mode in options:
                    control.addItem(_MODES[mode], mode)
            control.setCurrentIndex(control.findData(self.view.presentation_for(name)))
            control.blockSignals(False)
            control.show()

        minimum_width, row_height = self._dimensions()
        self.setMinimumWidth(self._dimensions(self.MIN_SIZE)[0])
        columns = max(1, self.width() // minimum_width)
        cell_width = self.width() / columns
        self.setMinimumHeight(max(row_height, math.ceil(len(names) / columns) * row_height))
        pad = max(6, self.fontMetrics().lineSpacing() / 2)
        self.card_rects = {}
        for index, name in enumerate(names):
            rect = QRectF((index % columns) * cell_width + pad / 2,
                          (index // columns) * row_height + pad / 2,
                          cell_width - pad, row_height - pad)
            self.card_rects[name] = rect
            control = self.presentation_controls[name]
            hint = control.sizeHint()
            control.setGeometry(round(rect.right() - hint.width() - pad),
                                round(rect.top() + pad), hint.width(), hint.height())
        self.update()

    @staticmethod
    def _fraction(value, bounds):
        low, high = bounds
        return max(0., min(1., (value - low) / (high - low))) if high > low else .5

    def _draw_value(self, painter, rect, snapshot, color, *, digital=False):
        font = QFont(self.font())
        font.setFamily("Consolas")
        multiplier = ((2.0, 2.5, 3.1, 3.7) if digital else (1.3, 1.5, 1.8, 2.1))[self.size_level + 1]
        font.setPointSizeF(self.font().pointSizeF() * multiplier)
        text = snapshot["text"] if digital else f"{snapshot['text']} {snapshot['unit']}".strip()
        while (font.pointSizeF() > self.font().pointSizeF()
               and QFontMetricsF(font).horizontalAdvance(text) > rect.width() - 8):
            font.setPointSizeF(max(self.font().pointSizeF(), font.pointSizeF() - 1))
        line = self.fontMetrics().lineSpacing()
        metrics = QFontMetricsF(font)
        if not digital:
            painter.setFont(font)
            painter.setPen(color)
            painter.drawText(rect, Qt.AlignCenter | Qt.TextSingleLine,
                             metrics.elidedText(text, Qt.ElideRight, rect.width()))
            return
        text_height = min(rect.height() - line, metrics.boundingRect(
            QRectF(0, 0, rect.width(), rect.height()), Qt.TextWordWrap, text).height())
        top = rect.center().y() - (text_height + line) / 2
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(QRectF(rect.left(), top, rect.width(), text_height),
                         Qt.AlignCenter | Qt.TextWordWrap, text)
        painter.setFont(self.font())
        painter.setPen(QColor("#aaaaaa"))
        painter.drawText(QRectF(rect.left(), top + text_height, rect.width(), line),
                         Qt.AlignCenter, snapshot["unit"])

    def _draw_gauge(self, painter, rect, snapshot, color):
        compact = self.size_level == self.MIN_SIZE
        # A 240-degree arc uses only three quarters of its circle's height.
        diameter = min(rect.width() * .7, rect.height() / (.8 if compact else 1))
        dial = QRectF(rect.center().x() - diameter / 2,
                      rect.center().y() - diameter * (.375 if compact else .5),
                      diameter, diameter)
        line = self.fontMetrics().lineSpacing()
        painter.setPen(QPen(QColor("#555555"), line / 3))
        painter.drawArc(dial, -30 * 16, 240 * 16)
        bounds = snapshot["gauge_bounds"]
        low, high = bounds
        for value in (low, high, *([0] if low < 0 < high else [])):
            angle = math.radians(210 - 240 * self._fraction(value, bounds))
            direction = QPointF(math.cos(angle), -math.sin(angle))
            painter.setPen(QPen(QColor("#aaaaaa"), 2 if value == 0 else 1))
            painter.drawLine(dial.center() + direction * diameter * .42,
                             dial.center() + direction * diameter * .5)
        if snapshot["current"] is not None:
            angle = math.radians(210 - 240 * self._fraction(snapshot["current"], bounds))
            painter.setPen(QPen(color, line / 5))
            painter.drawLine(dial.center(), dial.center() + QPointF(
                math.cos(angle), -math.sin(angle)) * diameter * .42)
        painter.setFont(self.font())
        painter.setPen(QColor("#aaaaaa"))
        labels = QRectF(rect.left(), rect.bottom() - line, rect.width(), line)
        painter.drawText(labels, Qt.AlignLeft, _number(low))
        painter.drawText(labels, Qt.AlignRight, _number(high))
        if low < 0 < high:
            angle = math.radians(210 - 240 * self._fraction(0, bounds))
            point = (dial.center()
                     + QPointF(math.cos(angle), -math.sin(angle)) * diameter * .3
                     + QPointF(math.sin(angle), math.cos(angle)) * line * .8)
            painter.drawText(QRectF(point.x() - line, point.y() - line / 2, line * 2, line),
                             Qt.AlignCenter, "0")
        if snapshot["gauge_auto"]:
            painter.drawText(labels, Qt.AlignCenter, "Auto range")

    def _draw_box(self, painter, rect, snapshot, color):
        summary = snapshot["summary"]
        line = self.fontMetrics().lineSpacing()
        bounds = snapshot["bounds"]
        plot = rect.adjusted(9, line, -9, -line)
        center = plot.center().y()
        half = max(3, min(line * .6, plot.height() / 4))

        def x_at(value):
            return plot.left() + self._fraction(value, bounds) * plot.width()

        painter.setFont(self.font())
        painter.setPen(QColor("#aaaaaa"))
        status = "Collecting" if 0 < summary.count < 4 else "Last 30 s"
        if snapshot["current"] is None and summary.count:
            status += " · history only"
        painter.drawText(QRectF(rect.left(), rect.top(), rect.width(), line),
                         Qt.AlignCenter, f"{status} · n={summary.count}")
        painter.setPen(QPen(QColor("#444444"), 1))
        painter.drawLine(QPointF(plot.left(), center), QPointF(plot.right(), center))
        if bounds[0] <= 0 <= bounds[1]:
            painter.setPen(QPen(QColor("#999999"), 1.5))
            painter.drawLine(QPointF(x_at(0), plot.top()), QPointF(x_at(0), plot.bottom()))
        if summary.count >= 4:
            painter.setPen(QPen(color, 1.5))
            painter.drawLine(QPointF(x_at(summary.whisker_low), center),
                             QPointF(x_at(summary.whisker_high), center))
            for value in (summary.whisker_low, summary.whisker_high):
                painter.drawLine(QPointF(x_at(value), center - half * .7),
                                 QPointF(x_at(value), center + half * .7))
            fill = QColor(color)
            fill.setAlpha(65)
            painter.setBrush(fill)
            painter.drawRect(QRectF(x_at(summary.q1), center - half,
                                    x_at(summary.q3) - x_at(summary.q1), half * 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawLine(QPointF(x_at(summary.median), center - half),
                             QPointF(x_at(summary.median), center + half))
            for value in summary.outliers:
                painter.drawEllipse(QPointF(x_at(value), center), 2, 2)
        if snapshot["current"] is not None:
            x = x_at(snapshot["current"])
            diamond = QPainterPath(QPointF(x, center - 4))
            for point in (QPointF(x + 4, center), QPointF(x, center + 4), QPointF(x - 4, center)):
                diamond.lineTo(point)
            diamond.closeSubpath()
            painter.setPen(QPen(QColor("#242424"), 1))
            painter.setBrush(QColor("#f2f2f2"))
            painter.drawPath(diamond)
            painter.setBrush(Qt.NoBrush)
        painter.setPen(QColor("#aaaaaa"))
        labels = QRectF(rect.left(), rect.bottom() - line, rect.width(), line)
        painter.drawText(labels, Qt.AlignLeft, _number(bounds[0]))
        painter.drawText(labels, Qt.AlignRight, _number(bounds[1]))
        if bounds[0] < 0 < bounds[1]:
            painter.drawText(QRectF(x_at(0) - line, labels.top(), line * 2, line),
                             Qt.AlignCenter, "0")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        dirty = QRectF(event.rect())
        line = self.fontMetrics().lineSpacing()
        pad = max(6, line / 2)
        color_indices = {name: index for index, name in enumerate(self.view.series)}
        for name, rect in self.card_rects.items():
            if not dirty.intersects(rect):
                continue
            snapshot = self.view.card_snapshot(name)
            color = QColor(self.colors[color_indices[name] % len(self.colors)])
            painter.setBrush(QColor("#242424"))
            painter.setPen(QColor("#444444"))
            painter.drawRoundedRect(rect, 5, 5)
            control = self.presentation_controls[name]
            painter.setFont(self.font())
            painter.setPen(color)
            header = QRectF(rect.left() + pad, rect.top() + pad,
                            control.x() - rect.left() - pad * 2, control.height())
            painter.drawText(header, Qt.AlignLeft | Qt.AlignVCenter | Qt.TextSingleLine,
                             self.fontMetrics().elidedText(name, Qt.ElideRight, int(header.width())))
            footer = QRectF(rect.left() + pad, rect.bottom() - pad - line,
                            rect.width() - pad * 2, line)
            body = QRectF(rect.left() + pad, header.bottom() + pad,
                          rect.width() - pad * 2, footer.top() - header.bottom() - pad * 2)
            mode = self.view.presentation_for(name)
            if mode == "digital":
                self._draw_value(painter, body, snapshot, color, digital=True)
            else:
                value_height = line * (1.8, 2, 2.3, 2.6)[self.size_level + 1]
                plot = body.adjusted(0, 0, 0, -value_height)
                if mode == "box":
                    self._draw_box(painter, plot, snapshot, color)
                else:
                    self._draw_gauge(painter, plot, snapshot, color)
                self._draw_value(painter, QRectF(body.left(), plot.bottom(), body.width(), value_height),
                                 snapshot, color)
            painter.setFont(self.font())
            painter.setPen(QColor("#999999"))
            painter.drawLine(QPointF(footer.left(), footer.top() - 3),
                             QPointF(footer.right(), footer.top() - 3))
            for label, key, side in (("Min", "min", 0), ("Max", "max", 1)):
                cell = QRectF(footer.left() + side * footer.width() / 2, footer.top(),
                              footer.width() / 2, footer.height())
                painter.drawText(cell, Qt.AlignCenter | Qt.TextSingleLine,
                                 self.fontMetrics().elidedText(
                                     f"{label} {_number(snapshot[key])}", Qt.ElideRight, int(cell.width())))
            control.setAccessibleDescription(
                f"Current {snapshot['text']} {snapshot['unit']}; "
                f"minimum {_number(snapshot['min'])}, maximum {_number(snapshot['max'])} "
                "since capture/reset at latest, or retained history through the cursor after reset.")
