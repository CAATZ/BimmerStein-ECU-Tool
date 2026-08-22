#!/usr/bin/env python3
"""Compare the source-complete SAM2000 ID41 definition with a humanized ID41 XML."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import build_ms41_txt_definition as sam

BS = {"uint8": 1, "int8": 1, "uint16": 2, "int16": 2, "uint32": 4, "int32": 4, "float": 4}


@dataclass
class Compared:
    status: str
    source_name: str
    source_address: int | None
    source_layout: str
    source_storage: str
    source_units: str
    source_expression: str
    human_name: str = ""
    human_address: int | None = None
    human_layout: str = ""
    human_storage: str = ""
    human_units: str = ""
    human_expression: str = ""
    scale_status: str = ""
    unit_status: str = ""
    axis_status: str = ""
    candidates: str = ""
    notes: str = ""
    kind: str = "PARAMETER"
    source_sha256: str = ""
    humanized_sha256: str = ""


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location("ms41def_compare", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def dec(value: str | None, default: int = 1) -> int:
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


def layout(entry: dict[str, Any]) -> tuple[str, int, int]:
    table_type = entry.get("type", "?")
    sx = dec(entry.get("sizex"))
    sy = dec(entry.get("sizey"))
    if table_type != "3D":
        return table_type, max(sx, sy), 1
    return table_type, sx, sy


def layout_text(entry: dict[str, Any]) -> str:
    table_type, sx, sy = layout(entry)
    return f"{table_type} {sy}x{sx}" if table_type == "3D" else f"{table_type} {sx}"


def layout_cells(value: str) -> int:
    dimensions = [int(part) for part in re.findall(r"\d+", value.split(" ", 1)[-1])]
    return math.prod(dimensions) if dimensions else 0


def cell_count(entry: dict[str, Any]) -> int:
    table_type, sx, sy = layout(entry)
    return sx * sy if table_type == "3D" else sx


def span(entry: dict[str, Any]) -> int:
    return cell_count(entry) * BS.get(entry.get("storagetype", ""), 1)


def normalize_unit(value: str | None) -> str:
    value = sam.clean_unit(value).lower().replace(" ", "")
    aliases = {
        "u/min": "rpm", "1/min": "rpm", "rpm": "rpm",
        "mg/hub": "mg/stroke", "mg/stroke": "mg/stroke",
        "kg/hr": "kg/h", "kg/h": "kg/h",
        "milliseconds": "ms", "millisecond": "ms",
        "°c": "degc", "degc": "degc",
        "°kw": "degcrk", "°crk": "degcrk", "gradkw": "degcrk",
    }
    return aliases.get(value, value)


def scale_model(expression: str | None) -> tuple[float, float] | None:
    if not expression:
        return None
    expression = expression.replace("&deg;", "").strip()
    if not re.fullmatch(r"[0-9xX.+\-*/() ]+", expression):
        return None
    try:
        values = [float(eval(expression, {"__builtins__": {}}, {"x": x, "X": x})) for x in (0.0, 1.0, 2.0)]
    except Exception:
        return None
    slope = values[1] - values[0]
    if slope == 0 or abs((values[2] - values[1]) - slope) > max(1e-9, abs(slope) * 1e-9):
        return None
    return slope, values[0]


def compare_scale(source: dict[str, Any], human: dict[str, Any], source_scale_known: bool = True) -> str:
    left = scale_model(source.get("expr"))
    right = scale_model(human.get("expr"))
    if left is None or right is None:
        return "UNKNOWN"
    slope_delta = abs(left[0] - right[0]) / max(abs(left[0]), abs(right[0]), 1e-12)
    offset_delta = abs(left[1] - right[1])
    if slope_delta <= 1e-4 and offset_delta <= 1e-4:
        return "MATCH"
    if not source_scale_known:
        return "HUMANIZED_ENRICHMENT_UNVERIFIED"
    source_identity = abs(left[0] - 1.0) <= 1e-12 and abs(left[1]) <= 1e-12
    human_identity = abs(right[0] - 1.0) <= 1e-12 and abs(right[1]) <= 1e-12
    if source_identity and normalize_unit(source.get("units")) == "-" and not human_identity:
        return "HUMANIZED_ENRICHMENT_UNVERIFIED"
    if human_identity and not source_identity:
        return "HUMANIZED_RAW_SOURCE_PHYSICAL"
    source_factor = normalize_unit(source.get("units")) == "-"
    human_percent = "%" in (human.get("units") or "")
    if source_factor and human_percent:
        percent_slope_delta = abs(left[0] * 100 - right[0]) / max(abs(left[0] * 100), abs(right[0]), 1e-12)
        if percent_slope_delta <= 0.01 and abs(left[1] * 100 - right[1]) <= 0.1:
            return "PERCENT_FACTOR_PRESENTATION"
    if slope_delta <= 0.01 and offset_delta <= 0.1:
        return "CLOSE_ROUNDING_DIFFERENCE"
    return "DIFFERENT"


def unit_status(source: dict[str, Any], human: dict[str, Any], known: bool) -> str:
    if not known:
        return "SOURCE_UNIT_UNSPECIFIED"
    return "MATCH" if normalize_unit(source.get("units")) == normalize_unit(human.get("units")) else "DIFFERENT"


def axis_count(parent: dict[str, Any], axis: dict[str, Any], role: str) -> int:
    if parent.get("type") != "3D":
        return max(
            dec(axis.get("sizex"), 0), dec(axis.get("sizey"), 0),
            dec(parent.get("sizex"), 0), dec(parent.get("sizey"), 0), 1,
        )
    explicit = dec(axis.get("sizex") if role == "X" else axis.get("sizey"), 0)
    if explicit:
        return explicit
    return dec(parent.get("sizex") if role == "X" else parent.get("sizey"))


def axis_detail(source: dict[str, Any], human: dict[str, Any], catalog: dict[str, dict[str, str]]) -> tuple[str, str]:
    if cell_count(source) == 1:
        return "NONE", ""
    statuses = []
    details = []
    for role in ("X", "Y"):
        left = source.get("axes", {}).get(role)
        if not left:
            continue
        right = human.get("axes", {}).get(role)
        if not right:
            statuses.append("MISSING")
            details.append(f"{role}:missing")
            continue
        if left.get("static"):
            same = left.get("static") == right.get("static")
            statuses.append("MATCH" if same else "STATIC_PRESENTATION_DIFFERENT")
            details.append(f"{role}:static-{'match' if same else 'different'}")
            continue
        left_address = int(left.get("storageaddress"), 16) if left.get("storageaddress") else None
        right_address = int(right.get("storageaddress"), 16) if right.get("storageaddress") else None
        if left_address != right_address:
            statuses.append("ADDRESS_DIFFERENT")
            details.append(f"{role}:0x{left_address:X}->0x{right_address:X}" if right_address is not None else f"{role}:missing-address")
            continue
        left_axis = {"expr": left.get("expr"), "units": left.get("units")}
        right_axis = {"expr": right.get("expr"), "units": right.get("units")}
        evidence = next((row for row in catalog.values()
                         if row.get("kind") == "ROM_AXIS" and row.get("storageaddress")
                         and int(row["storageaddress"], 16) == left_address), {})
        source_known = evidence.get("scaling_source") != "RAW_FALLBACK_UNRESOLVED"
        scaling = compare_scale(left_axis, right_axis, source_known)
        if scaling == "DIFFERENT":
            statuses.append("SCALE_DIFFERENT")
        elif scaling == "HUMANIZED_ENRICHMENT_UNVERIFIED":
            statuses.append("HUMANIZED_ENRICHMENT_UNVERIFIED")
        elif scaling == "HUMANIZED_RAW_SOURCE_PHYSICAL":
            statuses.append("HUMANIZED_RAW_SOURCE_PHYSICAL")
        else:
            statuses.append("MATCH")
        details.append(f"{role}:0x{left_address:X}:{scaling}")
    if not statuses:
        return "NONE", ""
    if all(status == "MATCH" for status in statuses):
        return "MATCH", "; ".join(details)
    if any(status in {"MISSING", "ADDRESS_DIFFERENT"} for status in statuses):
        return "ADDRESS_OR_LAYOUT_DIFFERENT", "; ".join(details)
    if any(status == "STATIC_PRESENTATION_DIFFERENT" for status in statuses):
        return "STATIC_PRESENTATION_DIFFERENT", "; ".join(details)
    if any(status == "HUMANIZED_RAW_SOURCE_PHYSICAL" for status in statuses):
        return "HUMANIZED_RAW_SOURCE_PHYSICAL", "; ".join(details)
    if any(status == "HUMANIZED_ENRICHMENT_UNVERIFIED" for status in statuses):
        return "HUMANIZED_ENRICHMENT_UNVERIFIED", "; ".join(details)
    return "SCALE_DIFFERENT", "; ".join(details)


def score_candidate(source: dict[str, Any], human: dict[str, Any], source_scale_known: bool, source_unit_known: bool,
                    catalog: dict[str, dict[str, str]]) -> tuple[int, dict[str, str]]:
    same_storage = source.get("storagetype") == human.get("storagetype")
    same_layout = layout(source) == layout(human)
    same_span = span(source) == span(human)
    scale = compare_scale(source, human, source_scale_known)
    units = unit_status(source, human, source_unit_known)
    axes, axis_notes = axis_detail(source, human, catalog)
    score = 0
    score += 35 if same_span else 0
    score += 25 if same_storage else 0
    score += 25 if same_layout else 0
    score += {"MATCH": 20, "PERCENT_FACTOR_PRESENTATION": 18, "CLOSE_ROUNDING_DIFFERENCE": 12,
              "HUMANIZED_ENRICHMENT_UNVERIFIED": 8, "HUMANIZED_RAW_SOURCE_PHYSICAL": 8}.get(scale, 0)
    score += 5 if units == "MATCH" else 0
    score += {"MATCH": 20, "HUMANIZED_ENRICHMENT_UNVERIFIED": 10,
              "HUMANIZED_RAW_SOURCE_PHYSICAL": 10, "NONE": 5, "SCALE_DIFFERENT": 5}.get(axes, 0)
    return score, {
        "same_storage": str(same_storage), "same_layout": str(same_layout), "same_span": str(same_span),
        "scale": scale, "units": units, "axes": axes, "axis_notes": axis_notes,
    }


def classify(detail: dict[str, str]) -> str:
    layout_ok = detail["same_storage"] == detail["same_layout"] == detail["same_span"] == "True"
    if not layout_ok:
        return "LAYOUT_OR_STORAGE_CONFLICT"
    if detail["axes"] == "ADDRESS_OR_LAYOUT_DIFFERENT":
        return "AXIS_ADDRESS_OR_LAYOUT_CONFLICT"
    if detail["axes"] == "STATIC_PRESENTATION_DIFFERENT":
        if detail["scale"] == "HUMANIZED_ENRICHMENT_UNVERIFIED":
            return "HUMANIZED_ENRICHMENT_UNVERIFIED"
        return "AXIS_STATIC_PRESENTATION_DIFFERENCE"
    if detail["scale"] == "DIFFERENT":
        return "DATA_SCALE_CONFLICT"
    if detail["scale"] == "HUMANIZED_ENRICHMENT_UNVERIFIED":
        return "HUMANIZED_ENRICHMENT_UNVERIFIED"
    if detail["scale"] == "HUMANIZED_RAW_SOURCE_PHYSICAL":
        return "HUMANIZED_RAW_SOURCE_PHYSICAL"
    if detail["axes"] == "SCALE_DIFFERENT":
        return "AXIS_SCALE_CONFLICT"
    if detail["axes"] == "HUMANIZED_ENRICHMENT_UNVERIFIED":
        return "HUMANIZED_AXIS_ENRICHMENT_UNVERIFIED"
    if detail["axes"] == "HUMANIZED_RAW_SOURCE_PHYSICAL":
        return "AXIS_HUMANIZED_RAW_SOURCE_PHYSICAL"
    if detail["scale"] == "PERCENT_FACTOR_PRESENTATION":
        return "PERCENT_FACTOR_PRESENTATION"
    if detail["scale"] == "CLOSE_ROUNDING_DIFFERENCE":
        return "CLOSE_ROUNDING_DIFFERENCE"
    if detail["scale"] == "UNKNOWN":
        return "LAYOUT_MATCH_SCALE_UNRESOLVED"
    if detail["units"] == "DIFFERENT":
        return "MATCH_WITH_UNIT_LABEL_DIFFERENCE"
    return "FULL_STRUCTURAL_SCALE_MATCH"


def human_axis_candidates(human_tables: dict[str, dict[str, Any]]) -> tuple[dict[int, list[dict[str, Any]]], list[dict[str, Any]]]:
    addressed: dict[int, list[dict[str, Any]]] = defaultdict(list)
    static = []
    for parent_name, parent in human_tables.items():
        for role, axis in parent.get("axes", {}).items():
            candidate = dict(axis)
            candidate["display_name"] = f"{parent_name} / {role} Axis"
            candidate["count"] = axis_count(parent, axis, role)
            candidate["storagetype"] = axis.get("storagetype", parent.get("storagetype", "uint8"))
            if axis.get("static"):
                static.append(candidate)
            elif axis.get("storageaddress"):
                addressed[int(axis["storageaddress"], 16)].append(candidate)
    for name, table in human_tables.items():
        if "storageaddress" not in table:
            continue
        candidate = dict(table)
        candidate["display_name"] = name
        candidate["count"] = cell_count(table)
        addressed[int(table["storageaddress"], 16)].append(candidate)
    return addressed, static


def axis_rows(source_tables: dict[str, dict[str, Any]], human_tables: dict[str, dict[str, Any]], catalog: dict[str, dict[str, str]]) -> list[Compared]:
    source_axes: dict[str, dict[str, Any]] = {}
    for table_name, table in source_tables.items():
        if not table_name.startswith("SAM2000 Axis:"):
            continue
        name = table_name.removeprefix("SAM2000 Axis:").strip()
        if name in catalog:
            item = dict(table)
            item["count"] = cell_count(table)
            source_axes[name] = item
    for name, evidence in catalog.items():
        if evidence.get("kind") == "ROM_AXIS" and not evidence.get("storageaddress"):
            count = int(evidence["dimensions"])
            source_axes[name] = {
                "static": [str(index + 1) for index in range(count)],
                "count": count,
                "storagetype": evidence["storage"],
                "units": evidence["unit"],
                "expr": evidence["expression"],
            }
    human_by_address, human_static = human_axis_candidates(human_tables)
    rows = []
    for name in sorted(source_axes):
        source = source_axes[name]
        evidence = catalog.get(name, {})
        source_status = evidence.get("scaling_source", "")
        if source.get("static"):
            matches = [candidate for candidate in human_static if candidate.get("static") == source.get("static")]
            status = "STATIC_MATCH" if matches else "STATIC_SOURCE_ONLY"
            rows.append(Compared(status, name, None, f"Static {source['count']}", "static", "Index", "x",
                                 human_name="; ".join(c["display_name"] for c in matches), kind="AXIS",
                                 notes=f"source={source_status}"))
            continue
        address = int(source["storageaddress"], 16)
        candidates = human_by_address.get(address, [])
        best = None
        best_score = -1
        best_scale = ""
        for candidate in candidates:
            same_width = BS.get(source.get("storagetype", ""), 1) == BS.get(candidate.get("storagetype", ""), 1)
            same_count = source["count"] == candidate.get("count")
            source_known = source_status != "RAW_FALLBACK_UNRESOLVED"
            scale = compare_scale(source, candidate, source_known)
            score = (30 if same_width else 0) + (30 if same_count else 0) + {
                "MATCH": 30, "HUMANIZED_ENRICHMENT_UNVERIFIED": 20,
                "CLOSE_ROUNDING_DIFFERENCE": 15, "HUMANIZED_RAW_SOURCE_PHYSICAL": 15,
            }.get(scale, 0)
            if score > best_score:
                best, best_score, best_scale = candidate, score, scale
        if best is None:
            status = "MISSING_FROM_HUMANIZED"
            rows.append(Compared(status, name, address, f"2D {source['count']}", source.get("storagetype", ""), source.get("units", ""), source.get("expr", ""), kind="AXIS", notes=f"source={source_status}"))
            continue
        same_width = BS.get(source.get("storagetype", ""), 1) == BS.get(best.get("storagetype", ""), 1)
        same_count = source["count"] == best.get("count")
        if not same_width or not same_count:
            status = "LAYOUT_OR_STORAGE_CONFLICT"
        elif best_scale == "HUMANIZED_ENRICHMENT_UNVERIFIED":
            status = "HUMANIZED_ENRICHMENT_UNVERIFIED"
        elif best_scale == "DIFFERENT":
            status = "SCALE_CONFLICT"
        elif best_scale == "CLOSE_ROUNDING_DIFFERENCE":
            status = "CLOSE_ROUNDING_DIFFERENCE"
        elif best_scale == "HUMANIZED_RAW_SOURCE_PHYSICAL":
            status = "HUMANIZED_RAW_SOURCE_PHYSICAL"
        else:
            status = "FULL_STRUCTURAL_SCALE_MATCH"
        rows.append(Compared(
            status, name, address, f"2D {source['count']}", source.get("storagetype", ""), source.get("units", ""), source.get("expr", ""),
            human_name=best["display_name"], human_address=address, human_layout=f"2D {best.get('count', 1)}",
            human_storage=best.get("storagetype", ""), human_units=best.get("units", ""), human_expression=best.get("expr", ""),
            scale_status=best_scale, candidates="; ".join(c["display_name"] for c in candidates), kind="AXIS",
            notes=f"source={source_status}",
        ))
    return rows


def parameter_rows(source_tables: dict[str, dict[str, Any]], human_tables: dict[str, dict[str, Any]], records: dict[str, sam.Record],
                   catalog: dict[str, dict[str, str]]) -> list[Compared]:
    human_by_address: dict[int, list[dict[str, Any]]] = defaultdict(list)
    human_intervals = []
    for name, entry in human_tables.items():
        if "storageaddress" not in entry:
            continue
        candidate = dict(entry)
        candidate["name"] = name
        address = int(entry["storageaddress"], 16)
        human_by_address[address].append(candidate)
        human_intervals.append((address, address + span(entry), candidate))
    rows = []
    for rr_name, source in sorted(source_tables.items(), key=lambda item: int(item[1].get("storageaddress", "FFFF"), 16)):
        if not rr_name.startswith("SAM2000:"):
            continue
        symbol = rr_name.split(":", 1)[1].strip()
        record = records[symbol]
        evidence = catalog.get(symbol, {})
        address = int(source["storageaddress"], 16)
        candidates = human_by_address.get(address, [])
        unit_known = bool(record.attrs.get("U") or record.formula)
        scale_known = evidence.get("scaling_source") != "RAW_FALLBACK_UNRESOLVED"
        if not candidates:
            end = address + span(source)
            overlaps = [candidate for start, stop, candidate in human_intervals if start < end and address < stop]
            status = "OVERLAP_ONLY" if overlaps else "MISSING_FROM_HUMANIZED"
            rows.append(Compared(
                status, symbol, address, layout_text(source), source.get("storagetype", ""), source.get("units", ""), source.get("expr", ""),
                candidates="; ".join(candidate["name"] for candidate in overlaps),
                notes=f"SAM2000 Index {record.group.index}: {record.group.description}",
            ))
            continue
        scored = []
        for candidate in candidates:
            score, detail = score_candidate(source, candidate, scale_known, unit_known, catalog)
            scored.append((score, candidate, detail))
        _, best, detail = max(scored, key=lambda item: item[0])
        status = classify(detail)
        rows.append(Compared(
            status, symbol, address, layout_text(source), source.get("storagetype", ""), source.get("units", ""), source.get("expr", ""),
            human_name=best["name"], human_address=address, human_layout=layout_text(best), human_storage=best.get("storagetype", ""),
            human_units=best.get("units", ""), human_expression=best.get("expr", ""), scale_status=detail["scale"],
            unit_status=detail["units"], axis_status=detail["axes"], candidates="; ".join(candidate["name"] for candidate in candidates),
            notes=f"{detail['axis_notes']}; SAM2000 Index {record.group.index}: {record.group.description}".strip("; "),
        ))
    return rows


def humanized_only_rows(source_tables: dict[str, dict[str, Any]], human_tables: dict[str, dict[str, Any]]) -> list[Compared]:
    source_param_addresses = {
        int(entry["storageaddress"], 16) for name, entry in source_tables.items()
        if name.startswith("SAM2000:") and "storageaddress" in entry
    }
    source_axis_addresses = {
        int(entry["storageaddress"], 16) for name, entry in source_tables.items()
        if name.startswith("SAM2000 Axis:") and "storageaddress" in entry
    }
    source_intervals = [
        (int(entry["storageaddress"], 16), int(entry["storageaddress"], 16) + span(entry), name)
        for name, entry in source_tables.items() if name.startswith("SAM2000:") and "storageaddress" in entry
    ]
    rows = []
    for name, human in sorted(human_tables.items(), key=lambda item: int(item[1].get("storageaddress", "FFFF"), 16)):
        if "storageaddress" not in human:
            continue
        address = int(human["storageaddress"], 16)
        if address in source_param_addresses or address in source_axis_addresses:
            continue
        end = address + span(human)
        overlaps = [source_name for start, stop, source_name in source_intervals if start < end and address < stop]
        status = "HUMANIZED_OVERLAPS_SOURCE_REGION" if overlaps else "HUMANIZED_ONLY"
        rows.append(Compared(
            status, "", None, "", "", "", "", human_name=name, human_address=address,
            human_layout=layout_text(human), human_storage=human.get("storagetype", ""), human_units=human.get("units", ""),
            human_expression=human.get("expr", ""), candidates="; ".join(overlaps), kind="HUMANIZED_ONLY",
        ))
    return rows


def write_csv(path: Path, rows: list[Compared]) -> None:
    fields = list(Compared.__dataclass_fields__)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            values = row.__dict__.copy()
            for key in ("source_address", "human_address"):
                values[key] = f"0x{values[key]:X}" if values[key] is not None else ""
            writer.writerow(values)


def md_table(rows: list[list[str]], headers: list[str]) -> str:
    def safe(value: str) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(safe(value) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def write_report(path: Path, source_path: Path, human_path: Path, parameter: list[Compared], axes: list[Compared], human_only: list[Compared]) -> None:
    parameter_counts = Counter(row.status for row in parameter)
    axis_counts = Counter(row.status for row in axes)
    human_counts = Counter(row.status for row in human_only)
    conflict_order = {
        "LAYOUT_OR_STORAGE_CONFLICT", "AXIS_ADDRESS_OR_LAYOUT_CONFLICT", "DATA_SCALE_CONFLICT", "AXIS_SCALE_CONFLICT",
        "CLOSE_ROUNDING_DIFFERENCE", "MATCH_WITH_UNIT_LABEL_DIFFERENCE", "LAYOUT_MATCH_SCALE_UNRESOLVED",
        "HUMANIZED_RAW_SOURCE_PHYSICAL",
    }
    conflicts = [row for row in parameter if row.status in conflict_order]
    missing_maps = sorted(
        [row for row in parameter if row.status in {"MISSING_FROM_HUMANIZED", "OVERLAP_ONLY"} and row.source_layout != "2D 1"],
        key=lambda row: layout_cells(row.source_layout),
        reverse=True,
    )
    raw_axis = [row for row in axes if "RAW_FALLBACK_UNRESOLVED" in row.notes]
    exact_parameter_matches = sum(row.status not in {"MISSING_FROM_HUMANIZED", "OVERLAP_ONLY"} for row in parameter)
    exact_axis_matches = sum(row.status not in {"MISSING_FROM_HUMANIZED", "STATIC_SOURCE_ONLY"} for row in axes)
    scope_warning = (
        "- Scope warning: the compared humanized input is a quarantined snapshot. Results are hash-bound to this file and must not be assumed to describe a later corrected snapshot."
        if "_quarantine" in str(human_path)
        else "- Scope: results are hash-bound to this exact supplied definition."
    )
    by_address = {row.source_address: row for row in parameter}
    axis_by_address = {row.source_address: row for row in axes}
    findings = []
    if by_address[0x410].status == "LAYOUT_OR_STORAGE_CONFLICT":
        findings.append("- `0x410-0x412`: SAM2000 defines `kf_nmax_diag_1__dk` as a three-point RPM curve versus TPS; the compared file splits `0x410` and `0x411` into scalar MAF-plausibility RPM thresholds.")
    axis_4c9 = axis_by_address[0x4C9]
    if axis_4c9.status == "LAYOUT_OR_STORAGE_CONFLICT":
        findings.append("- `0x4C9-0x4CB`: SAM2000 defines one three-point RPM axis, `sst_n_kf_lm_ad_max_llfs`; the compared file treats `0x4C9` and `0x4CA` as low/high MAF-plausibility TPS scalars.")
    elif axis_4c9.status == "MISSING_FROM_HUMANIZED":
        findings.append("- `0x4C9-0x4CB`: the three-point SAM2000 RPM axis `sst_n_kf_lm_ad_max_llfs` is absent from the compared file.")
    if by_address[0xF1E].status == "AXIS_ADDRESS_OR_LAYOUT_CONFLICT":
        findings.append("- `0xF1E`: the 4x4 ASC ignition table uses X=`0x758`, Y=`0x75D` in SAM2000; the compared file swaps those axes.")
    if all(by_address[address].status == "AXIS_ADDRESS_OR_LAYOUT_CONFLICT" for address in (0x1D5E, 0x1D66)):
        findings.append("- `0x1D5E` and `0x1D66`: both fuel-restore RPM curves use the temperature axis at `0x1C44`; the compared overrides point one byte early, at `0x1C43`.")
    signed_conflicts = [address for address in (0x1C6, 0x1904, 0x1912) if by_address[address].status == "LAYOUT_OR_STORAGE_CONFLICT"]
    if signed_conflicts:
        findings.append(f"- {', '.join(f'`0x{address:X}`' for address in signed_conflicts)}: SAM2000 specifies signed-byte storage while the compared file uses unsigned bytes, changing the meaning of negative values.")
    knock_rows = [row for row in parameter if row.source_name.startswith(("tab_krfb_", "tab_krfe_"))]
    knock_conflicts = [row for row in knock_rows if row.status in {"DATA_SCALE_CONFLICT", "LAYOUT_OR_STORAGE_CONFLICT", "OVERLAP_ONLY"}]
    if knock_conflicts:
        findings.append(f"- `0x25DA-0x28C6`: {len(knock_conflicts)} knock-window maps conflict in scale or start address; SAM2000 uses `x*6`. Cylinder labels are also rotated relative to SAM2000's 1-5-3-6-2-4 sequence.")
    else:
        findings.append("- `0x25DA-0x28C6`: all 12 knock-window addresses, layouts, axes, and `x*6` scaling agree. Exact ID41 ring-preload tracing confirms SAM2000's 1-5-3-6-2-4 cylinder mapping; the humanized labels are rotated one firing event early.")
    if all(by_address[address].status == "DATA_SCALE_CONFLICT" for address in (0x292C, 0x2932)):
        findings.append("- `0x292C` and `0x2932`: knock-retard step sizes are positive `x*.375` in SAM2000 and negative `-x*.375` in the compared file.")
    if by_address[0x107C].status == "DATA_SCALE_CONFLICT":
        findings.append("- `0x107C`: the nominal ignition-correction scale is `x*.375-48` in SAM2000 but `(128-x)*.375` in the compared file, reversing its sign around the midpoint.")
    if all(by_address[address].status == "DATA_SCALE_CONFLICT" for address in (0x7F0, 0x11C8)):
        findings.append("- `0x7F0` and `0x11C8`: VANOS-related tables retain their layouts but disagree materially in conversion (`x*6-768` versus `x-128`, and `x*.375-48` versus `(x-128)*.392`).")
    scale_examples = [by_address[address] for address in (0x250, 0x44C, 0x818, 0x242E, 0x291A, 0x29CC)
                      if by_address[address].status == "HUMANIZED_RAW_SOURCE_PHYSICAL"]
    lines = [
        "# MS41 ID41 SAM2000 vs Humanized Definition Comparison",
        "",
        "Evidence status: `DAMOS_SOURCE_REFERENCE_UNTESTED`. This is a structural/scaling comparison, not bench or on-car validation.",
        "",
        "## Inputs",
        "",
        f"- SAM2000 source definition: `{source_path}`",
        f"- Source SHA-256: `{digest(source_path)}`",
        f"- Humanized definition: `{human_path}`",
        f"- Humanized SHA-256: `{digest(human_path)}`",
        scope_warning,
        "",
        "## Headline counts",
        "",
        md_table(
            [["Source parameters", str(len(parameter))],
             ["Exact-address parameter matches", f"{exact_parameter_matches} ({exact_parameter_matches / len(parameter):.1%})"],
             ["Source axes", str(len(axes))],
             ["Address/static axis matches", f"{exact_axis_matches} ({exact_axis_matches / len(axes):.1%})"],
             ["Humanized-only/address-unmatched tables", str(len(human_only))]],
            ["Population", "Count"],
        ),
        "",
        "### Parameter classifications",
        "",
        md_table([[status, str(count)] for status, count in sorted(parameter_counts.items())], ["Classification", "Count"]),
        "",
        "### Axis classifications",
        "",
        md_table([[status, str(count)] for status, count in sorted(axis_counts.items())], ["Classification", "Count"]),
        "",
        "### Humanized-only classifications",
        "",
        md_table([[status, str(count)] for status, count in sorted(human_counts.items())], ["Classification", "Count"]),
        "",
        "## Most consequential findings",
        "",
        "These compare explicit SAM2000 addresses, dimensions, storage types, axis links, and conversions. They are strong source evidence, but remain `DAMOS_SOURCE_REFERENCE_UNTESTED` until checked against firmware behavior.",
        "",
        *findings,
        "",
        "Presentation-only differences are separate: factor-versus-percent displays, close coefficient rounding, and physical conversions added where the TXT left an axis raw are not counted as direct defects.",
        "",
        "## Useful scale coverage gains",
        "",
        f"- {parameter_counts['HUMANIZED_RAW_SOURCE_PHYSICAL']} same-address parameters are raw `x` in the humanized file but have a non-identity SAM2000 conversion.",
        "- Examples: " + "; ".join(f"`0x{row.source_address:X}` `{row.source_name}` `{row.source_expression} {row.source_units}`" for row in scale_examples) + ".",
        f"- {parameter_counts['PERCENT_FACTOR_PRESENTATION']} apparent scale differences are only factor-versus-percent presentation, and {parameter_counts['CLOSE_ROUNDING_DIFFERENCE']} are close coefficient rounding.",
        f"- Of {len(raw_axis)} TXT axes whose physical scale was unresolved, {sum(row.status == 'HUMANIZED_ENRICHMENT_UNVERIFIED' for row in raw_axis)} gain a humanized physical conversion, {sum(row.status == 'FULL_STRUCTURAL_SCALE_MATCH' for row in raw_axis)} remain raw matches, and {sum(row.status == 'MISSING_FROM_HUMANIZED' for row in raw_axis)} are absent.",
        "",
        "## Parameter conflicts and meaningful differences",
        "",
        md_table(
            [[row.status, f"0x{row.source_address:X}", row.source_name, row.human_name, row.source_layout, row.human_layout,
              f"{row.source_expression} -> {row.human_expression}", row.axis_status] for row in conflicts[:80]],
            ["Status", "SA", "SAM2000 symbol", "Humanized label", "Source layout", "Human layout", "Scale", "Axes"],
        ) if conflicts else "None.",
        "",
        "## Largest missing or overlap-only SAM2000 maps",
        "",
        md_table(
            [[row.status, f"0x{row.source_address:X}", row.source_name, row.source_layout, row.source_units, row.candidates] for row in missing_maps[:60]],
            ["Status", "SA", "SAM2000 symbol", "Layout", "Units", "Overlap candidates"],
        ) if missing_maps else "None.",
        "",
        "## Previously raw SAM2000 axes versus humanized",
        "",
        md_table(
            [[row.status, f"0x{row.source_address:X}" if row.source_address is not None else "static", row.source_name,
              row.human_name, f"{row.source_expression} -> {row.human_expression}"] for row in raw_axis],
            ["Status", "SA", "SAM2000 axis", "Humanized mapping", "Scale"],
        ) if raw_axis else "None.",
        "",
        "## Humanized entries with no exact SAM2000 parameter or axis start",
        "",
        md_table(
            [[row.status, f"0x{row.human_address:X}", row.human_name, row.human_layout, row.human_units, row.candidates] for row in human_only[:100]],
            ["Status", "SA", "Humanized label", "Layout", "Units", "Overlapping source"],
        ) if human_only else "None.",
        "",
        "The companion CSV contains every source parameter, every source axis, every candidate at the same address, and all humanized-only entries.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-xml", required=True, type=Path)
    parser.add_argument("--humanized-xml", required=True, type=Path)
    parser.add_argument("--txt", required=True, type=Path)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--ms41def", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    ms41def = load_module(args.ms41def)
    source_roms, _ = ms41def.load_roms(args.source_xml)
    human_roms, _ = ms41def.load_roms(args.humanized_xml)
    source_tables, _ = ms41def.resolve_tables(source_roms, "41")
    human_tables, _ = ms41def.resolve_tables(human_roms, "41")
    records = {record.name: record for record in sam.parse_txt(args.txt) if record.memory == "ROM"}
    with args.catalog.open(encoding="utf-8-sig", newline="") as handle:
        catalog = {row["symbol"]: row for row in csv.DictReader(handle)}

    parameter = parameter_rows(source_tables, human_tables, records, catalog)
    axes = axis_rows(source_tables, human_tables, catalog)
    human_only = humanized_only_rows(source_tables, human_tables)
    source_hash, humanized_hash = digest(args.source_xml), digest(args.humanized_xml)
    for row in parameter + axes + human_only:
        row.source_sha256, row.humanized_sha256 = source_hash, humanized_hash
    assert len(parameter) == 670
    if len(axes) != 185:
        raise AssertionError(f"Expected 185 source axes, resolved {len(axes)}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / "MS41 ID41 - SAM2000 vs Humanized Comparison.csv"
    report_path = args.output_dir / "MS41 ID41 - SAM2000 vs Humanized Report.md"
    write_csv(csv_path, parameter + axes + human_only)
    write_report(report_path, args.source_xml, args.humanized_xml, parameter, axes, human_only)

    print(f"Report: {report_path}")
    print(f"CSV: {csv_path}")
    print("Parameters: " + ", ".join(f"{key}={value}" for key, value in sorted(Counter(row.status for row in parameter).items())))
    print("Axes: " + ", ".join(f"{key}={value}" for key, value in sorted(Counter(row.status for row in axes).items())))
    print("Humanized-only: " + ", ".join(f"{key}={value}" for key, value in sorted(Counter(row.status for row in human_only).items())))
    print(f"Report SHA-256: {digest(report_path)}")
    print(f"CSV SHA-256: {digest(csv_path)}")


if __name__ == "__main__":
    main()
