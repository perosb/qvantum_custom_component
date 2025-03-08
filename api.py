"""Qvantum API."""

import aiohttp
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import StrEnum
import logging
from random import choice, randrange

_LOGGER = logging.getLogger(__name__)

AUTH_URL = "https://identitytoolkit.googleapis.com"  # Separate domain for authentication
API_URL = "https://api.qvantum.com"    # Domain for data fetching


class QvantumAPI:
    """Class for Qvantum API."""

    def __init__(self, user: str, pwd: str) -> None:
        """Initialise."""
        self._auth_url = AUTH_URL
        self._api_url = API_URL
        self._username = user
        self._password = pwd
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
                expires_in = auth_data.get("expiresIn", 3600)  # Default to 1 hour if not provided
                self._token_expiry = datetime.now() + timedelta(seconds=int(expires_in))
            else:
                raise Exception(f"Authentication failed: {response.status}")

    async def _ensure_valid_token(self):
        """Ensure a valid token is available, refreshing if expired."""
        if not self._token or datetime.now() >= self._token_expiry:
            await self.authenticate()

    async def fetch_data(self, device_id: str):
        """Fetch data from the API with authentication."""

        await self._ensure_valid_token()
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        
        async with self._session.get(f"{self._api_url}/api/device-info/v1/devices/{device_id}/status?metrics=now", headers=headers) as response:
            if response.status == 200:
                self._data = await response.json()
            else:
                _LOGGER.error(f"Failed to fetch data, status: {response.status}")
                self._data = {}

        return self._data

    async def fetch_metrics(self, device_id: str):
        """Fetch metrics from the API with authentication."""

        await self._ensure_valid_token()
        headers = { "Authorization": f"Bearer {self._token}", "Content-Type": "application/json" }

        async with self._session.get(f"{self._api_url}/api/inventory/v1/devices/{device_id}/metrics", headers=headers) as response:
            if response.status == 200:
                return await response.json()
            else:
                _LOGGER.error(f"Failed to fetch metrics, status: {response.status}")
                return {}
                
    async def get_device(self):
        """Fetch device from the API with authentication."""

        await self._ensure_valid_token()
        headers = { "Authorization": f"Bearer {self._token}", "Content-Type": "application/json" }

        async with self._session.get(f"{self._api_url}/api/inventory/v1/users/me/devices", headers=headers) as response:
            if response.status == 200:
                devices_data = await response.json()
                _LOGGER.debug(f"Devices fetched successfully: {devices_data}")
                return devices_data.get('devices')[0] if devices_data.get('devices') else None
            else:
                _LOGGER.error(f"Failed to fetch devices, status: {response.status}")
                raise APIConnectionError(f"Failed to fetch devices: {response.status}")

class APIAuthError(Exception):
    """Exception class for auth error."""


class APIConnectionError(Exception):
    """Exception class for connection error."""
