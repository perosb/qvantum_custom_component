"""Qvantum API."""

import aiohttp
import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Optional

from modbus_connection import ModbusUnit

from .client.constants import (
    DHW_MODE_EXTRA,
    DHW_MODE_NORMAL,
    FAN_SPEED_STATE_EXTRA,
    FAN_SPEED_STATE_NORMAL,
    FAN_SPEED_STATE_OFF,
    FAN_SPEED_VALUE_EXTRA,
    FAN_SPEED_VALUE_NORMAL,
    FAN_SPEED_VALUE_OFF,
    TAP_WATER_CAPACITY_MAPPINGS,
)
from .client.exceptions import (
    APIAuthError,
    APIConnectionError,
    APIRateLimitError,
)
from .client.cloud import QvantumCloudClient
from .client.cloud.endpoints import (
    API_INTERNAL_URL,
    API_URL,
    AUTH_URL,
    DEFAULT_TOKEN_BUFFER_SECONDS,
    DEFAULT_TOKEN_EXPIRY_SECONDS,
    FIREBASE_API_KEY,
    METRICS_TIMEOUT_SECONDS,
    TOKEN_URL,
    VENTILATION_BOOST_MINUTES,
)
from .client.modbus import QvantumModbusClient
from .const import (
    DEFAULT_ENABLED_HTTP_METRICS,
    DEFAULT_ENABLED_MODBUS_METRICS,
)
from .extra_dhw import ExtraDhwTimer
from .modbus import MODBUS_HOLDING_REGISTER_MAP, MODBUS_HOLDING_TO_SETTINGS_MAP
from .modbus_device import QvantumModbusDevice


_LOGGER = logging.getLogger(__name__)

_CLOUD_ATTRS = frozenset(
    {
        "_api_url",
        "_auth_url",
        "_device_metadata",
        "_device_metadata_etag",
        "_metrics_data",
        "_metrics_etag",
        "_password",
        "_refreshtoken",
        "_session",
        "_session_owner",
        "_settings_data",
        "_settings_etag",
        "_token",
        "_token_expiry",
        "_token_url",
        "_user_agent",
        "_username",
    }
)


class QvantumAPI:
    """Class for Qvantum API."""

    def __init__(
        self,
        username: str | None = None,
        password: str | None = None,
        user_agent: str = "",
        session: Optional[aiohttp.ClientSession] = None,
        modbus_tcp: bool = False,
        modbus_host: str = "qvantum-hp",
        modbus_port: int = 502,
        modbus_unit_id: int = 1,
        modbus_unit: Optional[ModbusUnit] = None,
        modbus_write: bool = False,
    ) -> None:
        """Initialise."""
        self.hass = None
        self._modbus_tcp = modbus_tcp
        self._modbus_host = modbus_host
        self._modbus_port = modbus_port
        self._modbus_unit_id = modbus_unit_id
        self._modbus_unit = modbus_unit
        self._modbus_write_flag = bool(modbus_write)
        self._modbus_client = (
            QvantumModbusClient(modbus_unit, writable=self._modbus_write_flag)
            if modbus_tcp
            else None
        )
        self._cloud_client = (
            None
            if modbus_tcp
            else QvantumCloudClient(
                username, password, user_agent=user_agent, session=session
            )
        )
        self._fallback_lock = asyncio.Lock()
        self._closed = False
        self._extra_dhw = ExtraDhwTimer(self._write_dhw_normal)
        if modbus_tcp:
            self._session = None
            self._session_owner = False
            self._reset_state()

    def __getattr__(self, name: str):
        if name in _CLOUD_ATTRS:
            client = self.__dict__.get("_cloud_client")
            if client is not None:
                return getattr(client, name)
            if name == "_session_owner":
                return False
            if name in ("_settings_data", "_metrics_data", "_device_metadata"):
                return {}
            return None
        raise AttributeError(f"{type(self).__name__!r} has no attribute {name!r}")

    def __setattr__(self, name: str, value) -> None:
        if name in _CLOUD_ATTRS:
            client = self.__dict__.get("_cloud_client")
            if client is not None:
                setattr(client, name, value)
                return
        object.__setattr__(self, name, value)

    def _require_cloud(self) -> QvantumCloudClient:
        self._ensure_open()
        if self._cloud_client is None:
            raise APIConnectionError(
                None, "HTTP API is not available in Modbus mode"
            )
        return self._cloud_client

    def _reset_state(self):
        """Reset authentication and cached API data."""
        if self._cloud_client is not None:
            self._cloud_client._reset_state()
            return
        self._token = None
        self._refreshtoken = None
        self._token_expiry = None
        self._settings_data = {}
        self._settings_etag = None
        self._metrics_data = {}
        self._metrics_etag = None
        self._device_metadata = {}
        self._device_metadata_etag = None

    @property
    def _modbus_write(self) -> bool:
        return self._modbus_write_flag

    @_modbus_write.setter
    def _modbus_write(self, value: bool) -> None:
        self._modbus_write_flag = bool(value)
        if self._modbus_client is not None:
            self._modbus_client.writable = self._modbus_write_flag

    @property
    def _modbus_lock(self) -> asyncio.Lock:
        if self._modbus_client is not None:
            return self._modbus_client._lock
        return self._fallback_lock

    @property
    def _modbus_device(self) -> QvantumModbusDevice | None:
        if self._modbus_client is None:
            return None
        return self._modbus_client.device

    @_modbus_device.setter
    def _modbus_device(self, value: QvantumModbusDevice | None) -> None:
        if self._modbus_client is not None:
            self._modbus_client._device = value

    def _ensure_open(self) -> None:
        """Raise if the API client has been closed (e.g. during config reload)."""
        if self._closed:
            raise APIConnectionError(None, "API client is closed")

    def _sync_modbus_unit(self) -> None:
        """Attach a unit assigned after construct (tests) onto the client."""
        client = self._modbus_client
        if client is None or client.device is not None:
            return
        if self._modbus_unit is None:
            return
        client.attach_unit(self._modbus_unit)

    def _ensure_modbus_device(self) -> QvantumModbusDevice | None:
        """Return the device wrapper for the Home Assistant-owned Modbus unit."""
        if self._closed or self._modbus_client is None:
            return None
        self._sync_modbus_unit()
        return self._modbus_client._ensure_device()

    async def _reset_modbus_client(self):
        """Drop the device wrapper. The shared connection is owned by ``modbus``.

        Must only be called while holding ``_modbus_lock`` (or during final
        teardown after no more Modbus work can start).
        """
        self._modbus_device = None

    async def close(self):
        """Close HTTP session and stop Modbus use; reject further API use.

        Acquires the Modbus lock so an in-flight register read/write finishes
        before the wrapper is dropped. The TCP connection itself is owned by
        Home Assistant's ``modbus`` integration and is released with the entry.
        """
        if self._closed:
            return
        self._closed = True
        self._cancel_extra_dhw_timer()

        if self._modbus_client is not None:
            await self._modbus_client.close()
        if self._cloud_client is not None:
            await self._cloud_client.close()
            return

        # Only close the session if we created it; externally-provided sessions
        # should be closed by their owner.
        if getattr(self, "_session_owner", False) and self._session:
            try:
                await self._session.close()
            except asyncio.CancelledError:
                self._session = None
                raise
            except Exception as exc:
                _LOGGER.debug("Error closing HTTP session: %s", exc)
            self._session = None

    async def _run_modbus(
        self,
        operation,
        *,
        error_label: str,
        missing_client_message: str = "Modbus client not initialized",
        failure_prefix: str = "Modbus communication failed",
    ):
        """Run a Modbus device operation under the lock, mapping errors."""
        self._ensure_open()
        if self._modbus_client is None:
            raise APIConnectionError(None, missing_client_message)
        self._sync_modbus_unit()
        return await self._modbus_client._run(
            operation,
            error_label=error_label,
            missing_client_message=missing_client_message,
            failure_prefix=failure_prefix,
        )

    async def _read_modbus_metrics(self, device_id: str, enabled_metrics: list[str]):
        """Read metrics from Modbus TCP without injecting poll latency."""
        self._ensure_open()
        if self._modbus_client is None:
            raise APIConnectionError(None, "Modbus client not initialized")
        self._sync_modbus_unit()

        async def _update(device: QvantumModbusDevice):
            await device.async_update_inputs()
            payload = device.metrics_payload(device_id, enabled_metrics)
            _LOGGER.debug(
                "Raw Modbus metrics read: %s",
                sorted(payload.get("metrics", {}).items()),
            )
            return payload

        return await self._modbus_client._run(
            _update, error_label="reading input registers"
        )

    async def _read_modbus_settings(self, device_id: str, enabled_settings: list[str]):
        """Read settings from Modbus TCP holding registers."""
        self._ensure_open()
        if self._modbus_client is None:
            raise APIConnectionError(None, "Modbus client not initialized")
        self._sync_modbus_unit()

        async def _update(device: QvantumModbusDevice):
            await device.async_update_settings()
            return device.settings_payload(enabled_settings)

        return await self._modbus_client._run(
            _update, error_label="reading holding registers"
        )

    async def _handle_response(self, response: aiohttp.ClientResponse):
        """Handle API response, raising exceptions for errors."""
        return await self._require_cloud()._handle_response(response)

    async def unauthenticate(self):
        """Unauthenticate from the API."""
        self._reset_state()

    async def _request_json(
        self,
        method: str,
        url: str,
        payload: Optional[dict] = None,
        validate_status: bool = False,
    ) -> dict[str, Any]:
        """Send an authenticated request and return parsed JSON response."""
        return await self._require_cloud()._request_json(
            method, url, payload=payload, validate_status=validate_status
        )

    async def authenticate(self):
        """Authenticate with the API using username and password to retrieve a token."""
        return await self._require_cloud().authenticate()

    async def _refresh_authentication_token(self):
        """Refresh the authentication token."""
        return await self._require_cloud()._refresh_authentication_token()

    def _ensure_http(self) -> None:
        """Raise if this client has no HTTP session (Modbus-only mode)."""
        if self._cloud_client is None or self._session is None:
            raise APIConnectionError(
                None, "HTTP API is not available in Modbus mode"
            )

    async def async_probe_identity(self) -> dict[str, Any]:
        """Read serial and firmware from the heat pump over Modbus."""
        self._ensure_open()
        if self._modbus_client is None:
            raise APIConnectionError(None, "Modbus client not initialized")
        self._sync_modbus_unit()
        return await self._modbus_client.probe_identity()

    async def _ensure_valid_token(self):
        """Ensure a valid token is available, refreshing if expired."""
        self._ensure_open()
        self._ensure_http()
        if not self._token or datetime.now() >= self._token_expiry:
            try:
                await self._refresh_authentication_token()
                if not self._token:
                    await self.authenticate()
                    if not self._token:
                        raise APIAuthError(
                            None, "Failed to obtain authentication token"
                        )
            except APIAuthError:
                await self.authenticate()
                if not self._token:
                    raise APIAuthError(None, "Failed to obtain authentication token")

    def _request_headers(self):
        """Get request headers for API calls."""
        return self._require_cloud()._request_headers()

    @property
    def _extra_dhw_restore_at(self) -> float | None:
        return self._extra_dhw.restore_at

    @_extra_dhw_restore_at.setter
    def _extra_dhw_restore_at(self, value: float | None) -> None:
        self._extra_dhw.restore_at = value

    @property
    def _extra_dhw_armed_at(self) -> float | None:
        return self._extra_dhw.armed_at

    @_extra_dhw_armed_at.setter
    def _extra_dhw_armed_at(self, value: float | None) -> None:
        self._extra_dhw.armed_at = value

    @property
    def _extra_dhw_unsub(self):
        return self._extra_dhw.unsub

    @_extra_dhw_unsub.setter
    def _extra_dhw_unsub(self, value) -> None:
        self._extra_dhw.unsub = value

    @property
    def _extra_dhw_store(self):
        return self._extra_dhw.store

    @_extra_dhw_store.setter
    def _extra_dhw_store(self, value) -> None:
        self._extra_dhw.store = value

    def _sync_extra_dhw_hass(self) -> ExtraDhwTimer:
        self._extra_dhw.hass = self.hass
        return self._extra_dhw

    async def _write_dhw_normal(self, device_id: str) -> dict:
        return await self.write_holding_register_for_metric(
            device_id, "extra_tap_water", DHW_MODE_NORMAL
        )

    def _cancel_extra_dhw_timer(self, *, clear_store: bool = False) -> None:
        """Cancel a pending extra-DHW restore callback."""
        self._sync_extra_dhw_hass().cancel(clear_store=clear_store)

    async def async_clear_extra_dhw_timer(self) -> None:
        """Stop a pending extra-DHW restore because extra DHW is no longer active."""
        await self._sync_extra_dhw_hass().async_clear()

    async def async_persist_extra_dhw(self, payload: dict | None) -> None:
        """Save or clear the extra-DHW restore deadline."""
        await self._sync_extra_dhw_hass().async_persist(payload)

    def _persist_extra_dhw(self, payload: dict | None) -> None:
        """Fire-and-forget persist for sync callers (options listener)."""
        self._sync_extra_dhw_hass()._persist(payload)

    async def _schedule_extra_dhw_restore(self, device_id: str, minutes: int) -> None:
        """After *minutes*, write DHW mode back to Normal."""
        await self._sync_extra_dhw_hass().async_schedule(device_id, minutes)

    async def _schedule_extra_dhw_at(
        self, device_id: str, restore_at: float, *, persist: bool
    ) -> None:
        """Schedule restore at an absolute UTC epoch; persist when requested."""
        await self._sync_extra_dhw_hass().async_schedule_at(
            device_id, restore_at, persist=persist
        )

    async def async_restore_extra_dhw_timer(self) -> None:
        """Resume a persisted extra-DHW restore after Home Assistant restart."""
        if not self._modbus_tcp:
            return
        await self._sync_extra_dhw_hass().async_restore(writable=self._modbus_write)

    def _ensure_modbus_write_allowed(self) -> None:
        """Raise when Modbus TCP is on but holding-register writes are disabled."""
        if self._modbus_tcp and not self._modbus_write:
            raise APIConnectionError(None, "Modbus writing is disabled")

    async def update_setting(self, device_id: str, name: str, value: Any):
        """Update one setting."""
        if self._modbus_tcp:
            if isinstance(value, bool):
                value = int(value)
            return await self.write_holding_register_for_metric(
                device_id, name, value
            )

        payload = {"update_settings": {name: value}}

        return await self._send_command(device_id, payload)

    async def update_settings(self, device_id: str, settings: dict):
        """Update multiple settings from a dictionary."""

        payload = {"update_settings": settings}

        return await self._send_command(device_id, payload)

    async def write_holding_register(
        self, device_id: str, register_address: int, value: int
    ) -> dict:
        """Write a single Modbus holding register and return a status dict."""
        self._ensure_open()
        if self._modbus_client is None:
            raise APIConnectionError(
                None, f"Modbus client not initialized for device {device_id}"
            )
        self._sync_modbus_unit()
        return await self._modbus_client.write_holding_register(
            device_id, register_address, value
        )

    async def write_holding_register_for_metric(
        self, device_id: str, metric_key: str, value: float
    ) -> dict:
        """Write a Modbus holding register looked up by metric key."""
        self._ensure_open()
        if self._modbus_client is None:
            raise APIConnectionError(
                None, f"Modbus client not initialized for device {device_id}"
            )
        self._sync_modbus_unit()
        return await self._modbus_client.write_metric(device_id, metric_key, value)

    async def _update_settings(self, device_id: str, payload: dict):
        """Update one or several settings."""
        return await self._require_cloud()._update_settings(device_id, payload)

    async def _send_command(self, device_id: str, payload: dict):
        """Send a command to a device."""
        return await self._require_cloud()._send_command(device_id, payload)

    async def elevate_access(self, device_id: str):
        """Elevate access for a device."""
        return await self._require_cloud().elevate_access(device_id)

    async def get_access_level(self, device_id: str):
        """Get current access level for a device."""
        return await self._require_cloud().get_access_level(device_id)

    async def _generate_code(self, device_id: str):
        return await self._require_cloud()._generate_code(device_id)

    async def _claim_grant(self, device_id: str, access_code: str):
        return await self._require_cloud()._claim_grant(device_id, access_code)

    async def _approve_access(self, device_id: str, access_code: str):
        return await self._require_cloud()._approve_access(device_id, access_code)

    async def set_smartcontrol(self, device_id: str, sh: int, dhw: int):
        """Update smartcontrol setting."""

        use_adaptive = sh != -1 and dhw != -1
        if not use_adaptive:
            payload = {
                "use_adaptive": False,
            }
        else:
            payload = {
                "use_adaptive": use_adaptive,
                "smart_sh_mode": sh,
                "smart_dhw_mode": dhw,
            }

        return await self.update_settings(device_id, payload)

    async def set_extra_tap_water(self, device_id: str, minutes: int):
        """Update extra_tap_water setting."""
        if self._modbus_tcp:
            if minutes > 0:
                result = await self.write_holding_register_for_metric(
                    device_id, "extra_tap_water", DHW_MODE_EXTRA
                )
                await self._schedule_extra_dhw_restore(device_id, minutes)
                return result
            # Off or indefinite: write first so a failed write keeps the
            # persisted timed restore for the next restart.
            if minutes == 0:
                result = await self.write_holding_register_for_metric(
                    device_id, "extra_tap_water", DHW_MODE_NORMAL
                )
            else:
                result = await self.write_holding_register_for_metric(
                    device_id, "extra_tap_water", DHW_MODE_EXTRA
                )
            await self.async_clear_extra_dhw_timer()
            return result

        # Capture current time once to ensure consistency across all code paths
        current_time = datetime.now()

        if minutes == 0:
            # Cancel extra tap water
            stop_time = int(current_time.timestamp())
            indefinite = False
            cancel = True
        elif minutes > 0:
            # Set specific duration
            stop_time = int((current_time + timedelta(minutes=minutes)).timestamp())
            indefinite = False
            cancel = False
        else:
            # Set indefinite (always on)
            stop_time = -1
            indefinite = True
            cancel = False

        payload = {
            "set_additional_hot_water": {
                "stopTime": stop_time,
                "indefinite": indefinite,
                "cancel": cancel,
            }
        }

        return await self._send_command(device_id, payload)

    async def set_indoor_temperature_offset(self, device_id: str, value: int):
        """Update indoor_temperature_offset setting."""
        if self._modbus_tcp:
            return await self.write_holding_register_for_metric(
                device_id, "indoor_temperature_offset", value
            )

        payload = {"settings": [{"name": "indoor_temperature_offset", "value": value}]}

        return await self._update_settings(device_id, payload)

    async def set_fanspeedselector(self, device_id: str, preset_mode: str):
        """Update set_fanspeedselector setting."""
        if self._modbus_tcp:
            presets = {
                FAN_SPEED_STATE_OFF: FAN_SPEED_VALUE_OFF,
                FAN_SPEED_STATE_NORMAL: FAN_SPEED_VALUE_NORMAL,
                FAN_SPEED_STATE_EXTRA: FAN_SPEED_VALUE_EXTRA,
            }
            if preset_mode not in presets:
                raise ValueError(f"Invalid preset_mode: {preset_mode}")
            return await self.write_holding_register_for_metric(
                device_id, "fanspeedselector", presets[preset_mode]
            )

        # Capture current time once to ensure consistency across all code paths
        current_time = datetime.now()

        match preset_mode:
            case "off":
                payload = {"set_fan_mode": {"mode": 0}}
            case "normal":
                stop_time = int(current_time.timestamp())
                indefinite = False
                payload = {
                    "set_fan_mode": {"stopTime": stop_time, "indefinite": indefinite}
                }
            case "extra":
                stop_time = int(
                    (
                        current_time + timedelta(minutes=VENTILATION_BOOST_MINUTES)
                    ).timestamp()
                )
                indefinite = False
                payload = {
                    "set_fan_mode": {"stopTime": stop_time, "indefinite": indefinite}
                }
            case _:
                raise ValueError(f"Invalid preset_mode: {preset_mode}")

        return await self._send_command(device_id, payload)

    async def set_tap_water_capacity_target(self, device_id: str, capacity: int):
        """Update tap_water_capacity_target setting."""

        # Capacities 1, 6, and 7 are "custom" levels that the API does not accept
        # directly — they must be set by writing the corresponding stop/start temperatures.
        # Modbus has no capacity register; always write the start/stop pair.
        _CUSTOM_CAPACITIES = {1, 6, 7}

        if self._modbus_tcp or capacity in _CUSTOM_CAPACITIES:
            capacity_to_stop_start = {
                v: k for k, v in TAP_WATER_CAPACITY_MAPPINGS.items()
            }
            start, stop = capacity_to_stop_start[capacity]
            _LOGGER.debug(
                "Setting tap water capacity %s maps to stop %s and start %s.",
                capacity,
                stop,
                start,
            )
            return await self.set_tap_water(device_id, start=start, stop=stop)

        payload = {
            "settings": [{"name": "tap_water_capacity_target", "value": capacity}]
        }

        _LOGGER.debug("Setting tap water capacity target to %s.", capacity)
        return await self._update_settings(device_id, payload)

    async def set_tap_water(self, device_id: str, start: int = 0, stop: int = 0):
        """Update tap_water_start and tap_water_stop settings."""

        if stop == 0 and start == 0:
            _LOGGER.debug("No tap water settings to update, both stop and start are 0.")
            return

        if self._modbus_tcp:
            if stop:
                await self.write_holding_register_for_metric(
                    device_id, "tap_water_stop", stop
                )
            if start:
                await self.write_holding_register_for_metric(
                    device_id, "tap_water_start", start
                )
            return {"status": "APPLIED"}

        payload = {"settings": []}

        if stop:
            payload["settings"].append({"name": "tap_water_stop", "value": stop})
        if start:
            payload["settings"].append({"name": "tap_water_start", "value": start})

        return await self._update_settings(device_id, payload)

    async def set_indoor_temperature_target(self, device_id: str, temperature: float):
        """Update indoor_temperature_target setting."""
        if self._modbus_tcp:
            return await self.write_holding_register_for_metric(
                device_id, "indoor_temperature_target", temperature
            )

        payload = {
            "settings": [{"name": "indoor_temperature_target", "value": temperature}]
        }

        return await self._update_settings(device_id, payload)

    async def get_device_metadata(self, device_id: str):
        """Fetch data from the API with authentication."""
        return await self._require_cloud().get_device_metadata(device_id)

    async def get_metrics(
        self, device_id: str, method="now", enabled_metrics: Optional[list[str]] = None
    ):
        """Fetch data from the API or Modbus with authentication."""
        self._ensure_open()

        names = (
            enabled_metrics
            if enabled_metrics is not None
            else (
                DEFAULT_ENABLED_MODBUS_METRICS
                if self._modbus_tcp
                else DEFAULT_ENABLED_HTTP_METRICS
            )
        )

        if self._modbus_tcp:
            modbus_start = asyncio.get_running_loop().time()
            self._metrics_data = await self._read_modbus_metrics(device_id, names)
            modbus_latency = int(
                (asyncio.get_running_loop().time() - modbus_start) * 1000
            )
            if (
                isinstance(self._metrics_data, dict)
                and "metrics" in self._metrics_data
            ):
                self._metrics_data["metrics"]["latency"] = modbus_latency
            return self._metrics_data

        # HTTP cloud mode.
        self._metrics_data = await self._require_cloud().get_metrics(device_id, names)
        return self._metrics_data

    async def get_http_metrics(self, device_id: str, metric_names: list[str]) -> dict:
        """Fetch specific metrics from the HTTP API, bypassing Modbus."""
        return await self._require_cloud().get_http_metrics(device_id, metric_names)

    async def _get_http_values(
        self,
        device_id: str,
        metric_names: list[str],
        etag_header: Optional[str] = None,
    ) -> tuple[dict | None, str | None, int | None]:
        """Perform a raw HTTP values fetch and return (values_dict, etag, total_latency)."""
        return await self._require_cloud()._get_http_values(
            device_id, metric_names, etag_header=etag_header
        )

    async def get_settings(self, device_id: str):
        """Fetch settings from the API or Modbus."""
        self._ensure_open()

        if self._modbus_tcp:
            settings_to_read = [
                setting_key
                for setting_key in MODBUS_HOLDING_TO_SETTINGS_MAP
                if setting_key in MODBUS_HOLDING_REGISTER_MAP
            ]
            return await self._read_modbus_settings(device_id, settings_to_read)

        return await self._require_cloud().get_settings(device_id)

    async def get_primary_device(self):
        """Fetch device from the API with authentication."""
        return await self._require_cloud().get_primary_device()

    async def get_devices(self):
        """Fetch devices from the API with authentication."""
        return await self._require_cloud().get_devices()
