"""Qvantum Modbus maps, typed components, and HTTP-shaped adapter."""

from .client import QvantumModbusClient
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
    HEATING_CURVE_OUTDOOR_TEMPS,
    MODBUS_HOLDING_REGISTER_MAP,
    MODBUS_HOLDING_TO_SETTINGS_MAP,
    MODBUS_IDENTITY_REGISTER_MAP,
    MODBUS_INPUT_REGISTER_MAP,
    RELAY_BIT_MAP,
    heating_curve_points,
)
from .model import QvantumIdentity, QvantumInputs, QvantumSettings

__all__ = [
    "HEATING_CURVE_OUTDOOR_TEMPS",
    "IdentityProbeError",
    "MODBUS_HOLDING_REGISTER_MAP",
    "MODBUS_HOLDING_TO_SETTINGS_MAP",
    "MODBUS_IDENTITY_REGISTER_MAP",
    "MODBUS_INPUT_REGISTER_MAP",
    "QvantumIdentity",
    "QvantumInputs",
    "QvantumModbusClient",
    "QvantumModbusDevice",
    "QvantumSettings",
    "RELAY_BIT_MAP",
    "async_probe_identity",
    "build_metrics_payload",
    "build_settings_payload",
    "component_values",
    "decode_serial_number",
    "decode_sw_version",
    "heating_curve_points",
    "holding_field_for_metric",
]
