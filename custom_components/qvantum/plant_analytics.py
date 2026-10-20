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
from dataclasses import dataclass, field
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
#: Water specific heat in J/(kg·K); one litre of water is ~1 kg.
WATER_HEAT_CAPACITY_J_PER_KG_K = 4186.0
#: Window for the DHW heat-meter plausibility comparison.
DHW_METER_WINDOW_SECONDS = 7 * 86400.0
#: Bucket width. Only the granularity of the trim depends on it, never the
#: integral: every poll interval lands in exactly one bucket.
DHW_METER_BUCKET_SECONDS = 3600
#: A longer poll gap is not credited. The flow and ΔT of the sample in hand
#: describe the moment it was read, not the minutes the recorder was away.
DHW_METER_MAX_INTERVAL_SECONDS = 900.0
#: A larger counter jump is a reset or a teleport, not a draw.
DHW_METER_MAX_STEP_KWH = 5.0


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


def dhw_heat_meter_power_w(
    flow_l_min: object,
    hot_out_c: object,
    cold_in_c: object,
) -> float | None:
    """Instantaneous DHW heat from the secondary-side flow and ΔT.

    ``flow [L/min] / 60 · c [J/(kg·K)] · (hot − cold) [K]``, with one litre of
    water taken as ~1 kg. ``None`` when any input is unusable or the water is
    not being heated (ΔT ≤ 0), so idle hours never contribute a fake
    zero-draw estimate.
    """
    flow = _finite(flow_l_min)
    hot = _finite(hot_out_c)
    cold = _finite(cold_in_c)
    if flow is None or hot is None or cold is None or flow <= 0.0:
        return None
    delta_t = hot - cold
    if delta_t <= 0.0:
        return None
    power = flow / 60.0 * WATER_HEAT_CAPACITY_J_PER_KG_K * delta_t
    return power if math.isfinite(power) and power > 0.0 else None


@dataclass
class DhwMeterWindow:
    """Poll-resolution integral of flow × ΔT next to the metered counter.

    The recorder's hourly statistics cannot carry this comparison:
    ``mean(flow) · mean(ΔT)`` is not the integral of ``flow · ΔT``. A tap draw
    is minutes long and both signals move together inside it, so the product of
    two hourly means lands far below the truth — measured over three days on a
    real installation, 0.7 kWh against a 17.3 kWh meter, while integrating each
    poll interval reproduces the meter to 2.8 %. The flow sample in force is
    therefore integrated with the elapsed poll interval instead of being folded
    into an hourly average.

    State is bounded: one small bucket per hour, pruned to
    :data:`DHW_METER_WINDOW_SECONDS`.
    """

    buckets: dict[int, list[float]] = field(default_factory=dict)
    last_ts: float | None = None
    last_meter_kwh: float | None = None

    def add(
        self,
        *,
        now_ts: object,
        flow_l_min: object,
        hot_out_c: object,
        cold_in_c: object,
        meter_kwh: object,
    ) -> None:
        """Book one poll: the metered step and the interval's flow × ΔT."""
        now = _finite(now_ts)
        if now is None:
            return
        previous_ts = self.last_ts
        previous_meter = self.last_meter_kwh
        meter = _finite(meter_kwh)
        self.last_ts = now
        if meter is not None:
            self.last_meter_kwh = meter

        slot = self.buckets.setdefault(
            int(now // DHW_METER_BUCKET_SECONDS) * DHW_METER_BUCKET_SECONDS,
            [0.0, 0.0, 0.0],
        )
        if previous_meter is not None and meter is not None:
            step = meter - previous_meter
            if 0.0 <= step <= DHW_METER_MAX_STEP_KWH:
                slot[1] += step
        if previous_ts is not None:
            elapsed = now - previous_ts
            if 0.0 < elapsed <= DHW_METER_MAX_INTERVAL_SECONDS:
                power = dhw_heat_meter_power_w(flow_l_min, hot_out_c, cold_in_c)
                if power is not None:
                    slot[0] += power * elapsed / 3_600_000.0
                    slot[2] += elapsed
        self._prune(now)

    def totals(self, now_ts: object) -> tuple[float, float, float]:
        """``(estimated_kwh, metered_kwh, draw_hours)`` over the window."""
        now = _finite(now_ts)
        if now is not None:
            self._prune(now)
        estimated = sum(slot[0] for slot in self.buckets.values())
        metered = sum(slot[1] for slot in self.buckets.values())
        draw_hours = sum(slot[2] for slot in self.buckets.values()) / 3600.0
        return estimated, metered, draw_hours

    def signature(self) -> tuple:
        """Change marker for the persisted snapshot."""
        if not self.buckets:
            return ()
        newest = max(self.buckets)
        youngest = self.buckets[newest]
        return (
            len(self.buckets),
            newest,
            round(youngest[0], 3),
            round(youngest[1], 3),
        )

    def as_list(self) -> list[list[float]]:
        """Serializable rows for the store."""
        return [
            [bucket, round(slot[0], 6), round(slot[1], 6), round(slot[2], 1)]
            for bucket, slot in sorted(self.buckets.items())
        ]

    @classmethod
    def from_list(cls, rows: object) -> DhwMeterWindow:
        """Rebuild the window from stored rows, dropping anything unusable."""
        window = cls()
        if not isinstance(rows, (list, tuple)):
            return window
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) != 4:
                continue
            bucket, estimated, metered, seconds = (_finite(item) for item in row)
            if bucket is None or estimated is None or metered is None:
                continue
            if seconds is None or estimated < 0.0 or metered < 0.0:
                continue
            window.buckets[int(bucket)] = [estimated, metered, seconds]
        return window

    def _prune(self, now_ts: float) -> None:
        """Drop buckets that left the window."""
        cutoff = now_ts - DHW_METER_WINDOW_SECONDS
        for bucket in [b for b in self.buckets if b < cutoff]:
            del self.buckets[bucket]


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
