"""Tests for the Modbus client through the test-only client factory."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.qvantum.client.exceptions import APIConnectionError
from tests.test_api import QvantumAPI, attach_mock_modbus


class TestQvantumModbusClient:
    """Tests for Modbus reads, lifecycle, and transport isolation."""

    @pytest.mark.asyncio
    async def test_get_metrics_modbus_tcp_failure_does_not_use_http(self, mock_session):
        """Modbus TCP failures must not fall back to HTTP."""
        api = QvantumAPI(
            "test@example.com",
            "password",
            "test-agent",
            session=mock_session,
            modbus_tcp=True,
        )

        with patch.object(
            api,
            "get_metrics",
            AsyncMock(side_effect=APIConnectionError(None, "modbus not reachable")),
        ):
            with pytest.raises(APIConnectionError, match="modbus not reachable"):
                await api.get_metrics("test_device_123", enabled_metrics=["bt1"])

        mock_session.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_metrics_modbus_no_latency_placeholder_in_registers(
        self, mock_session
    ):
        """Modbus payloads must not include coordinator-only latency."""
        api = QvantumAPI(
            "test@example.com",
            "password",
            "test-agent",
            session=mock_session,
            modbus_tcp=True,
        )
        attach_mock_modbus(api)

        raw = await api.get_metrics("dev", ["bt1"])

        assert "latency" not in raw.get("metrics", {})

    @pytest.mark.asyncio
    async def test_read_modbus_reuse_connection_between_reads(self):
        """Modbus connection should remain open across successful reads."""
        api = QvantumAPI("test@example.com", "password", "test-agent", modbus_tcp=True)
        connection, _device = attach_mock_modbus(api)

        result1 = await api.get_metrics("test_device", ["bt1"])
        assert result1["metrics"]["hpid"] == "test_device"
        assert api.unit is connection.for_unit(1)

        result2 = await api.get_metrics("test_device", ["bt1"])
        assert result2["metrics"]["hpid"] == "test_device"
        assert api.device is not None

    @pytest.mark.asyncio
    async def test_read_modbus_concurrent_access_is_serialized(self):
        """Concurrent Modbus reads are serialized by the client lock."""
        api = QvantumAPI("test@example.com", "password", "test-agent", modbus_tcp=True)
        _connection, device = attach_mock_modbus(api)

        in_flight = 0
        max_in_flight = 0
        original = device.async_update_inputs

        async def slow_update():
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0.05)
            in_flight -= 1
            return await original()

        device.async_update_inputs = slow_update

        results = await asyncio.gather(
            api.get_metrics("test_device", ["bt1"]),
            api.get_metrics("test_device", ["bt1"]),
        )

        assert max_in_flight == 1
        assert results[0]["metrics"]["hpid"] == "test_device"
        assert results[1]["metrics"]["hpid"] == "test_device"

    @pytest.mark.asyncio
    async def test_get_metrics_after_close_raises(self, mock_session):
        """A closed Modbus client must not attempt HTTP requests."""
        api = QvantumAPI(
            "test@example.com",
            "password",
            "test-agent",
            session=mock_session,
            modbus_tcp=True,
        )
        await api.close()

        with pytest.raises(APIConnectionError, match="client is closed"):
            await api.get_metrics("test_device", enabled_metrics=["bt1"])

        mock_session.get.assert_not_called()
        mock_session.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_modbus_mode_does_not_open_http_session(self):
        """Constructing a Modbus client must not create an HTTP session."""
        with patch("aiohttp.ClientSession") as mock_session_class:
            api = QvantumAPI(modbus_tcp=True, user_agent="test-agent")

        mock_session_class.assert_not_called()
        assert not hasattr(api, "_session")

    @pytest.mark.asyncio
    async def test_modbus_mode_discards_injected_http_session(self, mock_session):
        """A Modbus client must not retain an injected HTTP session."""
        api = QvantumAPI(
            "test@example.com",
            "password",
            "test-agent",
            session=mock_session,
            modbus_tcp=True,
        )

        assert not hasattr(api, "_session")

    @pytest.mark.asyncio
    async def test_probe_identity(self):
        api = QvantumAPI(modbus_tcp=True, user_agent="test-agent")
        _connection, _device = attach_mock_modbus(api)
        api.unit.input[180] = 12
        api.unit.input[181] = 3
        api.unit.input[182] = 45
        api.unit.input[183] = 6
        api.unit.input[184] = 7
        api.unit.input[191] = 1
        api.unit.input[192] = 7
        api.unit.input[193] = 22

        result = await api.probe_identity()

        assert result["id"] == "12003045006007"
        assert result["serial"] == "12003045006007"
        assert result["vendor"] == "Qvantum"
        assert result["sw_version"] == "1.7.22"

    @pytest.mark.asyncio
    async def test_get_metrics_modbus_cancel_does_not_http_fallback(self, mock_session):
        """Cancelled Modbus polls must not fall back to HTTP during reload."""
        api = QvantumAPI(
            "test@example.com",
            "password",
            "test-agent",
            session=mock_session,
            modbus_tcp=True,
        )

        with patch.object(
            api, "get_metrics", AsyncMock(side_effect=asyncio.CancelledError())
        ):
            with pytest.raises(asyncio.CancelledError):
                await api.get_metrics("test_device_123", enabled_metrics=["bt1"])

        mock_session.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_metrics_modbus_closed_client_does_not_http_fallback(
        self, mock_session
    ):
        """Closed-client errors in Modbus mode must not fall back to HTTP."""
        api = QvantumAPI(
            "test@example.com",
            "password",
            "test-agent",
            session=mock_session,
            modbus_tcp=True,
        )

        with patch.object(
            api,
            "get_metrics",
            AsyncMock(side_effect=APIConnectionError(None, "API client is closed")),
        ):
            with pytest.raises(APIConnectionError, match="API client is closed"):
                await api.get_metrics("test_device_123", enabled_metrics=["bt1"])

        mock_session.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_metrics_shared_connection_closed_does_not_use_http(
        self, mock_session
    ):
        """A closed borrowed Modbus link must not fall back to HTTP."""
        from modbus_connection import ClientClosedError

        api = QvantumAPI(
            "test@example.com",
            "password",
            "test-agent",
            session=mock_session,
            modbus_tcp=True,
        )
        _connection, device = attach_mock_modbus(api)

        async def closed_update():
            raise ClientClosedError("connection closed")

        device.async_update_inputs = closed_update

        with pytest.raises(APIConnectionError, match="Modbus communication failed"):
            await api.get_metrics("test_device", enabled_metrics=["bt1"])

        mock_session.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_close_waits_for_in_flight_modbus_lock(self):
        """close() waits until a Modbus read releases the client lock."""
        api = QvantumAPI("test@example.com", "password", "test-agent", modbus_tcp=True)
        attach_mock_modbus(api)

        entered = asyncio.Event()
        release = asyncio.Event()
        holder = None
        closer = None

        async def hold_lock():
            try:
                async with api._lock:
                    entered.set()
                    await release.wait()
            except Exception:
                entered.set()
                raise

        try:
            holder = asyncio.create_task(hold_lock())
            await asyncio.wait_for(entered.wait(), timeout=1)

            closer = asyncio.create_task(api.close())
            done, _pending = await asyncio.wait({closer}, timeout=0.05)
            assert closer not in done
            assert api.device is not None

            release.set()
            await asyncio.wait_for(closer, timeout=1)
            await asyncio.wait_for(holder, timeout=1)

            assert api.device is None
            assert api._closed is True
        finally:
            release.set()
            for task in (holder, closer):
                if task is not None and not task.done():
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass

    @pytest.mark.asyncio
    async def test_read_modbus_cancelled_keeps_shared_unit(self):
        """Cancelled Modbus updates must not close the borrowed shared unit."""
        api = QvantumAPI("test@example.com", "password", "test-agent", modbus_tcp=True)
        _connection, device = attach_mock_modbus(api)

        async def cancel_update():
            raise asyncio.CancelledError()

        device.async_update_inputs = cancel_update

        with pytest.raises(asyncio.CancelledError):
            await api.get_metrics("dev", ["bt1"])

        assert api.unit is not None
        assert api.device is device

    @pytest.mark.asyncio
    async def test_get_metrics_modbus_tcp(self, mock_session):
        """Metrics are decoded from the mock Modbus unit."""
        api = QvantumAPI(
            "test@example.com",
            "password",
            "test-agent",
            session=mock_session,
            modbus_tcp=True,
            modbus_host="192.168.1.100",
            modbus_port=502,
        )
        _connection, device = attach_mock_modbus(api)
        device.unit.input[0] = 256

        result = await api.get_metrics("test_device", enabled_metrics=["bt1"])

        assert "metrics" in result
        assert result["metrics"]["hpid"] == "test_device"
        assert result["metrics"]["bt1"] == 25.6
        assert "latency" not in result["metrics"]

    @pytest.mark.asyncio
    async def test_get_metrics_modbus_connection_failure(self, mock_session):
        """A Modbus connection failure must not fall back to HTTP."""
        from modbus_connection import ModbusConnectionError

        api = QvantumAPI(
            "test@example.com",
            "password",
            "test-agent",
            session=mock_session,
            modbus_tcp=True,
        )
        _connection, device = attach_mock_modbus(api)
        device.unit.fail_requests(ModbusConnectionError())

        with pytest.raises(APIConnectionError, match="Modbus communication failed"):
            await api.get_metrics("test_device", enabled_metrics=["bt1"])

        mock_session.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_settings_modbus(self, mock_session):
        """Settings in Modbus mode are returned without HTTP requests."""
        api = QvantumAPI(
            "test@example.com",
            "password",
            "test-agent",
            session=mock_session,
            modbus_tcp=True,
        )
        expected_settings = {
            "settings": [
                {"name": "unit_on_off", "value": 1},
                {"name": "operation_mode", "value": 0},
            ]
        }

        with patch.object(
            api, "get_settings", AsyncMock(return_value=expected_settings)
        ) as mock_get_settings:
            result = await api.get_settings("test_device")

        assert result == expected_settings
        mock_session.get.assert_not_called()
        mock_get_settings.assert_awaited_once_with("test_device")

    def test_ensure_device_when_enabled(self, mock_session):
        api = QvantumAPI(
            "test@example.com",
            "password",
            "test-agent",
            session=mock_session,
            modbus_tcp=True,
        )
        _connection, attached = attach_mock_modbus(api)

        device = api._ensure_device()

        assert device is attached
        assert api.device is device

    def test_ensure_device_without_unit_returns_none(self, mock_session):
        api = QvantumAPI(
            "test@example.com",
            "password",
            "test-agent",
            session=mock_session,
            modbus_tcp=True,
        )

        assert api._ensure_device() is None

    @pytest.mark.asyncio
    async def test_get_metrics_device_error_raises(self, mock_session):
        from modbus_connection import ModbusTimeoutError

        api = QvantumAPI(
            "test@example.com",
            "password",
            "test-agent",
            session=mock_session,
            modbus_tcp=True,
        )
        _connection, device = attach_mock_modbus(api)
        device.unit.fail_requests(ModbusTimeoutError())

        with pytest.raises(APIConnectionError, match="Modbus communication failed"):
            await api.get_metrics("test_device", ["bt1"])

    @pytest.mark.asyncio
    async def test_get_metrics_ensures_use_adaptive(self, mock_session):
        api = QvantumAPI(
            "test@example.com",
            "password",
            "test-agent",
            session=mock_session,
            modbus_tcp=True,
        )
        _connection, device = attach_mock_modbus(api)
        device.unit.input[161] = 1

        result = await api.get_metrics("test_device", ["smart_dhw_mode"])

        assert result["metrics"]["use_adaptive"] is True
        assert result["metrics"]["smart_sh_mode"] == 1

    @pytest.mark.asyncio
    async def test_get_settings_fan_and_extra_tap_water(self, mock_session):
        api = QvantumAPI(
            "test@example.com",
            "password",
            "test-agent",
            session=mock_session,
            modbus_tcp=True,
        )
        _connection, device = attach_mock_modbus(api)
        device.unit.holding[53] = 2  # dhw_mode -> extra_tap_water
        device.unit.holding[68] = 2  # ventilation_state -> fanspeedselector

        result = await api.get_settings(
            "test_device", ["dhw_mode", "ventilation_state"]
        )

        settings = {item["name"]: item["value"] for item in result["settings"]}
        assert settings["extra_tap_water"] == "on"
        assert settings["fanspeedselector"] == "extra"


class TestWriteHoldingRegister:
    """Tests for Modbus holding-register writes."""

    def _make_api(self, mock_session=None):
        kwargs = {
            "modbus_tcp": True,
            "modbus_write": True,
            "modbus_host": "192.168.1.100",
            "modbus_port": 502,
        }
        if mock_session is not None:
            kwargs["session"] = mock_session
        return QvantumAPI("test@example.com", "password", "test-agent", **kwargs)

    @pytest.mark.asyncio
    async def test_write_holding_register_success(self, mock_session):
        api = self._make_api(mock_session)
        _connection, device = attach_mock_modbus(api)

        result = await api.write_holding_register("dev1", 59, 75)

        assert result == {"status": "APPLIED"}
        assert device.unit.holding[59] == 75

    @pytest.mark.asyncio
    async def test_write_holding_register_raises_on_device_error(self, mock_session):
        from modbus_connection import IllegalDataValueError

        api = self._make_api(mock_session)
        _connection, device = attach_mock_modbus(api)
        device.unit.fail_write(59, IllegalDataValueError())

        with pytest.raises(APIConnectionError, match="Modbus write failed"):
            await api.write_holding_register("dev1", 59, 75)

    @pytest.mark.asyncio
    async def test_write_metric_dhw_stop_extra(self, mock_session):
        api = self._make_api(mock_session)
        _connection, device = attach_mock_modbus(api)

        result = await api.write_metric("dev1", "dhw_stop_extra", 75)

        assert result == {"status": "APPLIED"}
        assert device.unit.holding[59] == 75

    @pytest.mark.asyncio
    async def test_write_metric_unknown_key_raises(self, mock_session):
        api = self._make_api(mock_session)

        with pytest.raises(
            ValueError, match="No Modbus holding register mapping found"
        ):
            await api.write_metric("dev1", "not_a_real_metric", 42)

    @pytest.mark.asyncio
    async def test_write_metric_applies_scale(self, mock_session):
        api = self._make_api(mock_session)
        _connection, device = attach_mock_modbus(api)

        result = await api.write_metric("dev1", "room_comp_factor", 2.5)

        assert result == {"status": "APPLIED"}
        assert device.unit.holding[13] == 25

    @pytest.mark.asyncio
    async def test_write_metric_room_temp_external_scaling(self, mock_session):
        api = self._make_api(mock_session)
        _connection, device = attach_mock_modbus(api)

        result = await api.write_metric("dev1", "room_temp_external", 21.5)

        assert result == {"status": "APPLIED"}
        assert device.unit.holding[14] == 215

    @pytest.mark.asyncio
    async def test_write_metric_stop_heating(self, mock_session):
        api = self._make_api(mock_session)
        _connection, device = attach_mock_modbus(api)

        result = await api.write_metric("dev1", "stop_heating", -15)

        assert result == {"status": "APPLIED"}
        assert device.unit.holding[18] == 0xFFF1

    @pytest.mark.asyncio
    async def test_write_metric_heating_curve_point(self, mock_session):
        api = self._make_api(mock_session)
        _connection, device = attach_mock_modbus(api)

        result = await api.write_metric("dev1", "curve_minus_30", 48)

        assert result == {"status": "APPLIED"}
        assert device.unit.holding[24] == 48
