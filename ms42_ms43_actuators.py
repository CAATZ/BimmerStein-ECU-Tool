"""Fixed BMW output tests; stock command22 start and command23 selective stop.

MS420DS0/MS430DS0 BEST job bodies supply selectors/arguments; native handlers
0214BC/02243C and 0A7006/0A7F06 verify parameter limits and restoration.
Command9F only ends the diagnostic session; it does not clear these overrides.
"""

_COMMON = {
    "purge_valve": ("Purge valve", b"\xBB\x80"),
    "idle_control_valve": ("Idle control valve", b"\xB7\x80"),
    "fuel_pump_relay": ("Fuel pump relay", b"\x84\xFF"),
    "ac_compressor_relay": ("A/C compressor relay", b"\x94\xFF"),
    "vanos_intake": ("VANOS intake solenoid", b"\xA6\x80"),
    "vanos_exhaust": ("VANOS exhaust solenoid", b"\xA7\x80"),
    "exhaust_flap": ("Exhaust flap", b"\x8A\xFF"),
    "secondary_air_pump_relay": ("Secondary air pump relay", b"\x81\xFF"),
    "secondary_air_valve": ("Secondary air valve", b"\xBA\xFF"),
    "malfunction_indicator_lamp": ("Check-engine lamp (MIL)", b"\x91\xFF"),
    "disa": ("DISA valve", b"\x88\xFF"),
    "cooling_fan": ("Electrical cooling fan", b"\x8E\x80"),
    "thermostat": ("Map-controlled thermostat", b"\x83\xCC"),
}

TESTS = {
    "MS42": {
        **{f"injector_{i}": (f"Injector {i}", bytes((0xB0 + i, 0x7D, 0x0A)))
           for i in range(1, 7)},
        **_COMMON,
        "running_losses_valve": ("Running losses valve", b"\x85\xFF"),
        "leak_diagnosis_valve": ("Leak diagnosis valve", b"\x86\xFF"),
        "venturi_pump": ("Venturi pump", b"\x87\xFF"),
    },
    "MS43": {
        **{f"injector_{i}": (f"Injector {i}", bytes((0xB0 + i, 0x5D, 0x0A)))
           for i in range(1, 7)},
        **_COMMON,
        "dmtl_motor": ("DMTL motor", b"\x86\xFF"),
        "dmtl_valve": ("DMTL valve", b"\x87\xFF"),
        "dmtl_heater": ("DMTL heater", b"\x8B\xFF"),
    },
}


def stop_output_override(ecu):
    """Release the pending selector; keep it for Retry Stop until session cleanup."""
    selector = getattr(ecu, "_ms42_ms43_actuator_selector", None)
    if selector is not None and ecu.execute(0x23, bytes((selector,))) != b"":
        raise ValueError("invalid selected-output stop response")
