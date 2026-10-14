"""Shared recorder long-term-statistics helpers.

The curve coordinator and the efficiency coordinator both read hourly
long-term statistics. Keeping the recorder access in one module means there is
a single place that knows how to degrade when the recorder is unavailable
(never blocking the heat-pump poll), and tests only need to patch one module.

This is a Home Assistant module, not part of ``client/``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, SensorMode

_LOGGER = logging.getLogger(__name__)


def resolve_statistic_entity_ids(
    hass: HomeAssistant,
    device_id: str | None,
    keys: Iterable[str],
) -> dict[str, str]:
    """Resolve Qvantum sensor unique ids to their entity ids.

    Sensors are registered as ``qvantum_<metric>_<device_id>`` (see
    ``QvantumEntity``). A missing entity means the metric has no statistics to
    read (disabled, not yet created, or not recorded) — never an error.
    """
    if not device_id:
        return {}
    registry = er.async_get(hass)
    resolved: dict[str, str] = {}
    for key in keys:
        entity_id = registry.async_get_entity_id(
            "sensor", DOMAIN, f"qvantum_{key}_{device_id}"
        )
        if entity_id:
            resolved[key] = entity_id
    return resolved


#: Every indoor-temperature metric a resolver may choose from.
INDOOR_METRIC_KEYS: tuple[str, ...] = (
    "room_temp_external",
    "room_temp_ext",
    "bt2",
)
#: Hourly rows a newly configured sensor needs before it replaces the fallback.
INDOOR_METRIC_MIN_HOURS = 24


def indoor_metric_candidates(
    resolved: Mapping[str, str], values: Mapping[str, Any]
) -> tuple[str, ...]:
    """Indoor-temperature metrics in preference order, configured one first.

    Mirrors the climate platform: ``sensor_mode`` selects BT2 or the external
    room sensor, and a missing mode falls back to BT2. BT2 stays last, so a
    configured external sensor wins whenever it has data.
    """
    mode = values.get("sensor_mode")
    if mode is None:
        mode = values.get("use_operation_sensor")
    # dict.fromkeys dedupes: BT2 is both a mode and the final fallback.
    ordered = (*SensorMode.current_temperature_keys(mode), "bt2")
    return tuple(dict.fromkeys(key for key in ordered if key in resolved))


def indoor_metric_entity_ids(resolved: Mapping[str, str]) -> dict[str, str]:
    """Every resolvable indoor metric by key, for one statistics fetch.

    Callers fetch all of them and let :func:`pick_indoor_metric_key` choose:
    the configured sensor may be an entity the recorder has no history for yet.
    """
    return {key: resolved[key] for key in INDOOR_METRIC_KEYS if key in resolved}


def resolve_indoor_metric_key(
    resolved: Mapping[str, str], values: Mapping[str, Any]
) -> str | None:
    """Pick the indoor-temperature metric the pump is configured to use.

    Returns ``None`` when none of the candidate metrics resolved to an entity.
    """
    candidates = indoor_metric_candidates(resolved, values)
    return candidates[0] if candidates else None


def pick_indoor_metric_key(
    resolved: Mapping[str, str],
    values: Mapping[str, Any],
    rows_by_key: Mapping[str, Sequence[Any]],
    *,
    min_rows: int = INDOOR_METRIC_MIN_HOURS,
) -> str | None:
    """First configured indoor metric with enough hourly rows, else ``None``.

    A newly added room sensor records nothing for its first hours, which would
    otherwise silence every statistics-based feature (curve trims, room-gain
    fit, solar calibration, building metrics) until the recorder caught up.
    Falling back to the next candidate keeps them running on the previous
    sensor instead of going dormant; ``min_rows`` is the consumer's own
    minimum, since what counts as "enough" differs per feature.
    """
    for key in indoor_metric_candidates(resolved, values):
        if len(rows_by_key.get(key, ())) >= min_rows:
            return key
    return None


async def async_statistics_during_period(
    hass: HomeAssistant,
    statistic_ids: set[str],
    start: datetime,
    *,
    types: set[str] | None = None,
) -> dict[str, list[Any]]:
    """Return hourly long-term statistics, or ``{}`` when unavailable.

    ``types`` selects which statistics columns to fetch (default ``{"mean"}``);
    energy counters want ``{"sum"}`` instead. The recorder import is lazy so the
    integration still loads when the recorder component is absent (unit tests,
    minimal setups). Every failure degrades to "no statistics": analytics must
    never block or fail the heat-pump poll.
    """
    if not statistic_ids:
        return {}
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import statistics_during_period

    try:
        recorder = get_instance(hass)
    except (KeyError, RuntimeError):
        return {}
    try:
        return await recorder.async_add_executor_job(
            statistics_during_period,
            hass,
            start,
            None,
            statistic_ids,
            "hour",
            None,
            types or {"mean"},
        )
    except Exception as err:  # noqa: BLE001 — recorder may be unavailable
        _LOGGER.debug("Recorder statistics unavailable: %s", err)
        return {}
