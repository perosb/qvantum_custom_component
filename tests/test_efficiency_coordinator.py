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
        assert stats.await_args.kwargs["types"] == {"sum", "mean"}

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

        assert [call.args[2] for call in resolve.call_args_list] == [
            ec.COUNTER_KEYS,
            ec.BUILDING_KEYS,
            ec.CYCLING_KEYS,
        ]

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


class TestBuildingMetrics:
    @staticmethod
    def _series(*, now_ts: int) -> dict[str, list[dict]]:
        """60 hourly days: old consumption rate first, new rate second."""
        hours = 60 * 24
        bt1: list[dict] = []
        bt2: list[dict] = []
        power: list[dict] = []
        heating: list[dict] = []
        energy = 0.0
        for index in range(hours):
            ts = now_ts - (hours - 1 - index) * 3600
            rate = 2.0 if index < hours // 2 else 4.0  # kWh/day
            energy += rate / 24.0
            bt1.append({"start": ts, "mean": 0.0})
            bt2.append({"start": ts, "mean": 21.0})
            power.append({"start": ts, "mean": 30.0 * 21.0})
            heating.append({"start": ts, "sum": energy})
        return {
            "sensor.heatingenergy": heating,
            "sensor.bt1": bt1,
            "sensor.bt2": bt2,
            "sensor.heatingpower": power,
        }

    @staticmethod
    def _patch_resolve():
        def fake_resolve(hass, device_id, keys):
            if tuple(keys) == ec.COUNTER_KEYS:
                return {"heatingenergy": "sensor.heatingenergy"}
            return {
                "bt1": "sensor.bt1",
                "bt2": "sensor.bt2",
                "heatingpower": "sensor.heatingpower",
            }

        return patch.object(ec, "resolve_statistic_entity_ids", fake_resolve)

    async def test_computes_building_metrics_and_rising_trend(self):
        coordinator = make_coordinator()
        now_ts = int(datetime.now(timezone.utc).timestamp())
        rows = self._series(now_ts=now_ts)
        with (
            self._patch_resolve(),
            patch.object(
                ec, "async_statistics_during_period", AsyncMock(return_value=rows)
            ),
        ):
            snapshot = await coordinator._async_compute_snapshot()

        assert snapshot.heat_loss_w_per_k == pytest.approx(30.0)
        assert snapshot.heating_degree_hours == pytest.approx(
            15.0 * 30 * 24, rel=0.01
        )
        assert snapshot.weather_normalized_heating == pytest.approx(
            4.0 * 30 / (15.0 * 30), rel=0.02
        )
        assert snapshot.normalized_rising is True
        assert snapshot.building_coverage_days == pytest.approx(30.0, rel=0.01)

    async def test_trend_is_none_without_previous_window(self):
        coordinator = make_coordinator()
        now_ts = int(datetime.now(timezone.utc).timestamp())
        cutoff = now_ts - 30 * 86400
        rows = {
            key: [row for row in value if row["start"] >= cutoff]
            for key, value in self._series(now_ts=now_ts).items()
        }
        with (
            self._patch_resolve(),
            patch.object(
                ec, "async_statistics_during_period", AsyncMock(return_value=rows)
            ),
        ):
            snapshot = await coordinator._async_compute_snapshot()

        assert snapshot.normalized_rising is None

    async def test_normalized_uses_aligned_hours(self):
        """Energy and degree hours must cover exactly the same hours."""
        coordinator = make_coordinator()
        now_ts = int(datetime.now(timezone.utc).timestamp())
        series = self._series(now_ts=now_ts)
        cutoff = now_ts - 10 * 86400
        rows = {
            "sensor.heatingenergy": series["sensor.heatingenergy"],
            "sensor.bt1": [
                row for row in series["sensor.bt1"] if row["start"] >= cutoff
            ],
            "sensor.bt2": series["sensor.bt2"],
            "sensor.heatingpower": series["sensor.heatingpower"],
        }
        with (
            self._patch_resolve(),
            patch.object(
                ec, "async_statistics_during_period", AsyncMock(return_value=rows)
            ),
        ):
            snapshot = await coordinator._async_compute_snapshot()

        # 10 aligned days at 4 kWh/day and 15 K -> 4/15 kWh/HDD, not the
        # cross-period 30-day energy over 10-day degree hours.
        assert snapshot.building_coverage_days == pytest.approx(10.0, abs=0.2)
        assert snapshot.weather_normalized_heating == pytest.approx(
            4.0 / 15.0, rel=0.02
        )
        assert snapshot.normalized_rising is None

    async def test_short_building_history_publishes_nothing(self):
        coordinator = make_coordinator()
        now_ts = int(datetime.now(timezone.utc).timestamp())
        cutoff = now_ts - 2 * 86400
        rows = {
            key: [row for row in value if row["start"] >= cutoff]
            for key, value in self._series(now_ts=now_ts).items()
        }
        with (
            self._patch_resolve(),
            patch.object(
                ec, "async_statistics_during_period", AsyncMock(return_value=rows)
            ),
        ):
            snapshot = await coordinator._async_compute_snapshot()

        assert snapshot.heat_loss_w_per_k is None
        assert snapshot.heating_degree_hours is None
        assert snapshot.weather_normalized_heating is None
        assert snapshot.building_coverage_days == pytest.approx(2.0, abs=0.1)

    async def test_building_metrics_unavailable_without_series(self):
        coordinator = make_coordinator()
        with (
            self._patch_resolve(),
            patch.object(
                ec, "async_statistics_during_period", AsyncMock(return_value={})
            ),
        ):
            snapshot = await coordinator._async_compute_snapshot()

        assert snapshot.heat_loss_w_per_k is None
        assert snapshot.heating_degree_hours is None
        assert snapshot.weather_normalized_heating is None
        assert snapshot.normalized_rising is None
        assert snapshot.building_coverage_days == 0.0


class TestCyclingAndHealth:
    @staticmethod
    def _patch_resolve():
        def fake_resolve(hass, device_id, keys):
            if tuple(keys) in (ec.COUNTER_KEYS, ec.BUILDING_KEYS):
                return {}
            return {
                "compressor_starts": "sensor.starts",
                "compressor_run_time": "sensor.run",
            }

        return patch.object(ec, "resolve_statistic_entity_ids", fake_resolve)

    async def test_computes_cycling_and_health(self):
        coordinator = make_coordinator()
        now_ts = int(datetime.now(timezone.utc).timestamp())
        hours = 24
        rows = {
            "sensor.starts": [
                {"start": now_ts - (hours - 1 - index) * 3600, "sum": index * 0.5}
                for index in range(hours)
            ],
            "sensor.run": [
                {"start": now_ts - (hours - 1 - index) * 3600, "sum": index * 0.5}
                for index in range(hours)
            ],
        }
        with (
            self._patch_resolve(),
            patch.object(
                ec, "async_statistics_during_period", AsyncMock(return_value=rows)
            ),
        ):
            snapshot = await coordinator._async_compute_snapshot()

        assert snapshot.compressor_starts_per_hour == pytest.approx(1.0)
        assert snapshot.compressor_run_hours_24h == pytest.approx(11.5)
        # Only the cycling component is available: score 1.0, coverage 0.25.
        assert snapshot.efficiency_health_grade == "A"
        assert snapshot.efficiency_health_score == pytest.approx(1.0)
        assert snapshot.efficiency_health_coverage == pytest.approx(0.25)
        assert snapshot.efficiency_health_components == {"cycling": 1.0}

    async def test_cycling_unavailable_without_counters(self):
        coordinator = make_coordinator()
        with (
            self._patch_resolve(),
            patch.object(
                ec, "async_statistics_during_period", AsyncMock(return_value={})
            ),
        ):
            snapshot = await coordinator._async_compute_snapshot()

        assert snapshot.compressor_starts_per_hour is None
        assert snapshot.compressor_run_hours_24h is None
        assert snapshot.efficiency_health_grade is None
        assert snapshot.efficiency_health_coverage == 0.0


class TestDhwStandingLoss:
    @staticmethod
    def _values(coordinator, **values):
        coordinator._main.data = {"values": values}

    def test_accumulates_and_blends_on_activity(self):
        coordinator = make_coordinator()
        self._values(coordinator, bt30=55.0, bf1_l_min=0.0, hp_status=0)
        assert coordinator._update_dhw_standing_loss(1000.0) is None
        self._values(coordinator, bt30=53.0, bf1_l_min=0.0, hp_status=0)
        assert coordinator._update_dhw_standing_loss(1000.0 + 8 * 3600) is None
        self._values(coordinator, bt30=53.0, bf1_l_min=6.0, hp_status=0)
        value = coordinator._update_dhw_standing_loss(1000.0 + 9 * 3600)

        expected = 175.0 * 4.186 / 3600.0 * 2.0 / 8.0 * 24.0
        assert value == pytest.approx(expected)

    def test_short_window_is_discarded(self):
        coordinator = make_coordinator()
        self._values(coordinator, bt30=55.0, bf1_l_min=0.0, hp_status=0)
        coordinator._update_dhw_standing_loss(1000.0)
        self._values(coordinator, bt30=54.0, bf1_l_min=0.0, hp_status=0)
        coordinator._update_dhw_standing_loss(1000.0 + 2 * 3600)
        self._values(coordinator, bt30=54.0, bf1_l_min=6.0, hp_status=0)
        assert coordinator._update_dhw_standing_loss(1000.0 + 2 * 3600) is None

    def test_reheating_rise_closes_window_before_the_rise(self):
        coordinator = make_coordinator()
        self._values(coordinator, bt30=55.0, bf1_l_min=0.0, hp_status=0)
        coordinator._update_dhw_standing_loss(1000.0)
        self._values(coordinator, bt30=53.0, bf1_l_min=0.0, hp_status=0)
        coordinator._update_dhw_standing_loss(1000.0 + 8 * 3600)
        self._values(coordinator, bt30=54.0, bf1_l_min=0.0, hp_status=0)
        value = coordinator._update_dhw_standing_loss(1000.0 + 9 * 3600)

        expected = 175.0 * 4.186 / 3600.0 * 2.0 / 8.0 * 24.0
        assert value == pytest.approx(expected)

    def test_dhw_heating_is_not_idle(self):
        coordinator = make_coordinator()
        self._values(coordinator, bt30=55.0, bf1_l_min=0.0, hp_status=2)
        assert coordinator._update_dhw_standing_loss(1000.0) is None
        assert coordinator._dhw_idle_start is None

    def test_compressor_state_marks_dhw_active(self):
        coordinator = make_coordinator()
        self._values(coordinator, bt30=55.0, bf1_l_min=0.0, compressor_state=8)
        assert coordinator._update_dhw_standing_loss(1000.0) is None

    def test_flow_above_threshold_is_not_idle(self):
        coordinator = make_coordinator()
        self._values(coordinator, bt30=55.0, bf1_l_min=0.2, hp_status=0)
        assert coordinator._update_dhw_standing_loss(1000.0) is None

    def test_missing_flow_is_not_idle(self):
        """Without a measured flow a draw is indistinguishable from cooling."""
        coordinator = make_coordinator()
        self._values(coordinator, bt30=55.0, hp_status=0)
        assert coordinator._update_dhw_standing_loss(1000.0) is None
        assert coordinator._dhw_idle_start is None

    async def test_standing_loss_reaches_snapshot(self):
        coordinator = make_coordinator()
        self._values(coordinator, bt30=55.0, bf1_l_min=0.0, hp_status=0)
        coordinator._dhw_standing_loss = 1.23
        with patch.object(ec, "resolve_statistic_entity_ids", return_value={}):
            snapshot = await coordinator._async_compute_snapshot()

        assert snapshot.dhw_standing_loss == 1.23
