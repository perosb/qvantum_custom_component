"""Qvantum API."""

import aiohttp
from datetime import datetime, timedelta
import logging, json


_LOGGER = logging.getLogger(__name__)

AUTH_URL = "https://identitytoolkit.googleapis.com"  # Separate domain for authentication
API_URL = "https://api.qvantum.com"    # Domain for data fetching


class QvantumAPI:
    """Class for Qvantum API."""

    def __init__(self, username: str, password: str) -> None:
        """Initialise."""
        self._auth_url = AUTH_URL
        self._api_url = API_URL
        self._username = username
        self._password = password
        self._session = aiohttp.ClientSession()
        self._data = {}
        self._token = None
        self._token_expiry = None

    async def close(self):
        """Close the session."""
        await self._session.close()

    async def authenticate(self):
        """Authenticate with the API using username and password to retrieve a token."""
        headers = {"Content-Type": "application/json"}
        payload = {
            "returnSecureToken":"true",
            "email":self._username,
            "password":self._password,
            "clientType":"CLIENT_TYPE_WEB"
        }

        async with self._session.post(f"{self._auth_url}/v1/accounts:signInWithPassword?key=AIzaSyCLQ22XHjH8LmId-PB1DY8FBsN53rWTpFw", json=payload, headers=headers) as response:
            if response.status == 200:
                auth_data = await response.json()
                self._token = auth_data.get("idToken")
                expires_in = auth_data.get("expiresIn", 3540)  # Default to 1 hour if not provided
                self._token_expiry = datetime.now() + timedelta(seconds=int(expires_in)-60)
            else:
                raise Exception(f"Authentication failed: {response}")


    async def _update_settings(self, device_id: str, payload: dict):
        """Update one or several settings."""

        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

        _LOGGER.debug(json.dumps(payload))

        async with self._session.patch(f"{self._api_url}/api/device-info/v1/devices/{device_id}/settings?dispatch=false", json=payload, headers=headers) as response:
            data = await response.json()
            _LOGGER.debug(data)
            return data

    async def set_extra_tap_water(self, device_id: str, minutes: int):
        """Update extra_tap_water setting."""

        enable = minutes > 0
        stop_time = int((datetime.now() + timedelta(minutes=minutes)).timestamp())

        payload = {
            "settings": [
                {
                    "name": "extra_tap_water",
                    "value": enable
                },
                {
                    "name": "extra_tap_water_stop",
                    "value": stop_time
                }
            ]
        }

        return await self._update_settings(device_id, payload)

    async def set_indoor_temperature_offset(self, device_id: str, value: int):
        """Update indoor_temperature_offset setting."""

        payload = {
            "settings": [
                {
                    "name": "indoor_temperature_offset",
                    "value": value
                }
            ]
        }

        return await self._update_settings(device_id, payload)

    async def set_room_comp_factor(self, device_id: str, value: int):
        """Update room_comp_factor setting."""

        payload = {
            "settings": [
                {
                    "name": "room_comp_factor",
                    "value": value
                }
            ]
        }

        return await self._update_settings(device_id, payload)

    async def set_tap_water_capacity_target(self, device_id: str, capacity: int):
        """Update tap_water_capacity_target setting."""

        payload = {
            "settings": [
                {
                    "name": "tap_water_capacity_target",
                    "value": capacity
                }
            ]
        }

        return await self._update_settings(device_id, payload)

    async def set_indoor_temperature_target(self, device_id: str, temperature: float):
        """Update indoor_temperature_target setting."""

        payload = {
            "settings": [
                {
                    "name": "indoor_temperature_target",
                    "value": temperature
                }
            ]
        }

        return await self._update_settings(device_id, payload)

    async def _ensure_valid_token(self):
        """Ensure a valid token is available, refreshing if expired."""
        if not self._token or datetime.now() >= self._token_expiry:
            await self.authenticate()

    async def get_metrics(self, device_id: str, method="now"):
        """Fetch data from the API with authentication."""

        await self._ensure_valid_token()
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        
        async with self._session.get(f"{self._api_url}/api/device-info/v1/devices/{device_id}/status?metrics={method}", headers=headers) as response:
            if response.status == 200:
                self._data = await response.json()

                # "now" will return telemetry data to the API user only if current values can be returned to the caller
                # "last" will return the "most recent metrics" device has reported, if the device has been connected last 7 days.                
                # now (recent) or last (less recent if device offline short time period)
                if method == "now" and \
                    "time" in self._data.get("metrics") and \
                    self._data.get("metrics").get("time") == None:
                    _LOGGER.warning(f"Failed to get 'now' metrics, falling back to 'last': {self._data}")
                    return await self.get_metrics(device_id=device_id, method="last")
                
            elif response.status == 403:
                raise APIAuthError(response)
            else:
                _LOGGER.error(f"Failed to fetch data, status: {response.status}")
                self._data = {}

        return self._data
    async def get_available_metrics(self, device_id: str):
        """Fetch metrics from the API with authentication."""

        await self._ensure_valid_token()
        headers = { "Authorization": f"Bearer {self._token}", "Content-Type": "application/json" }

        async with self._session.get(f"{self._api_url}/api/inventory/v1/devices/{device_id}/metrics", headers=headers) as response:
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
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        
        async with self._session.get(f"{self._api_url}/api/device-info/v1/devices/{device_id}/settings", headers=headers) as response:
            if response.status == 200:
                self._data = await response.json()
            elif response.status == 403:
                raise APIAuthError(response)
            else:
                _LOGGER.error(f"Failed to fetch settings, status: {response.status}")
                self._data = {}

        return self._data


    async def get_primary_device(self):
        """Fetch device from the API with authentication."""

        devices = await self.get_devices()
        return devices[0] if devices else None

    async def get_devices(self):
        """Fetch devices from the API with authentication."""

        await self._ensure_valid_token()
        headers = { "Authorization": f"Bearer {self._token}", "Content-Type": "application/json" }

        async with self._session.get(f"{self._api_url}/api/inventory/v1/users/me/devices", headers=headers) as response:
            if response.status == 200:
                devices_data = await response.json()
                _LOGGER.debug(f"Devices fetched successfully: {devices_data}")
                return devices_data.get('devices') if devices_data else None
            elif response.status == 403:
                raise APIAuthError(response)
            else:
                _LOGGER.error(f"Failed to fetch devices, status: {response.status}")
                raise APIConnectionError(f"Failed to fetch devices: {response.status}")

class APIAuthError(Exception):
    """Exception class for auth error."""


class APIConnectionError(Exception):
    """Exception class for connection error."""
