"""Interfaces with the Qvantum Heat Pump api sensors."""

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy, UnitOfPower, UnitOfTemperature, EntityCategory
from homeassistant.core import HomeAssistant, callback
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
    """Set up the Sensors."""
    # This gets the data update coordinator from the config entry runtime data as specified in your __init__.py
    coordinator: QvantumDataUpdateCoordinator = config_entry.runtime_data.coordinator

    device = DeviceInfo(
        identifiers={
            (DOMAIN, f"qvantum-{coordinator.data.get('device').get('id')}"),
        },
        manufacturer=coordinator.data.get("device").get("vendor"),
        model=coordinator.data.get("device").get("model"),
        name="Qvantum",
        serial_number=coordinator.data.get("device").get("serial"),
        sw_version=f"{coordinator.data.get('device_metadata').get('display_fw_version')}/{coordinator.data.get('device_metadata').get('cc_fw_version')}/{coordinator.data.get('device_metadata').get('inv_fw_version')}",
    )

    sensors = []
    metrics = await coordinator.api.fetch_metrics(coordinator.data.get('device').get('id'))
    for metric in metrics.get("metrics"):
        if metric.get("unit") == UnitOfTemperature.CELSIUS:
            sensors.append(QvantumTemperatureSensor(coordinator, metric.get("name"), metric.get("name").replace("_", " ").lower(), device))
        elif metric.get("unit") == UnitOfEnergy.KILO_WATT_HOUR:
            sensors.append(QvantumEnergySensor(coordinator, metric.get("name"), metric.get("name").replace("_", " ").lower(), device))
        else:
            sensors.append(QvantumGenericSensor(coordinator, metric.get("name"), metric.get("name").replace("_", " ").lower(), device))

    sensors.append(QvantumTotalEnergySensor(coordinator, "totalenergy", "total energy", device))
    sensors.append(QvantumConnectivitySensor(coordinator, "timestamp", "timestamp", device))
    sensors.append(QvantumConnectivitySensor(coordinator, "disconnect_reason", "disconnect reason", device))

    async_add_entities(sensors)


class QvantumGenericSensor(CoordinatorEntity, SensorEntity):
    """Sensor for qvantum."""

    def __init__(self, coordinator: QvantumDataUpdateCoordinator, metric_key: str, name: str, device: DeviceInfo) -> None:
        super().__init__(coordinator)
        self._hpid = self.coordinator.data.get("metrics").get("hpid")
        self._attr_translation_key = metric_key
        self._metric_key = metric_key
        self._attr_unique_id = f"qvantum_{metric_key}_{self._hpid}"
        self._attr_device_info = device
        self._attr_has_entity_name = True

    @property
    def state(self):
        """Get metric from API data."""
        return self.coordinator.data.get("metrics").get(self._metric_key)

    @property
    def available(self):
        """Check if data is available."""
        return self._metric_key in self.coordinator.data.get("metrics") and \
                   self.coordinator.data.get("metrics").get(self._metric_key) is not None


class QvantumTemperatureSensor(QvantumGenericSensor):
    """Sensor for temperature measurements."""

    def __init__(self, coordinator: QvantumDataUpdateCoordinator, metric_key: str, name: str, device: DeviceInfo) -> None:
        super().__init__(coordinator, metric_key, name, device)
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_state_class = SensorStateClass.MEASUREMENT

class QvantumEnergySensor(QvantumGenericSensor):
    """Sensor for energy measurements."""

    def __init__(self, coordinator: QvantumDataUpdateCoordinator, metric_key: str, name: str, device: DeviceInfo) -> None:
        super().__init__(coordinator, metric_key, name, device)
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING

class QvantumTotalEnergySensor(QvantumEnergySensor):
    """Sensor for energy measurements."""

    def __init__(self, coordinator: QvantumDataUpdateCoordinator, metric_key: str, name: str, device: DeviceInfo) -> None:
        super().__init__(coordinator, metric_key, name, device)

    @property
    def state(self):
        """Get metric from API data."""
        total = self.coordinator.data.get("metrics").get("compressorenergy") + self.coordinator.data.get("metrics").get("additionalenergy")
        return total

    @property
    def available(self):
        """Check if data is available."""
        return "compressorenergy" in self.coordinator.data.get("metrics") and \
                   self.coordinator.data.get("metrics").get("compressorenergy") is not None
   
class QvantumConnectivitySensor(QvantumGenericSensor):
    """Sensor for connectivity."""

    def __init__(self, coordinator: QvantumDataUpdateCoordinator, metric_key: str, name: str, device: DeviceInfo) -> None:
        super().__init__(coordinator, metric_key, name, device)
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        if metric_key == "timestamp":
            self._attr_device_class = "timestamp"

    @property
    def state(self):
        """Get metric from API data."""
        return self.coordinator.data.get("connectivity").get(self._metric_key)

    @property
    def available(self):
        """Check if data is available."""
        return self._metric_key in self.coordinator.data.get("connectivity") and \
                   self.coordinator.data.get("connectivity").get(self._metric_key) is not None
