"""QvantumDataUpdateCoordinator."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Optional
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_SCAN_INTERVAL,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import APIAuthError
from .const import (
    DEFAULT_DISABLED_HTTP_METRICS,
    DEFAULT_DISABLED_MODBUS_METRICS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    SETTING_UPDATE_APPLIED,
    DEFAULT_ENABLED_METRICS,
    REQUIRED_METRICS,
    REQUIRED_MODBUS_METRICS,
    CONF_MODBUS_TCP,
)

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

        # Modbus may be configured in options or in legacy config entry data.
        self.modbus_enabled = config_entry.options.get(
            CONF_MODBUS_TCP,
            config_entry.data.get(CONF_MODBUS_TCP, False),
        )

        if self.modbus_enabled:
            self.poll_interval = min(self.poll_interval, 15)  # Faster for Modbus

        self.api = hass.data[DOMAIN]
        self._device = None
        self._last_tap_stop_fetch: datetime | None = None
        self._cached_tap_stop: Any = None

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} ({config_entry.unique_id})",
            update_method=self.async_update_data,
            update_interval=timedelta(seconds=self.poll_interval),
        )

    @property
    def device_id(self) -> str | None:
        """Return the device ID."""
        if self._device:
            return self._device.get("id")
        return None

    def _get_enabled_metrics(self, device_id: str) -> list[str]:
        """Get list of enabled metrics for a device based on entity registry."""
        from homeassistant.helpers import device_registry as dr
        from homeassistant.helpers import entity_registry as er

        device_registry = dr.async_get(self.hass)
        device_reg_id = None
        for device in device_registry.devices.values():
            if (DOMAIN, f"qvantum-{device_id}") in device.identifiers:
                device_reg_id = device.id
                break
        if device_reg_id:
            registry = er.async_get(self.hass)
            enabled_metrics = set()
            known_metrics = set()
            for entity in registry.entities.values():
                if (
                    entity.device_id == device_reg_id
                    and entity.unique_id.startswith("qvantum_")
                    and entity.unique_id.endswith(f"_{device_id}")
                ):
                    from .entity import extract_metric_key

                    metric_key = extract_metric_key(entity.unique_id, device_id)

                    # Known metrics include the default metrics always.
                    # HTTP-only disabled metrics are only known in HTTP mode.
                    # Modbus disabled metrics are known in Modbus mode.
                    allowed_metrics = set(DEFAULT_ENABLED_METRICS)
                    if self.modbus_enabled:
                        allowed_metrics |= set(DEFAULT_DISABLED_MODBUS_METRICS)
                    else:
                        allowed_metrics |= set(DEFAULT_DISABLED_HTTP_METRICS)

                    if metric_key in allowed_metrics:
                        known_metrics.add(metric_key)
                        if entity.disabled_by is None:
                            enabled_metrics.add(metric_key)
            _LOGGER.debug(
                "Known metrics for device %s: %s", device_id, sorted(known_metrics)
            )
            _LOGGER.debug(
                "Enabled metrics for device %s: %s", device_id, sorted(enabled_metrics)
            )

            # Always include required metrics; Modbus-only intermediate metrics are
            # only needed when Modbus is enabled (they don't exist in the HTTP API).
            final_metrics = set(REQUIRED_METRICS)
            if self.modbus_enabled:
                final_metrics.update(REQUIRED_MODBUS_METRICS)

            if not known_metrics:
                # First setup: no registry entries yet - include all default metrics
                final_metrics.update(DEFAULT_ENABLED_METRICS)
            else:
                # Include all currently enabled metrics plus any new defaults not in registry
                final_metrics.update(enabled_metrics)
                for metric in DEFAULT_ENABLED_METRICS:
                    if metric not in known_metrics:
                        final_metrics.add(metric)

            return sorted(final_metrics)
        _LOGGER.debug(
            "No device registry entry found for device %s, returning all DEFAULT_ENABLED_METRICS",
            device_id,
        )
        # Always include required metrics; Modbus-only intermediate metrics are
        # only needed when Modbus is enabled (they don't exist in the HTTP API).
        final_metrics = set(DEFAULT_ENABLED_METRICS)
        final_metrics.update(REQUIRED_METRICS)
        if self.modbus_enabled:
            final_metrics.update(REQUIRED_MODBUS_METRICS)
        return sorted(final_metrics)

    def _process_settings_data(self, settings_data: dict) -> dict[str, Any]:
        """Process raw settings data into a dictionary.

        Args:
            settings_data: Raw settings response from API

        Returns:
            Dictionary mapping setting names to values
        """
        settings_dict = {}
        settings_list = settings_data.get("settings", [])

        if not isinstance(settings_list, list):
            _LOGGER.warning("Settings data is not a list: %s", type(settings_list))
            return settings_dict

        for setting in settings_list:
            if not isinstance(setting, dict):
                _LOGGER.warning(
                    "Invalid setting format, expected dict: %s", type(setting)
                )
                continue

            name = setting.get("name")
            value = setting.get("value")

            if name is None or value is None:
                _LOGGER.warning("Setting missing name or value: %s", setting)
                continue

            settings_dict[name] = value

        _LOGGER.debug("Processed %d settings", len(settings_dict))
        return settings_dict

    async def async_update_data(self):
        """Fetch data from API endpoint."""
        try:
            # Get device info if not cached
            if self._device is None:
                _LOGGER.debug("Fetching primary device info")
                self._device = await self.api.get_primary_device()

            # Validate device information before accessing it
            if self._device is None:
                raise UpdateFailed("No devices found")
            if not isinstance(self._device, dict):
                raise UpdateFailed(f"Invalid device data type: {type(self._device)}")
            device_id = self._device.get("id")
            if not device_id:
                raise UpdateFailed("Device ID not found in device data")

            # Get enabled metrics for this device
            enabled_metrics = self._get_enabled_metrics(device_id)
            _LOGGER.debug(
                "Fetching data for device %s with %d enabled metrics",
                device_id,
                len(enabled_metrics),
            )

            # Fetch metrics and settings concurrently for better performance
            metrics_task = self.api.get_metrics(
                device_id, enabled_metrics=enabled_metrics
            )
            settings_task = self.api.get_settings(device_id)

            data, settings = await asyncio.gather(metrics_task, settings_task)

            # Validate response data
            if not isinstance(data, dict):
                raise UpdateFailed(f"Invalid metrics data type: {type(data)}")
            if not isinstance(settings, dict):
                raise UpdateFailed(f"Invalid settings data type: {type(settings)}")

            # Extract metrics from API response
            metrics_dict = data.get("metrics", {})
            _LOGGER.debug("Metrics data: %s", metrics_dict)

            # Process settings data
            settings_dict = self._process_settings_data(settings)
            _LOGGER.debug("Settings data: %s", settings_dict)

            # Detect and log conflicts where settings override metrics
            overlapping_keys = metrics_dict.keys() & settings_dict.keys()
            for conflict_key in overlapping_keys:
                metrics_value = metrics_dict.get(conflict_key)
                settings_value = settings_dict.get(conflict_key)
                if metrics_value != settings_value:
                    _LOGGER.debug(
                        "Key conflict for device %s on '%s': using settings value over metrics value",
                        device_id,
                        conflict_key,
                    )
            # Merge metrics and settings into unified values structure
            # Settings take precedence over metrics in case of conflicts
            values = {**metrics_dict, **settings_dict}

            # In Modbus mode, tap_stop is HTTP-only. Poll it at the configured HTTP
            # scan interval whenever extra_tap_water is active.
            if self.modbus_enabled and values.get("extra_tap_water") == "on":
                now = dt_util.utcnow()
                elapsed = (
                    (now - self._last_tap_stop_fetch).total_seconds()
                    if self._last_tap_stop_fetch is not None
                    else float("inf")
                )
                if elapsed > DEFAULT_SCAN_INTERVAL:
                    try:
                        http_data = await self.api.get_http_metrics(
                            device_id, ["tap_stop"]
                        )
                        tap_stop = http_data.get("metrics", {}).get("tap_stop")
                        if tap_stop is not None:
                            self._cached_tap_stop = tap_stop
                            values["tap_stop"] = tap_stop
                    except Exception as exc:
                        _LOGGER.warning(
                            "Failed to fetch tap_stop via HTTP in Modbus mode for device %s: %s",
                            device_id,
                            exc,
                        )
                    finally:
                        self._last_tap_stop_fetch = now
                elif self._cached_tap_stop is not None:
                    values["tap_stop"] = self._cached_tap_stop

            _LOGGER.debug("Final values: %s", values)

            # Validate we have some data
            if not values:
                _LOGGER.warning("No data received from API for device %s", device_id)

            result = {"device": self._device, "values": values}

            _LOGGER.debug(
                "Successfully fetched data for device %s: %d values",
                device_id,
                len(values),
            )

            return result

        except APIAuthError as err:
            _LOGGER.error(
                "Authentication error for device %s: %s",
                getattr(self, "_device", {}).get("id", "unknown"),
                err,
            )
            raise UpdateFailed(f"Authentication failed: {err}") from err
        except asyncio.TimeoutError as err:
            _LOGGER.error(
                "Timeout fetching data for device %s",
                getattr(self, "_device", {}).get("id", "unknown"),
            )
            raise UpdateFailed("Request timeout") from err
        except Exception as err:
            device_id = getattr(self, "_device", {}).get("id", "unknown")
            _LOGGER.error(
                "Unexpected error fetching data for device %s: %s",
                device_id,
                err,
                exc_info=True,
            )
            raise UpdateFailed(f"Error communicating with API: {err}") from err
