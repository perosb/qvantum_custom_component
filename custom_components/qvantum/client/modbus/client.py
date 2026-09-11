"""Modbus TCP client for a Qvantum heat pump.

Accepts an injected ``ModbusUnit``. The client never opens or closes the TCP
connection; Home Assistant (or a test) owns that.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Awaitable

from modbus_connection import ModbusError, ModbusUnit

from ..constants import (
    DHW_MODE_EXTRA,
    DHW_MODE_NORMAL,
    FAN_SPEED_STATE_EXTRA,
    FAN_SPEED_STATE_NORMAL,
    FAN_SPEED_STATE_OFF,
    FAN_SPEED_VALUE_EXTRA,
    FAN_SPEED_VALUE_NORMAL,
    FAN_SPEED_VALUE_OFF,
    SETTING_UPDATE_APPLIED,
    TAP_WATER_CAPACITY_MAPPINGS,
)
from ..exceptions import TransportError
from .device import IdentityProbeError, QvantumModbusDevice, holding_field_for_metric
from .maps import MODBUS_HOLDING_REGISTER_MAP, MODBUS_HOLDING_TO_SETTINGS_MAP

_LOGGER = logging.getLogger(__name__)

_APPLIED = {"status": SETTING_UPDATE_APPLIED}
_FAN_PRESETS = {
    FAN_SPEED_STATE_OFF: FAN_SPEED_VALUE_OFF,
    FAN_SPEED_STATE_NORMAL: FAN_SPEED_VALUE_NORMAL,
    FAN_SPEED_STATE_EXTRA: FAN_SPEED_VALUE_EXTRA,
}


class QvantumModbusClient:
    """Qvantum heat pump reached over an injected ``ModbusUnit``."""

    def __init__(
        self,
        unit: ModbusUnit | None = None,
        *,
        writable: bool = False,
    ) -> None:
        self._unit = unit
        self._writable = bool(writable)
        self._device: QvantumModbusDevice | None = (
            QvantumModbusDevice(unit) if unit is not None else None
        )
        self._lock = asyncio.Lock()
        self._closed = False

    @property
    def device(self) -> QvantumModbusDevice | None:
        """Device wrapper for the current unit, if any."""
        return self._device

    @property
    def unit(self) -> ModbusUnit | None:
        """Injected Modbus unit, if any."""
        return self._unit

    @property
    def writable(self) -> bool:
        """Whether holding-register writes are allowed."""
        return self._writable

    @writable.setter
    def writable(self, value: bool) -> None:
        self._writable = bool(value)

    def attach_unit(self, unit: ModbusUnit) -> QvantumModbusDevice:
        """Bind a unit (used by tests that inject a mock after construct)."""
        self._unit = unit
        self._device = QvantumModbusDevice(unit)
        return self._device

    def _ensure_open(self) -> None:
        if self._closed:
            raise TransportError(None, "Modbus client is closed")

    def _ensure_device(self) -> QvantumModbusDevice | None:
        if self._closed:
            return None
        if self._device is None:
            if self._unit is None:
                return None
            self._device = QvantumModbusDevice(self._unit)
        return self._device

    def _ensure_writable(self) -> None:
        if not self._writable:
            raise TransportError(None, "Modbus writing is disabled")

    async def close(self) -> None:
        """Drop the device wrapper. Does not close the injected TCP unit."""
        if self._closed:
            return
        self._closed = True
        async with self._lock:
            self._device = None

    async def _run(
        self,
        operation: Callable[[QvantumModbusDevice], Awaitable[Any]],
        *,
        error_label: str,
        missing_client_message: str = "Modbus client not initialized",
        failure_prefix: str = "Modbus communication failed",
    ):
        """Run a device operation under the lock, mapping errors."""
        self._ensure_open()
        async with self._lock:
            self._ensure_open()
            device = self._ensure_device()
            if not device:
                raise TransportError(None, missing_client_message)
            try:
                return await operation(device)
            except asyncio.CancelledError:
                raise
            except ModbusError as err:
                _LOGGER.error("Modbus error %s: %s", error_label, err)
                raise TransportError(None, f"{failure_prefix}: {err}") from err
            except (TransportError, ValueError, IdentityProbeError):
                raise
            except Exception as err:
                _LOGGER.error(
                    "Unexpected error %s: %s", error_label, err, exc_info=True
                )
                raise TransportError(None, f"{failure_prefix}: {err}") from err

    async def get_metrics(
        self, device_id: str, enabled_metrics: list[str] | None = None
    ) -> dict[str, Any]:
        """Read input registers and return an HTTP-shaped metrics payload."""

        async def _update(device: QvantumModbusDevice):
            await device.async_update_inputs()
            payload = device.metrics_payload(device_id, enabled_metrics)
            _LOGGER.debug(
                "Raw Modbus metrics read: %s",
                sorted(payload.get("metrics", {}).items()),
            )
            return payload

        return await self._run(_update, error_label="reading input registers")

    async def get_settings(
        self, device_id: str, enabled_settings: list[str] | None = None
    ) -> dict[str, Any]:
        """Read holding registers exposed as HTTP-shaped settings."""
        enabled = enabled_settings or [
            key
            for key in MODBUS_HOLDING_TO_SETTINGS_MAP
            if key in MODBUS_HOLDING_REGISTER_MAP
        ]

        async def _update(device: QvantumModbusDevice):
            await device.async_update_settings()
            return device.settings_payload(enabled)

        return await self._run(_update, error_label="reading holding registers")

    async def probe_identity(self) -> dict[str, Any]:
        """Read serial and firmware from the identity island."""

        async def _probe(device: QvantumModbusDevice):
            await device.async_update_identity()
            serial = device.serial_number
            if not serial:
                raise IdentityProbeError("Heat pump did not return a serial number")
            return {
                "id": serial,
                "serial": serial,
                "vendor": "Qvantum",
                "sw_version": device.sw_version,
            }

        return await self._run(_probe, error_label="probing identity")

    async def async_probe_identity(self) -> dict[str, Any]:
        """Compatibility alias for ``probe_identity``."""
        return await self.probe_identity()

    async def write_holding_register(
        self, device_id: str, register_address: int, value: int
    ) -> dict[str, str]:
        """Write a raw holding register (FC06)."""
        self._ensure_writable()

        async def _write(device: QvantumModbusDevice):
            await device.write_holding_register(register_address, int(value))
            return dict(_APPLIED)

        return await self._run(
            _write,
            error_label=f"writing holding register {register_address} for device {device_id}",
            missing_client_message=f"Modbus client not initialized for device {device_id}",
            failure_prefix="Modbus write failed",
        )

    async def write_metric(
        self, device_id: str, metric_key: str, value: float
    ) -> dict[str, str]:
        """Write a holding field by HTTP/settings metric name."""
        self._ensure_writable()
        holding_field_for_metric(metric_key)

        async def _write(device: QvantumModbusDevice):
            await device.write_metric(metric_key, value)
            return dict(_APPLIED)

        return await self._run(
            _write,
            error_label=f"writing metric {metric_key} for device {device_id}",
            missing_client_message=f"Modbus client not initialized for device {device_id}",
            failure_prefix="Modbus write failed",
        )

    async def write_holding_register_for_metric(
        self, device_id: str, metric_key: str, value: float
    ) -> dict[str, str]:
        """Compatibility alias for ``write_metric``."""
        return await self.write_metric(device_id, metric_key, value)

    async def update_setting(
        self, device_id: str, name: str, value: Any
    ) -> dict[str, str]:
        """Write one setting, coercing bools to 0/1."""
        if isinstance(value, bool):
            value = int(value)
        return await self.write_metric(device_id, name, value)

    async def set_indoor_temperature_target(
        self, device_id: str, temperature: float
    ) -> dict[str, str]:
        return await self.write_metric(
            device_id, "indoor_temperature_target", temperature
        )

    async def set_indoor_temperature_offset(
        self, device_id: str, value: int
    ) -> dict[str, str]:
        return await self.write_metric(device_id, "indoor_temperature_offset", value)

    async def set_tap_water(
        self, device_id: str, start: int = 0, stop: int = 0
    ) -> dict[str, str] | None:
        if stop == 0 and start == 0:
            _LOGGER.debug("No tap water settings to update, both stop and start are 0.")
            return dict(_APPLIED)
        if stop:
            await self.write_metric(device_id, "tap_water_stop", stop)
        if start:
            await self.write_metric(device_id, "tap_water_start", start)
        return dict(_APPLIED)

    async def set_tap_water_capacity_target(
        self, device_id: str, capacity: int
    ) -> dict[str, str] | None:
        capacity_to_stop_start = {v: k for k, v in TAP_WATER_CAPACITY_MAPPINGS.items()}
        start, stop = capacity_to_stop_start[capacity]
        _LOGGER.debug(
            "Setting tap water capacity %s maps to stop %s and start %s.",
            capacity,
            stop,
            start,
        )
        return await self.set_tap_water(device_id, start=start, stop=stop)

    async def set_fanspeedselector(
        self, device_id: str, preset_mode: str
    ) -> dict[str, str]:
        if preset_mode not in _FAN_PRESETS:
            raise ValueError(f"Invalid preset_mode: {preset_mode}")
        return await self.write_metric(
            device_id, "fanspeedselector", _FAN_PRESETS[preset_mode]
        )

    async def set_extra_tap_water(
        self, device_id: str, minutes: int
    ) -> dict[str, str]:
        """Write DHW Extra if minutes != 0 else Normal. No restore timer."""
        mode = DHW_MODE_NORMAL if minutes == 0 else DHW_MODE_EXTRA
        return await self.write_metric(device_id, "extra_tap_water", mode)
