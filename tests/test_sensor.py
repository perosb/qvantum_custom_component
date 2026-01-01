"""Tests for Qvantum sensors."""

from unittest.mock import MagicMock, patch
import pytest


# Create mock base classes that don't have metaclass conflicts
class MockCoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator


class MockSensorEntity:
    pass


# Patch the imports before importing the sensor module
with patch(
    "homeassistant.helpers.update_coordinator.CoordinatorEntity", MockCoordinatorEntity
):
    with patch("homeassistant.components.sensor.SensorEntity", MockSensorEntity):
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
            _get_sensor_type,
            async_setup_entry,
        )
        from homeassistant.helpers.entity_registry import RegistryEntryDisabler


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
            "heatingpower": 2.5,  # Heating power in kW
            "dhwpower": 1.8,  # DHW power in kW
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

    def test_fanrpm_unit_assignment(self, mock_coordinator, mock_device):
        """Test fanrpm RPM unit assignment."""
        entity = QvantumBaseSensorEntity(mock_coordinator, "fanrpm", mock_device, True)
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
        assert not hasattr(entity, "_attr_suggested_display_precision")
        assert entity.state == 1500.0

    def test_heatingpower_init(self, mock_coordinator, mock_device):
        """Test heating power entity initialization with kW unit and precision."""
        entity = QvantumPowerEntity(mock_coordinator, "heatingpower", mock_device, True)

        assert entity._attr_device_class == SensorDeviceClass.POWER
        assert entity._attr_native_unit_of_measurement == UnitOfPower.KILO_WATT
        assert entity._attr_state_class == SensorStateClass.MEASUREMENT
        assert entity._attr_suggested_display_precision == 2
        assert entity.state == 2.5

    def test_dhwpower_init(self, mock_coordinator, mock_device):
        """Test DHW power entity initialization with kW unit and precision."""
        entity = QvantumPowerEntity(mock_coordinator, "dhwpower", mock_device, True)

        assert entity._attr_device_class == SensorDeviceClass.POWER
        assert entity._attr_native_unit_of_measurement == UnitOfPower.KILO_WATT
        assert entity._attr_state_class == SensorStateClass.MEASUREMENT
        assert entity._attr_suggested_display_precision == 2
        assert entity.state == 1.8


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


class TestGetSensorType:
    """Test the _get_sensor_type function."""

    def test_temperature_metrics(self):
        """Test temperature metric classification."""
        # Test various temperature patterns
        assert _get_sensor_type("bt1") == QvantumTemperatureEntity
        assert _get_sensor_type("bt2") == QvantumTemperatureEntity
        assert _get_sensor_type("bp1_temp") == QvantumTemperatureEntity
        assert _get_sensor_type("dhw_normal_start") == QvantumTemperatureEntity

    def test_energy_metrics(self):
        """Test energy metric classification."""
        assert _get_sensor_type("compressorenergy") == QvantumEnergyEntity
        assert _get_sensor_type("additionalenergy") == QvantumEnergyEntity

    def test_power_metrics(self):
        """Test power metric classification."""
        assert _get_sensor_type("powertotal") == QvantumPowerEntity

    def test_current_metrics(self):
        """Test current metric classification."""
        assert _get_sensor_type("inputcurrent1") == QvantumCurrentEntity
        assert _get_sensor_type("inputcurrent2") == QvantumCurrentEntity

    def test_pressure_metrics(self):
        """Test pressure metric classification."""
        assert _get_sensor_type("bp1_pressure") == QvantumPressureEntity
        assert _get_sensor_type("bp2_pressure") == QvantumPressureEntity

    def test_base_entity_default(self):
        """Test that unknown metrics default to base entity."""
        assert _get_sensor_type("unknown_metric") == QvantumBaseSensorEntity
        assert _get_sensor_type("fan0_10v") == QvantumBaseSensorEntity
        assert _get_sensor_type("compressormeasuredspeed") == QvantumBaseSensorEntity
        assert _get_sensor_type("bf1_l_min") == QvantumBaseSensorEntity
        assert _get_sensor_type("tap_water_cap") == QvantumBaseSensorEntity


class TestSensorSetup:
    """Test sensor setup and entity registry handling."""

    @pytest.fixture
    def mock_config_entry(self, mock_coordinator, mock_device):
        """Mock config entry with runtime data."""
        from homeassistant.config_entries import ConfigEntry
        from custom_components.qvantum import RuntimeData

        config_entry = MagicMock(spec=ConfigEntry)
        config_entry.runtime_data = RuntimeData(
            coordinator=mock_coordinator, device=mock_device
        )
        return config_entry

    @pytest.fixture
    def mock_entity_registry(self):
        """Mock entity registry."""
        registry = MagicMock()
        return registry

    @pytest.fixture
    def mock_hass(self, mock_entity_registry):
        """Mock Home Assistant instance with entity registry."""
        hass = MagicMock()
        hass.data = {"entity_registry": mock_entity_registry}
        return hass

    @pytest.mark.asyncio
    async def test_async_setup_entry_disables_default_disabled_entities(
        self, mock_hass, mock_config_entry, mock_coordinator, mock_device
    ):
        """Test that entities in DEFAULT_DISABLED_METRICS are disabled on first setup."""
        from custom_components.qvantum.const import DEFAULT_DISABLED_METRICS

        # Mock entity registry - entities don't exist yet (first setup)
        mock_entity_registry = mock_hass.data["entity_registry"]
        mock_entity_registry.async_get.return_value = None
        mock_entity_registry.async_update_entity = MagicMock()

        # Mock async_add_entities to assign entity_ids
        def mock_async_add_entities(entities):
            for sensor in entities:
                sensor.entity_id = f"sensor.qvantum_{sensor._metric_key}_{sensor._hpid}"

        async_add_entities = MagicMock(side_effect=mock_async_add_entities)

        await async_setup_entry(mock_hass, mock_config_entry, async_add_entities)

        # Verify entities were added
        assert async_add_entities.called
        entities = async_add_entities.call_args[0][0]

        # Find disabled entities
        disabled_entities = [
            entity
            for entity in entities
            if not entity._attr_entity_registry_enabled_default
        ]

        # Verify that async_update_entity was called for each disabled entity
        assert mock_entity_registry.async_update_entity.call_count == len(
            disabled_entities
        )

        # Verify calls were made with correct parameters
        calls = mock_entity_registry.async_update_entity.call_args_list
        for call in calls:
            args, kwargs = call
            assert "disabled_by" in kwargs
            assert kwargs["disabled_by"] == RegistryEntryDisabler.INTEGRATION

    @pytest.mark.asyncio
    async def test_async_setup_entry_respects_user_enabled_entities(
        self, mock_hass, mock_config_entry, mock_coordinator, mock_device
    ):
        """Test that manually enabled entities remain enabled after restart."""
        # Mock entity registry - entity exists and is enabled (user enabled it)
        mock_entity_registry = mock_hass.data["entity_registry"]

        # Create mock entity entry that's enabled
        mock_entity = MagicMock()
        mock_entity.disabled = False  # Entity is enabled
        mock_entity.disabled_by = None  # No one disabled it
        mock_entity_registry.async_get.return_value = mock_entity
        mock_entity_registry.async_update_entity = MagicMock()

        # Mock async_add_entities to assign entity_ids
        def mock_async_add_entities(entities):
            for sensor in entities:
                sensor.entity_id = f"sensor.qvantum_{sensor._metric_key}_{sensor._hpid}"

        async_add_entities = MagicMock(side_effect=mock_async_add_entities)

        await async_setup_entry(mock_hass, mock_config_entry, async_add_entities)

        # Verify that async_update_entity was NOT called for the enabled entity
        # (since we respect user's choice to enable it)
        mock_entity_registry.async_update_entity.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_setup_entry_respects_user_disabled_entities(
        self, mock_hass, mock_config_entry, mock_coordinator, mock_device
    ):
        """Test that manually disabled entities remain disabled after restart."""
        # Mock entity registry - entity exists and is disabled by user
        mock_entity_registry = mock_hass.data["entity_registry"]

        # Create mock entity entry that's disabled by user
        mock_entity = MagicMock()
        mock_entity.disabled = True  # Entity is disabled
        mock_entity.disabled_by = RegistryEntryDisabler.USER
        mock_entity_registry.async_get.return_value = mock_entity
        mock_entity_registry.async_update_entity = MagicMock()

        # Mock async_add_entities to assign entity_ids
        def mock_async_add_entities(entities):
            for sensor in entities:
                sensor.entity_id = f"sensor.qvantum_{sensor._metric_key}_{sensor._hpid}"

        async_add_entities = MagicMock(side_effect=mock_async_add_entities)

        await async_setup_entry(mock_hass, mock_config_entry, async_add_entities)

        # Verify that async_update_entity was NOT called
        # (since we respect user's choice to disable it)
        mock_entity_registry.async_update_entity.assert_not_called()

    @pytest.mark.asyncio
    async def test_integration_disabled_entities_update(
        self, mock_hass, mock_config_entry, mock_coordinator, mock_device
    ):
        """Test that entities disabled by integration can be updated on subsequent restarts."""
        from custom_components.qvantum.const import DEFAULT_DISABLED_METRICS

        # Define the exclusion patterns locally (same as in sensor.py)
        EXCLUDED_METRIC_PATTERNS = [
            "op_man_",
            "enable",
            "picpin_",
            "qn8",
            "use_",
        ]

        # Calculate expected calls dynamically to avoid relying on a magic number
        expected_calls = len(
            [
                metric
                for metric in DEFAULT_DISABLED_METRICS
                if not any(pattern in metric for pattern in EXCLUDED_METRIC_PATTERNS)
            ]
        )

        # Mock entity registry - all disabled entities exist and are disabled by integration
        mock_entity_registry = mock_hass.data["entity_registry"]

        # Create mock entity entry that's disabled by integration
        mock_entity = MagicMock()
        mock_entity.disabled = True  # Entity is disabled
        mock_entity.disabled_by = RegistryEntryDisabler.INTEGRATION

        # Mock async_get to return the entity for all disabled metrics that are actually created
        def mock_async_get(entity_id):
            # Extract metric key from entity_id (format: sensor.qvantum_{metric_key}_{hpid})
            parts = entity_id.split("_")
            if len(parts) >= 3 and parts[0] == "sensor" and parts[1] == "qvantum":
                metric_key = "_".join(
                    parts[2:-1]
                )  # Skip "sensor", "qvantum", and last part (hpid)
                # Check if metric should be included (not excluded by patterns)
                should_exclude = any(
                    pattern in metric_key for pattern in EXCLUDED_METRIC_PATTERNS
                )
                if metric_key in DEFAULT_DISABLED_METRICS and not should_exclude:
                    return mock_entity
            return None

        mock_entity_registry.async_get.side_effect = mock_async_get
        mock_entity_registry.async_update_entity = MagicMock()

        # Mock async_add_entities to assign entity_ids
        def mock_async_add_entities(entities):
            for sensor in entities:
                sensor.entity_id = f"sensor.qvantum_{sensor._metric_key}_{sensor._hpid}"

        async_add_entities = MagicMock(side_effect=mock_async_add_entities)

        await async_setup_entry(mock_hass, mock_config_entry, async_add_entities)

        # Verify that async_update_entity was called for all disabled entities that are actually created
        assert mock_entity_registry.async_update_entity.call_count == expected_calls

        # Verify calls were made with correct parameters
        calls = mock_entity_registry.async_update_entity.call_args_list
        for call in calls:
            args, kwargs = call
            assert "disabled_by" in kwargs
            assert kwargs["disabled_by"] == RegistryEntryDisabler.INTEGRATION
