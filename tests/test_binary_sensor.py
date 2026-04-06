"""Tests for Qvantum binary sensors."""

from unittest.mock import MagicMock, patch
import pytest


# Create mock base classes that don't have metaclass conflicts
class MockCoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator


class MockBinarySensorEntity:
    pass


# Patch the imports before importing the binary_sensor module
with patch(
    "homeassistant.helpers.update_coordinator.CoordinatorEntity", MockCoordinatorEntity
):
    with patch("homeassistant.components.binary_sensor.BinarySensorEntity", MockBinarySensorEntity):
        from homeassistant.helpers.device_registry import DeviceInfo
        from homeassistant.components.binary_sensor import BinarySensorDeviceClass
        from homeassistant.const import EntityCategory

        from custom_components.qvantum.binary_sensor import (
            QvantumBaseBinaryEntity,
        )


@pytest.fixture
def mock_coordinator():
    """Create a mock coordinator with test data."""
    coordinator = MagicMock()
    coordinator.data = {
        "device": {"id": "test_device_123"},
        "values": {
            "hpid": "test_device_123",  # Add hpid for unique_id generation
            "op_man_addition": 1,  # Binary sensor value
            "op_man_defrost": 0,  # Another binary sensor value
        },
        "connectivity": {
            "connected": True,
        },
    }
    return coordinator


@pytest.fixture
def mock_device():
    """Create a mock device info."""
    return DeviceInfo(
        identifiers={("qvantum", "qvantum-test_device_123")},
        manufacturer="Qvantum",
        model="QE-6",
    )


class TestQvantumBaseBinaryEntity:
    """Test the QvantumBaseBinaryEntity class."""

    def test_init(self, mock_coordinator, mock_device):
        """Test binary entity initialization."""
        entity = QvantumBaseBinaryEntity(
            mock_coordinator, "op_man_addition", mock_device, True
        )

        assert entity._metric_key == "op_man_addition"
        assert entity._attr_unique_id == "qvantum_op_man_addition_test_device_123"
        assert entity._attr_device_info == mock_device
        assert entity._attr_has_entity_name is True
        assert entity._attr_entity_registry_enabled_default is True

    def test_is_on_true(self, mock_coordinator, mock_device):
        """Test binary entity state when on."""
        mock_coordinator.data["values"]["op_man_addition"] = 1
        entity = QvantumBaseBinaryEntity(
            mock_coordinator, "op_man_addition", mock_device, True
        )
        assert entity.is_on

    def test_is_on_false(self, mock_coordinator, mock_device):
        """Test binary entity state when off."""
        mock_coordinator.data["values"]["op_man_addition"] = 0
        entity = QvantumBaseBinaryEntity(
            mock_coordinator, "op_man_addition", mock_device, True
        )
        assert not entity.is_on

    def test_available_true(self, mock_coordinator, mock_device):
        """Test binary entity availability when data exists."""
        mock_coordinator.data["values"]["op_man_addition"] = 1
        entity = QvantumBaseBinaryEntity(
            mock_coordinator, "op_man_addition", mock_device, True
        )
        assert entity.available is True

    def test_available_false(self, mock_coordinator, mock_device):
        """Test binary entity availability when data is missing."""
        entity = QvantumBaseBinaryEntity(
            mock_coordinator, "missing_metric", mock_device, True
        )
        assert entity.available is False