"""Interfaces with the Qvantum Heat Pump api sensors."""

import logging

from homeassistant.components.switch import (
    SwitchEntity,
    SwitchDeviceClass
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import STATE_ON, STATE_OFF

from .const import SETTING_UPDATE_APPLIED
from . import MyConfigEntry
from .coordinator import QvantumDataUpdateCoordinator

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
    sensors.append(QvantumSwitch(coordinator, "extra_tap_water", device))

    async_add_entities(sensors)

    _LOGGER.debug(f"Setting up platform SWITCH")

class QvantumSwitch(CoordinatorEntity, SwitchEntity):
    """Sensor for qvantum."""

    def __init__(self, coordinator: QvantumDataUpdateCoordinator, metric_key: str, device: DeviceInfo) -> None:
        super().__init__(coordinator)
        self._hpid = self.coordinator.data.get("metrics").get("hpid")
        self._attr_translation_key = metric_key
        self._metric_key = metric_key
        self._attr_unique_id = f"qvantum_{metric_key}_{self._hpid}"
        self._attr_device_info = device
        self._attr_device_class = SwitchDeviceClass.SWITCH
        self._attr_has_entity_name = True
        self._attr_icon = "mdi:water-boiler"
        self._attr_is_on = False
       
    async def async_turn_off(self, **kwargs):
        """Update the current value."""
        response = {}
        match self._metric_key:
            case "extra_tap_water":
                response = await self.coordinator.api.set_extra_tap_water(self._hpid, 0)
        
        if response.get("status") == SETTING_UPDATE_APPLIED:
            self.coordinator.data.get("settings")[self._metric_key] = STATE_OFF
            self.coordinator.async_set_updated_data(self.coordinator.data)

    async def async_turn_on(self, **kwargs):
        """Update the current value."""
        response = {}
        match self._metric_key:
            case "extra_tap_water":
                response = await self.coordinator.api.set_extra_tap_water(self._hpid, 60)
        
        if response.get("status") == SETTING_UPDATE_APPLIED:
            self.coordinator.data.get("settings")[self._metric_key] = STATE_ON
            self.coordinator.async_set_updated_data(self.coordinator.data)

    @property
    def is_on(self):
        return self.coordinator.data.get("settings").get(self._metric_key) == STATE_ON

    @property
    def available(self):
        return self._metric_key in self.coordinator.data.get("settings") and \
                    self.coordinator.data.get("settings").get(self._metric_key) is not None
