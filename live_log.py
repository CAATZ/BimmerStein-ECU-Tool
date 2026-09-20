"""Shared, bounded Live Data CSV reader for the desktop viewers."""

import csv
import io
import math

from live_data import display_rows, live_display_spec


MAX_LOG_BYTES = 16 * 1024 * 1024


def sample_value(raw):
    """Preserve text/missing samples; only finite numbers and known states plot."""
    raw = str(raw).strip() if raw is not None else ""
    lowered = raw.casefold()
    if lowered in {"active", "on", "enabled", "yes", "true"}:
        return 1.0, raw
    if lowered in {"inactive", "off", "disabled", "no", "false"}:
        return 0.0, raw
    try:
        value = float(raw)
        if math.isfinite(value):
            return value, None
    except ValueError:
        pass
    return None, raw or None


def display_specs(channels, definition_path=None):
    result = {}
    for name, unit in channels:
        spec = live_display_spec(name, unit, definition_path)
        if spec["kind"] != "value":
            result[name] = {
                key: spec[key] for key in ("kind", "minimum", "maximum", "step")
                if key in spec
            }
    return result


def parse_log(payload, definition_path=None):
    """Read canonical Time[,Datetime],channel... CSV without inventing units."""
    data = bytes(payload)
    if not data or len(data) > MAX_LOG_BYTES:
        raise ValueError("Live Data log must be between 1 byte and 16 MiB")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Live Data log must be UTF-8 CSV") from error
    rows = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(rows)
    except StopIteration as error:
        raise ValueError("Live Data log is empty") from error
    except csv.Error as error:
        raise ValueError(f"Invalid Live Data CSV: {error}") from error
    if not header or header[0] != "Time":
        raise ValueError("Live Data log must start with a Time column")
    channel_start = 2 if len(header) > 1 and header[1] == "Datetime" else 1
    channels = header[channel_start:]
    if not channels or len(channels) > 128:
        raise ValueError("Live Data log must contain 1 to 128 parameters")
    if any(not name.strip() for name in channels) or len(set(channels)) != len(channels):
        raise ValueError("Live Data log contains blank or duplicate parameter names")

    times = []
    series = [{"name": name, "values": [], "labels": []} for name in channels]
    try:
        for line_number, row in enumerate(rows, start=2):
            if not row or all(not value.strip() for value in row):
                continue
            if len(row) != len(header):
                raise ValueError(f"Live Data row {line_number} has the wrong column count")
            try:
                timestamp = float(row[0])
            except ValueError as error:
                raise ValueError(f"Live Data row {line_number} has an invalid Time") from error
            if not math.isfinite(timestamp) or timestamp < 0:
                raise ValueError(f"Live Data row {line_number} has an invalid Time")
            if times and timestamp < times[-1]:
                raise ValueError(f"Live Data row {line_number} moves backward in time")
            times.append(timestamp)
            if len(times) > 100_000:
                raise ValueError("Live Data log exceeds 100,000 rows")
            if len(times) * len(channels) > 2_000_000:
                raise ValueError("Live Data log exceeds 2,000,000 parameter samples")
            for item, raw in zip(series, row[channel_start:]):
                value, label = sample_value(raw)
                item["values"].append(value)
                item["labels"].append(label)
    except csv.Error as error:
        raise ValueError(f"Invalid Live Data CSV: {error}") from error
    if not times:
        raise ValueError("Live Data log contains no samples")
    units = dict(display_rows(definition_path))
    for item in series:
        item["unit"] = units.get(item["name"], "")
    return {
        "times": times,
        "series": series,
        "display_specs": display_specs(
            ((name, units.get(name, "")) for name in channels), definition_path),
    }
