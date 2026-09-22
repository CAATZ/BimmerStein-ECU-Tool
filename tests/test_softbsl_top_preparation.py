"""TOP preparation reads only preserved bytes and freezes the physical flash target."""
from types import SimpleNamespace

import pytest

import ecu_info
import identity
import softbsl_service as service
from checksum import correct_checksums
from ms41 import MS41ECU


AMD_SIGNATURE = bytes.fromhex("e00e0d58f04ec084")
INTEL_SIGNATURE = bytes.fromhex("e6f45000b84c6fe0")


def image(marker="T", *, boot_byte=0x51, signature=AMD_SIGNATURE):
    data = bytearray(b"\xff" * 0x40000)
    data[0x4000:0x6000] = bytes([boot_byte]) * 0x2000
    data[0x6025:0x602C] = b"1406464"
    data[0x1400E:0x14016] = b"12000000"
    for address in (0x6007, 0x6013, 0x601F, 0x1400C):
        data[address:address + 4] = b"0912"
    data[MS41ECU.CODING_FAMILY_FILE_ADDR:MS41ECU.CODING_FAMILY_FILE_ADDR + 3] = b"909"
    lo = ecu_info.DRV_SIG_FILE_OFFSET
    data[lo:lo + len(signature)] = signature
    data[0x5FFC:0x6000] = (bytes([0xA5, 0x5A, ord(marker), ord(marker) ^ 0xFF])
                                if marker else b"\xff" * 4)
    data[0x5CD5:0x5CEF] = b"P" * 26
    data[identity.AIF_OFF:identity.AIF_END] = b"\xff" * (identity.AIF_END - identity.AIF_OFF)
    return bytes(correct_checksums(MS41ECU.graft_coding_family(data, b"909"))[0])


class Agent:
    def __init__(self, live, *, bank="T", fail_before_erase=False, fail_after_erase=False):
        self.live = live
        self.bank = bank
        self.reads = []
        self.identifications = 0
        self.marker_reads = []
        self.images = []
        self.finalizations = []
        self.fail_before_erase = fail_before_erase
        self.fail_after_erase = fail_after_erase
        self.staged_entry = True

    def identify(self):
        self.identifications += 1
        return self.bank

    def crc_read(self, address, length):
        if address == ecu_info.BANK_MARKER_ADDR:
            self.marker_reads.append((address, length))
            return (bytes([0xA5, 0x5A, ord(self.bank), ord(self.bank) ^ 0xFF])
                    if self.bank in ("B", "T") else b"\xff" * length)
        return b"\x00" * length  # unchanged three-read link probe

    def read_range(self, address, length, *, descramble):
        assert descramble is False
        self.reads.append((address, length))
        offset = address ^ 0x4000
        return self.live[offset:offset + length]

    def flash_image(self, target, **kwargs):
        self.images.append((bytes(target), kwargs))
        assert kwargs["write_bootloader"] is True
        assert kwargs["assume_half"] == "T"
        if self.fail_before_erase:
            raise service.SoftBSLError("injected pre-erase failure")
        kwargs["progress_cb"](0, len(target), "erase")
        if self.fail_after_erase:
            raise service.SoftBSLError("injected program failure")

    def finalize_marker0(self, already_sent=False):
        self.finalizations.append(already_sent)
        return True


def attach(monkeypatch, agents):
    opened = []
    handles = []
    def open_session(_port, _log, _family, **kwargs):
        opened.append(kwargs["baud_tier"])
        handle = SimpleNamespace(is_open=True)
        handle.close = lambda: setattr(handle, "is_open", False)
        handles.append(handle)
        return handle, agents[len(opened) - 1]
    monkeypatch.setattr(service, "_open_session", open_session)
    return opened, handles


def run(target, *, boot=False, options=None, baud="high"):
    service.run_flash(
        "offline", target, "full", lambda _message: pytest.fail("unexpected bank prompt"),
        lambda *_args: None, baud=baud, write_bootloader=boot, chip_family="amd",
        top_full_options={} if options is None else options)


@pytest.mark.parametrize("donor_bank", ["T", "B", None])
def test_boot_off_grafts_only_live_boot_and_arms_sa7(monkeypatch, donor_bank):
    live = image()
    donor = image(donor_bank, boot_byte=0x32, signature=INTEL_SIGNATURE)
    agent = Agent(live)
    opened, handles = attach(monkeypatch, [agent])
    prepared = []
    run(donor, options={"prepared_image_cb": prepared.append})
    effective, _kwargs = agent.images[0]
    assert agent.reads == [(0, 0x2000)]
    assert effective[0x4000:0x6000] == live[0x4000:0x6000]
    assert effective[:0x4000] == donor[:0x4000]
    assert effective[0x6000:] == donor[0x6000:]
    assert service.marker(effective) == "T"
    assert ecu_info.image_chip_family(effective) == "amd"
    assert prepared == [effective]
    assert opened == ["high"] and not handles[0].is_open


@pytest.mark.parametrize("preserve", [True, False])
def test_boot_on_reads_only_selected_identity(monkeypatch, preserve):
    live = bytearray(image())
    live[identity.PRODUCTION_OFF:identity.PRODUCTION_END] = b"P" * 26
    live[identity.AIF_OFF:identity.AIF_END] = b"A" * 644
    donor = image(boot_byte=0x32)
    agent = Agent(bytes(live))
    attach(monkeypatch, [agent])
    run(donor, boot=True, options={"preserve_boot_identity": preserve})
    expected_reads = [(lo ^ 0x4000, hi - lo) for lo, hi in identity.IDENTITY_GRAFT_RANGES]
    assert agent.reads == (expected_reads if preserve else [])
    effective = agent.images[0][0]
    expected = bytes(identity.graft_identity(donor, live)) if preserve else donor
    assert effective == expected


@pytest.mark.parametrize("donor_bank", ["B", None])
def test_boot_on_wrong_file_bank_rejected_before_entry(monkeypatch, donor_bank):
    opened, _handles = attach(monkeypatch, [])
    with pytest.raises(service.CrossBankSafetyError, match="TOP-marked"):
        run(image(donor_bank), boot=True)
    assert opened == []


def test_wrong_live_bank_cleans_up_without_graft_write_or_fallback(monkeypatch):
    agent = Agent(image(), bank="B")
    opened, handles = attach(monkeypatch, [agent])
    with pytest.raises(service.CrossBankSafetyError, match="expected TOP"):
        run(image())
    assert agent.reads == [] and agent.images == []
    assert agent.finalizations == [False]
    assert opened == ["high"] and not handles[0].is_open


def test_preerase_fallback_reuses_frozen_target_and_one_callback(monkeypatch):
    first = Agent(image(), fail_before_erase=True)
    second = Agent(image(boot_byte=0x77))
    opened, handles = attach(monkeypatch, [first, second])
    prepared = []
    run(image("B", boot_byte=0x32), options={"prepared_image_cb": prepared.append})
    assert opened == ["high", "mid"]
    assert first.reads == [(0, 0x2000)] and second.reads == []
    assert first.images[0][0] == second.images[0][0] == prepared[0]
    assert len(prepared) == 1 and all(not handle.is_open for handle in handles)


@pytest.mark.parametrize("recovery_marker", ["T", "\xff", "?"])
def test_posterase_recovery_freezes_merge_and_rechecks_same_bank(monkeypatch, recovery_marker):
    agent = Agent(image(), fail_after_erase=True)
    opened, handles = attach(monkeypatch, [agent])
    prepared = []
    with pytest.raises(service.SoftBSLWriteRecoveryRequired) as caught:
        run(image("B", boot_byte=0x32), options={"prepared_image_cb": prepared.append})
    recovery = caught.value.recovery
    assert recovery.target == prepared[0] == agent.images[0][0]
    assert recovery.write_bootloader is True and recovery.expected_bank == "T"
    assert opened == ["high"] and handles[0].is_open
    agent.live = image(boot_byte=0x77)
    agent.bank = recovery_marker
    agent.fail_after_erase = False
    service.resume_write_recovery(recovery, log=lambda *_args: None)
    assert agent.reads == [(0, 0x2000)]
    assert agent.images[1][0] == recovery.target and agent.identifications == 0
    assert agent.marker_reads == [(ecu_info.BANK_MARKER_ADDR, ecu_info.BANK_MARKER_LEN)] * 2
    assert not handles[0].is_open


def test_retained_top_session_uses_same_preparation_and_frozen_recovery():
    agent = Agent(image(), fail_after_erase=True)
    handle = SimpleNamespace(is_open=True, close=lambda: None)
    session = service.SoftBSLBootSession("offline", handle, agent, "high", "amd", AMD_SIGNATURE)
    prepared = []
    with pytest.raises(service.SoftBSLWriteRecoveryRequired) as caught:
        service.run_flash_boot_recovery(
            session, image("B", boot_byte=0x32), "full", lambda _message: "",
            lambda *_args: None, write_bootloader=False,
            top_full_options={"prepared_image_cb": prepared.append})
    assert agent.reads == [(0, 0x2000)]
    assert caught.value.recovery.target == prepared[0]
    assert caught.value.recovery.write_bootloader is True
    assert caught.value.recovery.expected_bank == "T"


def test_short_preservation_read_cleans_up_before_fallback(monkeypatch):
    first = Agent(image())
    original = first.read_range
    first.read_range = lambda *args, **kwargs: original(*args, **kwargs)[:-1]
    second = Agent(image())
    opened, handles = attach(monkeypatch, [first, second])
    run(image("B"))
    assert opened == ["high", "mid"]
    assert first.images == [] and first.finalizations == [False]
    assert not handles[0].is_open
    assert second.reads == [(0, 0x2000)] and len(second.images) == 1


def test_family_graft_recomputes_effective_checksums_even_when_option_off(monkeypatch):
    live = bytearray(image())
    lo = MS41ECU.CODING_FAMILY_FILE_ADDR
    live[lo:lo + 3] = b"606"
    live = bytes(correct_checksums(MS41ECU.graft_coding_family(live, b"606"))[0])
    donor = image("B", boot_byte=0x32)
    agent = Agent(live)
    attach(monkeypatch, [agent])
    run(donor, options={"correct_checksums": False})
    effective = agent.images[0][0]
    assert effective[0x4000:0x6000] == live[0x4000:0x6000]
    assert MS41ECU.read_program_compatibility_id(effective) == "0612"
    assert MS41ECU.read_calibration_compatibility_id(effective) == "0612"
    assert bytes(correct_checksums(effective)[0]) == effective


def test_retry_refuses_changed_bank_without_switch_or_new_erase(monkeypatch):
    agent = Agent(image(), fail_after_erase=True)
    _opened, handles = attach(monkeypatch, [agent])
    with pytest.raises(service.SoftBSLWriteRecoveryRequired) as initial:
        run(image("B"))
    agent.bank = "B"
    with pytest.raises(service.SoftBSLWriteRecoveryRequired) as retried:
        service.resume_write_recovery(initial.value.recovery, log=lambda *_args: None)
    assert "No further erase" in str(retried.value.recovery.error)
    assert len(agent.images) == 1 and handles[0].is_open


def test_prepared_callback_failure_never_flashes_or_falls_back(monkeypatch):
    agent = Agent(image())
    opened, handles = attach(monkeypatch, [agent])
    calls = []
    def reject(effective):
        calls.append(effective)
        raise service.SoftBSLError("platform review declined")
    with pytest.raises(service.FlashImageCompatibilityError, match="platform review declined"):
        run(image("B"), options={"prepared_image_cb": reject})
    assert len(calls) == 1 and agent.images == [] and opened == ["high"]
    assert agent.finalizations == [False] and not handles[0].is_open


def test_open_boot_recovery_exposes_actual_agent_bank(monkeypatch):
    agent = Agent(image())
    agent.boot_chip_family = "amd"
    agent.boot_driver_signature = AMD_SIGNATURE
    _opened, handles = attach(monkeypatch, [agent])
    session = service.open_boot_recovery("offline", lambda *_args: None)
    assert session.bank_marker == "T"
    assert session.agent is agent and agent.identifications == 0
    assert session.ds2 is handles[0] and handles[0].is_open


@pytest.mark.parametrize("unknown_bank", [None, "?", "\xff"])
def test_unmarked_retained_bank_stays_open_and_usable(monkeypatch, unknown_bank):
    agent = Agent(image(), bank=unknown_bank)
    agent.boot_chip_family = "intel"
    agent.boot_driver_signature = INTEL_SIGNATURE
    _opened, handles = attach(monkeypatch, [agent])
    session = service.open_boot_recovery("offline", lambda *_args: None)
    assert session.bank_marker is None
    assert session.is_open and handles[0].is_open and agent.finalizations == []
    assert service.read_boot_recovery_range(session, 0, 4) == agent.live[0x4000:0x4004]


def test_open_boot_bank_transport_failure_closes_provisional_handle(monkeypatch):
    agent = Agent(image())
    agent.boot_chip_family = "amd"
    agent.boot_driver_signature = AMD_SIGNATURE
    def failed_marker_read(_address, _length):
        raise service.SoftBSLError("injected transport failure")
    agent.crc_read = failed_marker_read
    _opened, handles = attach(monkeypatch, [agent])
    with pytest.raises(service.SoftBSLError, match="injected transport failure"):
        service.open_boot_recovery("offline", lambda *_args: None)
    assert agent.finalizations == [False] and not handles[0].is_open


def test_retry_marker_transport_failure_keeps_recovery_open(monkeypatch):
    agent = Agent(image(), fail_after_erase=True)
    _opened, handles = attach(monkeypatch, [agent])
    with pytest.raises(service.SoftBSLWriteRecoveryRequired) as initial:
        run(image("B"))
    original = agent.crc_read
    def failed_marker_read(address, length):
        if address == ecu_info.BANK_MARKER_ADDR:
            raise service.SoftBSLError("marker read transport failed")
        return original(address, length)
    agent.crc_read = failed_marker_read
    with pytest.raises(service.SoftBSLWriteRecoveryRequired) as retried:
        service.resume_write_recovery(initial.value.recovery, log=lambda *_args: None)
    assert "marker read transport failed" in str(retried.value.recovery.error)
    assert handles[0].is_open and len(agent.images) == 1


def test_retry_does_not_treat_lone_b_byte_as_proven_opposite_bank(monkeypatch):
    agent = Agent(image(), fail_after_erase=True)
    _opened, handles = attach(monkeypatch, [agent])
    with pytest.raises(service.SoftBSLWriteRecoveryRequired) as initial:
        run(image("B"))
    original = agent.crc_read
    agent.crc_read = lambda address, length: (
        b"\xff\xffB\xbd" if address == ecu_info.BANK_MARKER_ADDR
        else original(address, length))
    agent.fail_after_erase = False
    service.resume_write_recovery(initial.value.recovery, log=lambda *_args: None)
    assert len(agent.images) == 2
    assert agent.images[1][0] == initial.value.recovery.target
    assert not handles[0].is_open


def test_partial_t_marker_never_qualifies_initial_top_write(monkeypatch):
    agent = Agent(image())
    original = agent.crc_read
    agent.crc_read = lambda address, length: (
        b"\xff\xffT\xab" if address == ecu_info.BANK_MARKER_ADDR
        else original(address, length))
    opened, handles = attach(monkeypatch, [agent])
    with pytest.raises(service.CrossBankSafetyError, match="expected TOP"):
        run(image("B"))
    assert opened == ["high"] and agent.reads == [] and agent.images == []
    assert not handles[0].is_open


def test_partial_t_marker_stays_unknown_in_retained_metadata(monkeypatch):
    agent = Agent(image())
    agent.boot_chip_family = "amd"
    agent.boot_driver_signature = AMD_SIGNATURE
    agent.crc_read = lambda _address, _length: b"\xff\xffT\xab"
    _opened, handles = attach(monkeypatch, [agent])
    session = service.open_boot_recovery("offline", lambda *_args: None)
    assert session.bank_marker is None and handles[0].is_open
