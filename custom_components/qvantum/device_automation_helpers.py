"""Shared helpers for Qvantum device triggers and conditions."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    BINARY_SENSOR_NAMES,
    DEFAULT_DISABLED_HTTP_METRICS,
    DEFAULT_DISABLED_MODBUS_METRICS,
    DEFAULT_ENABLED_HTTP_METRICS,
    DEFAULT_ENABLED_METRICS,
    DEFAULT_ENABLED_MODBUS_METRICS,
    FILTER_SOON_DUE_HOURS,
)

# Metric key -> (entity domain). Used to discover entities for a device.
BINARY_STATUS_METRICS: dict[str, str] = {
    "time_to_defrost": "binary_sensor",
    "compressor_blocked": "binary_sensor",
    "freeze_protection_active": "binary_sensor",
    "wifi_connected": "binary_sensor",
    "cloud_connected": "binary_sensor",
    "alarm_active": "binary_sensor",
}

SENSOR_STATUS_METRICS: dict[str, str] = {
    "ventilation_filter_time_left": "sensor",
}

ALL_STATUS_METRICS: dict[str, str] = {
    **BINARY_STATUS_METRICS,
    **SENSOR_STATUS_METRICS,
}

# Trigger type -> (metric_key, to_state) for binary state triggers.
# "on"/"off" are HA binary_sensor states.
BINARY_TRIGGER_MAP: dict[str, tuple[str, str]] = {
    "defrosting": ("time_to_defrost", "on"),
    "compressor_blocked": ("compressor_blocked", "on"),
    "freeze_protection_active": ("freeze_protection_active", "on"),
    "wifi_disconnected": ("wifi_connected", "off"),
    "cloud_disconnected": ("cloud_connected", "off"),
    "alarm_active": ("alarm_active", "on"),
}

# Condition type -> (metric_key, expected_state) for binary state conditions.
BINARY_CONDITION_MAP: dict[str, tuple[str, str]] = {
    "is_defrosting": ("time_to_defrost", "on"),
    "is_compressor_blocked": ("compressor_blocked", "on"),
    "is_freeze_protection_active": ("freeze_protection_active", "on"),
    "is_wifi_disconnected": ("wifi_connected", "off"),
    "is_cloud_disconnected": ("cloud_connected", "off"),
    "is_alarm_active": ("alarm_active", "on"),
}

TRIGGER_FILTER_SOON_DUE = "filter_soon_due"
CONDITION_FILTER_SOON_DUE = "is_filter_soon_due"
FILTER_METRIC = "ventilation_filter_time_left"

# Broader catalog so longest-prefix matching does not confuse
# compressor_blocked with compressor_blocked_sec (and similar).
_KNOWN_METRIC_KEYS: frozenset[str] = frozenset(
    {
        *BINARY_SENSOR_NAMES,
        *DEFAULT_ENABLED_METRICS,
        *DEFAULT_ENABLED_MODBUS_METRICS,
        *DEFAULT_ENABLED_HTTP_METRICS,
        *DEFAULT_DISABLED_MODBUS_METRICS,
        *DEFAULT_DISABLED_HTTP_METRICS,
        *ALL_STATUS_METRICS,
    }
)
_METRICS_BY_LENGTH = tuple(sorted(_KNOWN_METRIC_KEYS, key=len, reverse=True))


def metric_from_unique_id(unique_id: str | None) -> str | None:
    """Return the status metric encoded in a Qvantum entity unique_id."""
    if not unique_id or not unique_id.startswith("qvantum_"):
        return None
    rest = unique_id[len("qvantum_") :]
    for metric in _METRICS_BY_LENGTH:
        if rest.startswith(f"{metric}_"):
            # Only expose metrics we automate; longer non-status keys win first.
            return metric if metric in ALL_STATUS_METRICS else None
    return None


def async_entries_for_status_metrics(
    hass: HomeAssistant, device_id: str
) -> dict[str, er.RegistryEntry]:
    """Map status metric keys to entity-registry entries for a HA device."""
    registry = er.async_get(hass)
    found: dict[str, er.RegistryEntry] = {}
    for entry in er.async_entries_for_device(registry, device_id):
        metric = metric_from_unique_id(entry.unique_id)
        if metric is None:
            continue
        expected_domain = ALL_STATUS_METRICS.get(metric)
        if expected_domain and entry.domain != expected_domain:
            continue
        found[metric] = entry
    return found


def filter_below_hours() -> float:
    """Return the documented filter-soon-due threshold in hours."""
    return float(FILTER_SOON_DUE_HOURS)
