"""Read-only ventilation-filter replacement calendar (Modbus only).

The pump reports ``ventilation_filter_time_left`` (input register 91, hours)
but no absolute replacement date, and the cloud API does not expose the metric
at all. The calendar derives a stable "due" anchor from the first observed
reading and persists it so a restart does not move the event. A jump up in the
remaining hours means a new filter was fitted; the anchor is then reset. The
due date is exposed as a single all-day event on the local due date.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.calendar import (
    CalendarEntity,
    CalendarEntityFeature,
    CalendarEvent,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import MyConfigEntry
from .const import (
    DOMAIN,
    FILTER_CALENDAR_EVENT_DAYS,
    FILTER_CALENDAR_RESET_JUMP_HOURS,
)
from .coordinator import QvantumDataUpdateCoordinator
from .entity import QvantumEntity

_LOGGER = logging.getLogger(__name__)

# Input register 91 in client/modbus/maps.py; absent from the HTTP API.
FILTER_METRIC = "ventilation_filter_time_left"
FILTER_DUE_METRIC = "ventilation_filter_due"

_STORE_VERSION = 1
# Coalesce a burst of coordinator updates into one store write.
_STORE_SAVE_DELAY = 5.0
_FALLBACK_EVENT_SUMMARY = "Ventilation filter replacement"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the filter calendar. Modbus only: cloud has no filter hours."""
    coordinator: QvantumDataUpdateCoordinator = config_entry.runtime_data.coordinator
    modbus_enabled = bool(getattr(coordinator, "modbus_enabled", False))
    calendar_keys = {FILTER_DUE_METRIC} if modbus_enabled else set()

    if modbus_enabled:
        store = Store(
            hass,
            _STORE_VERSION,
            f"{DOMAIN}.filter_calendar.{config_entry.entry_id}",
        )

        async_add_entities(
            [
                QvantumFilterCalendarEntity(
                    coordinator, config_entry.runtime_data.device, store
                )
            ]
        )

    # Prune the entity registry when switching to cloud mode, or when an old
    # calendar metric is no longer provided.
    from .entity import cleanup_disabled_entities

    cleanup_disabled_entities(hass, coordinator, calendar_keys, "calendar")


class QvantumFilterCalendarEntity(QvantumEntity, CalendarEntity):
    """Calendar with the next ventilation-filter replacement due."""

    _attr_supported_features = CalendarEntityFeature(0)

    def __init__(
        self,
        coordinator: QvantumDataUpdateCoordinator,
        device: DeviceInfo | dict,
        store: Store | None = None,
    ) -> None:
        """Initialize the read-only calendar."""
        super().__init__(coordinator, FILTER_DUE_METRIC, device)
        self._store = store
        self._due_at: datetime | None = None
        self._anchor_hours: float | None = None

    @property
    def _hours_left(self) -> float | None:
        """Return the current filter hours remaining, if reported."""
        value = self._values.get(FILTER_METRIC)
        if value is None or isinstance(value, bool):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @property
    def event(self) -> CalendarEvent | None:
        """Return the next replacement all-day event, or None without data."""
        due_at = self._due_at
        if due_at is None or self._hours_left is None:
            return None
        # All-day event on the local due date; the end date is exclusive.
        due_date = dt_util.as_local(due_at).date()
        return CalendarEvent(
            start=due_date,
            end=due_date + timedelta(days=FILTER_CALENDAR_EVENT_DAYS),
            summary=self._event_summary,
        )

    @property
    def _event_summary(self) -> str:
        """Use the translated entity name; fall back before translations load."""
        name = getattr(self, "name", None)
        if isinstance(name, str) and name:
            return name
        return _FALLBACK_EVENT_SUMMARY

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return the replacement event when it overlaps the requested window."""
        event = self.event
        if event is None:
            return []
        if (
            event.end_datetime_local <= start_date
            or event.start_datetime_local >= end_date
        ):
            return []
        return [event]

    async def async_added_to_hass(self) -> None:
        """Restore the persisted due anchor, then keep it in sync."""
        await super().async_added_to_hass()
        await self._async_restore_anchor()
        self._refresh_due()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Re-evaluate the due time on every poll."""
        self._refresh_due()
        super()._handle_coordinator_update()

    async def _async_restore_anchor(self) -> None:
        """Load the due anchor persisted by a previous run."""
        store = self._store
        if store is None:
            return
        try:
            data = await store.async_load()
        except Exception:
            _LOGGER.debug("Failed to load filter calendar anchor", exc_info=True)
            return
        if not isinstance(data, dict):
            return
        due_at = data.get("due_at")
        anchor_hours = data.get("anchor_hours")
        if isinstance(due_at, (int, float)) and not isinstance(due_at, bool):
            self._due_at = dt_util.utc_from_timestamp(float(due_at))
        if isinstance(anchor_hours, (int, float)) and not isinstance(
            anchor_hours, bool
        ):
            self._anchor_hours = float(anchor_hours)

    def _refresh_due(self) -> None:
        """Anchor, or re-anchor, the due time from the current reading."""
        hours_left = self._hours_left
        if hours_left is None:
            return
        now = dt_util.utcnow()
        if self._due_at is None:
            self._anchor(now, hours_left)
            return
        anchor_hours = self._anchor_hours or 0.0
        if hours_left > anchor_hours + FILTER_CALENDAR_RESET_JUMP_HOURS:
            # Remaining hours jumped up: a new filter was fitted.
            self._anchor(now, hours_left)
            return
        if (
            hours_left > FILTER_CALENDAR_RESET_JUMP_HOURS
            and now
            > self._due_at + timedelta(days=FILTER_CALENDAR_EVENT_DAYS)
        ):
            # The anchored due passed while the counter still reported usable
            # hours (for example the pump pauses the countdown). Realign the
            # event with the pump's own reading.
            self._anchor(now, hours_left)

    def _anchor(self, now: datetime, hours_left: float) -> None:
        """Set the due time to now + hours_left and persist it."""
        self._due_at = now + timedelta(hours=hours_left)
        self._anchor_hours = hours_left
        self._schedule_persist()

    def _schedule_persist(self) -> None:
        """Store the anchor without blocking the coordinator update."""
        store = self._store
        if store is None:
            return
        try:
            store.async_delay_save(self._store_payload, _STORE_SAVE_DELAY)
        except Exception:
            _LOGGER.debug("Failed to persist filter calendar anchor", exc_info=True)

    def _store_payload(self) -> dict[str, Any]:
        """Return the JSON-serializable anchor for the store."""
        due_at = self._due_at
        return {
            "due_at": due_at.timestamp() if due_at is not None else None,
            "anchor_hours": self._anchor_hours,
        }