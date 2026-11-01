"""Shared Qt controls for configuration editing."""

from PyQt5.QtCore import QPointF
from PyQt5.QtGui import QWheelEvent
from PyQt5.QtWidgets import QApplication, QAbstractScrollArea, QComboBox


class ConfigComboBox(QComboBox):
    """Wheel movement scrolls the surrounding page or popup, never edits a value."""

    def wheelEvent(self, event):
        targets = []
        if self.view().isVisible():
            targets.append(self.view())
        else:
            parent = self.parentWidget()
            while parent is not None:
                if isinstance(parent, QAbstractScrollArea):
                    targets.append(parent)
                parent = parent.parentWidget()
        for area in targets:
            target = area.viewport()
            forwarded = QWheelEvent(
                QPointF(target.mapFromGlobal(event.globalPos())), event.globalPosF(),
                event.pixelDelta(), event.angleDelta(), event.buttons(), event.modifiers(),
                event.phase(), event.inverted(), event.source())
            forwarded.setTimestamp(event.timestamp())
            QApplication.sendEvent(target, forwarded)
            if forwarded.isAccepted():
                break
        event.accept()
