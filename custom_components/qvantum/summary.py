"""Opt-in summary export: facts about the plant and the identified rules.

The payload is built by **whitelist**: every field is picked explicitly and
coerced to a number, a boolean or a short string from a fixed set. Nothing is
copied from the values payload as free text, so an entity id, a friendly name or
a coordinate cannot leak by accident — a test asserts exactly that.

What goes in (the rule questions the pool can answer):

* device metadata: manufacturer, model, firmware version;
* the loop configuration: ``sensor_mode``, ``room_comp_factor``, curve type,
  heating offset, tap-water start/stop, the pump's own supply limits;
* the climate the installation has been observed in: the outdoor span and the
  number of heating hours;
* the identified parameters: room gain (configured *and* fitted), the heat-loss
  coefficient, the time constant, the thermal mass, the solar model;
* the derived verdicts, once they exist (snap/sag, cycling, DHW standing loss).

What never goes in: entity ids or names, coordinates, energy totals, schedules,
sensor identities, or any raw hourly series. See ``docs`` and the README section
for the published field list.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .statistics import INDOOR_METRIC_KEYS

#: Characters allowed in the few strings that travel. Device metadata and our own
#: enum-ish values only: nothing is ever copied from the values payload as text
#: (that path is numbers only), so a friendly name or an entity id cannot travel
#: this way — the whitelist is hygiene, the enumerated sources are the guarantee.
_ALLOWED_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ._-+/()"
)

#: Entity-id domains: a string shaped like one is refused even though the
#: whitelisted sources should never produce it.
_ENTITY_PREFIXES = (
    "sensor.",
    "binary_sensor.",
    "climate.",
    "switch.",
    "number.",
    "select.",
    "light.",
    "fan.",
    "water_heater.",
)

#: Bump when a field is added, removed or changes meaning: the pool groups by it.
SUMMARY_SCHEMA_VERSION = 1

#: Plant settings that describe the control loops. Everything else in the
#: payload is identified, not configured.
PLANT_KEYS: tuple[str, ...] = (
    "sensor_mode",
    "room_comp_factor",
    "curve_type_heating",
    "heating_offset",
    "tap_water_start",
    "tap_water_stop",
    "max_heating_supply",
    "stop_heating",
)

#: Identified building/envelope figures, straight off the efficiency snapshot.
FITTED_EFFICIENCY_KEYS: tuple[str, ...] = (
    "heat_loss_w_per_k",
    "heating_degree_hours",
    "weather_normalized_heating",
    "thermal_time_constant",
    "thermal_gain_k_per_h",
    "thermal_mass_kwh_per_k",
    "thermal_episodes",
    "dhw_standing_loss",
    "compressor_starts_per_hour",
    "compressor_run_hours_24h",
    "compressor_duty_cycle",
    "scop_total",
    "aux_heat_share",
    "efficiency_health_grade",
    "efficiency_health_score",
    "efficiency_health_coverage",
)


def _number(value: Any) -> float | int | None:
    """Return ``value`` when it is a real number, else ``None``.

    Booleans are rejected explicitly: ``True == 1`` would otherwise appear as a
    measurement.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and value != value:  # NaN
        return None
    return round(value, 3) if isinstance(value, float) else value


def _short_string(value: Any) -> str | None:
    """Return a short, plain string, or ``None``.

    Version strings legitimately contain dots (``2026.10.4``), friendly names do
    not have to, so the guard is a character whitelist plus a length cap rather
    than a pattern.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if (
        not text
        or len(text) > 40
        or set(text) - _ALLOWED_CHARS
        or text.startswith(_ENTITY_PREFIXES)
    ):
        return None
    return text


def _metric_key(value: Any) -> str | None:
    """The indoor metric the gain came from, or ``None`` for anything else."""
    return value if value in INDOOR_METRIC_KEYS else None


def plant_summary(values: Mapping[str, Any]) -> dict[str, Any]:
    """Whitelisted control-loop settings, coerced to numbers or ``None``."""
    return {key: _number(values.get(key)) for key in PLANT_KEYS}


def device_metadata(hass: Any, device_id: str) -> Any:
    """The HA device entry for a pump id, or ``None``.

    ``coordinator.device_id`` is the *pump's* API id (cloud id or Modbus
    serial), never a device-registry id: the entry is found by the identifier
    the integration registered. The registry is absent in a stripped harness, so
    this is best effort — an export without metadata beats no export.
    """
    try:
        registry = dr.async_get(hass)
    except (KeyError, RuntimeError):
        return None
    return registry.async_get_device(identifiers={(DOMAIN, f"qvantum-{device_id}")})


def device_summary(device: Any) -> dict[str, Any]:
    """Manufacturer/model/firmware — device metadata, never user data."""
    if device is None:
        return {"manufacturer": None, "model": None, "firmware": None, "hardware": None}
    return {
        "manufacturer": _short_string(getattr(device, "manufacturer", None)),
        "model": _short_string(getattr(device, "model", None)),
        "firmware": _short_string(getattr(device, "sw_version", None)),
        "hardware": _short_string(getattr(device, "hw_version", None)),
    }


def climate_summary(curve: Any) -> dict[str, Any]:
    """The outdoor range the installation has been observed in, and how much.

    Both live on the curve **snapshot** (``coordinator.data``): readiness and
    baseline fields travel with the snapshot, not with the coordinator.
    """
    snapshot = getattr(curve, "data", None) if curve is not None else None
    if snapshot is None:
        return {"outdoor_min_c": None, "outdoor_max_c": None, "heating_hours": None}
    return {
        "outdoor_min_c": _number(getattr(snapshot, "baseline_outdoor_min_c", None)),
        "outdoor_max_c": _number(getattr(snapshot, "baseline_outdoor_max_c", None)),
        "heating_hours": _number(getattr(snapshot, "baseline_learned_hours", None)),
    }


def curve_summary(coordinator: Any) -> dict[str, Any] | None:
    """The identified pump room gain, configured against fitted.

    This pair is the first pooled question: does the pump's own
    ``room_comp_factor`` equal the slope measured on hours it followed our table?
    One installation fixes the linear map at one factor value; the pool is what
    decides whether the fitted estimator can be retired.
    """
    if coordinator is None:
        return None
    snapshot = getattr(coordinator, "data", None)
    model = getattr(coordinator, "_model", None)
    daily = getattr(coordinator, "_daily_model", None)
    baseline = getattr(coordinator, "_baseline", None)
    return {
        "active": bool(getattr(coordinator, "active", False)),
        "room_gain_configured_c_per_c": _number(
            getattr(coordinator, "_room_gain_factor", None)
        ),
        "room_gain_fitted_c_per_c": _number(getattr(coordinator, "_room_gain", None)),
        "room_gain_source": _metric_key(
            getattr(coordinator, "_room_gain_source", None)
        ),
        "median_abs_c": _number(getattr(snapshot, "median_abs_c", None)),
        "p95_abs_c": _number(getattr(snapshot, "p95_abs_c", None)),
        "ready": bool(getattr(snapshot, "ready", False)),
        "baseline_points": len(baseline) if isinstance(baseline, Mapping) else None,
        "solar_a_w_per_k": _number(getattr(model, "a_w_per_k", None)),
        "solar_b_m2": _number(getattr(model, "b_m2", None)),
        "solar_trust": _number(getattr(model, "trust", None)),
        "daily_solar_b_m2": _number(getattr(daily, "b_m2", None)),
        "daily_solar_trust": _number(getattr(daily, "trust", None)),
        "daily_solar_days": _number(getattr(daily, "n_days", None)),
    }


def fitted_summary(snapshot: Any) -> dict[str, Any]:
    """Identified envelope figures from the efficiency snapshot."""
    if snapshot is None:
        return {key: None for key in FITTED_EFFICIENCY_KEYS}
    return {
        key: (
            _short_string(getattr(snapshot, key, None))
            if key == "efficiency_health_grade"
            else _number(getattr(snapshot, key, None))
        )
        for key in FITTED_EFFICIENCY_KEYS
    }


def build_summary(
    *,
    version: str,
    transport: str,
    device: Any = None,
    values: Mapping[str, Any] | None = None,
    curve: Any = None,
    efficiency: Any = None,
    verdicts: Mapping[str, Any] | None = None,
    generated_on: str | None = None,
) -> dict[str, Any]:
    """Assemble the exportable summary.

    ``generated_on`` is a **date**, not a timestamp: the time of day plus a
    temperature series is more identifying than it needs to be, and no question
    in the pool needs it.
    """
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "integration_version": _short_string(version) or version,
        "generated_on": generated_on or date.today().isoformat(),
        "transport": transport,
        "device": device_summary(device),
        "plant": plant_summary(values or {}),
        "climate": climate_summary(curve),
        "curve": curve_summary(curve),
        "fitted": fitted_summary(efficiency),
        "verdicts": dict(verdicts or {}),
    }
