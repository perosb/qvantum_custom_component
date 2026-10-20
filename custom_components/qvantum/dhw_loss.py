"""Pure standing-loss math for the DHW tank.

No Home Assistant imports. A window where the tank cools freely (no draw, no
reheating) yields an average loss rate; the coordinator decides when a window
qualifies and EMAs the accepted samples.

The model is a lumped tank: the energy lost while the temperature falls by
``ΔT`` is ``V · c · ΔT``. That is the standing loss (insulation + pipe
circulation) plus any draws too small to be seen, which is exactly why the
coordinator requires zero measured flow.
"""

from __future__ import annotations

import math

#: Volumetric heat capacity of water: 4.186 kJ/(kg·K) / 3600 = kWh per litre per K.
WATER_KWH_PER_LITER_K = 4.186 / 3600.0
#: Shorter windows are dominated by sensor resolution.
MIN_WINDOW_HOURS = 4.0
#: A smaller temperature fall than this is within sensor noise.
MIN_DROP_K = 0.5
#: EMA weight for newly accepted windows.
EMA_ALPHA = 0.3
#: Tank-to-ambient difference the published daily figure is quoted at.
#: A tank at 60 °C in a 20 °C utility room loses twice the heat of one at 40 °C,
#: so windows at different tank levels are only comparable as a coefficient.
REFERENCE_DELTA_K = 40.0
#: A window with a smaller tank-to-ambient difference says nothing about
#: insulation: the fall is within sensor resolution.
MIN_DELTA_K = 10.0


def _finite(value: object) -> float | None:
    """Coerce to a finite float, rejecting bools and non-numeric input."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return numeric if math.isfinite(numeric) else None


def standing_loss_kwh_per_day(
    drop_k: object,
    hours: object,
    volume_l: object,
) -> float | None:
    """Daily tank heat loss extrapolated from one free-cooling window.

    ``None`` when the window is too short, the fall too small, or the volume
    unusable — a two-hour, 0.1 K reading says nothing about a day.
    """
    drop = _finite(drop_k)
    duration = _finite(hours)
    volume = _finite(volume_l)
    if drop is None or duration is None or volume is None:
        return None
    if drop < MIN_DROP_K or duration < MIN_WINDOW_HOURS or volume <= 0.0:
        return None
    energy_kwh = volume * WATER_KWH_PER_LITER_K * drop
    value = energy_kwh / duration * 24.0
    if not math.isfinite(value) or value <= 0.0:
        return None
    return value


def mean_delta_k(
    tank_start: object, tank_end: object, ambient: object
) -> float | None:
    """Mean tank-to-ambient difference over a free-cooling window.

    ``None`` when any input is unusable or the difference is under
    :data:`MIN_DELTA_K`, which would turn sensor resolution into a loss figure.
    """
    start = _finite(tank_start)
    end = _finite(tank_end)
    room = _finite(ambient)
    if start is None or end is None or room is None:
        return None
    delta = (start + end) / 2.0 - room
    if not math.isfinite(delta) or delta < MIN_DELTA_K:
        return None
    return delta


def standing_loss_per_k(
    drop_k: object,
    hours: object,
    volume_l: object,
    delta_k: object,
) -> float | None:
    """Daily tank loss per kelvin of tank-to-ambient difference.

    The coefficient is what the tank's insulation determines; the daily kWh
    figure also depends on how warm the tank happened to be. Normalizing here
    keeps the EMA from mixing a window after a 69 °C extra-hot-water run with
    one at the normal setpoint — measured on a real installation, a single hot
    window moved the published figure by 30 %.
    """
    daily = standing_loss_kwh_per_day(drop_k, hours, volume_l)
    delta = _finite(delta_k)
    if daily is None or delta is None or delta < MIN_DELTA_K:
        return None
    return daily / delta


def reference_loss_kwh_per_day(
    per_k: object, delta_k: float = REFERENCE_DELTA_K
) -> float | None:
    """The per-kelvin loss quoted as kWh/d at ``delta_k`` tank-to-ambient."""
    coefficient = _finite(per_k)
    if coefficient is None or coefficient <= 0.0:
        return None
    return coefficient * delta_k


def blend(
    previous: float | None, sample: float | None, alpha: float = EMA_ALPHA
) -> float | None:
    """EMA of accepted windows; seeds from the first sample."""
    if sample is None:
        return previous
    if previous is None:
        return sample
    return alpha * sample + (1.0 - alpha) * previous
