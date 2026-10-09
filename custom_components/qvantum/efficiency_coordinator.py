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
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Mapping

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
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


def _range_delta(
    points: list[tuple[int, float]], start_ts: float, end_ts: float
) -> tuple[float | None, float]:
    """Cumulative delta inside an explicit ``[start, end)`` window."""
    window = [(ts, total) for ts, total in points if start_ts <= ts < end_ts]
    coverage_days = len(window) / 24.0
    if len(window) < 2:
        return None, coverage_days
    delta = window[-1][1] - window[0][1]
    if delta < 0.0:
        return None, coverage_days
    return delta, coverage_days


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

    def _main_values(self) -> dict:
        """Current merged values from the main coordinator."""
        data = self._main.data if isinstance(self._main.data, dict) else {}
        values = data.get("values")
        return values if isinstance(values, dict) else {}

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
        dhw_standing_loss = self._update_dhw_standing_loss(now_ts)
        keys = {**counters, **building}
        if not keys:
            return EfficiencySnapshot(
                dhw_standing_loss=dhw_standing_loss, updated_at=now.isoformat()
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
            for key, entity_id in building.items()
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

        return EfficiencySnapshot(
            scop_total=scop_total,
            scop_total_90d=scop_total_90d,
            aux_heat_share=aux_share,
            heat_loss_w_per_k=heat_loss_w_per_k,
            heating_degree_hours=heating_degree_hours,
            weather_normalized_heating=normalized_heating,
            normalized_rising=normalized_rising,
            dhw_standing_loss=dhw_standing_loss,
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
        outdoor = dict(outdoor_points)
        outdoor_window = {ts: value for ts, value in outdoor.items() if ts >= window_start}
        hdh = degree_hours(outdoor_window) if outdoor_window else None

        heating_delta, heating_coverage = _window_delta(
            heating_points, now_ts, BUILDING_WINDOW_DAYS
        )
        normalized = weather_normalized_heating(heating_delta, hdh)

        indoor = dict(indoor_points)
        power = dict(power_points)
        fit_hours = [
            (ts, indoor[ts] - outdoor[ts], power[ts])
            for ts in set(indoor) & set(outdoor) & set(power)
            if ts >= window_start
        ]
        heat_loss = fit_heat_loss(fit_hours, now_ts=int(now_ts))

        previous_hdh = degree_hours(
            {
                ts: value
                for ts, value in outdoor.items()
                if now_ts - 2 * BUILDING_WINDOW_DAYS * 86400.0
                <= ts
                < window_start
            }
        )
        previous_delta, previous_coverage = _range_delta(
            heating_points,
            now_ts - 2 * BUILDING_WINDOW_DAYS * 86400.0,
            window_start,
        )
        previous = weather_normalized_heating(previous_delta, previous_hdh)

        rising: bool | None = None
        if (
            normalized is not None
            and previous is not None
            and heating_coverage >= TREND_MIN_COVERAGE_DAYS
            and previous_coverage >= TREND_MIN_COVERAGE_DAYS
        ):
            rising = normalized >= previous * TREND_RISING_RATIO

        return heat_loss, hdh, normalized, rising, len(outdoor_window) / 24.0

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

    def _dhw_is_idle(self, values: Mapping[str, Any]) -> float | None:
        """Tank temperature when the DHW circuit is idle, else ``None``."""
        tank = _finite(values.get("bt30"))
        if tank is None:
            return None
        flow = _finite(values.get("bf1_l_min"))
        if flow is not None and flow > DHW_IDLE_FLOW_LPM:
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
