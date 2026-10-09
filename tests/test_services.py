"""Tests for Qvantum services."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import voluptuous as vol


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
    from custom_components.qvantum.services import (
        EXTRA_TAP_WATER_SCHEMA,
        SET_CURVE_CONTROL_SCHEMA,
        SET_CURVE_TERMS_SCHEMA,
        async_setup_services,
    )
    from custom_components.qvantum.const import DOMAIN


@pytest.fixture
def mock_hass():
    """Create a mock HomeAssistant instance."""
    hass = MagicMock()
    hass.data = {DOMAIN: MagicMock()}
    hass.services = MagicMock()
    hass.config_entries.async_entries.return_value = []
    return hass


@pytest.fixture
def mock_api():
    """Create a mock API."""
    api = MagicMock()
    api.async_set_extra_tap_water = AsyncMock(return_value={"status": "success"})
    return api


class TestQvantumServices:
    """Test the Qvantum services."""

    @pytest.mark.asyncio
    async def test_async_setup_services(self, mock_hass, mock_api):
        """Test service setup."""
        entry = MagicMock()
        entry.runtime_data.coordinator = mock_api
        mock_hass.config_entries.async_entries.return_value = [entry]

        await async_setup_services(mock_hass)

        # Verify all services were registered
        assert mock_hass.services.async_register.call_count == 3

        # Check first call (extra_hot_water)
        first_call = mock_hass.services.async_register.call_args_list[0]
        assert first_call[1]["domain"] == DOMAIN
        assert first_call[1]["service"] == "extra_hot_water"
        assert "service_func" in first_call[1]
        assert "schema" in first_call[1]

        # Check second call (set_curve_control)
        second_call = mock_hass.services.async_register.call_args_list[1]
        assert second_call[1]["domain"] == DOMAIN
        assert second_call[1]["service"] == "set_curve_control"
        assert "service_func" in second_call[1]
        assert "schema" in second_call[1]

        # Check third call (set_curve_terms)
        third_call = mock_hass.services.async_register.call_args_list[2]
        assert third_call[1]["domain"] == DOMAIN
        assert third_call[1]["service"] == "set_curve_terms"
        assert "service_func" in third_call[1]
        assert "schema" in third_call[1]

    @pytest.mark.asyncio
    async def test_extra_hot_water_service_success(self, mock_hass, mock_api):
        """Test the extra_hot_water service with successful API call."""
        entry = MagicMock()
        entry.runtime_data.coordinator = mock_api
        mock_hass.config_entries.async_entries.return_value = [entry]

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
        mock_api.async_set_extra_tap_water.assert_called_once_with(123, 60)

        # Verify response
        assert result == {"qvantum": [{"status": "success"}]}

    @pytest.mark.asyncio
    async def test_extra_hot_water_service_with_exception(self, mock_hass, mock_api):
        """Test the extra_hot_water service with API exception."""
        entry = MagicMock()
        entry.runtime_data.coordinator = mock_api
        mock_hass.config_entries.async_entries.return_value = [entry]

        # Make the API call raise an exception
        mock_api.async_set_extra_tap_water.side_effect = Exception("API error")

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
        mock_api.async_set_extra_tap_water.assert_called_once_with(123, 60)

        # Verify exception response
        assert result == {
            "qvantum": {"exception": "unknown_error", "details": "API error"}
        }

    @pytest.mark.asyncio
    async def test_extra_hot_water_service_different_device(self, mock_hass, mock_api):
        """Test the extra_hot_water service with a different device ID."""
        entry = MagicMock()
        entry.runtime_data.coordinator = mock_api
        mock_hass.config_entries.async_entries.return_value = [entry]

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
        mock_api.async_set_extra_tap_water.assert_called_once_with(456, 120)

        # Verify response
        assert result == {"qvantum": [{"status": "success"}]}

    @pytest.mark.asyncio
    async def test_extra_hot_water_service_auth_error(self, mock_hass, mock_api):
        """Test the extra_hot_water service with authentication error."""
        from custom_components.qvantum.client.exceptions import APIAuthError

        entry = MagicMock()
        entry.runtime_data.coordinator = mock_api
        mock_hass.config_entries.async_entries.return_value = [entry]

        # Make the API call raise an authentication error
        mock_api.async_set_extra_tap_water.side_effect = APIAuthError(
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
        mock_api.async_set_extra_tap_water.assert_called_once_with(123, 60)

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
        from custom_components.qvantum.client.exceptions import APIConnectionError

        entry = MagicMock()
        entry.runtime_data.coordinator = mock_api
        mock_hass.config_entries.async_entries.return_value = [entry]

        # Make the API call raise a connection error
        mock_api.async_set_extra_tap_water.side_effect = APIConnectionError(
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
        mock_api.async_set_extra_tap_water.assert_called_once_with(123, 60)

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
        from custom_components.qvantum.client.exceptions import APIRateLimitError

        entry = MagicMock()
        entry.runtime_data.coordinator = mock_api
        mock_hass.config_entries.async_entries.return_value = [entry]

        # Make the API call raise a rate limit error
        mock_api.async_set_extra_tap_water.side_effect = APIRateLimitError(
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
        mock_api.async_set_extra_tap_water.assert_called_once_with(123, 60)

        # Verify rate limit error response
        assert result == {
            "qvantum": {
                "exception": "rate_limit_exceeded",
                "details": "Too many requests",
            }
        }


class TestExtraTapWaterSchema:
    """Validate the service schema without invoking the service handler."""

    def test_accepts_modbus_serial_device_id(self):
        """Modbus device ids are serials, not integers."""
        validated = EXTRA_TAP_WATER_SCHEMA(
            {"device_id": "12003045006007", "minutes": 60}
        )
        assert validated["device_id"] == "12003045006007"

    def test_preserves_leading_zeros(self):
        """Serials must not be round-tripped through int()."""
        validated = EXTRA_TAP_WATER_SCHEMA(
            {"device_id": "0012003045006007", "minutes": 60}
        )
        assert validated["device_id"] == "0012003045006007"

    def test_normalizes_integer_device_id(self):
        """Cloud users may pass a numeric id; normalize it to str."""
        validated = EXTRA_TAP_WATER_SCHEMA({"device_id": 123, "minutes": 60})
        assert validated["device_id"] == "123"

    @pytest.mark.parametrize("value", [None, True, "", "   "])
    def test_rejects_invalid_device_id(self, value):
        """Empty, boolean, and null ids are rejected."""
        with pytest.raises(vol.Invalid):
            EXTRA_TAP_WATER_SCHEMA({"device_id": value, "minutes": 60})

class TestSetCurveControlService:
    """Custom curve control service (shadow / active)."""

    def _register(self, mock_hass, curve):
        entry = MagicMock()
        entry.runtime_data.coordinator = MagicMock()
        entry.runtime_data.curve_coordinator = curve
        mock_hass.config_entries.async_entries.return_value = [entry]

    async def _service_func(self, mock_hass):
        await async_setup_services(mock_hass)
        second_call = mock_hass.services.async_register.call_args_list[1]
        assert second_call[1]["service"] == "set_curve_control"
        return second_call[1]["service_func"]

    @pytest.mark.asyncio
    async def test_sets_active_mode(self, mock_hass):
        curve = MagicMock()
        curve.active = True
        curve.async_set_control_mode = AsyncMock()
        self._register(mock_hass, curve)
        service_func = await self._service_func(mock_hass)

        service_call = MagicMock()
        service_call.data = {"mode": "active"}
        service_call.hass = mock_hass

        result = await service_func(service_call)

        curve.async_set_control_mode.assert_awaited_once_with("active")
        assert result == {"qvantum": {"mode": "active", "active": True}}

    @pytest.mark.asyncio
    async def test_sets_shadow_mode(self, mock_hass):
        curve = MagicMock()
        curve.active = False
        curve.async_set_control_mode = AsyncMock()
        self._register(mock_hass, curve)
        service_func = await self._service_func(mock_hass)

        service_call = MagicMock()
        service_call.data = {"mode": "shadow"}
        service_call.hass = mock_hass

        result = await service_func(service_call)

        curve.async_set_control_mode.assert_awaited_once_with("shadow")
        assert result == {"qvantum": {"mode": "shadow", "active": False}}

    @pytest.mark.asyncio
    async def test_reports_unknown_error_on_failure(self, mock_hass):
        curve = MagicMock()
        curve.async_set_control_mode = AsyncMock(side_effect=Exception("boom"))
        self._register(mock_hass, curve)
        service_func = await self._service_func(mock_hass)

        service_call = MagicMock()
        service_call.data = {"mode": "active"}
        service_call.hass = mock_hass

        result = await service_func(service_call)

        assert result["qvantum"]["exception"] == "unknown_error"
        assert result["qvantum"]["details"] == "boom"

    @pytest.mark.asyncio
    async def test_requires_modbus_curve_coordinator(self, mock_hass):
        self._register(mock_hass, None)
        service_func = await self._service_func(mock_hass)

        service_call = MagicMock()
        service_call.data = {"mode": "active"}
        service_call.hass = mock_hass

        result = await service_func(service_call)

        assert result["qvantum"]["exception"] == "unknown_error"
        assert "Modbus" in result["qvantum"]["details"]

    @pytest.mark.asyncio
    async def test_without_entries(self, mock_hass):
        mock_hass.config_entries.async_entries.return_value = []
        service_func = await self._service_func(mock_hass)

        service_call = MagicMock()
        service_call.data = {"mode": "shadow"}
        service_call.hass = mock_hass

        result = await service_func(service_call)

        assert result["qvantum"]["exception"] == "unknown_error"


class TestSetCurveTermsService:
    """COP-feedback term service (opt-in, Modbus only)."""

    def _register(self, mock_hass, curve):
        entry = MagicMock()
        entry.runtime_data.coordinator = MagicMock()
        entry.runtime_data.curve_coordinator = curve
        mock_hass.config_entries.async_entries.return_value = [entry]

    async def _service_func(self, mock_hass):
        await async_setup_services(mock_hass)
        third_call = mock_hass.services.async_register.call_args_list[2]
        assert third_call[1]["service"] == "set_curve_terms"
        return third_call[1]["service_func"]

    @pytest.mark.asyncio
    async def test_enables_cop_feedback(self, mock_hass):
        curve = MagicMock()
        curve.async_set_terms = AsyncMock()
        curve.terms = {"cop_feedback": True, "precharge": False}
        self._register(mock_hass, curve)
        service_func = await self._service_func(mock_hass)

        service_call = MagicMock()
        service_call.data = {"cop_feedback": True}
        service_call.hass = mock_hass

        result = await service_func(service_call)

        curve.async_set_terms.assert_awaited_once_with(cop_feedback=True)
        assert result == {
            "qvantum": {"terms": {"cop_feedback": True, "precharge": False}}
        }

    @pytest.mark.asyncio
    async def test_omits_absent_flags(self, mock_hass):
        curve = MagicMock()
        curve.async_set_terms = AsyncMock()
        curve.terms = {"cop_feedback": False, "precharge": False}
        self._register(mock_hass, curve)
        service_func = await self._service_func(mock_hass)

        service_call = MagicMock()
        service_call.data = {}
        service_call.hass = mock_hass

        await service_func(service_call)

        curve.async_set_terms.assert_awaited_once_with(cop_feedback=None)

    @pytest.mark.asyncio
    async def test_reports_unknown_error_on_failure(self, mock_hass):
        curve = MagicMock()
        curve.async_set_terms = AsyncMock(side_effect=Exception("boom"))
        self._register(mock_hass, curve)
        service_func = await self._service_func(mock_hass)

        service_call = MagicMock()
        service_call.data = {"cop_feedback": True}
        service_call.hass = mock_hass

        result = await service_func(service_call)

        assert result["qvantum"]["exception"] == "unknown_error"
        assert result["qvantum"]["details"] == "boom"

    @pytest.mark.asyncio
    async def test_requires_modbus_curve_coordinator(self, mock_hass):
        self._register(mock_hass, None)
        service_func = await self._service_func(mock_hass)

        service_call = MagicMock()
        service_call.data = {"cop_feedback": True}
        service_call.hass = mock_hass

        result = await service_func(service_call)

        assert result["qvantum"]["exception"] == "unknown_error"
        assert "Modbus" in result["qvantum"]["details"]


class TestSetCurveTermsSchema:
    """Validate the terms schema without invoking the service handler."""

    def test_accepts_boolean(self):
        assert SET_CURVE_TERMS_SCHEMA({"cop_feedback": True}) == {"cop_feedback": True}

    def test_accepts_empty(self):
        assert SET_CURVE_TERMS_SCHEMA({}) == {}

    def test_rejects_non_boolean(self):
        with pytest.raises(vol.Invalid):
            SET_CURVE_TERMS_SCHEMA({"cop_feedback": "maybe"})


class TestSetCurveControlSchema:
    """Validate the service schema without invoking the service handler."""

    @pytest.mark.parametrize("mode", ["shadow", "active"])
    def test_accepts_known_modes(self, mode):
        assert SET_CURVE_CONTROL_SCHEMA({"mode": mode}) == {"mode": mode}

    def test_rejects_unknown_mode(self):
        with pytest.raises(vol.Invalid):
            SET_CURVE_CONTROL_SCHEMA({"mode": "on"})

