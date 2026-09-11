"""HTTP cloud client for the Qvantum heat pump API."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import aiohttp

from ..constants import TAP_WATER_CAPACITY_MAPPINGS
from ..exceptions import AuthError, RateLimitError, TransportError
from .endpoints import (
    API_INTERNAL_URL,
    API_URL,
    AUTH_URL,
    DEFAULT_TOKEN_BUFFER_SECONDS,
    DEFAULT_TOKEN_EXPIRY_SECONDS,
    FIREBASE_API_KEY,
    HTTP_TIMEOUT,
    METRICS_TIMEOUT_SECONDS,
    TOKEN_URL,
    VENTILATION_BOOST_MINUTES,
)

_LOGGER = logging.getLogger(__name__)

_CUSTOM_CAPACITIES = {1, 6, 7}


class QvantumCloudClient:
    """Qvantum cloud API (Firebase auth + REST)."""

    def __init__(
        self,
        username: str | None = None,
        password: str | None = None,
        user_agent: str = "",
        session: aiohttp.ClientSession | None = None,
        *,
        firebase_api_key: str = FIREBASE_API_KEY,
    ) -> None:
        self._auth_url = AUTH_URL
        self._token_url = TOKEN_URL
        self._api_url = API_URL
        self._username = username
        self._password = password
        self._user_agent = user_agent
        self._firebase_api_key = firebase_api_key
        self._closed = False
        if session is not None:
            self._session = session
            self._session_owner = False
        else:
            self._session = aiohttp.ClientSession(
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": self._user_agent,
                },
                timeout=HTTP_TIMEOUT,
            )
            self._session_owner = True
        self._reset_state()

    def _reset_state(self) -> None:
        self._token = None
        self._refreshtoken = None
        self._token_expiry = None
        self._settings_data: dict = {}
        self._settings_etag = None
        self._metrics_data: dict = {}
        self._metrics_etag = None
        self._device_metadata: dict = {}
        self._device_metadata_etag = None

    def _ensure_open(self) -> None:
        if self._closed:
            raise TransportError(None, "Cloud client is closed")

    async def close(self) -> None:
        """Close an owned HTTP session. Injected sessions are left to the owner."""
        if self._closed:
            return
        self._closed = True
        if getattr(self, "_session_owner", False) and self._session:
            try:
                await self._session.close()
            except asyncio.CancelledError:
                self._session = None
                raise
            except Exception as exc:
                _LOGGER.debug("Error closing HTTP session: %s", exc)
            self._session = None

    async def unauthenticate(self) -> None:
        self._reset_state()

    async def _handle_response(self, response: aiohttp.ClientResponse) -> None:
        if not response.ok:
            if response.status == 401:
                raise AuthError(response.status)
            if response.status == 429:
                raise RateLimitError(response.status)
            raise TransportError(response.status)

    def _request_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _http_kwargs(
        self,
        *,
        headers: dict[str, str] | None = None,
        include_auth: bool = True,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Timeout and User-Agent for owned and injected sessions."""
        merged: dict[str, str] = {
            "Content-Type": "application/json",
            "User-Agent": self._user_agent,
        }
        if include_auth:
            merged.update(self._request_headers())
        if headers:
            merged.update(headers)
        result: dict[str, Any] = {"headers": merged, "timeout": HTTP_TIMEOUT}
        result.update(kwargs)
        return result

    async def authenticate(self) -> bool:
        """Sign in with email/password and store tokens."""
        self._ensure_open()
        payload = {
            "returnSecureToken": "true",
            "email": self._username,
            "password": self._password,
            "clientType": "CLIENT_TYPE_WEB",
        }
        async with self._session.post(
            f"{self._auth_url}/v1/accounts:signInWithPassword?key={self._firebase_api_key}",
            **self._http_kwargs(include_auth=False, json=payload),
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
                    raise AuthError(response.status)

    async def _refresh_authentication_token(self) -> None:
        self._ensure_open()
        if not self._refreshtoken:
            return
        payload = {"grant_type": "refresh_token", "refresh_token": self._refreshtoken}
        self._token = None
        async with self._session.post(
            f"{self._token_url}/v1/token?key={self._firebase_api_key}",
            **self._http_kwargs(include_auth=False, json=payload),
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

    async def _ensure_valid_token(self) -> None:
        self._ensure_open()
        if not self._token or datetime.now() >= self._token_expiry:
            try:
                await self._refresh_authentication_token()
                if not self._token:
                    await self.authenticate()
                    if not self._token:
                        raise AuthError(None, "Failed to obtain authentication token")
            except AuthError:
                await self.authenticate()
                if not self._token:
                    raise AuthError(None, "Failed to obtain authentication token")

    async def _request_json(
        self,
        method: str,
        url: str,
        payload: Optional[dict] = None,
        validate_status: bool = False,
    ) -> dict[str, Any]:
        await self._ensure_valid_token()
        request = getattr(self._session, method)
        kwargs: dict[str, Any] = self._http_kwargs()
        if payload is not None:
            kwargs["json"] = payload
        async with request(url, **kwargs) as response:
            if validate_status:
                await self._handle_response(response)
            data = await response.json()
            _LOGGER.debug("Response received %s: %s", response.status, data)
            return data

    async def _update_settings(self, device_id: str, payload: dict) -> dict[str, Any]:
        _LOGGER.debug(json.dumps(payload))
        return await self._request_json(
            "patch",
            f"{self._api_url}/api/device-info/v1/devices/{device_id}/settings?dispatch=false",
            payload,
        )

    async def _send_command(self, device_id: str, payload: dict) -> dict[str, Any]:
        wrapped_payload = {"command": payload}
        _LOGGER.debug(json.dumps(wrapped_payload))
        return await self._request_json(
            "post",
            f"{self._api_url}/api/commands/v1/devices/{device_id}/commands?wait=true&use_internal_names=true",
            wrapped_payload,
        )

    async def update_setting(
        self, device_id: str, name: str, value: Any
    ) -> dict[str, Any]:
        return await self._send_command(device_id, {"update_settings": {name: value}})

    async def update_settings(
        self, device_id: str, settings: dict
    ) -> dict[str, Any]:
        return await self._send_command(device_id, {"update_settings": settings})

    async def set_smartcontrol(self, device_id: str, sh: int, dhw: int) -> dict[str, Any]:
        use_adaptive = sh != -1 and dhw != -1
        if not use_adaptive:
            payload = {"use_adaptive": False}
        else:
            payload = {
                "use_adaptive": use_adaptive,
                "smart_sh_mode": sh,
                "smart_dhw_mode": dhw,
            }
        return await self.update_settings(device_id, payload)

    async def set_extra_tap_water(self, device_id: str, minutes: int) -> dict[str, Any]:
        current_time = datetime.now()
        if minutes == 0:
            stop_time = int(current_time.timestamp())
            indefinite = False
            cancel = True
        elif minutes > 0:
            stop_time = int((current_time + timedelta(minutes=minutes)).timestamp())
            indefinite = False
            cancel = False
        else:
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

    async def set_indoor_temperature_offset(
        self, device_id: str, value: int
    ) -> dict[str, Any]:
        payload = {"settings": [{"name": "indoor_temperature_offset", "value": value}]}
        return await self._update_settings(device_id, payload)

    async def set_indoor_temperature_target(
        self, device_id: str, temperature: float
    ) -> dict[str, Any]:
        payload = {
            "settings": [{"name": "indoor_temperature_target", "value": temperature}]
        }
        return await self._update_settings(device_id, payload)

    async def set_fanspeedselector(
        self, device_id: str, preset_mode: str
    ) -> dict[str, Any]:
        current_time = datetime.now()
        match preset_mode:
            case "off":
                payload = {"set_fan_mode": {"mode": 0}}
            case "normal":
                payload = {
                    "set_fan_mode": {
                        "stopTime": int(current_time.timestamp()),
                        "indefinite": False,
                    }
                }
            case "extra":
                stop_time = int(
                    (
                        current_time + timedelta(minutes=VENTILATION_BOOST_MINUTES)
                    ).timestamp()
                )
                payload = {
                    "set_fan_mode": {"stopTime": stop_time, "indefinite": False}
                }
            case _:
                raise ValueError(f"Invalid preset_mode: {preset_mode}")
        return await self._send_command(device_id, payload)

    async def set_tap_water(
        self, device_id: str, start: int = 0, stop: int = 0
    ) -> dict[str, Any] | None:
        if stop == 0 and start == 0:
            _LOGGER.debug("No tap water settings to update, both stop and start are 0.")
            return None
        payload: dict[str, Any] = {"settings": []}
        if stop:
            payload["settings"].append({"name": "tap_water_stop", "value": stop})
        if start:
            payload["settings"].append({"name": "tap_water_start", "value": start})
        return await self._update_settings(device_id, payload)

    async def set_tap_water_capacity_target(
        self, device_id: str, capacity: int
    ) -> dict[str, Any] | None:
        if capacity in _CUSTOM_CAPACITIES:
            capacity_to_stop_start = {v: k for k, v in TAP_WATER_CAPACITY_MAPPINGS.items()}
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

    async def get_device_metadata(self, device_id: str) -> dict[str, Any]:
        await self._ensure_valid_token()
        extra: dict[str, str] = {}
        if self._device_metadata_etag:
            extra["If-None-Match"] = self._device_metadata_etag
        async with self._session.get(
            f"{self._api_url}/api/device-info/v1/devices/{device_id}/status",
            **self._http_kwargs(headers=extra),
        ) as response:
            match response.status:
                case 200:
                    self._device_metadata = await response.json()
                    self._device_metadata_etag = response.headers.get("ETag")
                case 403:
                    await self.unauthenticate()
                    raise AuthError(response.status)
                case 304:
                    _LOGGER.debug("Device metadata not modified, using cached data.")
                case 500:
                    _LOGGER.error("Internal server error, clearing data...")
                    raise TransportError(response.status)
                case _:
                    _LOGGER.error(
                        "Failed to fetch device metadata, status: %s", response.status
                    )
                    self._device_metadata = {}
        _LOGGER.debug("Device metadata fetched: %s", self._device_metadata)
        return self._device_metadata

    async def get_metrics(
        self, device_id: str, enabled_metrics: list[str] | None = None
    ) -> dict[str, Any]:
        self._ensure_open()
        http_values, etag, total_latency = await self._get_http_values(
            device_id,
            enabled_metrics or [],
            etag_header=self._metrics_etag,
        )
        if http_values is not None:
            metrics: dict = {"hpid": device_id, "latency": total_latency}
            names = (
                enabled_metrics
                if enabled_metrics is not None
                else list(http_values.keys())
            )
            for metric_name in names:
                if metric_name in http_values:
                    metrics[metric_name] = http_values[metric_name]
                    if metric_name == "fan0_10v":
                        metrics[metric_name] = int(float(metrics[metric_name]) * 10)
                else:
                    _LOGGER.warning("Metric %s not found in response data.", metric_name)
            self._metrics_data = {"metrics": metrics}
            self._metrics_etag = etag
        _LOGGER.debug("HTTP metrics read: %s", self._metrics_data)
        return self._metrics_data

    async def get_http_metrics(
        self, device_id: str, metric_names: list[str]
    ) -> dict[str, Any]:
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
        self._ensure_open()
        await self._ensure_valid_token()
        extra: dict[str, str] = {}
        if etag_header:
            extra["If-None-Match"] = etag_header
        names_list = "".join(f"&names[]={name}" for name in metric_names)
        async with self._session.get(
            f"{API_INTERNAL_URL}/api/internal/v1/devices/{device_id}/values"
            f"?use_internal_names=true&timeout={METRICS_TIMEOUT_SECONDS}{names_list}",
            **self._http_kwargs(headers=extra),
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
                    raise AuthError(response.status)
                case 304:
                    _LOGGER.debug("HTTP values not modified, using cached data.")
                    return None, None, None
                case 500:
                    _LOGGER.error("Internal server error: %s", response.status)
                    raise TransportError(response.status)
                case _:
                    _LOGGER.error(
                        "Failed to fetch HTTP values, status: %s", response.status
                    )
                    return None, None, None

    async def get_settings(self, device_id: str) -> dict[str, Any]:
        self._ensure_open()
        await self._ensure_valid_token()
        extra: dict[str, str] = {}
        if self._settings_etag:
            extra["If-None-Match"] = self._settings_etag
        async with self._session.get(
            f"{self._api_url}/api/device-info/v1/devices/{device_id}/settings",
            **self._http_kwargs(headers=extra),
        ) as response:
            match response.status:
                case 200:
                    self._settings_data = await response.json()
                    self._settings_etag = response.headers.get("ETag")
                    _LOGGER.debug("HTTP Settings fetched: %s", self._settings_data)
                case 403:
                    await self.unauthenticate()
                    raise AuthError(response.status)
                case 304:
                    _LOGGER.debug("HTTP Settings not modified, using cached data.")
                case 500:
                    _LOGGER.error("Internal server error, clearing data...")
                    raise TransportError(response.status)
                case _:
                    _LOGGER.error(
                        "Failed to fetch HTTP settings, status: %s", response.status
                    )
                    self._settings_data = {}
        _LOGGER.debug("HTTP Settings read: %s", self._settings_data)
        return self._settings_data

    async def get_devices(self) -> list | None:
        await self._ensure_valid_token()
        async with self._session.get(
            f"{self._api_url}/api/inventory/v1/users/me/devices",
            **self._http_kwargs(),
        ) as response:
            match response.status:
                case 200:
                    devices_data = await response.json()
                    _LOGGER.debug("Devices fetched successfully: %s", devices_data)
                    return devices_data.get("devices") if devices_data else None
                case 403:
                    await self.unauthenticate()
                    raise AuthError(response.status)
                case _:
                    _LOGGER.error(
                        "Failed to fetch devices, status: %s", response.status
                    )
                    raise TransportError(response.status, "Failed to fetch devices")

    async def get_primary_device(self) -> dict[str, Any] | None:
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

    async def get_access_level(self, device_id: str) -> dict[str, Any]:
        await self._ensure_valid_token()
        async with self._session.get(
            f"{API_INTERNAL_URL}/api/internal/v1/auth/device/{device_id}/my-access-level?use_internal_names=true",
            **self._http_kwargs(),
        ) as response:
            await self._handle_response(response)
            data = await response.json()
            _LOGGER.debug(
                "Access level response received %s: %s", response.status, data
            )
            return data

    async def _generate_code(self, device_id: str) -> dict[str, Any] | None:
        await self._ensure_valid_token()
        async with self._session.post(
            f"{API_INTERNAL_URL}/api/internal/v1/auth/device/{device_id}/generate-access-code?use_internal_names=true",
            **self._http_kwargs(),
        ) as response:
            if response.ok:
                data = await response.json()
                _LOGGER.debug("Response received %s: %s", response.status, data)
                return data
            _LOGGER.error(
                "Failed to generate access code for device %s, status: %s",
                device_id,
                response.status,
            )
            return None

    async def _claim_grant(self, device_id: str, access_code: str) -> bool:
        await self._ensure_valid_token()
        _LOGGER.debug(
            "Claiming grant for device %s with access code %s.", device_id, access_code
        )
        async with self._session.post(
            f"{API_INTERNAL_URL}/api/internal/v1/auth/device/claim-grant?access_code={access_code}&use_internal_names=true",
            **self._http_kwargs(),
        ) as response:
            if response.ok:
                data = await response.json()
                _LOGGER.debug("Response received %s: %s", response.status, data)
                return True
            _LOGGER.error(
                "Failed to claim grant for device %s, status: %s",
                device_id,
                response.status,
            )
            return False

    async def _approve_access(self, device_id: str, access_code: str) -> bool:
        await self._ensure_valid_token()
        async with self._session.post(
            f"{API_INTERNAL_URL}/api/internal/v1/auth/device/{device_id}/access-grants?access_code={access_code}&approve=true&use_internal_names=true",
            **self._http_kwargs(),
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

    async def elevate_access(self, device_id: str) -> dict[str, Any] | None:
        await self._ensure_valid_token()
        async with self._session.get(
            f"{API_INTERNAL_URL}/api/internal/v1/auth/device/{device_id}/my-access-level?use_internal_names=true",
            **self._http_kwargs(),
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
            code_data = await self._generate_code(device_id)
            if not code_data:
                return None
            access_code = code_data.get("accessCode")
            if not access_code:
                return None
            if not await self._claim_grant(device_id, access_code):
                return None
            if not await self._approve_access(device_id, access_code):
                return None
            async with self._session.get(
                f"{API_INTERNAL_URL}/api/internal/v1/auth/device/{device_id}/my-access-level?use_internal_names=true",
                **self._http_kwargs(),
            ) as response:
                await self._handle_response(response)
                data = await response.json()
                _LOGGER.debug("Response received %s: %s", response.status, data)
                return data
