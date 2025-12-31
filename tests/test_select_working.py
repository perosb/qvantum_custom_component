"""Tests for Qvantum select entities (working version that avoids metaclass issues)."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest


# Create mock base classes that don't have metaclass conflicts
class MockCoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator


class MockSelectEntity:
    pass


# Mock STATE_ON and STATE_OFF
STATE_ON = "on"
STATE_OFF = "off"


# Patch the imports before importing the select module
with patch(
    "homeassistant.helpers.update_coordinator.CoordinatorEntity", MockCoordinatorEntity
):
    with patch("homeassistant.components.select.SelectEntity", MockSelectEntity):
        with patch(
            "custom_components.qvantum.const.SETTING_UPDATE_APPLIED",
            "APPLIED",
        ):
            with patch(
                "custom_components.qvantum.coordinator.handle_setting_update_response",
                new_callable=AsyncMock,
            ):
                from homeassistant.helpers.device_registry import DeviceInfo

                from custom_components.qvantum.select import QvantumSelectEntity


@pytest.fixture
def mock_coordinator():
    """Create a mock coordinator with test data."""
    coordinator = MagicMock()
    coordinator.data = {
        "device": {"id": "test_device_123"},
        "metrics": {
            "hpid": "test_device_123",
            "use_adaptive": True,  # Default to enabled
            "smart_sh_mode": 0,  # Default to Eco
        },
    }
    coordinator.api = MagicMock()
    coordinator.async_set_updated_data = MagicMock()
    coordinator.async_refresh = AsyncMock()
    return coordinator


@pytest.fixture
def mock_device():
    """Create a mock device info."""
    return DeviceInfo(
        identifiers={("qvantum", "test_device_123")},
        manufacturer="Qvantum",
        model="QE-6",
    )


class TestQvantumSelectEntity:
    """Test the QvantumSelectEntity class."""

    def test_init(self, mock_coordinator, mock_device):
        """Test select entity initialization."""
        entity = QvantumSelectEntity(mock_coordinator, "use_adaptive", mock_device)

        assert entity._hpid == "test_device_123"
        assert entity._metric_key == "use_adaptive"
        assert entity._attr_unique_id == "qvantum_use_adaptive_test_device_123"
        assert entity._attr_device_info == mock_device
        assert entity._attr_has_entity_name is True
        assert entity._attr_options == ["-1", "0", "1", "2"]
        assert entity._attr_icon == "mdi:leaf"
        assert entity._attr_translation_key == "use_adaptive"

    def test_current_option(self, mock_coordinator, mock_device):
        """Test getting current option."""
        entity = QvantumSelectEntity(mock_coordinator, "use_adaptive", mock_device)

        # Test Off (use_adaptive = False)
        mock_coordinator.data["metrics"]["use_adaptive"] = False
        assert entity.current_option == "-1"

        # Test Eco (use_adaptive = True, smart_sh_mode = 0)
        mock_coordinator.data["metrics"]["use_adaptive"] = True
        mock_coordinator.data["metrics"]["smart_sh_mode"] = 0
        mock_coordinator.data["metrics"]["smart_dhw_mode"] = 0
        assert entity.current_option == "0"

        # Test Balanced (use_adaptive = True, smart_sh_mode = 1)
        mock_coordinator.data["metrics"]["smart_sh_mode"] = 1
        mock_coordinator.data["metrics"]["smart_dhw_mode"] = 1
        assert entity.current_option == "1"

        # Test Comfort (use_adaptive = True, smart_sh_mode = 2)
        mock_coordinator.data["metrics"]["smart_sh_mode"] = 2
        mock_coordinator.data["metrics"]["smart_dhw_mode"] = 2
        assert entity.current_option == "2"

        # Test default case (use_adaptive = True, smart_sh_mode = unknown)
        mock_coordinator.data["metrics"]["smart_sh_mode"] = 99
        mock_coordinator.data["metrics"]["smart_dhw_mode"] = 99
        assert entity.current_option == "-1"

        # Test mismatched modes case (use_adaptive = True, sh_mode=0, dhw_mode=1)
        mock_coordinator.data["metrics"]["smart_sh_mode"] = 0
        mock_coordinator.data["metrics"]["smart_dhw_mode"] = 1
        assert entity.current_option == "0"  # Should fall back to smart_sh_mode

        # Test valid sh_mode with invalid dhw_mode (sh_mode=1, dhw_mode=99)
        mock_coordinator.data["metrics"]["smart_sh_mode"] = 1
        mock_coordinator.data["metrics"]["smart_dhw_mode"] = 99
        assert entity.current_option == "1"  # Should fall back to smart_sh_mode

        # Test invalid sh_mode with valid dhw_mode (sh_mode=99, dhw_mode=2)
        mock_coordinator.data["metrics"]["smart_sh_mode"] = 99
        mock_coordinator.data["metrics"]["smart_dhw_mode"] = 2
        assert entity.current_option == "2"  # Should fall back to smart_dhw_mode

    @pytest.mark.asyncio
    async def test_async_select_option(self, mock_coordinator, mock_device):
        """Test selecting an option."""
        entity = QvantumSelectEntity(mock_coordinator, "use_adaptive", mock_device)

        # Mock the API call
        mock_coordinator.api.set_smartcontrol = AsyncMock()

        # Test selecting Off
        await entity.async_select_option("-1")
        mock_coordinator.api.set_smartcontrol.assert_called_with(
            "test_device_123", -1, -1
        )

        # Test selecting Eco
        await entity.async_select_option("0")
        mock_coordinator.api.set_smartcontrol.assert_called_with(
            "test_device_123", 0, 0
        )

        # Test selecting Balanced
        await entity.async_select_option("1")
        mock_coordinator.api.set_smartcontrol.assert_called_with(
            "test_device_123", 1, 1
        )

        # Test selecting Comfort
        await entity.async_select_option("2")
        mock_coordinator.api.set_smartcontrol.assert_called_with(
            "test_device_123", 2, 2
        )

    def test_available(self, mock_coordinator, mock_device):
        """Test availability check."""
        entity = QvantumSelectEntity(mock_coordinator, "use_adaptive", mock_device)

        # Should be available when data exists
        assert entity.available is True

        # Should not be available when coordinator data is None
        mock_coordinator.data = None
        assert entity.available is False

        # Reset and test when metric is missing
        mock_coordinator.data = {"metrics": {}}
        assert entity.available is False
