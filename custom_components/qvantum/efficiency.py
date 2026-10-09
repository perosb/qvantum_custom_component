"""Pure efficiency math for the Qvantum integration.

No Home Assistant imports: the coordinator feeds already-parsed numbers in and
the sensors read the results out. Every function refuses to fabricate a value it
cannot support — missing, non-finite, or nonsensical input yields ``None`` — because
an efficiency figure that is silently wrong is worse than no figure at all.

The energy counters are cumulative kWh meters available in both transports
(``heatingenergy``, ``dhwenergy``, ``compressorenergy``, ``additionalenergy``).
Instantaneous COP is a ratio of counter deltas over one poll interval; rolling
SCOP is a ratio of long-term-statistics sums over weeks.
"""

from __future__ import annotations

import math

#: Counter drops smaller than this are treated as noise, not a reset. The kWh
#: counters move in 0.1 kWh steps, so this is safely below one step.
COUNTER_NOISE_KWH = 1e-6

#: A ratio below this electrical input is rounding noise, not a measurement.
MIN_ELECTRICAL_KWH = 1e-6

#: Thermal deltas below this are ignored for the same reason.
MIN_THERMAL_KWH = 1e-6


def _finite(value: object) -> float | None:
    """Coerce to a finite float, rejecting bools and non-numeric input."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return numeric if math.isfinite(numeric) else None


def energy_delta(previous: object, current: object) -> float | None:
    """Delta between two cumulative counter reads.

    ``None`` when either read is unusable or the counter went down: a reset
    means the interval delta is unknown, not negative consumption. Small
    negatives within counter rounding are clamped to zero.
    """
    before = _finite(previous)
    after = _finite(current)
    if before is None or after is None:
        return None
    delta = after - before
    if delta < -COUNTER_NOISE_KWH:
        return None
    return max(0.0, delta)


def cop_ratio(thermal_kwh: object, electrical_kwh: object) -> float | None:
    """Thermal output divided by electrical input.

    ``None`` unless both are finite and electrical input is measurably
    positive; a COP without measured electrical input is not a measurement.
    """
    thermal = _finite(thermal_kwh)
    electrical = _finite(electrical_kwh)
    if thermal is None or electrical is None:
        return None
    if electrical < MIN_ELECTRICAL_KWH or thermal < MIN_THERMAL_KWH:
        return None
    ratio = thermal / electrical
    if not math.isfinite(ratio) or ratio <= 0.0:
        return None
    return ratio


def aux_heat_share(additional_kwh: object, compressor_kwh: object) -> float | None:
    """Share of electrical input that came from auxiliary heat, in [0, 1]."""
    additional = _finite(additional_kwh)
    compressor = _finite(compressor_kwh)
    if additional is None or compressor is None:
        return None
    total = additional + compressor
    if total < MIN_ELECTRICAL_KWH:
        return None
    return max(0.0, min(1.0, additional / total))


def scop_from_counters(
    heating_kwh: object,
    dhw_kwh: object,
    compressor_kwh: object,
    additional_kwh: object,
) -> float | None:
    """Seasonal COP over a window from counter deltas.

    ``(heating + DHW) / (compressor + auxiliary)``. ``None`` when any delta is
    missing or there is no measurable thermal output/electrical input in the
    window; the counter split between modes is not available, so this is the
    system figure only.
    """
    heating = _finite(heating_kwh)
    dhw = _finite(dhw_kwh)
    compressor = _finite(compressor_kwh)
    additional = _finite(additional_kwh)
    if heating is None or dhw is None or compressor is None or additional is None:
        return None
    return cop_ratio(heating + dhw, compressor + additional)
