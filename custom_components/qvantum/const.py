"""Constants for the Qvantum Heat Pump Integration."""

DOMAIN = "qvantum"
DEFAULT_SCAN_INTERVAL = 120
MIN_SCAN_INTERVAL = 60
SETTING_UPDATE_APPLIED = "APPLIED"
FAN_SPEED_STATE_OFF = "off"
FAN_SPEED_STATE_NORMAL = "normal"
FAN_SPEED_STATE_EXTRA = "extra"
FAN_SPEED_VALUE_OFF = 0
FAN_SPEED_VALUE_NORMAL = 1
FAN_SPEED_VALUE_EXTRA = 2
VERSION = "2026.4.1"
CONFIG_VERSION = 5

# Modbus TCP configuration
CONF_MODBUS_TCP = "modbus_tcp"
CONF_MODBUS_HOST = "modbus_host"
CONF_MODBUS_PORT = "modbus_port"
CONF_MODBUS_UNIT_ID = "modbus_unit_id"
DEFAULT_MODBUS_HOST = "Qvantum-HP"
DEFAULT_MODBUS_PORT = 502
DEFAULT_MODBUS_UNIT_ID = 1

DEFAULT_ENABLED_METRICS = [
    "bf1_l_min",
    "bp1_pressure",
    "bp1_temp",
    "bp2_pressure",
    "bp2_temp",
    "bt1",
    "bt11",
    "bt14",
    "bt15",
    "bt2",
    "bt30",
    "bt33",
    "bt34",
    "bt4",
    "bt10",
    "bt12",
    "bt13",
    "bt20",
    "bt21",
    "bt22",
    "bt23",
    "bt31",
    "cal_heat_temp",
    "compressormeasuredspeed",
    "compressor_state",
    "fanrpm",
    "tap_water_stop",
    "tap_water_start",
    "fan0_10v",
    "gp1_speed",
    "gp2_speed",
    "hp_status",
    "picpin_relay_heat_l1",
    "picpin_relay_heat_l2",
    "picpin_relay_heat_l3",
    "picpin_relay_gp10",
    "picpin_relay_qm10",
    "picpin_relay_qn8_1",
    "picpin_relay_qn8_2",
    "picpin_relay_gp3",
    "picpin_relay_pump",
    "picpin_relay_ha12",
    "qn8position",
    "compressorenergy",
    "additionalenergy",
    "heatingenergy",
    "dhwenergy",
    "powertotal",
]

DEFAULT_DISABLED_HTTP_METRICS = [
    "calc_suppy_cpr",  # Note: Typo is in the original data
    "btx",
    "cooling_enabled",
    "dhw_outl_temp_15",
    "dhw_outl_temp_5",
    "dhw_outl_temp_max",
    "dhw_prioritytime",
    "guide_des_temp",
    "guide_he",
    "op_man_cooling",
    "op_mode_sensor",
    "price_region",
    "room_temp_ext",
    "dhwdemand",
    "heatingdemand",
    "coolingdemand",
    "heatingreleased",
    "coolingreleased",
    "compressorreleased",
    "additionreleased",
    "dhw_prioritytimeleft",
    "heating_prioritytimeleft",
    "cooling_priotitytimeleft",
    "switch_state",
    "dhwstop_temp",
    "dhwstart_temp",
    "filtered60sec_outdoortemp",
    "max_freq_env",
    "dhw_set",
    "bp1_temp_20min_filter",
    "max_bp2_env",
    "inputcurrent1",
    "inputcurrent2",
    "inputcurrent3",
    "picpin_mask",
]

DEFAULT_DISABLED_MODBUS_METRICS = [
    "bf1_rpm",
]

# Number metrics configuration
DEFAULT_ENABLED_NUMBER_METRICS = [
    "tap_water_capacity_target",
    "room_comp_factor",
    "indoor_temperature_offset",
    "tap_water_stop",
    "tap_water_start",
    "fan_normal",
    "fan_speed_2",
]

DEFAULT_DISABLED_HTTP_NUMBER_METRICS = []
DEFAULT_DISABLED_MODBUS_NUMBER_METRICS = []

# Metrics that must always be fetched regardless of entity enablement (HTTP and Modbus)
REQUIRED_METRICS = [
    "bt2",  # Required by climate component for current temperature
    "man_mode",  # Required by switch component
    "op_man_addition",  # Required by switch component
    "op_man_dhw",  # Required by switch component
    "op_mode",  # Required by switch components for availability checks
    "tap_water_stop",  # Required by number component
    "tap_water_start",  # Required by number component
    "smart_sh_mode",  # Required by select component
    "smart_dhw_mode",  # Required by select component
    "use_adaptive",  # Required by select component and switch components
    "room_comp_factor",  # Required by number component
    "fan_normal",  # Required by number component
    "fan_speed_2",  # Required by number component
    "enable_sc_dhw",
    "enable_sc_sh",
    "compressorenergy",
    "additionalenergy",
    "heatingenergy",
    "dhwenergy",
    "powertotal",
    "fanspeedselector",
    "tap_water_cap",
]

# Modbus-only intermediate metrics required to compute derived values (e.g. energy totals
# like compressorenergy, and powertotal). These are NOT available via the HTTP API and must
# only be included in the fetch list when Modbus is enabled to avoid noisy "missing metric"
# warnings and a larger HTTP query string.
REQUIRED_MODBUS_METRICS = [
    "compressor_power",  # Used to compute powertotal
    "smart_dhw_control_status",
    "compressor_mwh",
    "compressor_kwh",
    "additional_mwh",
    "additional_kwh",
    "heating_mwh",
    "heating_kwh",
    "cooling_mwh",
    "cooling_kwh",
    "dhw_mwh",
    "dhw_kwh",
]

# Sensor filtering patterns
EXCLUDED_METRIC_PATTERNS = [
    "op_man_",
    "enable",
    "smart_",
    "picpin_",
    "use_",
]

# Sensor type classification
TEMPERATURE_METRICS = [
    "temp",
    "bt",
    "tap_water_start",
    "tap_water_stop",
]
ENERGY_METRICS = ["energy"]
POWER_METRICS = ["powertotal"]
CURRENT_METRICS = ["current"]
PRESSURE_METRICS = ["pressure"]

# Firmware component keys
FIRMWARE_KEYS = ["display_fw_version", "cc_fw_version", "inv_fw_version"]

# Tap water capacity mappings (stop, start) -> capacity
TAP_WATER_CAPACITY_MAPPINGS = {
    (58, 52): 1,  # Capacity 1
    (62, 52): 2,  # Capacity 2
    (74, 55): 6,  # Capacity 6
    (76, 55): 7,  # Capacity 7
}

# Relay power constants (watts) used to compute total power from relay stages.
# NOTE:
# The base system power is an estimate of the constant power draw of the heat pump system when no relays are active.
# This includes circulation pumps, controls and other auxiliary loads that are always on when the system is running.
# The relay heat stage power values are actual ratings for the heater stages.
BASE_SYSTEM_POWER_W = 160.0  # Base system consumption when relays are off (W)
RELAY_HEAT_L1_POWER_W = 2000.0  # Heater stage L1 rating (W)
RELAY_HEAT_L2_POWER_W = 2000.0  # Heater stage L2 rating (W)
RELAY_HEAT_L3_POWER_W = 1000.0  # Heater stage L3 rating (W)

# Relay stage wattage map used in Modbus metrics translation
RELAY_STAGE_POWER_MAP = {
    "picpin_relay_heat_l1": RELAY_HEAT_L1_POWER_W,
    "picpin_relay_heat_l2": RELAY_HEAT_L2_POWER_W,
    "picpin_relay_heat_l3": RELAY_HEAT_L3_POWER_W,
}

# Modbus data mappings moved from api.py
MODBUS_INPUT_REGISTER_MAP = {
    "bt1 - fast filtered (1min) outdoor temp": (0, "int16", 0.1),
    "bt2 - indoor temperature": (2, "int16", 0.1),
    "bt4 sensor value": (4, "int16", 0.1),
    "bt10 - heating medium condenser outlet": (5, "int16", 0.1),
    "bt11 - heating supply with addition": (6, "int16", 0.1),
    "bt12 - heating supply external": (7, "int16", 0.1),
    "bt13 - heating medium condenser inlet": (8, "int16", 0.1),
    "bt14 - heating source flow": (9, "int16", 0.1),
    "bt15 - heating source return": (10, "int16", 0.1),
    "bt20 - discharge line": (11, "int16", 0.1),
    "bt21 - liquid line": (12, "int16", 0.1),
    "bt22 - evaporator inlet": (13, "int16", 0.1),
    "bt23 - suction line": (14, "int16", 0.1),
    "bt30 - dhw tank": (16, "int16", 0.1),
    "bt31 - dhw inlet": (17, "int16", 0.1),
    "bt33 - dhw secondary inlet": (18, "int16", 0.1),
    "bt34 - dhw secondary outlet": (19, "int16", 0.1),
    "btx sensor value": (21, "int16", 0.1),
    "bf1 - dhw flow": (26, "uint16", 0.01),
    "bf1 rpm": (27, "uint16", 0.1),
    "gp1 - distribution system circulation pump speed": (28, "uint16", 0.1),
    "gp2 - dhw circulation pump speed": (29, "uint16", 1.0),
    "fan speed [rpm]": (30, "uint16", 1.0),
    "compressor speed [rpm]": (31, "uint16", 1.0),
    "relays (l1, l2, l3, gp10, qm10, qn8_1, qn8_2, gp3, pump, ha12)": (
        33,
        "uint16",
        1.0,
    ),
    "calculated supply temp heating (°c)": (35, "int16", 0.1),
    "heatpump state": (41, "uint16", 1.0),
    "compressor state": (70, "uint16", 1.0),
    "qn8 position": (76, "int16", 1.0),
    "compressor power (w)": (93, "uint16", 1.0),
    "compressor mwh": (95, "uint16", 1.0),
    "compressor kwh": (96, "uint16", 0.1),
    "additional mwh": (97, "uint16", 1.0),
    "additional kwh": (98, "uint16", 0.1),
    "heating mwh": (99, "uint16", 1.0),
    "heating kwh": (100, "uint16", 0.1),
    "cooling mwh": (101, "uint16", 1.0),
    "cooling kwh": (102, "uint16", 0.1),
    "dhw mwh": (103, "uint16", 1.0),
    "dhw kwh": (104, "uint16", 0.1),
    "smart dhw mode (0=off, 1=eco, 2=balanced, 3=comfort)": (161, "uint16", 1.0),
    "smart dhw control status (0=unavailable, 1=standby, 2=raising, 3=lowering, 4=lowering long term, 5=paused)": (
        162,
        "uint16",
        1.0,
    ),
    "enable smart price for the user (dhw) (0=off, 1=on)": (163, "uint16", 1.0),
    "enable smart price for the user (space heating) (0=off, 1=on)": (
        164,
        "uint16",
        1.0,
    ),
}

MODBUS_HOLDING_REGISTER_MAP = {
    "unit_on_off": (0, "uint16", 1.0),
    "operation_mode": (
        1,
        "uint16",
        1.0,
    ),
    "allow_heating": (2, "uint16", 1.0),
    "allow_cooling": (3, "uint16", 1.0),
    "allow_addition": (4, "uint16", 1.0),
    "allow_dhw": (5, "uint16", 1.0),
    "time_between_modes": (6, "uint16", 1.0),
    "allow_addition_temp": (
        7,
        "int16",
        1.0,
    ),
    "filtertime_outdoor": (8, "uint16", 1.0),
    "use_operation_sensor": (
        9,
        "uint16",
        1.0,
    ),
    "desired_indoor_temp": (12, "int16", 0.1),
    "room_compensation": (13, "uint16", 0.1),
    "room_temp_external": (14, "int16", 0.1),
    "heating_offset": (15, "int16", 1.0),
    "outdoor_stop_heating": (18, "int16", 1.0),
    "max_heating_supply": (19, "int16", 1.0),
    "min_heating_supply": (
        20,
        "uint16",
        1.0,
    ),
    "curve_type_heating": (
        22,
        "uint16",
        1.0,
    ),
    "temp_compensation_curve": (
        23,
        "uint16",
        1.0,
    ),
    "heating_curve_neg30": (24, "uint16", 1.0),
    "heating_curve_neg20": (25, "uint16", 1.0),
    "heating_curve_neg10": (26, "uint16", 1.0),
    "heating_curve_0": (27, "uint16", 1.0),
    "heating_curve_10": (28, "uint16", 1.0),
    "heating_curve_20": (29, "uint16", 1.0),
    "heating_curve_30": (30, "uint16", 1.0),
    "cooling_offset": (36, "int16", 1.0),
    "start_cooling_temp": (38, "int16", 1.0),
    "dew_point_protection": (
        39,
        "uint16",
        1.0,
    ),
    "min_cooling_supply": (40, "int16", 1.0),
    "dhw_mode": (53, "uint16", 1.0),
    "dhw_start_normal": (56, "uint16", 1.0),
    "dhw_stop_normal": (57, "uint16", 1.0),
    "dhw_start_extra": (58, "uint16", 1.0),
    "dhw_stop_extra": (59, "uint16", 1.0),
    "dhw_outlet_temp": (
        60,
        "uint16",
        1.0,
    ),
    "dhw_uninterrupted_cooling": (
        61,
        "uint16",
        1.0,
    ),
    "pump_speed_heating": (
        63,
        "uint16",
        1.0,
    ),
    "pump_speed_cooling": (
        64,
        "uint16",
        1.0,
    ),
    "pump_speed_dhw": (65, "uint16", 1.0),
    "pump_idle_speed": (
        66,
        "uint16",
        1.0,
    ),
    "ventilation_state": (
        68,
        "uint16",
        1.0,
    ),
    "fan_speed_reduced": (69, "uint16", 1.0),
    "fan_speed_normal": (70, "uint16", 1.0),
    "fan_speed_extra": (71, "uint16", 1.0),
    "compressor_fan_speed": (72, "uint16", 1.0),
    "heating_priority_time": (
        73,
        "uint16",
        1.0,
    ),
    "cooling_priority_time": (
        74,
        "uint16",
        1.0,
    ),
    "dhw_priority_time": (75, "uint16", 1.0),
    "bt12_mounted": (83, "uint16", 1.0),
    "qs_unit_connected": (84, "uint16", 1.0),
    "sg_enabled": (88, "uint16", 1.0),
    "outdoor_air_mixed": (89, "uint16", 1.0),
    "reset_alarms": (99, "uint16", 1.0),
}

RELAY_BIT_MAP = {
    "picpin_relay_heat_l1": 0,
    "picpin_relay_heat_l2": 1,
    "picpin_relay_heat_l3": 2,
    "picpin_relay_gp10": 3,
    "picpin_relay_qm10": 4,
    "picpin_relay_qn8_1": 5,
    "picpin_relay_qn8_2": 6,
    "picpin_relay_gp3": 7,
    "picpin_relay_pump": 8,
    "picpin_relay_ha12": 9,
}

MODBUS_SPEC_TO_INTERNAL_MAP = {
    # Input registers
    "bt1 - fast filtered (1min) outdoor temp": "bt1",
    "bt2 - indoor temperature": "bt2",
    "bt4 sensor value": "bt4",
    "bt10 - heating medium condenser outlet": "bt10",
    "bt11 - heating supply with addition": "bt11",
    "bt12 - heating supply external": "bt12",
    "bt13 - heating medium condenser inlet": "bt13",
    "bt14 - heating source flow": "bt14",
    "bt15 - heating source return": "bt15",
    "bt20 - discharge line": "bt20",
    "bt21 - liquid line": "bt21",
    "bt22 - evaporator inlet": "bt22",
    "bt23 - suction line": "bt23",
    "bt30 - dhw tank": "bt30",
    "bt31 - dhw inlet": "bt31",
    "bt33 - dhw secondary inlet": "bt33",
    "bt34 - dhw secondary outlet": "bt34",
    "btx sensor value": "btx",
    "bf1 - dhw flow": "bf1_l_min",
    "bf1 rpm": "bf1_rpm",
    "gp1 - distribution system circulation pump speed": "gp1_speed",
    "gp2 - dhw circulation pump speed": "gp2_speed",
    "fan speed [rpm]": "fanrpm",
    "compressor speed [rpm]": "compressormeasuredspeed",
    "relays (l1, l2, l3, gp10, qm10, qn8_1, qn8_2, gp3, pump, ha12)": "relays_bitmask",
    "calculated supply temp heating (°c)": "cal_heat_temp",
    "heatpump state": "hp_status",
    "compressor state": "compressor_state",
    "qn8 position": "qn8position",
    "compressor power (w)": "compressor_power",
    "compressor mwh": "compressor_mwh",
    "compressor kwh": "compressor_kwh",
    "additional mwh": "additional_mwh",
    "additional kwh": "additional_kwh",
    "heating mwh": "heating_mwh",
    "heating kwh": "heating_kwh",
    "cooling mwh": "cooling_mwh",
    "cooling kwh": "cooling_kwh",
    "dhw mwh": "dhw_mwh",
    "dhw kwh": "dhw_kwh",
    "smart dhw mode (0=off, 1=eco, 2=balanced, 3=comfort)": "smart_dhw_mode",
    "smart dhw control status (0=unavailable, 1=standby, 2=raising, 3=lowering, 4=lowering long term, 5=paused)": "smart_dhw_control_status",
    "enable smart price for the user (dhw) (0=off, 1=on)": "enable_sc_dhw",
    "enable smart price for the user (space heating) (0=off, 1=on)": "enable_sc_sh",
}

MODBUS_INTERNAL_TO_SPEC_MAP = {v: k for k, v in MODBUS_SPEC_TO_INTERNAL_MAP.items()}


# Modbus input register internal→HTTP key map (source data conversion path)
MODBUS_INPUT_TO_HTTP_MAP = {
    "dhw_normal_start": "tap_water_start",
    "dhw_normal_stop": "tap_water_stop",
    "tap_stop": "tap_stop",
    "smart_price_dhw_enabled": "enable_sc_dhw",
    "smart_price_heating_enabled": "enable_sc_sh",
}

# Modbus holding register internal→HTTP key map (settings conversion path)
MODBUS_HOLDING_TO_HTTP_MAP = {
    "dhw_start_normal": "tap_water_start",
    "dhw_stop_normal": "tap_water_stop",
    "dhw_mode": "extra_tap_water",
    "room_compensation": "room_comp_factor",
    "desired_indoor_temp": "indoor_temperature_target",
    "heating_offset": "indoor_temperature_offset",
    "use_operation_sensor": "sensor_mode",
    "fan_speed_normal": "fan_normal",
    "fan_speed_reduced": "fan_speed_2",
    "operation_mode": "op_mode",
    "allow_dhw": "op_man_dhw",
    "allow_addition": "op_man_addition",
    "allow_heating": "man_mode",
    "ventilation_state": "fanspeedselector",
}
