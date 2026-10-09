"""Pure heating-curve computation for the custom curve module.

Baseline + **one** shared adjustment in °C, the same on all seven points.
The adjustment is the sum of four forecast-driven terms:

- ``outdoor``   — anticipation: move a fraction of the baseline's supply
  change over the next hours, driven by the forecast temperature trend
  rather than the instantaneous BT1.
- ``night_day`` — a daylight-rhythm offset whose amplitude is a fraction of
  the forecast's diurnal supply swing (local curve slope × outdoor range),
  so a flat day adds nothing and a volatile one gets a larger (bounded)
  offset.
- ``solar``     — the uncertainty-weighted solar gain from
  :mod:`~custom_components.qvantum.solar_gain`, converted to supply degrees
  through the local baseline slope.
- ``load``      — one-sided reduction when the measured heating power is
  below the model demand for the target indoor temperature (the house is
  already warm against the forecast).

Optional one-sided control terms (``cop_c``, ``precharge_c``) are computed by
the coordinator, off by default, and folded into the same total before the
indoor cap and the supply clamp.

Indoor deviation is a **cap only** — a warm house vetoes upward adjustment
and a cold house vetoes downward adjustment, it never drives a term.

The frozen baseline is the app's seven-point table, which is only a side
dump: on Auto the firmware follows holding 23, so that table can diverge
from what the pump actually delivers. :func:`corrected_baseline` learns the
residual from observed ``(BT1, cal_heat_temp)`` hours and corrects the table
without changing its shape outside the observed range.

No Home Assistant imports and no pump writes: the module takes mappings and
returns the seven supply temperatures plus the term breakdown.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from .client.modbus.maps import HEATING_CURVE_OUTDOOR_TEMPS
from .solar_gain import SolarModel, smooth_ghi

CURVE_KEYS: tuple[str, ...] = tuple(HEATING_CURVE_OUTDOOR_TEMPS)
OUTDOOR_TEMPS: tuple[int, ...] = tuple(HEATING_CURVE_OUTDOOR_TEMPS.values())

MIN_SUPPLY_C = 10.0
MAX_SUPPLY_C = 80.0

#: How many hours ahead the outdoor anticipation looks.
DEFAULT_OUTDOOR_HORIZON_H = 6
#: Fraction of the baseline supply change applied now.
OUTDOOR_DAMP = 0.5
#: Anticipation clamp.
OUTDOOR_MAX_C = 1.0

#: Daylight rhythm amplitude as a fraction of the forecast diurnal supply
#: swing (|local curve slope| × forecast outdoor range). A flat day has no
#: rhythm offset; a volatile day gets a larger (bounded) one.
NIGHT_DAY_SWING_FRACTION = 0.1
#: Cap on the daylight rhythm amplitude.
NIGHT_DAY_MAX_C = 1.0
#: Hours over which the diurnal swing is measured.
NIGHT_DAY_HORIZON_H = 24

#: Solar term clamp (only ever reduces the curve).
SOLAR_MAX_C = 2.0

#: Load term clamp (only ever reduces the curve).
LOAD_MAX_C = 0.5
#: Measured/predicted shortfall that reaches the full load reduction.
LOAD_SPAN = 0.5
#: Predicted demand must exceed this multiple of the identified heat-loss
#: coefficient (W/K) for the load ratio to be meaningful. Relative to ``a``
#: so it scales with the house instead of a fixed watt floor.
LOAD_Q_FLOOR_K = 1.0

#: Observed Auto hours needed before a baseline correction is attempted.
BASELINE_MIN_SAMPLES = 24
#: Minimum observed outdoor span for a meaningful correction.
BASELINE_MIN_SPAN_C = 5.0
#: Ignore corrections smaller than this (fit noise).
BASELINE_MIN_CORRECTION_C = 1.0
#: Never move a baseline point by more than this.
BASELINE_MAX_CORRECTION_C = 8.0

#: Recency half-life for the indoor-error trim observations.
TRIM_HALF_LIFE_DAYS = 2.0
#: Hourly observations in an outdoor bucket before it carries evidence.
TRIM_MIN_BUCKET_HOURS = 6
#: A reference point with no qualifying bucket within this distance stays at 0.
TRIM_MAX_DISTANCE_C = 7.5
#: Cap on the accumulated trim per point and on a single indicated residual.
TRIM_MAX_C = 2.0

#: COP-feedback clamp (the term only ever reduces the curve).
COP_FEEDBACK_MAX_C = 1.0
#: recent/reference ratio at which the COP feedback starts reducing supply.
COP_FEEDBACK_TRIGGER_RATIO = 0.9
#: Ratio span below the trigger over which the full reduction is reached.
COP_FEEDBACK_SPAN_RATIO = 0.2

#: Hard clamp on the total adjustment.
TOTAL_MAX_C = 3.0

#: Indoor margin (measured − target) at which its cap fully vetoes movement
#: in the margin's direction.
INDOOR_CAP_C = 1.0


@dataclass(frozen=True)
class DayPhase:
    """Sunrise/sunset timestamps around ``now`` for the user's location."""

    sunrise_ts: int
    sunset_ts: int
    previous_sunset_ts: int
    next_sunrise_ts: int


@dataclass(frozen=True)
class CurveResult:
    """The seven supply points plus the shared adjustment breakdown."""

    points: tuple[tuple[str, float], ...]
    adjustment_c: float
    outdoor_c: float
    night_day_c: float
    solar_c: float
    load_c: float
    capped_by_indoor: bool
    trims: Mapping[str, float] = field(default_factory=dict)
    clamped: bool = False
    cop_c: float = 0.0
    precharge_c: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return {key: round(supply, 2) for key, supply in self.points}

    def integer_points(self) -> tuple[tuple[str, int], ...]:
        """Supplies as whole °C for Modbus writes (half away from zero)."""
        return tuple(
            (key, int(math.floor(supply + 0.5))) for key, supply in self.points
        )


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def effective_supply_bounds(
    min_supply_c: float | None = None,
    max_supply_c: float | None = None,
) -> tuple[float, float]:
    """Pump min/max heating supply limits, sanitized.

    Falls back to the register range 10–80 °C when a limit is missing,
    non-numeric, or contradictory. Keeping the effective bounds tight means a
    computed point is never written above what the firmware will actually use.
    """
    low, high = MIN_SUPPLY_C, MAX_SUPPLY_C
    if isinstance(min_supply_c, (int, float)) and not isinstance(min_supply_c, bool):
        low = _clamp(float(min_supply_c), MIN_SUPPLY_C, MAX_SUPPLY_C)
    if isinstance(max_supply_c, (int, float)) and not isinstance(max_supply_c, bool):
        high = _clamp(float(max_supply_c), MIN_SUPPLY_C, MAX_SUPPLY_C)
    if high <= low:
        return MIN_SUPPLY_C, MAX_SUPPLY_C
    return low, high


def normalize_baseline(
    baseline: Mapping[str, float],
) -> tuple[tuple[float, float], ...]:
    """Return ``(outdoor, supply)`` pairs in +30 … −30 order.

    Supplies are clamped to 10–80 °C and made non-decreasing toward colder
    outdoors (falling toward warmer outdoors), which is what the firmware
    table must satisfy. Raises ``ValueError`` when a point is missing.
    """
    missing = [key for key in CURVE_KEYS if key not in baseline]
    if missing:
        raise ValueError(f"baseline missing points: {', '.join(missing)}")
    pairs: list[list[float]] = [
        [
            float(HEATING_CURVE_OUTDOOR_TEMPS[key]),
            _clamp(float(baseline[key]), MIN_SUPPLY_C, MAX_SUPPLY_C),
        ]
        for key in CURVE_KEYS
    ]
    for index in range(len(pairs) - 1):
        if pairs[index + 1][1] < pairs[index][1]:
            pairs[index + 1][1] = pairs[index][1]
    return tuple((outdoor, supply) for outdoor, supply in pairs)


def interpolate_supply(
    points: Sequence[tuple[float, float]], outdoor_c: float
) -> float:
    """Linear interpolation of the baseline; clamped outside the range."""
    if outdoor_c >= points[0][0]:
        return points[0][1]
    for index in range(1, len(points)):
        if outdoor_c >= points[index][0]:
            outdoor_warm, supply_warm = points[index - 1]
            outdoor_cold, supply_cold = points[index]
            span = outdoor_warm - outdoor_cold
            ratio = (outdoor_c - outdoor_cold) / span
            return supply_cold + (supply_warm - supply_cold) * ratio
    return points[-1][1]


def curve_slope(points: Sequence[tuple[float, float]], outdoor_c: float) -> float:
    """Local ``d(supply)/d(outdoor)``; negative for a heating curve."""
    warmer = interpolate_supply(points, outdoor_c + 1.0)
    colder = interpolate_supply(points, outdoor_c - 1.0)
    return (warmer - colder) / 2.0


def outdoor_adjustment_c(
    points: Sequence[tuple[float, float]],
    forecast_temperature: Mapping[int, float],
    now_ts: int,
    *,
    horizon_hours: int = DEFAULT_OUTDOOR_HORIZON_H,
) -> float:
    """Anticipate the forecast trend over the next hours (damped, clamped).

    Both ends come from the forecast series, never the instantaneous BT1.
    Without a current or future hour the term is zero.
    """
    now_hour = now_ts - (now_ts % 3600)
    temperature_now = forecast_temperature.get(now_hour)
    if temperature_now is None:
        return 0.0
    ahead = [
        forecast_temperature[now_hour + hour * 3600]
        for hour in range(1, max(1, horizon_hours) + 1)
        if now_hour + hour * 3600 in forecast_temperature
    ]
    if not ahead:
        return 0.0
    temperature_ahead = sum(ahead) / len(ahead)
    slope = curve_slope(points, temperature_now)
    raw = OUTDOOR_DAMP * slope * (temperature_ahead - temperature_now)
    return _clamp(raw, -OUTDOOR_MAX_C, OUTDOOR_MAX_C)


def diurnal_swing_c(
    forecast_temperature: Mapping[int, float],
    now_ts: int,
    *,
    horizon_hours: int = NIGHT_DAY_HORIZON_H,
) -> float | None:
    """Forecast outdoor max−min over the next ``horizon_hours``.

    ``None`` when fewer than two usable hours exist, which turns the
    daylight-rhythm term off rather than inventing a swing.
    """
    now_hour = now_ts - (now_ts % 3600)
    values: list[float] = []
    for step in range(max(1, horizon_hours) + 1):
        value = forecast_temperature.get(now_hour + step * 3600)
        if value is None:
            continue
        numeric = float(value)
        if math.isfinite(numeric):
            values.append(numeric)
    if len(values) < 2:
        return None
    return max(values) - min(values)


def night_day_adjustment_c(
    now_ts: int,
    daylight: DayPhase | None,
    *,
    amplitude_c: float,
) -> float:
    """Bounded daylight-rhythm offset: boost around noon, setback at night.

    ``amplitude_c`` comes from :func:`compute_curve` (a fraction of the
    forecast's diurnal supply swing); zero disables the term when there is
    no usable forecast.
    """
    if daylight is None or amplitude_c <= 0.0:
        return 0.0
    sunrise = daylight.sunrise_ts
    sunset = daylight.sunset_ts
    if sunrise <= now_ts <= sunset:
        span = sunset - sunrise
        if span <= 0:
            return 0.0
        phase = (now_ts - sunrise) / span
        return amplitude_c * math.sin(math.pi * _clamp(phase, 0.0, 1.0))
    if now_ts < sunrise:
        span = sunrise - daylight.previous_sunset_ts
        if span <= 0:
            return 0.0
        phase = (now_ts - daylight.previous_sunset_ts) / span
    else:
        span = daylight.next_sunrise_ts - sunset
        if span <= 0:
            return 0.0
        phase = (now_ts - sunset) / span
    return -amplitude_c * math.sin(math.pi * _clamp(phase, 0.0, 1.0))


def solar_adjustment_c(
    model: SolarModel | None,
    ghi_by_hour: Mapping[int, float],
    now_ts: int,
    points: Sequence[tuple[float, float]],
    forecast_temperature_c: float | None,
) -> float:
    """Solar gain in W → equivalent ΔT → supply degrees, damped by trust."""
    if model is None or not model.valid or model.a_w_per_k <= 0:
        return 0.0
    ghi = smooth_ghi(ghi_by_hour, now_ts)
    gain_w = model.solar_gain_w(ghi)
    if gain_w <= 0.0:
        return 0.0
    reference = 0.0 if forecast_temperature_c is None else forecast_temperature_c
    slope = curve_slope(points, reference)
    raw = slope * (gain_w / model.a_w_per_k)
    return _clamp(raw, -SOLAR_MAX_C, 0.0)


def load_adjustment_c(
    model: SolarModel | None,
    q_actual_w: float | None,
    indoor_target_c: float | None,
    outdoor_c: float | None,
    ghi_by_hour: Mapping[int, float],
    now_ts: int,
) -> float:
    """One-sided reduction when the pump coasts below the model demand.

    The expected demand includes the solar term, so a sun-driven drop does
    not trigger the load term twice.
    """
    if model is None or not model.valid or q_actual_w is None:
        return 0.0
    if indoor_target_c is None or outdoor_c is None:
        return 0.0
    expected_w = (
        model.a_w_per_k * (indoor_target_c - outdoor_c)
        + model.c_w
        - model.solar_gain_w(smooth_ghi(ghi_by_hour, now_ts))
    )
    if expected_w <= model.a_w_per_k * LOAD_Q_FLOOR_K:
        return 0.0
    shortfall = 1.0 - (q_actual_w / expected_w)
    if shortfall <= 0.0:
        return 0.0
    return -LOAD_MAX_C * _clamp(shortfall / LOAD_SPAN, 0.0, 1.0)


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return 0.5 * (ordered[mid - 1] + ordered[mid])


def _fit_line(x: Sequence[float], y: Sequence[float]) -> tuple[float, float]:
    """Ordinary least squares ``y ≈ slope·x + intercept``."""
    n = len(x)
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    sxx = sum((xi - mean_x) ** 2 for xi in x)
    if sxx <= 1e-12:
        return 0.0, mean_y
    sxy = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    slope = sxy / sxx
    return slope, mean_y - slope * mean_x


def corrected_baseline(
    cached: Mapping[str, float],
    observations: Sequence[tuple[float, float]],
    *,
    min_samples: int = BASELINE_MIN_SAMPLES,
    min_span_c: float = BASELINE_MIN_SPAN_C,
    min_correction_c: float = BASELINE_MIN_CORRECTION_C,
    max_correction_c: float = BASELINE_MAX_CORRECTION_C,
) -> dict[str, float] | None:
    """Correct the frozen Auto baseline against observed pump behaviour.

    ``cached`` is the app's seven-point table (a side dump: on Auto the
    firmware follows holding 23, so this table can diverge from what the
    pump actually delivers). ``observations`` are hourly
    ``(outdoor BT1, cal_heat_temp)`` pairs measured while the pump was on
    Auto. The residual ``observed − interpolated(cached)`` is median-bucketed
    by outdoor and fitted with a line, so the correction keeps the cached
    curve's shape beyond the observed range instead of extrapolating raw data.

    Returns ``None`` when there is too little data, the outdoor span is too
    narrow, or the correction is below the noise threshold — the caller then
    keeps the cached baseline.
    """
    try:
        base = normalize_baseline(cached)
    except (TypeError, ValueError):
        return None

    clean: list[tuple[float, float]] = []
    for outdoor, supply in observations:
        outdoor_c = float(outdoor)
        supply_c = float(supply)
        if math.isfinite(outdoor_c) and math.isfinite(supply_c):
            clean.append((outdoor_c, supply_c))
    if len(clean) < min_samples:
        return None
    outdoors = [outdoor for outdoor, _ in clean]
    if max(outdoors) - min(outdoors) < min_span_c:
        return None

    buckets: dict[int, list[float]] = {}
    for outdoor_c, supply_c in clean:
        residual = supply_c - interpolate_supply(base, outdoor_c)
        buckets.setdefault(round(outdoor_c), []).append(residual)
    if len(buckets) < 2:
        return None
    bucket_x = sorted(buckets)
    slope, intercept = _fit_line(
        [float(x) for x in bucket_x], [_median(buckets[x]) for x in bucket_x]
    )

    correction = {
        key: _clamp(
            slope * outdoor + intercept, -max_correction_c, max_correction_c
        )
        for key, outdoor in HEATING_CURVE_OUTDOOR_TEMPS.items()
    }
    if max(abs(value) for value in correction.values()) < min_correction_c:
        return None

    raw = {
        key: supply + correction[key]
        for key, (_, supply) in zip(CURVE_KEYS, base)
    }
    repaired = normalize_baseline(raw)
    return {key: supply for key, (_, supply) in zip(CURVE_KEYS, repaired)}


def _weighted_median(values: Sequence[tuple[float, float]]) -> float:
    """Median of ``(value, weight)`` pairs; weights need not be normalized."""
    ordered = sorted(values)
    threshold = sum(weight for _, weight in ordered) / 2.0
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return ordered[-1][0]


def trim_residuals(
    baseline: Mapping[str, float],
    observations: Sequence[tuple[int, float, float]],
    now_ts: int,
    *,
    half_life_days: float = TRIM_HALF_LIFE_DAYS,
    min_bucket_hours: int = TRIM_MIN_BUCKET_HOURS,
    max_distance_c: float = TRIM_MAX_DISTANCE_C,
    max_residual_c: float = TRIM_MAX_C,
) -> dict[str, float] | None:
    """Per-point supply corrections indicated by indoor-error history.

    ``observations`` are hourly ``(hour_ts, outdoor BT1, indoor − target)``.
    The indoor error is converted to supply degrees through the local curve
    slope (the same conversion the solar term uses) and median-bucketed by
    outdoor with recency weights, so only weather the house has actually seen
    produces a trim. A reference point without a qualifying bucket within
    ``max_distance_c`` stays at 0 — no evidence, no correction — and the line
    fit keeps the shape beyond the observed range. Returns ``None`` when there
    is not enough evidence for any point.
    """
    try:
        base = normalize_baseline(baseline)
    except (TypeError, ValueError):
        return None

    buckets: dict[int, list[tuple[float, float]]] = {}
    for hour_ts, outdoor, error in observations:
        outdoor_c = float(outdoor)
        error_c = float(error)
        if not (math.isfinite(outdoor_c) and math.isfinite(error_c)):
            continue
        value = curve_slope(base, outdoor_c) * error_c
        if not math.isfinite(value):
            continue
        weight = 1.0
        if half_life_days > 0.0:
            age_days = max(0.0, (float(now_ts) - float(hour_ts)) / 86400.0)
            weight = 0.5 ** (age_days / half_life_days)
        buckets.setdefault(round(outdoor_c), []).append((value, weight))

    qualifying = {
        outdoor: values
        for outdoor, values in buckets.items()
        if len(values) >= min_bucket_hours
    }
    if len(qualifying) < 2:
        return None
    bucket_x = sorted(qualifying)
    slope, intercept = _fit_line(
        [float(x) for x in bucket_x],
        [_weighted_median(qualifying[x]) for x in bucket_x],
    )

    trims: dict[str, float] = {}
    any_evidence = False
    for key, outdoor in HEATING_CURVE_OUTDOOR_TEMPS.items():
        nearest = min(abs(outdoor - x) for x in bucket_x)
        if nearest > max_distance_c:
            trims[key] = 0.0
            continue
        value = _clamp(slope * outdoor + intercept, -max_residual_c, max_residual_c)
        trims[key] = value
        if abs(value) > 1e-9:
            any_evidence = True
    return trims if any_evidence else None


def cop_feedback_adjustment_c(
    recent_cop: float | None,
    reference_cop: float | None,
    *,
    max_reduction_c: float = COP_FEEDBACK_MAX_C,
) -> float:
    """One-sided supply reduction when measured COP falls below its reference.

    ``recent_cop`` and ``reference_cop`` are in the same units. The term is
    zero while the ratio is at or above ``COP_FEEDBACK_TRIGGER_RATIO`` and
    reaches ``-max_reduction_c`` a span of ``COP_FEEDBACK_SPAN_RATIO`` below
    it. Missing, non-finite, or non-positive inputs yield zero: without a
    trusted reference there is nothing to feedback against.
    """
    if recent_cop is None or reference_cop is None:
        return 0.0
    try:
        recent = float(recent_cop)
        reference = float(reference_cop)
    except (TypeError, ValueError):
        return 0.0
    if not (math.isfinite(recent) and math.isfinite(reference)):
        return 0.0
    if recent <= 0.0 or reference <= 0.0:
        return 0.0
    ratio = recent / reference
    if ratio >= COP_FEEDBACK_TRIGGER_RATIO:
        return 0.0
    fraction = _clamp(
        (COP_FEEDBACK_TRIGGER_RATIO - ratio) / COP_FEEDBACK_SPAN_RATIO,
        0.0,
        1.0,
    )
    return -max_reduction_c * fraction


def compute_curve(
    *,
    baseline: Mapping[str, float],
    now_ts: int,
    forecast_temperature: Mapping[int, float],
    ghi_by_hour: Mapping[int, float],
    model: SolarModel | None = None,
    daylight: DayPhase | None = None,
    indoor_margin_c: float | None = None,
    indoor_target_c: float | None = None,
    q_actual_w: float | None = None,
    outdoor_horizon_hours: int = DEFAULT_OUTDOOR_HORIZON_H,
    point_trims: Mapping[str, float] | None = None,
    min_supply_c: float | None = None,
    max_supply_c: float | None = None,
    cop_c: float = 0.0,
    precharge_c: float = 0.0,
) -> CurveResult:
    """Baseline + outdoor + night/day + solar + load → seven supply points.

    ``point_trims`` adds a per-point correction on top of the shared
    adjustment (active-mode indoor-error trims); ``min_supply_c`` /
    ``max_supply_c`` are the pump's effective supply limits, applied instead
    of the register range when known.
    """
    points = normalize_baseline(baseline)
    now_hour = now_ts - (now_ts % 3600)
    forecast_now = forecast_temperature.get(now_hour)

    outdoor_c = outdoor_adjustment_c(
        points,
        forecast_temperature,
        now_ts,
        horizon_hours=outdoor_horizon_hours,
    )
    night_amplitude_c = 0.0
    swing_c = diurnal_swing_c(forecast_temperature, now_ts)
    if swing_c is not None and forecast_now is not None:
        night_amplitude_c = _clamp(
            NIGHT_DAY_SWING_FRACTION * abs(curve_slope(points, forecast_now)) * swing_c,
            0.0,
            NIGHT_DAY_MAX_C,
        )
    night_day_c = night_day_adjustment_c(
        now_ts, daylight, amplitude_c=night_amplitude_c
    )
    solar_c = solar_adjustment_c(
        model, ghi_by_hour, now_ts, points, forecast_now
    )
    load_c = load_adjustment_c(
        model, q_actual_w, indoor_target_c, forecast_now, ghi_by_hour, now_ts
    )

    total = _clamp(
        outdoor_c + night_day_c + solar_c + load_c + cop_c + precharge_c,
        -TOTAL_MAX_C,
        TOTAL_MAX_C,
    )
    capped_by_indoor = False
    if indoor_margin_c is not None:
        if indoor_margin_c > 0.0:
            ceiling = max(0.0, INDOOR_CAP_C - indoor_margin_c)
            if total > ceiling:
                total = ceiling
                capped_by_indoor = True
        elif indoor_margin_c < 0.0:
            floor = -max(0.0, INDOOR_CAP_C + indoor_margin_c)
            if total < floor:
                total = floor
                capped_by_indoor = True

    low, high = effective_supply_bounds(min_supply_c, max_supply_c)
    effective_trims: dict[str, float] = {}
    supplies: list[tuple[str, float]] = []
    clamped = False
    for key, (_, supply) in zip(CURVE_KEYS, points):
        trim = 0.0
        if point_trims is not None:
            candidate = float(point_trims.get(key, 0.0))
            if math.isfinite(candidate):
                trim = candidate
        effective_trims[key] = trim
        value = supply + total + trim
        bounded = _clamp(value, low, high)
        if abs(bounded - value) > 1e-9:
            clamped = True
        supplies.append((key, bounded))

    return CurveResult(
        points=tuple(supplies),
        adjustment_c=total,
        outdoor_c=outdoor_c,
        night_day_c=night_day_c,
        solar_c=solar_c,
        load_c=load_c,
        capped_by_indoor=capped_by_indoor,
        trims=effective_trims,
        clamped=clamped,
        cop_c=cop_c,
        precharge_c=precharge_c,
    )
