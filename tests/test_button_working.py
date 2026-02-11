"""Tests for Qvantum button entities."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.qvantum.button import QvantumButtonEntity, async_setup_entry
from custom_components.qvantum import RuntimeData


@pytest.fixture
def mock_coordinator():
    """Create a mock coordinator."""
    coordinator = MagicMock()
    coordinator.data = {
        "device": {"id": "test_device_123"},
        "metrics": {"hpid": "test_device_123"},
        "settings": {
            "extra_tap_water": None,  # Will be set based on test
            "extra_tap_water_stop": None,
        },
    }
    coordinator.async_refresh = AsyncMock()
    coordinator.async_set_updated_data = AsyncMock()
    coordinator.api = MagicMock()
    coordinator.api.set_extra_tap_water = AsyncMock(return_value={"status": "APPLIED"})
    coordinator.api.elevate_access = AsyncMock(return_value={"writeAccessLevel": 30})

    # Mock config_entry and runtime_data for access level check
    config_entry = MagicMock()
    maintenance_coordinator = MagicMock()
    maintenance_coordinator.data = {"access_level": {"writeAccessLevel": 20}}
    config_entry.runtime_data.maintenance_coordinator = maintenance_coordinator
    coordinator.config_entry = config_entry

    return coordinator


@pytest.fixture
def mock_maintenance_coordinator():
    """Create a mock maintenance coordinator."""
    coordinator = MagicMock()
    coordinator.async_refresh = AsyncMock()
    return coordinator


@pytest.fixture
def mock_device():
    """Create a mock device."""
    return DeviceInfo(
        identifiers={("qvantum", "qvantum-test_device_123")},
        name="Qvantum Heat Pump",
        manufacturer="Qvantum",
        model="QE-6",
    )


class TestQvantumButtonEntity:
    """Test the Qvantum button entity."""

    def test_button_entity_initialization(
        self, mock_coordinator, mock_device, mock_maintenance_coordinator
    ):
        """Test button entity initialization."""
        button = QvantumButtonEntity(
            mock_coordinator, "extra_tap_water_60min", mock_device
        )

        assert button._metric_key == "extra_tap_water_60min"
        assert button._attr_translation_key == "extra_tap_water_60min"
        assert button._attr_unique_id == "qvantum_extra_tap_water_60min_test_device_123"
        assert button._attr_device_info == mock_device
        assert button._attr_has_entity_name is True

        # Test elevate_access button
        elevate_button = QvantumButtonEntity(
            mock_coordinator,
            "elevate_access",
            mock_device,
            mock_maintenance_coordinator,
        )

        assert elevate_button._metric_key == "elevate_access"
        assert elevate_button._attr_translation_key == "elevate_access"
        assert (
            elevate_button._attr_unique_id == "qvantum_elevate_access_test_device_123"
        )
        assert elevate_button._attr_device_info == mock_device
        assert elevate_button._attr_has_entity_name is True
        assert elevate_button._attr_entity_category == EntityCategory.DIAGNOSTIC

    @pytest.mark.asyncio
    async def test_async_press_extra_tap_water_60min(
        self, mock_coordinator, mock_device
    ):
        """Test pressing the extra tap water 60min button."""
        button = QvantumButtonEntity(
            mock_coordinator, "extra_tap_water_60min", mock_device
        )

        await button.async_press()

        mock_coordinator.api.set_extra_tap_water.assert_called_once_with(
            "test_device_123", 60
        )
        # Data is updated when response comes back
        mock_coordinator.async_set_updated_data.assert_called_once()
        assert mock_coordinator.data["settings"]["extra_tap_water"] == "on"

    @pytest.mark.asyncio
    async def test_async_press_elevate_access(
        self, mock_coordinator, mock_device, mock_maintenance_coordinator
    ):
        """Test pressing the elevate access button."""
        button = QvantumButtonEntity(
            mock_coordinator,
            "elevate_access",
            mock_device,
            mock_maintenance_coordinator,
        )

        await button.async_press()

        mock_coordinator.api.elevate_access.assert_called_once_with(
            "test_device_123"
        )
        # Verify maintenance coordinator is refreshed after elevating access
        mock_maintenance_coordinator.async_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_press_elevate_access_failure(
        self, mock_coordinator, mock_device, mock_maintenance_coordinator, caplog
    ):
        """Test pressing the elevate access button when elevation fails."""
        # Mock the API to return None (failure)
        mock_coordinator.api.elevate_access = AsyncMock(return_value=None)

        button = QvantumButtonEntity(
            mock_coordinator,
            "elevate_access",
            mock_device,
            mock_maintenance_coordinator,
        )

        await button.async_press()

        mock_coordinator.api.elevate_access.assert_called_once_with("test_device_123")

        # Verify error is logged
        assert "Failed to elevate access" in caplog.text
        # Verify maintenance coordinator is not refreshed on failure
        mock_maintenance_coordinator.async_refresh.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_setup_entry(
        self,
        hass,
        mock_config_entry,
        mock_coordinator,
        mock_device,
        mock_maintenance_coordinator,
    ):
        """Test setting up button entities."""
        mock_config_entry.runtime_data = RuntimeData(
            coordinator=mock_coordinator,
            device=mock_device,
            maintenance_coordinator=mock_maintenance_coordinator,
        )

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Check that entities were added
        assert async_add_entities.called
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 2
        assert isinstance(entities[0], QvantumButtonEntity)
        assert entities[0]._metric_key == "extra_tap_water_60min"
        assert isinstance(entities[1], QvantumButtonEntity)
        assert entities[1]._metric_key == "elevate_access"
