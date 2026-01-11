"""Interfaces with the Qvantum Heat Pump api sensors."""

import logging

from homeassistant.components.number import NumberEntity
from homeassistant.const import UnitOfEnergy, UnitOfTemperature, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MyConfigEntry
from .const import TAP_WATER_CAPACITY_MAPPINGS
from .coordinator import QvantumDataUpdateCoordinator, handle_setting_update_response
from .entity import QvantumEntity

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
        QvantumNumberEntity(coordinator, "tap_water_capacity_target", 1, 7, 1, device)
    )
    sensors.append(
        QvantumNumberEntity(coordinator, "room_comp_factor", 0, 10, 0.5, device)
    )
    sensors.append(
        QvantumNumberEntity(
            coordinator, "indoor_temperature_offset", -10, 10, 1, device
        )
    )
    sensors.append(
        QvantumNumberEntity(coordinator, "tap_water_stop", 60, 90, 1, device)
    )
    sensors.append(
        QvantumNumberEntity(coordinator, "tap_water_start", 50, 65, 1, device)
    )

    async_add_entities(sensors)

    _LOGGER.debug("Setting up platform NUMBER")


class QvantumNumberEntity(QvantumEntity, NumberEntity):
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
        super().__init__(coordinator, metric_key, device)
        self._attr_native_min_value = min
        self._attr_native_max_value = max
        self._attr_native_step = step

    async def async_set_native_value(self, value: float) -> None:
        """Update the current value."""

        response = {}
        match self._metric_key:
            case "tap_water_capacity_target":
                response = await self.coordinator.api.set_tap_water_capacity_target(
                    self._hpid, int(value)
                )
            case "room_comp_factor":
                response = await self.coordinator.api.update_setting(
                    self._hpid, "room_comp_factor", int(value)
                )
            case "indoor_temperature_offset":
                response = await self.coordinator.api.set_indoor_temperature_offset(
                    self._hpid, int(value)
                )
            case "tap_water_stop":
                response = await self.coordinator.api.set_tap_water_stop(
                    self._hpid, int(value)
                )
            case "tap_water_start":
                response = await self.coordinator.api.set_tap_water_start(
                    self._hpid, int(value)
                )

        await handle_setting_update_response(
            response,
            self.coordinator,
            "settings",
            self._metric_key,
            int(value),
        )

    @property
    def state(self):
        """Get metric from API data."""
        if self._metric_key == "tap_water_capacity_target":
            # Map (stop, start) pairs to capacity values using TAP_WATER_CAPACITY_MAPPINGS
            stop = self.coordinator.data.get("settings", {}).get("tap_water_stop")
            start = self.coordinator.data.get("settings", {}).get("tap_water_start")
            if (stop, start) in TAP_WATER_CAPACITY_MAPPINGS:
                return TAP_WATER_CAPACITY_MAPPINGS[(stop, start)]

        value = self.coordinator.data.get("settings", {}).get(self._metric_key)
        if value is None:
            value = self.coordinator.data.get("metrics", {}).get(self._metric_key)
        return value

    @property
    def available(self):
        """Check if data is available."""

        avail = (
            self._metric_key in self.coordinator.data.get("settings")
            and self.coordinator.data.get("settings").get(self._metric_key) is not None
        )
        if not avail:
            avail = (
                self._metric_key in self.coordinator.data.get("metrics")
                and self.coordinator.data.get("metrics").get(self._metric_key)
                is not None
            )

        return avail
