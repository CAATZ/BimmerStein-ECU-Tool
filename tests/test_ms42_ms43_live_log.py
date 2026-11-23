"""Family capture reopening uses existing bounded CSV parser and viewer metadata."""

import pytest

from live_data import display_rows, live_display_spec
from live_log import display_specs, parse_log
from ms42_ms43_live import display_rows as family_rows


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_family_csv_units_gauges_and_missing_values(family):
    parsed = parse_log(b"Time,Engine Speed,Battery Voltage,Crankshaft Wheel Adapted\n0,800,12.5,1\n1,900,,0\n", family=family)
    speed, voltage, adapted = parsed["series"]
    assert speed["unit"] == "rpm" and speed["values"] == [800, 900]
    assert voltage["unit"] == "V" and voltage["values"] == [12.5, None]
    assert adapted["values"] == [1, 0]
    assert parsed["display_specs"]["Engine Speed"] == {"kind": "dial", "minimum": 0, "maximum": 10000, "step": 1000}
    assert parsed["display_specs"]["Crankshaft Wheel Adapted"] == {"kind": "status"}


def test_old_csv_can_resolve_unique_family_channels_without_guessing_unknown_units():
    parsed = parse_log(b"Time,Ignition Supply Voltage,Crankshaft Wheel Adapted,Unknown\n0,12.5,1,4\n")
    assert [channel["unit"] for channel in parsed["series"]] == ["V", "", ""]
    assert parsed["display_specs"]["Ignition Supply Voltage"]["kind"] == "dial"
    assert parsed["display_specs"]["Crankshaft Wheel Adapted"]["kind"] == "status"
    assert "Unknown" not in parsed["display_specs"]


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_fan_request_unit_follows_the_capture_and_legacy_is_ambiguous(family, tmp_path):
    from ms42_ms43_live import MS42MS43LiveDataPoller, catalog_for
    from types import SimpleNamespace
    param = next(p for p in catalog_for(family) if p.name == "Electric Fan Request")
    poller = MS42MS43LiveDataPoller(SimpleNamespace(family=family), selected_ids=[param.id])
    path = tmp_path / "renamed.csv"
    poller._open_csv(path)
    poller._write_csv_row({param.name: "128"})
    poller._csv_file.close()
    other = "MS43" if family == "MS42" else "MS42"
    replay = parse_log(path.read_bytes(), family=other)["series"][0]
    assert (replay["name"], replay["unit"], replay["values"]) == (param.name, param.unit, [128])
    legacy = parse_log(b"Time,Electric Fan Request\n0,128\n", family=other)
    assert legacy["series"][0]["unit"] == "" and param.name not in legacy["display_specs"]


def test_large_log_pages_retain_every_boundary_and_last_sample(tmp_path):
    from live_log import parse_log_stream
    path = tmp_path / "wide.csv"
    names = [name for name, _unit in family_rows("MS43")]
    with path.open("w", newline="") as stream:
        stream.write("Time," + ",".join(names) + "\n")
        body = ",".join(["12345678"] * len(names))
        for i in range(20_000):
            stream.write(f"{i},{body}\n")
    assert path.stat().st_size > 16 * 1024 * 1024
    def page(n):
        with path.open(encoding="utf-8", newline="") as stream:
            return parse_log_stream(stream, page=n)
    first, second = page(0), page(1)
    assert first["has_next"] and first["times"][-1] + 1 == second["times"][0]
    last = page(19_999 // len(first["times"]))
    assert not last["has_next"] and last["times"][-1] == 19_999
    assert last["series"][-1]["values"][-1] == 12345678


def test_ms41_display_specs_remain_owned_by_original_definition():
    channels = display_rows()
    specs = display_specs(channels)
    for name, unit in channels:
        original = live_display_spec(name, unit)
        if original["kind"] != "value":
            assert specs[name] == {key: original[key] for key in ("kind", "minimum", "maximum", "step") if key in original}
        else:
            assert name not in specs


def test_explicit_family_does_not_show_ms41_patch_channels():
    channels = family_rows("MS43")
    assert not any("Launch" in name or "Cut Recovery" in name for name, _ in channels)
    assert display_specs(channels, family="MS43")["Battery Voltage (after main relay)"]["maximum"] == 20


def test_imported_gauge_metadata_is_used_for_family_csv(tmp_path):
    from tests.test_ms42_ms43_live import imported_definition
    path = imported_definition(tmp_path)
    text = path.read_text(encoding="utf-8").replace('format="0"', 'format="0" gauge_min="-10" gauge_max="10" gauge_step="2"')
    path.write_text(text, encoding="utf-8")
    parsed = parse_log(b"Time,Test\n0,-1\n", definition_path=path, family="MS43")
    assert parsed["series"][0]["unit"] == "u"
    assert parsed["display_specs"]["Test"] == {"kind": "dial", "minimum": -10, "maximum": 10, "step": 2}


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_family_viewer_hint_keeps_other_ecu_csv_units_and_metadata(family):
    family_names = {name for name, _unit in family_rows(family)}
    channels = [(name, unit) for name, unit in display_rows()
                if name not in family_names and unit and live_display_spec(name, unit)["kind"] == "dial"]
    assert channels
    name, unit = channels[0]
    payload = f"Time,{name},Battery Voltage\n0,1,12.5\n".encode("utf-8")
    parsed = parse_log(payload, family=family)
    assert parsed["series"][0]["unit"] == unit
    assert parsed["display_specs"][name] == display_specs([(name, unit)])[name]
    assert parsed["series"][1]["unit"] == "V"
    assert parsed["display_specs"]["Battery Voltage"]["maximum"] == 20
