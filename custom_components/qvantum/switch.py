"""Interfaces with the Qvantum Heat Pump api sensors."""

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchDeviceClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MyConfigEntry
from .coordinator import QvantumDataUpdateCoordinator, handle_setting_update_response
from .curve_coordinator import QvantumCurveCoordinator
from .entity import QvantumEntity, finalize_platform_setup

_LOGGER = logging.getLogger(__name__)

# Switch metrics that write booleans rather than 0/1 integers.
_BOOLEAN_SWITCH_KEYS = frozenset({"enable_sc_dhw", "enable_sc_sh", "vacation_mode"})


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up the Switch."""
    coordinator: QvantumDataUpdateCoordinator = config_entry.runtime_data.coordinator
    device: DeviceInfo = config_entry.runtime_data.device

    switch_names = [
        "extra_tap_water",
        "op_mode",
        "op_man_dhw",
        "op_man_addition",
        "man_mode",
    ]
    if not coordinator.modbus_enabled:
        # Cloud-only writes: SmartControl and vacation_mode. On Modbus,
        # vacation_mode is a read-only input (binary sensor).
        switch_names.extend(["enable_sc_sh", "enable_sc_dhw", "vacation_mode"])

    sensors = []
    for switch_name in switch_names:
        if switch_name in coordinator.data.get("values", {}):
            sensors.append(QvantumSwitchEntity(coordinator, switch_name, device))

    curve_coordinator = getattr(config_entry.runtime_data, "curve_coordinator", None)
    switch_metrics = set(switch_names)
    if coordinator.modbus_enabled and isinstance(
        curve_coordinator, QvantumCurveCoordinator
    ):
        sensors.append(
            QvantumCurveControlSwitch(coordinator, curve_coordinator, device)
        )
        switch_metrics.add("adaptive_curve_control")

    finalize_platform_setup(
        hass,
        coordinator,
        async_add_entities,
        sensors,
        switch_metrics,
        "switch",
    )

    _LOGGER.debug("Setting up platform SWITCH")


class QvantumSwitchEntity(QvantumEntity, SwitchEntity):
    """Switch entity for a writable Qvantum setting."""

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        metric_key: str,
        device: DeviceInfo,
    ) -> None:
        super().__init__(coordinator, metric_key, device)
        self._attr_device_class = SwitchDeviceClass.SWITCH

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the setting off."""
        await self._async_write(False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the setting on."""
        await self._async_write(True)

    async def _async_write(self, turn_on: bool) -> None:
        """Write the on/off value, mapping per-metric transports."""
        # vacation_mode is intentionally available without elevated write
        # access, so it must not be blocked by the generic write guard.
        if self._metric_key != "vacation_mode":
            self._require_write_access()

        if self._metric_key == "extra_tap_water":
            response = await self.coordinator.async_set_extra_tap_water(
                self._hpid, -1 if turn_on else 0
            )
            value: bool | str = "on" if turn_on else "off"
        elif self._metric_key in _BOOLEAN_SWITCH_KEYS:
            value = turn_on
            response = await self.coordinator.client.update_setting(
                self._hpid, self._metric_key, value
            )
        else:
            value = 1 if turn_on else 0
            response = await self.coordinator.client.update_setting(
                self._hpid, self._metric_key, value
            )

        await handle_setting_update_response(
            response, self.coordinator, "values", self._metric_key, value
        )

    @property
    def is_on(self):
        if not self.coordinator.data:
            return False

        # Handle both integer (== 1) and boolean (True) values
        value = self._values.get(self._metric_key)
        return value == "on" or value == 1 or value is True

    @property
    def available(self):
        if not self.coordinator.data:
            return False

        if not super().available:
            return False

        values = self._values
        match self._metric_key:
            case "extra_tap_water":
                return (
                    values.get("extra_tap_water") is not None
                    and self._has_write_access
                )
            case "op_man_addition" | "op_man_dhw" | "man_mode":
                return (
                    self._metric_key in values
                    and values.get("op_mode") == 1
                    and self._has_write_access
                )

            case "enable_sc_dhw" | "enable_sc_sh":
                return (
                    self._metric_key in values
                    and values.get("use_adaptive") is True
                    and self._has_write_access
                )

            case "vacation_mode":
                if getattr(self.coordinator, "modbus_enabled", False):
                    return False
                return values.get("vacation_mode") is not None

            case _:
                return (
                    values.get(self._metric_key) is not None
                    and self._has_write_access
                )


class QvantumCurveControlSwitch(QvantumEntity, SwitchEntity):
    """Enable active custom-curve writing; off is shadow mode.

    The switch is the only user-facing source of truth for whether the curve
    module may write. It is bound to the main coordinator for write access
    but reflects the curve coordinator's control mode.
    """

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_icon = "mdi:chart-bell-curve"

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        curve_coordinator: QvantumCurveCoordinator,
        device: DeviceInfo,
    ) -> None:
        super().__init__(coordinator, "adaptive_curve_control", device)
        self._curve = curve_coordinator

    @property
    def suggested_object_id(self) -> str | None:
        """Stable English slug."""
        return "adaptive_curve_control"

    async def async_added_to_hass(self) -> None:
        """Follow control-mode changes from the curve coordinator."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._curve.async_add_listener(self._handle_curve_update)
        )

    @callback
    def _handle_curve_update(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._curve.active

    @property
    def available(self) -> bool:
        """Only offer the switch when a write could actually succeed."""
        if not super().available:
            return False
        if not getattr(self._curve, "last_update_success", True):
            return False
        return self._has_write_access

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Activate writing: points first, holding 22 last."""
        self._require_write_access()
        await self._curve.async_set_control_mode("active")

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Return to shadow mode and holding 22 Auto."""
        self._require_write_access()
        await self._curve.async_set_control_mode("shadow")

