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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TypeGuard

from .client.modbus.maps import HEATING_CURVE_OUTDOOR_TEMPS
from .numeric import finite, median
from .solar_gain import (
    SolarDailyModel,
    SolarModel,
    effective_solar_model,
    smooth_ghi,
)

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

#: Knots of margin kept beyond the coldest temperature the forecast can bring.
#: The pump interpolates the table, so the pair bracketing the outdoor
#: temperature decides the supply; a knot is only skipped while no weather can
#: reach its segment. One knot means a forecast that is 10 K too warm still
#: leaves every reachable segment with freshly written endpoints. There is no
#: margin on the warm side: overshooting upwards only moves the pump onto a
#: flatter, lower part of the curve that the supply floor already covers.
REACH_MARGIN_KNOTS = 1

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
#: Wind can only *raise* the supply (extra envelope loss), and a forecast wind
#: is a grid value rather than the wind at the house, so the term is bounded
#: tightly: it is the smallest of the optional terms on purpose.
WIND_MAX_C: float = 2.0
#: Hours averaged for the wind term. A gust in one forecast hour is noise; the
#: envelope response integrates over hours, which is what the calm average of
#: the next hours approximates.
WIND_SMOOTH_HOURS: int = 3

#: Load term clamp (only ever reduces the curve).
LOAD_MAX_C = 0.5
#: A heat pump cannot sustain a thermal-to-electrical ratio below this while it
#: heats, so a figure under it is a mis-measurement rather than a coasting pump.
#: A **physical** bound, not a measured threshold: on a live QE-6 the two means
#: settle at 2.27 (2022 W thermal against 892 W electrical), well clear of it.
#: Comparing a mean with an instantaneous reading is what makes a cycling pump
#: look like it sits under the bound, which is why both sides share one window.
LOAD_MIN_THERMAL_TO_ELECTRICAL = 1.5
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

#: Observed Auto hours needed before the room-compensation gain is identified.
ROOM_GAIN_MIN_SAMPLES = 24
#: Minimum observed room-error span for a meaningful gain fit.
ROOM_GAIN_MIN_SPAN_C = 0.3
#: Upper bound on the identified gain (supply °C per °C room error).
ROOM_GAIN_MAX_C = 25.0

#: Outdoor bucket width for the active-hour measurement. Wide enough that a
#: bucket holds several hours, narrow enough that the table's own slope inside
#: it is negligible.
ROOM_GAIN_ACTIVE_BUCKET_C = 2.0
#: Gates for a publishable active-hour measurement.
ROOM_GAIN_ACTIVE_MIN_HOURS = 18
ROOM_GAIN_ACTIVE_MIN_BUCKETS = 3
ROOM_GAIN_ACTIVE_MIN_BUCKET_HOURS = 2
#: The measurement may not hinge on a single hour: on the live pump one hour
#: moved the two-regressor fit between 4.40 and 6.24 °C/°C.
ROOM_GAIN_ACTIVE_MAX_LOO_C = 0.5
#: …and it has to be distinguishable from zero and actually explain the supply.
ROOM_GAIN_ACTIVE_MIN_T = 4.0
ROOM_GAIN_ACTIVE_MIN_R2 = 0.8

#: Recency half-life for the indoor-error trim observations.
TRIM_HALF_LIFE_DAYS = 2.0
#: Hourly observations in an outdoor bucket before it carries evidence.
TRIM_MIN_BUCKET_HOURS = 6
#: A reference point with no qualifying bucket within this distance stays at 0.
TRIM_MAX_DISTANCE_C = 7.5
#: Cap on the accumulated trim per point and on a single indicated residual.
TRIM_MAX_C = 2.0

#: Pre-charge clamp (the term only ever raises the curve).
PRECHARGE_MAX_C = 1.5
#: Forecast temperature drop over the horizon that starts the pre-charge,
#: and the span above it that reaches the full raise.
PRECHARGE_TRIGGER_K = 4.0
PRECHARGE_HORIZON_H = 8

#: COP-feedback clamp (the term only ever reduces the curve).
COP_FEEDBACK_MAX_C = 1.0
#: recent/reference ratio at which the COP feedback starts reducing supply.
COP_FEEDBACK_TRIGGER_RATIO = 0.9
#: Ratio span below the trigger over which the full reduction is reached.
COP_FEEDBACK_SPAN_RATIO = 0.2

#: Hard clamp on the total adjustment.
TOTAL_MAX_C = 3.0

#: Which conversion produced the solar term. Published with it because the two
#: differ by roughly a factor three on a bright hour, and the unclamped request
#: is only interpretable together with the conversion that made it.
SOLAR_CONVERSION_EMITTER = "emitter"
SOLAR_CONVERSION_CURVE = "curve"

#: Emitter exponent in ``Q ∝ (T_supply − T_room)^n``, used only to form the
#: *local* derivative that turns a watt of solar gain into supply degrees. It is
#: a property of the emitter, not of the house or of an installation's curve, so
#: it travels between installations; 1.3 is the middle of the usual 1.1–1.4 for
#: hydronic emitters, and the whole range moves the conversion by ±15 %.
SOLAR_EMITTER_EXPONENT = 1.3

#: Indoor margin (measured − target) at which its cap fully vetoes movement
#: in the margin's direction.
INDOOR_CAP_C = 1.0

#: An adjustment counts as clamped only when the clamp moves it by more than
#: this. The band's edge is ``±(INDOOR_CAP_C + margin)`` and the total is a sum
#: of seven floats, so the two land within a few ulp of each other whenever the
#: total already sits on the edge — measured live 2026-11-26: total
#: -0.9562499999999993 against an edge of -0.95625, i.e. 7e-16. Without the
#: tolerance the cap reported itself binding without changing anything, and the
#: trim loop pauses whenever it "binds".
CAP_EPSILON_C = 1e-9


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
    #: The solar correction the model asked for **before** ``SOLAR_MAX_C``. The
    #: clamp is the last step before the table, so without this field a bound
    #: request of −2.1 K and one of −15 K read identically — and whether the
    #: bound is doing work cannot be measured.
    solar_raw_c: float
    #: Which conversion produced it: ``emitter`` (live thermal power and
    #: temperatures) or ``curve`` (the fitted-loss fallback), ``None`` when the
    #: term is off. The two differ by ~3× on a sunny hour, so a raw value
    #: without this cannot be interpreted.
    solar_raw_source: str | None
    load_c: float
    capped_by_indoor: bool
    #: The band the shared adjustment was tested against, ``None`` on the side
    #: that did not apply this cycle. Published so a reader can tell a clamp
    #: that bit from one that merely sat on the edge — and see *what* it was
    #: clamped to, which the bool alone cannot say.
    cap_floor_c: float | None = None
    cap_ceiling_c: float | None = None
    trims: Mapping[str, float] = field(default_factory=dict)
    clamped: bool = False
    #: The points the pump's supply limits actually bit. ``clamped`` stays a
    #: bool for the snapshot and the card; this is what tells a user (and a
    #: support dump) *which* point wants to leave the limits, because the
    #: baseline point is the thing to adjust then.
    clamped_points: tuple[str, ...] = ()
    #: Points whose baseline already sits on the pump's floor, so a request
    #: below it (a negative adjustment or trim) is clamped on every
    #: computation without the user being able to move the baseline point down.
    #: Kept apart from ``clamped_points`` so that field keeps meaning "something
    #: to act on", which is also what the ``days_clamped`` counter pools.
    floor_pinned_points: tuple[str, ...] = ()
    cop_c: float = 0.0
    precharge_c: float = 0.0
    #: Wind's extra envelope loss as supply degrees, one-sided (0…WIND_MAX_C),
    #: computed only while the optional term is enabled and the model's wind
    #: column passed its gates.
    wind_c: float = 0.0

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


def lift_baseline_floor(
    baseline: Mapping[str, float], min_supply_c: float | None = None
) -> dict[str, float]:
    """Raise baseline points that sit below the pump's minimum supply.

    A knot under the pump's floor is not a request the firmware can follow:
    every computation clamps it, so the point cannot show its trim feedback
    and the clamping is structural rather than weather-driven — the warm end
    stays pinned at the floor whatever the weather does. The baseline must
    live inside the same bounds the supplied points are clamped to.

    Only the pump's floor is applied. The table still goes through
    ``normalize_baseline``, so the register range (10–80 °C) and the
    non-decreasing shape toward colder outdoor apply as for any baseline; a
    point inside the register range above the pump's own maximum is left
    alone, because that is a genuine demand the pump cannot meet and lowering
    it would hide that.
    """
    points = normalize_baseline(baseline)
    low, _ = effective_supply_bounds(min_supply_c, None)
    return {
        key: max(supply, low)
        for key, (_, supply) in zip(CURVE_KEYS, points, strict=True)
    }


def points_in_reach(
    forecast_temperature: Mapping[int, float],
    *,
    outdoor_c: float | None = None,
    margin_knots: int = REACH_MARGIN_KNOTS,
) -> tuple[str, ...]:
    """Curve knots the pump can interpolate before the weather changes.

    The table is interpolated, so only the knot pair bracketing an outdoor
    temperature can affect the supply the pump asks for. A knot below the
    coldest hour in the forecast cannot be reached before the next forecast
    arrives (the coordinator refetches hourly), so rewriting it only moves a
    number nothing reads. That matters because the shared adjustment moves all
    knots together: on a mild day the cold end used to be rewritten several
    times a day for weather that was not coming.

    ``margin_knots`` keeps that many knots beyond the coldest forecast hour, so
    a forecast that is too warm still leaves the reachable segments covered.

    A reading is enough, a forecast is not required: without one the scope
    follows ``outdoor_c`` alone, so it is the bracket around that temperature.
    Only when neither the forecast nor ``outdoor_c`` is usable is every knot
    returned — nothing is known then, so nothing may be skipped.
    """
    reachable: list[float] = []
    candidates: list[object] = [outdoor_c, *forecast_temperature.values()]
    for candidate in candidates:
        if isinstance(candidate, bool) or not isinstance(candidate, (int, float)):
            continue
        numeric = float(candidate)
        if math.isfinite(numeric):
            reachable.append(numeric)
    if not reachable:
        return CURVE_KEYS

    temps = [HEATING_CURVE_OUTDOOR_TEMPS[key] for key in CURVE_KEYS]

    def _bracket(value: float, *, strict: bool) -> tuple[int, int]:
        # Knots are ordered warm → cold, so the run of entries above ``value``
        # ends at the warmer half of the bracketing pair. Outside the table the
        # nearest end covers it. The warm end is compared strictly: a
        # temperature sitting exactly on a knot also uses the segment above it.
        upper = 0
        for index, temp in enumerate(temps):
            if temp > value or (not strict and temp == value):
                upper = index
            else:
                break
        return upper, min(upper + 1, len(temps) - 1)

    positions: set[int] = set()
    positions.update(_bracket(min(reachable), strict=False))
    positions.update(_bracket(max(reachable), strict=True))

    start = min(positions)
    end = min(len(CURVE_KEYS) - 1, max(positions) + max(0, margin_knots))
    return CURVE_KEYS[start : end + 1]


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


def solar_gain_w_now(
    model: SolarModel | None,
    daily: SolarDailyModel | None,
    ghi_by_hour: Mapping[int, float],
    now_ts: int,
) -> float:
    """Effective solar gain in W at the smoothed irradiance.

    The day-block model wins whenever it passed its evidence gates; the
    hourly model is the fallback (see ``effective_solar_model``), never a
    second opinion averaged in. A censored window often gives it ``b = 0``,
    which is no term, but a positive hourly aperture is applied at its own
    trust rather than discarded.
    """
    aperture, _ = effective_solar_model(model, daily)
    if aperture <= 0.0:
        return 0.0
    return max(0.0, aperture * smooth_ghi(ghi_by_hour, now_ts))


def _emitter_gain_w_per_k(
    q_actual_w: float | None,
    supply_temperature_c: float | None,
    room_temperature_c: float | None,
) -> float | None:
    """``n · Q / (T_supply − T_room)``, or ``None`` when it cannot be trusted.

    Every rejection is a case where the division would report a number the data
    does not support: a missing reading, a thermal input the payload does not
    carry, the pump coasting (``Q = 0`` gives no derivative, not an infinite
    one), or a flow temperature at or below the room's.
    """
    for value in (q_actual_w, supply_temperature_c, room_temperature_c):
        if value is None or isinstance(value, bool):
            return None
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            return None
    assert q_actual_w is not None and supply_temperature_c is not None
    assert room_temperature_c is not None
    if q_actual_w <= 0.0:
        return None
    delta = supply_temperature_c - room_temperature_c
    if delta <= 0.0:
        return None
    return SOLAR_EMITTER_EXPONENT * q_actual_w / delta


def solar_raw_adjustment_c(
    model: SolarModel | None,
    ghi_by_hour: Mapping[int, float],
    now_ts: int,
    points: Sequence[tuple[float, float]],
    forecast_temperature_c: float | None,
    daily: SolarDailyModel | None = None,
    *,
    q_actual_w: float | None = None,
    supply_temperature_c: float | None = None,
    room_temperature_c: float | None = None,
) -> float:
    """The solar correction the model asks for, **before** ``SOLAR_MAX_C``.

    Two conversions from the identified gain ``G`` in watts to supply degrees:

    - **Emitter derivative**, when the live inputs are usable:
      ``ΔT = −G / (n · Q / (T_supply − T_room))``. This is the physical one —
      the house needs ``G`` less heat, and the emitter's local gain says how far
      the flow temperature must fall to deliver that. Measured live 2026-11-26:
      ``1.3 · 2000 W / 13.1 K ≈ 200 W/K``, so 291 W of gain (250 W/m²) is
      **1.45 K**.
    - **Curve slope over the fitted loss coefficient**, otherwise:
      ``ΔT = curve_slope · G / a``. Kept as the fallback because it needs no
      live reading at all — and it is why the term behaved as a switch rather
      than a proportional correction: its 0.0196 K per W/m² reaches
      ``SOLAR_MAX_C`` already at 102 W/m², where the emitter conversion gives
      0.6 K.

    The emitter path is deliberately independent of the curve slope and of the
    fitted ``a``: neither is a property of the emitter, and a house with a
    steeper curve does not receive more sunlight. Unusable inputs fall back
    rather than guess — absence is not evidence, and a thermal ``Q`` must never
    be stood in for by the electrical input.
    """
    aperture, loss_w_per_k = effective_solar_model(model, daily)
    if aperture <= 0.0 or loss_w_per_k <= 0.0:
        return 0.0
    gain_w = solar_gain_w_now(model, daily, ghi_by_hour, now_ts)
    if gain_w <= 0.0:
        return 0.0
    emitter_w_per_k = _emitter_gain_w_per_k(
        q_actual_w, supply_temperature_c, room_temperature_c
    )
    if emitter_w_per_k is not None:
        return -gain_w / emitter_w_per_k
    reference = 0.0 if forecast_temperature_c is None else forecast_temperature_c
    slope = curve_slope(points, reference)
    return slope * (gain_w / loss_w_per_k)


def solar_adjustment_c(
    model: SolarModel | None,
    ghi_by_hour: Mapping[int, float],
    now_ts: int,
    points: Sequence[tuple[float, float]],
    forecast_temperature_c: float | None,
    daily: SolarDailyModel | None = None,
    *,
    q_actual_w: float | None = None,
    supply_temperature_c: float | None = None,
    room_temperature_c: float | None = None,
) -> float:
    """The solar correction actually applied: the raw request, clamped.

    The clamp is not a scaling — it bounds how far sunlight may pull the flow
    temperature down. ``SOLAR_MAX_C`` is deliberately unchanged until the raw
    request has been measured over real weather.
    """
    raw = solar_raw_adjustment_c(
        model,
        ghi_by_hour,
        now_ts,
        points,
        forecast_temperature_c,
        daily,
        q_actual_w=q_actual_w,
        supply_temperature_c=supply_temperature_c,
        room_temperature_c=room_temperature_c,
    )
    return _clamp(raw, -SOLAR_MAX_C, 0.0)


def smooth_wind(
    wind_by_hour: Mapping[int, float], now_ts: int, *, hours: int = WIND_SMOOTH_HOURS
) -> float | None:
    """Mean of the *available* hours in the next ``hours``, or ``None``.

    Hours missing from the payload are skipped rather than counted as calm, so a
    partially covered window still yields a mean; ``None`` only when none of them
    carries a reading, because a payload without wind is "not measured" and a
    wind term must not act on a claim of calm.
    """
    now_hour = now_ts - (now_ts % 3600)
    values = [
        float(wind_by_hour[now_hour + step * 3600])
        for step in range(max(1, hours))
        if now_hour + step * 3600 in wind_by_hour
    ]
    if not values:
        return None
    return sum(values) / len(values)


def wind_adjustment_c(
    daily: SolarDailyModel | None,
    wind_by_hour: Mapping[int, float],
    now_ts: int,
    points: Sequence[tuple[float, float]],
    outdoor_c: float | None,
    indoor_target_c: float | None,
) -> float:
    """Wind's extra envelope loss in W → supply degrees, one-sided and bounded.

    Wind multiplies the loss, so the extra watts at wind ``v`` are
    ``w · v · ΔT`` with ``w`` the coefficient identified per installation. It is
    converted with the *same* pair it was identified with (``a`` from the same
    day-block model) and the local curve slope, exactly like the solar term, and
    capped at :data:`WIND_MAX_C`. Returns 0.0 whenever anything is missing or
    the model's wind column did not pass its gates — no term beats a guess.
    """
    if daily is None or not daily.wind_valid:
        return 0.0
    w_per_ms = daily.w_per_ms
    loss_w_per_k = daily.a_w_per_k
    if w_per_ms is None or w_per_ms <= 0.0 or loss_w_per_k <= 0.0:
        return 0.0
    wind_ms = smooth_wind(wind_by_hour, now_ts)
    if wind_ms is None or wind_ms <= 0.0:
        return 0.0
    if outdoor_c is None or indoor_target_c is None:
        return 0.0
    delta_t = indoor_target_c - outdoor_c
    if delta_t <= 0.0:
        return 0.0
    extra_w = w_per_ms * wind_ms * delta_t
    # ``curve_slope`` is d(supply)/d(outdoor), i.e. negative: colder outside
    # means more supply. Extra loss therefore needs the negated slope, which is
    # the same conversion the solar term uses with its sign (a gain lowers the
    # supply, a loss raises it).
    slope = curve_slope(points, outdoor_c)
    return _clamp(-slope * extra_w / loss_w_per_k, 0.0, WIND_MAX_C)


def load_adjustment_c(
    model: SolarModel | None,
    q_actual_w: float | None,
    indoor_target_c: float | None,
    outdoor_c: float | None,
    ghi_by_hour: Mapping[int, float],
    now_ts: int,
    daily: SolarDailyModel | None = None,
    compressor_power_w: float | None = None,
) -> float:
    """One-sided reduction when the pump coasts below the model demand.

    The expected demand subtracts the *same* solar gain the curve applies —
    including a day-block term — so a sun-driven drop does not trigger the
    load term twice. Its loss coefficient and intercept stay the hourly
    fit's: with a day-block aperture identified separately, ``a``/``c`` were
    never re-identified for this term.
    """
    if model is None or not model.valid or q_actual_w is None:
        return 0.0
    if indoor_target_c is None or outdoor_c is None:
        return 0.0
    # The term's premise is a pump that *deliberately* delivers less than the
    # model asks. A thermal figure below LOAD_MIN_THERMAL_TO_ELECTRICAL of the
    # compressor's input breaks that premise physically: a running heat pump
    # cannot be there, so the figure is the counter, not a level. A missing
    # electrical reading is not evidence either way, so the term keeps working
    # where it always did.
    electrical = finite(compressor_power_w)
    if electrical is not None and electrical > 0.0:
        thermal = finite(q_actual_w)
        if thermal is None or thermal < LOAD_MIN_THERMAL_TO_ELECTRICAL * electrical:
            return 0.0
    expected_w = (
        model.a_w_per_k * (indoor_target_c - outdoor_c)
        + model.c_w
        - solar_gain_w_now(model, daily, ghi_by_hour, now_ts)
    )
    if expected_w <= model.a_w_per_k * LOAD_Q_FLOOR_K:
        return 0.0
    shortfall = 1.0 - (q_actual_w / expected_w)
    if shortfall <= 0.0:
        return 0.0
    return -LOAD_MAX_C * _clamp(shortfall / LOAD_SPAN, 0.0, 1.0)


def _fit_line(x: Sequence[float], y: Sequence[float]) -> tuple[float, float]:
    """Ordinary least squares ``y ≈ slope·x + intercept``."""
    n = len(x)
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    sxx = sum((xi - mean_x) ** 2 for xi in x)
    if sxx <= 1e-12:
        return 0.0, mean_y
    sxy = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y, strict=True))
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
        [float(x) for x in bucket_x], [median(buckets[x]) for x in bucket_x]
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
        for key, (_, supply) in zip(CURVE_KEYS, base, strict=True)
    }
    repaired = normalize_baseline(raw)
    return {key: supply for key, (_, supply) in zip(CURVE_KEYS, repaired, strict=True)}


def room_compensation_gain(
    observations: Sequence[tuple[float, float, float]],
    baseline: Mapping[str, float],
    *,
    min_samples: int = ROOM_GAIN_MIN_SAMPLES,
    min_span_c: float = ROOM_GAIN_MIN_SPAN_C,
    max_gain_c: float = ROOM_GAIN_MAX_C,
) -> float | None:
    """Identify the pump's room-compensation gain from observed Auto hours.

    ``observations`` are ``(outdoor BT1, cal_heat_temp, indoor − target)``
    triples measured while the pump followed its own curve. When the firmware
    adds a room term, the supply residual against the baseline is
    ``intercept + b1 · outdoor + b2 · (indoor − target)`` with ``b2 = −g``;
    the residual is fitted against outdoor as well so baseline shape error
    does not leak into the room slope. Returns ``None`` when there is too
    little data or the room error barely moved, and clamps the gain to
    ``[0, max_gain_c]`` (a negative fit is noise, not a cooling loop).
    """
    try:
        base = normalize_baseline(baseline)
    except (TypeError, ValueError):
        return None

    clean: list[tuple[float, float, float]] = []
    for outdoor, supply, error in observations:
        values = (float(outdoor), float(supply), float(error))
        if all(math.isfinite(value) for value in values):
            clean.append(values)
    if len(clean) < min_samples:
        return None
    errors = [error for _, _, error in clean]
    if max(errors) - min(errors) < min_span_c:
        return None

    outdoors = [outdoor for outdoor, _, _ in clean]
    residuals = [
        supply - interpolate_supply(base, outdoor)
        for outdoor, supply, _ in clean
    ]
    mean_outdoor = sum(outdoors) / len(outdoors)
    mean_error = sum(errors) / len(errors)
    mean_residual = sum(residuals) / len(residuals)
    s_oo = sum((value - mean_outdoor) ** 2 for value in outdoors)
    s_ee = sum((value - mean_error) ** 2 for value in errors)
    s_oe = sum(
        (outdoor - mean_outdoor) * (error - mean_error)
        for outdoor, error in zip(outdoors, errors, strict=True)
    )
    s_or = sum(
        (outdoor - mean_outdoor) * (residual - mean_residual)
        for outdoor, residual in zip(outdoors, residuals, strict=True)
    )
    s_er = sum(
        (error - mean_error) * (residual - mean_residual)
        for error, residual in zip(errors, residuals, strict=True)
    )
    determinant = s_oo * s_ee - s_oe * s_oe
    if abs(determinant) <= 1e-12:
        return None
    # b2 is the room slope; g = −b2.
    slope_error = (s_oo * s_er - s_oe * s_or) / determinant
    return _clamp(-slope_error, 0.0, max_gain_c)


@dataclass(frozen=True)
class ActiveRoomGain:
    """A room-gain measurement from hours the pump followed **our** table.

    ``valid`` is ``False`` whenever an evidence gate fails; callers then publish
    nothing rather than a guess. The evidence travels with the number so a
    reader can weigh it — the leave-one-out swing is the one that showed the
    two-regressor fit being decided by a single high-leverage hour.
    """

    gain_c_per_c: float
    hours: int
    buckets: int
    span_c: float
    #: The indoor metric the slope was measured against, so a caller can bind it
    #: to that sensor instead of applying it to another one's room error.
    source: str | None = None
    std_err_c_per_c: float | None = None
    r2: float | None = None
    residual_std_c: float | None = None
    leave_one_out_c_per_c: float | None = None
    #: The nuisance column's slope: how much the supply moved with the outdoor
    #: temperature *inside* a bucket, with the room term held constant. It is
    #: the curve's own shape, absorbed so it cannot be mistaken for the room
    #: term; reported so a dump shows the absorption worked (it should land on
    #: the curve's slope, not on 0 and not on something wild).
    outdoor_slope_c_per_c: float | None = None
    #: The time column's slope in °C/day: how fast the table itself moved while
    #: the window filled. It should land on the trim drift (a few tenths of a
    #: kelvin per day while the integrator is still moving) and is reported so a
    #: dump shows the absorption worked.
    drift_c_per_day: float | None = None
    #: When the fit ran, in epoch seconds, or ``None`` when unknown. The pure fit
    #: has no clock — the coordinator stamps this — and a value restored from a
    #: store written before the field existed is ``None`` rather than a guess.
    #: Without it a valid estimate survives failed attempts indefinitely and no
    #: reader can tell a five-day-old number from a fresh one.
    measured_at: float | None = None
    valid: bool = False
    notes: str = ""

    @property
    def t_stat(self) -> float | None:
        """Slope over its standard error, or ``None`` when unknown."""
        if not self.std_err_c_per_c:
            return None
        return self.gain_c_per_c / self.std_err_c_per_c


def measured_age_hours(
    measured_at: object, now_ts: object
) -> float | None:
    """Age of a measurement in hours, or ``None`` when it carries no date.

    One spelling of the rule, shared by the snapshot, the diagnostics and the
    export: three modules computing ``(now - measured_at) / 3600`` separately is
    how they drift. A stamp in the future reads ``0.0`` — a clock jump is not
    this helper's question, and a negative age would be read as "very fresh".
    """
    stamped = finite(measured_at)
    now = finite(now_ts)
    if stamped is None or now is None:
        return None
    return max(0.0, (now - stamped) / 3600.0)


def _solve_normal_equations(
    matrix: Sequence[Sequence[float]], vector: Sequence[float]
) -> list[float] | None:
    """Solve ``matrix · beta = vector`` by Gauss-Jordan, or ``None`` if singular."""
    size = len(matrix)
    rows = [list(matrix[i]) + [vector[i]] for i in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda r: abs(rows[r][column]))
        if abs(rows[pivot][column]) < 1e-12:
            return None
        rows[column], rows[pivot] = rows[pivot], rows[column]
        for other in range(size):
            if other == column:
                continue
            factor = rows[other][column] / rows[column][column]
            for k in range(column, size + 1):
                rows[other][k] -= factor * rows[column][k]
    return [rows[i][size] / rows[i][i] for i in range(size)]


def _invert(matrix: Sequence[Sequence[float]]) -> list[list[float]] | None:
    """Inverse of a small symmetric matrix, or ``None`` if singular."""
    size = len(matrix)
    rows = [
        list(matrix[i]) + [1.0 if i == j else 0.0 for j in range(size)]
        for i in range(size)
    ]
    for column in range(size):
        pivot = max(range(column, size), key=lambda r: abs(rows[r][column]))
        if abs(rows[pivot][column]) < 1e-12:
            return None
        rows[column], rows[pivot] = rows[pivot], rows[column]
        divisor = rows[column][column]
        rows[column] = [value / divisor for value in rows[column]]
        for other in range(size):
            if other == column:
                continue
            factor = rows[other][column]
            rows[other] = [
                value - factor * base
                for value, base in zip(rows[other], rows[column], strict=True)
            ]
    return [row[size:] for row in rows]


def room_gain_from_active_hours(
    observations: Sequence[tuple[float, float, float]],
    *,
    bucket_c: float = ROOM_GAIN_ACTIVE_BUCKET_C,
    min_hours: int = ROOM_GAIN_ACTIVE_MIN_HOURS,
    min_buckets: int = ROOM_GAIN_ACTIVE_MIN_BUCKETS,
    min_bucket_hours: int = ROOM_GAIN_ACTIVE_MIN_BUCKET_HOURS,
    min_span_c: float = ROOM_GAIN_MIN_SPAN_C,
    max_loo_c: float = ROOM_GAIN_ACTIVE_MAX_LOO_C,
    min_t: float = ROOM_GAIN_ACTIVE_MIN_T,
    min_r2: float = ROOM_GAIN_ACTIVE_MIN_R2,
    max_gain_c: float = ROOM_GAIN_MAX_C,
) -> ActiveRoomGain:
    """Measure the pump's room term from hours it followed our own table.

    ``observations`` are ``(outdoor, cal_heat_temp, indoor − target)`` triples
    measured **while the curve is active**: the pump's flow-line target is then
    our written table plus its room term, so the room term is what moves the
    supply inside an outdoor bucket.

    Bucketing by outdoor temperature and centring inside each bucket absorbs the
    table's shape — including its integer rounding — without fitting it, and an
    hour that sits alone in its bucket is dropped. That is what makes this
    robust where the two-regressor fit is decided by one hour.

    The pump *lowers* its target when the room is warm, so the slope is negative
    and the gain is ``−slope``. On Auto hours the same measurement would be
    meaningless: the pump's own control carries the term there.

    Centring inside a bucket removes the table's *level*, not its *shape*: the
    outdoor still moves ~2 K inside a bucket while the supply follows the
    curve, so a fit on the room error alone attributes part of the curve's own
    slope to the room term. Measured on a live install (2026-11-11, 160 h,
    7 buckets): the centred-only fit read 4.32 ± 0.32 °C/°C against a configured
    factor of 5.0, and adding the within-bucket outdoor as a nuisance column
    moved it to 4.75 ± 0.20 with the nuisance at −0.85 °C/°C — the curve's own
    slope. The nuisance column is reported, never applied, and a bucket with no
    outdoor variation simply contributes nothing to it.

    A **time** column absorbs the other confound: the table itself moves while
    the window fills, because the trims are still integrating. Centring inside a
    bucket removes the table's level *at one instant*, not a level that drifts
    across the window, so hours from before and after a trim change are pooled
    as if the table had been constant. Measured on a live install (2026-11-18,
    14 days, trims 1.51 → 2.00): the pooled fit read 3.79 °C/°C while the hours
    before the change read 4.90 ± 0.19 and the hours after 5.18 ± 0.26 — the
    between-period slope was 13.5 °C/°C, a Simpson artefact of the table moving
    while the room error moved with it. Adding the time column moved the same
    windows to 4.42–5.13 (spread 0.7 instead of 1.4) with R² 0.86–0.93, and its
    own slope lands on the trim drift (+0.09…+0.28 °C/day against ~0.13 measured).
    Rows without a timestamp simply contribute nothing to it.
    """
    clean: list[tuple[float, float, float, float | None]] = []
    for row in observations:
        outdoor, supply, error = row[0], row[1], row[2]
        stamp = row[3] if len(row) > 3 else None
        values = (float(outdoor), float(supply), float(error))
        if not all(math.isfinite(value) for value in values):
            continue
        when = None
        if stamp is not None:
            try:
                when = float(stamp)
            except (TypeError, ValueError):
                when = None
            if when is not None and not math.isfinite(when):
                when = None
        clean.append((*values, when))

    errors = [error for _, _, error, _ in clean]
    span_c = max(errors) - min(errors) if errors else 0.0

    def unusable(notes: str) -> ActiveRoomGain:
        return ActiveRoomGain(
            gain_c_per_c=0.0,
            hours=len(clean),
            buckets=0,
            span_c=span_c,
            notes=notes,
        )

    if not clean:
        return unusable("no usable hours")
    if span_c < min_span_c:
        return unusable(
            f"the room error barely moved ({span_c:.2f} °C < {min_span_c:.2f})"
        )

    groups: dict[float, list[tuple[float, float, float, float | None]]] = {}
    for outdoor, supply, error, when in clean:
        groups.setdefault(round(outdoor / bucket_c), []).append(
            (error, supply, outdoor, when)
        )
    usable = [group for group in groups.values() if len(group) >= min_bucket_hours]
    hours = sum(len(group) for group in usable)
    if len(usable) < min_buckets or hours < min_hours:
        return ActiveRoomGain(
            gain_c_per_c=0.0,
            hours=hours,
            buckets=len(usable),
            span_c=span_c,
            notes=(
                f"needs {min_buckets} outdoor buckets with {min_bucket_hours}+ hours "
                f"and {min_hours} hours in total, has {len(usable)} and {hours}"
            ),
        )

    def fit(rows: Sequence[Sequence[tuple]], n_buckets: int):
        """Centred fit: room slope, se, r², residual std, the two nuisances.

        The outdoor column is centred inside the same bucket, so it carries
        only the shape the bucket mean could not absorb. When a bucket has no
        outdoor variation the column is empty and the fit degrades to the
        single-slope form, which is what synthetic and single-temperature
        buckets need.

        The time column is centred the same way and absorbs a table that drifts
        while the window fills — the trims are still integrating, so hours from
        before and after a change would otherwise be pooled as if the table had
        been constant. It is only used when every row carries a timestamp.
        """
        xs: list[float] = []
        ys: list[float] = []
        zs: list[float] = []
        ts: list[float] = []
        timed = all(row[3] is not None for group in rows for row in group)
        for group in rows:
            mean_error = sum(row[0] for row in group) / len(group)
            mean_supply = sum(row[1] for row in group) / len(group)
            mean_outdoor = sum(row[2] for row in group) / len(group)
            mean_time = (
                sum(row[3] for row in group) / len(group) if timed else 0.0
            )
            for error, supply, outdoor, when in group:
                xs.append(error - mean_error)
                ys.append(supply - mean_supply)
                zs.append(outdoor - mean_outdoor)
                if timed:
                    ts.append((when - mean_time) / 86400.0)
        sxx = sum(value * value for value in xs)
        if sxx <= 1e-12:
            return None
        # Columns: the room error, then whichever nuisances carry variation.
        columns: list[list[float]] = [xs]
        if sum(value * value for value in zs) > 1e-12:
            columns.append(zs)
        if timed and sum(value * value for value in ts) > 1e-12:
            columns.append(ts)
        n_columns = len(columns)
        matrix = [
            [sum(a * b for a, b in zip(columns[i], columns[j], strict=True)) for j in range(n_columns)]
            for i in range(n_columns)
        ]
        vector = [sum(a * y for a, y in zip(columns[i], ys, strict=True)) for i in range(n_columns)]
        beta = _solve_normal_equations(matrix, vector)
        if beta is None:
            # The columns move together inside the buckets, so no slope is
            # separable from the others.
            return None
        slope = beta[0]
        nuisance = beta[1] if n_columns > 1 and columns[1] is zs else None
        drift = beta[-1] if timed and columns[-1] is ts else None
        residual = [
            y - sum(b * column[index] for b, column in zip(beta, columns, strict=True))
            for index, y in enumerate(ys)
        ]
        variance = sum(value * value for value in residual) / max(
            1, len(xs) - n_buckets - n_columns
        )
        # Standard error of the first coefficient from the inverse's corner.
        inverse = _invert(matrix)
        se = (
            math.sqrt(variance * inverse[0][0])
            if inverse is not None and inverse[0][0] > 0.0
            else float("nan")
        )
        mean_y = sum(ys) / len(ys)
        total = sum((y - mean_y) ** 2 for y in ys)
        return (
            slope,
            se,
            1.0 - sum(value * value for value in residual) / total if total else None,
            math.sqrt(variance),
            nuisance,
            drift,
        )

    fitted = fit(usable, len(usable))
    if fitted is None:
        return ActiveRoomGain(
            gain_c_per_c=0.0,
            hours=hours,
            buckets=len(usable),
            span_c=span_c,
            notes=(
                "the room error does not separate from the outdoor inside "
                "the buckets"
            ),
        )
    slope, std_err, r2, residual_std, nuisance, drift = fitted
    gain = -slope

    # Leave-one-out: the swing that tells whether one hour carries the number.
    swings: list[float] = []
    for group_index, group in enumerate(usable):
        for row_index in range(len(group)):
            thinned = list(usable)
            thinned[group_index] = [
                row for index, row in enumerate(group) if index != row_index
            ]
            thinned = [item for item in thinned if len(item) >= min_bucket_hours]
            if len(thinned) < min_buckets:
                continue
            other = fit(thinned, len(thinned))
            if other is not None:
                swings.append(abs(-other[0] - gain))
    leave_one_out = max(swings) if swings else None

    notes = ""
    valid = True
    if not 0.0 < gain <= max_gain_c:
        valid = False
        notes = (
            f"the measured slope is not a room term ({gain:+.2f} °C/°C): the pump "
            "does not visibly compensate on these hours"
        )
    elif leave_one_out is None:
        valid = False
        notes = "too few hours per bucket to test whether one hour carries it"
    elif leave_one_out > max_loo_c:
        valid = False
        notes = (
            f"one hour carries the estimate (leave-one-out {leave_one_out:.2f} "
            f"°C/°C > {max_loo_c:.2f})"
        )
    elif std_err <= 0.0 or abs(gain / std_err) < min_t:
        valid = False
        notes = (
            "the slope is not distinguishable from zero "
            f"(t = {gain / std_err if std_err else float('nan'):+.1f} < {min_t:.1f})"
        )
    elif r2 is not None and r2 < min_r2:
        valid = False
        notes = (
            f"the room error explains too little of the supply (R² {r2:.2f} < "
            f"{min_r2:.2f})"
        )
    return ActiveRoomGain(
        gain_c_per_c=gain,
        hours=hours,
        buckets=len(usable),
        span_c=span_c,
        std_err_c_per_c=std_err,
        r2=r2,
        residual_std_c=residual_std,
        leave_one_out_c_per_c=leave_one_out,
        outdoor_slope_c_per_c=nuisance,
        drift_c_per_day=drift,
        valid=valid,
        notes=notes,
    )


def room_corrected_deviation(
    deviation_c: float | None,
    gain_c: float | None,
    room_error_c: float | None,
) -> float | None:
    """Remove the pump's room-compensation term from a shadow deviation.

    ``deviation = shadow − cal_heat_temp`` carries the room term the firmware
    adds on top of its curve, so the readiness window can never settle while
    the pump runs room compensation. Subtracting ``gain · (indoor − target)``
    leaves the curve comparison. Returns ``deviation_c`` unchanged when any
    input is missing.
    """
    if deviation_c is None or gain_c is None or room_error_c is None:
        return deviation_c
    return deviation_c - gain_c * room_error_c


#: A room probe cannot read below this: an unconnected BT2 falls back to the
#: firmware's own placeholder (reported as -30 C on a QE4, 2026-10-23), which
#: would put the pump's room value ~26 K off. Cold-setback rooms stay well above
#: it, so the bound only rejects a placeholder or a dead probe.
BT2_MIN_PLAUSIBLE_C = -10.0
BT2_MAX_PLAUSIBLE_C = 45.0
#: Two indoor readings further apart than this are not two views of one room.
#: Measured on a live QE-6 over 30 days, |bt2 - external| was median 0.54 C,
#: p95 0.77 C and max 0.85 C, so this only catches a stuck or placeholder probe
#: - not the normal offset between the pump's sensor and the room sensor.
BT2_MAX_DIVERGENCE_C = 10.0


def _room_reading_is_plausible(value: object) -> TypeGuard[float]:
    """True for a number that could be an indoor temperature.

    A ``TypeGuard`` rather than a plain ``bool``: callers rely on the guard to
    make the value usable as a float, and saying so lets the checker see the
    invariant instead of re-asserting it at every call site.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return BT2_MIN_PLAUSIBLE_C <= float(value) <= BT2_MAX_PLAUSIBLE_C


def _bt2_is_usable(
    configured_c: float | None, bt2_c: object, bt2_detected: object
) -> bool:
    """True when BT2 is a probe the pump can actually read in this room.

    ``bt2_detected`` is the datasheet's ``BT2 Detected`` input register
    (86: 0=No, 1=Yes, 2=Detecting) and is authoritative when the transport
    carries it - Modbus does, the cloud does not. Only a confirmed probe (1)
    counts; 0 and 2 are "no probe" and "not yet decided". Without the bit
    (cloud, or firmware that does not report it) the value itself decides.
    """
    if not _room_reading_is_plausible(bt2_c):
        return False
    if (
        isinstance(bt2_detected, (int, float))
        and not isinstance(bt2_detected, bool)
        and int(bt2_detected) != 1
    ):
        return False
    if configured_c is None:
        return True
    return abs(float(bt2_c) - float(configured_c)) <= BT2_MAX_DIVERGENCE_C


@dataclass(frozen=True)
class PumpRoomReading:
    """The room value the pump's room term used, and where it came from.

    ``source`` is one of ``blended`` (configured + BT2 mean), ``configured``
    (BT2 unusable, not in the external mode, or missing), ``bt2`` (configured
    unknown) and ``none`` (no plausible room reading at all). It travels to the
    snapshot, the deviation sensor and the support dump, because *which* value
    was removed decides whether a deviation figure can be read at all.
    """

    value_c: float | None
    source: str


def pump_room_reading(
    configured_c: float | None,
    bt2_c: float | None,
    *,
    external_mode: bool,
    bt2_detected: float | None = None,
) -> PumpRoomReading:
    """The room value the pump's own room term is computed from.

    In the external-sensor mode the firmware does not use the external value on
    its own: measured on a QE-6 (2026-10-21), ``cal_heat_temp`` follows
    ``curve(bt1) + 5.0 * (desired - room)`` with ``room`` the **mean of the
    external value and BT2** - median |deviation| 0.10 C against 0.32 C for
    the external sensor alone and 0.30 C for BT2, while a +1.1 K target step
    moved ``cal_heat_temp`` by 5.5 K and confirmed the 5.0 gain.

    Removing a room term built from one sensor while the pump built it from the
    mean leaves a constant bias of ``gain * (bt2 - configured) / 2``, which on
    that install was ~1.7 C - more than the readiness median gate. The blend is
    applied only in the external mode, the one regime it was measured in;
    everywhere else the configured sensor is what the pump reads.

    That measurement was taken with a fitted BT2. An install without one holds
    the firmware's placeholder (reported as -30 C on a QE4, 2026-10-23) while
    the pump's own room value comes from the external sensor alone, so the blend
    is gated on BT2 being a *probe* (``_bt2_is_usable``) and, independently, on
    the resulting value being a room temperature at all: in the DISABLED/BT3/AUX
    modes there is no mode-specific indoor metric, the configured value falls
    back to BT2, and the BT2 gate alone would hand that placeholder straight
    through. Degrading to the configured sensor (or to nothing) costs the ~0.3 C
    single-sensor bias; blending a placeholder costs ``gain * 26 K``.
    """
    usable = _bt2_is_usable(configured_c, bt2_c, bt2_detected)
    if configured_c is None:
        reading = PumpRoomReading(
            float(bt2_c) if usable and bt2_c is not None else None,
            "bt2" if usable else "none",
        )
    elif usable and external_mode and bt2_c is not None:
        # `usable` already implies a plausible BT2 reading; the explicit test
        # states the invariant the blend depends on.
        reading = PumpRoomReading(
            (float(configured_c) + float(bt2_c)) / 2.0, "blended"
        )
    else:
        reading = PumpRoomReading(configured_c, "configured")
    if reading.value_c is not None and not _room_reading_is_plausible(
        reading.value_c
    ):
        return PumpRoomReading(None, "none")
    return reading


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


def trim_evidence_hours(
    observations: Sequence[tuple[int, float, float]],
    *,
    min_bucket_hours: int = TRIM_MIN_BUCKET_HOURS,
    max_distance_c: float = TRIM_MAX_DISTANCE_C,
) -> dict[str, int]:
    """Hours behind each curve point's trim, keyed like the seven points.

    A point with 0 hours gets **no new** correction: either no observation lies
    within ``max_distance_c`` of it, or none in an outdoor bucket that reached
    ``min_bucket_hours``. It does *not* mean the accumulated trim is 0 — the
    trims have no leak, so a correction learned while the evidence existed is
    kept after the weather moves away from that point (measured on a live
    install: a point at 99.6 % of ``TRIM_MAX_C`` with 0 evidence hours). What 0
    hours rules out is only that the point is being *driven*; the indicated
    residual beside the trims says which.
    """
    buckets: dict[int, int] = {}
    for _hour_ts, outdoor, _error in observations:
        outdoor_c = float(outdoor)
        if not math.isfinite(outdoor_c):
            continue
        rounded = round(outdoor_c)
        buckets[rounded] = buckets.get(rounded, 0) + 1

    qualifying = {
        outdoor: hours
        for outdoor, hours in buckets.items()
        if hours >= min_bucket_hours
    }
    return {
        key: sum(
            hours
            for outdoor, hours in qualifying.items()
            if abs(outdoor - outdoor_c) <= max_distance_c
        )
        for key, outdoor_c in HEATING_CURVE_OUTDOOR_TEMPS.items()
    }


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


def precharge_adjustment_c(
    forecast_temperature: Mapping[int, float],
    now_ts: int,
    *,
    trigger_k: float = PRECHARGE_TRIGGER_K,
    horizon_hours: int = PRECHARGE_HORIZON_H,
    max_raise_c: float = PRECHARGE_MAX_C,
) -> float:
    """One-sided raise ahead of a forecast cold snap.

    Compares the forecast now with its minimum over the next
    ``horizon_hours``. A drop at or below ``trigger_k`` (or a missing
    forecast) yields zero; the raise grows linearly and caps at
    ``max_raise_c`` a further ``trigger_k`` below the trigger. The caller
    still applies it through the total clamp, the indoor cap and the pump's
    max supply, so this never exceeds what the firmware will use.
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
    drop = float(temperature_now) - min(ahead)
    if drop <= trigger_k:
        return 0.0
    fraction = _clamp((drop - trigger_k) / trigger_k, 0.0, 1.0)
    return max_raise_c * fraction


def apply_indoor_cap(
    total_c: float, indoor_margin_c: float | None
) -> tuple[float, bool, float | None, float | None]:
    """Clamp the shared adjustment toward the house, and say against what.

    The margin's **sign** picks the side: a warm house (margin > 0) caps the
    adjustment under a ceiling that shrinks as the house gets warmer, a cold one
    (margin < 0) lifts it over a floor that rises. Exactly 0.0 is the middle —
    neither side applies, and the house is exactly at its band's centre.

    Returns ``(total, clamped, floor, ceiling)`` where the last two are the band
    edges that applied this cycle (``None`` on the idle side) and ``clamped``
    means the value actually **moved**: see ``CAP_EPSILON_C``.
    """
    if indoor_margin_c is None:
        return total_c, False, None, None
    if indoor_margin_c > 0.0:
        ceiling = max(0.0, INDOOR_CAP_C - indoor_margin_c)
        if total_c > ceiling + CAP_EPSILON_C:
            return ceiling, True, None, ceiling
        return total_c, False, None, ceiling
    if indoor_margin_c < 0.0:
        floor = -max(0.0, INDOOR_CAP_C + indoor_margin_c)
        if total_c < floor - CAP_EPSILON_C:
            return floor, True, floor, None
        return total_c, False, floor, None
    return total_c, False, None, None


def compute_curve(
    *,
    baseline: Mapping[str, float],
    now_ts: int,
    forecast_temperature: Mapping[int, float],
    ghi_by_hour: Mapping[int, float],
    model: SolarModel | None = None,
    daily_model: SolarDailyModel | None = None,
    daylight: DayPhase | None = None,
    indoor_margin_c: float | None = None,
    indoor_target_c: float | None = None,
    q_actual_w: float | None = None,
    compressor_power_w: float | None = None,
    outdoor_horizon_hours: int = DEFAULT_OUTDOOR_HORIZON_H,
    point_trims: Mapping[str, float] | None = None,
    min_supply_c: float | None = None,
    max_supply_c: float | None = None,
    cop_c: float = 0.0,
    precharge_c: float = 0.0,
    wind_by_hour: Mapping[int, float] | None = None,
    wind_enabled: bool = False,
    solar_enabled: bool = True,
    supply_temperature_c: float | None = None,
    room_temperature_c: float | None = None,
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
    # The raw request is published beside the clamped one: the clamp is the only
    # thing between the model and the table, so a reader has to be able to see
    # how much of the term the bound is eating (`solar_raw_source` says which
    # conversion produced it).
    if solar_enabled:
        solar_raw_c = solar_raw_adjustment_c(
            model,
            ghi_by_hour,
            now_ts,
            points,
            forecast_now,
            daily_model,
            q_actual_w=q_actual_w,
            supply_temperature_c=supply_temperature_c,
            room_temperature_c=room_temperature_c,
        )
        solar_c = _clamp(solar_raw_c, -SOLAR_MAX_C, 0.0)
        solar_raw_source = (
            SOLAR_CONVERSION_EMITTER
            if _emitter_gain_w_per_k(
                q_actual_w, supply_temperature_c, room_temperature_c
            )
            is not None
            else SOLAR_CONVERSION_CURVE
        )
    else:
        solar_raw_c = 0.0
        solar_c = 0.0
        solar_raw_source = None
    wind_c = (
        wind_adjustment_c(
            daily_model,
            wind_by_hour or {},
            now_ts,
            points,
            forecast_now,
            indoor_target_c,
        )
        if wind_enabled
        else 0.0
    )
    load_c = load_adjustment_c(
        model,
        q_actual_w,
        indoor_target_c,
        forecast_now,
        ghi_by_hour,
        now_ts,
        daily_model,
        compressor_power_w,
    )

    total = _clamp(
        outdoor_c + night_day_c + solar_c + load_c + cop_c + precharge_c + wind_c,
        -TOTAL_MAX_C,
        TOTAL_MAX_C,
    )
    total, capped_by_indoor, cap_floor_c, cap_ceiling_c = apply_indoor_cap(
        total, indoor_margin_c
    )

    low, high = effective_supply_bounds(min_supply_c, max_supply_c)
    effective_trims: dict[str, float] = {}
    supplies: list[tuple[str, float]] = []
    clamped_points: list[str] = []
    floor_pinned_points: list[str] = []
    for key, (_, supply) in zip(CURVE_KEYS, points, strict=True):
        trim = 0.0
        if point_trims is not None:
            candidate = float(point_trims.get(key, 0.0))
            if math.isfinite(candidate):
                trim = candidate
        effective_trims[key] = trim
        value = supply + total + trim
        bounded = _clamp(value, low, high)
        if abs(bounded - value) > 1e-9:
            # A baseline point already on the pump's floor (``lift_baseline_floor``
            # puts it there) cannot express a request below it, and the clamp
            # repeats on every computation whatever the weather does. Nothing
            # can be adjusted — the floor is the answer — so it is not a
            # ``clamped`` point that a user should act on. The ceiling side is
            # left alone: a demand above the pump's maximum is real and should
            # stay visible.
            if bounded > value and supply <= low + 1e-9:
                floor_pinned_points.append(key)
            else:
                clamped_points.append(key)
        supplies.append((key, bounded))

    # A heating curve is non-decreasing toward colder outdoor. Per-point trims
    # are fitted independently, so a *flat* warm end (the baseline lifted to
    # the pump's floor, e.g. 20/20) can be inverted by a sub-degree trim while
    # a positive shared adjustment holds both points above that floor. Project
    # the final table onto the shape instead of letting the write guard revert
    # the whole curve for a tenth of a degree; raising the warmer knot keeps
    # the pump's floor as the lower bound.
    for index in range(1, len(supplies)):
        key, value = supplies[index]
        previous = supplies[index - 1][1]
        if value < previous:
            supplies[index] = (key, previous)

    return CurveResult(
        points=tuple(supplies),
        adjustment_c=total,
        outdoor_c=outdoor_c,
        night_day_c=night_day_c,
        solar_c=solar_c,
        load_c=load_c,
        solar_raw_c=solar_raw_c,
        solar_raw_source=solar_raw_source,
        capped_by_indoor=capped_by_indoor,
        cap_floor_c=cap_floor_c,
        cap_ceiling_c=cap_ceiling_c,
        trims=effective_trims,
        clamped=bool(clamped_points),
        clamped_points=tuple(clamped_points),
        floor_pinned_points=tuple(floor_pinned_points),
        cop_c=cop_c,
        precharge_c=precharge_c,
        wind_c=wind_c,
    )
