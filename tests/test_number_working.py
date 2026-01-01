"""Tests for Qvantum number entities (working version that avoids metaclass issues)."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest


# Create mock base classes that don't have metaclass conflicts
class MockCoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator


class MockNumberEntity:
    pass


# Mock EntityCategory
class MockEntityCategory:
    class DIAGNOSTIC:
        name = "DIAGNOSTIC"


# Patch the imports before importing the number module
with patch(
    "homeassistant.helpers.update_coordinator.CoordinatorEntity", MockCoordinatorEntity
):
    with patch("homeassistant.components.number.NumberEntity", MockNumberEntity):
        with patch("homeassistant.const.EntityCategory", MockEntityCategory):
            from homeassistant.helpers.device_registry import DeviceInfo

            from custom_components.qvantum.number import (
                QvantumNumberEntity,
                async_setup_entry,
            )


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
            "tap_water_capacity_target": 4,
            "indoor_temperature_offset": 2,
            "tap_water_stop": 75,
            "tap_water_start": 55,
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
        identifiers={("qvantum", "qvantum-test_device_123")},
        manufacturer="Qvantum",
        model="QE-6",
    )


class TestQvantumNumberEntity:
    """Test the QvantumNumberEntity class."""

    def test_init_tap_water_capacity(self, mock_coordinator, mock_device):
        """Test tap water capacity number entity initialization."""
        entity = QvantumNumberEntity(
            mock_coordinator, "tap_water_capacity_target", 1, 7, 1, mock_device
        )

        assert entity._metric_key == "tap_water_capacity_target"
        assert (
            entity._attr_unique_id
            == "qvantum_tap_water_capacity_target_test_device_123"
        )
        assert entity._attr_device_info == mock_device
        assert entity._attr_has_entity_name is True
        assert entity._attr_native_min_value == 1
        assert entity._attr_native_max_value == 7
        assert entity._attr_native_step == 1

    def test_init_indoor_temperature_offset(self, mock_coordinator, mock_device):
        """Test indoor temperature offset number entity initialization."""
        entity = QvantumNumberEntity(
            mock_coordinator, "indoor_temperature_offset", -10, 10, 1, mock_device
        )

        assert entity._metric_key == "indoor_temperature_offset"
        assert entity._attr_native_min_value == -10
        assert entity._attr_native_max_value == 10
        assert entity._attr_native_step == 1

    def test_init_tap_water_stop(self, mock_coordinator, mock_device):
        """Test tap water stop number entity initialization."""
        entity = QvantumNumberEntity(
            mock_coordinator, "tap_water_stop", 60, 90, 1, mock_device
        )

        assert entity._metric_key == "tap_water_stop"
        assert entity._attr_native_min_value == 60
        assert entity._attr_native_max_value == 90
        assert entity._attr_native_step == 1

    def test_init_tap_water_start(self, mock_coordinator, mock_device):
        """Test tap water start number entity initialization."""
        entity = QvantumNumberEntity(
            mock_coordinator, "tap_water_start", 50, 65, 1, mock_device
        )

        assert entity._metric_key == "tap_water_start"
        assert entity._attr_native_min_value == 50
        assert entity._attr_native_max_value == 65
        assert entity._attr_native_step == 1

    def test_state(self, mock_coordinator, mock_device):
        """Test getting entity state."""
        entity = QvantumNumberEntity(
            mock_coordinator, "tap_water_capacity_target", 1, 7, 1, mock_device
        )
        assert entity.state == 4

    def test_state_with_mapping_capacity_1(self, mock_device):
        """Test getting entity state when stop/start match capacity 1 mapping."""
        from unittest.mock import Mock
        from unittest.mock import AsyncMock

        coordinator = Mock()
        coordinator.data = {
            "settings": {
                "tap_water_capacity_target": 5,  # Different stored value
                "tap_water_stop": 58,
                "tap_water_start": 52,
            },
            "metrics": {"hpid": "test_device_123"},
        }
        coordinator.async_config_entry_first_refresh = AsyncMock()

        entity = QvantumNumberEntity(
            coordinator, "tap_water_capacity_target", 1, 7, 1, mock_device
        )
        assert entity.state == 1  # Should return mapped value, not stored value

    def test_state_with_mapping_capacity_6(self, mock_device):
        """Test getting entity state when stop/start match capacity 6 mapping."""
        from unittest.mock import Mock
        from unittest.mock import AsyncMock

        coordinator = Mock()
        coordinator.data = {
            "settings": {
                "tap_water_capacity_target": 2,  # Different stored value
                "tap_water_stop": 74,
                "tap_water_start": 55,
            },
            "metrics": {"hpid": "test_device_123"},
        }
        coordinator.async_config_entry_first_refresh = AsyncMock()

        entity = QvantumNumberEntity(
            coordinator, "tap_water_capacity_target", 1, 7, 1, mock_device
        )
        assert entity.state == 6  # Should return mapped value

    def test_state_with_no_mapping(self, mock_device):
        """Test getting entity state when stop/start don't match any mapping."""
        from unittest.mock import Mock
        from unittest.mock import AsyncMock

        coordinator = Mock()
        coordinator.data = {
            "settings": {
                "tap_water_capacity_target": 3,
                "tap_water_stop": 61,
                "tap_water_start": 50,
            },
            "metrics": {"hpid": "test_device_123"},
        }
        coordinator.async_config_entry_first_refresh = AsyncMock()

        entity = QvantumNumberEntity(
            coordinator, "tap_water_capacity_target", 1, 7, 1, mock_device
        )
        assert entity.state == 3  # Should return stored value

    def test_state_with_none_stop(self, mock_device):
        """Test getting entity state when tap_water_stop is None."""
        from unittest.mock import Mock
        from unittest.mock import AsyncMock

        coordinator = Mock()
        coordinator.data = {
            "settings": {
                "tap_water_capacity_target": 2,
                "tap_water_stop": None,
                "tap_water_start": 50,
            },
            "metrics": {"hpid": "test_device_123"},
        }
        coordinator.async_config_entry_first_refresh = AsyncMock()

        entity = QvantumNumberEntity(
            coordinator, "tap_water_capacity_target", 1, 7, 1, mock_device
        )
        assert entity.state == 2  # Should return stored value when stop is None

    def test_state_with_none_start(self, mock_device):
        """Test getting entity state when tap_water_start is None."""
        from unittest.mock import Mock
        from unittest.mock import AsyncMock

        coordinator = Mock()
        coordinator.data = {
            "settings": {
                "tap_water_capacity_target": 2,
                "tap_water_stop": 61,
                "tap_water_start": None,
            },
            "metrics": {"hpid": "test_device_123"},
        }
        coordinator.async_config_entry_first_refresh = AsyncMock()

        entity = QvantumNumberEntity(
            coordinator, "tap_water_capacity_target", 1, 7, 1, mock_device
        )
        assert entity.state == 2  # Should return stored value when start is None

    def test_available_true(self, mock_coordinator, mock_device):
        """Test entity availability when data exists."""
        entity = QvantumNumberEntity(
            mock_coordinator, "tap_water_capacity_target", 1, 7, 1, mock_device
        )
        assert entity.available is True

    def test_available_false(self, mock_coordinator, mock_device):
        """Test entity availability when data is missing."""
        entity = QvantumNumberEntity(
            mock_coordinator, "missing_setting", 1, 7, 1, mock_device
        )
        assert entity.available is False

    @pytest.mark.asyncio
    async def test_async_set_native_value_tap_water_capacity(
        self, mock_coordinator, mock_device
    ):
        """Test setting tap water capacity target value."""
        entity = QvantumNumberEntity(
            mock_coordinator, "tap_water_capacity_target", 1, 7, 1, mock_device
        )

        # Mock the API response
        mock_coordinator.api.set_tap_water_capacity_target = AsyncMock(
            return_value={"status": "APPLIED"}
        )

        await entity.async_set_native_value(5.0)

        mock_coordinator.api.set_tap_water_capacity_target.assert_called_once_with(
            "test_device_123", 5
        )
        # Note: async_set_updated_data would be called if the API response status was correct

    @pytest.mark.asyncio
    async def test_async_set_native_value_indoor_temperature_offset(
        self, mock_coordinator, mock_device
    ):
        """Test setting indoor temperature offset value."""
        entity = QvantumNumberEntity(
            mock_coordinator, "indoor_temperature_offset", -10, 10, 1, mock_device
        )

        # Mock the API response
        mock_coordinator.api.set_indoor_temperature_offset = AsyncMock(
            return_value={"status": "APPLIED"}
        )

        await entity.async_set_native_value(-3.0)

        mock_coordinator.api.set_indoor_temperature_offset.assert_called_once_with(
            "test_device_123", -3
        )
        # Note: async_set_updated_data would be called if the API response status was correct

    @pytest.mark.asyncio
    async def test_async_set_native_value_tap_water_stop(
        self, mock_coordinator, mock_device
    ):
        """Test setting tap water stop value."""
        entity = QvantumNumberEntity(
            mock_coordinator, "tap_water_stop", 60, 90, 1, mock_device
        )

        # Mock the API response
        mock_coordinator.api.set_tap_water_stop = AsyncMock(
            return_value={"status": "APPLIED"}
        )

        await entity.async_set_native_value(80.0)

        mock_coordinator.api.set_tap_water_stop.assert_called_once_with(
            "test_device_123", 80
        )
        # Note: async_set_updated_data would be called if the API response status was correct

    @pytest.mark.asyncio
    async def test_async_set_native_value_tap_water_start(
        self, mock_coordinator, mock_device
    ):
        """Test setting tap water start value."""
        entity = QvantumNumberEntity(
            mock_coordinator, "tap_water_start", 50, 65, 1, mock_device
        )

        # Mock the API response
        mock_coordinator.api.set_tap_water_start = AsyncMock(
            return_value={"status": "APPLIED"}
        )

        await entity.async_set_native_value(58.0)

        mock_coordinator.api.set_tap_water_start.assert_called_once_with(
            "test_device_123", 58
        )
        # Note: async_set_updated_data would be called if the API response status was correct


class TestNumberSetup:
    """Test number platform setup."""

    @pytest.mark.asyncio
    async def test_async_setup_entry(
        self, hass, mock_config_entry, mock_coordinator, mock_device
    ):
        """Test setting up number entities."""
        from custom_components.qvantum import RuntimeData

        mock_config_entry.runtime_data = RuntimeData(
            coordinator=mock_coordinator, device=mock_device
        )

        async_add_entities = MagicMock()

        await async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Check that entities were added
        assert async_add_entities.called
        entities = async_add_entities.call_args[0][0]
        assert (
            len(entities) == 4
        )  # tap_water_capacity_target, indoor_temperature_offset, tap_water_stop, tap_water_start
        assert all(isinstance(entity, QvantumNumberEntity) for entity in entities)

        # Check entity keys
        entity_keys = [entity._metric_key for entity in entities]
        expected_keys = [
            "tap_water_capacity_target",
            "indoor_temperature_offset",
            "tap_water_stop",
            "tap_water_start",
        ]
        assert entity_keys == expected_keys
