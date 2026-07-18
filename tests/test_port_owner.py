import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from port_owner import PortOwner, PortBusyError
import pytest


def test_acquire_and_release():
    po = PortOwner()
    assert po.is_free() and po.owner is None
    po.acquire("flasher")
    assert po.owner == "flasher" and not po.is_free()
    po.release("flasher")
    assert po.is_free()


def test_second_owner_blocked_and_state_unchanged():
    po = PortOwner()
    po.acquire("flasher")
    with pytest.raises(PortBusyError) as ei:
        po.acquire("softbsl")
    assert ei.value.holder == "flasher"
    assert po.owner == "flasher"          # a blocked acquire does not steal ownership


def test_reacquire_same_owner_is_idempotent():
    po = PortOwner()
    po.acquire("flasher")
    po.acquire("flasher")                 # no error
    assert po.owner == "flasher"


def test_release_by_non_owner_is_noop():
    po = PortOwner()
    po.acquire("flasher")
    po.release("softbsl")                 # not the holder
    assert po.owner == "flasher"
