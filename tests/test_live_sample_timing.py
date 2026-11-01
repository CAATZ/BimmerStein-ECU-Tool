"""Graph and CSV timestamps describe the same completed acquisition cycles."""

import csv
import datetime
from types import SimpleNamespace

import pytest

import live_data


class _IdleThread:
    def __init__(self, **_kwargs):
        pass

    def start(self):
        pass

    def is_alive(self):
        return False

    def join(self):
        pass


@pytest.mark.parametrize("logging", [True, False])
def test_completed_sample_origin_spacing_and_wall_clock_changes(tmp_path, monkeypatch, logging):
    clock = {"monotonic": 100., "wall": datetime.datetime(2026, 10, 3, 10, 0)}
    monkeypatch.setattr(live_data.time, "monotonic", lambda: clock["monotonic"])
    monkeypatch.setattr(live_data.time, "time", lambda: clock["wall"].timestamp())
    monkeypatch.setattr(live_data, "datetime", SimpleNamespace(
        datetime=SimpleNamespace(now=lambda: clock["wall"])))
    monkeypatch.setattr(live_data.threading, "Thread", _IdleThread)
    poller = live_data.LiveDataPoller(ecu_id="1429861")
    path = tmp_path / "samples.csv"
    poller.start(str(path) if logging else None)
    clock["monotonic"] = 102.  # Profile/setup delay precedes CSV opening.
    poller._ensure_csv()
    early_row = poller._csv_row_base()
    assert "Time" not in early_row and "Datetime" not in early_row
    rows = []
    for completed_at, wall in (
            (102.6, datetime.datetime(2026, 10, 3, 10, 0, 2, 600000)),
            (103.1, datetime.datetime(2026, 10, 2, 9, 0)),  # Wall clock moves backward.
            (109.35, datetime.datetime(2026, 10, 4, 11, 0))):
        clock.update(monotonic=completed_at, wall=wall)
        row = early_row if not rows else poller._csv_row_base()
        row["Engine RPM"] = "800"
        poller._write_csv_row(row)
        rows.append(dict(row))
    samples = poller.completed_samples_since(0)[4]
    assert [sample[1] for sample in samples] == [0., .5, 6.75]
    assert [float(row["Time"]) for row in rows] == [sample[1] for sample in samples]
    assert rows[0]["Datetime"] == "2026-10-03 10:00:02.600"
    assert rows[1]["Datetime"] == "2026-10-02 09:00:00.000"
    assert poller._sample_started == 100.  # Preserve the operation clock.
    assert poller.sample_rate == 0.  # Earlier samples are outside the rate window.
    poller.stop()
    if logging:
        with path.open(encoding="utf-8", newline="") as stream:
            saved = list(csv.DictReader(stream))
        assert [(row["Time"], row["Datetime"]) for row in saved] == [
            (row["Time"], row["Datetime"]) for row in rows]
    else:
        assert not path.exists() and poller.csv_rows == 0


def test_completion_timestamp_precedes_disk_write_and_restart_resets_origin(monkeypatch):
    clock = [10.]
    monkeypatch.setattr(live_data.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(live_data.threading, "Thread", _IdleThread)
    poller = live_data.LiveDataPoller(ecu_id="1429861")
    poller.start()

    class SlowWriter:
        def writerow(self, row):
            assert "Time" in row and "Datetime" in row
            clock[0] += 5

    poller._csv_writer = SlowWriter()
    poller._csv_file = SimpleNamespace(flush=lambda: None, close=lambda: None)
    clock[0] = 12.6
    poller._write_csv_row({})
    assert poller.completed_samples_since(0)[4][0][1] == 0.
    clock[0] = 18.1
    poller._write_csv_row({})
    assert poller.completed_samples_since(0)[4][1][1] == 5.5
    poller.stop()
    clock[0] = 30.
    poller.start()
    clock[0] = 40.
    poller._write_csv_row({})
    assert poller.completed_samples_since(0)[4][0][:2] == (1, 0.)
    assert poller._sample_started == 30.
    assert poller.sample_rate == 0.
    poller.stop()


def test_sample_rate_uses_precise_recent_completions_and_decays_after_stop(monkeypatch):
    clock = [100.]
    monkeypatch.setattr(live_data.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(live_data.threading, "Thread", _IdleThread)
    poller = live_data.LiveDataPoller(ecu_id="1429861")
    poller.start()
    clock[0] = 120.  # Setup time does not dilute the acquisition rate.
    assert poller.sample_rate == 0.
    poller._write_csv_row({})
    assert poller.sample_rate == 0.
    clock[0] = 120.123456
    poller._write_csv_row({})
    assert poller.sample_rate == pytest.approx(1 / .123456)
    assert poller.completed_samples_since(0)[4][-1][1] == .123

    # Old startup samples do not affect the recent two-per-second cadence.
    for completed_at in (124., 124.5, 125., 125.5):
        clock[0] = completed_at
        poller._write_csv_row({})
    assert poller.sample_rate == pytest.approx(2.)
    poller.stop()
    clock[0] = 126.
    assert poller.sample_rate == pytest.approx(1.5)
    clock[0] = 128.6
    assert poller.sample_rate == 0.

    poller.start()
    assert poller.sample_rate == 0.
    poller._write_csv_row({})
    assert poller.sample_rate == 0.
    poller.stop()
