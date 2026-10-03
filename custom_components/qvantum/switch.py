"""Interfaces with the Qvantum Heat Pump api sensors."""

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MyConfigEntry
from .coordinator import QvantumDataUpdateCoordinator, handle_setting_update_response
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

    finalize_platform_setup(
        hass,
        coordinator,
        async_add_entities,
        sensors,
        set(switch_names),
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
