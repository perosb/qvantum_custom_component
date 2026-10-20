"""Calculation mixin for Qvantum coordinator derived metrics."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Any, Callable

from homeassistant.util import dt as dt_util

from .const import (
    DHW_CAP_HYSTERESIS_C,
    DHW_COMPRESSOR_STATE_HOT_WATER,
    DHW_DEFAULT_COLD_TEMP_C,
    DHW_DEFAULT_FLOW_LPM,
    DHW_EMA_ALPHA,
    DHW_FLOW_SNAPSHOT_THRESHOLD_LPM,
    DHW_MIN_SHOWER_FLOW_LPM,
    DHW_MIN_SHOWER_DURATION_MIN,
    DHW_MIN_TEMPERATURE_DELTA_C,
    DHW_MAX_SHOWER_HISTORY_SIZE,
    DHW_OUTLET_TEMP_THRESHOLD_DELTA_C,
    DHW_ROLLING_BUFFER_WINDOW_SEC,
    DHW_SESSION_GAP_SEC,
    DHW_SHOWER_TEMP_STABLE_MIN_OFFSET_SEC,
    DHW_SHOWER_TEMP_STABLE_MAX_OFFSET_SEC,
    DHW_SHOWER_DURATION_MIN,
    DHW_SHOWER_TEMP_C,
    DHW_CHARGE_MAX_SECONDS,
    DHW_TANK_ID_MIN_KWH,
    DHW_TANK_ID_MIN_RISE_C,
    DHW_TANK_LITERS_ALPHA,
    DHW_TANK_LITERS_MAX,
    DHW_TANK_LITERS_MIN,
    DHW_TANK_VOLUME_L,
    HP_STATUS_HEATING,
    HP_STATUS_HOT_WATER,
    SensorMode,
)
from .dhw_loss import WATER_KWH_PER_LITER_K
from .efficiency import (
    COP_MIN_ELECTRICAL_KWH,
    COP_WINDOW_SECONDS,
    cap_dhw_ratio,
    cop_ratio,
    energy_delta,
)

_LOGGER = logging.getLogger(__name__)


def _flag(value: object) -> bool:
    """Truthiness of an optional 0/1 metric.

    A missing metric is unknown, not true: every caller treats unknown as "not
    demanded", which falls back to the plain ``hp_status`` attribution.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "on", "yes"}
    return False


def _split_electrical_input(
    *,
    hp_status: object,
    dhw_demand: bool,
    aux_demand: bool,
    compressor_kwh: float,
    aux_kwh: float,
) -> tuple[float, float, float]:
    """Attribute one poll interval's electrical input to heating and DHW.

    ``hp_status`` alone cannot do this. The immersion heater charges the DHW
    tank while the compressor is stopped — the pump then reports ``Idle``,
    because the register follows the compressor, not the element — or while the
    compressor heats the house (``Heating``). Booked as heating input, that
    auxiliary energy collapses the heating COP for a charge it never delivered.

    Returns ``(heating_kwh, dhw_kwh, dhw_compressor_kwh)``. The last figure says
    how much of the DHW bucket is compressor work, which bounds how high the DHW
    ratio can physically be — an element-only charge cannot exceed COP 1.
    """
    total = compressor_kwh + aux_kwh
    if hp_status == HP_STATUS_HOT_WATER:
        return 0.0, total, compressor_kwh
    if not (dhw_demand and not aux_demand):
        # No DHW charge, or the auxiliary heat is flagged for space heating.
        if hp_status == HP_STATUS_HEATING:
            return total, 0.0, 0.0
        return 0.0, 0.0, 0.0
    if hp_status == HP_STATUS_HEATING:
        # Parallel run: the compressor heats the house, the element the tank.
        return compressor_kwh, aux_kwh, 0.0
    # Compressor stopped while DHW is still demanded: the element alone.
    return 0.0, aux_kwh, 0.0


@dataclass
class DhwCharge:
    """One DHW charging segment, measured from its own baseline.

    The tank balance only means something against the level the charge started
    from. A fixed window would mix the tank cooling before the charge with the
    charge itself, so production is accumulated per segment instead.
    """

    started: datetime
    start_tank: float | None
    last_tank: float | None
    draw_kwh: float = 0.0
    electrical_kwh: float = 0.0
    compressor_kwh: float = 0.0
    # Evidence gathered while only the element ran (see _identified_dhw_liters).
    element_kwh: float = 0.0
    element_rise_c: float = 0.0


class QvantumCalculationsMixin:
    """Derived metric calculations extracted from the coordinator."""

    def _calculate_mode_power(
        self,
        values: dict,
        *,
        energy_key: str,
        power_key: str,
        is_active: Callable[[dict[str, Any]], bool],
        last_energy_attr: str,
        last_time_attr: str,
        mode_label: str,
    ) -> None:
        """Derive a mode-specific power metric from an energy counter delta.

        Uses elapsed real time between counter increments (not poll interval).
        While active mode is on and the counter is unchanged, holds the previously
        emitted power value. Outside active mode, writes 0 W and resets baseline.
        Negative deltas (counter resets) are clamped to 0 W.
        """
        now = dt_util.utcnow()
        current_energy = values.get(energy_key)
        if current_energy is None:
            return

        prev_power: float = (self.data or {}).get("values", {}).get(power_key, 0.0)
        last_energy = getattr(self, last_energy_attr)
        last_time = getattr(self, last_time_attr)

        if not is_active(values):
            values[power_key] = 0.0
            setattr(self, last_energy_attr, current_energy)
            setattr(self, last_time_attr, now)
            return

        _LOGGER.debug(
            "Calculating %s power: current_energy=%.6f kWh, last_energy=%s kWh, last_time=%s",
            mode_label,
            current_energy,
            last_energy,
            last_time,
        )

        if last_energy is not None and last_time is not None:
            delta_kwh = current_energy - last_energy
            delta_seconds = (now - last_time).total_seconds()
            if delta_kwh != 0 and delta_seconds > 0:
                # kWh / s -> W: (kWh * 3_600_000 J/kWh) / s = J/s = W
                power_w = (delta_kwh * 3_600_000) / delta_seconds
                prev_power = max(0.0, round(power_w, 1))
                _LOGGER.debug(
                    "Calculated %s: %.1f W (delta=%.6f kWh over %.1f s)",
                    power_key,
                    prev_power,
                    delta_kwh,
                    delta_seconds,
                )

        values[power_key] = prev_power

        if current_energy != last_energy:
            setattr(self, last_time_attr, now)
        setattr(self, last_energy_attr, current_energy)

    def _calculate_heating_power(self, values: dict) -> None:
        """Derive heatingpower (W) from the heatingenergy (kWh) delta between polls."""
        self._calculate_mode_power(
            values,
            energy_key="heatingenergy",
            power_key="heatingpower",
            is_active=lambda v: v.get("hp_status") == HP_STATUS_HEATING,
            last_energy_attr="_last_heatingenergy",
            last_time_attr="_last_heatingenergy_time",
            mode_label="heating",
        )

    def _calculate_dhw_power(self, values: dict) -> None:
        """Derive dhwpower (W) from the dhwenergy (kWh) delta between polls."""
        self._calculate_mode_power(
            values,
            energy_key="dhwenergy",
            power_key="dhwpower",
            # DHW activity is determined by BF1 flow rate rather than hp_status,
            # because active tap water production is most reliably indicated by
            # water flow through the DHW circuit.
            is_active=lambda v: v.get("bf1_l_min", 0) > 0,
            last_energy_attr="_last_dhwenergy",
            last_time_attr="_last_dhwenergy_time",
            mode_label="dhw",
        )

    def _calculate_dhw_meter(self, values: dict) -> None:
        """Feed the poll into the DHW heat-meter comparison window.

        The flow × ΔT estimate can only be integrated where the interval is
        known: the recorder's hourly means multiply two averages, and a tap
        draw is minutes long with both signals moving together inside it, so
        that product lands far below the integral (see ``DhwMeterWindow``). The
        metered counter rides along in the same window, so both sides cover
        exactly the same polls.

        The running totals travel in ``values`` for the efficiency coordinator,
        which owns the thresholds and the warning. They stay bounded: one small
        bucket per hour, pruned to the window.
        """
        now_ts = dt_util.utcnow().timestamp()
        self._dhw_meter.add(
            now_ts=now_ts,
            flow_l_min=values.get("bf1_l_min"),
            hot_out_c=values.get("bt34"),
            cold_in_c=values.get("bt33"),
            meter_kwh=values.get("dhwenergy"),
        )
        estimated, metered, draw_hours = self._dhw_meter.totals(now_ts)
        values["dhw_meter_estimated_kwh"] = round(estimated, 3)
        values["dhw_meter_metered_kwh"] = round(metered, 3)
        values["dhw_meter_draw_hours"] = round(draw_hours, 2)

    def _calculate_cop(self, values: dict) -> None:
        """Derive the space-heating and DHW COP from the cumulative counters.

        The counters advance in 0.1 kWh steps, so the delta over one poll is
        zero most of the time and a single-step ratio is a quantisation
        artefact. The calculation keeps a rolling window of counter samples and
        publishes a ratio once the window holds measurable electrical input. The
        last value is held across mode changes (a DHW charge must not blank the
        heating figure) and only a counter reset or restart clears it.

        Space heating: only the electrical energy consumed while ``hp_status``
        is heating is divided into ``heatingenergy`` — the compressor/auxiliary
        counter is shared with DHW charging. The split per interval comes from
        ``_split_electrical_input``: auxiliary heat that charges the tank is not
        heating input, even when the pump reports heating (parallel run) or idle
        (compressor stopped, element running).

        DHW: ``dhwenergy`` meters the draw (secondary side), not the
        compressor's output, so it is combined with the tank energy balance
        ``(bt30_end - bt30_start) · V · c`` to recover production, then divided
        by the electrical consumed in DHW mode. This is an estimate, not a
        meter, and needs ``bt30``. It is measured over one charge segment (see
        ``_update_dhw_charge``) rather than over the heating window, and its
        tank volume is identified from element-only charges rather than taken
        from the datasheet.

        A counter reset drops the window instead of showing a bogus ratio.
        """
        keys = (
            "heatingenergy",
            "dhwenergy",
            "compressorenergy",
            "additionalenergy",
        )
        current: dict[str, float] = {}
        for key in keys:
            value = values.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return
            current[key] = float(value)

        tank_temp = values.get("bt30")
        if isinstance(tank_temp, bool) or not isinstance(tank_temp, (int, float)):
            tank_temp = None
        else:
            tank_temp = float(tank_temp)

        now = dt_util.utcnow()
        previous = self._last_cop_energies
        self._last_cop_energies = current
        hp_status = values.get("hp_status")
        # The demand flags travel with the sample they were read at, so an
        # interval is attributed with the mode in force at its end.
        dhw_demand = _flag(values.get("dhwdemand"))
        aux_demand = _flag(values.get("additiondemand"))
        history = self._cop_history
        sample = (now, current, hp_status, tank_temp, dhw_demand, aux_demand)

        if previous is None:
            # First observation only seeds the window. A snapshot restored from
            # storage is still published so the sensors survive a restart.
            history.clear()
            history.append(sample)
            self._set_cop(
                values,
                self._cop_last.get("cop_heating"),
                self._cop_last.get("cop_dhw"),
            )
            return

        if any(energy_delta(previous[key], current[key]) is None for key in keys):
            # A counter reset makes every accumulated delta unknown. Drop the
            # window and restart it from this sample, but keep the last
            # published figures: a reset only invalidates the window, not the
            # ratios measured before it, and the sensor is defined to hold its
            # last value until a new window is measurable.
            history.clear()
            history.append(sample)
            # The open charge lost its baseline with the counters.
            self._dhw_charge = None
            self._set_cop(
                values,
                self._cop_last.get("cop_heating"),
                self._cop_last.get("cop_dhw"),
            )
            return

        previous_tank = history[-1][3] if history else None
        history.append(sample)
        cutoff = now - timedelta(seconds=COP_WINDOW_SECONDS)
        # Always keep the previous sample as the baseline: if the poll interval
        # exceeds the window, the window would otherwise trim down to the
        # current sample and never produce a delta.
        while len(history) > 2 and history[0][0] < cutoff:
            history.popleft()

        heating_thermal = 0.0
        heating_electrical = 0.0
        for (_, before, _, _, _, _), (
            _,
            after,
            mode,
            _,
            interval_dhw_demand,
            interval_aux_demand,
        ) in pairwise(history):
            interval_heating, _, _ = _split_electrical_input(
                hp_status=mode,
                dhw_demand=interval_dhw_demand,
                aux_demand=interval_aux_demand,
                compressor_kwh=(after["compressorenergy"] - before["compressorenergy"]),
                aux_kwh=(after["additionalenergy"] - before["additionalenergy"]),
            )
            if mode == HP_STATUS_HEATING:
                heating_thermal += after["heatingenergy"] - before["heatingenergy"]
            heating_electrical += interval_heating

        # The counters are read as floats, so a sum can land a hair below its
        # nominal value (0.2 -> 0.19999999999999574). Rounding below the
        # 0.1 kWh counter resolution keeps the threshold comparison exact.
        heating_thermal = round(heating_thermal, 6)
        heating_electrical = round(heating_electrical, 6)

        if heating_electrical >= COP_MIN_ELECTRICAL_KWH:
            heating_ratio = cop_ratio(heating_thermal, heating_electrical)
            if heating_ratio is not None:
                self._cop_last["cop_heating"] = heating_ratio
                self._cop_last_times["cop_heating"] = now

        self._update_dhw_charge(
            now=now,
            tank_temp=tank_temp,
            previous_tank=previous_tank,
            previous=previous,
            current=current,
            hp_status=hp_status,
            dhw_demand=dhw_demand,
            aux_demand=aux_demand,
        )

        self._set_cop(
            values,
            self._cop_last.get("cop_heating"),
            self._cop_last.get("cop_dhw"),
        )

    def _update_dhw_charge(
        self,
        *,
        now: datetime,
        tank_temp: float | None,
        previous_tank: float | None,
        previous: dict[str, float],
        current: dict[str, float],
        hp_status: object,
        dhw_demand: bool,
        aux_demand: bool,
    ) -> None:
        """Measure the current DHW charge as one segment and publish its COP.

        A charge is a segment, not a window: the tank balance only means
        something against the level the charge started from. The segment opens
        on the poll where DHW heating is first demanded — with the tank level of
        the poll before it, so the first rise is not lost — and closes on the
        first poll where the demand is gone, which is when the volume
        identification and the final ratio are taken.

        A demand flag that never clears would carry standing loss for hours, so
        a segment older than ``DHW_CHARGE_MAX_SECONDS`` restarts its baseline
        instead of publishing a figure nobody can trust.
        """
        charge = self._dhw_charge
        charging = dhw_demand or hp_status == HP_STATUS_HOT_WATER

        if charge is not None and (
            now - charge.started
        ).total_seconds() > DHW_CHARGE_MAX_SECONDS:
            charge = None
            self._dhw_charge = None

        if not charging:
            if charge is None:
                return
            self._dhw_charge = None
            # Identify first: the closing figure is then computed with the
            # volume this very charge just measured.
            self._learn_dhw_tank_liters(charge)
            # Publish against the tank the accumulated figures were measured
            # at: the interval that cleared the demand is not booked to the
            # charge, so its rise (a compressor tail reaching the stop
            # temperature) must not be counted as free production either.
            self._publish_dhw_charge(charge, charge.last_tank, now)
            return

        if charge is None:
            if previous_tank is None:
                # No tank level to measure from: open at the current one and
                # start accumulating on the next poll rather than counting this
                # interval's energy against a baseline it is already part of.
                self._dhw_charge = DhwCharge(now, tank_temp, tank_temp)
                return
            charge = DhwCharge(now, previous_tank, previous_tank)
            self._dhw_charge = charge

        compressor_delta = current["compressorenergy"] - previous["compressorenergy"]
        _, interval_dhw, interval_dhw_compressor = _split_electrical_input(
            hp_status=hp_status,
            dhw_demand=dhw_demand,
            aux_demand=aux_demand,
            compressor_kwh=compressor_delta,
            aux_kwh=current["additionalenergy"] - previous["additionalenergy"],
        )
        interval_draw = current["dhwenergy"] - previous["dhwenergy"]
        charge.draw_kwh += interval_draw
        charge.electrical_kwh += interval_dhw
        charge.compressor_kwh += interval_dhw_compressor
        if (
            interval_dhw_compressor == 0.0
            and compressor_delta == 0.0
            and interval_draw == 0.0
            and hp_status not in (HP_STATUS_HEATING, HP_STATUS_HOT_WATER)
            and tank_temp is not None
            and charge.last_tank is not None
        ):
            # The compressor is idle and nothing is drawn: the element is the
            # only heat source, so its energy and the tank rise measure the
            # same volume. Both sums span every such interval, because the
            # element counter steps far less often than the tank rises.
            charge.element_kwh += interval_dhw
            charge.element_rise_c += tank_temp - charge.last_tank
        if tank_temp is not None:
            charge.last_tank = tank_temp

        self._publish_dhw_charge(charge, tank_temp, now)

    def _publish_dhw_charge(
        self,
        charge: DhwCharge,
        tank_temp: float | None,
        now: datetime,
    ) -> None:
        """Publish the ratio for the charge so far (live and at its close)."""
        if tank_temp is None or charge.start_tank is None:
            return
        electrical = round(charge.electrical_kwh, 6)
        if electrical < COP_MIN_ELECTRICAL_KWH:
            return
        # Production recovered from the tank balance: what was drawn since the
        # charge started plus what stayed in (or came out of) the tank.
        production = charge.draw_kwh + (tank_temp - charge.start_tank) * (
            self._dhw_tank_volume() * WATER_KWH_PER_LITER_K
        )
        dhw_ratio = cap_dhw_ratio(
            cop_ratio(production, electrical), charge.compressor_kwh
        )
        if dhw_ratio is None:
            return
        self._cop_last["cop_dhw"] = dhw_ratio
        self._cop_last_times["cop_dhw"] = now

    def _dhw_tank_volume(self) -> float:
        """Effective tank volume for the balance, best measurement first.

        While a charge is open its own element-only stretch is the most direct
        measurement available; otherwise the volume identified over earlier
        charges, and only then the datasheet default.
        """
        charge = self._dhw_charge
        if charge is not None:
            live = self._identified_dhw_liters(charge)
            if live is not None:
                return live
        identified = getattr(self, "_dhw_tank_liters", None)
        return identified if identified else DHW_TANK_VOLUME_L

    @staticmethod
    def _identified_dhw_liters(charge: DhwCharge) -> float | None:
        """Effective tank volume from the element-only stretch of a charge.

        Without the compressor, every kWh the element draws is stored in the
        buffer tank (times the heater's efficiency), so the rise measures the
        volume the balance really sees. The datasheet volume (175 L on the
        floor-standing models, unpublished and smaller in the wall-mounted
        QH100) is the store itself, but `bt30` reads the top of a stratified
        tank that hot water is drawn from through a plate heat exchanger, so the
        energy per kelvin the balance can account for is smaller. That effective
        volume is what this measures, and the DHW COP carries any error in it
        linearly.
        """
        if charge.element_kwh < DHW_TANK_ID_MIN_KWH:
            return None
        if charge.element_rise_c < DHW_TANK_ID_MIN_RISE_C:
            return None
        liters = charge.element_kwh / (
            charge.element_rise_c * WATER_KWH_PER_LITER_K
        )
        if not DHW_TANK_LITERS_MIN <= liters <= DHW_TANK_LITERS_MAX:
            return None
        return liters

    def _learn_dhw_tank_liters(self, charge: DhwCharge) -> None:
        """Fold a finished charge's element-only evidence into the volume."""
        liters = self._identified_dhw_liters(charge)
        if liters is None:
            return
        previous = getattr(self, "_dhw_tank_liters", None)
        self._dhw_tank_liters = (
            liters
            if previous is None
            else DHW_TANK_LITERS_ALPHA * liters
            + (1.0 - DHW_TANK_LITERS_ALPHA) * previous
        )
        _LOGGER.debug(
            "Identified DHW tank volume: %.0f L from %.1f kWh over %.1f K (EMA -> %.0f L)",
            liters,
            charge.element_kwh,
            charge.element_rise_c,
            self._dhw_tank_liters,
        )

    @staticmethod
    def _set_cop(
        values: dict,
        cop_heating: float | None,
        cop_dhw: float | None,
    ) -> None:
        """Publish (or clear) the COP values."""
        values["cop_heating"] = cop_heating
        values["cop_dhw"] = cop_dhw

    def _finalize_tap_water_session(self, *, tank_temp: float | None) -> None:
        """Finalize the current tap-water session and clear session state.

        Uses ``_shower_pause_time`` as the true flow-stop timestamp so idle time
        after flow has stopped is not counted toward duration.
        """
        if self._shower_start_time is None or self._shower_pause_time is None:
            return

        session_dhw_reheating = getattr(self, "_session_dhw_reheating", False)
        session_started_with_reheating = getattr(
            self, "_session_started_with_reheating", False
        )
        active_flow_duration_sec = getattr(
            self, "_session_active_flow_duration_sec", None
        )

        duration_min = (
            self._shower_pause_time - self._shower_start_time
        ).total_seconds() / 60.0
        # Use active-flow time (excludes mid-session pauses) so a short rinse
        # followed by a long pause does not inflate the qualifying duration.
        # None means the attribute was never set via polling (test state with manually
        # seeded session fields); fall back to wall-clock only in that case.
        # An explicit 0.0 means polling ran but no active-flow time accumulated,
        # which correctly yields 0 min and fails the minimum duration check.
        active_flow_min = (
            active_flow_duration_sec / 60.0
            if active_flow_duration_sec is not None
            else duration_min
        )
        if active_flow_min >= DHW_MIN_SHOWER_DURATION_MIN:
            # Compute avg_flow early so it can gate all EMA learning below.
            if self._shower_event_samples:
                avg_flow = sum(s[1] for s in self._shower_event_samples) / len(
                    self._shower_event_samples
                )
            else:
                avg_flow = 0.0
            flow_qualifies = avg_flow >= DHW_MIN_SHOWER_FLOW_LPM

            if session_dhw_reheating and session_started_with_reheating:
                # DHW reheating was already active when flow started — this is
                # likely a recirculation pulse, not a real shower. Skip EMA.
                _LOGGER.debug(
                    "Tap water session started with reheating active: duration=%.1f min — skipping EMA update (recirculation pulse)",
                    duration_min,
                )
            elif not flow_qualifies:
                # Low average flow (dishwasher, hand-washing, etc.) — do not
                # corrupt the shower EMAs with non-shower behaviour.
                _LOGGER.debug(
                    "Tap water session ended: avg_flow=%.2f L/min < min=%.1f — skipping EMA update (not a shower)",
                    avg_flow,
                    DHW_MIN_SHOWER_FLOW_LPM,
                )
            else:
                prior_dur = (
                    self._last_shower_duration_min
                    if self._last_shower_duration_min is not None
                    else DHW_SHOWER_DURATION_MIN
                )
                self._last_shower_duration_min = (
                    DHW_EMA_ALPHA * duration_min + (1 - DHW_EMA_ALPHA) * prior_dur
                )
                _LOGGER.debug(
                    "Shower ended: duration=%.1f min; EMA shower duration → %.1f min (tank=%s)",
                    duration_min,
                    self._last_shower_duration_min,
                    f"{tank_temp:.1f}°C" if tank_temp is not None else "unknown",
                )

            # Record completed session and update shower temp/flow EMAs.
            # Skip only if reheating was active at session start (recirculation pulse).
            # A real shower can trigger reheating mid-session; don't skip those.
            is_recirculation_pulse = (
                session_dhw_reheating and session_started_with_reheating
            )
            if (
                not is_recirculation_pulse
                and flow_qualifies
                and self._shower_event_samples
            ):
                cold_samples = [
                    s[2] for s in self._shower_event_samples if s[2] is not None
                ]
                avg_cold = (
                    sum(cold_samples) / len(cold_samples)
                    if cold_samples
                    else DHW_DEFAULT_COLD_TEMP_C
                )
                outlet_samples = [
                    s[3] for s in self._shower_event_samples if s[3] is not None
                ]
                avg_outlet_temp = (
                    sum(outlet_samples) / len(outlet_samples)
                    if outlet_samples
                    else None
                )
                if active_flow_duration_sec is None or active_flow_duration_sec <= 0:
                    # None = not tracked via polling (test state); 0 = no flow time
                    # accumulated. Fall back to wall-clock duration in both cases so
                    # water_used_l is at least a reasonable estimate.
                    active_flow_duration_min = duration_min
                else:
                    active_flow_duration_min = active_flow_duration_sec / 60.0
                water_used_l = avg_flow * active_flow_duration_min
                # Update the shower flow EMA once per completed session
                # (same pattern as shower duration EMA) so that unrelated
                # tap events (e.g. dishes at low flow) cannot corrupt it
                # during an in-progress flow poll.
                prior_flow = (
                    self._last_shower_flow_lpm
                    if self._last_shower_flow_lpm is not None
                    else DHW_DEFAULT_FLOW_LPM
                )
                self._last_shower_flow_lpm = (
                    DHW_EMA_ALPHA * avg_flow + (1 - DHW_EMA_ALPHA) * prior_flow
                )
                _LOGGER.debug(
                    "Shower ended: avg_flow=%.2f L/min; EMA shower flow → %.2f L/min",
                    avg_flow,
                    self._last_shower_flow_lpm,
                )
                event = {
                    "start": self._shower_start_time.isoformat(),
                    "end": self._shower_pause_time.isoformat(),
                    "duration_min": round(duration_min, 1),
                    "avg_flow": round(avg_flow, 2),
                    "avg_cold": round(avg_cold, 1),
                    "avg_outlet_temp": (
                        round(avg_outlet_temp, 1)
                        if avg_outlet_temp is not None
                        else None
                    ),
                    "water_used_l": round(water_used_l, 1),
                }
                self._shower_event_history.append(event)
                if len(self._shower_event_history) > DHW_MAX_SHOWER_HISTORY_SIZE:
                    self._shower_event_history.pop(0)
                avg_outlet_display = (
                    f"{avg_outlet_temp:.1f}°C"
                    if avg_outlet_temp is not None
                    else "unknown"
                )
                _LOGGER.info(
                    "Shower event: duration=%.1f min, avg_flow=%.2f L/min, "
                    "avg_cold=%.1f°C, avg_outlet=%s, water_used=%.1f L",
                    duration_min,
                    avg_flow,
                    avg_cold,
                    avg_outlet_display,
                    water_used_l,
                )

                # Update shower temperature EMA once per shower using the
                # early-stable window (60–180 s after onset): past warmup
                # transients, before tank-depletion drives bt34 upward.
                shower_start_ts = self._shower_start_time.timestamp()
                stable_outlet_samples = [
                    s[3]
                    for s in self._shower_event_samples
                    if s[3] is not None
                    and DHW_SHOWER_TEMP_STABLE_MIN_OFFSET_SEC
                    <= (s[0] - shower_start_ts)
                    <= DHW_SHOWER_TEMP_STABLE_MAX_OFFSET_SEC
                    and s[3]
                    > (s[2] if s[2] is not None else DHW_DEFAULT_COLD_TEMP_C)
                    + DHW_OUTLET_TEMP_THRESHOLD_DELTA_C
                ]
                if not stable_outlet_samples:
                    # Fallback: all post-warmup samples.
                    stable_outlet_samples = [
                        s[3]
                        for s in self._shower_event_samples
                        if s[3] is not None
                        and (s[0] - shower_start_ts)
                        >= DHW_SHOWER_TEMP_STABLE_MIN_OFFSET_SEC
                        and s[3]
                        > (s[2] if s[2] is not None else DHW_DEFAULT_COLD_TEMP_C)
                        + DHW_OUTLET_TEMP_THRESHOLD_DELTA_C
                    ]
                if stable_outlet_samples:
                    stable_outlet_temp = sum(stable_outlet_samples) / len(
                        stable_outlet_samples
                    )
                    prior_shower_temp = (
                        self._last_shower_temp_c
                        if self._last_shower_temp_c is not None
                        else DHW_SHOWER_TEMP_C
                    )
                    self._last_shower_temp_c = (
                        DHW_EMA_ALPHA * stable_outlet_temp
                        + (1 - DHW_EMA_ALPHA) * prior_shower_temp
                    )
                    _LOGGER.debug(
                        "Learned shower_temp from %d early-stable samples: avg=%.1f°C → EMA=%.1f°C",
                        len(stable_outlet_samples),
                        stable_outlet_temp,
                        self._last_shower_temp_c,
                    )

        self._shower_start_time = None
        self._shower_pause_time = None
        self._session_dhw_reheating = False
        self._session_started_with_reheating = False
        self._session_active_flow_duration_sec = None
        self._last_active_flow_sample_time = None
        self._flow_rolling_buffer.clear()
        self._shower_event_samples.clear()
        # A finalised session always resets warmup; the next flow-onset starts fresh.
        self._tap_water_cap_start_time = None

    def _calculate_tap_water_cap(self, values: dict) -> None:
        """Derive tap_water_cap (showers remaining) from tank and flow sensor data.

        bt30 (tank temp) is used directly as the effective hot water temperature,
        since it represents current stored energy and decreases as hot water is
        drawn — making capacity correctly decrease during a shower.
        bt33 (cold water in) and bf1_l_min (flow) are snapshotted during active
        flow and EMA-smoothed to prevent brief transients from dominating.
        Formula (integrated perfect-mixing tank model):
                 minutes = (volume / flow) * ln((tank - cold) / (shower - cold)),
                 showers = minutes / shower_duration_min.
        This is the analytic solution to T(t)=cold+(hot-cold)*exp(-flow/V*t)
        for the time t* at which T drops to shower_temp.
        """
        tank_temp = values.get("bt30")
        flow = values.get("bf1_l_min")
        cold = values.get("bt33")

        # Snapshot realistic shower-time values while water is actually flowing.
        # EMA-smooth cold to prevent a brief transient (e.g. warm pipe water at
        # the start of a 15-second run) from dominating the estimate.
        flow_is_active = flow is not None and flow > DHW_FLOW_SNAPSHOT_THRESHOLD_LPM

        # Compute now early so start/end tracking can use it.
        now = dt_util.utcnow()

        # Compute reheating state early — needed both for skipping EMA learning
        # during system recirculation pulses and for the capacity guard logic below.
        dhw_reheating = (
            values.get("compressor_state") == DHW_COMPRESSOR_STATE_HOT_WATER
            or values.get("picpin_relay_heat_l1")
            or values.get("picpin_relay_heat_l2")
            or values.get("picpin_relay_heat_l3")
        )

        if flow_is_active:
            flow_qualifies_for_session = (
                flow is not None and flow >= DHW_MIN_SHOWER_FLOW_LPM
            )
            # If flow resumes within the session gap, continue the same session
            # rather than starting a new one (e.g. shampoo pause mid-shower).
            # Low-flow bursts (tooth brushing, hand washing) are excluded from
            # both session creation and gap extension so they cannot keep a
            # just-finished shower session alive indefinitely.
            if self._shower_start_time is None:
                if flow_qualifies_for_session:
                    self._shower_start_time = now
                    self._shower_pause_time = None
                    self._session_dhw_reheating = False
                    self._session_started_with_reheating = bool(dhw_reheating)
                    self._session_active_flow_duration_sec = 0.0
                    self._last_active_flow_sample_time = now
            elif self._shower_pause_time is not None:
                gap_sec = (now - self._shower_pause_time).total_seconds()
                if gap_sec <= DHW_SESSION_GAP_SEC:
                    if flow_qualifies_for_session:
                        # High-flow resumption (e.g. shampoo pause mid-shower) —
                        # clear the pause time so the session gap resets normally.
                        _LOGGER.debug(
                            "Flow resumed after %.0f s pause — continuing tap-water session (gap ≤ %.0f s)",
                            gap_sec,
                            DHW_SESSION_GAP_SEC,
                        )
                        self._shower_pause_time = None
                    else:
                        # Low-flow burst within the gap (tooth brushing, etc.) —
                        # do NOT reset _shower_pause_time so the gap keeps counting
                        # from the last qualifying-flow stop.
                        _LOGGER.debug(
                            "Low-flow burst (%.1f L/min) %.0f s after last flow — not extending session gap",
                            flow or 0.0,
                            gap_sec,
                        )
                else:
                    # Gap too long: finalise previous session now (if it has not
                    # already been finalised by an intermediate no-flow poll),
                    # then start fresh for this new flow event if it qualifies.
                    self._finalize_tap_water_session(tank_temp=tank_temp)
                    if flow_qualifies_for_session:
                        self._shower_start_time = now
                        self._session_dhw_reheating = False
                        self._session_started_with_reheating = bool(dhw_reheating)
                        self._session_active_flow_duration_sec = 0.0
                        self._last_active_flow_sample_time = now
                    # _shower_pause_time is already None after finalization.

            # Only accumulate session-scoped learning inputs when a session is
            # open and not paused. Low-flow bursts leave _shower_pause_time set
            # so they are excluded from gap extension and from learning inputs.
            session_is_active_for_learning = (
                self._shower_start_time is not None and self._shower_pause_time is None
            )

            # Active-flow duration should advance only while the session is
            # actively flowing (not paused).
            if session_is_active_for_learning:
                if (
                    self._last_active_flow_sample_time is not None
                    and self._last_active_flow_sample_time != now
                ):
                    self._session_active_flow_duration_sec += (
                        now - self._last_active_flow_sample_time
                    ).total_seconds()
                self._last_active_flow_sample_time = now

            if session_is_active_for_learning:
                self._session_dhw_reheating = self._session_dhw_reheating or bool(
                    dhw_reheating
                )

                # Phase 1: maintain a rolling 60-second buffer of flow/cold
                # readings for the active session.
                ts = now.timestamp()
                self._flow_rolling_buffer.append((ts, flow, cold))
                cutoff = ts - DHW_ROLLING_BUFFER_WINDOW_SEC
                self._flow_rolling_buffer = [
                    s for s in self._flow_rolling_buffer if s[0] >= cutoff
                ]

                if cold is not None:
                    prior_cold = (
                        self._last_shower_cold_temp
                        if self._last_shower_cold_temp is not None
                        else DHW_DEFAULT_COLD_TEMP_C
                    )
                    self._last_shower_cold_temp = (
                        DHW_EMA_ALPHA * cold + (1 - DHW_EMA_ALPHA) * prior_cold
                    )
                # Collect outlet temp for end-of-shower statistics and
                # temperature learning.
                # The shower temperature EMA is NOT updated here; it is updated
                # once at the end of flow using the early-stable window (60–180
                # s post-onset) to avoid the EMA being driven upward by rising
                # bt34 readings that occur during a long shower.
                outlet_temp = values.get("bt34")

                # Phase 2: accumulate per-event samples for end-of-shower
                # statistics.
                self._shower_event_samples.append((ts, flow, cold, outlet_temp))
        else:
            # Flow is not active.
            if self._shower_start_time is not None:
                # First poll after flow stops: record the pause time.
                if self._shower_pause_time is None:
                    if self._last_active_flow_sample_time is not None:
                        self._session_active_flow_duration_sec += (
                            now - self._last_active_flow_sample_time
                        ).total_seconds()
                        self._last_active_flow_sample_time = None
                    self._shower_pause_time = now

                gap_sec = (now - self._shower_pause_time).total_seconds()
                if gap_sec > DHW_SESSION_GAP_SEC:
                    # Gap expired — finalise the session.
                    # Uses _shower_pause_time as end of flow (not now, which
                    # includes the idle wait time).
                    self._finalize_tap_water_session(tank_temp=tank_temp)
                # else: gap not yet expired — keep session open and DO NOT clear
                # buffers or state; samples are retained for when flow resumes.
            else:
                # No active session — ensure buffers are clean.
                self._flow_rolling_buffer.clear()
                self._shower_event_samples.clear()

        # Resolve values for the capacity calculation.
        # calc_flow always uses the EMA-learned shower flow rate rather than the
        # current tap flow: the question is "how many showers at typical shower
        # conditions", so an unrelated flow event (e.g. washing dishes at
        # 4 L/min) must not inflate the estimate by lowering the assumed flow.
        # calc_cold uses the rolling buffer mean during active flow to react to
        # real-time cold-water conditions; otherwise falls back to the EMA snapshot.
        calc_flow = (
            self._last_shower_flow_lpm
            if self._last_shower_flow_lpm is not None
            else DHW_DEFAULT_FLOW_LPM
        )
        calc_shower_temp = (
            self._last_shower_temp_c
            if self._last_shower_temp_c is not None
            else DHW_SHOWER_TEMP_C
        )
        if flow_is_active:
            cold_buf = [s[2] for s in self._flow_rolling_buffer if s[2] is not None]
            calc_cold = (
                sum(cold_buf) / len(cold_buf)
                if cold_buf
                else (
                    self._last_shower_cold_temp
                    if self._last_shower_cold_temp is not None
                    else DHW_DEFAULT_COLD_TEMP_C
                )
            )
        else:
            calc_cold = (
                self._last_shower_cold_temp
                if self._last_shower_cold_temp is not None
                else DHW_DEFAULT_COLD_TEMP_C
            )

        # Use the EMA-learned shower duration when available.
        calc_shower_duration = (
            self._last_shower_duration_min
            if self._last_shower_duration_min is not None
            else DHW_SHOWER_DURATION_MIN
        )

        if tank_temp is None:
            return

        # Use tank_temp (bt30) directly as the hot water temperature.
        # tank_temp decreases as hot water is drawn, so capacity correctly
        # decreases during a shower — unlike bt34 outlet temp which rises as
        # pipes warm up and would cause capacity to appear to increase.
        effective_hot_temp = tank_temp

        cold_ge_shower_temp = calc_cold >= calc_shower_temp

        # Hysteresis around the hot-vs-shower threshold prevents rapid
        # 0/non-zero toggling when temperatures hover within sensor noise.
        in_zero_mode = getattr(self, "_tap_water_cap_zero_mode", False)
        if in_zero_mode:
            should_force_zero = (
                effective_hot_temp < calc_shower_temp + DHW_CAP_HYSTERESIS_C
                and not dhw_reheating
            ) or cold_ge_shower_temp
        else:
            should_force_zero = (
                effective_hot_temp <= calc_shower_temp - DHW_CAP_HYSTERESIS_C
                and not dhw_reheating
            ) or cold_ge_shower_temp

        if should_force_zero:
            if not in_zero_mode:
                # First poll entering zero mode: reset EMA so the first recovery
                # poll seeds from raw rather than blending with the stale high value.
                self._last_tap_water_cap = None
            self._tap_water_cap_zero_mode = True
            if cold_ge_shower_temp:
                reason = "cold_ge_shower_temp"
            elif in_zero_mode:
                reason = "hot_below_hysteresis_hold"
            else:
                reason = "hot_below_hysteresis_entry"
            values["tap_water_cap"] = 0.0
            values["tap_water_minutes"] = 0
            # Keep published state consistent with emitted zero values so
            # persistence and warmup interpolation do not reuse stale baselines.
            self._last_published_tap_water_cap = 0.0
            self._last_published_tap_water_minutes = 0
            _LOGGER.debug(
                "Calculated tap_water_cap=0.00 showers (0 min, reason=%s, tank=%.1f°C, cold=%.1f°C, flow=%.1f L/min, shower_temp=%.1f°C, shower_dur=%.1f min, hysteresis=%.1f°C, zero_mode=true, reheating=%s)",
                reason,
                effective_hot_temp,
                calc_cold,
                calc_flow,
                calc_shower_temp,
                calc_shower_duration,
                DHW_CAP_HYSTERESIS_C,
                dhw_reheating,
            )
            return

        self._tap_water_cap_zero_mode = False

        delta_available = effective_hot_temp - calc_cold
        if delta_available < DHW_MIN_TEMPERATURE_DELTA_C:
            values["tap_water_cap"] = 0.0
            values["tap_water_minutes"] = 0
            _LOGGER.debug(
                "Calculated tap_water_cap=0.00 showers (0 min, reason=delta_below_min, tank=%.1f°C, cold=%.1f°C, delta=%.1f°C, min_delta=%.1f°C, flow=%.1f L/min, shower_temp=%.1f°C, shower_dur=%.1f min)",
                effective_hot_temp,
                calc_cold,
                delta_available,
                DHW_MIN_TEMPERATURE_DELTA_C,
                calc_flow,
                calc_shower_temp,
                calc_shower_duration,
            )
            return

        if calc_flow <= 0:
            values["tap_water_cap"] = 0.0
            values["tap_water_minutes"] = 0
            _LOGGER.debug(
                "Calculated tap_water_cap=0.00 showers (0 min, reason=non_positive_flow, tank=%.1f°C, cold=%.1f°C, flow=%.1f L/min, shower_temp=%.1f°C)",
                effective_hot_temp,
                calc_cold,
                calc_flow,
                calc_shower_temp,
            )
            return

        log_ratio = (effective_hot_temp - calc_cold) / (
            calc_shower_temp - calc_cold
        )
        reheating_floor_applied = False
        if effective_hot_temp <= calc_shower_temp or log_ratio <= 1.0:
            if not dhw_reheating:
                values["tap_water_cap"] = 0.0
                values["tap_water_minutes"] = 0
                self._tap_water_cap_zero_mode = True
                self._tap_water_cap_reheating_floor_mode = False
                _LOGGER.debug(
                    "Calculated tap_water_cap=0.00 showers (0 min, reason=log_ratio_not_gt_one, tank=%.1f°C, cold=%.1f°C, flow=%.1f L/min, shower_temp=%.1f°C, ratio=%.3f)",
                    effective_hot_temp,
                    calc_cold,
                    calc_flow,
                    calc_shower_temp,
                    log_ratio,
                )
                return
            # Reheating: tank temporarily depleted to/below shower temp.
            # Bypass the log model and apply the floor directly.
            minutes = calc_shower_duration
            reheating_floor_applied = True
        else:
            # Integrated perfect-mixing tank model: time until outlet temperature
            # drops from effective_hot_temp to calc_shower_temp under continuous
            # flow of calc_flow. Guards above ensure the log arguments are valid
            # and the ratio is strictly greater than 1, so the result is positive.
            minutes = (DHW_TANK_VOLUME_L / calc_flow) * math.log(log_ratio)

        # When the compressor is in DHW mode or electric heaters are active the
        # tank is being replenished faster than cold dilution can drain it.  The
        # log model returns a small (or even incorrect) estimate in this case,
        # so floor the raw minutes at one full shower duration to guarantee at
        # least 1.0 shower is reported while reheating is active.
        if dhw_reheating and minutes < calc_shower_duration:
            minutes = calc_shower_duration
            reheating_floor_applied = True
        raw_showers = minutes / calc_shower_duration

        # Determine warmup state BEFORE applying EMA so that transient raw values
        # during the initial 60 s flow-onset window (cold pipe flush, inlet temp
        # spike, etc.) never contaminate the running average.  If the EMA were
        # updated during warmup, an inflated raw value (e.g. raw=11.91) would
        # bias _last_tap_water_cap upward and cause the published estimate to
        # drift downward for many polls after warmup ends.
        is_warmup = False
        warmup_progress = 1.0
        if flow_is_active:
            if self._tap_water_cap_start_time is None:
                self._tap_water_cap_start_time = now
            elapsed_warmup_sec = (now - self._tap_water_cap_start_time).total_seconds()
            if elapsed_warmup_sec < 60:
                is_warmup = True
                warmup_progress = max(0.0, min(1.0, elapsed_warmup_sec / 60.0))
        else:
            # Keep the warmup start time during short within-gap pauses so
            # intermittent tap usage (on/off while rinsing dishes) does not
            # repeatedly restart warmup and cause estimate dips. Reset only
            # when no active session remains (already finalised or never started).
            if self._shower_start_time is None:
                self._tap_water_cap_start_time = None

        # Track when the reheating floor is actively clamping the estimate.
        # On entry: clear the EMA baseline so the first post-floor poll seeds
        # from the real raw value rather than blending from the stale ~1.0.
        # While floored: freeze the EMA state so the baseline does not decay
        # toward 1.0 and cause a slow, pessimistic recovery after reheating ends.
        in_reheating_floor_mode = getattr(
            self, "_tap_water_cap_reheating_floor_mode", False
        )
        if reheating_floor_applied and not in_reheating_floor_mode:
            # First poll where the floor is binding — reset so exit is clean.
            self._last_tap_water_cap = None
            self._tap_water_cap_reheating_floor_mode = True
        elif not reheating_floor_applied and in_reheating_floor_mode:
            # Floor no longer binding — clear flag; _last_tap_water_cap is
            # still None from entry-reset, so recovery seeds from raw.
            self._tap_water_cap_reheating_floor_mode = False

        # Compute the EMA candidate without mutating state yet.  Using the same
        # formula in both warmup and post-warmup ensures the interpolation target
        # during warmup is the value the EMA *would* converge to, so the ramp
        # is meaningful rather than a no-op against the frozen baseline.
        if self._last_tap_water_cap is not None:
            smoothed = (
                DHW_EMA_ALPHA * raw_showers
                + (1 - DHW_EMA_ALPHA) * self._last_tap_water_cap
            )
        else:
            smoothed = raw_showers

        # Advance the EMA state only outside the warmup window and while the
        # reheating floor is not binding.  Updating the EMA while floored would
        # drag _last_tap_water_cap toward 1.0, causing a slow pessimistic
        # recovery once the tank is replenished and reheating ends.
        if not is_warmup and not reheating_floor_applied:
            self._last_tap_water_cap = smoothed
        smoothed_minutes = smoothed * calc_shower_duration

        published_cap = round(smoothed, 1)
        published_minutes = round(smoothed_minutes)

        if is_warmup:
            if self._last_published_tap_water_cap is not None:
                previous_minutes = (
                    self._last_published_tap_water_minutes
                    if self._last_published_tap_water_minutes is not None
                    else round(
                        self._last_published_tap_water_cap * calc_shower_duration
                    )
                )
                published_warmup_cap = round(
                    self._last_published_tap_water_cap
                    + (published_cap - self._last_published_tap_water_cap)
                    * warmup_progress,
                    1,
                )
                published_warmup_minutes = round(
                    previous_minutes
                    + (published_minutes - previous_minutes) * warmup_progress
                )
                values["tap_water_cap"] = published_warmup_cap
                values["tap_water_minutes"] = published_warmup_minutes
            else:
                published_warmup_cap = published_cap
                published_warmup_minutes = published_minutes
                values["tap_water_cap"] = published_warmup_cap
                values["tap_water_minutes"] = published_warmup_minutes
            _LOGGER.debug(
                "Calculated tap_water_cap=%.2f showers (%d min, raw=%.2f, tank=%.1f°C, cold=%.1f°C, flow=%.1f L/min, shower_temp=%.1f°C, shower_dur=%.1f min, warmup=true, warmup_progress=%.2f, reheating=%s)",
                published_warmup_cap,
                published_warmup_minutes,
                raw_showers,
                tank_temp,
                calc_cold,
                calc_flow,
                calc_shower_temp,
                calc_shower_duration,
                warmup_progress,
                dhw_reheating,
            )
            return

        # Publish the derived values rounded to the sensor's display precision
        # so small fluctuations do not cause UI flicker between updates. Keep
        # the EMA state in full precision for subsequent calculations.
        values["tap_water_cap"] = published_cap
        values["tap_water_minutes"] = published_minutes
        self._last_published_tap_water_cap = published_cap
        self._last_published_tap_water_minutes = published_minutes
        _LOGGER.debug(
            "Calculated tap_water_cap=%.2f showers (%d min, raw=%.2f, tank=%.1f°C, cold=%.1f°C, flow=%.1f L/min, shower_temp=%.1f°C, shower_dur=%.1f min, reheating=%s)",
            smoothed,
            published_minutes,
            raw_showers,
            tank_temp,
            calc_cold,
            calc_flow,
            calc_shower_temp,
            calc_shower_duration,
            dhw_reheating,
        )
