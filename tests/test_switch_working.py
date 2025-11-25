"""Tests for Qvantum switch entities (working version that avoids metaclass issues)."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from datetime import datetime


# Create mock base classes that don't have metaclass conflicts
class MockCoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator


class MockSwitchEntity:
    pass


# Mock SwitchDeviceClass
class MockSwitchDeviceClass:
    SWITCH = "switch"


# Mock STATE_ON and STATE_OFF
STATE_ON = "on"
STATE_OFF = "off"


# Patch the imports before importing the switch module
with patch(
    "homeassistant.helpers.update_coordinator.CoordinatorEntity", MockCoordinatorEntity
):
    with patch("homeassistant.components.switch.SwitchEntity", MockSwitchEntity):
        with patch(
            "homeassistant.components.switch.SwitchDeviceClass", MockSwitchDeviceClass
        ):
            with patch("homeassistant.const.STATE_ON", STATE_ON):
                with patch("homeassistant.const.STATE_OFF", STATE_OFF):
                    with patch(
                        "custom_components.qvantum.const.SETTING_UPDATE_APPLIED", "APPLIED"
                    ):
                        from homeassistant.helpers.device_registry import DeviceInfo

                        from custom_components.qvantum.switch import QvantumSwitchEntity


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
            "extra_tap_water": None,  # Will be set based on test
            "extra_tap_water_stop": None,
        },
    }
    coordinator.api = MagicMock()
    coordinator.async_set_updated_data = MagicMock()
    return coordinator


@pytest.fixture
def mock_device():
    """Create a mock device info."""
    return DeviceInfo(
        identifiers={("qvantum", "test_device_123")},
        manufacturer="Qvantum",
        model="QE-6",
    )


class TestQvantumSwitchEntity:
    """Test the QvantumSwitchEntity class."""

    def test_init(self, mock_coordinator, mock_device):
        """Test switch entity initialization."""
        entity = QvantumSwitchEntity(mock_coordinator, "extra_tap_water", mock_device)

        assert entity._hpid == "test_device_123"
        assert entity._metric_key == "extra_tap_water"
        assert entity._attr_unique_id == "qvantum_extra_tap_water_test_device_123"
        assert entity._attr_device_info == mock_device
        assert entity._attr_device_class == "switch"
        assert entity._attr_has_entity_name is True
        assert entity._attr_icon == "mdi:water-boiler"
        assert entity._attr_is_on is False
        assert entity._attr_translation_key == "extra_tap_water"

    def test_is_on_none_stop(self, mock_coordinator, mock_device):
        """Test is_on when extra_tap_water_stop is None."""
        mock_coordinator.data["settings"]["extra_tap_water_stop"] = None
        entity = QvantumSwitchEntity(mock_coordinator, "extra_tap_water", mock_device)
        assert entity.is_on is False

    def test_is_on_stop_minus_one(self, mock_coordinator, mock_device):
        """Test is_on when extra_tap_water_stop is -1 (always on)."""
        mock_coordinator.data["settings"]["extra_tap_water_stop"] = -1
        entity = QvantumSwitchEntity(mock_coordinator, "extra_tap_water", mock_device)
        assert entity.is_on is True

    def test_is_on_stop_zero(self, mock_coordinator, mock_device):
        """Test is_on when extra_tap_water_stop is 0 (off)."""
        mock_coordinator.data["settings"]["extra_tap_water_stop"] = 0
        entity = QvantumSwitchEntity(mock_coordinator, "extra_tap_water", mock_device)
        assert entity.is_on is False

    def test_is_on_stop_future_timestamp(self, mock_coordinator, mock_device):
        """Test is_on when extra_tap_water_stop is a future timestamp."""
        future_time = int((datetime.now()).timestamp()) + 3600  # 1 hour from now
        mock_coordinator.data["settings"]["extra_tap_water_stop"] = future_time
        entity = QvantumSwitchEntity(mock_coordinator, "extra_tap_water", mock_device)
        assert entity.is_on is True

    def test_is_on_stop_past_timestamp(self, mock_coordinator, mock_device):
        """Test is_on when extra_tap_water_stop is a past timestamp."""
        past_time = int((datetime.now()).timestamp()) - 3600  # 1 hour ago
        mock_coordinator.data["settings"]["extra_tap_water_stop"] = past_time
        entity = QvantumSwitchEntity(mock_coordinator, "extra_tap_water", mock_device)
        assert entity.is_on is False

    def test_is_on_other_metric_on(self, mock_coordinator, mock_device):
        """Test is_on for other metrics when set to 1."""
        entity = QvantumSwitchEntity(mock_coordinator, "other_switch", mock_device)
        mock_coordinator.data["metrics"]["other_switch"] = 1
        assert entity.is_on is True

    def test_is_on_other_metric_off(self, mock_coordinator, mock_device):
        """Test is_on for other metrics when set to 0."""
        entity = QvantumSwitchEntity(mock_coordinator, "other_switch", mock_device)
        mock_coordinator.data["metrics"]["other_switch"] = 0
        assert entity.is_on is False

    def test_available_true(self, mock_coordinator, mock_device):
        """Test entity availability when data exists."""
        mock_coordinator.data["settings"]["extra_tap_water_stop"] = 1234567890
        entity = QvantumSwitchEntity(mock_coordinator, "extra_tap_water", mock_device)
        assert entity.available is True

    def test_available_false_missing_key(self, mock_coordinator, mock_device):
        """Test entity availability when key is missing."""
        entity = QvantumSwitchEntity(mock_coordinator, "missing_switch", mock_device)
        assert entity.available is False

    def test_available_false_none_value(self, mock_coordinator, mock_device):
        """Test entity availability when value is None."""
        mock_coordinator.data["settings"]["extra_tap_water"] = None
        entity = QvantumSwitchEntity(mock_coordinator, "extra_tap_water", mock_device)
        assert entity.available is False

    def test_available_extra_tap_water_with_stop_data(
        self, mock_coordinator, mock_device
    ):
        """Test availability for extra_tap_water when stop data exists."""
        mock_coordinator.data["settings"]["extra_tap_water_stop"] = 1234567890
        entity = QvantumSwitchEntity(mock_coordinator, "extra_tap_water", mock_device)
        assert entity.available is True

    def test_available_extra_tap_water_without_stop_data(
        self, mock_coordinator, mock_device
    ):
        """Test availability for extra_tap_water when stop data is missing."""
        # Remove extra_tap_water_stop from settings
        if "extra_tap_water_stop" in mock_coordinator.data["settings"]:
            del mock_coordinator.data["settings"]["extra_tap_water_stop"]
        entity = QvantumSwitchEntity(mock_coordinator, "extra_tap_water", mock_device)
        assert entity.available is False

    def test_available_extra_tap_water_stop_none(self, mock_coordinator, mock_device):
        """Test availability for extra_tap_water when stop data is None."""
        mock_coordinator.data["settings"]["extra_tap_water_stop"] = None
        entity = QvantumSwitchEntity(mock_coordinator, "extra_tap_water", mock_device)
        assert entity.available is False

    def test_available_other_switch_with_data(self, mock_coordinator, mock_device):
        """Test availability for other switches when data exists."""
        mock_coordinator.data["metrics"]["op_mode"] = 1
        entity = QvantumSwitchEntity(mock_coordinator, "op_mode", mock_device)
        assert entity.available is True

    def test_available_other_switch_without_data(self, mock_coordinator, mock_device):
        """Test availability for other switches when data is missing."""
        entity = QvantumSwitchEntity(mock_coordinator, "op_mode", mock_device)
        assert entity.available is False

    def test_available_other_switch_none_value(self, mock_coordinator, mock_device):
        """Test availability for other switches when value is None."""
        mock_coordinator.data["settings"]["op_mode"] = None
        entity = QvantumSwitchEntity(mock_coordinator, "op_mode", mock_device)
        assert entity.available is False

    @pytest.mark.asyncio
    async def test_async_turn_on_extra_tap_water(self, mock_coordinator, mock_device):
        """Test turning on extra tap water."""
        entity = QvantumSwitchEntity(mock_coordinator, "extra_tap_water", mock_device)

        # Mock the API response
        mock_coordinator.api.set_extra_tap_water = AsyncMock(
            return_value={"status": "APPLIED"}
        )

        await entity.async_turn_on()

        mock_coordinator.api.set_extra_tap_water.assert_called_once_with(
            "test_device_123", -1
        )
        # The method updates metrics, not settings
        assert mock_coordinator.data["metrics"]["extra_tap_water"] == 1
        mock_coordinator.async_set_updated_data.assert_called_once_with(
            mock_coordinator.data
        )

    @pytest.mark.asyncio
    async def test_async_turn_off_extra_tap_water(self, mock_coordinator, mock_device):
        """Test turning off extra tap water."""
        entity = QvantumSwitchEntity(mock_coordinator, "extra_tap_water", mock_device)

        # Mock the API response
        mock_coordinator.api.set_extra_tap_water = AsyncMock(
            return_value={"status": "APPLIED"}
        )

        await entity.async_turn_off()

        mock_coordinator.api.set_extra_tap_water.assert_called_once_with(
            "test_device_123", 0
        )
        # The method updates metrics, not settings
        assert mock_coordinator.data["metrics"]["extra_tap_water"] == 0
        mock_coordinator.async_set_updated_data.assert_called_once_with(
            mock_coordinator.data
        )

    @pytest.mark.asyncio
    async def test_async_turn_on_other_metric(self, mock_coordinator, mock_device):
        """Test turning on other metrics."""
        entity = QvantumSwitchEntity(mock_coordinator, "other_switch", mock_device)

        # Mock the API response
        mock_coordinator.api.update_setting = AsyncMock(
            return_value={"status": "APPLIED"}
        )

        await entity.async_turn_on()

        mock_coordinator.api.update_setting.assert_called_once_with(
            "test_device_123", "other_switch", 1
        )
        # The method updates metrics
        assert mock_coordinator.data["metrics"]["other_switch"] == 1
        mock_coordinator.async_set_updated_data.assert_called_once_with(
            mock_coordinator.data
        )

    @pytest.mark.asyncio
    async def test_async_turn_off_other_metric(self, mock_coordinator, mock_device):
        """Test turning off other metrics."""
        entity = QvantumSwitchEntity(mock_coordinator, "other_switch", mock_device)

        # Mock the API response
        mock_coordinator.api.update_setting = AsyncMock(
            return_value={"status": "APPLIED"}
        )

        await entity.async_turn_off()

        mock_coordinator.api.update_setting.assert_called_once_with(
            "test_device_123", "other_switch", 0
        )
        # The method updates metrics
        assert mock_coordinator.data["metrics"]["other_switch"] == 0
        mock_coordinator.async_set_updated_data.assert_called_once_with(
            mock_coordinator.data
        )