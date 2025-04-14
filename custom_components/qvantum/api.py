"""Qvantum API."""

import aiohttp
from datetime import datetime, timedelta
import logging, json


_LOGGER = logging.getLogger(__name__)

AUTH_URL = "https://identitytoolkit.googleapis.com"
TOKEN_URL = "https://securetoken.googleapis.com"
API_URL = "https://api.qvantum.com"
from .const import (
    FAN_SPEED_STATE_OFF,
    FAN_SPEED_STATE_NORMAL,
    FAN_SPEED_STATE_EXTRA,
    FAN_SPEED_VALUE_OFF,
    FAN_SPEED_VALUE_NORMAL,
    FAN_SPEED_VALUE_EXTRA,
)


class QvantumAPI:
    """Class for Qvantum API."""

    def __init__(self, username: str, password: str, user_agent: str) -> None:
        """Initialise."""
        self._auth_url = AUTH_URL
        self._token_url = TOKEN_URL
        self._api_url = API_URL
        self._username = username
        self._password = password
        self._user_agent = user_agent
        self._session = aiohttp.ClientSession(
            headers={"Content-Type": "application/json", "User-Agent": self._user_agent}
        )
        self._token = None
        self._refreshtoken = None
        self._token_expiry = None
        self._settings_data = {}
        self._settings_etag = None
        self._metrics_data = {}
        self._metrics_etag = None

    async def close(self):
        """Close the session."""
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

    async def authenticate(self):
        """Authenticate with the API using username and password to retrieve a token."""
        payload = {
            "returnSecureToken": "true",
            "email": self._username,
            "password": self._password,
            "clientType": "CLIENT_TYPE_WEB",
        }

        async with self._session.post(
            f"{self._auth_url}/v1/accounts:signInWithPassword?key=AIzaSyCLQ22XHjH8LmId-PB1DY8FBsN53rWTpFw",
            json=payload,
        ) as response:
            if response.status == 200:
                _LOGGER.debug(f"Authentication successful: {response.status}")
                auth_data = await response.json()
                self._token = auth_data.get("idToken")
                self._refreshtoken = auth_data.get("refreshToken")
                expires_in = auth_data.get("expiresIn", 3540)
                self._token_expiry = datetime.now() + timedelta(
                    seconds=int(expires_in) - 60
                )
            else:
                _LOGGER.error(f"Authentication failed: {response.status}")
                raise Exception(f"Authentication failed: {response}")

    async def _refresh_authentication_token(self):
        """Refresh the authentication token."""

        if not self._refreshtoken:
            return

        payload = {"grant_type": "refresh_token", "refresh_token": self._refreshtoken}

        self._token = None

        async with self._session.post(
            f"{self._token_url}/v1/token?key=AIzaSyCLQ22XHjH8LmId-PB1DY8FBsN53rWTpFw",
            json=payload,
        ) as response:
            if response.status == 200:
                _LOGGER.debug(f"Token refreshed successfully: {response.status}")
                auth_data = await response.json()
                self._token = auth_data.get("access_token")
                self._refreshtoken = auth_data.get("refresh_token")
                expires_in = auth_data.get("expires_in", 3540)
                self._token_expiry = datetime.now() + timedelta(
                    seconds=int(expires_in) - 60
                )
            else:
                _LOGGER.error(f"Token refresh failed: {response.status}")

    async def _ensure_valid_token(self):
        """Ensure a valid token is available, refreshing if expired."""
        if not self._token or datetime.now() >= self._token_expiry:
            await self._refresh_authentication_token()
            if not self._token:
                await self.authenticate()
                if not self._token:
                    raise Exception("Failed to authenticate.")

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
            stop_time = -1 # -1 means "always on"

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
            stop_time = int((datetime.now() + timedelta(minutes=120)).timestamp())
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

        payload = {
            "settings": [{"name": "tap_water_capacity_target", "value": capacity}]
        }

        return await self._update_settings(device_id, payload)

    async def set_tap_water_stop(self, device_id: str, stop: int):
        """Update tap_water_stop setting."""

        payload = {
            "settings": [{"name": "tap_water_stop", "value": stop}]
        }

        return await self._update_settings(device_id, payload)

    async def set_indoor_temperature_target(self, device_id: str, temperature: float):
        """Update indoor_temperature_target setting."""

        payload = {
            "settings": [{"name": "indoor_temperature_target", "value": temperature}]
        }

        return await self._update_settings(device_id, payload)

    async def get_metrics(self, device_id: str, method="now"):
        """Fetch data from the API with authentication."""

        await self._ensure_valid_token()
        headers = self._request_headers()
        if self._metrics_etag:
            headers["If-None-Match"] = self._metrics_etag

        async with self._session.get(
            f"{self._api_url}/api/device-info/v1/devices/{device_id}/status?metrics={method}",
            headers=headers,
        ) as response:
            if response.status == 200:
                data = await response.json()

                # "now" will return telemetry data to the API user only if current values can be returned to the caller
                if (
                    method == "now"
                    and "time" in data.get("metrics")
                    and data.get("metrics").get("time") is None
                ):
                    _LOGGER.warning(
                        f"Failed to get 'now' metrics, metrics might be stale: {data}"
                    )
                else:
                    self._metrics_data = data
                    self._metrics_etag = response.headers.get("ETag")


            elif response.status == 403:
                _LOGGER.error(f"Authentication failure: {response.status}")
                _LOGGER.debug(f"Authentication failure: {response}")
                raise APIAuthError(response)
            elif response.status == 304:
                _LOGGER.debug("Metrics not modified, using cached data.")
            elif response.status == 500:
                _LOGGER.error(f"Internal server error, clearing data: {response.status}")
                _LOGGER.debug(f"Internal server error, clearing data: {response}")
                await self.unauthenticate()
                raise APIConnectionError(response)
            else:
                _LOGGER.error(f"Failed to fetch data, status: {response.status}")
                _LOGGER.debug(f"Failed to fetch data, status: {response}")
                self._metrics_data = {}

        return self._metrics_data

    async def get_available_metrics(self, device_id: str):
        """Fetch metrics from the API with authentication."""

        await self._ensure_valid_token()

        async with self._session.get(
            f"{self._api_url}/api/inventory/v1/devices/{device_id}/metrics",
            headers=self._request_headers(),
        ) as response:
            if response.status == 200:
                return await response.json()
            elif response.status == 403:
                raise APIAuthError(response)
            else:
                _LOGGER.error(f"Failed to fetch metrics, status: {response.status}")
                return {}

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
            if response.status == 200:
                self._settings_data = await response.json()
                self._settings_etag = response.headers.get("ETag")
            elif response.status == 403:
                raise APIAuthError(response)
            elif response.status == 304:
                _LOGGER.debug("Settings not modified, using cached data.")
            elif response.status == 500:
                _LOGGER.error("Internal server error, clearing data...")
                await self.unauthenticate()
                raise APIConnectionError(response)
            else:
                _LOGGER.error(f"Failed to fetch settings, status: {response.status}")
                self._settings_data = {}

        return self._settings_data

    async def get_primary_device(self):
        """Fetch device from the API with authentication."""

        devices = await self.get_devices()
        return devices[0] if devices else None

    async def get_devices(self):
        """Fetch devices from the API with authentication."""

        await self._ensure_valid_token()

        async with self._session.get(
            f"{self._api_url}/api/inventory/v1/users/me/devices",
            headers=self._request_headers(),
        ) as response:
            if response.status == 200:
                devices_data = await response.json()
                _LOGGER.debug(f"Devices fetched successfully: {devices_data}")
                return devices_data.get("devices") if devices_data else None
            elif response.status == 403:
                raise APIAuthError(response)
            else:
                _LOGGER.error(f"Failed to fetch devices, status: {response.status}")
                raise APIConnectionError(f"Failed to fetch devices: {response.status}")


class APIAuthError(Exception):
    """Exception class for auth error."""


class APIConnectionError(Exception):
    """Exception class for connection error."""
