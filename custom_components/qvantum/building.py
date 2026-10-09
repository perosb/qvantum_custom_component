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
