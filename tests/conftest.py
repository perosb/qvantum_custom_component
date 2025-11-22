"""Pytest conftest to provide minimal custom stubs for unit tests.

The pytest-homeassistant-custom-component plugin provides automatic stubbing
for most Home Assistant modules. This file only contains minimal custom stubs
for integration-specific functionality.
"""

import datetime
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio

# Ensure the pytest-homeassistant-custom-component plugin is loaded
pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture
def hass():
    """Mock Home Assistant instance."""
    mock_hass = Mock()
    mock_hass.data = {}
    mock_hass.config_entries = Mock()
    mock_hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=None)
    mock_hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    return mock_hass


@pytest.fixture
def mock_session():
    """Provide a reusable mocked aiohttp ClientSession for tests."""
    from unittest.mock import AsyncMock, Mock
    
    def make_cm_response(status=200, json_data=None, headers=None):
        resp = Mock()
        resp.status = status
        resp.headers = headers or {}
        resp.json = AsyncMock(return_value=json_data or {})

        cm = Mock()
        cm.__aenter__ = AsyncMock(return_value=resp)
        cm.__aexit__ = AsyncMock(return_value=None)
        return cm, resp

    session = Mock()
    session.make_cm_response = make_cm_response
    return session


@pytest.fixture
def api_with_session(mock_session):
    """Create a QvantumAPI instance with a mocked session and authentication."""
    from custom_components.qvantum.api import QvantumAPI
    
    api = QvantumAPI("test@example.com", "password", "test-agent", session=mock_session)
    api._token = "test_token"
    api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
    api._refreshtoken = "test_refresh"
    return api


@pytest.fixture
def mock_config_entry():
    """Mock ConfigEntry instance for testing."""
    entry = Mock()
    entry.entry_id = "test_entry_id"
    entry.domain = "qvantum"
    entry.data = {
        "username": "test@example.com",
        "password": "test_password",
    }
    entry.title = "Test Qvantum"
    return entry


@pytest.fixture
def mock_api():
    """Mock QvantumAPI instance."""
    api = Mock()
    api.authenticate = AsyncMock(return_value=True)
    # Return test data that matches what the tests expect
    api.get_devices = AsyncMock(
        return_value=[
            {
                "id": "test_device_123",
                "model": "QE-6",
                "serial": "test_device_123",
                "type": "heatpump",
            }
        ]
    )
    api.get_primary_device = AsyncMock(
        return_value={
            "id": "test_device_123",
            "model": "QE-6",
            "serial": "test_device_123",
            "type": "heatpump",
        }
    )
    api.get_metrics = AsyncMock(return_value={"metrics": {}})
    api.get_settings = AsyncMock(return_value={})
    api.close = AsyncMock()
    return api


@pytest.fixture
def mock_coordinator():
    """Mock QvantumDataUpdateCoordinator instance."""
    coordinator = Mock()
    coordinator.data = {}
    coordinator.async_config_entry_first_refresh = AsyncMock()
    return coordinator


@pytest_asyncio.fixture
async def authenticated_api():
    """Authenticated API fixture with mocked session."""
    from custom_components.qvantum.api import QvantumAPI

    # Create real API instance
    api = QvantumAPI("test@example.com", "password", "test-agent")

    # Mock the session with make_cm_response method
    def make_cm_response(status=200, json_data=None, headers=None):
        resp = Mock()
        resp.status = status
        resp.headers = headers or {}
        resp.json = AsyncMock(return_value=json_data or {})

        cm = Mock()
        cm.__aenter__ = AsyncMock(return_value=resp)
        cm.__aexit__ = AsyncMock(return_value=None)
        return cm, resp

    session = Mock()
    session.make_cm_response = make_cm_response
    api._session = session

    # Set up authentication
    api._token = "test_token"
    api._token_expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
    api._refreshtoken = "test_refresh"

    # Mock get_available_metrics
    api.get_available_metrics = AsyncMock(return_value=["bt1", "bt2"])

    return api
