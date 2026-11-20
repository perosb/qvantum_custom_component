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
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    REVERT_REASONS,
    SENSOR_MODE_HTTP_BT2,
    SENSOR_MODE_HTTP_EXT_ROOM_SENSOR,
    SensorMode,
    VERSION,
)
from .statistics import INDOOR_METRIC_KEYS
from .coordinator import transport_kind

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
#: Only a version a *release* has carried needs superseding — an unreleased number
#: has no rows to protect, so further changes on `main` edit it in place.
#: 6: dhw_standing_loss is a normalized coefficient quoted at a fixed
#: tank-to-ambient difference (and the identified tank volume), not the raw
#: daily figure at whatever tank temperature the window saw.
#: 7: days_clamped counts days with a *clamped* knot only; a warm end pinned
#: on the pump's floor is structural (nothing the baseline can answer) and no
#: longer counts, so the figure means "days with something to act on".
#: 8: adds the wind column of the day balance (`daily_wind_w_per_ms`,
#: `daily_wind_t_stat`, `daily_wind_hours`, `daily_wind_valid`) and the applied
#: term (`wind_c`). The fields are new, so v7 rows stay valid for their own
#: fields — but the pool key must not mix a schema where "no wind term" was not
#: expressible with one where it is.
#: 9: adds `daily_wind_evidence_days`, the number of days the cold+windy hours
#: are spread over. The hours gate alone could not be read from a row, so a
#: window refused for having 200 h on four days looked identical to one with no
#: evidence at all.
#: 10: carries the *evidence* behind the two readiness questions instead of only
#: their outcome — the day-block model's `t`/R²/`valid`/`notes` (which gate bit),
#: the readiness `blocker` and window, the signed median (an offset and noise
#: around zero share a median absolute value), and the room error over the same
#: window. v9 could report that a model was invalid but not why, so "is the gate
#: too strict or is it the season?" was unanswerable from the pool.
#: The readiness figures are also **null while the curve is active**
#: (`blocker: active`): the gate compares the baseline against a target that is
#: then our own table, so the value was the trims and never meant what the field
#: name said. A v10 row taken before this line existed carries a number there,
#: which is why the meaning change belongs in the version comment.
#: 11: adds `scop_total_90d`, the same SCOP over the 90-day window. The health
#: grade is built from the 30-day figure while its scorer's range is documented
#: as a *seasonal* one, so the pool needs both windows side by side to tell a
#: shoulder-season 30-day figure from a genuinely poor installation. The number
#: already existed as an attribute on the SCOP sensor; the export is what makes
#: it poolable. Also adds `counters.revert_reasons`: `reverts` says how often the
#: curve was kicked out, the per-code counts say whether it was a transport
#: failure, a pump that rejected the write, a table we computed wrong or a user
#: switching control off — four different answers to the same number.
#: 12: fills `plant.sensor_mode` on cloud installations. Cloud/HTTP reports the
#: indoor source as a name ("bt2", "ext_room_sensor") while Modbus reports holding
#: 9 as an integer, and every plant setting is coerced through a numeric check —
#: so the name was dropped and the field read null on all 31 cloud rows in the
#: pool while the other seven arrived as numbers. The export maps the two names
#: to `SensorMode`'s integers; an unrecognised name still exports null.
#: 13: two changes to what a row can be weighed with. `device.firmware` becomes
#: the **display** component only: the device registry holds `display/cc/inv`
#: when the cloud wrote it and the display version alone when the Modbus probe
#: did, because cc/inv exist only in the cloud metadata — the same build was
#: therefore listed twice in the pool. And `fitted` gains `scop_coverage_days`
#: and `building_coverage_days`, the observed days behind the SCOP and the
#: heat-loss fit: without them a SCOP over seven scattered days reads exactly
#: like one over thirty, which is what an installation that only runs while its
#: owner tests produces.
#: 14: `curve.trim_max_abs_c` and `curve.trim_cap_days`. The trims were not in
#: the export at all, so the pool could not see that one of them was sitting on
#: `TRIM_MAX_C` — `counters.trim_updates` says how often the table changed, never
#: that the integrator had run out of room. The age is a **duration**, not the
#: date `trim_cap_since` carries in the diagnostics: the counters block is counts
#: only, and a date would leave the installation.
#: 15: `counters` on a cloud row. The block was documented as Modbus-only because
#: the curve was, and now that the curve runs on an elevated cloud account a
#: cloud row carries reverts, trim updates and the days a knot was clamped. A
#: consumer that learned "counters is always zero on cloud" is the reader this
#: version exists for; nothing about the block's shape changed.
#: 16: `fitted.dhw_standing_episodes` and `fitted.dhw_standing_hours`. The
#: coefficient is a blend and has no window of its own, so a fresh installation
#: and a mature one carried the same figure — the pool could compare the
#: number but not weigh it. `thermal_episodes` already does this for the
#: building fit.
SUMMARY_SCHEMA_VERSION = 16

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
    # The evidence behind the coefficient: it is an EMA, so the published figure
    # is the same whether it rests on one tank-cooling night or twenty.
    "dhw_standing_episodes",
    "dhw_standing_hours",
    "compressor_starts_per_hour",
    "compressor_run_hours_24h",
    "compressor_duty_cycle",
    "scop_total",
    # The same figure over the 90-day window. The health grade reads the 30-day
    # one, so a shoulder-season row and a poor installation look alike without
    # the longer window beside it.
    "scop_total_90d",
    "aux_heat_share",
    "efficiency_health_grade",
    "efficiency_health_score",
    "efficiency_health_coverage",
    # How much of each window was actually observed. A SCOP over 30 days and a
    # SCOP over seven scattered days read the same in a row without them, and
    # an installation that only runs while its owner tests produces the second
    # while looking like the first.
    "scop_coverage_days",
    "building_coverage_days",
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


#: Cloud/HTTP reports the indoor sensor source as a **name** while Modbus reports
#: the register integer (``SensorMode``). The export carries the integer on both
#: transports so a cloud row and a Modbus row can be compared — which is why
#: ``sensor_mode`` is the one plant field that was null on every cloud row while
#: the other seven arrived as numbers.
_SENSOR_MODE_NAMES = {
    SENSOR_MODE_HTTP_BT2: int(SensorMode.BT2),
    SENSOR_MODE_HTTP_EXT_ROOM_SENSOR: int(SensorMode.EXTERNAL),
}


def _plant_value(key: str, value: Any) -> Any:
    """Coerce one plant setting, normalising the cloud's sensor-mode names."""
    if key == "sensor_mode" and isinstance(value, str):
        return _SENSOR_MODE_NAMES.get(value.strip().lower())
    return _number(value)


def plant_summary(values: Mapping[str, Any]) -> dict[str, Any]:
    """Whitelisted control-loop settings, coerced to numbers or ``None``."""
    return {key: _plant_value(key, values.get(key)) for key in PLANT_KEYS}


def device_metadata(hass: Any, device_id: str, entry_id: str) -> Any:
    """The HA device entry for a pump id, or ``None``.

    ``coordinator.device_id`` is the *pump's* API id (cloud id or Modbus
    serial), never a device-registry id: the entry is found by the identifier
    the integration registered, scoped to the owning config entry. Identifiers
    are only unique *per config entry*, so ``async_get_device(identifiers=…)``
    is deprecated and ambiguous; ``async_get_device_by_identifier`` is the
    lookup that stays correct when two entries register the same pump. The
    registry is absent in a stripped harness, so this is best effort — an
    export without metadata beats no export.
    """
    try:
        registry = dr.async_get(hass)
    except (KeyError, RuntimeError):
        return None
    return registry.async_get_device_by_identifier(
        (DOMAIN, f"qvantum-{device_id}"), entry_id
    )


def _display_firmware(sw_version: object) -> str | None:
    """The display component of ``sw_version``, or ``None``.

    The device registry carries ``display/cc/inv`` when the cloud wrote it and
    the display version alone when the Modbus probe did, because the two extra
    component versions are only in the cloud's metadata. The export keeps the
    display part so one build cannot appear in the pool under two spellings —
    measured: ``1.12.8/436/239`` and ``1.12.8`` are the same pump, and the
    firmware column already lists both.
    """
    text = _short_string(sw_version)
    if text is None:
        return None
    return text.split("/", 1)[0] or None


def device_summary(device: Any) -> dict[str, Any]:
    """Manufacturer/model/firmware — device metadata, never user data."""
    if device is None:
        return {"manufacturer": None, "model": None, "firmware": None, "hardware": None}
    return {
        "manufacturer": _short_string(getattr(device, "manufacturer", None)),
        "model": _short_string(getattr(device, "model", None)),
        "firmware": _display_firmware(getattr(device, "sw_version", None)),
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


def _trim_max_abs(coordinator: Any) -> float | None:
    """Largest absolute trim across the knots, or ``None`` when there are none."""
    trims = getattr(coordinator, "_trims", None)
    if not isinstance(trims, Mapping):
        return None
    values = [
        abs(value)
        for value in trims.values()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    return _number(max(values)) if values else 0.0


def _trim_cap_days(coordinator: Any) -> float | None:
    """How many days the oldest capped trim has been capped, or ``0``.

    A duration rather than the date the diagnostics carry: the counters block is
    counts only. ``None`` when the clock is unusable.
    """
    stamps = getattr(coordinator, "_trim_cap_since", None)
    if not isinstance(stamps, Mapping) or not stamps:
        return 0.0
    oldest = [
        stamp
        for stamp in stamps.values()
        if isinstance(stamp, (int, float)) and not isinstance(stamp, bool)
    ]
    if not oldest:
        return 0.0
    # ``dt_util`` and not ``time.time()``: the stamps come from
    # ``now_ts`` in the curve coordinator, which is ``dt_util.utcnow()``, and HA
    # freezes that in tests. Mixing the two made the age untestable and would
    # have drifted if HA ever virtualised the clock.
    age_days = (dt_util.utcnow().timestamp() - min(oldest)) / 86400.0
    return _number(max(0.0, age_days))


def curve_summary(coordinator: Any) -> dict[str, Any] | None:
    """The identified pump room gain, configured against fitted, plus evidence.

    Three numbers, not one: **configured** is the pump's own setting,
    **applied** is what the correction actually uses (the factor when it is
    usable, else the fit) and **fitted** is the measured slope. This is the first
    pooled question — does the configured factor equal the measured slope? — and
    one installation only fixes the linear map at one factor value.

    Every field here is the *evidence* for a pooled question, not just its
    outcome: which gate refused the model (``notes``/``blocker``), the `t` and
    R² behind the aperture, and the signed median beside the absolute one. A row
    that carries only the outcome cannot distinguish "the gate is too strict"
    from "there is nothing to identify yet", which is the question the pool
    exists to answer.
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
        # The signed median beside the absolute one: a constant offset (the room
        # term left in, a baseline shift) and noise around zero look the same in
        # `median_abs_c` and need opposite answers.
        "median_c": _number(getattr(snapshot, "median_c", None)),
        "p95_abs_c": _number(getattr(snapshot, "p95_abs_c", None)),
        "ready": bool(getattr(snapshot, "ready", False)),
        # Why the gate is not met (`window`, `median`, `max`, the missing
        # signal, or `active` = not assessed because the curve is writing) and
        # the window it was judged over. Without the blocker a row that failed
        # the median gate is indistinguishable from one that never had the
        # coverage to be judged at all — or from one that was never judged.
        "blocker": _short_string(getattr(snapshot, "blocker", None)),
        "window_hours": _number(getattr(snapshot, "window_hours", None)),
        # The house's own room error over the same window. Readiness compares
        # the baseline against the pump's target, which carries the room term,
        # so a deviation cannot be weighed without it.
        "room_error_median_abs_c": _number(
            getattr(snapshot, "room_error_median_abs_c", None)
        ),
        "room_error_p90_abs_c": _number(
            getattr(snapshot, "room_error_p90_abs_c", None)
        ),
        "baseline_points": len(baseline) if isinstance(baseline, Mapping) else None,
        # The trim layer, which the export never carried. `trim_max_abs_c` says
        # how close the largest trim is to `TRIM_MAX_C` and `trim_cap_days` how
        # long the oldest capped one has been capped — the difference between
        # "the integrator converged at the cap" and "it is stuck against it".
        "trim_max_abs_c": _trim_max_abs(coordinator),
        "trim_cap_days": _trim_cap_days(coordinator),
        "solar_a_w_per_k": _number(getattr(model, "a_w_per_k", None)),
        "solar_b_m2": _number(getattr(model, "b_m2", None)),
        "solar_trust": _number(getattr(model, "trust", None)),
        "daily_solar_b_m2": _number(getattr(daily, "b_m2", None)),
        "daily_solar_trust": _number(getattr(daily, "trust", None)),
        "daily_solar_days": _number(getattr(daily, "n_days", None)),
        # The evidence behind the day-block model, not just its outcome. An
        # invalid model zeroes `b` and `trust`, so v9 rows could say "no model"
        # but not whether the day count, the t-stat, R² or a non-positive
        # aperture refused it — the difference between "the gate is too strict"
        # and "there is nothing to identify yet".
        "daily_solar_t_stat": _number(getattr(daily, "t_stat", None)),
        "daily_solar_r2": _number(getattr(daily, "r2", None)),
        "daily_solar_valid": bool(getattr(daily, "valid", False)),
        "daily_solar_notes": _note(getattr(daily, "notes", None)),
        # Wind column of the same day balance: the identified sensitivity (W/K
        # per m/s), whether it passed its gates, and the evidence behind it.
        # The pool question is "how often is a wind term identifiable at all",
        # which no single installation can answer.
        "daily_wind_w_per_ms": _number(getattr(daily, "w_per_ms", None)),
        "daily_wind_t_stat": _number(getattr(daily, "w_t_stat", None)),
        "daily_wind_hours": _number(getattr(daily, "wind_hours", None)),
        "daily_wind_cold_hours": _number(getattr(daily, "wind_cold_hours", None)),
        # Days the cold+windy hours are spread over — the gate's other half,
        # because the four-column fit runs on day blocks.
        "daily_wind_evidence_days": _number(
            getattr(daily, "wind_evidence_days", None)
        ),
        "daily_wind_vif": _number(getattr(daily, "wind_vif", None)),
        "daily_wind_valid": bool(getattr(daily, "wind_valid", False)),
        "wind_c": _number(getattr(snapshot, "wind_c", None)),
    }


#: Curves-side counters: how often the installation actually lived with a
#: clamped or extrapolated knot, and how often the table changed. Counts only —
#: the pool needs a rate, never a log of when. ``days_clamped`` deliberately
#: excludes a warm end pinned on the pump's floor: that clamp is structural and
#: would otherwise fire on every mild day at every installation.
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
    payload = {
        key: (
            _short_string(counters.get(key))
            if key == "since"
            else _number(counters.get(key))
        )
        for key in COUNTER_KEYS
    }
    # Why, not just how often. The codes are a fixed set, so the block stays
    # counts and never a log; an absent code is zero, which is what makes
    # "this installation never reverted on a write" expressible at all.
    reasons = counters.get("revert_reasons")
    if not isinstance(reasons, Mapping):
        reasons = {}
    payload["revert_reasons"] = {
        code: int(_number(reasons.get(code)) or 0) for code in REVERT_REASONS
    }
    return payload


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
    # ``scop_coverage_days`` is the snapshot's ``coverage_days``: the weakest
    # counter's observed days in the 30-day SCOP window.
    attribute = {"scop_coverage_days": "coverage_days"}
    return {
        key: (
            _short_string(getattr(snapshot, attribute.get(key, key), None))
            if key == "efficiency_health_grade"
            else _number(getattr(snapshot, attribute.get(key, key), None))
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
            transport=transport_kind(coordinator),
            device=(
                device_metadata(hass, str(device_id), entry.entry_id)
                if device_id
                else None
            ),
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
