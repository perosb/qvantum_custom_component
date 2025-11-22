"""Pytest conftest to provide lightweight Home Assistant stubs for unit tests.

This file injects minimal module and class stubs into `sys.modules` so that
the component modules can be imported without the full Home Assistant runtime.
It avoids metaclass conflicts by ensuring all entity base classes use the
default `type` metaclass.
"""

import sys
import importlib
import datetime
from types import ModuleType


class Entity:
    """Minimal stand-in for Home Assistant Entity base class."""


class CoordinatorEntity(Entity):
    """Minimal stand-in for CoordinatorEntity."""

    def __init__(self, coordinator=None):
        self.coordinator = coordinator


class BinarySensorEntity(Entity):
    pass


class SensorEntity(Entity):
    pass


class SwitchEntity(Entity):
    pass


class FanEntity(Entity):
    pass


class ClimateEntity(Entity):
    pass


class NumberEntity(Entity):
    pass


class EntityDescription:
    pass


def _stub_module(name: str, attrs: dict):
    m = ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


# Remove any already-imported Home Assistant modules (pytest plugins may import them)
for key in list(sys.modules.keys()):
    if key.startswith("homeassistant"):
        del sys.modules[key]


# Create minimal package modules to satisfy imports
sys.modules.setdefault("homeassistant", ModuleType("homeassistant"))
sys.modules.setdefault("homeassistant.helpers", ModuleType("homeassistant.helpers"))
sys.modules.setdefault(
    "homeassistant.components", ModuleType("homeassistant.components")
)

# Core helper modules used by the integration
_stub_module(
    "homeassistant.helpers.entity",
    {
        "Entity": Entity,
        "EntityDescription": EntityDescription,
    },
)

_stub_module(
    "homeassistant.helpers.update_coordinator",
    {"CoordinatorEntity": CoordinatorEntity},
)

# Platform entity classes
_stub_module(
    "homeassistant.components.binary_sensor", {"BinarySensorEntity": BinarySensorEntity}
)
_stub_module("homeassistant.components.sensor", {"SensorEntity": SensorEntity})
_stub_module("homeassistant.components.switch", {"SwitchEntity": SwitchEntity})
_stub_module("homeassistant.components.fan", {"FanEntity": FanEntity})
_stub_module("homeassistant.components.climate", {"ClimateEntity": ClimateEntity})
_stub_module("homeassistant.components.number", {"NumberEntity": NumberEntity})

# Provide a minimal homeassistant.const module for any constants usage
_stub_module(
    "homeassistant.const",
    {
        "Platform": type("Platform", (), {}),
        "MAJOR_VERSION": 2025,
        "MINOR_VERSION": 11,
        "PATCH_VERSION": 2,
    },
)

# Stub more HA modules
_stub_module(
    "homeassistant.config_entries", {"ConfigEntry": type("ConfigEntry", (), {})}
)

# Stub homeassistant.core used in services and elsewhere
_stub_module(
    "homeassistant.core",
    {
        "HomeAssistant": object,
        "ServiceCall": type("ServiceCall", (), {}),
        "SupportsResponse": type("SupportsResponse", (), {"OPTIONAL": None}),
        "callback": lambda f: f,
    },
)

# Stub config_validation submodule referenced by the integration
_stub_module("homeassistant.helpers.config_validation", {})

# Note: QvantumAPI is imported inside fixtures to avoid module-level import issues
"""Test configuration for Qvantum integration."""

import sys
import os

# Add the project root to the Python path at the very beginning
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

sys.modules["homeassistant"] = MagicMock()
sys.modules["homeassistant.core"] = MagicMock()
sys.modules["homeassistant.helpers"] = MagicMock()
sys.modules["homeassistant.helpers.device_registry"] = MagicMock()
sys.modules["homeassistant.helpers.entity_registry"] = MagicMock()
sys.modules["homeassistant.helpers.update_coordinator"] = MagicMock()
sys.modules["homeassistant.components.sensor"] = MagicMock()
sys.modules["homeassistant.components.binary_sensor"] = MagicMock()
sys.modules["homeassistant.const"] = MagicMock()
sys.modules["homeassistant.const"].__version__ = "2025.11.2"
sys.modules["homeassistant.const"].CONF_USERNAME = "username"
sys.modules["homeassistant.const"].CONF_PASSWORD = "password"
sys.modules["homeassistant.helpers.device_registry"] = MagicMock()
sys.modules["homeassistant.helpers.entity_platform"] = MagicMock()
sys.modules["homeassistant.util"] = MagicMock()
sys.modules["homeassistant.config_entries"] = MagicMock()
sys.modules["homeassistant.components"] = MagicMock()
sys.modules["homeassistant.components.climate"] = MagicMock()
sys.modules["homeassistant.components.number"] = MagicMock()
sys.modules["homeassistant.components.switch"] = MagicMock()
sys.modules["homeassistant.components.fan"] = MagicMock()

# Some integrations import submodules like `homeassistant.components.climate.const`.
# Ensure those submodules exist so Python's import machinery works when a package
# module is a MagicMock (which is not a package).
import types


class _HVACMode:
    HEAT = "heat"


class _HVACAction:
    HEATING = "heating"
    IDLE = "idle"
    DEFROSTING = "defrosting"


class _ClimateEntityFeature:
    TARGET_TEMPERATURE = 1


climate_const = types.ModuleType("homeassistant.components.climate.const")
climate_const.HVACMode = _HVACMode
climate_const.HVACAction = _HVACAction
climate_const.ClimateEntityFeature = _ClimateEntityFeature
sys.modules["homeassistant.components.climate.const"] = climate_const

# Mock custom components
# sys.modules['custom_components'] = MagicMock()
# qvantum_mock = MagicMock()
# qvantum_mock.DOMAIN = "qvantum"
# sys.modules['custom_components.qvantum'] = qvantum_mock
# Don't mock the api module so we can import the real QvantumAPI
# sys.modules['custom_components.qvantum.api'] = MagicMock()
# sys.modules['custom_components.qvantum.coordinator'] = MagicMock()

# Mock the const module with all required constants
const_mock = MagicMock()
const_mock.DOMAIN = "qvantum"
const_mock.CONF_USERNAME = "username"
const_mock.CONF_PASSWORD = "password"
const_mock.FAN_SPEED_STATE_OFF = "off"
const_mock.FAN_SPEED_STATE_NORMAL = "normal"
const_mock.FAN_SPEED_STATE_EXTRA = "extra"
const_mock.FAN_SPEED_VALUE_OFF = 0
const_mock.FAN_SPEED_VALUE_NORMAL = 1
const_mock.FAN_SPEED_VALUE_EXTRA = 2
const_mock.DEFAULT_ENABLED_METRICS = ["bt1", "bt2"]
const_mock.DEFAULT_DISABLED_METRICS = ["bt3", "bt4"]
const_mock.__version__ = "2025.11.2"  # Mock HA version
sys.modules["custom_components.qvantum.const"] = const_mock

# from custom_components.qvantum.const import DOMAIN
# Don't import QvantumAPI at module level
# from custom_components.qvantum.api import QvantumAPI
# from custom_components.qvantum.coordinator import QvantumDataUpdateCoordinator


@pytest.fixture
def hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    hass.data = {}
    hass.config_entries = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

    # Mock registries
    hass.data["device_registry"] = MagicMock()
    hass.data["entity_registry"] = MagicMock()

    return hass


@pytest.fixture
def mock_api():
    """Create a mock QvantumAPI instance."""
    api = MagicMock()
    api.authenticate = AsyncMock()
    api.get_devices = AsyncMock(return_value=[{"id": "test_device", "model": "QE-6"}])
    api.get_primary_device = AsyncMock(
        return_value={"id": "test_device", "model": "QE-6"}
    )
    api.get_metrics = AsyncMock(return_value={"metrics": {"hpid": "test_device"}})
    api.get_settings = AsyncMock(return_value={"settings": {}})
    api.get_device_metadata = AsyncMock(return_value={"uptime_hours": 100})
    api.get_available_metrics = AsyncMock(return_value=["bt1", "bt2"])
    return api


@pytest.fixture
def mock_config_entry():
    """Create a mock config entry."""
    config_entry = MagicMock()
    config_entry.data = {"username": "test@example.com", "password": "test_password"}
    config_entry.options = {}
    config_entry.entry_id = "test_entry"
    config_entry.runtime_data = MagicMock()
    return config_entry


@pytest.fixture
def authenticated_api(mock_session):
    """Create a QvantumAPI instance with valid token and expiry."""
    QvantumAPI = importlib.import_module("custom_components.qvantum.api").QvantumAPI
    api = QvantumAPI("test@example.com", "password", "test-agent", session=mock_session)
    api._token = "test_token"
    api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
    return api


@pytest.fixture
def mock_metrics_response():
    """Pre-configured mock response for metrics data."""
    return {"values": {"bt1": 20.5, "bt2": 21.0}, "total_latency": 100}


@pytest.fixture
def mock_coordinator(mock_api):
    """Create a mock coordinator."""
    coordinator = MagicMock()
    coordinator.api = mock_api
    coordinator.data = {
        "device": {"id": "test_device", "model": "QE-6", "vendor": "Qvantum"},
        "metrics": {"hpid": "test_device", "bt1": 20.5},
        "settings": {},
    }
    coordinator.async_config_entry_first_refresh = AsyncMock()
    return coordinator
