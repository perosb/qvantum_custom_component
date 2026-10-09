"""Pure plant analytics: cycling rate and a diagnostic efficiency health grade.

No Home Assistant imports. The efficiency coordinator feeds hourly statistics
and already-derived figures in; these functions decide whether there is enough
evidence and return ``None`` when there is not.

``health_grade`` is deliberately a documented heuristic, not a measurement: it
combines the available components, excludes missing ones, and reports the
coverage so a grade built from one signal is never mistaken for a full picture.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

#: A cycling figure needs at least this much compressor run time (hours).
MIN_RUN_HOURS = 1.0
#: Component weights in the health grade; missing components renormalise.
DEFAULT_HEALTH_WEIGHTS: dict[str, float] = {
    "scop": 0.5,
    "aux_share": 0.25,
    "cycling": 0.25,
}
#: SCOP score: 2.0 -> 0.0, 5.0 -> 1.0 (air-source seasonal range).
SCOP_WORST = 2.0
SCOP_BEST = 5.0
#: Auxiliary-heat share score: 0.0 -> 1.0, 0.2 -> 0.0.
AUX_SHARE_WORST = 0.2
#: Starts per compressor running hour score: 1.0 -> 1.0, 4.0 -> 0.0.
CYCLING_BEST = 1.0
CYCLING_WORST = 4.0


def _finite(value: object) -> float | None:
    """Coerce to a finite float, rejecting bools and non-numeric input."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return numeric if math.isfinite(numeric) else None


def _clamp01(value: float) -> float:
    """Clamp a score into [0, 1]."""
    return max(0.0, min(1.0, value))


def starts_per_hour(
    starts_delta: object,
    run_hours_delta: object,
    *,
    min_run_hours: float = MIN_RUN_HOURS,
) -> float | None:
    """Compressor starts per running hour over the window.

    ``None`` when either counter delta is missing, negative (reset), or there
    was too little running time for the rate to mean anything.
    """
    starts = _finite(starts_delta)
    run_hours = _finite(run_hours_delta)
    if starts is None or run_hours is None:
        return None
    if starts < 0.0 or run_hours < min_run_hours:
        return None
    value = starts / run_hours
    return value if math.isfinite(value) and value >= 0.0 else None


def mean_while_running(
    points: Sequence[tuple[int, float]],
    *,
    start_ts: float,
    end_ts: float,
    min_value: float = 0.0,
) -> float | None:
    """Mean of hourly means at or above ``min_value`` inside the window.

    Hours where the metric is zero or absent (compressor stopped, pump idle)
    are excluded so the average describes the operating point instead of being
    diluted by idle time. ``None`` when no hour qualifies.
    """
    values: list[float] = []
    for ts, value in points:
        if not (start_ts <= ts < end_ts):
            continue
        numeric = _finite(value)
        if numeric is None or numeric <= min_value:
            continue
        values.append(numeric)
    if not values:
        return None
    return sum(values) / len(values)


def duty_cycle(run_hours: object, window_hours: object) -> float | None:
    """Share of the observed window spent running, clamped to [0, 1]."""
    run = _finite(run_hours)
    window = _finite(window_hours)
    if run is None or window is None or window <= 0.0:
        return None
    return _clamp01(run / window)


def score_scop(scop: float) -> float:
    """Higher seasonal COP is better."""
    return _clamp01((scop - SCOP_WORST) / (SCOP_BEST - SCOP_WORST))


def score_aux_share(share: float) -> float:
    """Less auxiliary heat is better."""
    return _clamp01(1.0 - share / AUX_SHARE_WORST)


def score_cycling(cycling: float) -> float:
    """Fewer compressor starts per running hour is better."""
    return _clamp01((CYCLING_WORST - cycling) / (CYCLING_WORST - CYCLING_BEST))


_SCORERS: dict[str, Callable[[float], float]] = {
    "scop": score_scop,
    "aux_share": score_aux_share,
    "cycling": score_cycling,
}


@dataclass(frozen=True)
class HealthGrade:
    """Letter grade plus the score, coverage and per-component scores."""

    letter: str | None
    score: float | None
    coverage: float
    components: dict[str, float]


def _letter(score: float) -> str:
    if score >= 0.85:
        return "A"
    if score >= 0.7:
        return "B"
    if score >= 0.55:
        return "C"
    if score >= 0.4:
        return "D"
    return "F"


def health_grade(
    values: Mapping[str, object],
    weights: Mapping[str, float] | None = None,
) -> HealthGrade:
    """Weighted grade over whichever components are available.

    ``values`` maps component key (``scop``, ``aux_share``, ``cycling``) to a
    raw figure. Components that are missing or not finite are excluded and the
    remaining weights renormalise; ``coverage`` is the available share of the
    total weight. An empty result yields no letter rather than a fabricated F.
    """
    weights = weights or DEFAULT_HEALTH_WEIGHTS
    components: dict[str, float] = {}
    weighted = 0.0
    available_weight = 0.0
    total_weight = 0.0
    for key, weight in weights.items():
        total_weight += weight
        raw = _finite(values.get(key))
        if raw is None:
            continue
        scorer = _SCORERS.get(key)
        if scorer is None:
            continue
        score = scorer(raw)
        components[key] = score
        weighted += weight * score
        available_weight += weight

    if not components or available_weight <= 0.0:
        return HealthGrade(None, None, 0.0, {})

    score = weighted / available_weight
    coverage = available_weight / total_weight if total_weight > 0.0 else 0.0
    return HealthGrade(_letter(score), score, coverage, components)
