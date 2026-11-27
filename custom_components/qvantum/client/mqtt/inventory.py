"""The display's MQTT telemetry catalogue (POC).

Firmware 1.13.0 grew a **telemetry** section beside ``connection`` and
``command``: a publisher with an item list and a frequency (minimum 5 s), and a
``get_telemetry_inventory`` call that returns the catalogue. The item names below
are that catalogue's — taken from Qvantum's own naming, not invented here.

Two consequences for this transport, and both are why the map exists:

- the feed is a **list of named items**, not the full snapshot the POC's own
  stand-in publishes, so an item has to be mapped to the canonical key the rest
  of the integration reads;
- several item names are **not** the canonical ones: the telemetry says
  ``compressor_energy_kwh`` where the register is ``compressorkwh``, and
  ``additional_energy_kwh`` where it is ``additionalenergy``.

Items whose key was already the canonical name map to themselves. Anything the
map does not know is passed through unchanged, so a firmware that adds an item
does not require a client release to read it.
"""

from __future__ import annotations

#: Telemetry item -> canonical metric key. Only names whose spelling differs are
#: here; the plain register names (``bt1``, ``bt11``, ``gp1_speed``, …) are the
#: same in both vocabularies and therefore absent by design.
TELEMETRY_ITEM_KEYS: dict[str, str] = {
    "compressor_power": "compressor_power",
    "compressor_energy_kwh": "compressorenergy",
    "additional_energy_kwh": "additionalenergy",
    "calculated_heating_supply_temperature": "cal_heat_temp",
    "fan_control_voltage": "fan_control_voltage",
    "heater_relay_l1": "picpin_relay_heat_l1",
    "heater_relay_l2": "picpin_relay_heat_l2",
    "heater_relay_l3": "picpin_relay_heat_l3",
    "qm10_relay": "picpin_relay_qm10",
    "qn8_position": "qn8position",
    # Degree minutes: the register map's canonical key for input 34, which the
    # firmware's telemetry calls ``dm``.
    "dm": "degree_minute",
}

#: Per-compressor-unit items (QG only) map to the register names the same way.
for _unit in ("qg1", "qg2"):
    TELEMETRY_ITEM_KEYS.update(
        {
            f"{_unit}_bp1_temperature": f"{_unit}_bp1_pressure",
            f"{_unit}_bp2_temperature": f"{_unit}_bp2_pressure",
            f"{_unit}_bt10": f"{_unit}_bt10",
            f"{_unit}_bt13": f"{_unit}_bt13",
            f"{_unit}_bt14": f"{_unit}_bt14",
            f"{_unit}_bt15": f"{_unit}_bt15",
            f"{_unit}_bt20": f"{_unit}_bt20",
            f"{_unit}_bt23": f"{_unit}_bt23",
            f"{_unit}_gp4_pwm": f"{_unit}_gp4_pwm",
            f"{_unit}_compressor_speed_rps": f"{_unit}_compressor_rpm",
        }
    )


def canonical_key(item: str) -> str:
    """Return the canonical metric key for a telemetry item name."""
    return TELEMETRY_ITEM_KEYS.get(item, item)
