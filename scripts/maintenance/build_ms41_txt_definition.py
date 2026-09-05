#!/usr/bin/env python3
"""Build a source-complete ID41 XML reference from the SAM2000 MS41.TXT listing."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, getcontext
from pathlib import Path

getcontext().prec = 28

HEADER_RE = re.compile(r'^\(([0-9A-F]+)H\)\s+(\S+)\s+"([^"]+)"')
DIM_RE = re.compile(r'\[(\d+),"([^"]+)"\]')
CONV_RE = re.compile(
    r"CONVERSION:\s+LINEAR\s+\[\s*([^,]+),\s*([^\]]+)\]\s+"
    r"\[\s*([^,]+),\s*([^\]]+)\]"
)
FORMULA_RE = re.compile(r"([A-Z][A-Z0-9_]*)\[([^\]]*)\]\s*=\s*f\(([^)]*)\)")
ARG_RE = re.compile(r"([A-Z][A-Z0-9_]*)\[([^\]]*)\]")
ATTR_RE = re.compile(r'^\s+([A-Z]+)="([^"]*)"')
ADDR_VALUE_RE = re.compile(r"@([0-9A-F]{6})\s+([0-9A-F]{2,8})H")
ADDR_RE = re.compile(r"@([0-9A-F]{6})")

STORAGE = {
    "BYTE": ("uint8", 1),
    "WORD": ("uint16", 2),
    "SIGNED_BYTE": ("int8", 1),
    "SIGNED_WORD": ("int16", 2),
}

AXIS_QUANTITIES = {
    "DK": "throttle angle",
    "DK_GRD": "throttle-angle rate",
    "FAK_MDZ": "torque-reduction factor",
    "FAK_ZW_MDZ": "torque-reduction ignition factor",
    "LM": "engine load",
    "LM_ADD_KOR": "corrected additive load",
    "LM_DK_DIAG": "diagnostic throttle-based load",
    "LM_GEM": "mixture-related load",
    "LM_GMW": "filtered load",
    "LM_KG_H": "air mass flow",
    "LM_Q": "filtered load",
    "N": "engine speed",
    "NDFSS": "serial target-idle adjustment",
    "NDIFKAT2": "catalyst-heating target-speed difference",
    "NGRD": "engine-speed gradient",
    "NSOLL_DIF": "target-idle-speed difference",
    "N_DIF_KOR": "corrected idle-speed error",
    "TAL": "intake-air temperature",
    "TALLFS": "idle-air actuator command",
    "TAL_ST": "intake-air temperature at start",
    "TATE": "canister-purge valve duty",
    "TI": "injection time",
    "TI_ADD_TOTZ": "injector dead time",
    "TI_AD_ADD_GMW": "filtered additive fuel adaptation",
    "TI_AD_FAK_GMW": "filtered multiplicative fuel adaptation",
    "TKW": "coolant temperature",
    "TKW_ST": "coolant temperature at start",
    "T_NS": "time since start",
    "UB": "battery voltage",
    "UB_GMW": "filtered battery voltage",
    "ULS_DIF_1": "oxygen-sensor voltage error 1",
    "ULS_DIF_2": "oxygen-sensor voltage error 2",
    "ZWDIFKT2": "catalyst-heating ignition difference",
    "ZYK": "engine cycles",
}

SYMBOL_TERMS = {
    "aebg": "change limit", "ad": "adaptation", "add": "additive", "abr": "reduction",
    "ak": "exhaust flap", "anz": "count", "ar": "torque reduction", "asc": "traction control",
    "ba": "acceleration", "can": "CAN", "dec": "decrease", "diag": "diagnostic",
    "dif": "difference", "dk": "throttle angle", "dshpt": "dashpot", "dyf": "dynamic filter",
    "ebw": "injection-start angle", "eew": "injection-end angle", "ekp": "fuel pump",
    "epz": "diagnostic debounce counter", "ers": "substitute value", "ev": "injector",
    "fak": "factor", "fs": "drive-range load", "gmw": "filtered value", "grd": "rate",
    "gw": "noise value", "hys": "hysteresis", "i": "integral", "inc": "increase",
    "ini": "initial", "inst": "transient", "ka": "air-conditioning request",
    "kat": "catalyst heating", "kat2": "catalyst-heating phase 2", "kat3": "catalyst-heating phase 3",
    "katv": "catalyst variant", "kh2": "catalyst-heating phase 2", "kk": "air-conditioning compressor",
    "kns": "cold post-start", "kor": "correction", "kr": "knock control", "krfb": "knock-window start",
    "krfe": "knock-window end", "ks": "catalyst protection", "kst": "cold start",
    "lam": "lambda control", "lammw": "mean lambda", "lda": "load dynamics",
    "ll": "idle", "llfs": "idle-air actuator", "lm": "engine load", "lmm": "mass-airflow sensor",
    "lsh": "oxygen-sensor heater", "max": "maximum", "mdz": "torque reduction",
    "min": "minimum", "mitko": "filter coefficient", "n": "engine speed", "neg": "negative",
    "ngrd": "engine-speed gradient", "nmax": "maximum engine speed", "nmin": "minimum engine speed",
    "ns": "post-start", "nsoll": "target idle speed", "nw": "VANOS position", "ob": "upper",
    "p": "proportional", "pos": "positive", "rst": "decay", "s": "overrun",
    "sa": "fuel restoration", "schw": "threshold", "soll": "target", "sr": "deceleration",
    "st": "start", "sz": "coil charge time", "tal": "intake-air temperature",
    "tallfs": "idle-air command", "talsh": "oxygen-sensor heater command", "tate": "purge-valve duty",
    "te": "canister purge", "tev": "canister-purge valve", "ti": "injection time",
    "tkw": "coolant temperature", "tl": "part load", "tmot": "displayed engine temperature",
    "totz": "delay", "tv": "vehicle speed", "ub": "battery voltage", "uls": "oxygen-sensor voltage",
    "unt": "lower", "v": "gain", "vanos": "VANOS", "vl": "full load", "vmax": "vehicle-speed limit",
    "vorl": "prime", "vti": "pre-injection time", "we": "fuel restoration", "well": "idle fuel restoration",
    "wetl": "part-load fuel restoration", "wl": "warm-up", "wst": "repeat start",
    "zyk": "engine cycles", "zyka": "engine-cycle duration", "zkr": "spark-plug cleaning",
    "zs": "ignition", "zsi": "ignition current", "zsr": "spark burn", "zw": "ignition timing",
    "zwb": "base ignition timing",
}


@dataclass(frozen=True)
class Formula:
    name: str
    unit: str
    args: tuple[tuple[str, str], ...]


@dataclass
class Group:
    index: int
    description: str
    formulas: list[Formula]


@dataclass
class Linear:
    p0: Decimal
    r0: int
    p1: Decimal
    r1: int

    @property
    def slope(self) -> Decimal:
        return (self.p1 - self.p0) / Decimal(self.r1 - self.r0)

    @property
    def intercept(self) -> Decimal:
        return self.p0 - self.slope * Decimal(self.r0)


@dataclass
class Record:
    ordinal: int
    group: Group
    cpu_address: int
    storage: str
    name: str
    memory: str
    dimensions: list[tuple[int, str]]
    conversion: Linear
    attrs: dict[str, str]
    body: list[str]
    formula: Formula | None = None
    unit: str = "-"
    axes: list["Axis"] = field(default_factory=list)

    @property
    def storageaddress(self) -> int | None:
        return self.cpu_address - 0x10000 if self.memory == "ROM" else None

    @property
    def width(self) -> int:
        return STORAGE[self.storage][1]


@dataclass
class Axis:
    name: str
    storageaddress: int
    count: int
    width: int
    unit: str
    static: bool = False
    storagetype: str = "uint8"
    expression: str = "x"
    to_byte: str = "x"
    format: str = "0"
    fineincrement: str = "1"
    coarseincrement: str = "10"
    source: str = "RAW_FALLBACK_UNRESOLVED"


@dataclass(frozen=True)
class ScaleCandidate:
    storagetype: str
    units: str
    expression: str
    to_byte: str
    format: str
    fineincrement: str
    coarseincrement: str
    source: str


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_raw(value: str) -> int:
    value = value.strip()
    return int(value[:-1], 16) if value.upper().endswith("H") else int(value)


def clean_unit(value: str | None) -> str:
    if not value:
        return "-"
    value = value.strip().replace("&deg;", "\N{DEGREE SIGN}")
    value = value.replace("\ufffd", "\N{DEGREE SIGN}").replace("\xb0", "\N{DEGREE SIGN}")
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1].strip()
    return value or "-"


def unit_key(value: str) -> str:
    value = clean_unit(value).lower().replace(" ", "")
    aliases = {
        "u/min": "rpm",
        "1/min": "rpm",
        "rpm": "rpm",
        "mg/hub": "mg/stroke",
        "mg/stroke": "mg/stroke",
        "\N{DEGREE SIGN}kw": "degcrk",
        "\N{DEGREE SIGN}crk": "degcrk",
        "gradkw": "degcrk",
        "-": "-",
    }
    return aliases.get(value, value)


def dec_text(value: Decimal, places: int = 14) -> str:
    quant = Decimal(1).scaleb(-places)
    value = value.quantize(quant).normalize()
    text = format(value, "f")
    return "0" if text in {"-0", ""} else text


def linear_text(linear: Linear) -> tuple[str, str]:
    slope = linear.slope
    intercept = linear.intercept
    a = dec_text(slope)
    b = dec_text(intercept)
    expression = f"x*{a}" if intercept == 0 else f"x*{a}{'+' if intercept > 0 else ''}{b}"
    inverse_intercept = -intercept / slope
    ib = dec_text(inverse_intercept)
    inv_slope = dec_text(Decimal(1) / slope)
    to_byte = f"x*{inv_slope}" if inverse_intercept == 0 else f"x*{inv_slope}{'+' if inverse_intercept > 0 else ''}{ib}"
    return expression, to_byte


def format_from_sam(value: str | None) -> str:
    match = re.search(r"\.(\d+)f", value or "")
    places = int(match.group(1)) if match else 0
    return "0" if places == 0 else "0." + "0" * places


def increments(slope: Decimal, fmt: str) -> tuple[str, str]:
    places = len(fmt.partition(".")[2])
    display_step = Decimal(1).scaleb(-places)
    fine = max(abs(slope), display_step)
    return dec_text(fine, max(places + 4, 8)), dec_text(fine * 10, max(places + 4, 8))


def distinguishable_format(fmt: str, slope: Decimal) -> str:
    places = len(fmt.partition(".")[2])
    while Decimal(1).scaleb(-places) > abs(slope):
        places += 1
    return "0" if places == 0 else "0." + "0" * places


def parse_formulas(lines: list[str]) -> list[Formula]:
    formulas = []
    for line in lines:
        for match in FORMULA_RE.finditer(line):
            args = tuple((m.group(1), clean_unit(m.group(2))) for m in ARG_RE.finditer(match.group(3)))
            formulas.append(Formula(match.group(1), clean_unit(match.group(2)), args))
    return formulas


def pick_formula(record: Record) -> Formula | None:
    target = record.attrs.get("T", record.name.split("__", 1)[0]).upper()
    exact = []
    for formula in record.group.formulas:
        stem = re.sub(r"^(KF|TAB|KL|K)_", "", formula.name)
        if stem == target or formula.name == target:
            exact.append(formula)
    if exact:
        return exact[0]
    if len(record.group.formulas) == 1:
        return record.group.formulas[0]
    return None


def parse_txt(path: Path) -> list[Record]:
    lines = path.read_text(encoding="cp1252").splitlines()
    index_positions = [(i, int(m.group(1))) for i, line in enumerate(lines) if (m := re.match(r"Index:\s+(\d+)", line))]
    records: list[Record] = []
    ordinal = 0
    for group_no, (start, index) in enumerate(index_positions):
        end = index_positions[group_no + 1][0] if group_no + 1 < len(index_positions) else len(lines)
        headers = [i for i in range(start, end) if HEADER_RE.match(lines[i])]
        if not headers:
            continue
        prefix = lines[start:headers[0]]
        description = ""
        for line in prefix:
            match = re.match(r"\s*\d+:\s+(.*)", line)
            if match:
                description = match.group(1).strip()
                break
        group = Group(index, description, parse_formulas(prefix))
        for number, pos in enumerate(headers):
            block_end = headers[number + 1] if number + 1 < len(headers) else end
            block = lines[pos:block_end]
            match = HEADER_RE.match(block[0])
            assert match
            attrs = {}
            for line in block:
                am = ATTR_RE.match(line)
                if am:
                    attrs[am.group(1)] = am.group(2)
            memory_match = next((re.match(r'\s+TYPE:"([^"]+)"', line) for line in block if 'TYPE:"' in line), None)
            conversion_match = next((CONV_RE.search(line) for line in block if "CONVERSION:" in line), None)
            if memory_match is None or conversion_match is None:
                raise ValueError(f"Incomplete record at line {pos + 1}")
            dimensions = []
            for line in block:
                if "DIMENSIONS:" in line:
                    dimensions = [(int(m.group(1)), m.group(2)) for m in DIM_RE.finditer(line)]
                    break
            ordinal += 1
            record = Record(
                ordinal=ordinal,
                group=group,
                cpu_address=int(match.group(1), 16),
                storage=match.group(2),
                name=match.group(3),
                memory=memory_match.group(1),
                dimensions=dimensions,
                conversion=Linear(
                    Decimal(conversion_match.group(1).strip()),
                    parse_raw(conversion_match.group(2)),
                    Decimal(conversion_match.group(3).strip()),
                    parse_raw(conversion_match.group(4)),
                ),
                attrs=attrs,
                body=block,
            )
            record.formula = pick_formula(record)
            record.unit = clean_unit(attrs.get("U") or (record.formula.unit if record.formula else None))
            records.append(record)
    return records


def find_run(values: list[int], start: int, count: int) -> tuple[list[int], int]:
    for offset in range(start, len(values) - count + 1):
        run = values[offset:offset + count]
        if count == 1 or (run[1] - run[0] in (1, 2, 4) and all(run[i] - run[i - 1] == run[1] - run[0] for i in range(2, count))):
            return run, offset + count
    raise ValueError(f"No contiguous {count}-cell axis in {values[start:]}")


def extract_axis_occurrences(records: list[Record]) -> dict[str, Axis]:
    occurrences: dict[str, list[Axis]] = defaultdict(list)
    unit_hints: dict[str, set[str]] = defaultdict(set)
    for record in records:
        if record.memory != "ROM" or not record.dimensions:
            continue
        data_start = record.storageaddress
        assert data_start is not None
        cells = 1
        for count, _ in record.dimensions:
            cells *= count
        data_end = data_start + cells * record.width
        extra = []
        static_extra = []
        for line in record.body:
            for match in ADDR_RE.finditer(line):
                cpu = int(match.group(1), 16)
                if cpu < 0x10000:
                    if cpu not in static_extra:
                        static_extra.append(cpu)
                    continue
                if cpu >= 0x14000:
                    continue
                sa = cpu - 0x10000
                if data_start <= sa < data_end or sa in extra:
                    continue
                extra.append(sa)
        cursor = 0
        by_dimension: dict[int, tuple[list[int], int, bool]] = {}
        for dim_index in reversed(range(len(record.dimensions))):
            count, _ = record.dimensions[dim_index]
            if not extra and len(static_extra) >= count:
                run = static_extra[:count]
                width = run[1] - run[0] if count > 1 else 1
                by_dimension[dim_index] = (run, width, True)
                continue
            try:
                run, cursor = find_run(extra, cursor, count)
            except ValueError as exc:
                raise ValueError(
                    f"{record.name} at 0x{record.cpu_address:X}, dimensions={record.dimensions}, extra={extra}: {exc}"
                ) from exc
            width = run[1] - run[0] if count > 1 else 1
            by_dimension[dim_index] = (run, width, False)
        for dim_index, (count, name) in enumerate(record.dimensions):
            run, width, static = by_dimension[dim_index]
            unit = "-"
            if record.formula and dim_index < len(record.formula.args):
                unit = record.formula.args[dim_index][1]
            if unit != "-":
                unit_hints[name].add(unit)
            occurrences[name].append(Axis(name, run[0], count, width, unit, static=static))

    axes = {}
    for name, seen in occurrences.items():
        layouts = {(a.storageaddress, a.count, a.width, a.static) for a in seen}
        if len(layouts) != 1:
            raise ValueError(f"Conflicting layouts for axis {name}: {sorted(layouts)}")
        address, count, width, static = next(iter(layouts))
        units = unit_hints.get(name, set())
        unit = sorted(units)[0] if len({unit_key(u) for u in units}) == 1 and units else "-"
        axes[name] = Axis(name, address, count, width, unit, static=static)
    return axes


def parse_xdf_axes(path: Path, wanted: set[str]) -> dict[str, ScaleCandidate]:
    root = ET.parse(path).getroot()
    result = {}
    for table in root.findall("XDFTABLE"):
        title = table.findtext("title") or ""
        if title not in wanted:
            continue
        z = next((axis for axis in table.findall("XDFAXIS") if axis.get("id") == "z"), None)
        if z is None:
            continue
        embedded = z.find("EMBEDDEDDATA")
        math = z.find("MATH")
        if embedded is None or math is None or embedded.get("mmedaddress") is None:
            continue
        bits = int(embedded.get("mmedelementsizebits", "8"))
        flags = int(embedded.get("mmedtypeflags", "0"), 0)
        signed = bool(flags & 1)
        storagetype = ("int" if signed else "uint") + str(bits)
        expression = (math.get("equation") or "X").replace("X", "x").replace("+ -", "-")
        model = expression_model(expression)
        if model is None:
            continue
        slope, intercept = model
        fmt = "0" if int(z.findtext("decimalpl") or 0) == 0 else "0." + "0" * int(z.findtext("decimalpl") or 0)
        fine, coarse = increments(abs(Decimal(str(slope))), fmt)
        result[title] = ScaleCandidate(
            storagetype, clean_unit(z.findtext("units")), expression,
            inverse_expression(slope, intercept), fmt, fine, coarse, "XDF_DIRECT",
        )
        result[title + "@address"] = int(embedded.get("mmedaddress"), 0)  # type: ignore[assignment]
    return result


def cleaned_rr_root(path: Path) -> ET.Element:
    raw = path.read_text(encoding="utf-8", errors="replace")
    raw = re.sub(r"<!DOCTYPE.*?\]>", "", raw, flags=re.DOTALL)
    raw = raw.replace("&deg;", "\N{DEGREE SIGN}").replace("&micro;", "\N{MICRO SIGN}").replace("&NL;", "\n")
    raw = re.sub(r"&(?!(amp|lt|gt|quot|apos);)", "&amp;", raw)
    return ET.fromstring(raw)


def role(table: ET.Element) -> str:
    value = (table.get("type") or "").lower()
    if "x axis" in value:
        return "X"
    if "y axis" in value:
        return "Y"
    return value


def scaling_candidate(table: ET.Element, parent: ET.Element | None, source: str) -> ScaleCandidate | None:
    scaling = table.find("scaling")
    if scaling is None and parent is not None:
        scaling = parent.find("scaling")
    if scaling is None:
        return None
    storagetype = table.get("storagetype") or (parent.get("storagetype") if parent is not None else None) or "uint8"
    return ScaleCandidate(
        storagetype=storagetype,
        units=clean_unit(scaling.get("units")),
        expression=scaling.get("expression") or "x",
        to_byte=scaling.get("to_byte") or "x",
        format=scaling.get("format") or "0",
        fineincrement=scaling.get("fineincrement") or "1",
        coarseincrement=scaling.get("coarseincrement") or "10",
        source=source,
    )


def parse_stock_scales(path: Path) -> dict[int, list[ScaleCandidate]]:
    root = cleaned_rr_root(path)
    roms = root.findall("rom")
    base = next(rom for rom in roms if rom.findtext("romid/xmlid") == "BMWMS41BASE")
    derived = next(
        rom for rom in roms
        if rom.findtext("romid/xmlid") == "41" and (rom.findtext("romid/internalidaddress") or "").upper() == "E"
    )
    base_tables = {table.get("name"): table for table in base.findall("table") if table.get("name")}
    derived_tables = {table.get("name"): table for table in derived.findall("table") if table.get("name")}
    by_address: dict[int, list[ScaleCandidate]] = defaultdict(list)
    for name, override in derived_tables.items():
        structural = base_tables.get(name)
        if structural is None:
            structural = override
        if override.get("storageaddress"):
            candidate = scaling_candidate(override, structural, f"STOCK_EXACT_ADDRESS:{name}")
            if candidate:
                by_address[int(override.get("storageaddress"), 16)].append(candidate)
        base_axes = {role(axis): axis for axis in structural.findall("table")}
        for axis_override in override.findall("table"):
            if not axis_override.get("storageaddress"):
                continue
            axis_structural = base_axes.get(role(axis_override), axis_override)
            candidate = scaling_candidate(axis_override, axis_structural, f"STOCK_EXACT_ADDRESS:{name}:{role(axis_override)}")
            if candidate:
                by_address[int(axis_override.get("storageaddress"), 16)].append(candidate)
    return by_address


def expression_model(expression: str) -> tuple[float, float] | None:
    expression = expression.replace("&deg;", "").strip()
    if not re.fullmatch(r"[0-9xX.+\-*/() ]+", expression):
        return None
    try:
        values = [float(eval(expression, {"__builtins__": {}}, {"x": x, "X": x})) for x in (0.0, 1.0, 2.0)]
    except Exception:
        return None
    slope = values[1] - values[0]
    if abs((values[2] - values[1]) - slope) > max(1e-9, abs(slope) * 1e-9) or slope == 0:
        return None
    return slope, values[0]


def inverse_expression(slope: float, intercept: float) -> str:
    inv_slope = dec_text(Decimal(1) / Decimal(str(slope)))
    inv_intercept = dec_text(-Decimal(str(intercept)) / Decimal(str(slope)))
    return f"x*{inv_slope}" if inv_intercept == "0" else f"x*{inv_slope}{'+' if not inv_intercept.startswith('-') else ''}{inv_intercept}"


def eval_linear(expression: str, x: Decimal) -> Decimal:
    match = re.fullmatch(r"x\*(-?[0-9.]+)(?:([+-])([0-9.]+))?", expression)
    if not match:
        raise ValueError(expression)
    value = x * Decimal(match.group(1))
    if match.group(2):
        offset = Decimal(match.group(3))
        value = value + offset if match.group(2) == "+" else value - offset
    return value


def validate_conversions(records: list[Record]) -> None:
    for record in records:
        expression, to_byte = linear_text(record.conversion)
        for raw, physical in ((record.conversion.r0, record.conversion.p0), (record.conversion.r1, record.conversion.p1)):
            assert abs(eval_linear(expression, Decimal(raw)) - physical) <= Decimal("0.000001")
            assert abs(eval_linear(to_byte, physical) - Decimal(raw)) <= Decimal("0.000001")


def candidate_key(candidate: ScaleCandidate) -> tuple[float, float, str] | None:
    model = expression_model(candidate.expression)
    if model is None:
        return None
    return round(model[0], 10), round(model[1], 10), candidate.storagetype


def choose_stock(candidates: list[ScaleCandidate], axis: Axis) -> ScaleCandidate | None:
    width_candidates = [c for c in candidates if int(re.search(r"(8|16|32)$", c.storagetype).group(1)) // 8 == axis.width]  # type: ignore[union-attr]
    if axis.unit != "-":
        matching = [c for c in width_candidates if unit_key(c.units) == unit_key(axis.unit)]
        if not matching:
            return None
        width_candidates = matching
    unique = {}
    for candidate in width_candidates:
        key = candidate_key(candidate)
        if key is not None:
            unique.setdefault(key, candidate)
    return next(iter(unique.values())) if len(unique) == 1 else None


def apply_axis_scales(axes: dict[str, Axis], xdf: dict[str, ScaleCandidate], stock: dict[int, list[ScaleCandidate]]) -> None:
    consensus: dict[tuple[str, int], dict[tuple[float, float, str], ScaleCandidate]] = defaultdict(dict)
    for name, axis in axes.items():
        if axis.static:
            continue
        candidate = xdf.get(name)
        address = xdf.get(name + "@address")
        if candidate and address == axis.storageaddress and int(re.search(r"(8|16|32)$", candidate.storagetype).group(1)) // 8 == axis.width:  # type: ignore[union-attr]
            key = candidate_key(candidate)
            if key is not None:
                consensus[(unit_key(axis.unit if axis.unit != "-" else candidate.units), axis.width)][key] = candidate

    for name, axis in axes.items():
        if axis.static:
            axis.unit = "Index"
            axis.source = "STATIC_INDEX_FROM_TXT"
            continue
        candidate = None
        if name == "sst_lm_kf_zw_asc" and axis.storageaddress == 0x758:
            # ID41 stages E8E5 (the standard u8 load variable) against this axis; the old
            # humanized definition swapped it with the RPM axis and therefore cannot supply its scale.
            candidate = ScaleCandidate(
                "uint8", "mg/Hub", "x*(1389/255)", "x*(255/1389)", "0.0",
                "5.4470588235", "54.470588235", "ID41_FIRMWARE_LOAD_AXIS_CONFIRMED",
            )
        elif name == "sst_dk_grd_kf_ti_fak_dk_ba" and axis.storageaddress == 0x1451:
            candidate = ScaleCandidate(
                "uint8", "°DK/s", "x*(2988/255)", "x*(255/2988)", "0.0",
                "11.717647059", "117.17647059", "ID41_FIRMWARE_TPS_GRADIENT_AXIS_CONFIRMED",
            )
        if candidate is None:
            candidate = choose_stock(stock.get(axis.storageaddress, []), axis)
        if candidate is None:
            candidate = xdf.get(name)
            address = xdf.get(name + "@address")
            if not (candidate and address == axis.storageaddress and int(re.search(r"(8|16|32)$", candidate.storagetype).group(1)) // 8 == axis.width):  # type: ignore[union-attr]
                candidate = None
        if candidate is None:
            models = consensus.get((unit_key(axis.unit), axis.width), {})
            if len(models) == 1:
                source = next(iter(models.values()))
                candidate = ScaleCandidate(
                    source.storagetype, axis.unit, source.expression, source.to_byte,
                    source.format, source.fineincrement, source.coarseincrement,
                    "XDF_UNIT_WIDTH_CONSENSUS_INFERRED",
                )
        if candidate is not None:
            axis.storagetype = candidate.storagetype
            axis.expression = candidate.expression
            axis.to_byte = candidate.to_byte
            axis.format = candidate.format
            axis.fineincrement = candidate.fineincrement
            axis.coarseincrement = candidate.coarseincrement
            axis.source = candidate.source.split(":", 1)[0]
            if axis.unit == "-":
                axis.unit = candidate.units
        else:
            axis.storagetype = "uint16" if axis.width == 2 else "uint8"
            if axis.unit != "-":
                axis.unit = f"Raw; intended {axis.unit}"


def xml_attr(value: object) -> str:
    return html.escape(str(value), quote=True)


def load_group_purposes(path: Path) -> dict[int, str]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    purposes = {int(row["index"]): row["english_purpose"].strip() for row in rows}
    if len(purposes) != len(rows) or any(not value for value in purposes.values()):
        raise ValueError(f"Invalid group-purpose catalog: {path}")
    return purposes


def load_firmware_adjudication(paths: list[Path] | None) -> dict[int, dict[str, str]]:
    if not paths:
        return {}
    required = {"address", "verdict", "authoritative_contract", "firmware_evidence", "recommended_action"}
    result: dict[int, dict[str, str]] = {}
    for path in paths:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if not rows or not required.issubset(rows[0]):
            raise ValueError(f"Invalid firmware-adjudication ledger: {path}")
        for row in rows:
            address = int(row["address"], 16)
            if address in result:
                raise ValueError(f"Duplicate firmware-adjudication address 0x{address:X}: {path}")
            result[address] = row
    return result


def load_candidate_categories(path: Path | None) -> dict[int, str]:
    if path is None:
        return {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows or not {"storageaddress", "subsystem"}.issubset(rows[0]):
        raise ValueError(f"Invalid candidate-category catalog: {path}")
    result: dict[int, str] = {}
    for row in rows:
        address = int(row["storageaddress"], 16)
        category = row["subsystem"].strip()
        if address in result or not category:
            raise ValueError(f"Duplicate or blank candidate category at 0x{address:X}: {path}")
        result[address] = category
    return result


def apply_firmware_axis_overrides(axes: dict[str, Axis], path: Path | None) -> None:
    if path is None:
        return
    required = {
        "address", "source_symbol", "storagetype", "units", "expression", "to_byte",
        "evidence", "evidence_ledger",
    }
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"Invalid firmware-axis override ledger: {path}")
    by_address = {axis.storageaddress: axis for axis in axes.values() if not axis.static}
    seen: set[int] = set()
    for row in rows:
        address = int(row["address"], 16)
        if address in seen or address not in by_address:
            raise ValueError(f"Duplicate or unknown firmware-axis override 0x{address:X}: {path}")
        seen.add(address)
        axis = by_address[address]
        if row["source_symbol"].strip() != axis.name:
            raise ValueError(f"Axis symbol mismatch at 0x{address:X}: {row['source_symbol']} != {axis.name}")
        storagetype = row["storagetype"].strip()
        width_match = re.search(r"(8|16|32)$", storagetype)
        if width_match is None or int(width_match.group(1)) // 8 != axis.width:
            raise ValueError(f"Axis width mismatch at 0x{address:X}: {storagetype}")
        expression = row["expression"].strip()
        model = expression_model(expression)
        to_byte = row["to_byte"].strip()
        inverse = expression_model(to_byte)
        if (model is None or inverse is None or not row["units"].strip()
                or not row["evidence"].strip() or not row["evidence_ledger"].strip()):
            raise ValueError(f"Incomplete firmware-axis override at 0x{address:X}")
        for raw in (0.0, 255.0 if axis.width == 1 else 65535.0):
            physical = model[0] * raw + model[1]
            round_trip = inverse[0] * physical + inverse[1]
            if abs(round_trip - raw) > 1e-4:
                raise ValueError(f"Non-inverse firmware-axis override at 0x{address:X}")
        axis.storagetype = storagetype
        axis.unit = row["units"].strip()
        axis.expression = expression
        axis.to_byte = to_byte
        axis.format = distinguishable_format(axis.format, Decimal(str(model[0])))
        axis.fineincrement, axis.coarseincrement = increments(Decimal(str(model[0])), axis.format)
        axis.source = "ID41_FIRMWARE_OVERRIDE"


def humanize_symbol(name: str) -> str:
    stem = name.split("__", 1)[0]
    words = []
    for token in stem.split("_"):
        if token in {"kf", "tab", "kl", "sst", "sstm"} or token.isdigit():
            continue
        words.append(SYMBOL_TERMS.get(token, token.upper() if len(token) <= 5 else token.replace("_", " ")))
    return " ".join(words).replace("  ", " ").strip()


def human_unit(value: str) -> str:
    exact = {
        "-": "dimensionless units", "U/min": "RPM", "1/min": "RPM", "min-1": "RPM",
        "mg/Hub": "mg/stroke", "°DK": "degrees throttle", "°DK/s": "degrees throttle per second",
        "Grad DK/10ms": "degrees throttle per 10 ms", "°KW": "degrees crank",
        "GRAD KW": "degrees crank", "grd.KW": "degrees crank", "°C": "degrees Celsius",
    }
    return exact.get(value, value)


def record_axis_text(record: Record) -> str:
    if not record.dimensions:
        return "It is a scalar and has no lookup axes."
    if record.formula and len(record.formula.args) == len(record.dimensions):
        labels = [AXIS_QUANTITIES.get(name, humanize_symbol(name.lower())) for name, _ in record.formula.args]
    else:
        labels = [humanize_symbol(name) for _, name in record.dimensions]
    if len(labels) == 1:
        return f"The curve is indexed by {labels[0]}."
    return f"The map is indexed by {labels[0]} and {labels[1]}."


def record_description(
    record: Record,
    purposes: dict[int, str],
    adjudication: dict[int, dict[str, str]],
) -> tuple[str, str]:
    purpose = purposes[record.group.index]
    shape = "map" if len(record.dimensions) == 2 else "curve" if len(record.dimensions) == 1 else "scalar"
    firmware = adjudication.get(record.storageaddress if record.storageaddress is not None else -1)
    symbol_hint = (firmware or {}).get("recommended_name", "").strip() or humanize_symbol(record.name)
    expression, _ = linear_text(record.conversion)
    units = human_unit(record.unit)
    intro = f"{purpose}. This {shape} is the Siemens {symbol_hint} calibration (`{record.name}`)."
    if firmware:
        behavior = firmware.get("humanized_interpretation", "").strip()
        description = (
            f"{intro} {record_axis_text(record)}{f' {behavior}' if behavior else ''} "
            "Firmware-grounded ID41 note: "
            f"{firmware['firmware_evidence']} Authoritative interpretation: "
            f"{firmware['authoritative_contract']}. {firmware['recommended_action']}"
        )
        return description, f"ID41_FIRMWARE_ADJUDICATED:{firmware['verdict']}"
    value_text = f"Cell values use `{expression}` and are expressed in {units}."
    description = (
        f"{intro} {record_axis_text(record)} {value_text} Purpose, layout, and conversion come from "
        "the ID41 SAM2000/DAMOS source. The exact firmware consumer and increase/decrease effect "
        "have not yet been code-grounded."
    )
    return description, "DAMOS_SOURCE_REFERENCE_UNTESTED"


def axis_description(axis: Axis, use_count: int, adjudication: dict[int, dict[str, str]]) -> str:
    firmware = adjudication.get(axis.storageaddress)
    quantity = (firmware or {}).get("recommended_name", "").strip() or humanize_symbol(axis.name)
    units = human_unit(axis.unit)
    description = (
        f"Breakpoint axis for {quantity} (`{axis.name}`). It contains {axis.count} points, is used by "
        f"{use_count} SAM2000 calibration item{'s' if use_count != 1 else ''}, and is expressed in {units}. "
        f"Scaling source: {axis.source}."
    )
    if firmware:
        behavior = firmware.get("humanized_interpretation", "").strip()
        if behavior:
            description += f" {behavior}"
        description += (
            f" Firmware-grounded ID41 note: {firmware['firmware_evidence']} Authoritative interpretation: "
            f"{firmware['authoritative_contract']}. {firmware['recommended_action']}"
        )
    return description


def scaling_xml(units: str, expression: str, to_byte: str, fmt: str, fine: str, coarse: str, indent: str) -> str:
    return (
        f'{indent}<scaling units="{xml_attr(units)}" expression="{xml_attr(expression)}" '
        f'to_byte="{xml_attr(to_byte)}" format="{xml_attr(fmt)}" '
        f'fineincrement="{xml_attr(fine)}" coarseincrement="{xml_attr(coarse)}" />'
    )


def axis_structure_xml(
    axis: Axis,
    axis_type: str,
    indent: str = "  ",
    storage_offset: int | None = None,
    display_name: str | None = None,
) -> list[str]:
    name = display_name or axis.name
    if axis.static:
        lines = [
            f'{indent}<table type="Static {axis_type} Axis" name="{xml_attr(name)}" '
            f'size{axis_type.lower()}="{axis.count}">'
        ]
        lines.extend(f"{indent}  <data>{i + 1}</data>" for i in range(axis.count))
        lines.append(f"{indent}</table>")
        return lines
    address = (
        f' storageaddress="0x{axis.storageaddress + storage_offset:X}"'
        if storage_offset is not None else ""
    )
    fmt, fine, coarse = axis.format, axis.fineincrement, axis.coarseincrement
    model = expression_model(axis.expression)
    if storage_offset is not None and model is not None:
        slope = Decimal(str(model[0]))
        fmt = distinguishable_format(fmt, slope)
        fine, coarse = increments(slope, fmt)
    lines = [
        f'{indent}<table type="{axis_type} Axis" name="{xml_attr(name)}" '
        f'storagetype="{axis.storagetype}" endian="little"{address}>',
        scaling_xml(axis.unit, axis.expression, axis.to_byte, fmt, fine, coarse, indent + "  "),
        f"{indent}</table>",
    ]
    return lines


def record_structure_xml(
    record: Record,
    purposes: dict[int, str],
    adjudication: dict[int, dict[str, str]],
    *,
    table_name: str | None = None,
    category_prefix: str = "SAM2000 ID41",
    category_name: str | None = None,
    storage_offset: int | None = None,
) -> str:
    storagetype, _ = STORAGE[record.storage]
    dims = record.dimensions
    table_type = "3D" if len(dims) == 2 else "2D"
    category = category_name or f'{category_prefix} / {"Maps" if len(dims) == 2 else "Curves" if len(dims) == 1 else "Scalars"}'
    attrs = [
        f'type="{table_type}"', f'name="{xml_attr(table_name or f"SAM2000: {record.name}")}"',
        f'category="{xml_attr(category)}"', f'storagetype="{storagetype}"',
        'endian="little"', 'userlevel="5"',
    ]
    if storage_offset is not None:
        assert record.storageaddress is not None
        attrs.append(f'storageaddress="0x{record.storageaddress + storage_offset:X}"')
    if len(dims) == 2:
        attrs.extend((f'sizex="{dims[1][0]}"', f'sizey="{dims[0][0]}"'))
    else:
        attrs.append(f'sizey="{dims[0][0] if dims else 1}"')
    expression, to_byte = linear_text(record.conversion)
    units = record.unit
    slope = record.conversion.slope
    if storage_offset is not None and record.storageaddress == 0x13C:
        expression, to_byte, slope = "x/187.5", "x*187.5", Decimal(1) / Decimal("187.5")
    elif storage_offset is not None and record.storageaddress == 0x1EC2:
        expression, to_byte, slope, units = "x*100/256-50", "x*2.56+128", Decimal(100) / Decimal(256), "%"
    fmt = format_from_sam(record.attrs.get("F"))
    if storage_offset is not None:
        fmt = distinguishable_format(fmt, slope)
    fine, coarse = increments(slope, fmt)
    lines = ["<table " + " ".join(attrs) + ">"]
    lines.append(scaling_xml(units, expression, to_byte, fmt, fine, coarse, "  "))
    axis_names = [
        adjudication.get(axis.storageaddress, {}).get("recommended_name", "").strip()
        or humanize_symbol(axis.name) or axis.name
        for axis in record.axes
    ]
    if len(dims) == 2:
        lines.extend(axis_structure_xml(record.axes[1], "X", storage_offset=storage_offset, display_name=axis_names[1]))
        lines.extend(axis_structure_xml(record.axes[0], "Y", storage_offset=storage_offset, display_name=axis_names[0]))
    elif len(dims) == 1:
        lines.extend(axis_structure_xml(record.axes[0], "Y", storage_offset=storage_offset, display_name=axis_names[0]))
    else:
        lines.extend(('  <table type="Static Y Axis" name="Value" sizey="1">', "    <data>Value</data>", "  </table>"))
    description, _ = record_description(record, purposes, adjudication)
    lines.append(f"  <description>{xml_attr(description)}</description>")
    lines.append("</table>")
    return "\n".join(lines)


def axis_top_structure_xml(
    axis: Axis,
    use_count: int,
    adjudication: dict[int, dict[str, str]],
) -> str:
    category = "Axes" if axis.source != "RAW_FALLBACK_UNRESOLVED" else "Axes - Raw unresolved"
    lines = [
        f'<table type="2D" name="SAM2000 Axis: {xml_attr(axis.name)}" '
        f'category="SAM2000 ID41 / {category}" storagetype="{axis.storagetype}" endian="little" '
        f'sizey="{axis.count}" userlevel="5">',
        scaling_xml(axis.unit, axis.expression, axis.to_byte, axis.format, axis.fineincrement, axis.coarseincrement, "  "),
        f'  <table type="Static Y Axis" name="Index" sizey="{axis.count}">',
    ]
    lines.extend(f"    <data>{i + 1}</data>" for i in range(axis.count))
    lines.extend(("  </table>", f"  <description>{xml_attr(axis_description(axis, use_count, adjudication))}</description>", "</table>"))
    return "\n".join(lines)


def record_override_xml(record: Record, full: bool) -> str:
    offset = 0x14000 if full else 0
    lines = [f'<table name="SAM2000: {xml_attr(record.name)}" storageaddress="0x{record.storageaddress + offset:X}">']
    if len(record.dimensions) == 2:
        if not record.axes[1].static:
            lines.append(f'  <table type="X Axis" storageaddress="0x{record.axes[1].storageaddress + offset:X}" />')
        if not record.axes[0].static:
            lines.append(f'  <table type="Y Axis" storageaddress="0x{record.axes[0].storageaddress + offset:X}" />')
    elif len(record.dimensions) == 1:
        if not record.axes[0].static:
            lines.append(f'  <table type="Y Axis" storageaddress="0x{record.axes[0].storageaddress + offset:X}" />')
    lines.append("</table>")
    return "\n".join(lines)


def axis_override_xml(axis: Axis, full: bool) -> str:
    offset = 0x14000 if full else 0
    return f'<table name="SAM2000 Axis: {xml_attr(axis.name)}" storageaddress="0x{axis.storageaddress + offset:X}" />'


def insert_before_rom_close(source: str, marker: str, fragment: str) -> str:
    start = source.index(marker)
    close = source.index("</rom>", start)
    return source[:close] + fragment + "\n" + source[close:]


def insert_into_rom(source: str, xmlid: str, internalidaddress: str, fragment: str) -> str:
    address_tag = re.compile(
        rf"<internalidaddress>\s*{re.escape(internalidaddress)}\s*</internalidaddress>"
    )
    for match in address_tag.finditer(source):
        romid_start = source.rfind("<romid>", 0, match.start())
        romid_close = source.index("</romid>", match.end())
        romid = source[romid_start:romid_close]
        if not re.search(rf"<xmlid>\s*{re.escape(xmlid)}\s*</xmlid>", romid):
            continue
        close = source.index("</rom>", romid_close)
        return source[:close] + fragment + "\n" + source[close:]
    raise ValueError(f"ROM not found: xmlid={xmlid}, internalidaddress={internalidaddress}")


def build_xml(
    stock_path: Path,
    output_path: Path,
    records: list[Record],
    axes: dict[str, Axis],
    hashes: dict[str, str],
    purposes: dict[int, str],
    adjudication: dict[int, dict[str, str]],
) -> None:
    source = stock_path.read_text(encoding="utf-8")
    rom_records = [record for record in records if record.memory == "ROM"]
    ordered_axes = [axes[name] for name in sorted(axes) if not axes[name].static]
    provenance = (
        "\n<!-- SAM2000 ID41 source-complete reference; generated, not activated.\n"
        f"     MS41.TXT SHA-256: {hashes['txt']}\n"
        f"     Stock XML SHA-256: {hashes['stock']}\n"
        f"     Supporting XDF SHA-256: {hashes['xdf']}\n"
        f"     English purpose catalog SHA-256: {hashes['purposes']}\n"
        f"     Firmware adjudication SHA-256: {hashes.get('adjudication', 'not supplied')}\n"
        f"     Firmware axis overrides SHA-256: {hashes.get('axis_overrides', 'not supplied')}\n"
        "     ROM parameter addresses/types/conversions come directly from MS41.TXT.\n"
        "     Axis scaling source is recorded per axis in the companion CSV.\n"
        "     Evidence status: DAMOS_SOURCE_REFERENCE_UNTESTED; no bench/on-car validation. -->\n"
    )
    axis_use_count = defaultdict(int)
    for record in rom_records:
        for axis in record.axes:
            axis_use_count[axis.name] += 1
    structures = provenance + "\n\n".join(
        [record_structure_xml(record, purposes, adjudication) for record in rom_records]
        + [axis_top_structure_xml(axis, axis_use_count[axis.name], adjudication) for axis in ordered_axes]
    ) + "\n"
    source = insert_before_rom_close(source, "<rom> BMWMS41BASE", structures)
    partial = "\n<!-- SAM2000 ID41/24KB address layer -->\n" + "\n".join(
        [record_override_xml(record, False) for record in rom_records]
        + [axis_override_xml(axis, False) for axis in ordered_axes]
    ) + "\n"
    source = insert_before_rom_close(source, '<rom base="BMWMS41BASE"> ID41  24KB', partial)
    full = "\n<!-- SAM2000 ID41/256KB address layer -->\n" + "\n".join(
        [record_override_xml(record, True) for record in rom_records]
        + [axis_override_xml(axis, True) for axis in ordered_axes]
    ) + "\n"
    source = insert_before_rom_close(source, '<rom base="BMWMS41BASE"> ID41 256KB', full)
    output_path.write_text(source, encoding="utf-8", newline="\n")


HARD_CONFLICTS = {
    "AXIS_ADDRESS_OR_LAYOUT_CONFLICT",
    "AXIS_SCALE_CONFLICT",
    "DATA_SCALE_CONFLICT",
    "LAYOUT_OR_STORAGE_CONFLICT",
}


def build_candidate(
    humanized_path: Path,
    comparison_path: Path,
    output_path: Path,
    summary_path: Path,
    records: list[Record],
    purposes: dict[int, str],
    adjudication: dict[int, dict[str, str]],
    candidate_categories: dict[int, str],
    hashes: dict[str, str],
) -> tuple[int, int, int]:
    with comparison_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    expected_hashes = {
        "source_sha256": hashes["source_complete"],
        "humanized_sha256": hashes["humanized"],
    }
    for hash_field, expected in expected_hashes.items():
        found = {row.get(hash_field, "").lower() for row in rows}
        if found != {expected.lower()}:
            raise ValueError(
                f"Comparison {hash_field} does not match its current build input: {sorted(found)}"
            )
    parameter_rows = {
        row["source_name"]: row for row in rows
        if row["kind"] == "PARAMETER" and row["source_name"]
    }
    if len(parameter_rows) != 670:
        raise ValueError(f"Expected 670 parameter comparisons, found {len(parameter_rows)}")
    humanized_root = cleaned_rr_root(humanized_path)
    humanized_names = {
        table.get("name")
        for rom in humanized_root.findall("rom")
        for table in rom.findall("table")
        if table.get("name")
    }
    missing_humanized = sorted(
        {row["human_name"].strip() for row in parameter_rows.values() if row["human_name"].strip()}
        - humanized_names
    )
    if missing_humanized:
        raise ValueError(
            f"Comparison references {len(missing_humanized)} tables absent from candidate base; "
            f"wrong comparison/base pair (examples: {missing_humanized[:3]})"
        )

    omitted = {
        row["human_name"] for row in rows
        if row["human_name"] and (
            (row["kind"] == "PARAMETER" and row["status"] in HARD_CONFLICTS)
            or row["status"] == "HUMANIZED_OVERLAPS_SOURCE_REGION"
        )
    }
    for row in rows:
        if row["kind"] == "PARAMETER" and row["status"] == "OVERLAP_ONLY":
            omitted.update(name.strip() for name in row["candidates"].split(";") if name.strip())
        if row["kind"] == "PARAMETER" and ";" in row["candidates"] and row["status"] != "HUMANIZED_ENRICHMENT_UNVERIFIED":
            omitted.update(
                name.strip() for name in row["candidates"].split(";")
                if name.strip() and name.strip() != row["human_name"].strip()
            )

    categories_by_name: dict[str, str] = {}
    for rom in humanized_root.findall("rom"):
        for table in rom.findall("table"):
            if table.get("name") and table.get("category"):
                categories_by_name.setdefault(table.get("name"), table.get("category"))
    existing_categories: dict[int, str] = {}
    for rom in humanized_root.findall("rom"):
        if rom.findtext("romid/xmlid") != "41":
            continue
        internal = (rom.findtext("romid/internalidaddress") or "").upper()
        if internal not in {"E", "1400E"}:
            continue
        offset = 0x14000 if internal == "1400E" else 0
        for table in rom.findall("table"):
            category = table.get("category") or categories_by_name.get(table.get("name"))
            if table.get("storageaddress") and category:
                existing_categories.setdefault(int(table.get("storageaddress"), 16) - offset, category)

    selected: list[tuple[Record, str, str, str]] = []
    selected_categories: dict[int, str] = {}
    names: set[str] = set()
    for record in (item for item in records if item.memory == "ROM"):
        row = parameter_rows[record.name]
        existing = row["human_name"].strip()
        action = "OVERRIDE_EXISTING"
        if not existing or row["status"] in HARD_CONFLICTS | {"OVERLAP_ONLY", "HUMANIZED_ENRICHMENT_UNVERIFIED"}:
            title = humanize_symbol(record.name) or record.name
            title = title[:1].upper() + title[1:]
            name = f"ID41 DAMOS: {title} [{record.name}]"
            if row["status"] == "HUMANIZED_ENRICHMENT_UNVERIFIED":
                action = "ADD_SOURCE_VIEW"
            else:
                action = "REPLACE_CONFLICT" if existing else "ADD_SOURCE_ONLY"
        else:
            name = existing
        recommended_name = adjudication.get(record.storageaddress, {}).get("recommended_name", "").strip()
        if recommended_name:
            name = recommended_name
        if existing and name != existing and action != "ADD_SOURCE_VIEW":
            omitted.add(existing)
            if action == "OVERRIDE_EXISTING":
                action = "RENAME_EXISTING"
        if name in names:
            raise ValueError(f"Duplicate candidate table name: {name}")
        names.add(name)
        evidence = (
            f"ID41_FIRMWARE_ADJUDICATED:{adjudication[record.storageaddress]['verdict']}"
            if record.storageaddress in adjudication else "DAMOS_SOURCE_REFERENCE_UNTESTED"
        )
        selected_categories[record.storageaddress] = (
            candidate_categories.get(record.storageaddress)
            or existing_categories.get(record.storageaddress)
            or ("ID41 DAMOS Grounded" if evidence.startswith("ID41_FIRMWARE") else "ID41 DAMOS Reference")
        )
        selected.append((record, name, action, evidence))

    selected_names = {name for _, name, _, _ in selected}
    overlap = selected_names & omitted
    if overlap:
        raise ValueError(f"Candidate definitions also marked omitted: {sorted(overlap)}")

    provenance = (
        "\n<!-- ID41 DAMOS/firmware-grounded humanized candidate; generated, not activated.\n"
        f"     Canonical humanized XML SHA-256: {hashes['humanized']}\n"
        f"     MS41.TXT SHA-256: {hashes['txt']}\n"
        f"     Comparison CSV SHA-256: {hashes['comparison']}\n"
        f"     Firmware adjudication SHA-256: {hashes.get('adjudication', 'not supplied')}\n"
        f"     Firmware axis overrides SHA-256: {hashes.get('axis_overrides', 'not supplied')}\n"
        f"     Candidate categories SHA-256: {hashes.get('candidate_categories', 'not supplied')}\n"
        "     Scope: exact MS41.0 ID41 only. Other CAL IDs retain canonical XML content and inheritance; line endings may be normalized.\n"
        "     ID41 firmware-adjudicated entries are separated from DAMOS-source-only references.\n"
        "     Evidence status: CODE_GROUNDED_REFERENCE_UNTESTED; no bench/on-car validation. -->\n"
    )
    source = humanized_path.read_text(encoding="utf-8")
    omit_xml = "\n".join(f'<table name="{xml_attr(name)}" omit="true" />' for name in sorted(omitted))
    for full, internalidaddress in (
        (False, "E"),
        (True, "1400E"),
    ):
        offset = 0x14000 if full else 0
        tables = []
        for record, name, _, evidence in selected:
            tables.append(record_structure_xml(
                record, purposes, adjudication, table_name=name,
                category_name=selected_categories[record.storageaddress], storage_offset=offset,
            ))
        # The table-name pre-scan stops at the first omit entry.
        fragment = provenance + "\n\n".join(tables) + "\n\n" + omit_xml + "\n"
        source = insert_into_rom(source, "41", internalidaddress, fragment)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(source, encoding="utf-8", newline="\n")

    with summary_path.open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["storageaddress", "source_symbol", "candidate_name", "category", "action", "comparison_status", "previous_name", "evidence"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record, name, action, evidence in selected:
            row = parameter_rows[record.name]
            writer.writerow({
                "storageaddress": f"0x{record.storageaddress:X}", "source_symbol": record.name,
                "candidate_name": name, "category": selected_categories[record.storageaddress],
                "action": action, "comparison_status": row["status"],
                "previous_name": row["human_name"], "evidence": evidence,
            })

    validate_candidate(output_path, selected, omitted, selected_categories)
    grounded = sum(evidence.startswith("ID41_FIRMWARE") for _, _, _, evidence in selected)
    return grounded, len(omitted), sum(action.startswith("ADD_SOURCE") for _, _, action, _ in selected)


def validate_candidate(
    path: Path,
    selected: list[tuple[Record, str, str, str]],
    omitted: set[str],
    selected_categories: dict[int, str],
) -> None:
    root = cleaned_rr_root(path)
    id41 = [rom for rom in root.findall("rom") if rom.findtext("romid/xmlid") == "41"]
    for rom, offset in (
        (next(rom for rom in id41 if (rom.findtext("romid/internalidaddress") or "").upper() == "E"), 0),
        (next(rom for rom in id41 if (rom.findtext("romid/internalidaddress") or "").upper() == "1400E"), 0x14000),
    ):
        nodes = rom.findall("table")
        direct = {table.get("name"): table for table in nodes}
        first_omit = next((index for index, table in enumerate(nodes) if table.get("omit") == "true"), len(nodes))
        omitted_here = {table.get("name") for table in nodes if table.get("omit") == "true"}
        names_by_address: dict[int, set[str]] = defaultdict(set)
        for table in nodes:
            if table.get("storageaddress") and table.get("name") and table.get("name") not in omitted_here:
                names_by_address[int(table.get("storageaddress"), 16)].add(table.get("name"))
        unexpected_aliases = {
            address: names for address, names in names_by_address.items()
            if len(names) > 1 and address != 0x2B4A + offset
        }
        assert not unexpected_aliases, f"Unexpected same-address aliases: {unexpected_aliases}"
        for record, name, _, _ in selected:
            table = direct[name]
            assert nodes.index(table) < first_omit, f"{name}: appears after first omit entry"
            assert table.get("category") == selected_categories[record.storageaddress]
            actual = int(table.get("storageaddress"), 16)
            expected = record.storageaddress + offset
            assert actual == expected, f"{name}: 0x{actual:X} != 0x{expected:X}"
            assert (table.findtext("description") or "").strip()
            for scaled in [table, *table.findall("table")]:
                scaling = scaled.find("scaling")
                if scaling is None or (model := expression_model(scaling.get("expression", ""))) is None:
                    continue
                places = len(scaling.get("format", "0").partition(".")[2])
                assert Decimal(1).scaleb(-places) <= abs(Decimal(str(model[0])))
            dynamic_axes = [axis for axis in table.findall("table") if " Axis" in (axis.get("type") or "") and not (axis.get("type") or "").startswith("Static")]
            actual_axes = [int(axis.get("storageaddress"), 16) for axis in dynamic_axes]
            ordered_axes = [record.axes[1], record.axes[0]] if len(record.axes) == 2 else record.axes
            expected_axes = [axis.storageaddress + offset for axis in ordered_axes if not axis.static]
            assert actual_axes == expected_axes, f"{name}: axes {actual_axes} != {expected_axes}"
        for name in omitted:
            assert direct[name].get("omit") == "true"


def attach_axes(records: list[Record], axes: dict[str, Axis]) -> None:
    for record in records:
        if record.memory == "ROM":
            names = [name for _, name in record.dimensions]
            if record.storageaddress == 0x18D8:
                # Exact ID41 stages descriptor 0x18E2/axis 0x14BE before reading 0x18D8;
                # the source-declared 0x18D6/0x14D0 descriptor is never staged.
                names = ["sstm_n_3_4"]
            record.axes = [axes[name] for name in names]
            assert [axis.count for axis in record.axes] == [count for count, _ in record.dimensions]


def compare_sample(records: list[Record], sample_path: Path | None) -> tuple[int, int]:
    if sample_path is None:
        return 0, 0
    sample = sample_path.read_bytes()
    printed: dict[tuple[int, int], int] = {}
    for record in records:
        for line in record.body:
            for match in ADDR_VALUE_RE.finditer(line):
                cpu = int(match.group(1), 16)
                if not 0x10000 <= cpu < 0x14000:
                    continue
                raw_text = match.group(2)
                width = len(raw_text) // 2
                if width not in (1, 2):
                    continue
                printed[(cpu - 0x10000, width)] = int(raw_text, 16)
    matches = 0
    for (sa, width), expected in printed.items():
        offset = sa + 0x14000
        actual = int.from_bytes(sample[offset:offset + width], "little")
        matches += actual == expected
    return matches, len(printed)


def write_catalog(
    path: Path,
    records: list[Record],
    axes: dict[str, Axis],
    purposes: dict[int, str],
    adjudication: dict[int, dict[str, str]],
) -> None:
    columns = [
        "kind", "emitted", "record", "index", "symbol", "rr_name", "memory", "cpu_address",
        "storageaddress", "full_file_offset", "storage", "width_bits", "dimensions", "unit",
        "scaling_source", "expression", "to_byte", "axis_sources", "source_description",
        "description", "description_evidence",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for record in records:
            is_rom = record.memory == "ROM"
            expression, to_byte = linear_text(record.conversion)
            description, description_evidence = record_description(record, purposes, adjudication)
            writer.writerow({
                "kind": "ROM_PARAMETER" if is_rom else "RAM_SYMBOL",
                "emitted": "yes" if is_rom else "no",
                "record": record.ordinal,
                "index": record.group.index,
                "symbol": record.name,
                "rr_name": f"SAM2000: {record.name}" if is_rom else "",
                "memory": record.memory,
                "cpu_address": f"0x{record.cpu_address:05X}",
                "storageaddress": f"0x{record.storageaddress:X}" if is_rom else "",
                "full_file_offset": f"0x{record.storageaddress + 0x14000:X}" if is_rom else "",
                "storage": record.storage,
                "width_bits": record.width * 8,
                "dimensions": " x ".join(str(count) for count, _ in record.dimensions) or "1",
                "unit": record.unit,
                "scaling_source": "MS41.TXT_DIRECT" if is_rom else "NOT_EMITTED_RUNTIME_RAM",
                "expression": expression,
                "to_byte": to_byte,
                "axis_sources": "; ".join(f"{axis.name}:{axis.source}" for axis in record.axes),
                "source_description": record.group.description,
                "description": description,
                "description_evidence": description_evidence,
            })
        axis_use_count = defaultdict(int)
        for record in records:
            if record.memory == "ROM":
                for axis in record.axes:
                    axis_use_count[axis.name] += 1
        for axis in sorted(axes.values(), key=lambda item: item.name):
            writer.writerow({
                "kind": "ROM_AXIS", "emitted": "yes", "record": "", "index": "", "symbol": axis.name,
                "rr_name": f"SAM2000 Axis: {axis.name}", "memory": "ROM",
                "cpu_address": "static" if axis.static else f"0x{axis.storageaddress + 0x10000:05X}",
                "storageaddress": "" if axis.static else f"0x{axis.storageaddress:X}",
                "full_file_offset": "" if axis.static else f"0x{axis.storageaddress + 0x14000:X}",
                "storage": axis.storagetype, "width_bits": axis.width * 8, "dimensions": axis.count,
                "unit": axis.unit, "scaling_source": axis.source, "expression": axis.expression,
                "to_byte": axis.to_byte, "axis_sources": "", "source_description": "SAM2000 referenced axis",
                "description": axis_description(axis, axis_use_count[axis.name], adjudication),
                "description_evidence": (
                    f"ID41_FIRMWARE_ADJUDICATED:{adjudication[axis.storageaddress]['verdict']}"
                    if axis.storageaddress in adjudication else axis.source
                ),
            })


def validate_output(path: Path, rom_count: int, axis_count: int, adjudicated_count: int) -> None:
    root = cleaned_rr_root(path)
    base = next(rom for rom in root.findall("rom") if rom.findtext("romid/xmlid") == "BMWMS41BASE")
    new_base = [table for table in base.findall("table") if (table.get("name") or "").startswith("SAM2000")]
    assert len(new_base) == rom_count + axis_count
    descriptions = [table.findtext("description", "").strip() for table in new_base]
    assert all(descriptions)
    assert sum("Firmware-grounded ID41 note:" in description for description in descriptions) == adjudicated_count
    assert all("The exact firmware consumer" in description or "Firmware-grounded ID41 note:" in description
               or (table.get("name") or "").startswith("SAM2000 Axis:")
               for table, description in zip(new_base, descriptions))
    id41 = [rom for rom in root.findall("rom") if rom.findtext("romid/xmlid") == "41"]
    partial = next(rom for rom in id41 if (rom.findtext("romid/internalidaddress") or "").upper() == "E")
    full = next(rom for rom in id41 if (rom.findtext("romid/internalidaddress") or "").upper() == "1400E")
    partial_tables = {table.get("name"): table for table in partial.findall("table") if (table.get("name") or "").startswith("SAM2000")}
    full_tables = {table.get("name"): table for table in full.findall("table") if (table.get("name") or "").startswith("SAM2000")}
    assert len(partial_tables) == rom_count + axis_count
    assert len(full_tables) == rom_count + axis_count
    for name, partial_table in partial_tables.items():
        full_table = full_tables[name]
        assert int(full_table.get("storageaddress"), 16) == int(partial_table.get("storageaddress"), 16) + 0x14000
        partial_axes = {role(axis): axis for axis in partial_table.findall("table")}
        full_axes = {role(axis): axis for axis in full_table.findall("table")}
        assert partial_axes.keys() == full_axes.keys()
        for axis_role, partial_axis in partial_axes.items():
            assert int(full_axes[axis_role].get("storageaddress"), 16) == int(partial_axis.get("storageaddress"), 16) + 0x14000


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--txt", required=True, type=Path)
    parser.add_argument("--stock", required=True, type=Path)
    parser.add_argument("--xdf", required=True, type=Path)
    parser.add_argument("--sample-bin", type=Path)
    parser.add_argument(
        "--group-purposes",
        type=Path,
        default=Path(__file__).with_name("ms41_txt_group_purposes.csv"),
    )
    parser.add_argument("--firmware-adjudication", type=Path, action="append")
    parser.add_argument("--firmware-axis-overrides", type=Path)
    parser.add_argument("--candidate-categories", type=Path)
    parser.add_argument("--candidate-humanized", type=Path)
    parser.add_argument("--comparison-csv", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    records = parse_txt(args.txt)
    assert len(records) == 743
    rom_records = [record for record in records if record.memory == "ROM"]
    assert len(rom_records) == 670 and len({record.name for record in rom_records}) == 670
    assert all(record.storage in STORAGE for record in records)
    validate_conversions(records)

    axes = extract_axis_occurrences(records)
    assert len(axes) == 185
    xdf = parse_xdf_axes(args.xdf, set(axes))
    stock = parse_stock_scales(args.stock)
    apply_axis_scales(axes, xdf, stock)
    apply_firmware_axis_overrides(axes, args.firmware_axis_overrides)
    attach_axes(records, axes)
    purposes = load_group_purposes(args.group_purposes)
    missing_purposes = sorted({record.group.index for record in rom_records} - purposes.keys())
    if missing_purposes:
        raise ValueError(f"Missing English purpose for SAM2000 groups: {missing_purposes}")
    adjudication = load_firmware_adjudication(args.firmware_adjudication)
    candidate_categories = load_candidate_categories(args.candidate_categories)
    known_addresses = {record.storageaddress for record in rom_records} | {
        axis.storageaddress for axis in axes.values() if not axis.static
    }
    unknown_adjudication = sorted(set(adjudication) - known_addresses)
    if unknown_adjudication:
        raise ValueError(f"Firmware adjudication contains unknown SAM2000 addresses: {unknown_adjudication}")
    unknown_categories = sorted(set(candidate_categories) - {record.storageaddress for record in rom_records})
    if unknown_categories:
        raise ValueError(f"Candidate categories contain unknown ROM addresses: {unknown_categories}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    xml_path = args.output_dir / "MS41.0 ID41 - SAM2000 Source Complete.xml"
    csv_path = args.output_dir / "MS41.0 ID41 - SAM2000 Source Catalog.csv"
    hashes = {
        "txt": sha256(args.txt),
        "stock": sha256(args.stock),
        "xdf": sha256(args.xdf),
        "purposes": sha256(args.group_purposes),
    }
    if args.firmware_adjudication:
        hashes["adjudication"] = ", ".join(sha256(path) for path in args.firmware_adjudication)
    if args.firmware_axis_overrides:
        hashes["axis_overrides"] = sha256(args.firmware_axis_overrides)
    if args.candidate_categories:
        hashes["candidate_categories"] = sha256(args.candidate_categories)
    build_xml(args.stock, xml_path, records, axes, hashes, purposes, adjudication)
    write_catalog(csv_path, records, axes, purposes, adjudication)
    dynamic_axis_count = len([axis for axis in axes.values() if not axis.static])
    validate_output(xml_path, len(rom_records), dynamic_axis_count, len(adjudication))

    sample_matches, sample_total = compare_sample(records, args.sample_bin)
    sources = defaultdict(int)
    for axis in axes.values():
        sources[axis.source] += 1
    print(f"XML: {xml_path}")
    print(f"Catalog: {csv_path}")
    print(f"TXT records: {len(records)} (ROM {len(rom_records)}, RAM {len(records) - len(rom_records)})")
    print(
        f"Emitted: {len(rom_records)} parameters + {dynamic_axis_count} addressable axes = "
        f"{len(rom_records) + dynamic_axis_count} top-level ID41 definitions; "
        f"{len(axes) - dynamic_axis_count} static axes are nested"
    )
    print("Axis scaling sources: " + ", ".join(f"{key}={value}" for key, value in sorted(sources.items())))
    grounded_parameters = sum(record.storageaddress in adjudication for record in rom_records)
    grounded_axes = sum(not axis.static and axis.storageaddress in adjudication for axis in axes.values())
    print(
        f"Humanized descriptions: {len(rom_records)} parameters; "
        f"firmware-grounded parameters={grounded_parameters}; firmware-grounded axes={grounded_axes}"
    )
    if sample_total:
        print(f"Sample/TXT printed-cell fingerprint: {sample_matches}/{sample_total} ({sample_matches / sample_total:.2%})")
    print(f"Output XML SHA-256: {sha256(xml_path)}")
    print(f"Catalog SHA-256: {sha256(csv_path)}")
    if bool(args.candidate_humanized) != bool(args.comparison_csv):
        raise ValueError("--candidate-humanized and --comparison-csv must be supplied together")
    if args.candidate_humanized:
        candidate_dir = args.output_dir / "humanized_candidate"
        candidate_path = candidate_dir / "MS41.0 ID41 - DAMOS Firmware Grounded Humanized Candidate.xml"
        summary_path = candidate_dir / "MS41.0 ID41 - Humanized Candidate Changes.csv"
        candidate_hashes = hashes | {
            "humanized": sha256(args.candidate_humanized),
            "comparison": sha256(args.comparison_csv),
            "source_complete": sha256(xml_path),
        }
        grounded, omitted, source_only = build_candidate(
            args.candidate_humanized, args.comparison_csv, candidate_path, summary_path,
            records, purposes, adjudication, candidate_categories, candidate_hashes,
        )
        print(f"Candidate: {candidate_path}")
        print(f"Candidate changes: {summary_path}")
        print(
            f"Candidate coverage: 670 source parameters; firmware-grounded={grounded}; "
            f"DAMOS-source-only={670 - grounded}; added-source-entries={source_only}; omitted-conflicts={omitted}"
        )
        print(f"Candidate SHA-256: {sha256(candidate_path)}")
        print(f"Candidate changes SHA-256: {sha256(summary_path)}")


if __name__ == "__main__":
    main()
