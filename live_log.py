"""Bounded Live Data CSV reader for saved recordings."""

import csv
import io
import math
from functools import lru_cache

from live_data import display_rows, live_display_spec


_PAGE_VALUES = 64_000
_PAGE_ROWS = 4_000
_SUPPLY_NAMES = ("Ignition Supply Voltage", "Ignition Supply Voltage (KL15)",
                 "Battery Voltage (after main relay)")


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


def display_specs(channels, definition_path=None, family=None):
    result = {}
    ms41_names = {name for name, _unit in display_rows()} if definition_path is None and family is None else set()
    family_names = set()
    if family in {"MS42", "MS43"} and definition_path is None:
        from ms42_ms43_live import display_rows as family_rows
        family_names = {name for name, _unit in family_rows(family)}
    for name, unit in channels:
        if name in family_names or (definition_path is None and name in _SUPPLY_NAMES):
            from ms42_ms43_live import display_spec
            spec = display_spec(name, unit)
        else:
            spec = live_display_spec(name, unit, definition_path)
            if definition_path is None and family is None and name not in ms41_names:
                from ms42_ms43_live import display_spec, display_rows as family_rows
                if any((name, unit) in family_rows(variant) for variant in ("MS42", "MS43")):
                    spec = display_spec(name, unit)
        if spec["kind"] != "value":
            result[name] = {
                key: spec[key] for key in ("kind", "minimum", "maximum", "step")
                if key in spec
            }
    return result


@lru_cache(maxsize=1)
def _bundled_units():
    from ms42_ms43_live import display_rows as family_rows
    units = {}
    for name, unit in (*display_rows(), *family_rows("MS42"), *family_rows("MS43")):
        units.setdefault(name, set()).add(unit)
    for name in _SUPPLY_NAMES:
        units.setdefault(name, set()).add("V")
    return units


def csv_channel_header(name, unit):
    if _bundled_units().get(name) == {unit} and " [" not in name:
        return name
    return f"{name} [{unit}]"


def parse_log(payload, definition_path=None, family=None, *, page=0):
    with io.TextIOWrapper(io.BytesIO(bytes(payload)), encoding="utf-8-sig", newline="") as stream:
        return parse_log_stream(stream, definition_path, family, page=page)


def parse_log_stream(stream, definition_path=None, family=None, *, page=0):
    """Read one contiguous page of original samples, including missing readings."""
    if type(page) is not int or page < 0:
        raise ValueError("Invalid Live Data page")
    rows = csv.reader(stream)
    try:
        header = next(rows)
    except StopIteration as error:
        raise ValueError("Live Data log is empty") from error
    except UnicodeDecodeError as error:
        raise ValueError("Live Data log must be UTF-8 CSV") from error
    except csv.Error as error:
        raise ValueError(f"Invalid Live Data CSV: {error}") from error
    if not header or header[0] != "Time":
        raise ValueError("Live Data log must start with a Time column")
    channel_start = 2 if len(header) > 1 and header[1] == "Datetime" else 1
    channels = []
    units = {}
    ambiguous = set()
    declared = dict(display_rows(definition_path)) if definition_path is not None else {}
    for column in header[channel_start:]:
        name, separator, suffix = column.rpartition(" [")
        if separator and suffix.endswith("]"):
            unit = suffix[:-1]
        else:
            name = column
            candidates = _bundled_units().get(name, set())
            unit = declared.get(name, next(iter(candidates)) if len(candidates) == 1 else "")
            if len(candidates) > 1 and name not in declared:
                ambiguous.add(name)
        channels.append(name)
        units[name] = unit
    if not channels:
        raise ValueError("Live Data log contains no parameters")
    if any(not name.strip() for name in channels) or len(set(channels)) != len(channels):
        raise ValueError("Live Data log contains blank or duplicate parameter names")

    times = []
    series = [{"name": name, "values": [], "labels": []} for name in channels]
    page_size = max(1, min(_PAGE_ROWS, _PAGE_VALUES // len(channels)))
    start = page * page_size
    count = 0
    previous = None
    has_next = False
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
            if previous is not None and timestamp < previous:
                raise ValueError(f"Live Data row {line_number} moves backward in time")
            previous = timestamp
            count += 1
            if count <= start:
                continue
            if len(times) == page_size:
                has_next = True
                break
            times.append(timestamp)
            for item, raw in zip(series, row[channel_start:]):
                value, label = sample_value(raw)
                item["values"].append(value)
                item["labels"].append(label)
    except UnicodeDecodeError as error:
        raise ValueError("Live Data log must be UTF-8 CSV") from error
    except csv.Error as error:
        raise ValueError(f"Invalid Live Data CSV: {error}") from error
    if not times:
        raise ValueError("Live Data log contains no samples")
    for item in series:
        item["unit"] = units.get(item["name"], "")
    return {
        "times": times,
        "series": series,
        "page": page, "first_sample": start + 1, "has_next": has_next,
        "display_specs": display_specs(
            ((name, units[name]) for name in channels if name not in ambiguous), definition_path, family),
    }
