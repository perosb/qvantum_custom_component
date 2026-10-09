"""Interfaces with the Qvantum Heat Pump api sensors."""

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    BINARY_SENSOR_NAMES,
    DEFAULT_DISABLED_HTTP_METRICS,
    DEFAULT_DISABLED_MODBUS_METRICS,
    MODBUS_ONLY_BINARY_SENSORS,
)
from . import MyConfigEntry
from .coordinator import QvantumDataUpdateCoordinator
from .efficiency_coordinator import QvantumEfficiencyCoordinator
from .entity import QvantumEntity, finalize_platform_setup, resolve_device_id

_LOGGER = logging.getLogger(__name__)

# Derived from recorder statistics rather than a pump metric.
_EFFICIENCY_BINARY_SENSORS = frozenset(
    {"weather_normalized_consumption_rising", "heat_meter_deviation_warning"}
)
_EFFICIENCY_BINARY_FIELDS = {
    "weather_normalized_consumption_rising": "normalized_rising",
    "heat_meter_deviation_warning": "dhw_heat_meter_warning",
}

_CONNECTIVITY_BINARY_SENSORS = frozenset({"wifi_connected", "cloud_connected"})
_PROBLEM_BINARY_SENSORS = frozenset({"alarm_active"})

# Status / release / protection flags — Diagnostics section, not main UI.
_DIAGNOSTIC_BINARY_SENSORS = frozenset(
    {
        "heatingreleased",
        "coolingreleased",
        "compressorreleased",
        "additionreleased",
        "freeze_protection_active",
        "compressor_blocked",
        # All picpin relay status bits (HTTP + Modbus-only pump)
        "picpin_relay_heat_l1",
        "picpin_relay_heat_l2",
        "picpin_relay_heat_l3",
        "picpin_relay_gp10",
        "picpin_relay_qm10",
        "picpin_relay_qn8_1",
        "picpin_relay_qn8_2",
        "picpin_relay_gp3",
        "picpin_relay_ha12",
        "picpin_relay_pump",
    }
)


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

    values = coordinator.data.get("values", {})
    disabled_metrics = (
        DEFAULT_DISABLED_MODBUS_METRICS
        if coordinator.modbus_enabled
        else DEFAULT_DISABLED_HTTP_METRICS
    )

    names = list(BINARY_SENSOR_NAMES)
    if coordinator.modbus_enabled:
        names.extend(MODBUS_ONLY_BINARY_SENSORS)

    for metric in sorted(names):
        enabled_by_default = metric not in disabled_metrics
        if enabled_by_default and metric not in values:
            continue

        sensors.append(
            QvantumBaseBinaryEntity(
                coordinator,
                metric,
                device,
                enabled_by_default=enabled_by_default,
            )
        )

    possible_metrics = set(names)
    efficiency_coordinator = getattr(
        config_entry.runtime_data, "efficiency_coordinator", None
    )
    if isinstance(efficiency_coordinator, QvantumEfficiencyCoordinator):
        possible_metrics.update(_EFFICIENCY_BINARY_SENSORS)
        for binary_key in sorted(_EFFICIENCY_BINARY_SENSORS):
            sensors.append(
                QvantumEfficiencyBinaryEntity(
                    efficiency_coordinator, binary_key, device, False
                )
            )

    finalize_platform_setup(
        hass,
        coordinator,
        async_add_entities,
        sensors,
        possible_metrics,
        "binary_sensor",
        disable_by_default=True,
    )


class QvantumEfficiencyBinaryEntity(CoordinatorEntity, BinarySensorEntity):
    """Diagnostic flag derived from rolling efficiency analytics."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        efficiency_coordinator: QvantumEfficiencyCoordinator,
        metric_key: str,
        device: DeviceInfo | dict,
        enabled_by_default: bool = False,
    ) -> None:
        super().__init__(efficiency_coordinator)
        self._metric_key = metric_key
        self._attr_translation_key = metric_key
        self._attr_unique_id = f"qvantum_{metric_key}_{resolve_device_id(device)}"
        self._attr_device_info = device
        self._attr_entity_registry_enabled_default = enabled_by_default

    @property
    def suggested_object_id(self) -> str | None:
        """Stable English slug; translated names must not move entity IDs."""
        return self._metric_key

    @property
    def is_on(self) -> bool | None:
        """Return the verdict for this key, or None without enough history."""
        snapshot = self.coordinator.data
        if snapshot is None:
            return None
        return getattr(snapshot, _EFFICIENCY_BINARY_FIELDS[self._metric_key], None)

    @property
    def available(self) -> bool:
        """Only meaningful once both trend windows have enough coverage."""
        return super().available and self.is_on is not None


class QvantumBaseBinaryEntity(QvantumEntity, BinarySensorEntity):
    """Base binary sensor entity for Qvantum devices."""

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        metric_key: str,
        device: DeviceInfo,
        enabled_by_default: bool = True,
    ) -> None:
        super().__init__(coordinator, metric_key, device, enabled_by_default)
        if metric_key in _CONNECTIVITY_BINARY_SENSORS:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
            self._attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
        elif metric_key in _PROBLEM_BINARY_SENSORS:
            self._attr_device_class = BinarySensorDeviceClass.PROBLEM
        elif metric_key in _DIAGNOSTIC_BINARY_SENSORS:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self):
        """Get metric from API data."""
        if not self._values:
            return None
        return self._values.get(self._metric_key)

    @property
    def available(self):
        """Check if data is available."""
        if not self._values:
            return False
        return (
            super().available
            and self._values.get(self._metric_key) is not None
        )

