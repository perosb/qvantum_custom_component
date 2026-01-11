"""Interfaces with the Qvantum Heat Pump api buttons."""

import logging

from homeassistant.const import (
    EntityCategory,
)


from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MyConfigEntry
from .coordinator import QvantumDataUpdateCoordinator, handle_setting_update_response
from .entity import QvantumEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up the Button."""
    coordinator: QvantumDataUpdateCoordinator = config_entry.runtime_data.coordinator
    device: DeviceInfo = config_entry.runtime_data.device

    buttons = []
    buttons.append(QvantumButtonEntity(coordinator, "extra_tap_water_60min", device))

    buttons.append(QvantumButtonEntity(coordinator, "elevate_access", device))

    async_add_entities(buttons)

    _LOGGER.debug("Setting up platform BUTTON")


class QvantumButtonEntity(QvantumEntity, ButtonEntity):
    """Button for qvantum."""

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        button_key: str,
        device: DeviceInfo,
    ) -> None:
        super().__init__(coordinator, button_key, device)

        if button_key == "elevate_access":
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_press(self) -> None:
        """Handle the button press."""
        match self._metric_key:
            case "extra_tap_water_60min":
                # Activate extra tap water for 60 minutes
                response = await self.coordinator.api.set_extra_tap_water(
                    self._hpid, 60
                )
                await handle_setting_update_response(
                    response, self.coordinator, "settings", "extra_tap_water", "on"
                )
                _LOGGER.info(
                    "Extra tap water activated for 60 minutes via button press"
                )
            case "elevate_access":
                # Generate a new access code
                response = await self.coordinator.api.elevate_access(self._hpid)

                if response is None:
                    _LOGGER.error("Failed to elevate access")
                    return

                _LOGGER.info("Access level: %s", response)

    @property
    def available(self):
        """Check if button is available."""
        return True
