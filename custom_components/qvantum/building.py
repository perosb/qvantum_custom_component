"""Pure building-physics helpers: degree hours and heat-loss estimation.

No Home Assistant imports. The efficiency coordinator feeds hourly recorder
statistics in; these functions decide whether there is enough evidence for a
building figure and return ``None`` when there is not. Nothing here writes to
the pump.

The heat-loss coefficient is a steady-state estimate: an hour's measured
heating output is attributed to the indoor-outdoor temperature difference at
that hour, ignoring thermal storage and solar/internal gains. Over many hours
those balance out, which is why the fit needs a spread of outdoor temperatures
and a recency-weighted sample rather than a single reading.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Sequence

#: Heating base temperature (°C) for degree hours (European convention).
DEFAULT_BASE_TEMP_C = 15.0
#: Minimum usable hours before a heat-loss fit is published.
MIN_FIT_ROWS = 12
#: Below this indoor-outdoor spread an hour says almost nothing about the loss.
MIN_DELTA_T_K = 5.0
#: Below this heating power the hour may be circulation only, not a load.
MIN_HEAT_POWER_W = 150.0
#: Recency half-life for the weighted fit (effective memory ≈ 60 days).
HALF_LIFE_DAYS = 30.0

#: Hours one non-heating episode needs before its decay slope is trusted.
MIN_EPISODE_HOURS = 3
#: Episodes needed before a time constant is published.
MIN_EPISODES = 3
#: Spread of indoor-outdoor differences the episodes must cover. Without it the
#: fit cannot separate the loss rate from the standing gains.
MIN_EPISODE_SPREAD_K = 3.0
#: Plausible envelope time constants; outside this the fit is noise.
MIN_TIME_CONSTANT_H = 5.0
MAX_TIME_CONSTANT_H = 500.0


@dataclass(frozen=True)
class TimeConstant:
    """Envelope decay identified from hours the heat pump was not heating."""

    time_constant_h: float
    gain_k_per_h: float
    episodes: int
    delta_t_min_k: float
    delta_t_max_k: float


def _slope(points: Sequence[tuple[float, float]]) -> float | None:
    """Least-squares slope of ``y`` against ``x``, or ``None`` if x is flat."""
    count = len(points)
    mean_x = sum(x for x, _ in points) / count
    mean_y = sum(y for _, y in points) / count
    sxx = sum((x - mean_x) ** 2 for x, _ in points)
    if sxx <= 1e-12:
        return None
    return sum((x - mean_x) * (y - mean_y) for x, y in points) / sxx


def _episode_decay(
    hours: Sequence[int],
    indoor: Mapping[int, float],
    outdoor: Mapping[int, float],
    min_hours: int,
) -> tuple[float, float] | None:
    """``(mean ΔT, indoor slope in K/h)`` for one non-heating episode."""
    if len(hours) < min_hours:
        return None
    first = hours[0]
    series = [(float((hour - first) / 3600.0), float(indoor[hour])) for hour in hours]
    slope = _slope(series)
    if slope is None:
        return None
    delta_t = sum(float(indoor[hour] - outdoor[hour]) for hour in hours) / len(hours)
    if not math.isfinite(delta_t) or not math.isfinite(slope):
        return None
    return delta_t, slope


def fit_time_constant(
    indoor_by_hour: Mapping[int, float],
    outdoor_by_hour: Mapping[int, float],
    power_by_hour: Mapping[int, float],
    *,
    min_episode_hours: int = MIN_EPISODE_HOURS,
    max_power_w: float = MIN_HEAT_POWER_W,
    min_episodes: int = MIN_EPISODES,
    min_spread_k: float = MIN_EPISODE_SPREAD_K,
    min_time_constant_h: float = MIN_TIME_CONSTANT_H,
    max_time_constant_h: float = MAX_TIME_CONSTANT_H,
) -> TimeConstant | None:
    """Identify the envelope time constant from measured room decays.

    While the pump is not heating, the room decays as
    ``dT/dt = gains - (UA/C) · (T_indoor - T_outdoor)``, so a line through the
    episode points ``(ΔT, slope)`` has slope ``-1/τ``. The intercept is the
    standing gain (sun, occupants, appliances), which is why the episodes must
    cover a spread of ``ΔT``: with a single one the two cannot be separated.

    Returns ``None`` when there are too few episodes, the spread is too narrow,
    the decay is not downwards, or the result falls outside the plausible
    envelope range - a guess here would be worse than no figure.
    """
    hours = sorted(set(indoor_by_hour) & set(outdoor_by_hour) & set(power_by_hour))
    episodes: list[tuple[float, float]] = []
    run: list[int] = []
    for hour in hours:
        heating = float(power_by_hour[hour]) >= max_power_w
        if not heating and run and hour != run[-1] + 3600:
            # A gap in the statistics ends the episode: those hours are
            # unknown, not off.
            heating = True
        if heating:
            if run:
                episode = _episode_decay(
                    run, indoor_by_hour, outdoor_by_hour, min_episode_hours
                )
                if episode is not None:
                    episodes.append(episode)
                run = []
            continue
        run.append(hour)
    if run:
        episode = _episode_decay(
            run, indoor_by_hour, outdoor_by_hour, min_episode_hours
        )
        if episode is not None:
            episodes.append(episode)

    if len(episodes) < min_episodes:
        return None
    delta_ts = [delta_t for delta_t, _ in episodes]
    if max(delta_ts) - min(delta_ts) < min_spread_k:
        return None
    slope, intercept = _fit_episodes(delta_ts, [s for _, s in episodes])
    if slope is None:
        return None
    decay_k_per_h = -slope
    if decay_k_per_h <= 0.0:
        return None
    time_constant_h = 1.0 / decay_k_per_h
    if not min_time_constant_h <= time_constant_h <= max_time_constant_h:
        return None
    return TimeConstant(
        time_constant_h=time_constant_h,
        gain_k_per_h=intercept,
        episodes=len(episodes),
        delta_t_min_k=min(delta_ts),
        delta_t_max_k=max(delta_ts),
    )


def _fit_episodes(
    delta_ts: Sequence[float], slopes: Sequence[float]
) -> tuple[float | None, float]:
    """``(slope, intercept)`` of the episode line, slope ``None`` when flat."""
    points = list(zip(delta_ts, slopes))
    slope = _slope(points)
    if slope is None:
        return None, 0.0
    count = len(points)
    mean_x = sum(x for x, _ in points) / count
    mean_y = sum(y for _, y in points) / count
    return slope, mean_y - slope * mean_x


def _finite(value: object) -> float | None:
    """Coerce to a finite float, rejecting bools and non-numeric input."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return numeric if math.isfinite(numeric) else None


def degree_hours(
    outdoor_by_hour: Mapping[int, float],
    *,
    base_c: float = DEFAULT_BASE_TEMP_C,
) -> float:
    """Sum of ``max(0, base - outdoor)`` over all usable hours (K·h).

    Missing or non-finite hours are skipped, not treated as zero: an outage
    should not cool the building on paper.
    """
    total = 0.0
    for value in outdoor_by_hour.values():
        outdoor = _finite(value)
        if outdoor is None:
            continue
        total += max(0.0, base_c - outdoor)
    return total


def weather_normalized_heating(
    heating_kwh: object, degree_hours_k: object
) -> float | None:
    """Heating energy per heating degree day (HDD = degree hours / 24).

    ``None`` when either side is missing or non-positive — normalizing by zero
    degree hours would divide by a warm month.
    """
    energy = _finite(heating_kwh)
    degree_hours_val = _finite(degree_hours_k)
    if energy is None or degree_hours_val is None:
        return None
    if energy <= 0.0 or degree_hours_val <= 0.0:
        return None
    value = energy / (degree_hours_val / 24.0)
    return value if math.isfinite(value) and value > 0.0 else None


def fit_heat_loss(
    hours: Sequence[tuple[int, float, float]],
    *,
    now_ts: int,
    half_life_days: float = HALF_LIFE_DAYS,
) -> float | None:
    """Recency-weighted through-origin fit of ``heat_w = k · ΔT``.

    ``hours`` are ``(hour_ts, delta_t_k, heat_w)``. Returns the building
    heat-loss coefficient in W/K, or ``None`` when there are too few usable
    rows or the remaining spread carries no signal.
    """
    sum_xy = 0.0
    sum_xx = 0.0
    rows = 0
    for item in hours:
        if not isinstance(item, (tuple, list)) or len(item) != 3:
            continue
        ts = _finite(item[0])
        delta_t = _finite(item[1])
        heat_w = _finite(item[2])
        if ts is None or delta_t is None or heat_w is None:
            continue
        if delta_t < MIN_DELTA_T_K or heat_w < MIN_HEAT_POWER_W:
            continue
        if half_life_days > 0.0:
            age_days = max(0.0, (now_ts - ts) / 86400.0)
            weight = 0.5 ** (age_days / half_life_days)
        else:
            weight = 1.0
        sum_xy += weight * delta_t * heat_w
        sum_xx += weight * delta_t * delta_t
        rows += 1

    if rows < MIN_FIT_ROWS or sum_xx <= 0.0:
        return None
    coefficient = sum_xy / sum_xx
    if not math.isfinite(coefficient) or coefficient <= 0.0:
        return None
    return coefficient
