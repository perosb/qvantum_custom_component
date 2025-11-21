"""Tests for Qvantum API."""

import sys
import datetime
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Mock HA imports before importing QvantumAPI
const_mock = MagicMock()
const_mock.DOMAIN = "qvantum"
const_mock.FAN_SPEED_STATE_OFF = "off"
const_mock.FAN_SPEED_STATE_NORMAL = "normal"
const_mock.FAN_SPEED_STATE_EXTRA = "extra"
const_mock.FAN_SPEED_VALUE_OFF = 0
const_mock.FAN_SPEED_VALUE_NORMAL = 1
const_mock.FAN_SPEED_VALUE_EXTRA = 2
const_mock.DEFAULT_ENABLED_METRICS = ["bt1", "bt2"]
const_mock.DEFAULT_DISABLED_METRICS = ["bt3", "bt4"]
sys.modules['custom_components.qvantum.const'] = const_mock

# Import the real QvantumAPI after mocking
from custom_components.qvantum.api import QvantumAPI


class TestQvantumAPI:
    """Test the QvantumAPI class."""

    def test_init(self):
        """Test API initialization."""
        with patch('aiohttp.ClientSession'):
            api = QvantumAPI("test@example.com", "password", "test-agent")

            assert api._username == "test@example.com"
            assert api._password == "password"
            assert api._user_agent == "test-agent"
            assert api.hass is None

    @pytest.mark.asyncio
    async def test_authenticate_success(self):
        """Test successful authentication."""
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = MagicMock()
            mock_session_class.return_value = mock_session

            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"idToken": "test_token", "refreshToken": "refresh_token", "expiresIn": "3600"})

            mock_session.post.return_value.__aenter__ = AsyncMock(return_value=mock_response)
            mock_session.post.return_value.__aexit__ = AsyncMock(return_value=None)

            api = QvantumAPI("test@example.com", "password", "test-agent")
            result = await api.authenticate()

            assert result is True
            assert api._token == "test_token"
            assert api._refreshtoken == "refresh_token"
            assert api._token_expiry is not None

    @pytest.mark.asyncio
    async def test_authenticate_failure(self):
        """Test authentication failure."""
        api = QvantumAPI("test@example.com", "password", "test-agent")

        mock_response = MagicMock()
        mock_response.status = 400

        with patch("aiohttp.ClientSession.post", return_value=mock_response):
            with pytest.raises(Exception, match="Authentication failed"):
                await api.authenticate()

    @pytest.mark.asyncio
    async def test_get_devices(self):
        """Test getting devices."""
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = MagicMock()
            mock_session_class.return_value = mock_session

            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"devices": [{"id": "device1"}]})

            mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_response)
            mock_session.get.return_value.__aexit__ = AsyncMock(return_value=None)

            api = QvantumAPI("test@example.com", "password", "test-agent")
            api._token = "test_token"
            api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

            result = await api.get_devices()

            assert result == [{"id": "device1"}]

    @pytest.mark.asyncio
    async def test_get_metrics(self):
        """Test getting metrics."""
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = MagicMock()
            mock_session_class.return_value = mock_session

            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.headers = {"ETag": "etag123"}
            mock_response.json = AsyncMock(return_value={"values": {"bt1": 20.5, "bt2": 15.0}, "total_latency": 100})

            mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_response)
            mock_session.get.return_value.__aexit__ = AsyncMock(return_value=None)

            api = QvantumAPI("test@example.com", "password", "test-agent")
            api._token = "test_token"
            api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
            api.get_available_metrics = AsyncMock(return_value=["bt1", "bt2"])

            result = await api.get_metrics("test_device")

            assert "metrics" in result
            assert result["metrics"]["bt1"] == 20.5
            assert result["metrics"]["bt2"] == 15.0
            assert result["metrics"]["latency"] == 100

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
            "entity_registry": mock_entity_registry
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
    async def test_set_tap_water(self):
        """Test setting tap water settings."""
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = MagicMock()
            mock_session_class.return_value = mock_session

            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"success": True})

            mock_session.patch.return_value.__aenter__ = AsyncMock(return_value=mock_response)
            mock_session.patch.return_value.__aexit__ = AsyncMock(return_value=None)

            api = QvantumAPI("test@example.com", "password", "test-agent")
            api._token = "test_token"
            api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

            result = await api.set_tap_water("test_device", stop=60, start=50)

            assert result == {"success": True}

    def test_request_headers(self):
        """Test request headers generation."""
        with patch('aiohttp.ClientSession'):
            api = QvantumAPI("test@example.com", "password", "test-agent")
            api._token = "test_token"

            headers = api._request_headers()
            assert headers["Authorization"] == "Bearer test_token"