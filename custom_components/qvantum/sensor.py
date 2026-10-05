"""Interfaces with the Qvantum Heat Pump api sensors."""

import logging
from datetime import datetime, timezone
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
    UnitOfTime,
    EntityCategory,
    UnitOfPressure,
    UnitOfElectricCurrent,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_utils
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client.modbus.maps import HEATING_CURVE_OUTDOOR_TEMPS
from .const import (
    DEFAULT_ENABLED_HTTP_METRICS,
    DEFAULT_ENABLED_MODBUS_METRICS,
    DEFAULT_DISABLED_HTTP_METRICS,
    DEFAULT_DISABLED_MODBUS_METRICS,
    EXCLUDED_METRIC_NAMES,
    EXCLUDED_METRIC_PATTERNS,
    TEMPERATURE_METRICS,
    ENERGY_METRICS,
    POWER_METRICS,
    CURRENT_METRICS,
    PRESSURE_METRICS,
)
from .entity import QvantumEntity, finalize_platform_setup, resolve_device_id
from . import MyConfigEntry
from .coordinator import QvantumDataUpdateCoordinator
from .curve_coordinator import QvantumCurveCoordinator
from .maintenance_coordinator import QvantumMaintenanceCoordinator

_LOGGER = logging.getLogger(__name__)

# Countdown / remaining-life and lifetime counters belong under Diagnostics.
_ALARM_CODE_SENSORS = frozenset(
    {f"alarm_{index}_code" for index in range(1, 6)}
)
_DIAGNOSTIC_SENSORS = frozenset(
    {
        "ventilation_filter_time_left",
        "compressor_blocked_sec",
        "compressor_run_time",
        "compressor_starts",
        "ventilation_fan_run_time",
        "active_alarms",
        *_ALARM_CODE_SENSORS,
    }
)
_DURATION_HOURS_SENSORS = frozenset(
    {
        "ventilation_filter_time_left",
        "compressor_run_time",
        "ventilation_fan_run_time",
    }
)
_TOTAL_INCREASING_SENSORS = frozenset(
    {
        "compressor_run_time",
        "compressor_starts",
        "ventilation_fan_run_time",
    }
)

# Custom-curve sensor keys (translation/unique-id/slug) mapped to the pump's
# canonical curve point keys used in coordinator snapshots. Stable English
# slugs keep dashboards and automations working across locales.
_CURVE_POINT_METRICS: dict[str, str] = {
    (
        f"adaptive_curve_minus_{abs(outdoor)}" if outdoor < 0 else f"adaptive_curve_{outdoor}"
    ): metric_key
    for metric_key, outdoor in HEATING_CURVE_OUTDOOR_TEMPS.items()
}
_CURVE_SENSOR_KEYS = frozenset(
    {
        *_CURVE_POINT_METRICS,
        "adaptive_curve_adjustment",
        "adaptive_curve_deviation",
        "adaptive_curve_solar_model",
    }
)

# Numeric prefix so the seven points sort 1→7 (+30 … −30) like the pump's own
# curve numbers in any entity list, independent of locale.
_CURVE_POINT_SLUGS: dict[str, str] = {
    sensor_key: f"adaptive_curve_{index:02d}_{sensor_key.removeprefix('adaptive_curve_')}"
    for index, sensor_key in enumerate(_CURVE_POINT_METRICS, start=1)
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up the Sensors."""
    coordinator: QvantumDataUpdateCoordinator = config_entry.runtime_data.coordinator
    device: DeviceInfo | dict = config_entry.runtime_data.device

    _LOGGER.debug("Setting up platform SENSOR")

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

    # Special metrics that have dedicated sensor classes (created explicitly below)
    special_metrics = {"latency", "hpid", "tap_stop"}

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
    if coordinator.modbus_enabled:
        # Local Modbus: display firmware from input registers 191-193 and the
        # custom heating-curve shadow sensors (no cloud equivalent).
        sensors.append(
            QvantumDisplayFirmwareEntity(
                coordinator, "display_fw_version", device, True
            )
        )
        curve_coordinator = getattr(
            config_entry.runtime_data, "curve_coordinator", None
        )
        if isinstance(curve_coordinator, QvantumCurveCoordinator):
            for curve_key in _CURVE_POINT_METRICS:
                sensors.append(
                    QvantumCurvePointSensor(curve_coordinator, curve_key, device)
                )
            sensors.append(
                QvantumCurveAdjustmentSensor(
                    curve_coordinator, "adaptive_curve_adjustment", device
                )
            )
            sensors.append(
                QvantumCurveDeviationSensor(
                    curve_coordinator, "adaptive_curve_deviation", device
                )
            )
            sensors.append(
                QvantumCurveSolarModelSensor(
                    curve_coordinator, "adaptive_curve_solar_model", device
                )
            )
    else:
        # Cloud-only: firmware and access level from the HTTP API
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
            QvantumFirmwareSensorEntity(
                maintenance_coordinator, "cc_fw_version", device, True
            )
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

    # Register entities, disable them by default where needed, and prune
    # registry entries for metrics no longer supported in the current mode.
    special_sensor_keys = {"totalenergy", "latency", "hpid", "tap_stop"}
    if coordinator.modbus_enabled:
        special_sensor_keys.update(
            {
                "display_fw_version",
                *_CURVE_SENSOR_KEYS,
            }
        )
    else:
        special_sensor_keys.update(
            {
                "expiresAt",
                "display_fw_version",
                "cc_fw_version",
                "inv_fw_version",
                "firmware_last_check",
            }
        )

    finalize_platform_setup(
        hass,
        coordinator,
        async_add_entities,
        sensors,
        possible_metrics | special_sensor_keys,
        "sensor",
        disable_by_default=True,
    )


class QvantumBaseSensorEntity(QvantumEntity, SensorEntity):
    """Base sensor entity for Qvantum metrics."""

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
        if metric_key in _DIAGNOSTIC_SENSORS:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    def _set_units_from_metric(self, metric_key: str) -> None:
        """Set appropriate units based on metric key patterns."""
        if "rpm" in metric_key or metric_key in ["compressormeasuredspeed"]:
            self._attr_native_unit_of_measurement = "rpm"
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif metric_key in _DURATION_HOURS_SENSORS:
            # Must precede the generic "fan" match so ventilation_fan_run_time
            # is hours, not percent.
            self._attr_native_unit_of_measurement = UnitOfTime.HOURS
            self._attr_device_class = SensorDeviceClass.DURATION
            self._attr_suggested_display_precision = 0
        elif (
            "fan" in metric_key
            or metric_key.startswith("gp")
            or metric_key.startswith("qn8")
        ):
            self._attr_native_unit_of_measurement = "%"
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif "timeleft" in metric_key or metric_key == "compressor_blocked_sec":
            self._attr_native_unit_of_measurement = UnitOfTime.SECONDS
            self._attr_device_class = SensorDeviceClass.DURATION
            self._attr_suggested_display_precision = 0
        elif metric_key == "compressor_starts":
            self._attr_suggested_display_precision = 0
        elif metric_key == "active_alarms":
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_suggested_display_precision = 0
        elif metric_key in _ALARM_CODE_SENSORS:
            self._attr_suggested_display_precision = 0
        elif "tap_water_cap" == metric_key:
            self._attr_suggested_display_precision = 1
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif "tap_water_minutes" == metric_key:
            self._attr_native_unit_of_measurement = UnitOfTime.MINUTES
            self._attr_device_class = SensorDeviceClass.DURATION
            self._attr_suggested_display_precision = 0
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif "bf1_l_min" == metric_key:
            self._attr_native_unit_of_measurement = "l/m"
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif "degree_minute" == metric_key:
            self._attr_native_unit_of_measurement = "°min"
            self._attr_state_class = SensorStateClass.MEASUREMENT
        if metric_key in _TOTAL_INCREASING_SENSORS:
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def native_value(self):
        """Get metric from API data."""
        return self._values.get(self._metric_key)

    @property
    def available(self):
        """Check if data is available."""
        return (
            super().available
            and self._values.get(self._metric_key) is not None
        )

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
    """Cumulative energy (kWh) for Energy Dashboard one-click (ENERGY + TOTAL_INCREASING).

    Assigned when the metric name contains ENERGY_METRICS ("energy"): compressorenergy,
    heatingenergy, dhwenergy, additionalenergy, coolingenergy (if present).
    """

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
    """Instantaneous power in WATTS (POWER + MEASUREMENT); not kW.

    POWER_METRICS: powertotal, heatingpower, dhwpower (derived W in calculations.py).
    """

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
    """compressorenergy + additionalenergy; inherits Energy Dashboard ENERGY + kWh classes."""

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        metric_key: str,
        device: DeviceInfo | dict,
        enabled_by_default: bool = True,
    ) -> None:
        super().__init__(coordinator, metric_key, device, enabled_by_default)

    def _is_data_valid(
        self, compressor: float | int | None, additional: float | int | None
    ) -> bool:
        """Validate total-energy source values.

        Values are considered invalid when either source is missing, or when both
        are exactly zero (treated as a transient communication anomaly).
        """
        if compressor is None or additional is None:
            return False
        if compressor == 0 and additional == 0:
            return False
        return True

    @property
    def native_value(self):
        """Get metric from API data."""
        compressor = self._values.get("compressorenergy")
        additional = self._values.get("additionalenergy")
        if not self._is_data_valid(compressor, additional):
            return None
        return compressor + additional

    @property
    def available(self):
        """Check if data is available.

        Uses the coordinator helper directly rather than
        ``super().available`` because the synthetic ``totalenergy`` key is not
        present in the values payload that the base sensor checks.
        """
        compressor = self._values.get("compressorenergy")
        additional = self._values.get("additionalenergy")
        return self._coordinator_available and self._is_data_valid(
            compressor, additional
        )


class QvantumCurveSensorEntity(CoordinatorEntity, SensorEntity):
    """Base for custom heating-curve sensors (Modbus-only coordinator)."""

    _attr_has_entity_name = True

    def __init__(
        self,
        curve_coordinator: QvantumCurveCoordinator,
        metric_key: str,
        device: DeviceInfo | dict,
        enabled_by_default: bool = True,
    ) -> None:
        super().__init__(curve_coordinator)
        self._metric_key = metric_key
        self._attr_translation_key = metric_key
        self._attr_unique_id = f"qvantum_{metric_key}_{resolve_device_id(device)}"
        self._attr_device_info = device
        self._attr_entity_registry_enabled_default = enabled_by_default

    @property
    def suggested_object_id(self) -> str | None:
        """Stable English slug; translated names must not move entity IDs."""
        return self._metric_key


class QvantumCurvePointSensor(QvantumCurveSensorEntity):
    """One computed supply point of the shadow curve."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:chart-bell-curve"

    def __init__(
        self,
        curve_coordinator: QvantumCurveCoordinator,
        metric_key: str,
        device: DeviceInfo | dict,
        enabled_by_default: bool = True,
    ) -> None:
        super().__init__(curve_coordinator, metric_key, device, enabled_by_default)
        self._curve_key = _CURVE_POINT_METRICS[metric_key]

    @property
    def suggested_object_id(self) -> str | None:
        """Indexed slug so the seven points sort like the pump's 1–7."""
        return _CURVE_POINT_SLUGS[self._metric_key]

    @property
    def native_value(self):
        """Return the computed supply temperature for this outdoor point."""
        snapshot = self.coordinator.data
        if snapshot is None:
            return None
        return snapshot.points.get(self._curve_key)

    @property
    def available(self) -> bool:
        """Check the snapshot has this point."""
        snapshot = self.coordinator.data
        return (
            super().available
            and snapshot is not None
            and self._curve_key in snapshot.points
        )

    @property
    def extra_state_attributes(self):
        """Return the frozen baseline and the shared adjustment."""
        snapshot = self.coordinator.data
        if snapshot is None:
            return None
        return {
            "baseline": snapshot.baseline.get(self._curve_key),
            "adjustment": snapshot.adjustment_c,
            "trim": snapshot.trims.get(self._curve_key, 0.0),
        }


class QvantumCurveAdjustmentSensor(QvantumCurveSensorEntity):
    """The shared adjustment applied to every curve point."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:tune-variant"

    @property
    def native_value(self):
        """Return the adjustment in °C."""
        snapshot = self.coordinator.data
        return None if snapshot is None else snapshot.adjustment_c

    @property
    def available(self) -> bool:
        """Only meaningful once the curve has been computed."""
        snapshot = self.coordinator.data
        return super().available and snapshot is not None and bool(snapshot.points)

    @property
    def extra_state_attributes(self):
        """Return the term breakdown."""
        snapshot = self.coordinator.data
        if snapshot is None:
            return None
        return {
            "outdoor_c": snapshot.outdoor_c,
            "night_day_c": snapshot.night_day_c,
            "solar_c": snapshot.solar_c,
            "load_c": snapshot.load_c,
            "capped_by_indoor": snapshot.capped_by_indoor,
            "trims": dict(snapshot.trims),
            "clamped": snapshot.clamped,
        }


class QvantumCurveDeviationSensor(QvantumCurveSensorEntity):
    """Computed supply minus the pump's ``cal_heat_temp`` (shadow comparison)."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:scale-balance"

    @property
    def native_value(self):
        """Return computed minus Auto supply in °C."""
        snapshot = self.coordinator.data
        return None if snapshot is None else snapshot.deviation_c

    @property
    def available(self) -> bool:
        """Check a deviation has been derived."""
        return super().available and self.native_value is not None

    @property
    def extra_state_attributes(self):
        """Return shadow state and activation-readiness details."""
        snapshot = self.coordinator.data
        if snapshot is None:
            return None
        return {
            "shadow": snapshot.shadow,
            "ready": snapshot.ready,
            "blocker": snapshot.blocker,
            "median_abs_c": snapshot.median_abs_c,
            "max_abs_c": snapshot.max_abs_c,
            "window_hours": snapshot.window_hours,
            "baseline_auto": snapshot.baseline_auto,
            "baseline_learned_hours": snapshot.baseline_learned_hours,
            "baseline_outdoor_min_c": snapshot.baseline_outdoor_min_c,
            "baseline_outdoor_max_c": snapshot.baseline_outdoor_max_c,
        }


class QvantumCurveSolarModelSensor(QvantumCurveSensorEntity):
    """Diagnostic solar-model confidence and coefficients."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:weather-sunny"

    @property
    def native_value(self):
        """Return the model trust in percent."""
        snapshot = self.coordinator.data
        if snapshot is None or snapshot.model is None:
            return None
        return round(snapshot.model.trust * 100)

    @property
    def available(self) -> bool:
        """Check a solar model has been identified."""
        return super().available and self.native_value is not None

    @property
    def extra_state_attributes(self):
        """Return the identified coefficients and diagnostics."""
        snapshot = self.coordinator.data
        if snapshot is None or snapshot.model is None:
            return None
        model = snapshot.model
        return {
            "a_w_per_k": round(model.a_w_per_k, 2),
            "b_m2": round(model.b_m2, 4),
            "c_w": round(model.c_w, 1),
            "b_std_err": (
                None if model.b_std_err is None else round(model.b_std_err, 5)
            ),
            "r2_opaque": round(model.r2_opaque, 3),
            "r2_solar": round(model.r2_solar, 3),
            "n_opaque": model.n_opaque,
            "n_solar": model.n_solar,
            "valid": model.valid,
            "calibrated_at": snapshot.calibrated_at,
        }


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
        self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        """Get metric from API data."""
        epoch = self._values.get(self._metric_key)
        if epoch is None or epoch <= 0:
            return None
        return datetime.fromtimestamp(epoch, tz=timezone.utc)

    @property
    def available(self):
        """Check if data is available."""
        val = self._values.get(self._metric_key)
        return super().available and val is not None and val > 0


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
    def native_value(self) -> datetime | None:
        """Get expires_at from access_level data."""
        access_level = (self.coordinator.data or {}).get("access_level") or {}
        expire_at_str = access_level.get(self._metric_key)
        if expire_at_str:
            return dt_utils.parse_datetime(expire_at_str)
        return None

    @property
    def available(self) -> bool:
        """Check if data is available."""
        access_level = (self.coordinator.data or {}).get("access_level") or {}
        return (
            super().available and access_level.get(self._metric_key) is not None
        )


def _should_exclude_metric(metric: str) -> bool:
    """Check if a metric should be excluded from sensor creation."""
    if metric in EXCLUDED_METRIC_NAMES:
        return True
    return any(pattern in metric for pattern in EXCLUDED_METRIC_PATTERNS)


def _get_sensor_type(metric: str) -> Type[QvantumBaseSensorEntity]:
    """Map a metric name to a sensor class (ENERGY_METRICS before POWER_METRICS)."""
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
    def native_value(self) -> str | None:
        """Return the firmware version."""
        # First try to get from firmware coordinator data (updated every 2 hours)
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
            and "firmware_versions" in (self.coordinator.data or {})
            and self.firmware_key
            in (self.coordinator.data or {}).get("firmware_versions", {})
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


class QvantumDisplayFirmwareEntity(QvantumEntity, SensorEntity):
    """Modbus-only display firmware version (input registers 191-193)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        metric_key: str,
        device: DeviceInfo | dict,
        enabled_by_default: bool = True,
    ) -> None:
        """Initialize the display firmware sensor."""
        super().__init__(coordinator, metric_key, device, enabled_by_default)
        self._attr_translation_key = "firmware_display_fw_version"

    @property
    def native_value(self) -> str | None:
        """Return display firmware from device data, metadata as fallback."""
        device_data = (self.coordinator.data or {}).get("device") or {}
        version = device_data.get("sw_version")
        if version:
            return version
        return (device_data.get("device_metadata") or {}).get("display_fw_version")

    @property
    def available(self) -> bool:
        """Return True when a display firmware version is known."""
        return super().available and self.native_value is not None


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
    def native_value(self) -> datetime | None:
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
