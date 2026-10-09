"""Tests for pure building physics: degree hours and heat loss."""

from __future__ import annotations

import math

import pytest

from custom_components.qvantum.building import (
    MIN_FIT_ROWS,
    degree_hours,
    fit_heat_loss,
    weather_normalized_heating,
)


class TestDegreeHours:
    def test_sums_positive_deficits_only(self):
        by_hour = {0: 10.0, 3600: 5.0, 7200: 15.0, 10800: 20.0}
        assert degree_hours(by_hour) == pytest.approx(5.0 + 10.0)

    def test_skips_missing_and_non_finite(self):
        by_hour = {
            0: 10.0,
            3600: None,
            7200: math.nan,
            10800: math.inf,
            14400: 12.0,
        }
        assert degree_hours(by_hour) == pytest.approx(5.0 + 3.0)

    def test_custom_base(self):
        assert degree_hours({0: 10.0}, base_c=20.0) == pytest.approx(10.0)

    def test_empty(self):
        assert degree_hours({}) == 0.0


class TestWeatherNormalizedHeating:
    def test_kwh_per_degree_day(self):
        # 48 K·h = 2 HDD; 20 kWh over those two days is 10 kWh/HDD.
        assert weather_normalized_heating(20.0, 48.0) == pytest.approx(10.0)

    def test_zero_or_missing_is_none(self):
        assert weather_normalized_heating(20.0, 0.0) is None
        assert weather_normalized_heating(0.0, 48.0) is None
        assert weather_normalized_heating(None, 48.0) is None
        assert weather_normalized_heating(20.0, None) is None

    def test_invalid_input(self):
        assert weather_normalized_heating(True, 48.0) is None
        assert weather_normalized_heating(20.0, "x") is None


class TestFitHeatLoss:
    def _hours(self, *, delta_t=20.0, heat=1000.0, count=24, start_ts=1_000_000):
        return [(start_ts + index * 3600, delta_t, heat) for index in range(count)]

    def test_recovers_constant_coefficient(self):
        # 1000 W at dT = 20 K → 50 W/K.
        coefficient = fit_heat_loss(self._hours(), now_ts=1_000_000 + 24 * 3600)
        assert coefficient == pytest.approx(50.0)

    def test_recovers_mixed_coefficient(self):
        now = 1_000_000 + 24 * 3600
        hours = [
            (now - index * 3600, 10.0 + index % 12, 40.0 * (10.0 + index % 12))
            for index in range(24)
        ]
        assert fit_heat_loss(hours, now_ts=now) == pytest.approx(40.0)

    def test_too_few_rows_is_none(self):
        assert (
            fit_heat_loss(self._hours(count=MIN_FIT_ROWS - 1), now_ts=1_000_000)
            is None
        )

    def test_low_spread_or_low_power_rows_are_filtered(self):
        assert fit_heat_loss(self._hours(delta_t=2.0), now_ts=1_000_000) is None
        assert fit_heat_loss(self._hours(heat=10.0), now_ts=1_000_000) is None

    def test_bad_rows_are_skipped(self):
        hours = self._hours(count=MIN_FIT_ROWS)
        hours.extend([None, (1, 2), (None, 20.0, 1000.0), ("x", 20.0, 1000.0)])
        assert fit_heat_loss(hours, now_ts=1_000_000) == pytest.approx(50.0)

    def test_recency_weights_old_rows_less(self):
        now = 1_000_000 + 400 * 86400
        hours = [(now, 10.0, 400.0), (now, 20.0, 800.0)]
        for index in range(MIN_FIT_ROWS):
            hours.append((1_000_000 - index * 86400, 10.0 + index, 200.0))
        assert fit_heat_loss(hours, now_ts=now) == pytest.approx(40.0, rel=0.05)
