"""Distinct physical supply rails and truthful system-check rejection handling."""

import pytest

import live_data
from live_log import parse_log
from ms42_ms43 import MS42MS43ECU
import ms42_ms43_live as live
from tests.test_ms42_ms43 import Link, reply
from tests.test_ms42_ms43_live import capture


def supply_reply(battery=25, ignition=121):
    payload = bytearray(41)
    payload[40], payload[22] = battery, ignition
    return reply(payload)


def test_both_ms43_supplies_are_read_once_without_guessing_or_substitution():
    ecu = MS42MS43ECU(Link(lambda *_: supply_reply()), "MS43")
    assert ecu.read_supply_voltages() == pytest.approx({"battery": 2.539, "ignition": 12.28876})
    assert ecu.transport.calls == [(0x12, 0x0B, b"\x03")]
    assert ecu.read_voltage() == pytest.approx(2.539)
    assert ecu.read_battery_voltage() == pytest.approx(2.539)


def test_ms42_supply_retains_original_field_and_scale():
    payload = bytearray(34)
    payload[20] = 121
    ecu = MS42MS43ECU(Link(lambda *_: reply(payload)), "MS42")
    assert ecu.read_supply_voltages() == pytest.approx({"battery": 121 * .10196078})
    assert ecu.transport.calls == [(0x12, 0x0B, b"\x03")]


@pytest.mark.parametrize("telegram", (False, True))
def test_ms43_live_capture_keeps_both_rail_values_and_labels(monkeypatch, tmp_path, telegram):
    def respond(command, args):
        assert command == 0x0B
        if args == b"\x03":
            return supply_reply()
        if args == b"\x00":
            return reply(bytes((121, 25)))  # Catalog order is KL15, then main relay.
        return reply()

    ecu = MS42MS43ECU(Link(respond), "MS43")
    poller = live.MS42MS43LiveDataPoller(ecu, ecu_id="430069", use_telegram=telegram,
                                      telegram_fallback=False, selected_ids=["VB", "VB_IGK"])
    path = tmp_path / "supplies.csv"
    values = capture(monkeypatch, poller, str(path))
    assert values == {"Battery Voltage (after main relay)": ("2.539", "V"),
                      "Ignition Supply Voltage (KL15)": ("12.289", "V")}
    parsed = parse_log(path.read_bytes(), family="MS43")
    assert {row["name"]: row["values"][0] for row in parsed["series"]} == {
        "Battery Voltage (after main relay)": 2.539, "Ignition Supply Voltage (KL15)": 12.289}
    if telegram:
        assert ecu.transport.calls[0][2] == bytes.fromhex("01 02 00 00 00 FB D6 00 00 00 FB D5")
        assert ecu.transport.calls[-1][2] == b"\xFF"
    else:
        assert ecu.transport.calls == [(0x12, 0x0B, b"\x03")]


@pytest.mark.parametrize("family", (None, "MS42", "MS43"))
@pytest.mark.parametrize("name", ("Battery Voltage", "Ignition Supply Voltage",
                                  "Battery Voltage (after main relay)", "Ignition Supply Voltage (KL15)"))
def test_old_and_new_supply_csv_names_keep_units_and_values(family, name):
    parsed = parse_log(f"Time,{name}\n0,2.539\n".encode(), family=family)
    assert parsed["series"][0]["values"] == [2.539]
    assert parsed["series"][0]["unit"] == "V"
    assert parsed["display_specs"][name]["maximum"] == 20


@pytest.mark.parametrize("family", ("MS42", "MS43"))
def test_explicit_decline_explains_conditions_and_does_not_stop_another_test(family):
    ecu = MS42MS43ECU(Link(lambda *_: reply(b"\x01")), family)
    poller = live_data.RoughnessPoller(ecu, family)
    poller._run()
    assert poller.stop_confirmed and poller.latest_reading() is None
    assert "run the engine" in poller.pop_errors()[0]
    assert ecu.transport.calls == [(0x12, 0x22, b"\x05")]


@pytest.mark.parametrize("family", ("MS41.0", "MS41.1", "MS41.2", "MS41.3"))
def test_ms41_decline_contract_is_unchanged(family):
    class Peer:
        def execute(self, command, args):
            assert (command, args) == (0x22, b"\x05")
            return b"\x01"

    with pytest.raises(live_data._RoughnessStartRejected, match="another system check is active"):
        live_data.start_roughness_check(Peer(), family)
