"""Ordinary RAM/telegram acquisition and CSV coverage, with no ECU connection."""
import csv

import pytest

import live_data


ECU_IDS = ("1429861", "1437806", "1406464", "SHINDE1")
VALUES = (-32768, -256, -1, 0, 1, 32767)
NAMES = tuple(f"Cylinder {index + 1} Roughness" for index in range(6))


class MemoryDS2:
    def __init__(self, rpm=900, running=4, faults=0, mask=0x3F):
        self.memory = bytearray(0x40000)
        for index, value in enumerate(VALUES):
            self.memory[0xE854 + 2 * index:0xE856 + 2 * index] = value.to_bytes(
                2, "little", signed=True)
        self.memory[0xE861] = mask
        self.memory[0xDA2A:0xDA2C] = rpm.to_bytes(2, "little")
        self.memory[0xFD14] = running
        self.memory[0xFD30:0xFD32] = faults.to_bytes(2, "little")
        self.reads = []
        self.entries = None
        self.polls = 0

    def read_mem(self, address, length):
        self.reads.append((address, length))
        return bytes(self.memory[address:address + length])

    def setup_telegram_batch(self, *, ecu_id, entries):
        self.entries = tuple(entries)

    def poll_telegram_batch(self):
        self.polls += 1
        return b"".join(self.memory[address:address + length][::-1]
                        for address, length in self.entries)


def poll_once(tmp_path, ecu_id, telegram, fake, *, prepare_profile=False):
    poller = live_data.LiveDataPoller(
        interval=0, ecu_id=ecu_id, use_telegram=telegram,
        telegram_fallback=False, ds2=fake)
    # Profile admission is covered separately; isolate recurring acquisition.
    poller._profile_ready = not prepare_profile
    poller._pending_log_path = str(tmp_path / "sample.csv")
    commit = poller._write_csv_row

    def commit_and_stop(row):
        commit(row)
        poller._stop.set()

    poller._write_csv_row = commit_and_stop
    poller._poll_loop()
    with (tmp_path / "sample.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    return poller, rows


@pytest.mark.parametrize("ecu_id", ECU_IDS)
@pytest.mark.parametrize("telegram", [False, True])
def test_signed_roughness_joins_one_ordinary_sample_and_csv_row(tmp_path, ecu_id, telegram):
    fake = MemoryDS2()
    poller, rows = poll_once(tmp_path, ecu_id, telegram, fake)
    assert poller.terminal_error is None
    assert not poller.pop_errors()
    assert poller.csv_rows == 1 and len(rows) == 1
    for name, value in zip(NAMES, VALUES):
        assert poller.latest_values()[name] == (str(value), "raw")
        assert rows[0][name] == str(value)
    assert rows[0]["Engine RPM"] == "900"
    sequence, dropped, count, channels, samples = poller.completed_samples_since(0)
    assert (sequence, dropped, count, len(samples)) == (1, 0, 1, 1)
    sample = dict(zip((name for name, _unit in channels), samples[0][2]))
    assert [sample[name] for name in NAMES] == list(map(str, VALUES))
    assert not any("mask" in name.lower() or "FD30" in name for name in rows[0])
    if telegram:
        assert 1 <= len(fake.entries) <= 49
        assert fake.polls == 1
        assert not fake.reads  # All stock measurements and validity bytes fit.
        assert len(fake.reads) + fake.polls < len(poller._tel_blocks)
    else:
        assert fake.entries is None and fake.polls == 0
        assert fake.reads.count((0xE854, 14)) == 1
        assert any(address <= 0xFD14 and address + length >= 0xFD32
                   for address, length in fake.reads)
        assert len(fake.reads) == (10 if ecu_id == "1429861" else 12)


@pytest.mark.parametrize("telegram", [False, True])
@pytest.mark.parametrize("state,valid", [
    ({"rpm": 0}, (False,) * 6),
    ({"running": 0}, (False,) * 6),
    ({"faults": 2}, (False,) * 6),
    ({"faults": 4}, (False,) * 6),
    ({"faults": 8}, (False,) * 6),
    ({"mask": 0x25}, (True, False, True, False, False, True)),
])
def test_ineligible_ram_values_are_blank_in_same_sample(tmp_path, telegram, state, valid):
    poller, rows = poll_once(tmp_path, "1429861", telegram, MemoryDS2(**state))
    assert len(rows) == 1
    for name, value, eligible in zip(NAMES, VALUES, valid):
        assert rows[0][name] == (str(value) if eligible else "")
        assert poller.latest_values()[name][0] == (str(value) if eligible else "—")


def test_stop_during_batch_supplement_discards_partial_sample(tmp_path):
    fake = MemoryDS2()
    poller = live_data.LiveDataPoller(
        interval=0, ecu_id="1437806", use_telegram=True, ds2=fake)
    poller._profile_ready = True
    # Install enough admitted patch channels to exercise grouped overflow.
    poller._configure_parameters(live_data.telegram_params_for(
        "1437806", include_cut=True, include_launch=True))
    assert len(poller._batch_supplements) > 1
    read = fake.read_mem

    def stop_after_first_read(address, length):
        result = read(address, length)
        poller._stop.set()
        return result

    fake.read_mem = stop_after_first_read
    poller._poll_loop()
    first = poller._batch_supplements[0]
    assert fake.reads == [(first.start, first.size)]
    assert poller.completed_samples_since(0)[0] == 0
    assert not any(name in poller.latest_values() for name in NAMES)


def test_short_batch_supplement_does_not_commit_stale_roughness():
    fake = MemoryDS2()
    poller = live_data.LiveDataPoller(
        interval=0, ecu_id="1437806", use_telegram=True, ds2=fake)
    poller._profile_ready = True
    poller._configure_parameters(live_data.telegram_params_for(
        "1437806", include_cut=True, include_launch=True))
    assert poller._batch_supplements

    def short_read(address, length):
        poller._stop.set()
        return bytes(length - 1)

    fake.read_mem = short_read
    poller._poll_loop()
    assert poller.completed_samples_since(0)[0] == 0
    assert any("short supplemental RAM read" in error for error in poller.pop_errors())


def test_unverified_ecu_does_not_acquire_roughness_metadata():
    poller = live_data.LiveDataPoller(ecu_id="1429373")
    assert not poller._roughness_params
    assert not any(name in dict(poller.resolved_rows()) for name in NAMES)
    assert not any(block.start == 0xE854 for block in poller._tel_blocks)


@pytest.mark.parametrize("ecu_id", ECU_IDS)
@pytest.mark.parametrize("cut,launch", [(None, None), ("V16", None),
                                       ("V16", "legacy"), ("V16", "V16"),
                                       ("legacy", "legacy")])
def test_patch_and_native_channels_follow_independent_admission(monkeypatch, ecu_id, cut, launch):
    monkeypatch.setattr(live_data, "detect_cut_runtime", lambda *_args: cut)
    monkeypatch.setattr(live_data, "detect_launch_runtime", lambda *_args: launch)
    monkeypatch.setattr(live_data, "detect_alphan_failsafe_runtime", lambda *_args: False)
    poller = live_data.LiveDataPoller(ecu_id=ecu_id, ds2=MemoryDS2())
    poller._prepare_live_profile()
    ids = {p.id for p in poller._tel_params}
    assert "BS_CUT_FUEL_ADD_B1" in ids and "BS_CUT_O2_FRONT_B1" in ids
    assert ("BS_CUT_IGNITION_SWITCH" in ids) == (cut is not None)
    assert ("BS_CUT_LAUNCH_SWITCH" in ids) == (cut is not None and launch == cut)
    assert ("BS_CUT_TIMING_STATE" in ids) == (cut == "V16")
    assert ("BS_CUT_LAUNCH_CUT_IGN_ANGLE" in ids) == (cut == launch == "V16")
    assert ("BS_CUT_LEGACY_FD5A7" in ids) == (cut == launch == "legacy")
    assert 1 <= len(poller._batch_entries) <= 49


@pytest.mark.parametrize("ecu_id", ECU_IDS)
def test_batch_reuses_core_bytes_for_patch_aliases(tmp_path, monkeypatch, ecu_id):
    monkeypatch.setattr(live_data, "detect_cut_runtime", lambda *_args: "V16")
    monkeypatch.setattr(live_data, "detect_launch_runtime", lambda *_args: "V16")
    monkeypatch.setattr(live_data, "detect_alphan_failsafe_runtime", lambda *_args: False)
    fake = MemoryDS2()
    fake.memory[0xE8D0] = 128
    poller, rows = poll_once(tmp_path, ecu_id, True, fake, prepare_profile=True)
    assert poller.terminal_error is None and not poller.pop_errors()
    assert len(rows) == 1 and 1 <= len(fake.entries) <= 49
    assert rows[0]["Throttle Position"] == "50.2"
    assert rows[0]["Launch TPS (threshold scale)"] == ("59.98" if ecu_id == "1429861" else "60.2")
    assert not any(address <= 0xE8D0 < address + length for address, length in fake.reads)


@pytest.mark.parametrize("telegram", [False, True])
def test_extended_alphan_values_become_blank_on_current_speed_density_sample(tmp_path, monkeypatch, telegram):
    monkeypatch.setattr(live_data, "detect_cut_runtime", lambda *_args: None)
    monkeypatch.setattr(live_data, "detect_alphan_failsafe_runtime", lambda *_args: True)
    fake = MemoryDS2()
    fake.memory[0x133BF] = 2
    fake.memory[0xE80A:0xE80C] = (1234).to_bytes(2, "little")
    fake.memory[0xE80C:0xE80E] = (2345).to_bytes(2, "little")
    poller = live_data.LiveDataPoller(
        interval=0, ecu_id="SHINDE1", use_telegram=telegram, ds2=fake)
    poller._pending_log_path = str(tmp_path / "alphan.csv")
    commit = poller._write_csv_row

    def commit_and_change_mode(row):
        commit(row)
        if poller.csv_rows == 1:
            fake.memory[0xFD23] |= 2  # SET is Speed Density; retain stale mirror bytes.
        else:
            poller._stop.set()

    poller._write_csv_row = commit_and_change_mode
    poller._poll_loop()
    assert poller.terminal_error is None and not poller.pop_errors()
    with (tmp_path / "alphan.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 2
    assert rows[0]["Extended Engine Load"] == f"{1234 * .021195 * 4:.1f}"
    assert rows[0]["Extended MAF / Model"] == "2345.0"
    assert rows[0]["Speed Density Mode"] == "Inactive"
    assert rows[1]["Extended Engine Load"] == rows[1]["Extended MAF / Model"] == ""
    assert rows[1]["Speed Density Mode"] == "Active"
    assert poller.latest_values()["Extended Engine Load"][0] == "—"


@pytest.mark.parametrize("installed,scale,expected", [(False, 2, False), (True, 0, False),
                                                     (True, 3, False), (True, 2, True)])
def test_extended_alphan_requires_exact_patch_and_x4_scale(monkeypatch, installed, scale, expected):
    monkeypatch.setattr(live_data, "detect_cut_runtime", lambda *_args: None)
    monkeypatch.setattr(live_data, "detect_alphan_failsafe_runtime", lambda *_args: installed)
    fake = MemoryDS2()
    fake.memory[0x133BF] = scale
    poller = live_data.LiveDataPoller(ecu_id="SHINDE1", ds2=fake)
    poller._prepare_live_profile()
    names = dict(poller.resolved_rows())
    assert ("Extended Engine Load" in names) == expected
    assert ("Extended MAF / Model" in names) == expected
    assert (0x133BF, 1) in fake.reads if installed else (0x133BF, 1) not in fake.reads
