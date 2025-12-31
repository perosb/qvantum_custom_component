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
from .const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    SETTING_UPDATE_APPLIED,
    DEFAULT_ENABLED_METRICS,
    DEFAULT_DISABLED_METRICS,
)
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
    if api_response and (
        api_response.get("status") == SETTING_UPDATE_APPLIED
        or api_response.get("heatpump_status") == SETTING_UPDATE_APPLIED
    ):
        if data_section and key is not None:
            coordinator.data.get(data_section)[key] = value
            # async_set_updated_data is a synchronous method despite the name
            coordinator.async_set_updated_data(coordinator.data)


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

    def _get_enabled_metrics(self, device_id: str) -> list[str]:
        """Get list of enabled metrics for a device based on entity registry."""
        device_registry = self.hass.data["device_registry"]
        device_reg_id = None
        for device in device_registry.devices.values():
            if (DOMAIN, f"qvantum-{device_id}") in device.identifiers:
                device_reg_id = device.id
                break
        if device_reg_id:
            registry = self.hass.data["entity_registry"]
            enabled_metrics = set()
            for entity in registry.entities.values():
                if (
                    entity.device_id == device_reg_id
                    and entity.disabled_by is None
                    and entity.unique_id.startswith("qvantum_")
                    and entity.unique_id.endswith(f"_{device_id}")
                ):
                    metric_key = entity.unique_id[
                        len("qvantum_") : -len(f"_{device_id}")
                    ]
                    if metric_key in DEFAULT_ENABLED_METRICS + DEFAULT_DISABLED_METRICS:
                        enabled_metrics.add(metric_key)
            _LOGGER.debug(
                f"Enabled metrics for device {device_id}: {list(enabled_metrics)}"
            )
            return list(enabled_metrics)
        _LOGGER.debug(
            f"No device registry entry found for device {device_id}, returning all DEFAULT_ENABLED_METRICS"
        )
        return DEFAULT_ENABLED_METRICS

    async def async_update_data(self):
        """Fetch data from API endpoint."""
        try:
            if self._device is None:
                self._device = await self.api.get_primary_device()

            enabled_metrics = self._get_enabled_metrics(self._device.get("id"))
            data = await self.api.get_metrics(
                self._device.get("id"), enabled_metrics=enabled_metrics
            )
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
            raise UpdateFailed(
                f"Error communicating with API: {err}\nStack trace:\n{stack_trace}"
            ) from err
