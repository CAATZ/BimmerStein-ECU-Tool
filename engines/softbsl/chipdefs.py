#!/usr/bin/env python3
"""Flash-family profiles used by the Soft-BSL host."""

FAMILY = {
    "28f200": {
        "cmdset": "intel",
        "vpp12": True,
        "agent": "agent_28f.hex",
        "label": "Intel 28F200 (Intel command set, needs 12 V VPP)",
    },
    "29f200": {
        "cmdset": "amd",
        "vpp12": False,
        "agent": "agent.hex",
        "label": "AMD 29F200 (256 KB, bottom-boot fine sectors, no 12 V)",
    },
    "29f400": {
        "cmdset": "amd",
        "vpp12": False,
        "agent": "agent.hex",
        "label": "AMD 29F400 (4 Mbit, no 12 V)",
    },
}
