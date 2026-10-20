"""Tests for pure DHW standing-loss math."""

from __future__ import annotations

import math

import pytest

from custom_components.qvantum.dhw_loss import (
    MIN_DROP_K,
    MIN_WINDOW_HOURS,
    REFERENCE_DELTA_K,
    WATER_KWH_PER_LITER_K,
    blend,
    mean_delta_k,
    reference_loss_kwh_per_day,
    standing_loss_kwh_per_day,
    standing_loss_per_k,
)


class TestStandingLoss:
    def test_extrapolates_daily_rate(self):
        expected = 175.0 * WATER_KWH_PER_LITER_K * 2.0 / 8.0 * 24.0
        assert standing_loss_kwh_per_day(2.0, 8.0, 175.0) == pytest.approx(expected)

    def test_too_short_window_is_none(self):
        assert (
            standing_loss_kwh_per_day(2.0, MIN_WINDOW_HOURS - 0.1, 175.0) is None
        )

    def test_too_small_drop_is_none(self):
        assert standing_loss_kwh_per_day(MIN_DROP_K - 0.01, 8.0, 175.0) is None

    def test_invalid_input(self):
        assert standing_loss_kwh_per_day(None, 8.0, 175.0) is None
        assert standing_loss_kwh_per_day(2.0, 8.0, 0.0) is None
        assert standing_loss_kwh_per_day(2.0, 8.0, "x") is None
        assert standing_loss_kwh_per_day(math.nan, 8.0, 175.0) is None


class TestBlend:
    def test_seeds_from_first_sample(self):
        assert blend(None, 1.2) == 1.2

    def test_ema(self):
        assert blend(1.0, 2.0, alpha=0.5) == 1.5

    def test_invalid_sample_keeps_previous(self):
        assert blend(1.0, None) == 1.0


class TestNormalizedLoss:
    def test_mean_delta_uses_the_window_midpoint(self):
        # 58 -> 54 K tank, 20 °C ambient: the mean tank is 56, so ΔT is 36.
        assert mean_delta_k(58.0, 54.0, 20.0) == pytest.approx(36.0)

    def test_mean_delta_requires_an_ambient(self):
        assert mean_delta_k(58.0, 54.0, None) is None

    def test_mean_delta_rejects_a_small_difference(self):
        """A tank 8 K above the room says nothing about its insulation."""
        assert mean_delta_k(28.0, 26.0, 20.0) is None

    def test_mean_delta_rejects_junk(self):
        assert mean_delta_k("x", 54.0, 20.0) is None
        assert mean_delta_k(58.0, math.nan, 20.0) is None

    def test_coefficient_divides_by_the_difference(self):
        daily = 175.0 * WATER_KWH_PER_LITER_K * 2.0 / 8.0 * 24.0
        assert standing_loss_per_k(2.0, 8.0, 175.0, 40.0) == pytest.approx(
            daily / 40.0
        )

    def test_coefficient_needs_a_usable_window(self):
        assert standing_loss_per_k(2.0, 8.0, 175.0, None) is None
        assert standing_loss_per_k(2.0, 8.0, 175.0, 5.0) is None
        # A window shorter than the minimum still yields nothing.
        assert standing_loss_per_k(2.0, 1.0, 175.0, 40.0) is None

    def test_reference_quotes_at_the_fixed_difference(self):
        assert REFERENCE_DELTA_K == 40.0
        assert reference_loss_kwh_per_day(0.05) == pytest.approx(2.0)

    def test_reference_rejects_unusable_coefficients(self):
        for value in (None, 0.0, -0.1, "x", math.inf):
            assert reference_loss_kwh_per_day(value) is None

    def test_a_hot_window_normalizes_to_the_same_coefficient(self):
        """The 69 °C extra-hot-water window must not inflate the figure.

        Same tank, same insulation: a window 10 K warmer falls 10/36 faster once
        divided out, so quoting both at the reference agrees.
        """
        cold_window = standing_loss_per_k(3.6, 8.0, 175.0, 36.0)
        hot_window = standing_loss_per_k(4.6, 8.0, 175.0, 46.0)
        assert cold_window == pytest.approx(hot_window)
        assert reference_loss_kwh_per_day(cold_window) == pytest.approx(
            reference_loss_kwh_per_day(hot_window)
        )
