"""Tests for Qvantum binary sensors."""

from unittest.mock import MagicMock, patch

import pytest


# Create mock base classes that don't have metaclass conflicts
class MockCoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator

    @property
    def available(self):
        return self.coordinator is not None


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

    class PROBLEM:
        name = "PROBLEM"


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
                )


@pytest.fixture
def mock_coordinator():
    """Create a mock coordinator with test data."""
    coordinator = MagicMock()
    coordinator.data = {
        "device": {"id": "test_device_123"},
        "values": {
            "hpid": "test_device_123",
            "op_man_addition": 1,  # Binary sensor values
            "op_man_cooling": 0,
            "op_man_dhw": 1,
            "enable_sc_dhw": 0,
            "enable_sc_sh": 1,
            "cooling_enabled": 1,
            "use_adaptive": 0,
            "picpin_relay_heat_l1": 1,
            "picpin_relay_heat_l2": 0,
            "picpin_relay_heat_l3": 1,
            "picpin_relay_gp10": 0,
            "picpin_relay_qm10": 0,
            "picpin_relay_qn8_1": 0,
            "picpin_relay_qn8_2": 1,
            "picpin_relay_gp3": 0,
            "picpin_relay_ha12": 1,
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
            mock_coordinator, "op_man_addition", mock_device, True
        )

        assert entity._metric_key == "op_man_addition"
        assert entity._attr_unique_id == "qvantum_op_man_addition_test_device_123"
        assert entity._attr_device_info == mock_device
        assert entity._attr_has_entity_name is True
        assert entity._attr_entity_registry_enabled_default is True

    def test_is_on_true(self, mock_coordinator, mock_device):
        """Test binary entity state when on."""
        entity = QvantumBaseBinaryEntity(
            mock_coordinator, "op_man_addition", mock_device, True
        )
        assert entity.is_on == 1  # op_man_addition is 1

    def test_is_on_false(self, mock_coordinator, mock_device):
        """Test binary entity state when off."""
        entity = QvantumBaseBinaryEntity(
            mock_coordinator, "op_man_cooling", mock_device
        )
        assert entity.is_on == 0  # op_man_cooling is 0

    def test_available_true(self, mock_coordinator, mock_device):
        """Test binary entity availability when data exists."""
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

    def test_connectivity_entities_are_diagnostic(self, mock_coordinator, mock_device):
        """Wi-Fi and cloud connected sensors belong in the diagnostics category."""
        wifi = QvantumBaseBinaryEntity(
            mock_coordinator, "wifi_connected", mock_device, True
        )
        cloud = QvantumBaseBinaryEntity(
            mock_coordinator, "cloud_connected", mock_device, True
        )
        heating = QvantumBaseBinaryEntity(
            mock_coordinator, "heatingreleased", mock_device, True
        )

        assert wifi._attr_entity_category.name == "DIAGNOSTIC"
        assert wifi._attr_device_class.name == "CONNECTIVITY"
        assert cloud._attr_entity_category.name == "DIAGNOSTIC"
        assert cloud._attr_device_class.name == "CONNECTIVITY"
        # Status flags are diagnostic but not connectivity device class
        assert heating._attr_entity_category.name == "DIAGNOSTIC"
        assert getattr(heating, "_attr_device_class", None) is None

    def test_alarm_active_is_problem_not_diagnostic(
        self, mock_coordinator, mock_device
    ):
        """Active-alarm flag uses PROBLEM and stays on the main device view."""
        mock_coordinator.data["values"]["alarm_active"] = 1
        entity = QvantumBaseBinaryEntity(
            mock_coordinator, "alarm_active", mock_device, True
        )
        assert entity._attr_device_class.name == "PROBLEM"
        assert getattr(entity, "_attr_entity_category", None) is None
        assert entity.is_on == 1
        assert entity.available is True

        mock_coordinator.data["values"]["alarm_active"] = 0
        assert entity.is_on == 0

    def test_status_binary_sensors_are_diagnostic(self, mock_coordinator, mock_device):
        """Release / protection / pump status flags use the diagnostics category."""
        for key in (
            "heatingreleased",
            "coolingreleased",
            "compressorreleased",
            "additionreleased",
            "freeze_protection_active",
            "compressor_blocked",
            "picpin_relay_heat_l1",
            "picpin_relay_heat_l2",
            "picpin_relay_heat_l3",
            "picpin_relay_gp10",
            "picpin_relay_qm10",
            "picpin_relay_qn8_1",
            "picpin_relay_qn8_2",
            "picpin_relay_gp3",
            "picpin_relay_ha12",
            "picpin_relay_pump",
        ):
            entity = QvantumBaseBinaryEntity(mock_coordinator, key, mock_device, True)
            assert entity._attr_entity_category.name == "DIAGNOSTIC", key
            assert getattr(entity, "_attr_device_class", None) is None, key

        demand = QvantumBaseBinaryEntity(
            mock_coordinator, "heatingdemand", mock_device, True
        )
        assert getattr(demand, "_attr_entity_category", None) is None


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

    # Mock the device registry
    mock_device_registry = MagicMock()
    mock_device_registry.async_get_device_by_identifier.return_value = None
    hass.data["device_registry"] = mock_device_registry

    mock_config_entry.runtime_data = RuntimeData(
        coordinator=mock_coordinator,
        device=mock_device,
        client=MagicMock(),
    )
    # HTTP path: MagicMock would otherwise make modbus_enabled truthy.
    mock_coordinator.modbus_enabled = False

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
        assert len(entities) == 17

        # Check that we have the expected sensor types
        sensor_names = [
            "additionreleased",
            "compressorreleased",
            "cooling_enabled",
            "coolingdemand",
            "coolingreleased",
            "dhwdemand",
            "heatingdemand",
            "heatingreleased",
            "picpin_relay_gp10",
            "picpin_relay_gp3",
            "picpin_relay_ha12",
            "picpin_relay_heat_l1",
            "picpin_relay_heat_l2",
            "picpin_relay_heat_l3",
            "picpin_relay_qm10",
            "picpin_relay_qn8_1",
            "picpin_relay_qn8_2",
        ]

        entity_metric_keys = sorted([e._metric_key for e in entities])
        assert entity_metric_keys == sorted(sensor_names)
    finally:
        # Clean up
        if hasattr(QvantumBaseBinaryEntity, "entity_id"):
            delattr(QvantumBaseBinaryEntity, "entity_id")


@pytest.mark.asyncio
async def test_async_setup_entry_modbus_includes_pump_relay(
    hass, mock_config_entry, mock_coordinator, mock_device
):
    """Modbus setup creates picpin_relay_pump; HTTP setup does not."""
    from custom_components.qvantum.binary_sensor import (
        async_setup_entry,
        QvantumBaseBinaryEntity,
    )
    from custom_components.qvantum import RuntimeData

    hass.data["entity_registry"] = MagicMock()
    mock_device_registry = MagicMock()
    mock_device_registry.async_get_device_by_identifier.return_value = None
    hass.data["device_registry"] = mock_device_registry

    mock_config_entry.runtime_data = RuntimeData(
        coordinator=mock_coordinator,
        device=mock_device,
        client=MagicMock(),
    )
    mock_coordinator.modbus_enabled = True
    mock_coordinator.data["values"]["picpin_relay_pump"] = 1
    mock_coordinator.data["values"]["vacation_mode"] = 0

    async_add_entities = MagicMock()

    @property
    def entity_id(self):
        return f"binary_sensor.{self._attr_unique_id}"

    QvantumBaseBinaryEntity.entity_id = entity_id

    try:
        await async_setup_entry(hass, mock_config_entry, async_add_entities)
        keys = {entity._metric_key for entity in async_add_entities.call_args[0][0]}
        assert "picpin_relay_pump" in keys
        assert "vacation_mode" in keys
        assert "picpin_relay_gp10" in keys
        assert "picpin_relay_ha12" in keys
    finally:
        if hasattr(QvantumBaseBinaryEntity, "entity_id"):
            delattr(QvantumBaseBinaryEntity, "entity_id")


@pytest.mark.asyncio
async def test_async_setup_entry_creates_efficiency_binary(
    hass, mock_config_entry, mock_coordinator, mock_device
):
    """Both recorder-derived flags are created when the coordinator exists."""
    from custom_components.qvantum.binary_sensor import (
        async_setup_entry,
        QvantumEfficiencyBinaryEntity,
    )
    from custom_components.qvantum import RuntimeData
    from custom_components.qvantum.efficiency_coordinator import (
        EfficiencySnapshot,
        QvantumEfficiencyCoordinator,
    )

    hass.data["entity_registry"] = MagicMock()
    mock_device_registry = MagicMock()
    mock_device_registry.async_get_device_by_identifier.return_value = None
    hass.data["device_registry"] = mock_device_registry

    efficiency = QvantumEfficiencyCoordinator.__new__(QvantumEfficiencyCoordinator)
    efficiency.data = EfficiencySnapshot(
        normalized_rising=True, dhw_heat_meter_warning=False
    )
    mock_config_entry.runtime_data = RuntimeData(
        coordinator=mock_coordinator,
        device=mock_device,
        client=MagicMock(),
        efficiency_coordinator=efficiency,
    )
    mock_coordinator.modbus_enabled = False

    async_add_entities = MagicMock()
    with (
        patch("custom_components.qvantum.entity.disable_entities_by_default"),
        patch("custom_components.qvantum.entity.cleanup_disabled_entities"),
    ):
        await async_setup_entry(hass, mock_config_entry, async_add_entities)

    entities = [
        entity
        for entity in async_add_entities.call_args[0][0]
        if isinstance(entity, QvantumEfficiencyBinaryEntity)
    ]
    assert len(entities) == 2
    by_key = {entity._metric_key: entity for entity in entities}
    assert set(by_key) == {
        "weather_normalized_consumption_rising",
        "heat_meter_deviation_warning",
    }
    trend = by_key["weather_normalized_consumption_rising"]
    warning = by_key["heat_meter_deviation_warning"]
    assert trend._attr_unique_id == (
        "qvantum_weather_normalized_consumption_rising_test_device_123"
    )
    assert warning._attr_unique_id == (
        "qvantum_heat_meter_deviation_warning_test_device_123"
    )
    assert trend.is_on is True
    assert warning.is_on is False
    assert trend.available is True
    assert warning.available is True

    efficiency.data = EfficiencySnapshot(
        normalized_rising=None, dhw_heat_meter_warning=None
    )
    assert trend.is_on is None
    assert warning.is_on is None
    assert trend.available is False
    assert warning.available is False