"""Constants for the Qvantum Heat Pump Integration."""

DOMAIN = "qvantum"
DEFAULT_SCAN_INTERVAL = 120
MIN_SCAN_INTERVAL = 60
SETTING_UPDATE_APPLIED = "APPLIED"

# Heat pump status values (hp_status metric)
HP_STATUS_IDLE = 0
HP_STATUS_DEFROSTING = 1
HP_STATUS_HOT_WATER = 2
HP_STATUS_HEATING = 3
HP_STATUS_COOLING = 4
FAN_SPEED_STATE_OFF = "off"
FAN_SPEED_STATE_NORMAL = "normal"
FAN_SPEED_STATE_EXTRA = "extra"
FAN_SPEED_VALUE_OFF = 0
FAN_SPEED_VALUE_NORMAL = 1
FAN_SPEED_VALUE_EXTRA = 2
VERSION = "2026.4.16"
CONFIG_VERSION = 7

# Modbus TCP configuration
CONF_MODBUS_TCP = "modbus_tcp"
CONF_MODBUS_HOST = "modbus_host"
CONF_MODBUS_PORT = "modbus_port"
CONF_MODBUS_UNIT_ID = "modbus_unit_id"
DEFAULT_MODBUS_HOST = "Qvantum-HP"
DEFAULT_MODBUS_PORT = 502
DEFAULT_MODBUS_UNIT_ID = 1

# Metrics available in both HTTP and Modbus modes
DEFAULT_ENABLED_METRICS = [
    "bf1_l_min",
    "bt1",
    "bt10",
    "bt11",
    "bt13",
    "bt14",
    "bt15",
    "bt2",
    "bt20",
    "bt21",
    "bt22",
    "bt23",
    "bt30",
    "bt31",
    "bt33",
    "bt34",
    "cal_heat_temp",
    "compressormeasuredspeed",
    "compressor_state",
    "fanrpm",
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

# Additional metrics only available via Modbus input registers (not the HTTP API)
DEFAULT_ENABLED_MODBUS_ONLY_METRICS = [
    "heatingpower",  # Derived from heatingenergy delta; only meaningful with fast Modbus polling
    "dhwpower",  # Derived from dhwenergy delta; only meaningful with fast Modbus polling
    "smart_dhw_control_status",
]

# Holding register keys that should be exposed as sensor entities in Modbus mode
DEFAULT_ENABLED_MODBUS_HOLDING_METRICS = [
    "start_cooling_temp",
]

# Additional metrics only available via the HTTP API (not Modbus input registers)
DEFAULT_ENABLED_HTTP_ONLY_METRICS = [
    "bp1_pressure",
    "bp1_temp",
    "bp2_pressure",
    "bp2_temp",
    "fan0_10v",
    "tap_water_stop",
    "tap_water_start",
]

DEFAULT_ENABLED_MODBUS_METRICS = (
    DEFAULT_ENABLED_METRICS
    + DEFAULT_ENABLED_MODBUS_ONLY_METRICS
    + DEFAULT_ENABLED_MODBUS_HOLDING_METRICS
)
DEFAULT_ENABLED_HTTP_METRICS = (
    DEFAULT_ENABLED_METRICS + DEFAULT_ENABLED_HTTP_ONLY_METRICS
)

DEFAULT_DISABLED_HTTP_METRICS = [
    "calc_suppy_cpr",  # Note: Typo is in the original data
    "btx",
    "bt12",
    "bt4",
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
    "heatingpower",
    "dhwpower",
    "heatingreleased",
    "coolingreleased",
    "compressorreleased",
    "additionreleased",
    "dhw_prioritytimeleft",
    "heating_prioritytimeleft",
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
    "tap_water_cap",
]

DEFAULT_DISABLED_MODBUS_METRICS = [
    "bf1_rpm",
    "bt12",
    "bt4",
    "dhwdemand",
    "heatingdemand",
    "coolingdemand",
    "additiondemand",
    "additiondhwdemand",
    "dhw_prioritytimeleft",
    "heating_prioritytimeleft",
    "cooling_prioritytimeleft",
    "degree_minute",
]

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
    "smart_sh_mode",
    "smart_dhw_mode",
    "picpin_",
    "use_",
    "demand",
]

# Sensor type classification
TEMPERATURE_METRICS = [
    "temp",
    "bt",
    "tap_water_start",
    "tap_water_stop",
]
ENERGY_METRICS = ["energy"]
POWER_METRICS = ["powertotal", "heatingpower", "dhwpower"]
CURRENT_METRICS = ["current"]
PRESSURE_METRICS = ["pressure"]

# Firmware component keys
FIRMWARE_KEYS = ["display_fw_version", "cc_fw_version", "inv_fw_version"]

# Tap water capacity mappings (start, stop) -> capacity
TAP_WATER_CAPACITY_MAPPINGS = {
    (52, 58): 1,  # Capacity 1
    (52, 62): 2,  # Capacity 2
    (55, 69): 3,  # Capacity 3
    (55, 70): 4,  # Capacity 4
    (55, 71): 5,  # Capacity 5
    (55, 74): 6,  # Capacity 6
    (55, 76): 7,  # Capacity 7
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

# Binary sensor names
BINARY_SENSOR_NAMES = [
    "cooling_enabled",
    "picpin_relay_heat_l1",
    "picpin_relay_heat_l2",
    "picpin_relay_heat_l3",
    "picpin_relay_qm10",
    "dhwdemand",
    "heatingdemand",
    "coolingdemand",
    "additiondemand",
    "additiondhwdemand",
    "time_to_defrost",
]
