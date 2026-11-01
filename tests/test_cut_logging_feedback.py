"""Exact-family limiter feedback and selected transport reads; no ECU required."""
from xml.etree import ElementTree as ET

import pytest

import live_data
from logger_definition import bundled_logger_definition_path, parse_logger_definition
from tests.test_live_roughness_channels import MemoryDS2
from tests.test_live_selection import capture_once


FAMILIES = (("1429861", 0xED52, 0xFD80, 0x13026),
            ("1437806", 0xF02C, 0xFDB6, 0x13716),
            ("1406464", 0xF014, 0xFDB6, 0x13532),
            ("SHINDE1", 0xF014, 0xFDB6, 0x147E6))


@pytest.mark.parametrize("ecu_id,soft,armed,min_tps", FAMILIES)
def test_exact_family_feedback_has_distinct_native_threshold_and_recovery(ecu_id, soft, armed, min_tps):
    native = {p.id:p for p in live_data.telegram_params_for(ecu_id)}
    assert native["BS_CUT_EFFECTIVE_SOFT_RPM"].address == soft
    assert native["BS_CUT_EFFECTIVE_SOFT_RPM"].convert(125) == 4000
    assert native["BS_CUT_STOCK_LIMITER"].name == "Native Fuel Limiter Active"
    assert "BS_CUT_LAUNCH_RECOVERY_PRIORITY" not in native
    legacy = {p.id:p for p in live_data.telegram_params_for(
        ecu_id, include_cut=True, include_launch=True, cut_revision="legacy")}
    assert "BS_CUT_LAUNCH_RECOVERY_PRIORITY" not in legacy
    current = {p.id:p for p in live_data.telegram_params_for(
        ecu_id, include_cut=True, include_launch=True)}
    priority = current["BS_CUT_LAUNCH_RECOVERY_PRIORITY"]
    assert priority.address == 0xD801
    assert [priority.display(priority.convert(x)) for x in (0, 8, 0xA0, 0xA1, 0xA2, 0xA8, 0xAF)] == [
        "Uninitialized", "Uninitialized", "Not selected", "Not selected", "Not selected", "Native recovery", "Native recovery"]
    assert current["BS_CUT_LAUNCH_ARMED"].address == armed
    assert current["BS_CUT_LAUNCH_MIN_TPS"].address == min_tps
    assert current["BS_CUT_TIMING_STATE"].display(0xC3) == "Timing constraint active"


def test_ms410_threshold_and_live_angle_share_exact_units_without_renaming_app_selections():
    path = bundled_logger_definition_path()
    ids = [node.get("id") for node in ET.parse(path).findall(".//ecuparam")]
    assert len(ids) == len(set(ids))  # XML definition XML IDs remain unique.
    definition = parse_logger_definition(path)
    native = {p.id:p for p in definition.parameters_for("1429861")}
    assert native["BS_CUT_LAUNCH_MIN_TPS_MS410"].unit == "° throttle"
    assert native["BS_CUT_LAUNCH_TPS_MS410"].unit == "° throttle"
    params = {p.id:p for p in live_data.telegram_params_for(
        "1429861", include_cut=True, include_launch=True)}
    minimum, working = (params[key] for key in ("BS_CUT_LAUNCH_MIN_TPS", "BS_CUT_LAUNCH_TPS"))
    assert minimum.unit == working.unit == "° throttle"
    for raw in range(256):
        assert minimum.convert(raw) == working.convert(raw) == pytest.approx(raw * 119.5 / 255)
        # The two-decimal display retains every byte when converted back.
        assert round(float(minimum.display(minimum.convert(raw))) * 255 / 119.5) == raw
    assert working.display(working.convert(255)) == "119.50"
    catalog = {key:unit for key, _name, unit in live_data.selection_catalog("1429861")}
    assert catalog["BS_CUT_LAUNCH_MIN_TPS"] == catalog["BS_CUT_LAUNCH_TPS"] == "° throttle"
    for ecu_id, *_ in FAMILIES[1:]:
        other = {p.id:p for p in live_data.telegram_params_for(ecu_id, include_cut=True, include_launch=True)}
        assert other["BS_CUT_LAUNCH_TPS"].unit == "%"
        assert other["BS_CUT_LAUNCH_TPS"].convert(128) == pytest.approx(60.16)


@pytest.mark.parametrize("channel,address,name", [
    ("BS_CUT_LAUNCH_MIN_TPS",0x13026,"Launch Min TPS"),
    ("BS_CUT_LAUNCH_TPS",0xE8D0,"Launch TPS (threshold scale)"),
])
@pytest.mark.parametrize("mode", ["standard", "auto", "telegram"])
def test_ms410_throttle_channel_can_be_selected_alone(tmp_path, monkeypatch, channel, address, name, mode):
    monkeypatch.setattr(live_data, "detect_cut_runtime", lambda *_: "V16")
    monkeypatch.setattr(live_data, "detect_launch_runtime", lambda *_: "V16")
    fake = MemoryDS2()
    fake.memory[address] = 128
    poller, fake, row = capture_once(
        tmp_path, (channel,), mode, ecu_id="1429861", fake=fake, prepare=True)
    assert poller.profile_ready
    assert poller.resolved_rows() == [(name,"° throttle")]
    assert poller.latest_values() == {name:("59.98","° throttle")}
    assert set(row) == {"Time","Datetime",name}
    assert row[name] == "59.98"
    if mode == "standard":
        assert fake.reads == [(address,1)] and fake.entries is None
    else:
        assert fake.entries == ((address,1),) and fake.reads == []


@pytest.mark.parametrize("mode", ["standard", "telegram"])
@pytest.mark.parametrize("ecu_id,soft,armed,min_tps", FAMILIES)
def test_selected_feedback_reads_only_family_addresses_and_preserves_csv_context(
        tmp_path, monkeypatch, mode, ecu_id, soft, armed, min_tps):
    monkeypatch.setattr(live_data, "detect_cut_runtime", lambda *_: "V16")
    monkeypatch.setattr(live_data, "detect_launch_runtime", lambda *_: "V16")
    monkeypatch.setattr(live_data, "detect_alphan_failsafe_runtime", lambda *_: False)
    selected = ("BS_CUT_STOCK_LIMITER", "BS_CUT_EFFECTIVE_SOFT_RPM",
                "BS_CUT_LAUNCH_RECOVERY_PRIORITY", "BS_CUT_LAUNCH_ARMED",
                "BS_CUT_LAUNCH_IGN_REQUEST", "BS_CUT_LAUNCH_MIN_TPS", "BS_CUT_LAUNCH_TPS")
    fake = MemoryDS2()
    for address, value in ((soft,125), (armed,64), (min_tps,128),
                           (0xFD13,128), (0xD801,0xAA), (0xE8D0,128)):
        fake.memory[address] = value
    poller, fake, row = capture_once(tmp_path, selected, mode, ecu_id=ecu_id, fake=fake, prepare=True)
    assert row["Native Fuel Limiter Active"] == "Active"
    assert row["Effective Fuel Limiter RPM"] == "4000"
    assert row["Native Fuel Recovery Priority"] == "Native recovery"
    assert row["Launch Armed"] == row["Launch Ignition Request"] == "Active"
    expected_unit = "° throttle" if ecu_id == "1429861" else "%"
    assert poller.latest_values()["Launch TPS (threshold scale)"][1] == expected_unit
    assert sum(name == "Launch TPS (threshold scale)" for name, _ in poller.resolved_rows()) == 1
    expected_addresses = {soft, armed, min_tps, 0xFD13, 0xD801, 0xE8D0}
    if mode == "telegram":
        assert set(fake.entries) == {(address,1) for address in expected_addresses}
        assert not fake.reads
    else:
        expected_reads = {(address,1) for address in expected_addresses}
        if ecu_id == "1429861":
            # The existing standard reader groups these nearby IRAM fields.
            expected_reads -= {(0xFD13,1),(0xFD80,1)}
            expected_reads.add((0xFD13,110))
        assert set(fake.reads) == expected_reads
        assert fake.entries is None
