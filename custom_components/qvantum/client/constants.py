"""Protocol constants shared by the HTTP and Modbus clients."""

SETTING_UPDATE_APPLIED = "APPLIED"

FAN_SPEED_STATE_OFF = "off"
FAN_SPEED_STATE_NORMAL = "normal"
FAN_SPEED_STATE_EXTRA = "extra"
FAN_SPEED_VALUE_OFF = 0
FAN_SPEED_VALUE_NORMAL = 1
FAN_SPEED_VALUE_EXTRA = 2

# QAD EN 2609-AXC holding 53 (DHW Mode).
DHW_MODE_ECO = 0
DHW_MODE_NORMAL = 1
DHW_MODE_EXTRA = 2
DHW_MODE_SMART = 3

# Tap water capacity mappings (start, stop) -> capacity.
TAP_WATER_CAPACITY_MAPPINGS = {
    (52, 58): 1,
    (52, 62): 2,
    (55, 69): 3,
    (55, 70): 4,
    (55, 71): 5,
    (55, 74): 6,
    (55, 76): 7,
}

# Relay power constants (watts) used to compute total power from relay stages.
# The base system power is an estimate of the constant power draw of the heat
# pump when no relays are active (circulation pumps, controls, auxiliaries).
BASE_SYSTEM_POWER_W = 160.0
RELAY_HEAT_L1_POWER_W = 2000.0
RELAY_HEAT_L2_POWER_W = 2000.0
RELAY_HEAT_L3_POWER_W = 1000.0

RELAY_STAGE_POWER_MAP = {
    "picpin_relay_heat_l1": RELAY_HEAT_L1_POWER_W,
    "picpin_relay_heat_l2": RELAY_HEAT_L2_POWER_W,
    "picpin_relay_heat_l3": RELAY_HEAT_L3_POWER_W,
}

# Canonical names (HA / Modbus holdings 24-30) -> cloud ``update_settings`` keys.
HEATING_CURVE_HTTP_KEYS = {
    "curve_30": "ud_curve_30",
    "curve_20": "ud_curve_20",
    "curve_10": "ud_curve_10",
    "curve_0": "ud_curve_0",
    "curve_minus_10": "ud_curve_minus10",
    "curve_minus_20": "ud_curve_minus20",
    "curve_minus_30": "ud_curve_minus30",
}


def alias_heating_curve_settings(settings: dict) -> dict:
    """Copy cloud ``ud_curve_*`` keys onto canonical ``curve_*`` names."""
    for canonical, http_key in HEATING_CURVE_HTTP_KEYS.items():
        if canonical not in settings and http_key in settings:
            settings[canonical] = settings[http_key]
    return settings
