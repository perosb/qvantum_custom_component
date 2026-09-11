"""Compatibility re-export; datasheet maps live in ``client.modbus.maps``."""

from .client.modbus.maps import (
    MODBUS_HOLDING_REGISTER_MAP,
    MODBUS_HOLDING_TO_SETTINGS_MAP,
    MODBUS_IDENTITY_REGISTER_MAP,
    MODBUS_INPUT_REGISTER_MAP,
    RELAY_BIT_MAP,
)

__all__ = [
    "MODBUS_HOLDING_REGISTER_MAP",
    "MODBUS_HOLDING_TO_SETTINGS_MAP",
    "MODBUS_IDENTITY_REGISTER_MAP",
    "MODBUS_INPUT_REGISTER_MAP",
    "RELAY_BIT_MAP",
]
