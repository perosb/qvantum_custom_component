"""QvantumDataUpdateCoordinator."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Optional
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_SCAN_INTERVAL,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.storage import Store

from .api import APIAuthError
from .calculations import QvantumCalculationsMixin
from .const import (
    DEFAULT_DISABLED_HTTP_METRICS,
    DEFAULT_DISABLED_MODBUS_METRICS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    HP_STATUS_COOLING,
    HP_STATUS_DEFROSTING,
    HP_STATUS_HEATING,
    HP_STATUS_HOT_WATER,
    SETTING_UPDATE_APPLIED,
    DEFAULT_ENABLED_HTTP_METRICS,
    DEFAULT_ENABLED_MODBUS_METRICS,
    REQUIRED_METRICS,
    REQUIRED_MODBUS_METRICS,
    CONF_MODBUS_TCP,
    TAP_WATER_CAPACITY_MAPPINGS,
)

_LOGGER = logging.getLogger(__name__)

_COMPRESSOR_TO_HP_STATUS_MAP = {
    2: HP_STATUS_HEATING,   # Heating → Heating
    3: HP_STATUS_COOLING,   # Cooling → Cooling
    4: HP_STATUS_HOT_WATER, # Hot water → Hot water
    5: HP_STATUS_HOT_WATER, # Hot water (alias) → Hot water
    6: HP_STATUS_HEATING,   # Heating (alias) → Heating
    7: HP_STATUS_COOLING,   # Cooling (alias) → Cooling
    8: HP_STATUS_HOT_WATER, # Hot water (alias) → Hot water
    9: HP_STATUS_DEFROSTING,  # Defrost DHW passive → Defrosting
    10: HP_STATUS_DEFROSTING, # Defrost heating passive → Defrosting
    11: HP_STATUS_HOT_WATER,  # Pool → Hot water
    12: HP_STATUS_HOT_WATER,  # Pool (alias) → Hot water
    13: HP_STATUS_DEFROSTING, # Defrost pool passive → Defrosting
}


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


class QvantumDataUpdateCoordinator(QvantumCalculationsMixin, DataUpdateCoordinator):
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
        self._last_heatingenergy: float | None = None
        self._last_heatingenergy_time: datetime | None = None
        self._last_dhwenergy: float | None = None
        self._last_dhwenergy_time: datetime | None = None
        self._last_shower_cold_temp: float | None = None
        self._last_shower_flow_lpm: float | None = None
        self._last_shower_temp_c: float | None = (
            None  # EMA of observed shower outlet temperature (bt34)
        )
        self._last_shower_duration_min: float | None = (
            None  # EMA of observed shower duration
        )
        self._shower_start_time: datetime | None = (
            None  # Timestamp when current flow event started
        )
        self._shower_pause_time: datetime | None = (
            None  # Timestamp when flow last stopped (used for session continuation gap)
        )
        self._session_dhw_reheating: bool = False
        self._session_started_with_reheating: bool = (
            False  # True if DHW reheating was already active when this session started
        )
        self._session_active_flow_duration_sec: float | None = (
            None  # cumulative active-flow time within current session; None until first session starts
        )
        self._last_active_flow_sample_time: datetime | None = None
        self._flow_rolling_buffer: list = []  # [(timestamp, flow, cold)] within 60-second window
        self._shower_event_samples: list = []  # [(timestamp, flow, cold, outlet_temp)] for current event
        self._shower_event_history: list = []  # Last 10 completed shower events
        self._last_tap_water_cap: float | None = None
        self._last_published_tap_water_cap: float | None = None
        self._last_published_tap_water_minutes: int | None = None
        self._tap_water_cap_zero_mode: bool = False
        self._tap_water_cap_reheating_floor_mode: bool = False
        self._tap_water_cap_start_time: datetime | None = None
        self._last_persisted_dhw_state: tuple | None = None
        self._dhw_store: Store = Store(
            hass, 1, f"{DOMAIN}.dhw_ema.{config_entry.entry_id}"
        )

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} ({config_entry.unique_id})",
            update_method=self.async_update_data,
            update_interval=timedelta(seconds=self.poll_interval),
        )

    async def async_restore_dhw_state(self) -> None:
        """Restore DHW EMA snapshot from persistent storage after a restart.

        Errors (corrupted JSON, I/O failures) are caught and logged so that a
        bad store file never prevents the integration from loading — the EMA
        simply starts fresh from its defaults.
        """
        try:
            data = await self._dhw_store.async_load()
        except Exception:
            _LOGGER.warning(
                "Failed to load DHW EMA state from storage; starting with defaults",
                exc_info=True,
            )
            return
        if data:
            self._last_shower_cold_temp = data.get("cold_temp")
            self._last_shower_flow_lpm = data.get("flow_lpm")
            self._last_shower_temp_c = data.get("shower_temp")
            self._last_shower_duration_min = data.get("shower_duration")
            self._last_tap_water_cap = data.get("tap_water_cap")
            self._last_published_tap_water_cap = data.get("published_cap")
            self._last_published_tap_water_minutes = data.get("published_minutes")
            _LOGGER.debug(
                "Restored DHW EMA state: cold=%.1f°C, flow=%.1f L/min, shower_temp=%.1f°C, dur=%.1f min, cap=%.2f showers",
                self._last_shower_cold_temp or 0.0,
                self._last_shower_flow_lpm or 0.0,
                self._last_shower_temp_c or 0.0,
                self._last_shower_duration_min or 0.0,
                self._last_tap_water_cap or 0.0,
            )

    def _persist_dhw_state(self) -> None:
        """Save DHW EMA snapshot via a debounced write so it survives a restart.

        Uses async_delay_save (coalesces multiple calls within the delay window
        into a single disk write). Only schedules a write when the state has
        actually changed since the last save, and swallows storage errors so
        they cannot propagate into the coordinator update cycle.
        """
        current_state = (
            self._last_shower_cold_temp,
            self._last_shower_flow_lpm,
            self._last_shower_temp_c,
            self._last_shower_duration_min,
            self._last_tap_water_cap,
            self._last_published_tap_water_cap,
            self._last_published_tap_water_minutes,
        )
        if current_state == self._last_persisted_dhw_state:
            return
        try:
            self._dhw_store.async_delay_save(
                lambda: {
                    "cold_temp": self._last_shower_cold_temp,
                    "flow_lpm": self._last_shower_flow_lpm,
                    "shower_temp": self._last_shower_temp_c,
                    "shower_duration": self._last_shower_duration_min,
                    "tap_water_cap": self._last_tap_water_cap,
                    "published_cap": self._last_published_tap_water_cap,
                    "published_minutes": self._last_published_tap_water_minutes,
                },
                delay=30,
            )
            self._last_persisted_dhw_state = current_state
        except Exception:
            _LOGGER.warning("Failed to schedule DHW state persistence", exc_info=True)

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

        default_metrics = (
            DEFAULT_ENABLED_MODBUS_METRICS
            if self.modbus_enabled
            else DEFAULT_ENABLED_HTTP_METRICS
        )

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
                    allowed_metrics = set(default_metrics)
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
                final_metrics.update(default_metrics)
            else:
                # Include all currently enabled metrics plus any new defaults not in registry
                final_metrics.update(enabled_metrics)
                for metric in default_metrics:
                    if metric not in known_metrics:
                        final_metrics.add(metric)
                        _LOGGER.debug(
                            "Adding new default metric '%s' for device %s since it's not in the registry",
                            metric,
                            device_id,
                        )

            _LOGGER.debug(
                "Final enabled metrics for device %s: %s",
                device_id,
                sorted(final_metrics),
            )

            return sorted(final_metrics)

        _LOGGER.debug(
            "No device registry entry found for device %s, returning all default enabled metrics",
            device_id,
        )
        # Always include required metrics; Modbus-only intermediate metrics are
        # only needed when Modbus is enabled (they don't exist in the HTTP API).
        final_metrics = set(default_metrics)
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

    async def _fetch_tap_stop_modbus(self, device_id: str, values: dict) -> None:
        """Poll tap_stop via HTTP in Modbus mode when extra_tap_water is active.

        tap_stop is HTTP-only; it is fetched at the DEFAULT HTTP scan interval
        and cached between fetches.
        """
        now = dt_util.utcnow()
        elapsed = (
            (now - self._last_tap_stop_fetch).total_seconds()
            if self._last_tap_stop_fetch is not None
            else float("inf")
        )
        if elapsed > DEFAULT_SCAN_INTERVAL:
            try:
                _LOGGER.debug(
                    "Fetching tap_stop via HTTP for device %s due to active extra_tap_water and elapsed time %.1f seconds",
                    device_id,
                    elapsed,
                )
                http_data = await self.api.get_http_metrics(device_id, ["tap_stop"])
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

    def _derive_tap_water_capacity(self, values: dict) -> None:
        """Derive tap_water_capacity_target from tap_water_start/stop when absent.

        Uses TAP_WATER_CAPACITY_MAPPINGS to convert the (start, stop) temperature
        pair into a capacity level (1–7) and stores it back into values.
        """
        if values.get("tap_water_capacity_target") is not None:
            return
        tap_start = values.get("tap_water_start")
        tap_stop = values.get("tap_water_stop")
        if tap_start is None or tap_stop is None:
            return
        capacity = TAP_WATER_CAPACITY_MAPPINGS.get((tap_start, tap_stop))
        if capacity is not None:
            values["tap_water_capacity_target"] = capacity
        else:
            # use nearest tap_stop for unmapped pairs and log a debug message
            if isinstance(tap_stop, (int, float)):
                nearest_pair = min(
                    TAP_WATER_CAPACITY_MAPPINGS.keys(), key=lambda pair: abs(pair[1] - tap_stop)
                )
                nearest_capacity = TAP_WATER_CAPACITY_MAPPINGS[nearest_pair]
                values["tap_water_capacity_target"] = nearest_capacity
            _LOGGER.debug(
                "No tap water capacity mapping found for start=%s and stop=%s, using nearest capacity=%s based on stop temperature",
                tap_start,
                tap_stop,
                values.get("tap_water_capacity_target", "none"),
            )

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

            # Post process metrics for UI
            # When hp_status reports 0 (idle), derive a more specific value from
            # compressor_state using the same 5-state hp_status schema:
            #   0=Idle, 1=Defrosting, 2=Hot water, 3=Heating, 4=Cooling
            if (
                metrics_dict.get("hp_status") == 0
                and "compressor_state" in metrics_dict
            ):
                comp = metrics_dict["compressor_state"]
                metrics_dict["hp_status"] = _COMPRESSOR_TO_HP_STATUS_MAP.get(comp, 0)

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

            # In Modbus mode, tap_stop is HTTP-only. Poll it at the DEFAULT HTTP
            # scan interval whenever extra_tap_water is active.
            if self.modbus_enabled and values.get("extra_tap_water") == "on":
                await self._fetch_tap_stop_modbus(device_id, values)

            self._derive_tap_water_capacity(values)

            if self.modbus_enabled:
                self._calculate_heating_power(values)
                self._calculate_dhw_power(values)
                self._calculate_tap_water_cap(values)
                self._persist_dhw_state()

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
