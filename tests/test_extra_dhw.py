"""Tests for ExtraDhwTimer independent of QvantumAPI."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.qvantum.api import QvantumAPI
from custom_components.qvantum.extra_dhw import ExtraDhwTimer


def test_http_mode_does_not_construct_extra_dhw_timer(mock_session):
    api = QvantumAPI(
        "test@example.com", "password", "test-agent", session=mock_session
    )
    assert api._extra_dhw is None
    api._cancel_extra_dhw_timer()
    assert api._extra_dhw_restore_at is None


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
