"""Tests for Qvantum climate entities (working version that avoids metaclass issues)."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest


# Create mock base classes that don't have metaclass conflicts
class MockCoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator


class MockClimateEntity:
    pass


# Mock ClimateEntityFeature
class MockClimateEntityFeature:
    TARGET_TEMPERATURE = 1


# Mock HVACMode and HVACAction
class MockHVACMode:
    HEAT = "heat"


class MockHVACAction:
    HEATING = "heating"
    IDLE = "idle"
    DEFROSTING = "defrosting"


# Mock UnitOfTemperature
class MockUnitOfTemperature:
    CELSIUS = "°C"


# Patch the imports before importing the climate module
with patch(
    "homeassistant.helpers.update_coordinator.CoordinatorEntity", MockCoordinatorEntity
):
    with patch("homeassistant.components.climate.ClimateEntity", MockClimateEntity):
        with patch(
            "homeassistant.components.climate.ClimateEntityFeature",
            MockClimateEntityFeature,
        ):
            with patch("homeassistant.components.climate.HVACMode", MockHVACMode):
                with patch(
                    "homeassistant.components.climate.HVACAction", MockHVACAction
                ):
                    with patch(
                        "homeassistant.const.UnitOfTemperature", MockUnitOfTemperature
                    ):
                        with patch(
                            "custom_components.qvantum.const.SETTING_UPDATE_APPLIED", "APPLIED"
                        ):
                            from homeassistant.helpers.device_registry import DeviceInfo

                            from custom_components.qvantum.climate import (
                                QvantumIndoorClimateEntity,
                            )
                            from custom_components.qvantum.const import SETTING_UPDATE_APPLIED


@pytest.fixture
def mock_coordinator():
    """Create a mock coordinator with test data."""
    coordinator = MagicMock()
    coordinator.data = {
        "device": {"id": "test_device_123"},
        "metrics": {
            "hpid": "test_device_123",
            "bt2": 22.5,  # Current temperature
            "hp_status": 3,  # Heating status
        },
        "settings": {
            "indoor_temperature_target": 21.0,
            "sensor_mode": "bt2",
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


class TestQvantumIndoorClimateEntity:
    """Test the QvantumIndoorClimateEntity class."""

    def test_init(self, mock_coordinator, mock_device):
        """Test climate entity initialization."""
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)

        assert entity._hpid == "test_device_123"
        assert entity._attr_unique_id == "qvantum_indoor_climate_test_device_123"
        assert entity._attr_temperature_unit == "°C"
        assert entity._attr_device_info == mock_device
        assert entity._attr_translation_key == "indoor_climate"
        assert entity._attr_has_entity_name is True

    def test_current_temperature(self, mock_coordinator, mock_device):
        """Test getting current temperature."""
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.current_temperature == 22.5

    def test_target_temperature(self, mock_coordinator, mock_device):
        """Test getting target temperature."""
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.target_temperature == 21.0

    def test_hvac_mode(self, mock_coordinator, mock_device):
        """Test HVAC mode."""
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.hvac_mode == "heat"

    def test_hvac_modes(self, mock_coordinator, mock_device):
        """Test available HVAC modes."""
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.hvac_modes == ["heat"]

    def test_hvac_action_heating(self, mock_coordinator, mock_device):
        """Test HVAC action when heating."""
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.hvac_action == "heating"  # hp_status = 3

    def test_hvac_action_idle(self, mock_coordinator, mock_device):
        """Test HVAC action when idle."""
        mock_coordinator.data["metrics"]["hp_status"] = 0
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.hvac_action == "idle"

    def test_hvac_action_defrosting(self, mock_coordinator, mock_device):
        """Test HVAC action when defrosting."""
        mock_coordinator.data["metrics"]["hp_status"] = 1
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.hvac_action == "defrosting"

    def test_hvac_action_unknown(self, mock_coordinator, mock_device):
        """Test HVAC action for unknown status."""
        mock_coordinator.data["metrics"]["hp_status"] = 99
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.hvac_action == "idle"  # Default to idle

    def test_supported_features_with_bt2(self, mock_coordinator, mock_device):
        """Test supported features when sensor_mode is bt2."""
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.supported_features == 1  # TARGET_TEMPERATURE

    def test_supported_features_without_bt2(self, mock_coordinator, mock_device):
        """Test supported features when sensor_mode is not bt2."""
        mock_coordinator.data["settings"]["sensor_mode"] = "other"
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.supported_features == {}

    def test_available_true(self, mock_coordinator, mock_device):
        """Test entity availability when bt2 data exists."""
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.available is True

    def test_available_false_no_bt2(self, mock_coordinator, mock_device):
        """Test entity availability when bt2 key is missing."""
        del mock_coordinator.data["metrics"]["bt2"]
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.available is False

    def test_available_false_bt2_none(self, mock_coordinator, mock_device):
        """Test entity availability when bt2 is None."""
        mock_coordinator.data["metrics"]["bt2"] = None
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.available is False

    @pytest.mark.asyncio
    async def test_async_set_temperature(self, mock_coordinator, mock_device):
        """Test setting target temperature."""
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)

        # Mock the API response
        mock_coordinator.api.set_indoor_temperature_target = AsyncMock(
            return_value={"status": "APPLIED"}
        )

        await entity.async_set_temperature(temperature=23.5)

        mock_coordinator.api.set_indoor_temperature_target.assert_called_once_with(
            "test_device_123", 23.5
        )
        # Check that async_set_updated_data was called (indicating successful update)
        mock_coordinator.async_set_updated_data.assert_called_once_with(
            mock_coordinator.data
        )
        # The data should have been updated
        assert mock_coordinator.data["settings"]["indoor_temperature_target"] == 23.5

    @pytest.mark.asyncio
    async def test_async_set_hvac_mode(self, mock_coordinator, mock_device):
        """Test setting HVAC mode (currently just logs)."""
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)

        # This method currently just logs the mode
        await entity.async_set_hvac_mode("heat")

        # No assertions needed as it just logs