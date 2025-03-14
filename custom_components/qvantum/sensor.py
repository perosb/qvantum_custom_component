"""Interfaces with the Qvantum Heat Pump api sensors."""

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy, UnitOfTemperature, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_utils
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
    device: DeviceInfo = config_entry.runtime_data.device

    sensors = []
    metrics = await coordinator.api.get_available_metrics(coordinator.data.get('device').get('id'))
    for metric in metrics.get("metrics"):
        if metric.get("unit") == UnitOfTemperature.CELSIUS:
            sensors.append(QvantumTemperatureSensor(coordinator, metric.get("name"), metric.get("name").replace("_", " ").lower(), device))
        elif metric.get("unit") == UnitOfEnergy.KILO_WATT_HOUR:
            sensors.append(QvantumEnergySensor(coordinator, metric.get("name"), metric.get("name").replace("_", " ").lower(), device))
        else:
            sensors.append(QvantumGenericSensor(coordinator, metric.get("name"), metric.get("name").replace("_", " ").lower(), device))

    sensors.append(QvantumTotalEnergySensor(coordinator, "totalenergy", "total energy", device))
    sensors.append(QvantumConnectivitySensor(coordinator, "timestamp", "timestamp", device))
    sensors.append(QvantumTimerSensor(coordinator, "extra_tap_water_stop", "extra tap water stop", device))
    sensors.append(QvantumTimerSensor(coordinator, "ventilation_boost_stop", "ventilation boost stop", device))
    sensors.append(QvantumConnectivitySensor(coordinator, "disconnect_reason", "disconnect reason", device))
    sensors.append(QvantumDiagnosticSensor(coordinator, "hpid", "heatpump id", device))

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

class QvantumDiagnosticSensor(QvantumGenericSensor):
    """Sensor for diagnostic."""

    def __init__(self, coordinator: QvantumDataUpdateCoordinator, metric_key: str, name: str, device: DeviceInfo) -> None:
        super().__init__(coordinator, metric_key, name, device)
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

class QvantumTimerSensor(QvantumGenericSensor):
    """Sensor for connectivity."""

    def __init__(self, coordinator: QvantumDataUpdateCoordinator, metric_key: str, name: str, device: DeviceInfo) -> None:
        super().__init__(coordinator, metric_key, name, device)
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_device_class = "timestamp"

    @property
    def state(self):
        """Get metric from API data."""
        epoch = self.coordinator.data.get("settings").get(self._metric_key)
        return dt_utils.utc_from_timestamp(epoch)

    @property
    def available(self):
        """Check if data is available."""
        return self._metric_key in self.coordinator.data.get("settings") and \
                   self.coordinator.data.get("settings").get(self._metric_key) is not None and \
                   self.coordinator.data.get("settings").get(self._metric_key) > 0

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
