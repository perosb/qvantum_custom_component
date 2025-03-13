"""Interfaces with the Qvantum Heat Pump api sensors."""

import logging

from homeassistant.components.number import (
    NumberEntity
)
from homeassistant.const import UnitOfEnergy, UnitOfTemperature, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MyConfigEntry
from .const import DOMAIN
from .coordinator import QvantumDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up the Number."""
    # This gets the data update coordinator from the config entry runtime data as specified in your __init__.py
    coordinator: QvantumDataUpdateCoordinator = config_entry.runtime_data.coordinator
    device: DeviceInfo = config_entry.runtime_data.device

    sensors = []
    sensors.append(QvantumCapacityNumber(coordinator, "tap_water_capacity_target", device))

    async_add_entities(sensors)

    _LOGGER.debug(f"Setting up platform NUMBER")

class QvantumCapacityNumber(CoordinatorEntity, NumberEntity):
    """Sensor for qvantum."""

    def __init__(self, coordinator: QvantumDataUpdateCoordinator, metric_key: str, device: DeviceInfo) -> None:
        super().__init__(coordinator)
        self._hpid = self.coordinator.data.get("metrics").get("hpid")
        self._attr_translation_key = metric_key
        self._metric_key = metric_key
        self._attr_unique_id = f"qvantum_{metric_key}_{self._hpid}"
        self._attr_device_info = device
        self._attr_has_entity_name = True
        self._attr_native_min_value = 1
        self._attr_native_max_value = 5
        self._attr_native_step = 1
        

    async def async_set_native_value(self, value: float) -> None:
        """Update the current value."""
        await self.coordinator.api.set_tap_water_capacity_target(self._hpid, int(value))

    @property
    def state(self):
        """Get metric from API data."""
        return self.coordinator.data.get("settings").get(self._metric_key)

    @property
    def available(self):
        """Check if data is available."""
        return self._metric_key in self.coordinator.data.get("settings") and \
                   self.coordinator.data.get("settings").get(self._metric_key) is not None
