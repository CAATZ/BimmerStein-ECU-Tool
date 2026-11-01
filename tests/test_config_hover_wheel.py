"""Real Qt tooltip/wheel events; no transport or file mutations."""

import pytest
from PyQt5.QtCore import QEvent, QObject, QPoint, QPointF, Qt
from PyQt5.QtGui import QHelpEvent, QWheelEvent
from PyQt5.QtTest import QSignalSpy, QTest
from PyQt5.QtWidgets import (
    QApplication, QAbstractScrollArea, QComboBox, QScrollArea, QToolTip,
    QTableWidget, QVBoxLayout, QWidget,
)

import gui
from gui_controls import ConfigComboBox
from ms42_ms43_config_patches import features_for
from tests.test_ms42_ms43_desktop import connect, window as window


def wheel(widget, *, delta=-120, pixel=None, modifiers=Qt.NoModifier,
          phase=Qt.NoScrollPhase, inverted=False):
    point = widget.rect().center()
    event = QWheelEvent(QPointF(point), QPointF(widget.mapToGlobal(point)),
                        pixel or QPoint(), QPoint(0, delta), Qt.NoButton,
                        modifiers, phase, inverted, Qt.MouseEventSynthesizedBySystem)
    event.setTimestamp(1234)
    QApplication.sendEvent(widget, event)
    QApplication.processEvents()
    return event


def tooltip(widget, point=None):
    QToolTip.hideText()
    QTest.qWait(350)
    point = point or widget.rect().center()
    QApplication.sendEvent(widget, QHelpEvent(QEvent.ToolTip, point, widget.mapToGlobal(point)))
    QApplication.processEvents()
    return QToolTip.text() if QToolTip.isVisible() else ""


def test_resize_help_is_on_the_divider_and_never_inherited_by_its_children(window):
    splitter = gui._PanelSplitter("tooltip-test", Qt.Vertical, [100, 100])
    splitter.setParent(window)
    empty, explicit = QComboBox(), QComboBox()
    splitter.addWidget(empty)
    splitter.addWidget(explicit)
    explicit.setToolTip("The parameter's own description.")
    splitter.resize(400, 180)
    window.show()
    splitter.show()
    QApplication.processEvents()
    assert splitter.toolTip() == ""
    assert "Drag the divider" in tooltip(splitter.handle(1))
    assert tooltip(empty) == ""
    assert tooltip(explicit) == "The parameter's own description."
    QToolTip.hideText()


@pytest.mark.parametrize("family,software", (("MS42", "0110C6"), ("MS43", "430069")))
def test_all_config_rows_and_combos_show_their_parameter_help(window, family, software):
    connect(window, family)
    window.tabs.setCurrentIndex(window._config_tab_index)
    window.show()
    QApplication.processEvents()
    table = window._ms42_ms43_config_table
    for row, feature in enumerate(features_for(family, software)):
        assert feature.note
        assert table.item(row, 0).toolTip() == feature.note
        assert table.cellWidget(row, 1).toolTip() == feature.note
        assert "Drag the divider" not in feature.note
    first = table.cellWidget(0, 1)
    first.setEnabled(True)
    assert tooltip(first) == features_for(family, software)[0].note
    point = table.visualItemRect(table.item(0, 0)).center()
    assert tooltip(table.viewport(), point) == features_for(family, software)[0].note
    QToolTip.hideText()


@pytest.mark.parametrize("family", ("MS41", "MS42", "MS43"))
@pytest.mark.parametrize("focused", (False, True))
def test_closed_config_wheel_scrolls_page_without_changing_value(window, family, focused):
    if family == "MS41":
        combo = window._config_combos["VANOS"]
    else:
        connect(window, family)
        combo = window._ms42_ms43_config_combos["Electrical cooling fan"]
    assert isinstance(combo, ConfigComboBox)
    window.resize(1000, 780)
    window.tabs.setCurrentIndex(window._config_tab_index)
    window.show()
    if family == "MS41":
        window._config_scroll.setMaximumHeight(180)
    combo.setEnabled(True)
    combo.setCurrentIndex(0 if family == "MS41" else 1)
    window._config_scroll.ensureWidgetVisible(combo)
    QApplication.processEvents()
    if focused:
        combo.setFocus()
    else:
        window.btn_config_load.setFocus()
    QApplication.processEvents()
    parent = combo.parentWidget()
    while not isinstance(parent, QAbstractScrollArea):
        parent = parent.parentWidget()
    bar = parent.verticalScrollBar()
    assert bar.maximum() > 0
    bar.setValue(0)
    before = combo.currentIndex()
    changed = QSignalSpy(combo.currentIndexChanged)
    wheel(combo)
    assert combo.currentIndex() == before and len(changed) == 0
    assert bar.value() > 0


def test_no_scroll_parent_and_keyboard_selection_remain_usable(window):
    combo = ConfigComboBox(window)
    combo.addItems(["First", "Second", "Third"])
    window.show()
    combo.show()
    for delta in (-120, 120):
        wheel(combo, delta=delta)
        assert combo.currentIndex() == 0
    QTest.keyClick(combo, Qt.Key_Down)
    assert combo.currentIndex() == 1


@pytest.mark.parametrize("rows,delta", ((20, -120), (20, 120), (1, -120)))
def test_closed_combo_scrolls_outer_page_when_inner_table_cannot_scroll(window, rows, delta):
    outer = QScrollArea(window)
    outer.setWidgetResizable(True)
    page = QWidget()
    page.setMinimumHeight(1000)
    layout = QVBoxLayout(page)
    table = QTableWidget(rows, 1)
    table.setFixedHeight(180)
    combo = ConfigComboBox()
    combo.addItems(["First", "Second"])
    table.setCellWidget(rows - 1 if delta < 0 else 0, 0, combo)
    layout.addWidget(table)
    layout.addStretch()
    outer.setWidget(page)
    outer.resize(400, 240)
    window.show()
    outer.show()
    QApplication.processEvents()
    inner_bar, outer_bar = table.verticalScrollBar(), outer.verticalScrollBar()
    inner_bar.setValue(inner_bar.maximum() if delta < 0 else 0)
    outer_bar.setValue(0 if delta < 0 else 40)
    previous_inner, previous_outer = inner_bar.value(), outer_bar.value()
    wheel(combo, delta=delta)
    assert combo.currentIndex() == 0
    assert inner_bar.value() == previous_inner
    assert outer_bar.value() > previous_outer if delta < 0 else outer_bar.value() < previous_outer


def test_popup_wheel_scrolls_choices_and_click_commits_selection(window):
    combo = ConfigComboBox(window)
    combo.addItems([f"Choice {i}" for i in range(80)])
    combo.setMaxVisibleItems(5)
    combo.setStyleSheet("QComboBox { combobox-popup: 0; }")
    window.show()
    combo.show()
    combo.showPopup()
    QApplication.processEvents()
    view = combo.view()
    assert view.isVisible() and view.verticalScrollBar().maximum() > 0
    changed = QSignalSpy(combo.currentIndexChanged)
    wheel(combo)
    assert combo.currentIndex() == 0 and len(changed) == 0
    wheel(view.viewport())
    assert combo.currentIndex() == 0 and len(changed) == 0
    assert view.verticalScrollBar().value() > 0
    index = combo.model().index(30, 0)
    view.scrollTo(index)
    QApplication.processEvents()
    QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=view.visualRect(index).center())
    assert combo.currentIndex() == 30 and not view.isVisible()


def test_forwarded_wheel_preserves_trackpad_event_context(window):
    scroll = QScrollArea(window)
    page = QWidget()
    layout = QVBoxLayout(page)
    combo = ConfigComboBox()
    combo.addItems(["First", "Second"])
    layout.addWidget(combo)
    page.setMinimumHeight(1500)
    scroll.setWidget(page)
    scroll.resize(300, 200)
    window.show()
    scroll.show()
    QApplication.processEvents()
    captured = []

    class Observer(QObject):
        def eventFilter(self, widget, event):
            if event.type() == QEvent.Wheel:
                captured.append((event.pos(), event.globalPos(), event.pixelDelta(), event.angleDelta(),
                                 event.buttons(), event.modifiers(), event.phase(), event.inverted(),
                                 event.source(), event.timestamp()))
            return False

    observer = Observer(scroll)
    scroll.viewport().installEventFilter(observer)
    sent = wheel(combo, pixel=QPoint(0, -15), modifiers=Qt.ControlModifier,
                 phase=Qt.ScrollUpdate, inverted=True)
    assert len(captured) == 1
    assert captured[0] == (scroll.viewport().mapFromGlobal(sent.globalPos()), sent.globalPos(),
                           sent.pixelDelta(), sent.angleDelta(), sent.buttons(), sent.modifiers(),
                           sent.phase(), sent.inverted(), sent.source(), sent.timestamp())
    assert combo.currentIndex() == 0
