"""Test configuration for Qvantum integration."""

import sys
import os

# Add the project root to the Python path at the very beginning
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
sys.modules['homeassistant'] = MagicMock()
sys.modules['homeassistant.core'] = MagicMock()
sys.modules['homeassistant.helpers'] = MagicMock()
sys.modules['homeassistant.helpers.device_registry'] = MagicMock()
sys.modules['homeassistant.helpers.entity_registry'] = MagicMock()
sys.modules['homeassistant.helpers.update_coordinator'] = MagicMock()
sys.modules['homeassistant.components.sensor'] = MagicMock()
sys.modules['homeassistant.components.binary_sensor'] = MagicMock()
sys.modules['homeassistant.const'] = MagicMock()
sys.modules['homeassistant.const'].__version__ = "2025.11.2"
sys.modules['homeassistant.const'].CONF_USERNAME = "username"
sys.modules['homeassistant.const'].CONF_PASSWORD = "password"
sys.modules['homeassistant.helpers.device_registry'] = MagicMock()
sys.modules['homeassistant.helpers.entity_platform'] = MagicMock()
sys.modules['homeassistant.util'] = MagicMock()
sys.modules['homeassistant.config_entries'] = MagicMock()
sys.modules['homeassistant.components'] = MagicMock()
sys.modules['homeassistant.components.climate'] = MagicMock()
sys.modules['homeassistant.components.number'] = MagicMock()
sys.modules['homeassistant.components.switch'] = MagicMock()
sys.modules['homeassistant.components.fan'] = MagicMock()

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
sys.modules['custom_components.qvantum.const'] = const_mock

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
    api.get_primary_device = AsyncMock(return_value={"id": "test_device", "model": "QE-6"})
    api.get_metrics = AsyncMock(return_value={"metrics": {"hpid": "test_device"}})
    api.get_settings = AsyncMock(return_value={"settings": {}})
    api.get_device_metadata = AsyncMock(return_value={"uptime_hours": 100})
    api.get_available_metrics = AsyncMock(return_value=["bt1", "bt2"])
    return api


@pytest.fixture
def mock_config_entry():
    """Create a mock config entry."""
    config_entry = MagicMock()
    config_entry.data = {
        "username": "test@example.com",
        "password": "test_password"
    }
    config_entry.options = {}
    config_entry.entry_id = "test_entry"
    config_entry.runtime_data = MagicMock()
    return config_entry


@pytest.fixture
def mock_coordinator(hass, mock_api):
    """Create a mock coordinator."""
    coordinator = MagicMock()
    coordinator.api = mock_api
    coordinator.data = {
        "device": {"id": "test_device", "model": "QE-6", "vendor": "Qvantum"},
        "metrics": {"hpid": "test_device", "bt1": 20.5},
        "settings": {}
    }
    coordinator.async_config_entry_first_refresh = AsyncMock()
    return coordinator