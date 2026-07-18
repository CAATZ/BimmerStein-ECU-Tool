"""Single-owner mutex for live serial operations.

The DS2/Soft-BSL connection and hardware-BSL direct tap have independent COM
selectors, but only one ECU protocol may run at a time. Hardware BSL resets the
CPU into its silicon loader, where DS2 cannot coexist. PortOwner is a plain
in-process guard (single-threaded GUI): acquire under an engine name before a
live operation and release it when done.
"""


class PortBusyError(Exception):
    """Raised when a caller tries to acquire the port while another holder has it."""

    def __init__(self, holder):
        self.holder = holder
        super().__init__(f"serial port is held by '{holder}'")


class PortOwner:
    def __init__(self):
        self._owner = None

    @property
    def owner(self):
        return self._owner

    def is_free(self):
        return self._owner is None

    def acquire(self, name):
        """Take ownership under `name`. Idempotent for the same holder; raises
        PortBusyError if a DIFFERENT engine currently holds the port."""
        if self._owner is not None and self._owner != name:
            raise PortBusyError(self._owner)
        self._owner = name
        return True

    def release(self, name):
        """Release the port if `name` is the current holder; otherwise a no-op."""
        if self._owner == name:
            self._owner = None
