"""Rolling efficiency analytics (SCOP, aux share, building and DHW losses).

Transport-agnostic: it reads the recorder's hourly long-term statistics for the
metrics the main coordinator exposes. A missing recorder, missing entities, or
a fresh install simply yields ``None`` values; the heat-pump poll is never
affected and nothing here writes to the pump.

Instantaneous COP lives on the main coordinator (``calculations._calculate_cop``)
because it needs the poll-to-poll counter deltas. This coordinator owns the
multi-week windows and the slow DHW standing-loss state machine.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Mapping

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .building import degree_hours, fit_heat_loss, weather_normalized_heating
from .const import (
    DHW_COMPRESSOR_STATE_HOT_WATER,
    DHW_TANK_VOLUME_L,
    DOMAIN,
    HP_STATUS_HOT_WATER,
)
from .dhw_loss import blend, standing_loss_kwh_per_day
from .efficiency import aux_heat_share, scop_from_counters
from .open_meteo import OpenMeteoError, fetch_forecast, fetch_ghi_history
from .plant_analytics import (
    dhw_heat_meter_power_w,
    duty_cycle,
    health_grade,
    mean_while_running,
    starts_per_hour,
)
from .solar_gain import SolarModel, build_samples, fit_solar_model, smooth_ghi
from .statistics import (
    async_statistics_during_period,
    resolve_indoor_metric_key,
    resolve_statistic_entity_ids,
)

_LOGGER = logging.getLogger(__name__)

#: Statistics are hourly; half an hour is often enough for a rolling average.
UPDATE_INTERVAL = timedelta(minutes=30)
#: Rolling window exposed as the SCOP sensor state.
SCOP_WINDOW_DAYS = 30
#: Longer sibling window exposed as an attribute (same fetch, no extra cost).
SCOP_LONG_WINDOW_DAYS = 90
#: Minimum weeks of history before a window is published, so a fresh install
#: does not report a one-day sample as a seasonal average.
MIN_COVERAGE_DAYS = 7.0
#: Energy counter keys present in both transports.
COUNTER_KEYS = ("heatingenergy", "dhwenergy", "compressorenergy", "additionalenergy")
#: Outdoor / indoor / power series behind the building figures.
BUILDING_KEYS = ("bt1", "bt2", "room_temp_external", "room_temp_ext", "heatingpower")
#: Compressor cycling counters (Modbus-only metrics; absent in cloud).
CYCLING_KEYS = ("compressor_starts", "compressor_run_time")
#: Rolling window for the cycling rate and operating point.
CYCLING_WINDOW_DAYS = 1
#: Operating-point series (hourly means while the unit is running).
OPERATING_KEYS = ("compressormeasuredspeed", "compressor_power", "fanrpm")
#: Minimum observed hours before the duty cycle is published.
MIN_DUTY_CYCLE_HOURS = 12.0
#: Secondary-side flow / temperatures behind the DHW heat-meter check.
DHW_METER_KEYS = ("bf1_l_min", "bt33", "bt34")
#: Window for the heat-meter plausibility comparison.
DHW_METER_WINDOW_DAYS = 7
#: Minimum matched draw hours and metered energy before a deviation is published.
MIN_DHW_METER_HOURS = 6
MIN_DHW_METER_KWH = 0.5
#: |estimated - metered| above this share of metered is a warning.
HEAT_METER_WARNING_PERCENT = 20.0
#: Window for degree hours, normalized heating and the heat-loss fit.
BUILDING_WINDOW_DAYS = 30
#: Both the current and the previous window need this coverage for a trend.
TREND_MIN_COVERAGE_DAYS = 14.0
#: Normalized consumption at or above this factor of the previous window is rising.
TREND_RISING_RATIO = 1.15
#: Flow at or below this is a closed DHW circuit, not a draw.
DHW_IDLE_FLOW_LPM = 0.1
#: Tank rise between samples that means reheating started, not sensor noise.
DHW_TEMP_NOISE_K = 0.2
#: Forecast hours fetched from Open-Meteo for the solar exposure.
SOLAR_FORECAST_FUTURE_HOURS = 12
#: Days of hourly history behind the solar-gain fit; mirrors the curve
#: coordinator's calibration window and minimum sample count so both models
#: are identified the same way.
SOLAR_FIT_WINDOW_DAYS = 60
MIN_SOLAR_FIT_SAMPLES = 72
#: A solar fit is refreshed at most this often (the archive changes daily).
SOLAR_FIT_REFRESH_HOURS = 24.0


@dataclass(frozen=True)
class SolarSnapshot:
    """Current irradiance, its forecast peak, and the modelled solar gain."""

    ghi_now: float | None = None
    ghi_peak: float | None = None
    peak_in_hours: float | None = None
    gain_w: float | None = None


@dataclass(frozen=True)
class EfficiencySnapshot:
    """Rolling efficiency figures; ``None`` where the data does not support one."""

    scop_total: float | None = None
    scop_total_90d: float | None = None
    aux_heat_share: float | None = None
    heat_loss_w_per_k: float | None = None
    heating_degree_hours: float | None = None
    weather_normalized_heating: float | None = None
    normalized_rising: bool | None = None
    dhw_standing_loss: float | None = None
    compressor_starts_per_hour: float | None = None
    compressor_run_hours_24h: float | None = None
    compressor_speed_avg: float | None = None
    compressor_power_avg: float | None = None
    compressor_duty_cycle: float | None = None
    exhaust_fan_speed_avg: float | None = None
    dhw_heat_meter_deviation: float | None = None
    dhw_heat_meter_warning: bool | None = None
    dhw_heat_meter_estimated_kwh: float | None = None
    dhw_heat_meter_metered_kwh: float | None = None
    dhw_heat_meter_hours: int = 0
    solar_ghi_now: float | None = None
    solar_ghi_peak: float | None = None
    solar_ghi_peak_in_hours: float | None = None
    solar_gain_now_w: float | None = None
    efficiency_health_grade: str | None = None
    efficiency_health_score: float | None = None
    efficiency_health_coverage: float = 0.0
    efficiency_health_components: dict[str, float] = field(default_factory=dict)
    coverage_days: float = 0.0
    building_coverage_days: float = 0.0
    updated_at: str | None = None


def _finite(value: object) -> float | None:
    """Coerce to a finite float, rejecting bools and non-numeric input."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return numeric if math.isfinite(numeric) else None


def _stat_series(rows: list[Any], field: str) -> list[tuple[int, float]]:
    """Validated ``(hour_ts, value)`` points for one statistics column."""
    points: list[tuple[int, float]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = row.get(field)
        start = row.get("start")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if isinstance(start, bool) or not isinstance(start, (int, float)):
            continue
        points.append((int(start), float(value)))
    points.sort()
    return points


def _cumulative_series(rows: list[Any]) -> list[tuple[int, float]]:
    """Validated ``(hour_ts, cumulative sum)`` points, sorted by time."""
    return _stat_series(rows, "sum")


def _window_delta(
    points: list[tuple[int, float]], now_ts: float, days: int
) -> tuple[float | None, float]:
    """First-to-last cumulative delta inside the window, plus coverage in days.

    The recorder's ``sum`` is monotonic across meter resets, so first-to-last
    is a safe delta. ``None`` when fewer than two hours fall in the window.
    """
    cutoff = now_ts - days * 86400.0
    window = [(ts, total) for ts, total in points if ts >= cutoff]
    coverage_days = len(window) / 24.0
    if len(window) < 2:
        return None, coverage_days
    delta = window[-1][1] - window[0][1]
    if delta < 0.0:
        return None, coverage_days
    return delta, coverage_days


def _aligned_window(
    heating_points: list[tuple[int, float]],
    outdoor: Mapping[int, float],
    start_ts: float,
    end_ts: float,
) -> tuple[float | None, dict[int, float], float]:
    """Energy delta and outdoor hours over exactly the same observed hours.

    Normalizing energy over a different period than the degree hours silently
    inflates the result (and can trip the rising trend): if the outdoor series
    is missing hours the energy series has, the numerator covers more days
    than the denominator. Sum the hourly energy deltas only for hours where
    both the current and the previous energy sample exist, and keep the
    outdoor value for those same hours.
    """
    heating = dict(heating_points)
    aligned_outdoor: dict[int, float] = {}
    energy = 0.0
    for ts in sorted(outdoor):
        if not (start_ts <= ts < end_ts):
            continue
        previous = heating.get(ts - 3600)
        current = heating.get(ts)
        if previous is None or current is None:
            continue
        delta = current - previous
        if delta < 0.0:
            # A counter reset inside the hour: the delta is unknown, skip it
            # rather than letting a negative value cancel real consumption.
            continue
        energy += delta
        aligned_outdoor[ts] = outdoor[ts]

    coverage_days = len(aligned_outdoor) / 24.0
    if len(aligned_outdoor) < 2:
        return None, aligned_outdoor, coverage_days
    return energy, aligned_outdoor, coverage_days


class QvantumEfficiencyCoordinator(DataUpdateCoordinator[EfficiencySnapshot]):
    """Rolling efficiency analytics for one Qvantum entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        main_coordinator: Any,
    ) -> None:
        self.config_entry = config_entry
        self._main = main_coordinator
        # DHW standing-loss window state (see _update_dhw_standing_loss).
        self._dhw_idle_start: tuple[float, float] | None = None
        self._dhw_idle_last: tuple[float, float] | None = None
        self._dhw_standing_loss: float | None = None
        # Solar state: the session is created lazily, the model is fitted by
        # this coordinator so the gain works without the Modbus-only curve one.
        self._session: Any = None
        self._solar_model: SolarModel | None = None
        self._last_solar_fit_ts: float | None = None
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN} Efficiency ({config_entry.unique_id})",
            update_method=self._async_update_data,
            update_interval=UPDATE_INTERVAL,
        )

    @property
    def device_id(self) -> str | None:
        """Device id of the main coordinator, when known."""
        return getattr(self._main, "device_id", None)

    @property
    def session(self) -> Any:
        """Borrow Home Assistant's aiohttp session; never owned here."""
        if self._session is None:
            self._session = async_get_clientsession(self.hass)
        return self._session

    def _main_values(self) -> dict:
        """Current merged values from the main coordinator."""
        data = self._main.data if isinstance(self._main.data, dict) else {}
        values = data.get("values")
        return values if isinstance(values, dict) else {}

    async def _async_solar(
        self,
        mean_series: Mapping[str, list[tuple[int, float]]],
        now_ts: float,
    ) -> SolarSnapshot:
        """Current smoothed irradiance, its forecast peak, and modelled gain.

        GHI comes from Open-Meteo and needs no hardware, so it is available to
        every user with a location. The gain needs a fitted ``SolarModel``;
        this coordinator fits and owns its own so the figure works in cloud
        mode, where the adaptive-curve coordinator does not exist. Missing
        location or forecast simply yields ``None`` values.
        """
        config = getattr(self.hass, "config", None)
        latitude = _finite(getattr(config, "latitude", None))
        longitude = _finite(getattr(config, "longitude", None))
        if latitude is None or longitude is None:
            return SolarSnapshot()

        ghi_now = None
        ghi_peak = None
        peak_in_hours = None
        try:
            forecast = await fetch_forecast(
                self.session,
                latitude,
                longitude,
                future_hours=SOLAR_FORECAST_FUTURE_HOURS,
            )
        except OpenMeteoError as err:
            _LOGGER.debug("Solar forecast unavailable: %s", err)
        else:
            ghi_by_hour = forecast.ghi_by_hour()
            ghi_now = smooth_ghi(ghi_by_hour, int(now_ts))
            future = [(ts, ghi) for ts, ghi in ghi_by_hour.items() if ts > now_ts]
            if future:
                peak_ts, ghi_peak = max(future, key=lambda item: item[1])
                peak_in_hours = (peak_ts - now_ts) / 3600.0

        await self._async_refresh_solar_model(mean_series, now_ts, latitude, longitude)
        gain = None
        if self._solar_model is not None and ghi_now is not None:
            gain = self._solar_model.solar_gain_w(ghi_now)
        return SolarSnapshot(
            ghi_now=ghi_now,
            ghi_peak=ghi_peak,
            peak_in_hours=peak_in_hours,
            gain_w=gain,
        )

    async def _async_refresh_solar_model(
        self,
        mean_series: Mapping[str, list[tuple[int, float]]],
        now_ts: float,
        latitude: float,
        longitude: float,
    ) -> None:
        """Fit or refresh the solar-gain model from recorder series + GHI.

        Throttled: a failed or skipped attempt still records its timestamp so
        a fresh install does not re-fetch 60 days of archive every 30 minutes.
        """
        if (
            self._last_solar_fit_ts is not None
            and now_ts - self._last_solar_fit_ts < SOLAR_FIT_REFRESH_HOURS * 3600.0
        ):
            return
        self._last_solar_fit_ts = now_ts

        power = dict(mean_series.get("heatingpower", []))
        outdoor = dict(mean_series.get("bt1", []))
        indoor_key = resolve_indoor_metric_key(
            {key: key for key in mean_series}, self._main_values()
        )
        indoor = dict(mean_series.get(indoor_key, [])) if indoor_key else {}
        if not power or not outdoor or not indoor:
            return

        try:
            today = dt_util.utcnow().date()
            ghi_history = await fetch_ghi_history(
                self.session,
                latitude,
                longitude,
                today - timedelta(days=SOLAR_FIT_WINDOW_DAYS),
                today,
            )
        except OpenMeteoError as err:
            _LOGGER.debug("Solar history unavailable: %s", err)
            return

        samples = build_samples(power, indoor, outdoor, ghi_history)
        if len(samples) < MIN_SOLAR_FIT_SAMPLES:
            _LOGGER.debug(
                "Solar fit skipped: %s samples < %s",
                len(samples),
                MIN_SOLAR_FIT_SAMPLES,
            )
            return
        model = fit_solar_model(samples, now_ts=int(now_ts))
        if not model.valid:
            _LOGGER.debug("Solar fit rejected: %s", model.notes)
            return
        self._solar_model = model
        _LOGGER.info(
            "Efficiency solar model fitted: a=%.1f W/K b=%.3f m² trust=%.2f",
            model.a_w_per_k,
            model.b_m2,
            model.trust,
        )

    async def _async_update_data(self) -> EfficiencySnapshot:
        """Never fail: degrade to the last good snapshot on any error."""
        try:
            return await self._async_compute_snapshot()
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 — analytics must stay alive
            _LOGGER.debug("Efficiency update failed: %s", err)
            return self.data or EfficiencySnapshot()

    async def _async_compute_snapshot(self) -> EfficiencySnapshot:
        now = dt_util.utcnow()
        now_ts = now.timestamp()
        counters = resolve_statistic_entity_ids(self.hass, self.device_id, COUNTER_KEYS)
        building = resolve_statistic_entity_ids(self.hass, self.device_id, BUILDING_KEYS)
        cycling = resolve_statistic_entity_ids(self.hass, self.device_id, CYCLING_KEYS)
        operating = resolve_statistic_entity_ids(
            self.hass, self.device_id, OPERATING_KEYS
        )
        dhw_meter = resolve_statistic_entity_ids(
            self.hass, self.device_id, DHW_METER_KEYS
        )
        dhw_standing_loss = self._update_dhw_standing_loss(now_ts)
        keys = {**counters, **building, **cycling, **operating, **dhw_meter}
        if not keys:
            solar = await self._async_solar({}, now_ts)
            return EfficiencySnapshot(
                dhw_standing_loss=dhw_standing_loss,
                solar_ghi_now=solar.ghi_now,
                solar_ghi_peak=solar.ghi_peak,
                solar_ghi_peak_in_hours=solar.peak_in_hours,
                solar_gain_now_w=solar.gain_w,
                updated_at=now.isoformat(),
            )

        rows = await async_statistics_during_period(
            self.hass,
            set(keys.values()),
            now - timedelta(days=SCOP_LONG_WINDOW_DAYS),
            types={"sum", "mean"},
        )
        sum_series = {
            key: _cumulative_series(rows.get(entity_id, []))
            for key, entity_id in counters.items()
        }
        mean_series = {
            key: _stat_series(rows.get(entity_id, []), "mean")
            for key, entity_id in {**building, **operating, **dhw_meter}.items()
        }
        cycling_series = {
            key: _cumulative_series(rows.get(entity_id, []))
            for key, entity_id in cycling.items()
        }

        short_deltas, short_coverage = self._window(
            sum_series, now_ts, SCOP_WINDOW_DAYS
        )
        long_deltas, long_coverage = self._window(
            sum_series, now_ts, SCOP_LONG_WINDOW_DAYS
        )

        scop_total = None
        scop_total_90d = None
        aux_share = None
        if short_coverage >= MIN_COVERAGE_DAYS:
            scop_total = scop_from_counters(
                short_deltas.get("heatingenergy"),
                short_deltas.get("dhwenergy"),
                short_deltas.get("compressorenergy"),
                short_deltas.get("additionalenergy"),
            )
            aux_share = aux_heat_share(
                short_deltas.get("additionalenergy"),
                short_deltas.get("compressorenergy"),
            )
        if long_coverage >= MIN_COVERAGE_DAYS:
            scop_total_90d = scop_from_counters(
                long_deltas.get("heatingenergy"),
                long_deltas.get("dhwenergy"),
                long_deltas.get("compressorenergy"),
                long_deltas.get("additionalenergy"),
            )

        (
            heat_loss_w_per_k,
            heating_degree_hours,
            normalized_heating,
            normalized_rising,
            building_coverage,
        ) = self._building_metrics(
            sum_series.get("heatingenergy", []), mean_series, now_ts
        )

        starts_delta, _ = _window_delta(
            cycling_series.get("compressor_starts", []),
            now_ts,
            CYCLING_WINDOW_DAYS,
        )
        run_hours_delta, run_coverage_days = _window_delta(
            cycling_series.get("compressor_run_time", []),
            now_ts,
            CYCLING_WINDOW_DAYS,
        )
        cycling_rate = starts_per_hour(starts_delta, run_hours_delta)
        operating_start = now_ts - CYCLING_WINDOW_DAYS * 86400.0
        speed_avg = mean_while_running(
            mean_series.get("compressormeasuredspeed", []),
            start_ts=operating_start,
            end_ts=now_ts,
        )
        power_avg = mean_while_running(
            mean_series.get("compressor_power", []),
            start_ts=operating_start,
            end_ts=now_ts,
        )
        fan_avg = mean_while_running(
            mean_series.get("fanrpm", []),
            start_ts=operating_start,
            end_ts=now_ts,
        )
        duty = None
        if run_coverage_days * 24.0 >= MIN_DUTY_CYCLE_HOURS:
            duty = duty_cycle(run_hours_delta, run_coverage_days * 24.0)
        (
            dhw_meter_deviation,
            dhw_meter_warning,
            dhw_meter_estimated_kwh,
            dhw_meter_metered_kwh,
            dhw_meter_hours,
        ) = self._dhw_heat_meter_metrics(mean_series, sum_series, now_ts)
        solar = await self._async_solar(mean_series, now_ts)
        health = health_grade(
            {
                "scop": scop_total,
                "aux_share": aux_share,
                "cycling": cycling_rate,
            }
        )

        return EfficiencySnapshot(
            scop_total=scop_total,
            scop_total_90d=scop_total_90d,
            aux_heat_share=aux_share,
            heat_loss_w_per_k=heat_loss_w_per_k,
            heating_degree_hours=heating_degree_hours,
            weather_normalized_heating=normalized_heating,
            normalized_rising=normalized_rising,
            dhw_standing_loss=dhw_standing_loss,
            compressor_starts_per_hour=cycling_rate,
            compressor_run_hours_24h=run_hours_delta,
            compressor_speed_avg=speed_avg,
            compressor_power_avg=power_avg,
            compressor_duty_cycle=duty,
            exhaust_fan_speed_avg=fan_avg,
            dhw_heat_meter_deviation=dhw_meter_deviation,
            dhw_heat_meter_warning=dhw_meter_warning,
            dhw_heat_meter_estimated_kwh=dhw_meter_estimated_kwh,
            dhw_heat_meter_metered_kwh=dhw_meter_metered_kwh,
            dhw_heat_meter_hours=dhw_meter_hours,
            solar_ghi_now=solar.ghi_now,
            solar_ghi_peak=solar.ghi_peak,
            solar_ghi_peak_in_hours=solar.peak_in_hours,
            solar_gain_now_w=solar.gain_w,
            efficiency_health_grade=health.letter,
            efficiency_health_score=health.score,
            efficiency_health_coverage=health.coverage,
            efficiency_health_components=health.components,
            coverage_days=short_coverage,
            building_coverage_days=building_coverage,
            updated_at=now.isoformat(),
        )

    def _building_metrics(
        self,
        heating_points: list[tuple[int, float]],
        mean_series: Mapping[str, list[tuple[int, float]]],
        now_ts: float,
    ) -> tuple[float | None, float | None, float | None, bool | None, float]:
        """Heat-loss coefficient, degree hours, normalized use and its trend."""
        indoor_key = resolve_indoor_metric_key(
            {key: key for key in mean_series}, self._main_values()
        )
        outdoor_points = mean_series.get("bt1", [])
        indoor_points = mean_series.get(indoor_key, []) if indoor_key else []
        power_points = mean_series.get("heatingpower", [])

        window_start = now_ts - BUILDING_WINDOW_DAYS * 86400.0
        previous_start = window_start - BUILDING_WINDOW_DAYS * 86400.0
        outdoor = dict(outdoor_points)

        energy, aligned_outdoor, building_coverage = _aligned_window(
            heating_points, outdoor, window_start, now_ts
        )
        # A fresh install must not read as a 30-day figure from a day or two
        # of rows; the same minimum as the counters applies here.
        hdh = (
            degree_hours(aligned_outdoor)
            if building_coverage >= MIN_COVERAGE_DAYS
            else None
        )
        normalized = weather_normalized_heating(energy, hdh)

        indoor = dict(indoor_points)
        power = dict(power_points)
        fit_hours = [
            (ts, indoor[ts] - outdoor[ts], power[ts])
            for ts in set(indoor) & set(outdoor) & set(power)
            if ts >= window_start
        ]
        heat_loss = (
            fit_heat_loss(fit_hours, now_ts=int(now_ts))
            if building_coverage >= MIN_COVERAGE_DAYS
            else None
        )

        previous_energy, previous_outdoor, previous_coverage = _aligned_window(
            heating_points, outdoor, previous_start, window_start
        )
        previous_hdh = (
            degree_hours(previous_outdoor)
            if previous_coverage >= TREND_MIN_COVERAGE_DAYS
            else None
        )
        previous = weather_normalized_heating(previous_energy, previous_hdh)

        rising: bool | None = None
        if (
            normalized is not None
            and previous is not None
            and building_coverage >= TREND_MIN_COVERAGE_DAYS
            and previous_coverage >= TREND_MIN_COVERAGE_DAYS
        ):
            rising = normalized >= previous * TREND_RISING_RATIO

        return heat_loss, hdh, normalized, rising, building_coverage

    @staticmethod
    def _window(
        series: Mapping[str, list[tuple[int, float]]], now_ts: float, days: int
    ) -> tuple[dict[str, float | None], float]:
        """Deltas for every counter in the window, with the weakest coverage."""
        deltas: dict[str, float | None] = {}
        coverages: list[float] = []
        for key in COUNTER_KEYS:
            delta, coverage_days = _window_delta(series.get(key, []), now_ts, days)
            deltas[key] = delta
            coverages.append(coverage_days)
        return deltas, min(coverages) if coverages else 0.0

    def _dhw_heat_meter_metrics(
        self,
        mean_series: Mapping[str, list[tuple[int, float]]],
        sum_series: Mapping[str, list[tuple[int, float]]],
        now_ts: float,
    ) -> tuple[float | None, bool | None, float | None, float | None, int]:
        """Compare flow×ΔT DHW heat against the metered dhwenergy counter.

        Both sides are integrated over exactly the same hours: an hour needs
        the flow/temperature samples and the metered counter delta, and only
        hours with a real draw qualify. Below a minimum of matched hours and
        metered energy no deviation is published, so one short draw cannot
        produce a wild percentage.
        """
        window_start = now_ts - DHW_METER_WINDOW_DAYS * 86400.0
        counter = dict(sum_series.get("dhwenergy", []))
        flow = dict(mean_series.get("bf1_l_min", []))
        hot = dict(mean_series.get("bt34", []))
        cold = dict(mean_series.get("bt33", []))

        estimated_kwh = 0.0
        metered_kwh = 0.0
        hours = 0
        for ts in sorted(set(flow) & set(hot) & set(cold)):
            if ts < window_start or flow[ts] <= DHW_IDLE_FLOW_LPM:
                continue
            power_w = dhw_heat_meter_power_w(flow[ts], hot[ts], cold[ts])
            if power_w is None:
                continue
            previous = counter.get(ts - 3600)
            current = counter.get(ts)
            if previous is None or current is None:
                continue
            delta = current - previous
            if delta < 0.0:
                continue
            estimated_kwh += power_w / 1000.0
            metered_kwh += delta
            hours += 1

        if hours < MIN_DHW_METER_HOURS or metered_kwh < MIN_DHW_METER_KWH:
            return None, None, estimated_kwh, metered_kwh, hours
        deviation = (estimated_kwh - metered_kwh) / metered_kwh * 100.0
        warning = abs(deviation) >= HEAT_METER_WARNING_PERCENT
        return deviation, warning, estimated_kwh, metered_kwh, hours

    def _dhw_is_idle(self, values: Mapping[str, Any]) -> float | None:
        """Tank temperature when the DHW circuit is idle, else ``None``."""
        tank = _finite(values.get("bt30"))
        if tank is None:
            return None
        flow = _finite(values.get("bf1_l_min"))
        # A missing flow metric must not count as "no draw": without it a hot
        # water draw during the window is indistinguishable from tank cooling
        # and the standing loss would be silently inflated.
        if flow is None or flow > DHW_IDLE_FLOW_LPM:
            return None
        if values.get("hp_status") == HP_STATUS_HOT_WATER:
            return None
        if values.get("compressor_state") == DHW_COMPRESSOR_STATE_HOT_WATER:
            return None
        return tank

    def _update_dhw_standing_loss(self, now_ts: float) -> float | None:
        """Track free-cooling windows and EMA the accepted daily loss."""
        tank = self._dhw_is_idle(self._main_values())
        current = getattr(self, "_dhw_standing_loss", None)
        if tank is None:
            self._close_dhw_window()
            return getattr(self, "_dhw_standing_loss", None)

        start = getattr(self, "_dhw_idle_start", None)
        last = getattr(self, "_dhw_idle_last", None)
        if start is None:
            self._dhw_idle_start = (now_ts, tank)
            self._dhw_idle_last = (now_ts, tank)
            return current
        if last is not None and tank > last[1] + DHW_TEMP_NOISE_K:
            # Reheating began between samples; keep the last measured fall.
            self._close_dhw_window()
            return getattr(self, "_dhw_standing_loss", None)
        self._dhw_idle_last = (now_ts, tank)
        return current

    def _close_dhw_window(self) -> None:
        """Finalize the open idle window; discard windows too short to trust."""
        start = getattr(self, "_dhw_idle_start", None)
        last = getattr(self, "_dhw_idle_last", None)
        self._dhw_idle_start = None
        self._dhw_idle_last = None
        if start is None or last is None:
            return
        sample = standing_loss_kwh_per_day(
            start[1] - last[1],
            (last[0] - start[0]) / 3600.0,
            DHW_TANK_VOLUME_L,
        )
        if sample is not None:
            self._dhw_standing_loss = blend(
                getattr(self, "_dhw_standing_loss", None), sample
            )
