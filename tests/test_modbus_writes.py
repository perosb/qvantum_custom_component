"""Modbus write routing, encoders, and extra-DHW timer."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.qvantum.api import QvantumAPI
from custom_components.qvantum.const import DHW_MODE_EXTRA, DHW_MODE_NORMAL
from tests.test_api import attach_mock_modbus


def _modbus_api():
    api = QvantumAPI(modbus_tcp=True, user_agent="test-agent")
    attach_mock_modbus(api)
    return api


class TestModbusSettingWrites:
    @pytest.mark.asyncio
    async def test_update_setting_writes_holding_register(self):
        api = _modbus_api()
        with patch.object(
            api,
            "write_holding_register_for_metric",
            AsyncMock(return_value={"status": "APPLIED"}),
        ) as write:
            result = await api.update_setting("dev1", "op_mode", 1)
        write.assert_awaited_once_with("dev1", "op_mode", 1)
        assert result == {"status": "APPLIED"}

    @pytest.mark.asyncio
    async def test_update_setting_coerces_bool(self):
        api = _modbus_api()
        with patch.object(
            api,
            "write_holding_register_for_metric",
            AsyncMock(return_value={"status": "APPLIED"}),
        ) as write:
            await api.update_setting("dev1", "man_mode", True)
        write.assert_awaited_once_with("dev1", "man_mode", 1)

    @pytest.mark.asyncio
    async def test_indoor_temperature_target_writes_metric(self):
        api = _modbus_api()
        with patch.object(
            api,
            "write_holding_register_for_metric",
            AsyncMock(return_value={"status": "APPLIED"}),
        ) as write:
            await api.set_indoor_temperature_target("dev1", 21.5)
        write.assert_awaited_once_with("dev1", "indoor_temperature_target", 21.5)

    @pytest.mark.asyncio
    async def test_indoor_offset_writes_metric(self):
        api = _modbus_api()
        with patch.object(
            api,
            "write_holding_register_for_metric",
            AsyncMock(return_value={"status": "APPLIED"}),
        ) as write:
            await api.set_indoor_temperature_offset("dev1", -2)
        write.assert_awaited_once_with("dev1", "indoor_temperature_offset", -2)

    @pytest.mark.asyncio
    async def test_tap_water_writes_start_and_stop(self):
        api = _modbus_api()
        with patch.object(
            api,
            "write_holding_register_for_metric",
            AsyncMock(return_value={"status": "APPLIED"}),
        ) as write:
            result = await api.set_tap_water("dev1", start=52, stop=62)
        assert write.await_count == 2
        write.assert_any_await("dev1", "tap_water_stop", 62)
        write.assert_any_await("dev1", "tap_water_start", 52)
        assert result == {"status": "APPLIED"}

    @pytest.mark.asyncio
    async def test_capacity_writes_mapped_start_stop(self):
        api = _modbus_api()
        with patch.object(
            api, "set_tap_water", AsyncMock(return_value={"status": "APPLIED"})
        ) as set_tap:
            await api.set_tap_water_capacity_target("dev1", 2)
        set_tap.assert_awaited_once_with("dev1", start=52, stop=62)

    @pytest.mark.asyncio
    async def test_fan_preset_writes_ventilation_state(self):
        api = _modbus_api()
        with patch.object(
            api,
            "write_holding_register_for_metric",
            AsyncMock(return_value={"status": "APPLIED"}),
        ) as write:
            await api.set_fanspeedselector("dev1", "extra")
        write.assert_awaited_once_with("dev1", "fanspeedselector", 2)

    @pytest.mark.asyncio
    async def test_fan_invalid_preset_raises(self):
        api = _modbus_api()
        with pytest.raises(ValueError, match="Invalid preset_mode"):
            await api.set_fanspeedselector("dev1", "turbo")


class TestExtraTapWaterModbus:
    @pytest.mark.asyncio
    async def test_indefinite_writes_extra_mode(self):
        api = _modbus_api()
        with patch.object(
            api,
            "write_holding_register_for_metric",
            AsyncMock(return_value={"status": "APPLIED"}),
        ) as write:
            await api.set_extra_tap_water("dev1", -1)
        write.assert_awaited_once_with("dev1", "extra_tap_water", DHW_MODE_EXTRA)
        assert api._extra_dhw_unsub is None

    @pytest.mark.asyncio
    async def test_off_writes_normal_mode(self):
        api = _modbus_api()
        with patch.object(
            api,
            "write_holding_register_for_metric",
            AsyncMock(return_value={"status": "APPLIED"}),
        ) as write:
            await api.set_extra_tap_water("dev1", 0)
        write.assert_awaited_once_with("dev1", "extra_tap_water", DHW_MODE_NORMAL)

    @pytest.mark.asyncio
    async def test_timed_extra_schedules_restore(self):
        api = _modbus_api()
        api.hass = MagicMock()
        unsub = MagicMock()
        with (
            patch.object(
                api,
                "write_holding_register_for_metric",
                AsyncMock(return_value={"status": "APPLIED"}),
            ) as write,
            patch(
                "homeassistant.helpers.event.async_call_later", return_value=unsub
            ) as later,
        ):
            await api.set_extra_tap_water("dev1", 60)
        write.assert_awaited_once_with("dev1", "extra_tap_water", DHW_MODE_EXTRA)
        later.assert_called_once()
        assert later.call_args.args[1] == 3600
        assert api._extra_dhw_unsub is unsub

    @pytest.mark.asyncio
    async def test_off_cancels_pending_timer(self):
        api = _modbus_api()
        unsub = MagicMock()
        api._extra_dhw_unsub = unsub
        with patch.object(
            api,
            "write_holding_register_for_metric",
            AsyncMock(return_value={"status": "APPLIED"}),
        ):
            await api.set_extra_tap_water("dev1", 0)
        unsub.assert_called_once()
        assert api._extra_dhw_unsub is None

    @pytest.mark.asyncio
    async def test_close_cancels_timer(self):
        api = _modbus_api()
        unsub = MagicMock()
        api._extra_dhw_unsub = unsub
        await api.close()
        unsub.assert_called_once()
        assert api._extra_dhw_unsub is None

    @pytest.mark.asyncio
    async def test_restore_callback_writes_normal(self):
        api = _modbus_api()
        api.hass = MagicMock()
        captured = {}

        def fake_later(_hass, _seconds, callback):
            captured["cb"] = callback
            return MagicMock()

        with (
            patch.object(
                api,
                "write_holding_register_for_metric",
                AsyncMock(return_value={"status": "APPLIED"}),
            ) as write,
            patch("homeassistant.helpers.event.async_call_later", side_effect=fake_later),
        ):
            await api.set_extra_tap_water("dev1", 60)
            write.reset_mock()
            await captured["cb"](None)
        write.assert_awaited_once_with("dev1", "extra_tap_water", DHW_MODE_NORMAL)
