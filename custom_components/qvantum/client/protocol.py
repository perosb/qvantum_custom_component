"""Shared client protocol implemented by cloud HTTP and local Modbus."""

from __future__ import annotations

from typing import Any, Protocol

from .models import ApplyResult, MetricsPayload, SettingsPayload


class QvantumClient(Protocol):
    """Transport-agnostic heat-pump client.

    Cloud and Modbus both speak canonical metric/setting names and return
    HTTP-shaped payloads so Home Assistant coordinators stay protocol-blind.
    """

    async def get_metrics(
        self,
        device_id: str,
        enabled_metrics: list[str] | None = None,
    ) -> MetricsPayload:
        """Fetch live metrics for ``device_id``."""
        ...

    async def get_settings(self, device_id: str) -> SettingsPayload:
        """Fetch settings for ``device_id``."""
        ...

    async def update_setting(
        self, device_id: str, name: str, value: Any
    ) -> ApplyResult:
        """Write a single named setting."""
        ...

    async def set_indoor_temperature_target(
        self, device_id: str, temperature: float
    ) -> ApplyResult:
        """Write the indoor temperature setpoint."""
        ...

    async def set_indoor_temperature_offset(
        self, device_id: str, value: int
    ) -> ApplyResult:
        """Write the indoor temperature offset."""
        ...

    async def set_tap_water(
        self, device_id: str, start: int = 0, stop: int = 0
    ) -> ApplyResult:
        """Write DHW start/stop temperatures."""
        ...

    async def set_tap_water_capacity_target(
        self, device_id: str, capacity: int
    ) -> ApplyResult:
        """Write the DHW capacity level (1–7)."""
        ...

    async def set_fanspeedselector(
        self, device_id: str, preset_mode: str
    ) -> ApplyResult:
        """Write fan preset (``off`` / ``normal`` / ``extra``)."""
        ...

    async def set_extra_tap_water(self, device_id: str, minutes: int) -> ApplyResult:
        """Request extra DHW.

        Cloud encodes duration on the wire. Modbus writes Extra/Normal only;
        any restore timer belongs to the caller.
        """
        ...

    async def close(self) -> None:
        """Release owned resources. Must not close injected connections."""
        ...
