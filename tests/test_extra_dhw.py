"""Tests for ExtraDhwTimer independent of QvantumAPI."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.qvantum.extra_dhw import ExtraDhwTimer


def _timer():
    write = AsyncMock()
    timer = ExtraDhwTimer(write)
    timer.hass = MagicMock()
    timer.store = MagicMock()
    timer.store.async_save = AsyncMock()
    timer.store.async_remove = AsyncMock()
    timer.store.async_load = AsyncMock(return_value=None)
    return write, timer


@pytest.mark.asyncio
async def test_schedule_owns_async_call_later_and_store():
    write, timer = _timer()
    unsub = MagicMock()
    with patch(
        "homeassistant.helpers.event.async_call_later", return_value=unsub
    ) as later:
        await timer.async_schedule("dev1", 60)

    later.assert_called_once()
    hass, delay, callback = later.call_args[0]
    assert hass is timer.hass
    assert delay > 0
    timer.store.async_save.assert_awaited_once()
    assert timer.unsub is unsub
    assert timer.restore_at is not None

    await callback(None)
    write.assert_awaited_once_with("dev1")
    timer.store.async_remove.assert_awaited()
    assert timer.restore_at is None


@pytest.mark.asyncio
async def test_clear_cancels_callback_and_store():
    write, timer = _timer()
    unsub = MagicMock()
    timer.unsub = unsub
    timer.restore_at = 123.0
    await timer.async_clear()
    unsub.assert_called_once()
    write.assert_not_called()
    timer.store.async_remove.assert_awaited_once()
    assert timer.restore_at is None


@pytest.mark.asyncio
async def test_restore_without_write_drops_store():
    write, timer = _timer()
    await timer.async_restore(writable=False)
    timer.store.async_remove.assert_awaited_once()
    write.assert_not_called()


@pytest.mark.asyncio
async def test_restore_retries_after_failed_write():
    """A failed DHW->Normal write keeps the deadline and reschedules."""
    write, timer = _timer()
    write.side_effect = RuntimeError("modbus down")
    unsub = MagicMock()
    with patch(
        "homeassistant.helpers.event.async_call_later", return_value=unsub
    ) as later:
        await timer.async_schedule("dev1", 60)
        _, _, callback = later.call_args[0]
        await callback(None)

    assert later.call_count == 2
    assert timer.restore_at is not None


@pytest.mark.asyncio
async def test_restore_ignores_implausible_deadline():
    """A far-future persisted deadline is rejected and cleared."""
    write, timer = _timer()
    timer.store.async_load = AsyncMock(
        return_value={"device_id": "dev1", "restore_at": 1e15}
    )

    await timer.async_restore(writable=True)

    write.assert_not_called()
    timer.store.async_remove.assert_awaited()


@pytest.mark.asyncio
async def test_restore_ignores_bool_deadline():
    """A boolean restore_at must not be treated as a numeric deadline."""
    write, timer = _timer()
    timer.store.async_load = AsyncMock(
        return_value={"device_id": "dev1", "restore_at": True}
    )

    await timer.async_restore(writable=True)

    write.assert_not_called()
    timer.store.async_remove.assert_awaited()


@pytest.mark.asyncio
async def test_restore_ignores_nan_deadline():
    """A NaN restore_at (accepted by json.loads) must not be scheduled."""
    write, timer = _timer()
    timer.store.async_load = AsyncMock(
        return_value={"device_id": "dev1", "restore_at": float("nan")}
    )

    await timer.async_restore(writable=True)

    write.assert_not_called()
    timer.store.async_remove.assert_awaited()
