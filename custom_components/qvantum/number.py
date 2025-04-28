"""Interfaces with the Qvantum Heat Pump api sensors."""

import logging

from homeassistant.components.number import NumberEntity
from homeassistant.const import UnitOfEnergy, UnitOfTemperature, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MyConfigEntry
from .const import SETTING_UPDATE_APPLIED
from .coordinator import QvantumDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up the Number."""
    coordinator: QvantumDataUpdateCoordinator = config_entry.runtime_data.coordinator
    device: DeviceInfo = config_entry.runtime_data.device

    sensors = []
    sensors.append(
        QvantumNumberEntity(coordinator, "tap_water_capacity_target", 1, 5, 1, device)
    )
    # sensors.append(
    #     QvantumNumberEntity(coordinator, "room_comp_factor", 0, 10, 0.5, device)
    # )
    sensors.append(
        QvantumNumberEntity(
            coordinator, "indoor_temperature_offset", -10, 10, 1, device
        )
    )
    sensors.append(
        QvantumNumberEntity(
            coordinator, "tap_water_stop", 60, 90, 1, device
        )
    )

    async_add_entities(sensors)

    _LOGGER.debug(f"Setting up platform NUMBER")


class QvantumNumberEntity(CoordinatorEntity, NumberEntity):
    """Sensor for qvantum."""

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        metric_key: str,
        min: int,
        max: int,
        step: float,
        device: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self._hpid = self.coordinator.data.get("metrics").get("hpid")
        self._attr_translation_key = metric_key
        self._metric_key = metric_key
        self._attr_unique_id = f"qvantum_{metric_key}_{self._hpid}"
        self._attr_device_info = device
        self._attr_has_entity_name = True
        self._attr_native_min_value = min
        self._attr_native_max_value = max
        self._attr_native_step = step

        if self._metric_key == "indoor_temperature_offset":
            self._attr_entity_registry_enabled_default = self.available

    async def async_set_native_value(self, value: float) -> None:
        """Update the current value."""

        response = {}
        match self._metric_key:
            case "tap_water_capacity_target":
                response = await self.coordinator.api.set_tap_water_capacity_target(
                    self._hpid, int(value)
                )
            case "room_comp_factor":
                response = await self.coordinator.api.set_room_comp_factor(
                    self._hpid, int(value)
                )
            case "indoor_temperature_offset":
                response = await self.coordinator.api.set_indoor_temperature_offset(
                    self._hpid, int(value)
                )
            case "tap_water_stop":
                response = await self.coordinator.api.set_tap_water_stop(
                    self._hpid, int(value)
                )

        if response.get("status") == SETTING_UPDATE_APPLIED:
            self.coordinator.data.get("settings")[self._metric_key] = int(value)
            self.coordinator.async_set_updated_data(self.coordinator.data)

    @property
    def state(self):
        """Get metric from API data."""
        return self.coordinator.data.get("settings").get(self._metric_key)

    @property
    def available(self):
        """Check if data is available."""

        avail = (
            self._metric_key in self.coordinator.data.get("settings")
            and self.coordinator.data.get("settings").get(self._metric_key) is not None
        )

        # if using outdoor sensor, allow setting parallel offset
        if (
            self._metric_key == "indoor_temperature_offset"
            and self.coordinator.data.get("settings").get("sensor_mode") != "bt1"
        ):
            return False

        return avail
