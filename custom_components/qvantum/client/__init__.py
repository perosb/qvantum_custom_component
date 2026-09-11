"""Vendored Qvantum communication library (HTTP + Modbus).

This package must not import Home Assistant. It is the seed of a future
standalone ``qvantum-client`` distribution.

Transport implementations live in ``client.cloud`` and ``client.modbus`` and
are imported from those subpackages so ``from .client.constants`` (used by
HA ``const.py``) does not pull aiohttp or ``modbus_connection``.
"""

from .constants import (
    BASE_SYSTEM_POWER_W,
    DHW_MODE_ECO,
    DHW_MODE_EXTRA,
    DHW_MODE_NORMAL,
    DHW_MODE_SMART,
    FAN_SPEED_STATE_EXTRA,
    FAN_SPEED_STATE_NORMAL,
    FAN_SPEED_STATE_OFF,
    FAN_SPEED_VALUE_EXTRA,
    FAN_SPEED_VALUE_NORMAL,
    FAN_SPEED_VALUE_OFF,
    RELAY_HEAT_L1_POWER_W,
    RELAY_HEAT_L2_POWER_W,
    RELAY_HEAT_L3_POWER_W,
    RELAY_STAGE_POWER_MAP,
    SETTING_UPDATE_APPLIED,
    TAP_WATER_CAPACITY_MAPPINGS,
)
from .exceptions import (
    APIAuthError,
    APIConnectionError,
    APIRateLimitError,
    AuthError,
    RateLimitError,
    TransportError,
)
from .models import ApplyResult, Device, MetricsPayload, SettingsPayload
from .protocol import QvantumClient

__all__ = [
    "APIAuthError",
    "APIConnectionError",
    "APIRateLimitError",
    "ApplyResult",
    "AuthError",
    "BASE_SYSTEM_POWER_W",
    "DHW_MODE_ECO",
    "DHW_MODE_EXTRA",
    "DHW_MODE_NORMAL",
    "DHW_MODE_SMART",
    "Device",
    "FAN_SPEED_STATE_EXTRA",
    "FAN_SPEED_STATE_NORMAL",
    "FAN_SPEED_STATE_OFF",
    "FAN_SPEED_VALUE_EXTRA",
    "FAN_SPEED_VALUE_NORMAL",
    "FAN_SPEED_VALUE_OFF",
    "MetricsPayload",
    "QvantumClient",
    "RELAY_HEAT_L1_POWER_W",
    "RELAY_HEAT_L2_POWER_W",
    "RELAY_HEAT_L3_POWER_W",
    "RELAY_STAGE_POWER_MAP",
    "RateLimitError",
    "SETTING_UPDATE_APPLIED",
    "SettingsPayload",
    "TAP_WATER_CAPACITY_MAPPINGS",
    "TransportError",
]
