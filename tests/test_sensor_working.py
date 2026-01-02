"""Tests for Qvantum sensors (working version that avoids metaclass issues)."""

from datetime import datetime
from unittest.mock import MagicMock, patch
import pytest


# Create mock base classes that don't have metaclass conflicts
class MockCoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator

    @property
    def available(self):
        """Mock available property."""
        return self.coordinator is not None


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
                QvantumFirmwareSensorEntity,
                QvantumFirmwareLastCheckSensorEntity,
            )


@pytest.fixture
def mock_coordinator():
    """Create a mock coordinator with test data."""
    coordinator = MagicMock()
    coordinator.data = {
        "device": {
            "id": "test_device_123",
            "model": "QE-6",
            "vendor": "Qvantum",
            "device_metadata": {
                "display_fw_version": "1.3.6",
                "cc_fw_version": "140",
                "inv_fw_version": "140",
            },
        },
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


@pytest.fixture
def mock_firmware_coordinator(mock_coordinator):
    """Create a mock firmware coordinator with test data."""
    firmware_coordinator = MagicMock()
    firmware_coordinator.main_coordinator = mock_coordinator
    firmware_coordinator.data = {
        "firmware_versions": {
            "display_fw_version": "1.3.6",
            "cc_fw_version": "140",
            "inv_fw_version": "140",
        },
        "last_check": "2024-01-01T12:00:00.000Z",
    }
    return firmware_coordinator


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


class TestQvantumFirmwareSensorEntity:
    """Test the QvantumFirmwareSensorEntity class."""

    def test_init(self, mock_firmware_coordinator, mock_device):
        """Test firmware sensor entity initialization."""
        entity = QvantumFirmwareSensorEntity(
            mock_firmware_coordinator, "display_fw_version", mock_device, True
        )

        assert entity._attr_entity_category.name == "DIAGNOSTIC"
        assert entity.firmware_key == "display_fw_version"
        assert entity._attr_translation_key == "firmware_display_fw_version"

    def test_state_from_firmware_coordinator(
        self, mock_firmware_coordinator, mock_device
    ):
        """Test firmware version from firmware coordinator data."""
        entity = QvantumFirmwareSensorEntity(
            mock_firmware_coordinator, "display_fw_version", mock_device, True
        )

        assert entity.state == "1.3.6"

    def test_state_fallback_to_device_metadata(
        self, mock_firmware_coordinator, mock_device
    ):
        """Test firmware version fallback to device metadata."""
        # Clear firmware coordinator data to test fallback
        mock_firmware_coordinator.data = {}

        entity = QvantumFirmwareSensorEntity(
            mock_firmware_coordinator, "display_fw_version", mock_device, True
        )

        # The mock_coordinator fixture has device metadata
        assert entity.state == "1.3.6"  # From device metadata in main coordinator

    def test_state_none_when_no_data(self, mock_firmware_coordinator, mock_device):
        """Test firmware version returns None when no data available."""
        mock_firmware_coordinator.data = {}
        mock_firmware_coordinator.main_coordinator.data = {}

        entity = QvantumFirmwareSensorEntity(
            mock_firmware_coordinator, "display_fw_version", mock_device, True
        )

        assert entity.state is None


class TestQvantumFirmwareLastCheckSensorEntity:
    """Test the QvantumFirmwareLastCheckSensorEntity class."""

    def test_init(self, mock_firmware_coordinator, mock_device):
        """Test firmware last check sensor entity initialization."""
        entity = QvantumFirmwareLastCheckSensorEntity(
            mock_firmware_coordinator, "firmware_last_check", mock_device, True
        )

        assert entity._attr_entity_category.name == "DIAGNOSTIC"
        assert entity._attr_device_class == SensorDeviceClass.TIMESTAMP

    def test_state_with_last_check(self, mock_firmware_coordinator, mock_device):
        """Test last check timestamp parsing."""
        entity = QvantumFirmwareLastCheckSensorEntity(
            mock_firmware_coordinator, "firmware_last_check", mock_device, True
        )

        state = entity.state
        assert state is not None
        # Should be a datetime object for TIMESTAMP device class
        assert isinstance(state, datetime)
        # Should parse the expected timestamp "2024-01-01T12:00:00.000Z"
        assert state.year == 2024
        assert state.month == 1
        assert state.day == 1
        assert state.hour == 12
        assert state.minute == 0
        assert state.second == 0

    def test_state_none_when_no_data(self, mock_firmware_coordinator, mock_device):
        """Test last check returns None when no data available."""
        mock_firmware_coordinator.data = {}

        entity = QvantumFirmwareLastCheckSensorEntity(
            mock_firmware_coordinator, "firmware_last_check", mock_device, True
        )

        assert entity.state is None
