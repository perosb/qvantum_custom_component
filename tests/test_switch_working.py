"""Tests for Qvantum switch entities (working version that avoids metaclass issues)."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest


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
                        "custom_components.qvantum.const.SETTING_UPDATE_APPLIED",
                        "APPLIED",
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

    def test_init_op_mode_icon(self, mock_coordinator, mock_device):
        """Test switch entity initialization with op_mode icon."""
        entity = QvantumSwitchEntity(mock_coordinator, "op_mode", mock_device)
        assert entity._attr_icon == "mdi:auto-mode"

    def test_init_op_man_dhw_icon(self, mock_coordinator, mock_device):
        """Test switch entity initialization with op_man_dhw icon."""
        entity = QvantumSwitchEntity(mock_coordinator, "op_man_dhw", mock_device)
        assert entity._attr_icon == "mdi:water-outline"

    def test_init_op_man_addition_icon(self, mock_coordinator, mock_device):
        """Test switch entity initialization with op_man_addition icon."""
        entity = QvantumSwitchEntity(mock_coordinator, "op_man_addition", mock_device)
        assert entity._attr_icon == "mdi:transmission-tower-import"

    def test_init_default_icon(self, mock_coordinator, mock_device):
        """Test switch entity initialization with default icon."""
        entity = QvantumSwitchEntity(mock_coordinator, "unknown_metric", mock_device)
        assert entity._attr_icon == "mdi:water-boiler"

    def test_is_on_extra_tap_water_off(self, mock_coordinator, mock_device):
        """Test is_on when extra_tap_water is 'off'."""
        mock_coordinator.data["settings"]["extra_tap_water"] = "off"
        entity = QvantumSwitchEntity(mock_coordinator, "extra_tap_water", mock_device)
        assert entity.is_on is False

    def test_is_on_extra_tap_water_on(self, mock_coordinator, mock_device):
        """Test is_on when extra_tap_water is 'on'."""
        mock_coordinator.data["settings"]["extra_tap_water"] = "on"
        entity = QvantumSwitchEntity(mock_coordinator, "extra_tap_water", mock_device)
        assert entity.is_on is True

    def test_is_on_extra_tap_water_none(self, mock_coordinator, mock_device):
        """Test is_on when extra_tap_water is None."""
        mock_coordinator.data["settings"]["extra_tap_water"] = None
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
        mock_coordinator.data["settings"]["extra_tap_water"] = "on"
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

    def test_available_extra_tap_water_with_data(self, mock_coordinator, mock_device):
        """Test availability for extra_tap_water when data exists."""
        mock_coordinator.data["settings"]["extra_tap_water"] = "on"
        entity = QvantumSwitchEntity(mock_coordinator, "extra_tap_water", mock_device)
        assert entity.available is True

    def test_available_extra_tap_water_without_stop_data(
        self, mock_coordinator, mock_device
    ):
        """Test availability for extra_tap_water when stop data is missing."""
        # Remove extra_tap_water from settings
        if "extra_tap_water" in mock_coordinator.data["settings"]:
            del mock_coordinator.data["settings"]["extra_tap_water"]
        entity = QvantumSwitchEntity(mock_coordinator, "extra_tap_water", mock_device)
        assert entity.available is False

    def test_available_extra_tap_water_none(self, mock_coordinator, mock_device):
        """Test availability for extra_tap_water when data is None."""
        mock_coordinator.data["settings"]["extra_tap_water"] = None
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

    def test_available_op_man_addition_available(self, mock_coordinator, mock_device):
        """Test availability for op_man_addition when op_mode is 1."""
        mock_coordinator.data["metrics"]["op_man_addition"] = 0
        mock_coordinator.data["metrics"]["op_mode"] = 1
        entity = QvantumSwitchEntity(mock_coordinator, "op_man_addition", mock_device)
        assert entity.available is True

    def test_available_op_man_addition_unavailable_wrong_op_mode(
        self, mock_coordinator, mock_device
    ):
        """Test availability for op_man_addition when op_mode is not 1."""
        mock_coordinator.data["metrics"]["op_man_addition"] = 0
        mock_coordinator.data["metrics"]["op_mode"] = 0
        entity = QvantumSwitchEntity(mock_coordinator, "op_man_addition", mock_device)
        assert entity.available is False

    def test_available_op_man_addition_unavailable_missing_metric(
        self, mock_coordinator, mock_device
    ):
        """Test availability for op_man_addition when metric is missing."""
        mock_coordinator.data["metrics"]["op_mode"] = 1
        entity = QvantumSwitchEntity(mock_coordinator, "op_man_addition", mock_device)
        assert entity.available is False

    def test_available_op_man_dhw_available(self, mock_coordinator, mock_device):
        """Test availability for op_man_dhw when op_mode is 1."""
        mock_coordinator.data["metrics"]["op_man_dhw"] = 0
        mock_coordinator.data["metrics"]["op_mode"] = 1
        entity = QvantumSwitchEntity(mock_coordinator, "op_man_dhw", mock_device)
        assert entity.available is True

    def test_available_op_man_dhw_unavailable_wrong_op_mode(
        self, mock_coordinator, mock_device
    ):
        """Test availability for op_man_dhw when op_mode is not 1."""
        mock_coordinator.data["metrics"]["op_man_dhw"] = 0
        mock_coordinator.data["metrics"]["op_mode"] = 0
        entity = QvantumSwitchEntity(mock_coordinator, "op_man_dhw", mock_device)
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
        # Data is updated via coordinator refresh
        mock_coordinator.async_refresh.assert_called_once()
        mock_coordinator.async_set_updated_data.assert_not_called()

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
        # Data is updated via coordinator refresh
        mock_coordinator.async_refresh.assert_called_once()
        mock_coordinator.async_set_updated_data.assert_not_called()

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

    @pytest.mark.asyncio
    async def test_async_turn_on_enable_sc_dhw(self, mock_coordinator, mock_device):
        """Test turning on enable_sc_dhw."""
        entity = QvantumSwitchEntity(mock_coordinator, "enable_sc_dhw", mock_device)

        # Mock the API response
        mock_coordinator.api.update_setting = AsyncMock(
            return_value={"status": "APPLIED"}
        )

        await entity.async_turn_on()

        mock_coordinator.api.update_setting.assert_called_once_with(
            "test_device_123", "enable_sc_dhw", True
        )
        # The method updates metrics
        assert mock_coordinator.data["metrics"]["enable_sc_dhw"] is True
        mock_coordinator.async_set_updated_data.assert_called_once_with(
            mock_coordinator.data
        )

    @pytest.mark.asyncio
    async def test_async_turn_off_enable_sc_dhw(self, mock_coordinator, mock_device):
        """Test turning off enable_sc_dhw."""
        entity = QvantumSwitchEntity(mock_coordinator, "enable_sc_dhw", mock_device)

        # Mock the API response
        mock_coordinator.api.update_setting = AsyncMock(
            return_value={"status": "APPLIED"}
        )

        await entity.async_turn_off()

        mock_coordinator.api.update_setting.assert_called_once_with(
            "test_device_123", "enable_sc_dhw", False
        )
        # The method updates metrics
        assert mock_coordinator.data["metrics"]["enable_sc_dhw"] is False
        mock_coordinator.async_set_updated_data.assert_called_once_with(
            mock_coordinator.data
        )

    @pytest.mark.asyncio
    async def test_async_turn_on_enable_sc_sh(self, mock_coordinator, mock_device):
        """Test turning on enable_sc_sh."""
        entity = QvantumSwitchEntity(mock_coordinator, "enable_sc_sh", mock_device)

        # Mock the API response
        mock_coordinator.api.update_setting = AsyncMock(
            return_value={"status": "APPLIED"}
        )

        await entity.async_turn_on()

        mock_coordinator.api.update_setting.assert_called_once_with(
            "test_device_123", "enable_sc_sh", True
        )
        # The method updates metrics
        assert mock_coordinator.data["metrics"]["enable_sc_sh"] is True
        mock_coordinator.async_set_updated_data.assert_called_once_with(
            mock_coordinator.data
        )

    @pytest.mark.asyncio
    async def test_async_turn_off_enable_sc_sh(self, mock_coordinator, mock_device):
        """Test turning off enable_sc_sh."""
        entity = QvantumSwitchEntity(mock_coordinator, "enable_sc_sh", mock_device)

        # Mock the API response
        mock_coordinator.api.update_setting = AsyncMock(
            return_value={"status": "APPLIED"}
        )

        await entity.async_turn_off()

        mock_coordinator.api.update_setting.assert_called_once_with(
            "test_device_123", "enable_sc_sh", False
        )
        # The method updates metrics
        assert mock_coordinator.data["metrics"]["enable_sc_sh"] is False
        mock_coordinator.async_set_updated_data.assert_called_once_with(
            mock_coordinator.data
        )
