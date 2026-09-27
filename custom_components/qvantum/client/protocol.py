"""Shared client protocol implemented by cloud HTTP and local Modbus.

``QvantumClient`` is the contract both transports share. The transport
protocols narrow it to the operations only one side implements, so the Home
Assistant coordinator can type its client as either without pretending a
Modbus client can do SmartControl, or that a cloud client can write holding
registers.

Write methods document the ``APPLIED`` success shape. Cloud returns the raw
API body (the settings PATCH path answers ``{"success": true}``), so callers
must treat the response as opaque and check ``status`` / ``heatpump_status``
before applying optimistic updates.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .models import ApplyResult, MetricsPayload, SettingsPayload


@runtime_checkable
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

    async def set_curve_type_heating(
        self, device_id: str, value: int
    ) -> ApplyResult:
        """Write Auto (0) / User defined (1) heating-curve source."""
        ...

    async def set_heating_curve_point(
        self, device_id: str, metric_key: str, value: int
    ) -> ApplyResult:
        """Write one user-defined heating-curve supply point (canonical name)."""
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


@runtime_checkable
class QvantumCloudClientProtocol(QvantumClient, Protocol):
    """Cloud-only operations: inventory, metadata, access, SmartControl."""

    async def update_settings(
        self, device_id: str, settings: dict
    ) -> ApplyResult:
        """Write several settings in one command."""
        ...

    async def set_smartcontrol(self, device_id: str, sh: int, dhw: int) -> ApplyResult:
        """Write the SmartControl heating/DHW modes."""
        ...

    async def get_device_metadata(self, device_id: str) -> dict[str, Any]:
        """Fetch device status metadata (firmware versions, model)."""
        ...

    async def get_http_metrics(
        self, device_id: str, metric_names: list[str]
    ) -> MetricsPayload:
        """Fetch a one-off metrics snapshot without touching the poll cache."""
        ...

    async def get_devices(self) -> list | None:
        """List the devices on the account."""
        ...

    async def get_primary_device(self) -> dict[str, Any] | None:
        """Fetch the account's primary device with metadata merged in."""
        ...

    async def get_access_level(self, device_id: str) -> dict[str, Any]:
        """Fetch the caller's write access level for the device."""
        ...

    async def elevate_access(self, device_id: str) -> dict[str, Any] | None:
        """Elevate write access via the cloud access-grant flow."""
        ...


@runtime_checkable
class QvantumModbusClientProtocol(QvantumClient, Protocol):
    """Modbus-only operations: holding writes and identity probe."""

    @property
    def writable(self) -> bool:
        """Whether holding-register writes are allowed."""
        ...

    async def write_metric(
        self, device_id: str, metric_key: str, value: float
    ) -> ApplyResult:
        """Write a holding field by HTTP/settings metric name."""
        ...

    async def probe_identity(self) -> dict[str, Any]:
        """Read serial and firmware from the identity island."""
        ...
