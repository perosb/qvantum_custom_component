"""Interfaces with the Qvantum Heat Pump api sensors."""

import logging
from typing import Type

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfEnergy,
    UnitOfTemperature,
    UnitOfPower,
    EntityCategory,
    UnitOfPressure,
    UnitOfElectricCurrent,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_utils
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_registry import RegistryEntryDisabler
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DEFAULT_ENABLED_METRICS,
    DEFAULT_DISABLED_METRICS,
    EXCLUDED_METRIC_PATTERNS,
    TEMPERATURE_METRICS,
    ENERGY_METRICS,
    POWER_METRICS,
    CURRENT_METRICS,
    PRESSURE_METRICS,
    TAP_WATER_CAPACITY_METRICS,
)
from .coordinator import QvantumDataUpdateCoordinator
from . import MyConfigEntry

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up the Sensors."""
    coordinator: QvantumDataUpdateCoordinator = config_entry.runtime_data.coordinator
    device: DeviceInfo | dict = config_entry.runtime_data.device

    sensors = []
    metrics = DEFAULT_ENABLED_METRICS + DEFAULT_DISABLED_METRICS

    for metric in metrics:
        if _should_exclude_metric(metric):
            continue

        enabled_default = metric not in DEFAULT_DISABLED_METRICS
        sensor_class = _get_sensor_type(metric)

        sensors.append(
            sensor_class(
                coordinator,
                metric,
                device,
                enabled_default,
            )
        )

    # Add special sensors
    sensors.append(QvantumTotalEnergyEntity(coordinator, "totalenergy", device, True))
    sensors.append(QvantumDiagnosticEntity(coordinator, "latency", device, True))
    sensors.append(QvantumDiagnosticEntity(coordinator, "hpid", device, True))
    sensors.append(
        QvantumTimerEntity(coordinator, "extra_tap_water_stop", device, True)
    )

    async_add_entities(sensors)

    # Disable entities that should be disabled by default
    entity_registry = hass.data["entity_registry"]
    for sensor in sensors:
        if not sensor._attr_entity_registry_enabled_default:
            entity_entry = entity_registry.async_get(sensor.entity_id)
            if entity_entry and entity_entry.disabled_by is None:
                entity_registry.async_update_entity(
                    sensor.entity_id, disabled_by=RegistryEntryDisabler.USER
                )


class QvantumBaseEntity(CoordinatorEntity, SensorEntity):
    """Sensor for qvantum."""

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        metric_key: str,
        device: DeviceInfo | dict,
        enabled_default: bool = True,
    ) -> None:
        super().__init__(coordinator)
        self._hpid = self.coordinator.data.get("metrics").get("hpid")
        self._attr_translation_key = metric_key
        self._metric_key = metric_key
        self._attr_unique_id = f"qvantum_{metric_key}_{self._hpid}"
        self._attr_device_info = device
        self._attr_has_entity_name = True
        self._attr_entity_registry_enabled_default = enabled_default

        # Set units based on metric patterns
        self._set_units_from_metric(metric_key)

    def _set_units_from_metric(self, metric_key: str) -> None:
        """Set appropriate units based on metric key patterns."""
        if "fan" in metric_key or metric_key.startswith("gp"):
            self._attr_native_unit_of_measurement = "%"
        elif "compressormeasuredspeed" in metric_key:
            self._attr_native_unit_of_measurement = "rpm"
        elif "bf1_l_min" == metric_key:
            self._attr_native_unit_of_measurement = "l/m"

    @property
    def state(self):
        """Get metric from API data."""
        return self.coordinator.data.get("metrics").get(self._metric_key)

    @property
    def available(self):
        """Check if data is available."""
        metrics = self.coordinator.data.get("metrics", {})
        return metrics.get(self._metric_key) is not None


class QvantumTapWaterCapacityEntity(QvantumBaseEntity):
    """Sensor for tap water capacity measurements."""

    @property
    def state(self):
        """Get metric from API data."""
        value = super().state
        if value is not None:
            # Note: The API returns the capacity as "number of half-people",
            #       I.e. a value of 4 means capacity for 2 people.
            return value / 2
        return None


class QvantumTemperatureEntity(QvantumBaseEntity):
    """Sensor for temperature measurements."""

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        metric_key: str,
        device: DeviceInfo | dict,
        enabled_default: bool = True,
    ) -> None:
        super().__init__(coordinator, metric_key, device, enabled_default)
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_state_class = SensorStateClass.MEASUREMENT


class QvantumEnergyEntity(QvantumBaseEntity):
    """Sensor for energy measurements."""

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        metric_key: str,
        device: DeviceInfo | dict,
        enabled_default: bool = True,
    ) -> None:
        super().__init__(coordinator, metric_key, device, enabled_default)
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def available(self):
        """Check if data is available."""
        return (
            super().available
            and self.coordinator.data.get("metrics").get(self._metric_key) > 0
        )


class QvantumPowerEntity(QvantumBaseEntity):
    """Sensor for power measurements."""

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        metric_key: str,
        device: DeviceInfo | dict,
        enabled_default: bool = True,
    ) -> None:
        super().__init__(coordinator, metric_key, device, enabled_default)
        self._attr_native_unit_of_measurement = UnitOfPower.WATT
        self._attr_device_class = SensorDeviceClass.POWER
        self._attr_state_class = SensorStateClass.MEASUREMENT


class QvantumCurrentEntity(QvantumBaseEntity):
    """Sensor for current measurements."""

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        metric_key: str,
        device: DeviceInfo | dict,
        enabled_default: bool = True,
    ) -> None:
        super().__init__(coordinator, metric_key, device, enabled_default)
        self._attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
        self._attr_device_class = SensorDeviceClass.CURRENT
        self._attr_state_class = SensorStateClass.MEASUREMENT


class QvantumPressureEntity(QvantumBaseEntity):
    """Sensor for pressure measurements."""

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        metric_key: str,
        device: DeviceInfo | dict,
        enabled_default: bool = True,
    ) -> None:
        super().__init__(coordinator, metric_key, device, enabled_default)
        self._attr_native_unit_of_measurement = UnitOfPressure.BAR
        self._attr_device_class = SensorDeviceClass.PRESSURE
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def available(self):
        """Check if data is available."""
        return (
            super().available
            and self.coordinator.data.get("metrics").get(self._metric_key) > 0
        )


class QvantumTotalEnergyEntity(QvantumEnergyEntity):
    """Sensor for energy measurements."""

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        metric_key: str,
        device: DeviceInfo | dict,
        enabled_default: bool = True,
    ) -> None:
        super().__init__(coordinator, metric_key, device, enabled_default)

    @property
    def state(self):
        """Get metric from API data."""
        total = self.coordinator.data.get("metrics").get(
            "compressorenergy"
        ) + self.coordinator.data.get("metrics").get("additionalenergy")
        return total

    @property
    def available(self):
        """Check if data is available."""
        return (
            "compressorenergy" in self.coordinator.data.get("metrics")
            and self.coordinator.data.get("metrics").get("compressorenergy") is not None
        )


class QvantumDiagnosticEntity(QvantumBaseEntity):
    """Sensor for diagnostic."""

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        metric_key: str,
        device: DeviceInfo | dict,
        enabled_default: bool = True,
    ) -> None:
        super().__init__(coordinator, metric_key, device, enabled_default)
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        if "latency" in metric_key:
            self._attr_device_class = SensorDeviceClass.DURATION
            self._attr_native_unit_of_measurement = "ms"


class QvantumTimerEntity(QvantumBaseEntity):
    """Sensor for connectivity."""

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        metric_key: str,
        device: DeviceInfo | dict,
        enabled_default: bool = True,
    ) -> None:
        super().__init__(coordinator, metric_key, device, enabled_default)
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
        return (
            self._metric_key in self.coordinator.data.get("settings")
            and self.coordinator.data.get("settings").get(self._metric_key) is not None
            and self.coordinator.data.get("settings").get(self._metric_key) > 0
        )


class QvantumLatencyEntity(QvantumBaseEntity):
    """Sensor for connectivity."""

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        metric_key: str,
        device: DeviceInfo | dict,
        enabled_default: bool = True,
    ) -> None:
        super().__init__(coordinator, metric_key, device, enabled_default)
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def state(self):
        """Get metric from API data."""
        return self.coordinator.data.get(self._metric_key)

    @property
    def available(self):
        """Check if data is available."""
        latency = self.coordinator.data.get(self._metric_key)
        return latency is not None


def _should_exclude_metric(metric: str) -> bool:
    """Check if a metric should be excluded from sensor creation."""
    return any(pattern in metric for pattern in EXCLUDED_METRIC_PATTERNS)


def _get_sensor_type(metric: str) -> Type[QvantumBaseEntity]:
    """Determine the appropriate sensor type for a metric."""
    if any(pattern in metric for pattern in TEMPERATURE_METRICS):
        return QvantumTemperatureEntity
    elif any(pattern in metric for pattern in ENERGY_METRICS):
        return QvantumEnergyEntity
    elif any(pattern in metric for pattern in POWER_METRICS):
        return QvantumPowerEntity
    elif any(pattern in metric for pattern in CURRENT_METRICS):
        return QvantumCurrentEntity
    elif any(pattern in metric for pattern in PRESSURE_METRICS):
        return QvantumPressureEntity
    elif any(pattern in metric for pattern in TAP_WATER_CAPACITY_METRICS):
        return QvantumTapWaterCapacityEntity
    else:
        return QvantumBaseEntity
