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
* the identified parameters: room gain grouped as ``configured`` / ``applied`` /
  ``fitted`` (the applied one may simply be the pump's factor, so the fitted
  slope is what checks the identity, and it carries ``valid``/``notes`` plus its
  evidence), the heat-loss coefficient, the time constant, the thermal mass and
  the solar model;
* the derived verdicts, once they exist (snap/sag, cycling, DHW standing loss);
* the curve counters (reverts, trim updates, days the warm or cold end was
  clamped or extrapolated) with the date they were reset at.

What never goes in: entity ids or names, coordinates, energy totals, schedules,
sensor identities, or any raw hourly series. See ``docs`` and the README section
for the published field list.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, VERSION
from .statistics import INDOOR_METRIC_KEYS

#: Where the summary can be downloaded while logged in. The browser session
#: carries the auth, so no token travels in the URL.
SUMMARY_URL = "/api/qvantum/summary"

#: The download's filename.
SUMMARY_FILENAME = "qvantum_summary.json"

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
SUMMARY_SCHEMA_VERSION = 5  # counters block: reverts, clamped/extrapolated days

#: Plant settings that describe the control loops. Everything else in the
#: payload is identified, not configured.
#: Canonical setting names, **not** the raw holding-register keys: the cloud
#: settings API only speaks the canonical names, so a raw key exports as null for
#: every cloud installation (``heating_offset`` versus
#: ``indoor_temperature_offset``). ``test_summary.py`` asserts the whitelist only
#: contains names that are canonical in ``MODBUS_HOLDING_TO_SETTINGS_MAP``.
PLANT_KEYS: tuple[str, ...] = (
    "sensor_mode",
    "room_comp_factor",
    "curve_type_heating",
    "indoor_temperature_offset",
    "tap_water_start",
    "tap_water_stop",
    "max_heating_supply",
    "stop_heating",
)

#: The pump's own supply limits are Modbus-only: the cloud API does not expose
#: them, so those fields are legitimately ``None`` on a cloud export.
MODBUS_ONLY_PLANT_KEYS: tuple[str, ...] = ("max_heating_supply",)

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


def _note(value: Any) -> str:
    """Our own note text, length-capped.

    Notes are generated by the estimator, never copied from the payload, so the
    character whitelist used for device metadata would only mangle them (they
    contain °C, <, parentheses).
    """
    if not isinstance(value, str):
        return ""
    return value.strip()[:120]


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

    Three numbers, not one: **configured** is the pump's own setting,
    **applied** is what the correction actually uses (the factor when it is
    usable, else the fit) and **fitted** is the measured slope. This is the first
    pooled question — does the configured factor equal the measured slope? — and
    one installation only fixes the linear map at one factor value.
    """
    if coordinator is None:
        return None
    snapshot = getattr(coordinator, "data", None)
    model = getattr(coordinator, "_model", None)
    daily = getattr(coordinator, "_daily_model", None)
    baseline = getattr(coordinator, "_baseline", None)
    return {
        "active": bool(getattr(coordinator, "active", False)),
        "room_gain": room_gain_summary(coordinator),
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


#: Curves-side counters: how often the installation actually lived with a
#: clamped or extrapolated knot, and how often the table changed. Counts only —
#: the pool needs a rate, never a log of when.
COUNTER_KEYS: tuple[str, ...] = (
    "since",
    "reverts",
    "trim_updates",
    "days_clamped",
    "days_extrapolated",
)


def counters_summary(coordinator: Any) -> dict[str, Any] | None:
    """The curve's counters for the pooled questions, or ``None`` on cloud."""
    counters = getattr(coordinator, "counters", None)
    if not isinstance(counters, Mapping):
        return None
    return {
        key: (
            _short_string(counters.get(key))
            if key == "since"
            else _number(counters.get(key))
        )
        for key in COUNTER_KEYS
    }


def room_gain_summary(coordinator: Any) -> dict[str, Any]:
    """The room gain in three parts, with the evidence behind the measurement.

    **configured** is the pump's own ``room_comp_factor``, **applied** is what
    the deviation correction uses (the factor when it is usable, else the fit)
    and **fitted** is a measurement: the active-hour slope when one exists —
    the only clean one on an install that never runs Auto — otherwise the
    Auto-hour fit. ``valid`` and ``notes`` say whether the measurement passed
    its gates, so a dump can be weighed instead of trusted.
    """
    applied_source = _metric_key(getattr(coordinator, "_room_gain_source", None))
    active = getattr(coordinator, "_room_gain_active", None)
    if active is not None:
        fitted = {
            "c_per_c": _number(getattr(active, "gain_c_per_c", None)),
            "source": _metric_key(getattr(active, "source", None)),
            "window": "active",
            "valid": bool(getattr(active, "valid", False)),
            "notes": _note(getattr(active, "notes", None)),
            "hours": _number(getattr(active, "hours", None)),
            "buckets": _number(getattr(active, "buckets", None)),
            "r2": _number(getattr(active, "r2", None)),
            # The standard error (and its t) is what lets a borderline estimate
            # be weighed instead of discarded: a low R² with a tight interval is
            # still evidence, and the pool should decide rather than the gate.
            "std_err_c_per_c": _number(getattr(active, "std_err_c_per_c", None)),
            "t_stat": _number(getattr(active, "t_stat", None)),
            "leave_one_out_c_per_c": _number(
                getattr(active, "leave_one_out_c_per_c", None)
            ),
        }
    else:
        fitted = {
            "c_per_c": _number(getattr(coordinator, "_room_gain_fitted", None)),
            "source": _metric_key(
                getattr(coordinator, "_room_gain_fitted_source", None)
            ),
            "window": "auto",
            "valid": getattr(coordinator, "_room_gain_fitted", None) is not None,
            "notes": "",
            "hours": None,
            "buckets": None,
            "r2": None,
            "std_err_c_per_c": None,
            "t_stat": None,
            "leave_one_out_c_per_c": None,
        }
    return {
        "configured_c_per_c": _number(getattr(coordinator, "_room_gain_factor", None)),
        "applied_c_per_c": _number(getattr(coordinator, "_room_gain", None)),
        "applied_source": applied_source,
        "fitted": fitted,
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


async def async_build_summary(hass: HomeAssistant) -> dict[str, Any] | None:
    """Build the summary for the configured entry, or ``None`` when unset.

    Shared by the service and the download view, so the written file, the
    service response and the download cannot drift apart.
    """
    for entry in hass.config_entries.async_entries(DOMAIN):
        runtime = getattr(entry, "runtime_data", None)
        coordinator = getattr(runtime, "coordinator", None)
        if coordinator is None:
            continue
        device_id = getattr(coordinator, "device_id", None)
        data = coordinator.data if isinstance(coordinator.data, dict) else {}
        values = data.get("values") if isinstance(data.get("values"), dict) else {}
        efficiency = getattr(runtime, "efficiency_coordinator", None)
        return build_summary(
            version=VERSION,
            transport=(
                "modbus" if getattr(coordinator, "modbus_enabled", False) else "cloud"
            ),
            device=device_metadata(hass, str(device_id)) if device_id else None,
            values=values,
            curve=getattr(runtime, "curve_coordinator", None),
            efficiency=getattr(efficiency, "data", None),
        )
    return None


class QvantumSummaryView(HomeAssistantView):
    """Serve the opt-in summary as a download, for logged-in users only."""

    url = SUMMARY_URL
    name = "api:qvantum:summary"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        """Return the same payload the service writes, as an attachment."""
        summary = await async_build_summary(request.app["hass"])
        if summary is None:
            return web.json_response(
                {
                    "exception": "not_loaded",
                    "details": "The Qvantum integration is not set up",
                },
                status=404,
            )
        return web.json_response(
            summary,
            headers={
                "Content-Disposition": f'attachment; filename="{SUMMARY_FILENAME}"'
            },
        )


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
        "counters": counters_summary(curve),
        "fitted": fitted_summary(efficiency),
        "verdicts": dict(verdicts or {}),
    }
