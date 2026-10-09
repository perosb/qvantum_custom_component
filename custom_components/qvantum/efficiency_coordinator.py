"""Rolling efficiency analytics (SCOP, auxiliary-heat share).

Transport-agnostic: it reads the recorder's hourly long-term statistics for the
energy counters the main coordinator exposes in both HTTP and Modbus mode. A
missing recorder, missing entities, or a fresh install simply yields ``None``
values; the heat-pump poll is never affected and nothing here writes to the
pump.

Instantaneous COP lives on the main coordinator (``calculations._calculate_cop``)
because it needs the poll-to-poll counter deltas; this coordinator owns the
multi-week windows only.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .efficiency import aux_heat_share, scop_from_counters
from .statistics import async_statistics_during_period, resolve_statistic_entity_ids

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


@dataclass(frozen=True)
class EfficiencySnapshot:
    """Rolling efficiency figures; ``None`` where the data does not support one."""

    scop_total: float | None = None
    scop_total_90d: float | None = None
    aux_heat_share: float | None = None
    coverage_days: float = 0.0
    updated_at: str | None = None


def _cumulative_series(rows: list[Any]) -> list[tuple[int, float]]:
    """Validated ``(hour_ts, cumulative sum)`` points, sorted by time."""
    points: list[tuple[int, float]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        total = row.get("sum")
        start = row.get("start")
        if isinstance(total, bool) or not isinstance(total, (int, float)):
            continue
        if isinstance(start, bool) or not isinstance(start, (int, float)):
            continue
        points.append((int(start), float(total)))
    points.sort()
    return points


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
        resolved = resolve_statistic_entity_ids(self.hass, self.device_id, COUNTER_KEYS)
        if not resolved:
            return EfficiencySnapshot(updated_at=now.isoformat())

        rows = await async_statistics_during_period(
            self.hass,
            set(resolved.values()),
            now - timedelta(days=SCOP_LONG_WINDOW_DAYS),
            types={"sum"},
        )
        series = {
            key: _cumulative_series(rows.get(entity_id, []))
            for key, entity_id in resolved.items()
        }

        short_deltas, short_coverage = self._window(series, now_ts, SCOP_WINDOW_DAYS)
        long_deltas, long_coverage = self._window(
            series, now_ts, SCOP_LONG_WINDOW_DAYS
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

        return EfficiencySnapshot(
            scop_total=scop_total,
            scop_total_90d=scop_total_90d,
            aux_heat_share=aux_share,
            coverage_days=short_coverage,
            updated_at=now.isoformat(),
        )

    @staticmethod
    def _window(
        series: dict[str, list[tuple[int, float]]], now_ts: float, days: int
    ) -> tuple[dict[str, float | None], float]:
        """Deltas for every counter in the window, with the weakest coverage."""
        deltas: dict[str, float | None] = {}
        coverages: list[float] = []
        for key in COUNTER_KEYS:
            delta, coverage_days = _window_delta(series.get(key, []), now_ts, days)
            deltas[key] = delta
            coverages.append(coverage_days)
        return deltas, min(coverages) if coverages else 0.0
