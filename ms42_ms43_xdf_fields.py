"""Configuration fields in MS42/MS43 community patch lists, in full BIN coordinates."""

import ast
from hashlib import sha256
from math import isfinite

from ms42_ms43_image import FULL_SIZE, PROFILES, image_identity
from ms42_ms43_xdf import _number

# The full initializer and partial reload together identify the ROM table actually consumed.
_CAN_INITIALIZERS = {
    "MS42": (("0110C6", "0110C7"), 0x1369E, "e6d6c3796753f3eae2f8e7f72870efd236e5f070743eda83782b33870d0a60eb"),
    "MS43": (("430069",), 0x5437C, "7d2595ffb37ab2238100b8c168aedccd98f7f187c5e1ab21261f990199fa0a90"),
}


def _linear(node, variable):
    """Resolve only affine arithmetic, so every offered conversion can be reversed."""
    if isinstance(node, ast.Name) and node.id.casefold() == variable:
        return 1.0, 0.0
    if isinstance(node, ast.Constant) and type(node.value) in (float, int):
        return 0.0, float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        a, b = _linear(node.operand, variable)
        return (-a, -b) if isinstance(node.op, ast.USub) else (a, b)
    if isinstance(node, ast.BinOp):
        a, b = _linear(node.left, variable)
        c, d = _linear(node.right, variable)
        if isinstance(node.op, ast.Add):
            return a + c, b + d
        if isinstance(node.op, ast.Sub):
            return a - c, b - d
        if isinstance(node.op, ast.Mult) and not (a and c):
            return a * d + b * c, b * d
        if isinstance(node.op, ast.Div) and c == 0 and d:
            return a / d, b / d
    raise ValueError("Unsupported XDF conversion")


def _scale(node):
    maths = node.findall("MATH")
    if not maths:
        return 1.0, 0.0
    math = maths[0]
    variables = list(math)
    if (len(maths) != 1 or set(math.attrib) != {"equation"} or len(variables) != 1
            or variables[0].tag != "VAR" or set(variables[0].attrib) != {"id"}):
        raise ValueError("Cell-specific or external XDF conversion")
    variable = variables[0].get("id")
    equation = math.get("equation").strip()
    if not variable.isidentifier() or len(equation) > 4096:
        raise ValueError("Invalid XDF conversion")
    a, b = _linear(ast.parse(equation, mode="eval").body, variable.casefold())
    if not a or not isfinite(a) or not isfinite(b):
        raise ValueError("XDF conversion has no finite inverse")
    return a, b


def _layout(node, base, size, rows, columns):
    mem = node.find("EMBEDDEDDATA")
    if mem is None or "mmedaddress" not in mem.attrib:
        raise ValueError("Missing XDF data address")
    valid = {"mmedaddress", "mmedtypeflags", "mmedelementsizebits", "mmedrowcount",
             "mmedcolcount", "mmedmajorstridebits", "mmedminorstridebits"}
    flags = _number(mem.get("mmedtypeflags"))
    bits = _number(mem.get("mmedelementsizebits"), 8)
    if set(mem.attrib) - valid or flags & ~7 or bits not in (8, 16, 32):
        raise ValueError("Unsupported XDF storage type")
    if min(rows, columns) < 1 or rows * columns > size:
        raise ValueError("Invalid XDF field dimensions")
    width = bits // 8
    major = _number(mem.get("mmedmajorstridebits")) or bits
    minor = _number(mem.get("mmedminorstridebits")) or bits
    if min(major, minor) < bits or major % 8 or minor % 8:
        raise ValueError("Unsupported XDF storage stride")
    # The minor stride steps from the LAST element of one line to the next line.
    along = major // 8
    across = ((rows if flags & 4 else columns) - 1) * along + minor // 8
    row_step, col_step = (along, across) if flags & 4 else (across, along)
    address = _number(mem.get("mmedaddress")) + base
    end = address + (rows - 1) * row_step + (columns - 1) * col_step + width
    if address < 0 or end > size:
        raise ValueError("XDF field falls outside its declared image")
    slope, intercept = _scale(node)
    raw_min = -(1 << (bits - 1)) if flags & 1 else 0
    raw_max = raw_min + (1 << bits) - 1
    if not all(isfinite(raw * slope + intercept) for raw in (raw_min, raw_max)):
        raise ValueError("XDF conversion overflows the numeric range")

    def bound(tag):
        value = node.findtext(tag)
        if value is None:
            return None
        number = float(value)
        if not isfinite(number):
            raise ValueError("Nonfinite XDF field bound")
        return number
    minimum, maximum = bound("rangelow"), bound("rangehigh")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError("Invalid XDF field bounds")
    return dict(address=address, end=end, width=width, rows=rows, columns=columns,
                row_step=row_step, col_step=col_step, endian="little" if flags & 2 else "big",
                signed=bool(flags & 1), mask=0, slope=slope, intercept=intercept,
                units=node.findtext("units", ""), decimals=max(0, min(12, _number(node.findtext("decimalpl"), 2))),
                minimum=minimum, maximum=maximum)


def _shape(item):
    z = item.find("XDFAXIS[@id='z']") if item.tag == "XDFTABLE" else item
    mem = None if z is None else z.find("EMBEDDEDDATA")
    if mem is None:
        raise ValueError("Missing XDF field storage")
    rows = _number(mem.get("mmedrowcount"), _number(item.findtext("XDFAXIS[@id='y']/indexcount"), 1))
    cols = _number(mem.get("mmedcolcount"), _number(item.findtext("XDFAXIS[@id='x']/indexcount"), 1))
    return z, rows, cols


def _categories(item, xdf):
    return [xdf.categories.get(_number(c.get("category")), "") for c in item.findall("CATEGORYMEM")]


def _read_only(storage, categories, profile):
    if any(c.casefold() == "ecu information" for c in categories):
        return "ECU information is display-only."
    first = profile.calibration_offset
    if storage["address"] < first or storage["end"] > first + profile.partial_size:
        return "This field lies outside the calibration."
    return ""


def _axis(axis, count, xdf, links, categories):
    if axis is None or count == 1:
        return None
    if _number(axis.findtext("indexcount"), count) != count:
        raise ValueError("XDF axis count differs from its table")
    embed = axis.find("embedinfo")
    kind = _number(embed.get("type")) if embed is not None else 0
    units = axis.findtext("units", "")
    title = units or axis.get("id", "").upper()
    if kind == 3:
        targets = links.get(_number(embed.get("linkobjid")), [])
        if len(targets) != 1:
            raise ValueError("Missing or ambiguous XDF axis link")
        target = targets[0]
        node, rows, columns = _shape(target)
        if rows * columns != count or min(rows, columns) != 1:
            raise ValueError("Linked XDF axis is not a matching curve")
        categories = categories + _categories(target, xdf)
        title = target.findtext("title", title)
    elif kind == 1:
        node, rows, columns = axis, 1, count
    elif kind:
        raise ValueError("Unsupported XDF axis link mode")
    else:
        labels = axis.findall("LABEL")
        if not labels:
            if axis.find("EMBEDDEDDATA[@mmedaddress]") is not None:
                raise ValueError("XDF axis address has no source mode")
            return None
        values = {_number(label.get("index"), i): label.get("value", "") for i, label in enumerate(labels)}
        if len(labels) != count or set(values) != set(range(count)):
            raise ValueError("Incomplete XDF axis labels")
        return dict(title=title, units=units, read_only=True, labels=[values[i] for i in range(count)])
    data = _layout(node, xdf.base, xdf.size, rows, columns)
    return dict(title=title, units=units or data["units"], data=data,
                read_only=bool(_read_only(data, categories, PROFILES[xdf.family])))


def _can_view(xdf, field):
    if (xdf.base != 0 or xdf.software not in _CAN_INITIALIZERS[xdf.family][0]
            or field["kind"] != "table" or any("data" in axis for axis in field["axes"].values())):
        return None
    data = field["data"]
    if (data["signed"], data["mask"], data["endian"], data["intercept"]) != (False, 0, "big", 0):
        return None
    start = 0x52000 if xdf.family == "MS42" else 0x6C494
    layout = tuple(data[key] for key in ("address", "rows", "columns", "width", "row_step", "col_step", "slope"))
    if layout == (start, 16, 15, 1, 1, 16, 1):
        return "init"
    if layout == (start + 2, 1, 15, 2, 2, 16, 1 / 32):
        return "arbid"
    return None


def build_fields(xdf):
    """Compile fields from one admitted patch list; IDs are local to that source."""
    if xdf.size != FULL_SIZE:
        raise ValueError("Patch list configuration requires a full image definition")
    links = {}
    for item in xdf.entries:
        if item.tag == "XDFTABLE" and item.get("uniqueid"):
            try:
                identifier = _number(item.get("uniqueid"))
            except ValueError:
                continue
            links.setdefault(identifier, []).append(item)
    fields = []
    for i, item in enumerate((*xdf.entries, *xdf.flags)):
        field = dict(id=f"field-{i}", title=item.findtext("title") or "Unnamed field",
                     kind={"XDFTABLE": "table", "XDFFLAG": "flag", "XDFCONSTANT": "scalar"}[item.tag],
                     category="", categories=[],
                     description=item.findtext("description", ""), rows=0, columns=0,
                     units="", read_only=True, reason="", axes={})
        try:
            categories = _categories(item, xdf)
            field.update(category=next(iter(categories), ""), categories=categories)
            z, rows, columns = _shape(item)
            data = _layout(z, xdf.base, xdf.size, rows, columns)
            if item.tag == "XDFFLAG":
                mask = _number(item.findtext("mask"))
                if mask <= 0 or mask & (mask - 1) or mask.bit_length() > data["width"] * 8:
                    raise ValueError("Unsupported XDF flag mask")
                data.update(mask=mask, signed=False)
            axes = {}
            for axis_name, count in (("x", columns), ("y", rows)):
                axis = _axis(item.find(f"XDFAXIS[@id='{axis_name}']"), count, xdf, links, categories)
                if axis is not None:
                    axes[axis_name] = axis
            reason = _read_only(data, categories, PROFILES[xdf.family])
            field.update(data=data, axes=axes, rows=rows, columns=columns, units=data["units"],
                         read_only=bool(reason), reason=reason)
            view = _can_view(xdf, field)
            if view:
                field.update(can_view=view, can_family=xdf.family, read_only=False, reason="")
                if view == "init":
                    field.update(read_only_rows=[15], description="Raw CAN initialization bytes. The last row is padding.")
                else:
                    field["description"] = ("Upper 11 arbitration ID bits. The remaining bits are preserved "
                                            "when editing a standard or extended identifier.")
                    data.update(minimum=0, maximum=2047, decimals=0)
        except (ValueError, SyntaxError, TypeError, ArithmeticError, RecursionError) as error:
            field["reason"] = str(error)
        fields.append(field)
    return fields


def field_summary(field):
    return {k: v for k, v in field.items() if k not in ("data", "axes", "can_family")}


def _can_image_reason(field, image):
    if not field.get("can_view"):
        return ""
    family = field["can_family"]
    revisions, start, digest = _CAN_INITIALIZERS[family]
    if len(image) == FULL_SIZE:
        identity = image_identity(image)
        if (identity["family"] == family and identity["software_id"] in revisions
                and sha256(image[start:start + 120]).hexdigest() == digest):
            return ""
    return "This BIN does not match the verified CAN initializer for this table."


def field_write_mask(field, axis, row, layout):
    if axis == "z" and field.get("can_view") == "arbid":
        return 0xFFE0
    if axis == "z" and field.get("can_view") == "init":
        # The low bits of the second LAR byte and the configuration byte are reserved.
        return {5: 0xF8, 6: 0xFC}.get(row, 0)
    return layout["mask"]


def _address(data, image, row, column):
    if (type(row) is not int or type(column) is not int
            or not 0 <= row < data["rows"] or not 0 <= column < data["columns"]):
        raise ValueError("XDF field cell index is outside the table")
    address = data["address"] + row * data["row_step"] + column * data["col_step"]
    if len(image) != FULL_SIZE or address < 0 or address + data["width"] > len(image):
        raise ValueError("XDF field requires a matching full image")
    return address


def _value(data, image, row, column):
    address = _address(data, image, row, column)
    raw = int.from_bytes(image[address:address + data["width"]], data["endian"], signed=data["signed"])
    if data["mask"]:
        return bool(raw & data["mask"])
    return raw * data["slope"] + data["intercept"]


def read_field(field, image):
    if "data" not in field:
        raise ValueError(field["reason"])
    data = field["data"]
    values = [[_value(data, image, row, col) for col in range(data["columns"])] for row in range(data["rows"])]
    if field.get("can_view") == "arbid":
        values = [[int(v) for v in values[0]]]
    axes = {}
    for name, axis in field["axes"].items():
        if "labels" in axis:
            labels = axis["labels"]
        else:
            layout = axis["data"]
            labels = [_value(layout, image, r, c) for r in range(layout["rows"]) for c in range(layout["columns"])]
        axes[name] = dict(title=axis["title"], units=axis["units"], read_only=field["read_only"] or axis["read_only"],
                          values=labels)
    summary = field_summary(field)
    reason = _can_image_reason(field, image)
    if reason:
        summary.update(read_only=True, reason=reason)
    return dict(**summary, values=values, axes=axes, minimum=data["minimum"],
                maximum=data["maximum"], decimals=data["decimals"])


def write_field(field, image, value, *, row=0, column=0, axis="z"):
    """Set one engineering value and return its file offset and resulting encoded word."""
    if field["read_only"]:
        raise ValueError(field["reason"])
    reason = _can_image_reason(field, image)
    if reason:
        raise ValueError(reason)
    data = field["data"]
    if axis != "z":
        coord = field["axes"].get(axis)
        if coord is None or coord["read_only"]:
            raise ValueError("XDF axis is unavailable or display-only")
        index = column if axis == "x" else row
        if (axis == "x" and row != 0) or (axis == "y" and column != 0):
            raise ValueError("XDF axis requires a single index")
        data = coord["data"]
        row, column = (0, index) if data["rows"] == 1 else (index, 0)
    address = _address(data, image, row, column)
    if axis == "z" and row in field.get("read_only_rows", ()):
        raise ValueError("This row is reserved CAN initialization padding")
    if data["mask"]:
        if isinstance(value, str):
            value = value.strip().casefold()
            value = {"true": 1, "false": 0, "1": 1, "0": 0}.get(value)
        if value not in (0, 1):
            raise ValueError("Flag value must be on or off")
        previous = int.from_bytes(image[address:address + data["width"]], data["endian"])
        raw = previous | data["mask"] if value else previous & ~data["mask"]
    else:
        try:
            number = float(value)
        except (ValueError, TypeError, OverflowError) as error:
            raise ValueError("XDF value must be a finite number") from error
        if (not isfinite(number) or data["minimum"] is not None and number < data["minimum"]
                or data["maximum"] is not None and number > data["maximum"]):
            raise ValueError("XDF value is outside the declared range")
        scaled = (number - data["intercept"]) / data["slope"]
        if not isfinite(scaled):
            raise ValueError("XDF value is outside its storage range")
        if field.get("can_view") and not number.is_integer():
            raise ValueError("CAN initialization bytes and arbitration IDs must be integers")
        raw = round(scaled)
        mask = field_write_mask(field, axis, row, data)
        if mask:
            previous = int.from_bytes(image[address:address + data["width"]], data["endian"])
            if field["can_view"] == "arbid":
                raw = raw & mask | previous & ~mask
            elif (raw ^ previous) & ~mask:
                raise ValueError("Keep the reserved bits of this CAN register byte unchanged")
    try:
        encoded = raw.to_bytes(data["width"], data["endian"], signed=data["signed"])
    except OverflowError as error:
        raise ValueError("XDF value is outside its storage range") from error
    image[address:address + len(encoded)] = encoded
    return address, encoded
