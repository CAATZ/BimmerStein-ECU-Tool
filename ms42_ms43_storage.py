"""Explicit storage backups/restores, separate from ordinary ROM writes.

Retained flash is the exact 16 KiB journal at 4000..7FFF. MS43 EEPROM is a
separate 2 KiB device. Neither operation copies boot identity, normalizes records,
or claims a complete ECU clone. The current firmware interprets restored bytes.
"""

from hashlib import sha256
import json
from pathlib import Path
import uuid

from ds2 import DS2Error
from ms42_ms43 import AuthorizationBusyError, BaudTransitionError, FlashInterrupted, WriteState
from ms42_ms43_agent import BLOCK_SIZE, recoverable_reply, select_reader, select_writer
from ms42_ms43_resident import SUPPORTED_SOFTWARE
from operation_log import send_to_sink


SIZES = {"retained": 0x4000, "eeprom": 0x800}
SOFTWARE = {"MS42": "0110C6", "MS43": "430069"}
FORMAT = "BimmerStein MS42/MS43 storage snapshot"


def make_snapshot(family, kind, data, *, software_id, hardware_id=None):
    """Bind explicit family/layout metadata to complete raw storage bytes."""
    if family not in SOFTWARE or kind not in SIZES or (family == "MS42" and kind == "eeprom"):
        raise ValueError("Unsupported ECU storage layout")
    if not isinstance(data, (bytes, bytearray, memoryview)) or len(data) != SIZES[kind]:
        raise ValueError(f"{kind} requires exactly {SIZES[kind]:,} bytes")
    if (not isinstance(software_id, str) or len(software_id) != 6
            or not all(c.isascii() and c.isalnum() for c in software_id)
            or hardware_id is not None and (not isinstance(hardware_id, str)
                or not 1 <= len(hardware_id) <= 64 or not hardware_id.isascii() or not hardware_id.isprintable())):
        raise ValueError("Invalid storage software/hardware identity")
    raw = bytes(data)
    return {"family": family, "kind": kind, "software_id": software_id,
            "hardware_id": hardware_id, "data": raw, "sha256": sha256(raw).hexdigest()}


def _validated(snapshot):
    if not isinstance(snapshot, dict):
        raise ValueError("Expected a storage snapshot")
    try:
        value = make_snapshot(snapshot["family"], snapshot["kind"], snapshot["data"],
                              software_id=snapshot["software_id"], hardware_id=snapshot.get("hardware_id"))
        if snapshot["sha256"] != value["sha256"]:
            raise ValueError("Storage checksum does not match its contents")
        return value
    except (KeyError, TypeError) as error:
        raise ValueError("Incomplete storage snapshot") from error


def _json_digest(data):
    return sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def encode_snapshot(snapshot):
    data = _validated(snapshot)
    data.update(format=FORMAT, version=1, data_hex=data.pop("data").hex())
    data["snapshot_sha256"] = _json_digest(data)
    return json.dumps(data, indent=2) + "\n"


def decode_snapshot(text):
    if not isinstance(text, str) or len(text) > 256 * 1024:
        raise ValueError("Invalid storage snapshot size")
    data = json.loads(text)
    if not isinstance(data, dict) or data.get("format") != FORMAT or data.get("version") != 1:
        raise ValueError("Unsupported storage snapshot")
    if data.pop("snapshot_sha256", None) != _json_digest(data):
        raise ValueError("Storage snapshot checksum does not match its contents")
    try:
        data["data"] = bytes.fromhex(data.pop("data_hex"))
        return _validated(data)
    except (KeyError, TypeError) as error:
        raise ValueError("Incomplete storage snapshot") from error


def from_identity_snapshot(info):
    """Convert an already-decoded older identity snapshot's retained flash only."""
    from ms42_ms43_diagnostics import _validate_retained

    try:
        _validate_retained(info["retained_address"], info["retained_raw"], info["retained_provenance"])
        return make_snapshot(info["family"], "retained", info["retained_raw"],
                             software_id=info["software_id"], hardware_id=info["hardware_id"])
    except (KeyError, TypeError) as error:
        raise ValueError("Identity snapshot has no complete retained-flash backup") from error


def _idle(ecu):
    if any(getattr(ecu, name, None) is not None for name in ("_pending_image", "_pending_vin", "_pending_storage")):
        raise DS2Error("An incomplete write owns this connection; retry it first")


def _select(ecu, kind, identity, log_fn, *, writing=False):
    if kind == "eeprom":
        from ms42_ms43_eeprom import select_eeprom
        agent = select_eeprom(ecu, log_fn, identity=identity)
    else:
        agent = (select_writer if writing else select_reader)(ecu, log_fn, identity=identity)
    if agent is None and (writing or kind == "eeprom"):
        raise DS2Error("This storage operation requires the supported RAM-agent firmware")
    return agent


def _read(agent, kind, progress_cb=None):
    return (agent.read_all(progress_cb=progress_cb) if kind == "eeprom"
            else agent._read(0x84000, SIZES[kind], progress_cb))


def _read_before_mutation(agent, kind, progress_cb=None, log_fn=None):
    # Reuse the normal agent's confirmed-reset/identity-check/rate ladder.
    # Callers never use this after an erase or EEPROM/flash program request.
    while True:
        try:
            return _read(agent, kind, progress_cb)
        except DS2Error as error:
            if not recoverable_reply(error) or not agent._lower_baud(log_fn):
                raise
            try:
                agent.enter(log_fn)
            except Exception as error:
                if not (isinstance(error, AuthorizationBusyError) and error.phase == "RAM challenge"):
                    _uncertain(agent.ecu)
                raise
            agent.active = True


def _uncertain(ecu):
    ecu.rate_confirmed = False
    ecu._report_transfer_rate(0, "unconfirmed")


def read_storage(ecu, kind, *, force_slow=False, log_fn=None, progress_cb=None):
    _idle(ecu)
    if (ecu.family not in SOFTWARE or kind not in SIZES
            or ecu.family == "MS42" and kind == "eeprom"):
        raise ValueError("Unsupported ECU storage layout")
    if force_slow and kind == "eeprom":
        raise ValueError("MS43 EEPROM reading requires its RAM agent; stock DS2 is unavailable")
    with ecu.transfer(force_slow=force_slow):
        identity = ecu._identity_fields()
        agent = None if force_slow else _select(ecu, kind, identity, log_fn)
        if agent is not None:
            agent.active = False
            try:
                try:
                    agent.enter(log_fn)
                except Exception as error:
                    if kind == "eeprom" or not agent._fallback_to_ds2(error, log_fn):
                        raise
                    agent = None
                    ecu.set_baud(125000 if ecu.transport.native_fast_capable else 9600)
            except Exception as error:
                if not (isinstance(error, AuthorizationBusyError) and error.phase == "RAM challenge"):
                    _uncertain(ecu)
                raise
        if agent is None:
            if log_fn:
                send_to_sink(log_fn, f"Reading {ecu.family} flash storage over stock DS2.")
            raw = ecu.read_memory(0x4000, SIZES[kind], progress_cb=progress_cb)
            return make_snapshot(ecu.family, kind, raw, software_id=identity["software_id"],
                                 hardware_id=identity["hardware_id"])
        agent.active = True
        try:
            raw = _read_before_mutation(agent, kind, progress_cb, log_fn)
            result = make_snapshot(ecu.family, kind, raw, software_id=identity["software_id"],
                                   hardware_id=identity["hardware_id"])
            agent.active = False
            agent._reset()
            return result
        except Exception as error:
            if agent.active and ecu.rate_confirmed:
                agent.active = False
                try:
                    agent._reset()
                except Exception:
                    _uncertain(ecu)
            elif not (isinstance(error, AuthorizationBusyError) and error.phase == "RAM challenge"):
                _uncertain(ecu)
            raise


def _journal(ecu, kind, directory=None):
    from app_paths import mutable_path
    from ds2_fast_safety import OperationJournal

    directory = directory or mutable_path("backups", "ms42_ms43", "journals")
    return OperationJournal(Path(directory) / f"{kind}-{uuid.uuid4()}.jsonl",
                            operation=f"{ecu.family.lower()}_{kind}_restore",
                            metadata={"family": ecu.family, "port": ecu.port})


class _StorageRestore:
    def __init__(self, ecu, agent, target):
        self.ecu, self.agent, self.target = ecu, agent, target
        self.kind = target["kind"]
        self.serial = ecu.transport._ser
        self.state = WriteState(ecu.family, f"{self.kind}_restore", target_sha256=target["sha256"])
        self.active = False
        self.before = None
        self.backup_journal = None

    @property
    def can_retry(self):
        return (self.active and not self.state.completed and self.ecu.transport._ser is self.serial
                and self.ecu.is_open)

    def _write(self, current, journal, progress_cb):
        agent, state, wanted = self.agent, self.state, self.target["data"]
        if self.kind == "eeprom":
            for offset in range(0, len(wanted), 2):
                if current[offset:offset + 2] != wanted[offset:offset + 2]:
                    state.address = offset
                    journal.append("storage_program_requested", address=offset, length=2)
                    self.ecu._check_operation()
                    state.destructive_started = True
                    agent.write_word(offset, wanted[offset:offset + 2])
                    state.bytes_written += 2
                    journal.append("storage_program_accepted", address=offset, length=2)
                if progress_cb:
                    progress_cb(offset + 2, len(wanted), "Restoring EEPROM")
            return
        # Complete and verify one 8 KiB bank before touching the other. No
        # journal counters, CRCs, record lengths, or native fault flags are fixed.
        for bank in (0, 0x2000):
            old, new = current[bank:bank + 0x2000], wanted[bank:bank + 0x2000]
            if old == new:
                continue
            if any((a & b) != b for a, b in zip(old, new)):
                state.phase = "Erasing retained-flash bank"
                agent._erase(1 + bank // 0x2000, journal, state)
                old = agent._read(0x84000 + bank, 0x2000)
                if old != b"\xFF" * 0x2000:
                    raise DS2Error("Retained-flash erase did not verify; keep ignition on and retry")
            state.phase = "Restoring retained flash"
            for offset in range(0, 0x2000, BLOCK_SIZE):
                chunk = new[offset:offset + BLOCK_SIZE]
                if old[offset:offset + len(chunk)] != chunk:
                    agent._program(0x4000 + bank + offset, chunk, journal, state)
                if progress_cb:
                    progress_cb(bank + offset + len(chunk), len(wanted), state.phase)
            if agent._read(0x84000 + bank, 0x2000) != new:
                raise DS2Error("Retained-flash bank differs after programming; keep ignition on and retry")

    def run(self, journal, progress_cb=None, log_fn=None, *, recovering=False, backup_cb=None):
        ecu, state, agent = self.ecu, self.state, self.agent
        state.journal_path = str(journal.path) if hasattr(journal, "path") else None
        restored = False

        def phase(label):
            state.phase = label
            if progress_cb:
                progress_cb(0, len(self.target["data"]), label)

        try:
            if recovering:
                if not self.can_retry:
                    raise DS2Error("Storage recovery requires the original open RAM-agent connection")
                journal.append("storage_retry", backup_journal=self.backup_journal,
                               target_sha256=state.target_sha256)
                agent._confirm_identity()
            else:
                phase("Entering RAM agent")
                if self.kind == "retained":
                    agent.write_boot = True  # Authorizes low flash; this service writes only4000..7FFF.
                try:
                    agent.enter(log_fn)
                except Exception as error:
                    if not (isinstance(error, AuthorizationBusyError) and error.phase == "RAM challenge"):
                        _uncertain(ecu)
                    raise
                self.active = True
                agent.active = True
                phase("Saving storage backup")
                current = _read_before_mutation(agent, self.kind, log_fn=log_fn)
                self.before = make_snapshot(ecu.family, self.kind, current,
                                            software_id=agent.identity["software_id"],
                                            hardware_id=agent.identity["hardware_id"])
                journal.append("storage_backup", kind=self.kind, identity=agent.identity,
                               original_hex=current.hex(), target_hex=self.target["data"].hex(),
                               original_sha256=self.before["sha256"], target_sha256=state.target_sha256,
                               address=0x4000 if self.kind == "retained" else 0)
                self.backup_journal = state.journal_path
                ecu._pending_storage = self
                if backup_cb:
                    backup_cb(dict(self.before), self.backup_journal)
                if log_fn:
                    send_to_sink(log_fn, f"Storage backup saved: {self.backup_journal}")
            current = _read(agent, self.kind) if recovering else self.before["data"]
            phase("Restoring EEPROM" if self.kind == "eeprom" else "Restoring retained flash")
            self._write(current, journal, progress_cb)
            phase("Verifying restored storage")
            if _read(agent, self.kind) != self.target["data"]:
                raise DS2Error("Storage readback differs; keep ignition on and retry")
            state.verified = state.finalized = state.completed = True
            ecu._pending_storage = None
            self.active = False
            phase("Restoring diagnostics")
            agent.active = False
            agent._reset()
            restored = True
            journal.finish("success", verified=True, bytes_written=state.bytes_written)
            if progress_cb:
                progress_cb(len(current), len(current), "Storage restored and verified")
            return {"snapshot": dict(self.target), "backup": dict(self.before),
                    "backup_journal": self.backup_journal, "bytes_written": state.bytes_written, "verified": True}
        except Exception as error:
            if not state.destructive_started:
                ecu._pending_storage = None
                if self.active:
                    self.active = False
                    if agent.active and ecu.rate_confirmed:
                        agent.active = False
                        try:
                            agent._reset()
                        except Exception:
                            _uncertain(ecu)
            if not getattr(journal, "closed", False):
                try:
                    journal.finish("failed" if state.completed else
                                   "commit_unknown" if state.destructive_started else "aborted",
                                   phase=state.phase, error=str(error), verified=state.verified)
                except Exception as journal_error:
                    error = DS2Error(f"{error}; journal completion failed: {journal_error}")
            if state.completed:
                if not restored:
                    _uncertain(ecu)
                completed = BaudTransitionError(
                    f"Storage was restored and verified, but completion failed: {error}. "
                    "Do not repeat the restore; reconnect and read storage to confirm.")
                completed.programming_completed = True
                completed.state = state
                raise completed from error
            interrupted = FlashInterrupted(error, state)
            if self.kind == "eeprom" and state.destructive_started:
                interrupted.args = (f"EEPROM restore incomplete: {error}. Keep ignition on and retry the retained operation.",)
            raise interrupted from error


def restore_storage(ecu, snapshot, *, log_fn=None, progress_cb=None, journal=None, backup_dir=None, backup_cb=None):
    _idle(ecu)
    target = _validated(snapshot)
    if target["family"] != ecu.family or target["software_id"] not in SUPPORTED_SOFTWARE[ecu.family]:
        raise ValueError("Storage layout does not match the supported ECU family/software")
    with ecu.transfer():
        identity = ecu._identity_fields()
        if identity["software_id"] != target["software_id"]:
            raise ValueError("Storage software version differs from the connected ECU")
        agent = _select(ecu, target["kind"], identity, log_fn, writing=True)
        transaction = _StorageRestore(ecu, agent, target)
        ecu.last_write = transaction.state
        return transaction.run(journal or _journal(ecu, target["kind"], backup_dir),
                               progress_cb, log_fn, backup_cb=backup_cb)


def retry_storage(ecu, *, log_fn=None, progress_cb=None, journal=None):
    transaction = getattr(ecu, "_pending_storage", None)
    if transaction is None or not transaction.can_retry:
        raise DS2Error("No storage restore is retained on the original open RAM-agent connection")
    with ecu.transfer():
        return transaction.run(journal or _journal(ecu, transaction.kind), progress_cb, log_fn, recovering=True)
