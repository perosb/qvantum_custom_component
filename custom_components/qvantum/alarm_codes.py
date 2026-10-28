"""Qvantum alarm codes and their friendly names.

The names are Qvantum's own, from the public alarm list at
https://www.qvantum.com/alarms/ (fetched 2026-10-28). They are English on
purpose: the page publishes one language, and inventing translations would
put words in Qvantum's mouth. Entity names are translated elsewhere; these
values are data.

Two codes are not on that page and must not be dropped when it is refreshed:
252 and 391 are the texts the Qvantum app shows (issue #459). 279 is on the
page but the app words it as the QGM1 flow switch, which is why 391 exists.

Do not add a code without a source. An unknown code is better shown as the
bare number than as a guessed name: :func:`alarm_description` returns
``None`` for it, and the sensor then carries no description.
"""

from __future__ import annotations

from typing import Any

#: Error code -> Qvantum's friendly name.
ALARM_CODES: dict[int, str] = {
    1: "Outdoor Sensor Error (BT1)",
    2: "Indoor Sensor Error (BT2)",
    3: "Pool Sensor Error (BT4)",
    4: "Flow Line Sensor Error (BT11)",
    5: "External Flow Line Sensor Error (BT12)",
    6: "Tank Temperature Sensor Error (BT30)",
    7: "Heating Medium PHEX Inlet Sensor Error (BT31)",
    9: "Hot Water PHEX Outlet Sensor Error (BT34)",
    10: "Auxiliary Input Error (BTX)",
    11: "Condenser Outlet Sensor Error (BT10)",
    13: "Source Flow Sensor Error (BT14)",
    14: "Source Return Flow Sensor Error (BT15)",
    15: "Sink Flow Sensor Error (BT20)",
    19: "Suction Line Sensor Error (BT23)",
    21: "Low Pressure Transmitter Error (BP1)",
    22: "High Pressure Transmitter Error (BP2)",
    27: "Com EA2W Module Error",
    29: "Inverter Error 5× in 3 hours",
    30: "Filter Change Alarm",
    31: "Hot Gas Alarm",
    33: "High Pressure Alarm",
    35: "Low Pressure Alarm",
    37: "Source Temperature Alarm",
    39: "EEV Actuator Malfunction",
    42: "BT23 Low Temperature Alarm",
    43: "No Communication With Outdoor Module",
    44: "Low Defrost Flow",
    45: "Fan Error (No RPM Feedback)",
    47: "Incompatible Software in the Outdoor Module",
    53: "Cooling blocked – Low ambient temperature",
    60: "Anti-freeze protection",
    61: "Low water flow protection",
    64: "Exhaust temperature overprotection",
    69: "High flow temperature difference protection",
    70: "Outlet water over heat",
    84: "High pressure protection 3 times",
    86: "Low pressure protection 3 times",
    90: "Low water flow protection 3+",
    92: "Anti-freeze protection 3 times",
    96: "Discharge over heat protection 3 times",
    98: "High flow temperature difference protection 3 times",
    99: "Low outlet water temp 3 times",
    100: "Outlet water overtemperature protection 3 times",
    112: "Inlet water temperature failure",
    113: "Outlet water temperature failure",
    114: "Coil temperature failure",
    115: "Ambient temperature failure",
    116: "Suction temperature failure",
    123: "Discharge temperature failure",
    126: "Low ambient temperature failure",
    127: "Low outlet water temperature",
    139: "Fan 1 failure",
    140: "Fan 2 failure",
    141: "Communication failure (mainboard with fan motor module board)",
    143: "Communication failure (mainboard with fan motor 2 module board)",
    144: "IPM overheat",
    145: "Compressor start failure",
    146: "Compressor over current",
    147: "Input voltage phase loss",
    148: "IPM current sampling fault",
    149: "Overheat protection of drive board devices",
    150: "Pre‑charge failure",
    151: "DC bus overvoltage",
    152: "DC bus undervoltage",
    153: "AC input voltage undervoltage",
    154: "AC input overcurrent shutdown",
    155: "Input voltage sampling fault",
    156: "DSP and PFC communication failure",
    157: "Drive plate temperature fault",
    158: "DSP and communication board communication failure",
    159: "Mainboard communication failure",
    161: "Compressor default phase",
    163: "Faulty input current sampling",
    166: "EEPROM fault",
    167: "Input voltage overlimiting protection",
    175: "Compressor overspeed protection",
    247: "Return water temperature sensor fault",
    248: "Outlet water temperature sensor fault",
    252: "Source water low temperature protection",
    253: "Fault of water inlet sensor at water source side",
    254: "Fault of water outlet temperature sensor at water source side",
    256: "Exhaust temperature sensor fault",
    257: "Suction air temperature sensor fault",
    269: "Main Control-Press Driver Communication Failure",
    274: "Driver DC bus over-voltage and under-voltage protection",
    275: "Low pressure sensor failure",
    276: "High pressure sensor fault",
    279: "Fault of water flow switch on the ground source side.",
    306: "Driver module protection (FO)",
    307: "Compressor out of step/or start failure/phase loss",
    308: "Driver input overcurrent protection/phase current overcurrent protection/current detection circuit fault",
    309: "Driver module hardware protection/IPM module temperature sensor fault",
    310: "Low pressure switch off protection (Low low pressure protection)",
    311: "High pressure switch off protection",
    312: "Excessive exhaust temperature protection",
    313: "Driver IPM overtemperature protection",
    315: "Over-pressure protection of high pressure",
    325: "Low-pressure protection for high-pressure",
    326: "High-pressure protection for low-pressure",
    327: "Fault of water flow switch on the ground source side",
    333: "Com W2W Module 1 Error",
    334: "Com W2W Module 2 Error",
    391: "QGM2 fault of water flow switch on the ground source side",
}


def alarm_description(code: Any) -> str | None:
    """Return the friendly name for an alarm code, or ``None`` if unknown."""
    if code is None or isinstance(code, bool):
        return None
    try:
        number = int(code)
    except (TypeError, ValueError):
        return None
    return ALARM_CODES.get(number)
