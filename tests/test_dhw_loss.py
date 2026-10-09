"""Tests for pure DHW standing-loss math."""

from __future__ import annotations

import math

import pytest

from custom_components.qvantum.dhw_loss import (
    MIN_DROP_K,
    MIN_WINDOW_HOURS,
    WATER_KWH_PER_LITER_K,
    blend,
    standing_loss_kwh_per_day,
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
