"""Diagnostics support for the Qvantum integration.

Exposes a snapshot of the integration's local state so support can see which
transport is active, what the coordinator last received, and how the local
DHW/extra-DHW state machines look. Credentials, tokens and access expiry
timestamps are redacted or omitted before the payload leaves the instance.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

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
}

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
        "dhw_ema": dhw_ema,
        "shower_event_history_count": len(
            getattr(coordinator, "_shower_event_history", None) or []
        ),
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


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for a Qvantum config entry."""
    runtime = getattr(config_entry, "runtime_data", None)
    diagnostics: dict[str, Any] = {
        "entry": _entry_diagnostics(config_entry),
        "entities": _entity_counts(hass, getattr(config_entry, "entry_id", None)),
        "runtime_data_loaded": runtime is not None,
    }

    if runtime is None:
        return async_redact_data(diagnostics, TO_REDACT)

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
    diagnostics["modbus_link"] = (
        {
            "host": getattr(runtime, "modbus_host", None),
            "port": getattr(runtime, "modbus_port", None),
            "unit_id": getattr(runtime, "modbus_unit_id", None),
        }
        if bool(getattr(coordinator, "modbus_enabled", False))
        else None
    )

    return async_redact_data(diagnostics, TO_REDACT)
