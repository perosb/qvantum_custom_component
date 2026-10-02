"""HTTP cloud client for the Qvantum heat pump API."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import aiohttp

from ..constants import (
    HEATING_CURVE_HTTP_KEYS,
    SETTING_UPDATE_APPLIED,
    TAP_WATER_CAPACITY_MAPPINGS,
)
from ..exceptions import AuthError, RateLimitError, TransportError
from ..models import MetricsPayload, SettingsPayload
from .endpoints import (
    API_INTERNAL_URL,
    API_URL,
    AUTH_URL,
    DEFAULT_AUTH_FAILURE_COOLDOWN_SECONDS,
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
        # Guards token acquisition so concurrent callers share one
        # refresh/sign-in instead of issuing one request each. It survives
        # ``unauthenticate()``: only the token state resets, not the lock.
        self._token_lock = asyncio.Lock()
        # In-flight HTTP bookkeeping so ``close()`` can drain active requests
        # before closing an owned session. Both survive ``unauthenticate()``.
        self._inflight_requests = 0
        self._drained = asyncio.Event()
        self._drained.set()
        self._reset_state()

    def _reset_state(self) -> None:
        self._token = None
        self._refreshtoken = None
        self._token_expiry = None
        self._settings_data: SettingsPayload = {}
        self._settings_etag = None
        self._metrics_data: MetricsPayload = {}
        self._metrics_etag = None
        self._device_metadata: dict = {}
        self._device_metadata_etag = None
        self._missing_metrics_warned: set[str] = set()
        self._auth_failure: (
            tuple[float, type[AuthError | TransportError | RateLimitError], int | None, str]
            | None
        ) = None

    def _ensure_open(self) -> None:
        if self._closed:
            raise TransportError(None, "Cloud client is closed")

    async def close(self) -> None:
        """Drain in-flight requests, then close an owned HTTP session.

        Injected sessions are left to the owner. Mirroring the Modbus
        client's write lock, ``close()`` waits for requests that are already
        running so a shared session is never closed beneath them.
        """
        if self._closed:
            # A concurrent close() is already draining: wait for the same
            # drain point instead of returning while requests are in flight.
            await self._drained.wait()
            if not self._closed:
                # The draining close() was cancelled; finish the close here.
                await self.close()
            return
        self._closed = True
        try:
            await self._drained.wait()
        except asyncio.CancelledError:
            # A cancelled close() must not leave the client closed with the
            # session still open; let a later close() retry.
            self._closed = False
            raise
        if getattr(self, "_session_owner", False) and self._session:
            try:
                await self._session.close()
            except asyncio.CancelledError:
                self._session = None
                raise
            except Exception as exc:
                _LOGGER.debug("Error closing HTTP session: %s", exc)
            self._session = None

    @asynccontextmanager
    async def _track_request(self, request: Any):
        """Run an HTTP request while counting it as in flight.

        ``close()`` waits for these to drain before closing an owned
        session. The closed check and the increment are one synchronous
        step, so a request either is counted by ``close()`` or is rejected
        instead of touching a session that is closing.
        """
        if self._closed:
            raise TransportError(None, "Cloud client is closed")
        self._inflight_requests += 1
        self._drained.clear()
        try:
            async with request as response:
                yield response
        finally:
            self._inflight_requests -= 1
            if not self._inflight_requests:
                self._drained.set()

    async def unauthenticate(self) -> None:
        self._reset_state()

    async def _handle_response(self, response: aiohttp.ClientResponse) -> None:
        """Raise a typed error for a failed response.

        401/403 invalidate the stored token, 429 is client throttling, and
        anything else is a transport failure. 2xx responses return to the
        caller.
        """
        if response.ok:
            return
        if response.status in (401, 403):
            await self.unauthenticate()
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

    @staticmethod
    async def _decode_json(response: aiohttp.ClientResponse) -> Any:
        """Decode a JSON body, mapping decode failures to ``TransportError``.

        A successful status with a non-JSON body (proxy error page, truncated
        gateway response) must not leak an ``aiohttp``/``json`` exception
        across the library boundary.
        """
        try:
            return await response.json()
        except (aiohttp.ClientError, ValueError) as err:
            raise TransportError(
                response.status, "Invalid JSON response"
            ) from err

    @staticmethod
    async def _firebase_error_message(
        response: aiohttp.ClientResponse, default: str = "Authentication failed"
    ) -> str:
        """Return Firebase's ``error.message`` when the body carries one."""
        try:
            data = await response.json()
        except (aiohttp.ClientError, ValueError):
            return default
        if isinstance(data, dict):
            error = data.get("error")
            if isinstance(error, dict):
                message = error.get("message")
                if isinstance(message, str) and message:
                    return message
        return default

    async def authenticate(self) -> bool:
        """Sign in with email/password and store tokens.

        A rejected sign-in is an ``AuthError``, throttling a
        ``RateLimitError``, and a server-side failure a ``TransportError`` so
        a transient outage does not send Home Assistant into reauth.
        """
        self._ensure_open()
        payload = {
            "returnSecureToken": "true",
            "email": self._username,
            "password": self._password,
            "clientType": "CLIENT_TYPE_WEB",
        }
        async with self._track_request(
            self._session.post(
                f"{self._auth_url}/v1/accounts:signInWithPassword?key={self._firebase_api_key}",
                **self._http_kwargs(include_auth=False, json=payload),
            )
        ) as response:
            match response.status:
                case 200:
                    _LOGGER.debug("Authentication successful: %s", response.status)
                    auth_data = await self._decode_json(response)
                    self._token = auth_data.get("idToken")
                    self._refreshtoken = auth_data.get("refreshToken")
                    expires_in = auth_data.get(
                        "expiresIn", DEFAULT_TOKEN_EXPIRY_SECONDS
                    )
                    self._token_expiry = datetime.now() + timedelta(
                        seconds=int(expires_in) - DEFAULT_TOKEN_BUFFER_SECONDS
                    )
                    return True
                case 429:
                    raise RateLimitError(response.status)
                case 400:
                    message = await self._firebase_error_message(response)
                    _LOGGER.error(
                        "Authentication failed: %s (%s)", response.status, message
                    )
                    if message.startswith("TOO_MANY_ATTEMPTS_TRY_LATER"):
                        # Firebase's sign-in lockout is a 400, not a 429; it
                        # must not send Home Assistant into reauth.
                        raise RateLimitError(response.status, message)
                    raise AuthError(response.status, message)
                case status if status >= 500:
                    _LOGGER.error("Authentication failed: %s", status)
                    raise TransportError(status, "Authentication failed")
                case _:
                    _LOGGER.error("Authentication failed: %s", response.status)
                    raise AuthError(response.status)

    async def _refresh_authentication_token(self) -> None:
        self._ensure_open()
        if not self._refreshtoken:
            return
        payload = {"grant_type": "refresh_token", "refresh_token": self._refreshtoken}
        self._token = None
        async with self._track_request(
            self._session.post(
                f"{self._token_url}/v1/token?key={self._firebase_api_key}",
                **self._http_kwargs(include_auth=False, json=payload),
            )
        ) as response:
            match response.status:
                case 200:
                    _LOGGER.debug("Token refreshed successfully: %s", response.status)
                    auth_data = await self._decode_json(response)
                    self._token = auth_data.get("access_token")
                    self._refreshtoken = auth_data.get("refresh_token")
                    expires_in = auth_data.get(
                        "expires_in", DEFAULT_TOKEN_EXPIRY_SECONDS
                    )
                    self._token_expiry = datetime.now() + timedelta(
                        seconds=int(expires_in) - DEFAULT_TOKEN_BUFFER_SECONDS
                    )
                case 429:
                    # Throttling must not fall through to a full sign-in.
                    raise RateLimitError(response.status)
                case status if status >= 500:
                    # A server-side failure is retryable; do not add a
                    # sign-in request on top of an outage.
                    _LOGGER.error("Token refresh failed: %s", status)
                    raise TransportError(status, "Token refresh failed")
                case _:
                    _LOGGER.error("Token refresh failed: %s", response.status)

    async def _ensure_valid_token(self) -> None:
        """Ensure a valid token, signing in at most once per call.

        Refresh first when a refresh token exists; otherwise (or when refresh
        yields no token) sign in with the stored credentials. An AuthError
        from authenticate() propagates immediately so a rejected password is
        not retried within the same request. A rate-limited refresh raises
        RateLimitError instead of adding a sign-in request on top.

        Concurrent callers share ``_token_lock`` so an expired token triggers
        one refresh/sign-in rather than one request per caller; the token is
        re-checked after acquiring the lock because a waiter may have already
        refreshed it. A failed attempt is cached for a short cooldown so the
        waiters fail fast instead of each issuing their own request.
        """
        self._ensure_open()
        if self._token_is_valid():
            return
        self._raise_recent_auth_failure()

        async with self._token_lock:
            if self._token_is_valid():
                return
            self._raise_recent_auth_failure()

            try:
                if self._refreshtoken:
                    await self._refresh_authentication_token()
                if not self._token:
                    await self.authenticate()
                if not self._token:
                    raise AuthError(None, "Failed to obtain authentication token")
            except (AuthError, RateLimitError, TransportError) as err:
                self._auth_failure = (
                    time.monotonic(),
                    type(err),
                    err.status,
                    err.message,
                )
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                # Connection-level failures must be typed and cached too, or
                # every waiter retries the network in the flaky cases the
                # cooldown targets.
                failure = TransportError(None, f"Authentication request failed: {err}")
                self._auth_failure = (
                    time.monotonic(),
                    type(failure),
                    failure.status,
                    failure.message,
                )
                raise failure from err
            self._auth_failure = None

    def _raise_recent_auth_failure(self) -> None:
        """Re-raise the last auth failure while its cooldown is active.

        Without this, releasing the lock after a failed refresh/sign-in lets
        every waiter retry the API, so a burst of concurrent callers with a
        rejected password still fires one request per caller. Each caller
        gets a fresh exception instance so tracebacks stay per-caller.

        The cache also covers retryable ``TransportError``/``RateLimitError``,
        so a transient blip fails fast for the cooldown window instead of
        re-hitting the API on every poll.
        """
        failure = self._auth_failure
        if failure is None:
            return
        failed_at, err_type, status, message = failure
        if time.monotonic() - failed_at < DEFAULT_AUTH_FAILURE_COOLDOWN_SECONDS:
            raise err_type(status, message)
        self._auth_failure = None

    def _token_is_valid(self) -> bool:
        """Whether a stored token exists and has not expired yet."""
        return bool(
            self._token
            and self._token_expiry
            and datetime.now() < self._token_expiry
        )

    async def _request_json(
        self,
        method: str,
        url: str,
        payload: Optional[dict] = None,
        validate_status: bool = True,
    ) -> dict[str, Any]:
        """Send a JSON request and return the decoded body.

        Writes validate the HTTP status by default so a failed update raises
        instead of surfacing an error body as if it were a result.
        """
        await self._ensure_valid_token()
        request = getattr(self._session, method)
        kwargs: dict[str, Any] = self._http_kwargs()
        if payload is not None:
            kwargs["json"] = payload
        async with self._track_request(request(url, **kwargs)) as response:
            if validate_status:
                await self._handle_response(response)
            data = await self._decode_json(response)
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

    async def set_curve_type_heating(
        self, device_id: str, value: int
    ) -> dict[str, Any]:
        return await self.update_setting(device_id, "curve_type_heating", int(value))

    async def set_heating_curve_point(
        self, device_id: str, metric_key: str, value: int
    ) -> dict[str, Any]:
        http_key = HEATING_CURVE_HTTP_KEYS.get(metric_key)
        if http_key is None:
            raise ValueError(f"Unknown heating-curve point: {metric_key}")
        return await self.update_setting(device_id, http_key, int(value))

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
    ) -> dict[str, Any]:
        if stop == 0 and start == 0:
            _LOGGER.debug("No tap water settings to update, both stop and start are 0.")
            return {"status": SETTING_UPDATE_APPLIED}
        payload: dict[str, Any] = {"settings": []}
        if stop:
            payload["settings"].append({"name": "tap_water_stop", "value": stop})
        if start:
            payload["settings"].append({"name": "tap_water_start", "value": start})
        return await self._update_settings(device_id, payload)

    async def set_tap_water_capacity_target(
        self, device_id: str, capacity: int
    ) -> dict[str, Any]:
        if capacity not in set(TAP_WATER_CAPACITY_MAPPINGS.values()):
            raise ValueError(
                f"Unsupported tap water capacity {capacity}; expected one of "
                f"{sorted(TAP_WATER_CAPACITY_MAPPINGS.values())}"
            )
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
        async with self._track_request(
            self._session.get(
                f"{self._api_url}/api/device-info/v1/devices/{device_id}/status",
                **self._http_kwargs(headers=extra),
            )
        ) as response:
            match response.status:
                case 200:
                    self._device_metadata = await self._decode_json(response)
                    self._device_metadata_etag = response.headers.get("ETag")
                case 401 | 403:
                    await self.unauthenticate()
                    raise AuthError(response.status)
                case 429:
                    raise RateLimitError(response.status)
                case 304:
                    _LOGGER.debug("Device metadata not modified, using cached data.")
                case status if status >= 500:
                    _LOGGER.error(
                        "Server error fetching device metadata, status: %s", status
                    )
                    raise TransportError(response.status)
                case _:
                    # Keep the last known metadata so the device stays
                    # identified during a transient failure, matching the
                    # metrics and settings reads.
                    _LOGGER.warning(
                        "Failed to fetch device metadata, status: %s; keeping "
                        "cached metadata.",
                        response.status,
                    )
        _LOGGER.debug("Device metadata fetched: %s", self._device_metadata)
        return self._device_metadata

    async def get_metrics(
        self, device_id: str, enabled_metrics: list[str] | None = None
    ) -> MetricsPayload:
        self._ensure_open()
        http_values, etag, total_latency = await self._get_http_values(
            device_id,
            enabled_metrics or [],
            etag_header=self._metrics_etag,
        )
        if http_values is not None:
            metrics: dict[str, Any] = {"hpid": device_id, "latency": total_latency}
            names = (
                enabled_metrics
                if enabled_metrics is not None
                else list(http_values.keys())
            )
            for metric_name in names:
                if metric_name in http_values:
                    metrics[metric_name] = http_values[metric_name]
                    if metric_name == "fan0_10v":
                        try:
                            metrics[metric_name] = int(
                                float(metrics[metric_name]) * 10
                            )
                        except (TypeError, ValueError):
                            _LOGGER.debug(
                                "Could not scale fan0_10v value %r; keeping raw value",
                                metrics[metric_name],
                            )
                else:
                    if metric_name not in self._missing_metrics_warned:
                        self._missing_metrics_warned.add(metric_name)
                        _LOGGER.warning(
                            "Metric %s not found in response data; suppressing "
                            "further warnings until the session is reset.",
                            metric_name,
                        )
                    else:
                        _LOGGER.debug(
                            "Metric %s still not found in response data.", metric_name
                        )
            self._metrics_data = {"metrics": metrics}
            self._metrics_etag = etag
        _LOGGER.debug("HTTP metrics read: %s", self._metrics_data)
        return self._metrics_data

    async def get_http_metrics(
        self, device_id: str, metric_names: list[str]
    ) -> MetricsPayload:
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
        async with self._track_request(
            self._session.get(
                f"{API_INTERNAL_URL}/api/internal/v1/devices/{device_id}/values"
                f"?use_internal_names=true&timeout={METRICS_TIMEOUT_SECONDS}{names_list}",
                **self._http_kwargs(headers=extra),
            )
        ) as response:
            match response.status:
                case 200:
                    data = await self._decode_json(response)
                    _LOGGER.debug("HTTP values fetched: %s", data)
                    return (
                        data.get("values", {}),
                        response.headers.get("ETag"),
                        data.get("total_latency"),
                    )
                case 401 | 403:
                    _LOGGER.error("Authentication failure: %s", response.status)
                    await self.unauthenticate()
                    raise AuthError(response.status)
                case 429:
                    raise RateLimitError(response.status)
                case 304:
                    _LOGGER.debug("HTTP values not modified, using cached data.")
                    return None, None, None
                case status if status >= 500:
                    _LOGGER.error("Server error fetching HTTP values: %s", status)
                    raise TransportError(response.status)
                case _:
                    _LOGGER.warning(
                        "Failed to fetch HTTP values, status: %s; keeping "
                        "cached values.",
                        response.status,
                    )
                    return None, None, None

    async def get_settings(self, device_id: str) -> SettingsPayload:
        self._ensure_open()
        await self._ensure_valid_token()
        extra: dict[str, str] = {}
        if self._settings_etag:
            extra["If-None-Match"] = self._settings_etag
        async with self._track_request(
            self._session.get(
                f"{self._api_url}/api/device-info/v1/devices/{device_id}/settings",
                **self._http_kwargs(headers=extra),
            )
        ) as response:
            match response.status:
                case 200:
                    self._settings_data = await self._decode_json(response)
                    self._settings_etag = response.headers.get("ETag")
                    _LOGGER.debug("HTTP Settings fetched: %s", self._settings_data)
                case 401 | 403:
                    await self.unauthenticate()
                    raise AuthError(response.status)
                case 429:
                    raise RateLimitError(response.status)
                case 304:
                    _LOGGER.debug("HTTP Settings not modified, using cached data.")
                case status if status >= 500:
                    _LOGGER.error(
                        "Server error fetching HTTP settings, status: %s", status
                    )
                    raise TransportError(response.status)
                case _:
                    # Keep the last known settings so entities stay usable
                    # during a transient failure, matching get_metrics.
                    _LOGGER.warning(
                        "Failed to fetch HTTP settings, status: %s; keeping "
                        "cached settings.",
                        response.status,
                    )
        _LOGGER.debug("HTTP Settings read: %s", self._settings_data)
        return self._settings_data

    async def get_devices(self) -> list | None:
        await self._ensure_valid_token()
        async with self._track_request(
            self._session.get(
                f"{self._api_url}/api/inventory/v1/users/me/devices",
                **self._http_kwargs(),
            )
        ) as response:
            match response.status:
                case 200:
                    devices_data = await self._decode_json(response)
                    _LOGGER.debug("Devices fetched successfully: %s", devices_data)
                    return devices_data.get("devices") if devices_data else None
                case 401 | 403:
                    await self.unauthenticate()
                    raise AuthError(response.status)
                case 429:
                    raise RateLimitError(response.status)
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
        async with self._track_request(
            self._session.get(
                f"{API_INTERNAL_URL}/api/internal/v1/auth/device/{device_id}/my-access-level?use_internal_names=true",
                **self._http_kwargs(),
            )
        ) as response:
            await self._handle_response(response)
            data = await self._decode_json(response)
            _LOGGER.debug(
                "Access level response received %s: %s", response.status, data
            )
            return data

    async def _generate_code(self, device_id: str) -> dict[str, Any] | None:
        await self._ensure_valid_token()
        async with self._track_request(
            self._session.post(
                f"{API_INTERNAL_URL}/api/internal/v1/auth/device/{device_id}/generate-access-code?use_internal_names=true",
                **self._http_kwargs(),
            )
        ) as response:
            if response.ok:
                data = await self._decode_json(response)
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
        async with self._track_request(
            self._session.post(
                f"{API_INTERNAL_URL}/api/internal/v1/auth/device/claim-grant?access_code={access_code}&use_internal_names=true",
                **self._http_kwargs(),
            )
        ) as response:
            if response.ok:
                data = await self._decode_json(response)
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
        async with self._track_request(
            self._session.post(
                f"{API_INTERNAL_URL}/api/internal/v1/auth/device/{device_id}/access-grants?access_code={access_code}&approve=true&use_internal_names=true",
                **self._http_kwargs(),
            )
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
        async with self._track_request(
            self._session.get(
                f"{API_INTERNAL_URL}/api/internal/v1/auth/device/{device_id}/my-access-level?use_internal_names=true",
                **self._http_kwargs(),
            )
        ) as response:
            await self._handle_response(response)
            data = await self._decode_json(response)
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
            async with self._track_request(
                self._session.get(
                    f"{API_INTERNAL_URL}/api/internal/v1/auth/device/{device_id}/my-access-level?use_internal_names=true",
                    **self._http_kwargs(),
                )
            ) as response:
                await self._handle_response(response)
                data = await self._decode_json(response)
                _LOGGER.debug("Response received %s: %s", response.status, data)
                return data
