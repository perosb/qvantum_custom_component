"""Tests for Qvantum API."""

import asyncio
import datetime
from datetime import timedelta, timezone
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

    def test_init(self, mock_session):
        """Test API initialization."""
        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )

        assert api._username == "test@example.com"
        assert api._password == "password"
        assert api._user_agent == "test-agent"
        assert api._session == mock_session
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

        result = await authenticated_api.get_metrics("test_device")

        assert "metrics" in result
        assert result["metrics"]["bt1"] == metrics_data["values"]["bt1"]
        assert result["metrics"]["bt2"] == metrics_data["values"]["bt2"]
        assert result["metrics"]["latency"] == metrics_data["total_latency"]

    @pytest.mark.asyncio
    async def test_read_modbus_metrics_powertotal_computed(self, mock_session):
        """Test power total is derived from relay stages and compressor power."""
        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )

        with patch.object(
            QvantumAPI,
            "_read_modbus_registers",
            AsyncMock(
                return_value={
                    "picpin_relay_heat_l1": 1,
                    "picpin_relay_heat_l2": 1,
                    "picpin_relay_heat_l3": 1,
                    "compressor_power": 100,
                }
            ),
        ):
            result = await api._read_modbus_metrics(
                "test_device_123",
                [
                    "picpin_relay_heat_l1",
                    "picpin_relay_heat_l2",
                    "picpin_relay_heat_l3",
                    "compressor_power",
                ],
            )

        metrics = result["metrics"]
        assert metrics["powertotal"] == 5260.0

    @pytest.mark.asyncio
    async def test_read_modbus_metrics_compressorenergy_from_mwh_kwh(
        self, mock_session
    ):
        """Test compressorenergy tracks combined mwh+kwh data from modbus."""
        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )

        with patch.object(
            QvantumAPI,
            "_read_modbus_registers",
            AsyncMock(
                return_value={
                    "compressor_mwh": 4,
                    # As the modbus register reads have already applied the scaling factor from the register map (e.g. 0.1 for kwh values),
                    # we need to provide the scaled value here too.
                    "compressor_kwh": 8010 * 0.1,
                }
            ),
        ):
            result = await api._read_modbus_metrics(
                "test_device_123", ["compressor_mwh", "compressor_kwh"]
            )

        metrics = result["metrics"]
        assert metrics["compressorenergy"] == 4801.0

    @pytest.mark.asyncio
    async def test_read_modbus_metrics_rounds_to_two_decimals(self, mock_session):
        """Test numeric outputs from modbus are rounded to two decimals."""
        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )

        with patch.object(
            QvantumAPI,
            "_read_modbus_registers",
            AsyncMock(
                return_value={
                    "picpin_relay_heat_l1": 1,
                    "picpin_relay_heat_l2": 0,
                    "picpin_relay_heat_l3": 0,
                    "compressor_power": 100.12345,
                    "compressor_mwh": 1.2345,
                    "compressor_kwh": 2.3456,
                }
            ),
        ):
            result = await api._read_modbus_metrics(
                "test_device_123",
                [
                    "picpin_relay_heat_l1",
                    "picpin_relay_heat_l2",
                    "picpin_relay_heat_l3",
                    "compressor_power",
                    "compressor_mwh",
                    "compressor_kwh",
                ],
            )

        metrics = result["metrics"]
        assert metrics["powertotal"] == 2260.12
        assert metrics["compressorenergy"] == 1236.85

    @pytest.mark.asyncio
    async def test_get_metrics_modbus_tcp_failure_fallback_http(self, mock_session):
        """Modbus TCP failures should fall back to HTTP when explicitly enabled."""
        api = QvantumAPI(
            "test@example.com",
            "password",
            "test-agent",
            session=mock_session,
            modbus_tcp=True,
        )
        api._token = "test_token"
        api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

        cm, mock_response = mock_session.make_cm_response(
            status=200,
            json_data={"values": {"bt1": 123}, "total_latency": 10},
            headers={"ETag": "etag123"},
        )
        mock_session.get.return_value = cm

        with patch.object(
            QvantumAPI,
            "_read_modbus_metrics",
            AsyncMock(side_effect=Exception("modbus not reachable")),
        ):
            result = await api.get_metrics("test_device_123", enabled_metrics=["bt1"])

        assert "metrics" in result
        assert result["metrics"]["bt1"] == 123
        assert mock_session.get.called

    @pytest.mark.asyncio
    async def test_get_metrics_modbus_sets_latency_ms(self, mock_session):
        """When Modbus succeeds, latency should be an integer millisecond measurement."""
        api = QvantumAPI(
            "test@example.com",
            "password",
            "test-agent",
            session=mock_session,
            modbus_tcp=True,
        )

        fake_metrics = {"metrics": {"bt1": 21, "powertotal": 100}}

        with patch.object(
            QvantumAPI,
            "_read_modbus_metrics",
            AsyncMock(return_value=fake_metrics),
        ):
            result = await api.get_metrics("test_device_123", enabled_metrics=["bt1"])

        assert "metrics" in result
        latency = result["metrics"]["latency"]
        assert isinstance(latency, int)
        assert latency >= 0

    @pytest.mark.asyncio
    async def test_get_metrics_modbus_no_latency_placeholder_in_registers(
        self, mock_session
    ):
        """_read_modbus_registers must not inject a latency key; latency is set in get_metrics."""
        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )

        with patch.object(
            QvantumAPI,
            "_read_modbus_registers",
            AsyncMock(return_value={"hpid": "dev", "bt1": 20}),
        ):
            raw = await api._read_modbus_metrics("dev", ["bt1"])

        # latency must not be present unless explicitly measured and injected
        assert "latency" not in raw.get("metrics", {})

    @pytest.mark.asyncio
    async def test_read_modbus_settings_aliasing(self, mock_session):
        """Test that Modbus settings values are mapped to HTTP and modbus alias keys."""
        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )

        with patch.object(
            QvantumAPI,
            "_read_modbus_registers",
            AsyncMock(
                return_value={
                    "dhw_start_normal": 52,
                    "dhw_stop_normal": 62,
                    "operation_mode": 1,
                }
            ),
        ):
            result = await api._read_modbus_settings(
                "test_device",
                [
                    "dhw start temperature normal",
                    "dhw stop temperature normal",
                    "operation mode",
                ],
            )

        settings = {item["name"]: item["value"] for item in result["settings"]}

        # dhw_* internal keys should be hidden from public settings output
        assert "dhw_start_normal" not in settings
        assert "dhw_stop_normal" not in settings

        # tap_water *_ values should still be available for number entity usage
        assert settings["tap_water_start"] == 52
        assert settings["tap_water_stop"] == 62

        assert "use_adaptive" not in settings
        assert settings["op_mode"] == 1

    @pytest.mark.asyncio
    async def test_read_modbus_reuse_client_between_reads(self):
        """Modbus client should remain open across successful reads."""
        api = QvantumAPI("test@example.com", "password", "test-agent", modbus_tcp=True)

        created_clients = []
        client = None

        async def fake_connect():
            nonlocal client
            client.connected = True

        async def fake_close():
            nonlocal client
            client.connected = False

        def fake_init():
            nonlocal client
            if api._modbus_client is not None:
                return
            client = MagicMock()
            client.connected = False
            client.connect = AsyncMock(side_effect=fake_connect)
            client.close = AsyncMock(side_effect=fake_close)
            created_clients.append(client)
            api._modbus_client = client

        api._init_modbus_client = fake_init

        # First attempt should create a client and keep it open after read
        result1 = await api._read_modbus_registers(
            "test_device", [], {}, use_input_registers=True
        )
        assert result1["hpid"] == "test_device"
        assert api._modbus_client is client

        # Second attempt should reuse the same client
        result2 = await api._read_modbus_registers(
            "test_device", [], {}, use_input_registers=True
        )
        assert result2["hpid"] == "test_device"
        assert api._modbus_client is client

        assert len(created_clients) == 1

    @pytest.mark.asyncio
    async def test_read_modbus_concurrent_access_is_serialized(self):
        """Ensure concurrent Modbus reads are serialized via lock to avoid client conflicts."""
        api = QvantumAPI("test@example.com", "password", "test-agent", modbus_tcp=True)

        created_clients = []
        client = None

        async def fake_connect():
            client.connected = True
            return True

        async def fake_close():
            client.connected = False

        class DummyResult:
            def __init__(self, value):
                self.registers = [value]

            def isError(self):
                return False

        async def fake_execute(_sync, _request):
            # Simulate slow long-running request to force concurrent timing
            await asyncio.sleep(0.05)
            return DummyResult(100)

        def fake_init():
            nonlocal client
            if api._modbus_client is not None:
                return
            client = MagicMock()
            client.connected = False
            client.connect = AsyncMock(side_effect=fake_connect)
            client.close = AsyncMock(side_effect=fake_close)
            client.execute = AsyncMock(side_effect=fake_execute)
            created_clients.append(client)
            api._modbus_client = client

        api._init_modbus_client = fake_init

        # Run two reads concurrently that should serialize via _modbus_lock.
        task1 = asyncio.create_task(
            api._read_modbus_registers(
                "test_device",
                ["bt1 - fast filtered (1min) outdoor temp"],
                {"bt1 - fast filtered (1min) outdoor temp": (0, "int16", 1.0)},
                use_input_registers=True,
            )
        )
        task2 = asyncio.create_task(
            api._read_modbus_registers(
                "test_device",
                ["bt1 - fast filtered (1min) outdoor temp"],
                {"bt1 - fast filtered (1min) outdoor temp": (0, "int16", 1.0)},
                use_input_registers=True,
            )
        )

        await asyncio.sleep(0.01)
        assert client.execute.call_count == 1

        results = await asyncio.gather(task1, task2)

        assert results[0]["bt1 - fast filtered (1min) outdoor temp"] == 100
        assert results[1]["bt1 - fast filtered (1min) outdoor temp"] == 100
        assert client.execute.call_count == 2

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

        result = await api.get_metrics("test_device")

        assert result == {}

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
    async def test_get_metrics_modbus_tcp(self, mock_session):
        """Test getting metrics via Modbus TCP."""
        from unittest.mock import patch, AsyncMock, MagicMock

        with patch(
            "custom_components.qvantum.api.ReadInputRegistersRequest", MagicMock()
        ):
            # Mock Modbus client
            mock_client = MagicMock()
            mock_client.connected = False  # Initially not connected
            mock_client.connect = AsyncMock(
                side_effect=lambda: setattr(mock_client, "connected", True)
            )
            mock_client.execute = AsyncMock(
                return_value=MagicMock(
                    isError=MagicMock(return_value=False), registers=[256]
                )
            )  # Mock execute for ReadInputRegistersRequest
            mock_client.close = AsyncMock()

            with patch(
                "custom_components.qvantum.api.AsyncModbusTcpClient",
                return_value=mock_client,
            ):
                api = QvantumAPI(
                    "test@example.com",
                    "password",
                    "test-agent",
                    session=mock_session,
                    modbus_tcp=True,
                    modbus_host="192.168.1.100",
                    modbus_port=502,
                )

                # Mock the register map with a test entry
                api.MODBUS_REGISTER_MAP = {"bt1": (0, "int16", 10.0)}

                result = await api.get_metrics("test_device", enabled_metrics=["bt1"])

                assert "metrics" in result
                assert result["metrics"]["hpid"] == "test_device"
                assert "bt1" in result["metrics"]
                # Verify Modbus client was used
                mock_client.connect.assert_called_once()
                mock_client.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_metrics_modbus_connection_failure(self, mock_session):
        """Test Modbus connection failure falls back to HTTP."""
        from unittest.mock import patch, AsyncMock, MagicMock

        mock_client = MagicMock()
        mock_client.connected = False
        mock_client.connect = AsyncMock(side_effect=Exception("Connection failed"))
        mock_client.close = AsyncMock()

        cm, mock_response = mock_session.make_cm_response(
            status=200,
            json_data={"values": {"bt1": 123}, "total_latency": 10},
            headers={"ETag": "etag123"},
        )
        mock_session.get.return_value = cm

        with patch(
            "custom_components.qvantum.api.AsyncModbusTcpClient",
            return_value=mock_client,
        ):
            api = QvantumAPI(
                "test@example.com",
                "password",
                "test-agent",
                session=mock_session,
                modbus_tcp=True,
            )
            api._token = "test_token"
            api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)

            result = await api.get_metrics("test_device", enabled_metrics=["bt1"])

            assert "metrics" in result
            assert result["metrics"]["bt1"] == 123
            assert mock_client.connect.call_count == 1
            assert mock_session.get.called

    @pytest.mark.asyncio
    async def test_get_settings_modbus(self, mock_session):
        """Test settings fetching from Modbus."""
        from unittest.mock import patch, AsyncMock

        api = QvantumAPI(
            "test@example.com",
            "password",
            "test-agent",
            session=mock_session,
            modbus_tcp=True,
        )

        # Mock the modbus settings reading
        expected_settings = {
            "settings": [
                {"name": "unit_on_off", "value": 1},
                {"name": "operation_mode", "value": 0},
            ]
        }

        with patch.object(
            api, "_read_modbus_settings", AsyncMock(return_value=expected_settings)
        ) as mock_read_modbus_settings:
            result = await api.get_settings("test_device")

            # Should return settings read from Modbus
            assert result == expected_settings
            # HTTP session should NOT be called when Modbus succeeds
            mock_session.get.assert_not_called()

            # Should request internal holding setting keys (not spec-string keys)
            called_args = mock_read_modbus_settings.call_args[0]
            assert len(called_args) == 2
            assert isinstance(called_args[1], list)
            assert "dhw_start_normal" in called_args[1]
            assert "dhw_stop_normal" in called_args[1]

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
    async def test_normalize_modbus_value(self, mock_session):
        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )

        assert api._normalize_modbus_value(123.4, 1.0) == 123
        assert api._normalize_modbus_value(123.456, 0.1) == 123.46

    def test_init_modbus_client_when_enabled(self, mock_session):
        with patch(
            "custom_components.qvantum.api.AsyncModbusTcpClient",
            return_value=MagicMock(),
        ) as mock_client_ctor:
            api = QvantumAPI(
                "test@example.com",
                "password",
                "test-agent",
                session=mock_session,
                modbus_tcp=True,
            )
            api._init_modbus_client()
            assert api._modbus_client is not None
            mock_client_ctor.assert_called_once_with(
                host=api._modbus_host,
                port=api._modbus_port,
                timeout=10.0,
                retries=3,
            )

    @pytest.mark.asyncio
    async def test_read_modbus_registers_raises_when_no_client(self, mock_session):
        api = QvantumAPI(
            "test@example.com",
            "password",
            "test-agent",
            session=mock_session,
            modbus_tcp=False,
        )

        with pytest.raises(Exception) as exc_info:
            await api._read_modbus_registers(
                "test_device",
                ["bt1"],
                {"bt1": (0, "int16", 1.0)},
                use_input_registers=True,
            )

        assert "Modbus client not initialized" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_read_modbus_registers_float32_handling(self, mock_session):
        from custom_components.qvantum.api import ReadInputRegistersRequest

        api = QvantumAPI(
            "test@example.com",
            "password",
            "test-agent",
            session=mock_session,
            modbus_tcp=True,
        )

        fake_client = MagicMock()
        fake_client.connected = False

        async def connect():
            fake_client.connected = True

        class FakeResult:
            def __init__(self):
                self.registers = [0, 64]  # float32 big-endian: 2.0? (0x000040)

            def isError(self):
                return False

        fake_client.connect = AsyncMock(side_effect=connect)
        fake_client.execute = AsyncMock(return_value=FakeResult())
        api._modbus_client = fake_client

        result = await api._read_modbus_registers(
            "test_device",
            ["f32_metric"],
            {"f32_metric": (0, "float32", 1.0)},
            use_input_registers=True,
        )

        assert result["hpid"] == "test_device"

    @pytest.mark.asyncio
    async def test_handle_response_rate_limits_and_auth_error(self):
        from custom_components.qvantum.api import APIAuthError, APIRateLimitError

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
    async def test_reset_modbus_client_handles_error(self, mock_session):
        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )
        client = MagicMock()
        client.close.side_effect = Exception("close fail")
        api._modbus_client = client

        await api._reset_modbus_client()

        assert api._modbus_client is None

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
    async def test_read_modbus_registers_execute_returns_none_raises(self, mock_session):
        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session, modbus_tcp=True
        )

        fake_client = MagicMock()
        fake_client.connected = True
        fake_client.execute = AsyncMock(return_value=None)
        api._modbus_client = fake_client

        with pytest.raises(Exception):
            await api._read_modbus_registers(
                "test_device",
                ["bt1"],
                {"bt1": (0, "int16", 1.0)},
                use_input_registers=True,
            )

    @pytest.mark.asyncio
    async def test_read_modbus_registers_iserror_logs_and_returns_base(self, mock_session):
        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session, modbus_tcp=True
        )

        fake_client = MagicMock()
        fake_client.connected = True

        class ErrorResult:
            registers = [0]
            def isError(self):
                return True

        fake_client.execute = AsyncMock(return_value=ErrorResult())
        api._modbus_client = fake_client

        result = await api._read_modbus_registers(
            "test_device",
            ["bt1"],
            {"bt1": (0, "int16", 1.0)},
            use_input_registers=True,
        )

        assert result["hpid"] == "test_device"

    @pytest.mark.asyncio
    async def test_read_modbus_metrics_ensures_use_adaptive(self, mock_session):
        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )

        with patch.object(
            QvantumAPI,
            "_read_modbus_registers",
            AsyncMock(
                return_value={
                    "smart_dhw_mode": 1,
                    "compressor_power": 20,
                    "compressor_mwh": 1,
                    "compressor_kwh": 1,
                }
            ),
        ):
            result = await api._read_modbus_metrics("test_device", ["smart_dhw_mode"])

        assert result["metrics"]["use_adaptive"] is True
        assert result["metrics"]["smart_sh_mode"] == 1

    @pytest.mark.asyncio
    async def test_read_modbus_settings_fan_and_extra_tap_water(self, mock_session):
        api = QvantumAPI(
            "test@example.com", "password", "test-agent", session=mock_session
        )

        with patch.object(
            QvantumAPI,
            "_read_modbus_registers",
            AsyncMock(
                return_value={
                    "extra_tap_water": 2,
                    "fanspeedselector": 2,
                    "dhw_start_normal": 1,
                    "dhw_stop_normal": 1,
                }
            ),
        ):
            result = await api._read_modbus_settings("test_device", ["extra_tap_water", "fanspeedselector"])

        settings = {item["name"]: item["value"] for item in result["settings"]}
        assert settings["extra_tap_water"] == "on"
        assert settings["fanspeedselector"] == "extra"

    @pytest.mark.asyncio
    async def test_handle_response_403_raises_connection_error(self):
        from custom_components.qvantum.api import APIConnectionError

        class DummyResponse:
            status = 403
            ok = False

        api = QvantumAPI("test@example.com", "password", "test-agent")

        with pytest.raises(APIConnectionError):
            await api._handle_response(DummyResponse())

    @pytest.mark.asyncio
    async def test_get_device_metadata_403_raises_auth_error(self, mock_session):
        from custom_components.qvantum.api import APIAuthError

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

    @pytest.mark.asyncio
    async def test_elevate_access_sufficient_level(self, authenticated_api):
        """Test elevate_access when access level is already sufficient."""
        access_data = {"writeAccessLevel": 25}

        cm, mock_response = authenticated_api._session.make_cm_response(
            status=200, json_data=access_data
        )
        authenticated_api._session.get.return_value = cm

        result = await authenticated_api.elevate_access("test_device")

        assert result == access_data
        authenticated_api._session.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_elevate_access_expires_soon(self, authenticated_api):
        """Test elevate_access when access expires within 1 day."""
        # Set expiresAt to 12 hours from now (within 1 day)
        tomorrow = datetime.datetime.now(timezone.utc) + timedelta(hours=12)
        expires_at_str = tomorrow.isoformat().replace("+00:00", "Z")
        access_data = {"writeAccessLevel": 15, "expiresAt": expires_at_str}

        cm, mock_response = authenticated_api._session.make_cm_response(
            status=200, json_data=access_data
        )
        authenticated_api._session.get.return_value = cm

        result = await authenticated_api.elevate_access("test_device")

        assert result == access_data
        authenticated_api._session.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_elevate_access_expires_later(self, authenticated_api):
        """Test elevate_access when access expires more than 1 day away."""
        # Set expiresAt to 2 days from now
        future = datetime.datetime.now(timezone.utc) + timedelta(days=2)
        expires_at_str = future.isoformat().replace("+00:00", "Z")
        initial_access_data = {"writeAccessLevel": 15, "expiresAt": expires_at_str}
        generate_data = {"accessCode": "12345"}
        claim_data = {"message": "ok"}
        approve_data = {"status": "approved"}
        updated_access_data = {"writeAccessLevel": 25}

        # Mock initial access level check
        cm1, mock_response1 = authenticated_api._session.make_cm_response(
            status=200, json_data=initial_access_data
        )
        # Mock generate code
        cm2, mock_response2 = authenticated_api._session.make_cm_response(
            status=200, json_data=generate_data
        )
        # Mock claim grant
        cm3, mock_response3 = authenticated_api._session.make_cm_response(
            status=200, json_data=claim_data
        )
        # Mock approve access
        cm4, mock_response4 = authenticated_api._session.make_cm_response(
            status=200, json_data=approve_data
        )
        # Mock updated access level
        cm5, mock_response5 = authenticated_api._session.make_cm_response(
            status=200, json_data=updated_access_data
        )

        get_call_count = 0

        def get_side_effect(*args, **kwargs):
            nonlocal get_call_count
            get_call_count += 1
            if get_call_count == 1:
                return cm1
            elif get_call_count == 2:
                return cm5
            else:
                raise ValueError(f"Unexpected get call count: {get_call_count}")

        post_call_count = 0

        def post_side_effect(*args, **kwargs):
            nonlocal post_call_count
            post_call_count += 1
            if post_call_count == 1:
                return cm2
            elif post_call_count == 2:
                return cm3
            elif post_call_count == 3:
                return cm4
            else:
                raise ValueError(
                    f"Unexpected post call count: {post_call_count}, url: {args[0]}"
                )

        authenticated_api._session.get.side_effect = get_side_effect
        authenticated_api._session.post.side_effect = post_side_effect

        result = await authenticated_api.elevate_access("test_device")

        assert result == updated_access_data
        assert get_call_count == 2
        assert post_call_count == 3

    @pytest.mark.asyncio
    async def test_elevate_access_invalid_expires_at(self, authenticated_api):
        """Test elevate_access when expiresAt is invalid."""
        access_data = {"writeAccessLevel": 15, "expiresAt": "invalid-date"}
        generate_data = {"accessCode": "12345"}
        claim_data = {"message": "ok"}
        approve_data = {"status": "approved"}
        updated_access_data = {"writeAccessLevel": 25}

        # Mock initial access level check
        cm1, mock_response1 = authenticated_api._session.make_cm_response(
            status=200, json_data=access_data
        )
        # Mock generate code
        cm2, mock_response2 = authenticated_api._session.make_cm_response(
            status=200, json_data=generate_data
        )
        # Mock claim grant
        cm3, mock_response3 = authenticated_api._session.make_cm_response(
            status=200, json_data=claim_data
        )
        # Mock approve access
        cm4, mock_response4 = authenticated_api._session.make_cm_response(
            status=200, json_data=approve_data
        )
        # Mock updated access level
        cm5, mock_response5 = authenticated_api._session.make_cm_response(
            status=200, json_data=updated_access_data
        )

        get_call_count = 0

        def get_side_effect(*args, **kwargs):
            nonlocal get_call_count
            get_call_count += 1
            if get_call_count == 1:
                return cm1
            elif get_call_count == 2:
                return cm5
            else:
                raise ValueError(f"Unexpected get call count: {get_call_count}")

        post_call_count = 0

        def post_side_effect(*args, **kwargs):
            nonlocal post_call_count
            post_call_count += 1
            if post_call_count == 1:
                return cm2
            elif post_call_count == 2:
                return cm3
            elif post_call_count == 3:
                return cm4
            else:
                raise ValueError(
                    f"Unexpected post call count: {post_call_count}, url: {args[0]}"
                )

        authenticated_api._session.get.side_effect = get_side_effect
        authenticated_api._session.post.side_effect = post_side_effect

        result = await authenticated_api.elevate_access("test_device")

        assert result == updated_access_data
        assert get_call_count == 2
        assert post_call_count == 3

    @pytest.mark.asyncio
    async def test_elevate_access_insufficient_level(self, authenticated_api):
        """Test elevate_access when access level is low and elevates access."""
        initial_access_data = {"writeAccessLevel": 15}
        generate_data = {"accessCode": "12345"}
        claim_data = {"message": "ok"}
        approve_data = {"status": "approved"}
        updated_access_data = {"writeAccessLevel": 25}

        # Mock initial access level check
        cm1, mock_response1 = authenticated_api._session.make_cm_response(
            status=200, json_data=initial_access_data
        )
        # Mock generate code
        cm2, mock_response2 = authenticated_api._session.make_cm_response(
            status=200, json_data=generate_data
        )
        # Mock claim grant
        cm3, mock_response3 = authenticated_api._session.make_cm_response(
            status=200, json_data=claim_data
        )
        # Mock approve access
        cm4, mock_response4 = authenticated_api._session.make_cm_response(
            status=200, json_data=approve_data
        )
        # Mock updated access level
        cm5, mock_response5 = authenticated_api._session.make_cm_response(
            status=200, json_data=updated_access_data
        )

        get_call_count = 0

        def get_side_effect(*args, **kwargs):
            nonlocal get_call_count
            get_call_count += 1
            if get_call_count == 1:
                return cm1
            elif get_call_count == 2:
                return cm5
            else:
                raise ValueError(f"Unexpected get call count: {get_call_count}")

        post_call_count = 0

        def post_side_effect(*args, **kwargs):
            nonlocal post_call_count
            post_call_count += 1
            if post_call_count == 1:
                return cm2
            elif post_call_count == 2:
                return cm3
            elif post_call_count == 3:
                return cm4
            else:
                raise ValueError(
                    f"Unexpected post call count: {post_call_count}, url: {args[0]}"
                )

        authenticated_api._session.get.side_effect = get_side_effect
        authenticated_api._session.post.side_effect = post_side_effect

        result = await authenticated_api.elevate_access("test_device")

        assert result == updated_access_data
        assert get_call_count == 2
        assert post_call_count == 3

    @pytest.mark.asyncio
    async def test_elevate_access_generate_code_failure(self, authenticated_api):
        """Test elevate_access when code generation fails."""
        initial_access_data = {"writeAccessLevel": 15}

        # Mock initial access level check (insufficient)
        cm1, mock_response1 = authenticated_api._session.make_cm_response(
            status=200, json_data=initial_access_data
        )
        # Mock generate code failure (400 error)
        cm2, mock_response2 = authenticated_api._session.make_cm_response(
            status=400, json_data={"error": "Failed to generate code"}
        )

        get_call_count = 0

        def get_side_effect(*args, **kwargs):
            nonlocal get_call_count
            get_call_count += 1
            if get_call_count == 1:
                return cm1
            else:
                raise ValueError(f"Unexpected get call count: {get_call_count}")

        authenticated_api._session.get.side_effect = get_side_effect
        authenticated_api._session.post.return_value = cm2

        result = await authenticated_api.elevate_access("test_device")

        assert result is None
        assert get_call_count == 1  # Only initial check, no final verification

    @pytest.mark.asyncio
    async def test_elevate_access_missing_access_code(self, authenticated_api):
        """Test elevate_access when generate_code response lacks accessCode."""
        initial_access_data = {"writeAccessLevel": 15}
        generate_data = {"someOtherField": "value"}  # Missing accessCode

        # Mock initial access level check
        cm1, mock_response1 = authenticated_api._session.make_cm_response(
            status=200, json_data=initial_access_data
        )
        # Mock generate code (successful but missing accessCode)
        cm2, mock_response2 = authenticated_api._session.make_cm_response(
            status=200, json_data=generate_data
        )

        get_call_count = 0

        def get_side_effect(*args, **kwargs):
            nonlocal get_call_count
            get_call_count += 1
            if get_call_count == 1:
                return cm1
            else:
                raise ValueError(f"Unexpected get call count: {get_call_count}")

        authenticated_api._session.get.side_effect = get_side_effect
        authenticated_api._session.post.return_value = cm2

        result = await authenticated_api.elevate_access("test_device")

        assert result is None
        assert get_call_count == 1  # Only initial check

    @pytest.mark.asyncio
    async def test_elevate_access_claim_grant_failure(self, authenticated_api):
        """Test elevate_access when grant claiming fails."""
        initial_access_data = {"writeAccessLevel": 15}
        generate_data = {"accessCode": "12345"}

        # Mock initial access level check
        cm1, mock_response1 = authenticated_api._session.make_cm_response(
            status=200, json_data=initial_access_data
        )
        # Mock generate code
        cm2, mock_response2 = authenticated_api._session.make_cm_response(
            status=200, json_data=generate_data
        )
        # Mock claim grant failure
        cm3, mock_response3 = authenticated_api._session.make_cm_response(
            status=400, json_data={"error": "Failed to claim grant"}
        )

        get_call_count = 0

        def get_side_effect(*args, **kwargs):
            nonlocal get_call_count
            get_call_count += 1
            if get_call_count == 1:
                return cm1
            else:
                raise ValueError(f"Unexpected get call count: {get_call_count}")

        post_call_count = 0

        def post_side_effect(*args, **kwargs):
            nonlocal post_call_count
            post_call_count += 1
            if post_call_count == 1:
                return cm2
            elif post_call_count == 2:
                return cm3
            else:
                raise ValueError(f"Unexpected post call count: {post_call_count}")

        authenticated_api._session.get.side_effect = get_side_effect
        authenticated_api._session.post.side_effect = post_side_effect

        result = await authenticated_api.elevate_access("test_device")

        assert result is None
        assert get_call_count == 1  # Only initial check
        assert post_call_count == 2  # Generate and failed claim

    @pytest.mark.asyncio
    async def test_elevate_access_approve_failure(self, authenticated_api):
        """Test elevate_access when access approval fails."""
        initial_access_data = {"writeAccessLevel": 15}
        generate_data = {"accessCode": "12345"}
        claim_data = {"message": "ok"}

        # Mock initial access level check
        cm1, mock_response1 = authenticated_api._session.make_cm_response(
            status=200, json_data=initial_access_data
        )
        # Mock generate code
        cm2, mock_response2 = authenticated_api._session.make_cm_response(
            status=200, json_data=generate_data
        )
        # Mock claim grant
        cm3, mock_response3 = authenticated_api._session.make_cm_response(
            status=200, json_data=claim_data
        )
        # Mock approve access failure
        cm4, mock_response4 = authenticated_api._session.make_cm_response(status=400)

        get_call_count = 0

        def get_side_effect(*args, **kwargs):
            nonlocal get_call_count
            get_call_count += 1
            if get_call_count == 1:
                return cm1
            else:
                raise ValueError(f"Unexpected get call count: {get_call_count}")

        post_call_count = 0

        def post_side_effect(*args, **kwargs):
            nonlocal post_call_count
            post_call_count += 1
            if post_call_count == 1:
                return cm2
            elif post_call_count == 2:
                return cm3
            elif post_call_count == 3:
                return cm4
            else:
                raise ValueError(f"Unexpected post call count: {post_call_count}")

        authenticated_api._session.get.side_effect = get_side_effect
        authenticated_api._session.post.side_effect = post_side_effect

        result = await authenticated_api.elevate_access("test_device")

        assert result is None
        assert get_call_count == 1  # Only initial check
        assert post_call_count == 3  # Generate, claim, failed approve

    @pytest.mark.asyncio
    async def test_generate_code(self, authenticated_api):
        """Test _generate_code method."""
        generate_data = {"accessCode": "12345"}

        # Mock generate code
        cm, mock_response = authenticated_api._session.make_cm_response(
            status=200, json_data=generate_data
        )

        authenticated_api._session.post.return_value = cm

        result = await authenticated_api._generate_code("test_device")

        assert result == generate_data
        authenticated_api._session.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_claim_grant(self, authenticated_api):
        """Test _claim_grant method."""
        claim_data = {"message": "ok"}

        # Mock claim grant
        cm, mock_response = authenticated_api._session.make_cm_response(
            status=200, json_data=claim_data
        )

        authenticated_api._session.post.return_value = cm

        result = await authenticated_api._claim_grant("test_device", "12345")

        assert result is True
        authenticated_api._session.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_approve_access(self, authenticated_api):
        """Test _approve_access method."""
        cm, mock_response = authenticated_api._session.make_cm_response(status=200)
        authenticated_api._session.post.return_value = cm

        result = await authenticated_api._approve_access("test_device", "12345")

        assert result is True
        authenticated_api._session.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_approve_access_failure(self, authenticated_api):
        """Test _approve_access method with failure."""
        cm, mock_response = authenticated_api._session.make_cm_response(status=400)
        authenticated_api._session.post.return_value = cm

        result = await authenticated_api._approve_access("test_device", "12345")

        assert result is False
        authenticated_api._session.post.assert_called_once()

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
