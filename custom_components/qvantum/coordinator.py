"""QvantumDataUpdateCoordinator."""

from datetime import timedelta
import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_SCAN_INTERVAL,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import APIAuthError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


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
            _LOGGER.error(f"Unexpected error: {err}")
            raise UpdateFailed(f"Error communicating with API: {err}") from err
