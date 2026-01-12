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
VERSION = "2026.1.4"
CONFIG_VERSION = 4

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
    "cal_heat_temp",
    "compressormeasuredspeed",
    "fanrpm",
    "dhw_normal_start",
    "dhw_normal_stop",
    "fan0_10v",
    "gp1_speed",
    "hp_status",
    "picpin_relay_heat_l1",
    "picpin_relay_heat_l2",
    "picpin_relay_heat_l3",
    "tap_water_cap",
    "compressorenergy",
    "additionalenergy",
    "powertotal",
    "op_man_addition",
    "op_man_dhw",
    "op_mode",
    "man_mode",
    "enable_sc_dhw",
    "enable_sc_sh",
    "use_adaptive",
    "smart_sh_mode",
    "smart_dhw_mode",
]

DEFAULT_DISABLED_METRICS = [
    "gp2_speed",
    "calc_suppy_cpr",  # Note: Typo is in the original data
    "btx",
    "bt4",
    "dhwpower",
    "heatingpower",
    "bt10",
    "bt12",
    "bt13",
    "bt20",
    "bt21",
    "bt22",
    "bt23",
    "bt31",
    "cooling_enabled",
    "dhw_outl_temp_15",
    "dhw_outl_temp_5",
    "dhw_outl_temp_max",
    "dhw_prioritytime",
    "guide_des_temp",
    "guide_he",
    "op_man_cooling",
    "op_mode_sensor",
    "picpin_relay_qm10",
    "price_region",
    "qn8position",
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
    "smart_sh_mode",  # Required by select component
    "smart_dhw_mode",  # Required by select component
    "use_adaptive",  # Required by select component and switch components
    "room_comp_factor",  # Required by number component
]

# Sensor filtering patterns
EXCLUDED_METRIC_PATTERNS = [
    "op_man_",
    "enable",
    "smart_",
    "picpin_",
    "qn8",
    "use_",
]

# Sensor type classification
TEMPERATURE_METRICS = ["temp", "bt", "dhw_normal_st"]
ENERGY_METRICS = ["energy"]
POWER_METRICS = ["powertotal", "heatingpower", "dhwpower"]
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
