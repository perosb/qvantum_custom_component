"""Qvantum API."""

import aiohttp
from datetime import datetime, timedelta, timezone
import logging
import json
from typing import Optional

from .const import (
    FAN_SPEED_VALUE_OFF,
    FAN_SPEED_VALUE_NORMAL,
    FAN_SPEED_VALUE_EXTRA,
    DEFAULT_ENABLED_METRICS,
    TAP_WATER_CAPACITY_MAPPINGS,
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
        username: str,
        password: str,
        user_agent: str,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> None:
        """Initialise."""
        self._auth_url = AUTH_URL
        self._token_url = TOKEN_URL
        self._api_url = API_URL
        self._username = username
        self._password = password
        self._user_agent = user_agent
        self.hass = None
        # Accept an optional aiohttp session for easier testing. If not provided,
        # create one and mark it as owned so we can close it when `close()` is called.
        if session is not None:
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
        self._token = None
        self._refreshtoken = None
        self._token_expiry = None
        self._settings_data = {}
        self._settings_etag = None
        self._metrics_data = {}
        self._metrics_etag = None
        self._device_metadata = {}
        self._device_metadata_etag = None

    async def close(self):
        """Close the session."""
        # Only close the session if we created it; externally-provided sessions
        # should be closed by their owner.
        if getattr(self, "_session_owner", False) and self._session:
            await self._session.close()
            self._session = None

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
        self._token = None
        self._refreshtoken = None
        self._token_expiry = None
        self._settings_data = {}
        self._settings_etag = None
        self._metrics_data = {}
        self._metrics_etag = None
        self._device_metadata = {}
        self._device_metadata_etag = None

    async def authenticate(self):
        """Authenticate with the API using username and password to retrieve a token."""
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

    async def _ensure_valid_token(self):
        """Ensure a valid token is available, refreshing if expired."""
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

    async def update_setting(self, device_id: str, name: str, value: any):
        """Update one setting."""

        payload = {"update_settings": {name: value}}

        return await self._send_command(device_id, payload)

    async def update_settings(self, device_id: str, settings: dict):
        """Update multiple settings from a dictionary."""

        payload = {"update_settings": settings}

        return await self._send_command(device_id, payload)

    async def _update_settings(self, device_id: str, payload: dict):
        """Update one or several settings."""

        _LOGGER.debug(json.dumps(payload))

        await self._ensure_valid_token()

        async with self._session.patch(
            f"{self._api_url}/api/device-info/v1/devices/{device_id}/settings?dispatch=false",
            json=payload,
            headers=self._request_headers(),
        ) as response:
            data = await response.json()
            _LOGGER.debug("Response received %s: %s", response.status, data)
            return data

    async def _send_command(self, device_id: str, payload: dict):
        """Send a command to a device."""

        wrapped_payload = {"command": payload}
        _LOGGER.debug(json.dumps(wrapped_payload))

        await self._ensure_valid_token()

        async with self._session.post(
            f"{self._api_url}/api/commands/v1/devices/{device_id}/commands?wait=true&use_internal_names=true",
            json=wrapped_payload,
            headers=self._request_headers(),
        ) as response:
            data = await response.json()
            _LOGGER.debug("Response received %s: %s", response.status, data)
            return data

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

        payload = {"settings": [{"name": "indoor_temperature_offset", "value": value}]}

        return await self._update_settings(device_id, payload)

    async def set_fanspeedselector(self, device_id: str, preset_mode: str):
        """Update set_fanspeedselector setting."""

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

        # Create reverse mapping: capacity -> (stop, start)
        capacity_to_stop_start = {v: k for k, v in TAP_WATER_CAPACITY_MAPPINGS.items()}

        if capacity in capacity_to_stop_start:
            stop, start = capacity_to_stop_start[capacity]
            _LOGGER.debug(
                f"Setting tap water capacity {capacity} maps to stop {stop} and start {start}."
            )
            return await self.set_tap_water(device_id, stop, start)

        payload = {
            "settings": [{"name": "tap_water_capacity_target", "value": capacity}]
        }

        _LOGGER.debug("Setting tap water capacity target to %s.", capacity)
        return await self._update_settings(device_id, payload)

    async def set_tap_water(self, device_id: str, stop: int = 0, start: int = 0):
        """Update tap_water_start setting."""

        if stop == 0 and start == 0:
            _LOGGER.debug("No tap water settings to update, both stop and start are 0.")
            return

        payload = {"settings": []}

        if stop:
            payload["settings"].append({"name": "tap_water_stop", "value": stop})
        if start:
            payload["settings"].append({"name": "tap_water_start", "value": start})

        return await self._update_settings(device_id, payload)

    async def set_tap_water_start(self, device_id: str, start: int):
        """Update tap_water_start setting."""

        return await self.set_tap_water(device_id, start=start)  # pragma: no cover

    async def set_tap_water_stop(self, device_id: str, stop: int):
        """Update tap_water_stop setting."""

        return await self.set_tap_water(device_id, stop=stop)  # pragma: no cover

    async def set_indoor_temperature_target(self, device_id: str, temperature: float):
        """Update indoor_temperature_target setting."""

        payload = {
            "settings": [{"name": "indoor_temperature_target", "value": temperature}]
        }

        return await self._update_settings(device_id, payload)

    async def get_device_metadata(self, device_id: str):
        """Fetch data from the API with authentication."""

        await self._ensure_valid_token()
        headers = self._request_headers()
        if self._metrics_etag:
            headers["If-None-Match"] = self._metrics_etag

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
                    # await self.unauthenticate()
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
        """Fetch data from the API with authentication."""

        await self._ensure_valid_token()
        headers = self._request_headers()
        if self._metrics_etag:
            headers["If-None-Match"] = self._metrics_etag

        names = (
            enabled_metrics if enabled_metrics is not None else DEFAULT_ENABLED_METRICS
        )
        names_list = ""
        for metric_name in names:
            names_list += f"&names[]={metric_name}"

        async with self._session.get(
            f"{API_INTERNAL_URL}/api/internal/v1/devices/{device_id}/values?use_internal_names=true&timeout={METRICS_TIMEOUT_SECONDS}{names_list}",
            headers=headers,
        ) as response:
            match response.status:
                case 200:
                    data = await response.json()

                    _LOGGER.debug("Metrics fetched: %s", data)

                    metrics = {}
                    metrics["hpid"] = device_id

                    metrics_data = data.get("values", {})
                    metrics["latency"] = (
                        data["total_latency"] if "total_latency" in data else None
                    )

                    for metric_name in names:
                        if metric_name in metrics_data:
                            metrics[metric_name] = metrics_data[metric_name]

                            if metric_name == "fan0_10v":
                                metrics[metric_name] = int(
                                    float(metrics[metric_name]) * 10
                                )
                        else:
                            _LOGGER.warning(
                                f"Metric {metric_name} not found in response data."
                            )

                    self._metrics_data = {"metrics": metrics}
                    self._metrics_etag = response.headers.get("ETag")

                case 403:
                    _LOGGER.error("Authentication failure: %s", response.status)
                    _LOGGER.debug("Authentication failure: %s", response)
                    await self.unauthenticate()
                    raise APIAuthError(response)
                case 304:
                    _LOGGER.debug("Metrics not modified, using cached data.")
                case 500:
                    _LOGGER.error(
                        "Internal server error, clearing data: %s", response.status
                    )
                    _LOGGER.debug("Internal server error, clearing data: %s", response)
                    # await self.unauthenticate()
                    raise APIConnectionError(response)
                case _:
                    _LOGGER.error("Failed to fetch data, status: %s", response.status)
                    _LOGGER.debug("Failed to fetch data, status: %s", response)

        return self._metrics_data

    async def get_settings(self, device_id: str):
        """Fetch settings from the API with authentication."""

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
                    _LOGGER.debug("Settings fetched: %s", self._settings_data)
                case 403:
                    await self.unauthenticate()
                    raise APIAuthError(response)
                case 304:
                    _LOGGER.debug("Settings not modified, using cached data.")
                case 500:
                    _LOGGER.error("Internal server error, clearing data...")
                    # await self.unauthenticate()
                    raise APIConnectionError(response)
                case _:
                    _LOGGER.error(
                        "Failed to fetch settings, status: %s", response.status
                    )
                    self._settings_data = {}

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
