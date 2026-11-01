"""Variable Telegram plans use the selected definition's native memory bytes."""
import csv
from dataclasses import replace
import struct

import pytest

import live_data
from tests.test_live_roughness_channels import ECU_IDS, MemoryDS2
from tests.test_live_selection import capture_once


def parameter(name, address, length=2, **kwargs):
    template = next(p for p in live_data.telegram_params_for("1429861") if p.id == "P8")
    return replace(template, id=name, name=name, address=address, length=length,
                   conversion=lambda value: value, expression="x", **kwargs)


def capture_parameters(tmp_path, parameters, *, auto=False, cycles=1, fake=None):
    fake = fake or MemoryDS2()
    poller = live_data.LiveDataPoller(
        interval=0, ecu_id="1429861", ds2=fake, use_telegram=True,
        telegram_fallback=auto)
    poller._profile_ready = True
    poller._configure_parameters(parameters)
    path = tmp_path / "dynamic.csv"
    poller._pending_log_path = str(path)
    commit = poller._write_csv_row

    def commit_and_stop(row):
        commit(row)
        if poller.csv_rows == cycles:
            poller._stop.set()

    poller._write_csv_row = commit_and_stop
    poller._poll_loop()
    assert poller.terminal_error is None, poller.pop_errors()
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    return poller, fake, rows


@pytest.mark.parametrize("ecu_id", ECU_IDS)
def test_reported_four_channel_selection_is_one_poll_with_hidden_dependencies(tmp_path, ecu_id):
    poller, fake, row = capture_once(
        tmp_path, ("P8", "P12", "E9", "BS_ROUGHNESS_CYL1"), "auto", ecu_id=ecu_id)
    assert poller.acquisition_mode == "telegram"
    assert len(fake.entries) == 7 and sum(size for _, size in fake.entries) == 12
    assert fake.polls == 1 and fake.reads == []
    assert set(row) == {"Time", "Datetime", "Engine RPM", "Mass Air Flow",
                        "Idle Valve Pos", "Cylinder 1 Roughness"}
    assert row["Engine RPM"] == "900" and row["Cylinder 1 Roughness"] == "-32768"


def test_mixed_storage_types_restore_native_bytes_before_definition_conversion(tmp_path):
    fake = MemoryDS2()
    fake.memory[0x1000:0x100A] = bytes.fromhex("7f00fefb123412345678")
    fake.memory[0x100A:0x100E] = struct.pack("<f", -12.5)
    fake.memory[0x100F:0x1013] = struct.pack(">f", 2.25)
    parameters = [
        parameter("byte", 0x1000, 1),
        parameter("signed", 0x1002, signed=True),
        parameter("odd word", 0x1003),
        parameter("big endian", 0x1004, endian="big"),
        parameter("dword", 0x1006, 4),
        parameter("float", 0x100A, 4, storage_type="float", format="0.00"),
        parameter("odd big float", 0x100F, 4, storage_type="float", endian="big", format="0.00"),
    ]
    poller, fake, rows = capture_parameters(tmp_path, parameters, fake=fake)
    assert not poller.pop_errors() and not fake.reads
    assert {p.name: rows[0][p.name] for p in parameters} == {
        "byte": "127", "signed": "-1026", "odd word": "4859",
        "big endian": "4660", "dword": str(0x78563412),
        "float": "-12.50", "odd big float": "2.25",
    }
    covered = [address + offset for address, length in fake.entries for offset in range(length)]
    assert len(covered) == len(set(covered))


@pytest.mark.parametrize("auto", [False, True])
def test_dense_overflow_uses_bounded_supplement_or_smaller_standard_plan(tmp_path, auto):
    class CountingDS2(MemoryDS2):
        setups = 0

        def setup_telegram_batch(self, **kwargs):
            self.setups += 1
            super().setup_telegram_batch(**kwargs)

    fake = CountingDS2()
    parameters = [parameter(f"word {index}", 0x1000 + index * 2) for index in range(55)]
    for index, param in enumerate(parameters):
        fake.memory[param.address:param.address + 2] = (index + 1000).to_bytes(2, "little")
    poller, fake, rows = capture_parameters(tmp_path, parameters, auto=auto, cycles=2, fake=fake)
    assert len(rows) == 2 and all(row["word 54"] == "1054" for row in rows)
    assert poller.completed_samples_since(0)[0] == 2
    if auto:
        assert poller.acquisition_mode == "standard"
        assert fake.setups == fake.polls == 0 and fake.reads == [(0x1000, 110)] * 2
    else:
        assert poller.acquisition_mode == "telegram"
        assert fake.setups == 1 and fake.polls == 2 and len(fake.entries) == 49
        assert fake.reads == [(0x1062, 12)] * 2


def test_out_of_range_bytes_and_large_overflow_never_create_invalid_descriptors():
    parameters = [parameter(str(index), 0x1000 + index * 2) for index in range(200)]
    parameters.extend([parameter("boundary", 0x1FFFF), parameter("high", 0x20020)])
    entries, blocks = live_data._batch_entries_for(parameters)
    assert len(entries) == 49
    assert all(0 <= address <= 0x1FFFF and (length == 1 or address % 2 == 0)
               for address, length in entries)
    assert all(block.size <= 120 for block in blocks)
    covered = {address + offset for address, length in entries for offset in range(length)}
    covered.update(block.start + offset for block in blocks for offset in range(block.size))
    assert all(param.address + offset in covered
               for param in parameters for offset in range(param.length))


@pytest.mark.parametrize("malformed", [b"\x00", b"\x00\x01\x02"])
def test_bad_reply_cannot_publish_or_commit_and_next_reply_recovers(tmp_path, malformed):
    fake = MemoryDS2()
    poller = live_data.LiveDataPoller(
        interval=0, ecu_id="1429861", ds2=fake, selected_ids=("P8",),
        use_telegram=True, telegram_fallback=False)
    poller._profile_ready = True
    path = tmp_path / "recovered.csv"
    poller._pending_log_path = str(path)
    read = fake.poll_telegram_batch

    def reply():
        valid = read()
        if fake.polls == 1:
            return malformed
        assert poller.completed_samples_since(0)[0] == 0 and poller.csv_rows == 0
        assert poller.latest_values() == {}
        poller._stop.set()
        return valid

    fake.poll_telegram_batch = reply
    poller._poll_loop()
    assert poller.terminal_error is None
    assert len(poller.pop_errors()) == 1 and fake.polls == 2
    assert poller.completed_samples_since(0)[0] == poller.csv_rows == 1
    assert poller.latest_values() == {"Engine RPM": ("900", "RPM")}
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1 and rows[0]["Time"] == "0.000"
