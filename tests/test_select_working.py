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
        "values": {
            "hpid": "test_device_123",
            "use_adaptive": True,  # Default to enabled
            "smart_sh_mode": 0,
            "smart_dhw_mode": 0,
        },
        "metrics": {
            "hpid": "test_device_123",
            "smart_dhw_mode": 0,  # legacy - not used in values mode
        },
    }
    coordinator.api = MagicMock()
    coordinator.async_set_updated_data = MagicMock()
    coordinator.async_refresh = AsyncMock()

    # Mock config_entry and runtime_data for access level check
    config_entry = MagicMock()
    maintenance_coordinator = MagicMock()
    maintenance_coordinator.data = {"access_level": {"writeAccessLevel": 20}}
    config_entry.runtime_data.maintenance_coordinator = maintenance_coordinator
    coordinator.config_entry = config_entry

    return coordinator


@pytest.fixture
def mock_device():
    """Create a mock device info."""
    return DeviceInfo(
        identifiers={("qvantum", "qvantum-test_device_123")},
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
        assert entity._attr_options == ["off", "0", "1", "2"]
        assert entity._attr_icon == "mdi:leaf"
        assert entity._attr_translation_key == "use_adaptive"

    def test_current_option(self, mock_coordinator, mock_device):
        """Test getting current option."""
        entity = QvantumSelectEntity(mock_coordinator, "use_adaptive", mock_device)

        # Test Off (use_adaptive = False)
        mock_coordinator.data["values"]["use_adaptive"] = False
        assert entity.current_option == "off"

        # Test Eco (use_adaptive = True, smart_dhw_mode = 0)
        mock_coordinator.data["values"]["use_adaptive"] = True
        mock_coordinator.data["values"]["smart_sh_mode"] = 0
        mock_coordinator.data["values"]["smart_dhw_mode"] = 0
        assert entity.current_option == "0"

        # Test Eco when smart_sh_mode is missing (use_adaptive = True, smart_dhw_mode = 0)
        if "smart_sh_mode" in mock_coordinator.data["values"]:
            del mock_coordinator.data["values"]["smart_sh_mode"]
        assert entity.current_option == "0"
        # Restore smart_sh_mode for subsequent tests
        mock_coordinator.data["values"]["smart_sh_mode"] = 0
        # Test Balanced (use_adaptive = True, smart_dhw_mode = 1)
        mock_coordinator.data["values"]["smart_sh_mode"] = 1
        mock_coordinator.data["values"]["smart_dhw_mode"] = 1
        assert entity.current_option == "1"

        # Test Comfort (use_adaptive = True, smart_dhw_mode = 2)
        mock_coordinator.data["values"]["smart_sh_mode"] = 2
        mock_coordinator.data["values"]["smart_dhw_mode"] = 2
        assert entity.current_option == "2"

        # Test default case (use_adaptive = True, smart_dhw_mode = unknown)
        mock_coordinator.data["values"]["smart_sh_mode"] = 99
        mock_coordinator.data["values"]["smart_dhw_mode"] = 99
        assert entity.current_option == "off"

        # Test mismatched modes case (use_adaptive = True, sh_mode=0, dhw_mode=2)
        mock_coordinator.data["values"]["smart_sh_mode"] = 0
        mock_coordinator.data["values"]["smart_dhw_mode"] = 2
        assert entity.current_option == "0"

        # Test matching modes case (use_adaptive = True, both mode 0)
        mock_coordinator.data["values"]["smart_sh_mode"] = 0
        mock_coordinator.data["values"]["smart_dhw_mode"] = 0
        assert entity.current_option == "0"

    @pytest.mark.asyncio
    async def test_async_select_option(self, mock_coordinator, mock_device):
        """Test selecting an option."""
        entity = QvantumSelectEntity(mock_coordinator, "use_adaptive", mock_device)

        # Mock the API call
        mock_coordinator.api.set_smartcontrol = AsyncMock()

        # Test selecting Off
        await entity.async_select_option("off")
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

    @pytest.mark.asyncio
    async def test_async_select_option_mode_synchronization_success(
        self, mock_coordinator, mock_device
    ):
        """Test that smart_sh_mode and smart_dhw_mode are updated on successful API response."""
        entity = QvantumSelectEntity(mock_coordinator, "use_adaptive", mock_device)

        # Mock successful API response
        mock_coordinator.api.set_smartcontrol = AsyncMock(
            return_value={"status": "APPLIED"}
        )

        # Test selecting Eco (option "0")
        await entity.async_select_option("0")

        # Verify mode is updated in coordinator data
        assert mock_coordinator.data["values"]["smart_dhw_mode"] == 0

        # Test selecting Balanced (option "1")
        await entity.async_select_option("1")

        # Verify mode is updated
        assert mock_coordinator.data["values"]["smart_dhw_mode"] == 1

    @pytest.mark.asyncio
    async def test_async_select_option_mode_synchronization_heatpump_status(
        self, mock_coordinator, mock_device
    ):
        """Test that modes are updated when heatpump_status indicates success."""
        entity = QvantumSelectEntity(mock_coordinator, "use_adaptive", mock_device)

        # Mock API response with heatpump_status success
        mock_coordinator.api.set_smartcontrol = AsyncMock(
            return_value={"heatpump_status": "APPLIED"}
        )

        # Test selecting Comfort (option "2")
        await entity.async_select_option("2")

        # Verify mode is updated
        assert mock_coordinator.data["values"]["smart_dhw_mode"] == 2

    @pytest.mark.asyncio
    async def test_async_select_option_mode_synchronization_failure(
        self, mock_coordinator, mock_device
    ):
        """Test that modes are not updated when API response indicates failure."""
        entity = QvantumSelectEntity(mock_coordinator, "use_adaptive", mock_device)

        # Set initial mode value
        initial_dhw_mode = mock_coordinator.data["values"]["smart_dhw_mode"]

        # Mock failed API response
        mock_coordinator.api.set_smartcontrol = AsyncMock(
            return_value={"status": "FAILED"}
        )

        # Test selecting Eco (option "0")
        await entity.async_select_option("0")

        # Verify mode is NOT updated
        assert mock_coordinator.data["values"]["smart_dhw_mode"] == initial_dhw_mode

    @pytest.mark.asyncio
    async def test_async_select_option_mode_synchronization_off_option(
        self, mock_coordinator, mock_device
    ):
        """Test that modes are not updated when selecting 'off' option."""
        entity = QvantumSelectEntity(mock_coordinator, "use_adaptive", mock_device)

        # Set initial mode value
        initial_dhw_mode = mock_coordinator.data["values"]["smart_dhw_mode"]

        # Mock successful API response
        mock_coordinator.api.set_smartcontrol = AsyncMock(
            return_value={"status": "APPLIED"}
        )

        # Test selecting Off
        await entity.async_select_option("off")

        # Verify mode is NOT updated (use_adaptive_value is False for "off")
        assert mock_coordinator.data["values"]["smart_dhw_mode"] == initial_dhw_mode

    @pytest.mark.asyncio
    async def test_async_select_option_mode_synchronization_no_response(
        self, mock_coordinator, mock_device
    ):
        """Test that modes are not updated when API returns no response."""
        entity = QvantumSelectEntity(mock_coordinator, "use_adaptive", mock_device)

        # Set initial mode value
        initial_dhw_mode = mock_coordinator.data["values"]["smart_dhw_mode"]

        # Mock None response
        mock_coordinator.api.set_smartcontrol = AsyncMock(return_value=None)

        # Test selecting Balanced (option "1")
        await entity.async_select_option("1")

        # Verify mode is NOT updated
        assert mock_coordinator.data["values"]["smart_dhw_mode"] == initial_dhw_mode

    def test_available(self, mock_coordinator, mock_device):
        """Test availability check."""
        entity = QvantumSelectEntity(mock_coordinator, "use_adaptive", mock_device)

        # Should be available when data exists
        assert entity.available is True

        # Should still be available when use_adaptive is False
        mock_coordinator.data["values"]["use_adaptive"] = False
        assert entity.available is True

        # Should not be available when coordinator data is None
        mock_coordinator.data = None
        assert entity.available is False

        # Reset and test when metric is missing
        mock_coordinator.data = {"values": {}}
        assert entity.available is False

    def test_is_valid_mode(self, mock_coordinator, mock_device):
        """Test the _is_valid_mode helper method."""
        entity = QvantumSelectEntity(mock_coordinator, "use_adaptive", mock_device)

        # Test valid modes
        assert entity._is_valid_mode(0, {0, 1, 2}) is True
        assert entity._is_valid_mode(1, {0, 1, 2}) is True
        assert entity._is_valid_mode(2, {0, 1, 2}) is True
        assert entity._is_valid_mode("0", {0, 1, 2}) is True
        assert entity._is_valid_mode("1", {0, 1, 2}) is True

        # Test invalid modes
        assert entity._is_valid_mode(3, {0, 1, 2}) is False
        assert entity._is_valid_mode(99, {0, 1, 2}) is False
        assert entity._is_valid_mode("abc", {0, 1, 2}) is False
        assert entity._is_valid_mode(None, {0, 1, 2}) is False
        assert entity._is_valid_mode([], {0, 1, 2}) is False


class TestQvantumSelectEntityOperationSensor:
    """Test the use_operation_sensor select entity."""

    def test_init(self, mock_coordinator, mock_device):
        """Test select entity initialization for use_operation_sensor."""
        mock_coordinator.data["values"]["use_operation_sensor"] = 0
        entity = QvantumSelectEntity(
            mock_coordinator, "use_operation_sensor", mock_device
        )

        assert entity._metric_key == "use_operation_sensor"
        assert entity._attr_unique_id == "qvantum_use_operation_sensor_test_device_123"
        assert entity._attr_options == ["0", "1", "2", "3", "4"]
        assert entity._attr_icon == "mdi:motion-sensor"
        assert entity._attr_translation_key == "use_operation_sensor"

    def test_current_option_all_values(self, mock_coordinator, mock_device):
        """Test current_option returns string representation for each valid integer value."""
        mock_coordinator.data["values"]["use_operation_sensor"] = 0
        entity = QvantumSelectEntity(
            mock_coordinator, "use_operation_sensor", mock_device
        )

        for val in range(5):
            mock_coordinator.data["values"]["use_operation_sensor"] = val
            assert entity.current_option == str(val)

    def test_current_option_no_data(self, mock_coordinator, mock_device):
        """Test current_option returns None when metric key is missing from values."""
        mock_coordinator.data["values"]["use_operation_sensor"] = 0
        entity = QvantumSelectEntity(
            mock_coordinator, "use_operation_sensor", mock_device
        )
        del mock_coordinator.data["values"]["use_operation_sensor"]
        assert entity.current_option is None

    @pytest.mark.asyncio
    async def test_async_select_option(self, mock_coordinator, mock_device):
        """Test selecting an option writes directly to Modbus holding register 9."""
        mock_coordinator.data["values"]["use_operation_sensor"] = 0
        mock_coordinator.config_entry.options = {
            "modbus_write": True,
            "modbus_tcp": True,
        }
        entity = QvantumSelectEntity(
            mock_coordinator, "use_operation_sensor", mock_device
        )

        mock_coordinator.api.write_holding_register = AsyncMock(
            return_value={"status": "APPLIED"}
        )

        for opt in ["0", "1", "2", "3", "4"]:
            mock_coordinator.api.write_holding_register.reset_mock()
            await entity.async_select_option(opt)
            mock_coordinator.api.write_holding_register.assert_called_once_with(
                "test_device_123", 9, int(opt)
            )

    def test_available_modbus_write_enabled(self, mock_coordinator, mock_device):
        """Test entity is available when Modbus write is enabled."""
        mock_coordinator.data["values"]["use_operation_sensor"] = 0
        mock_coordinator.config_entry.options = {
            "modbus_write": True,
            "modbus_tcp": True,
        }
        entity = QvantumSelectEntity(
            mock_coordinator, "use_operation_sensor", mock_device
        )
        assert entity.available is True

    def test_available_modbus_write_disabled(self, mock_coordinator, mock_device):
        """Test entity is unavailable when Modbus write is disabled."""
        mock_coordinator.data["values"]["use_operation_sensor"] = 0
        mock_coordinator.config_entry.options = {}
        mock_coordinator.config_entry.data = {}
        entity = QvantumSelectEntity(
            mock_coordinator, "use_operation_sensor", mock_device
        )
        assert entity.available is False
