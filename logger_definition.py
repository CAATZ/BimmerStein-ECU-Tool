"""Small, safe reader for the XML DS2 logger-definition subset we use."""

from __future__ import annotations

import ast
import functools
import re
import struct
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree as ET


BUNDLED_LOGGER_DEFINITION_NAME = "BimmerStein MS41 Logger Definitions.xml"
_WIDTHS = {
    "int8": (1, True), "uint8": (1, False),
    "int16": (2, True), "uint16": (2, False),
    "int32": (4, True), "uint32": (4, False),
    "float": (4, False),
}
_FORMATS = {"0", "0.0", "0.00", "0.000", "0.0000", "0.00000", "0.000000"}
_NOT = re.compile(r"(?<![<>=!])!(?!=)")


class LoggerDefinitionError(ValueError):
    """The selected file is not a supported XML DS2 logger definition."""


def bundled_logger_definition_path() -> Path:
    return Path(__file__).resolve().parent / "logger_definitions" / BUNDLED_LOGGER_DEFINITION_NAME


def _bitwise(mask: float, value: float, operation: float = 1) -> float:
    mask, value, operation = int(mask), int(value), int(operation)
    operations = {
        1: lambda: value & mask,
        2: lambda: value | mask,
        3: lambda: value ^ mask,
        4: lambda: value << mask,
        5: lambda: value >> mask,
        6: lambda: (value & 0xFFFFFFFF) >> mask,
        7: lambda: ~value,
    }
    try:
        return float(operations[operation]())
    except KeyError as exc:
        raise LoggerDefinitionError(
            f"unsupported BitWise operation {operation}") from exc


def _translate_expression(source: str) -> str:
    result = source.replace("&&", " and ").replace("||", " or ")
    result = _NOT.sub(" not ", result)
    result = re.sub(r"\bif\s*\(", "_if(", result, flags=re.IGNORECASE)
    return result.strip()


def _compile_expression(source: str) -> Callable[[float], float]:
    try:
        tree = ast.parse(_translate_expression(source), mode="eval")
    except (SyntaxError, ValueError) as exc:
        raise LoggerDefinitionError(f"unsupported conversion expression {source!r}: {exc}") from exc

    binary = {
        ast.Add: lambda a, b: a + b,
        ast.Sub: lambda a, b: a - b,
        ast.Mult: lambda a, b: a * b,
        ast.Div: lambda a, b: a / b,
        ast.Mod: lambda a, b: a % b,
    }
    compare = {
        ast.Lt: lambda a, b: a < b,
        ast.LtE: lambda a, b: a <= b,
        ast.Gt: lambda a, b: a > b,
        ast.GtE: lambda a, b: a >= b,
        ast.Eq: lambda a, b: a == b,
        ast.NotEq: lambda a, b: a != b,
    }

    def evaluate(node: ast.AST, x: float):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return node.value
        if isinstance(node, ast.Name) and node.id.lower() == "x":
            return x
        if isinstance(node, ast.BinOp) and type(node.op) in binary:
            return binary[type(node.op)](evaluate(node.left, x), evaluate(node.right, x))
        if isinstance(node, ast.UnaryOp):
            value = evaluate(node.operand, x)
            if isinstance(node.op, ast.USub):
                return -value
            if isinstance(node.op, ast.UAdd):
                return value
            if isinstance(node.op, ast.Not):
                return float(not value)
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            values = [bool(evaluate(item, x)) for item in node.values]
            return float(all(values) if isinstance(node.op, ast.And) else any(values))
        if isinstance(node, ast.Compare):
            left = evaluate(node.left, x)
            for operator_node, comparator in zip(node.ops, node.comparators):
                operation = compare.get(type(operator_node))
                right = evaluate(comparator, x)
                if operation is None or not operation(left, right):
                    return 0.0
                left = right
            return 1.0
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id.lower()
            if name == "_if" and len(node.args) == 3:
                branch = node.args[1] if evaluate(node.args[0], x) else node.args[2]
                return evaluate(branch, x)
            args = [evaluate(item, x) for item in node.args]
            if name == "bitwise" and len(args) in (2, 3):
                return _bitwise(*args)
            if name == "abs" and len(args) == 1:
                return abs(args[0])
            if name == "min" and args:
                return min(args)
            if name == "max" and args:
                return max(args)
        raise LoggerDefinitionError(f"unsupported conversion expression {source!r}")

    # Validate the complete AST now; no definition-supplied code reaches eval().
    for node in ast.walk(tree):
        if isinstance(node, (ast.Attribute, ast.Subscript, ast.Lambda, ast.Dict,
                             ast.List, ast.Set, ast.Tuple, ast.NamedExpr)):
            raise LoggerDefinitionError(f"unsupported conversion expression {source!r}")

    def convert(x: float) -> float:
        try:
            return float(evaluate(tree.body, float(x)))
        except LoggerDefinitionError:
            raise
        except (ArithmeticError, TypeError, ValueError) as exc:
            raise LoggerDefinitionError(f"conversion {source!r} failed: {exc}") from exc

    # Walking through every node catches unknown names/calls without evaluating
    # expressions whose valid domain excludes zero.
    def validate(node: ast.AST) -> None:
        if isinstance(node, ast.Expression):
            validate(node.body)
            return
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return
        if isinstance(node, ast.Name) and node.id.lower() == "x":
            return
        if isinstance(node, ast.BinOp) and type(node.op) in binary:
            validate(node.left)
            validate(node.right)
            return
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd, ast.Not)):
            validate(node.operand)
            return
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            for item in node.values:
                validate(item)
            return
        if isinstance(node, ast.Compare) and all(type(item) in compare for item in node.ops):
            validate(node.left)
            for item in node.comparators:
                validate(item)
            return
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id.lower()
            valid_arity = ((name == "_if" and len(node.args) == 3)
                           or (name == "bitwise" and len(node.args) in (2, 3))
                           or (name == "abs" and len(node.args) == 1)
                           or (name in {"min", "max"} and bool(node.args)))
            if valid_arity and not node.keywords:
                for item in node.args:
                    validate(item)
                return
        raise LoggerDefinitionError(f"unsupported conversion expression {source!r}")

    validate(tree)
    return convert


@dataclass(frozen=True)
class LoggerParameter:
    id: str
    name: str
    description: str
    unit: str
    address: int
    length: int
    signed: bool
    endian: str
    conversion: Callable[[float], float]
    expression: str
    format: str
    replacements: tuple[tuple[str, str], ...]
    gauge_min: float | None = None
    gauge_max: float | None = None
    gauge_step: float | None = None
    group: str | None = None
    subgroup: str | None = None
    groupsize: int | None = None
    storage_type: str = "uint8"
    bit: int | None = None

    @property
    def fmt(self) -> str:
        decimals = len(self.format.partition(".")[2])
        return "{:." + str(decimals) + "f}" if decimals else "{:.0f}"

    def convert(self, raw: float) -> float:
        if self.bit is not None:
            raw = 1 if int(raw) & (1 << self.bit) else 0
        return self.conversion(raw)

    def with_address(self, address: int) -> "LoggerParameter":
        if not 0 <= int(address) <= 0xFFFFFF:
            raise LoggerDefinitionError(f"invalid absolute address {address!r}")
        return replace(self, address=int(address))

    def parse(self, block: bytes, block_start: int, wire_endian: str | None = None):
        wire_endian = wire_endian or self.endian
        if wire_endian not in {"little", "big"}:
            raise LoggerDefinitionError(f"unsupported wire endian {wire_endian!r}")
        offset = self.address - int(block_start)
        if offset < 0 or offset + self.length > len(block):
            return None
        raw_bytes = bytes(block[offset:offset + self.length])
        if self.storage_type == "float":
            raw = struct.unpack("<f" if wire_endian == "little" else ">f", raw_bytes)[0]
        else:
            raw = int.from_bytes(raw_bytes, wire_endian, signed=self.signed)
        return self.convert(raw)

    def display(self, value) -> str:
        if value is None:
            return "—"
        decimals = len(self.format.partition(".")[2])
        rendered = f"{float(value):.{decimals}f}"
        for original, replacement_text in self.replacements:
            if original == rendered:
                return replacement_text
            try:
                matches = float(original) == float(value)
            except ValueError:
                matches = False
            if matches:
                return replacement_text
        return rendered


@dataclass(frozen=True)
class _ParameterTemplate:
    parameter: LoggerParameter
    ecu_addresses: tuple[tuple[tuple[str, ...], int, int | None], ...]

    def resolve(self, ecu_id: str) -> LoggerParameter | None:
        for ids, address, bit in self.ecu_addresses:
            if ecu_id in ids:
                return replace(self.parameter, address=address, bit=bit)
        return None


@dataclass(frozen=True)
class LoggerDefinition:
    version: str
    protocol_id: str
    baud: int
    module_address: int
    parameters: tuple[LoggerParameter, ...]
    _templates: tuple[_ParameterTemplate, ...]

    def parameters_for(self, ecu_id: str) -> tuple[LoggerParameter, ...]:
        resolved = (item.resolve(str(ecu_id)) for item in self._templates)
        return tuple(item for item in resolved if item is not None)


def _strip_doctype(text: str) -> str:
    if re.search(r"<!ENTITY\b", text, flags=re.IGNORECASE):
        raise LoggerDefinitionError("XML entity declarations are not supported")
    match = re.search(r"<!DOCTYPE\b", text, flags=re.IGNORECASE)
    if match is None:
        return text
    quote = None
    subset_depth = 0
    index = match.start()
    while index < len(text):
        char = text[index]
        if quote:
            if char == quote:
                quote = None
        elif char in {'\"', "'"}:
            quote = char
        elif char == "[":
            subset_depth += 1
        elif char == "]" and subset_depth:
            subset_depth -= 1
        elif char == ">" and not subset_depth:
            return text[:match.start()] + text[index + 1:]
        index += 1
    raise LoggerDefinitionError("unterminated XML doctype")


def _required(element: ET.Element, attribute: str, owner: str) -> str:
    value = element.get(attribute)
    if value is None or value == "":
        raise LoggerDefinitionError(f"{owner} requires {attribute!r}")
    return value


def _number(value: str | None, name: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise LoggerDefinitionError(f"invalid {name} {value!r}") from exc


def _hex_number(value: str, name: str) -> int:
    """Parse hexadecimal values: the ``0x`` prefix is optional."""
    try:
        return int(value.replace(" ", ""), 16)
    except ValueError as exc:
        raise LoggerDefinitionError(f"invalid {name} {value!r}") from exc


def _parse_content(raw: bytes) -> LoggerDefinition:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise LoggerDefinitionError(f"logger definition is not UTF-8: {exc}") from exc
    try:
        root = ET.fromstring(_strip_doctype(text))
    except ET.ParseError as exc:
        raise LoggerDefinitionError(f"cannot parse logger definition: {exc}") from exc
    if root.tag != "logger":
        raise LoggerDefinitionError("root element must be <logger>")
    protocols = [item for item in root.findall("./protocols/protocol") if item.get("id") == "DS2"]
    if len(protocols) != 1:
        raise LoggerDefinitionError("logger definition must contain exactly one DS2 protocol")
    protocol = protocols[0]
    framing = {}
    for attribute in ("databits", "stopbits", "parity", "connect_timeout", "send_timeout"):
        value = _required(protocol, attribute, "DS2 protocol")
        try:
            framing[attribute] = int(value, 10)
        except ValueError as exc:
            raise LoggerDefinitionError(
                f"DS2 protocol has an invalid {attribute}") from exc
    try:
        baud = int(_required(protocol, "baud", "DS2 protocol"), 0)
    except ValueError as exc:
        raise LoggerDefinitionError("DS2 protocol has an invalid baud") from exc
    modules = [item for item in protocol.findall("./transports/transport/module")
               if item.get("id") == "ecu"]
    if len(modules) != 1:
        raise LoggerDefinitionError("DS2 protocol must contain exactly one ecu module")
    module_address = _hex_number(
        _required(modules[0], "address", "ecu module"), "ecu module address")
    if (
        baud != 9600
        or framing["databits"] != 8
        or framing["stopbits"] != 1
        or framing["parity"] != 2
        or module_address != 0x12
    ):
        raise LoggerDefinitionError(
            "logger definition transport is not compatible with MS41 DS2")

    templates = []
    for element in protocol.findall("./ecuparams/ecuparam"):
        parameter_id = _required(element, "id", "ecuparam")
        _required(element, "target", f"ecuparam {parameter_id}")
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]*", parameter_id) is None:
            raise LoggerDefinitionError(f"ecuparam id {parameter_id!r} is not a valid XML ID")
        name = _required(element, "name", f"ecuparam {parameter_id}")
        description = _required(element, "desc", f"ecuparam {parameter_id}")
        conversions = element.findall("./conversions/conversion")
        if not conversions:
            raise LoggerDefinitionError(f"ecuparam {parameter_id!r} has no conversion")
        conversion_element = conversions[0]
        expression = _required(conversion_element, "expr", f"conversion {parameter_id}")
        rr_format = _required(conversion_element, "format", f"conversion {parameter_id}")
        if "units" not in conversion_element.attrib:
            raise LoggerDefinitionError(f"conversion {parameter_id} requires 'units'")
        unit = conversion_element.get("units") or ""
        if rr_format not in _FORMATS:
            raise LoggerDefinitionError(f"ecuparam {parameter_id!r} has unsupported format {rr_format!r}")
        storage_type = conversion_element.get("storagetype") or "uint8"
        if storage_type not in _WIDTHS:
            raise LoggerDefinitionError(
                f"ecuparam {parameter_id!r} has unsupported storage type {storage_type!r}")
        length, signed = _WIDTHS[storage_type]
        endian = conversion_element.get("endian") or "big"
        if endian not in {"little", "big"}:
            raise LoggerDefinitionError(f"ecuparam {parameter_id!r} has invalid endian {endian!r}")
        replacements = tuple(
            (_required(item, "value", f"replace {parameter_id}"),
             _required(item, "with", f"replace {parameter_id}"))
            for item in conversion_element.findall("replace")
        )
        try:
            groupsize = int(element.get("groupsize")) if element.get("groupsize") else None
        except ValueError as exc:
            raise LoggerDefinitionError(f"ecuparam {parameter_id!r} has invalid groupsize") from exc
        base = LoggerParameter(
            id=parameter_id, name=name, description=description, unit=unit,
            address=0, length=length, signed=signed, endian=endian,
            conversion=_compile_expression(expression), expression=expression,
            format=rr_format, replacements=replacements,
            gauge_min=_number(conversion_element.get("gauge_min"), "gauge_min"),
            gauge_max=_number(conversion_element.get("gauge_max"), "gauge_max"),
            gauge_step=_number(conversion_element.get("gauge_step"), "gauge_step"),
            group=element.get("group"), subgroup=element.get("subgroup"),
            groupsize=groupsize, storage_type=storage_type,
        )
        mappings = []
        seen_ecus = set()
        for ecu in element.findall("ecu"):
            ids_text = _required(ecu, "id", f"ecu mapping {parameter_id}")
            ids = tuple(ids_text.split(","))  # Definition membership is exact and does not trim.
            if any(not item for item in ids):
                raise LoggerDefinitionError(f"ecuparam {parameter_id!r} has an empty ECU id")
            duplicate = seen_ecus.intersection(ids)
            if duplicate:
                raise LoggerDefinitionError(
                    f"ecuparam {parameter_id!r} maps ECU id {next(iter(duplicate))!r} twice")
            seen_ecus.update(ids)
            addresses = ecu.findall("address")
            if len(addresses) != 1:
                raise LoggerDefinitionError(
                    f"ecuparam {parameter_id!r} requires exactly one address per ECU mapping")
            address_element = addresses[0]
            bit_text = address_element.get("bit")
            try:
                bit = int(bit_text) if bit_text is not None else None
            except ValueError as exc:
                raise LoggerDefinitionError(
                    f"ecuparam {parameter_id!r} has an invalid address bit") from exc
            if bit is not None and not 0 <= bit <= 31:
                raise LoggerDefinitionError(
                    f"ecuparam {parameter_id!r} has an invalid address bit")
            if address_element.get("length") not in (None, "1"):
                raise LoggerDefinitionError(
                    f"ecuparam {parameter_id!r} address ranges are not supported")
            address = _hex_number(
                (address_element.text or "").strip(),
                f"ecuparam {parameter_id!r} address",
            )
            if not 0 <= address <= 0xFFFFFF:
                raise LoggerDefinitionError(
                    f"ecuparam {parameter_id!r} address is outside 24-bit CPU memory")
            mappings.append((ids, address, bit))
        if not mappings:
            raise LoggerDefinitionError(f"ecuparam {parameter_id!r} has no ECU mappings")
        templates.append(_ParameterTemplate(base, tuple(mappings)))
    if not templates:
        raise LoggerDefinitionError("DS2 protocol contains no ecuparams")
    return LoggerDefinition(
        version=root.get("version") or "", protocol_id="DS2", baud=baud,
        module_address=module_address,
        parameters=tuple(item.parameter for item in templates),
        _templates=tuple(templates),
    )


def parse_logger_definition(path: str | Path) -> LoggerDefinition:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise LoggerDefinitionError(f"cannot read logger definition {path}: {exc}") from exc
    return _parse_content(raw)


@functools.lru_cache(maxsize=2)
def _load_content(raw: bytes) -> LoggerDefinition:
    return _parse_content(raw)


def load_logger_definition(path: str | Path) -> LoggerDefinition:
    """Load a definition, reusing an immutable parse only when its bytes match."""
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise LoggerDefinitionError(f"cannot read logger definition {path}: {exc}") from exc
    return _load_content(raw)
