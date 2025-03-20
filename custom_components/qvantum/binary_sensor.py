"""Interfaces with the Qvantum Heat Pump api sensors."""

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import EntityCategory


from . import MyConfigEntry
from .const import DOMAIN
from .coordinator import QvantumDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up the Sensors."""
    # This gets the data update coordinator from the config entry runtime data as specified in your __init__.py
    coordinator: QvantumDataUpdateCoordinator = config_entry.runtime_data.coordinator
    device: DeviceInfo = config_entry.runtime_data.device

    sensors = []
    sensors.append(QvantumConnectedEntity(coordinator, "connected", "connected", device))

    async_add_entities(sensors)


class QvantumConnectedEntity(CoordinatorEntity, BinarySensorEntity):
    """Sensor for qvantum."""

    def __init__(self, coordinator: QvantumDataUpdateCoordinator, metric_key: str, name: str, device: DeviceInfo) -> None:
        super().__init__(coordinator)
        self._hpid = self.coordinator.data.get("metrics").get("hpid")
        self._metric_key = metric_key
        self._attr_unique_id = f"qvantum_{metric_key}_{self._hpid}"
        self._attr_device_info = device
        self._attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_has_entity_name = True

    @property
    def is_on(self):
        """Get metric from API data."""
        return self.coordinator.data.get("connectivity").get(self._metric_key) == True

    @property
    def available(self):
        """Check if data is available."""
        return self._metric_key in self.coordinator.data.get("connectivity") and \
                   self.coordinator.data.get("connectivity").get(self._metric_key) is not None
