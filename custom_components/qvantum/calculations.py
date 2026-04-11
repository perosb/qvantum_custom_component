"""Calculation mixin for Qvantum coordinator derived metrics."""

from __future__ import annotations

import logging
from typing import Any, Callable

from homeassistant.util import dt as dt_util

from .const import (
    DHW_DEFAULT_COLD_TEMP_C,
    DHW_DEFAULT_FLOW_LPM,
    DHW_EMA_ALPHA,
    DHW_FLOW_SNAPSHOT_THRESHOLD_LPM,
    DHW_MIN_TEMPERATURE_DELTA_C,
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
        if flow is not None and flow > DHW_FLOW_SNAPSHOT_THRESHOLD_LPM:
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

        # Resolve effective values: use last observed or fall back to defaults
        cold_temp = (
            self._last_shower_cold_temp
            if self._last_shower_cold_temp is not None
            else DHW_DEFAULT_COLD_TEMP_C
        )
        flow_lpm = (
            self._last_shower_flow_lpm
            if self._last_shower_flow_lpm is not None
            else DHW_DEFAULT_FLOW_LPM
        )

        if tank_temp is None:
            return

        # Use tank_temp (bt30) directly as the hot water temperature.
        # tank_temp decreases as hot water is drawn, so capacity correctly
        # decreases during a shower — unlike bt34 outlet temp which rises as
        # pipes warm up and would cause capacity to appear to increase.
        effective_hot_temp = tank_temp

        if (
            effective_hot_temp <= DHW_SHOWER_TEMP_C
            or cold_temp >= DHW_SHOWER_TEMP_C
        ):
            values["tap_water_cap"] = 0.0
            return

        delta_available = effective_hot_temp - cold_temp
        if delta_available < DHW_MIN_TEMPERATURE_DELTA_C:
            values["tap_water_cap"] = 0.0
            return

        hot_fraction = (DHW_SHOWER_TEMP_C - cold_temp) / delta_available
        hot_fraction = min(1.0, max(0.0, hot_fraction))
        hot_per_min = flow_lpm * hot_fraction
        if hot_per_min <= 0:
            values["tap_water_cap"] = 0.0
            return

        minutes = (
            DHW_TANK_VOLUME_L * DHW_USABLE_FRACTION / hot_per_min
        ) * DHW_TEMP_DROP_FACTOR
        raw_showers = minutes / DHW_SHOWER_DURATION_MIN
        if self._last_tap_water_cap is not None:
            smoothed = (
                DHW_EMA_ALPHA * raw_showers
                + (1 - DHW_EMA_ALPHA) * self._last_tap_water_cap
            )
        else:
            smoothed = raw_showers
        self._last_tap_water_cap = smoothed
        # Publish the derived value rounded to the sensor's display precision
        # so small fluctuations do not cause UI flicker between updates. Keep
        # the EMA state in full precision for subsequent calculations.
        values["tap_water_cap"] = round(smoothed, 1)
        _LOGGER.debug(
            "Calculated tap_water_cap=%.2f showers (raw=%.2f, tank=%.1f°C, cold=%.1f°C, flow=%.1f L/min)",
            smoothed,
            raw_showers,
            tank_temp,
            cold_temp,
            flow_lpm,
        )
