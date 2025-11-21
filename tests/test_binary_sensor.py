"""Tests for Qvantum binary sensors."""

import sys
from unittest.mock import MagicMock, patch

# Mock HA imports
sys.modules['homeassistant.helpers.device_registry'] = MagicMock()

# Patch the base classes to avoid metaclass conflicts
with patch('homeassistant.helpers.update_coordinator.CoordinatorEntity') as mock_coordinator_entity, \
     patch('homeassistant.components.binary_sensor.BinarySensorEntity') as mock_binary_sensor_entity:

    from homeassistant.helpers.device_registry import DeviceInfo

    from custom_components.qvantum.binary_sensor import (
        QvantumBaseBinaryEntity,
        QvantumConnectedEntity,
    )


class TestQvantumBaseBinaryEntity:
    """Test the QvantumBaseBinaryEntity class."""

    def test_init(self, mock_coordinator):
        """Test binary entity initialization."""
        device = DeviceInfo(
            identifiers={("qvantum", "test_device")},
            manufacturer="Qvantum",
            model="QE-6"
        )

        entity = QvantumBaseBinaryEntity(mock_coordinator, "op_man_addition", "Addition Mode", device)

        assert entity._metric_key == "op_man_addition"
        assert entity._attr_unique_id == "qvantum_op_man_addition_test_device"
        assert entity._attr_device_info == device
        assert entity._attr_has_entity_name is True
        assert entity._data_bearer == "metrics"

    def test_is_on(self, mock_coordinator):
        """Test binary entity state."""
        device = DeviceInfo(
            identifiers={("qvantum", "test_device")},
            manufacturer="Qvantum",
            model="QE-6"
        )

        # Mock binary sensor value
        mock_coordinator.data["metrics"]["op_man_addition"] = 1

        entity = QvantumBaseBinaryEntity(mock_coordinator, "op_man_addition", "Addition Mode", device)
        assert entity.is_on is True

        # Test False value
        mock_coordinator.data["metrics"]["op_man_addition"] = 0
        assert entity.is_on is False

    def test_available(self, mock_coordinator):
        """Test binary entity availability."""
        device = DeviceInfo(
            identifiers={("qvantum", "test_device")},
            manufacturer="Qvantum",
            model="QE-6"
        )

        entity = QvantumBaseBinaryEntity(mock_coordinator, "op_man_addition", "Addition Mode", device)
        assert entity.available is True

        # Test unavailable
        entity_unavailable = QvantumBaseBinaryEntity(mock_coordinator, "missing_metric", "Missing", device)
        assert entity_unavailable.available is False


class TestQvantumConnectedEntity:
    """Test the QvantumConnectedEntity class."""

    def test_init(self, mock_coordinator):
        """Test connected entity initialization."""
        device = DeviceInfo(
            identifiers={("qvantum", "test_device")},
            manufacturer="Qvantum",
            model="QE-6"
        )

        entity = QvantumConnectedEntity(mock_coordinator, "connected", "Connected", device)

        assert entity._attr_device_class.name == "CONNECTIVITY"
        assert entity._attr_entity_category.name == "DIAGNOSTIC"
        assert entity._data_bearer == "connectivity"

    def test_is_on_connected(self, mock_coordinator):
        """Test connected entity state."""
        device = DeviceInfo(
            identifiers={("qvantum", "test_device")},
            manufacturer="Qvantum",
            model="QE-6"
        )

        # Mock connectivity data
        mock_coordinator.data["connectivity"] = {"connected": True}

        entity = QvantumConnectedEntity(mock_coordinator, "connected", "Connected", device)
        assert entity.is_on is True

    def test_available_connected(self, mock_coordinator):
        """Test connected entity availability."""
        device = DeviceInfo(
            identifiers={("qvantum", "test_device")},
            manufacturer="Qvantum",
            model="QE-6"
        )

        mock_coordinator.data["connectivity"] = {"connected": True}

        entity = QvantumConnectedEntity(mock_coordinator, "connected", "Connected", device)
        assert entity.available is True