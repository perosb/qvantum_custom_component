"""Interfaces with the Qvantum Heat Pump api sensors."""

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity_registry import RegistryEntryDisabler
from homeassistant.const import EntityCategory


from . import MyConfigEntry
from .const import DOMAIN, DEFAULT_ENABLED_METRICS, DEFAULT_DISABLED_METRICS
from .coordinator import QvantumDataUpdateCoordinator
from .entity import QvantumEntity

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

    sensor_names = [
        "op_man_addition",
        "op_man_cooling",
        "op_man_dhw",
        "enable_sc_dhw",
        "enable_sc_sh",
        "cooling_enabled",
        "picpin_relay_heat_l1",
        "picpin_relay_heat_l2",
        "picpin_relay_heat_l3",
        "picpin_relay_qm10",
        "qn8position",
    ]

    for sensor_name in sensor_names:
        enabled_by_default = sensor_name not in DEFAULT_DISABLED_METRICS
        sensors.append(
            QvantumBaseBinaryEntity(
                coordinator,
                sensor_name,
                sensor_name,
                device,
                enabled_by_default,
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


class QvantumBaseBinaryEntity(QvantumEntity, BinarySensorEntity):
    """Sensor for qvantum."""

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        metric_key: str,
        name: str,
        device: DeviceInfo,
        enabled_by_default: bool = True,
    ) -> None:
        super().__init__(coordinator, metric_key, device, enabled_by_default)
        self._data_bearer = "metrics"

    @property
    def is_on(self):
        """Get metric from API data."""
        return self.coordinator.data.get(self._data_bearer).get(self._metric_key)

    @property
    def available(self):
        """Check if data is available."""
        data = self.coordinator.data.get(self._data_bearer, {})
        # if self._metric_key.startswith("op_man_"):
        #     if self.coordinator.data.get(self._data_bearer).get("op_mode") != 1:
        #         return False

        return data.get(self._metric_key) is not None


class QvantumConnectedEntity(QvantumBaseBinaryEntity):
    """Sensor for qvantum."""

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        metric_key: str,
        name: str,
        device: DeviceInfo,
        enabled_by_default: bool = True,
    ) -> None:
        super().__init__(coordinator, metric_key, name, device, enabled_by_default)

        self._attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._data_bearer = "connectivity"
