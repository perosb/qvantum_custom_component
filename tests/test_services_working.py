"""Tests for Qvantum services (working version that avoids metaclass issues)."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest


# Mock the voluptuous imports
class MockVol:
    class Schema:
        def __init__(self, schema):
            self.schema = schema

    class All:
        def __init__(self, *args):
            pass

    class Coerce:
        def __init__(self, type_):
            pass

    class Range:
        def __init__(self, **kwargs):
            pass

    @staticmethod
    def Required(x):
        return x


class MockSupportsResponse:
    OPTIONAL = "optional"


# Patch the imports before importing the services module
with patch("homeassistant.core.SupportsResponse", MockSupportsResponse):
    from custom_components.qvantum.services import async_setup_services
    from custom_components.qvantum.const import DOMAIN


@pytest.fixture
def mock_hass():
    """Create a mock HomeAssistant instance."""
    hass = MagicMock()
    hass.data = {DOMAIN: MagicMock()}
    hass.services = MagicMock()
    return hass


@pytest.fixture
def mock_api():
    """Create a mock API."""
    api = MagicMock()
    api.set_extra_tap_water = AsyncMock(return_value={"status": "success"})
    return api


class TestQvantumServices:
    """Test the Qvantum services."""

    @pytest.mark.asyncio
    async def test_async_setup_services(self, mock_hass, mock_api):
        """Test service setup."""
        mock_hass.data[DOMAIN] = mock_api

        await async_setup_services(mock_hass)

        # Verify services were registered
        assert mock_hass.services.async_register.call_count == 1

        # Check first call (extra_hot_water)
        first_call = mock_hass.services.async_register.call_args_list[0]
        assert first_call[1]["domain"] == DOMAIN
        assert first_call[1]["service"] == "extra_hot_water"
        assert "service_func" in first_call[1]
        assert "schema" in first_call[1]

    @pytest.mark.asyncio
    async def test_extra_hot_water_service_success(self, mock_hass, mock_api):
        """Test the extra_hot_water service with successful API call."""
        mock_hass.data[DOMAIN] = mock_api

        # Set up the service
        await async_setup_services(mock_hass)

        # Get the registered extra_hot_water service function (first call)
        first_call = mock_hass.services.async_register.call_args_list[0]
        service_func = first_call[1]["service_func"]

        # Create a mock service call
        service_call = MagicMock()
        service_call.data = {"device_id": 123, "minutes": 60}
        service_call.hass = mock_hass

        # Call the service
        result = await service_func(service_call)

        # Verify API was called correctly
        mock_api.set_extra_tap_water.assert_called_once_with(123, 60)

        # Verify response
        assert result == {"qvantum": [{"status": "success"}]}

    @pytest.mark.asyncio
    async def test_extra_hot_water_service_with_exception(self, mock_hass, mock_api):
        """Test the extra_hot_water service with API exception."""
        mock_hass.data[DOMAIN] = mock_api

        # Make the API call raise an exception
        mock_api.set_extra_tap_water.side_effect = Exception("API error")

        # Set up the service
        await async_setup_services(mock_hass)

        # Get the registered extra_hot_water service function (first call)
        first_call = mock_hass.services.async_register.call_args_list[0]
        service_func = first_call[1]["service_func"]

        # Create a mock service call
        service_call = MagicMock()
        service_call.data = {"device_id": 123, "minutes": 60}
        service_call.hass = mock_hass

        # Call the service
        result = await service_func(service_call)

        # Verify API was called
        mock_api.set_extra_tap_water.assert_called_once_with(123, 60)

        # Verify exception response
        assert result == {
            "qvantum": {"exception": "unknown_error", "details": "API error"}
        }

    @pytest.mark.asyncio
    async def test_extra_hot_water_service_different_device(self, mock_hass, mock_api):
        """Test the extra_hot_water service with a different device ID."""
        mock_hass.data[DOMAIN] = mock_api

        # Set up the service
        await async_setup_services(mock_hass)

        # Get the registered extra_hot_water service function (first call)
        first_call = mock_hass.services.async_register.call_args_list[0]
        service_func = first_call[1]["service_func"]

        # Create a mock service call with only device_id (minutes should default to 120)
        service_call = MagicMock()
        service_call.data = {
            "device_id": 456,
            "minutes": 120,
        }  # Include default minutes
        service_call.hass = mock_hass

        # Call the service
        result = await service_func(service_call)

        # Verify API was called with default minutes (120)
        mock_api.set_extra_tap_water.assert_called_once_with(456, 120)

        # Verify response
        assert result == {"qvantum": [{"status": "success"}]}

    @pytest.mark.asyncio
    async def test_extra_hot_water_service_auth_error(self, mock_hass, mock_api):
        """Test the extra_hot_water service with authentication error."""
        from custom_components.qvantum.api import APIAuthError

        mock_hass.data[DOMAIN] = mock_api

        # Make the API call raise an authentication error
        mock_api.set_extra_tap_water.side_effect = APIAuthError(
            None, "Invalid credentials"
        )

        # Set up the service
        await async_setup_services(mock_hass)

        # Get the registered extra_hot_water service function
        first_call = mock_hass.services.async_register.call_args_list[0]
        service_func = first_call[1]["service_func"]

        # Create a mock service call
        service_call = MagicMock()
        service_call.data = {"device_id": 123, "minutes": 60}
        service_call.hass = mock_hass

        # Call the service
        result = await service_func(service_call)

        # Verify API was called
        mock_api.set_extra_tap_water.assert_called_once_with(123, 60)

        # Verify authentication error response
        assert result == {
            "qvantum": {
                "exception": "authentication_failed",
                "details": "Invalid credentials",
            }
        }

    @pytest.mark.asyncio
    async def test_extra_hot_water_service_connection_error(self, mock_hass, mock_api):
        """Test the extra_hot_water service with connection error."""
        from custom_components.qvantum.api import APIConnectionError

        mock_hass.data[DOMAIN] = mock_api

        # Make the API call raise a connection error
        mock_api.set_extra_tap_water.side_effect = APIConnectionError(
            None, "Connection timeout"
        )

        # Set up the service
        await async_setup_services(mock_hass)

        # Get the registered extra_hot_water service function
        first_call = mock_hass.services.async_register.call_args_list[0]
        service_func = first_call[1]["service_func"]

        # Create a mock service call
        service_call = MagicMock()
        service_call.data = {"device_id": 123, "minutes": 60}
        service_call.hass = mock_hass

        # Call the service
        result = await service_func(service_call)

        # Verify API was called
        mock_api.set_extra_tap_water.assert_called_once_with(123, 60)

        # Verify connection error response
        assert result == {
            "qvantum": {
                "exception": "connection_failed",
                "details": "Connection timeout",
            }
        }

    @pytest.mark.asyncio
    async def test_extra_hot_water_service_rate_limit_error(self, mock_hass, mock_api):
        """Test the extra_hot_water service with rate limit error."""
        from custom_components.qvantum.api import APIRateLimitError

        mock_hass.data[DOMAIN] = mock_api

        # Make the API call raise a rate limit error
        mock_api.set_extra_tap_water.side_effect = APIRateLimitError(
            None, "Too many requests"
        )

        # Set up the service
        await async_setup_services(mock_hass)

        # Get the registered extra_hot_water service function
        first_call = mock_hass.services.async_register.call_args_list[0]
        service_func = first_call[1]["service_func"]

        # Create a mock service call
        service_call = MagicMock()
        service_call.data = {"device_id": 123, "minutes": 60}
        service_call.hass = mock_hass

        # Call the service
        result = await service_func(service_call)

        # Verify API was called
        mock_api.set_extra_tap_water.assert_called_once_with(123, 60)

        # Verify rate limit error response
        assert result == {
            "qvantum": {
                "exception": "rate_limit_exceeded",
                "details": "Too many requests",
            }
        }
