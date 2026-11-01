import pytest

from logger_definition import (
    BUNDLED_LOGGER_DEFINITION_NAME,
    LoggerDefinitionError,
    bundled_logger_definition_path,
    load_logger_definition,
    parse_logger_definition,
)


def _by_id(definition, ecu_id):
    return {parameter.id: parameter for parameter in definition.parameters_for(ecu_id)}


def _minimal_xml(expression="x", ecu_ids="1429861", address="0xE8D0", address_attrs=""):
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE logger SYSTEM "https://example.invalid/logger.dtd">
<logger version="test"><protocols>
  <protocol id="DS2" baud="9600" databits="8" stopbits="1" parity="2"
            connect_timeout="2000" send_timeout="55">
    <transports><transport id="iso9141" name="K-Line" desc="test">
      <module id="ecu" address="0x12" desc="ECU"/>
    </transport></transports>
    <ecuparams><ecuparam id="TEST" name="Test" desc="Test channel" target="1">
      <ecu id="{ecu_ids}"><address {address_attrs}>{address}</address></ecu>
      <conversions><conversion units="u" storagetype="uint8"
        expr="{expression}" format="0"/></conversions>
    </ecuparam></ecuparams>
  </protocol>
</protocols></logger>'''


def test_bundled_definition_owns_corrected_core_profile_and_cut_maps():
    path = bundled_logger_definition_path()
    assert path.name == BUNDLED_LOGGER_DEFINITION_NAME
    definition = parse_logger_definition(path)

    assert definition.protocol_id == "DS2"
    assert definition.baud == 9600
    assert definition.module_address == 0x12
    assert len(definition.parameters) == 95
    assert sum(parameter.id.startswith("BS_STD_") for parameter in definition.parameters) == 3
    assert sum(parameter.id.startswith("BS_WB_") for parameter in definition.parameters) == 3
    assert sum(parameter.id.startswith("BS_CUT_") for parameter in definition.parameters) == 54

    old = _by_id(definition, "1429861")
    ms411 = _by_id(definition, "1437806")
    ms412 = _by_id(definition, "1406464")
    ms413 = _by_id(definition, "SHINDE1")
    assert old["P13"].address == ms411["P13"].address == ms412["P13"].address == 0xE8D0
    assert old["P17"].address == 0xFB47
    assert ms411["P17"].address == ms412["P17"].address == ms413["P17"].address == 0xFC9D
    assert old["BS_CUT_LAUNCH_SWITCH"].address == 0x13020
    assert ms411["BS_CUT_LAUNCH_SWITCH"].address == 0x13710
    assert ms412["BS_CUT_LAUNCH_SWITCH"].address == 0x1352C
    assert ms413["BS_CUT_LAUNCH_SWITCH"].address == 0x147E0
    assert "BS_WB_AFR" not in old and ms413["BS_WB_AFR"].address == 0xE800
    assert definition.parameters_for("1438137") == ()


@pytest.mark.parametrize("ecu_id", ["1429861", "1437806", "1406464", "SHINDE1"])
def test_native_roughness_channels_preserve_signed_little_endian_words(ecu_id):
    definition = parse_logger_definition(bundled_logger_definition_path())
    params = _by_id(definition, ecu_id)
    for index in range(6):
        parameter = params[f"BS_ROUGHNESS_CYL{index + 1}"]
        assert parameter.name == f"Cylinder {index + 1} Roughness"
        assert (parameter.address, parameter.length, parameter.signed, parameter.endian) == (
            0xE854 + 2 * index, 2, True, "little")
        for value in (-32768, -256, -1, 0, 1, 32767):
            raw = value.to_bytes(2, "little", signed=True)
            assert parameter.display(parameter.parse(raw, parameter.address)) == str(value)
    for unverified_id in ("1405854", "1429373", "1432401", "1440176", "unknown"):
        assert not any(p.id.startswith("BS_ROUGHNESS_")
                       for p in definition.parameters_for(unverified_id))


@pytest.mark.parametrize("ecu_id,cut,launch", [
    ("1429861", 0x13015, 0x1302B), ("1437806", 0x13705, 0x1371B),
    ("1406464", 0x12A6A, 0x13537), ("SHINDE1", 0x12A6A, 0x147EB),
])
def test_v16_timing_and_recovery_channels_match_native_encoding(ecu_id, cut, launch):
    params = _by_id(parse_logger_definition(bundled_logger_definition_path()), ecu_id)
    offsets = {
        "RETARD_RANGE": cut, "IGN_ANGLE": cut + 1,
        "LAUNCH_FUEL_HYST_B": launch, "LAUNCH_FUEL_HYST_A": launch + 1,
        "LAUNCH_IGN_ANGLE": launch + 2, "LAUNCH_RETARD_RANGE": launch + 3,
        "LAUNCH_CUT_IGN_ANGLE": launch + 4,
        "RECOVERY_PENDING": 0xD802, "TIMING_STATE": 0xD803, "TIMING_SOURCE": 0xD804,
    }
    for suffix, address in offsets.items():
        assert params["BS_CUT_" + suffix].address == address
    for suffix in ("IGN_ANGLE", "LAUNCH_IGN_ANGLE", "LAUNCH_CUT_IGN_ANGLE"):
        parameter = params["BS_CUT_" + suffix]
        for raw, expected in ((0, "-23.625"), (63, "0.000"),
                              (64, "Invalid / disabled"), (255, "Off")):
            assert parameter.display(parameter.parse(bytes((raw,)), parameter.address)) == expected
    for suffix in ("RETARD_RANGE", "LAUNCH_RETARD_RANGE"):
        parameter = params["BS_CUT_" + suffix]
        assert parameter.display(parameter.convert(10)) == "320"
    for suffix in ("LAUNCH_FUEL_HYST_B", "LAUNCH_FUEL_HYST_A"):
        parameter = params["BS_CUT_" + suffix]
        assert parameter.display(parameter.convert(255)) == "Follow stock"
        assert parameter.display(parameter.convert(10)) == "320"
    state = params["BS_CUT_TIMING_STATE"]
    assert [state.display(state.convert(raw)) for raw in (0xC0, 0xC1, 0xC3)] == [
        "Inactive", "Eligible / inhibited", "Timing constraint active"]
    source = params["BS_CUT_TIMING_SOURCE"]
    assert [source.display(source.convert(raw)) for raw in range(4)] == [
        "None", "Fuel launch", "Ignition launch", "Standalone cut"]


def test_bundled_conversion_parse_format_replace_and_runtime_address_override():
    params = _by_id(parse_logger_definition(bundled_logger_definition_path()), "SHINDE1")

    load = params["E2"]
    assert (load.length, load.signed, load.endian, load.fmt) == (2, False, "little", "{:.1f}")
    assert load.parse(b"\x34\x12", load.address) == pytest.approx(0x1234 * 0.021195)
    assert load.parse(b"\x12\x34", load.address, wire_endian="big") == pytest.approx(
        0x1234 * 0.021195)

    closed = params["BS_STATE_CLOSED_THROTTLE"]
    assert closed.display(closed.parse(b"\x00", closed.address)) == "Active"
    assert closed.display(closed.parse(b"\x01", closed.address)) == "Inactive"

    fixed_ipw = params["BS_CUT_FIXED_IPW"]
    assert fixed_ipw.display(fixed_ipw.parse(b"\xff\xff", fixed_ipw.address)) == "Stock"
    assert fixed_ipw.display(fixed_ipw.parse(b"\x34\x12", fixed_ipw.address)) == "24.88"

    selected = params["BS_WB_INPUT"].with_address(0xFA98)
    assert selected.address == 0xFA98
    assert selected.display(selected.parse(b"\x00\x02", 0xFA98)) == "2.502"
    assert selected.parse(b"\x00", 0xFA98) is None


def test_load_cache_is_keyed_by_file_content(tmp_path):
    content = bundled_logger_definition_path().read_bytes()
    first_path = tmp_path / "first.xml"
    second_path = tmp_path / "second.xml"
    first_path.write_bytes(content)
    second_path.write_bytes(content)

    first = load_logger_definition(first_path)
    assert load_logger_definition(first_path) is first
    assert load_logger_definition(second_path) is first

    second_path.write_bytes(content.replace(b'version="1.0.0"', b'version="1.0.1"', 1))
    assert load_logger_definition(second_path) is not first


def test_parser_uses_exact_untrimmed_ecu_membership_and_never_fetches_dtd(tmp_path):
    path = tmp_path / "exact.xml"
    path.write_text(_minimal_xml(ecu_ids="1429861, 1437806"), encoding="utf-8")
    definition = parse_logger_definition(path)

    assert len(definition.parameters_for("1429861")) == 1
    assert definition.parameters_for("1437806") == ()
    assert len(definition.parameters_for(" 1437806")) == 1


def test_unspecified_endian_matches_xml_definition_big_endian_default(tmp_path):
    path = tmp_path / "default-endian.xml"
    path.write_text(
        _minimal_xml().replace('storagetype="uint8"', 'storagetype="uint16"'),
        encoding="utf-8",
    )
    parameter = parse_logger_definition(path).parameters_for("1429861")[0]

    assert parameter.endian == "big"
    assert parameter.parse(b"\x12\x34", parameter.address) == 0x1234


def test_xml_definition_bare_hex_addresses_are_not_read_as_decimal(tmp_path):
    path = tmp_path / "bare-hex.xml"
    path.write_text(
        _minimal_xml(address="DA2A").replace('address="0x12"', 'address="12"'),
        encoding="utf-8",
    )
    definition = parse_logger_definition(path)

    assert definition.module_address == 0x12
    assert definition.parameters_for("1429861")[0].address == 0xDA2A


def test_xml_definition_bitwise_operation_selector_is_honored(tmp_path):
    path = tmp_path / "bitwise.xml"
    path.write_text(_minimal_xml(expression="BitWise(1,x,2)"), encoding="utf-8")
    parameter = parse_logger_definition(path).parameters_for("1429861")[0]

    assert parameter.parse(b"\x02", parameter.address) == 3


@pytest.mark.parametrize("expression,raw,expected", [
    ("x != 0 && 100/x > 1", 0, 0),
    ("x != 0 && 100/x > 1", 2, 1),
    ("x == 0 || 100/x > 1", 0, 1),
    ("x == 0 || 100/x > 1", 200, 0),
    ("if(x != 0 && 10/x > 1,10/x,0)", 0, 0),
    ("if(x != 0 && 10/x > 1,10/x,0)", 2, 5),
])
def test_conversion_boolean_guards_skip_undefined_branches(tmp_path, expression, raw, expected):
    path = tmp_path / "guarded.xml"
    path.write_text(_minimal_xml(expression=expression.replace("&", "&amp;")), encoding="utf-8")
    parameter = parse_logger_definition(path).parameters_for("1429861")[0]

    assert parameter.parse(bytes((raw,)), parameter.address) == expected


@pytest.mark.parametrize("expression", [
    "0 && __import__('os')", "1 || x.real", "if(1,0,[x])",
])
def test_conversion_validates_even_unreachable_branches(tmp_path, expression):
    path = tmp_path / "unsafe-branch.xml"
    path.write_text(_minimal_xml(expression=expression.replace("&", "&amp;")), encoding="utf-8")
    with pytest.raises(LoggerDefinitionError, match="unsupported conversion"):
        parse_logger_definition(path)


@pytest.mark.parametrize(
    ("xml", "message"),
    [
        (
            '<!DOCTYPE logger [<!ENTITY steal SYSTEM "file:///secret">]>'
            + _minimal_xml().split(">", 1)[1],
            "entity declarations",
        ),
        (_minimal_xml(expression="__import__('os')"), "unsupported conversion"),
        (_minimal_xml(address_attrs='length="2"'), "address ranges"),
        (_minimal_xml(address="0x1000000"), "24-bit"),
        (_minimal_xml().replace('baud="9600"', 'baud="10400"'), "MS41 DS2"),
        (_minimal_xml().replace('address="0x12"', 'address="0x44"'), "MS41 DS2"),
    ],
)
def test_parser_rejects_unsupported_or_unsafe_definition_features(tmp_path, xml, message):
    path = tmp_path / "bad.xml"
    path.write_text(xml, encoding="utf-8")
    with pytest.raises(LoggerDefinitionError, match=message):
        parse_logger_definition(path)
