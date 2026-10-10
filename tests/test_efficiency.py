"""Tests for pure efficiency math and the space-heating COP calculation."""

from __future__ import annotations

import math
from collections import deque
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from custom_components.qvantum.calculations import QvantumCalculationsMixin
from custom_components.qvantum.const import HP_STATUS_HEATING, HP_STATUS_HOT_WATER
from custom_components.qvantum.efficiency import (
    COP_WINDOW_SECONDS,
    aux_heat_share,
    cop_ratio,
    energy_delta,
    scop_from_counters,
)


class _Calculator(QvantumCalculationsMixin):
    """Minimal host for the mixin method under test."""

    def __init__(self) -> None:
        self._last_cop_energies: dict[str, float] | None = None
        self._cop_history: deque[
            tuple[datetime, dict[str, float], int | None, float | None]
        ] = deque()
        self._cop_last: dict[str, float | None] = {}
        self._cop_last_time: datetime | None = None


def _values(**overrides):
    base = {
        "heatingenergy": 100.0,
        "dhwenergy": 50.0,
        "compressorenergy": 40.0,
        "additionalenergy": 10.0,
        "hp_status": HP_STATUS_HEATING,
        "bt30": 50.0,
    }
    base.update(overrides)
    return base


class TestEnergyDelta:
    def test_positive_delta(self):
        assert energy_delta(10.0, 12.5) == 2.5

    def test_counter_reset_is_unknown(self):
        assert energy_delta(10.0, 1.0) is None

    def test_rounding_noise_clamps_to_zero(self):
        assert energy_delta(10.0, 10.0 - 1e-9) == 0.0

    def test_missing_and_invalid_input(self):
        assert energy_delta(None, 1.0) is None
        assert energy_delta(1.0, None) is None
        assert energy_delta(True, 1.0) is None
        assert energy_delta("x", 1.0) is None
        assert energy_delta(0.0, math.nan) is None
        assert energy_delta(0.0, math.inf) is None


class TestCopRatio:
    def test_ratio(self):
        assert cop_ratio(5.0, 1.0) == 5.0

    def test_zero_or_negative_electrical_is_not_a_measurement(self):
        assert cop_ratio(5.0, 0.0) is None
        assert cop_ratio(5.0, -1.0) is None

    def test_zero_thermal_is_none(self):
        assert cop_ratio(0.0, 1.0) is None

    def test_invalid_input(self):
        assert cop_ratio(None, 1.0) is None
        assert cop_ratio(1.0, "x") is None
        assert cop_ratio(1.0, True) is None
        assert cop_ratio(math.nan, 1.0) is None


class TestAuxHeatShare:
    def test_share(self):
        assert aux_heat_share(40.0, 60.0) == 0.4

    def test_high_share_stays_a_fraction(self):
        assert aux_heat_share(120.0, 30.0) == 0.8

    def test_negative_input_clamps_to_zero(self):
        assert aux_heat_share(-0.5, 2.0) == 0.0

    def test_no_electrical_input_is_none(self):
        assert aux_heat_share(0.0, 0.0) is None
        assert aux_heat_share(None, 1.0) is None


class TestScopFromCounters:
    def test_system_scop(self):
        assert scop_from_counters(200.0, 100.0, 60.0, 40.0) == 3.0

    def test_missing_counter_is_none(self):
        assert scop_from_counters(None, 100.0, 60.0, 40.0) is None
        assert scop_from_counters(200.0, 100.0, 60.0, None) is None

    def test_no_thermal_output_is_none(self):
        assert scop_from_counters(0.0, 0.0, 60.0, 40.0) is None


class TestCalculateCop:
    def test_first_poll_only_seeds_the_baseline(self):
        calculator = _Calculator()
        values = _values()

        calculator._calculate_cop(values)

        assert values["cop_heating"] is None
        assert values["cop_dhw"] is None
        assert calculator._last_cop_energies == {
            "heatingenergy": 100.0,
            "dhwenergy": 50.0,
            "compressorenergy": 40.0,
            "additionalenergy": 10.0,
        }
        assert len(calculator._cop_history) == 1

    def test_first_poll_publishes_a_restored_snapshot(self):
        """A snapshot restored after a restart is shown immediately."""
        calculator = _Calculator()
        calculator._cop_last = {"cop_heating": 3.2, "cop_dhw": 1.4}
        values = _values()

        calculator._calculate_cop(values)

        assert values["cop_heating"] == 3.2
        assert values["cop_dhw"] == 1.4

    def test_quantised_single_step_does_not_publish(self):
        """One 0.1 kWh step is quantisation, not a measurement."""
        calculator = _Calculator()
        calculator._calculate_cop(_values())
        values = _values(heatingenergy=100.1, compressorenergy=40.1)

        calculator._calculate_cop(values)

        assert values["cop_heating"] is None

    def test_window_accumulates_until_electrical_is_measurable(self):
        calculator = _Calculator()
        calculator._calculate_cop(_values())
        first = _values(heatingenergy=100.1, compressorenergy=40.1)
        calculator._calculate_cop(first)
        assert first["cop_heating"] is None

        values = _values(heatingenergy=100.6, compressorenergy=40.2)
        calculator._calculate_cop(values)

        # Two compressor steps (0.2 kWh) against 0.6 kWh of heating energy.
        assert values["cop_heating"] == pytest.approx(3.0)

    def test_heating_window_publishes_heating_cop(self):
        calculator = _Calculator()
        calculator._calculate_cop(_values())
        values = _values(heatingenergy=105.0, compressorenergy=41.0)

        calculator._calculate_cop(values)

        assert values["cop_heating"] == 5.0
        assert calculator._cop_last_time is not None

    def test_no_short_window_system_cop(self):
        """A short window cannot publish a system COP from the draw meter."""
        calculator = _Calculator()
        calculator._calculate_cop(_values())
        values = _values(heatingenergy=105.0, compressorenergy=41.0)

        calculator._calculate_cop(values)

        assert values["cop_dhw"] is None
        assert "cop_system" not in values

    def test_dhw_cop_recovers_production_from_tank_balance(self):
        calculator = _Calculator()
        calculator._calculate_cop(_values())
        # A draw discharges the tank; the drawn energy leaves with no DHW
        # electrical input in that interval.
        draw = _values(dhwenergy=52.0, bt30=40.0)
        calculator._calculate_cop(draw)
        assert draw["cop_dhw"] is None
        # The recharge returns the tank to its starting temperature using
        # 0.5 kWh of DHW-mode electrical.
        charge = _values(
            dhwenergy=52.0,
            bt30=50.0,
            compressorenergy=40.5,
            hp_status=HP_STATUS_HOT_WATER,
        )
        calculator._calculate_cop(charge)
        # production = 2 kWh drawn + 0 kWh tank change -> 2 / 0.5.
        assert charge["cop_dhw"] == pytest.approx(4.0)

        # A quiet poll keeps the last value instead of dropping to unavailable.
        hold = _values(dhwenergy=52.0, bt30=50.0, compressorenergy=40.5)
        calculator._calculate_cop(hold)
        assert hold["cop_dhw"] == pytest.approx(4.0)

    def test_dhw_cop_requires_the_tank_temperature(self):
        calculator = _Calculator()
        calculator._calculate_cop(_values())
        charge = _values(
            bt30=None,
            compressorenergy=40.5,
            hp_status=HP_STATUS_HOT_WATER,
        )

        calculator._calculate_cop(charge)

        assert charge["cop_dhw"] is None

    def test_dhw_charging_does_not_lower_heating_cop(self):
        """Electrical spent charging the DHW tank is not heating input."""
        calculator = _Calculator()
        calculator._calculate_cop(_values())
        # A DHW charge: electrical advances, but no space-heating output.
        charging = _values(
            heatingenergy=100.0,
            compressorenergy=40.3,
            hp_status=HP_STATUS_HOT_WATER,
        )
        calculator._calculate_cop(charging)
        assert charging["cop_heating"] is None

        values = _values(heatingenergy=100.6, compressorenergy=40.5)
        calculator._calculate_cop(values)

        # Only the heating interval counts: 0.6 / 0.2, not 0.6 / 0.5.
        assert values["cop_heating"] == pytest.approx(3.0)

    def test_idle_window_does_not_publish(self):
        calculator = _Calculator()
        calculator._calculate_cop(_values())
        values = _values(heatingenergy=102.0, compressorenergy=41.0)
        values["hp_status"] = 0

        calculator._calculate_cop(values)

        assert values["cop_heating"] is None

    def test_holds_heating_cop_across_mode_changes(self):
        """A DHW charge must not blank the space-heating figure."""
        calculator = _Calculator()
        calculator._calculate_cop(_values())
        calculator._calculate_cop(_values(heatingenergy=105.0, compressorenergy=41.0))
        values = _values(
            heatingenergy=105.0,
            compressorenergy=41.3,
            hp_status=HP_STATUS_HOT_WATER,
        )

        calculator._calculate_cop(values)

        assert values["cop_heating"] == 5.0

    def test_holds_last_value_after_the_window_goes_idle(self):
        calculator = _Calculator()
        t0 = datetime(2026, 10, 10, 0, 0, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(seconds=10)
        t_idle = t0 + timedelta(seconds=COP_WINDOW_SECONDS + 20)

        with patch(
            "custom_components.qvantum.calculations.dt_util.utcnow",
            return_value=t0,
        ):
            calculator._calculate_cop(_values())
        with patch(
            "custom_components.qvantum.calculations.dt_util.utcnow",
            return_value=t1,
        ):
            calculator._calculate_cop(
                _values(heatingenergy=105.0, compressorenergy=41.0)
            )

        # The only activity has aged out of the window; the last value is held
        # instead of the sensor dropping to unavailable.
        values = _values(heatingenergy=105.0, compressorenergy=41.0)
        with patch(
            "custom_components.qvantum.calculations.dt_util.utcnow",
            return_value=t_idle,
        ):
            calculator._calculate_cop(values)

        assert values["cop_heating"] == 5.0

    def test_window_drops_samples_older_than_the_window(self):
        calculator = _Calculator()
        t0 = datetime(2026, 10, 10, 0, 0, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(seconds=100)
        t2 = t0 + timedelta(seconds=COP_WINDOW_SECONDS + 100)

        with patch(
            "custom_components.qvantum.calculations.dt_util.utcnow",
            return_value=t0,
        ):
            calculator._calculate_cop(_values())
        with patch(
            "custom_components.qvantum.calculations.dt_util.utcnow",
            return_value=t1,
        ):
            calculator._calculate_cop(
                _values(heatingenergy=100.1, compressorenergy=40.0)
            )

        values = _values(heatingenergy=101.1, compressorenergy=40.3)
        with patch(
            "custom_components.qvantum.calculations.dt_util.utcnow",
            return_value=t2,
        ):
            calculator._calculate_cop(values)

        # Baseline is the t1 sample (+1.0 kWh heating, +0.3 kWh compressor),
        # not the t0 seed (+1.1 / +0.3).
        assert values["cop_heating"] == pytest.approx(10 / 3)

    def test_keeps_the_previous_sample_when_polls_exceed_the_window(self):
        """A poll interval longer than the window still yields a ratio."""
        calculator = _Calculator()
        t0 = datetime(2026, 10, 10, 0, 0, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(seconds=COP_WINDOW_SECONDS * 2)

        with patch(
            "custom_components.qvantum.calculations.dt_util.utcnow",
            return_value=t0,
        ):
            calculator._calculate_cop(_values())
        values = _values(heatingenergy=105.0, compressorenergy=41.0)
        with patch(
            "custom_components.qvantum.calculations.dt_util.utcnow",
            return_value=t1,
        ):
            calculator._calculate_cop(values)

        assert values["cop_heating"] == 5.0

    def test_counter_reset_clears_published_values(self):
        calculator = _Calculator()
        calculator._calculate_cop(_values())
        calculator._calculate_cop(_values(heatingenergy=105.0, compressorenergy=41.0))
        values = _values(heatingenergy=1.0, compressorenergy=1.0)

        calculator._calculate_cop(values)

        assert values["cop_heating"] is None
        assert calculator._cop_last == {}
        assert calculator._cop_last_time is None

    def test_missing_counter_skips_without_advancing_state(self):
        calculator = _Calculator()
        calculator._calculate_cop(_values())
        baseline = dict(calculator._last_cop_energies)
        values = _values()
        del values["dhwenergy"]

        calculator._calculate_cop(values)

        assert "cop_heating" not in values
        assert calculator._last_cop_energies == baseline
