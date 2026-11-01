"""Channel selection changes wire reads and one stable output schema."""
import csv

import pytest

import live_data
from tests.test_live_roughness_channels import MemoryDS2


def capture_once(tmp_path, selected, mode, *, ecu_id="1429861", fake=None,
                 prepare=False, log_columns=None):
    fake = fake or MemoryDS2()
    poller = live_data.LiveDataPoller(
        interval=0, ecu_id=ecu_id, ds2=fake, selected_ids=selected,
        use_telegram=mode != "standard", telegram_fallback=mode == "auto",
        log_columns=log_columns)
    if prepare:
        poller._prepare_live_profile()
        fake.reads.clear()  # Count recurring acquisition separately from admission.
    else:
        poller._profile_ready = True
    poller._pending_log_path = str(tmp_path / "selected.csv")
    commit = poller._write_csv_row

    def commit_and_stop(row):
        commit(row)
        poller._stop.set()

    poller._write_csv_row = commit_and_stop
    poller._poll_loop()
    assert poller.terminal_error is None, poller.pop_errors()
    with (tmp_path / "selected.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1
    return poller, fake, rows[0]


@pytest.mark.parametrize("mode", ["standard", "auto", "telegram"])
def test_rpm_only_selection_filters_reads_latest_samples_and_csv(tmp_path, mode):
    poller, fake, row = capture_once(
        tmp_path, ("P8",), mode, log_columns=("Engine RPM", "Battery Voltage"))
    assert set(row) == {"Time", "Datetime", "Engine RPM"}
    assert row["Engine RPM"] == "900"
    assert poller.latest_values() == {"Engine RPM": ("900", "RPM")}
    assert poller.resolved_rows() == [("Engine RPM", "RPM")]
    assert len(poller.available_channels()) > 1
    _sequence, _dropped, _count, channels, samples = poller.completed_samples_since(0)
    assert channels == (("Engine RPM", "RPM"),)
    assert samples[0][2] == ("900",)
    if mode != "standard":
        assert fake.entries == ((0xDA2A, 2),) and fake.polls == 1 and fake.reads == []
        assert poller.acquisition_mode == "telegram"
    else:
        assert fake.entries is None and fake.polls == 0
        assert fake.reads == [(0xDA2A, 2)]
        assert poller.acquisition_mode == "standard"


def test_auto_preserves_telegram_when_selected_core_needs_more_read_transactions(tmp_path):
    selected = ("P8", "P13", "P17", "E2")
    poller, fake, row = capture_once(tmp_path, selected, "auto")
    assert poller.acquisition_mode == "telegram"
    assert len(fake.entries) == 4 and fake.polls == 1 and not fake.reads
    assert set(row) == {"Time", "Datetime", "Engine RPM", "Throttle Position",
                        "Battery Voltage", "Engine Load"}


@pytest.mark.parametrize("mode", ["standard", "telegram"])
@pytest.mark.parametrize("ecu_id", ["1429861", "1437806", "1406464", "SHINDE1"])
def test_one_roughness_channel_keeps_invisible_validity_dependencies(tmp_path, mode, ecu_id):
    poller, fake, row = capture_once(tmp_path, ("BS_ROUGHNESS_CYL3",), mode, ecu_id=ecu_id)
    assert set(row) == {"Time", "Datetime", "Cylinder 3 Roughness"}
    assert row["Cylinder 3 Roughness"] == "-1"
    assert poller.latest_values() == {"Cylinder 3 Roughness": ("-1", "raw")}
    if mode == "telegram":
        assert not fake.reads and len(fake.entries) == 5
        assert (0xE858, 2) in fake.entries and (0xFD30, 2) in fake.entries
    else:
        assert fake.reads == [(0xDA2A, 2), (0xE858, 10), (0xFD14, 30)]


def test_selected_state_uses_batch_group_without_exposing_other_group_members(tmp_path):
    poller, fake, row = capture_once(tmp_path, ("BS_STATE_PART_LOAD",), "telegram")
    assert set(row) == {"Time", "Datetime", "Part Load"}
    assert poller.latest_values() == {"Part Load": ("Inactive", "")}
    assert fake.reads == [] and fake.polls == 1


@pytest.mark.parametrize("mode", ["standard", "telegram"])
def test_unselected_installed_patch_channels_do_not_add_recurring_reads(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(live_data, "detect_cut_runtime", lambda *_args: "V16")
    monkeypatch.setattr(live_data, "detect_launch_runtime", lambda *_args: "V16")
    poller, fake, row = capture_once(tmp_path, ("P8",), mode, prepare=True)
    available = {key for key, _name, _unit in poller.available_channels()}
    assert "BS_CUT_LAUNCH_SWITCH" in available and "BS_CUT_TIMING_STATE" in available
    assert poller.profile_ready and set(row) == {"Time", "Datetime", "Engine RPM"}
    assert fake.reads == ([(0xDA2A, 2)] if mode == "standard" else [])


@pytest.mark.parametrize("mode", ["standard", "telegram"])
def test_selected_extended_load_reads_mode_gate_without_logging_unselected_context(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(live_data, "detect_cut_runtime", lambda *_args: None)
    monkeypatch.setattr(live_data, "detect_alphan_failsafe_runtime", lambda *_args: True)
    fake = MemoryDS2()
    fake.memory[0x133BF] = 2
    fake.memory[0xE80A:0xE80C] = (1000).to_bytes(2, "little")
    poller, fake, row = capture_once(
        tmp_path, ("BS_ALPHA_EXTENDED_LOAD",), mode, ecu_id="SHINDE1", fake=fake, prepare=True)
    assert set(row) == {"Time", "Datetime", "Extended Engine Load"}
    assert row["Extended Engine Load"] == "84.8"
    if mode == "telegram":
        assert not fake.reads and set(fake.entries) == {(0xFD22, 2), (0xE80A, 2)}
    else:
        assert (0xFD22, 2) in fake.reads and (0xE80A, 2) in fake.reads
    assert not any(address <= 0xE854 < address + length for address, length in fake.reads)
    assert poller.latest_values() == {"Extended Engine Load": ("84.8", "mg/st")}


def test_selected_profile_metadata_accompanies_measurement_without_recurring_metadata_read(tmp_path, monkeypatch):
    monkeypatch.setattr(live_data, "detect_cut_runtime", lambda *_args: None)
    monkeypatch.setattr(live_data, "detect_alphan_failsafe_runtime", lambda *_args: False)
    poller, fake, row = capture_once(
        tmp_path, ("P8", "BS_PROFILE_WIDEBAND_MODE"), "standard", ecu_id="SHINDE1", prepare=True)
    assert set(row) == {"Time", "Datetime", "Engine RPM", "Wideband Mode"}
    assert row["Wideband Mode"] == "Disabled"
    assert fake.reads == [(0xDA2A, 2)]
    assert "Narrowband Emulation" not in poller.latest_values()
    assert "BS_PROFILE_NARROWBAND_EMULATION" in {key for key, *_ in poller.available_channels()}


@pytest.mark.parametrize("selected", [(), ("unknown",), ("BS_PROFILE_WIDEBAND_MODE",)])
def test_empty_or_metadata_only_selection_stops_before_bus_activity(selected):
    fake = MemoryDS2()
    poller = live_data.LiveDataPoller(ecu_id="SHINDE1", selected_ids=selected, ds2=fake)
    poller._poll_loop()
    assert "Select at least one measurement channel" in poller.terminal_error
    assert fake.reads == [] and fake.entries is None and fake.polls == 0


def test_inadmissible_patch_selection_preserves_prepared_available_catalog(monkeypatch):
    monkeypatch.setattr(live_data, "detect_cut_runtime", lambda *_args: None)
    poller = live_data.LiveDataPoller(
        ecu_id="1429861", selected_ids=("BS_CUT_LAUNCH_SWITCH",), ds2=MemoryDS2())
    poller._poll_loop()
    assert "Select at least one measurement channel" in poller.terminal_error
    assert poller.profile_ready and poller.acquisition_mode is None
    assert "P8" in {key for key, *_ in poller.available_channels()}
    assert "BS_CUT_LAUNCH_SWITCH" not in {key for key, *_ in poller.available_channels()}


def test_static_catalog_is_exact_id_scoped_and_exposes_stable_profile_metadata_ids():
    catalog = live_data.selection_catalog("SHINDE1")
    assert ("P8", "Engine RPM", "RPM") in catalog
    assert ("BS_PROFILE_WIDEBAND_MODE", "Wideband Mode", "") in catalog
    assert ("BS_CUT_LAUNCH_SWITCH", "Launch Control Switch", "") in catalog
    assert not any(key.startswith("BS_PROFILE_")
                   for key, *_ in live_data.selection_catalog("1429861"))
    assert live_data.selection_catalog("unknown") == ()


def test_none_selection_preserves_legacy_full_capture(tmp_path):
    poller, fake, row = capture_once(tmp_path, None, "auto")
    assert poller.acquisition_mode == "telegram"
    assert 1 <= len(fake.entries) <= 49 and not fake.reads
    assert len(row) == len(poller.resolved_rows()) + 2
    assert "Cylinder 6 Roughness" in row and "O2 Heater Front B1" in row
