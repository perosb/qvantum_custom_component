"""Interfaces with the Qvantum Heat Pump api sensors."""

import logging

from homeassistant.components.number import NumberEntity
from homeassistant.const import UnitOfEnergy, UnitOfTemperature, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MyConfigEntry
from .const import (
    CONF_MODBUS_WRITE,
    TAP_WATER_CAPACITY_MAPPINGS,
)
from .coordinator import QvantumDataUpdateCoordinator, handle_setting_update_response
from .entity import QvantumEntity

_LOGGER = logging.getLogger(__name__)

# Metrics that require writing via Modbus holding registers.
# Entities for these metrics show as unavailable when "Enable writing via Modbus" is off.
MODBUS_WRITE_METRICS = {"dhw_stop_extra"}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up the Number."""
    coordinator: QvantumDataUpdateCoordinator = config_entry.runtime_data.coordinator
    device: DeviceInfo = config_entry.runtime_data.device

    # Configuration for number entities: metric_key -> (min, max, step)
    NUMBER_CONFIG = {
        "tap_water_capacity_target": (1, 7, 1),
        "room_comp_factor": (0, 10, 0.5),
        "indoor_temperature_offset": (-10, 10, 1),
        "tap_water_stop": (60, 80, 1),
        "tap_water_start": (50, 65, 1),
        "dhw_stop_extra": (60, 85, 5),
        "fan_normal": (0, 100, 5),
        "fan_speed_2": (0, 100, 5),
    }

    # Only create number entities for metrics present in the coordinator's current data.
    # This ensures HTTP-only number metrics (e.g., tap_water_capacity_target) are not
    # created as permanently unavailable entities when in Modbus mode.
    sensors = []
    for metric, (min_val, max_val, step_val) in NUMBER_CONFIG.items():
        if metric in coordinator.data.get("values", {}):
            sensors.append(
                QvantumNumberEntity(
                    coordinator, metric, min_val, max_val, step_val, device
                )
            )

    async_add_entities(sensors)

    _LOGGER.debug("Setting up platform NUMBER")


class QvantumNumberEntity(QvantumEntity, NumberEntity):
    """Sensor for qvantum."""

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        metric_key: str,
        min_value: int,
        max_value: int,
        step: float,
        device: DeviceInfo,
    ) -> None:
        super().__init__(coordinator, metric_key, device)
        # Prefix unique_id with 'number_' to avoid conflicts with sensor entities
        # that share the same metric key (e.g. tap_water_start, tap_water_stop).
        self._attr_unique_id = f"qvantum_number_{metric_key}_{self._hpid}"
        self._attr_native_min_value = min_value
        self._attr_native_max_value = max_value
        self._attr_native_step = step

    async def async_set_native_value(self, value: float) -> None:
        """Update the current value."""

        response = {}
        match self._metric_key:
            case "tap_water_capacity_target":
                response = await self.coordinator.api.set_tap_water_capacity_target(
                    self._hpid, int(value)
                )
            case "indoor_temperature_offset":
                response = await self.coordinator.api.set_indoor_temperature_offset(
                    self._hpid, int(value)
                )
            case "tap_water_stop":
                response = await self.coordinator.api.set_tap_water(
                    self._hpid, stop=int(value)
                )
            case "tap_water_start":
                response = await self.coordinator.api.set_tap_water(
                    self._hpid, start=int(value)
                )
            case "room_comp_factor" | "fan_normal" | "fan_speed_2":
                response = await self.coordinator.api.update_setting(
                    self._hpid, self._metric_key, int(value)
                )

            case "dhw_stop_extra":
                # dhw_stop_extra has no update_setting HTTP endpoint; write via Modbus holding register
                response = await self.coordinator.api.write_holding_register_for_metric(
                    self._hpid, self._metric_key, int(value)
                )

        await handle_setting_update_response(
            response,
            self.coordinator,
            "values",
            self._metric_key,
            int(value),
        )

    @property
    def state(self):
        """Get metric from API data."""
        if self._metric_key == "tap_water_capacity_target":
            # Map (start, stop) pairs to capacity values using TAP_WATER_CAPACITY_MAPPINGS
            stop = self._values.get("tap_water_stop")
            start = self._values.get("tap_water_start")
            if (start, stop) in TAP_WATER_CAPACITY_MAPPINGS:
                return TAP_WATER_CAPACITY_MAPPINGS[(start, stop)]

        return self._values.get(self._metric_key)

    @property
    def available(self):
        """Check if data is available."""
        config_entry = self.coordinator.config_entry
        modbus_write_enabled = config_entry.options.get(
            CONF_MODBUS_WRITE,
            config_entry.data.get(CONF_MODBUS_WRITE, False),
        )
        return (
            (self._metric_key not in MODBUS_WRITE_METRICS or modbus_write_enabled)
            and self._values.get(self._metric_key) is not None
            and self._has_write_access
        )
