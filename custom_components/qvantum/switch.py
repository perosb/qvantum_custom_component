"""Interfaces with the Qvantum Heat Pump api sensors."""

import logging
from datetime import datetime

from homeassistant.const import STATE_ON
from homeassistant.components.switch import SwitchEntity, SwitchDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MyConfigEntry
from .coordinator import QvantumDataUpdateCoordinator, handle_setting_update_response

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up the Switch."""
    coordinator: QvantumDataUpdateCoordinator = config_entry.runtime_data.coordinator
    device: DeviceInfo = config_entry.runtime_data.device

    sensors = []
    sensors.append(QvantumSwitchEntity(coordinator, "extra_tap_water", device))

    sensors.append(QvantumSwitchEntity(coordinator, "op_mode", device))
    sensors.append(QvantumSwitchEntity(coordinator, "op_man_dhw", device))
    sensors.append(QvantumSwitchEntity(coordinator, "op_man_addition", device))

    async_add_entities(sensors)

    _LOGGER.debug("Setting up platform SWITCH")


class QvantumSwitchEntity(CoordinatorEntity, SwitchEntity):
    """Sensor for qvantum."""

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        metric_key: str,
        device: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self._hpid = self.coordinator.data.get("metrics").get("hpid")
        self._attr_translation_key = metric_key
        self._metric_key = metric_key
        self._attr_unique_id = f"qvantum_{metric_key}_{self._hpid}"
        self._attr_device_info = device
        self._attr_device_class = SwitchDeviceClass.SWITCH
        self._attr_has_entity_name = True
        self._attr_is_on = False

        match self._metric_key:
            case "op_mode":
                self._attr_icon = "mdi:auto-mode"
            case "op_man_dhw":
                self._attr_icon = "mdi:water-outline"
            case "op_man_addition":
                self._attr_icon = "mdi:transmission-tower-import"
            case _:
                self._attr_icon = "mdi:water-boiler"

    async def async_turn_off(self, **kwargs):
        """Update the current value."""
        response = {}
        match self._metric_key:
            case "extra_tap_water":
                response = await self.coordinator.api.set_extra_tap_water(self._hpid, 0)
                # Data will be updated via coordinator refresh

            case _:
                response = await self.coordinator.api.update_setting(
                    self._hpid, self._metric_key, 0
                )
                await handle_setting_update_response(
                    response, self.coordinator, "metrics", self._metric_key, 0
                )

    async def async_turn_on(self, **kwargs):
        """Update the current value."""
        response = {}
        match self._metric_key:
            case "extra_tap_water":
                response = await self.coordinator.api.set_extra_tap_water(
                    self._hpid, -1
                )
                # Data will be updated via coordinator refresh

            case _:
                response = await self.coordinator.api.update_setting(
                    self._hpid, self._metric_key, 1
                )
                await handle_setting_update_response(
                    response, self.coordinator, "metrics", self._metric_key, 1
                )

    @property
    def is_on(self):
        match self._metric_key:
            case "extra_tap_water":
                return (
                    self.coordinator.data.get("settings").get(self._metric_key)
                    == STATE_ON
                )

        return self.coordinator.data.get("metrics").get(self._metric_key) == 1

    @property
    def available(self):
        match self._metric_key:
            case "extra_tap_water":
                return (
                    "extra_tap_water_stop" in self.coordinator.data.get("settings", {})
                    and self.coordinator.data.get("settings", {}).get(
                        "extra_tap_water_stop"
                    )
                    is not None
                )
            case _:
                if (
                    self._metric_key == "op_man_addition"
                    or self._metric_key == "op_man_dhw"
                ):
                    return (
                        self._metric_key in self.coordinator.data.get("metrics", {})
                        and self.coordinator.data.get("metrics", {}).get("op_mode") == 1
                    )

                return (
                    self._metric_key in self.coordinator.data.get("metrics", {})
                    and self.coordinator.data.get("metrics", {}).get(self._metric_key)
                    is not None
                )
