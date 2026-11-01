"""Instrument controls and painting with cached, offline sample snapshots."""

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

import pytest
from PyQt5.QtCore import QRectF
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QApplication, QScrollArea

from live_instruments import LiveInstruments


@pytest.fixture
def app():
    application = QApplication.instance() or QApplication([])
    application.setStyle("Fusion")
    application.setFont(QFont("Segoe UI", 9))
    return application


class _View:
    def __init__(self, names):
        self.names = names
        self.series = {name: {} for name in names}
        self.modes = dict.fromkeys(names, "digital")
        self.requests = []
        self.snapshot = {
            "current": -1., "text": "-1", "unit": "raw", "min": -20., "max": 40.,
            "bounds": (-50., 50.), "gauge_bounds": (-50., 50.), "gauge_auto": True,
            "summary": SimpleNamespace(count=6, q1=-5., median=0., q3=10.,
                                       whisker_low=-20., whisker_high=20., outliers=(40.,)),
        }

    def selected_channels(self):
        return self.names

    def presentation_options(self, name):
        return ("digital",) if name == "Status" else ("gauge", "digital", "box")

    def presentation_for(self, name):
        return self.modes[name]

    def set_presentation(self, name, mode):
        self.modes[name] = mode

    def card_snapshot(self, name):
        self.requests.append(name)
        return self.snapshot


def test_instruments_keep_controls_and_card_geometry_when_presentation_changes(app):
    view = _View(["Cylinder 1 Roughness", "Engine RPM", "Status"])
    widget = LiveInstruments(view, ("#70c7ec", "#efbd65", "#93d58e"))
    try:
        widget.resize(900, 400)
        widget.show()
        app.processEvents()
        control = widget.presentation_controls[view.names[0]]
        original = widget.card_rects.copy()
        assert control.accessibleName() == "Cylinder 1 Roughness: Display as"
        assert widget.presentation_controls["Status"].count() == 1
        for mode in ("gauge", "box", "digital"):
            control.setCurrentIndex(control.findData(mode))
            widget.update_geometry()
            assert widget.presentation_controls[view.names[0]] is control
            assert view.modes[view.names[0]] == mode
            assert widget.card_rects == original
            assert not widget.grab().isNull()
        assert widget._fraction(-50, (-50, 50)) == 0
        assert widget._fraction(0, (-50, 50)) == .5
        assert widget._fraction(50, (-50, 50)) == 1
        assert "minimum -20" in control.accessibleDescription()
        view.names = view.names[1:]
        widget.update_geometry()
        assert control.isHidden()
    finally:
        widget.close()


def test_instruments_fit_largest_readable_size_and_preserve_signed_missing_states(app, monkeypatch):
    view = _View([f"Cylinder {i + 1} Roughness" for i in range(6)])
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    widget = LiveInstruments(view, ("#70c7ec", "#efbd65"))
    dial_sizes = []
    draw_gauge = widget._draw_gauge

    def record_gauge(painter, rect, snapshot, color):
        dial_sizes.append((widget.size_level, min(rect.width() * .7, rect.height())))
        draw_gauge(painter, rect, snapshot, color)

    monkeypatch.setattr(widget, "_draw_gauge", record_gauge)
    scroll.setWidget(widget)
    scroll.resize(760, 500)
    scroll.show()
    try:
        app.processEvents()
        widget.fit_panel()
        app.processEvents()
        level = widget.size_level
        assert level in (-1, 0, 1, 2)
        if level < 2:
            width, height = widget._dimensions(level + 1)
            columns = max(1, scroll.viewport().width() // width)
            assert ((len(view.names) + columns - 1) // columns) * height > scroll.viewport().height()
        for size in (-1, 0, 1, 2):
            widget.set_size(size)
            app.processEvents()
            for name, rect in widget.card_rects.items():
                assert rect.contains(QRectF(widget.presentation_controls[name].geometry()))
            for current in (0., -1., None):
                view.snapshot.update(current=current, text="—" if current is None else f"{current:g}")
                for mode in ("gauge", "box", "digital"):
                    view.modes = dict.fromkeys(view.names, mode)
                    widget.update_geometry()
                    assert not widget.grab().isNull()
        # Extra card controls must not squeeze the dial back to a tiny icon.
        assert dial_sizes
        for level, diameter in dial_sizes:
            assert diameter >= widget.fontMetrics().lineSpacing() * (4 if level == -1 else 6)
    finally:
        scroll.close()


def test_extra_small_cards_fit_two_rows_in_a_short_panel(app):
    view = _View([f"Cylinder {i + 1} Roughness" for i in range(6)])
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    widget = LiveInstruments(view, ("#70c7ec",))
    scroll.setWidget(widget)
    scroll.resize(760, 355)
    scroll.show()
    try:
        app.processEvents()
        assert scroll.verticalScrollBar().maximum() > 0
        widget.fit_panel()
        app.processEvents()
        assert widget.size_level == -1
        assert scroll.verticalScrollBar().maximum() == 0
        assert scroll.horizontalScrollBar().maximum() == 0
        for mode in ("gauge", "digital", "box"):
            view.modes = dict.fromkeys(view.names, mode)
            widget.update_geometry()
            assert not widget.grab().isNull()
            assert len({rect.y() for rect in widget.card_rects.values()}) == 2
    finally:
        scroll.close()


def test_instruments_only_request_visible_cached_snapshots_but_export_every_card(app):
    view = _View([f"Channel {i + 1}" for i in range(77)])
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    widget = LiveInstruments(view, ("#70c7ec",))
    scroll.setWidget(widget)
    scroll.resize(700, 460)
    scroll.show()
    try:
        app.processEvents()
        view.requests.clear()
        widget.repaint()
        assert "Channel 1" in view.requests and "Channel 77" not in view.requests
        assert len(set(view.requests)) < 20
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        app.processEvents()
        view.requests.clear()
        widget.repaint()
        assert "Channel 77" in view.requests and "Channel 1" not in view.requests
        assert len(set(view.requests)) < 20
        view.requests.clear()
        assert not widget.grab().isNull()
        assert set(view.requests) == set(view.names)
    finally:
        scroll.close()
