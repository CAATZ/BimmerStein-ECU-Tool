"""VIN and virginize transactions through the unchanged MS42/MS43 RAM agent."""

from hashlib import sha256
from pathlib import Path
import uuid

from ds2 import DS2Error
from ms42_ms43 import AuthorizationBusyError, BaudTransitionError, FlashInterrupted, WriteState
from ms42_ms43_agent import BLOCK_SIZE, select_writer
from ms42_ms43_vin import (
    AIF_ADDRESSES, AIF_SIZE, VIN_WRITE_SOFTWARE, PAIRING_STATUS, decode_aif, encode_vin, plan_vin, read_aif,
)
from operation_log import send_to_sink


# Verified clear-immobilizer ranges and native unpaired-status words.
PAIRING_LAYOUT = {"MS42": (0x3EDE, 66, 0xF90C), "MS43": (0x3ED4, 76, 0xE2D0)}


def _journal(ecu, directory=None, operation="vin"):
    from app_paths import mutable_path
    from ds2_fast_safety import OperationJournal

    directory = directory or mutable_path("backups", "ms42_ms43", "journals")
    return OperationJournal(Path(directory) / f"{operation}-{uuid.uuid4()}.jsonl",
                            operation=f"{ecu.family.lower()}_{operation}_write",
                            metadata={"family": ecu.family, "port": ecu.port})


def _result(ecu, address, history):
    return dict(decode_aif(history), family=ecu.family, aif_address=address, aif_raw=history)


class _IdentityWrite:
    def __init__(self, ecu, agent, address, plan=None, *, clear_vin=False):
        self.ecu, self.agent, self.address, self.plan = ecu, agent, address, plan
        self.virginize = plan is None
        self.clear_vin = clear_vin
        self.rewrite = self.virginize or plan.needs_sector_rewrite
        self.operation = "virginize" if self.virginize else "vin"
        self.label = "Virginize" if self.virginize else "VIN"
        self.serial = ecu.transport._ser
        self.active = False
        self.original = self.target = None
        self.offset = 0 if self.rewrite else address
        self.commit = None if self.virginize else address + plan.record_index * 46 - self.offset
        self.erase_verified = False
        self.state = WriteState(ecu.family, self.operation)
        self.backup_journal = None

    @property
    def can_retry(self):
        return (self.active and self.ecu.transport._ser is self.serial
                and self.ecu.is_open and not self.state.completed)

    def _read(self):
        return self.agent._read(0x80000 + self.offset, len(self.target))

    def run(self, journal, progress_cb=None, log_fn=None, *, recovering=False):
        ecu, state, agent = self.ecu, self.state, self.agent
        state.journal_path = str(journal.path) if hasattr(journal, "path") else None

        def phase(text, done=0, total=1):
            state.phase = text
            if progress_cb:
                progress_cb(done, total, text)

        try:
            if recovering:
                if not self.can_retry:
                    raise DS2Error(f"{self.label} recovery requires the original open RAM-agent connection")
                journal.append(f"{self.operation}_retry", backup_journal=self.backup_journal,
                               target_sha256=state.target_sha256)
                agent._confirm_identity()
            else:
                phase("Entering RAM agent")
                agent.write_boot = True
                try:
                    agent.enter(log_fn)
                except Exception as error:
                    if not (isinstance(error, AuthorizationBusyError) and error.phase == "RAM challenge"):
                        ecu.rate_confirmed = False
                        ecu._report_transfer_rate(0, "unconfirmed")
                    raise
                self.active = True
                phase(f"Saving {self.label} backup")
                size = 0x4000 if self.rewrite else AIF_SIZE
                self.original = agent._read(0x80000 + self.offset, size)
                lo = self.address - self.offset
                if not self.virginize and self.original[lo:lo + AIF_SIZE] != self.plan.original_history:
                    raise DS2Error("VIN history changed since preparation; read it again")
                target = bytearray(self.original)
                if self.virginize:
                    start, count, _ = PAIRING_LAYOUT[ecu.family]
                    pointer = 0x3C04 if ecu.family == "MS42" else 0x0250
                    if int.from_bytes(self.original[pointer:pointer + 3], "little") != start:
                        raise DS2Error("Immobilizer data address differs from the verified firmware layout")
                    target[start:start + count] = b"\xFF" * count
                    if self.clear_vin:
                        target[lo:lo + AIF_SIZE] = b"\xFF" * AIF_SIZE
                else:
                    target[lo:lo + AIF_SIZE] = self.plan.target_history
                self.target = bytes(target)
                state.target_sha256 = sha256(self.target).hexdigest()
                # OperationJournal flushes and fsyncs this complete recovery copy
                # before the first erase/program; bytes must not be log-redacted.
                journal.append(f"{self.operation}_backup", identity=agent.identity, address=self.offset,
                               aif_address=self.address, vin=None if self.virginize else self.plan.vin,
                               original_hex=self.original.hex(), target_hex=self.target.hex(),
                               original_sha256=sha256(self.original).hexdigest(),
                               target_sha256=state.target_sha256,
                               sector_rewrite=self.rewrite, clear_vin=self.clear_vin)
                self.backup_journal = state.journal_path
                if log_fn:
                    send_to_sink(log_fn, f"{self.label} backup saved: {self.backup_journal}")
                ecu._pending_vin = self

            current = self._read() if recovering else self.original
            if not self.rewrite:
                lo = self.commit
                if current[:lo] != self.original[:lo] or current[lo + 46:] != self.original[lo + 46:]:
                    raise DS2Error("VIN history outside the selected record changed; write stopped")
            if current != self.target:
                programmable = all((old & new) == new for old, new in zip(current, self.target))
                if self.rewrite and (not self.erase_verified or not programmable):
                    phase(("Clearing VIN history and pairing data" if self.clear_vin else
                           "Clearing pairing data") if self.virginize else
                          "Rewriting full or interrupted VIN history")
                    if current != b"\xFF" * len(self.target):
                        agent._erase(0, journal, state)
                        current = self._read()
                    if current != b"\xFF" * len(self.target):
                        raise DS2Error(f"{self.label} sector erase did not verify; keep ignition on and retry")
                    self.erase_verified = True
                elif not programmable:
                    raise DS2Error("Interrupted VIN data cannot be completed without a sector rewrite")

                # A VIN record's first word commits the native OBD slot selection.
                if self.virginize:
                    spans = ((0, len(self.target)),)
                elif self.rewrite:
                    spans = ((0, self.commit), (self.commit + 2, len(self.target)))
                else:
                    spans = ((self.commit + 2, self.commit + 46),)
                for start, end in spans:
                    for offset in range(start, end, BLOCK_SIZE):
                        stop = min(offset + BLOCK_SIZE, end)
                        chunk = self.target[offset:stop]
                        if current[offset:stop] != chunk:
                            phase("Restoring retained ECU data" if self.virginize else
                                  "Writing VIN data", offset, len(self.target))
                            agent._program(self.offset + offset, chunk, journal, state)
                prepared = self._read()
                if self.virginize:
                    if prepared != self.target:
                        raise DS2Error("Virginized data readback differs; keep ignition on and retry")
                elif (prepared[:self.commit] != self.target[:self.commit]
                        or prepared[self.commit + 2:] != self.target[self.commit + 2:]):
                    raise DS2Error("VIN data readback differs; the record was not committed")
                if not self.virginize and prepared[self.commit:self.commit + 2] != self.target[self.commit:self.commit + 2]:
                    phase("Committing VIN")
                    agent._program(self.offset + self.commit,
                                   self.target[self.commit:self.commit + 2], journal, state)
                if not self.virginize and self._read() != self.target:
                    raise DS2Error("VIN readback differs after commit; keep ignition on and retry")
            state.verified = state.finalized = True
            # The flash is complete. A failed reset/report must never replay it.
            state.completed = True
            ecu._pending_vin = None
            phase("Restoring diagnostics")
            self.active = False
            agent._reset()
            address, history = read_aif(ecu)
            lo = self.address - self.offset
            if address != self.address or history != self.target[lo:lo + AIF_SIZE]:
                raise DS2Error("VIN history after restart differs from the verified write")
            try:
                info = _result(ecu, address, history)
            except ValueError as error:
                if not self.virginize or self.clear_vin:
                    raise
                # Preserving malformed history must not prevent a pairing-only reset.
                info = dict(family=ecu.family, aif_address=address, aif_raw=history,
                            vin=None, native_vin=None, aif_records=[], programming_count=None,
                            aif_warnings=[f"AIF history preserved but could not be decoded: {error}"])
            if self.virginize:
                # 6C/6D can start EWS alignment; inspect the native status only.
                status_address = PAIRING_STATUS[ecu.family][agent.identity["software_id"]]
                if ecu.read_memory(status_address, 2) != b"\xFF\xFF":
                    raise DS2Error("ECU did not report the unpaired state after restart")
                info.update(virginized=True, already_virginized=self.original == self.target,
                            vin_cleared=self.clear_vin)
            elif info["vin"] != self.plan.vin or info["native_vin"] != self.plan.vin:
                raise DS2Error("VIN readers disagree after restart")
            journal.finish("success", verified=True, vin=info["vin"],
                           bytes_written=state.bytes_written)
            phase("ECU virginized and verified" if self.virginize else "VIN written and verified", 1, 1)
            return info
        except Exception as error:
            if not state.destructive_started and self.active:
                try:
                    agent._reset()
                    self.active = False
                except Exception:
                    ecu.rate_confirmed = False
            if not state.destructive_started:
                ecu._pending_vin = None
            if not getattr(journal, "closed", False):
                try:
                    journal.finish("failed" if state.completed else
                                   "commit_unknown" if state.destructive_started else "aborted",
                                   phase=state.phase, error=str(error), verified=state.verified)
                except Exception as journal_error:
                    error = DS2Error(f"{error}; journal completion failed: {journal_error}")
            if state.completed:
                if not ecu.rate_confirmed:
                    ecu._report_transfer_rate(0, "unconfirmed")
                completed = BaudTransitionError(
                    f"{'Virginized data was verified' if self.virginize else 'VIN was programmed and read back successfully'}, "
                    f"but completion failed: {error}. "
                    "Do not repeat the write; reconnect and read ECU identity to confirm.")
                completed.programming_completed = True
                completed.state = state
                raise completed from error
            raise FlashInterrupted(error, state) from error


def write_vin(ecu, vin, progress_cb=None, log_fn=None, *, journal=None, backup_dir=None):
    """Fresh VIN operation; an interrupted operation is retried explicitly."""
    encode_vin(vin)
    if ecu._pending_image is not None or getattr(ecu, "_pending_vin", None) is not None:
        raise DS2Error("An incomplete write owns this connection; retry it first")
    with ecu.transfer():
        address, history = read_aif(ecu)
        if address != AIF_ADDRESSES[ecu.family]:
            raise ValueError("VIN write address differs from the verified firmware layout")
        plan = plan_vin(history, vin)
        if plan.mode == "unchanged":
            return _result(ecu, address, history)
        agent = select_writer(ecu, log_fn)
        if agent is None:
            raise DS2Error("VIN editing requires the supported RAM-agent connection")
        if agent.identity["software_id"] not in VIN_WRITE_SOFTWARE[ecu.family]:
            raise DS2Error("VIN editing is not mapped for this software version")
        transaction = _IdentityWrite(ecu, agent, address, plan)
        ecu.last_write = transaction.state
        return transaction.run(journal or _journal(ecu, backup_dir), progress_cb, log_fn)


def virginize(ecu, progress_cb=None, log_fn=None, *, clear_vin=False, journal=None, backup_dir=None):
    """Clear pairing records, optionally VIN/AIF, without EWS alignment."""
    if type(clear_vin) is not bool:
        raise ValueError("clear_vin must be True or False")
    if ecu._pending_image is not None or getattr(ecu, "_pending_vin", None) is not None:
        raise DS2Error("An incomplete write owns this connection; retry it first")
    with ecu.transfer():
        agent = select_writer(ecu, log_fn)
        if agent is None:
            raise DS2Error("Virginizing requires the supported RAM-agent connection")
        if agent.identity["software_id"] not in PAIRING_STATUS[ecu.family]:
            raise DS2Error("Virginizing is not mapped for this software version")
        directory = ecu._frame(0x0D, minimum=67)
        address = int.from_bytes(directory[63:66], "big")
        if address != AIF_ADDRESSES[ecu.family]:
            raise DS2Error("VIN history address differs from the verified firmware layout")
        transaction = _IdentityWrite(ecu, agent, address, clear_vin=clear_vin)
        ecu.last_write = transaction.state
        return transaction.run(journal or _journal(ecu, backup_dir, "virginize"), progress_cb, log_fn)


def retry_vin(ecu, progress_cb=None, log_fn=None, *, journal=None):
    """Resume the retained VIN or virginize transaction without changing its target."""
    transaction = getattr(ecu, "_pending_vin", None)
    if transaction is None or not transaction.can_retry:
        raise DS2Error("No identity write is retained on the original open RAM-agent connection")
    with ecu.transfer():
        return transaction.run(journal or _journal(ecu, operation=transaction.operation),
                               progress_cb, log_fn, recovering=True)
