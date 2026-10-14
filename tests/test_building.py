"""Tests for pure building physics: degree hours and heat loss."""

from __future__ import annotations

import math

import pytest

from custom_components.qvantum.building import (
    MAX_TIME_CONSTANT_H,
    MIN_FIT_ROWS,
    MIN_TIME_CONSTANT_H,
    degree_hours,
    fit_heat_loss,
    fit_time_constant,
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

def _decay_series(
    delta_ts, *, tau_h: float = 40.0, gain: float = 0.05, separator: bool = True
):
    """Four off-hours per episode, plus a heating hour that ends it."""
    indoor: dict[int, float] = {}
    outdoor: dict[int, float] = {}
    power: dict[int, float] = {}
    hour = 1_000_000
    for delta_t in delta_ts:
        temperature = 21.0
        for index in range(4):
            ts = hour + index * 3600
            indoor[ts] = temperature
            outdoor[ts] = temperature - delta_t
            power[ts] = 0.0
            temperature += gain - delta_t / tau_h
        if separator:
            ts = hour + 4 * 3600
            indoor[ts] = 21.0
            outdoor[ts] = 21.0 - delta_t
            power[ts] = 900.0
            hour = ts + 3600
        else:
            # No separator: the next episode starts right after this one with a
            # missing hour between them.
            hour = hour + 5 * 3600
    return indoor, outdoor, power


class TestFitTimeConstant:
    def test_recovers_decay_rate_and_standing_gain(self):
        fit = fit_time_constant(*_decay_series([10.0, 20.0, 30.0]))

        assert fit is not None
        assert fit.time_constant_h == pytest.approx(40.0)
        assert fit.gain_k_per_h == pytest.approx(0.05)
        assert fit.episodes == 3
        assert fit.delta_t_min_k == pytest.approx(10.0)
        assert fit.delta_t_max_k == pytest.approx(30.0)

    def test_gap_in_the_statistics_ends_an_episode(self):
        """A missing hour breaks the decay; it must not join two of them."""
        fit = fit_time_constant(*_decay_series([10.0, 20.0, 30.0], separator=False))

        assert fit is not None
        assert fit.episodes == 3

    def test_needs_episodes_and_a_spread(self):
        assert fit_time_constant(*_decay_series([20.0])) is None
        assert fit_time_constant(*_decay_series([20.0, 20.0, 20.0])) is None

    def test_short_episodes_are_skipped(self):
        indoor, outdoor, power = _decay_series([10.0, 20.0, 30.0])
        # Keep the first two hours of every episode: below MIN_EPISODE_HOURS.
        keep = set()
        for separator in (ts for ts in power if power[ts] > 0):
            start = separator - 4 * 3600
            keep.update((start, start + 3600))
        indoor = {ts: value for ts, value in indoor.items() if ts in keep}
        outdoor = {ts: value for ts, value in outdoor.items() if ts in keep}
        power = {ts: value for ts, value in power.items() if ts in keep}

        assert fit_time_constant(indoor, outdoor, power) is None

    def test_rejects_no_decay_and_implausible_rates(self):
        # A room that keeps rising says nothing about the envelope.
        rising = _decay_series([10.0, 20.0, 30.0], gain=5.0, tau_h=1e6)
        assert fit_time_constant(*rising) is None

        # 2 h and 1000 h are outside the plausible envelope range.
        assert fit_time_constant(*_decay_series([10.0, 20.0, 30.0], tau_h=2.0)) is None
        assert (
            fit_time_constant(*_decay_series([10.0, 20.0, 30.0], tau_h=1000.0)) is None
        )
        assert MIN_TIME_CONSTANT_H == 5.0
        assert MAX_TIME_CONSTANT_H == 500.0
