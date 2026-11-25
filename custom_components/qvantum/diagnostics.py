"""Diagnostics support for the Qvantum integration.

Exposes a snapshot of the integration's local state so support can see which
transport is active, what the coordinator last received, and how the local
DHW/extra-DHW state machines look. Credentials, tokens, access expiry
timestamps and device identifiers (serial/hpid) are redacted or omitted
before the payload leaves the instance.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .open_meteo import hour_ts as weather_hour_ts
from .plant_analytics import HeatingPowerWindow, eu_label

# Keys that must never leave the instance. ``async_redact_data`` applies these
# recursively, so they cover config-entry data as well as nested client state.
TO_REDACT = {
    "password",
    "username",
    "token",
    "access_token",
    "refresh_token",
    "refreshtoken",
    "id_token",
    "accessCode",
    "expiresAt",
    # Device identifiers; also scrubbed dynamically when they appear inside
    # other strings or mapping keys (title, enabled-metrics cache).
    "serial",
    "device_id",
    "hpid",
}

# Coordinator device fields that carry the heat pump serial / hpid.
_IDENTIFIER_KEYS = ("id", "serial", "hpid")

# Local DHW EMA state kept on the coordinator (values only, no timestamps).
_DHW_EMA_FIELDS = (
    "_last_shower_cold_temp",
    "_last_shower_flow_lpm",
    "_last_shower_temp_c",
    "_last_shower_duration_min",
    "_last_tap_water_cap",
    "_last_published_tap_water_cap",
    "_last_published_tap_water_minutes",
    "_tap_water_cap_zero_mode",
    "_tap_water_cap_reheating_floor_mode",
)

# Curve-coordinator timestamps worth surfacing to support (epoch seconds).
_CURVE_TIMESTAMP_FIELDS = (
    "_last_calibration_ts",
    "_last_baseline_learn_ts",
    "_last_active_ts",
    "_active_since_ts",
    "_room_gain_active_ts",
    "_last_trim_ts",
)


def _sensitive_identifiers(config_entry: ConfigEntry, runtime: Any) -> set[str]:
    """Return device identifiers (serial/hpid) that must not leave the instance.

    The config-entry unique id is the device serial set up by the config flow,
    so it also covers diagnostics for an entry that is not loaded yet.
    """
    identifiers: set[str] = set()

    def _add(candidate: Any) -> None:
        if isinstance(candidate, str) and candidate:
            identifiers.add(candidate)

    _add(getattr(config_entry, "unique_id", None))

    coordinator = getattr(runtime, "coordinator", None)
    if coordinator is None:
        return identifiers

    _add(getattr(coordinator, "device_id", None))
    data = getattr(coordinator, "data", None)
    if isinstance(data, dict):
        for section in ("device", "values"):
            section_data = data.get(section)
            if not isinstance(section_data, dict):
                continue
            for key in _IDENTIFIER_KEYS:
                _add(section_data.get(key))
    return identifiers


def _redact_identifier_text(value: str, identifiers: set[str]) -> str:
    """Replace every known device identifier inside a string.

    Longest identifiers first so a serial that contains another identifier
    (or vice versa) is replaced deterministically.
    """
    for identifier in sorted(identifiers, key=len, reverse=True):
        if identifier in value:
            value = value.replace(identifier, REDACTED)
    return value


def _redact_identifiers(value: Any, identifiers: set[str]) -> Any:
    """Recursively replace device identifiers in mapping keys and string values.

    Device ids show up in more than value fields: the config-entry title
    ("Qvantum QE-6 (<serial>)"), ``enabled_metrics`` cache keys and values
    ``hpid`` all embed the serial. Scrub keys as well as values so a dump
    never carries it.
    """
    if not identifiers:
        return value
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            new_key = (
                _redact_identifier_text(key, identifiers)
                if isinstance(key, str)
                else key
            )
            redacted[new_key] = _redact_identifiers(item, identifiers)
        return redacted
    if isinstance(value, list):
        return [_redact_identifiers(item, identifiers) for item in value]
    if isinstance(value, str):
        return _redact_identifier_text(value, identifiers)
    return value


def _entry_diagnostics(config_entry: ConfigEntry) -> dict[str, Any]:
    """Return the config-entry shell without deciding on redaction yet."""
    return {
        "entry_id": getattr(config_entry, "entry_id", None),
        "title": getattr(config_entry, "title", None),
        "version": getattr(config_entry, "version", None),
        "minor_version": getattr(config_entry, "minor_version", None),
        "state": str(getattr(config_entry, "state", None)),
        "data": dict(getattr(config_entry, "data", None) or {}),
        "options": dict(getattr(config_entry, "options", None) or {}),
    }


def _entity_counts(hass: HomeAssistant, entry_id: str | None) -> dict[str, int]:
    """Return total and enabled entity-registry entry counts.

    Registry access is best-effort: diagnostics must render for an entry that
    is not loaded yet or in a stripped test harness.
    """
    try:
        registry = er.async_get(hass)
        entries = er.async_entries_for_config_entry(registry, entry_id)
    except Exception:
        return {"total": 0, "enabled": 0}
    enabled = sum(1 for entry in entries if getattr(entry, "disabled_by", None) is None)
    return {"total": len(entries), "enabled": enabled}


def _iso(value: Any) -> str | None:
    """ISO timestamp for a store/state field, or None when unusable."""
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else None


def _cop_diagnostics(coordinator: Any) -> dict[str, Any]:
    """Return the held COP figures and the DHW charge being measured.

    A DHW COP that is held, not computed, is indistinguishable from a fresh one
    in the entity state, and the charge segment is where the tank balance comes
    from. Both have to be readable from a user's report.
    """
    figures = getattr(coordinator, "_cop_last", None)
    figures = figures if isinstance(figures, dict) else {}
    times = getattr(coordinator, "_cop_last_times", None)
    times = times if isinstance(times, dict) else {}
    charge = getattr(coordinator, "_dhw_charge", None)
    meter = getattr(coordinator, "_dhw_meter", None)
    return {
        "dhw_meter": None if meter is None else _dhw_meter_diagnostics(meter),
        "heating": figures.get("cop_heating"),
        "dhw": figures.get("cop_dhw"),
        "heating_updated_at": _iso(times.get("cop_heating")),
        "dhw_updated_at": _iso(times.get("cop_dhw")),
        "dhw_tank_liters": getattr(coordinator, "_dhw_tank_liters", None),
        # Electrical energy the mode split could not attribute to either ratio
        # since this process started. It is left out of both COPs, so a report
        # that shows it growing points at the counter cadence rather than at the
        # pumps. Reset on restart by construction.
        "unattributed_kwh": round(
            float(getattr(coordinator, "_cop_unattributed_kwh", 0.0) or 0.0), 3
        ),
        # The charging electricity the pump has no meter for. Kept beside the
        # ratios because it explains them: a window where the tank charged reads
        # as a poor `heating` COP unless this figure is subtracted.
        "charge_electrical_kwh": round(
            float(getattr(coordinator, "_dhw_charge_electrical_kwh", 0.0) or 0.0), 3
        ),
        "charge_element_kwh": round(
            float(getattr(coordinator, "_dhw_charge_element_kwh", 0.0) or 0.0), 3
        ),
        # The only durable defrost record: the pump's flag is a binary_sensor
        # without long-term statistics, so a count is what makes a past window
        # answerable. Beside the COP figures because they are read together —
        # an hour with a defrost is not comparable with one without.
        "defrost_count": int(getattr(coordinator, "_defrost_count", 0) or 0),
        "dhw_charge": None
        if charge is None
        else {
            "started": _iso(getattr(charge, "started", None)),
            "start_tank_c": getattr(charge, "start_tank", None),
            "tank_c": getattr(charge, "last_tank", None),
            "draw_kwh": round(getattr(charge, "draw_kwh", 0.0), 3),
            "electrical_kwh": round(getattr(charge, "electrical_kwh", 0.0), 3),
            "compressor_kwh": round(getattr(charge, "compressor_kwh", 0.0), 3),
        },
    }


def _dhw_meter_diagnostics(window: Any) -> dict[str, Any]:
    """The poll-resolution flow × ΔT window behind the DHW meter check.

    The deviation the sensor shows is only as good as the two totals it is
    built from, and they are accumulated over a week: without this block a
    report of "the meter warning is on" cannot be checked against the evidence.
    """
    try:
        estimated, metered, draw_hours = window.totals(dt_util.utcnow().timestamp())
    except Exception:
        return {"error": "unavailable"}
    return {
        "buckets": len(getattr(window, "buckets", {}) or {}),
        "estimated_kwh": round(estimated, 3),
        "metered_kwh": round(metered, 3),
        "draw_hours": round(draw_hours, 2),
    }


#: The efficiency figures a row carries, in the order they appear in the summary
#: export. The dump mirrors them so a null can be read from a report instead of
#: guessed at — the curve established this rule and the envelope fits are the
#: same kind of evidence.
_EFFICIENCY_FIELDS = (
    "scop_total",
    "scop_total_90d",
    "aux_heat_share",
    "heat_loss_w_per_k",
    "heating_degree_hours",
    "weather_normalized_heating",
    "thermal_time_constant",
    "thermal_gain_k_per_h",
    "thermal_mass_kwh_per_k",
    "thermal_episodes",
    "dhw_standing_loss",
    "dhw_standing_episodes",
    "dhw_standing_hours",
    # The DHW-meter comparison: the level carries a structural bias, so the
    # dump also carries the two halves the warning compares.
    "dhw_heat_meter_deviation_recent",
    "dhw_heat_meter_deviation_prior",
    "compressor_starts_per_hour",
    "compressor_run_hours_24h",
    "compressor_duty_cycle",
    "efficiency_health_grade",
    "efficiency_health_score",
    "efficiency_health_coverage",
    "coverage_days",
    "scop_coverage_days_90d",
    "building_coverage_days",
    "building_fit_rows",
    "building_indoor_key",
)


def _efficiency_diagnostics(coordinator: Any) -> dict[str, Any] | None:
    """Mirror the efficiency snapshot, with the fit's own inputs.

    A null heat-loss coefficient has two very different causes — too few hours
    where indoor, outdoor and power coincide, or a slope that is not positive —
    and neither is visible from the published figure. ``building_fit_rows`` and
    ``building_indoor_key`` are the inputs :func:`building.fit_heat_loss` saw.
    """
    snapshot = getattr(coordinator, "data", None)
    if snapshot is None:
        return None
    block: dict[str, Any] = {
        field: getattr(snapshot, field, None) for field in _EFFICIENCY_FIELDS
    }
    # The EU label's unit and class are derived from the SCOP, not stored, so
    # they have no snapshot field to read: a report that cannot show them
    # cannot answer "why is my class A+?" — and the export carries them.
    for suffix, scop_field in (("", "scop_total"), ("_90d", "scop_total_90d")):
        eta_s, eu_class = eu_label(getattr(snapshot, scop_field, None))
        block[f"eta_s_percent{suffix}"] = eta_s
        block[f"eu_class{suffix}"] = eu_class
    block["updated_at"] = getattr(snapshot, "updated_at", None)
    components = getattr(snapshot, "efficiency_health_components", None)
    if isinstance(components, dict):
        block["efficiency_health_components"] = dict(components)
    return block


def _coordinator_diagnostics(coordinator: Any) -> dict[str, Any]:
    """Return poll state, last values and local derived state."""
    data = getattr(coordinator, "data", None)
    data = data if isinstance(data, dict) else {}
    values = data.get("values")
    device = data.get("device")

    cache = getattr(coordinator, "_enabled_metrics_cache", None)
    enabled_metrics: dict[str, Any] = {}
    if isinstance(cache, dict):
        enabled_metrics = {
            str(device_id): sorted(str(metric) for metric in metrics)
            if isinstance(metrics, (list, tuple, set))
            else metrics
            for device_id, metrics in cache.items()
        }

    dhw_ema = {
        field.removeprefix("_"): getattr(coordinator, field, None)
        for field in _DHW_EMA_FIELDS
    }
    modbus_enabled = bool(getattr(coordinator, "modbus_enabled", False))

    return {
        "modbus_enabled": modbus_enabled,
        "modbus_writable": bool(
            getattr(getattr(coordinator, "client", None), "writable", False)
        )
        if modbus_enabled
        else False,
        "poll_interval": getattr(coordinator, "poll_interval", None),
        "device_id": getattr(coordinator, "device_id", None),
        "device": device if isinstance(device, dict) else {},
        "values": values if isinstance(values, dict) else {},
        "enabled_metrics": enabled_metrics,
        "cop": _cop_diagnostics(coordinator),
        "dhw_ema": dhw_ema,
        "shower_event_history_count": len(
            getattr(coordinator, "_shower_event_history", None) or []
        ),
        "external_room_feed": {
            "entity_id": getattr(coordinator, "external_room_temp_entity_id", None),
            "last_value": getattr(coordinator, "_external_room_last_value", None),
            "last_write": getattr(coordinator, "_external_room_last_write_ts", None),
            "write_errors": getattr(coordinator, "_external_room_write_errors", None),
        },
    }


def _extra_dhw_diagnostics(timer: Any) -> dict[str, Any] | None:
    """Return the local extra-DHW restore timer state (Modbus only)."""
    if timer is None:
        return None
    return {
        "restore_at": getattr(timer, "restore_at", None),
        "armed": getattr(timer, "unsub", None) is not None,
    }


def _maintenance_diagnostics(coordinator: Any) -> dict[str, Any] | None:
    """Return firmware and access state, with only numeric access fields."""
    if coordinator is None:
        return None
    data = getattr(coordinator, "data", None)
    data = data if isinstance(data, dict) else {}

    access_level = data.get("access_level")
    numeric_access: dict[str, int | float] = {}
    if isinstance(access_level, dict):
        numeric_access = {
            key: value
            for key, value in access_level.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }

    firmware_versions = data.get("firmware_versions")
    last_versions = getattr(coordinator, "_last_firmware_versions", None)
    return {
        "firmware_versions": firmware_versions
        if isinstance(firmware_versions, dict)
        else {},
        "last_firmware_versions": dict(last_versions)
        if isinstance(last_versions, dict)
        else {},
        "access_level": numeric_access,
        "firmware_changed": data.get("firmware_changed"),
        "last_check": data.get("last_check"),
    }


def _curve_model_diagnostics(model: Any) -> dict[str, Any] | None:
    """Return the fitted solar-model coefficients and confidence."""
    if model is None:
        return None
    return {
        "a_w_per_k": getattr(model, "a_w_per_k", None),
        "b_m2": getattr(model, "b_m2", None),
        "c_w": getattr(model, "c_w", None),
        "trust": getattr(model, "trust", None),
        "r2_opaque": getattr(model, "r2_opaque", None),
        "r2_solar": getattr(model, "r2_solar", None),
        "b_std_err": getattr(model, "b_std_err", None),
        "n_opaque": getattr(model, "n_opaque", None),
        "n_solar": getattr(model, "n_solar", None),
        "valid": getattr(model, "valid", None),
        "notes": getattr(model, "notes", None),
    }


def _curve_trim_seed_diagnostics(coordinator: Any) -> dict[str, Any] | None:
    """The remembered trim set: its values, the baseline it belongs to, its age."""
    trims = getattr(coordinator, "_trim_seed", None)
    baseline = getattr(coordinator, "_trim_seed_baseline", None)
    if not isinstance(trims, dict) or not isinstance(baseline, dict):
        return None
    current = getattr(coordinator, "_baseline", None)
    matches: bool | None = None
    if isinstance(current, dict):
        matches = all(
            abs(float(value) - float(current.get(key, float("nan")))) <= 0.05
            for key, value in baseline.items()
        )
    return {
        "trims": {key: float(value) for key, value in trims.items()},
        "baseline": {key: float(value) for key, value in baseline.items()},
        "at": getattr(coordinator, "_trim_seed_ts", None),
        "baseline_matches": matches,
    }


def _curve_active_gain_diagnostics(estimate: Any) -> dict[str, Any] | None:
    """The room gain measured on active hours, with the evidence for it."""
    if estimate is None:
        return None
    return {
        "gain_c_per_c": getattr(estimate, "gain_c_per_c", None),
        "source": getattr(estimate, "source", None),
        "hours": getattr(estimate, "hours", None),
        "buckets": getattr(estimate, "buckets", None),
        "span_c": getattr(estimate, "span_c", None),
        "std_err_c_per_c": getattr(estimate, "std_err_c_per_c", None),
        "t_stat": getattr(estimate, "t_stat", None),
        "r2": getattr(estimate, "r2", None),
        "residual_std_c": getattr(estimate, "residual_std_c", None),
        "leave_one_out_c_per_c": getattr(estimate, "leave_one_out_c_per_c", None),
        # The absorbed within-bucket outdoor slope: it should land on the
        # curve's own slope. A 0 here means the window held no outdoor
        # variation inside the buckets, so the room slope carries the shape.
        "outdoor_slope_c_per_c": getattr(
            estimate, "outdoor_slope_c_per_c", None
        ),
        # The absorbed time slope in °C/day: the table itself moving while the
        # window filled, because the trims were still integrating. It should
        # land on the trim drift; a 0 means the window held no drift, so the
        # room slope carries it.
        "drift_c_per_day": getattr(estimate, "drift_c_per_day", None),
        "valid": getattr(estimate, "valid", None),
        "notes": getattr(estimate, "notes", None),
    }


def _curve_daily_model_diagnostics(model: Any) -> dict[str, Any] | None:
    """Return the day-block identification (the solar term's actual source).

    Its ``b`` is the aperture the curve applies whenever ``valid``; the hourly
    model's own ``b`` is 0 on a censored window, so this block is where a
    missing solar term is explained.
    """
    if model is None:
        return None
    return {
        "a_w_per_k": getattr(model, "a_w_per_k", None),
        "b_m2": getattr(model, "b_m2", None),
        "c_w": getattr(model, "c_w", None),
        "trust": getattr(model, "trust", None),
        "r2": getattr(model, "r2", None),
        "b_std_err": getattr(model, "b_std_err", None),
        "t_stat": getattr(model, "t_stat", None),
        "n_days": getattr(model, "n_days", None),
        "b_first_half_m2": getattr(model, "b_first_half_m2", None),
        "b_second_half_m2": getattr(model, "b_second_half_m2", None),
        "b_jackknife_min_m2": getattr(model, "b_jackknife_min_m2", None),
        "b_jackknife_max_m2": getattr(model, "b_jackknife_max_m2", None),
        "valid": getattr(model, "valid", None),
        "notes": getattr(model, "notes", None),
        # The fourth column: whether this window could identify a wind
        # coefficient at all, and what the extra column did to a/b. A missing
        # term must be explainable from the dump alone.
        "w_per_ms": getattr(model, "w_per_ms", None),
        "w_std_err": getattr(model, "w_std_err", None),
        "w_t_stat": getattr(model, "w_t_stat", None),
        "wind_hours": getattr(model, "wind_hours", None),
        "wind_cold_hours": getattr(model, "wind_cold_hours", None),
        "wind_evidence_days": getattr(model, "wind_evidence_days", None),
        "wind_vif": getattr(model, "wind_vif", None),
        "w_first_half_ms": getattr(model, "w_first_half_ms", None),
        "w_second_half_ms": getattr(model, "w_second_half_ms", None),
        "wind_valid": getattr(model, "wind_valid", None),
        "wind_notes": getattr(model, "wind_notes", None),
        "a_w_per_k_4col": getattr(model, "a_w_per_k_4col", None),
        "b_m2_4col": getattr(model, "b_m2_4col", None),
    }


def _curve_room_error_diagnostics(snapshot: Any) -> dict[str, Any] | None:
    """Return the indoor-error outcome over the readiness window.

    Readiness compares our baseline with the pump, which says nothing about the
    house once the curve is active. This is the outcome signal for that case.
    """
    if snapshot is None:
        return None
    return {
        "median_abs_c": getattr(snapshot, "room_error_median_abs_c", None),
        "median_c": getattr(snapshot, "room_error_median_c", None),
        "p90_abs_c": getattr(snapshot, "room_error_p90_abs_c", None),
        "samples": getattr(snapshot, "room_error_samples", None),
        "source": getattr(snapshot, "room_error_source", None),
    }


def _curve_diagnostics(coordinator: Any) -> dict[str, Any] | None:
    """Return adaptive heating-curve state (Modbus only).

    Mirrors the persisted curve state plus the live computed snapshot, so a
    support dump shows the frozen baseline, the shared adjustment and its
    terms, the solar model, the active trims, the activation blocker, the
    learned room gain with its source, the indoor-error outcome and the
    per-point trim evidence. Keep it in step with the snapshot: a new curve
    field that is missing here cannot be read from a user's support dump.
    """
    if coordinator is None:
        return None

    snapshot = getattr(coordinator, "data", None)
    main_values = getattr(coordinator, "_main_values", None)
    live = main_values() if callable(main_values) else {}
    forecast = getattr(coordinator, "_forecast", None)
    wind_ms = None
    wind_available = False
    if forecast is not None:
        # The site proxy behind any wind figure: Open-Meteo's 10 m grid wind,
        # not the wind at the house. A dump has to show it to be judgeable.
        wind_by_hour = forecast.wind_by_hour()
        wind_available = bool(wind_by_hour)
        wind_ms = wind_by_hour.get(weather_hour_ts(dt_util.utcnow().timestamp()))
    points = getattr(snapshot, "points", None)
    trims = getattr(coordinator, "_trims", None)
    baseline = getattr(coordinator, "_baseline", None)
    baseline_stats = getattr(coordinator, "_baseline_stats", None)
    margins = getattr(coordinator, "_indoor_margins", None)
    # The load term's input: the poll-resolution mean the main coordinator
    # keeps, plus how much of its window is actually observed. Without the
    # coverage a dump cannot tell a listed zero from a window still filling.
    main = getattr(coordinator, "_main", None)
    power_window = getattr(main, "_heating_power_mean", None)
    coverage_hours = None
    if isinstance(power_window, HeatingPowerWindow):
        coverage_hours = round(
            power_window.coverage_seconds(dt_util.utcnow().timestamp()) / 3600.0, 2
        )

    # The forecast temperature the terms were derived from. The dump carried the
    # outdoor *term* (`outdoor_c`, kelvin, clamped ±1.0) alone, and a report read
    # it as the forecast air temperature and compared it with BT1: measured
    # 2026-11-18, `outdoor_c` −0.07 K against `bt1` 1.1 °C was written up as
    # "1.2 K warmer outside than the forecast", which compares a temperature with
    # a difference. BT1 itself tracks Open-Meteo to +0.01 K median over 238 h, so
    # there was no discrepancy to explain.
    forecast = getattr(coordinator, "_forecast", None)
    forecast_now = None
    if forecast is not None:
        now_hour = int(dt_util.utcnow().timestamp()) // 3600 * 3600
        forecast_now = forecast.temperature_by_hour().get(now_hour)

    return {
        "active": bool(getattr(coordinator, "active", False)),
        "forecast_now_c": forecast_now,
        "writable": bool(getattr(coordinator, "writable", False)),
        "mode": getattr(coordinator, "_mode", None),
        "shadow": None if snapshot is None else getattr(snapshot, "shadow", None),
        "revert_pending": bool(getattr(coordinator, "_revert_pending", False)),
        "forecast_ok": bool(getattr(coordinator, "_forecast_ok", False)),
        "forecast_reused": bool(getattr(coordinator, "_forecast_reused", False)),
        "forecast_wind_ms": wind_ms,
        "forecast_wind_available": wind_available,
        "baseline": dict(baseline) if isinstance(baseline, dict) else {},
        "baseline_auto": getattr(coordinator, "_baseline_auto", None),
        "temp_compensation_curve": getattr(coordinator, "_curve_23", None),
        "baseline_stats": dict(baseline_stats)
        if isinstance(baseline_stats, dict)
        else {},
        "points": dict(points) if isinstance(points, dict) else {},
        "trims": dict(trims) if isinstance(trims, dict) else {},
        "adjustment_c": getattr(snapshot, "adjustment_c", None),
        "outdoor_c": getattr(snapshot, "outdoor_c", None),
        "night_day_c": getattr(snapshot, "night_day_c", None),
        "solar_c": getattr(snapshot, "solar_c", None),
        # Wind's contribution: 0 whenever the optional term is off or the
        # model's wind column failed its gates, which a dump must show.
        "wind_c": getattr(snapshot, "wind_c", None),
        "load_c": getattr(snapshot, "load_c", None),
        "capped_by_indoor": getattr(snapshot, "capped_by_indoor", None),
        # The cap needs a fresh indoor reading: without one it is inert and the
        # curve has no warm-house brake, which a support dump should show.
        "indoor_margin_c": getattr(snapshot, "indoor_margin_c", None),
        # The two inputs the margin is chosen from: a cold house acts on the
        # current reading, a warm one on the 24 h mean within the setpoint
        # regime. Without both, a lagging cap looks like a biased one.
        "indoor_margin_now_c": getattr(snapshot, "indoor_margin_now_c", None),
        "indoor_margin_mean_c": getattr(snapshot, "indoor_margin_mean_c", None),
        "indoor_cap_armed": getattr(snapshot, "indoor_cap_armed", None),
        # Which room value the removed room term used: the firmware blends the
        # external sensor with BT2 in the external mode, so a dump that only
        # showed the configured sensor could not explain the deviation.
        "pump_room_c": getattr(snapshot, "pump_room_c", None),
        # Which input that value came from, and what the pump says about BT2:
        # an unconnected probe holds the firmware's -30 C placeholder, so the
        # blend is skipped and the deviation rests on the configured sensor.
        "pump_room_source": getattr(snapshot, "pump_room_source", None),
        "bt2_detected": live.get("bt2_detected"),
        "clamped": getattr(snapshot, "clamped", None),
        # Which point wants to leave the pump's supply limits: the baseline
        # point is what to adjust, so a dump must name it.
        "clamped_points": [
            str(point)
            for point in (getattr(snapshot, "clamped_points", None) or ())
        ],
        # The warm end pinned on the pump's floor: clamped on every computation
        # and not something the baseline can answer, so it is not in the list
        # above. A dump names them so the two cases stay distinguishable.
        "floor_pinned_points": [
            str(point)
            for point in (getattr(snapshot, "floor_pinned_points", None) or ())
        ],
        "deviation_c": getattr(snapshot, "deviation_c", None),
        "ready": getattr(snapshot, "ready", None),
        "blocker": getattr(snapshot, "blocker", None),
        "median_abs_c": getattr(snapshot, "median_abs_c", None),
        # Signed median: a constant offset and noise around zero share an
        # absolute median and need opposite answers.
        "median_c": getattr(snapshot, "median_c", None),
        "max_abs_c": getattr(snapshot, "max_abs_c", None),
        "p95_abs_c": getattr(snapshot, "p95_abs_c", None),
        "window_hours": getattr(snapshot, "window_hours", None),
        "baseline_learned_hours": getattr(snapshot, "baseline_learned_hours", None),
        "baseline_outdoor_min_c": getattr(snapshot, "baseline_outdoor_min_c", None),
        "baseline_outdoor_max_c": getattr(snapshot, "baseline_outdoor_max_c", None),
        # Readiness only speaks for the outdoor range the baseline was observed
        # at; the rest is extrapolated from the cached table.
        "extrapolated_points": [
            str(point) for point in (getattr(snapshot, "extrapolated_points", None) or ())
        ],
        # The gain is bound to the sensor it was identified from: an unknown
        # source means no correction is applied at all. ``room_gain_factor``
        # is the pump's own setting, so a report shows both numbers.
        "room_gain": getattr(coordinator, "_room_gain", None),
        "room_gain_source": getattr(coordinator, "_room_gain_source", None),
        "room_gain_factor": getattr(coordinator, "_room_gain_factor", None),
        # The fitted measurement beside the applied gain: the applied one may
        # just be the configured factor.
        "room_gain_fitted": getattr(coordinator, "_room_gain_fitted", None),
        "room_gain_fitted_source": getattr(
            coordinator, "_room_gain_fitted_source", None
        ),
        # The measurement taken on hours the pump followed our own table: the
        # only clean slope on an install that runs active permanently. An
        # invalid estimate is kept with its reason, so a dump explains itself.
        "room_gain_active": _curve_active_gain_diagnostics(
            getattr(coordinator, "_room_gain_active", None)
        ),
        "room_gain_from_factor": bool(
            getattr(coordinator, "_room_gain_from_factor", False)
        ),
        # Hours behind each point's trim; 0 means the trim is deliberately 0.
        "trim_evidence": dict(getattr(coordinator, "_trim_evidence", None) or {}),
        # When each trim reached TRIM_MAX_C, cleared when it leaves. The trims
        # themselves are attributes on the point sensors, which the recorder
        # does not keep, so "how long has the cap been binding?" had no answer
        # in the data — two sessions reconstructed it two days apart.
        # Whether the user answered "not now" to the activation invitation. A
        # report cannot otherwise tell "they declined" from "readiness never
        # held long enough to ask".
        "ready_issue_declined": bool(
            getattr(coordinator, "_ready_issue_declined", False)
        ),
        "trim_cap_since": {
            key: stamp
            for key, stamp in (
                getattr(coordinator, "_trim_cap_since", None) or {}
            ).items()
        },
        # The trims the last active period ended with, and the baseline they
        # were learned against: a re-activation applies them only when that
        # baseline is still current, so a dump has to show both sides.
        "trim_seed": _curve_trim_seed_diagnostics(coordinator),
        # Persisted curve counters (reverts, trim updates, days with a clamped
        # or extrapolated knot); they also travel in the summary export.
        "counters": (
            dict(getattr(coordinator, "counters", None) or {})
            if isinstance(getattr(coordinator, "counters", None), dict)
            else None
        ),
        "room_error": _curve_room_error_diagnostics(snapshot),
        "model": _curve_model_diagnostics(getattr(coordinator, "_model", None)),
        # Day-block identification: the aperture the solar term uses when valid.
        "model_daily": _curve_daily_model_diagnostics(
            getattr(coordinator, "_daily_model", None)
        ),
        "calibrated_at": getattr(coordinator, "_calibrated_at", None),
        # None = no attempt since this version loaded, False = the last attempt
        # could not fetch its inputs (it retries within the hour).
        "calibration_ok": getattr(coordinator, "_last_calibration_ok", None),
        "timestamps": {
            field.removeprefix("_"): getattr(coordinator, field, None)
            for field in _CURVE_TIMESTAMP_FIELDS
        },
        "indoor_margins": [
            [ts, margin]
            for ts, margin in (margins or [])
            if isinstance(ts, (int, float)) and isinstance(margin, (int, float))
        ],
        # The setpoint the samples above belong to: the window is cleared when
        # it changes, so without it a dump cannot tell a regime change from a
        # cap that simply drifted.
        "indoor_margins_target": getattr(coordinator, "_indoor_margins_target", None),
        "load_power_mean_w": live.get("heating_power_mean_w"),
        # The electrical mean the implausibility guard compares against; without
        # it a withheld `load_c` is indistinguishable from an observed balance.
        "load_power_mean_electrical_w": live.get("compressor_power_mean_w"),
        # The air side's drop over the same window. Beside the pair because the
        # three together give `(P_thermal − P_elec) / P_elec`, and a support dump
        # without it cannot say whether a pump is short of air.
        "air_delta_mean_k": live.get("air_delta_mean_k"),
        "load_power_coverage_hours": coverage_hours,
        # Knots of the last active write cycle: a dump must distinguish a knot
        # the forecast cannot reach (the pump keeps an older value) from one the
        # write path failed to move.
        "write_scope": [
            key
            for key in (getattr(coordinator, "_write_scope", ()) or ())
            if isinstance(key, str)
        ],
        # Which optional terms are on. A term that did nothing is either off,
        # switched on but gated by its own evidence, or on and contributing
        # nothing — three answers a report cannot separate from the figures.
        "terms": {
            str(key): bool(value)
            for key, value in (getattr(coordinator, "terms", None) or {}).items()
        },
        # The learned COP reference the feedback term compares against, and the
        # samples behind each bucket: a term that stays silent while the
        # reference is empty looks identical to one that was switched off.
        "cop_reference": {
            str(bucket): value
            for bucket, value in (getattr(coordinator, "_cop_reference", None) or {}).items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        },
        "cop_samples": {
            str(bucket): count
            for bucket, count in (getattr(coordinator, "_cop_samples", None) or {}).items()
            if isinstance(count, int) and not isinstance(count, bool)
        },
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for a Qvantum config entry."""
    runtime = getattr(config_entry, "runtime_data", None)
    identifiers = _sensitive_identifiers(config_entry, runtime)
    diagnostics: dict[str, Any] = {
        "entry": _entry_diagnostics(config_entry),
        "entities": _entity_counts(hass, getattr(config_entry, "entry_id", None)),
        "runtime_data_loaded": runtime is not None,
    }

    if runtime is None:
        return async_redact_data(
            _redact_identifiers(diagnostics, identifiers), TO_REDACT
        )

    coordinator = getattr(runtime, "coordinator", None)
    diagnostics["coordinator"] = (
        _coordinator_diagnostics(coordinator) if coordinator is not None else None
    )
    diagnostics["extra_dhw"] = _extra_dhw_diagnostics(
        getattr(runtime, "extra_dhw", None)
    )
    diagnostics["maintenance"] = _maintenance_diagnostics(
        getattr(runtime, "maintenance_coordinator", None)
    )
    diagnostics["curve"] = _curve_diagnostics(
        getattr(runtime, "curve_coordinator", None)
    )
    diagnostics["efficiency"] = _efficiency_diagnostics(
        getattr(runtime, "efficiency_coordinator", None)
    )
    diagnostics["modbus_link"] = (
        {
            "host": getattr(runtime, "modbus_host", None),
            "port": getattr(runtime, "modbus_port", None),
            "unit_id": getattr(runtime, "modbus_unit_id", None),
        }
        if bool(getattr(coordinator, "modbus_enabled", False))
        else None
    )

    return async_redact_data(_redact_identifiers(diagnostics, identifiers), TO_REDACT)
