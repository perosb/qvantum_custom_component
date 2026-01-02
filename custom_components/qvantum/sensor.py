"""Interfaces with the Qvantum Heat Pump api sensors."""

import logging
from datetime import datetime
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
    PRESSURE_METRICS
)
from .entity import QvantumEntity
from . import MyConfigEntry
from .coordinator import QvantumDataUpdateCoordinator
from .firmware_coordinator import QvantumFirmwareUpdateCoordinator

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

        enabled_by_default = metric not in DEFAULT_DISABLED_METRICS
        sensor_class = _get_sensor_type(metric)

        sensors.append(
            sensor_class(
                coordinator,
                metric,
                device,
                enabled_by_default,
            )
        )

    # Add special sensors
    sensors.append(QvantumTotalEnergyEntity(coordinator, "totalenergy", device, True))
    sensors.append(QvantumDiagnosticEntity(coordinator, "latency", device, True))
    sensors.append(QvantumDiagnosticEntity(coordinator, "hpid", device, True))
    sensors.append(
        QvantumTimerEntity(coordinator, "extra_tap_water_stop", device, True)
    )

    # Add firmware sensors
    firmware_coordinator = config_entry.runtime_data.firmware_coordinator
    sensors.append(
        QvantumFirmwareSensorEntity(
            firmware_coordinator, "display_fw_version", device, True
        )
    )
    sensors.append(
        QvantumFirmwareSensorEntity(firmware_coordinator, "cc_fw_version", device, True)
    )
    sensors.append(
        QvantumFirmwareSensorEntity(
            firmware_coordinator, "inv_fw_version", device, True
        )
    )
    sensors.append(
        QvantumFirmwareLastCheckSensorEntity(
            firmware_coordinator, "firmware_last_check", device, True
        )
    )

    async_add_entities(sensors)

    # Disable entities that should be disabled by default
    entity_registry = hass.data["entity_registry"]
    for sensor in sensors:
        if not sensor._attr_entity_registry_enabled_default:
            entity_entry = entity_registry.async_get(sensor.entity_id)
            if entity_entry and entity_entry.disabled_by is None:
                # Entity is currently enabled, respect user's choice
                continue
            if (
                entity_entry is None
                or entity_entry.disabled_by != RegistryEntryDisabler.USER
            ):
                entity_registry.async_update_entity(
                    sensor.entity_id, disabled_by=RegistryEntryDisabler.INTEGRATION
                )


class QvantumBaseSensorEntity(QvantumEntity, SensorEntity):
    """Sensor for qvantum."""

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        metric_key: str,
        device: DeviceInfo | dict,
        enabled_by_default: bool = True,
    ) -> None:
        super().__init__(coordinator, metric_key, device, enabled_by_default)

        # Set units based on metric patterns
        self._set_units_from_metric(metric_key)

    def _set_units_from_metric(self, metric_key: str) -> None:
        """Set appropriate units based on metric key patterns."""
        if metric_key in ["compressormeasuredspeed", "fanrpm"]:
            self._attr_native_unit_of_measurement = "rpm"
        elif "fan" in metric_key or metric_key.startswith("gp"):
            self._attr_native_unit_of_measurement = "%"
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

class QvantumTemperatureEntity(QvantumBaseSensorEntity):
    """Sensor for temperature measurements."""

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        metric_key: str,
        device: DeviceInfo | dict,
        enabled_by_default: bool = True,
    ) -> None:
        super().__init__(coordinator, metric_key, device, enabled_by_default)
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_state_class = SensorStateClass.MEASUREMENT


class QvantumEnergyEntity(QvantumBaseSensorEntity):
    """Sensor for energy measurements."""

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        metric_key: str,
        device: DeviceInfo | dict,
        enabled_by_default: bool = True,
    ) -> None:
        super().__init__(coordinator, metric_key, device, enabled_by_default)
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


class QvantumPowerEntity(QvantumBaseSensorEntity):
    """Sensor for power measurements."""

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        metric_key: str,
        device: DeviceInfo | dict,
        enabled_by_default: bool = True,
    ) -> None:
        super().__init__(coordinator, metric_key, device, enabled_by_default)
        self._attr_device_class = SensorDeviceClass.POWER
        self._attr_state_class = SensorStateClass.MEASUREMENT
        if metric_key in ["heatingpower", "dhwpower"]:
            self._attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
            self._attr_suggested_display_precision = 2
        else:
            self._attr_native_unit_of_measurement = UnitOfPower.WATT


class QvantumCurrentEntity(QvantumBaseSensorEntity):
    """Sensor for current measurements."""

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        metric_key: str,
        device: DeviceInfo | dict,
        enabled_by_default: bool = True,
    ) -> None:
        super().__init__(coordinator, metric_key, device, enabled_by_default)
        self._attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
        self._attr_device_class = SensorDeviceClass.CURRENT
        self._attr_state_class = SensorStateClass.MEASUREMENT


class QvantumPressureEntity(QvantumBaseSensorEntity):
    """Sensor for pressure measurements."""

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        metric_key: str,
        device: DeviceInfo | dict,
        enabled_by_default: bool = True,
    ) -> None:
        super().__init__(coordinator, metric_key, device, enabled_by_default)
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
        enabled_by_default: bool = True,
    ) -> None:
        super().__init__(coordinator, metric_key, device, enabled_by_default)

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


class QvantumDiagnosticEntity(QvantumBaseSensorEntity):
    """Sensor for diagnostic."""

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        metric_key: str,
        device: DeviceInfo | dict,
        enabled_by_default: bool = True,
    ) -> None:
        super().__init__(coordinator, metric_key, device, enabled_by_default)
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        if "latency" in metric_key:
            self._attr_device_class = SensorDeviceClass.DURATION
            self._attr_native_unit_of_measurement = "ms"


class QvantumTimerEntity(QvantumBaseSensorEntity):
    """Sensor for connectivity."""

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        metric_key: str,
        device: DeviceInfo | dict,
        enabled_by_default: bool = True,
    ) -> None:
        super().__init__(coordinator, metric_key, device, enabled_by_default)
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


class QvantumLatencyEntity(QvantumBaseSensorEntity):
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


def _get_sensor_type(metric: str) -> Type[QvantumBaseSensorEntity]:
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
    else:
        return QvantumBaseSensorEntity


class QvantumFirmwareSensorEntity(QvantumEntity, SensorEntity):
    """Firmware version sensor for Qvantum device."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: QvantumFirmwareUpdateCoordinator,
        firmware_key: str,
        device: DeviceInfo,
        enabled_by_default: bool,
    ) -> None:
        """Initialize the firmware sensor."""
        super().__init__(coordinator, firmware_key, device)
        self.firmware_key = firmware_key
        self._attr_entity_registry_enabled_default = enabled_by_default
        self._attr_translation_key = f"firmware_{firmware_key}"

    @property
    def state(self) -> str | None:
        """Return the firmware version."""
        # First try to get from firmware coordinator data (updated every 60 minutes)
        if self.coordinator.data and "firmware_versions" in self.coordinator.data:
            firmware_versions = self.coordinator.data.get("firmware_versions", {})
            version = firmware_versions.get(self.firmware_key)
            if version is not None:
                return version

        # Fall back to device metadata from main coordinator (available immediately)
        if (
            self.coordinator.main_coordinator
            and self.coordinator.main_coordinator.data
            and "device" in self.coordinator.main_coordinator.data
        ):
            device_data = self.coordinator.main_coordinator.data["device"]
            device_metadata = device_data.get("device_metadata", {})
            version = device_metadata.get(self.firmware_key)
            if version is not None:
                return version

        return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        # Check if firmware coordinator has data
        firmware_available = (
            super().available
            and self.coordinator.data is not None
            and "firmware_versions" in self.coordinator.data
            and self.firmware_key in self.coordinator.data.get("firmware_versions", {})
        )

        # Check if main coordinator has device metadata
        device_available = (
            self.coordinator.main_coordinator
            and self.coordinator.main_coordinator.data
            and "device" in self.coordinator.main_coordinator.data
            and self.firmware_key
            in self.coordinator.main_coordinator.data["device"].get(
                "device_metadata", {}
            )
        )

        return firmware_available or device_available


class QvantumFirmwareLastCheckSensorEntity(QvantumEntity, SensorEntity):
    """Firmware last check timestamp sensor for Qvantum device."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self,
        coordinator: QvantumFirmwareUpdateCoordinator,
        sensor_key: str,
        device: DeviceInfo,
        enabled_by_default: bool,
    ) -> None:
        """Initialize the firmware last check sensor."""
        super().__init__(coordinator, sensor_key, device)
        self._attr_entity_registry_enabled_default = enabled_by_default
        self._attr_translation_key = sensor_key

    @property
    def state(self) -> datetime | None:
        """Return the last firmware check timestamp."""
        if not self.coordinator.data:
            return None
        last_check = self.coordinator.data.get("last_check")
        if last_check:
            return dt_utils.parse_datetime(last_check)
        return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            super().available
            and self.coordinator.data is not None
            and "last_check" in self.coordinator.data
        )
