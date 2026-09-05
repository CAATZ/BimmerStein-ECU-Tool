"""
romraider_defs.py — Parser for MS41 ECU definition XML files.

Makes the ROM Analyzer data-driven instead of relying on
a handful of hardcoded scalars.  It:

  * parses a user-selected XML-format MS41 ECU definition XML,
  * resolves the rom inheritance chain (BMWMS41BASE -> ID12_BASE / child -> leaf),
  * matches a loaded .bin to the correct rom definition by
    (internalidaddress + internalidstring + filesize) — this is how the definition format
    distinguishes a 256 KB FULL file (OBDII/readiness tables only) from a 24 KB
    PARTIAL "tune space" file (all the scalars/maps), for MS41.0/.1/.2 AND MS41.3,
  * exposes every table with its merged attributes, computing scalar values.

Definitions are loaded only from the explicit path selected in the ROM Analyzer.
"""

from functools import lru_cache
import hashlib
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional, Dict, List

# Characters allowed in a scaling expression we will eval (defence-in-depth).
_EXPR_OK = set("x0123456789.+-*/%() eE")


@dataclass
class RomDef:
    xmlid:   str
    base:    str
    idaddr:  str
    idstr:   str
    filesize: str
    submodel: str
    ecuid:   str
    tables:  Dict[str, dict] = field(default_factory=dict)   # name -> attrs (+ "_scaling")


class DefinitionError(ValueError):
    """A selected XML file is not a usable calibration definition."""


class Definitions:
    def __init__(self, root: ET.Element):
        if root.tag != "roms":
            raise DefinitionError("the root element must be <roms>")
        self._roms: List[RomDef] = []
        self._by_xmlid: Dict[str, List[RomDef]] = {}
        for rom in root.findall("rom"):
            rid = rom.find("romid")
            if rid is None:
                continue
            def gt(tag): return (rid.findtext(tag) or "").strip()
            rd = RomDef(
                xmlid=gt("xmlid"), base=(rom.get("base") or "").strip(),
                idaddr=gt("internalidaddress"), idstr=gt("internalidstring"),
                filesize=gt("filesize"), submodel=gt("submodel"), ecuid=gt("ecuid"),
            )
            for t in rom.findall("table"):
                nm = t.get("name")
                if not nm:
                    continue
                attrs = dict(t.attrib)
                sc = t.find("scaling")
                if sc is not None:
                    attrs["_scaling"] = dict(sc.attrib)
                states = t.findall("state")
                if states:
                    attrs["_states"] = [(s.get("name", ""), s.get("data", "")) for s in states]
                rd.tables[nm] = attrs
            self._roms.append(rd)
            self._by_xmlid.setdefault(rd.xmlid, []).append(rd)
        if not self._roms:
            raise DefinitionError("the file does not contain any <rom> definitions")

    # ── matching ────────────────────────────────────────────────────────────
    def match(self, data: bytes) -> Optional[RomDef]:
        n = len(data)
        want = "256kb" if n == 262144 else "24kb" if n == 24576 else None
        if want is None:
            return None
        for rd in self._roms:
            if rd.filesize != want or not rd.idaddr or not rd.idstr:
                continue
            try:
                a = int(rd.idaddr, 16)
            except ValueError:
                continue
            s = rd.idstr.encode("ascii", "ignore")
            if data[a:a + len(s)] == s:
                return rd
        return None

    # ── inheritance ──────────────────────────────────────────────────────────
    def resolve(self, rom: RomDef) -> Dict[str, dict]:
        """Merge the base chain (root first) by table name; leaf overrides."""
        chain, seen, cur = [], set(), rom
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur)); chain.append(cur)
            cur = self._base_of(cur)
        merged: Dict[str, dict] = {}
        for rd in reversed(chain):
            for nm, attrs in rd.tables.items():
                m = merged.get(nm, {})
                m.update(attrs)
                merged[nm] = m
        return merged

    def _base_of(self, rom: RomDef) -> Optional[RomDef]:
        if not rom.base or rom.base not in self._by_xmlid:
            return None
        cands = self._by_xmlid[rom.base]
        # prefer the base variant with the same filesize (E vs 1400E share xmlid)
        return next((c for c in cands if c.filesize == rom.filesize), cands[0])


# ── value evaluation ──────────────────────────────────────────────────────────

def _eval(expr: str, x):
    if not expr or any(c not in _EXPR_OK for c in expr):
        return None
    try:
        return eval(expr, {"__builtins__": {}}, {"x": x})   # noqa: S307 (charset-restricted)
    except Exception:
        return None


def _one(v): return v is None or v == "1"


def classify(attrs: dict) -> str:
    t = attrs.get("type", "")
    if t == "Switch":
        return "switch"
    if _one(attrs.get("sizex")) and _one(attrs.get("sizey")):
        return "scalar"
    return "map"


def read_scalar(data: bytes, attrs: dict):
    """Return (value, units, fmt) for a single-value table, or (None, unit, fmt)."""
    sc = attrs.get("_scaling", {})
    units = sc.get("units", "")
    fmt = sc.get("format", "")
    sa = attrs.get("storageaddress")
    if not sa:
        return None, units, fmt
    try:
        a = int(sa, 16)
    except ValueError:
        return None, units, fmt
    st = attrs.get("storagetype", "uint8")
    en = attrs.get("endian", "little")
    if st in ("uint16", "int16"):
        if a + 2 > len(data):
            return None, units, fmt
        raw = (data[a] | (data[a + 1] << 8)) if en == "little" else ((data[a] << 8) | data[a + 1])
        if st == "int16" and raw > 0x7FFF:
            raw -= 0x10000
    else:
        if a >= len(data):
            return None, units, fmt
        raw = data[a]
    return _eval(sc.get("expression", "x"), raw), units, fmt


def switch_state(data: bytes, attrs: dict) -> Optional[str]:
    """Read the switch byte and return the matching state name, or a hex value."""
    sa = attrs.get("storageaddress")
    if not sa:
        return None
    try:
        a = int(sa, 16)
    except ValueError:
        return None
    if a >= len(data):
        return None
    b = data[a]
    for name, hexval in attrs.get("_states", []):
        try:
            if int(hexval, 16) == b:
                return name
        except ValueError:
            continue
    return f"0x{b:02X}"


def fmt_value(value, fmt: str) -> str:
    if value is None:
        return "—"
    try:
        if fmt and fmt.startswith("0.0"):
            decimals = len(fmt.split(".")[1])
            return f"{value:.{decimals}f}"
        if fmt == "0" or not fmt:
            return f"{value:.0f}" if isinstance(value, float) else str(value)
        return f"{value:.2f}"
    except Exception:
        return str(value)


# ── module-level cache / loader ───────────────────────────────────────────────

def _parse(path: Path) -> Definitions:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        text = re.sub(r"<!DOCTYPE.*?\]>", "", text, flags=re.S)
        text = text.replace("&NL;", "\n").replace("&deg;", "deg").replace("&micro;", "u")
        return Definitions(ET.fromstring(text))
    except DefinitionError:
        raise
    except (OSError, ET.ParseError) as exc:
        raise DefinitionError(str(exc)) from exc


@lru_cache(maxsize=16)
def _cached(path_text: str, modified_ns: int, size: int, digest: str) -> Definitions:
    del modified_ns, size, digest
    return _parse(Path(path_text))


def load_definitions(path: Path | str) -> Definitions:
    """Load a definition from an explicit path, caching unchanged files."""
    selected = Path(path).resolve()
    try:
        stat = selected.stat()
    except OSError as exc:
        raise DefinitionError(str(exc)) from exc
    if not selected.is_file():
        raise DefinitionError("the selected path is not a file")
    try:
        digest = hashlib.sha256(selected.read_bytes()).hexdigest()
    except OSError as exc:
        raise DefinitionError(str(exc)) from exc
    return _cached(str(selected), stat.st_mtime_ns, stat.st_size, digest)


def get_definitions(path: Path | str | None = None) -> Optional[Definitions]:
    """Load a selected definition, or return ``None`` when none is selected."""
    return None if path is None else load_definitions(path)
