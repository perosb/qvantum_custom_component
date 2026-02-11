"""Tests for Qvantum fan entities (working version that avoids metaclass issues)."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest


# Create mock base classes that don't have metaclass conflicts
class MockCoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator


class MockFanEntity:
    pass


# Mock FanEntityFeature
class MockFanEntityFeature:
    PRESET_MODE = 1
    TURN_OFF = 2
    TURN_ON = 4


# Mock constants
FAN_SPEED_STATE_OFF = "off"
FAN_SPEED_STATE_NORMAL = "normal"
FAN_SPEED_STATE_EXTRA = "extra"


# Patch the imports before importing the fan module
with patch(
    "homeassistant.helpers.update_coordinator.CoordinatorEntity", MockCoordinatorEntity
):
    with patch("homeassistant.components.fan.FanEntity", MockFanEntity):
        with patch(
            "homeassistant.components.fan.FanEntityFeature", MockFanEntityFeature
        ):
            with patch(
                "custom_components.qvantum.const.SETTING_UPDATE_APPLIED", "APPLIED"
            ):
                with patch(
                    "custom_components.qvantum.const.FAN_SPEED_STATE_OFF", FAN_SPEED_STATE_OFF
                ):
                    with patch(
                        "custom_components.qvantum.const.FAN_SPEED_STATE_NORMAL", FAN_SPEED_STATE_NORMAL
                    ):
                        with patch(
                            "custom_components.qvantum.const.FAN_SPEED_STATE_EXTRA", FAN_SPEED_STATE_EXTRA
                        ):
                            from homeassistant.helpers.device_registry import DeviceInfo

                            from custom_components.qvantum.fan import (
                                QvantumFanEntity,
                                async_setup_entry,
                            )
                            from custom_components.qvantum import RuntimeData


@pytest.fixture
def mock_coordinator():
    """Create a mock coordinator with test data."""
    coordinator = MagicMock()
    coordinator.data = {
        "device": {"id": "test_device_123"},
        "metrics": {
            "hpid": "test_device_123",
        },
        "settings": {
            "fanspeedselector": FAN_SPEED_STATE_NORMAL,
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


class TestQvantumFanEntity:
    """Test the QvantumFanEntity class."""

    def test_init(self, mock_coordinator, mock_device):
        """Test fan entity initialization."""
        entity = QvantumFanEntity(mock_coordinator, "fanspeedselector", mock_device)

        assert entity._hpid == "test_device_123"
        assert entity._metric_key == "fanspeedselector"
        assert entity._attr_unique_id == "qvantum_fanspeedselector_test_device_123"
        assert entity._attr_device_info == mock_device
        assert entity._attr_has_entity_name is True
        assert entity._attr_translation_key == "fanspeedselector"
        assert entity._attr_preset_modes == ["off", "normal", "extra"]
        assert entity._attr_supported_features == (1 | 2 | 4)  # PRESET_MODE | TURN_OFF | TURN_ON

    def test_preset_mode(self, mock_coordinator, mock_device):
        """Test getting preset mode."""
        entity = QvantumFanEntity(mock_coordinator, "fanspeedselector", mock_device)
        assert entity.preset_mode == FAN_SPEED_STATE_NORMAL

    def test_is_on_normal(self, mock_coordinator, mock_device):
        """Test is_on when fan is on normal speed."""
        entity = QvantumFanEntity(mock_coordinator, "fanspeedselector", mock_device)
        assert entity.is_on is True

    def test_is_on_extra(self, mock_coordinator, mock_device):
        """Test is_on when fan is on extra speed."""
        mock_coordinator.data["settings"]["fanspeedselector"] = FAN_SPEED_STATE_EXTRA
        entity = QvantumFanEntity(mock_coordinator, "fanspeedselector", mock_device)
        assert entity.is_on is True

    def test_is_on_off(self, mock_coordinator, mock_device):
        """Test is_on when fan is off."""
        mock_coordinator.data["settings"]["fanspeedselector"] = FAN_SPEED_STATE_OFF
        entity = QvantumFanEntity(mock_coordinator, "fanspeedselector", mock_device)
        assert entity.is_on is False

    def test_available_true(self, mock_coordinator, mock_device):
        """Test entity availability when data exists."""
        entity = QvantumFanEntity(mock_coordinator, "fanspeedselector", mock_device)
        assert entity.available is True

    def test_available_false_missing_key(self, mock_coordinator, mock_device):
        """Test entity availability when key is missing."""
        entity = QvantumFanEntity(mock_coordinator, "missing_fan", mock_device)
        assert entity.available is False

    def test_available_false_none_value(self, mock_coordinator, mock_device):
        """Test entity availability when value is None."""
        mock_coordinator.data["settings"]["fanspeedselector"] = None
        entity = QvantumFanEntity(mock_coordinator, "fanspeedselector", mock_device)
        assert entity.available is False

    @pytest.mark.asyncio
    async def test_async_set_preset_mode(self, mock_coordinator, mock_device):
        """Test setting preset mode."""
        entity = QvantumFanEntity(mock_coordinator, "fanspeedselector", mock_device)

        # Mock the API response
        mock_coordinator.api.set_fanspeedselector = AsyncMock(
            return_value={"status": "APPLIED"}
        )

        await entity.async_set_preset_mode(FAN_SPEED_STATE_EXTRA)

        mock_coordinator.api.set_fanspeedselector.assert_called_once_with(
            "test_device_123", FAN_SPEED_STATE_EXTRA
        )
        assert mock_coordinator.data["settings"]["fanspeedselector"] == FAN_SPEED_STATE_EXTRA
        mock_coordinator.async_set_updated_data.assert_called_once_with(
            mock_coordinator.data
        )

    @pytest.mark.asyncio
    async def test_async_turn_on_with_preset_mode(self, mock_coordinator, mock_device):
        """Test turning on fan with specific preset mode."""
        entity = QvantumFanEntity(mock_coordinator, "fanspeedselector", mock_device)

        # Mock the API response
        mock_coordinator.api.set_fanspeedselector = AsyncMock(
            return_value={"status": "APPLIED"}
        )

        await entity.async_turn_on(preset_mode=FAN_SPEED_STATE_EXTRA)

        mock_coordinator.api.set_fanspeedselector.assert_called_once_with(
            "test_device_123", FAN_SPEED_STATE_EXTRA
        )
        assert mock_coordinator.data["settings"]["fanspeedselector"] == FAN_SPEED_STATE_EXTRA

    @pytest.mark.asyncio
    async def test_async_turn_on_default(self, mock_coordinator, mock_device):
        """Test turning on fan without preset mode (defaults to normal)."""
        entity = QvantumFanEntity(mock_coordinator, "fanspeedselector", mock_device)

        # Mock the API response
        mock_coordinator.api.set_fanspeedselector = AsyncMock(
            return_value={"status": "APPLIED"}
        )

        await entity.async_turn_on()

        mock_coordinator.api.set_fanspeedselector.assert_called_once_with(
            "test_device_123", FAN_SPEED_STATE_NORMAL
        )
        assert mock_coordinator.data["settings"]["fanspeedselector"] == FAN_SPEED_STATE_NORMAL

    @pytest.mark.asyncio
    async def test_async_turn_off(self, mock_coordinator, mock_device):
        """Test turning off fan."""
        entity = QvantumFanEntity(mock_coordinator, "fanspeedselector", mock_device)

        # Mock the API response
        mock_coordinator.api.set_fanspeedselector = AsyncMock(
            return_value={"status": "APPLIED"}
        )

        await entity.async_turn_off()

        mock_coordinator.api.set_fanspeedselector.assert_called_once_with(
            "test_device_123", FAN_SPEED_STATE_OFF
        )
        assert mock_coordinator.data["settings"]["fanspeedselector"] == FAN_SPEED_STATE_OFF

    @pytest.mark.asyncio
    async def test_set_fanspeedselector_api_failure(self, mock_coordinator, mock_device):
        """Test that data is not updated when API call fails."""
        entity = QvantumFanEntity(mock_coordinator, "fanspeedselector", mock_device)

        # Mock the API response with failure
        mock_coordinator.api.set_fanspeedselector = AsyncMock(
            return_value={"status": "FAILED"}
        )

        await entity.async_set_preset_mode(FAN_SPEED_STATE_EXTRA)

        # Data should not be updated
        assert mock_coordinator.data["settings"]["fanspeedselector"] == FAN_SPEED_STATE_NORMAL
        mock_coordinator.async_set_updated_data.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_setup_entry_with_fan_support(
        self, hass, mock_config_entry, mock_coordinator, mock_device
    ):
        """Test setting up fan entities when device supports fan control."""
        mock_config_entry.runtime_data = RuntimeData(
            coordinator=mock_coordinator,
            device=mock_device,
            maintenance_coordinator=None,
        )

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Check that entities were added
        assert async_add_entities.called
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 1
        assert isinstance(entities[0], QvantumFanEntity)

    @pytest.mark.asyncio
    async def test_async_setup_entry_without_fan_support(
        self, hass, mock_config_entry, mock_coordinator, mock_device
    ):
        """Test setting up fan entities when device does not support fan control."""
        # Remove fanspeedselector from settings
        mock_coordinator.data["settings"].pop("fanspeedselector", None)

        mock_config_entry.runtime_data = RuntimeData(
            coordinator=mock_coordinator,
            device=mock_device,
            maintenance_coordinator=None,
        )

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Check that no entities were added
        assert async_add_entities.called
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 0