"""Interfaces with the Qvantum Heat Pump api buttons."""

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
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
    """Set up the Button."""
    coordinator: QvantumDataUpdateCoordinator = config_entry.runtime_data.coordinator
    device: DeviceInfo = config_entry.runtime_data.device

    buttons = []
    buttons.append(QvantumButtonEntity(coordinator, "extra_tap_water_60min", device))

    async_add_entities(buttons)

    _LOGGER.debug("Setting up platform BUTTON")


class QvantumButtonEntity(CoordinatorEntity, ButtonEntity):
    """Button for qvantum."""

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        button_key: str,
        device: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self._hpid = self.coordinator.data.get("metrics").get("hpid")
        self._attr_translation_key = button_key
        self._button_key = button_key
        self._attr_unique_id = f"qvantum_{button_key}_{self._hpid}"
        self._attr_device_info = device
        self._attr_has_entity_name = True

    async def async_press(self) -> None:
        """Handle the button press."""
        if self._button_key == "extra_tap_water_60min":
            # Activate extra tap water for 60 minutes
            await self.coordinator.api.set_extra_tap_water(self._hpid, 60)
            _LOGGER.info("Extra tap water activated for 60 minutes via button press")
            # Data will be updated via coordinator refresh

        await self.coordinator.async_refresh()

    @property
    def available(self):
        """Check if button is available."""
        return True
