"""MS430069 external 2 KiB Microwire EEPROM transport; separate from flash.

The dedicated payload uses the existing resident handoff and serial framing.
It has no flash erase/program commands and never runs during ordinary flashing.
File policy, durable backups and retained restore state belong to storage.py.
"""

from ds2 import DS2Error
from ms42_ms43_agent import (
    AgentReader, BLOCK_SIZE, IDENTITY, PACKET_ATTEMPTS, PROGRAM, READ, RESET,
    recoverable_reply,
)
from ms42_ms43_resident import _select_service


SIZE = 2048


def select_eeprom(ecu, log_fn=None, *, identity=None):
    if ecu.family != "MS43":
        return None
    # Its generic rejection log promises stock DS2 fallback, which does not
    # exist for this EEPROM. The storage workflow reports unavailable support.
    return _select_service(ecu, None, EepromAgent, "EEPROM", identity=identity)


class EepromAgent(AgentReader):
    read_source = "ECU EEPROM read"
    operation = "EEPROM transfer"

    def _agent_payload(self):
        from engines.ms42_ms43.eeprom_payload import payload
        return payload("MS43")

    def _confirm_identity(self):
        if self._read_packet(0, 4, command=IDENTITY) != b"BE43":
            raise DS2Error("Unexpected MS43 EEPROM agent identity")

    def _validate_transfer(self, command, offset, count, data):
        if type(offset) is not int or type(count) is not int:
            raise ValueError("EEPROM address and count must be integers")
        if command in (READ, PROGRAM):
            if (offset < 0 or offset & 1 or count & 1 or
                    not 2 <= count <= BLOCK_SIZE or offset + count > SIZE):
                raise ValueError("EEPROM range must be aligned and inside 2 KiB")
            if command == PROGRAM and (count != 2 or len(data) != 2):
                raise ValueError("EEPROM writes require exactly one aligned word")
        elif command == IDENTITY:
            if offset or count != 4:
                raise ValueError("Invalid EEPROM agent identity request")
        elif command == RESET:
            if offset or count:
                raise ValueError("Invalid EEPROM agent reset request")
        else:
            raise ValueError("Command is not supported by the EEPROM agent")
        if command != PROGRAM and data:
            raise ValueError("EEPROM read/control requests have no payload")

    def read_all(self, progress_cb=None):
        result = bytearray()
        while len(result) < SIZE:
            count = min(BLOCK_SIZE, SIZE - len(result))
            result.extend(self._read_packet(len(result), count))
            if progress_cb:
                progress_cb(len(result), SIZE, "MS43 EEPROM read")
        return bytes(result)

    def write_word(self, byte_offset, two_bytes):
        data = bytes(two_bytes)
        self._validate_transfer(PROGRAM, byte_offset, len(data), data)
        for attempt in range(PACKET_ATTEMPTS):
            try:
                # Native success includes ready polling, word readback and EWDS.
                self._transfer(PROGRAM, byte_offset, 2, data)
                return
            except DS2Error as error:
                if not recoverable_reply(error):
                    raise
                self._drain_until_quiet()
                if self._read_packet(byte_offset, 2) == data:
                    return
                if attempt + 1 == PACKET_ATTEMPTS:
                    raise
