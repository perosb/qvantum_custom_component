"""QvantumDataUpdateCoordinator."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Optional
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.storage import Store

from .api import APIAuthError
from .calculations import QvantumCalculationsMixin
from .const import (
    DEFAULT_DISABLED_HTTP_METRICS,
    DEFAULT_DISABLED_MODBUS_METRICS,
    DEFAULT_MODBUS_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    FIRMWARE_KEYS,
    HP_STATUS_COOLING,
    HP_STATUS_DEFROSTING,
    HP_STATUS_HEATING,
    HP_STATUS_HOT_WATER,
    MIN_MODBUS_SCAN_INTERVAL,
    SETTING_UPDATE_APPLIED,
    DEFAULT_ENABLED_HTTP_METRICS,
    DEFAULT_ENABLED_MODBUS_METRICS,
    REQUIRED_METRICS,
    REQUIRED_MODBUS_METRICS,
    CONF_MODBUS_SCAN_INTERVAL,
    CONF_MODBUS_TCP,
    HTTP_CLOUD_LOOKUP_TIMEOUT,
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
) -> bool:
    """Handle API response for setting updates and update coordinator data if successful."""
    success = bool(
        api_response
        and (
            api_response.get("status") == SETTING_UPDATE_APPLIED
            or api_response.get("heatpump_status") == SETTING_UPDATE_APPLIED
        )
    )
    if success and data_section and key is not None:
        coordinator.data.get(data_section)[key] = value
        # async_set_updated_data is a synchronous method despite the name
        coordinator.async_set_updated_data(coordinator.data)
        return True
    return False


def _firmware_metadata_from_sw_version(sw_version: str | None) -> dict:
    """Parse DeviceInfo sw_version (display/cc/inv) back into metadata keys."""
    if not sw_version:
        return {}
    parts = str(sw_version).split("/")
    metadata = {}
    for key, part in zip(FIRMWARE_KEYS, parts):
        if not part or part == "None":
            continue
        metadata[key] = part
    return metadata


class QvantumDataUpdateCoordinator(QvantumCalculationsMixin, DataUpdateCoordinator):
    """Qvantum coordinator."""

    @staticmethod
    def resolve_poll_interval(config_entry: ConfigEntry) -> tuple[bool, int]:
        """Return (modbus_enabled, poll_interval_seconds) from a config entry."""
        modbus_enabled = config_entry.options.get(
            CONF_MODBUS_TCP,
            config_entry.data.get(CONF_MODBUS_TCP, False),
        )
        if not modbus_enabled:
            poll_interval = config_entry.options.get(
                CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
            )
            try:
                return False, int(poll_interval)
            except (TypeError, ValueError):
                return False, DEFAULT_SCAN_INTERVAL

        # Dedicated Modbus interval. Falls back to the historical 15s default
        # when unset. Enforce a sensible minimum.
        modbus_interval = config_entry.options.get(
            CONF_MODBUS_SCAN_INTERVAL, DEFAULT_MODBUS_SCAN_INTERVAL
        )
        try:
            modbus_interval = int(modbus_interval)
        except (TypeError, ValueError):
            modbus_interval = DEFAULT_MODBUS_SCAN_INTERVAL
        return True, max(modbus_interval, MIN_MODBUS_SCAN_INTERVAL)

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize coordinator."""
        self.modbus_enabled, self.poll_interval = self.resolve_poll_interval(
            config_entry
        )

        self.api = hass.data[DOMAIN]
        self._config_entry = config_entry
        self._device = None
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
        self._device_store: Store = Store(
            hass, 1, f"{DOMAIN}.device.{config_entry.entry_id}"
        )
        self._store_account_mismatch = False

        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN} ({config_entry.unique_id})",
            update_method=self.async_update_data,
            update_interval=timedelta(seconds=self.poll_interval),
        )

    def apply_poll_interval(self, config_entry: ConfigEntry) -> bool:
        """Apply poll-interval options in place without tearing down the entry.

        Returns True when the interval changed. Does not change Modbus
        enablement; callers must reload when transport settings change.
        """
        _, poll_interval = self.resolve_poll_interval(config_entry)
        if poll_interval == self.poll_interval:
            return False

        self.poll_interval = poll_interval
        # DataUpdateCoordinator reschedules when update_interval is assigned.
        self.update_interval = timedelta(seconds=poll_interval)
        _LOGGER.debug(
            "Updated poll interval to %ss for %s",
            poll_interval,
            self.name,
        )
        return True

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

    async def _load_cached_device(self) -> dict | None:
        """Load last-known device identity from persistent storage."""
        self._store_account_mismatch = False
        try:
            data = await self._device_store.async_load()
        except Exception:
            _LOGGER.debug("Failed to load cached device info", exc_info=True)
            return None
        device = self._device_from_store_payload(data)
        if self._is_usable_cached_device(device):
            return device
        return None

    def _current_username(self) -> str | None:
        """Return the configured account username, if any."""
        data = getattr(self._config_entry, "data", None) or {}
        username = data.get(CONF_USERNAME)
        return username if isinstance(username, str) and username else None

    def _device_from_store_payload(self, data: dict | None) -> dict | None:
        """Unwrap a stored device bound to the current account.

        Payloads without an account binding are ignored so a reconfigure that
        keeps the same config-entry ID cannot keep serving the previous device.
        """
        if not isinstance(data, dict):
            return None
        stored_user = data.get("username")
        device = data.get("device")
        if stored_user != self._current_username():
            # A present binding for another account must also block registry
            # recovery: reconfigure keeps this config-entry ID, so the HA
            # device registry still points at the previous account's device.
            if stored_user:
                self._store_account_mismatch = True
            _LOGGER.debug(
                "Ignoring cached device identity bound to a different account"
            )
            return None
        return device if isinstance(device, dict) else None

    def _is_usable_cached_device(self, cached: dict | None) -> bool:
        """Return True when cached identity is enough for the current transport.

        HTTP mode needs metadata so a previous empty metadata response cannot
        permanently skip a recovered cloud lookup. Modbus mode only needs an id.
        """
        if not isinstance(cached, dict) or not cached.get("id"):
            return False
        if self.modbus_enabled:
            return True
        metadata = cached.get("device_metadata")
        return isinstance(metadata, dict) and bool(metadata)

    async def _persist_device_state(self) -> None:
        """Persist device identity so Modbus can start without the HTTP API."""
        if not isinstance(self._device, dict) or not self._device.get("id"):
            return
        payload: dict[str, Any] = {"device": self._device}
        username = self._current_username()
        if username:
            payload["username"] = username
        try:
            await self._device_store.async_save(payload)
        except Exception:
            _LOGGER.debug("Failed to persist device info", exc_info=True)

    def _device_from_registry(self) -> dict | None:
        """Rebuild device identity from the HA device registry after a restart."""
        try:
            from homeassistant.helpers import device_registry as dr

            registry = dr.async_get(self.hass)
        except Exception:
            return None
        if registry is None:
            return None

        devices = getattr(registry, "devices", None)
        if devices is None:
            return None

        entry_id = getattr(self._config_entry, "entry_id", None)
        values = devices.values() if hasattr(devices, "values") else devices
        prefix = f"{DOMAIN}-"
        for ha_device in values:
            config_entries = getattr(ha_device, "config_entries", None)
            if (
                entry_id
                and config_entries is not None
                and entry_id not in config_entries
            ):
                continue
            identifiers = getattr(ha_device, "identifiers", None) or set()
            for identifier in identifiers:
                if not isinstance(identifier, (tuple, list)) or len(identifier) != 2:
                    continue
                domain, raw_id = identifier
                if domain != DOMAIN or not isinstance(raw_id, str):
                    continue
                if not raw_id.startswith(prefix):
                    continue
                device_id = raw_id[len(prefix) :]
                if not device_id:
                    continue
                return {
                    "id": device_id,
                    "vendor": getattr(ha_device, "manufacturer", None),
                    "model": getattr(ha_device, "model", None),
                    "serial": getattr(ha_device, "serial_number", None),
                    "device_metadata": _firmware_metadata_from_sw_version(
                        getattr(ha_device, "sw_version", None)
                    ),
                }
        return None

    async def _ensure_device(self) -> None:
        """Populate device identity from cache, Modbus probe, HTTP, or registry.

        Modbus mode never contacts the cloud. Cached identity is preferred so
        existing unique IDs stay stable; otherwise the pump is probed.
        """
        if self._device is None:
            cached = await self._load_cached_device()
            if isinstance(cached, dict) and cached.get("id"):
                self._device = cached
                _LOGGER.debug(
                    "Loaded cached device info for %s", cached.get("id")
                )

        if self._device is not None:
            return

        if self.modbus_enabled:
            try:
                probed = await self.api.async_probe_identity()
            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.warning("Failed to probe Modbus identity: %s", err)
                probed = None
            if isinstance(probed, dict) and probed.get("id"):
                self._device = probed
                await self._persist_device_state()
                return
            if not self._store_account_mismatch:
                from_registry = self._device_from_registry()
                if from_registry:
                    self._device = from_registry
                    await self._persist_device_state()
                    _LOGGER.warning(
                        "Using device registry for local Modbus startup"
                    )
                    return
            if self._store_account_mismatch:
                _LOGGER.warning(
                    "Skipping device registry recovery; cached identity is bound to a different account"
                )
            raise UpdateFailed("No device identity from Modbus or device registry")

        try:
            device = await asyncio.wait_for(
                self.api.get_primary_device(),
                timeout=HTTP_CLOUD_LOOKUP_TIMEOUT,
            )
            if isinstance(device, dict) and device.get("id"):
                self._device = device
                await self._persist_device_state()
                return
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.warning(
                "Failed to fetch device info from HTTP API: %s", err
            )
            raise

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
            await self._ensure_device()

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
                self._logged_device_id(),
                err,
            )
            raise UpdateFailed(f"Authentication failed: {err}") from err
        except asyncio.TimeoutError as err:
            _LOGGER.error(
                "Timeout fetching data for device %s",
                self._logged_device_id(),
            )
            raise UpdateFailed("Request timeout") from err
        except Exception as err:
            _LOGGER.error(
                "Unexpected error fetching data for device %s: %s",
                self._logged_device_id(),
                err,
                exc_info=True,
            )
            raise UpdateFailed(f"Error communicating with API: {err}") from err

    def _logged_device_id(self) -> str:
        """Return a device id safe to include in error logs."""
        device = self._device
        if isinstance(device, dict):
            return str(device.get("id") or "unknown")
        return "unknown"
