"""Tests for pure efficiency math and the instantaneous COP calculation."""

from __future__ import annotations

import math

from custom_components.qvantum.calculations import QvantumCalculationsMixin
from custom_components.qvantum.const import HP_STATUS_HEATING, HP_STATUS_HOT_WATER
from custom_components.qvantum.efficiency import (
    aux_heat_share,
    cop_ratio,
    energy_delta,
    scop_from_counters,
)


class _Calculator(QvantumCalculationsMixin):
    """Minimal host for the mixin method under test."""

    def __init__(self) -> None:
        self._last_cop_energies: dict[str, float] | None = None


def _values(**overrides):
    base = {
        "heatingenergy": 100.0,
        "dhwenergy": 50.0,
        "compressorenergy": 40.0,
        "additionalenergy": 10.0,
        "hp_status": HP_STATUS_HEATING,
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

        assert "cop_heating" not in values
        assert calculator._last_cop_energies == {
            "heatingenergy": 100.0,
            "dhwenergy": 50.0,
            "compressorenergy": 40.0,
            "additionalenergy": 10.0,
        }

    def test_heating_interval_publishes_system_and_heating(self):
        calculator = _Calculator()
        calculator._calculate_cop(_values())
        values = _values(heatingenergy=105.0, compressorenergy=41.0)

        calculator._calculate_cop(values)

        assert values["cop_heating"] == 5.0
        assert values["cop_dhw"] is None
        assert values["cop_system"] == 5.0

    def test_dhw_interval_publishes_system_and_dhw(self):
        calculator = _Calculator()
        calculator._calculate_cop(_values())
        values = _values(
            dhwenergy=53.0, compressorenergy=41.0, hp_status=HP_STATUS_HOT_WATER
        )

        calculator._calculate_cop(values)

        assert values["cop_heating"] is None
        assert values["cop_dhw"] == 3.0
        assert values["cop_system"] == 3.0

    def test_idle_interval_publishes_system_only(self):
        calculator = _Calculator()
        calculator._calculate_cop(_values())
        values = _values(heatingenergy=102.0, compressorenergy=41.0)
        values["hp_status"] = 0

        calculator._calculate_cop(values)

        assert values["cop_heating"] is None
        assert values["cop_dhw"] is None
        assert values["cop_system"] == 2.0

    def test_no_electrical_delta_clears_per_mode_values(self):
        calculator = _Calculator()
        calculator._calculate_cop(_values())
        values = _values(heatingenergy=102.0, compressorenergy=40.0)

        calculator._calculate_cop(values)

        assert values["cop_heating"] is None
        assert values["cop_dhw"] is None
        assert values["cop_system"] is None

    def test_counter_reset_clears_published_values(self):
        calculator = _Calculator()
        calculator._calculate_cop(_values())
        values = _values(heatingenergy=1.0, compressorenergy=1.0)

        calculator._calculate_cop(values)

        assert values["cop_heating"] is None
        assert values["cop_dhw"] is None
        assert values["cop_system"] is None

    def test_missing_counter_skips_without_advancing_state(self):
        calculator = _Calculator()
        calculator._calculate_cop(_values())
        baseline = dict(calculator._last_cop_energies)
        values = _values()
        del values["dhwenergy"]

        calculator._calculate_cop(values)

        assert "cop_heating" not in values
        assert calculator._last_cop_energies == baseline
