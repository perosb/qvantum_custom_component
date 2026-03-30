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
VERSION = "2026.3.1"
CONFIG_VERSION = 4

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
    "tap_water_cap",
    "op_man_addition",
    "op_man_dhw",
    "op_mode",
    "man_mode",
    "enable_sc_dhw",
    "enable_sc_sh",
    "use_adaptive",
    "smart_dhw_mode",
    "smart_dhw_control_status",
    "qn8position",
    "compressorenergy",
    "additionalenergy",
    "heatingenergy",
    "dhwenergy",
    "powertotal",
]

DEFAULT_DISABLED_METRICS = [
    "gp2_speed",
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

# Metrics that must always be fetched regardless of entity enablement
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
    "compressor_power",  # Required for power sensor and modbus mapping
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
    "compressorenergy",
    "additionalenergy",
    "heatingenergy",
    "dhwenergy",
    "powertotal",
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
MODBUS_REGISTER_MAP = {
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
    # System control
    "unit_on_off": (0, "uint16", 1.0),  # Unit On/Off (0=Off, 1=On)
    "operation_mode": (
        1,
        "uint16",
        1.0,
    ),  # Operation mode (see Modbus specification for value definitions)
    "allow_heating": (2, "uint16", 1.0),  # Allow heating in manual mode (0=No, 1=Yes)
    "allow_cooling": (3, "uint16", 1.0),  # Allow cooling in manual mode (0=No, 1=Yes)
    "allow_addition": (4, "uint16", 1.0),  # Allow addition in manual mode (0=No, 1=Yes)
    "allow_dhw": (5, "uint16", 1.0),  # Allow DHW in manual mode (0=No, 1=Yes)
    # Timing and protection
    "time_between_modes": (6, "uint16", 1.0),  # Time between heating and cooling (h)
    "allow_addition_temp": (
        7,
        "int16",
        1.0,
    ),  # Allow addition temperature (°C, -29=Off)
    "filtertime_outdoor": (8, "uint16", 1.0),  # Filter time outdoor sensor (h)
    "use_operation_sensor": (
        9,
        "uint16",
        1.0,
    ),  # Use operation mode sensor (0=No, 1=BT2, 2=BT3, 3=AUX, 4=External)
    # Temperature settings
    "desired_indoor_temp": (12, "int16", 0.1),  # Desired indoor temperature (°C)
    "room_compensation": (13, "uint16", 0.1),  # Room compensation factor
    "room_temp_external": (14, "int16", 0.1),  # Room temp external (°C)
    "heating_offset": (15, "int16", 1.0),  # Heating curve parallel offset (°C)
    "outdoor_stop_heating": (18, "int16", 1.0),  # Outdoor temperature stop heating (°C)
    "max_heating_supply": (19, "int16", 1.0),  # Maximum heating supply temperature (°C)
    "min_heating_supply": (
        20,
        "uint16",
        1.0,
    ),  # Minimum heating supply temperature (°C)
    # Heating curve settings
    "curve_type_heating": (
        22,
        "uint16",
        1.0,
    ),  # Curve type heating (0=Auto, 1=User defined)
    "temp_compensation_curve": (
        23,
        "uint16",
        1.0,
    ),  # Temperature compensation curve heating
    "heating_curve_neg30": (24, "uint16", 1.0),  # User defined heating curve @ -30°C
    "heating_curve_neg20": (25, "uint16", 1.0),  # User defined heating curve @ -20°C
    "heating_curve_neg10": (26, "uint16", 1.0),  # User defined heating curve @ -10°C
    "heating_curve_0": (27, "uint16", 1.0),  # User defined heating curve @ 0°C
    "heating_curve_10": (28, "uint16", 1.0),  # User defined heating curve @ 10°C
    "heating_curve_20": (29, "uint16", 1.0),  # User defined heating curve @ 20°C
    "heating_curve_30": (30, "uint16", 1.0),  # User defined heating curve @ 30°C
    # Cooling settings
    "cooling_offset": (36, "int16", 1.0),  # Cooling curve parallel offset (°C)
    "start_cooling_temp": (38, "int16", 1.0),  # Start cooling temperature (°C)
    "dew_point_protection": (
        39,
        "uint16",
        1.0,
    ),  # Dew point protection cooling (0=No, 1=Yes)
    "min_cooling_supply": (40, "int16", 1.0),  # Minimum cooling supply temperature (°C)
    # DHW settings
    "dhw_mode": (53, "uint16", 1.0),  # DHW Mode (0=Eco, 1=Normal, 2=Extra, 3=Smart)
    "dhw_start_normal": (56, "uint16", 1.0),  # DHW start temperature Normal (°C)
    "dhw_stop_normal": (57, "uint16", 1.0),  # DHW stop temperature Normal (°C)
    "dhw_start_extra": (58, "uint16", 1.0),  # DHW start temperature Extra (°C)
    "dhw_stop_extra": (59, "uint16", 1.0),  # DHW stop temperature Extra (°C)
    "dhw_outlet_temp": (
        60,
        "uint16",
        1.0,
    ),  # DHW outlet temperature level (0=Normal, 1=+, 2=++)
    "dhw_uninterrupted_cooling": (
        61,
        "uint16",
        1.0,
    ),  # DHW uninterrupted cooling (0=No, 1=Yes)
    # Pump speeds
    "pump_speed_heating": (
        63,
        "uint16",
        1.0,
    ),  # Distribution circulation pump speed heating (%)
    "pump_speed_cooling": (
        64,
        "uint16",
        1.0,
    ),  # Distribution circulation pump speed cooling (%)
    "pump_speed_dhw": (65, "uint16", 1.0),  # DHW circulation pump speed (%)
    "pump_idle_speed": (
        66,
        "uint16",
        1.0,
    ),  # Distribution circulation pump idle speed (%)
    # Ventilation
    "ventilation_state": (
        68,
        "uint16",
        1.0,
    ),  # Ventilation State (0=Off, 1=Normal, 2=Extra, 3=Reduced)
    "fan_speed_reduced": (69, "uint16", 1.0),  # Reduced ventilation fan speed (%)
    "fan_speed_normal": (70, "uint16", 1.0),  # Normal ventilation fan speed (%)
    "fan_speed_extra": (71, "uint16", 1.0),  # Extra ventilation fan speed (%)
    "compressor_fan_speed": (72, "uint16", 1.0),  # Compressor fan speed (%)
    # Priority times
    "heating_priority_time": (
        73,
        "uint16",
        1.0,
    ),  # Heating priority time (min, 0=No priority)
    "cooling_priority_time": (
        74,
        "uint16",
        1.0,
    ),  # Cooling priority time (min, 0=No priority)
    "dhw_priority_time": (75, "uint16", 1.0),  # DHW priority time (min, 0=No priority)
    # System configuration
    "bt12_mounted": (83, "uint16", 1.0),  # BT12 Mounted (0=No, 1=Yes)
    "qs_unit_connected": (84, "uint16", 1.0),  # QS unit connected (0=No, 1=Yes)
    "sg_enabled": (88, "uint16", 1.0),  # SG enabled (0=No, 1=Yes)
    "outdoor_air_mixed": (89, "uint16", 1.0),  # Outdoor Air Mixed (0=No, 1=Yes)
    "reset_alarms": (99, "uint16", 1.0),  # Reset Alarms (1=Resets alarms)
}

RELAY_BIT_MAP = {
    "picpin_relay_heat_l1": 0,  # L1
    "picpin_relay_heat_l2": 1,  # L2
    "picpin_relay_heat_l3": 2,  # L3
    "picpin_relay_gp10": 3,  # GP10
    "picpin_relay_qm10": 4,  # QM10
    "picpin_relay_qn8_1": 5,  # QN8_1
    "picpin_relay_qn8_2": 6,  # QN8_2
    "picpin_relay_gp3": 7,  # GP3
    "picpin_relay_pump": 8,  # Pump
    "picpin_relay_ha12": 9,  # HA12
}

MODBUS_SPEC_TO_INTERNAL_MAP = {
    # Input registers
    "bt1 - fast filtered (1min) outdoor temp": "bt1",
    "bt2 - indoor temperature": "bt2",
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
    "bt24 - crank case": "bt24",
    "bt30 - dhw tank": "bt30",
    "bt31 - dhw inlet": "bt31",
    "bt33 - dhw secondary inlet": "bt33",
    "bt34 - dhw secondary outlet": "bt34",
    "bf1 - dhw flow": "bf1_l_min",
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
    "dhw start temperature normal (°c)": "dhw_normal_start",
    "dhw stop temperature normal (°c)": "dhw_normal_stop",
    # Holding registers
    "unit on/off": "unit_on_off",
    "operation mode": "operation_mode",
    "operation manual mode - allow heating": "allow_heating",
    "operation manual mode - allow cooling": "allow_cooling",
    "operation manual mode - allow addition": "allow_addition",
    "operation manual mode - allow dhw": "allow_dhw",
    "time between heating and cooling": "time_between_modes",
    "allow addition temperature": "allow_addition_temp",
    "filtertime outdoor sensor": "filtertime_outdoor",
    "use operation mode sensor": "use_operation_sensor",
    "desired indoor temperature": "desired_indoor_temp",
    "room compensation factor": "room_compensation",
    "room temp external": "room_temp_external",
    "heating curve parallel offset": "heating_offset",
    "outdoor temperature stop heating": "outdoor_stop_heating",
    "maximum heating supply temperature": "max_heating_supply",
    "minimum heating supply temperature": "min_heating_supply",
    "curve type heating": "curve_type_heating",
    "temperature compensation curve heating": "temp_compensation_curve",
    "user defined heating curve @ -30c": "heating_curve_neg30",
    "user defined heating curve @ -20c": "heating_curve_neg20",
    "user defined heating curve @ -10c": "heating_curve_neg10",
    "user defined heating curve @ 0c": "heating_curve_0",
    "user defined heating curve @ 10c": "heating_curve_10",
    "user defined heating curve @ 20c": "heating_curve_20",
    "user defined heating curve @ 30c": "heating_curve_30",
    "cooling curve parallel offset": "cooling_offset",
    "start cooling temperature": "start_cooling_temp",
    "dew point protection (cooling)": "dew_point_protection",
    "minimum cooling supply temperature": "min_cooling_supply",
    "dhw mode": "dhw_mode",
    "dhw start temperature normal": "dhw_start_normal",
    "dhw stop temperature normal": "dhw_stop_normal",
    "dhw start temperature extra": "dhw_start_extra",
    "dhw stop temperature extra": "dhw_stop_extra",
    "dwh outlet temperature": "dhw_outlet_temp",
    "dhw uninterrupted cooling": "dhw_uninterrupted_cooling",
    "distribution circulation pump speed heating": "pump_speed_heating",
    "distribution circulation pump speed cooling": "pump_speed_cooling",
    "dhw circulation pump speed": "pump_speed_dhw",
    "distribution circulation pump idle speed": "pump_idle_speed",
    "ventilation state": "ventilation_state",
    "reduced ventilation fan speed": "fan_speed_reduced",
    "normal ventilation fan speed": "fan_speed_normal",
    "extra ventilation fan speed": "fan_speed_extra",
    "compressor fan speed": "compressor_fan_speed",
    "heating priority time": "heating_priority_time",
    "cooling priority time": "cooling_priority_time",
    "dhw priority time": "dhw_priority_time",
    "bt12 mounted": "bt12_mounted",
    "qs unit connected": "qs_unit_connected",
    "sg enabled": "sg_enabled",
    "outdoor air mixed": "outdoor_air_mixed",
    "reset alarms": "reset_alarms",
}

MODBUS_INTERNAL_TO_SPEC_MAP = {v: k for k, v in MODBUS_SPEC_TO_INTERNAL_MAP.items()}


# Modbus input register internal→HTTP key map (source data conversion path)
MODBUS_INPUT_TO_HTTP_MAP = {
    "dhw_normal_start": "tap_water_start",
    "dhw_normal_stop": "tap_water_stop",
    "tap_stop": "tap_stop",
    "desired_indoor_temp": "indoor_temperature_target",
    "heating_offset": "indoor_temperature_offset",
    "use_operation_sensor": "sensor_mode",
    "dhw_mode": "extra_tap_water",
    "room_compensation": "room_comp_factor",
    "fan_speed_normal": "fan_normal",
    "fan_speed_reduced": "fan_speed_2",
    "operation_mode": "op_mode",
    "allow_dhw": "op_man_dhw",
    "allow_addition": "op_man_addition",
    "allow_heating": "man_mode",
    "ventilation_state": "fanspeedselector",
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
