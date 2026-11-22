"""Tests for the vendored Qvantum client package skeleton."""

import ast
from pathlib import Path

from custom_components.qvantum import client as qvantum_client
from custom_components.qvantum.client import constants as client_constants
from custom_components.qvantum.client.exceptions import (
    APIAuthError,
    APIConnectionError,
    APIRateLimitError,
    AuthError,
    RateLimitError,
    TransportError,
)
from custom_components.qvantum.const import (
    BASE_SYSTEM_POWER_W,
    DHW_MODE_EXTRA,
    FAN_SPEED_STATE_OFF,
    RELAY_STAGE_POWER_MAP,
    SETTING_UPDATE_APPLIED,
    TAP_WATER_CAPACITY_MAPPINGS,
)

CLIENT_ROOT = (
    Path(__file__).resolve().parents[1] / "custom_components" / "qvantum" / "client"
)


def _is_forbidden_absolute(name: str) -> bool:
    return name == "homeassistant" or name.startswith("homeassistant.") or name == "custom_components" or name.startswith(
        "custom_components."
    )


def _import_leaks(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    depth = len(path.relative_to(CLIENT_ROOT).parts) - 1
    max_relative_level = depth + 1
    leaks: list[str] = []
    rel = path.relative_to(CLIENT_ROOT.parent)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_forbidden_absolute(alias.name):
                    leaks.append(f"{rel}:{alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.level > max_relative_level:
                leaks.append(f"{rel}:relative-level-{node.level}")
            module = node.module or ""
            if module and _is_forbidden_absolute(module):
                leaks.append(f"{rel}:{module}")
    return leaks


def test_client_package_does_not_import_homeassistant():
    """The vendored library must stay free of Home Assistant and parent packages."""
    py_files = sorted(CLIENT_ROOT.rglob("*.py"))
    assert py_files, f"no Python files under {CLIENT_ROOT}"
    leaks: list[str] = []
    for path in py_files:
        leaks.extend(_import_leaks(path))
    assert leaks == []


def test_exception_aliases_match_canonical_types():
    assert APIAuthError is AuthError
    assert APIConnectionError is TransportError
    assert APIRateLimitError is RateLimitError


def test_auth_error_without_status_keeps_message():
    err = AuthError(None, "Invalid credentials")
    assert err.status is None
    assert err.message == "Invalid credentials"
    assert str(err) == "Invalid credentials"


def test_transport_error_with_status_appends_code():
    err = TransportError(500, "API request failed")
    assert err.status == 500
    assert err.message == "API request failed"
    assert str(err) == "API request failed: 500"


def test_rate_limit_error_default_message():
    err = RateLimitError(429)
    assert err.status == 429
    assert err.message == "Rate limit exceeded"
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
    assert hasattr(qvantum_client, "QvantumCloudClientProtocol")
    assert hasattr(qvantum_client, "QvantumModbusClientProtocol")
    assert not hasattr(qvantum_client, "QvantumModbusClient")
    assert not hasattr(qvantum_client, "QvantumCloudClient")


def test_transport_protocols_match_concrete_clients():
    """Each client satisfies its transport protocol and not the other's."""
    from unittest.mock import Mock

    from custom_components.qvantum.client.cloud import QvantumCloudClient
    from custom_components.qvantum.client.modbus import QvantumModbusClient
    from custom_components.qvantum.client.protocol import (
        QvantumClient,
        QvantumCloudClientProtocol,
        QvantumModbusClientProtocol,
    )

    cloud = QvantumCloudClient("user", "pass", "agent", session=Mock())
    modbus = QvantumModbusClient()

    assert isinstance(cloud, QvantumClient)
    assert isinstance(modbus, QvantumClient)
    assert isinstance(cloud, QvantumCloudClientProtocol)
    assert isinstance(modbus, QvantumModbusClientProtocol)
    assert not isinstance(cloud, QvantumModbusClientProtocol)
    assert not isinstance(modbus, QvantumCloudClientProtocol)


def test_modbus_shims_removed():
    root = CLIENT_ROOT.parent
    for name in ("modbus.py", "modbus_device.py", "modbus_model.py"):
        assert not (root / name).exists(), f"{name} compatibility shim should be gone"


def test_result_applied_accepts_both_success_shapes():
    """Both transports report success with an APPLIED marker."""
    assert qvantum_client.result_applied({"status": "APPLIED"}) is True
    assert qvantum_client.result_applied({"heatpump_status": "APPLIED"}) is True


def test_result_applied_rejects_failure_shapes():
    """Missing or state-less bodies are not treated as applied."""
    assert qvantum_client.result_applied(None) is False
    assert qvantum_client.result_applied({}) is False
    assert qvantum_client.result_applied({"status": "FAILED"}) is False
    assert qvantum_client.result_applied({"success": True}) is False


def test_every_client_error_shares_one_base() -> None:
    """`except QvantumError` must catch the client's whole vocabulary."""
    from custom_components.qvantum.client.exceptions import (
        AuthError,
        QvantumError,
        RateLimitError,
        TransportError,
    )

    for error in (AuthError, TransportError, RateLimitError):
        assert issubclass(error, QvantumError)


def test_the_error_message_keeps_its_shape() -> None:
    from custom_components.qvantum.client.exceptions import (
        AuthError,
        RateLimitError,
        TransportError,
    )

    assert str(AuthError()) == "Authentication failed"
    assert str(AuthError(401)) == "Authentication failed: 401"
    assert str(AuthError(None, "token refresh failed")) == "token refresh failed"
    assert str(TransportError()) == "API request failed"
    assert str(RateLimitError(429, retry_after=2.0)) == "Rate limit exceeded: 429"
    assert RateLimitError(429, retry_after=2.0).retry_after == 2.0


def test_the_relay_keys_are_shared_between_the_poll_and_the_sensors() -> None:
    """A relay added to one list must not be missing from the other."""
    from custom_components.qvantum.binary_sensor import _DIAGNOSTIC_BINARY_SENSORS
    from custom_components.qvantum.const import (
        BINARY_SENSOR_NAMES,
        DEFAULT_ENABLED_METRICS,
        PICPIN_RELAY_KEYS,
    )

    for key in PICPIN_RELAY_KEYS:
        assert key in DEFAULT_ENABLED_METRICS
        assert key in BINARY_SENSOR_NAMES
        assert key in _DIAGNOSTIC_BINARY_SENSORS
    # The pump bit is Modbus-only, so it is a sensor but not a fetched metric.
    assert "picpin_relay_pump" not in PICPIN_RELAY_KEYS
    assert "picpin_relay_pump" in _DIAGNOSTIC_BINARY_SENSORS
