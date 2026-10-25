"""Tests for Qvantum services."""

import json
from types import SimpleNamespace
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
        registered = {
            call[1]["service"]
            for call in mock_hass.services.async_register.call_args_list
        }
        assert registered == {
            "extra_hot_water",
            "set_curve_control",
            "set_curve_terms",
            "telemetry_upload",
            "telemetry_delete",
            "export_summary",
            "recalibrate",
        }

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

        # Every registration carries the domain, a handler and a schema.
        for call in mock_hass.services.async_register.call_args_list:
            assert call[1]["domain"] == DOMAIN
            assert "service_func" in call[1]
            assert "schema" in call[1]

    @pytest.mark.asyncio
    async def test_export_summary_writes_and_returns_the_payload(
        self, mock_hass, mock_api, tmp_path
    ):
        """The export writes a file and returns it; nothing is sent anywhere."""
        entry = MagicMock()
        entry.runtime_data.coordinator = mock_api
        entry.runtime_data.efficiency_coordinator = None
        entry.runtime_data.curve_coordinator = None
        mock_api.data = {
            "values": {
                "room_comp_factor": 5.0,
                "sensor_mode": 4,
                # Must never reach the payload.
                "external_room_temp_entity": "sensor.inomhustemperatur",
            }
        }
        mock_api.device_id = "3010100111111111"
        mock_api.modbus_enabled = True
        mock_hass.config_entries.async_entries.return_value = [entry]
        target = tmp_path / "qvantum_summary.json"
        mock_hass.config.path = MagicMock(return_value=str(target))
        mock_hass.async_add_executor_job = AsyncMock(
            side_effect=lambda func, *args: func(*args)
        )
        registry = MagicMock()
        registry.async_get_device_by_identifier.return_value = SimpleNamespace(
            manufacturer="Qvantum", model="QE-6", sw_version="2026.10.4", hw_version=None
        )

        with patch("custom_components.qvantum.summary.dr") as patched_registry:
            patched_registry.async_get.return_value = registry
            await async_setup_services(mock_hass)
            service_func = next(
                call[1]["service_func"]
                for call in mock_hass.services.async_register.call_args_list
                if call[1]["service"] == "export_summary"
            )
            service_call = MagicMock()
            service_call.hass = mock_hass
            result = await service_func(service_call)

        summary = result["qvantum"]["summary"]
        assert summary["plant"]["room_comp_factor"] == 5.0
        assert summary["transport"] == "modbus"
        # The pump id is the API id, so the device is found by the identifier
        # the integration registered — not by treating the id as a registry id.
        # The lookup is scoped to the owning entry: identifiers are only unique
        # per config entry, so ``async_get_device`` is deprecated/ambiguous.
        registry.async_get_device.assert_not_called()
        registry.async_get_device_by_identifier.assert_called_once_with(
            ("qvantum", "qvantum-3010100111111111"), entry.entry_id
        )
        assert summary["device"]["model"] == "QE-6"
        assert summary["device"]["firmware"] == "2026.10.4"
        assert result["qvantum"]["path"] == str(target)
        # The same payload is downloadable while logged in.
        assert result["qvantum"]["url"] == "/api/qvantum/summary"
        written = json.loads(target.read_text(encoding="utf-8"))
        assert written == summary
        assert "sensor.inomhustemperatur" not in json.dumps(written)

    @pytest.mark.asyncio
    async def test_export_summary_without_a_configured_entry(self, mock_hass):
        """No entry means no summary, and no exception."""
        mock_hass.config_entries.async_entries.return_value = []

        await async_setup_services(mock_hass)
        # Look the handler up by name: a positional index breaks every time a
        # service is added before this one.
        service_func = _service_func(mock_hass, "export_summary")
        service_call = MagicMock()
        service_call.hass = mock_hass

        result = await service_func(service_call)

        assert result["qvantum"]["exception"] == "not_loaded"

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

        curve.async_set_terms.assert_awaited_once_with(
            solar=None,
            cop_feedback=True,
            precharge=None,
            wind=None,
        )
        assert result == {
            "qvantum": {"terms": {"cop_feedback": True, "precharge": False}}
        }

    @pytest.mark.asyncio
    async def test_omits_absent_flags(self, mock_hass):
        curve = MagicMock()
        curve.async_set_terms = AsyncMock()
        curve.terms = {"cop_feedback": False, "precharge": False, "wind": False}
        self._register(mock_hass, curve)
        service_func = await self._service_func(mock_hass)

        service_call = MagicMock()
        service_call.data = {}
        service_call.hass = mock_hass

        await service_func(service_call)

        curve.async_set_terms.assert_awaited_once_with(
            solar=None,
            cop_feedback=None,
            precharge=None,
            wind=None,
        )

    @pytest.mark.asyncio
    async def test_enables_precharge(self, mock_hass):
        curve = MagicMock()
        curve.async_set_terms = AsyncMock()
        curve.terms = {"cop_feedback": False, "precharge": True}
        self._register(mock_hass, curve)
        service_func = await self._service_func(mock_hass)

        service_call = MagicMock()
        service_call.data = {"precharge": True}
        service_call.hass = mock_hass

        await service_func(service_call)

        curve.async_set_terms.assert_awaited_once_with(
            solar=None,
            cop_feedback=None,
            precharge=True,
            wind=None,
        )

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

    def test_accepts_precharge(self):
        assert SET_CURVE_TERMS_SCHEMA({"precharge": False}) == {"precharge": False}

    def test_accepts_empty(self):
        assert SET_CURVE_TERMS_SCHEMA({}) == {}

    def test_rejects_non_boolean(self):
        with pytest.raises(vol.Invalid):
            SET_CURVE_TERMS_SCHEMA({"cop_feedback": "maybe"})
        with pytest.raises(vol.Invalid):
            SET_CURVE_TERMS_SCHEMA({"precharge": "maybe"})


class TestSetCurveControlSchema:
    """Validate the service schema without invoking the service handler."""

    @pytest.mark.parametrize("mode", ["shadow", "active"])
    def test_accepts_known_modes(self, mode):
        assert SET_CURVE_CONTROL_SCHEMA({"mode": mode}) == {"mode": mode}

    def test_rejects_unknown_mode(self):
        with pytest.raises(vol.Invalid):
            SET_CURVE_CONTROL_SCHEMA({"mode": "on"})



class TestTelemetryServices:
    """The two services the consent prompt promises."""

    @staticmethod
    def _handler(hass, name):
        return next(
            call[1]["service_func"]
            for call in hass.services.async_register.call_args_list
            if call[1]["service"] == name
        )

    @pytest.mark.asyncio
    async def test_nothing_registered_means_not_loaded(self, mock_hass, mock_api):
        mock_hass.config_entries.async_entries.return_value = []
        await async_setup_services(mock_hass)
        call = MagicMock()
        call.hass = mock_hass

        upload = self._handler(mock_hass, "telemetry_upload")
        delete = self._handler(mock_hass, "telemetry_delete")

        assert await upload(call) == {"qvantum": {"exception": "not_loaded"}}
        assert await delete(call) == {"qvantum": {"exception": "not_loaded"}}

    @pytest.mark.asyncio
    async def test_upload_service_forces_a_send(self, mock_hass, mock_api):
        entry = MagicMock()
        entry.entry_id = "entry"
        entry.runtime_data.coordinator = mock_api
        mock_hass.config_entries.async_entries.return_value = [entry]
        call = MagicMock()
        call.hass = mock_hass

        with patch(
            "custom_components.qvantum.services.async_upload_summary",
            AsyncMock(return_value=True),
        ) as upload:
            await async_setup_services(mock_hass)
            result = await self._handler(mock_hass, "telemetry_upload")(call)

        assert result == {"qvantum": {"sent": True}}
        upload.assert_awaited_once_with(mock_hass, "entry", force=True)

    @pytest.mark.asyncio
    async def test_delete_service_reports_the_result(self, mock_hass, mock_api):
        entry = MagicMock()
        entry.entry_id = "entry"
        entry.runtime_data.coordinator = mock_api
        mock_hass.config_entries.async_entries.return_value = [entry]
        call = MagicMock()
        call.hass = mock_hass

        with patch(
            "custom_components.qvantum.services.async_delete_telemetry",
            AsyncMock(return_value=False),
        ):
            await async_setup_services(mock_hass)
            result = await self._handler(mock_hass, "telemetry_delete")(call)

        assert result == {"qvantum": {"deleted": False}}

    @pytest.mark.asyncio
    async def test_service_failures_are_reported_not_raised(self, mock_hass, mock_api):
        entry = MagicMock()
        entry.entry_id = "entry"
        entry.runtime_data.coordinator = mock_api
        mock_hass.config_entries.async_entries.return_value = [entry]
        call = MagicMock()
        call.hass = mock_hass

        with (
            patch(
                "custom_components.qvantum.services.async_upload_summary",
                AsyncMock(side_effect=RuntimeError("boom")),
            ),
            patch(
                "custom_components.qvantum.services.async_delete_telemetry",
                AsyncMock(side_effect=RuntimeError("boom")),
            ),
        ):
            await async_setup_services(mock_hass)
            upload = await self._handler(mock_hass, "telemetry_upload")(call)
            delete = await self._handler(mock_hass, "telemetry_delete")(call)

        assert upload["qvantum"]["exception"] == "unknown_error"
        assert delete["qvantum"]["exception"] == "unknown_error"


def _service_func(hass, name: str):
    """The handler registered under ``name`` (order-independent)."""
    return next(
        call.kwargs["service_func"]
        for call in hass.services.async_register.call_args_list
        if call.kwargs.get("service") == name
    )


class TestRecalibrateService:
    """Forcing a calibration attempt instead of waiting for the daily cycle."""

    @pytest.mark.asyncio
    async def test_returns_what_the_attempt_produced(self, mock_hass):
        from custom_components.qvantum.services import async_setup_services

        curve = MagicMock()
        curve.async_recalibrate = AsyncMock(
            return_value={
                "calibration_ok": True,
                "daily_model_valid": False,
                "daily_model_days": 11,
                "wind_valid": False,
                "wind_hours": 264,
                "wind_notes": "cold+windy hours 37 < 200",
            }
        )
        entry = MagicMock()
        entry.runtime_data.curve_coordinator = curve
        mock_hass.config_entries.async_entries.return_value = [entry]

        await async_setup_services(mock_hass)
        service_call = MagicMock()
        service_call.hass = mock_hass

        result = await _service_func(mock_hass, "recalibrate")(service_call)

        curve.async_recalibrate.assert_awaited_once_with()
        assert result["qvantum"]["wind_hours"] == 264
        assert result["qvantum"]["calibration_ok"] is True

    @pytest.mark.asyncio
    async def test_without_a_curve_coordinator_it_reports_an_error(self, mock_hass):
        from custom_components.qvantum.services import async_setup_services

        mock_hass.config_entries.async_entries.return_value = []
        await async_setup_services(mock_hass)
        service_call = MagicMock()
        service_call.hass = mock_hass

        result = await _service_func(mock_hass, "recalibrate")(service_call)

        assert result["qvantum"]["exception"] == "unknown_error"
        assert "Modbus" in result["qvantum"]["details"]
