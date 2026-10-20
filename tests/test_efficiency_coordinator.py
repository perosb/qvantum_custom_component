"""Tests for the rolling efficiency coordinator (SCOP / auxiliary share)."""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.qvantum import efficiency_coordinator as ec
from custom_components.qvantum.efficiency_coordinator import (
    MIN_COVERAGE_DAYS,
    QvantumEfficiencyCoordinator,
    _cumulative_series,
    _window_delta,
)
from custom_components.qvantum.open_meteo import HourlyWeather, WeatherForecast
from custom_components.qvantum.solar_gain import SolarDailyModel, SolarModel


def make_coordinator() -> QvantumEfficiencyCoordinator:
    with patch.object(
        QvantumEfficiencyCoordinator, "__init__", lambda self, *args, **kwargs: None
    ):
        coordinator = QvantumEfficiencyCoordinator.__new__(QvantumEfficiencyCoordinator)
    main = MagicMock()
    main.device_id = "test_device_123"
    # A MagicMock answers float() with 1.0, which would become a 1 L tank.
    main.dhw_tank_liters = None
    coordinator._main = main
    coordinator.hass = MagicMock()
    coordinator.data = None
    coordinator._listeners = {}
    # Mirrors QvantumEfficiencyCoordinator.__init__ state that the tests
    # bypass by constructing via __new__.
    coordinator._session = MagicMock()
    coordinator._solar_model = None
    coordinator._solar_daily = None
    coordinator._last_solar_fit_ts = None
    coordinator._solar_forecast = None
    coordinator._solar_forecast_ts = None
    coordinator._dhw_idle_start = None
    coordinator._dhw_idle_last = None
    coordinator._dhw_standing_loss_per_k = None
    coordinator._dhw_standing_window = None
    coordinator._store = None
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

        # The DHW-meter comparison no longer resolves statistics: both sides
        # come from the poll-resolution window the main coordinator keeps.
        assert [call.args[2] for call in resolve.call_args_list] == [
            ec.COUNTER_KEYS,
            ec.BUILDING_KEYS,
            ec.CYCLING_KEYS,
            ec.OPERATING_KEYS,
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
        assert snapshot.compressor_duty_cycle == pytest.approx(11.5 / 24.0)
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


class TestOperatingPoint:
    @staticmethod
    def _patch_resolve():
        def fake_resolve(hass, device_id, keys):
            if tuple(keys) != ec.OPERATING_KEYS:
                return {}
            return {
                "compressormeasuredspeed": "sensor.speed",
                "compressor_power": "sensor.power",
                "fanrpm": "sensor.fan",
            }

        return patch.object(ec, "resolve_statistic_entity_ids", fake_resolve)

    async def test_computes_operating_point(self):
        coordinator = make_coordinator()
        now_ts = int(datetime.now(timezone.utc).timestamp())
        hours = 24

        def rows(value):
            return [
                {"start": now_ts - (hours - 1 - index) * 3600, "mean": value}
                for index in range(hours)
            ]

        rows_by_entity = {
            "sensor.speed": rows(3000.0),
            "sensor.power": rows(1200.0),
            "sensor.fan": rows(700.0),
        }
        with (
            self._patch_resolve(),
            patch.object(
                ec,
                "async_statistics_during_period",
                AsyncMock(return_value=rows_by_entity),
            ),
        ):
            snapshot = await coordinator._async_compute_snapshot()

        assert snapshot.compressor_speed_avg == pytest.approx(3000.0)
        assert snapshot.compressor_power_avg == pytest.approx(1200.0)
        assert snapshot.exhaust_fan_speed_avg == pytest.approx(700.0)
        # No cycling counters were resolved, so there is no duty cycle.
        assert snapshot.compressor_duty_cycle is None

    async def test_operating_point_unavailable_without_series(self):
        coordinator = make_coordinator()
        with (
            self._patch_resolve(),
            patch.object(
                ec, "async_statistics_during_period", AsyncMock(return_value={})
            ),
        ):
            snapshot = await coordinator._async_compute_snapshot()

        assert snapshot.compressor_speed_avg is None
        assert snapshot.compressor_power_avg is None
        assert snapshot.exhaust_fan_speed_avg is None
        assert snapshot.compressor_duty_cycle is None


class TestDhwHeatMeter:
    """The deviation reads the poll-resolution window the coordinator keeps.

    It is deliberately not recomputed from hourly statistics: ``mean(flow) ×
    mean(ΔT)`` is not the integral of ``flow · ΔT`` (see ``DhwMeterWindow``).
    """

    @staticmethod
    def _values(coordinator, **values):
        coordinator._main.data = {"values": values}

    async def test_matching_meter_and_estimate(self):
        coordinator = make_coordinator()
        self._values(
            coordinator,
            dhw_meter_estimated_kwh=16.744,
            dhw_meter_metered_kwh=16.8,
            dhw_meter_draw_hours=6.5,
        )
        with patch.object(ec, "resolve_statistic_entity_ids", return_value={}):
            snapshot = await coordinator._async_compute_snapshot()

        assert snapshot.dhw_heat_meter_draw_hours == pytest.approx(6.5)
        assert snapshot.dhw_heat_meter_estimated_kwh == pytest.approx(16.744)
        assert snapshot.dhw_heat_meter_metered_kwh == pytest.approx(16.8)
        assert snapshot.dhw_heat_meter_deviation == pytest.approx(-0.33, abs=0.05)
        assert snapshot.dhw_heat_meter_warning is False

    async def test_low_meter_reading_warns(self):
        coordinator = make_coordinator()
        self._values(
            coordinator,
            dhw_meter_estimated_kwh=16.0,
            dhw_meter_metered_kwh=8.0,
            dhw_meter_draw_hours=6.5,
        )
        with patch.object(ec, "resolve_statistic_entity_ids", return_value={}):
            snapshot = await coordinator._async_compute_snapshot()

        assert snapshot.dhw_heat_meter_deviation == pytest.approx(100.0)
        assert snapshot.dhw_heat_meter_warning is True

    async def test_below_minimum_draw_time_is_not_published(self):
        coordinator = make_coordinator()
        self._values(
            coordinator,
            dhw_meter_estimated_kwh=0.4,
            dhw_meter_metered_kwh=0.4,
            dhw_meter_draw_hours=0.2,
        )
        with patch.object(ec, "resolve_statistic_entity_ids", return_value={}):
            snapshot = await coordinator._async_compute_snapshot()

        assert snapshot.dhw_heat_meter_draw_hours == pytest.approx(0.2)
        assert snapshot.dhw_heat_meter_deviation is None
        assert snapshot.dhw_heat_meter_warning is None

    async def test_below_minimum_metered_energy_is_not_published(self):
        coordinator = make_coordinator()
        self._values(
            coordinator,
            dhw_meter_estimated_kwh=0.4,
            dhw_meter_metered_kwh=0.3,
            dhw_meter_draw_hours=9.0,
        )
        with patch.object(ec, "resolve_statistic_entity_ids", return_value={}):
            snapshot = await coordinator._async_compute_snapshot()

        assert snapshot.dhw_heat_meter_deviation is None
        assert snapshot.dhw_heat_meter_warning is None

    async def test_unavailable_without_the_window(self):
        coordinator = make_coordinator()
        with patch.object(ec, "resolve_statistic_entity_ids", return_value={}):
            snapshot = await coordinator._async_compute_snapshot()

        assert snapshot.dhw_heat_meter_deviation is None
        assert snapshot.dhw_heat_meter_warning is None
        assert snapshot.dhw_heat_meter_draw_hours == 0.0


class TestSolar:
    @staticmethod
    def _fixed_now():
        return datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

    async def test_ghi_now_and_forecast_peak(self):
        """GHI needs only Open-Meteo, so it works with no model and no stats."""
        coordinator = make_coordinator()
        coordinator.hass.config.latitude = 59.3
        coordinator.hass.config.longitude = 18.1
        fixed = self._fixed_now()
        now_hour = int(fixed.timestamp())
        forecast = WeatherForecast(
            points=(
                HourlyWeather(now_hour, 5.0, 100.0),
                HourlyWeather(now_hour + 3600, 5.0, 400.0),
            )
        )
        with (
            patch.object(ec.dt_util, "utcnow", return_value=fixed),
            patch.object(ec, "resolve_statistic_entity_ids", return_value={}),
            patch.object(ec, "fetch_forecast", AsyncMock(return_value=forecast)),
        ):
            snapshot = await coordinator._async_compute_snapshot()

        assert snapshot.solar_ghi_now is not None
        assert 0.0 < snapshot.solar_ghi_now < 400.0
        assert snapshot.solar_ghi_peak == 400.0
        assert snapshot.solar_ghi_peak_in_hours == pytest.approx(1.0)
        assert snapshot.solar_gain_now_w is None

    async def test_forecast_failure_degrades_to_none(self):
        coordinator = make_coordinator()
        coordinator.hass.config.latitude = 59.3
        coordinator.hass.config.longitude = 18.1
        with (
            patch.object(ec, "resolve_statistic_entity_ids", return_value={}),
            patch.object(
                ec,
                "fetch_forecast",
                AsyncMock(side_effect=ec.OpenMeteoError("down")),
            ),
        ):
            snapshot = await coordinator._async_compute_snapshot()

        assert snapshot.solar_ghi_now is None
        assert snapshot.solar_gain_now_w is None

    async def test_forecast_failure_keeps_last_good_payload(self):
        """A 503 after a good fetch must not blank the irradiance figure."""
        coordinator = make_coordinator()
        coordinator.hass.config.latitude = 59.3
        coordinator.hass.config.longitude = 18.1
        fixed = self._fixed_now()
        now_ts = int(fixed.timestamp())
        forecast = WeatherForecast(points=(HourlyWeather(now_ts, 5.0, 300.0),))
        fetch = AsyncMock(side_effect=[forecast, ec.OpenMeteoError("503")])
        later = fixed + timedelta(minutes=15)

        with (
            patch.object(ec.dt_util, "utcnow", return_value=fixed),
            patch.object(ec, "resolve_statistic_entity_ids", return_value={}),
            patch.object(ec, "fetch_forecast", fetch),
        ):
            first = await coordinator._async_compute_snapshot()

        with (
            patch.object(ec.dt_util, "utcnow", return_value=later),
            patch.object(ec, "resolve_statistic_entity_ids", return_value={}),
            patch.object(ec, "fetch_forecast", fetch),
        ):
            second = await coordinator._async_compute_snapshot()

        assert first.solar_ghi_now is not None
        assert second.solar_ghi_now == first.solar_ghi_now
        assert fetch.await_count == 2

    async def test_forecast_failure_drops_payload_past_reuse_window(self):
        """The cached payload must not outlive the hours it describes."""
        coordinator = make_coordinator()
        coordinator.hass.config.latitude = 59.3
        coordinator.hass.config.longitude = 18.1
        fixed = self._fixed_now()
        now_ts = int(fixed.timestamp())
        forecast = WeatherForecast(points=(HourlyWeather(now_ts, 5.0, 300.0),))
        fetch = AsyncMock(side_effect=[forecast, ec.OpenMeteoError("503")])
        later = fixed + timedelta(hours=4)

        with (
            patch.object(ec.dt_util, "utcnow", return_value=fixed),
            patch.object(ec, "resolve_statistic_entity_ids", return_value={}),
            patch.object(ec, "fetch_forecast", fetch),
        ):
            await coordinator._async_compute_snapshot()

        with (
            patch.object(ec.dt_util, "utcnow", return_value=later),
            patch.object(ec, "resolve_statistic_entity_ids", return_value={}),
            patch.object(ec, "fetch_forecast", fetch),
        ):
            stale = await coordinator._async_compute_snapshot()

        assert stale.solar_ghi_now is None

    async def test_solar_gain_uses_fitted_model(self):
        coordinator = make_coordinator()
        coordinator.hass.config.latitude = 59.3
        coordinator.hass.config.longitude = 18.1
        fixed = self._fixed_now()
        now_ts = int(fixed.timestamp())
        series = TestBuildingMetrics._series(now_ts=now_ts)
        rows = {
            "sensor.heatingenergy": series["sensor.heatingenergy"],
            "sensor.bt1": series["sensor.bt1"],
            "sensor.bt2": series["sensor.bt2"],
            "sensor.heatingpower": series["sensor.heatingpower"],
        }
        ghi_history = {row["start"]: 100.0 for row in series["sensor.bt1"]}
        model = SolarModel(
            a_w_per_k=100.0,
            b_m2=0.5,
            c_w=0.0,
            trust=1.0,
            r2_opaque=0.9,
            r2_solar=0.9,
            b_std_err=None,
            n_opaque=100,
            n_solar=100,
            valid=True,
        )

        def fake_resolve(hass, device_id, keys):
            if tuple(keys) == ec.BUILDING_KEYS:
                return {
                    "heatingpower": "sensor.heatingpower",
                    "bt1": "sensor.bt1",
                    "bt2": "sensor.bt2",
                }
            return {}

        with (
            patch.object(ec.dt_util, "utcnow", return_value=fixed),
            patch.object(ec, "resolve_statistic_entity_ids", fake_resolve),
            patch.object(
                ec, "async_statistics_during_period", AsyncMock(return_value=rows)
            ),
            patch.object(
                ec,
                "fetch_forecast",
                AsyncMock(
                    return_value=WeatherForecast(
                        points=(HourlyWeather(now_ts, 5.0, 300.0),)
                    )
                ),
            ),
            patch.object(ec, "fetch_ghi_history", AsyncMock(return_value=ghi_history)),
            patch.object(ec, "fit_solar_model", MagicMock(return_value=model)) as fit,
        ):
            snapshot = await coordinator._async_compute_snapshot()

        assert fit.called
        assert snapshot.solar_gain_now_w is not None
        assert snapshot.solar_gain_now_w > 0.0

    async def test_model_fit_is_throttled(self):
        coordinator = make_coordinator()
        coordinator._solar_model = SolarModel(
            a_w_per_k=1.0,
            b_m2=1.0,
            c_w=0.0,
            trust=1.0,
            r2_opaque=0.0,
            r2_solar=0.0,
            b_std_err=None,
            n_opaque=0,
            n_solar=0,
            valid=True,
        )
        coordinator._last_solar_fit_ts = 1_000_000.0
        with patch.object(ec, "fetch_ghi_history", AsyncMock()) as history:
            await coordinator._async_refresh_solar_model(
                {}, 1_000_000.0 + 60.0, 59.3, 18.1
            )

        history.assert_not_awaited()


class TestDhwStandingLoss:
    @staticmethod
    def _values(coordinator, **values):
        coordinator._main.data = {"values": values}

    def test_accumulates_and_blends_on_activity(self):
        coordinator = make_coordinator()
        self._values(coordinator, bt30=55.0, bf1_l_min=0.0, hp_status=0, bt2=15.0)
        assert coordinator._update_dhw_standing_loss(1000.0) is None
        self._values(coordinator, bt30=53.0, bf1_l_min=0.0, hp_status=0, bt2=15.0)
        assert coordinator._update_dhw_standing_loss(1000.0 + 8 * 3600) is None
        self._values(coordinator, bt30=53.0, bf1_l_min=6.0, hp_status=0, bt2=15.0)
        value = coordinator._update_dhw_standing_loss(1000.0 + 9 * 3600)

        # 175 L · 2 K over 8 h, quoted at the 40 K reference: the window itself
        # sat at 54 - 15 = 39 K, so the published figure is that much higher.
        daily = 175.0 * 4.186 / 3600.0 * 2.0 / 8.0 * 24.0
        assert value == pytest.approx(daily / 39.0 * 40.0)

    def test_identified_volume_replaces_the_datasheet_volume(self):
        """The coordinator exposes the identified volume as a method, not an
        attribute: reading the bound method would silently use 175 L."""
        coordinator = make_coordinator()
        coordinator._main.dhw_tank_liters = MagicMock(return_value=120.0)
        self._values(coordinator, bt30=55.0, bf1_l_min=0.0, hp_status=0, bt2=15.0)
        coordinator._update_dhw_standing_loss(1000.0)
        self._values(coordinator, bt30=53.0, bf1_l_min=0.0, hp_status=0, bt2=15.0)
        coordinator._update_dhw_standing_loss(1000.0 + 8 * 3600)
        self._values(coordinator, bt30=53.0, bf1_l_min=6.0, hp_status=0, bt2=15.0)
        value = coordinator._update_dhw_standing_loss(1000.0 + 9 * 3600)

        assert coordinator._dhw_standing_window["tank_liters"] == 120.0
        daily = 120.0 * 4.186 / 3600.0 * 2.0 / 8.0 * 24.0
        assert value == pytest.approx(daily / 39.0 * 40.0)

    def test_window_without_ambient_is_discarded(self):
        """A fall of a few kelvin cannot be turned into a coefficient blind."""
        coordinator = make_coordinator()
        self._values(coordinator, bt30=55.0, bf1_l_min=0.0, hp_status=0)
        coordinator._update_dhw_standing_loss(1000.0)
        self._values(coordinator, bt30=53.0, bf1_l_min=0.0, hp_status=0)
        coordinator._update_dhw_standing_loss(1000.0 + 8 * 3600)
        self._values(coordinator, bt30=53.0, bf1_l_min=6.0, hp_status=0)
        assert coordinator._update_dhw_standing_loss(1000.0 + 9 * 3600) is None
        assert coordinator._dhw_standing_window is None

    def test_ambient_falls_back_to_the_configured_room_sensor(self):
        coordinator = make_coordinator()
        self._values(
            coordinator, bt30=55.0, bf1_l_min=0.0, hp_status=0, room_temp_external=19.0
        )
        coordinator._update_dhw_standing_loss(1000.0)
        self._values(
            coordinator, bt30=53.0, bf1_l_min=0.0, hp_status=0, room_temp_external=19.0
        )
        coordinator._update_dhw_standing_loss(1000.0 + 8 * 3600)
        self._values(
            coordinator, bt30=53.0, bf1_l_min=6.0, hp_status=0, room_temp_external=19.0
        )
        value = coordinator._update_dhw_standing_loss(1000.0 + 9 * 3600)

        assert coordinator._dhw_standing_window["ambient_source"] == "room_temp_external"
        assert coordinator._dhw_standing_window["ambient_c"] == 19.0
        assert value is not None

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
        self._values(coordinator, bt30=55.0, bf1_l_min=0.0, hp_status=0, bt2=15.0)
        coordinator._update_dhw_standing_loss(1000.0)
        self._values(coordinator, bt30=53.0, bf1_l_min=0.0, hp_status=0, bt2=15.0)
        coordinator._update_dhw_standing_loss(1000.0 + 8 * 3600)
        self._values(coordinator, bt30=54.0, bf1_l_min=0.0, hp_status=0, bt2=15.0)
        value = coordinator._update_dhw_standing_loss(1000.0 + 9 * 3600)

        daily = 175.0 * 4.186 / 3600.0 * 2.0 / 8.0 * 24.0
        assert value == pytest.approx(daily / 39.0 * 40.0)

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
        coordinator._dhw_standing_loss_per_k = 0.06
        with patch.object(ec, "resolve_statistic_entity_ids", return_value={}):
            snapshot = await coordinator._async_compute_snapshot()

        assert snapshot.dhw_standing_loss == pytest.approx(2.4)
        assert snapshot.dhw_standing_loss_w_per_k == pytest.approx(2.5)

async def test_thermal_metrics_reports_the_decay_fit() -> None:
    """The decay fit drives the time constant and, with UA, the capacity."""
    coordinator = make_coordinator()
    hour = 1_000_000
    indoor: list[tuple[int, float]] = []
    outdoor: list[tuple[int, float]] = []
    power: list[tuple[int, float]] = []
    for delta_t in (10.0, 20.0, 30.0):
        temperature = 21.0
        for index in range(4):
            ts = hour + index * 3600
            indoor.append((ts, temperature))
            outdoor.append((ts, temperature - delta_t))
            power.append((ts, 0.0))
            temperature += 0.05 - delta_t / 40.0
        ts = hour + 4 * 3600
        indoor.append((ts, 21.0))
        outdoor.append((ts, 21.0 - delta_t))
        power.append((ts, 900.0))
        hour = ts + 3600
    mean_series = {"bt2": indoor, "bt1": outdoor, "heatingpower": power}

    tau, gain, mass, episodes = coordinator._thermal_metrics(mean_series, 106.8)

    assert tau == pytest.approx(40.0)
    assert gain == pytest.approx(0.05)
    assert mass == pytest.approx(106.8 * 40.0 / 1000.0)
    assert episodes == 3


def test_thermal_metrics_without_an_indoor_series() -> None:
    coordinator = make_coordinator()

    assert coordinator._thermal_metrics({}, 106.8) == (None, None, None, 0)


def test_thermal_metrics_without_a_loss_coefficient() -> None:
    """The time constant stands alone; only the capacity needs UA."""
    coordinator = make_coordinator()
    hour = 1_000_000
    indoor: list[tuple[int, float]] = []
    outdoor: list[tuple[int, float]] = []
    power: list[tuple[int, float]] = []
    for delta_t in (10.0, 20.0, 30.0):
        temperature = 21.0
        for index in range(4):
            ts = hour + index * 3600
            indoor.append((ts, temperature))
            outdoor.append((ts, temperature - delta_t))
            power.append((ts, 0.0))
            temperature += 0.05 - delta_t / 40.0
        ts = hour + 4 * 3600
        indoor.append((ts, 21.0))
        outdoor.append((ts, 21.0 - delta_t))
        power.append((ts, 900.0))
        hour = ts + 3600

    tau, _gain, mass, episodes = coordinator._thermal_metrics(
        {"bt2": indoor, "bt1": outdoor, "heatingpower": power}, None
    )

    assert tau == pytest.approx(40.0)
    assert mass is None
    assert episodes == 3


class TestSolarDaily:
    """The gain must follow the same identification policy as the curve."""

    @staticmethod
    def _daily_model() -> SolarDailyModel:
        return SolarDailyModel(
            a_w_per_k=152.0,
            b_m2=1.9,
            c_w=-600.0,
            trust=0.7,
            r2=0.92,
            n_days=25,
            b_std_err=0.6,
            t_stat=3.0,
            valid=True,
            notes="days=25",
        )

    def test_daily_model_drives_the_gain(self):
        coordinator = make_coordinator()
        coordinator._solar_daily = self._daily_model()
        coordinator._solar_model = None

        aperture, loss = ec.effective_solar_model(
            coordinator.solar_model, coordinator.solar_daily
        )

        assert aperture == pytest.approx(1.9 * 0.7)
        assert loss == pytest.approx(152.0)

    async def test_refresh_fits_the_day_block_model(self):
        """The day-block fit must actually see the series it is given."""
        coordinator = make_coordinator()
        fixed = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        now_ts = int(fixed.timestamp())
        days = 30
        mean_series: dict[str, list[tuple[int, float]]] = {
            "heatingpower": [],
            "bt1": [],
            "bt2": [],
        }
        ghi_history: dict[int, float] = {}
        for index in range(days * 24):
            ts = now_ts - (days * 24 - 1 - index) * 3600
            ts -= ts % 3600
            day = index // 24
            outdoor = 12.0 + 0.4 * (day % 5)
            hour = (ts // 3600) % 24
            ghi = (
                (300.0 + 90.0 * (day % 11)) * math.sin(math.pi * hour / 24.0)
                if 6 <= hour < 18
                else 0.0
            )
            ghi_history[ts] = ghi
            mean_series["bt1"].append((ts, outdoor))
            mean_series["bt2"].append((ts, 21.0))
            mean_series["heatingpower"].append(
                (ts, max(0.0, 152.0 * (21.0 - outdoor) - 600.0 - 1.9 * ghi))
            )

        with patch.object(
            ec, "fetch_ghi_history", AsyncMock(return_value=ghi_history)
        ):
            await coordinator._async_refresh_solar_model(
                mean_series, now_ts, 59.3, 18.1
            )

        daily = coordinator.solar_daily
        assert daily is not None
        assert daily.valid
        assert daily.n_days >= 20
        assert daily.b_m2 > 0.0
        assert daily.notes.startswith("days=")


class TestDhwStandingLossPersistence:
    """The EMA survives a restart; the open window deliberately does not."""

    class _Store:
        def __init__(self, data=None):
            self.data = data
            self.saved: list[dict] = []
            self.delayed: list[object] = []

        async def async_load(self):
            return self.data

        def async_delay_save(self, factory, delay):
            self.delayed.append(factory)
            self.saved.append(factory())

    @pytest.mark.asyncio
    async def test_restore_reads_the_stored_ema(self):
        coordinator = make_coordinator()
        coordinator._store = self._Store(
            {"dhw_standing_loss_per_k": 0.0625, "dhw_standing_window": {"hours": 6.0}}
        )
        coordinator._dhw_standing_loss_per_k = None

        await coordinator.async_restore()

        assert coordinator._dhw_standing_loss_per_k == pytest.approx(0.0625)
        assert coordinator._dhw_standing_window == {"hours": 6.0}

    @pytest.mark.asyncio
    async def test_restore_ignores_unusable_payloads(self):
        for payload in (
            None,
            {},
            {"dhw_standing_loss_per_k": "x"},
            {"dhw_standing_loss_per_k": -1.0},
            # A snapshot from before the normalization holds a daily figure at
            # an unknown tank temperature; it cannot be converted and is ignored.
            {"dhw_standing_loss": 5.11},
        ):
            coordinator = make_coordinator()
            coordinator._store = self._Store(payload)

            await coordinator.async_restore()

            assert coordinator._dhw_standing_loss_per_k is None
            # The open window is never restored: it would book unobserved draws.
            assert coordinator._dhw_idle_start is None

    @pytest.mark.asyncio
    async def test_restore_without_a_store_is_a_noop(self):
        coordinator = make_coordinator()
        await coordinator.async_restore()
        assert coordinator._dhw_standing_loss_per_k is None

    def test_window_close_persists_the_blended_value(self):
        coordinator = make_coordinator()
        store = self._Store()
        coordinator._store = store
        coordinator._main.data = {
            "values": {"bt30": 55.0, "bf1_l_min": 0.0, "hp_status": 0, "bt2": 15.0}
        }
        coordinator._update_dhw_standing_loss(1000.0)
        coordinator._main.data = {
            "values": {"bt30": 53.0, "bf1_l_min": 0.0, "hp_status": 0, "bt2": 15.0}
        }
        coordinator._update_dhw_standing_loss(1000.0 + 8 * 3600)
        coordinator._main.data = {
            "values": {"bt30": 53.0, "bf1_l_min": 6.0, "hp_status": 0, "bt2": 15.0}
        }
        value = coordinator._update_dhw_standing_loss(1000.0 + 9 * 3600)

        assert value is not None
        saved = store.saved[-1]
        assert saved["dhw_standing_loss_per_k"] == pytest.approx(value / 40.0)
        assert saved["dhw_standing_window"]["delta_k"] == pytest.approx(39.0)

    def test_discarded_window_does_not_persist(self):
        coordinator = make_coordinator()
        store = self._Store()
        coordinator._store = store
        coordinator._main.data = {
            "values": {"bt30": 55.0, "bf1_l_min": 0.0, "hp_status": 0}
        }
        coordinator._update_dhw_standing_loss(1000.0)
        coordinator._main.data = {
            "values": {"bt30": 54.0, "bf1_l_min": 6.0, "hp_status": 0}
        }
        coordinator._update_dhw_standing_loss(1000.0 + 2 * 3600)

        assert store.saved == []
