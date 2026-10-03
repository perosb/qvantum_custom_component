"""Shared payload shapes for the Qvantum client.

These TypedDicts document the HTTP-shaped dicts both transports return.
They are not runtime-validated; coordinators still consume plain dicts.
"""

from __future__ import annotations

from typing import Any, TypedDict

from .constants import SETTING_UPDATE_APPLIED


class MetricsPayload(TypedDict, total=False):
    """``get_metrics`` result: ``{"metrics": {name: value, ...}}``."""

    metrics: dict[str, Any]


class SettingItem(TypedDict):
    name: str
    value: Any


class SettingsPayload(TypedDict, total=False):
    """``get_settings`` result: ``{"settings": [{"name", "value"}, ...]}``."""

    settings: list[SettingItem]


class ApplyResult(TypedDict, total=False):
    """Write result. Success is ``status`` or ``heatpump_status`` == ``APPLIED``."""

    status: str
    heatpump_status: str


class Device(TypedDict, total=False):
    """Identity returned by cloud inventory or a Modbus probe."""

    id: str
    serial: str
    vendor: str
    model: str
    sw_version: str | None


def result_applied(result: dict[str, Any] | None) -> bool:
    """Return True when a write result reports the setting as applied.

    Modbus answers ``{"status": "APPLIED"}``; some cloud endpoints answer
    ``{"heatpump_status": "APPLIED"}``. A settings PATCH answers
    ``{"success": true}``, which carries no state and is not treated as
    applied, so callers fall back to the next poll.
    """
    return bool(
        result
        and (
            result.get("status") == SETTING_UPDATE_APPLIED
            or result.get("heatpump_status") == SETTING_UPDATE_APPLIED
        )
    )
