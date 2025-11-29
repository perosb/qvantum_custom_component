"""Interfaces with the Qvantum Heat Pump api sensors."""

import logging

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from typing import Optional, Any

from .const import (
    FAN_SPEED_STATE_OFF,
    FAN_SPEED_STATE_NORMAL,
    FAN_SPEED_STATE_EXTRA,
)
from . import MyConfigEntry
from .coordinator import QvantumDataUpdateCoordinator, handle_setting_update_response

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up the Select."""
    coordinator: QvantumDataUpdateCoordinator = config_entry.runtime_data.coordinator
    device: DeviceInfo = config_entry.runtime_data.device

    fans = []
    fans.append(QvantumFanEntity(coordinator, "fanspeedselector", device))

    async_add_entities(fans)

    _LOGGER.debug("Setting up platform FAN")


class QvantumFanEntity(CoordinatorEntity, FanEntity):
    """Fan for qvantum."""

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        metric_key: str,
        device: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self._hpid = self.coordinator.data.get("metrics").get("hpid")
        self._attr_translation_key = metric_key
        self._metric_key = metric_key
        self._attr_unique_id = f"qvantum_{metric_key}_{self._hpid}"
        self._attr_device_info = device
        self._attr_has_entity_name = True
        self._attr_preset_modes: list[str] = [
            FAN_SPEED_STATE_OFF,
            FAN_SPEED_STATE_NORMAL,
            FAN_SPEED_STATE_EXTRA,
        ]
        self._attr_supported_features = (
            FanEntityFeature.PRESET_MODE
            | FanEntityFeature.TURN_OFF
            | FanEntityFeature.TURN_ON
        )

    @property
    def preset_mode(self):
        """Get metric from API data."""
        return self.coordinator.data.get("settings").get(self._metric_key)

    @property
    def is_on(self):
        """Return true if the fan is on."""
        return (
            self.coordinator.data.get("settings").get(self._metric_key)
            != FAN_SPEED_STATE_OFF
        )

    @property
    def available(self):
        return (
            self._metric_key in self.coordinator.data.get("settings")
            and self.coordinator.data.get("settings").get(self._metric_key) is not None
        )

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the preset mode of the fan."""
        await self.set_fanspeedselector(preset_mode)

    async def async_turn_on(
        self,
        speed: Optional[str] = None,
        percentage: Optional[int] = None,
        preset_mode: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        """Turn on the fan."""
        await self.set_fanspeedselector(preset_mode or FAN_SPEED_STATE_NORMAL)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the fan off."""
        await self.set_fanspeedselector(FAN_SPEED_STATE_OFF)

    async def set_fanspeedselector(self, preset: str) -> None:
        response = await self.coordinator.api.set_fanspeedselector(self._hpid, preset)
        await handle_setting_update_response(
            response,
            self.coordinator,
            "settings",
            self._metric_key,
            preset,
        )
