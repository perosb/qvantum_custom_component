"""Calculation mixin for Qvantum coordinator derived metrics."""

from __future__ import annotations

import logging
from typing import Any, Callable

from homeassistant.util import dt as dt_util

from .const import (
    DHW_CAP_HYSTERESIS_C,
    DHW_DEFAULT_COLD_TEMP_C,
    DHW_DEFAULT_FLOW_LPM,
    DHW_EMA_ALPHA,
    DHW_FLOW_SNAPSHOT_THRESHOLD_LPM,
    DHW_HEADROOM_FULL_CAP_C,
    DHW_MIN_SHOWER_DURATION_MIN,
    DHW_MIN_TEMPERATURE_DELTA_C,
    DHW_MAX_SHOWER_HISTORY_SIZE,
    DHW_OUTLET_TEMP_THRESHOLD_DELTA_C,
    DHW_ROLLING_BUFFER_WINDOW_SEC,
    DHW_SHOWER_DURATION_MIN,
    DHW_SHOWER_TEMP_C,
    DHW_TANK_VOLUME_L,
    DHW_TEMP_DROP_FACTOR,
    DHW_USABLE_FRACTION,
    HP_STATUS_HEATING,
)

_LOGGER = logging.getLogger(__name__)


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

    def _calculate_tap_water_cap(self, values: dict) -> None:
        """Derive tap_water_cap (showers remaining) from tank and flow sensor data.

        bt30 (tank temp) is used directly as the effective hot water temperature,
        since it represents current stored energy and decreases as hot water is
        drawn — making capacity correctly decrease during a shower.
        bt33 (cold water in) and bf1_l_min (flow) are snapshotted during active
        flow and EMA-smoothed to prevent brief transients from dominating.
        Formula: hot_fraction = (shower_temp - cold) / (tank_temp - cold),
                 hot_per_min = flow * hot_fraction,
                 minutes = (volume * usable_fraction / hot_per_min) * drop_factor,
                 showers = minutes / shower_duration_min.
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

        if flow_is_active:
            # Record when this flow event started (used for duration learning).
            if self._shower_start_time is None:
                self._shower_start_time = now

            # Phase 1: maintain a rolling 60-second buffer of flow/cold readings.
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
            prior_flow = (
                self._last_shower_flow_lpm
                if self._last_shower_flow_lpm is not None
                else DHW_DEFAULT_FLOW_LPM
            )
            self._last_shower_flow_lpm = (
                DHW_EMA_ALPHA * flow + (1 - DHW_EMA_ALPHA) * prior_flow
            )

            # Learn shower temperature from outlet sensor bt34.
            # Only update when the outlet clearly carries warm water (> cold + threshold)
            # to avoid polluting the EMA with the initial cold pipe-flush phase.
            outlet_temp = values.get("bt34")
            cold_for_guard = cold if cold is not None else DHW_DEFAULT_COLD_TEMP_C
            if (
                outlet_temp is not None
                and outlet_temp > cold_for_guard + DHW_OUTLET_TEMP_THRESHOLD_DELTA_C
            ):
                prior_shower_temp = (
                    self._last_shower_temp_c
                    if self._last_shower_temp_c is not None
                    else DHW_SHOWER_TEMP_C
                )
                self._last_shower_temp_c = (
                    DHW_EMA_ALPHA * outlet_temp
                    + (1 - DHW_EMA_ALPHA) * prior_shower_temp
                )

            # Phase 2: accumulate per-event samples for end-of-shower statistics.
            self._shower_event_samples.append((ts, flow, cold, outlet_temp))
        else:
            # Flow just stopped — compute shower duration and update the EMA.
            if self._shower_start_time is not None:
                duration_min = (now - self._shower_start_time).total_seconds() / 60.0
                if (
                    duration_min >= DHW_MIN_SHOWER_DURATION_MIN
                ):  # Ignore brief draws (tap, filling kettle, etc.)
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

                    # Phase 2: record completed shower event to history.
                    if self._shower_event_samples:
                        avg_flow = sum(s[1] for s in self._shower_event_samples) / len(
                            self._shower_event_samples
                        )
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
                        water_used_l = avg_flow * duration_min
                        event = {
                            "start": self._shower_start_time.isoformat(),
                            "end": now.isoformat(),
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
                        if (
                            len(self._shower_event_history)
                            > DHW_MAX_SHOWER_HISTORY_SIZE
                        ):
                            self._shower_event_history.pop(0)
                        _LOGGER.info(
                            "Shower event: duration=%.1f min, avg_flow=%.2f L/min, "
                            "avg_cold=%.1f\u00b0C, avg_outlet=%.1f\u00b0C, water_used=%.1f L",
                            duration_min,
                            avg_flow,
                            avg_cold,
                            avg_outlet_temp if avg_outlet_temp is not None else 0.0,
                            water_used_l,
                        )
                self._shower_start_time = None
            self._flow_rolling_buffer.clear()
            self._shower_event_samples.clear()

        # Resolve values for the capacity calculation:
        # - During active flow: use raw current readings so the estimate reflects
        #   real-time conditions without EMA lag from the default-seeded snapshot.
        # - No active flow: fall back to EMA snapshot (or defaults if never seen).
        if flow_is_active:
            # Phase 1: use 60-second rolling buffer means for flow and cold so
            # brief transients do not distort the capacity estimate.
            calc_flow = sum(s[1] for s in self._flow_rolling_buffer) / len(
                self._flow_rolling_buffer
            )
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
            # During flow, calculate against the learned EMA shower temperature
            # rather than the instantaneous outlet reading to avoid abrupt
            # estimate swings from short-lived bt34 spikes.
            calc_shower_temp = (
                self._last_shower_temp_c
                if self._last_shower_temp_c is not None
                else DHW_SHOWER_TEMP_C
            )
        else:
            calc_cold = (
                self._last_shower_cold_temp
                if self._last_shower_cold_temp is not None
                else DHW_DEFAULT_COLD_TEMP_C
            )
            calc_flow = (
                self._last_shower_flow_lpm
                if self._last_shower_flow_lpm is not None
                else DHW_DEFAULT_FLOW_LPM
            )
            # After flow use the EMA snapshot (learned preferred shower temperature).
            calc_shower_temp = (
                self._last_shower_temp_c
                if self._last_shower_temp_c is not None
                else DHW_SHOWER_TEMP_C
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
                or cold_ge_shower_temp
            )
        else:
            should_force_zero = (
                effective_hot_temp <= calc_shower_temp - DHW_CAP_HYSTERESIS_C
                or cold_ge_shower_temp
            )

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
                "Calculated tap_water_cap=0.00 showers (0 min, reason=%s, tank=%.1f°C, cold=%.1f°C, flow=%.1f L/min, shower_temp=%.1f°C, shower_dur=%.1f min, hysteresis=%.1f°C, zero_mode=true)",
                reason,
                effective_hot_temp,
                calc_cold,
                calc_flow,
                calc_shower_temp,
                calc_shower_duration,
                DHW_CAP_HYSTERESIS_C,
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

        hot_fraction = (calc_shower_temp - calc_cold) / delta_available
        hot_fraction = min(1.0, max(0.0, hot_fraction))
        hot_per_min = calc_flow * hot_fraction
        if hot_per_min <= 0:
            values["tap_water_cap"] = 0.0
            values["tap_water_minutes"] = 0
            _LOGGER.debug(
                "Calculated tap_water_cap=0.00 showers (0 min, reason=non_positive_hot_per_min, tank=%.1f°C, cold=%.1f°C, flow=%.1f L/min, shower_temp=%.1f°C, hot_fraction=%.3f)",
                effective_hot_temp,
                calc_cold,
                calc_flow,
                calc_shower_temp,
                hot_fraction,
            )
            return

        minutes = (
            DHW_TANK_VOLUME_L * DHW_USABLE_FRACTION / hot_per_min
        ) * DHW_TEMP_DROP_FACTOR
        # Reduce optimistic estimates when tank temperature is only slightly
        # above the target shower temperature.
        headroom_c = max(effective_hot_temp - calc_shower_temp, 0.0)
        headroom_factor = min(headroom_c / DHW_HEADROOM_FULL_CAP_C, 1.0)
        minutes *= headroom_factor
        raw_showers = minutes / calc_shower_duration
        if self._last_tap_water_cap is not None:
            smoothed = (
                DHW_EMA_ALPHA * raw_showers
                + (1 - DHW_EMA_ALPHA) * self._last_tap_water_cap
            )
        else:
            smoothed = raw_showers
        self._last_tap_water_cap = smoothed
        smoothed_minutes = smoothed * calc_shower_duration

        # When flow is active, use a 60 s warmup window so the EMA can stabilise
        # on real inlet conditions. During warmup, if a value was previously
        # published, linearly ramp from the previous published value toward the
        # newly calculated value instead of hard-switching between them.
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
            # Reset the window so the next flow onset triggers a fresh 60 s hold.
            self._tap_water_cap_start_time = None

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
                "Calculated tap_water_cap=%.2f showers (%d min, raw=%.2f, tank=%.1f°C, cold=%.1f°C, flow=%.1f L/min, shower_temp=%.1f°C, shower_dur=%.1f min, headroom=%.1f°C, headroom_factor=%.2f, warmup=true, warmup_progress=%.2f)",
                published_warmup_cap,
                published_warmup_minutes,
                raw_showers,
                tank_temp,
                calc_cold,
                calc_flow,
                calc_shower_temp,
                calc_shower_duration,
                headroom_c,
                headroom_factor,
                warmup_progress,
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
            "Calculated tap_water_cap=%.2f showers (%d min, raw=%.2f, tank=%.1f°C, cold=%.1f°C, flow=%.1f L/min, shower_temp=%.1f°C, shower_dur=%.1f min, headroom=%.1f°C, headroom_factor=%.2f)",
            smoothed,
            published_minutes,
            raw_showers,
            tank_temp,
            calc_cold,
            calc_flow,
            calc_shower_temp,
            calc_shower_duration,
            headroom_c,
            headroom_factor,
        )
