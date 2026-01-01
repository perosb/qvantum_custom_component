"""Tests for Qvantum sensors (working version that avoids metaclass issues)."""

from unittest.mock import MagicMock, patch
import pytest


# Create mock base classes that don't have metaclass conflicts
class MockCoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator


class MockSensorEntity:
    pass


# Mock EntityCategory
class MockEntityCategory:
    class DIAGNOSTIC:
        name = "DIAGNOSTIC"


# Patch the imports before importing the sensor module
with patch(
    "homeassistant.helpers.update_coordinator.CoordinatorEntity", MockCoordinatorEntity
):
    with patch("homeassistant.components.sensor.SensorEntity", MockSensorEntity):
        with patch("homeassistant.const.EntityCategory", MockEntityCategory):
            from homeassistant.components.sensor import (
                SensorDeviceClass,
                SensorStateClass,
            )
            from homeassistant.const import (
                UnitOfTemperature,
                UnitOfEnergy,
                UnitOfPower,
                UnitOfPressure,
                UnitOfElectricCurrent,
            )
            from homeassistant.helpers.device_registry import DeviceInfo

            from custom_components.qvantum.sensor import (
                QvantumBaseSensorEntity,
                QvantumTemperatureEntity,
                QvantumEnergyEntity,
                QvantumPowerEntity,
                QvantumPressureEntity,
                QvantumCurrentEntity,
                QvantumDiagnosticEntity,
                QvantumTotalEnergyEntity,
                QvantumLatencyEntity,
            )


@pytest.fixture
def mock_coordinator():
    """Create a mock coordinator with test data."""
    coordinator = MagicMock()
    coordinator.data = {
        "device": {"id": "test_device_123"},
        "latency": 45,  # Latency at top level for QvantumLatencyEntity
        "metrics": {
            "hpid": "test_device_123",
            "bt1": 20.5,  # Temperature
            "compressorenergy": 100.0,  # Energy
            "additionalenergy": 50.0,  # Additional energy
            "powertotal": 1500.0,  # Power
            "bp1_pressure": 2.1,  # Pressure
            "inputcurrent1": 5.2,  # Current
            "tap_water_cap": 4,  # Capacity (should be divided by 2)
            "fan0_10v": 75,  # Fan percentage
            "compressormeasuredspeed": 3000,  # RPM
            "bf1_l_min": 25.5,  # Flow rate
        },
        "settings": {
            "tap_water_start": 3600,
            "tap_water_stop": 7200,
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


class TestQvantumBaseSensorEntity:
    """Test the QvantumBaseSensorEntity class."""

    def test_init_basic(self, mock_coordinator, mock_device):
        """Test basic entity initialization."""
        entity = QvantumBaseSensorEntity(mock_coordinator, "bt1", mock_device, True)

        assert entity._metric_key == "bt1"
        assert entity._attr_unique_id == "qvantum_bt1_test_device_123"
        assert entity._attr_entity_registry_enabled_default is True
        assert entity._attr_has_entity_name is True
        assert entity._attr_device_info == mock_device

    def test_state(self, mock_coordinator, mock_device):
        """Test getting entity state."""
        entity = QvantumBaseSensorEntity(mock_coordinator, "bt1", mock_device, True)
        assert entity.state == 20.5

    def test_available_true(self, mock_coordinator, mock_device):
        """Test entity availability when data exists."""
        entity = QvantumBaseSensorEntity(mock_coordinator, "bt1", mock_device, True)
        assert entity.available is True

    def test_available_false(self, mock_coordinator, mock_device):
        """Test entity availability when data is missing."""
        entity = QvantumBaseSensorEntity(
            mock_coordinator, "missing_metric", mock_device, True
        )
        assert entity.available is False

    def test_fan_unit_assignment(self, mock_coordinator, mock_device):
        """Test fan speed unit assignment."""
        entity = QvantumBaseSensorEntity(
            mock_coordinator, "fan0_10v", mock_device, True
        )
        assert entity._attr_native_unit_of_measurement == "%"

    def test_rpm_unit_assignment(self, mock_coordinator, mock_device):
        """Test RPM unit assignment."""
        entity = QvantumBaseSensorEntity(
            mock_coordinator, "compressormeasuredspeed", mock_device, True
        )
        assert entity._attr_native_unit_of_measurement == "rpm"

    def test_flow_unit_assignment(self, mock_coordinator, mock_device):
        """Test flow rate unit assignment."""
        entity = QvantumBaseSensorEntity(
            mock_coordinator, "bf1_l_min", mock_device, True
        )
        assert entity._attr_native_unit_of_measurement == "l/m"


class TestQvantumTemperatureEntity:
    """Test the QvantumTemperatureEntity class."""

    def test_init(self, mock_coordinator, mock_device):
        """Test temperature entity initialization."""
        entity = QvantumTemperatureEntity(mock_coordinator, "bt1", mock_device, True)

        assert entity._attr_device_class == SensorDeviceClass.TEMPERATURE
        assert entity._attr_native_unit_of_measurement == UnitOfTemperature.CELSIUS
        assert entity._attr_state_class == SensorStateClass.MEASUREMENT
        assert entity.state == 20.5


class TestQvantumEnergyEntity:
    """Test the QvantumEnergyEntity class."""

    def test_init(self, mock_coordinator, mock_device):
        """Test energy entity initialization."""
        entity = QvantumEnergyEntity(
            mock_coordinator, "compressorenergy", mock_device, True
        )

        assert entity._attr_device_class == SensorDeviceClass.ENERGY
        assert entity._attr_native_unit_of_measurement == UnitOfEnergy.KILO_WATT_HOUR
        assert entity._attr_state_class == SensorStateClass.TOTAL_INCREASING
        assert entity.state == 100.0

    def test_available_with_positive_value(self, mock_coordinator, mock_device):
        """Test availability when energy value is positive."""
        entity = QvantumEnergyEntity(
            mock_coordinator, "compressorenergy", mock_device, True
        )
        assert entity.available is True

    def test_available_with_zero_value(self, mock_coordinator, mock_device):
        """Test availability when energy value is zero."""
        mock_coordinator.data["metrics"]["compressorenergy"] = 0
        entity = QvantumEnergyEntity(
            mock_coordinator, "compressorenergy", mock_device, True
        )
        assert entity.available is False


class TestQvantumPowerEntity:
    """Test the QvantumPowerEntity class."""

    def test_init(self, mock_coordinator, mock_device):
        """Test power entity initialization."""
        entity = QvantumPowerEntity(mock_coordinator, "powertotal", mock_device, True)

        assert entity._attr_device_class == SensorDeviceClass.POWER
        assert entity._attr_native_unit_of_measurement == UnitOfPower.WATT
        assert entity._attr_state_class == SensorStateClass.MEASUREMENT
        assert entity.state == 1500.0


class TestQvantumPressureEntity:
    """Test the QvantumPressureEntity class."""

    def test_init(self, mock_coordinator, mock_device):
        """Test pressure entity initialization."""
        entity = QvantumPressureEntity(
            mock_coordinator, "bp1_pressure", mock_device, True
        )

        assert entity._attr_device_class == SensorDeviceClass.PRESSURE
        assert entity._attr_native_unit_of_measurement == UnitOfPressure.BAR
        assert entity._attr_state_class == SensorStateClass.MEASUREMENT
        assert entity.state == 2.1

    def test_available_with_positive_value(self, mock_coordinator, mock_device):
        """Test availability when pressure value is positive."""
        entity = QvantumPressureEntity(
            mock_coordinator, "bp1_pressure", mock_device, True
        )
        assert entity.available is True

    def test_available_with_zero_value(self, mock_coordinator, mock_device):
        """Test availability when pressure value is zero."""
        mock_coordinator.data["metrics"]["bp1_pressure"] = 0
        entity = QvantumPressureEntity(
            mock_coordinator, "bp1_pressure", mock_device, True
        )
        assert entity.available is False


class TestQvantumCurrentEntity:
    """Test the QvantumCurrentEntity class."""

    def test_init(self, mock_coordinator, mock_device):
        """Test current entity initialization."""
        entity = QvantumCurrentEntity(
            mock_coordinator, "inputcurrent1", mock_device, True
        )

        assert entity._attr_device_class == SensorDeviceClass.CURRENT
        assert entity._attr_native_unit_of_measurement == UnitOfElectricCurrent.AMPERE
        assert entity._attr_state_class == SensorStateClass.MEASUREMENT
        assert entity.state == 5.2

class TestQvantumTotalEnergyEntity:
    """Test the QvantumTotalEnergyEntity class."""

    def test_state_calculation(self, mock_coordinator, mock_device):
        """Test total energy calculation (compressor + additional)."""
        entity = QvantumTotalEnergyEntity(
            mock_coordinator, "totalenergy", mock_device, True
        )
        assert entity.state == 150  # 100 + 50

    def test_available_with_data(self, mock_coordinator, mock_device):
        """Test availability when compressor energy data exists."""
        entity = QvantumTotalEnergyEntity(
            mock_coordinator, "totalenergy", mock_device, True
        )
        assert entity.available is True

    def test_available_without_data(self, mock_coordinator, mock_device):
        """Test availability when compressor energy data is missing."""
        del mock_coordinator.data["metrics"]["compressorenergy"]
        entity = QvantumTotalEnergyEntity(
            mock_coordinator, "totalenergy", mock_device, True
        )
        assert entity.available is False


class TestQvantumDiagnosticEntity:
    """Test the QvantumDiagnosticEntity class."""

    def test_init_latency(self, mock_coordinator, mock_device):
        """Test diagnostic entity initialization for latency."""
        entity = QvantumDiagnosticEntity(mock_coordinator, "latency", mock_device, True)

        assert entity._attr_entity_category.name == "DIAGNOSTIC"
        assert entity._attr_device_class == SensorDeviceClass.DURATION
        assert entity._attr_native_unit_of_measurement == "ms"

    def test_init_generic(self, mock_coordinator, mock_device):
        """Test diagnostic entity initialization for generic metrics."""
        entity = QvantumDiagnosticEntity(mock_coordinator, "hpid", mock_device, True)

        assert entity._attr_entity_category.name == "DIAGNOSTIC"
        assert (
            not hasattr(entity, "_attr_device_class")
            or entity._attr_device_class is None
        )


class TestQvantumLatencyEntity:
    """Test the QvantumLatencyEntity class."""

    def test_init(self, mock_coordinator, mock_device):
        """Test latency entity initialization."""
        entity = QvantumLatencyEntity(mock_coordinator, "latency", mock_device, True)

        assert entity._attr_entity_category.name == "DIAGNOSTIC"
        assert entity.state == 45
        assert entity.available is True

    def test_available_false(self, mock_coordinator, mock_device):
        """Test latency entity availability when data is missing."""
        mock_coordinator.data["latency"] = None
        entity = QvantumLatencyEntity(mock_coordinator, "latency", mock_device, True)
        assert entity.available is False
