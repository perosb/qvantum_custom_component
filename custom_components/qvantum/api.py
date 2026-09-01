"""Qvantum API."""

import aiohttp
import asyncio
import json
from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Optional

from modbus_connection import ModbusError, ModbusUnit

from .const import (
    DEFAULT_ENABLED_HTTP_METRICS,
    DEFAULT_ENABLED_MODBUS_METRICS,
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
from .modbus import MODBUS_HOLDING_REGISTER_MAP, MODBUS_HOLDING_TO_SETTINGS_MAP
from .modbus_device import (
    IdentityProbeError,
    QvantumModbusDevice,
    holding_field_for_metric,
)


_LOGGER = logging.getLogger(__name__)

# API Configuration
AUTH_URL = "https://identitytoolkit.googleapis.com"
TOKEN_URL = "https://securetoken.googleapis.com"
API_URL = "https://api.qvantum.com"
API_INTERNAL_URL = "https://internal-api.qvantum.com"

# Timeouts and buffers
DEFAULT_TOKEN_BUFFER_SECONDS = 60
DEFAULT_TOKEN_EXPIRY_SECONDS = 3540
METRICS_TIMEOUT_SECONDS = 12
VENTILATION_BOOST_MINUTES = 120

# Firebase API Key (consider moving to config if needed)
FIREBASE_API_KEY = "AIzaSyCLQ22XHjH8LmId-PB1DY8FBsN53rWTpFw"


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
        self._auth_url = AUTH_URL
        self._token_url = TOKEN_URL
        self._api_url = API_URL
        self._username = username
        self._password = password
        self._user_agent = user_agent
        self.hass = None
        self._modbus_tcp = modbus_tcp
        self._modbus_host = modbus_host
        self._modbus_port = modbus_port
        self._modbus_unit_id = modbus_unit_id
        self._modbus_unit = modbus_unit
        self._modbus_write = bool(modbus_write)
        self._modbus_device: QvantumModbusDevice | None = None
        self._modbus_lock = asyncio.Lock()
        self._closed = False
        self._extra_dhw_unsub = None
        self._extra_dhw_restore_at: float | None = None
        self._extra_dhw_store = None
        # Modbus-only mode never opens an HTTP session, even if a caller
        # injects one. Cloud mode owns a session unless a test injects it.
        if modbus_tcp:
            self._session = None
            self._session_owner = False
        elif session is not None:
            self._session = session
            self._session_owner = False
        else:
            self._session = aiohttp.ClientSession(
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": self._user_agent,
                }
            )
            self._session_owner = True
        self._reset_state()

    def _reset_state(self):
        """Reset authentication and cached API data."""
        self._token = None
        self._refreshtoken = None
        self._token_expiry = None
        self._settings_data = {}
        self._settings_etag = None
        self._metrics_data = {}
        self._metrics_etag = None
        self._device_metadata = {}
        self._device_metadata_etag = None

    def _ensure_open(self) -> None:
        """Raise if the API client has been closed (e.g. during config reload)."""
        if self._closed:
            raise APIConnectionError(None, "API client is closed")

    def _ensure_modbus_device(self) -> QvantumModbusDevice | None:
        """Return the device wrapper for the Home Assistant-owned Modbus unit."""
        if self._closed:
            return None
        if not self._modbus_tcp:
            return None
        if self._modbus_device is None:
            if self._modbus_unit is None:
                return None
            self._modbus_device = QvantumModbusDevice(self._modbus_unit)
        return self._modbus_device

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

        # Wait for any in-flight Modbus operation, then drop the wrapper.
        async with self._modbus_lock:
            await self._reset_modbus_client()

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
        async with self._modbus_lock:
            self._ensure_open()
            device = self._ensure_modbus_device()
            if not device:
                raise APIConnectionError(None, missing_client_message)
            try:
                return await operation(device)
            except asyncio.CancelledError:
                # Reload/unload cancels in-flight polls. Do not close or
                # disconnect the shared connection; other consumers may hold it.
                raise
            except ModbusError as err:
                _LOGGER.error("Modbus error %s: %s", error_label, err)
                raise APIConnectionError(None, f"{failure_prefix}: {err}") from err
            except (APIConnectionError, ValueError, IdentityProbeError):
                raise
            except Exception as err:
                _LOGGER.error(
                    "Unexpected error %s: %s", error_label, err, exc_info=True
                )
                raise APIConnectionError(None, f"{failure_prefix}: {err}") from err

    async def _read_modbus_metrics(self, device_id: str, enabled_metrics: list[str]):
        """Read metrics from Modbus TCP."""

        async def _update(device: QvantumModbusDevice):
            await device.async_update_inputs()
            payload = device.metrics_payload(device_id, enabled_metrics)
            _LOGGER.debug(
                "Raw Modbus metrics read: %s",
                sorted(payload.get("metrics", {}).items()),
            )
            return payload

        return await self._run_modbus(_update, error_label="reading input registers")

    async def _read_modbus_settings(self, device_id: str, enabled_settings: list[str]):
        """Read settings from Modbus TCP holding registers."""

        async def _update(device: QvantumModbusDevice):
            await device.async_update_settings()
            return device.settings_payload(enabled_settings)

        return await self._run_modbus(_update, error_label="reading holding registers")

    async def _handle_response(self, response: aiohttp.ClientResponse):
        """Handle API response, raising exceptions for errors."""
        if not response.ok:
            if response.status == 401:
                raise APIAuthError(response)
            elif response.status == 429:
                raise APIRateLimitError(response)
            else:
                raise APIConnectionError(response)

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
        """Send an authenticated request and return parsed JSON response.

        By default, this helper does not validate HTTP status codes and will try
        to parse JSON regardless of response status. Set ``validate_status=True``
        to enforce ``_handle_response`` before reading the response body.
        """
        await self._ensure_valid_token()
        request = getattr(self._session, method)
        kwargs: dict[str, Any] = {"headers": self._request_headers()}
        if payload is not None:
            kwargs["json"] = payload

        async with request(url, **kwargs) as response:
            if validate_status:
                await self._handle_response(response)
            data = await response.json()
            _LOGGER.debug("Response received %s: %s", response.status, data)
            return data

    async def authenticate(self):
        """Authenticate with the API using username and password to retrieve a token."""
        self._ensure_open()
        self._ensure_http()
        payload = {
            "returnSecureToken": "true",
            "email": self._username,
            "password": self._password,
            "clientType": "CLIENT_TYPE_WEB",
        }

        async with self._session.post(
            f"{self._auth_url}/v1/accounts:signInWithPassword?key={FIREBASE_API_KEY}",
            json=payload,
        ) as response:
            match response.status:
                case 200:
                    _LOGGER.debug("Authentication successful: %s", response.status)
                    auth_data = await response.json()
                    self._token = auth_data.get("idToken")
                    self._refreshtoken = auth_data.get("refreshToken")
                    expires_in = auth_data.get(
                        "expiresIn", DEFAULT_TOKEN_EXPIRY_SECONDS
                    )
                    self._token_expiry = datetime.now() + timedelta(
                        seconds=int(expires_in) - DEFAULT_TOKEN_BUFFER_SECONDS
                    )
                    return True
                case _:
                    _LOGGER.error("Authentication failed: %s", response.status)
                    raise APIAuthError(response)

    async def _refresh_authentication_token(self):
        """Refresh the authentication token."""
        self._ensure_open()

        if not self._refreshtoken:
            return

        payload = {"grant_type": "refresh_token", "refresh_token": self._refreshtoken}

        self._token = None

        async with self._session.post(
            f"{self._token_url}/v1/token?key={FIREBASE_API_KEY}",
            json=payload,
        ) as response:
            match response.status:
                case 200:
                    _LOGGER.debug("Token refreshed successfully: %s", response.status)
                    auth_data = await response.json()
                    self._token = auth_data.get("access_token")
                    self._refreshtoken = auth_data.get("refresh_token")
                    expires_in = auth_data.get(
                        "expires_in", DEFAULT_TOKEN_EXPIRY_SECONDS
                    )
                    self._token_expiry = datetime.now() + timedelta(
                        seconds=int(expires_in) - DEFAULT_TOKEN_BUFFER_SECONDS
                    )
                case _:
                    _LOGGER.error("Token refresh failed: %s", response.status)
                    # Don't raise exception here, let _ensure_valid_token handle it

    def _ensure_http(self) -> None:
        """Raise if this client has no HTTP session (Modbus-only mode)."""
        if self._session is None:
            raise APIConnectionError(
                None, "HTTP API is not available in Modbus mode"
            )

    async def async_probe_identity(self) -> dict[str, Any]:
        """Read serial and firmware from the heat pump over Modbus."""

        async def _probe(device: QvantumModbusDevice):
            await device.async_update_identity()
            serial = device.serial_number
            if not serial:
                raise IdentityProbeError("Heat pump did not return a serial number")
            return {
                "id": serial,
                "serial": serial,
                "vendor": "Qvantum",
                "sw_version": device.sw_version,
            }

        return await self._run_modbus(_probe, error_label="probing identity")

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
                # If refresh fails, try fresh authentication
                await self.authenticate()
                if not self._token:
                    raise APIAuthError(None, "Failed to obtain authentication token")

    def _request_headers(self):
        """Get request headers for API calls."""
        return {
            "Authorization": f"Bearer {self._token}",
        }

    def _cancel_extra_dhw_timer(self, *, clear_store: bool = False) -> None:
        """Cancel a pending extra-DHW restore callback."""
        unsub = self._extra_dhw_unsub
        self._extra_dhw_unsub = None
        if unsub:
            unsub()
        if clear_store:
            self._extra_dhw_restore_at = None
            self._persist_extra_dhw(None)

    async def async_persist_extra_dhw(self, payload: dict | None) -> None:
        """Save or clear the extra-DHW restore deadline."""
        store = self._extra_dhw_store
        if store is None:
            return
        try:
            if payload is None:
                await store.async_remove()
            else:
                await store.async_save(payload)
        except Exception:
            if payload is None:
                _LOGGER.debug("Failed to clear extra DHW timer", exc_info=True)
            else:
                _LOGGER.debug("Failed to persist extra DHW timer", exc_info=True)

    def _persist_extra_dhw(self, payload: dict | None) -> None:
        """Fire-and-forget persist for sync callers (options listener)."""
        hass = self.hass
        if self._extra_dhw_store is None or hass is None:
            return
        create_task = getattr(hass, "async_create_task", None)
        if callable(create_task):
            create_task(
                self.async_persist_extra_dhw(payload),
                name="qvantum_persist_extra_dhw",
            )

    async def _schedule_extra_dhw_restore(self, device_id: str, minutes: int) -> None:
        """After *minutes*, write DHW mode back to Normal."""
        if minutes <= 0:
            return
        restore_at = datetime.now(timezone.utc).timestamp() + minutes * 60
        await self._schedule_extra_dhw_at(device_id, restore_at, persist=True)

    async def _schedule_extra_dhw_at(
        self, device_id: str, restore_at: float, *, persist: bool
    ) -> None:
        """Schedule restore at an absolute UTC epoch; persist when requested."""
        self._cancel_extra_dhw_timer(clear_store=False)
        remaining = restore_at - datetime.now(timezone.utc).timestamp()
        if not self.hass:
            return
        self._extra_dhw_restore_at = restore_at
        if persist:
            await self.async_persist_extra_dhw(
                {"device_id": str(device_id), "restore_at": restore_at}
            )
        from homeassistant.helpers.event import async_call_later

        async def _restore(_now) -> None:
            self._extra_dhw_unsub = None
            try:
                await self.write_holding_register_for_metric(
                    device_id, "extra_tap_water", DHW_MODE_NORMAL
                )
            except Exception as err:
                _LOGGER.warning(
                    "Failed to restore DHW mode after extra hot water timer: %s", err
                )
                return
            self._extra_dhw_restore_at = None
            try:
                await self.async_persist_extra_dhw(None)
            except Exception:
                _LOGGER.debug("Failed to clear extra DHW timer", exc_info=True)

        delay = max(remaining, 0)
        self._extra_dhw_unsub = async_call_later(self.hass, delay, _restore)

    async def async_restore_extra_dhw_timer(self) -> None:
        """Resume a persisted extra-DHW restore after Home Assistant restart."""
        if not self._modbus_tcp:
            return
        if not self._modbus_write:
            # Writes off: do not reschedule, and drop any saved deadline.
            await self.async_persist_extra_dhw(None)
            return
        store = self._extra_dhw_store
        if store is None:
            return
        try:
            data = await store.async_load()
        except Exception:
            _LOGGER.debug("Failed to load extra DHW timer", exc_info=True)
            return
        if not isinstance(data, dict):
            return
        device_id = data.get("device_id")
        restore_at = data.get("restore_at")
        if not device_id or not isinstance(restore_at, (int, float)):
            return
        remaining = float(restore_at) - datetime.now(timezone.utc).timestamp()
        if remaining <= 0:
            self._extra_dhw_restore_at = float(restore_at)
            try:
                await self.write_holding_register_for_metric(
                    device_id, "extra_tap_water", DHW_MODE_NORMAL
                )
            except Exception as err:
                _LOGGER.warning(
                    "Failed to restore DHW mode after extra hot water timer: %s", err
                )
                return
            self._extra_dhw_restore_at = None
            try:
                await self.async_persist_extra_dhw(None)
            except Exception:
                _LOGGER.debug("Failed to clear extra DHW timer", exc_info=True)
            return
        await self._schedule_extra_dhw_at(str(device_id), float(restore_at), persist=False)

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
        self._ensure_modbus_write_allowed()

        async def _write(device: QvantumModbusDevice):
            await device.write_holding_register(register_address, int(value))
            return {"status": "APPLIED"}

        return await self._run_modbus(
            _write,
            error_label=f"writing holding register {register_address} for device {device_id}",
            missing_client_message=f"Modbus client not initialized for device {device_id}",
            failure_prefix="Modbus write failed",
        )

    async def write_holding_register_for_metric(
        self, device_id: str, metric_key: str, value: float
    ) -> dict:
        """Write a Modbus holding register looked up by metric key."""
        self._ensure_modbus_write_allowed()
        holding_field_for_metric(metric_key)

        async def _write(device: QvantumModbusDevice):
            await device.write_metric(metric_key, value)
            return {"status": "APPLIED"}

        return await self._run_modbus(
            _write,
            error_label=f"writing metric {metric_key} for device {device_id}",
            missing_client_message=f"Modbus client not initialized for device {device_id}",
            failure_prefix="Modbus write failed",
        )

    async def _update_settings(self, device_id: str, payload: dict):
        """Update one or several settings."""

        _LOGGER.debug(json.dumps(payload))
        return await self._request_json(
            "patch",
            f"{self._api_url}/api/device-info/v1/devices/{device_id}/settings?dispatch=false",
            payload,
        )

    async def _send_command(self, device_id: str, payload: dict):
        """Send a command to a device."""

        wrapped_payload = {"command": payload}
        _LOGGER.debug(json.dumps(wrapped_payload))
        return await self._request_json(
            "post",
            f"{self._api_url}/api/commands/v1/devices/{device_id}/commands?wait=true&use_internal_names=true",
            wrapped_payload,
        )

    async def elevate_access(self, device_id: str):
        """Elevate access for a device."""

        await self._ensure_valid_token()

        async with self._session.get(
            f"{API_INTERNAL_URL}/api/internal/v1/auth/device/{device_id}/my-access-level?use_internal_names=true",
            headers=self._request_headers(),
        ) as response:
            await self._handle_response(response)
            data = await response.json()
            _LOGGER.debug("Response received %s: %s", response.status, data)

            expires_at = data.get("expiresAt")
            has_sufficient_access = data.get("writeAccessLevel", 0) >= 20
            if not has_sufficient_access and expires_at:
                try:
                    expires_at_dt = datetime.fromisoformat(
                        expires_at.replace("Z", "+00:00")
                    )
                    if expires_at_dt < datetime.now(timezone.utc) + timedelta(days=1):
                        has_sufficient_access = True
                except ValueError:
                    pass
            if has_sufficient_access:
                return data

            # Access insufficient, elevate it
            code_data = await self._generate_code(device_id)
            if not code_data:
                return None
            access_code = code_data.get("accessCode")
            if not access_code:
                return None

            claim_status = await self._claim_grant(device_id, access_code)
            if not claim_status:
                return None

            approve_status = await self._approve_access(device_id, access_code)
            if not approve_status:
                return None

            # Get updated access level
            async with self._session.get(
                f"{API_INTERNAL_URL}/api/internal/v1/auth/device/{device_id}/my-access-level?use_internal_names=true",
                headers=self._request_headers(),
            ) as response:
                await self._handle_response(response)
                data = await response.json()
                _LOGGER.debug("Response received %s: %s", response.status, data)
                return data

    async def get_access_level(self, device_id: str):
        """Get current access level for a device."""

        await self._ensure_valid_token()

        async with self._session.get(
            f"{API_INTERNAL_URL}/api/internal/v1/auth/device/{device_id}/my-access-level?use_internal_names=true",
            headers=self._request_headers(),
        ) as response:
            await self._handle_response(response)
            data = await response.json()
            _LOGGER.debug(
                "Access level response received %s: %s", response.status, data
            )
            return data

    async def _generate_code(self, device_id: str):
        """Generate an access code for a device."""

        await self._ensure_valid_token()

        async with self._session.post(
            f"{API_INTERNAL_URL}/api/internal/v1/auth/device/{device_id}/generate-access-code?use_internal_names=true",
            headers=self._request_headers(),
        ) as response:
            if response.ok:
                data = await response.json()
                _LOGGER.debug("Response received %s: %s", response.status, data)
                return data
            else:
                _LOGGER.error(
                    "Failed to generate access code for device %s, status: %s",
                    device_id,
                    response.status,
                )
                return None

    async def _claim_grant(self, device_id: str, access_code: str):
        """Claim a grant for a device."""

        await self._ensure_valid_token()

        _LOGGER.debug(
            "Claiming grant for device %s with access code %s.", device_id, access_code
        )

        async with self._session.post(
            f"{API_INTERNAL_URL}/api/internal/v1/auth/device/claim-grant?access_code={access_code}&use_internal_names=true",
            headers=self._request_headers(),
        ) as response:
            if response.ok:
                data = await response.json()
                _LOGGER.debug("Response received %s: %s", response.status, data)
                return True
            else:
                _LOGGER.error(
                    "Failed to claim grant for device %s, status: %s",
                    device_id,
                    response.status,
                )
                return False

    async def _approve_access(self, device_id: str, access_code: str):
        """Approve an access grant for a device."""

        await self._ensure_valid_token()

        async with self._session.post(
            f"{API_INTERNAL_URL}/api/internal/v1/auth/device/{device_id}/access-grants?access_code={access_code}&approve=true&use_internal_names=true",
            headers=self._request_headers(),
        ) as response:
            if response.ok:
                _LOGGER.debug("Access approved for device %s.", device_id)
            else:
                _LOGGER.error(
                    "Failed to approve access for device %s, status: %s",
                    device_id,
                    response.status,
                )

            return response.ok

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
            self._cancel_extra_dhw_timer(clear_store=False)
            self._extra_dhw_restore_at = None
            await self.async_persist_extra_dhw(None)
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

        await self._ensure_valid_token()
        headers = self._request_headers()
        if self._device_metadata_etag:
            headers["If-None-Match"] = self._device_metadata_etag

        async with self._session.get(
            f"{self._api_url}/api/device-info/v1/devices/{device_id}/status",
            headers=headers,
        ) as response:
            match response.status:
                case 200:
                    self._device_metadata = await response.json()
                    self._device_metadata_etag = response.headers.get("ETag")
                case 403:
                    await self.unauthenticate()
                    raise APIAuthError(response)
                case 304:
                    _LOGGER.debug("Device metadata not modified, using cached data.")
                case 500:
                    _LOGGER.error("Internal server error, clearing data...")
                    raise APIConnectionError(response)
                case _:
                    _LOGGER.error(
                        f"Failed to fetch device metadata, status: {response.status}"
                    )
                    self._device_metadata = {}

        _LOGGER.debug("Device metadata fetched: %s", self._device_metadata)
        return self._device_metadata

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
        http_values, etag, total_latency = await self._get_http_values(
            device_id, names, etag_header=self._metrics_etag
        )

        if http_values is not None:
            metrics: dict = {"hpid": device_id}
            metrics["latency"] = total_latency
            for metric_name in names:
                if metric_name in http_values:
                    metrics[metric_name] = http_values[metric_name]
                    if metric_name == "fan0_10v":
                        metrics[metric_name] = int(float(metrics[metric_name]) * 10)
                else:
                    _LOGGER.warning(f"Metric {metric_name} not found in response data.")
            self._metrics_data = {"metrics": metrics}
            self._metrics_etag = etag

        _LOGGER.debug("HTTP metrics read: %s", self._metrics_data)
        return self._metrics_data

    async def get_http_metrics(self, device_id: str, metric_names: list[str]) -> dict:
        """Fetch specific metrics from the HTTP API, bypassing Modbus."""
        http_values, _, _ = await self._get_http_values(device_id, metric_names)
        if not http_values:
            return {"metrics": {}}
        metrics = {
            name: http_values[name] for name in metric_names if name in http_values
        }
        return {"metrics": metrics}

    async def _get_http_values(
        self,
        device_id: str,
        metric_names: list[str],
        etag_header: Optional[str] = None,
    ) -> tuple[dict | None, str | None, int | None]:
        """Perform a raw HTTP values fetch and return (values_dict, etag, total_latency).

        Returns (None, None, None) on 304 Not Modified and on any other
        unhandled non-200 response status.
        Raises APIAuthError on 403, APIConnectionError on 500.
        """
        self._ensure_open()
        await self._ensure_valid_token()
        headers = self._request_headers()
        if etag_header:
            headers["If-None-Match"] = etag_header

        names_list = "".join(f"&names[]={name}" for name in metric_names)
        async with self._session.get(
            f"{API_INTERNAL_URL}/api/internal/v1/devices/{device_id}/values"
            f"?use_internal_names=true&timeout={METRICS_TIMEOUT_SECONDS}{names_list}",
            headers=headers,
        ) as response:
            match response.status:
                case 200:
                    data = await response.json()
                    _LOGGER.debug("HTTP values fetched: %s", data)
                    return (
                        data.get("values", {}),
                        response.headers.get("ETag"),
                        data.get("total_latency"),
                    )
                case 403:
                    _LOGGER.error("Authentication failure: %s", response.status)
                    await self.unauthenticate()
                    raise APIAuthError(response)
                case 304:
                    _LOGGER.debug("HTTP values not modified, using cached data.")
                    return None, None, None
                case 500:
                    _LOGGER.error("Internal server error: %s", response.status)
                    raise APIConnectionError(response)
                case _:
                    _LOGGER.error(
                        "Failed to fetch HTTP values, status: %s", response.status
                    )
                    return None, None, None

    async def get_settings(self, device_id: str):
        """Fetch settings from the API or Modbus."""
        self._ensure_open()

        if self._modbus_tcp:
            settings_to_read = [
                setting_key
                for setting_key in MODBUS_HOLDING_TO_SETTINGS_MAP.keys()
                if setting_key in MODBUS_HOLDING_REGISTER_MAP
            ]
            return await self._read_modbus_settings(device_id, settings_to_read)

        # HTTP cloud mode.
        await self._ensure_valid_token()
        headers = self._request_headers()
        if self._settings_etag:
            headers["If-None-Match"] = self._settings_etag

        async with self._session.get(
            f"{self._api_url}/api/device-info/v1/devices/{device_id}/settings",
            headers=headers,
        ) as response:
            match response.status:
                case 200:
                    self._settings_data = await response.json()
                    self._settings_etag = response.headers.get("ETag")
                    _LOGGER.debug("HTTP Settings fetched: %s", self._settings_data)
                case 403:
                    await self.unauthenticate()
                    raise APIAuthError(response)
                case 304:
                    _LOGGER.debug("HTTP Settings not modified, using cached data.")
                case 500:
                    _LOGGER.error("Internal server error, clearing data...")
                    raise APIConnectionError(response)
                case _:
                    _LOGGER.error(
                        "Failed to fetch HTTP settings, status: %s", response.status
                    )
                    self._settings_data = {}

        _LOGGER.debug("HTTP Settings read: %s", self._settings_data)
        return self._settings_data

    async def get_primary_device(self):
        """Fetch device from the API with authentication."""

        devices = await self.get_devices()
        if not devices:
            _LOGGER.error("No devices found.")
            return None

        device = devices[0]

        metadata = await self.get_device_metadata(device.get("id"))
        if metadata:
            device = {**device, **metadata}

        _LOGGER.debug("Primary device fetched: %s", device)

        return device

    async def get_devices(self):
        """Fetch devices from the API with authentication."""

        await self._ensure_valid_token()

        async with self._session.get(
            f"{self._api_url}/api/inventory/v1/users/me/devices",
            headers=self._request_headers(),
        ) as response:
            match response.status:
                case 200:
                    devices_data = await response.json()
                    _LOGGER.debug("Devices fetched successfully: %s", devices_data)
                    return devices_data.get("devices") if devices_data else None
                case 403:
                    await self.unauthenticate()
                    raise APIAuthError(response)
                case _:
                    _LOGGER.error(
                        "Failed to fetch devices, status: %s", response.status
                    )
                    raise APIConnectionError(
                        response=response, message="Failed to fetch devices"
                    )


class APIAuthError(Exception):
    """Exception raised for authentication errors."""

    def __init__(
        self,
        response: Optional[aiohttp.ClientResponse],
        message: str = "Authentication failed",
    ):
        if response is not None:
            self.response = response
            self.status = response.status
            super().__init__(f"{message}: {response.status}")
        else:
            self.response = None
            self.status = None
            super().__init__(message)


class APIConnectionError(Exception):
    """Exception raised for connection/API errors."""

    def __init__(
        self,
        response: Optional[aiohttp.ClientResponse],
        message: str = "API request failed",
    ):
        if response is not None:
            self.response = response
            self.status = response.status
            super().__init__(f"{message}: {response.status}")
        else:
            self.response = None
            self.status = None
            super().__init__(message)


class APIRateLimitError(Exception):
    """Exception raised for rate limiting."""

    def __init__(
        self,
        response: Optional[aiohttp.ClientResponse],
        message: str = "Rate limit exceeded",
    ):
        if response is not None:
            self.response = response
            self.status = response.status
            super().__init__(f"{message}: {response.status}")
        else:
            self.response = None
            self.status = None
            super().__init__(message)
