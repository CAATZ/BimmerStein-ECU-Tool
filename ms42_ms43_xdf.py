"""Read-only stock XDF scalar/map inventory for explicitly identified MS42/MS43 files."""

from functools import lru_cache
from math import isfinite
from pathlib import Path
import re
from xml.etree import ElementTree as ET
from xml.parsers import expat

from logger_definition import _compile_expression
from ms42_ms43_image import FULL_SIZE, PROFILES, extract_calibration, image_identity
from calibration_defs import DefinitionError, fmt_value


def _number(text, default=0):
    return default if text is None else int(text.strip(), 0)


class XDFDefinitions:
    def __init__(self, root):
        if root.tag != "XDFFORMAT":
            raise DefinitionError("the XDF root must be <XDFFORMAT>")
        header = root.find("XDFHEADER")
        if header is None:
            raise DefinitionError("XDF header is missing")
        self.title = header.findtext("deftitle", "").strip()
        title = self.title.upper()
        families = [family for family in PROFILES if family in title]
        self.family = families[0] if len(families) == 1 else None
        # The reviewed MS42 v1.3 header contains this extra zero in its title.
        title = title.replace("01100C6", "0110C6") if self.family == "MS42" else title
        pattern = r"\b(011[0-9A-F]{3})\b" if self.family == "MS42" else r"(?<![A-Z0-9])(?:MS)?(43[0-9A-F]{4})(?![A-Z0-9])"
        versions = set(re.findall(pattern, title))
        if self.family is None or len(versions) != 1:
            raise DefinitionError("XDF title must identify one MS42/MS43 software revision")
        self.software = versions.pop()
        base = header.find("BASEOFFSET")
        self.base = 0 if base is None else _number(base.get("offset"))
        if base is not None and _number(base.get("subtract")):
            self.base = -self.base
        region = header.find("REGION")
        self.size = 0 if region is None else _number(region.get("size"))
        profile = PROFILES[self.family]
        if self.size not in (FULL_SIZE, profile.partial_size):
            raise DefinitionError("XDF must declare the full or calibration image size")
        self.categories = {_number(item.get("index")) + 1: item.get("name", "")
                           for item in header.findall("CATEGORY")}
        self.entries = [item for item in root if item.tag in ("XDFCONSTANT", "XDFTABLE")]
        if not self.entries:
            raise DefinitionError("XDF has no constants or tables to inspect")

    def analyze(self, data):
        identity = image_identity(data)
        if (identity["family"], identity["software_id"].upper()) != (self.family, self.software):
            return [], 0, 0, ["The selected XDF does not match this image's family and software."]
        if len(data) == FULL_SIZE:
            try:
                extract_calibration(data)
            except ValueError as error:
                return [], 0, 0, [f"XDF parameters unavailable: {error}"]
        profile = PROFILES[self.family]
        file_base = profile.calibration_offset if len(data) == profile.partial_size else 0
        definition_base = profile.calibration_offset if self.size == profile.partial_size else 0
        rows, scalars, maps, unsupported, outside = [], 0, 0, 0, 0
        for item in self.entries:
            value_axis = item if item.tag == "XDFCONSTANT" else item.find("XDFAXIS[@id='z']")
            if value_axis is None:
                unsupported += 1
                continue
            memory = value_axis.find("EMBEDDEDDATA")
            if memory is None or memory.get("mmedaddress") is None:
                unsupported += 1
                continue
            try:
                absolute = _number(memory.get("mmedaddress")) + self.base + definition_base
                address = absolute - file_base
                width = _number(memory.get("mmedelementsizebits"), 8)
                x = item.find("XDFAXIS[@id='x']")
                y = item.find("XDFAXIS[@id='y']")
                columns = _number(memory.get("mmedcolcount"), int(x.findtext("indexcount", "1")) if x is not None else 1)
                count = _number(memory.get("mmedrowcount"), int(y.findtext("indexcount", "1")) if y is not None else 1)
                if (width not in (8, 16, 32) or columns < 1 or count < 1
                        or columns * count > FULL_SIZE):
                    raise ValueError("unsupported element layout")
                if address < 0 or address + width // 8 * columns * count > len(data):
                    outside += 1
                    continue
                category = item.find("CATEGORYMEM")
                category = self.categories.get(_number(category.get("category")), "Uncategorised") if category is not None else "Uncategorised"
                name = item.findtext("title", "Unnamed parameter")
                if columns * count > 1:
                    rows.append((category, name, f"{columns}×{count} map", f"0x{absolute:05X}"))
                    maps += 1
                    continue
                flags = _number(memory.get("mmedtypeflags"))
                if flags & ~3:
                    raise ValueError("unsupported numeric representation")
                raw = int.from_bytes(data[address:address + width // 8],
                                     "little" if flags & 2 else "big", signed=bool(flags & 1))
                math = value_axis.find("MATH")
                variables = [] if math is None else math.findall("VAR")
                if len(variables) != 1 or set(variables[0].attrib) != {"id"}:
                    raise ValueError("unsupported conversion variables")
                variable = variables[0].get("id", "")
                if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", variable):
                    raise ValueError("unsupported conversion variable")
                equation = math.get("equation", "X")
                if len(equation) > 4096:
                    raise ValueError("conversion expression is too long")
                equation = re.sub(r"\b" + re.escape(variable) + r"\b", "x", equation)
                if not re.fullmatch(r"[xX0-9.eE+\-*/%()\s]+", equation):
                    raise ValueError("unsupported arithmetic conversion")
                value = _compile_expression(equation)(raw)
                if not isfinite(value):
                    raise ValueError("conversion is not finite")
                decimals = min(8, max(0, int(value_axis.findtext("decimalpl", "0"))))
                rows.append((category, name, fmt_value(value, "0" + ("." + "0" * decimals if decimals else "")),
                             value_axis.findtext("units", "")))
                scalars += 1
            except (ValueError, TypeError, ArithmeticError, RecursionError):
                unsupported += 1
        warnings = []
        if unsupported:
            warnings.append(f"{unsupported} XDF entries have unsupported layout or conversion and were omitted.")
        if outside:
            warnings.append(f"{outside} XDF entries lie outside this image and were omitted.")
        return rows, scalars, maps, warnings


def _reject_declaration(*_args):
    raise DefinitionError("XDF document/entity declarations are not supported")


@lru_cache(maxsize=8)
def _load(raw):
    try:
        # Expat recognizes declarations in the XML's actual encoding, including UTF-16.
        parser = expat.ParserCreate()
        parser.StartDoctypeDeclHandler = _reject_declaration
        parser.EntityDeclHandler = _reject_declaration
        parser.Parse(raw, True)
        return XDFDefinitions(ET.fromstring(raw))
    except (expat.ExpatError, ET.ParseError, ValueError) as error:
        raise DefinitionError(str(error)) from error


def load_definitions(path):
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise DefinitionError("XDF exceeds the 16 MiB size limit")
        return _load(raw)
    except OSError as error:
        raise DefinitionError(str(error)) from error
