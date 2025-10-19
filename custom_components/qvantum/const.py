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
VERSION = "2025.10.3"
CONFIG_VERSION = 4

NAMES = [
    "bf1_l_min",
    "bp1_pressure",
    "bp1_temp",
    "bp2_pressure",
    "bp2_temp",
    "bt1",
    # "bt10",
    "bt11",
    # "bt12",
    # "bt13",
    "bt14",
    "bt15",
    "bt2",
    # "bt20",
    # "bt21",
    # "bt22",
    # "bt23",
    "bt30",
    # "bt31",
    "bt33",
    "bt34",
    "cal_heat_temp",
    "compressormeasuredspeed",
    # "cooling_enabled",
    # "dhw_outl_temp_15",
    # "dhw_outl_temp_5",
    # "dhw_outl_temp_max",
    # "dhw_prioritytime",
    "dhw_normal_start",
    "dhw_normal_stop",
    # "enable_sc_dhw",
    "fan0_10v",
    "gp1_speed",
    "gp2_speed",
    # "guide_des_temp",
    # "guide_he",
    "hp_status",
    # "man_mode",
    # "op_man_addition",
    # "op_man_cooling",
    # "op_man_dhw",
    # "op_mode",
    # "op_mode_sensor",
    "picpin_relay_heat_l1",
    "picpin_relay_heat_l2",
    "picpin_relay_heat_l3",
    # "picpin_relay_qm10",
    # "price_region",
    # "qn8position",
    # "room_temp_ext",
    # "smart_sh_mode",
    "tap_water_cap",
    # "use_adaptive",
    "compressorenergy",
    "additionalenergy",
    "dhwpower",
    "heatingpower",
    "powertotal",
    # "dhwdemand",
    # "heatingdemand",
    # "coolingdemand",
    # "heatingreleased",
    # "coolingreleased",
    # "compressorreleased",
    # "additionreleased", # Note: Additional typo is in the original data
    # "dhw_prioritytimeleft",
    # "heating_prioritytimeleft",
    # "cooling_priotitytimeleft", # Note: Typo is in the original data
    # "switch_state",
    # "dhwstop_temp",
    # "dhwstart_temp",
    # "filtered60sec_outdoortemp",
    # "max_freq_env",
    "calc_suppy_cpr", # Note: Typo is in the original data
    # "dhw_set",
    # "bp1_temp_20min_filter",
    # "max_bp2_env",
    # "inputcurrent1",
    # "inputcurrent2",
    # "inputcurrent3",
    # "picpin_mask",
]
