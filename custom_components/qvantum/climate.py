"""Interfaces with the Qvantum Heat Pump api sensors."""

import logging

from homeassistant.components.climate import ClimateEntity
from homeassistant.const import UnitOfEnergy, UnitOfTemperature, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.components.climate.const import HVACMode
from homeassistant.components.climate.const import HVACAction
from homeassistant.components.climate.const import ClimateEntityFeature

from . import MyConfigEntry
from .coordinator import QvantumDataUpdateCoordinator, handle_setting_update_response

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
    sensors.append(QvantumIndoorClimateEntity(coordinator, device))

    async_add_entities(sensors)

    _LOGGER.debug(f"Setting up platform CLIMATE")


class QvantumIndoorClimateEntity(CoordinatorEntity, ClimateEntity):
    """Sensor for qvantum."""

    def __init__(
        self, coordinator: QvantumDataUpdateCoordinator, device: DeviceInfo
    ) -> None:
        super().__init__(coordinator)
        self._hpid = self.coordinator.data.get("metrics").get("hpid")
        self._attr_unique_id = f"qvantum_indoor_climate_{self._hpid}"
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_device_info = device
        self._attr_translation_key = "indoor_climate"
        self._attr_has_entity_name = True

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        temperature = kwargs["temperature"]
        response = await self.coordinator.api.set_indoor_temperature_target(
            self._hpid, temperature
        )
        await handle_setting_update_response(
            response,
            self.coordinator,
            "settings",
            "indoor_temperature_target",
            temperature,
        )

    async def async_set_hvac_mode(self, hvac_mode):
        """Set new target hvac mode."""
        _LOGGER.debug(hvac_mode)

    @property
    def supported_features(self):
        """Return the list of supported features."""
        if self.coordinator.data.get("settings").get("sensor_mode") == "bt2":
            return ClimateEntityFeature.TARGET_TEMPERATURE

        return {}

    @property
    def available(self):
        """Check if data is available."""
        return (
            "bt2" in self.coordinator.data.get("metrics")
            and self.coordinator.data.get("metrics").get("bt2")
            is not None
        )

    @property
    def current_temperature(self):
        """Return the temperature we try to reach."""
        return self.coordinator.data.get("metrics").get("bt2")

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self.coordinator.data.get("settings").get("indoor_temperature_target")

    @property
    def hvac_action(self):
        """Current HVAC action"""
        status = self.coordinator.data.get("metrics").get("hp_status")
        if status == 3:
            return HVACAction.HEATING
        if status == 0:
            return HVACAction.IDLE
        if status == 1:
            return HVACAction.DEFROSTING

        return HVACAction.IDLE

    @property
    def hvac_mode(self):
        """Must be implemented"""
        return HVACMode.HEAT

    @property
    def hvac_modes(self):
        """Must be implemented"""
        return [HVACMode.HEAT]
