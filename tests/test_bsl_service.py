import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bsl_service
import ecu_info
from engines.bsl import bsl_unbrick as engine


def test_hardware_bsl_prefers_d2xx_and_configures_raw_8n1(monkeypatch):
    calls = []

    class FakeD2XX:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            self.is_open = True
            self.dtr = None

        def setDTR(self, value):
            self.dtr = bool(value)

        def close(self):
            self.is_open = False

    monkeypatch.setattr(engine, "_import_d2xx_serial", lambda: FakeD2XX)
    bsl = engine.BSL("COM1", baud=9600, reset_line="dtr")
    try:
        assert bsl.transport_name == "d2xx"
        assert calls == [{
            "port": "COM1", "baudrate": 9600, "bytesize": 8,
            "parity": 0, "stopbits": 1, "timeout": 2.0,
            "write_timeout": 3.0, "two_stop": False,
        }]
        assert bsl.ser.dtr is False
    finally:
        bsl.close()


def test_hardware_bsl_falls_back_to_pyserial(monkeypatch):
    class Boom:
        def __init__(self, **_kwargs):
            raise OSError("D2XX unavailable")

    class FakeSerial:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            self.is_open = True

        def close(self):
            self.is_open = False

    pyserial = type("SerialModule", (), {
        "Serial": FakeSerial, "EIGHTBITS": 8,
        "PARITY_NONE": "N", "STOPBITS_ONE": 1,
    })
    monkeypatch.setattr(engine, "_import_d2xx_serial", lambda: Boom)
    monkeypatch.setattr(engine, "serial", pyserial)
    bsl = engine.BSL("COM1", baud=9600)
    try:
        assert bsl.transport_name == "pyserial"
        assert bsl.ser.args == ("COM1", 9600)
        assert bsl.ser.kwargs["parity"] == "N"
        assert bsl.ser.kwargs["stopbits"] == 1
    finally:
        bsl.close()


def test_hardware_bsl_d2xx_monitor_handoff_waits_for_wire_drain(monkeypatch):
    sleeps = []

    class FakeSerial:
        def reset_input_buffer(self):
            pass

        def write(self, payload):
            return len(payload)

        def flush(self):
            pass

    bsl = object.__new__(engine.BSL)
    bsl.ser = FakeSerial()
    bsl.baud = 9600
    bsl.transport_name = "d2xx"
    bsl.sync = lambda _log: True
    bsl.load_stub = lambda _payload: None
    bsl.mon_ping = lambda _log: True
    monkeypatch.setattr(engine.time, "sleep", sleeps.append)

    assert bsl.start_monitor(log=lambda _line: None) is True
    assert sleeps[0] == 0.05
    assert sleeps[1] == engine.MONITOR_LOAD_LEN * 10.0 / 9600 + 0.05


def test_hardware_bsl_pyserial_monitor_handoff_keeps_existing_delay(monkeypatch):
    sleeps = []

    class FakeSerial:
        def reset_input_buffer(self):
            pass

        def write(self, payload):
            return len(payload)

        def flush(self):
            pass

    bsl = object.__new__(engine.BSL)
    bsl.ser = FakeSerial()
    bsl.baud = 9600
    bsl.transport_name = "pyserial"
    bsl.sync = lambda _log: True
    bsl.load_stub = lambda _payload: None
    bsl.mon_ping = lambda _log: True
    monkeypatch.setattr(engine.time, "sleep", sleeps.append)

    assert bsl.start_monitor(log=lambda _line: None) is True
    assert sleeps == [0.05, 0.05]


def test_hardware_bsl_d2xx_programming_uses_timeout_safe_chunks():
    writes = []

    class FakeSerial:
        def reset_input_buffer(self):
            pass

        def write(self, payload):
            writes.append(bytes(payload))
            return len(payload)

        def flush(self):
            pass

    bsl = object.__new__(engine.BSL)
    bsl.ser = FakeSerial()
    bsl.transport_name = "d2xx"
    bsl.read = lambda _length, timeout=2.0: b"K"

    payload = b"\x5A\xA5" * 5000
    assert bsl.mon_program(0x10000, payload, log=lambda _line: None) is True
    assert len(writes) == 3
    assert max(len(frame) - 7 for frame in writes) <= 4096
    assert sum(len(frame) - 7 for frame in writes) == len(payload)


def test_hardware_bsl_programming_rejects_short_transport_write():
    class FakeSerial:
        def reset_input_buffer(self):
            pass

        def write(self, payload):
            return len(payload) - 1

        def flush(self):
            raise AssertionError("short write must fail before flush")

    bsl = object.__new__(engine.BSL)
    bsl.ser = FakeSerial()
    bsl.transport_name = "d2xx"
    messages = []

    assert bsl.mon_program(0x10000, b"\x00\x00", log=messages.append) is False
    assert any("SHORT TRANSPORT WRITE" in message for message in messages)


def test_flash_dry_run_never_opens_port(tmp_path, monkeypatch):
    ref = tmp_path / "tune_ref.bin"
    ref.write_bytes(b"\xFF" * 24576)
    monkeypatch.setattr(engine, "_bsl", lambda _args: (_ for _ in ()).throw(AssertionError("port opened")))
    plan = bsl_service.create_flash_plan(
        "COM_NONEXISTENT_TEST", "tune", str(ref), "28f200", "upper")
    logs = []
    rc = bsl_service.flash_dry_run(plan, logs.append)
    assert rc == 0
    assert "DRY RUN" in "\n".join(logs)


def test_plan_freezes_reference_and_options(tmp_path, monkeypatch):
    ref = tmp_path / "ref.bin"
    original = b"\xFF" * 24576
    ref.write_bytes(original)
    plan = bsl_service.create_flash_plan(
        "COM3", "tune", str(ref), "28f200", "upper", True, True,
        baud=38400, reset_line="dtr")
    ref.write_bytes(b"\x00" * 24576)

    captured = {}
    monkeypatch.setattr(bsl_service, "_run_handler",
                        lambda handler, args, log, **_options: captured.update(args=args) or 0)
    bsl_service.flash_arm(plan, lambda _line: None)

    assert captured["args"].ref_bytes == original
    assert captured["args"].chip == "28f200"
    assert captured["args"].baud == 38400
    assert captured["args"].reset_line == "dtr"
    assert captured["args"].fix_checksums is True
    assert captured["args"].force is True
    assert captured["args"].arm is True


def test_auto_chip_is_rejected_for_flash_plan(tmp_path):
    ref = tmp_path / "ref.bin"
    ref.write_bytes(b"\xFF" * 24576)
    try:
        bsl_service.create_flash_plan("COM3", "tune", str(ref), "auto", "upper")
    except ValueError as error:
        assert "explicitly" in str(error)
    else:
        raise AssertionError("auto flash plan was accepted")


def test_unknown_chip_geometry_is_rejected_for_flash_plan(tmp_path):
    ref = tmp_path / "ref.bin"
    ref.write_bytes(b"\xFF" * 24576)
    try:
        bsl_service.create_flash_plan("COM3", "tune", str(ref), "mystery", "upper")
    except ValueError as error:
        assert "Unsupported flash-chip geometry" in str(error)
    else:
        raise AssertionError("unknown flash-chip geometry was accepted")


def test_flash_plan_rejects_unsupported_bsl_baud(tmp_path):
    ref = tmp_path / "ref.bin"
    ref.write_bytes(b"\xFF" * 24576)
    try:
        bsl_service.create_flash_plan(
            "COM3", "tune", str(ref), "28f200", "upper", baud=57600)
    except ValueError as error:
        assert "9600, 19200, or 38400" in str(error)
    else:
        raise AssertionError("unsupported BSL baud was accepted")


def test_flash_plan_rejects_amd_image_for_intel_geometry(tmp_path):
    image = bytearray(b"\xFF" * engine.MS41ECU.FULL_ROM_SIZE)
    image[ecu_info.DRV_SIG_FILE_OFFSET:
          ecu_info.DRV_SIG_FILE_OFFSET + ecu_info.DRV_SIG_LEN] = bytes.fromhex(
              "e00e0d58f04ec084")
    ref = tmp_path / "amd_patched.bin"
    ref.write_bytes(image)

    try:
        bsl_service.create_flash_plan(
            "COM3", "all", str(ref), "28f200", "upper", force=True)
        raise AssertionError("AMD image was accepted for Intel recovery geometry")
    except ValueError as error:
        assert "AMD/JEDEC 29F driver" in str(error)
        assert "Intel 28F200" in str(error)


def test_flash_plan_rejects_intel_image_for_amd_geometry(tmp_path):
    image = bytearray(b"\xFF" * engine.MS41ECU.FULL_ROM_SIZE)
    image[ecu_info.DRV_SIG_FILE_OFFSET:
          ecu_info.DRV_SIG_FILE_OFFSET + ecu_info.DRV_SIG_LEN] = bytes.fromhex(
              "e6f45000b84c6fe0")
    ref = tmp_path / "intel.bin"
    ref.write_bytes(image)

    for chip in ("29f200", "29f400"):
        try:
            bsl_service.create_flash_plan(
                "COM3", "all", str(ref), chip, "upper", force=True)
            raise AssertionError(f"Intel image was accepted for {chip} recovery geometry")
        except ValueError as error:
            assert "Intel 28F200 driver" in str(error)
            assert "AMD/JEDEC 29F" in str(error)


def test_flash_plan_rejects_internally_incompatible_full_reference(tmp_path):
    image = bytearray(b"\xFF" * engine.MS41ECU.FULL_ROM_SIZE)
    for address in (0x6007, 0x6013, 0x601F):
        image[address:address + 4] = b"0641"
    for address in (0x1400C, 0x14016, 0x14026, 0x14036):
        image[address:address + 4] = b"0659"
    ref = tmp_path / "hybrid.bin"
    ref.write_bytes(image)

    try:
        bsl_service.create_flash_plan("COM3", "all", str(ref), "28f200", "upper")
        raise AssertionError("incompatible reference was accepted")
    except ValueError as error:
        assert "internally incompatible" in str(error)


def test_flash_plan_allows_amd_image_for_amd_geometry(tmp_path):
    image = bytearray(b"\xFF" * engine.MS41ECU.FULL_ROM_SIZE)
    image[0x6025:0x602C] = b"1406464"
    image[0x1400E:0x14016] = b"12000000"
    for address in (0x6007, 0x6013, 0x601F, 0x1400C):
        image[address:address + 4] = b"0912"
    image[ecu_info.DRV_SIG_FILE_OFFSET:
          ecu_info.DRV_SIG_FILE_OFFSET + ecu_info.DRV_SIG_LEN] = bytes.fromhex(
              "e00e0d58f04ec084")
    ref = tmp_path / "amd_patched.bin"
    ref.write_bytes(image)

    plan = bsl_service.create_flash_plan(
        "COM3", "all", str(ref), "29f200", "upper")
    assert plan.chip == "29f200"


def test_diagnostic_commands_call_handlers_in_process(monkeypatch):
    calls = []

    def fake_run(handler, args, log):
        calls.append((handler, args))
        return 0

    monkeypatch.setattr(bsl_service, "_run_handler", fake_run)
    log = lambda _line: None
    assert bsl_service.sync("COM3", "auto", "upper", log) == 0
    assert bsl_service.chip_id("COM3", "auto", "upper", log) == 0
    assert bsl_service.businfo("COM3", "auto", "upper", log) == 0
    assert bsl_service.verify_alias("COM3", "auto", "upper", log) == 0
    progress = []
    assert bsl_service.dump_full(
        "COM3", "out.bin", "28f200", "upper", log,
        progress=lambda *args: progress.append(args), baud=19200) == 0
    assert bsl_service.dump_tune(
        "COM3", "tune.bin", "28f200", "upper", log, baud=38400) == 0
    assert [handler for handler, _args in calls] == [
        engine.cmd_sync, engine.cmd_id, engine.cmd_businfo,
        engine.cmd_verify_alias, engine.cmd_dump, engine.cmd_dump]
    assert calls[-2][1].file == "out.bin"
    assert calls[-2][1].partial is False
    assert calls[-2][1].baud == 19200
    assert calls[-2][1].reset_line == "dtr"
    assert calls[-1][1].file == "tune.bin"
    assert calls[-1][1].partial is True
    assert calls[-1][1].baud == 38400
    calls[-2][1].progress_cb(1, 2, "BSL dump")
    assert progress == [(1, 2, "BSL dump")]


def test_vpp_rejected_for_amd_without_opening_port(monkeypatch):
    monkeypatch.setattr(bsl_service, "_run_handler",
                        lambda *_args: (_ for _ in ()).throw(AssertionError("handler called")))
    assert bsl_service.vpp_on("COM3", "29f400", "upper", lambda _line: None) == 2
