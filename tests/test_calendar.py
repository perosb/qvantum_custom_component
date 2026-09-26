"""Tests for the Qvantum ventilation filter calendar."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.util import dt as dt_util

from custom_components.qvantum.calendar import (
    FILTER_DUE_METRIC,
    FILTER_METRIC,
    QvantumFilterCalendarEntity,
    async_setup_entry,
)
from custom_components.qvantum.const import REQUIRED_MODBUS_METRICS
from custom_components.qvantum.entity import QvantumEntity


def _coordinator(
    *, modbus: bool = True, hours_left: float | None = 100.0
) -> MagicMock:
    """Coordinator mock with the Modbus filter metric."""
    coordinator = MagicMock()
    coordinator.modbus_enabled = modbus
    values = {} if hours_left is None else {FILTER_METRIC: hours_left}
    coordinator.data = {"values": values}
    return coordinator


def _entity(
    *,
    hours_left: float | None = 100.0,
    store: MagicMock | None = None,
) -> QvantumFilterCalendarEntity:
    """Build the calendar entity without a live Home Assistant."""
    return QvantumFilterCalendarEntity(
        _coordinator(hours_left=hours_left), {"id": "dev-1"}, store
    )


def _config_entry(*, modbus: bool) -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.runtime_data.coordinator = _coordinator(modbus=modbus)
    entry.runtime_data.device = {"id": "dev-1"}
    return entry


def test_filter_metric_is_always_polled_in_modbus_mode():
    """Disabling the diagnostic filter sensor must not blank the calendar."""
    assert FILTER_METRIC in REQUIRED_MODBUS_METRICS


def test_entity_identity():
    entity = _entity()

    assert entity.metric_key == FILTER_DUE_METRIC
    assert entity.unique_id == f"qvantum_{FILTER_DUE_METRIC}_dev-1"
    assert entity.translation_key == FILTER_DUE_METRIC
    assert entity.icon == "mdi:air-filter"
    assert int(entity.supported_features) == 0


def test_event_is_none_without_reading():
    entity = _entity(hours_left=None)
    entity._refresh_due()

    assert entity.event is None


def test_event_is_all_day_on_due_date():
    entity = _entity(hours_left=100.0)
    before = dt_util.utcnow()

    entity._refresh_due()

    after = dt_util.utcnow()
    event = entity.event
    assert event is not None
    assert event.all_day is True
    expected_dates = {
        dt_util.as_local(before + timedelta(hours=100)).date(),
        dt_util.as_local(after + timedelta(hours=100)).date(),
    }
    assert event.start in expected_dates
    assert event.end == event.start + timedelta(days=1)
    assert event.summary == "Ventilation filter replacement"


def test_anchor_is_stable_while_counter_ticks_down():
    entity = _entity(hours_left=100.0)
    entity._refresh_due()
    due_at = entity._due_at

    entity.coordinator.data["values"][FILTER_METRIC] = 99.0
    entity._refresh_due()

    assert entity._due_at == due_at


def test_new_filter_reanchors_due():
    entity = _entity(hours_left=2.0)
    entity._refresh_due()
    first_due = entity._due_at
    assert first_due is not None

    before = dt_util.utcnow()
    entity.coordinator.data["values"][FILTER_METRIC] = 4380.0
    entity._refresh_due()
    after = dt_util.utcnow()

    assert entity._due_at is not None
    assert entity._due_at > first_due
    assert before + timedelta(hours=4380) <= entity._due_at
    assert entity._due_at <= after + timedelta(hours=4380)


def test_stale_anchor_realigns_to_live_reading():
    entity = _entity(hours_left=48.0)
    entity._refresh_due()
    entity._due_at = dt_util.utcnow() - timedelta(days=2)

    entity.coordinator.data["values"][FILTER_METRIC] = 12.0
    before = dt_util.utcnow()
    entity._refresh_due()

    assert entity._due_at is not None
    assert entity._due_at >= before + timedelta(hours=12)


def test_zero_hours_keeps_expired_anchor():
    entity = _entity(hours_left=5.0)
    entity._refresh_due()
    expired = dt_util.utcnow() - timedelta(hours=2)
    entity._due_at = expired

    entity.coordinator.data["values"][FILTER_METRIC] = 0.0
    entity._refresh_due()

    assert entity._due_at == expired


def test_invalid_reading_is_ignored():
    entity = _entity(hours_left=10.0)
    entity.coordinator.data["values"][FILTER_METRIC] = "not-a-number"

    entity._refresh_due()

    assert entity._due_at is None
    assert entity.event is None


def test_coordinator_update_refreshes_due():
    entity = _entity(hours_left=100.0)

    with (
        # Other test modules import the HA CoordinatorEntity while it is
        # mocked, so in the full suite the shared QvantumEntity base can lack
        # the callback. Supply it to exercise only this method.
        patch.object(QvantumEntity, "_handle_coordinator_update", create=True),
        patch.object(entity, "async_write_ha_state"),
    ):
        entity._handle_coordinator_update()

    assert entity._due_at is not None


def test_event_summary_prefers_translated_name():
    entity = _entity(hours_left=10.0)
    entity.__dict__["name"] = "Ventilationsfilterbyte"

    entity._refresh_due()

    assert entity.event is not None
    assert entity.event.summary == "Ventilationsfilterbyte"


@pytest.mark.asyncio
async def test_added_to_hass_restores_and_anchors():
    store = MagicMock()
    store.async_load = AsyncMock(return_value=None)
    entity = _entity(hours_left=24.0, store=store)

    await entity.async_added_to_hass()

    assert entity.event is not None


@pytest.mark.asyncio
async def test_restore_without_store_is_noop():
    entity = _entity(hours_left=1.0, store=None)

    await entity._async_restore_anchor()

    assert entity._due_at is None


@pytest.mark.asyncio
async def test_async_get_events_filters_window(hass):
    entity = _entity(hours_left=100.0)
    entity._refresh_due()
    event = entity.event
    assert event is not None
    start = event.start_datetime_local

    assert await entity.async_get_events(
        hass, start - timedelta(hours=1), start + timedelta(days=1, hours=1)
    ) == [event]
    assert (
        await entity.async_get_events(
            hass, start - timedelta(days=1), start - timedelta(hours=1)
        )
        == []
    )
    assert (
        await entity.async_get_events(
            hass, start + timedelta(days=1), start + timedelta(days=2)
        )
        == []
    )


@pytest.mark.asyncio
async def test_async_get_events_empty_without_reading(hass):
    entity = _entity(hours_left=None)
    entity._refresh_due()

    assert (
        await entity.async_get_events(
            hass, dt_util.utcnow(), dt_util.utcnow() + timedelta(days=1)
        )
        == []
    )


def test_anchor_is_persisted():
    store = MagicMock()
    entity = _entity(hours_left=100.0, store=store)

    entity._refresh_due()

    store.async_delay_save.assert_called_once()
    payload = store.async_delay_save.call_args.args[0]()
    assert payload["anchor_hours"] == 100.0
    assert payload["due_at"] == pytest.approx(entity._due_at.timestamp())


def test_persist_failure_does_not_raise():
    store = MagicMock()
    store.async_delay_save.side_effect = RuntimeError("boom")
    entity = _entity(hours_left=100.0, store=store)

    entity._refresh_due()

    assert entity._due_at is not None


@pytest.mark.asyncio
async def test_restores_anchor_from_store():
    anchor = dt_util.utcnow() + timedelta(hours=42)
    store = MagicMock()
    store.async_load = AsyncMock(
        return_value={"due_at": anchor.timestamp(), "anchor_hours": 80.0}
    )
    entity = _entity(hours_left=42.0, store=store)

    await entity._async_restore_anchor()
    entity._refresh_due()

    assert entity._due_at == anchor
    assert entity._anchor_hours == 80.0
    assert entity.event is not None


@pytest.mark.asyncio
async def test_malformed_store_data_is_ignored():
    store = MagicMock()
    store.async_load = AsyncMock(
        return_value={"due_at": "yesterday", "anchor_hours": None}
    )
    entity = _entity(hours_left=10.0, store=store)

    await entity._async_restore_anchor()

    assert entity._due_at is None
    entity._refresh_due()
    assert entity.event is not None


@pytest.mark.asyncio
async def test_non_dict_store_data_is_ignored():
    store = MagicMock()
    store.async_load = AsyncMock(return_value=["not", "a", "dict"])
    entity = _entity(hours_left=10.0, store=store)

    await entity._async_restore_anchor()

    assert entity._due_at is None


@pytest.mark.asyncio
async def test_store_load_failure_is_tolerated():
    store = MagicMock()
    store.async_load = AsyncMock(side_effect=RuntimeError("boom"))
    entity = _entity(hours_left=10.0, store=store)

    await entity._async_restore_anchor()

    assert entity._due_at is None


def test_anchor_without_store_is_kept_in_memory():
    entity = _entity(hours_left=20.0, store=None)

    entity._refresh_due()

    assert entity.event is not None


@pytest.mark.asyncio
async def test_setup_entry_modbus_creates_calendar():
    entry = _config_entry(modbus=True)
    async_add_entities = MagicMock()

    with patch(
        "custom_components.qvantum.entity.cleanup_disabled_entities"
    ) as cleanup:
        await async_setup_entry(MagicMock(), entry, async_add_entities)

    entities = async_add_entities.call_args.args[0]
    assert len(entities) == 1
    assert isinstance(entities[0], QvantumFilterCalendarEntity)
    assert entities[0].unique_id == "qvantum_ventilation_filter_due_dev-1"
    assert cleanup.call_args.args[2] == {FILTER_DUE_METRIC}
    assert cleanup.call_args.args[3] == "calendar"


@pytest.mark.asyncio
async def test_setup_entry_cloud_creates_nothing():
    entry = _config_entry(modbus=False)
    async_add_entities = MagicMock()

    with patch(
        "custom_components.qvantum.entity.cleanup_disabled_entities"
    ) as cleanup:
        await async_setup_entry(MagicMock(), entry, async_add_entities)

    async_add_entities.assert_not_called()
    assert cleanup.call_args.args[2] == set()
    assert cleanup.call_args.args[3] == "calendar"


@pytest.mark.asyncio
async def test_setup_entry_modbus_creates_store():
    hass = MagicMock()
    entry = _config_entry(modbus=True)
    captured: list[QvantumFilterCalendarEntity] = []

    def capture(entities):
        captured.extend(entities)

    with patch("custom_components.qvantum.entity.cleanup_disabled_entities"):
        await async_setup_entry(hass, entry, capture)

    assert captured
    store = captured[0]._store
    assert store is not None
    assert store.key == "qvantum.filter_calendar.entry-1"