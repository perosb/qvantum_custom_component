"""Modbus write routing, encoders, and extra-DHW timer."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.qvantum.api import QvantumAPI
from custom_components.qvantum.const import DHW_MODE_EXTRA, DHW_MODE_NORMAL
from tests.test_api import attach_mock_modbus


def _modbus_api(*, modbus_write: bool = True):
    api = QvantumAPI(
        modbus_tcp=True, user_agent="test-agent", modbus_write=modbus_write
    )
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
        assert later.call_args.args[1] == pytest.approx(3600, abs=1)
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

    @pytest.mark.asyncio
    async def test_timed_extra_persists_restore_deadline(self):
        api = _modbus_api()
        api.hass = MagicMock()
        api.hass.async_create_task = MagicMock()
        store = MagicMock()
        store.async_save = AsyncMock()
        store.async_remove = AsyncMock()
        api._extra_dhw_store = store
        with (
            patch.object(
                api,
                "write_holding_register_for_metric",
                AsyncMock(return_value={"status": "APPLIED"}),
            ),
            patch("homeassistant.helpers.event.async_call_later", return_value=MagicMock()),
        ):
            await api.set_extra_tap_water("dev1", 60)
        store.async_save.assert_awaited_once()
        store.async_remove.assert_not_called()
        api.hass.async_create_task.assert_not_called()
        payload = store.async_save.call_args.args[0]
        assert payload["device_id"] == "dev1"
        assert payload["restore_at"] > 0
        assert api._extra_dhw_restore_at == payload["restore_at"]

    @pytest.mark.asyncio
    async def test_off_clears_persisted_timer(self):
        api = _modbus_api()
        api.hass = MagicMock()
        api.hass.async_create_task = MagicMock()
        store = MagicMock()
        store.async_remove = AsyncMock()
        api._extra_dhw_store = store
        api._extra_dhw_restore_at = 123.0
        with patch.object(
            api,
            "write_holding_register_for_metric",
            AsyncMock(return_value={"status": "APPLIED"}),
        ):
            await api.set_extra_tap_water("dev1", 0)
        store.async_remove.assert_awaited()
        api.hass.async_create_task.assert_not_called()
        assert api._extra_dhw_restore_at is None

    @pytest.mark.asyncio
    async def test_off_keeps_persisted_timer_when_write_fails(self):
        api = _modbus_api()
        api.hass = MagicMock()
        store = MagicMock()
        store.async_remove = AsyncMock()
        api._extra_dhw_store = store
        api._extra_dhw_restore_at = 123.0
        unsub = MagicMock()
        api._extra_dhw_unsub = unsub
        with patch.object(
            api,
            "write_holding_register_for_metric",
            AsyncMock(side_effect=RuntimeError("modbus down")),
        ):
            with pytest.raises(RuntimeError, match="modbus down"):
                await api.set_extra_tap_water("dev1", 0)
        store.async_remove.assert_not_called()
        unsub.assert_not_called()
        assert api._extra_dhw_unsub is unsub
        assert api._extra_dhw_restore_at == 123.0

    @pytest.mark.asyncio
    async def test_close_keeps_persisted_timer(self):
        api = _modbus_api()
        api.hass = MagicMock()
        api.hass.async_create_task = MagicMock()
        store = MagicMock()
        store.async_remove = AsyncMock()
        api._extra_dhw_store = store
        api._extra_dhw_restore_at = 123.0
        unsub = MagicMock()
        api._extra_dhw_unsub = unsub
        await api.close()
        unsub.assert_called_once()
        store.async_remove.assert_not_called()
        assert api._extra_dhw_restore_at == 123.0

    @pytest.mark.asyncio
    async def test_restore_timer_clears_store_when_writes_disabled(self):
        api = _modbus_api(modbus_write=False)
        api.hass = MagicMock()
        store = MagicMock()
        store.async_load = AsyncMock(
            return_value={"device_id": "dev1", "restore_at": 9999999999.0}
        )
        store.async_remove = AsyncMock()
        api._extra_dhw_store = store
        with patch(
            "homeassistant.helpers.event.async_call_later", return_value=MagicMock()
        ) as later:
            await api.async_restore_extra_dhw_timer()
        later.assert_not_called()
        store.async_load.assert_not_called()
        store.async_remove.assert_awaited_once()
        assert api._extra_dhw_restore_at is None

    @pytest.mark.asyncio
    async def test_restore_timer_reschedules_remaining(self):
        api = _modbus_api()
        api.hass = MagicMock()
        restore_at = __import__("time").time() + 120
        store = MagicMock()
        store.async_load = AsyncMock(
            return_value={"device_id": "dev1", "restore_at": restore_at}
        )
        api._extra_dhw_store = store
        with patch(
            "homeassistant.helpers.event.async_call_later", return_value=MagicMock()
        ) as later:
            await api.async_restore_extra_dhw_timer()
        later.assert_called_once()
        assert later.call_args.args[1] == pytest.approx(120, abs=2)
        assert api._extra_dhw_restore_at == restore_at

    @pytest.mark.asyncio
    async def test_restore_timer_writes_normal_when_expired(self):
        api = _modbus_api()
        api.hass = MagicMock()
        store = MagicMock()
        store.async_load = AsyncMock(
            return_value={"device_id": "dev1", "restore_at": 1.0}
        )
        order: list[str] = []

        async def write_normal(*_args, **_kwargs):
            order.append("write")
            return {"status": "APPLIED"}

        async def remove_store():
            order.append("remove")

        store.async_remove = AsyncMock(side_effect=remove_store)
        api._extra_dhw_store = store
        with patch.object(
            api,
            "write_holding_register_for_metric",
            AsyncMock(side_effect=write_normal),
        ) as write:
            await api.async_restore_extra_dhw_timer()
        write.assert_awaited_once_with("dev1", "extra_tap_water", DHW_MODE_NORMAL)
        store.async_remove.assert_awaited()
        assert order == ["write", "remove"]
        assert api._extra_dhw_restore_at is None

    @pytest.mark.asyncio
    async def test_restore_timer_keeps_deadline_when_expired_write_fails(self):
        api = _modbus_api()
        api.hass = MagicMock()
        store = MagicMock()
        store.async_load = AsyncMock(
            return_value={"device_id": "dev1", "restore_at": 1.0}
        )
        store.async_remove = AsyncMock()
        api._extra_dhw_store = store
        with patch.object(
            api,
            "write_holding_register_for_metric",
            AsyncMock(side_effect=RuntimeError("modbus down")),
        ):
            await api.async_restore_extra_dhw_timer()
        store.async_remove.assert_not_called()
        assert api._extra_dhw_restore_at == 1.0

    @pytest.mark.asyncio
    async def test_restore_callback_clears_store_after_write(self):
        api = _modbus_api()
        api.hass = MagicMock()
        store = MagicMock()
        order: list[str] = []

        async def write_normal(*_args, **_kwargs):
            order.append("write")
            return {"status": "APPLIED"}

        async def remove_store():
            order.append("remove")

        store.async_save = AsyncMock()
        store.async_remove = AsyncMock(side_effect=remove_store)
        api._extra_dhw_store = store
        captured = {}

        def fake_later(_hass, _seconds, callback):
            captured["cb"] = callback
            return MagicMock()

        with (
            patch.object(
                api,
                "write_holding_register_for_metric",
                AsyncMock(side_effect=write_normal),
            ) as write,
            patch("homeassistant.helpers.event.async_call_later", side_effect=fake_later),
        ):
            await api.set_extra_tap_water("dev1", 60)
            write.reset_mock()
            order.clear()
            await captured["cb"](None)
        write.assert_awaited_once_with("dev1", "extra_tap_water", DHW_MODE_NORMAL)
        store.async_remove.assert_awaited()
        assert order == ["write", "remove"]
        assert api._extra_dhw_restore_at is None

    @pytest.mark.asyncio
    async def test_restore_callback_keeps_store_when_write_fails(self):
        api = _modbus_api()
        api.hass = MagicMock()
        store = MagicMock()
        store.async_save = AsyncMock()
        store.async_remove = AsyncMock()
        api._extra_dhw_store = store
        captured = {}

        def fake_later(_hass, _seconds, callback):
            captured["cb"] = callback
            return MagicMock()

        with (
            patch.object(
                api,
                "write_holding_register_for_metric",
                AsyncMock(return_value={"status": "APPLIED"}),
            ),
            patch("homeassistant.helpers.event.async_call_later", side_effect=fake_later),
        ):
            await api.set_extra_tap_water("dev1", 60)
            deadline = api._extra_dhw_restore_at
            with patch.object(
                api,
                "write_holding_register_for_metric",
                AsyncMock(side_effect=RuntimeError("modbus down")),
            ):
                await captured["cb"](None)
        store.async_remove.assert_not_called()
        assert api._extra_dhw_restore_at == deadline

    @pytest.mark.asyncio
    async def test_persist_extra_dhw_swallows_store_errors(self):
        api = _modbus_api()
        store = MagicMock()
        store.async_save = AsyncMock(side_effect=OSError("disk full"))
        store.async_remove = AsyncMock(side_effect=OSError("disk full"))
        api._extra_dhw_store = store
        await api.async_persist_extra_dhw({"device_id": "dev1", "restore_at": 1.0})
        await api.async_persist_extra_dhw(None)
        store.async_save.assert_awaited_once()
        store.async_remove.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_schedule_expired_deadline_still_restores(self):
        api = _modbus_api()
        api.hass = MagicMock()
        captured = {}

        def fake_later(_hass, seconds, callback):
            captured["delay"] = seconds
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
            await api._schedule_extra_dhw_at("dev1", 1.0, persist=False)
            assert captured["delay"] == 0
            await captured["cb"](None)
        write.assert_awaited_once_with("dev1", "extra_tap_water", DHW_MODE_NORMAL)

    @pytest.mark.asyncio
    async def test_schedule_clamps_negative_delay(self):
        api = _modbus_api()
        api.hass = MagicMock()
        with patch(
            "homeassistant.helpers.event.async_call_later", return_value=MagicMock()
        ) as later:
            await api._schedule_extra_dhw_at("dev1", 1.0, persist=False)
        later.assert_called_once()
        assert later.call_args.args[1] >= 0


class TestModbusWriteOptionGate:
    @pytest.mark.asyncio
    async def test_extra_tap_water_rejected_when_writes_disabled(self):
        from custom_components.qvantum.api import APIConnectionError

        api = _modbus_api(modbus_write=False)
        with pytest.raises(APIConnectionError, match="Modbus writing is disabled"):
            await api.set_extra_tap_water("dev1", 60)

    @pytest.mark.asyncio
    async def test_update_setting_rejected_when_writes_disabled(self):
        from custom_components.qvantum.api import APIConnectionError

        api = _modbus_api(modbus_write=False)
        with pytest.raises(APIConnectionError, match="Modbus writing is disabled"):
            await api.update_setting("dev1", "op_mode", 1)
