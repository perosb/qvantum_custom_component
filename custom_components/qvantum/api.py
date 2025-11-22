"""Qvantum API."""

import aiohttp
from datetime import datetime, timedelta
import logging, json
from typing import Optional

from .const import (
    FAN_SPEED_VALUE_OFF,
    FAN_SPEED_VALUE_NORMAL,
    FAN_SPEED_VALUE_EXTRA,
    DEFAULT_ENABLED_METRICS,
    DEFAULT_DISABLED_METRICS,
    DOMAIN,
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
        if getattr(self, "_session_owner", False):
            await self._session.close()

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
                    _LOGGER.debug(f"Authentication successful: {response.status}")
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
                    _LOGGER.error(f"Authentication failed: {response.status}")
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
                    _LOGGER.debug(f"Token refreshed successfully: {response.status}")
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
                    _LOGGER.error(f"Token refresh failed: {response.status}")
                    # Don't raise exception here, let _ensure_valid_token handle it

    async def _ensure_valid_token(self):
        """Ensure a valid token is available, refreshing if expired."""
        if not self._token or datetime.now() >= self._token_expiry:
            await self._refresh_authentication_token()
            if not self._token:
                await self.authenticate()
                if not self._token:
                    raise APIAuthError(None, "Failed to obtain authentication token")

    def _request_headers(self):
        """Get request headers for API calls."""
        return {
            "Authorization": f"Bearer {self._token}",
        }

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
            _LOGGER.debug(f"Response received {response.status}: {data}")
            return data

    async def set_extra_tap_water(self, device_id: str, minutes: int):
        """Update extra_tap_water setting."""

        if minutes >= 0:
            stop_time = int((datetime.now() + timedelta(minutes=minutes)).timestamp())
        else:
            stop_time = -1  # -1 means "always on"

        dhw_mode = 1
        if minutes != 0:
            dhw_mode = 2

        payload = {
            "settings": [
                {"name": "extra_tap_water_stop", "value": stop_time},
                {"name": "dhw_mode", "value": dhw_mode},
            ]
        }

        return await self._update_settings(device_id, payload)

    async def set_indoor_temperature_offset(self, device_id: str, value: int):
        """Update indoor_temperature_offset setting."""

        payload = {"settings": [{"name": "indoor_temperature_offset", "value": value}]}

        return await self._update_settings(device_id, payload)

    async def set_fanspeedselector(self, device_id: str, preset_mode: str):
        """Update set_fanspeedselector setting."""

        match preset_mode:
            case "off":
                value = FAN_SPEED_VALUE_OFF
            case "normal":
                value = FAN_SPEED_VALUE_NORMAL
            case "extra":
                value = FAN_SPEED_VALUE_EXTRA
            case _:
                raise ValueError(f"Invalid preset_mode: {preset_mode}")

        payload = {"settings": [{"name": "fanspeedselector", "value": value}]}
        if value == FAN_SPEED_VALUE_EXTRA:
            stop_time = int(
                (
                    datetime.now() + timedelta(minutes=VENTILATION_BOOST_MINUTES)
                ).timestamp()
            )
            payload["settings"].append(
                {"name": "ventilation_boost_stop", "value": stop_time}
            )

        return await self._update_settings(device_id, payload)

    async def set_room_comp_factor(self, device_id: str, value: int):
        """Update room_comp_factor setting."""

        payload = {"settings": [{"name": "room_comp_factor", "value": value}]}

        return await self._update_settings(device_id, payload)

    async def set_tap_water_capacity_target(self, device_id: str, capacity: int):
        """Update tap_water_capacity_target setting."""

        if capacity == 1:
            return await self.set_tap_water(device_id, 59, 50)

        if capacity == 6:
            return await self.set_tap_water(device_id, 74, 55)

        if capacity == 7:
            return await self.set_tap_water(device_id, 76, 55)

        payload = {
            "settings": [{"name": "tap_water_capacity_target", "value": capacity}]
        }

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

        _LOGGER.debug(f"Device metadata fetched: {self._device_metadata}")
        return self._device_metadata

    async def get_metrics(self, device_id: str, method="now"):
        """Fetch data from the API with authentication."""

        await self._ensure_valid_token()
        headers = self._request_headers()
        if self._metrics_etag:
            headers["If-None-Match"] = self._metrics_etag

        names = await self.get_available_metrics(device_id)
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

                    _LOGGER.debug(f"Metrics fetched: {data}")

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
                    _LOGGER.error(f"Authentication failure: {response.status}")
                    _LOGGER.debug(f"Authentication failure: {response}")
                    await self.unauthenticate()
                    raise APIAuthError(response)
                case 304:
                    _LOGGER.debug("Metrics not modified, using cached data.")
                case 500:
                    _LOGGER.error(
                        f"Internal server error, clearing data: {response.status}"
                    )
                    _LOGGER.debug(f"Internal server error, clearing data: {response}")
                    # await self.unauthenticate()
                    raise APIConnectionError(response)
                case _:
                    _LOGGER.error(f"Failed to fetch data, status: {response.status}")
                    _LOGGER.debug(f"Failed to fetch data, status: {response}")
                    self._metrics_data = {}

        return self._metrics_data

    async def get_available_metrics(self, device_id: str):
        """Fetch metrics from the API with authentication."""

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
                    _LOGGER.debug(f"Settings fetched: {self._settings_data}")
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
                        f"Failed to fetch settings, status: {response.status}"
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

        _LOGGER.debug(f"Primary device fetched: {device}")

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
                    _LOGGER.debug(f"Devices fetched successfully: {devices_data}")
                    return devices_data.get("devices") if devices_data else None
                case 403:
                    await self.unauthenticate()
                    raise APIAuthError(response)
                case _:
                    _LOGGER.error(f"Failed to fetch devices, status: {response.status}")
                    raise APIConnectionError(
                        f"Failed to fetch devices: {response.status}"
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
        self, response: aiohttp.ClientResponse, message: str = "API request failed"
    ):
        self.response = response
        self.status = response.status
        super().__init__(f"{message}: {response.status}")


class APIRateLimitError(Exception):
    """Exception raised for rate limiting."""

    def __init__(
        self,
        response: Optional[aiohttp.ClientResponse],
        message: str = "Rate limit exceeded"
    ):
        if response is not None:
            self.response = response
            self.status = response.status
            super().__init__(f"{message}: {response.status}")
        else:
            self.response = None
            self.status = None
            super().__init__(message)
