"""Offline live capture workflows; every ECU connection is the fake Link."""

import csv
import hashlib
from pathlib import Path
import threading

import pytest

from ds2 import DS2Error
import live_data
from ms42_ms43 import MS42MS43ECU
import ms42_ms43_live as live
from tests.test_ms42_ms43 import Link, reply


def capture(monkeypatch, poller, path=None):
    original = poller._write_csv_row

    def finish(row):
        original(row)
        poller._stop.set()

    monkeypatch.setattr(poller, "_write_csv_row", finish)
    poller._pending_log_path = path
    poller._poll_loop()
    assert poller.terminal_error is None
    return poller.latest_values()


@pytest.mark.parametrize("family,voltage_offset,voltage", [("MS42", 23, 13.05088), ("MS43", 43, 12.99968)])
def test_fixed_group_decode_uses_bmw_offsets_and_familyscale(family, voltage_offset, voltage):
    payload = bytearray(41 if family == "MS43" else 34)
    payload[0:2] = (3000).to_bytes(2, "big")
    payload[voltage_offset - 3] = 128
    if family == "MS43":
        payload[22] = 17  # KL15 is not battery voltage.
    link = Link(lambda command, args: reply(payload))
    ecu = MS42MS43ECU(link, family)
    channels = [p for p in live.catalog_for(family) if p.id in {"N", "VB"}]
    values = live._group_read(ecu, channels)
    assert values == pytest.approx({"N": 3000, "VB": voltage})
    assert link.calls == [(0x12, 0x0B, b"\x03")]


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_family_catalog_selection_is_unique_and_excludes_fake_barometric(family):
    channels = live.catalog_for(family)
    assert len({p.id for p in channels}) == len(channels)
    assert len({p.name for p in channels}) == len(channels)
    assert live.selection_catalog(family) == tuple((p.id, p.name, p.unit) for p in channels)
    assert live.display_rows(family)[:len(channels)] == [(p.name, p.unit) for p in channels]
    assert not any("patch" in p.name.lower() for p in channels)
    if family == "MS42":
        assert not any(p.id == "AMP_MES" for p in channels)


@pytest.mark.parametrize("family", ["MS42", "MS43"])
@pytest.mark.parametrize("raw", [-32768, -1, 0, 32767])
def test_signed_idle_adaptation_is_not_unsigned(family, raw):
    parameter = next(p for p in live.catalog_for(family) if p.id == "ISAPWM_AD_MMV_IS")
    assert parameter.decode(raw.to_bytes(2, "big", signed=True)) == pytest.approx(raw * .001526)


@pytest.mark.parametrize("family,offset", [("MS42", 3), ("MS43", 4)])
def test_adaptations_use_distinct_lambda_offsets_and_preserve_unavailable_groups(family, offset):
    def respond(command, args):
        if args == b"\x92":
            return reply(status=0xB0)
        payload = bytearray(41)
        if args == b"\x91":
            payload[offset - 3:offset - 1] = (33018).to_bytes(2, "big")
        return reply(payload)

    ecu = MS42MS43ECU(Link(respond), family)
    result = live.read_adaptations(ecu)
    rows = {name: (value, unit) for name, value, unit in result["values"]}
    assert rows["Additive Fuel Adaptation Bank 1"] == (1., "ms")
    assert rows["Idle Adaptation"] == (None, "%")
    assert result["errors"]
    assert {"Intake Camshaft Adaptation", "Exhaust Camshaft Adaptation", "Octane Adaptation"} <= rows.keys()
    assert all(call[1] == 0x0B for call in ecu.transport.calls)


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_short_measurement_cannot_consume_checksum_as_value(family):
    ecu = MS42MS43ECU(Link(lambda *_: reply(b"\x00")), family)
    with pytest.raises(DS2Error, match="Short"):
        live._group_read(ecu, [live.catalog_for(family)[0]])


@pytest.mark.parametrize("family,maximum,adc_max", [("MS42", 0x7FFFF, 27), ("MS43", 0xEFFFF, 26)])
def test_configured_telegram_uses_family_native_limits(family, maximum, adc_max):
    descriptor = b"\x00" + maximum.to_bytes(4, "big")
    assert live.build_telegram_setup(family, [descriptor] * 24)[:2] == b"\x01\x18"
    adc = bytes((2, 0, 0, 0, adc_max))
    assert live.build_telegram_setup(family, [adc]) == b"\x01\x01" + adc
    for entries in ([], [descriptor] * 25, [b"\x01\x00\x00\xE0\x01"],
                    [b"\x00" + (maximum + 1).to_bytes(4, "big")],
                    [bytes((2, 0, 0, 0, adc_max + 1))], [b"\x00"]):
        with pytest.raises(ValueError):
            live.build_telegram_setup(family, entries)


@pytest.mark.parametrize("family,software,address", [("MS42", "0110C6", 0xFA22), ("MS43", "430069", 0xE0E4)])
def test_real_worker_telegram_csv_and_cleanup(monkeypatch, tmp_path, family, software, address):
    def respond(command, args):
        assert command == 0x0B
        return reply(b"\x0B\xB8\x80") if args == b"\x00" else reply()

    link = Link(respond)
    ecu = MS42MS43ECU(link, family)
    poller = live.MS42MS43LiveDataPoller(ecu, ecu_id=software, use_telegram=True,
                                      telegram_fallback=False, selected_ids=["N", "VB"])
    path = tmp_path / "capture.csv"
    values = capture(monkeypatch, poller, str(path))
    assert values["Engine Speed"] == ("3000", "rpm")
    assert poller.acquisition_mode == "telegram"
    assert link.calls[0][2][:7] == b"\x01\x02\x01" + address.to_bytes(4, "big")
    assert link.calls[-1] == (0x12, 0x0B, b"\xFF")
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert len(rows) == 1 and rows[0]["Engine Speed"] == "3000"
    voltage_name = "Battery Voltage (after main relay)" if family == "MS43" else "Battery Voltage"
    assert set(rows[0]) == {"Time", "Datetime", "Engine Speed", voltage_name}
    assert poller.completed_samples_since(0)[0] == 1
    assert poller.csv_rows == 1


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_adc_uses_native_derived_selector_in_standard_and_telegram(monkeypatch, family):
    for telegram in (False, True):
        link = Link(lambda command, args: reply(b"\x00\x80") if args[0] in (0, 2) else reply())
        ecu = MS42MS43ECU(link, family)
        poller = live.MS42MS43LiveDataPoller(ecu, use_telegram=telegram, telegram_fallback=False,
                                          selected_ids=["VLS_DOWN_1"])
        values = capture(monkeypatch, poller)
        expected = 128 * (.004887 if family == "MS42" else .0048828)
        assert float(values["Rear O2 Sensor Bank 1"][0]) == pytest.approx(expected, abs=.0005)
        if telegram:
            assert link.calls[0][2] == b"\x01\x01\x02\x00\x00\x00\x19"
        else:
            assert link.calls == [(0x12, 0x0B, b"\x02\x0E\x00\x00\x19")]


def test_auto_uses_fixed_groups_if_telegram_needs_more_roundtrips(monkeypatch):
    link = Link(lambda *_: reply(bytes(41)))
    ecu = MS42MS43ECU(link, "MS43")
    poller = live.MS42MS43LiveDataPoller(ecu, ecu_id="430069", use_telegram=True,
                                      selected_ids=["N", "TIA"])
    capture(monkeypatch, poller)
    assert poller.acquisition_mode == "standard"
    assert link.calls == [(0x12, 0x0B, b"\x03")]


@pytest.mark.parametrize("mode", ["standard", "auto", "telegram"])
def test_unknown_software_keeps_fixed_groups_without_guessing_ram(monkeypatch, mode):
    link = Link(lambda *_: reply(bytes(41)))
    ecu = MS42MS43ECU(link, "MS43")
    poller = live.MS42MS43LiveDataPoller(ecu, ecu_id="430056", use_telegram=mode != "standard",
                                      telegram_fallback=mode != "telegram", selected_ids=["N"])
    if mode == "telegram":
        poller._poll_loop()
        assert "reviewed software ID" in poller.terminal_error
        assert not link.calls
    else:
        capture(monkeypatch, poller)
        assert poller.acquisition_mode == "standard"
        assert link.calls == [(0x12, 0x0B, b"\x03")]


def test_telegram_overflow_supplements_groups_without_sending_25_descriptors(monkeypatch):
    entries = []

    def respond(command, args):
        if args[0] == 1:
            entries.extend(args[2 + index * 5:7 + index * 5] for index in range(args[1]))
            return reply()
        if args == b"\x00":
            return reply(bytes(sum(1 if d[0] == 0 else 2 for d in entries)))
        return reply() if args == b"\xFF" else reply(bytes(41))

    link = Link(respond)
    poller = live.MS42MS43LiveDataPoller(MS42MS43ECU(link, "MS43"), ecu_id="430069",
                                      use_telegram=True, telegram_fallback=False)
    capture(monkeypatch, poller)
    assert len(entries) == 24
    assert (0x12, 0x0B, b"\x90") in link.calls
    assert len(poller.latest_values()) == len(live.catalog_for("MS43", ecu_id="430069"))


def test_malformed_batch_does_not_publish_sample_and_stop_cleans_up(monkeypatch):
    poller = None

    def respond(command, args):
        if args == b"\x00":
            poller._stop.set()
            return reply(b"\x01")
        return reply()

    link = Link(respond)
    poller = live.MS42MS43LiveDataPoller(MS42MS43ECU(link, "MS42"), ecu_id="0110C6",
                                      use_telegram=True, selected_ids=["N"])
    poller._poll_loop()
    assert poller.completed_samples_since(0)[0] == 0
    assert "expected 2" in " ".join(poller.pop_errors())
    assert link.calls[-1] == (0x12, 0x0B, b"\xFF")


def test_rejected_group_does_not_erase_good_values_or_log_false_zero(monkeypatch, tmp_path):
    link = Link(lambda command, args: reply(status=0xB0) if args == b"\x91" else reply(bytes(41)))
    poller = live.MS42MS43LiveDataPoller(MS42MS43ECU(link, "MS43"),
                                      selected_ids=["N", "TI_AD_ADD_MMV_1"])
    path = tmp_path / "partial.csv"
    values = capture(monkeypatch, poller, str(path))
    assert values["Engine Speed"][0] == "0"
    assert values["Additive Fuel Adaptation Bank 1"][0] == "—"
    row = next(csv.DictReader(path.open(encoding="utf-8")))
    assert row["Additive Fuel Adaptation Bank 1"] == ""


@pytest.mark.parametrize("selection", [[], ["NOT_A_CHANNEL"], "N"])
def test_invalid_selection_never_contacts_transport(selection):
    link = Link(lambda *_: pytest.fail("Invalid selection contacted ECU"))
    with pytest.raises(ValueError):
        live.MS42MS43LiveDataPoller(MS42MS43ECU(link, "MS43"), selected_ids=selection)


def test_stop_waits_for_worker_then_reconnect_reregisters(monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def respond(command, args):
        if args == b"\x00":
            entered.set()
            assert release.wait(2)
            return reply(b"\x00\x01")
        return reply()

    link = Link(respond)
    poller = live.MS42MS43LiveDataPoller(MS42MS43ECU(link, "MS43"), ecu_id="430069",
                                      use_telegram=True, selected_ids=["N"])
    poller.start()
    assert entered.wait(2)
    stopping = threading.Thread(target=poller.stop)
    stopping.start()
    assert stopping.is_alive()
    release.set()
    stopping.join(2)
    assert not stopping.is_alive()
    assert link.calls[-1][2] == b"\xFF"
    assert poller.completed_samples_since(0)[0] == 0
    count = len(link.calls)
    assert len(link.calls) == count
    poller._stop.clear()
    capture(monkeypatch, poller)
    assert sum(call[2].startswith(b"\x01") for call in link.calls) == 2
    assert link.calls[-1][2] == b"\xFF"


@pytest.mark.parametrize("family", ["MS42", "MS43"])
def test_roughness_reorders_firing_order_scales_and_keeps_negative_one(family):
    wire = (-1, -32768, 100, 32767, -200, 0)
    payload = b"\x00" + b"".join(value.to_bytes(2, "big", signed=True) for value in wire)
    if family == "MS43":
        payload += bytes.fromhex("112233445566778899aabbcc")
    link = Link(lambda command, args: reply(payload if command == 0x2B else b"\x00" if command == 0x22 else b""))
    ecu = MS42MS43ECU(link, family)
    live_data.start_roughness_check(ecu, family)
    reading = live_data.read_roughness(ecu, family)
    live_data.stop_roughness_check(ecu, family)
    assert reading.cylinders == pytest.approx(tuple(x * .00122 for x in (-1, -200, 100, 0, -32768, 32767)))
    assert reading.state == "available"
    assert link.calls == [(0x12, 0x22, b"\x05"), (0x12, 0x2B, b"\x05"), (0x12, 0x23, b"\x05")]


def test_roughness_unavailable_status_is_not_six_zeroes():
    ecu = MS42MS43ECU(Link(lambda *_: reply(b"\x05" + bytes(24))), "MS43")
    assert live.read_roughness(ecu).cylinders == (None,) * 6


@pytest.mark.parametrize("family,size", [("MS42", 13), ("MS43", 25)])
@pytest.mark.parametrize("delta", [-1, 1])
def test_roughness_rejects_truncated_or_extra_bytes(family, size, delta):
    ecu = MS42MS43ECU(Link(lambda *_: reply(bytes(size + delta))), family)
    with pytest.raises(DS2Error, match="payload length"):
        live.read_roughness(ecu)


@pytest.mark.parametrize("family,software,table,rpm,load", [
    ("MS42", "7500255", 0xC486, 0x4890D, 0x4891E),
    ("MS43", "430069", 0x40221, 0x7400C, 0x74095),
])
def test_exact_knock_tables_axes_shape_and_slot_names(family, software, table, rpm, load):
    def respond(command, args):
        if command == 0x0B:
            return reply(bytes(41))
        assert command == 6
        address, length = int.from_bytes(args[:4], "big"), args[4]
        if address == rpm:
            return reply(bytes(range(16)))
        if address == load:
            return reply(bytes((29, 40, 50, 60)))
        assert table <= address < table + 384 and length == 64
        return reply(bytes((128, 127, 96, 88)) * 16)

    link = Link(respond)
    result = live.read_adaptations(MS42MS43ECU(link, family), software)
    assert result["rpm"] == [index * 32 for index in range(16)]
    assert result["load"] == pytest.approx([value * 1389 / 255 for value in (29, 40, 50, 60)])
    assert len(result["knock"]) == 6
    assert all(len(table) == 16 for table in result["knock"])
    assert all(row == [0, -.375, -12, -15] for table in result["knock"] for row in table)
    assert result["knock_labels"] == [f"Ignition slot {index + 1}" for index in range(6)]
    assert len([call for call in link.calls if call[1] == 6]) == 8


@pytest.mark.parametrize("software", [None, "unknown", "7519308"])
def test_unreviewed_software_keeps_bmw_adaptations_without_guessed_ram_reads(software):
    link = Link(lambda command, args: reply(bytes(41)))
    result = live.read_adaptations(MS42MS43ECU(link, "MS43"), software)
    assert result["values"] and "knock" not in result
    assert all(call[1] == 0x0B for call in link.calls)


def test_failed_knock_read_preserves_scalar_adaptations():
    link = Link(lambda command, args: reply(bytes(41)) if command == 0x0B else reply(status=0xB0))
    result = live.read_adaptations(MS42MS43ECU(link, "MS43"), "430069")
    assert result["values"] and "knock" not in result and result["errors"]


def imported_definition(tmp_path, *, family="MS43", address="0xE0E4", subgroup="0x01",
                        groupsize=None, storage="int16", expression="x*0.5", command="0x0B"):
    from tests.test_logger_definition import _minimal_xml
    text = _minimal_xml(expression, "7551615" if family == "MS43" else "7500255", address)
    text = text.replace('target="1"', f'target="1" group="{command}" subgroup="{subgroup}"' +
                        (f' groupsize="{groupsize}"' if groupsize else ""))
    text = text.replace('storagetype="uint8"', f'storagetype="{storage}"')
    path = tmp_path / "family.xml"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("family,software", [("MS42", "0110C6"), ("MS43", "430069")])
@pytest.mark.parametrize("telegram", [False, True])
def test_imported_memory_definition_runs_real_worker_endian_and_conversion(tmp_path, monkeypatch, family, software, telegram):
    path = imported_definition(tmp_path, family=family)
    def respond(command, args):
        if command == 6:
            assert args == bytes.fromhex("00 00 E0 E4 02")
            return reply(bytes.fromhex("FE FF"))
        return reply(bytes.fromhex("FF FE")) if args == b"\x00" else reply()
    link = Link(respond)
    poller = live.MS42MS43LiveDataPoller(MS42MS43ECU(link, family), ecu_id=software,
                                      definition_path=path, use_telegram=telegram, interval=0)
    assert capture(monkeypatch, poller)["Test"] == ("-1", "u")
    assert poller.resolved_rows() == [("Test", "u")]
    assert live.selection_catalog(family, path, software) == (("TEST", "Test", "u"),)


@pytest.mark.parametrize("family,software", [("MS42", "0110C6"), ("MS43", "430069")])
def test_imported_fixed_group_uses_payload_offsets_and_big_endian(tmp_path, monkeypatch, family, software):
    path = imported_definition(tmp_path, family=family, address="0x01", subgroup="0x03", groupsize=34)
    link = Link(lambda command, args: reply(bytes.fromhex("00 FF FE") + bytes(31)))
    poller = live.MS42MS43LiveDataPoller(MS42MS43ECU(link, family), ecu_id=software, definition_path=path)
    assert capture(monkeypatch, poller)["Test"] == ("-1", "u")
    assert link.calls == [(0x12, 0x0B, b"\x03")]


@pytest.mark.parametrize("telegram", [False, True])
def test_imported_adc_uses_native_selector(tmp_path, monkeypatch, telegram):
    path = imported_definition(tmp_path, address="0x07", storage="uint16")
    link = Link(lambda command, args: reply(b"\x02\x00") if args == b"\x00" or args.startswith(b"\x02") else reply())
    poller = live.MS42MS43LiveDataPoller(MS42MS43ECU(link, "MS43"), ecu_id="430069",
                                      definition_path=path, use_telegram=telegram)
    assert capture(monkeypatch, poller)["Test"] == ("256", "u")
    assert link.calls[0][2] == (b"\x01\x01\x02\x00\x00\x00\x07" if telegram else b"\x02\x0E\x00\x00\x07")


@pytest.mark.parametrize("changes", [{"subgroup": "0xFF", "address": "0x00", "groupsize": 34},
                                    {"command": "0x22"}, {"address": "0xF0000"},
                                    {"address": "0x1B", "storage": "uint16"}])
def test_imported_invalid_commands_selectors_and_ranges_fail_before_transport(tmp_path, changes):
    path = imported_definition(tmp_path, **changes)
    link = Link(lambda *_: pytest.fail("Invalid definition contacted transport"))
    with pytest.raises(ValueError):
        live.MS42MS43LiveDataPoller(MS42MS43ECU(link, "MS43"), ecu_id="430069", definition_path=path)


def test_imported_exact_id_does_not_cross_families(tmp_path):
    path = imported_definition(tmp_path)
    with pytest.raises(ValueError, match="does not belong"):
        live.catalog_for("MS42", path, "7551615")
    with pytest.raises(ValueError, match="no channels"):
        live.catalog_for("MS43", path, "unreviewed")


def test_native_digital_state_uses_bmw_byte_and_mask():
    parameter = next(p for p in live.catalog_for("MS43") if p.id == "STAT_GEB_OK")
    assert (parameter.selector, parameter.offset) == (4, 8)
    assert parameter.decode(b"\x10") == 1
    assert parameter.decode(b"\xEF") == 0
    assert live.display_spec(parameter.name, parameter.unit)["kind"] == "status"


def test_uncertain_setup_ack_still_cleans_registration(monkeypatch):
    link = Link(lambda command, args: reply(b"unexpected") if args.startswith(b"\x01") else reply())
    poller = live.MS42MS43LiveDataPoller(MS42MS43ECU(link, "MS43"), ecu_id="430069",
                                      selected_ids=["N"], use_telegram=True, telegram_fallback=False)
    poller._poll_loop()
    assert "setup response" in poller.terminal_error
    assert link.calls[-1][2] == b"\xFF"


@pytest.mark.parametrize("family,name,digest,anchors,rpm,load", [
    ("MS42", "Siemens_MS42_0110C6_E46_M52TUB28_EU3_LHD.bin",
     "010d6bf17bcfe46a9f436ba8e11dff375466ee948aaaf10d43a1d8325757b2cc",
     {0x2EF4E: "e6fc0c09", 0x2EF5A: "e6fc1d09", 0x2F0B8: "f0485c64c2f57dc40054f48586c4",
      0x2FC8E: "48968ddf09c147fc4000"}, 0x4890D, 0x4891E),
    ("MS43", "Siemens_MS43_MS430069_E46_M54B30_US.bin",
     "eb687fdd8eee4a0671dbe1d3294b4c25788b6cfa60c29f15d3b77293981909da",
     {0x35EC8: "e6fc0b00e6fd1d00", 0x35ED8: "e6fc9400e6fd1d00",
      0x35EE8: "c2f46bfb5c2403f86cfb"}, 0x7400C, 0x74095),
])
def test_knock_axis_consumers_against_actual_reviewed_firmware(family, name, digest, anchors, rpm, load):
    path = Path(__file__).resolve().parents[2] / "reference_inputs" / family / "inputs" / name
    if not path.is_file():
        pytest.skip("Private canonical firmware is not present")
    image = path.read_bytes()
    assert hashlib.sha256(image).hexdigest() == digest
    for address, expected in anchors.items():
        wanted = bytes.fromhex(expected)
        assert image[address:address + len(wanted)] == wanted
    assert image[rpm - 1] == 16 and image[load - 1] == 4
    assert list(image[rpm:rpm + 16]) == sorted(image[rpm:rpm + 16])
    assert list(image[load:load + 4]) == sorted(image[load:load + 4])
