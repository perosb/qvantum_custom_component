"""Qvantum Modbus maps, typed components, and HTTP-shaped adapter."""

from .device import (
    IdentityProbeError,
    QvantumModbusDevice,
    async_probe_identity,
    build_metrics_payload,
    build_settings_payload,
    component_values,
    decode_serial_number,
    decode_sw_version,
    holding_field_for_metric,
)
from .maps import (
    MODBUS_HOLDING_REGISTER_MAP,
    MODBUS_HOLDING_TO_SETTINGS_MAP,
    MODBUS_IDENTITY_REGISTER_MAP,
    MODBUS_INPUT_REGISTER_MAP,
    RELAY_BIT_MAP,
)
from .model import QvantumIdentity, QvantumInputs, QvantumSettings

__all__ = [
    "IdentityProbeError",
    "MODBUS_HOLDING_REGISTER_MAP",
    "MODBUS_HOLDING_TO_SETTINGS_MAP",
    "MODBUS_IDENTITY_REGISTER_MAP",
    "MODBUS_INPUT_REGISTER_MAP",
    "QvantumIdentity",
    "QvantumInputs",
    "QvantumModbusDevice",
    "QvantumSettings",
    "RELAY_BIT_MAP",
    "async_probe_identity",
    "build_metrics_payload",
    "build_settings_payload",
    "component_values",
    "decode_serial_number",
    "decode_sw_version",
    "holding_field_for_metric",
]
