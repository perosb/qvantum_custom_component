"""Compatibility re-export; adapter lives in ``client.modbus.device``."""

from .client.modbus.device import (
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

__all__ = [
    "IdentityProbeError",
    "QvantumModbusDevice",
    "async_probe_identity",
    "build_metrics_payload",
    "build_settings_payload",
    "component_values",
    "decode_serial_number",
    "decode_sw_version",
    "holding_field_for_metric",
]
