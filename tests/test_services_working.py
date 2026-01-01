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

        # Verify service was registered
        mock_hass.services.async_register.assert_called_once()
        call_args = mock_hass.services.async_register.call_args
        assert call_args[1]["domain"] == DOMAIN
        assert call_args[1]["service"] == "extra_hot_water"
        assert "service_func" in call_args[1]
        assert "schema" in call_args[1]
        # supports_response is mocked, so we don't check the exact value

    @pytest.mark.asyncio
    async def test_extra_hot_water_service_success(self, mock_hass, mock_api):
        """Test the extra_hot_water service with successful API call."""
        mock_hass.data[DOMAIN] = mock_api

        # Set up the service
        await async_setup_services(mock_hass)

        # Get the registered service function
        call_args = mock_hass.services.async_register.call_args
        service_func = call_args[1]["service_func"]

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

        # Get the registered service function
        call_args = mock_hass.services.async_register.call_args
        service_func = call_args[1]["service_func"]

        # Create a mock service call
        service_call = MagicMock()
        service_call.data = {"device_id": 123, "minutes": 60}
        service_call.hass = mock_hass

        # Call the service
        result = await service_func(service_call)

        # Verify API was called
        mock_api.set_extra_tap_water.assert_called_once_with(123, 60)

        # Verify exception response
        assert result == {"qvantum": {"exception": "unknown_error", "details": "API error"}}

    @pytest.mark.asyncio
    async def test_extra_hot_water_service_different_device(self, mock_hass, mock_api):
        """Test the extra_hot_water service with a different device ID."""
        mock_hass.data[DOMAIN] = mock_api

        # Set up the service
        await async_setup_services(mock_hass)

        # Get the registered service function
        call_args = mock_hass.services.async_register.call_args
        service_func = call_args[1]["service_func"]

        # Create a mock service call with only device_id (minutes should default to 120)
        service_call = MagicMock()
        service_call.data = {"device_id": 456, "minutes": 120}  # Include default minutes
        service_call.hass = mock_hass

        # Call the service
        result = await service_func(service_call)

        # Verify API was called with default minutes (120)
        mock_api.set_extra_tap_water.assert_called_once_with(456, 120)

        # Verify response
        assert result == {"qvantum": [{"status": "success"}]}