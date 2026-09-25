"""Cloud client tests and the test-only Cloud XOR Modbus factory.

Modbus coverage lives in ``tests/test_api_modbus.py``; Cloud maintenance and
elevated-access coverage lives in ``tests/test_api_cloud_maintenance.py``.
"""

import datetime
import json
import logging
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from modbus_connection.mock import MockModbusConnection

from custom_components.qvantum.client.cloud import QvantumCloudClient
from custom_components.qvantum.client.exceptions import (
    APIAuthError,
    APIConnectionError,
    APIRateLimitError,
)
from custom_components.qvantum.client.modbus import QvantumModbusClient


def QvantumAPI(
    username=None,
    password=None,
    user_agent="",
    session=None,
    *,
    modbus_tcp=False,
    modbus_unit=None,
    modbus_write=False,
    **kwargs,
):
    """Test helper: construct Cloud XOR Modbus. Not a production facade."""
    if modbus_tcp:
        return QvantumModbusClient(modbus_unit, writable=modbus_write)
    return QvantumCloudClient(username, password, user_agent, session=session)


def attach_mock_modbus(client):
    """Attach an in-memory Modbus unit so tests never open a TCP socket."""
    connection = MockModbusConnection()
    unit = connection.for_unit(1)
    client.attach_unit(unit)
    return connection, client.device


def load_test_data(filename):
    """Load test data from JSON file."""
    test_data_dir = os.path.join(
        os.path.dirname(__file__), "..", "custom_components", "qvantum", "test_data"
    )
    filepath = os.path.join(test_data_dir, filename)
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


class TestQvantumCloudClient:
    """Test the Cloud client selected by the test-only client factory."""

    def test_init(self, mock_session):
        """Test API initialization."""
        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )

        assert api._username == "test@example.com"
        assert api._password == "password"
        assert api._user_agent == "test-agent"
        assert api._session == mock_session
        assert getattr(api, "hass", None) is None

    @pytest.mark.asyncio
    async def test_authenticate_success(self, mock_session):
        """Test successful authentication."""
        auth_data = load_test_data("auth_signin.json")

        cm, mock_response = mock_session.make_cm_response(
            status=200, json_data=auth_data
        )
        mock_session.post.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )

        result = await api.authenticate()

        assert result is True
        assert api._token == auth_data["idToken"]
        assert api._refreshtoken == auth_data["refreshToken"]
        assert api._token_expiry is not None

    @pytest.mark.asyncio
    async def test_authenticate_failure(self, mock_session):
        """Test authentication failure."""
        cm, mock_response = mock_session.make_cm_response(status=400)
        mock_session.post.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        with pytest.raises(Exception, match="Authentication failed"):
            await api.authenticate()

    @pytest.mark.asyncio
    async def test_authenticate_rate_limit(self, mock_session):
        """A throttled sign-in is retryable, not invalid credentials."""
        cm, mock_response = mock_session.make_cm_response(status=429)
        mock_session.post.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )

        with pytest.raises(APIRateLimitError):
            await api.authenticate()

    @pytest.mark.asyncio
    async def test_get_devices_429_error(self, mock_session):
        """Throttling must raise instead of returning partial device data."""
        cm, mock_response = mock_session.make_cm_response(status=429)
        mock_session.get.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        with pytest.raises(APIRateLimitError):
            await api.get_devices()

    @pytest.mark.asyncio
    async def test_get_devices(self, authenticated_api):
        """Test getting devices."""
        devices_data = load_test_data("devices.json")

        cm, mock_response = authenticated_api._session.make_cm_response(
            status=200, json_data=devices_data
        )
        authenticated_api._session.get.return_value = cm

        result = await authenticated_api.get_devices()

        assert result == devices_data["devices"]

    @pytest.mark.asyncio
    async def test_get_metrics(self, authenticated_api):
        """Test getting metrics."""
        metrics_data = load_test_data("metrics_test_device.json")

        cm, mock_response = authenticated_api._session.make_cm_response(
            status=200, json_data=metrics_data, headers={"ETag": "etag123"}
        )
        mock_response.headers = {"ETag": "etag123"}
        authenticated_api._session.get.return_value = cm

        result = await authenticated_api.get_metrics("test_device")

        assert "metrics" in result
        assert result["metrics"]["bt1"] == metrics_data["values"]["bt1"]
        assert result["metrics"]["bt2"] == metrics_data["values"]["bt2"]
        assert result["metrics"]["latency"] == metrics_data["total_latency"]

    @pytest.mark.asyncio
    async def test_set_tap_water(self, mock_session):
        """Test setting tap water settings."""
        settings_update_data = load_test_data("settings_update_test_device.json")

        cm, mock_response = mock_session.make_cm_response(
            status=200, json_data=settings_update_data
        )
        mock_session.patch.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        result = await api.set_tap_water("test_device", stop=60, start=50)

        assert result == settings_update_data

    def test_request_headers(self, mock_session):
        """Test request headers generation."""
        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"

        headers = api._request_headers()
        assert headers["Authorization"] == "Bearer test_token"

    @pytest.mark.asyncio
    async def test_close(self, mock_session):
        """Test closing the session."""
        mock_session.close = AsyncMock()

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        await api.close()

        # Since session was provided externally, the API shouldn't close it.
        mock_session.close.assert_not_called()
        assert api._closed is True

    @pytest.mark.asyncio
    async def test_unauthenticate(self):
        """Test unauthenticate method."""
        api = QvantumAPI("test@example.com", "password", "test-agent")
        api._token = "test_token"
        api._refreshtoken = "refresh_token"
        api._token_expiry = datetime.datetime.now()
        api._settings_data = {"test": "data"}
        api._settings_etag = "etag"
        api._metrics_data = {"test": "data"}
        api._metrics_etag = "etag"
        api._device_metadata = {"test": "data"}
        api._device_metadata_etag = "etag"

        await api.unauthenticate()

        assert api._token is None
        assert api._refreshtoken is None
        assert api._token_expiry is None
        assert api._settings_data == {}
        assert api._settings_etag is None
        assert api._metrics_data == {}
        assert api._metrics_etag is None
        assert api._device_metadata == {}
        assert api._device_metadata_etag is None

    @pytest.mark.asyncio
    async def test_refresh_token(self, mock_session):
        """Test token refresh."""
        refresh_data = {
            "access_token": "new_access_token",
            "refresh_token": "new_refresh_token",
            "expires_in": 3600,
        }

        cm, mock_response = mock_session.make_cm_response(
            status=200, json_data=refresh_data
        )
        mock_session.post.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._refreshtoken = "refresh_token"

        await api._refresh_authentication_token()

        assert api._token == refresh_data["access_token"]
        assert api._refreshtoken == refresh_data["refresh_token"]
        assert api._token_expiry is not None

    @pytest.mark.asyncio
    async def test_ensure_valid_token_raises_when_auth_fails(self):
        """_ensure_valid_token should raise when refresh/auth both fail."""
        with patch("aiohttp.ClientSession"):
            api = QvantumAPI("test@example.com", "password", "test-agent")

            # Simulate expired/missing token
            api._token = None
            api._token_expiry = datetime.datetime.now() - datetime.timedelta(seconds=1)

            # Make refresh and authenticate not set a token
            api._refresh_authentication_token = AsyncMock(return_value=None)
            api.authenticate = AsyncMock(return_value=None)

            with pytest.raises(
                Exception, match="Failed to obtain authentication token"
            ):
                await api._ensure_valid_token()

    @pytest.mark.asyncio
    async def test_ensure_valid_token_authenticates_once_on_failure(self):
        """A rejected sign-in must not be retried within one token check."""
        with patch("aiohttp.ClientSession"):
            api = QvantumAPI("test@example.com", "password", "test-agent")
            api._token = None
            api._token_expiry = datetime.datetime.now() - datetime.timedelta(seconds=1)
            api._refreshtoken = "refresh_token"
            api._refresh_authentication_token = AsyncMock(return_value=None)
            api.authenticate = AsyncMock(side_effect=APIAuthError(400))

            with pytest.raises(APIAuthError):
                await api._ensure_valid_token()

            api.authenticate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ensure_valid_token_refresh_avoids_sign_in(self):
        """A successful refresh must not fall back to a full sign-in."""
        with patch("aiohttp.ClientSession"):
            api = QvantumAPI("test@example.com", "password", "test-agent")
            api._token = "expired_token"
            api._token_expiry = datetime.datetime.now() - datetime.timedelta(seconds=1)
            api._refreshtoken = "refresh_token"

            async def fake_refresh():
                api._token = "fresh_token"

            api._refresh_authentication_token = AsyncMock(side_effect=fake_refresh)
            api.authenticate = AsyncMock()

            await api._ensure_valid_token()

            api.authenticate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ensure_valid_token_signs_in_once_without_refresh_token(self):
        """Without a refresh token, sign in exactly once."""
        with patch("aiohttp.ClientSession"):
            api = QvantumAPI("test@example.com", "password", "test-agent")
            api._token = None
            api._token_expiry = None

            async def fake_authenticate():
                api._token = "signed_in_token"
                api._token_expiry = datetime.datetime.now() + datetime.timedelta(
                    hours=1
                )

            api.authenticate = AsyncMock(side_effect=fake_authenticate)

            await api._ensure_valid_token()

            api.authenticate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test__refresh_authentication_token_no_refreshtoken_returns_none(self):
        """_refresh_authentication_token returns immediately when no refresh token is set."""
        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = MagicMock()
            mock_session_class.return_value = mock_session

            api = QvantumAPI("test@example.com", "password", "test-agent")
            api._refreshtoken = None

            result = await api._refresh_authentication_token()
            assert result is None

    @pytest.mark.asyncio
    async def test_get_metrics_token_refresh(self, mock_session):
        """Test getting metrics with token refresh."""
        metrics_data = load_test_data("metrics_test_device.json")

        # Mock refresh response
        cm_refresh, refresh_response = mock_session.make_cm_response(
            status=200,
            json_data={
                "access_token": "new_token",
                "refresh_token": "new_refresh",
                "expires_in": 3600,
            },
        )
        mock_session.post.return_value = cm_refresh

        # Mock metrics response
        cm_metrics, metrics_response = mock_session.make_cm_response(
            status=200, json_data=metrics_data, headers={"ETag": "etag123"}
        )
        metrics_response.headers = {"ETag": "etag123"}
        mock_session.get.return_value = cm_metrics

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        # Set expired token
        api._token = "old_token"
        api._token_expiry = datetime.datetime.now() - datetime.timedelta(hours=1)
        api._refreshtoken = "refresh_token"

        result = await api.get_metrics("test_device")

        assert "metrics" in result
        assert result["metrics"]["bt1"] == metrics_data["values"]["bt1"]

    @pytest.mark.asyncio
    async def test_get_metrics_403_error(self, mock_session):
        """Test getting metrics with 403 error."""
        cm, mock_response = mock_session.make_cm_response(status=403)
        mock_session.get.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        with pytest.raises(Exception):  # APIAuthError
            await api.get_metrics("test_device")

    @pytest.mark.asyncio
    async def test_get_metrics_304_not_modified(self, mock_session):
        """Test getting metrics with 304 not modified."""
        cm, mock_response = mock_session.make_cm_response(status=304)
        mock_session.get.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
        api._metrics_etag = "etag123"

        result = await api.get_metrics("test_device")

        # Should return cached data
        assert result == {}

    @pytest.mark.asyncio
    async def test_get_settings(self, mock_session):
        """Test getting settings."""
        settings_data = load_test_data("settings_test_device_123.json")

        cm, mock_response = mock_session.make_cm_response(
            status=200, json_data=settings_data, headers={"ETag": "etag123"}
        )
        mock_response.headers = {"ETag": "etag123"}
        mock_session.get.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        result = await api.get_settings("test_device")

        assert result == settings_data

    @pytest.mark.asyncio
    async def test_get_settings_304(self, mock_session):
        """Test getting settings with 304 Not Modified returns cached settings."""
        cached_data = {"settings": [{"name": "stop_heating", "value": 14}]}
        cm, mock_response = mock_session.make_cm_response(status=304)
        mock_session.get.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
        api._settings_data = cached_data

        result = await api.get_settings("test_device")

        assert result == cached_data

    @pytest.mark.asyncio
    async def test_get_settings_403_error(self, mock_session):
        """Test getting settings with 403 error raises APIAuthError."""
        cm, mock_response = mock_session.make_cm_response(status=403)
        mock_session.get.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        with pytest.raises(APIAuthError):
            await api.get_settings("test_device")

    @pytest.mark.asyncio
    async def test_get_settings_500_error(self, mock_session):
        """Test getting settings with 500 error raises APIConnectionError and preserves cache."""
        cached_data = {"settings": [{"name": "stop_heating", "value": 14}]}
        cm, mock_response = mock_session.make_cm_response(status=500)
        mock_session.get.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
        api._settings_data = cached_data

        with pytest.raises(APIConnectionError):
            await api.get_settings("test_device")

        # Verify cached data was not cleared on server error
        assert api._settings_data == cached_data

    @pytest.mark.asyncio
    async def test_get_settings_503_error(self, mock_session):
        """Test getting settings with 503 error raises APIConnectionError and preserves cache."""
        cached_data = {"settings": [{"name": "stop_heating", "value": 14}]}
        cm, mock_response = mock_session.make_cm_response(status=503)
        mock_session.get.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
        api._settings_data = cached_data

        with pytest.raises(APIConnectionError):
            await api.get_settings("test_device")

        # Verify cached data was not cleared on server error
        assert api._settings_data == cached_data

    @pytest.mark.asyncio
    async def test_get_settings_404_error(self, mock_session):
        """Test getting settings with 404 error clears cache and returns empty dict."""
        cached_data = {"settings": [{"name": "stop_heating", "value": 14}]}
        cm, mock_response = mock_session.make_cm_response(status=404)
        mock_session.get.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
        api._settings_data = cached_data

        result = await api.get_settings("test_device")

        assert result == {}
        assert api._settings_data == {}

    @pytest.mark.asyncio
    async def test_get_settings_429_error(self, mock_session):
        """Throttling must raise instead of silently serving stale settings."""
        cm, mock_response = mock_session.make_cm_response(status=429)
        mock_session.get.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        with pytest.raises(APIRateLimitError):
            await api.get_settings("test_device")

    @pytest.mark.asyncio
    async def test_set_extra_tap_water(self, mock_session):
        """Test setting extra tap water with positive minutes (duration)."""
        update_data = load_test_data("settings_update_test_device.json")

        cm, mock_response = mock_session.make_cm_response(
            status=200, json_data=update_data
        )
        mock_session.post.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        # Capture current time before API call to avoid race condition
        current_time = int(datetime.datetime.now().timestamp())

        result = await api.set_extra_tap_water("test_device", 60)

        assert result == update_data
        # Verify the payload contains the command structure
        call_args = mock_session.post.call_args
        payload = call_args[1]["json"]
        assert "command" in payload
        assert "set_additional_hot_water" in payload["command"]
        # For positive minutes, stopTime should be approximately current_time + 60 minutes
        stop_time = payload["command"]["set_additional_hot_water"]["stopTime"]
        assert isinstance(stop_time, int)
        expected_stop_time = int(
            current_time + datetime.timedelta(minutes=60).total_seconds()
        )
        assert (
            abs(stop_time - expected_stop_time) <= 3
        )  # Allow 3 second tolerance for CI/CD environments
        assert payload["command"]["set_additional_hot_water"]["indefinite"] is False
        assert payload["command"]["set_additional_hot_water"]["cancel"] is False

    @pytest.mark.asyncio
    async def test_set_extra_tap_water_cancel(self, mock_session):
        """Test canceling extra tap water (minutes == 0)."""
        update_data = load_test_data("settings_update_test_device.json")

        cm, mock_response = mock_session.make_cm_response(
            status=200, json_data=update_data
        )
        mock_session.post.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        # Capture current time before API call to avoid race condition
        current_time = int(datetime.datetime.now().timestamp())

        result = await api.set_extra_tap_water("test_device", 0)

        assert result == update_data
        # Verify the payload contains the command structure
        call_args = mock_session.post.call_args
        payload = call_args[1]["json"]
        assert "command" in payload
        assert "set_additional_hot_water" in payload["command"]
        # For cancel (minutes == 0), stopTime should be current timestamp
        stop_time = payload["command"]["set_additional_hot_water"]["stopTime"]
        assert isinstance(stop_time, int)
        assert (
            abs(stop_time - current_time) <= 3
        )  # Allow 3 second tolerance for CI/CD environments
        assert payload["command"]["set_additional_hot_water"]["indefinite"] is False
        assert payload["command"]["set_additional_hot_water"]["cancel"] is True

    @pytest.mark.asyncio
    async def test_set_indoor_temperature_offset(self, mock_session):
        """Test setting indoor temperature offset."""
        update_data = load_test_data("settings_update_test_device.json")

        cm, mock_response = mock_session.make_cm_response(
            status=200, json_data=update_data
        )
        mock_session.patch.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        result = await api.set_indoor_temperature_offset("test_device", 5)

        assert result == update_data

    @pytest.mark.asyncio
    async def test_set_fanspeedselector(self, mock_session):
        """Test setting fan speed selector."""
        update_data = load_test_data("settings_update_test_device.json")

        cm, mock_response = mock_session.make_cm_response(
            status=200, json_data=update_data
        )
        mock_session.post.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        result = await api.set_fanspeedselector("test_device", "normal")

        assert result == update_data

    @pytest.mark.asyncio
    async def test_set_tap_water_capacity_target(self, mock_session):
        """Test setting tap water capacity target."""
        update_data = load_test_data("settings_update_test_device.json")

        cm, mock_response = mock_session.make_cm_response(
            status=200, json_data=update_data
        )
        mock_session.patch.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        result = await api.set_tap_water_capacity_target("test_device", 5)

        assert result == update_data

    @pytest.mark.asyncio
    async def test_set_indoor_temperature_target(self, mock_session):
        """Test setting indoor temperature target."""
        update_data = load_test_data("settings_update_test_device.json")

        cm, mock_response = mock_session.make_cm_response(
            status=200, json_data=update_data
        )
        mock_session.patch.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        result = await api.set_indoor_temperature_target("test_device", 22.5)

        assert result == update_data

    @pytest.mark.asyncio
    async def test_get_primary_device(self, authenticated_api):
        """Test getting primary device."""
        devices_data = load_test_data("devices.json")
        metadata_data = load_test_data("device_metadata_test_device_123.json")

        # Mock devices response
        cm_devices, devices_response = authenticated_api._session.make_cm_response(
            status=200, json_data=devices_data
        )

        # Mock metadata response
        cm_meta, metadata_response = authenticated_api._session.make_cm_response(
            status=200, json_data=metadata_data, headers={"ETag": "etag123"}
        )

        # make get return context managers in sequence
        authenticated_api._session.get.side_effect = [cm_devices, cm_meta]

        result = await authenticated_api.get_primary_device()

        assert result is not None
        assert result["id"] == devices_data["devices"][0]["id"]

    @pytest.mark.asyncio
    async def test_refresh_token_failure(self, mock_session):
        """Test token refresh failure."""
        cm, mock_response = mock_session.make_cm_response(status=400)
        mock_session.post.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._refreshtoken = "refresh_token"

        await api._refresh_authentication_token()

        # Token should be None after failed refresh
        assert api._token is None

    @pytest.mark.asyncio
    async def test_refresh_token_rate_limit_propagates(self, mock_session):
        """A throttled refresh must not fall back to a full sign-in."""
        cm, mock_response = mock_session.make_cm_response(status=429)
        mock_session.post.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._refreshtoken = "refresh_token"
        api._token = None

        with pytest.raises(APIRateLimitError):
            await api._ensure_valid_token()

        assert mock_session.post.call_count == 1

    @pytest.mark.asyncio
    async def test_set_extra_tap_water_negative_minutes(self, mock_session):
        """Test setting extra tap water with negative minutes (always on)."""
        update_data = load_test_data("settings_update_test_device.json")

        cm, mock_response = mock_session.make_cm_response(
            status=200, json_data=update_data
        )
        mock_session.post.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        result = await api.set_extra_tap_water("test_device", -1)

        assert result == update_data
        # Verify the payload contains the command structure
        call_args = mock_session.post.call_args
        payload = call_args[1]["json"]
        assert "command" in payload
        assert "set_additional_hot_water" in payload["command"]
        assert payload["command"]["set_additional_hot_water"]["stopTime"] == -1
        assert payload["command"]["set_additional_hot_water"]["indefinite"] is True
        assert payload["command"]["set_additional_hot_water"]["cancel"] is False

    @pytest.mark.asyncio
    async def test_set_fanspeedselector_off(self, mock_session):
        """Test setting fan speed selector to off."""
        update_data = load_test_data("settings_update_test_device.json")

        cm, mock_response = mock_session.make_cm_response(
            status=200, json_data=update_data
        )
        mock_session.post.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        result = await api.set_fanspeedselector("test_device", "off")

        assert result == update_data

    @pytest.mark.asyncio
    async def test_set_fanspeedselector_extra(self, mock_session):
        """Test setting fan speed selector to extra (with boost stop time)."""
        update_data = load_test_data("settings_update_test_device.json")

        cm, mock_response = mock_session.make_cm_response(
            status=200, json_data=update_data
        )
        mock_session.post.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        result = await api.set_fanspeedselector("test_device", "extra")

        assert result == update_data

    @pytest.mark.asyncio
    async def test_set_fanspeedselector_invalid_preset(self, mock_session):
        """Test setting fan speed selector with invalid preset mode."""
        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )

        with pytest.raises(ValueError, match="Invalid preset_mode: invalid"):
            await api.set_fanspeedselector("test_device", "invalid")

    @pytest.mark.asyncio
    async def test_set_tap_water_capacity_target_1(self, mock_session):
        """Test setting tap water capacity target to 1."""
        update_data = load_test_data("settings_update_test_device.json")

        cm, mock_response = mock_session.make_cm_response(
            status=200, json_data=update_data
        )
        mock_session.patch.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        result = await api.set_tap_water_capacity_target("test_device", 1)

        assert result == update_data
        # Should call set_tap_water with stop=59, start=50
        mock_session.patch.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_tap_water_capacity_target_6(self, mock_session):
        """Test setting tap water capacity target to 6."""
        update_data = load_test_data("settings_update_test_device.json")

        cm, mock_response = mock_session.make_cm_response(
            status=200, json_data=update_data
        )
        mock_session.patch.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        result = await api.set_tap_water_capacity_target("test_device", 6)

        assert result == update_data

    @pytest.mark.asyncio
    async def test_set_tap_water_capacity_target_7(self, mock_session):
        """Test setting tap water capacity target to 7."""
        update_data = load_test_data("settings_update_test_device.json")

        cm, mock_response = mock_session.make_cm_response(
            status=200, json_data=update_data
        )
        mock_session.patch.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        result = await api.set_tap_water_capacity_target("test_device", 7)

        assert result == update_data

    @pytest.mark.asyncio
    async def test_set_tap_water_only_stop(self, mock_session):
        """Test setting tap water with only stop parameter."""
        update_data = load_test_data("settings_update_test_device.json")

        cm, mock_response = mock_session.make_cm_response(
            status=200, json_data=update_data
        )
        mock_session.patch.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        result = await api.set_tap_water("test_device", stop=60)

        assert result == update_data
        call_args = mock_session.patch.call_args
        payload = call_args[1]["json"]
        assert len(payload["settings"]) == 1
        assert payload["settings"][0]["name"] == "tap_water_stop"
        assert payload["settings"][0]["value"] == 60

    @pytest.mark.asyncio
    async def test_set_tap_water_only_start(self, mock_session):
        """Test setting tap water with only start parameter."""
        update_data = load_test_data("settings_update_test_device.json")

        cm, mock_response = mock_session.make_cm_response(
            status=200, json_data=update_data
        )
        mock_session.patch.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        result = await api.set_tap_water("test_device", start=50)

        assert result == update_data
        call_args = mock_session.patch.call_args
        payload = call_args[1]["json"]
        assert len(payload["settings"]) == 1
        assert payload["settings"][0]["name"] == "tap_water_start"
        assert payload["settings"][0]["value"] == 50

    @pytest.mark.asyncio
    async def test_set_tap_water_no_settings(self, mock_session):
        """Test setting tap water with no parameters (should return early)."""
        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        result = await api.set_tap_water("test_device")

        assert result is None
        mock_session.patch.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_device_metadata_500_error(self, mock_session):
        """Test getting device metadata with 500 error raises APIConnectionError and preserves cache."""
        cached_data = {"id": "test_device", "model": "QE-6"}
        cm, mock_response = mock_session.make_cm_response(status=500)
        mock_session.get.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
        api._device_metadata = cached_data

        with pytest.raises(APIConnectionError):
            await api.get_device_metadata("test_device")

        assert api._device_metadata == cached_data

    @pytest.mark.asyncio
    async def test_get_device_metadata_503_error(self, mock_session):
        """Test getting device metadata with 503 error raises APIConnectionError and preserves cache."""
        cached_data = {"id": "test_device", "model": "QE-6"}
        cm, mock_response = mock_session.make_cm_response(status=503)
        mock_session.get.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
        api._device_metadata = cached_data

        with pytest.raises(APIConnectionError):
            await api.get_device_metadata("test_device")

        assert api._device_metadata == cached_data

    @pytest.mark.asyncio
    async def test_get_device_metadata_404_error(self, mock_session):
        """Test getting device metadata with 404 error."""
        cm, mock_response = mock_session.make_cm_response(status=404)
        mock_session.get.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        result = await api.get_device_metadata("test_device")

        assert result == {}

    @pytest.mark.asyncio
    async def test_get_device_metadata_429_error(self, mock_session):
        """A 429 must preserve cached metadata instead of clearing it."""
        cached_data = {"id": "test_device", "model": "QE-6"}
        cm, mock_response = mock_session.make_cm_response(status=429)
        mock_session.get.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
        api._device_metadata = cached_data

        with pytest.raises(APIRateLimitError):
            await api.get_device_metadata("test_device")

        assert api._device_metadata == cached_data

    @pytest.mark.asyncio
    async def test_get_metrics_500_error(self, mock_session):
        """Test getting metrics with 500 error."""
        cm, mock_response = mock_session.make_cm_response(status=500)
        mock_session.get.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        with pytest.raises(APIConnectionError):
            await api.get_metrics("test_device")

    @pytest.mark.asyncio
    async def test_get_metrics_503_error(self, mock_session):
        """Test getting metrics with 503 error."""
        cm, mock_response = mock_session.make_cm_response(status=503)
        mock_session.get.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        with pytest.raises(APIConnectionError):
            await api.get_metrics("test_device")

    @pytest.mark.asyncio
    async def test_get_metrics_404_error(self, mock_session):
        """Test getting metrics with 404 error."""
        cm, mock_response = mock_session.make_cm_response(status=404)
        mock_session.get.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        result = await api.get_metrics("test_device")

        assert result == {}

    @pytest.mark.asyncio
    async def test_get_metrics_429_error(self, mock_session):
        """Throttling must raise instead of silently serving stale metrics."""
        cm, mock_response = mock_session.make_cm_response(status=429)
        mock_session.get.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        with pytest.raises(APIRateLimitError):
            await api.get_metrics("test_device")

    @pytest.mark.asyncio
    async def test_get_metrics_with_custom_enabled_metrics(self, authenticated_api):
        """Test getting metrics with custom enabled_metrics list."""
        metrics_data = load_test_data("metrics_test_device.json")

        cm, mock_response = authenticated_api._session.make_cm_response(
            status=200, json_data=metrics_data, headers={"ETag": "etag123"}
        )
        mock_response.headers = {"ETag": "etag123"}
        authenticated_api._session.get.return_value = cm

        # Test with custom enabled metrics list
        custom_metrics = ["bt1", "latency"]
        result = await authenticated_api.get_metrics(
            "test_device", enabled_metrics=custom_metrics
        )

        assert "metrics" in result
        assert "bt1" in result["metrics"]
        assert "bt2" not in result["metrics"]  # bt2 should not be included
        assert result["metrics"]["bt1"] == metrics_data["values"]["bt1"]

    @pytest.mark.asyncio
    async def test_get_metrics_warns_once_per_missing_metric(
        self, authenticated_api, caplog
    ):
        """Each missing metric warns once instead of on every poll."""
        metrics_data = load_test_data("metrics_test_device.json")
        cm, _ = authenticated_api._session.make_cm_response(
            status=200, json_data=metrics_data, headers={"ETag": "etag123"}
        )
        authenticated_api._session.get.return_value = cm

        missing = ["missing_one", "missing_two"]
        enabled = ["bt1", *missing]
        with caplog.at_level(
            logging.WARNING, logger="custom_components.qvantum.client.cloud.client"
        ):
            await authenticated_api.get_metrics("test_device", enabled_metrics=enabled)
            await authenticated_api.get_metrics("test_device", enabled_metrics=enabled)

        warnings = [
            record.getMessage()
            for record in caplog.records
            if record.levelno >= logging.WARNING
        ]
        assert len(warnings) == 2
        for metric_name in missing:
            assert sum(metric_name in message for message in warnings) == 1, (
                f"expected exactly one warning for {metric_name}"
            )

    @pytest.mark.asyncio
    async def test_missing_metric_warning_repeats_after_unauthenticate(
        self, authenticated_api, caplog
    ):
        """A new session (unauthenticate) may warn about the same metric again."""
        metrics_data = load_test_data("metrics_test_device.json")
        cm, _ = authenticated_api._session.make_cm_response(
            status=200, json_data=metrics_data, headers={"ETag": "etag123"}
        )
        authenticated_api._session.get.return_value = cm

        enabled = ["bt1", "missing_metric"]
        with caplog.at_level(
            logging.WARNING, logger="custom_components.qvantum.client.cloud.client"
        ):
            await authenticated_api.get_metrics("test_device", enabled_metrics=enabled)
            await authenticated_api.unauthenticate()
            # The next poll re-authenticates; keep the mocked credentials valid.
            authenticated_api._token = "test_token"
            authenticated_api._token_expiry = datetime.datetime(2100, 1, 1)
            api_metrics = await authenticated_api.get_metrics(
                "test_device", enabled_metrics=enabled
            )

        assert "bt1" in api_metrics["metrics"]
        warnings = [
            record.getMessage()
            for record in caplog.records
            if record.levelno >= logging.WARNING
            and "missing_metric" in record.getMessage()
        ]
        assert len(warnings) == 2

    @pytest.mark.asyncio
    async def test_get_metrics_with_empty_enabled_metrics(self, authenticated_api):
        """Test getting metrics with empty enabled_metrics list."""
        metrics_data = load_test_data("metrics_test_device.json")

        cm, mock_response = authenticated_api._session.make_cm_response(
            status=200, json_data=metrics_data, headers={"ETag": "etag123"}
        )
        mock_response.headers = {"ETag": "etag123"}
        authenticated_api._session.get.return_value = cm

        # Test with empty enabled metrics list
        result = await authenticated_api.get_metrics("test_device", enabled_metrics=[])

        assert "metrics" in result
        # Should only contain hpid and latency (no actual metrics)
        assert "hpid" in result["metrics"]
        assert result["metrics"]["hpid"] == "test_device"
        assert result["metrics"]["latency"] == metrics_data["total_latency"]
        # No bt1 or bt2 should be present
        assert "bt1" not in result["metrics"]
        assert "bt2" not in result["metrics"]

    @pytest.mark.asyncio
    async def test_get_metrics_with_none_enabled_metrics(self, authenticated_api):
        """Test getting metrics with None enabled_metrics (should use defaults)."""
        metrics_data = load_test_data("metrics_test_device.json")

        cm, mock_response = authenticated_api._session.make_cm_response(
            status=200, json_data=metrics_data, headers={"ETag": "etag123"}
        )
        mock_response.headers = {"ETag": "etag123"}
        authenticated_api._session.get.return_value = cm

        # Test with None enabled_metrics (should default to DEFAULT_ENABLED_METRICS)
        result = await authenticated_api.get_metrics(
            "test_device", enabled_metrics=None
        )

        assert "metrics" in result
        # Should contain default metrics
        assert result["metrics"]["bt1"] == metrics_data["values"]["bt1"]
        assert result["metrics"]["bt2"] == metrics_data["values"]["bt2"]
        assert result["metrics"]["latency"] == metrics_data["total_latency"]

    @pytest.mark.asyncio
    async def test_handle_response_rate_limits_and_auth_error(self):
        from custom_components.qvantum.client.exceptions import APIAuthError, APIRateLimitError

        class DummyResponse:
            def __init__(self, status):
                self.status = status
                self.ok = False

        api = QvantumAPI("test@example.com", "password", "test-agent")

        with pytest.raises(APIAuthError):
            await api._handle_response(DummyResponse(401))

        with pytest.raises(APIRateLimitError):
            await api._handle_response(DummyResponse(429))

    @pytest.mark.asyncio
    async def test_set_smartcontrol_off_and_on(self, mock_session):
        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        cm, _ = mock_session.make_cm_response(status=200, json_data={"command": {}})
        mock_session.post.return_value = cm

        result_off = await api.set_smartcontrol("test_device", -1, -1)
        assert result_off == {"command": {}}

        result_on = await api.set_smartcontrol("test_device", 1, 1)
        assert result_on == {"command": {}}

    @pytest.mark.asyncio
    async def test_close_closes_owned_session(self, mock_session):
        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._session_owner = True
        mock_session.close = AsyncMock()

        await api.close()

        mock_session.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handle_response_403_unauthenticates_and_raises_auth_error(self):
        """403 means the stored token was rejected: drop it for a reauth."""
        from custom_components.qvantum.client.exceptions import APIAuthError

        class DummyResponse:
            status = 403
            ok = False

        api = QvantumAPI("test@example.com", "password", "test-agent")
        api._token = "stale_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        with pytest.raises(APIAuthError):
            await api._handle_response(DummyResponse())

        assert api._token is None

    @pytest.mark.asyncio
    async def test_get_device_metadata_403_raises_auth_error(self, mock_session):
        from custom_components.qvantum.client.exceptions import APIAuthError

        cm, _ = mock_session.make_cm_response(status=403)
        mock_session.get.return_value = cm

        api = QvantumAPI("test@example.com", "password", "test-agent", session=mock_session)
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        with pytest.raises(APIAuthError):
            await api.get_device_metadata("test_device")

    @pytest.mark.asyncio
    async def test_get_device_metadata_uses_device_metadata_etag(self, mock_session):
        """Metadata requests should use _device_metadata_etag, not _metrics_etag."""
        cm, _ = mock_session.make_cm_response(status=304)
        mock_session.get.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
        api._metrics_etag = "metrics-etag"
        api._device_metadata_etag = "metadata-etag"

        await api.get_device_metadata("test_device")

        call_args = mock_session.get.call_args
        assert call_args[1]["headers"]["If-None-Match"] == "metadata-etag"

    @pytest.mark.asyncio
    async def test_request_json_omits_json_when_payload_not_provided(
        self, mock_session
    ):
        """_request_json should not send a json body when no payload is provided."""
        cm, _ = mock_session.make_cm_response(status=200, json_data={"ok": True})
        mock_session.post.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        result = await api._request_json("post", "https://example.test/endpoint")

        assert result == {"ok": True}
        call_args = mock_session.post.call_args
        assert "json" not in call_args[1]
        assert call_args[1]["headers"]["Authorization"] == "Bearer test_token"

    @pytest.mark.asyncio
    async def test_request_json_validate_status_raises_on_non_2xx(self, mock_session):
        """_request_json should raise when validate_status is enabled for non-2xx."""
        from custom_components.qvantum.client.exceptions import APIConnectionError

        cm, _ = mock_session.make_cm_response(status=400, json_data={"error": "bad"})
        mock_session.post.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        with pytest.raises(APIConnectionError):
            await api._request_json(
                "post", "https://example.test/endpoint", validate_status=True
            )

    @pytest.mark.asyncio
    async def test_send_command_wraps_payload_in_command(self, mock_session):
        """_send_command should wrap payload in a top-level command object."""
        cm, _ = mock_session.make_cm_response(status=200, json_data={"ok": True})
        mock_session.post.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        await api._send_command("test_device", {"set_fan_mode": {"mode": 0}})

        call_args = mock_session.post.call_args
        assert call_args[1]["json"] == {"command": {"set_fan_mode": {"mode": 0}}}

    @pytest.mark.asyncio
    async def test_send_command_non_2xx_raises(self, mock_session):
        """A failed command must surface the HTTP error, not return its body."""
        cm, mock_response = mock_session.make_cm_response(
            status=500, json_data={"error": "boom"}
        )
        mock_session.post.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        with pytest.raises(APIConnectionError):
            await api.set_fanspeedselector("test_device", "off")

    @pytest.mark.asyncio
    async def test_set_heating_curve_point_uses_cloud_ud_curve_key(
        self, mock_session
    ):
        cm, _ = mock_session.make_cm_response(status=200, json_data={"status": "APPLIED"})
        mock_session.post.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        await api.set_heating_curve_point("test_device", "curve_minus_30", 59)
        await api.set_curve_type_heating("test_device", 1)

        payloads = [call.kwargs["json"] for call in mock_session.post.call_args_list]
        assert payloads[0] == {
            "command": {"update_settings": {"ud_curve_minus30": 59}}
        }
        assert payloads[1] == {
            "command": {"update_settings": {"curve_type_heating": 1}}
        }

    @pytest.mark.asyncio
    async def test_update_settings_non_200_response(self, mock_session):
        """A failed settings write must raise instead of returning an error body."""
        from custom_components.qvantum.client.exceptions import APIConnectionError

        cm, mock_response = mock_session.make_cm_response(status=400)
        mock_session.patch.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        with pytest.raises(APIConnectionError):
            await api._update_settings("test_device", {"settings": []})

    # --- get_http_metrics tests ---

    @pytest.mark.asyncio
    async def test_get_http_metrics_returns_requested_metrics(self, authenticated_api):
        """Test that get_http_metrics returns only the requested metric names."""
        cm, _ = authenticated_api._session.make_cm_response(
            status=200,
            json_data={"values": {"tap_stop": 65, "bt1": 5.2, "extra_field": 99}},
            headers={"ETag": "abc123"},
        )
        authenticated_api._session.get.return_value = cm

        result = await authenticated_api.get_http_metrics("dev1", ["tap_stop", "bt1"])

        assert result == {"metrics": {"tap_stop": 65, "bt1": 5.2}}

    @pytest.mark.asyncio
    async def test_get_http_metrics_missing_metric_omitted(self, authenticated_api):
        """Test that metrics absent from the response are silently omitted."""
        cm, _ = authenticated_api._session.make_cm_response(
            status=200,
            json_data={"values": {"tap_stop": 70}},
            headers={"ETag": "abc123"},
        )
        authenticated_api._session.get.return_value = cm

        result = await authenticated_api.get_http_metrics(
            "dev1", ["tap_stop", "missing_metric"]
        )

        assert result == {"metrics": {"tap_stop": 70}}
        assert "missing_metric" not in result["metrics"]

    @pytest.mark.asyncio
    async def test_get_http_metrics_returns_empty_on_304(self, authenticated_api):
        """Test that get_http_metrics returns empty metrics dict on 304 Not Modified."""
        cm, _ = authenticated_api._session.make_cm_response(status=304)
        authenticated_api._session.get.return_value = cm

        result = await authenticated_api.get_http_metrics("dev1", ["tap_stop"])

        assert result == {"metrics": {}}

    @pytest.mark.asyncio
    async def test_get_http_metrics_returns_empty_on_empty_values(
        self, authenticated_api
    ):
        """Test that get_http_metrics returns empty metrics dict when values dict is empty."""
        cm, _ = authenticated_api._session.make_cm_response(
            status=200,
            json_data={"values": {}},
            headers={"ETag": "xyz"},
        )
        authenticated_api._session.get.return_value = cm

        result = await authenticated_api.get_http_metrics("dev1", ["tap_stop"])

        assert result == {"metrics": {}}
