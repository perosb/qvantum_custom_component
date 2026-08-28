"""Qvantum Modbus device object and dict adapter.

Consumes a ``ModbusUnit`` and exposes the same metrics/settings payloads the
HTTP path uses, so coordinators and entities do not need to change.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from .const import (
    BASE_SYSTEM_POWER_W,
    FAN_SPEED_STATE_EXTRA,
    FAN_SPEED_STATE_NORMAL,
    FAN_SPEED_STATE_OFF,
    RELAY_STAGE_POWER_MAP,
)
from .modbus import MODBUS_HOLDING_TO_SETTINGS_MAP
from .modbus_model import QvantumInputs, QvantumSettings

if TYPE_CHECKING:
    from modbus_connection import ModbusUnit

_ENERGY_PREFIXES = ("compressor", "additional", "heating", "cooling", "dhw")
_HIDDEN_SETTINGS = frozenset({"dhw_start_normal", "dhw_stop_normal"})
_SETTINGS_NAME_TO_HOLDING = {
    settings_name: holding_key
    for holding_key, settings_name in MODBUS_HOLDING_TO_SETTINGS_MAP.items()
}

# Fan holding register 68: 0/1/2 -> off/normal/extra.
_FAN_SPEED_STATES = {
    0: FAN_SPEED_STATE_OFF,
    1: FAN_SPEED_STATE_NORMAL,
    2: FAN_SPEED_STATE_EXTRA,
}


def component_values(component) -> dict[str, Any]:
    """Return decoded public values for a component, skipping unread fields."""
    data: dict[str, Any] = {}
    for name, field in component.declared_fields.items():
        value = getattr(component, name)
        if value is None:
            continue
        if isinstance(value, bool):
            data[name] = int(value)
            continue
        scale = getattr(field, "scale", 1.0) or 1.0
        if scale == 1.0:
            data[name] = int(value)
        else:
            data[name] = round(float(value), 2)
    return data


def build_metrics_payload(
    device_id: str,
    values: dict[str, Any],
    enabled_metrics: list[str] | None = None,
) -> dict[str, Any]:
    """Turn raw input-register values into the metrics dict used by the API."""
    metrics = dict(values)
    if enabled_metrics is not None:
        enabled = set(enabled_metrics)
        metrics = {key: value for key, value in metrics.items() if key in enabled}

    if "smart_dhw_mode" in metrics:
        metrics["smart_sh_mode"] = metrics["smart_dhw_mode"]
        metrics["use_adaptive"] = metrics["smart_dhw_mode"] != -1

    relay_sum = 0.0
    for key, wattage in RELAY_STAGE_POWER_MAP.items():
        value = metrics.get(key, 0)
        try:
            relay_sum += float(value) * wattage
        except (TypeError, ValueError):
            continue

    compressor_power = 0.0
    raw_power = metrics.pop("compressor_power", 0.0)
    try:
        compressor_power = float(raw_power)
    except (TypeError, ValueError):
        compressor_power = 0.0

    metrics["powertotal"] = round(
        BASE_SYSTEM_POWER_W + relay_sum + compressor_power, 2
    )

    for prefix in _ENERGY_PREFIXES:
        mwh = metrics.get(f"{prefix}_mwh")
        kwh = metrics.get(f"{prefix}_kwh")
        if mwh is not None and kwh is not None:
            try:
                metrics[f"{prefix}energy"] = round(float(mwh) * 1000.0 + float(kwh), 2)
            except (TypeError, ValueError):
                pass
        metrics.pop(f"{prefix}_mwh", None)
        metrics.pop(f"{prefix}_kwh", None)

    metrics["hpid"] = device_id
    return {"metrics": metrics}


def build_settings_payload(
    values: dict[str, Any],
    enabled_settings: list[str] | None = None,
) -> dict[str, Any]:
    """Turn holding-register values into the settings list used by the API."""
    if enabled_settings is not None:
        enabled = set(enabled_settings)
        values = {key: value for key, value in values.items() if key in enabled}

    settings_dict: dict[str, Any] = {}
    for key, value in values.items():
        settings_dict[key] = value
        http_key = MODBUS_HOLDING_TO_SETTINGS_MAP.get(key)
        if http_key and http_key != key:
            settings_dict[http_key] = value

    if "extra_tap_water" in settings_dict:
        settings_dict["extra_tap_water"] = (
            "on" if settings_dict["extra_tap_water"] == 2 else "off"
        )

    if "fanspeedselector" in settings_dict:
        settings_dict["fanspeedselector"] = _FAN_SPEED_STATES.get(
            settings_dict["fanspeedselector"],
            settings_dict["fanspeedselector"],
        )

    for hide_key in _HIDDEN_SETTINGS:
        settings_dict.pop(hide_key, None)

    return {"settings": [{"name": name, "value": value} for name, value in settings_dict.items()]}


def holding_field_for_metric(metric_key: str) -> str:
    """Resolve an HTTP/settings metric key to a holding-register field name."""
    if metric_key in QvantumSettings.declared_fields:
        return metric_key
    holding_key = _SETTINGS_NAME_TO_HOLDING.get(metric_key)
    if holding_key in QvantumSettings.declared_fields:
        return holding_key
    raise ValueError(f"No Modbus holding register mapping found for metric '{metric_key}'")


class QvantumModbusDevice:
    """Qvantum heat pump reached through a ``ModbusUnit``."""

    def __init__(self, unit: ModbusUnit) -> None:
        self.unit = unit
        self.inputs = QvantumInputs(unit)
        self.settings = QvantumSettings(unit)

    async def async_update_inputs(self) -> None:
        """Refresh input-register metrics."""
        await self.inputs.async_update()

    async def async_update_settings(self) -> None:
        """Refresh holding-register settings."""
        await self.settings.async_update()

    def metrics_payload(
        self, device_id: str, enabled_metrics: list[str] | None = None
    ) -> dict[str, Any]:
        """Build the metrics dict from the last input-register update."""
        return build_metrics_payload(
            device_id, component_values(self.inputs), enabled_metrics
        )

    def settings_payload(
        self, enabled_settings: list[str] | None = None
    ) -> dict[str, Any]:
        """Build the settings list from the last holding-register update."""
        return build_settings_payload(
            component_values(self.settings), enabled_settings
        )

    async def write_holding_register(self, address: int, value: int) -> None:
        """Write a raw holding register (FC06)."""
        await self.unit.write_register(address, value)

    async def write_metric(self, metric_key: str, value: float) -> None:
        """Write a holding field by HTTP/settings metric name, applying scale."""
        field_name = holding_field_for_metric(metric_key)
        await self.settings.write(field_name, value)
