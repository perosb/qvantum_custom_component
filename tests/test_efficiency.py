"""Tests for pure efficiency math and the space-heating COP calculation."""

from __future__ import annotations

import math
from collections import deque
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from custom_components.qvantum.calculations import (
    QvantumCalculationsMixin,
    _flag,
    _split_electrical_input,
)
from custom_components.qvantum.const import (
    DHW_CHARGE_MAX_SECONDS,
    DHW_TANK_VOLUME_L,
    HP_STATUS_DEFROSTING,
    HP_STATUS_HEATING,
    HP_STATUS_HOT_WATER,
    HP_STATUS_IDLE,
)
from custom_components.qvantum.efficiency import (
    COP_WINDOW_SECONDS,
    aux_heat_share,
    cap_dhw_ratio,
    cop_ratio,
    energy_delta,
    scop_from_counters,
)


class _Calculator(QvantumCalculationsMixin):
    """Minimal host for the mixin method under test."""

    def __init__(self) -> None:
        self._last_cop_energies: dict[str, float] | None = None
        self._cop_history: deque[
            tuple[
                datetime,
                dict[str, float],
                int | None,
                float | None,
                bool,
                bool,
                bool,
            ]
        ] = deque()
        self._cop_last: dict[str, float | None] = {}
        self._cop_unattributed_kwh = 0.0
        self._cop_last_times: dict[str, datetime] = {}
        self._dhw_charge = None
        self._dhw_tank_liters: float | None = None


def _values(**overrides):
    base = {
        "heatingenergy": 100.0,
        "dhwenergy": 50.0,
        "compressorenergy": 40.0,
        "additionalenergy": 10.0,
        "hp_status": HP_STATUS_HEATING,
        "bt30": 50.0,
        "dhwdemand": 0,
        "additiondemand": 0,
        "additiondhwdemand": 0,
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


class TestCapDhwRatio:
    """An element-only charge cannot exceed COP 1."""

    def test_element_only_ratio_is_capped(self):
        assert cap_dhw_ratio(6.1, 0.0) == 1.0

    def test_compressor_work_is_not_capped(self):
        assert cap_dhw_ratio(2.4, 0.5) == 2.4

    def test_missing_ratio_stays_none(self):
        assert cap_dhw_ratio(None, 0.0) is None

    def test_unknown_compressor_share_is_capped(self):
        assert cap_dhw_ratio(3.0, None) == 1.0


class TestFlag:
    def test_metric_values(self):
        assert _flag(1) is True
        assert _flag(True) is True
        assert _flag(0) is False
        assert _flag("on") is True
        assert _flag("off") is False
        assert _flag(None) is False
        assert _flag(2.5) is True


class TestSplitElectricalInput:
    """Attribution of one poll interval's electrical input."""

    def test_hot_water_books_everything_to_dhw(self):
        heating, dhw, dhw_compressor, unattributed = _split_electrical_input(
            hp_status=HP_STATUS_HOT_WATER,
            dhw_demand=True,
            aux_demand=False,
            compressor_kwh=0.3,
            aux_kwh=0.1,
        )
        assert (heating, dhw, dhw_compressor, unattributed) == (0.0, 0.4, 0.3, 0.0)

    def test_heating_without_dhw_demand_books_everything_to_heating(self):
        heating, dhw, dhw_compressor, _ = _split_electrical_input(
            hp_status=HP_STATUS_HEATING,
            dhw_demand=False,
            aux_demand=True,
            compressor_kwh=0.3,
            aux_kwh=0.1,
        )
        assert (heating, dhw, dhw_compressor) == (0.4, 0.0, 0.0)

    def test_auxiliary_heat_for_space_heating_stays_heating(self):
        heating, dhw, _, _ = _split_electrical_input(
            hp_status=HP_STATUS_HEATING,
            dhw_demand=True,
            aux_demand=True,
            compressor_kwh=0.3,
            aux_kwh=0.1,
        )
        assert (heating, dhw) == (0.4, 0.0)

    def test_parallel_top_up_splits_by_source(self):
        heating, dhw, dhw_compressor, _ = _split_electrical_input(
            hp_status=HP_STATUS_HEATING,
            dhw_demand=True,
            aux_demand=False,
            compressor_kwh=0.3,
            aux_kwh=0.1,
        )
        assert (heating, dhw, dhw_compressor) == (0.3, 0.1, 0.0)

    def test_stopped_compressor_books_only_the_element(self):
        heating, dhw, dhw_compressor, unattributed = _split_electrical_input(
            hp_status=HP_STATUS_IDLE,
            dhw_demand=True,
            aux_demand=False,
            compressor_kwh=0.0,
            aux_kwh=0.4,
        )
        assert (heating, dhw, dhw_compressor, unattributed) == (0.0, 0.4, 0.0, 0.0)

    def test_idle_without_dhw_demand_attributes_nothing(self):
        assert _split_electrical_input(
            hp_status=HP_STATUS_IDLE,
            dhw_demand=False,
            aux_demand=False,
            compressor_kwh=0.0,
            aux_kwh=0.0,
        ) == (0.0, 0.0, 0.0, 0.0)

    def test_element_dhw_flag_attributes_element_energy_when_idle(self):
        """The counters step after the demand has cleared; the flag still says.

        Measured 2026-10-21: in all 1 087 counter-step intervals of a 10-day
        window both ``dhw_demand`` and ``additiondemand`` had cleared, so the
        element's 3.7 kWh of DHW work was booked nowhere and the charge never
        accumulated the energy the volume identification needs.
        """
        heating, dhw, dhw_compressor, unattributed = _split_electrical_input(
            hp_status=HP_STATUS_IDLE,
            dhw_demand=False,
            aux_demand=False,
            aux_dhw_demand=True,
            compressor_kwh=0.0,
            aux_kwh=0.3,
        )

        assert (heating, dhw, dhw_compressor, unattributed) == (0.0, 0.3, 0.0, 0.0)

    def test_element_dhw_flag_splits_a_parallel_run(self):
        """Compressor to the house, element to the tank, without dhw_demand."""
        heating, dhw, _, unattributed = _split_electrical_input(
            hp_status=HP_STATUS_HEATING,
            dhw_demand=False,
            aux_demand=False,
            aux_dhw_demand=True,
            compressor_kwh=0.5,
            aux_kwh=0.2,
        )

        assert (heating, dhw, unattributed) == (0.5, 0.2, 0.0)

    def test_unflagged_element_energy_is_counted_not_dropped(self):
        """A kWh that vanishes is how a COP drifts: report it instead."""
        heating, dhw, _, unattributed = _split_electrical_input(
            hp_status=HP_STATUS_IDLE,
            dhw_demand=False,
            aux_demand=False,
            compressor_kwh=0.0,
            aux_kwh=0.3,
        )

        assert (heating, dhw) == (0.0, 0.0)
        assert unattributed == pytest.approx(0.3)


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
        assert calculator._cop_last_times["cop_heating"] is not None

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
        # The recharge returns the tank to 50 °C using 0.5 kWh of DHW-mode
        # electrical. Production is the stored energy against the 40 °C the
        # charge started from (10 K · 175 L · c = 2.03 kWh); the metered draw
        # happened before the charge and is already in that baseline.
        charge = _values(
            dhwenergy=52.0,
            bt30=50.0,
            compressorenergy=40.5,
            hp_status=HP_STATUS_HOT_WATER,
        )
        calculator._calculate_cop(charge)
        assert charge["cop_dhw"] == pytest.approx(4.07, rel=1e-3)

        # A quiet poll closes the charge and keeps the last value instead of
        # dropping to unavailable.
        hold = _values(dhwenergy=52.0, bt30=50.0, compressorenergy=40.5)
        calculator._calculate_cop(hold)
        assert hold["cop_dhw"] == pytest.approx(4.07, rel=1e-3)
        assert calculator._dhw_charge is None

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

    def test_aux_only_tank_charge_publishes_a_dhw_cop(self):
        """Compressor stopped, element charging the tank, pump reporting idle."""
        calculator = _Calculator()
        calculator._calculate_cop(_values())
        # 0.4 kWh from the element into a tank that gained 1 K (0.20 kWh).
        values = _values(
            hp_status=HP_STATUS_IDLE,
            dhwdemand=1,
            additionalenergy=10.4,
            bt30=51.0,
        )

        calculator._calculate_cop(values)

        # Production comes from the tank balance; the element alone is ~0.5.
        assert values["cop_dhw"] == pytest.approx(0.509, abs=1e-3)
        assert values["cop_heating"] is None

    def test_element_only_charge_is_capped_at_cop_one(self):
        """A resistive element cannot exceed COP 1, whatever the balance says."""
        calculator = _Calculator()
        calculator._calculate_cop(_values())
        values = _values(
            hp_status=HP_STATUS_IDLE,
            dhwdemand=1,
            additionalenergy=10.4,
            bt30=62.0,
        )

        calculator._calculate_cop(values)

        assert values["cop_dhw"] == 1.0

    def test_parallel_dhw_top_up_does_not_dilute_heating_cop(self):
        """The element charges the tank while the compressor heats the house."""
        calculator = _Calculator()
        calculator._calculate_cop(_values())
        values = _values(
            heatingenergy=100.6,
            compressorenergy=40.2,
            additionalenergy=10.4,
            dhwdemand=1,
            bt30=51.0,
        )

        calculator._calculate_cop(values)

        # 0.6 kWh of house heat against the compressor's 0.2 kWh, not 0.6 kWh.
        assert values["cop_heating"] == pytest.approx(3.0)
        assert values["cop_dhw"] == pytest.approx(0.509, abs=1e-3)

    def test_auxiliary_heat_flagged_for_heating_is_not_dhw(self):
        calculator = _Calculator()
        calculator._calculate_cop(_values())
        values = _values(
            heatingenergy=100.6,
            compressorenergy=40.1,
            additionalenergy=10.4,
            dhwdemand=1,
            additiondemand=1,
        )

        calculator._calculate_cop(values)

        assert values["cop_heating"] == pytest.approx(1.2)
        assert values["cop_dhw"] is None

    def test_missing_demand_flags_keep_the_hp_status_attribution(self):
        """Without the demand flags the old behaviour must be unchanged."""
        calculator = _Calculator()
        baseline = _values()
        baseline.pop("dhwdemand")
        baseline.pop("additiondemand")
        calculator._calculate_cop(baseline)
        values = _values(
            heatingenergy=100.6,
            compressorenergy=40.1,
            additionalenergy=10.4,
        )
        values.pop("dhwdemand")
        values.pop("additiondemand")

        calculator._calculate_cop(values)

        assert values["cop_heating"] == pytest.approx(1.2)

    def test_compressor_dhw_charge_is_not_capped(self):
        calculator = _Calculator()
        calculator._calculate_cop(_values())
        values = _values(
            hp_status=HP_STATUS_HOT_WATER,
            compressorenergy=40.5,
            dhwenergy=52.0,
            bt30=51.0,
        )

        calculator._calculate_cop(values)

        # 2 kWh drawn + 0.20 kWh tank gain over 0.5 kWh of compressor work.
        assert values["cop_dhw"] == pytest.approx(4.407, abs=1e-3)

    def test_charge_segment_adds_the_draw_inside_the_charge(self):
        """A draw during the charge is production the tank balance cannot see."""
        calculator = _Calculator()
        calculator._calculate_cop(_values(bt30=60.0))
        # A shower during the recharge: 2 kWh drawn while the tank falls 2 K,
        # on top of 0.5 kWh of compressor work.
        values = _values(
            hp_status=HP_STATUS_HOT_WATER,
            dhwenergy=52.0,
            compressorenergy=40.5,
            bt30=58.0,
        )
        calculator._calculate_cop(values)

        # 2 kWh drawn − 0.41 kWh of tank loss, over 0.5 kWh of compressor work.
        assert values["cop_dhw"] == pytest.approx(1.593 / 0.5, rel=1e-2)

    def test_element_only_charge_identifies_the_tank_volume(self):
        """2 kWh of element into a 12 K rise measures the effective volume."""
        calculator = _Calculator()
        calculator._calculate_cop(_values(bt30=50.0))
        charge = _values(
            hp_status=HP_STATUS_IDLE,
            dhwdemand=1,
            additionalenergy=12.0,
            bt30=62.0,
        )

        calculator._calculate_cop(charge)

        # 2 kWh / (12 K · c) = 143 L, not the 175 L datasheet figure. The charge
        # itself is already measured with it; nothing is persisted until the
        # charge closes.
        assert calculator._dhw_tank_volume() == pytest.approx(143.3, rel=1e-3)
        assert calculator._dhw_tank_liters is None
        calculator._calculate_cop(
            _values(hp_status=HP_STATUS_IDLE, additionalenergy=12.0, bt30=62.0)
        )
        assert calculator._dhw_tank_liters == pytest.approx(143.3, rel=1e-3)
        assert calculator._dhw_tank_volume() == pytest.approx(143.3, rel=1e-3)

        # The next charge is measured with the identified volume.
        fresh = _Calculator()
        fresh._dhw_tank_liters = calculator._dhw_tank_liters
        fresh._calculate_cop(_values(bt30=50.0))
        with_compressor = _values(
            hp_status=HP_STATUS_HOT_WATER,
            compressorenergy=40.5,
            bt30=56.0,
        )
        fresh._calculate_cop(with_compressor)
        # 6 K · 143 L · c = 1.0 kWh over 0.5 kWh; 175 L would have said 2.44.
        assert with_compressor["cop_dhw"] == pytest.approx(2.0, rel=1e-2)

    def test_tank_volume_is_not_identified_from_a_compressor_charge(self):
        calculator = _Calculator()
        calculator._calculate_cop(_values(bt30=50.0))
        charge = _values(
            hp_status=HP_STATUS_HOT_WATER,
            compressorenergy=42.0,
            bt30=62.0,
        )

        calculator._calculate_cop(charge)

        assert calculator._dhw_tank_liters is None
        assert calculator._dhw_tank_volume() == DHW_TANK_VOLUME_L

    def test_closing_publish_uses_the_tank_the_figures_were_measured_at(self):
        """The interval that clears the demand is not booked to the charge.

        A compressor tail can keep heating the tank in that interval, so the
        closing figure must be measured against the tank level of the last
        charging poll — otherwise its rise counts as production with no
        electrical input and inflates the published COP.
        """
        calculator = _Calculator()
        calculator._calculate_cop(_values(bt30=50.0))
        # 0.4 kWh of element into a 1 K rise, still charging.
        calculator._calculate_cop(
            _values(
                hp_status=HP_STATUS_IDLE,
                dhwdemand=1,
                additionalenergy=10.4,
                bt30=51.0,
            )
        )

        # Demand clears while the tank rises another 5 K on heat that was never
        # booked to the charge.
        closing = _values(
            hp_status=HP_STATUS_IDLE,
            additionalenergy=10.4,
            bt30=56.0,
        )
        calculator._calculate_cop(closing)

        # 1 K · 175 L · c = 0.20 kWh over 0.4 kWh, not 6 K.
        assert closing["cop_dhw"] == pytest.approx(0.509, abs=1e-3)

    def test_charge_accrues_element_energy_from_its_own_flag(self):
        """An element top-up is DHW production even after the demand cleared.

        Measured 2026-10-21: the demand flags read at the moment the coarse
        energy counters step have almost always cleared, so the element's energy
        was booked nowhere and the charge never accumulated what the volume
        identification needs. The element's own DHW flag survives that gap.
        """
        calculator = _Calculator()
        calculator._calculate_cop(_values(bt30=50.0))

        calculator._calculate_cop(
            _values(
                hp_status=HP_STATUS_IDLE,
                dhwdemand=0,
                additiondhwdemand=1,
                additionalenergy=12.0,
                bt30=62.0,
            )
        )

        assert calculator._dhw_charge is not None
        assert calculator._dhw_charge.element_kwh == pytest.approx(2.0)
        assert calculator._dhw_tank_volume() == pytest.approx(143.3, rel=1e-3)

        # The flag clearing closes the charge and persists the identification.
        calculator._calculate_cop(
            _values(
                hp_status=HP_STATUS_IDLE,
                dhwdemand=0,
                additiondhwdemand=0,
                additionalenergy=12.0,
                bt30=62.0,
            )
        )
        assert calculator._dhw_tank_liters == pytest.approx(143.3, rel=1e-3)

    def test_unflagged_element_energy_is_counted_as_unattributed(self):
        calculator = _Calculator()
        calculator._calculate_cop(_values(bt30=50.0))
        calculator._calculate_cop(
            _values(
                hp_status=HP_STATUS_IDLE,
                dhwdemand=0,
                additionalenergy=10.3,
                bt30=50.2,
            )
        )

        assert calculator._cop_unattributed_kwh == pytest.approx(0.3)
    def test_tank_volume_identification_ignores_heat_leaving_the_tank(self):
        """The rise must be the element's, so nothing else may take heat out.

        Measured on a QE-6 (2026-10-21): extra hot water left the pump idle with
        the element demanded while the addition shunt had been open
        (`qn8position` 7) and `heatingpower` read 2.3 kW. That heat leaves the
        tank, so the same element energy shows up as a smaller rise and the
        identified volume comes out too large.
        """
        calculator = _Calculator()
        calculator._calculate_cop(_values(bt30=50.0))

        # The same 2 kWh / 12 K charge that identifies 143 L when the tank is
        # the only place the heat can go.
        with_shunt = _values(
            hp_status=HP_STATUS_IDLE,
            dhwdemand=1,
            additionalenergy=12.0,
            bt30=62.0,
            qn8position=7,
        )
        calculator._calculate_cop(with_shunt)

        assert calculator._dhw_tank_volume() == pytest.approx(DHW_TANK_VOLUME_L)

    def test_tank_volume_identification_ignores_house_heating_output(self):
        """The heating counter steps only when the house really took heat.

        The derived `heatingpower` cannot be used here: it is computed after the
        COP pass and reads zero whenever the compressor is not heating, which is
        exactly the state being guarded.
        """
        calculator = _Calculator()
        calculator._calculate_cop(_values(bt30=50.0))

        with_heating = _values(
            hp_status=HP_STATUS_IDLE,
            dhwdemand=1,
            additionalenergy=12.0,
            heatingenergy=102.4,
            bt30=62.0,
        )
        calculator._calculate_cop(with_heating)

        assert calculator._dhw_tank_volume() == pytest.approx(DHW_TANK_VOLUME_L)

    def test_tank_volume_identification_keeps_working_without_those_metrics(self):
        """A payload without them keeps identifying: absence is not heat flow."""
        calculator = _Calculator()
        calculator._calculate_cop(_values(bt30=50.0))

        charge = _values(
            hp_status=HP_STATUS_IDLE,
            dhwdemand=1,
            additionalenergy=12.0,
            bt30=62.0,
        )
        calculator._calculate_cop(charge)

        assert calculator._dhw_tank_volume() == pytest.approx(143.3, rel=1e-3)

    def test_tank_volume_identification_ignores_a_defrost(self):
        """A defrost inside the charge runs the compressor; it is not element-only.

        ``_split_electrical_input`` only books compressor energy to DHW under
        hot water, so the element-only evidence must also check the compressor
        counter itself — otherwise a defrost's tank change is measured against
        the element's energy and pollutes the persisted volume.
        """
        calculator = _Calculator()
        calculator._calculate_cop(_values(bt30=50.0))
        calculator._calculate_cop(
            _values(
                hp_status=HP_STATUS_IDLE,
                dhwdemand=1,
                additionalenergy=12.0,
                bt30=62.0,
            )
        )
        assert calculator._dhw_tank_volume() == pytest.approx(143.3, rel=1e-3)

        defrost = _values(
            hp_status=HP_STATUS_DEFROSTING,
            dhwdemand=1,
            compressorenergy=40.6,
            additionalenergy=12.0,
            bt30=67.0,
        )
        calculator._calculate_cop(defrost)

        assert calculator._dhw_charge.element_rise_c == pytest.approx(12.0)
        assert calculator._dhw_charge.element_kwh == pytest.approx(2.0)
        assert calculator._dhw_tank_volume() == pytest.approx(143.3, rel=1e-3)

    def test_tank_volume_identification_needs_enough_stored_energy(self):
        calculator = _Calculator()
        calculator._calculate_cop(_values(bt30=50.0))
        charge = _values(
            hp_status=HP_STATUS_IDLE,
            dhwdemand=1,
            additionalenergy=10.5,
            bt30=62.0,
        )

        calculator._calculate_cop(charge)

        assert calculator._dhw_tank_liters is None
        assert calculator._dhw_tank_volume() == DHW_TANK_VOLUME_L

    def test_tank_volume_identification_rejects_an_implausible_volume(self):
        """A 1 K rise on 20 kWh is not a tank measurement."""
        calculator = _Calculator()
        calculator._calculate_cop(_values(bt30=50.0))
        charge = _values(
            hp_status=HP_STATUS_IDLE,
            dhwdemand=1,
            additionalenergy=30.0,
            bt30=51.0,
        )

        calculator._calculate_cop(charge)

        assert calculator._dhw_tank_liters is None

    def test_charge_segment_restarts_after_the_maximum_duration(self):
        """A demand flag that never clears must not keep an ancient baseline."""
        calculator = _Calculator()
        t0 = datetime(2026, 10, 10, 0, 0, 0, tzinfo=timezone.utc)
        t_stuck = t0 + timedelta(seconds=DHW_CHARGE_MAX_SECONDS + 60)
        with patch(
            "custom_components.qvantum.calculations.dt_util.utcnow",
            return_value=t0,
        ):
            calculator._calculate_cop(_values(bt30=50.0))
            calculator._calculate_cop(
                _values(
                    hp_status=HP_STATUS_IDLE,
                    dhwdemand=1,
                    additionalenergy=10.4,
                    bt30=51.0,
                )
            )
        assert calculator._dhw_charge is not None
        assert calculator._dhw_charge.start_tank == 50.0

        values = _values(
            hp_status=HP_STATUS_IDLE,
            dhwdemand=1,
            additionalenergy=14.0,
            bt30=60.0,
        )
        with patch(
            "custom_components.qvantum.calculations.dt_util.utcnow",
            return_value=t_stuck,
        ):
            calculator._calculate_cop(values)

        # The baseline restarted at the last poll's tank instead of counting
        # ten hours of standing loss as missing production.
        assert calculator._dhw_charge is not None
        assert calculator._dhw_charge.start_tank == 51.0
        assert calculator._dhw_charge.electrical_kwh == pytest.approx(3.6)

    def test_counter_reset_drops_the_charge_segment(self):
        calculator = _Calculator()
        calculator._calculate_cop(_values(bt30=50.0))
        calculator._calculate_cop(
            _values(
                hp_status=HP_STATUS_IDLE,
                dhwdemand=1,
                additionalenergy=10.4,
                bt30=51.0,
            )
        )
        assert calculator._dhw_charge is not None

        calculator._calculate_cop(_values(additionalenergy=1.0, bt30=51.0))

        assert calculator._dhw_charge is None

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

    def test_counter_reset_keeps_the_held_value(self):
        """A reset drops the window but not the last measured figure."""
        calculator = _Calculator()
        calculator._calculate_cop(_values())
        calculator._calculate_cop(_values(heatingenergy=105.0, compressorenergy=41.0))
        held = calculator._cop_last["cop_heating"]
        values = _values(heatingenergy=1.0, compressorenergy=1.0)

        calculator._calculate_cop(values)

        # No bogus ratio from the negative delta, and the window restarts from
        # the post-reset sample.
        assert values["cop_heating"] == held
        assert calculator._cop_last == {"cop_heating": held}
        assert calculator._cop_last_times["cop_heating"] is not None
        assert len(calculator._cop_history) == 1

    def test_missing_counter_skips_without_advancing_state(self):
        calculator = _Calculator()
        calculator._calculate_cop(_values())
        baseline = dict(calculator._last_cop_energies)
        values = _values()
        del values["dhwenergy"]

        calculator._calculate_cop(values)

        assert "cop_heating" not in values
        assert calculator._last_cop_energies == baseline
