"""Tests for the vendored Qvantum client package skeleton."""

import ast
from pathlib import Path

from custom_components.qvantum.client.exceptions import (
    APIAuthError,
    APIConnectionError,
    APIRateLimitError,
    AuthError,
    ConnectionError,
    RateLimitError,
)
from custom_components.qvantum.const import (
    BASE_SYSTEM_POWER_W,
    DHW_MODE_EXTRA,
    FAN_SPEED_STATE_OFF,
    RELAY_STAGE_POWER_MAP,
    SETTING_UPDATE_APPLIED,
    TAP_WATER_CAPACITY_MAPPINGS,
)
from custom_components.qvantum import client as qvantum_client
from custom_components.qvantum.client import constants as client_constants


CLIENT_ROOT = (
    Path(__file__).resolve().parents[1] / "custom_components" / "qvantum" / "client"
)


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_client_package_does_not_import_homeassistant():
    """The vendored library must stay free of Home Assistant imports."""
    py_files = sorted(CLIENT_ROOT.rglob("*.py"))
    assert py_files, f"no Python files under {CLIENT_ROOT}"
    leaks: list[str] = []
    for path in py_files:
        for name in _imported_modules(path):
            if name == "homeassistant" or name.startswith("homeassistant."):
                leaks.append(f"{path.relative_to(CLIENT_ROOT.parent)}:{name}")
    assert leaks == []


def test_exception_aliases_match_canonical_types():
    assert APIAuthError is AuthError
    assert APIConnectionError is ConnectionError
    assert APIRateLimitError is RateLimitError


def test_auth_error_without_status_keeps_message():
    err = AuthError(None, "Invalid credentials")
    assert err.status is None
    assert str(err) == "Invalid credentials"


def test_connection_error_with_status_appends_code():
    err = ConnectionError(500, "API request failed")
    assert err.status == 500
    assert str(err) == "API request failed: 500"


def test_rate_limit_error_default_message():
    err = RateLimitError(429)
    assert err.status == 429
    assert str(err) == "Rate limit exceeded: 429"


def test_const_reexports_client_protocol_constants():
    assert SETTING_UPDATE_APPLIED is client_constants.SETTING_UPDATE_APPLIED
    assert TAP_WATER_CAPACITY_MAPPINGS is client_constants.TAP_WATER_CAPACITY_MAPPINGS
    assert RELAY_STAGE_POWER_MAP is client_constants.RELAY_STAGE_POWER_MAP
    assert DHW_MODE_EXTRA == 2
    assert FAN_SPEED_STATE_OFF == "off"
    assert BASE_SYSTEM_POWER_W == 160.0


def test_client_package_exports_protocol():
    assert hasattr(qvantum_client, "QvantumClient")
    assert hasattr(qvantum_client, "MetricsPayload")
