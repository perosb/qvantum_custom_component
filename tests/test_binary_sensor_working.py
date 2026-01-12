"""Tests for Qvantum binary sensors (working version that avoids metaclass issues)."""

from unittest.mock import MagicMock, patch
import pytest


# Create mock base classes that don't have metaclass conflicts
class MockCoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator


class MockBinarySensorEntity:
    pass


# Mock EntityCategory
class MockEntityCategory:
    class DIAGNOSTIC:
        name = "DIAGNOSTIC"


# Mock BinarySensorDeviceClass
class MockBinarySensorDeviceClass:
    class CONNECTIVITY:
        name = "CONNECTIVITY"


# Patch the imports before importing the binary_sensor module
with patch(
    "homeassistant.helpers.update_coordinator.CoordinatorEntity", MockCoordinatorEntity
):
    with patch(
        "homeassistant.components.binary_sensor.BinarySensorEntity",
        MockBinarySensorEntity,
    ):
        with patch("homeassistant.const.EntityCategory", MockEntityCategory):
            with patch(
                "homeassistant.components.binary_sensor.BinarySensorDeviceClass",
                MockBinarySensorDeviceClass,
            ):
                from homeassistant.helpers.device_registry import DeviceInfo

                from custom_components.qvantum.binary_sensor import (
                    QvantumBaseBinaryEntity,
                    QvantumConnectedEntity,
                )


@pytest.fixture
def mock_coordinator():
    """Create a mock coordinator with test data."""
    coordinator = MagicMock()
    coordinator.data = {
        "device": {"id": "test_device_123"},
        "metrics": {
            "hpid": "test_device_123",
            "op_man_addition": 1,  # Binary sensor values
            "op_man_cooling": 0,
            "op_man_dhw": 1,
            "enable_sc_dhw": 0,
            "cooling_enabled": 1,
            "use_adaptive": 0,
            "picpin_relay_heat_l1": 1,
            "picpin_relay_heat_l2": 0,
            "picpin_relay_heat_l3": 1,
            "picpin_relay_qm10": 0,
            "qn8position": 1,
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
            mock_coordinator, "op_man_addition", "Addition Mode", mock_device, True
        )

        assert entity._metric_key == "op_man_addition"
        assert entity._attr_unique_id == "qvantum_op_man_addition_test_device_123"
        assert entity._attr_device_info == mock_device
        assert entity._attr_has_entity_name is True
        assert entity._attr_entity_registry_enabled_default is True
        assert entity._data_bearer == "metrics"

    def test_is_on_true(self, mock_coordinator, mock_device):
        """Test binary entity state when on."""
        entity = QvantumBaseBinaryEntity(
            mock_coordinator, "op_man_addition", "Addition Mode", mock_device, True
        )
        assert entity.is_on == 1  # op_man_addition is 1

    def test_is_on_false(self, mock_coordinator, mock_device):
        """Test binary entity state when off."""
        entity = QvantumBaseBinaryEntity(
            mock_coordinator, "op_man_cooling", "Cooling Mode", mock_device
        )
        assert entity.is_on == 0  # op_man_cooling is 0

    def test_available_true(self, mock_coordinator, mock_device):
        """Test binary entity availability when data exists."""
        entity = QvantumBaseBinaryEntity(
            mock_coordinator, "op_man_addition", "Addition Mode", mock_device, True
        )
        assert entity.available is True

    def test_available_false(self, mock_coordinator, mock_device):
        """Test binary entity availability when data is missing."""
        entity = QvantumBaseBinaryEntity(
            mock_coordinator, "missing_metric", "Missing", mock_device, True
        )
        assert entity.available is False


class TestQvantumConnectedEntity:
    """Test the QvantumConnectedEntity class."""

    def test_init(self, mock_coordinator, mock_device):
        """Test connected entity initialization."""
        entity = QvantumConnectedEntity(
            mock_coordinator, "connected", "Connected", mock_device, True
        )

        assert entity._attr_device_class.name == "CONNECTIVITY"
        assert entity._attr_entity_category.name == "DIAGNOSTIC"
        assert entity._data_bearer == "connectivity"

    def test_is_on_connected(self, mock_coordinator, mock_device):
        """Test connected entity state."""
        entity = QvantumConnectedEntity(
            mock_coordinator, "connected", "Connected", mock_device, True
        )
        assert entity.is_on is True  # connected is True

    def test_available_connected(self, mock_coordinator, mock_device):
        """Test connected entity availability."""
        entity = QvantumConnectedEntity(
            mock_coordinator, "connected", "Connected", mock_device, True
        )
        assert entity.available is True

    def test_available_connected_missing(self, mock_coordinator, mock_device):
        """Test connected entity availability when data is missing."""
        entity = QvantumConnectedEntity(
            mock_coordinator,
            "missing_connection",
            "Missing Connection",
            mock_device,
            True,
        )
        assert entity.available is False


@pytest.mark.asyncio
async def test_async_setup_entry(
    hass, mock_config_entry, mock_coordinator, mock_device
):
    """Test setting up binary sensor entities."""
    from custom_components.qvantum.binary_sensor import (
        async_setup_entry,
        QvantumBaseBinaryEntity,
    )
    from custom_components.qvantum import RuntimeData

    # Mock the entity registry
    mock_entity_registry = MagicMock()
    hass.data["entity_registry"] = mock_entity_registry

    mock_config_entry.runtime_data = RuntimeData(
        coordinator=mock_coordinator,
        device=mock_device,
    )

    async_add_entities = MagicMock()

    # Add entity_id property to the class for the test
    @property
    def entity_id(self):
        return f"binary_sensor.{self._attr_unique_id}"

    QvantumBaseBinaryEntity.entity_id = entity_id

    try:
        await async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Check that entities were added
        assert async_add_entities.called
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 11  # 11 sensor names

        # Check that we have the expected sensor types
        sensor_names = [
            "op_man_addition",
            "op_man_cooling",
            "op_man_dhw",
            "enable_sc_dhw",
            "enable_sc_sh",
            "cooling_enabled",
            "picpin_relay_heat_l1",
            "picpin_relay_heat_l2",
            "picpin_relay_heat_l3",
            "picpin_relay_qm10",
            "qn8position",
        ]

        for i, sensor_name in enumerate(sensor_names):
            assert entities[i]._metric_key == sensor_name
            assert entities[i]._attr_translation_key == sensor_name
    finally:
        # Clean up
        if hasattr(QvantumBaseBinaryEntity, "entity_id"):
            delattr(QvantumBaseBinaryEntity, "entity_id")
