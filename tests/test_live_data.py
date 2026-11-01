import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import csv as _csv
import pytest
import live_data
from cut_runtime import detect_cut_runtime


@pytest.mark.parametrize("ecu_id,family,revision", [
    ("1429861", "MS41.0", "V18"), ("1437806", "MS41.1", "V18"),
    ("1406464", "MS41.2", "V18"), ("SHINDE1", "MS41.3", "V18"),
    ("1429861", "MS41.0", "V16"), ("1437806", "MS41.1", "V16"),
    ("1406464", "MS41.2", "V16"), ("SHINDE1", "MS41.3", "V16"),
    ("1429861", "MS41.0", "V15"), ("1437806", "MS41.1", "V14"),
    ("1406464", "MS41.2", "V14"), ("SHINDE1", "MS41.3", "V14"),
    ("SHINDE1", "MS41.3", "V12"), ("SHINDE1", "MS41.3", "V11"),
    ("SHINDE1", "MS41.3", "V10"),
])
def test_cut_ram_abi_follows_installed_program_not_native_marker(ecu_id, family, revision):
    from engines.patcher import patch_ms41
    from tests.conftest import ref

    suffix = {"MS41.0": "_ms410", "MS41.1": "_ms411", "MS41.2": "_ms412",
              "MS41.3": ""}[family]
    image, _ = patch_ms41.build(
        ref(family), ["ignition_cut_" + revision.lower() + suffix], allow_deprecated=True)
    reads = []

    class ImageDS2:
        def read_mem(self, address, length):
            reads.append((address, length))
            if address < 0x10000:
                # Native roughness can produce an A0-looking high byte at E847.
                return b"\xa0" if (address, length) == (0xE847, 1) else bytes(length)
            return bytes(image[(address + i) ^ 0x4000] for i in range(length))

    expected = "V16" if revision in ("V16", "V18") else "legacy"
    assert detect_cut_runtime(ImageDS2(), ecu_id) == expected
    assert all(address >= 0x18000 for address, _length in reads)
    poller = live_data.LiveDataPoller(ecu_id=ecu_id, ds2=ImageDS2())
    poller._prepare_live_profile()
    states = {param.name: param.address for param in poller._tel_params
              if param.id in {"BS_CUT_IGNITION_REQUEST", "BS_CUT_LAUNCH_IGN_REQUEST",
                              "BS_CUT_LAUNCH_FUEL_ACTIVE", "BS_CUT_INTENTIONAL", "BS_CUT_RUNTIME"}}
    assert len(states) == 3  # The fixture installs ignition cut, not Launch.
    assert set(states.values()) == {0xD801 if revision in ("V16", "V18") else 0xE847}

    # A RAM marker cannot compensate for a missing activation hook.
    from cut_runtime import cut_runtime_probes
    address, expected_bytes = cut_runtime_probes(ecu_id)[0][1][0]
    image = bytearray(image)
    for i in range(len(expected_bytes)):
        image[(address + i) ^ 0x4000] = 0
    assert detect_cut_runtime(ImageDS2(), ecu_id) is None


def test_native_a0_byte_without_patch_code_never_enables_cut_channels():
    reads = []

    class NativeDS2:
        def read_mem(self, address, length):
            reads.append((address, length))
            return b"\xa0" if (address, length) == (0xE847, 1) else bytes(length)

    poller = live_data.LiveDataPoller(ecu_id="1429861", ds2=NativeDS2())
    poller._prepare_live_profile()
    assert not poller._cut_patch_active
    assert (0xE847, 1) not in reads
    assert not any(param.id.startswith("BS_CUT_") and param.id not in live_data._NATIVE_LIVE_IDS
                   for param in poller._tel_params)


class _RoughnessRAMDS2:
    def __init__(self, values=(-2, -1, 7, 0, -32768, 32767), *,
                 mask=0x3F, rpm=800, running=4, faults=0):
        self.calls = []
        self.memory = {
            (0xE854, 14): b"".join(value.to_bytes(2, "little", signed=True)
                                  for value in values) + bytes((0, mask)),
            (0xDA2A, 2): rpm.to_bytes(2, "little"),
            (0xFD14, 1): bytes((running,)),
            (0xFD30, 2): faults.to_bytes(2, "little"),
        }

    def read_mem(self, address, length):
        self.calls.append((address, length))
        return self.memory[address, length]


@pytest.mark.parametrize("variant", ["MS41.0", "MS41.1", "MS41.2", "MS41.3"])
def test_roughness_ram_preserves_signed_little_endian_values(variant, monkeypatch):
    monkeypatch.setattr(live_data.time, "monotonic", lambda: 123.5)
    ds2 = _RoughnessRAMDS2()
    reading = live_data.read_ram_roughness(ds2, variant)
    assert reading.cylinders == (-2, -1, 7, 0, -32768, 32767)
    assert reading.timestamp == 123.5
    assert reading.rpm == 800
    assert reading.validity_mask == reading.status == 0x3F
    assert reading.state == "available"
    assert ds2.calls == [(0xE854, 14), (0xDA2A, 2), (0xFD14, 1), (0xFD30, 2)]


@pytest.mark.parametrize("variant", ["MS41.0", "MS41.1", "MS41.2", "MS41.3"])
@pytest.mark.parametrize("options,state", [
    ({"rpm": 0}, "engine_stopped"),
    ({"running": 0}, "unavailable"),
    ({"faults": 2}, "unavailable"),
    ({"faults": 4}, "unavailable"),
    ({"faults": 8}, "unavailable"),
    ({"mask": 0}, "unavailable"),
])
def test_roughness_does_not_show_stale_or_engine_stopped_values(variant, options, state):
    reading = live_data.read_ram_roughness(_RoughnessRAMDS2(values=(-5,) * 6, **options), variant)
    assert reading.cylinders == (None,) * 6
    assert reading.state == state


@pytest.mark.parametrize("variant", ["MS41.0", "MS41.1", "MS41.2", "MS41.3"])
def test_roughness_eligibility_bits_follow_cylinder_order(variant):
    reading = live_data.read_ram_roughness(
        _RoughnessRAMDS2(values=(-5,) * 6, mask=0xEA, faults=0xF001), variant)
    assert reading.cylinders == (None, -5, None, -5, None, -5)
    assert reading.validity_mask == 0x2A
    assert reading.state == "partial"
    reading = live_data.read_ram_roughness(_RoughnessRAMDS2(values=(-5,) * 6), variant)
    assert reading.cylinders == (-5,) * 6
    assert reading.state == "available"


@pytest.mark.parametrize("address,length", [(0xE854, 14), (0xDA2A, 2), (0xFD14, 1), (0xFD30, 2)])
def test_roughness_rejects_incomplete_ram_snapshots(address, length):
    ds2 = _RoughnessRAMDS2()
    ds2.memory[address, length] = bytes(length - 1)
    with pytest.raises(ValueError, match="short roughness RAM read"):
        live_data.read_ram_roughness(ds2, "MS41.2")


class _RoughnessCommandDS2:
    def __init__(self):
        self.calls = []
        self.threads = []
        self.responses = {
            0x22: [b"\x00"], 0x23: [b""],
            0x2B: [b"\x00" + b"".join(value.to_bytes(2, "big", signed=True)
                                       for value in (-32768, -200, -1, 0, 123, 32767))],
        }

    def execute(self, command, args):
        self.calls.append((command, args))
        self.threads.append(threading.get_ident())
        outcomes = self.responses[command]
        outcome = outcomes.pop(0) if len(outcomes) > 1 else outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.mark.parametrize("variant", ["MS41.0", "MS41.1", "MS41.2", "MS41.3"])
def test_bmw_roughness_commands_use_selector_five_and_preserve_signed_words(variant, monkeypatch):
    monkeypatch.setattr(live_data.time, "monotonic", lambda: 123.5)
    ds2 = _RoughnessCommandDS2()
    live_data.start_roughness_check(ds2, variant)
    reading = live_data.read_roughness(ds2, variant)
    live_data.stop_roughness_check(ds2, variant)
    assert ds2.calls == [(0x22, b"\x05"), (0x2B, b"\x05"), (0x23, b"\x05")]
    assert reading.cylinders == (-32768, -200, -1, 0, 123, 32767)
    assert reading.timestamp == 123.5 and reading.status == 0
    assert reading.rpm is None and reading.state == "available"


def test_roughness_unknown_family_never_contacts_ecu():
    ds2 = _RoughnessCommandDS2()
    with pytest.raises(ValueError, match="not mapped"):
        live_data.read_roughness(ds2, "unknown")
    assert ds2.calls == []


@pytest.mark.parametrize("payload", [b"", b"\x00" * 12, b"\x00" * 14])
def test_bmw_roughness_rejects_incomplete_or_extra_data(payload):
    ds2 = _RoughnessCommandDS2()
    ds2.responses[0x2B] = [payload]
    with pytest.raises(live_data.DS2Error, match="payload length"):
        live_data.read_roughness(ds2, "MS41.2")


def test_bmw_roughness_unknown_result_status_is_unavailable():
    ds2 = _RoughnessCommandDS2()
    ds2.responses[0x2B] = [b"\x01" + b"\xff" * 12]
    reading = live_data.read_roughness(ds2, "MS41.0")
    assert reading.status == 1 and reading.cylinders == (None,) * 6
    assert reading.state == "unavailable"


@pytest.mark.parametrize("command", [0x22, 0x23, 0x2B])
def test_bmw_roughness_busy_retry_is_bounded_and_only_retries_a1(command, monkeypatch):
    ds2 = _RoughnessCommandDS2()
    reply = ds2.responses[command][0]
    busy = live_data.DS2NegativeResponse("busy", command=command, status=0xA1)
    monkeypatch.setattr(live_data.time, "sleep", lambda _seconds: None)
    ds2.responses[command] = [busy, busy, reply]
    assert live_data._roughness_command(ds2, command) == reply
    assert len(ds2.calls) == 3
    ds2.calls.clear()
    ds2.responses[command] = [busy]
    with pytest.raises(live_data.DS2NegativeResponse):
        live_data._roughness_command(ds2, command)
    assert len(ds2.calls) == 3
    ds2.calls.clear()
    ds2.responses[command] = [live_data.DS2NegativeResponse("bad selector", command=command, status=0xB0)]
    with pytest.raises(live_data.DS2NegativeResponse):
        live_data._roughness_command(ds2, command)
    assert len(ds2.calls) == 1


def test_roughness_stop_only_joins_worker_and_cleanup_runs_after_blocked_read():
    reading = threading.Event()
    release = threading.Event()

    class BlockedDS2(_RoughnessCommandDS2):
        def execute(self, command, args):
            result = super().execute(command, args)
            if command == 0x2B:
                reading.set()
                assert release.wait(2)
            return result

    ds2 = BlockedDS2()
    poller = live_data.RoughnessPoller(ds2, "MS41.2")
    poller.start()
    try:
        assert reading.wait(2)
        assert poller.stop(timeout=0.01) is False
        assert not poller.stop_confirmed
        assert ds2.calls == [(0x22, b"\x05"), (0x2B, b"\x05")]
    finally:
        release.set()
        assert poller.stop(timeout=2) is True
    assert poller.stop_confirmed
    assert poller.stop(timeout=0) is True
    assert ds2.calls == [(0x22, b"\x05"), (0x2B, b"\x05"), (0x23, b"\x05")]
    assert all(thread != threading.get_ident() for thread in ds2.threads)
    completed = poller.drain_readings()
    assert len(completed) == 1
    assert completed[0] is poller.latest_reading()
    assert poller.drain_readings() == []


@pytest.mark.parametrize("outcome,needs_stop", [
    (live_data.DS2NegativeResponse("bad selector", command=0x22, status=0xB0), False),
    (b"\x01", False), (TimeoutError("lost start reply"), True), (b"", True),
])
def test_roughness_start_rejection_does_not_stop_but_ambiguous_start_does(outcome, needs_stop):
    ds2 = _RoughnessCommandDS2()
    ds2.responses[0x22] = [outcome]
    poller = live_data.RoughnessPoller(ds2, "MS41.2")
    poller.start()
    poller._thread.join(2)
    assert poller.stop_confirmed
    assert ds2.calls == [(0x22, b"\x05")] + ([(0x23, b"\x05")] if needs_stop else [])
    assert "start failed" in poller.pop_errors()[0]


def test_roughness_unconfirmed_cleanup_requires_explicit_worker_retry():
    retry_entered, retry_release = threading.Event(), threading.Event()

    class DS2(_RoughnessCommandDS2):
        def execute(self, command, args):
            if command == 0x23 and len([call for call in self.calls if call[0] == 0x23]):
                retry_entered.set()
                assert retry_release.wait(2)
            return super().execute(command, args)

    ds2 = DS2()
    ds2.responses[0x22] = [TimeoutError("start response lost")]
    ds2.responses[0x23] = [live_data.DS2NegativeResponse("rejected", command=0x23, status=0xB0), b""]
    poller = live_data.RoughnessPoller(ds2, "MS41.2")
    poller.start()
    poller._thread.join(2)
    assert not poller.is_running and not poller.stop_confirmed
    assert poller.cleanup_error is not None
    for _ in range(3):
        assert not poller.stop(timeout=0)
    assert len(ds2.calls) == 2
    with pytest.raises(live_data.DS2Error, match="STOP is unconfirmed"):
        poller.start()
    poller.retry_stop()
    try:
        assert retry_entered.wait(2)
        assert not poller.stop(timeout=0)  # stop Event must not cancel cleanup.
    finally:
        retry_release.set()
        assert poller.stop(timeout=2)
    assert poller.cleanup_error is None
    assert ds2.calls == [(0x22, b"\x05"), (0x23, b"\x05"), (0x23, b"\x05")]
    assert all(thread != threading.get_ident() for thread in ds2.threads)


@pytest.mark.parametrize("acquisition_time,expected_wait", [(0.2, 0.3), (0.8, 0.0)])
def test_roughness_completed_samples_have_gaps_and_deadline_based_pacing(
        monkeypatch, acquisition_time, expected_wait):
    clock = [0.0]
    waits = []
    attempts = []

    class ControlledStop:
        stopped = False

        def is_set(self):
            return self.stopped

        def set(self):
            self.stopped = True

        def wait(self, delay):
            waits.append(delay)
            clock[0] += delay
            if len(waits) == 3:
                self.set()

    def acquire(_ds2, _variant):
        clock[0] += acquisition_time
        attempts.append(clock[0])
        if len(attempts) == 2:
            raise TimeoutError("lost RAM reply")
        return live_data.RoughnessReading(0x3F, (5,) * 6, timestamp=clock[0],
                                          rpm=800, validity_mask=0x3F, state="available")

    monkeypatch.setattr(live_data.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(live_data, "read_roughness", acquire)
    poller = live_data.RoughnessPoller(_RoughnessCommandDS2(), "MS41.2", interval=0.5)
    poller._stop = ControlledStop()
    poller._run()
    samples = poller.drain_readings()
    assert len(samples) == 3 and samples[1] is None
    assert samples[0].timestamp == attempts[0]
    assert samples[2].timestamp == attempts[2]
    assert waits == pytest.approx([expected_wait] * 3)
    assert poller.latest_reading() is samples[2]
    assert poller.drain_readings() == []
    assert "lost RAM reply" in poller.pop_errors()[0]


def test_roughness_failures_clear_latest_and_stop_after_three_failed_reads(monkeypatch):
    def fail(_ds2, _variant):
        raise TimeoutError("lost RAM reply")

    monkeypatch.setattr(live_data, "read_roughness", fail)
    poller = live_data.RoughnessPoller(_RoughnessCommandDS2(), "MS41.2", interval=0.001)
    poller._latest = live_data.RoughnessReading(0x3F, (5,) * 6)
    poller._run()
    assert poller.latest_reading() is None
    assert poller.drain_readings() == [None, None, None]
    assert len(poller.pop_errors()) == 3
    assert poller.stop_confirmed


def test_roughness_pending_samples_are_bounded(monkeypatch):
    attempts = [0]
    poller = live_data.RoughnessPoller(_RoughnessCommandDS2(), "MS41.2", interval=0.000001)

    def acquire(_ds2, _variant):
        attempts[0] += 1
        if attempts[0] == 260:
            poller._stop.set()
        return live_data.RoughnessReading(0x3F, (5,) * 6, timestamp=float(attempts[0]))

    monkeypatch.setattr(live_data, "read_roughness", acquire)
    poller._run()
    samples = poller.drain_readings()
    assert len(samples) == 256
    assert samples[0].timestamp == 5
    assert samples[-1].timestamp == 260


class _FakeDS2:
    """Zero-fills each block read; fires `on_stop` on a chosen read to emulate the
    user pressing Stop part-way through a poll cycle."""
    def __init__(self, stop_on, on_stop):
        self.calls = 0
        self._stop_on = stop_on
        self._on_stop = on_stop

    def read_mem(self, start, size):
        if start >= 0x18000:
            return bytes(size)
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


def test_batch_mode_uses_one_poll_after_one_time_cut_runtime_probe(tmp_path):
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
            return bytes(sum(width for _address, width in self.setup_entries[-1]))

        def read_mem(self, address, length):
            return bytes(length)

    p = live_data.LiveDataPoller(interval=0, use_telegram=True, ecu_id="1437806")
    fake = FakeBatchDS2(p)
    p._ds2 = fake
    log = str(tmp_path / "batch.csv")
    p._open_csv(log)
    commit = p._write_csv_row

    def commit_and_stop(row):
        commit(row)
        p._stop.set()

    p._write_csv_row = commit_and_stop
    p._poll_loop_ds2_batch()
    p._close_csv()

    assert fake.setup_ids == ["1437806"]
    assert fake.setup_entries == [p._batch_entries]
    assert 1 <= len(fake.setup_entries[0]) <= 49
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


def _batch_values(parameters, memory):
    entries, supplemental = live_data._batch_entries_for(parameters)
    assert not supplemental
    raw = b"".join(bytes(memory.get(address + offset, 0)
                         for offset in reversed(range(width)))
                   for address, width in entries)
    snapshot = live_data._decode_batch_snapshot(raw, entries)
    return entries, {
        parameter.name: (parameter.display(parameter.parse(bytes(
            snapshot[parameter.address + i] for i in range(parameter.length)),
            parameter.address)), parameter.unit)
        for parameter in parameters
    }


def test_universal_batch_reads_state_bits_and_adc_channels_from_definition():
    wanted = {"Closed Throttle", "Full Load", "Part Load", "Decel Fuel Cut",
              "Engine Start", "Front O2 B1 Voltage", "Front O2 B2 Voltage",
              "MAF Sensor Voltage", "EVAP Purge Duty"}
    parameters = [p for p in live_data.telegram_params_for("1437806") if p.name in wanted]
    entries, latest = _batch_values(parameters, {
        0xFD24: 0x02, 0xFD14: 0x2A, 0xFA9B: 2, 0xFA9E: 0xFF, 0xFA9F: 3,
    })
    assert {address for address, _width in entries} == {
        0xDA56, 0xFD24, 0xFD14, 0xFA9A, 0xFA98, 0xFA9E}

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
    params = [p for p in live_data.telegram_params_for(ecu_id)
              if p.id in ("P13", "P17")]
    entries, values = _batch_values(params, {0xE8D0: 128, battery_address: 128})
    assert set(entries) == {(0xE8D0, 1), (battery_address, 1)}
    assert values["Throttle Position"] == ("50.2", "%")
    assert values["Battery Voltage"] == ("13.05", "V")


@pytest.mark.parametrize("ecu_id,injector,load,st1,st2,lt1,lt2", [
    ("1429861", 0xECBC, 0xFAFC, 0xED5C, 0xED96, 0xED6E, 0xEDA8),
    ("1437806", 0xEF96, 0xFC52, 0xF036, 0xF0F2, 0xF048, 0xF104),
    ("1406464", 0xEF7E, 0xFC52, 0xF01E, 0xF0CA, 0xF030, 0xF0DC),
    ("SHINDE1", 0xEF7E, 0xFC52, 0xF01E, 0xF0CA, 0xF030, 0xF0DC),
])
def test_descriptor_plan_preserves_corrected_family_words_and_scales(
        ecu_id, injector, load, st1, st2, lt1, lt2):
    expected = {
        "BS_INJECTOR_PW": (injector, 0x1234, "24.88", "ms"),
        "E2": (load, 0x1234, "98.8", "mg/st"),
        # Nonzero low bytes catch the old Telegram high-byte approximation.
        "E13": (st1, 0x8080, "0.2", "%"),
        "E14": (st2, 0x7F80, "-0.2", "%"),
        "E21": (lt1, 0x8000, "0.0", "%"),
        "E22": (lt2, 0, "-50.0", "%"),
        "E9": (0xDA36, 0xFF00, "-0.4", "%"),
        "BS_ROUGHNESS_CYL1": (0xE854, 0xFFFF, "-1", "raw"),
    }
    params = [p for p in live_data.telegram_params_for(ecu_id) if p.id in expected]
    assert {p.id for p in params} == set(expected)
    memory = {}
    for p in params:
        address, raw, _display, _unit = expected[p.id]
        assert (p.address, p.length, p.endian) == (address, 2, "little")
        memory[address] = raw & 255
        memory[address + 1] = raw >> 8
    entries, values = _batch_values(params, memory)
    assert set(entries) == {(address, 2) for address, *_rest in expected.values()}
    for p in params:
        assert values[p.name] == expected[p.id][2:]


@pytest.mark.parametrize("ecu_id,cut_address,launch_address", [
    ("1429861", 0x13013, 0x13029), ("1437806", 0x13703, 0x13719),
    ("1406464", 0x12A68, 0x13535), ("SHINDE1", 0x12A68, 0x147E9),
])
def test_descriptor_plan_splits_odd_cal_words_and_preserves_tps_alias_scaling(
        ecu_id, cut_address, launch_address):
    wanted = {"BS_CUT_FIXED_IPW", "BS_CUT_LAUNCH_FIXED_IPW", "P13", "BS_CUT_LAUNCH_TPS"}
    params = [p for p in live_data.telegram_params_for(
        ecu_id, include_cut=True, include_launch=True) if p.id in wanted]
    assert {p.id for p in params} == wanted
    memory = {cut_address: 0x34, cut_address + 1: 0x12,
              launch_address: 0xFF, launch_address + 1: 0xFF, 0xE8D0: 128}
    entries, values = _batch_values(params, memory)
    for address in (cut_address, launch_address):
        if address & 1:
            assert (address, 1) in entries and (address + 1, 1) in entries
            assert (address, 2) not in entries
        else:
            assert (address, 2) in entries
    assert entries.count((0xE8D0, 1)) == 1
    assert values["Cut Fixed IPW"] == ("24.88", "ms")
    assert values["Launch Ignition Fixed IPW"] == ("Stock", "ms")
    assert values["Throttle Position"] == ("50.2", "%")
    assert values["Launch TPS (threshold scale)"] == (
        ("59.98", "° throttle") if ecu_id == "1429861" else ("60.2", "%"))


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
    entries, values = _batch_values([rpm], {0xD000: 0xE8, 0xD001: 3})
    assert entries == ((0xD000, 2),)
    assert rpm.address == 0xD000
    assert rpm.convert(1000) == 2000
    assert values["Engine RPM"] == ("2000", "RPM")


def test_non_memory_xml_definition_selectors_are_not_read_as_ram(tmp_path):
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
        set(live_data._LIVE_RAW_CHANNELS),
    ]
    assert tuple(map(len, groups)) == (30, 26, 31, 11)
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


def test_wideband_batch_uses_resolved_input_and_target_byte():
    wanted = {"Wideband AFR", "AFR Target", "Wideband Input Voltage", "MAF Sensor Voltage"}
    params = [p for p in live_data.telegram_params_for(
        "SHINDE1", live_data.PROFILE_WIDEBAND, 0xFA98) if p.name in wanted]
    entries, latest = _batch_values(params, {
        0xE800: 117, 0xFA99: 2, 0xFA9E: 0xFF, 0xFA9F: 3, 0xE811: 128,
    })
    assert set(entries) == {(0xE800, 1), (0xE811, 1), (0xFA98, 2), (0xFA9E, 2)}

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
    assert not live_data.LiveDataPoller(ecu_id="1429373")._batch_entries
    assert not live_data.LiveDataPoller(ecu_id="1438068")._batch_entries


def test_unmapped_batch_uses_available_direct_parameters_instead():
    class FakeDS2:
        def __init__(self, poller):
            self.poller = poller
            self.setup_called = False
            self.reads = 0

        def setup_telegram_batch(self, **_kwargs):
            self.setup_called = True
            raise AssertionError("unqualified ECU must not configure a native batch")

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
            if (address, length) == (live_data._CUT_STATE_ADDR, 1):
                return b"\x00"
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
            return bytes(sum(width for _address, width in self.entries))

    p = live_data.LiveDataPoller(
        interval=0, use_telegram=True, ecu_id="1406464", ecu_variant="MS41.3")
    fake = FakeWidebandDS2(p)
    p._ds2 = fake
    p._poll_loop_ds2_batch()

    assert p._profile == live_data.PROFILE_WIDEBAND
    assert p._wideband_input_addr == 0xFA98
    assert (0xFA98, 2) in fake.entries
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
            return bytes(sum(width for _address, width in self.entries))

    p = live_data.LiveDataPoller(
        interval=0, use_telegram=True, ecu_id="1406464", ecu_variant="MS41.3")
    fake = FakeStockProfileDS2(p)
    p._ds2 = fake
    p._poll_loop_ds2_batch()

    assert p._profile == live_data.PROFILE_STANDARD
    assert (0x133C0, 2) not in fake.reads
    assert (0xDA56, 1) in fake.entries
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


@pytest.mark.parametrize(
    "ecu_id,input_addresses,rpm,speed,stage,ignition_cals,launch_cals,"
    "additive,sreg,front_heaters,rear_heaters,cal_block",
    [
        (
            "1429861", (0xFD51, 0xFD51, 0xFD50), 0xFAE6, 0xEDF4, 0xED51,
            0x13010, 0x13020, (0xED66, 0xEDA0),
            (0xED58, 0xED92), (0xEDE3, 0xEDEB), (), (0x13010, 32),
        ),
        (
            "1437806", (0xFD61, 0xFD61, 0xFD60), 0xFC3C, 0xF1BE, 0xF02B,
            0x13700, 0x13710, (0xF040, 0xF0FC),
            (0xF032, 0xF0EE), (0xF1AF, 0xF1B7), (0xF07C, 0xF138),
            (0x13700, 32),
        ),
        (
            "1406464", (0xFD61, 0xFD61, 0xFD60), 0xFC3C, 0xF19A, 0xF013,
            0x12A65, 0x1352C, (0xF028, 0xF0D4),
            (0xF01A, 0xF0C6), (0xF189, 0xF191), (0xF064, 0xF110),
            (0x1352C, 16),
        ),
        (
            "SHINDE1", (0xFD61, 0xFD61, 0xFD60), 0xFC3C, 0xF19A, 0xF013,
            0x12A65, 0x147E0, (0xF028, 0xF0D4),
            (0xF01A, 0xF0C6), (0xF189, 0xF191), (0xF064, 0xF110),
            (0x147E0, 16),
        ),
    ],
)
def test_cut_diagnostics_use_grounded_four_variant_addresses(
        ecu_id, input_addresses, rpm, speed, stage, ignition_cals, launch_cals,
        additive, sreg, front_heaters, rear_heaters, cal_block, monkeypatch):
    params = {
        param.name: param
        for param in live_data.telegram_params_for(ecu_id, include_cut=True, include_launch=True)
    }
    expected = {
        "Cut Input 80": (input_addresses[0], b"\x02", "Active"),
        "Cut Input 81": (input_addresses[1], b"\x01", "Active"),
        "Cut Input 82": (input_addresses[2], b"\x80", "Active"),
        "Ignition Cut Switch": (ignition_cals, b"\x01", "Input 80"),
        "Ignition Cut RPM": (ignition_cals + 1, b"\x7d", "4000"),
        "Cut Hysteresis": (ignition_cals + 2, b"\xff", "Legacy (0)"),
        "Cut Fixed IPW": (ignition_cals + 3, b"\x00\x00", "0.00"),
        "Launch Control Switch": (launch_cals, b"\x02", "Input 81"),
        "Launch Cut Type": (launch_cals + 1, b"\x01", "Ignition"),
        "Launch Clutch Polarity": (
            launch_cals + 2, b"\xff", "Active-low (0V)"),
        "Launch Control RPM": (launch_cals + 3, b"\x7d", "4000"),
        "Launch Arm Speed": (launch_cals + 4, b"\x05", "5"),
        "Launch Max Speed": (launch_cals + 5, b"\x28", "40"),
        "Launch Min TPS": (launch_cals + 6, b"\x80", "59.98" if ecu_id == "1429861" else "60"),
        "Launch Hard Cut RPM": (
            launch_cals + 7, b"\xff", "Soft + 96"),
        "Launch Ignition Hysteresis": (launch_cals + 8, b"\x0a", "320"),
        "Launch Ignition Fixed IPW": (launch_cals + 9, b"\x34\x12", "24.88"),
        "Ignition Cut Request": (0xD801, b"\x01", "Active"),
        "Launch Ignition Request": (0xD801, b"\x02", "Active"),
        "Launch Fuel Cut Active": (0xD801, b"\x04", "Active"),
        "Intentional Combustion Cut": (0xD801, b"\xa4", "Active"),
        "Cut Patch Runtime": (0xD801, b"\xa0", "Active"),
        "Launch Armed": (
            0xFD80 if ecu_id == "1429861" else 0xFDB6,
            b"\x40", "Active",
        ),
        "Native Fuel Limiter Active": (0xFD13, b"\x80", "Active"),
        "Effective Fuel Limiter RPM": (stage + 1, b"\x7d", "4000"),
        "Native Fuel Recovery Priority": (0xD801, b"\xa8", "Native recovery"),
        "Fuel Cut Stage Count": (stage, b"\x03", "3"),
        "Cut RPM": (rpm, b"\x7d", "4000"),
        "Launch TPS (threshold scale)": (0xE8D0, b"\x80", "59.98" if ecu_id == "1429861" else "60.2"),
        "Launch Speed": (speed, b"\x28", "40"),
        "Fuel Trim Additive": (additive[0], b"\x00\x80", "0.00"),
        "Fuel Trim Additive B2": (additive[1], b"\x00\x80", "0.00"),
        "Lambda Regulation B1": (sreg[0], b"\x08", "Active"),
        "Lambda Regulation B2": (sreg[1], b"\x08", "Active"),
        "O2 Heater Front B1": (front_heaters[0], b"\x80", "50.2"),
        "O2 Heater Front B2": (front_heaters[1], b"\x80", "50.2"),
    }
    expected.update({
        f"O2 Heater Rear B{bank}": (address, b"\x80", "50.2")
        for bank, address in enumerate(rear_heaters, 1)
    })
    if ecu_id != "1429861":
        expected["Lambda Monitor Counter"] = (0xEDE6, b"\x34\x12", "4660")
    if ecu_id in {"1406464", "SHINDE1"}:
        expected["Lambda Functions FD0E"] = (0xFD0E, b"\x34\x12", "4660")
        expected["Lambda Functions FD10"] = (0xFD10, b"\x34\x12", "4660")

    for name, (address, raw, display) in expected.items():
        param = params[name]
        assert param.address == address
        assert param.display(param.parse(raw, address)) == display

    clutch = params["Launch Clutch Polarity"]
    assert clutch.display(clutch.parse(b"\x00", launch_cals + 2)) == (
        "Active-high (5V)")

    launch_block = next(
        block for block in live_data._build_telegram_blocks(params.values())
        if block.start <= launch_cals
        and launch_cals + 11 <= block.start + block.size
    )
    assert (launch_block.start, launch_block.size) == cal_block

    monkeypatch.setattr(live_data, "detect_cut_runtime", lambda *_args: "V16")
    monkeypatch.setattr(live_data, "detect_launch_runtime", lambda *_args: "V16")

    class ActivePatchDS2:
        def read_mem(self, address, length):
            if (address, length) == (0xE847, 1):
                return b"\xA7"
            return bytes(length)

    poller = live_data.LiveDataPoller(ecu_id=ecu_id, ds2=ActivePatchDS2())
    poller._prepare_live_profile()
    assert expected.keys() <= poller.active_profile_names
    assert expected.keys() <= {name for name, _unit in live_data.display_rows()}


@pytest.mark.parametrize("use_telegram", [False, True])
@pytest.mark.parametrize("marker", [b"\x00", None])
def test_inactive_or_unavailable_cut_patch_hides_diagnostics(
        use_telegram, marker):
    class FakeDS2:
        def read_mem(self, address, length):
            assert address >= 0x18000
            if marker is None:
                raise TimeoutError("marker timeout")
            return bytes(length)

    poller = live_data.LiveDataPoller(
        use_telegram=use_telegram, ecu_id="1429861", ds2=FakeDS2())
    poller._prepare_live_profile()

    names = {param.name for param in poller._tel_params}
    assert "Engine RPM" in names
    assert "Ignition Cut Switch" not in names
    assert "Launch Control Switch" not in names
    assert "Ignition Cut Switch" not in poller.active_profile_names
    assert "Launch Control Switch" not in poller.active_profile_names
    if marker is None:
        assert any("runtime status unavailable" in error
                   for error in poller.pop_errors())


@pytest.mark.parametrize("ecu_id", ["1429861", "1437806", "1406464", "SHINDE1"])
def test_active_cut_patch_reads_dynamic_batch_and_remaining_supplements(ecu_id, monkeypatch):
    monkeypatch.setattr(live_data, "detect_cut_runtime", lambda *_args: "V16")
    monkeypatch.setattr(live_data, "detect_launch_runtime", lambda *_args: "V16")
    class FakeDS2:
        def __init__(self, poller):
            self.poller = poller
            self.setup_called = False
            self.reads = []

        def setup_telegram_batch(self, entries=None, **_kwargs):
            self.setup_called = True
            self.entries = tuple(entries)

        def poll_telegram_batch(self):
            return bytes(sum(width for _address, width in self.entries))

        def read_mem(self, address, length):
            self.reads.append((address, length))
            if (address, length) == (0xE847, 1):
                return b"\xA0"
            return bytes(length)

    poller = live_data.LiveDataPoller(
        interval=0, use_telegram=True, ecu_id=ecu_id)
    fake = FakeDS2(poller)
    poller._ds2 = fake
    commit = poller._write_csv_row

    def commit_and_stop(row):
        commit(row)
        poller._stop.set()

    poller._write_csv_row = commit_and_stop
    poller._poll_loop_ds2_batch()

    assert fake.setup_called
    assert fake.reads
    assert poller.completed_samples_since(0)[0] == 1
    assert "Ignition Cut Switch" in poller.latest_values()
    assert "Launch Control Switch" in poller.latest_values()
    assert not poller.pop_errors()


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
        "VANOS Measured Angle": ("4.5", "°"),
        "Wideband Mode": ("Enabled", ""),
    }

    assert poller.display_values() == [
        ("Engine RPM", "812", "RPM"),
        ("VANOS Measured Angle", "4.5", "°"),
        ("Wideband Mode", "Enabled", ""),
    ]

    path = tmp_path / "resolved.csv"
    poller._pending_log_path = str(path)
    poller._ensure_csv()
    poller._close_csv()
    header = path.read_text(encoding="utf-8").splitlines()[0]
    assert "VANOS Measured Angle" in header
    assert "Wideband Mode" in header


@pytest.mark.parametrize("use_telegram", [False, True])
@pytest.mark.parametrize("stage", ["open", "header", "write", "flush", "close", "write_and_close"])
def test_worker_reports_csv_failures_and_closes_stream(monkeypatch, tmp_path, use_telegram, stage):
    from io import StringIO

    stream = StringIO()

    class FailingCsv:
        writes = 0
        closed = False

        def write(self, text):
            self.writes += 1
            operation = "header" if self.writes == 1 else "write"
            if stage == operation or stage == "write_and_close" and operation == "write":
                raise OSError(f"CSV {operation} failed")
            return stream.write(text)

        def flush(self):
            if stage == "flush":
                raise OSError("CSV flush failed")
            stream.flush()

        def close(self):
            self.closed = True
            stream.close()
            if stage in ("close", "write_and_close"):
                raise OSError("CSV close failed")

    csv_file = FailingCsv()

    def open_csv(*args, **kwargs):
        if stage == "open":
            raise OSError("CSV open failed")
        return csv_file

    poller = live_data.LiveDataPoller(use_telegram=use_telegram)

    def sample_once():
        poller._ensure_csv()
        poller._csv_last_flush = 0.0
        poller._write_csv_row(poller._csv_row_base())

    monkeypatch.setattr(live_data, "open", open_csv, raising=False)
    method = "_poll_loop_ds2_batch" if use_telegram else "_poll_loop_ds2_reads"
    monkeypatch.setattr(poller, method, sample_once)
    poller.start(str(tmp_path / "live.csv"))
    worker = poller._thread
    worker.join(2)
    assert not worker.is_alive()
    expected = "write" if stage == "write_and_close" else stage
    assert f"CSV {expected} failed" in poller.terminal_error
    assert poller.pop_errors() == [poller.terminal_error]
    assert poller._stop.is_set()
    assert csv_file.closed == (stage != "open")
    assert poller._csv_file is poller._csv_writer is None
    poller.stop()  # A failed final flush must not be repeated by the caller.


def test_stop_waits_for_inflight_read_before_releasing_worker_and_csv(tmp_path):
    entered = live_data.threading.Event()
    release = live_data.threading.Event()
    stopped = live_data.threading.Event()

    class BlockedDS2:
        def read_mem(self, address, length):
            entered.set()
            assert release.wait(10)
            return bytes(length)

    poller = live_data.LiveDataPoller(ecu_id="1429373", ds2=BlockedDS2())
    poller.start(str(tmp_path / "live.csv"))
    worker = poller._thread
    stopper = None
    try:
        assert entered.wait(2)
        csv_file = poller._csv_file

        def stop():
            poller.stop()
            stopped.set()

        stopper = live_data.threading.Thread(target=stop)
        stopper.start()
        assert poller._stop.wait(2)
        assert not stopped.wait(4.2)
        assert poller._thread is worker and worker.is_alive()
        assert not csv_file.closed
    finally:
        release.set()
        worker.join(2)
        if stopper is not None:
            stopper.join(2)
    assert stopped.is_set()
    assert poller._thread is None
    assert csv_file.closed
    assert poller.terminal_error is None
