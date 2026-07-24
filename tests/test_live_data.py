import os, sys
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


def test_batch_mode_uses_one_poll_without_supplementary_reads(tmp_path):
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

        def read_mem(self, *_args):
            raise AssertionError("batch mode issued a supplementary RAM read")

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


def test_read_adaptations_rejects_unmapped_axis_variant():
    with pytest.raises(ValueError, match="not mapped"):
        live_data.read_adaptations(object(), "1429373")
