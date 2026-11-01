import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt5")
from PyQt5.QtWidgets import QApplication

from live_data_view import plot_segments
from roughness_view import RoughnessView, box_summary


def test_box_summary_inclusive_quartiles_actual_whiskers_and_outliers():
    summary = box_summary((0, 1, 2, 3, 4, 5, 100, None))
    assert summary.count == 7
    assert (summary.q1, summary.median, summary.q3) == (1.5, 3, 4.5)
    assert (summary.whisker_low, summary.whisker_high) == (0, 5)
    assert summary.outliers == (100,)
    signed = box_summary((-100, -5, -4, -3, -2, -1, 0, None))
    assert (signed.q1, signed.median, signed.q3) == (-4.5, -3, -1.5)
    assert (signed.whisker_low, signed.whisker_high) == (-5, 0)
    assert signed.outliers == (-100,)
    zero = box_summary((0, 0, 0, 0, None))
    assert zero.count == 4
    assert (zero.q1, zero.median, zero.q3, zero.whisker_low, zero.whisker_high) == (0, 0, 0, 0, 0)
    assert not zero.outliers
    for values, count, middle in (((None,), 0, None), ((0, None), 1, 0), ((1, 2, 9), 3, 2)):
        collecting = box_summary(values)
        assert collecting.count == count and collecting.median == middle
        assert collecting.q1 is None and collecting.q3 is None
        assert collecting.whisker_low is None and collecting.whisker_high is None


def test_roughness_scale_values_history_gaps_and_reset():
    app = QApplication.instance() or QApplication([])
    view = RoughnessView()
    try:
        assert view.mode == "box" and view.scale_max == 1
        view.add_sample(0, (0, -2, 3, None, -5, 6))
        assert [label.text() for label in view.value_labels] == ["0", "-2", "3", "—", "-5", "6"]
        assert view.scale_max == 10
        assert [view._value_fraction(value) for value in (-10, -5, 0, 5, 10)] == [0, .25, .5, .75, 1]
        view.add_sample(1, (1,) * 6)
        assert view.scale_max == 10
        view.set_unavailable()
        assert all(label.text() == "—" for label in view.value_labels)
        assert view.box_summaries[0].count == 2
        assert "history only" in view.chart.accessibleDescription()
        view.add_sample(2, (2,) * 6)
        segments = list(plot_segments(view.times, view.history[0], 0, len(view.times), 100))
        assert segments == [[(0., 0), (1., 1)], [(2., 2)]]
        view.add_sample(32, (-21,) * 6)
        assert view.times == [2., 32.]
        assert view.history[0] == [2, -21]
        assert view.box_summaries[0].count == 2
        assert view.box_summaries[0].median == -9.5
        assert view.scale_max == 50
        assert view._value_fraction(-21) + view._value_fraction(21) == pytest.approx(1)
        view.set_mode("trend")
        view.reset()
        assert view.mode == "trend"
        assert not view.times and all(not values for values in view.history)
        assert view.scale_max == 1
        assert all(summary.count == 0 for summary in view.box_summaries)
        assert all(label.text() == "—" for label in view.value_labels)
        app.processEvents()
    finally:
        view.close()


def test_roughness_modes_paint_with_aligned_labels_and_missing_samples():
    app = QApplication.instance() or QApplication([])
    view = RoughnessView()
    try:
        view.resize(780, 260)
        view.show()
        for mode, sample in (("box", (0, -1, None, -5, 10, 21)),
                             ("trend", (None,) * 6),
                             ("trend", (3,) * 6)):
            view.set_mode(mode)
            view.add_sample(len(view.times), sample)
            app.processEvents()
            assert not view.grab().isNull()
        rects = [label.geometry() for label in view.value_labels]
        assert len({rect.top() for rect in rects}) == 1
        assert max(rect.width() for rect in rects) - min(rect.width() for rect in rects) <= 1
        assert all(view.rect().contains(rect) for rect in rects)
        with pytest.raises(ValueError):
            view.add_sample(4, (1, 2))
        for timestamp in range(4, 11):
            view.add_sample(timestamp, (0, -1, -3, 8, 13, -32768))
        view.set_mode("box")
        app.processEvents()
        box_geometry = view.chart.geometry()
        assert not view.grab().isNull()
        assert all(not label.isVisible() for label in view.value_labels)
        assert view.box_summaries[0].count >= 4
        view.set_mode("trend")
        app.processEvents()
        assert view.chart.geometry() == box_geometry
        assert all(label.isVisible() for label in view.value_labels)
    finally:
        view.close()
