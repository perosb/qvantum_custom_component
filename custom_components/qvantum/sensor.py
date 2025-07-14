"""Interfaces with the Qvantum Heat Pump api sensors."""

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfEnergy,
    UnitOfTemperature,
    EntityCategory,
    UnitOfPressure,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_utils
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MyConfigEntry
from .coordinator import QvantumDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up the Sensors."""
    coordinator: QvantumDataUpdateCoordinator = config_entry.runtime_data.coordinator
    device: DeviceInfo = config_entry.runtime_data.device

    sensors = []
    metrics = await coordinator.api.get_available_metrics()
    for metric in metrics:
        if (
            metric.startswith("op_man_")
            or "enable" in metric
            or metric.startswith("picpin_")
            or metric.startswith("qn8")
            or metric.startswith("use_")
        ):
            continue

        if (
            "temp" in metric
            or metric.startswith("bt")
            or metric.startswith("dhw_normal_st")
        ):
            sensors.append(
                QvantumTemperatureEntity(
                    coordinator,
                    metric,
                    device,
                )
            )
        elif "energy" in metric:
            sensors.append(
                QvantumEnergyEntity(
                    coordinator,
                    metric,
                    device,
                )
            )
        elif "pressure" in metric:
            sensors.append(
                QvantumPressureEntity(
                    coordinator,
                    metric,
                    device,
                )
            )
        else:
            sensors.append(
                QvantumBaseEntity(
                    coordinator,
                    metric,
                    device,
                )
            )

    sensors.append(QvantumTotalEnergyEntity(coordinator, "totalenergy", device))
    sensors.append(QvantumDiagnosticEntity(coordinator, "latency", device))
    sensors.append(QvantumDiagnosticEntity(coordinator, "hpid", device))

    # sensors.append(
    #     QvantumTimerEntity(
    #         coordinator, "extra_tap_water_stop", "extra tap water stop", device
    #     )
    # )
    # sensors.append(
    #     QvantumTimerEntity(
    #         coordinator, "ventilation_boost_stop", "ventilation boost stop", device
    #     )
    # )
    # sensors.append(
    #     QvantumConnectivityEntity(
    #         coordinator, "disconnect_reason", "disconnect reason", device
    #     )
    # )

    async_add_entities(sensors)


class QvantumBaseEntity(CoordinatorEntity, SensorEntity):
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
        self._attr_has_entity_name = True

        if "fan" in metric_key or metric_key.startswith("gp"):
            self._attr_native_unit_of_measurement = "%"

        if "compressormeasuredspeed" in metric_key:
            self._attr_native_unit_of_measurement = "rpm"

        if "bf1_l_min" == metric_key:
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


class QvantumTemperatureEntity(QvantumBaseEntity):
    """Sensor for temperature measurements."""

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        metric_key: str,
        device: DeviceInfo,
    ) -> None:
        super().__init__(coordinator, metric_key, device)
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_state_class = SensorStateClass.MEASUREMENT


class QvantumEnergyEntity(QvantumBaseEntity):
    """Sensor for energy measurements."""

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        metric_key: str,
        device: DeviceInfo,
    ) -> None:
        super().__init__(coordinator, metric_key, device)
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


class QvantumPressureEntity(QvantumBaseEntity):
    """Sensor for pressure measurements."""

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        metric_key: str,
        device: DeviceInfo,
    ) -> None:
        super().__init__(coordinator, metric_key, device)
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
        device: DeviceInfo,
    ) -> None:
        super().__init__(coordinator, metric_key, device)

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
        device: DeviceInfo,
    ) -> None:
        super().__init__(coordinator, metric_key, device)
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
        device: DeviceInfo,
    ) -> None:
        super().__init__(coordinator, metric_key, device)
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
        device: DeviceInfo,
    ) -> None:
        super().__init__(coordinator, metric_key, device)
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
