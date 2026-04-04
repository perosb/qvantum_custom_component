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
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DEFAULT_ENABLED_HTTP_METRICS,
    DEFAULT_ENABLED_MODBUS_METRICS,
    DEFAULT_DISABLED_HTTP_METRICS,
    DEFAULT_DISABLED_MODBUS_METRICS,
    EXCLUDED_METRIC_PATTERNS,
    TEMPERATURE_METRICS,
    ENERGY_METRICS,
    POWER_METRICS,
    CURRENT_METRICS,
    PRESSURE_METRICS,
)
from .entity import QvantumEntity
from . import MyConfigEntry
from .coordinator import QvantumDataUpdateCoordinator
from .maintenance_coordinator import QvantumMaintenanceCoordinator

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

    values = coordinator.data.get("values", {})
    disabled_metrics = (
        DEFAULT_DISABLED_MODBUS_METRICS
        if coordinator.modbus_enabled
        else DEFAULT_DISABLED_HTTP_METRICS
    )

    # Define possible metrics for the current mode
    if coordinator.modbus_enabled:
        possible_metrics = set(
            DEFAULT_ENABLED_MODBUS_METRICS + DEFAULT_DISABLED_MODBUS_METRICS
        )
    else:
        possible_metrics = set(
            DEFAULT_ENABLED_HTTP_METRICS + DEFAULT_DISABLED_HTTP_METRICS
        )

    # Special metrics that have dedicated sensor classes
    special_metrics = {"latency", "hpid"}

    # Create entities using a hybrid approach:
    # - Disabled-by-default metrics: always create so they appear in the entity registry
    #   and users can enable them from the UI. They show as unavailable until fetched.
    # - Enabled-by-default metrics: only create if present in current values to avoid
    #   permanently unavailable entities for mode-specific metrics (e.g., HTTP-only
    #   metrics like fan0_10v and tap_water_cap that don't exist in Modbus mode).
    for metric in sorted(possible_metrics):
        if _should_exclude_metric(metric) or metric in special_metrics:
            continue

        enabled_by_default = metric not in disabled_metrics
        if enabled_by_default and metric not in values:
            _LOGGER.debug(
                "Skipping creation of enabled-by-default sensor for metric '%s' because it's not in current values. It will be created when the metric appears in the data.",
                metric,
            )
            continue

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
    sensors.append(QvantumTimerEntity(coordinator, "tap_stop", device, True))

    # Add maintenance sensors (firmware and access level)
    maintenance_coordinator = config_entry.runtime_data.maintenance_coordinator
    sensors.append(
        QvantumAccessExpireEntity(maintenance_coordinator, "expiresAt", device, True)
    )
    sensors.append(
        QvantumFirmwareSensorEntity(
            maintenance_coordinator, "display_fw_version", device, True
        )
    )
    sensors.append(
        QvantumFirmwareSensorEntity(maintenance_coordinator, "cc_fw_version", device, True)
    )
    sensors.append(
        QvantumFirmwareSensorEntity(
            maintenance_coordinator, "inv_fw_version", device, True
        )
    )
    sensors.append(
        QvantumFirmwareLastCheckSensorEntity(
            maintenance_coordinator, "firmware_last_check", device, True
        )
    )

    async_add_entities(sensors)

    # Disable entities that should be disabled by default
    from .entity import disable_entities_by_default

    disable_entities_by_default(hass, sensors)

    # Clean up disabled entities that are no longer supported in the current mode.
    # Include special sensor keys so they are never removed by cleanup.
    special_sensor_keys = {
        "totalenergy", "latency", "hpid", "tap_stop",
        "expiresAt", "display_fw_version", "cc_fw_version",
        "inv_fw_version", "firmware_last_check",
    }
    from .entity import cleanup_disabled_entities

    cleanup_disabled_entities(hass, coordinator, possible_metrics | special_sensor_keys, "sensor")


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
        if "rpm" in metric_key or metric_key in ["compressormeasuredspeed"]:
            self._attr_native_unit_of_measurement = "rpm"
        elif (
            "fan" in metric_key
            or metric_key.startswith("gp")
            or metric_key.startswith("qn8")
        ):
            self._attr_native_unit_of_measurement = "%"
        elif "bf1_l_min" == metric_key:
            self._attr_native_unit_of_measurement = "l/m"

    @property
    def state(self):
        """Get metric from API data."""
        return self._values.get(self._metric_key)

    @property
    def available(self):
        """Check if data is available."""
        return self._values.get(self._metric_key) is not None

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
        compressor = self._values.get("compressorenergy")
        additional = self._values.get("additionalenergy")
        if compressor is None or additional is None:
            return None
        return compressor + additional

    @property
    def available(self):
        """Check if data is available."""
        return (
            self._values.get("compressorenergy") is not None
            and self._values.get("additionalenergy") is not None
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
    """Sensor for tap water timer."""

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
        epoch = self._values.get(self._metric_key)
        if epoch is None or epoch <= 0:
            return None
        return dt_utils.utc_from_timestamp(epoch)

    @property
    def available(self):
        """Check if data is available."""
        val = self._values.get(self._metric_key)
        return val is not None and val > 0


class QvantumAccessExpireEntity(QvantumEntity, SensorEntity):
    """Sensor for access expiration."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self,
        coordinator: QvantumMaintenanceCoordinator,
        metric_key: str,
        device: DeviceInfo,
        enabled_by_default: bool,
    ) -> None:
        """Initialize the access expire sensor."""
        super().__init__(coordinator, metric_key, device)
        self._attr_entity_registry_enabled_default = enabled_by_default
        self._attr_translation_key = "expires_at"

    @property
    def state(self) -> datetime | None:
        """Get expires_at from access_level data."""
        expire_at_str = self.coordinator.data.get("access_level", {}).get(
            self._metric_key
        )
        if expire_at_str:
            return dt_utils.parse_datetime(expire_at_str)
        return None

    @property
    def available(self) -> bool:
        """Check if data is available."""
        return (
            self.coordinator.data is not None
            and "access_level" in self.coordinator.data
            and self.coordinator.data.get("access_level", {}).get(self._metric_key)
            is not None
        )


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
        coordinator: QvantumMaintenanceCoordinator,
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
        coordinator: QvantumMaintenanceCoordinator,
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
