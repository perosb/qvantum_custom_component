"""Tests for Qvantum button entities."""

import pytest
from unittest.mock import AsyncMock, MagicMock
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

    def test_button_entity_initialization(self, mock_coordinator, mock_device):
        """Test button entity initialization."""
        button = QvantumButtonEntity(
            mock_coordinator, "extra_tap_water_60min", mock_device
        )

        assert button._metric_key == "extra_tap_water_60min"
        assert button._attr_translation_key == "extra_tap_water_60min"
        assert button._attr_unique_id == "qvantum_extra_tap_water_60min_test_device_123"
        assert button._attr_device_info == mock_device
        assert button._attr_has_entity_name is True

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

    """Test button platform setup."""

    @pytest.mark.asyncio
    async def test_async_setup_entry(
        self, hass, mock_config_entry, mock_coordinator, mock_device
    ):
        """Test setting up button entities."""
        mock_config_entry.runtime_data = RuntimeData(
            coordinator=mock_coordinator, device=mock_device
        )

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Check that entities were added
        assert async_add_entities.called
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 1
        assert isinstance(entities[0], QvantumButtonEntity)
        assert entities[0]._metric_key == "extra_tap_water_60min"
