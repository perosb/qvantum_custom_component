"""QvantumDataUpdateCoordinator."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Any, Callable, Optional
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import EVENT_DEVICE_REGISTRY_UPDATED
from homeassistant.helpers.entity_registry import EVENT_ENTITY_REGISTRY_UPDATED
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .client.cloud import QvantumCloudClient
from .client.exceptions import (
    AuthError as APIAuthError,
    RateLimitError as APIRateLimitError,
    TransportError as APIConnectionError,
)
from .client.models import result_applied
from .client.modbus import QvantumModbusClient, metric_range
from .client.protocol import (
    QvantumCloudClientProtocol,
    QvantumModbusClientProtocol,
)
from .extra_dhw import ExtraDhwTimer, async_apply_extra_tap_water
from .calculations import QvantumCalculationsMixin
from .client.constants import alias_heating_curve_settings
from .const import (
    DEFAULT_DISABLED_HTTP_METRICS,
    DEFAULT_DISABLED_MODBUS_METRICS,
    DEFAULT_MODBUS_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DHW_MODE_EXTRA,
    DHW_MODE_NORMAL,
    DOMAIN,
    FIRMWARE_KEYS,
    HP_STATUS_COOLING,
    HP_STATUS_DEFROSTING,
    HP_STATUS_HEATING,
    HP_STATUS_HOT_WATER,
    MIN_MODBUS_SCAN_INTERVAL,
    DEFAULT_ENABLED_HTTP_METRICS,
    DEFAULT_ENABLED_MODBUS_METRICS,
    MODBUS_SW_VERSION_REFRESH_INTERVAL,
    REQUIRED_METRICS,
    REQUIRED_MODBUS_METRICS,
    CONF_EXTERNAL_ROOM_TEMP_ENTITY,
    CONF_MODBUS_SCAN_INTERVAL,
    CONF_MODBUS_TCP,
    EXTERNAL_ROOM_TEMP_EMA_TAU,
    EXTERNAL_ROOM_TEMP_MAX_FEED_INTERVAL,
    HTTP_CLOUD_LOOKUP_TIMEOUT,
    TAP_WATER_CAPACITY_MAPPINGS,
    default_metric_creates_entity,
)

_LOGGER = logging.getLogger(__name__)

# Cap a Retry-After backoff so a hostile/huge value cannot park the poller
# for hours; a successful poll restores the configured interval.
_MAX_RATE_LIMIT_BACKOFF_SECONDS = 3600

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
    extra_updates: Optional[dict[str, Any]] = None,
) -> bool:
    """Handle API response for setting updates and update coordinator data if successful."""
    if result_applied(api_response) and data_section and key is not None:
        section = coordinator.data.get(data_section)
        section[key] = value
        if extra_updates:
            section.update(extra_updates)
        if key == "extra_tap_water":
            _apply_extra_dhw_tap_stop(coordinator, section, value)
            # map_operation_mode prefers dhw_mode; keep it aligned with Extra on/off
            # so water_heater updates immediately (button/switch writers).
            if isinstance(section, dict) and "dhw_mode" in section:
                section["dhw_mode"] = (
                    DHW_MODE_EXTRA if _is_extra_dhw_on(value) else DHW_MODE_NORMAL
                )
        # async_set_updated_data is a synchronous method despite the name
        coordinator.async_set_updated_data(coordinator.data)
        return True
    return False


async def async_apply_setting(
    coordinator: QvantumDataUpdateCoordinator,
    *,
    response: Optional[dict[str, Any]],
    key: str,
    value: Any,
    extra_updates: Optional[dict[str, Any]] = None,
) -> bool:
    """Optimistically apply a confirmed setting write to coordinator data.

    Entities call this after a write instead of repeating the ``APPLIED``
    check: ``values[key]`` and ``extra_updates`` (for settings that must move
    together, e.g. the SmartControl modes) are only applied when the
    transport confirmed the write.
    """
    return await handle_setting_update_response(
        response, coordinator, "values", key, value, extra_updates=extra_updates
    )


def _is_extra_dhw_on(value: Any) -> bool:
    """Return True when extra DHW is active (holding 53 Extra, or on/True)."""
    return value in ("on", True, DHW_MODE_EXTRA)


def _apply_extra_dhw_tap_stop(coordinator: Any, section: Any, extra_value: Any) -> None:
    """Keep the extra-DHW timer sensor in sync with an optimistic extra DHW write."""
    if not isinstance(section, dict):
        return
    if extra_value in ("off", False, 0):
        section.pop("tap_stop", None)
        return
    if not _is_extra_dhw_on(extra_value):
        return
    restore_at = getattr(getattr(coordinator, "extra_dhw", None), "restore_at", None)
    if isinstance(restore_at, (int, float)):
        section["tap_stop"] = int(restore_at)
    elif restore_at is None:
        section.pop("tap_stop", None)


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

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        *,
        client: QvantumCloudClientProtocol | QvantumModbusClientProtocol,
        extra_dhw: ExtraDhwTimer | None = None,
    ) -> None:
        """Initialize coordinator."""
        self.modbus_enabled, self.poll_interval = self.resolve_poll_interval(
            config_entry
        )

        self.client: QvantumCloudClientProtocol | QvantumModbusClientProtocol = client
        self.extra_dhw = extra_dhw
        self._config_entry = config_entry
        self._device = None
        self._sw_version_refreshed_at: float | None = None
        self._last_heatingenergy: float | None = None
        self._last_heatingenergy_time: datetime | None = None
        self._last_dhwenergy: float | None = None
        self._last_dhwenergy_time: datetime | None = None
        # Cumulative energy counters from the previous poll, for instantaneous COP.
        self._last_cop_energies: dict[str, float] | None = None
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

        # External room temperature feed (Modbus holding 14). The EMA smooths
        # the configured HA sensor; `_external_room_*` fields are telemetry for
        # diagnostics only. The feed runs on its own timer, independent of the
        # poll cadence.
        self._external_room_ema_value: float | None = None
        self._external_room_ema_ts: float | None = None
        self._external_room_last_value: float | None = None
        self._external_room_last_write_ts: str | None = None
        self._external_room_write_errors: int = 0
        self._external_room_feed_unsub: Callable[[], None] | None = None

        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN} ({config_entry.unique_id})",
            update_method=self.async_update_data,
            update_interval=timedelta(seconds=self.poll_interval),
        )

        # Enabled metrics only change when the registries change (user toggles
        # an entity, a device appears, ...). The first poll runs before the
        # entity registration events, which immediately invalidate the entry.
        self._enabled_metrics_cache: dict[str, list[str]] = {}
        # Registry events are global; remember which ids belong to this entry
        # so a removal (already gone from the registry) can still be matched.
        self._known_entity_ids: set[str] = set()
        self._known_device_ids: set[str] = set()
        for event_type in (
            EVENT_ENTITY_REGISTRY_UPDATED,
            EVENT_DEVICE_REGISTRY_UPDATED,
        ):
            config_entry.async_on_unload(
                hass.bus.async_listen(event_type, self._handle_registry_updated)
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

    def _apply_rate_limit_backoff(self, retry_after: float | None) -> None:
        """Temporarily widen the poll interval while the API throttles us."""
        if not retry_after or retry_after <= self.poll_interval:
            return
        seconds = min(float(retry_after), _MAX_RATE_LIMIT_BACKOFF_SECONDS)
        current = getattr(self, "update_interval", None)
        current_seconds = current.total_seconds() if current else 0.0
        if current_seconds >= seconds:
            return
        self.update_interval = timedelta(seconds=seconds)
        _LOGGER.debug("Rate limited; backing off poll to %ss", seconds)

    def _restore_poll_interval(self) -> None:
        """Restore the configured interval after a throttled period."""
        desired = timedelta(seconds=self.poll_interval)
        if getattr(self, "update_interval", None) != desired:
            self.update_interval = desired

    async def async_set_extra_tap_water(self, device_id: str | int, minutes: int):
        """Write extra DHW and arm or clear the local restore timer."""
        return await async_apply_extra_tap_water(
            self.client, self.extra_dhw, device_id, minutes
        )

    async def async_write_metric(self, device_id: str, metric_key: str, value: Any):
        """Write a holding field by canonical name (Modbus only)."""
        if not isinstance(self.client, QvantumModbusClient):
            raise HomeAssistantError(
                f"{metric_key} writes require a Modbus client"
            )
        if not self.client.writable:
            raise HomeAssistantError(
                "Modbus writing is disabled. Turn on writing via Modbus in the integration options."
            )
        return await self.client.write_metric(device_id, metric_key, value)

    async def async_set_smartcontrol(self, device_id: str, sh: int, dhw: int):
        """Write SmartControl modes (cloud only)."""
        if not isinstance(self.client, QvantumCloudClient):
            raise HomeAssistantError("use_adaptive writes require a cloud client")
        return await self.client.set_smartcontrol(device_id, sh, dhw)

    async def async_elevate_access(self, device_id: str):
        """Elevate cloud write access. No-op when the transport is Modbus."""
        if not isinstance(self.client, QvantumCloudClient):
            return None
        return await self.client.elevate_access(device_id)

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
        if isinstance(data, dict):
            self._last_shower_cold_temp = self._coerce_store_number(
                data.get("cold_temp")
            )
            self._last_shower_flow_lpm = self._coerce_store_number(
                data.get("flow_lpm"), positive=True
            )
            self._last_shower_temp_c = self._coerce_store_number(
                data.get("shower_temp")
            )
            self._last_shower_duration_min = self._coerce_store_number(
                data.get("shower_duration"), positive=True
            )
            self._last_tap_water_cap = self._coerce_store_number(
                data.get("tap_water_cap")
            )
            self._last_published_tap_water_cap = self._coerce_store_number(
                data.get("published_cap")
            )
            self._last_published_tap_water_minutes = self._coerce_store_number(
                data.get("published_minutes")
            )
            _LOGGER.debug(
                "Restored DHW EMA state: cold=%.1f°C, flow=%.1f L/min, shower_temp=%.1f°C, dur=%.1f min, cap=%.2f showers",
                self._last_shower_cold_temp or 0.0,
                self._last_shower_flow_lpm or 0.0,
                self._last_shower_temp_c or 0.0,
                self._last_shower_duration_min or 0.0,
                self._last_tap_water_cap or 0.0,
            )

    @staticmethod
    def _coerce_store_number(
        value: object, *, positive: bool = False
    ) -> float | None:
        """Return a finite float from stored state, or None when unusable.

        A corrupted or hand-edited Store file must never break every poll:
        a zero shower duration used to raise ``ZeroDivisionError`` in the
        capacity calculation, and non-numeric values broke the EMA updates.
        """
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        number = float(value)
        if not math.isfinite(number):
            return None
        if positive and number <= 0:
            return None
        return number

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

        from .entity import _coordinator_config_entry_id

        entry_id = _coordinator_config_entry_id(self)
        if not entry_id:
            return None

        prefix = f"{DOMAIN}-"
        for ha_device in dr.async_entries_for_config_entry(registry, entry_id):
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
            if not isinstance(self.client, QvantumModbusClient):
                raise UpdateFailed("Modbus identity probe requires a Modbus client")
            try:
                probed = await self.client.probe_identity()
            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.warning("Failed to probe Modbus identity: %s", err)
                probed = None
            if isinstance(probed, dict) and probed.get("id"):
                self._device = probed
                # The probe just read registers 191-193; don't re-read until
                # the refresh interval elapses.
                self._sw_version_refreshed_at = time.monotonic()
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

        if not isinstance(self.client, QvantumCloudClient):
            raise UpdateFailed("Cloud device lookup requires a cloud client")
        try:
            device = await asyncio.wait_for(
                self.client.get_primary_device(),
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

    @property
    def external_room_temp_entity_id(self) -> str | None:
        """Return the configured external room temperature source sensor.

        None when the feed is disabled (option unset or cleared).
        """
        option = self._config_entry.options.get(CONF_EXTERNAL_ROOM_TEMP_ENTITY)
        if not isinstance(option, str):
            return None
        entity_id = option.strip()
        return entity_id or None

    @property
    def external_room_feed_interval(self) -> float | None:
        """Return the feed cadence in seconds, or None when disabled.

        The Qvantum app's external sensor mode treats the value as unavailable
        when it is older than ~5 minutes, so the cadence is capped at
        `EXTERNAL_ROOM_TEMP_MAX_FEED_INTERVAL` (240 s): a deliberately slow
        poll interval can never starve the feed.
        """
        if not self.modbus_enabled or self.external_room_temp_entity_id is None:
            return None
        if not bool(getattr(self.client, "writable", False)):
            return None
        return float(
            min(int(self.poll_interval), int(EXTERNAL_ROOM_TEMP_MAX_FEED_INTERVAL))
        )

    @callback
    def async_configure_external_room_feed(self) -> None:
        """Start, restart, or stop the external room temperature feed timer.

        Call after any change that affects `external_room_feed_interval`: the
        feed option, Modbus write access, or the poll interval.
        """
        self.async_cancel_external_room_feed()
        interval = self.external_room_feed_interval
        if interval is None:
            return
        self._external_room_feed_unsub = async_track_time_interval(
            self.hass,
            self._async_feed_external_room_temp,
            timedelta(seconds=interval),
            name=f"{DOMAIN} external room temperature",
        )
        _LOGGER.debug("External room temperature feed active every %ss", interval)

    @callback
    def async_cancel_external_room_feed(self) -> None:
        """Stop the external room temperature feed timer if it is running."""
        unsub = self._external_room_feed_unsub
        if unsub is None:
            return
        self._external_room_feed_unsub = None
        unsub()

    async def _async_feed_external_room_temp(
        self, _now: datetime | None = None
    ) -> None:
        """Feed the configured HA sensor to the external room temperature.

        The Qvantum app's external sensor mode expects a room temperature at
        least every ~5 minutes; the pump drops the sensor source and raises
        alarm 8 when the value goes stale. The feed runs on its own timer
        (`external_room_feed_interval`) instead of the poll, so a slow poll
        interval cannot starve it, and it writes even when the value is
        unchanged. It works while the pump is in any sensor mode, so the mode
        can be re-selected in the app at any time.

        Failures are logged and counted but never raise; the next tick retries.
        """
        device_id = self.device_id
        if not self.modbus_enabled or device_id is None:
            return
        entity_id = self.external_room_temp_entity_id
        if not entity_id or not self.client.writable:
            return

        state = self.hass.states.get(entity_id)
        if state is None:
            _LOGGER.debug(
                "External room temperature feed source %s is missing", entity_id
            )
            return
        try:
            sensor_value = float(state.state)
        except (TypeError, ValueError):
            _LOGGER.debug(
                "External room temperature feed source %s has non-numeric state %s",
                entity_id,
                state.state,
            )
            return
        if not math.isfinite(sensor_value):
            return

        now = time.monotonic()
        if self._external_room_ema_value is None or self._external_room_ema_ts is None:
            ema = sensor_value
        else:
            elapsed = max(0.0, now - self._external_room_ema_ts)
            alpha = 1.0 - math.exp(-elapsed / EXTERNAL_ROOM_TEMP_EMA_TAU)
            ema = self._external_room_ema_value + alpha * (
                sensor_value - self._external_room_ema_value
            )
        self._external_room_ema_value = ema
        self._external_room_ema_ts = now

        value = round(ema, 1)
        documented = metric_range("room_temp_external")
        if documented is not None:
            minimum, maximum = documented
            value = min(max(value, minimum), maximum)

        try:
            await self.async_write_metric(device_id, "room_temp_external", value)
        except (HomeAssistantError, APIConnectionError) as err:
            self._external_room_write_errors += 1
            _LOGGER.warning(
                "Failed to feed external room temperature %s to device %s: %s",
                value,
                device_id,
                err,
            )
            return

        self._external_room_last_value = value
        self._external_room_last_write_ts = dt_util.utcnow().isoformat()
        # Mirror into the last poll result (no listener notify: calling
        # async_set_updated_data would restart the poll schedule from the feed
        # timer). The next poll reads the value back from the pump anyway.
        if isinstance(self.data, dict):
            values = self.data.get("values")
            if isinstance(values, dict):
                values["room_temp_external"] = value

    def _get_enabled_metrics(self, device_id: str) -> list[str]:
        """Return enabled metrics for a device, cached until a registry changes."""
        cached = self._enabled_metrics_cache.get(device_id)
        if cached is not None:
            return list(cached)
        metrics = self._compute_enabled_metrics(device_id)
        self._enabled_metrics_cache[device_id] = metrics
        return list(metrics)

    @callback
    def _handle_registry_updated(self, event: Event) -> None:
        """Drop cached metrics only for this entry's registry changes."""
        if self._registry_event_affects_entry(event):
            self._invalidate_enabled_metrics_cache(event)

    def _registry_event_affects_entry(self, event: Event) -> bool:
        """Return True when a registry event touches this config entry.

        Entity and device registry events are global; without this filter
        every registry change in Home Assistant cleared the metrics cache.
        """
        data = event.data or {}
        entity_id = data.get("entity_id")
        if entity_id is not None:
            if entity_id in self._known_entity_ids:
                # Removed entities are gone from the registry, so a previously
                # seen id is the only way to recognize them.
                if data.get("action") == "remove":
                    self._known_entity_ids.discard(entity_id)
                return True
            entity_entry = er.async_get(self.hass).async_get(entity_id)
            if (
                entity_entry is None
                or entity_entry.config_entry_id != self.config_entry.entry_id
            ):
                return False
            self._known_entity_ids.add(entity_id)
            return True

        device_id = data.get("device_id")
        if device_id is None:
            return False
        if device_id in self._known_device_ids:
            if data.get("action") == "remove":
                self._known_device_ids.discard(device_id)
            return True
        device_entry = dr.async_get(self.hass).async_get(device_id)
        if (
            device_entry is None
            or self.config_entry.entry_id not in device_entry.config_entries
        ):
            return False
        self._known_device_ids.add(device_id)
        return True

    @callback
    def _invalidate_enabled_metrics_cache(self, _event: object = None) -> None:
        """Drop cached metrics after an entity or device registry change."""
        self._enabled_metrics_cache.clear()

    def _compute_enabled_metrics(self, device_id: str) -> list[str]:
        """Get list of enabled metrics for a device based on entity registry."""
        from .entity import (
            _coordinator_config_entry_id,
            async_get_qvantum_device_entry,
            extract_metric_key,
        )

        default_metrics = (
            DEFAULT_ENABLED_MODBUS_METRICS
            if self.modbus_enabled
            else DEFAULT_ENABLED_HTTP_METRICS
        )

        device_entry = async_get_qvantum_device_entry(
            self.hass,
            device_id,
            _coordinator_config_entry_id(self),
        )
        if device_entry:
            self._known_device_ids.add(device_entry.id)
            registry = er.async_get(self.hass)
            enabled_metrics = set()
            known_metrics = set()

            # Known metrics include the default metrics always.
            # HTTP-only disabled metrics are only known in HTTP mode.
            # Modbus disabled metrics are known in Modbus mode.
            allowed_metrics = set(default_metrics)
            if self.modbus_enabled:
                allowed_metrics |= set(DEFAULT_DISABLED_MODBUS_METRICS)
            else:
                allowed_metrics |= set(DEFAULT_DISABLED_HTTP_METRICS)

            for entity in er.async_entries_for_device(
                registry, device_entry.id, include_disabled_entities=True
            ):
                if entity.unique_id.startswith("qvantum_") and entity.unique_id.endswith(
                    f"_{device_id}"
                ):
                    metric_key = extract_metric_key(entity.unique_id, device_id)
                    self._known_entity_ids.add(entity.entity_id)

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
                        if default_metric_creates_entity(metric):
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

        alias_heating_curve_settings(settings_dict)
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

    async def _sync_modbus_extra_dhw_timer(
        self, values: dict[str, Any], *, poll_started: float
    ) -> None:
        """Cancel the HA extra-DHW restore timer when Modbus reports Extra is off.

        Extra DHW can be stopped on the heat pump (display or its own timeout)
        without going through ``set_extra_tap_water``. The local ``tap_stop``
        sensor is the restore deadline, so it must be cleared too.

        A poll that started before Extra was written must not cancel the timer
        just scheduled for that write.
        """
        timer = self.extra_dhw
        restore_at = getattr(timer, "restore_at", None)
        if not isinstance(restore_at, (int, float)):
            return
        extra = values.get("extra_tap_water")
        if extra is None or _is_extra_dhw_on(extra):
            values["tap_stop"] = int(restore_at)
            return
        armed_at = getattr(timer, "armed_at", None)
        if isinstance(armed_at, (int, float)) and armed_at > poll_started:
            values["tap_stop"] = int(restore_at)
            return
        await timer.async_clear()
        values.pop("tap_stop", None)
        _LOGGER.debug("Cleared extra-DHW restore timer; extra DHW is off")

    async def _refresh_modbus_sw_version(self) -> None:
        """Refresh the display firmware version from input registers 191-193.

        The version only changes on a display firmware update, so the identity
        island is re-probed at most once per interval. A failed read never
        fails the poll; the last known version stays on the device.
        """
        if not isinstance(self.client, QvantumModbusClientProtocol):
            return
        now = time.monotonic()
        refreshed_at = self._sw_version_refreshed_at
        if (
            refreshed_at is not None
            and now - refreshed_at < MODBUS_SW_VERSION_REFRESH_INTERVAL
        ):
            return
        self._sw_version_refreshed_at = now
        try:
            probed = await self.client.probe_identity()
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.debug(
                "Failed to refresh Modbus display firmware version: %s", err
            )
            return
        if not isinstance(probed, dict) or not isinstance(self._device, dict):
            return
        sw_version = probed.get("sw_version")
        if not sw_version or self._device.get("sw_version") == sw_version:
            return
        self._device["sw_version"] = sw_version
        await self._persist_device_state()
        await self._update_device_registry_sw_version(str(sw_version))

    async def _update_device_registry_sw_version(self, sw_version: str) -> None:
        """Write a refreshed Modbus display firmware version to the registry.

        Cloud firmware sync stays in the maintenance coordinator; Modbus only
        reports the display firmware.
        """
        try:
            from homeassistant.helpers import device_registry as dr

            from .entity import (
                _coordinator_config_entry_id,
                async_get_qvantum_device_entry,
            )

            device_id = self.device_id
            device_entry = async_get_qvantum_device_entry(
                self.hass, device_id, _coordinator_config_entry_id(self)
            )
            if device_entry is None or device_entry.sw_version == sw_version:
                return
            dr.async_get(self.hass).async_update_device(
                device_entry.id, sw_version=sw_version
            )
            _LOGGER.info(
                "Updated device registry firmware version for device %s to %s",
                device_id,
                sw_version,
            )
        except Exception as err:
            _LOGGER.debug(
                "Failed to update device registry firmware version: %s", err
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

            # Fetch metrics and settings concurrently for better performance.
            # Create explicit tasks so a failure in one cancels the other
            # instead of leaving an orphaned request running past the poll.
            poll_started = time.monotonic()
            metrics_task = asyncio.ensure_future(
                self.client.get_metrics(
                    device_id, enabled_metrics=enabled_metrics
                )
            )
            settings_task = asyncio.ensure_future(self.client.get_settings(device_id))
            try:
                data, settings = await asyncio.gather(metrics_task, settings_task)
            except BaseException:
                for task in (metrics_task, settings_task):
                    task.cancel()
                await asyncio.gather(
                    metrics_task, settings_task, return_exceptions=True
                )
                raise
            if (
                self.modbus_enabled
                and isinstance(data, dict)
                and isinstance(data.get("metrics"), dict)
            ):
                data["metrics"]["latency"] = int(
                    (time.monotonic() - poll_started) * 1000
                )

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

            if "active_alarms" in metrics_dict:
                metrics_dict["alarm_active"] = int(
                    metrics_dict["active_alarms"] > 0
                )

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
            alias_heating_curve_settings(values)

            if self.modbus_enabled:
                await self._sync_modbus_extra_dhw_timer(values, poll_started=poll_started)
                await self._refresh_modbus_sw_version()

            self._derive_tap_water_capacity(values)
            self._calculate_cop(values)

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

            # A successful poll clears any Retry-After backoff.
            self._restore_poll_interval()

            return result

        except APIAuthError as err:
            _LOGGER.error(
                "Authentication error for device %s: %s",
                self._logged_device_id(),
                err,
            )
            if self.modbus_enabled:
                raise UpdateFailed(f"Authentication failed: {err}") from err
            raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
        except APIConnectionError as err:
            _LOGGER.error(
                "Error communicating with API for device %s: %s",
                self._logged_device_id(),
                err,
            )
            raise UpdateFailed(f"Error communicating with API: {err}") from err
        except APIRateLimitError as err:
            # Cloud throttling is expected under load; warn without a traceback
            # instead of logging an unexpected error on every poll.
            self._apply_rate_limit_backoff(getattr(err, "retry_after", None))
            _LOGGER.warning(
                "Rate limit exceeded for device %s: %s",
                self._logged_device_id(),
                err,
            )
            raise UpdateFailed(f"Rate limit exceeded: {err}") from err
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
