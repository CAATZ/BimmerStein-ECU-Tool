import json
from hashlib import sha256
from xml.etree import ElementTree as ET

import pytest

from ms42_ms43_image import FULL_SIZE
from ms42_ms43_xdf import XDFDefinitions
from ms42_ms43_xdf_fields import build_fields, read_field, write_field
import ms42_ms43_xdf_fields as codec
from tests.test_ms42_ms43_image import synthetic_image


def fields(*items):
    root = ET.fromstring("""<XDFFORMAT><XDFHEADER><deftitle>MS43 430069</deftitle>
      <REGION size="0x80000"/><BASEOFFSET offset="0x70000"/>
      <CATEGORY index="0" name="ECU Information"/><CATEGORY index="1" name="Cluster LEDs"/>
      </XDFHEADER></XDFFORMAT>""")
    for item in items:
        root.append(ET.fromstring(item))
    return {f["title"]: f for f in build_fields(XDFDefinitions(root))}


def test_cluster_table_linked_axis_static_labels_and_source_units():
    definitions = fields("""<XDFTABLE uniqueid="0x45"><title>RPM</title>
      <XDFAXIS id="z"><EMBEDDEDDATA mmedaddress="8" mmedrowcount="2"
        mmedelementsizebits="16" mmedtypeflags="2"/><units>rpm</units>
      <MATH equation="X*0.5"><VAR id="X"/></MATH></XDFAXIS></XDFTABLE>""",
      """<XDFTABLE><title>Cluster LEDs</title><CATEGORYMEM category="2"/>
      <XDFAXIS id="x"><indexcount>2</indexcount><embedinfo type="3" linkobjid="0x45"/></XDFAXIS>
      <XDFAXIS id="y"><indexcount>2</indexcount><LABEL index="1" value="Warm"/>
      <LABEL index="0" value="Cold"/></XDFAXIS>
      <XDFAXIS id="z"><EMBEDDEDDATA mmedaddress="32" mmedrowcount="2" mmedcolcount="2"
      mmedtypeflags="3" mmedelementsizebits="16"/><units>deg</units>
      <MATH equation="(1-(V*0.5-2))*3"><VAR id="V"/></MATH></XDFAXIS></XDFTABLE>""")
    image = bytearray(FULL_SIZE)
    image[0x70008:0x7000c] = bytes.fromhex("a00fc012")
    field = definitions["Cluster LEDs"]
    result = json.loads(json.dumps(read_field(field, image)))
    assert result["units"] == "deg"
    assert result["category"] == "Cluster LEDs"
    assert result["axes"]["x"]["values"] == [2000, 2400]
    assert result["axes"]["y"]["values"] == ["Cold", "Warm"]
    write_field(field, image, 2700, column=1, axis="x")
    assert read_field(definitions["RPM"], image)["values"] == [[2000], [2700]]
    assert write_field(field, image, 15, row=1) == (0x70024, bytes.fromhex("fcff"))
    assert read_field(field, image)["values"] == [[9, 9], [15, 9]]
    expected = bytes(image)
    for kwargs in (dict(row=-1), dict(column=2), dict(row=1, axis="y"), dict(column=2, axis="x")):
        with pytest.raises(ValueError):
            write_field(field, image, 30, **kwargs)
    assert expected == bytes(image)


def test_uif_and_can_layouts_and_information_is_read_only():
    def table(name, rows, columns, width, flag, minor, extra=""):
        return f"""<XDFTABLE><title>{name}</title>{extra}<XDFAXIS id="z"><EMBEDDEDDATA
          mmedaddress="0" mmedelementsizebits="{width}" mmedtypeflags="{flag}"
          mmedrowcount="{rows}" mmedcolcount="{columns}" mmedminorstridebits="{minor}"/>
          </XDFAXIS></XDFTABLE>"""
    defs = fields(table("UIF", 14, 13, 8, 0, 272, '<CATEGORYMEM category="1"/>'),
                  table("CAN init", 16, 15, 8, 4, 0),
                  table("CAN IDs", 1, 15, 16, 4, 128))
    image = bytearray(FULL_SIZE)
    image[0x70000:0x70100] = bytes(range(256))
    assert read_field(defs["UIF"], image)["values"][2][3] == 2 * 46 + 3
    with pytest.raises(ValueError, match="display-only"):
        write_field(defs["UIF"], image, 4)
    assert read_field(defs["CAN init"], image)["values"][3][4] == 4 * 16 + 3
    assert write_field(defs["CAN IDs"], image, 0x8899, column=3) == (0x70030, b"\x88\x99")
    assert read_field(defs["CAN IDs"], image)["values"][0][4] == 0x4041


def test_flag_scalar_ranges_and_unsupported_item_independence():
    defs = fields("""<XDFFLAG><title>LED</title><EMBEDDEDDATA mmedaddress="0"/>
                  <mask>0x04</mask></XDFFLAG>""",
                  """<XDFCONSTANT><title>Count</title><EMBEDDEDDATA mmedaddress="3"/>
                  <rangehigh>2</rangehigh></XDFCONSTANT>""",
                  """<XDFCONSTANT><title>Broken</title><EMBEDDEDDATA mmedaddress="2"/>
                  <MATH equation="X*X"><VAR id="X"/></MATH></XDFCONSTANT>""")
    image = bytearray(FULL_SIZE)
    image[0x70000] = 0xa2
    assert write_field(defs["LED"], image, True) == (0x70000, b"\xa6")
    assert read_field(defs["LED"], image)["values"] == [[True]]
    assert write_field(defs["LED"], image, "false") == (0x70000, b"\xa2")
    assert write_field(defs["Count"], image, "2") == (0x70003, b"\x02")
    original = bytes(image)
    for field, bad in (("Broken", 1), ("LED", "bad"), ("Count", 3), ("Count", -30), ("Count", "nan")):
        with pytest.raises(ValueError):
            write_field(defs[field], image, bad)
    assert defs["Broken"]["reason"]
    assert bytes(image) == original


def test_verified_can_alias_owns_only_id_bits_and_preserves_padding(monkeypatch):
    root = ET.fromstring("""<XDFFORMAT><XDFHEADER><deftitle>MS43 430069</deftitle><REGION size="0x80000"/>
      <CATEGORY index="0" name="ECU Information"/></XDFHEADER>
      <XDFTABLE><title>Initializer</title><CATEGORYMEM category="1"/><XDFAXIS id="z">
      <EMBEDDEDDATA mmedaddress="0x6c494" mmedrowcount="16" mmedcolcount="15" mmedtypeflags="4"/>
      </XDFAXIS></XDFTABLE><XDFTABLE><title>ID</title><CATEGORYMEM category="1"/><XDFAXIS id="z">
      <EMBEDDEDDATA mmedaddress="0x6c496" mmedrowcount="1" mmedcolcount="15" mmedelementsizebits="16"
      mmedminorstridebits="128" mmedtypeflags="4"/><MATH equation="X/32"><VAR id="X"/></MATH>
      </XDFAXIS></XDFTABLE></XDFFORMAT>""")
    xdf = XDFDefinitions(root)
    init, ids = build_fields(xdf)
    assert not init["read_only"] and not ids["read_only"]
    image = synthetic_image("MS43")
    offset = 0x5437c
    digest = sha256(image[offset:offset + 120]).hexdigest()
    monkeypatch.setitem(codec._CAN_INITIALIZERS, "MS43", (("430069",), offset, digest))
    slot = 14
    word = 0x6c496 + slot * 16
    image[word:word + 2] = b"\x22\x37"
    assert read_field(ids, image)["values"][0][slot] == 0x2237 >> 5
    assert write_field(ids, image, 17, column=slot) == (word, b"\x02\x37")
    assert read_field(init, image)["values"][3][slot] == 0x37
    write_field(init, image, 0x90, row=2, column=slot)
    assert read_field(ids, image)["values"][0][slot] == 0x9037 >> 5
    before = bytes(image)
    for bad in (-1, 2048, 17.5):
        with pytest.raises(ValueError):
            write_field(ids, image, bad, column=slot)
    with pytest.raises(ValueError, match="padding"):
        write_field(init, image, 16, row=15, column=4)
    with pytest.raises(ValueError, match="reserved bits"):
        write_field(init, image, image[0x6c494 + 6] ^ 1, row=6)
    assert bytes(image) == before
    image[offset] ^= 1
    assert read_field(init, image)["read_only"] and read_field(ids, image)["read_only"]
    with pytest.raises(ValueError, match="verified CAN initializer"):
        write_field(ids, image, 17)


@pytest.mark.parametrize("family, software, address", [("MS42", "0110C6", 0x52000), ("MS43", "430069", 0x6C494)])
def test_can_view_uses_storage_layout_across_metadata_updates(family, software, address):
    root = ET.fromstring(f"""<XDFFORMAT><XDFHEADER><deftitle>{family} {software}</deftitle>
      <REGION size="0x80000"/><CATEGORY index="9" name="Renamed information"/></XDFHEADER>
      <XDFTABLE uniqueid="0x900"><title>Renamed CAN IDs</title><description>Revised text</description>
      <CATEGORYMEM category="10"/><XDFAXIS id="x"><indexcount>15</indexcount></XDFAXIS>
      <XDFAXIS id="z"><EMBEDDEDDATA mmedaddress="{hex(address + 2)}" mmedrowcount="1" mmedcolcount="15"
      mmedelementsizebits="16" mmedminorstridebits="128" mmedtypeflags="4"/>
      <MATH equation="V * 0.03125"><VAR id="V"/></MATH></XDFAXIS></XDFTABLE></XDFFORMAT>""")
    table = root.find("XDFTABLE")
    x = table.find("XDFAXIS[@id='x']")
    for index in reversed(range(15)):
        ET.SubElement(x, "LABEL", index=str(index), value=f"Object {index + 1}")
    field, = build_fields(XDFDefinitions(root))
    assert field["can_view"] == "arbid" and not field["read_only"]
    assert field["axes"]["x"]["labels"] == [f"Object {i + 1}" for i in range(15)]
    mem = table.find("XDFAXIS[@id='z']/EMBEDDEDDATA")
    for attribute, changed in (("mmedaddress", hex(address)), ("mmedcolcount", "14"),
                               ("mmedminorstridebits", "144"), ("mmedtypeflags", "6")):
        original = mem.get(attribute)
        mem.set(attribute, changed)
        field, = build_fields(XDFDefinitions(root))
        assert field["read_only"] and "can_view" not in field
        mem.set(attribute, original)
    math = table.find("XDFAXIS[@id='z']/MATH")
    math.set("equation", "V / 16")
    field, = build_fields(XDFDefinitions(root))
    assert field["read_only"] and "can_view" not in field
    math.set("equation", "V / 32")
    for label in list(x.findall("LABEL")):
        x.remove(label)
    ET.SubElement(x, "embedinfo", type="1")
    ET.SubElement(x, "EMBEDDEDDATA", mmedaddress=hex(address + 2), mmedcolcount="15")
    field, = build_fields(XDFDefinitions(root))
    assert field["read_only"] and "can_view" not in field
