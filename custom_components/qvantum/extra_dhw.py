"""Home Assistant extra-DHW restore timer.

Local Modbus has no ``stopTime``. This helper writes DHW Extra/Normal via a
callback and owns the ``async_call_later`` + ``Store`` policy.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

_LOGGER = logging.getLogger(__name__)

WriteNormal = Callable[[str], Awaitable[Any]]


async def async_apply_extra_tap_water(
    client: Any,
    timer: ExtraDhwTimer | None,
    device_id: str | int,
    minutes: int,
) -> Any:
    """Write extra DHW on the transport, then arm or clear the HA restore timer.

    Cloud encodes duration on the wire. Modbus only writes Extra/Normal; the
    timer is what restores Normal after *minutes*.
    """
    result = await client.set_extra_tap_water(device_id, minutes)
    if timer is not None:
        if minutes > 0:
            await timer.async_schedule(str(device_id), minutes)
        else:
            await timer.async_clear()
    return result


class ExtraDhwTimer:
    """Schedule restoring DHW mode to Normal after extra hot water."""

    def __init__(self, write_normal: WriteNormal) -> None:
        self.hass = None
        self.store = None
        self.restore_at: float | None = None
        self.armed_at: float | None = None
        self.unsub = None
        self._write_normal = write_normal

    def cancel(self, *, clear_store: bool = False) -> None:
        """Cancel a pending restore callback."""
        unsub = self.unsub
        self.unsub = None
        self.armed_at = None
        if unsub:
            unsub()
        if clear_store:
            self.restore_at = None
            self._persist(None)

    async def async_clear(self) -> None:
        """Stop a pending restore because extra DHW is no longer active."""
        self.cancel(clear_store=False)
        self.restore_at = None
        await self.async_persist(None)

    async def async_persist(self, payload: dict | None) -> None:
        """Save or clear the restore deadline."""
        store = self.store
        if store is None:
            return
        try:
            if payload is None:
                await store.async_remove()
            else:
                await store.async_save(payload)
        except Exception:
            if payload is None:
                _LOGGER.debug("Failed to clear extra DHW timer", exc_info=True)
            else:
                _LOGGER.debug("Failed to persist extra DHW timer", exc_info=True)

    def _persist(self, payload: dict | None) -> None:
        """Fire-and-forget persist for sync callers (options listener)."""
        hass = self.hass
        if self.store is None or hass is None:
            return
        create_task = getattr(hass, "async_create_task", None)
        if callable(create_task):
            coro = self.async_persist(payload)
            try:
                create_task(coro, name="qvantum_persist_extra_dhw")
            except TypeError:
                create_task(coro)

    async def async_schedule(self, device_id: str, minutes: int) -> None:
        """After *minutes*, write DHW mode back to Normal."""
        if minutes <= 0:
            return
        restore_at = datetime.now(timezone.utc).timestamp() + minutes * 60
        await self.async_schedule_at(device_id, restore_at, persist=True)

    async def async_schedule_at(
        self, device_id: str, restore_at: float, *, persist: bool
    ) -> None:
        """Schedule restore at an absolute UTC epoch; persist when requested."""
        self.cancel(clear_store=False)
        remaining = restore_at - datetime.now(timezone.utc).timestamp()
        if not self.hass:
            return
        self.restore_at = restore_at
        self.armed_at = time.monotonic()
        if persist:
            await self.async_persist(
                {"device_id": str(device_id), "restore_at": restore_at}
            )
        from homeassistant.helpers.event import async_call_later

        async def _restore(_now) -> None:
            self.unsub = None
            try:
                await self._write_normal(str(device_id))
            except Exception as err:
                _LOGGER.warning(
                    "Failed to restore DHW mode after extra hot water timer: %s", err
                )
                return
            self.restore_at = None
            self.armed_at = None
            try:
                await self.async_persist(None)
            except Exception:
                _LOGGER.debug("Failed to clear extra DHW timer", exc_info=True)

        delay = max(remaining, 0)
        self.unsub = async_call_later(self.hass, delay, _restore)

    async def async_restore(self, *, writable: bool) -> None:
        """Resume a persisted restore after Home Assistant restart."""
        if not writable:
            await self.async_persist(None)
            return
        store = self.store
        if store is None:
            return
        try:
            data = await store.async_load()
        except Exception:
            _LOGGER.debug("Failed to load extra DHW timer", exc_info=True)
            return
        if not isinstance(data, dict):
            return
        device_id = data.get("device_id")
        restore_at = data.get("restore_at")
        if not device_id or not isinstance(restore_at, (int, float)):
            return
        remaining = float(restore_at) - datetime.now(timezone.utc).timestamp()
        if remaining <= 0:
            self.restore_at = float(restore_at)
            try:
                await self._write_normal(str(device_id))
            except Exception as err:
                _LOGGER.warning(
                    "Failed to restore DHW mode after extra hot water timer: %s", err
                )
                return
            self.restore_at = None
            self.armed_at = None
            try:
                await self.async_persist(None)
            except Exception:
                _LOGGER.debug("Failed to clear extra DHW timer", exc_info=True)
            return
        await self.async_schedule_at(str(device_id), float(restore_at), persist=False)
