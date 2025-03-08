"""QvantumDataUpdateCoordinator."""

from dataclasses import dataclass
from datetime import timedelta
import logging
import json
import async_timeout

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)
from homeassistant.core import DOMAIN, HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import QvantumAPI, APIAuthError
from .const import DEFAULT_SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)

class QvantumDataUpdateCoordinator(DataUpdateCoordinator):
    """Qvantum coordinator."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize coordinator."""
        self.user = config_entry.data[CONF_USERNAME]
        self.pwd = config_entry.data[CONF_PASSWORD]
        self.poll_interval = config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        self.api = QvantumAPI(user=self.user, pwd=self.pwd)
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
                self._device = await self.api.get_device()

            data = await self.api.fetch_data(self._device.get("id"))
            data.update({"device": self._device})

            _LOGGER.debug("Fetched data: %s", data)

            return data

        except APIAuthError as err:
            _LOGGER.error(err)
            raise UpdateFailed(err) from err
        except Exception as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err