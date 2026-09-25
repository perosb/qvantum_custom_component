"""Tests for Qvantum sensors."""

from datetime import datetime, timezone
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
                UnitOfTime,
            )
            from homeassistant.helpers.device_registry import DeviceInfo
            from homeassistant.helpers.entity_registry import RegistryEntryDisabler

            from custom_components.qvantum.sensor import (
                QvantumAccessExpireEntity,
                QvantumBaseSensorEntity,
                QvantumCurrentEntity,
                QvantumDiagnosticEntity,
                QvantumDisplayFirmwareEntity,
                QvantumEnergyEntity,
                QvantumFirmwareLastCheckSensorEntity,
                QvantumFirmwareSensorEntity,
                QvantumHeatingCurveAdvisorEntity,
                QvantumPowerEntity,
                QvantumPressureEntity,
                QvantumTemperatureEntity,
                QvantumTimerEntity,
                QvantumTotalEnergyEntity,
                _get_sensor_type,
                async_setup_entry,
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
        "values": {
            "hpid": "test_device_123",
            "bt1": 20.5,  # Temperature
            "compressorenergy": 100.0,  # Energy kWh
            "heatingenergy": 80.0,  # Energy kWh
            "dhwenergy": 30.0,  # Energy kWh
            "additionalenergy": 50.0,  # Additional energy kWh
            "powertotal": 1500.0,  # Power W
            "heatingpower": 2500.0,  # Heating power W (derived; not kW)
            "dhwpower": 1800.0,  # DHW power W (derived; not kW)
            "bp1_pressure": 2.1,  # Pressure
            "inputcurrent1": 5.2,  # Current
            "tap_water_cap": 4,  # Capacity (should be divided by 2)
            "fan0_10v": 75,  # Fan percentage
            "compressormeasuredspeed": 3000,  # RPM
            "bf1_l_min": 25.5,  # Flow rate
            "qn8position": 1,  # Position sensor
            "tap_water_start": 3600,
            "tap_water_stop": 7200,
            "tap_stop": 1712232000,
        },
    }
    coordinator.modbus_enabled = False
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
        "access_level": {
            "readAccessLevel": 20,
            "writeAccessLevel": 20,
            "expiresAt": "2026-01-26T18:35:29.768Z",
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
        assert entity.native_value == 20.5

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

    def test_degree_minute_unit_assignment(self, mock_coordinator, mock_device):
        """Test degree minute unit assignment."""
        entity = QvantumBaseSensorEntity(
            mock_coordinator, "degree_minute", mock_device, True
        )
        assert entity._attr_native_unit_of_measurement in {"dm", "°min"}

    def test_compressor_blocked_sec_unit_assignment(self, mock_coordinator, mock_device):
        """Blocked-compressor countdown is a duration in seconds."""
        entity = QvantumBaseSensorEntity(
            mock_coordinator, "compressor_blocked_sec", mock_device, True
        )
        assert entity._attr_native_unit_of_measurement == UnitOfTime.SECONDS
        assert entity._attr_device_class == SensorDeviceClass.DURATION
        assert entity._attr_entity_category.name == "DIAGNOSTIC"

    def test_ventilation_filter_time_left_unit_assignment(
        self, mock_coordinator, mock_device
    ):
        """Filter remaining life is a duration in hours."""
        entity = QvantumBaseSensorEntity(
            mock_coordinator, "ventilation_filter_time_left", mock_device, True
        )
        assert entity._attr_native_unit_of_measurement == UnitOfTime.HOURS
        assert entity._attr_device_class == SensorDeviceClass.DURATION
        assert entity._attr_entity_category.name == "DIAGNOSTIC"

    @pytest.mark.parametrize(
        "metric_key",
        ("compressor_run_time", "ventilation_fan_run_time"),
    )
    def test_runtime_hours_unit_assignment(
        self, mock_coordinator, mock_device, metric_key
    ):
        """Lifetime run-time counters are diagnostic durations in hours."""
        entity = QvantumBaseSensorEntity(
            mock_coordinator, metric_key, mock_device, True
        )
        assert entity._attr_native_unit_of_measurement == UnitOfTime.HOURS
        assert entity._attr_device_class == SensorDeviceClass.DURATION
        assert entity._attr_state_class == SensorStateClass.TOTAL_INCREASING
        assert entity._attr_entity_category.name == "DIAGNOSTIC"

    def test_diagnostic_countdown_sensors(self, mock_coordinator, mock_device):
        """Filter life, blocked countdown, and lifetime counters are diagnostics."""
        for key in (
            "ventilation_filter_time_left",
            "compressor_blocked_sec",
            "compressor_run_time",
            "compressor_starts",
            "ventilation_fan_run_time",
        ):
            entity = QvantumBaseSensorEntity(mock_coordinator, key, mock_device, True)
            assert entity._attr_entity_category.name == "DIAGNOSTIC", key

        other = QvantumBaseSensorEntity(mock_coordinator, "bt1", mock_device, True)
        assert getattr(other, "_attr_entity_category", None) is None

    def test_alarm_count_sensor(self, mock_coordinator, mock_device):
        """Active-alarm count is a diagnostic measurement with integer display."""
        entity = QvantumBaseSensorEntity(
            mock_coordinator, "active_alarms", mock_device, True
        )
        assert entity._attr_entity_category.name == "DIAGNOSTIC"
        assert entity._attr_state_class == SensorStateClass.MEASUREMENT
        assert entity._attr_suggested_display_precision == 0
        assert getattr(entity, "_attr_device_class", None) is None

    @pytest.mark.parametrize("metric_key", [f"alarm_{i}_code" for i in range(1, 6)])
    def test_alarm_code_sensors(self, mock_coordinator, mock_device, metric_key):
        """Alarm code slots are diagnostic integers without a state class."""
        entity = QvantumBaseSensorEntity(
            mock_coordinator, metric_key, mock_device, True
        )
        assert entity._attr_entity_category.name == "DIAGNOSTIC"
        assert entity._attr_suggested_display_precision == 0
        assert getattr(entity, "_attr_state_class", None) is None
        assert getattr(entity, "_attr_device_class", None) is None

    def test_compressor_starts_unit_assignment(self, mock_coordinator, mock_device):
        """Compressor start count is a diagnostic total-increasing counter."""
        entity = QvantumBaseSensorEntity(
            mock_coordinator, "compressor_starts", mock_device, True
        )
        assert getattr(entity, "_attr_native_unit_of_measurement", None) is None
        assert getattr(entity, "_attr_device_class", None) is None
        assert entity._attr_state_class == SensorStateClass.TOTAL_INCREASING
        assert entity._attr_entity_category.name == "DIAGNOSTIC"


class TestQvantumTemperatureEntity:
    """Test the QvantumTemperatureEntity class."""

    def test_init(self, mock_coordinator, mock_device):
        """Test temperature entity initialization."""
        entity = QvantumTemperatureEntity(mock_coordinator, "bt1", mock_device, True)

        assert entity._attr_device_class == SensorDeviceClass.TEMPERATURE
        assert entity._attr_native_unit_of_measurement == UnitOfTemperature.CELSIUS
        assert entity._attr_state_class == SensorStateClass.MEASUREMENT
        assert entity.native_value == 20.5


class TestQvantumEnergyEntity:
    """Test the QvantumEnergyEntity class."""

    @pytest.mark.parametrize(
        "metric_key,expected_state",
        [
            ("compressorenergy", 100.0),
            ("heatingenergy", 80.0),
            ("dhwenergy", 30.0),
            ("additionalenergy", 50.0),
        ],
    )
    def test_init(self, mock_coordinator, mock_device, metric_key, expected_state):
        """Energy sensors are Energy Dashboard compatible (ENERGY + TOTAL_INCREASING + kWh)."""
        entity = QvantumEnergyEntity(mock_coordinator, metric_key, mock_device, True)

        assert entity._attr_device_class == SensorDeviceClass.ENERGY
        assert entity._attr_native_unit_of_measurement == UnitOfEnergy.KILO_WATT_HOUR
        assert entity._attr_state_class == SensorStateClass.TOTAL_INCREASING
        assert entity.native_value == expected_state

    def test_available_with_positive_value(self, mock_coordinator, mock_device):
        """Test availability when energy value is positive."""
        entity = QvantumEnergyEntity(
            mock_coordinator, "compressorenergy", mock_device, True
        )
        assert entity.available is True

    def test_available_with_zero_value(self, mock_coordinator, mock_device):
        """Test availability when energy value is zero (zero is a valid value)."""
        mock_coordinator.data["values"]["compressorenergy"] = 0
        entity = QvantumEnergyEntity(
            mock_coordinator, "compressorenergy", mock_device, True
        )
        assert entity.native_value == 0
        assert entity.available is True


class TestQvantumPowerEntity:
    """Test the QvantumPowerEntity class."""

    @pytest.mark.parametrize(
        "metric_key,expected_state",
        [
            ("powertotal", 1500.0),
            ("heatingpower", 2500.0),
            ("dhwpower", 1800.0),
        ],
    )
    def test_init(self, mock_coordinator, mock_device, metric_key, expected_state):
        """Power sensors are POWER + MEASUREMENT + W (heatingpower/dhwpower are W, not kW)."""
        entity = QvantumPowerEntity(mock_coordinator, metric_key, mock_device, True)

        assert entity._attr_device_class == SensorDeviceClass.POWER
        assert entity._attr_native_unit_of_measurement == UnitOfPower.WATT
        assert entity._attr_state_class == SensorStateClass.MEASUREMENT
        assert not hasattr(entity, "_attr_suggested_display_precision")
        assert entity.native_value == expected_state


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
        assert entity.native_value == 2.1

    def test_available_with_positive_value(self, mock_coordinator, mock_device):
        """Test availability when pressure value is positive."""
        entity = QvantumPressureEntity(
            mock_coordinator, "bp1_pressure", mock_device, True
        )
        assert entity.available is True

    def test_available_with_zero_value(self, mock_coordinator, mock_device):
        """Test availability when pressure value is zero (zero is a valid value)."""
        mock_coordinator.data["values"]["bp1_pressure"] = 0
        entity = QvantumPressureEntity(
            mock_coordinator, "bp1_pressure", mock_device, True
        )
        assert entity.available is True

    def test_available_without_value(self, mock_coordinator, mock_device):
        """Test availability when pressure value is missing."""
        del mock_coordinator.data["values"]["bp1_pressure"]
        entity = QvantumPressureEntity(
            mock_coordinator, "bp1_pressure", mock_device, True
        )
        assert entity.available is False

    def test_unavailable_with_none_value(self, mock_coordinator, mock_device):
        """Test availability when pressure value is None."""
        mock_coordinator.data["values"]["bp1_pressure"] = None
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
        assert entity.native_value == 5.2


class TestQvantumTotalEnergyEntity:
    """Test the QvantumTotalEnergyEntity class."""

    def test_state_calculation(self, mock_coordinator, mock_device):
        """Test total energy calculation (compressor + additional)."""
        entity = QvantumTotalEnergyEntity(
            mock_coordinator, "totalenergy", mock_device, True
        )
        assert entity._attr_device_class == SensorDeviceClass.ENERGY
        assert entity._attr_native_unit_of_measurement == UnitOfEnergy.KILO_WATT_HOUR
        assert entity._attr_state_class == SensorStateClass.TOTAL_INCREASING
        assert entity.native_value == 150  # 100 + 50

    def test_available_with_data(self, mock_coordinator, mock_device):
        """Test availability when compressor energy data exists."""
        entity = QvantumTotalEnergyEntity(
            mock_coordinator, "totalenergy", mock_device, True
        )
        assert entity.available is True

    def test_available_without_data(self, mock_coordinator, mock_device):
        """Test availability when compressor energy data is missing."""
        del mock_coordinator.data["values"]["compressorenergy"]
        entity = QvantumTotalEnergyEntity(
            mock_coordinator, "totalenergy", mock_device, True
        )
        assert entity.available is False

    def test_state_is_none_when_component_missing(self, mock_coordinator, mock_device):
        """Total energy state should be unknown when one component is missing."""
        del mock_coordinator.data["values"]["additionalenergy"]
        entity = QvantumTotalEnergyEntity(
            mock_coordinator, "totalenergy", mock_device, True
        )
        assert entity.native_value is None
        assert entity.available is False

    def test_unavailable_when_both_components_zero(self, mock_coordinator, mock_device):
        """Total energy should be unavailable when both components are zero."""
        mock_coordinator.data["values"]["compressorenergy"] = 0
        mock_coordinator.data["values"]["additionalenergy"] = 0
        entity = QvantumTotalEnergyEntity(
            mock_coordinator, "totalenergy", mock_device, True
        )
        assert entity.native_value is None
        assert entity.available is False


class TestQvantumHeatingCurveAdvisorEntity:
    """Test the QvantumHeatingCurveAdvisorEntity class."""

    def test_state_and_attributes(self, mock_coordinator, mock_device):
        """The advisor exposes the derived state and its context."""
        expected_attributes = {
            "mean_deviation_c": 1.5,
            "observed_hours": 6.0,
            "window_hours": 6.0,
            "curve_type_heating": 0,
            "bt1": 5.0,
        }
        mock_coordinator.data["values"]["heating_curve_advisor"] = {
            "state": "reduce",
            **expected_attributes,
        }
        entity = QvantumHeatingCurveAdvisorEntity(
            mock_coordinator, "heating_curve_advisor", mock_device, True
        )

        assert entity.native_value == "reduce"
        assert entity.available is True
        assert entity.extra_state_attributes == expected_attributes
        assert getattr(entity, "_attr_device_class", None) is None
        assert entity._attr_icon == "mdi:tune-variant"

    def test_unavailable_without_advice(self, mock_coordinator, mock_device):
        """No derived advice means no state and no attributes."""
        entity = QvantumHeatingCurveAdvisorEntity(
            mock_coordinator, "heating_curve_advisor", mock_device, True
        )

        assert entity.native_value is None
        assert entity.available is False
        assert entity.extra_state_attributes is None

    def test_non_dict_value_is_treated_as_missing(self, mock_coordinator, mock_device):
        """A plain metric value cannot satisfy the advisor contract."""
        mock_coordinator.data["values"]["heating_curve_advisor"] = "reduce"
        entity = QvantumHeatingCurveAdvisorEntity(
            mock_coordinator, "heating_curve_advisor", mock_device, True
        )

        assert entity.native_value is None
        assert entity.available is False
        assert entity.extra_state_attributes is None


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


class TestGetSensorType:
    """Test the _get_sensor_type function."""

    def test_temperature_metrics(self):
        """Test temperature metric classification."""
        # Test various temperature patterns
        assert _get_sensor_type("bt1") == QvantumTemperatureEntity
        assert _get_sensor_type("bt2") == QvantumTemperatureEntity
        assert _get_sensor_type("bp1_temp") == QvantumTemperatureEntity
        assert _get_sensor_type("tap_water_start") == QvantumTemperatureEntity
        assert _get_sensor_type("tap_water_stop") == QvantumTemperatureEntity

    def test_energy_metrics(self):
        """Test energy metric classification (substring 'energy')."""
        assert _get_sensor_type("compressorenergy") == QvantumEnergyEntity
        assert _get_sensor_type("heatingenergy") == QvantumEnergyEntity
        assert _get_sensor_type("dhwenergy") == QvantumEnergyEntity
        assert _get_sensor_type("additionalenergy") == QvantumEnergyEntity
        assert _get_sensor_type("coolingenergy") == QvantumEnergyEntity

    def test_power_metrics(self):
        """Test power metric classification (WATTS)."""
        assert _get_sensor_type("powertotal") == QvantumPowerEntity
        assert _get_sensor_type("heatingpower") == QvantumPowerEntity
        assert _get_sensor_type("dhwpower") == QvantumPowerEntity

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
            coordinator=mock_coordinator, device=mock_device, client=MagicMock())
        return config_entry

    @pytest.fixture
    def mock_entity_registry(self):
        """Mock entity registry."""
        registry = MagicMock()
        return registry

    @pytest.fixture
    def mock_device_registry(self):
        """Mock device registry."""
        registry = MagicMock()
        registry.async_get_device_by_identifier.return_value = None
        return registry

    @pytest.fixture
    def mock_hass(self, mock_entity_registry, mock_device_registry):
        """Mock Home Assistant instance with registries."""
        hass = MagicMock()
        hass.data = {
            "entity_registry": mock_entity_registry,
            "device_registry": mock_device_registry,
        }
        return hass

    @pytest.mark.asyncio
    async def test_async_setup_entry_creates_single_tap_stop_timer(
        self, mock_hass, mock_config_entry, mock_coordinator, mock_device
    ):
        """tap_stop must only be created once as QvantumTimerEntity (not also as a base sensor)."""
        mock_entity_registry = mock_hass.data["entity_registry"]
        mock_entity_registry.async_get.return_value = None
        mock_entity_registry.async_update_entity = MagicMock()

        def mock_async_add_entities(entities):
            for sensor in entities:
                sensor.entity_id = f"sensor.qvantum_{sensor._metric_key}_{sensor._hpid}"

        async_add_entities = MagicMock(side_effect=mock_async_add_entities)

        await async_setup_entry(mock_hass, mock_config_entry, async_add_entities)

        entities = async_add_entities.call_args[0][0]
        tap_stop_entities = [e for e in entities if e._metric_key == "tap_stop"]
        unique_ids = [e._attr_unique_id for e in entities]

        assert len(tap_stop_entities) == 1
        assert isinstance(tap_stop_entities[0], QvantumTimerEntity)
        assert tap_stop_entities[0]._attr_device_class is SensorDeviceClass.TIMESTAMP
        assert unique_ids.count("qvantum_tap_stop_test_device_123") == 1

    @pytest.mark.asyncio
    async def test_async_setup_entry_disables_default_disabled_entities(
        self, mock_hass, mock_config_entry, mock_coordinator, mock_device
    ):
        """Test that entities in DEFAULT_DISABLED_HTTP_METRICS are disabled on first setup."""
        from custom_components.qvantum.const import DEFAULT_DISABLED_HTTP_METRICS

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
    async def test_async_setup_entry_modbus_excludes_http_disabled_metrics(
        self, mock_hass, mock_config_entry, mock_coordinator, mock_device
    ):
        """Test that in Modbus mode HTTP-only disabled metrics are not created, and Modbus disabled metrics are disabled."""
        from custom_components.qvantum.const import (
            CONF_MODBUS_TCP,
            DEFAULT_DISABLED_HTTP_METRICS,
            DEFAULT_DISABLED_MODBUS_METRICS,
        )

        # Metrics in DEFAULT_DISABLED_MODBUS_METRICS that are handled by binary_sensor, not sensor
        binary_sensor_metrics = {
            "dhwdemand",
            "heatingdemand",
            "coolingdemand",
            "additiondemand",
            "additiondhwdemand",
            "time_to_defrost",
            "heatingreleased",
            "coolingreleased",
            "compressorreleased",
            "additionreleased",
            "unit_state",
        }

        # Mark config entry as Modbus mode
        mock_config_entry.options = {CONF_MODBUS_TCP: True}
        mock_coordinator.modbus_enabled = True

        # Remove HTTP disabled metrics from mock data to simulate they are not available in Modbus
        for metric in DEFAULT_DISABLED_HTTP_METRICS:
            mock_coordinator.data["values"].pop(metric, None)

        # Add Modbus disabled metrics to mock data if they should be created
        for metric in DEFAULT_DISABLED_MODBUS_METRICS:
            if metric not in mock_coordinator.data["values"]:
                mock_coordinator.data["values"][metric] = 0  # Dummy value

        # Mock async_add_entities to assign entity_ids
        def mock_async_add_entities(entities):
            for sensor in entities:
                sensor.entity_id = f"sensor.qvantum_{sensor._metric_key}_{sensor._hpid}"

        async_add_entities = MagicMock(side_effect=mock_async_add_entities)

        await async_setup_entry(mock_hass, mock_config_entry, async_add_entities)

        assert async_add_entities.called
        entities = async_add_entities.call_args[0][0]

        disabled_entities = [
            entity
            for entity in entities
            if entity._metric_key in DEFAULT_DISABLED_HTTP_METRICS
        ]

        # Only metrics that are exclusively HTTP-disabled (not also Modbus-disabled)
        # should be absent in Modbus mode. Metrics in both lists (e.g. bt4, bt12)
        # are valid Modbus sensors and must be created (disabled by default).
        http_only_disabled = set(DEFAULT_DISABLED_HTTP_METRICS) - set(
            DEFAULT_DISABLED_MODBUS_METRICS
        )
        disabled_entities = [
            entity for entity in entities if entity._metric_key in http_only_disabled
        ]

        assert disabled_entities == []

        # Demand metrics in DEFAULT_DISABLED_MODBUS_METRICS are binary sensors, not sensor entities,
        # so they won't be disabled via the entity registry in sensor setup.
        expected_call_count = len(
            [
                m
                for m in DEFAULT_DISABLED_MODBUS_METRICS
                if m not in binary_sensor_metrics
            ]
        )
        mock_entity_registry = mock_hass.data["entity_registry"]
        assert (
            mock_entity_registry.async_update_entity.call_count == expected_call_count
        )

    @pytest.mark.asyncio
    async def test_async_setup_entry_modbus_cleanup_drops_cloud_special_sensors(
        self, mock_hass, mock_config_entry, mock_coordinator, mock_device
    ):
        """HTTP-only special sensors must not be kept when cleaning up Modbus mode."""
        from custom_components.qvantum.const import CONF_MODBUS_TCP

        mock_config_entry.options = {CONF_MODBUS_TCP: True}
        mock_coordinator.modbus_enabled = True

        with (
            patch("custom_components.qvantum.entity.disable_entities_by_default"),
            patch(
                "custom_components.qvantum.entity.cleanup_disabled_entities"
            ) as cleanup,
        ):
            await async_setup_entry(mock_hass, mock_config_entry, MagicMock())

        allowed = cleanup.call_args.args[2]
        assert "totalenergy" in allowed
        assert "latency" in allowed
        assert "hpid" in allowed
        assert "tap_stop" in allowed
        assert "display_fw_version" in allowed
        assert "heating_curve_advisor" in allowed
        assert "expiresAt" not in allowed
        assert "firmware_last_check" not in allowed

    @pytest.mark.asyncio
    async def test_async_setup_entry_modbus_creates_display_firmware_sensor(
        self, mock_hass, mock_config_entry, mock_coordinator, mock_device
    ):
        """Modbus mode exposes the display firmware probed from input 191-193."""
        from custom_components.qvantum.const import CONF_MODBUS_TCP

        mock_config_entry.options = {CONF_MODBUS_TCP: True}
        mock_coordinator.modbus_enabled = True

        with (
            patch("custom_components.qvantum.entity.disable_entities_by_default"),
            patch("custom_components.qvantum.entity.cleanup_disabled_entities"),
        ):
            async_add_entities = MagicMock()
            await async_setup_entry(mock_hass, mock_config_entry, async_add_entities)

        entities = async_add_entities.call_args[0][0]
        firmware_entities = [
            entity
            for entity in entities
            if isinstance(entity, QvantumDisplayFirmwareEntity)
        ]

        assert len(firmware_entities) == 1
        entity = firmware_entities[0]
        assert entity._attr_unique_id == "qvantum_display_fw_version_test_device_123"
        assert entity._attr_translation_key == "firmware_display_fw_version"
        assert entity.native_value == "1.3.6"

    @pytest.mark.asyncio
    async def test_async_setup_entry_http_does_not_create_display_firmware_sensor(
        self, mock_hass, mock_config_entry, mock_coordinator, mock_device
    ):
        """Cloud mode keeps its cloud firmware sensors; no Modbus sensor is added."""
        mock_coordinator.modbus_enabled = False

        with (
            patch("custom_components.qvantum.entity.disable_entities_by_default"),
            patch("custom_components.qvantum.entity.cleanup_disabled_entities"),
        ):
            async_add_entities = MagicMock()
            await async_setup_entry(mock_hass, mock_config_entry, async_add_entities)

        entities = async_add_entities.call_args[0][0]
        assert not any(
            isinstance(entity, QvantumDisplayFirmwareEntity) for entity in entities
        )

    @pytest.mark.asyncio
    async def test_async_setup_entry_modbus_creates_heating_curve_advisor(
        self, mock_hass, mock_config_entry, mock_coordinator, mock_device
    ):
        """Modbus mode exposes the derived heating curve advisor once."""
        from custom_components.qvantum.const import CONF_MODBUS_TCP

        mock_config_entry.options = {CONF_MODBUS_TCP: True}
        mock_coordinator.modbus_enabled = True

        with (
            patch("custom_components.qvantum.entity.disable_entities_by_default"),
            patch("custom_components.qvantum.entity.cleanup_disabled_entities"),
        ):
            async_add_entities = MagicMock()
            await async_setup_entry(mock_hass, mock_config_entry, async_add_entities)

        entities = async_add_entities.call_args[0][0]
        advisors = [
            entity
            for entity in entities
            if isinstance(entity, QvantumHeatingCurveAdvisorEntity)
        ]

        assert len(advisors) == 1
        assert (
            advisors[0]._attr_unique_id
            == "qvantum_heating_curve_advisor_test_device_123"
        )
        assert advisors[0]._attr_translation_key == "heating_curve_advisor"

    @pytest.mark.asyncio
    async def test_async_setup_entry_http_does_not_create_heating_curve_advisor(
        self, mock_hass, mock_config_entry, mock_coordinator, mock_device
    ):
        """The advisor is derived from Modbus-only metrics; cloud mode skips it."""
        mock_coordinator.modbus_enabled = False

        with (
            patch("custom_components.qvantum.entity.disable_entities_by_default"),
            patch("custom_components.qvantum.entity.cleanup_disabled_entities"),
        ):
            async_add_entities = MagicMock()
            await async_setup_entry(mock_hass, mock_config_entry, async_add_entities)

        entities = async_add_entities.call_args[0][0]
        assert not any(
            isinstance(entity, QvantumHeatingCurveAdvisorEntity)
            for entity in entities
        )

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

        # Additional verification: ensure the entity's enabled state is preserved
        # The mock entity should still be disabled=False (enabled)
        assert mock_entity.disabled is False
        assert mock_entity.disabled_by is None

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
        from custom_components.qvantum.const import DEFAULT_DISABLED_HTTP_METRICS
        from custom_components.qvantum.sensor import _should_exclude_metric

        # Calculate expected calls for disabled-by-default HTTP metrics that are not
        # excluded by patterns. Setup is hybrid: enabled-by-default metrics are only
        # created when present in values, while disabled-by-default metrics still get
        # registry entries even on first install.
        expected_calls = len(
            [
                metric
                for metric in DEFAULT_DISABLED_HTTP_METRICS
                if not _should_exclude_metric(metric)
            ]
        )

        # Ensure this test runs in HTTP mode (not modbus), since we expect HTTP-disabled metrics to be created.
        mock_coordinator.modbus_enabled = False

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
                should_exclude = _should_exclude_metric(metric_key)
                if metric_key in DEFAULT_DISABLED_HTTP_METRICS and not should_exclude:
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

        # The integration still checks and updates disabled HTTP metrics, so we expect
        # integration-disabled call for each disabled metric that is included.
        assert mock_entity_registry.async_update_entity.call_count == expected_calls


def test_should_exclude_metric_respects_excluded_patterns():
    """Test that metrics matching excluded patterns are excluded."""
    from custom_components.qvantum.sensor import _should_exclude_metric

    assert _should_exclude_metric("op_man_dhw") is True
    assert _should_exclude_metric("smart_dhw_mode") is True
    assert _should_exclude_metric("picpin_relay_gp10") is True
    assert _should_exclude_metric("vacation_mode") is True
    assert _should_exclude_metric("heatingreleased") is True
    assert _should_exclude_metric("compressor_blocked") is True
    assert _should_exclude_metric("compressor_blocked_sec") is False
    assert _should_exclude_metric("some_other_metric") is False


def test_default_metric_creates_entity_for_binary_sensors_not_switches():
    """Relay bits become binary sensors; switch-style excluded patterns do not."""
    from custom_components.qvantum.const import default_metric_creates_entity

    assert default_metric_creates_entity("picpin_relay_gp10") is True
    assert default_metric_creates_entity("picpin_relay_pump") is True
    assert default_metric_creates_entity("bt1") is True
    assert default_metric_creates_entity("op_man_dhw") is False
    assert default_metric_creates_entity("use_adaptive") is False


class TestQvantumDisplayFirmwareEntity:
    """Test the Modbus display firmware sensor (input registers 191-193)."""

    def test_init(self, mock_coordinator, mock_device):
        """Test entity initialization, unique id, and translation key."""
        entity = QvantumDisplayFirmwareEntity(
            mock_coordinator, "display_fw_version", mock_device, True
        )

        assert entity._attr_entity_category.name == "DIAGNOSTIC"
        assert entity._attr_unique_id == "qvantum_display_fw_version_test_device_123"
        assert entity._attr_translation_key == "firmware_display_fw_version"
        assert entity._attr_entity_registry_enabled_default is True

    def test_state_from_device_sw_version(self, mock_coordinator, mock_device):
        """The probed sw_version wins over device metadata."""
        mock_coordinator.data["device"]["sw_version"] = "1.7.22"

        entity = QvantumDisplayFirmwareEntity(
            mock_coordinator, "display_fw_version", mock_device, True
        )

        assert entity.native_value == "1.7.22"

    def test_state_falls_back_to_device_metadata(self, mock_coordinator, mock_device):
        """The registry-recovery path only has device_metadata."""
        entity = QvantumDisplayFirmwareEntity(
            mock_coordinator, "display_fw_version", mock_device, True
        )

        assert entity.native_value == "1.3.6"

    def test_tracks_device_updates(self, mock_coordinator, mock_device):
        """A refreshed version is reflected without recreating the entity."""
        entity = QvantumDisplayFirmwareEntity(
            mock_coordinator, "display_fw_version", mock_device, True
        )

        mock_coordinator.data["device"]["sw_version"] = "1.7.23"

        assert entity.native_value == "1.7.23"

    def test_unavailable_without_version(self, mock_coordinator, mock_device):
        """No sw_version and no metadata makes the sensor unavailable."""
        mock_coordinator.data["device"] = {"id": "test_device_123"}

        entity = QvantumDisplayFirmwareEntity(
            mock_coordinator, "display_fw_version", mock_device, True
        )

        assert entity.native_value is None
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

        assert entity.native_value == "1.3.6"

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
        assert entity.native_value == "1.3.6"  # From device metadata in main coordinator

    def test_state_none_when_no_data(self, mock_firmware_coordinator, mock_device):
        """Test firmware version returns None when no data available."""
        mock_firmware_coordinator.data = {}
        mock_firmware_coordinator.main_coordinator.data = {}

        entity = QvantumFirmwareSensorEntity(
            mock_firmware_coordinator, "display_fw_version", mock_device, True
        )

        assert entity.native_value is None


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

        state = entity.native_value
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
        assert state.tzinfo is not None
        assert state.tzinfo == timezone.utc

    def test_state_none_when_no_data(self, mock_firmware_coordinator, mock_device):
        """Test last check returns None when no data available."""
        mock_firmware_coordinator.data = {}

        entity = QvantumFirmwareLastCheckSensorEntity(
            mock_firmware_coordinator, "firmware_last_check", mock_device, True
        )

        assert entity.native_value is None


class TestQvantumAccessExpireEntity:
    """Test the QvantumAccessExpireEntity class."""

    def test_init(self, mock_firmware_coordinator, mock_device):
        """Test access expire sensor entity initialization."""
        entity = QvantumAccessExpireEntity(
            mock_firmware_coordinator, "expiresAt", mock_device, True
        )

        assert entity._attr_entity_category.name == "DIAGNOSTIC"
        assert entity._attr_device_class == "timestamp"
        assert entity._metric_key == "expiresAt"
        assert entity._attr_translation_key == "expires_at"

    def test_state_with_valid_data(self, mock_firmware_coordinator, mock_device):
        """Test access expiration timestamp parsing with valid data."""
        entity = QvantumAccessExpireEntity(
            mock_firmware_coordinator, "expiresAt", mock_device, True
        )

        state = entity.native_value
        assert isinstance(state, datetime)
        assert state.year == 2026
        assert state.month == 1
        assert state.day == 26
        assert state.hour == 18
        assert state.minute == 35
        assert state.second == 29

    def test_state_with_none_data(self, mock_firmware_coordinator, mock_device):
        """Test access expiration returns None when no data available."""
        mock_firmware_coordinator.data = {}

        entity = QvantumAccessExpireEntity(
            mock_firmware_coordinator, "expiresAt", mock_device, True
        )

        assert entity.native_value is None

    def test_state_with_missing_access_level(
        self, mock_firmware_coordinator, mock_device
    ):
        """Test access expiration returns None when access_level is missing."""
        mock_firmware_coordinator.data = {"firmware_versions": {}}

        entity = QvantumAccessExpireEntity(
            mock_firmware_coordinator, "expiresAt", mock_device, True
        )

        assert entity.native_value is None

    def test_state_with_missing_expires_at(
        self, mock_firmware_coordinator, mock_device
    ):
        """Test access expiration returns None when expiresAt key is missing."""
        mock_firmware_coordinator.data = {
            "access_level": {
                "readAccessLevel": 20,
                "writeAccessLevel": 20,
            }
        }

        entity = QvantumAccessExpireEntity(
            mock_firmware_coordinator, "expiresAt", mock_device, True
        )

        assert entity.native_value is None

    def test_available_with_data(self, mock_firmware_coordinator, mock_device):
        """Test entity availability when data is present."""
        entity = QvantumAccessExpireEntity(
            mock_firmware_coordinator, "expiresAt", mock_device, True
        )

        assert entity.available is True

    def test_available_without_data(self, mock_firmware_coordinator, mock_device):
        """Test entity availability when no data is present."""
        mock_firmware_coordinator.data = {}

        entity = QvantumAccessExpireEntity(
            mock_firmware_coordinator, "expiresAt", mock_device, True
        )

        assert entity.available is False

    def test_available_without_access_level(
        self, mock_firmware_coordinator, mock_device
    ):
        """Test entity availability when access_level is missing."""
        mock_firmware_coordinator.data = {"firmware_versions": {}}

        entity = QvantumAccessExpireEntity(
            mock_firmware_coordinator, "expiresAt", mock_device, True
        )

        assert entity.available is False

    def test_available_without_expires_at(self, mock_firmware_coordinator, mock_device):
        """Test entity availability when expiresAt key is missing."""
        mock_firmware_coordinator.data = {
            "access_level": {
                "readAccessLevel": 20,
                "writeAccessLevel": 20,
            }
        }

        entity = QvantumAccessExpireEntity(
            mock_firmware_coordinator, "expiresAt", mock_device, True
        )

        assert entity.available is False