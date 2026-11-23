"""Shared Qt controls for configuration editing."""

from PyQt5.QtCore import QPointF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QPainter, QWheelEvent
from PyQt5.QtWidgets import QApplication, QAbstractScrollArea, QComboBox, QLabel, QSizePolicy, QSpinBox


class ElidedLabel(QLabel):
    """Single-line text that can shrink without losing its full value."""
    textChanged = pyqtSignal()

    def __init__(self, text="", *, elide=Qt.ElideRight):
        super().__init__(text)
        self._elide = elide
        self.setTextFormat(Qt.PlainText)
        self.setToolTip(text)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

    def setText(self, text):
        super().setText(text)
        self.setToolTip(text)
        self.textChanged.emit()

    def sizeHint(self):
        return QSize(0, self.fontMetrics().height())

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(self.palette().windowText().color())
        painter.drawText(
            self.contentsRect(), self.alignment(),
            self.fontMetrics().elidedText(
                " ".join(self.text().splitlines()), self._elide, self.contentsRect().width()))


def fit_window_to_screen(window, width, height):
    """Leave room for the window frame on the owner's screen."""
    owner = window.parentWidget() or window
    area = owner.screen().availableGeometry()
    window.resize(min(width, area.width() - 40), min(height, area.height() - 70))


def scroll_editor_wheel(widget, event, popup=None):
    area = popup or widget.parentWidget()
    while area is not None:
        if isinstance(area, QAbstractScrollArea):
            target = area.viewport()
            forwarded = QWheelEvent(
                QPointF(target.mapFromGlobal(event.globalPos())), event.globalPosF(),
                event.pixelDelta(), event.angleDelta(), event.buttons(), event.modifiers(),
                event.phase(), event.inverted(), event.source())
            forwarded.setTimestamp(event.timestamp())
            QApplication.sendEvent(target, forwarded)
            if forwarded.isAccepted() or popup is not None:
                break
        area = area.parentWidget()
    event.accept()


class ConfigComboBox(QComboBox):
    """Wheel movement scrolls the surrounding page or popup, never edits a value."""

    def wheelEvent(self, event):
        scroll_editor_wheel(self, event, self.view() if self.view().isVisible() else None)


class ConfigSpinBox(QSpinBox):
    def wheelEvent(self, event):
        scroll_editor_wheel(self, event)
