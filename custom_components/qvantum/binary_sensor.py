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

from .const import (
    BINARY_SENSOR_NAMES,
    DEFAULT_DISABLED_HTTP_METRICS,
    DEFAULT_DISABLED_MODBUS_METRICS,
    MODBUS_ONLY_BINARY_SENSORS,
)
from . import MyConfigEntry
from .coordinator import QvantumDataUpdateCoordinator
from .entity import QvantumEntity

_LOGGER = logging.getLogger(__name__)

_CONNECTIVITY_BINARY_SENSORS = frozenset({"wifi_connected", "cloud_connected"})


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

    async_add_entities(sensors)

    # Disable entities that should be disabled by default
    from .entity import disable_entities_by_default

    disable_entities_by_default(hass, sensors)

    # Clean up disabled entities that are no longer supported in the current mode
    from .entity import cleanup_disabled_entities

    cleanup_disabled_entities(hass, coordinator, set(names), "binary_sensor")


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
        return self._values.get(self._metric_key) is not None

