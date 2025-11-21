"""Tests for Qvantum sensors."""

import sys
from unittest.mock import MagicMock, patch

# Mock HA imports
sys.modules['homeassistant.components.sensor'] = MagicMock()
sys.modules['homeassistant.const'] = MagicMock()
sys.modules['homeassistant.helpers.device_registry'] = MagicMock()

# Patch the base classes to avoid metaclass conflicts
with patch('homeassistant.helpers.update_coordinator.CoordinatorEntity') as mock_coordinator_entity, \
     patch('homeassistant.components.sensor.SensorEntity') as mock_sensor_entity:

    from homeassistant.components.sensor import SensorDeviceClass
    from homeassistant.const import UnitOfTemperature, UnitOfEnergy, UnitOfPower
    from homeassistant.helpers.device_registry import DeviceInfo

    from custom_components.qvantum.sensor import (
        QvantumBaseEntity,
        QvantumTemperatureEntity,
        QvantumEnergyEntity,
        QvantumPowerEntity,
        QvantumTapWaterCapacityEntity,
        QvantumDiagnosticEntity,
        QvantumTotalEnergyEntity,
    )


class TestQvantumBaseEntity:
    """Test the QvantumBaseEntity class."""

    def test_init(self, mock_coordinator):
        """Test entity initialization."""
        device = DeviceInfo(
            identifiers={("qvantum", "test_device")},
            manufacturer="Qvantum",
            model="QE-6"
        )

        entity = QvantumBaseEntity(mock_coordinator, "bt1", device, True)

        assert entity._metric_key == "bt1"
        assert entity._attr_unique_id == "qvantum_bt1_test_device"
        assert entity._attr_entity_registry_enabled_default is True
        assert entity._attr_has_entity_name is True

    def test_state(self, mock_coordinator):
        """Test getting entity state."""
        device = DeviceInfo(
            identifiers={("qvantum", "test_device")},
            manufacturer="Qvantum",
            model="QE-6"
        )

        entity = QvantumBaseEntity(mock_coordinator, "bt1", device, True)
        assert entity.state == 20.5

    def test_available(self, mock_coordinator):
        """Test entity availability."""
        device = DeviceInfo(
            identifiers={("qvantum", "test_device")},
            manufacturer="Qvantum",
            model="QE-6"
        )

        entity = QvantumBaseEntity(mock_coordinator, "bt1", device, True)
        assert entity.available is True

        # Test unavailable
        entity_unavailable = QvantumBaseEntity(mock_coordinator, "missing_metric", device, True)
        assert entity_unavailable.available is False

    def test_fan_unit(self, mock_coordinator):
        """Test fan speed unit assignment."""
        device = DeviceInfo(
            identifiers={("qvantum", "test_device")},
            manufacturer="Qvantum",
            model="QE-6"
        )

        entity = QvantumBaseEntity(mock_coordinator, "fan0_10v", device, True)
        assert entity._attr_native_unit_of_measurement == "%"

    def test_rpm_unit(self, mock_coordinator):
        """Test RPM unit assignment."""
        device = DeviceInfo(
            identifiers={("qvantum", "test_device")},
            manufacturer="Qvantum",
            model="QE-6"
        )

        entity = QvantumBaseEntity(mock_coordinator, "compressormeasuredspeed", device, True)
        assert entity._attr_native_unit_of_measurement == "rpm"

    def test_l_min_unit(self, mock_coordinator):
        """Test l/min unit assignment."""
        device = DeviceInfo(
            identifiers={("qvantum", "test_device")},
            manufacturer="Qvantum",
            model="QE-6"
        )

        entity = QvantumBaseEntity(mock_coordinator, "bf1_l_min", device, True)
        assert entity._attr_native_unit_of_measurement == "l/m"


class TestQvantumTemperatureEntity:
    """Test the QvantumTemperatureEntity class."""

    def test_init(self, mock_coordinator):
        """Test temperature entity initialization."""
        device = DeviceInfo(
            identifiers={("qvantum", "test_device")},
            manufacturer="Qvantum",
            model="QE-6"
        )

        entity = QvantumTemperatureEntity(mock_coordinator, "bt1", device, True)

        assert entity._attr_device_class == SensorDeviceClass.TEMPERATURE
        assert entity._attr_native_unit_of_measurement == UnitOfTemperature.CELSIUS
        assert entity._attr_state_class.name == "MEASUREMENT"


class TestQvantumEnergyEntity:
    """Test the QvantumEnergyEntity class."""

    def test_init(self, mock_coordinator):
        """Test energy entity initialization."""
        device = DeviceInfo(
            identifiers={("qvantum", "test_device")},
            manufacturer="Qvantum",
            model="QE-6"
        )

        entity = QvantumEnergyEntity(mock_coordinator, "compressorenergy", device, True)

        assert entity._attr_device_class == SensorDeviceClass.ENERGY
        assert entity._attr_native_unit_of_measurement == UnitOfEnergy.KILO_WATT_HOUR
        assert entity._attr_state_class.name == "TOTAL_INCREASING"

    def test_available_with_zero_value(self, mock_coordinator):
        """Test availability when energy value is zero."""
        device = DeviceInfo(
            identifiers={("qvantum", "test_device")},
            manufacturer="Qvantum",
            model="QE-6"
        )

        # Mock zero energy value
        mock_coordinator.data["metrics"]["compressorenergy"] = 0

        entity = QvantumEnergyEntity(mock_coordinator, "compressorenergy", device, True)
        assert entity.available is False


class TestQvantumPowerEntity:
    """Test the QvantumPowerEntity class."""

    def test_init(self, mock_coordinator):
        """Test power entity initialization."""
        device = DeviceInfo(
            identifiers={("qvantum", "test_device")},
            manufacturer="Qvantum",
            model="QE-6"
        )

        entity = QvantumPowerEntity(mock_coordinator, "powertotal", device, True)

        assert entity._attr_device_class == SensorDeviceClass.POWER
        assert entity._attr_native_unit_of_measurement == UnitOfPower.WATT
        assert entity._attr_state_class.name == "MEASUREMENT"


class TestQvantumTapWaterCapacityEntity:
    """Test the QvantumTapWaterCapacityEntity class."""

    def test_state(self, mock_coordinator):
        """Test tap water capacity state calculation."""
        device = DeviceInfo(
            identifiers={("qvantum", "test_device")},
            manufacturer="Qvantum",
            model="QE-6"
        )

        # Mock tap water capacity value
        mock_coordinator.data["metrics"]["tap_water_cap"] = 4

        entity = QvantumTapWaterCapacityEntity(mock_coordinator, "tap_water_cap", device, True)
        assert entity.state == 2  # Should be divided by 2


class TestQvantumDiagnosticEntity:
    """Test the QvantumDiagnosticEntity class."""

    def test_init_latency(self, mock_coordinator):
        """Test diagnostic entity initialization for latency."""
        device = DeviceInfo(
            identifiers={("qvantum", "test_device")},
            manufacturer="Qvantum",
            model="QE-6"
        )

        entity = QvantumDiagnosticEntity(mock_coordinator, "latency", device, True)

        assert entity._attr_entity_category.name == "DIAGNOSTIC"
        assert entity._attr_device_class == SensorDeviceClass.DURATION
        assert entity._attr_native_unit_of_measurement == "ms"

    def test_init_hpid(self, mock_coordinator):
        """Test diagnostic entity initialization for HPID."""
        device = DeviceInfo(
            identifiers={("qvantum", "test_device")},
            manufacturer="Qvantum",
            model="QE-6"
        )

        entity = QvantumDiagnosticEntity(mock_coordinator, "hpid", device, True)

        assert entity._attr_entity_category.name == "DIAGNOSTIC"
        assert entity._attr_device_class is None


class TestQvantumTotalEnergyEntity:
    """Test the QvantumTotalEnergyEntity class."""

    def test_state(self, mock_coordinator):
        """Test total energy calculation."""
        device = DeviceInfo(
            identifiers={("qvantum", "test_device")},
            manufacturer="Qvantum",
            model="QE-6"
        )

        # Mock energy values
        mock_coordinator.data["metrics"]["compressorenergy"] = 100
        mock_coordinator.data["metrics"]["additionalenergy"] = 50

        entity = QvantumTotalEnergyEntity(mock_coordinator, "totalenergy", device, True)
        assert entity.state == 150

    def test_available(self, mock_coordinator):
        """Test total energy availability."""
        device = DeviceInfo(
            identifiers={("qvantum", "test_device")},
            manufacturer="Qvantum",
            model="QE-6"
        )

        entity = QvantumTotalEnergyEntity(mock_coordinator, "totalenergy", device, True)
        assert entity.available is True

        # Test unavailable when compressorenergy is missing
        del mock_coordinator.data["metrics"]["compressorenergy"]
        entity_unavailable = QvantumTotalEnergyEntity(mock_coordinator, "totalenergy", device, True)
        assert entity_unavailable.available is False