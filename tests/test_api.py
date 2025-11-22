"""Tests for Qvantum API."""

import datetime
import json
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.qvantum.api import QvantumAPI


def load_test_data(filename):
    """Load test data from JSON file."""
    test_data_dir = os.path.join(
        os.path.dirname(__file__), "..", "custom_components", "qvantum", "test_data"
    )
    filepath = os.path.join(test_data_dir, filename)
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


class TestQvantumAPI:
    """Test the QvantumAPI class."""

    def test_init(self):
        """Test API initialization."""
        with patch("aiohttp.ClientSession"):
            api = QvantumAPI("test@example.com", "password", "test-agent")

            assert api._username == "test@example.com"
            assert api._password == "password"
            assert api._user_agent == "test-agent"
            assert api.hass is None

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

        authenticated_api.get_available_metrics = AsyncMock(return_value=["bt1", "bt2"])

        result = await authenticated_api.get_metrics("test_device")

        assert "metrics" in result
        assert result["metrics"]["bt1"] == metrics_data["values"]["bt1"]
        assert result["metrics"]["bt2"] == metrics_data["values"]["bt2"]
        assert result["metrics"]["latency"] == metrics_data["total_latency"]

    @pytest.mark.asyncio
    async def test_get_available_metrics_with_registry(self):
        """Test getting available metrics when device registry exists."""
        api = QvantumAPI("test@example.com", "password", "test-agent")

        # Mock hass and registries
        mock_hass = MagicMock()
        mock_device_registry = MagicMock()
        mock_entity_registry = MagicMock()

        # Mock device
        mock_device = MagicMock()
        mock_device.id = "device_id"
        mock_device.identifiers = {("qvantum", "qvantum-test_device")}
        mock_device_registry.devices.values.return_value = [mock_device]

        # Mock entity
        mock_entity = MagicMock()
        mock_entity.device_id = "device_id"
        mock_entity.disabled_by = None
        mock_entity.unique_id = "qvantum_bt1_test_device"
        mock_entity_registry.entities.values.return_value = [mock_entity]

        mock_hass.data = {
            "device_registry": mock_device_registry,
            "entity_registry": mock_entity_registry,
        }
        api.hass = mock_hass

        result = await api.get_available_metrics("test_device")

        assert "bt1" in result

    @pytest.mark.asyncio
    async def test_get_available_metrics_no_registry(self):
        """Test getting available metrics when no device registry found."""
        api = QvantumAPI("test@example.com", "password", "test-agent")

        mock_hass = MagicMock()
        mock_device_registry = MagicMock()
        mock_device_registry.devices.values.return_value = []

        mock_hass.data = {"device_registry": mock_device_registry}
        api.hass = mock_hass

        result = await api.get_available_metrics("test_device")

        # Should return DEFAULT_ENABLED_METRICS
        from custom_components.qvantum.const import DEFAULT_ENABLED_METRICS

        assert result == DEFAULT_ENABLED_METRICS

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
        api.get_available_metrics = AsyncMock(return_value=["bt1", "bt2"])

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
        api.get_available_metrics = AsyncMock(return_value=["bt1"])

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
        api.get_available_metrics = AsyncMock(return_value=["bt1"])

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
    async def test_set_extra_tap_water(self, mock_session):
        """Test setting extra tap water."""
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

        result = await api.set_extra_tap_water("test_device", 60)

        assert result == update_data

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
        mock_session.patch.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        result = await api.set_fanspeedselector("test_device", "normal")

        assert result == update_data

    @pytest.mark.asyncio
    async def test_set_room_comp_factor(self, mock_session):
        """Test setting room compensation factor."""
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

        result = await api.set_room_comp_factor("test_device", 10)

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
    async def test_set_extra_tap_water_negative_minutes(self, mock_session):
        """Test setting extra tap water with negative minutes (always on)."""
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

        result = await api.set_extra_tap_water("test_device", -1)

        assert result == update_data
        # Verify the payload contains stop_time = -1 and dhw_mode = 2
        call_args = mock_session.patch.call_args
        payload = call_args[1]["json"]
        assert payload["settings"][0]["value"] == -1  # stop_time
        assert payload["settings"][1]["value"] == 2  # dhw_mode

    @pytest.mark.asyncio
    async def test_set_fanspeedselector_off(self, mock_session):
        """Test setting fan speed selector to off."""
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

        result = await api.set_fanspeedselector("test_device", "off")

        assert result == update_data

    @pytest.mark.asyncio
    async def test_set_fanspeedselector_extra(self, mock_session):
        """Test setting fan speed selector to extra (with boost stop time)."""
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

        result = await api.set_fanspeedselector("test_device", "extra")

        assert result == update_data
        # Verify ventilation_boost_stop is included
        call_args = mock_session.patch.call_args
        payload = call_args[1]["json"]
        assert len(payload["settings"]) == 2
        assert payload["settings"][1]["name"] == "ventilation_boost_stop"

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
        """Test getting device metadata with 500 error."""
        cm, mock_response = mock_session.make_cm_response(status=500)
        mock_session.get.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        with pytest.raises(Exception):  # APIConnectionError
            await api.get_device_metadata("test_device")

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
    async def test_get_metrics_500_error(self, mock_session):
        """Test getting metrics with 500 error."""
        cm, mock_response = mock_session.make_cm_response(status=500)
        mock_session.get.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
        api.get_available_metrics = AsyncMock(return_value=["bt1"])

        with pytest.raises(Exception):  # APIConnectionError
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
        api.get_available_metrics = AsyncMock(return_value=["bt1"])

        result = await api.get_metrics("test_device")

        assert result == {}

    @pytest.mark.asyncio
    async def test_update_settings_non_200_response(self, mock_session):
        """Test _update_settings with non-200 response."""
        cm, mock_response = mock_session.make_cm_response(status=400)
        mock_session.patch.return_value = cm

        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        result = await api._update_settings("test_device", {"settings": []})

        # Should still return the response data even on error status
        assert result is not None
