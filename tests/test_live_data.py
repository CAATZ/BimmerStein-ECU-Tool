import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import csv as _csv
import pytest
import live_data


class _FakeDS2:
    """Zero-fills each block read; fires `on_stop` on a chosen read to emulate the
    user pressing Stop part-way through a poll cycle."""
    def __init__(self, stop_on, on_stop):
        self.calls = 0
        self._stop_on = stop_on
        self._on_stop = on_stop

    def read_mem(self, start, size):
        self.calls += 1
        if self.calls == self._stop_on:
            self._on_stop()          # Stop pressed while this block is being read
        return bytes(size)


def _run_reads_loop(tmp_path, stop_on):
    """Drive the standard (cmd 0x06) poll loop synchronously with a Stop injected on
    the `stop_on(n_blocks)`-th block read. Returns (header, data_rows) from the CSV."""
    p = live_data.LiveDataPoller(interval=0.0, use_telegram=False, ecu_id="1437806")
    n_blocks = len(p._tel_blocks)
    assert n_blocks >= 2, "test needs a multi-block cycle to form a partial row"
    p._ds2 = _FakeDS2(stop_on(n_blocks), p._stop.set)
    log = os.path.join(str(tmp_path), "live.csv")
    p._open_csv(log)
    p._poll_loop_ds2_reads()         # loops until _stop is set
    p._close_csv()
    with open(log, newline="", encoding="utf-8") as f:
        rows = list(_csv.reader(f))
    return rows[0], rows[1:]


def test_stop_midcycle_does_not_write_a_partial_row(tmp_path):
    # Stop fires during block 1 of the SECOND cycle: cycle 1 completes, cycle 2 is
    # interrupted before its remaining blocks are read. The interrupted cycle's
    # half-filled row must NOT reach the CSV (it breaks log viewers).
    header, data = _run_reads_loop(tmp_path, lambda n: n + 1)
    assert len(data) == 1, f"a partial final row was written ({len(data)} data rows)"
    assert len(data[0]) == len(header)          # the kept row is well-formed


def test_cycle_completed_before_stop_is_still_written(tmp_path):
    # Stop fires during the LAST block of the FIRST cycle: that cycle still reads all
    # its blocks, so its complete row must be kept (the fix must not over-discard).
    header, data = _run_reads_loop(tmp_path, lambda n: n)
    assert len(data) == 1, f"a complete cycle was wrongly dropped on stop ({len(data)})"


def test_batch_mode_uses_one_poll(tmp_path):
    class FakeBatchDS2:
        def __init__(self, poller):
            self.poller = poller
            self.setup_ids = []
            self.setup_entries = []
            self.polls = 0

        def setup_telegram_batch(self, ecu_id="", entries=None):
            self.setup_ids.append(ecu_id)
            self.setup_entries.append(tuple(entries or ()))

        def poll_telegram_batch(self):
            self.polls += 1
            self.poller._stop.set()
            return bytes(38)

        def read_mem(self, address, length):
            raise AssertionError("batch polling must not read unrequested memory")

    p = live_data.LiveDataPoller(interval=0, use_telegram=True, ecu_id="1437806")
    fake = FakeBatchDS2(p)
    p._ds2 = fake
    log = str(tmp_path / "batch.csv")
    p._open_csv(log)
    p._poll_loop_ds2_batch()
    p._close_csv()

    assert fake.setup_ids == ["1437806"]
    assert len(fake.setup_entries[0]) == 24
    assert fake.polls == 1
    assert p.csv_rows == 1


def test_csv_rows_are_buffered_between_periodic_flushes(tmp_path):
    class FlushSpy:
        def __init__(self):
            self.flushes = 0

        def flush(self):
            self.flushes += 1

    class WriterSpy:
        def writerow(self, _row):
            pass

    p = live_data.LiveDataPoller()
    p._csv_file = FlushSpy()
    p._csv_writer = WriterSpy()
    p._csv_last_flush = live_data.time.monotonic()
    p._write_csv_row({})
    assert p._csv_file.flushes == 0


def test_completed_sample_cursor_returns_exact_ordered_rows(tmp_path):
    p = live_data.LiveDataPoller(ecu_id="1437806")
    p._open_csv(str(tmp_path / "deltas.csv"))
    for rpm in ("812", "824"):
        row = p._csv_row_base()
        row["Engine RPM"] = rpm
        p._write_csv_row(row)

    sequence, dropped, csv_rows, channels, samples = p.completed_samples_since(0)
    rpm_index = channels.index(("Engine RPM", "RPM"))
    assert (sequence, dropped, csv_rows) == (2, 0, 2)
    assert [sample[0] for sample in samples] == [1, 2]
    assert [sample[2][rpm_index] for sample in samples] == ["812", "824"]
    assert [sample[0] for sample in p.completed_samples_since(1)[4]] == [2]
    assert p.completed_samples_since(2)[4] == ()
    p._close_csv()


def test_completed_sample_cursor_reports_ring_overflow():
    p = live_data.LiveDataPoller(ecu_id="1437806")
    p._sample_started = live_data.time.monotonic()
    for rpm in range(257):
        p._write_csv_row({"Engine RPM": str(rpm)})

    sequence, dropped, _csv_rows, _channels, samples = p.completed_samples_since(0)
    assert sequence == 257
    assert dropped == 1
    assert len(samples) == 256
    assert (samples[0][0], samples[-1][0]) == (2, 257)
    with pytest.raises(ValueError, match="ahead"):
        p.completed_samples_since(258)


def _batch_payload(layout, values):
    raw = bytearray(38)
    offset = 2
    for index, entry in enumerate(layout):
        if index == 20:
            offset = 30
        nbytes = entry[2]
        raw[offset:offset + nbytes] = int(values.get(index, 0)).to_bytes(nbytes, "big")
        offset += nbytes
    return bytes(raw)


def test_universal_batch_uses_all_six_previous_spare_slots():
    layout = live_data.batch_layout_for("1437806")
    assert len(layout) == 24
    assert sum(entry[2] for entry in layout[:20]) == 26
    assert sum(entry[2] for entry in layout[20:]) == 8
    assert [layout[index][1] for index in (13, 18, 19, 21, 22, 23)] == [
        0xDA56, 0xFD24, 0xFD14, 0xFA9A, 0xFA98, 0xFA9E,
    ]

    latest = {}
    csv_row = {}
    raw = _batch_payload(layout, {
        18: 0x02,       # full load on; closed-throttle active-low bit remains clear
        19: 0x2A,       # part load, decel fuel cut, and engine start
        21: 512,
        22: 0,
        23: 1023,
    })
    live_data._parse_ds2_batch(raw, latest, live_data.threading.Lock(), csv_row, layout)

    assert latest["Closed Throttle"][0] == "Active"
    assert latest["Full Load"][0] == "Active"
    assert latest["Part Load"][0] == "Active"
    assert latest["Decel Fuel Cut"][0] == "Active"
    assert latest["Engine Start"][0] == "Active"
    assert latest["Front O2 B1 Voltage"] == ("2.502", "V")
    assert latest["Front O2 B2 Voltage"] == ("0.000", "V")
    assert latest["MAF Sensor Voltage"] == ("5.000", "V")


@pytest.mark.parametrize(
    ("ecu_id", "battery_address"),
    [
        ("1429861", 0xFB47),
        ("1437806", 0xFC9D),
        ("1406464", 0xFC9D),
        ("SHINDE1", 0xFC9D),
    ],
)
def test_verified_firmware_uses_normalized_tps_and_correct_battery(
        ecu_id, battery_address):
    direct = {param.name: param.address for param in live_data.telegram_params_for(ecu_id)}
    batch = {entry[0]: entry[1] for entry in live_data.batch_layout_for(ecu_id) if entry[0]}

    assert direct["Throttle Position"] == batch["Throttle Position"] == 0xE8D0
    assert direct["Battery Voltage"] == batch["Battery Voltage"] == battery_address


def test_selected_definition_drives_direct_and_batch_address_conversion(tmp_path):
    source = live_data.bundled_logger_definition_path().read_text(encoding="utf-8")
    selected = tmp_path / "selected.xml"
    selected.write_text(
        source.replace("0x0000DA2A", "0x0000D000", 1).replace(
            'endian="little" expr="x" format="0" gauge_min="0"',
            'endian="little" expr="x*2" format="0" gauge_min="0"', 1),
        encoding="utf-8",
    )

    rpm = next(param for param in live_data.telegram_params_for(
        "1437806", definition_path=selected) if param.name == "Engine RPM")
    layout = live_data.batch_layout_for("1437806", definition_path=selected)
    batch_rpm = next(entry for entry in layout if entry[0] == "Engine RPM")

    assert rpm.address == batch_rpm[1] == 0xD000
    assert rpm.convert(1000) == batch_rpm[4](1000) == 2000


def test_non_memory_romraider_selectors_are_not_read_as_ram(tmp_path):
    source = live_data.bundled_logger_definition_path().read_text(encoding="utf-8")
    selected = tmp_path / "selectors.xml"
    selected.write_text(
        source.replace("0x0000DA2A", "0x00000007", 1).replace(
            '<ecuparam id="P12"', '<ecuparam id="P12" groupsize="3"', 1),
        encoding="utf-8",
    )

    names = {param.name for param in live_data.telegram_params_for(
        "1437806", definition_path=selected)}
    assert "Engine RPM" not in names
    assert "Mass Air Flow" not in names


def test_display_omits_dead_lambda_and_names_measured_vanos_angle():
    names = [name for name, _unit in live_data.display_rows()]

    assert "Lambda Upstream" not in names
    assert "VANOS Advance" not in names
    assert "VANOS Measured Angle" in names


def test_live_display_specs_cover_packaged_rows_and_unknowns_fail_closed():
    rows = set(live_data.display_rows())
    gauges = {
        row for row in rows
        if live_data.live_display_spec(*row)["kind"] == "dial"
    }
    groups = [
        gauges,
        set(live_data._LIVE_STATUS_CHANNELS),
        set(live_data._LIVE_VALUE_CHANNELS),
    ]
    assert tuple(map(len, groups)) == (24, 7, 2)
    assert sum(map(len, groups)) == len(set().union(*groups))
    assert rows == set().union(*groups)
    for row in gauges:
        spec = live_data.live_display_spec(*row)
        minimum, maximum, step = (
            spec["minimum"], spec["maximum"], spec["step"])
        assert minimum < maximum
        assert 0 < step <= maximum - minimum
    assert live_data.live_display_spec("Engine RPM", "RPM")["kind"] == "dial"
    assert live_data.live_display_spec("Engine RPM", "rpm")["kind"] == "value"
    assert live_data.live_display_spec("Future Channel", "psi") == {
        "kind": "value", "evidence": "unknown",
    }


def test_wideband_batch_preserves_the_proven_38_byte_response_shape():
    layout = live_data.batch_layout_for(
        "SHINDE1", live_data.PROFILE_WIDEBAND, 0xFA98)
    assert len(layout) == 24
    assert sum(entry[2] for entry in layout[:20]) == 26
    assert sum(entry[2] for entry in layout[20:]) == 8
    assert [layout[index][1] for index in (13, 18, 19, 21, 22, 23)] == [
        0xE800, 0xFD24, 0xFD14, 0xFA98, 0xFA9E, 0xE810,
    ]

    latest = {}
    raw = _batch_payload(layout, {
        13: 117,        # 14.10 AFR
        21: 512,
        22: 1023,
        23: 0x8032,     # E811=0x80 target; E810=0x32 target-voltage byte
    })
    live_data._parse_ds2_batch(raw, latest, live_data.threading.Lock(), {}, layout)

    assert latest["Wideband AFR"] == ("14.10", "AFR")
    assert latest["AFR Target"] == ("14.65", "AFR")
    assert latest["Wideband Input Voltage"] == ("2.502", "V")
    assert latest["MAF Sensor Voltage"] == ("5.000", "V")


def test_live_parameters_do_not_inherit_unverified_ram_addresses():
    cal59 = {param.name: param for param in live_data.telegram_params_for("1429373")}
    assert "Throttle Position" not in cal59
    assert "Battery Voltage" not in cal59
    assert "Injector PW" not in cal59
    assert "Fuel Trim LT" not in cal59
    assert "Engine Load" not in cal59
    assert "Engine RPM" in cal59

    assert live_data.telegram_params_for("1438068") == []
    assert live_data.telegram_params_for("1438137") == []
    assert live_data.batch_layout_for("1429373") is None
    assert live_data.batch_layout_for("1438068") is None


def test_unmapped_batch_uses_available_direct_parameters_instead():
    class FakeDS2:
        def __init__(self, poller):
            self.poller = poller
            self.setup_called = False
            self.reads = 0

        def setup_telegram_batch(self, **_kwargs):
            self.setup_called = True
            raise AssertionError("unmapped ECU must not configure a fixed batch")

        def read_mem(self, _address, length):
            self.reads += 1
            self.poller._stop.set()
            return bytes(length)

    p = live_data.LiveDataPoller(interval=0, use_telegram=True, ecu_id="1429373")
    fake = FakeDS2(p)
    p._ds2 = fake
    p._poll_loop_ds2_batch()

    assert not fake.setup_called
    assert fake.reads
    assert any("not mapped" in error for error in p.pop_errors())


def test_forced_telegram_does_not_silently_fallback_to_standard_ds2():
    class FakeDS2:
        def setup_telegram_batch(self, **_kwargs):
            raise RuntimeError("unsupported")

        def read_mem(self, address, length):
            raise AssertionError("forced Telegram must not use standard DS2 reads")

    p = live_data.LiveDataPoller(
        interval=0,
        use_telegram=True,
        telegram_fallback=False,
        ecu_id="1437806",
        ds2=FakeDS2(),
    )
    p._poll_loop_ds2_batch()

    assert p.terminal_error == "Telegram setup failed (unsupported)"


def test_ms413_profile_detection_uses_runtime_flag_and_selected_input():
    class FakeWidebandDS2:
        def __init__(self, poller):
            self.poller = poller
            self.reads = []
            self.entries = None

        def read_mem(self, address, length):
            self.reads.append((address, length))
            values = {
                0xFD22: b"\x00\x01",       # runtime WBO2 enable mask 0x0100
                0xFD5A: b"\x04\x00",       # narrowband emulation enabled
                0x133C0: b"\x98\xFA",      # selected pointer = Front O2 Bank 2
            }
            return values[address]

        def setup_telegram_batch(self, ecu_id="", entries=None):
            self.entries = tuple(entries or ())

        def poll_telegram_batch(self):
            self.poller._stop.set()
            return bytes(38)

    p = live_data.LiveDataPoller(
        interval=0, use_telegram=True, ecu_id="1406464", ecu_variant="MS41.3")
    fake = FakeWidebandDS2(p)
    p._ds2 = fake
    p._poll_loop_ds2_batch()

    assert p._profile == live_data.PROFILE_WIDEBAND
    assert p._wideband_input_addr == 0xFA98
    assert fake.entries[21] == (0xFA98, 2)
    assert p.latest_values()["Wideband Mode"] == ("Enabled", "")
    assert p.latest_values()["Wideband Input Source"] == (
        "Front O2 Bank 2 (0xFA98)", "")
    assert p.latest_values()["Narrowband Emulation"] == ("Enabled", "")
    assert "Wideband AFR" in p.active_profile_names
    assert "EVAP Purge Duty" not in p.active_profile_names


def test_ms413_with_wideband_disabled_keeps_universal_profile():
    class FakeStockProfileDS2:
        def __init__(self, poller):
            self.poller = poller
            self.reads = []
            self.entries = None

        def read_mem(self, address, length):
            self.reads.append((address, length))
            return {
                0xFD22: b"\x00\x00",
                0xFD5A: b"\x00\x00",
            }[address]

        def setup_telegram_batch(self, ecu_id="", entries=None):
            self.entries = tuple(entries or ())

        def poll_telegram_batch(self):
            self.poller._stop.set()
            return bytes(38)

    p = live_data.LiveDataPoller(
        interval=0, use_telegram=True, ecu_id="1406464", ecu_variant="MS41.3")
    fake = FakeStockProfileDS2(p)
    p._ds2 = fake
    p._poll_loop_ds2_batch()

    assert p._profile == live_data.PROFILE_STANDARD
    assert (0x133C0, 2) not in fake.reads
    assert fake.entries[13] == (0xDA56, 1)
    assert p.latest_values()["Wideband Mode"] == ("Disabled", "")
    assert "EVAP Purge Duty" in p.active_profile_names
    assert "Wideband AFR" not in p.active_profile_names


def test_direct_read_profile_matches_wideband_batch_selection():
    params = live_data.telegram_params_for(
        "SHINDE1", live_data.PROFILE_WIDEBAND, 0xFA98)
    by_name = {param.name: param for param in params}
    assert by_name["Wideband Input Voltage"].address == 0xFA98
    assert by_name["Wideband AFR"].address == 0xE800
    assert by_name["AFR Target"].address == 0xE811
    assert "EVAP Purge Duty" not in by_name
    assert "Front O2 B1 Voltage" not in by_name


def test_read_adaptations_decodes_fuel_axes_and_six_knock_tables():
    def fuel_block(additive, ltft):
        return (additive.to_bytes(2, "little") + bytes(6)
                + ltft.to_bytes(2, "little"))

    responses = {
        (0xF040, 10): fuel_block(0x800A, 0x9000),
        (0xF0FC, 10): fuel_block(0x7FF6, 0x7000),
        (0xE8DE, 2): (1000).to_bytes(2, "little", signed=True),
        (0x12606, 4): bytes.fromhex("29 3C 4E 5C"),
        (0x125D9, 16): bytes.fromhex(
            "0F 19 1F 26 2F 36 3E 4E 5E 6D 7D 8A 9C AC BC C3"),
    }
    for index in range(6):
        responses[(0xD840 + index * 0x40, 0x40)] = bytes([0x80 - index]) * 0x40

    class FakeDS2:
        def __init__(self):
            self.reads = []

        def read_mem(self, address, length):
            self.reads.append((address, length))
            return responses[(address, length)]

    ds2 = FakeDS2()
    result = live_data.read_adaptations(ds2, "1437806")

    assert result["additive"] == pytest.approx([0.0534, -0.0534])
    assert result["ltft"] == pytest.approx([6.250095, -6.250095])
    assert result["throttle"] == pytest.approx(1.526)
    assert result["load"] == pytest.approx([223.3294, 326.8235, 424.8706, 501.1294])
    assert result["rpm"] == [
        480, 800, 992, 1216, 1504, 1728, 1984, 2496,
        3008, 3488, 4000, 4416, 4992, 5504, 6016, 6240,
    ]
    assert len(result["knock"]) == 6
    assert all(len(table) == 16 and all(len(row) == 4 for row in table)
               for table in result["knock"])
    assert result["knock"][0][0][0] == 0.0
    assert result["knock"][5][15][3] == -1.875
    assert ds2.reads[-6:] == [
        (0xD840, 0x40), (0xD880, 0x40), (0xD8C0, 0x40),
        (0xD900, 0x40), (0xD940, 0x40), (0xD980, 0x40),
    ]


def test_read_adaptations_uses_cal59_axes_without_unproven_fuel_addresses():
    responses = {
        (0xE8DE, 2): (500).to_bytes(2, "little", signed=True),
        (0x12144, 4): bytes.fromhex("29 3C 4E 5C"),
        (0x12117, 16): bytes.fromhex(
            "0F 19 1F 26 2F 36 3E 4E 5E 6D 7D 8A 9C AC BC C3"),
    }
    for index in range(6):
        responses[(0xD840 + index * 0x40, 0x40)] = bytes([0x80]) * 0x40

    class FakeDS2:
        def __init__(self):
            self.reads = []

        def read_mem(self, address, length):
            self.reads.append((address, length))
            return responses[(address, length)]

    ds2 = FakeDS2()
    result = live_data.read_adaptations(ds2, "1429373")

    assert result["additive"] == [None, None]
    assert result["ltft"] == [None, None]
    assert result["throttle"] == pytest.approx(0.763)
    assert result["load"] == pytest.approx([223.3294, 326.8235, 424.8706, 501.1294])
    assert result["rpm"][0] == 480
    assert (0x12144, 4) in ds2.reads
    assert (0x12117, 16) in ds2.reads


def test_read_adaptations_rejects_unmapped_axis_variant():
    with pytest.raises(ValueError, match="not mapped"):
        live_data.read_adaptations(object(), "1438137")


def test_capability_queries_follow_existing_exact_definition_owners():
    assert live_data.live_data_supported("1437806")
    assert live_data.adaptation_read_supported("1429373")
    assert not live_data.adaptation_read_supported("1438137")


def test_resolved_live_rows_drive_display_and_csv_without_a_mobile_whitelist(tmp_path):
    poller = live_data.LiveDataPoller(ecu_id="SHINDE1")
    poller._active_profile_names = {"Wideband Mode"}
    poller._latest = {
        "Engine RPM": ("812", "RPM"),
        "VANOS Measured Angle": ("4.5", "Â°"),
        "Wideband Mode": ("Enabled", ""),
    }

    assert poller.display_values() == [
        ("Engine RPM", "812", "RPM"),
        ("VANOS Measured Angle", "4.5", "Â°"),
        ("Wideband Mode", "Enabled", ""),
    ]

    path = tmp_path / "resolved.csv"
    poller._pending_log_path = str(path)
    poller._ensure_csv()
    poller._close_csv()
    header = path.read_text(encoding="utf-8").splitlines()[0]
    assert "VANOS Measured Angle" in header
    assert "Wideband Mode" in header
