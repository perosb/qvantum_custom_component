"""Tests for the rolling efficiency coordinator (SCOP / auxiliary share)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.qvantum import efficiency_coordinator as ec
from custom_components.qvantum.efficiency_coordinator import (
    MIN_COVERAGE_DAYS,
    QvantumEfficiencyCoordinator,
    _cumulative_series,
    _window_delta,
)


def make_coordinator() -> QvantumEfficiencyCoordinator:
    with patch.object(
        QvantumEfficiencyCoordinator, "__init__", lambda self, *args, **kwargs: None
    ):
        coordinator = QvantumEfficiencyCoordinator.__new__(QvantumEfficiencyCoordinator)
    main = MagicMock()
    main.device_id = "test_device_123"
    coordinator._main = main
    coordinator.hass = MagicMock()
    coordinator.data = None
    coordinator._listeners = {}
    return coordinator


def _hourly_rows(
    delta_kwh: float, *, hours: int = 240, end_ts: int | None = None
) -> list[dict]:
    """``hours`` hourly cumulative points ending now, rising by ``delta_kwh``."""
    end_ts = end_ts or int(datetime.now(timezone.utc).timestamp())
    step = delta_kwh / (hours - 1)
    return [
        {"start": end_ts - (hours - 1 - index) * 3600, "sum": step * index}
        for index in range(hours)
    ]


class TestCumulativeSeries:
    def test_filters_bad_rows_and_sorts(self):
        rows = [
            {"start": 200, "sum": 3.0},
            {"start": 100, "sum": 1.0},
            {"start": 300, "sum": None},
            {"start": None, "sum": 4.0},
            {"start": 400, "sum": True},
            "not a dict",
        ]
        assert _cumulative_series(rows) == [(100, 1.0), (200, 3.0)]

    def test_empty(self):
        assert _cumulative_series([]) == []


class TestWindowDelta:
    def test_delta_and_coverage(self):
        rows = [(index * 3600, float(index)) for index in range(240)]
        now_ts = 239 * 3600
        delta, coverage = _window_delta(rows, now_ts, 30)
        assert delta == 239.0
        assert coverage == pytest.approx(10.0)

    def test_window_excludes_old_rows(self):
        now_ts = 100 * 86400
        rows = [(0, 1.0), (now_ts - 100, 50.0), (now_ts - 10, 60.0)]
        delta, coverage = _window_delta(rows, now_ts, 30)
        assert delta == 10.0
        assert coverage == pytest.approx(2 / 24)

    def test_less_than_two_points_is_none(self):
        assert _window_delta([], 1000, 30) == (None, 0.0)
        assert _window_delta([(0, 1.0)], 1000, 30)[0] is None

    def test_negative_delta_is_none(self):
        now_ts = 10 * 3600
        rows = [(0, 5.0), (now_ts, 1.0)]
        delta, _ = _window_delta(rows, now_ts, 30)
        assert delta is None


class TestComputeSnapshot:
    async def test_no_resolved_entities_yields_empty_snapshot(self):
        coordinator = make_coordinator()
        with patch.object(ec, "resolve_statistic_entity_ids", return_value={}):
            snapshot = await coordinator._async_compute_snapshot()

        assert snapshot.scop_total is None
        assert snapshot.scop_total_90d is None
        assert snapshot.aux_heat_share is None
        assert snapshot.coverage_days == 0.0
        assert snapshot.updated_at is not None

    async def test_computes_scop_and_aux_share(self):
        coordinator = make_coordinator()
        rows = {
            "sensor.heatingenergy": _hourly_rows(200.0),
            "sensor.dhwenergy": _hourly_rows(100.0),
            "sensor.compressorenergy": _hourly_rows(60.0),
            "sensor.additionalenergy": _hourly_rows(40.0),
        }
        with (
            patch.object(
                ec,
                "resolve_statistic_entity_ids",
                return_value={
                    "heatingenergy": "sensor.heatingenergy",
                    "dhwenergy": "sensor.dhwenergy",
                    "compressorenergy": "sensor.compressorenergy",
                    "additionalenergy": "sensor.additionalenergy",
                },
            ),
            patch.object(
                ec, "async_statistics_during_period", AsyncMock(return_value=rows)
            ) as stats,
        ):
            snapshot = await coordinator._async_compute_snapshot()

        assert snapshot.scop_total == pytest.approx(3.0)
        assert snapshot.scop_total_90d == pytest.approx(3.0)
        assert snapshot.aux_heat_share == pytest.approx(0.4)
        assert snapshot.coverage_days == pytest.approx(10.0)
        assert stats.await_args.kwargs["types"] == {"sum"}

    async def test_short_history_yields_no_values(self):
        coordinator = make_coordinator()
        rows = {
            "sensor.heatingenergy": _hourly_rows(20.0, hours=24),
            "sensor.dhwenergy": _hourly_rows(10.0, hours=24),
            "sensor.compressorenergy": _hourly_rows(6.0, hours=24),
            "sensor.additionalenergy": _hourly_rows(4.0, hours=24),
        }
        with (
            patch.object(
                ec,
                "resolve_statistic_entity_ids",
                return_value={
                    "heatingenergy": "sensor.heatingenergy",
                    "dhwenergy": "sensor.dhwenergy",
                    "compressorenergy": "sensor.compressorenergy",
                    "additionalenergy": "sensor.additionalenergy",
                },
            ),
            patch.object(
                ec, "async_statistics_during_period", AsyncMock(return_value=rows)
            ),
        ):
            snapshot = await coordinator._async_compute_snapshot()

        assert snapshot.coverage_days < MIN_COVERAGE_DAYS
        assert snapshot.scop_total is None
        assert snapshot.scop_total_90d is None
        assert snapshot.aux_heat_share is None

    async def test_window_is_90_days_wide(self):
        coordinator = make_coordinator()
        with (
            patch.object(ec, "resolve_statistic_entity_ids", return_value={}) as resolve,
            patch.object(
                ec, "async_statistics_during_period", AsyncMock(return_value={})
            ),
        ):
            await coordinator._async_compute_snapshot()

        assert resolve.call_args.args[2] == ec.COUNTER_KEYS

    def test_device_id_property(self):
        coordinator = make_coordinator()
        assert coordinator.device_id == "test_device_123"


class TestUpdateData:
    async def test_returns_last_good_snapshot_on_error(self):
        coordinator = make_coordinator()
        coordinator.data = ec.EfficiencySnapshot(scop_total=2.5, coverage_days=30.0)
        with patch.object(
            coordinator, "_async_compute_snapshot", AsyncMock(side_effect=OSError("db"))
        ):
            snapshot = await coordinator._async_update_data()

        assert snapshot is coordinator.data
        assert snapshot.scop_total == 2.5

    async def test_returns_empty_snapshot_without_previous_data(self):
        coordinator = make_coordinator()
        coordinator.data = None
        with patch.object(
            coordinator, "_async_compute_snapshot", AsyncMock(side_effect=OSError("db"))
        ):
            snapshot = await coordinator._async_update_data()

        assert snapshot == ec.EfficiencySnapshot()

    async def test_cancellation_propagates(self):
        coordinator = make_coordinator()
        with patch.object(
            coordinator,
            "_async_compute_snapshot",
            AsyncMock(side_effect=asyncio.CancelledError()),
        ):
            with pytest.raises(asyncio.CancelledError):
                await coordinator._async_update_data()
