"""Small numeric helpers shared by the pure-math modules.

Nothing here imports Home Assistant: the modules that use it — ``building``,
``dhw_loss``, ``efficiency``, ``plant_analytics``, ``solar_gain`` and
``heating_curve`` — are meant to stay importable on their own, and the
coordinators that also need these coercions would otherwise grow their own copy.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def finite(value: object) -> float | None:
    """Coerce to a finite float, rejecting bools and non-numeric input."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return numeric if math.isfinite(numeric) else None


def median(values: Sequence[float]) -> float:
    """Median of a non-empty sequence."""
    ordered = sorted(values)
    count = len(ordered)
    middle = count // 2
    if count % 2:
        return ordered[middle]
    return 0.5 * (ordered[middle - 1] + ordered[middle])
