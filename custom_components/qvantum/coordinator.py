"""QvantumDataUpdateCoordinator."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any, Optional
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_SCAN_INTERVAL,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import APIAuthError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, SETTING_UPDATE_APPLIED
import traceback

_LOGGER = logging.getLogger(__name__)


async def handle_setting_update_response(
    api_response: Optional[dict[str, Any]],
    coordinator: QvantumDataUpdateCoordinator,
    data_section: Optional[str],
    key: Optional[str],
    value: Any,
) -> None:
    """Handle API response for setting updates and update coordinator data if successful."""
    if api_response and api_response.get("status") == SETTING_UPDATE_APPLIED:
        if data_section and key is not None:
            coordinator.data.get(data_section)[key] = value
            # async_set_updated_data is a synchronous method despite the name
            coordinator.async_set_updated_data(coordinator.data)

        await coordinator.async_refresh()


class QvantumDataUpdateCoordinator(DataUpdateCoordinator):
    """Qvantum coordinator."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize coordinator."""
        self.poll_interval = config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )

        self.api = hass.data[DOMAIN]
        self._device = None

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} ({config_entry.unique_id})",
            update_method=self.async_update_data,
            update_interval=timedelta(seconds=self.poll_interval),
        )

    async def async_update_data(self):
        """Fetch data from API endpoint."""
        try:
            if self._device is None:
                self._device = await self.api.get_primary_device()

            data = await self.api.get_metrics(self._device.get("id"))
            settings = await self.api.get_settings(self._device.get("id"))
            data.update({"device": self._device})

            settings_dict = {}
            for setting in settings.get("settings"):
                settings_dict[setting.get("name")] = setting.get("value")

            data.update({"settings": settings_dict})

            _LOGGER.debug("Fetched data: %s", data)

            return data

        except APIAuthError as err:
            _LOGGER.error(err)
            raise UpdateFailed(err) from err
        except Exception as err:
            stack_trace = traceback.format_exc()
            raise UpdateFailed(f"Error communicating with API: {err}\nStack trace:\n{stack_trace}") from err
