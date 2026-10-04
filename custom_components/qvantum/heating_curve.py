"""Pure heating-curve computation for the custom curve module.

Baseline + **one** shared adjustment in °C, the same on all seven points.
The adjustment is the sum of four forecast-driven terms:

- ``outdoor``   — anticipation: move a fraction of the baseline's supply
  change over the next hours, driven by the forecast temperature trend
  rather than the instantaneous BT1.
- ``night_day`` — a small bounded daylight-rhythm offset.
- ``solar``     — the uncertainty-weighted solar gain from
  :mod:`~custom_components.qvantum.solar_gain`, converted to supply degrees
  through the local baseline slope.
- ``load``      — one-sided reduction when the measured heating power is
  below the model demand for the target indoor temperature (the house is
  already warm against the forecast).

Indoor deviation is a **cap only** — a warm house vetoes upward adjustment
and a cold house vetoes downward adjustment, it never drives a term.

No Home Assistant imports and no pump writes: the module takes mappings and
returns the seven supply temperatures plus the term breakdown.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
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

#: Daylight rhythm: peak boost at local solar noon.
DAY_PHASE_MAX_C = 0.5
#: Daylight rhythm: peak setback at local solar midnight.
NIGHT_PHASE_MAX_C = 0.5

#: Solar term clamp (only ever reduces the curve).
SOLAR_MAX_C = 2.0

#: Load term clamp (only ever reduces the curve).
LOAD_MAX_C = 0.5
#: Measured/predicted shortfall that reaches the full load reduction.
LOAD_SPAN = 0.5
#: Below this predicted demand the ratio is meaningless.
LOAD_Q_FLOOR_W = 200.0

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

    def as_dict(self) -> dict[str, float]:
        return {key: round(supply, 2) for key, supply in self.points}

    def integer_points(self) -> tuple[tuple[str, int], ...]:
        """Supplies as whole °C for Modbus writes (half away from zero)."""
        return tuple(
            (key, int(math.floor(supply + 0.5))) for key, supply in self.points
        )


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


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


def night_day_adjustment_c(now_ts: int, daylight: DayPhase | None) -> float:
    """Small daylight-rhythm offset: boost around noon, setback at night."""
    if daylight is None:
        return 0.0
    sunrise = daylight.sunrise_ts
    sunset = daylight.sunset_ts
    if sunrise <= now_ts <= sunset:
        span = sunset - sunrise
        if span <= 0:
            return 0.0
        phase = (now_ts - sunrise) / span
        return DAY_PHASE_MAX_C * math.sin(math.pi * _clamp(phase, 0.0, 1.0))
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
    return -NIGHT_PHASE_MAX_C * math.sin(math.pi * _clamp(phase, 0.0, 1.0))


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
    if expected_w <= LOAD_Q_FLOOR_W:
        return 0.0
    shortfall = 1.0 - (q_actual_w / expected_w)
    if shortfall <= 0.0:
        return 0.0
    return -LOAD_MAX_C * _clamp(shortfall / LOAD_SPAN, 0.0, 1.0)


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
) -> CurveResult:
    """Baseline + outdoor + night/day + solar + load → seven supply points."""
    points = normalize_baseline(baseline)
    now_hour = now_ts - (now_ts % 3600)
    forecast_now = forecast_temperature.get(now_hour)

    outdoor_c = outdoor_adjustment_c(
        points,
        forecast_temperature,
        now_ts,
        horizon_hours=outdoor_horizon_hours,
    )
    night_day_c = night_day_adjustment_c(now_ts, daylight)
    solar_c = solar_adjustment_c(
        model, ghi_by_hour, now_ts, points, forecast_now
    )
    load_c = load_adjustment_c(
        model, q_actual_w, indoor_target_c, forecast_now, ghi_by_hour, now_ts
    )

    total = _clamp(
        outdoor_c + night_day_c + solar_c + load_c, -TOTAL_MAX_C, TOTAL_MAX_C
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

    supplies = tuple(
        (key, _clamp(supply + total, MIN_SUPPLY_C, MAX_SUPPLY_C))
        for key, (_, supply) in zip(CURVE_KEYS, points)
    )
    return CurveResult(
        points=supplies,
        adjustment_c=total,
        outdoor_c=outdoor_c,
        night_day_c=night_day_c,
        solar_c=solar_c,
        load_c=load_c,
        capped_by_indoor=capped_by_indoor,
    )
