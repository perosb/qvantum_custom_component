"""Tests for Qvantum climate entities."""

from enum import IntFlag
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


# Create mock base classes that don't have metaclass conflicts
class MockCoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator


class MockClimateEntity:
    pass


# Mock ClimateEntityFeature. An IntFlag so ClimateEntityFeature(0) is
# constructible, matching the real Home Assistant class.
class MockClimateEntityFeature(IntFlag):
    TARGET_TEMPERATURE = 1


# Mock HVACMode and HVACAction
class MockHVACMode:
    HEAT = "heat"


class MockHVACAction:
    HEATING = "heating"
    IDLE = "idle"
    DEFROSTING = "defrosting"
    COOLING = "cooling"


# Mock UnitOfTemperature
class MockUnitOfTemperature:
    CELSIUS = "°C"


# Patch the imports before importing the climate module. The climate module
# imports these names from ``homeassistant.components.climate.const``, so the
# patches must target that module rather than the ``climate`` package
# re-exports, which the module never reads.
with patch(
    "homeassistant.helpers.update_coordinator.CoordinatorEntity", MockCoordinatorEntity
):
    with patch("homeassistant.components.climate.ClimateEntity", MockClimateEntity):
        with patch(
            "homeassistant.components.climate.const.ClimateEntityFeature",
            MockClimateEntityFeature,
        ):
            with patch(
                "homeassistant.components.climate.const.HVACMode", MockHVACMode
            ):
                with patch(
                    "homeassistant.components.climate.const.HVACAction", MockHVACAction
                ):
                    with patch(
                        "homeassistant.const.UnitOfTemperature", MockUnitOfTemperature
                    ):
                        with patch(
                            "custom_components.qvantum.const.SETTING_UPDATE_APPLIED", "APPLIED"
                        ):
                            from homeassistant.const import PRECISION_TENTHS
                            from homeassistant.helpers.device_registry import DeviceInfo

                            from custom_components.qvantum.climate import (
                                QvantumIndoorClimateEntity,
                            )
                            from custom_components.qvantum.const import (
                                SENSOR_MODE_HTTP_BT2,
                                SENSOR_MODE_HTTP_EXT_ROOM_SENSOR,
                                SETTING_UPDATE_APPLIED,
                                SensorMode,
                            )


@pytest.fixture
def mock_coordinator():
    """Create a mock coordinator with test data."""
    coordinator = MagicMock()
    coordinator.data = {
        "device": {"id": "test_device_123"},
        "values": {
            "hpid": "test_device_123",
            "bt2": 22.5,  # Current temperature
            "hp_status": 3,  # Heating status
            "indoor_temperature_target": 21.0,
            "sensor_mode": SENSOR_MODE_HTTP_BT2,
        },
    }
    coordinator.client = MagicMock()
    coordinator.async_set_updated_data = MagicMock()
    coordinator.async_refresh = AsyncMock()

    # Mock config_entry and runtime_data for access level check
    config_entry = MagicMock()
    maintenance_coordinator = MagicMock()
    maintenance_coordinator.data = {"access_level": {"writeAccessLevel": 20}}
    config_entry.runtime_data.maintenance_coordinator = maintenance_coordinator
    coordinator.config_entry = config_entry

    return coordinator


@pytest.fixture
def mock_device():
    """Create a mock device info."""
    return DeviceInfo(
        identifiers={("qvantum", "qvantum-test_device_123")},
        manufacturer="Qvantum",
        model="QE-6",
    )


class TestSensorMode:
    """Test Modbus SensorMode enum and HTTP name mapping."""

    def test_modbus_values(self):
        """Modbus holding 9 uses these integer values."""
        assert SensorMode.DISABLED == 0
        assert SensorMode.BT2 == 1
        assert SensorMode.BT3 == 2
        assert SensorMode.AUX == 3
        assert SensorMode.EXTERNAL == 4

    @pytest.mark.parametrize(
        "value",
        [
            SENSOR_MODE_HTTP_BT2,
            SENSOR_MODE_HTTP_EXT_ROOM_SENSOR,
            SensorMode.BT2,
            SensorMode.EXTERNAL,
            1,
            4,
        ],
    )
    def test_allows_target_temperature(self, value):
        assert SensorMode.allows_target_temperature(value) is True

    @pytest.mark.parametrize(
        "value",
        [None, "other", True, False, SensorMode.DISABLED, SensorMode.BT3, SensorMode.AUX, 0, 2, 3],
    )
    def test_disallows_target_temperature(self, value):
        assert SensorMode.allows_target_temperature(value) is False

    @pytest.mark.parametrize(
        ("value", "keys"),
        [
            (None, ("bt2",)),
            (SENSOR_MODE_HTTP_BT2, ("bt2",)),
            (SensorMode.BT2, ("bt2",)),
            (1, ("bt2",)),
            (
                SENSOR_MODE_HTTP_EXT_ROOM_SENSOR,
                ("room_temp_external", "room_temp_ext"),
            ),
            (SensorMode.EXTERNAL, ("room_temp_external", "room_temp_ext")),
            (4, ("room_temp_external", "room_temp_ext")),
            (SensorMode.DISABLED, ()),
            (SensorMode.BT3, ()),
            (SensorMode.AUX, ()),
            (True, ()),
            (False, ()),
        ],
    )
    def test_current_temperature_keys(self, value, keys):
        assert SensorMode.current_temperature_keys(value) == keys


class TestQvantumIndoorClimateEntity:
    """Test the QvantumIndoorClimateEntity class."""

    def test_init(self, mock_coordinator, mock_device):
        """Test climate entity initialization."""
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)

        assert entity._hpid == "test_device_123"
        assert entity._attr_unique_id == "qvantum_indoor_climate_test_device_123"
        assert entity._attr_temperature_unit == "°C"
        assert entity._attr_target_temperature_step == PRECISION_TENTHS
        assert entity._attr_device_info == mock_device
        assert entity._attr_translation_key == "indoor_climate"
        assert entity._attr_has_entity_name is True

    def test_current_temperature(self, mock_coordinator, mock_device):
        """Test getting current temperature."""
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.current_temperature == 22.5

    def test_current_temperature_uses_bt2_for_bt2_mode(
        self, mock_coordinator, mock_device
    ):
        """BT2 mode reads bt2 even when an external reading is also present."""
        mock_coordinator.data["values"]["sensor_mode"] = SENSOR_MODE_HTTP_BT2
        mock_coordinator.data["values"]["bt2"] = 22.5
        mock_coordinator.data["values"]["room_temp_ext"] = 19.0
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.current_temperature == 22.5

    def test_current_temperature_uses_http_external_sensor(
        self, mock_coordinator, mock_device
    ):
        """HTTP ext_room_sensor mode reads room_temp_ext, not bt2."""
        mock_coordinator.data["values"]["sensor_mode"] = (
            SENSOR_MODE_HTTP_EXT_ROOM_SENSOR
        )
        mock_coordinator.data["values"]["bt2"] = 22.5
        mock_coordinator.data["values"]["room_temp_ext"] = 19.4
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.current_temperature == 19.4

    def test_current_temperature_uses_modbus_external_sensor(
        self, mock_coordinator, mock_device
    ):
        """Modbus EXTERNAL mode reads room_temp_external, not bt2."""
        mock_coordinator.data["values"]["sensor_mode"] = SensorMode.EXTERNAL
        mock_coordinator.data["values"]["bt2"] = 22.5
        mock_coordinator.data["values"]["room_temp_external"] = 18.7
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.current_temperature == 18.7

    def test_falls_back_to_use_operation_sensor(self, mock_coordinator, mock_device):
        """If sensor_mode is missing, use_operation_sensor selects reading and features."""
        del mock_coordinator.data["values"]["sensor_mode"]
        mock_coordinator.data["values"]["use_operation_sensor"] = SensorMode.EXTERNAL
        mock_coordinator.data["values"]["bt2"] = 22.5
        mock_coordinator.data["values"]["room_temp_external"] = 18.1
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.current_temperature == 18.1
        assert entity.supported_features == 1  # TARGET_TEMPERATURE

    def test_target_temperature(self, mock_coordinator, mock_device):
        """Test getting target temperature."""
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.target_temperature == 21.0

    def test_hvac_mode(self, mock_coordinator, mock_device):
        """Test HVAC mode."""
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.hvac_mode == "heat"

    def test_hvac_modes(self, mock_coordinator, mock_device):
        """Test available HVAC modes."""
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.hvac_modes == ["heat"]

    def test_hvac_action_heating(self, mock_coordinator, mock_device):
        """Test HVAC action when heating."""
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.hvac_action == "heating"  # hp_status = 3

    def test_hvac_action_idle(self, mock_coordinator, mock_device):
        """Test HVAC action when idle."""
        mock_coordinator.data["values"]["hp_status"] = 0
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.hvac_action == "idle"

    def test_hvac_action_defrosting(self, mock_coordinator, mock_device):
        """Test HVAC action when defrosting."""
        mock_coordinator.data["values"]["hp_status"] = 1
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.hvac_action == "defrosting"

    def test_hvac_action_cooling(self, mock_coordinator, mock_device):
        """Test HVAC action when cooling."""
        mock_coordinator.data["values"]["hp_status"] = 4
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.hvac_action == "cooling"

    def test_hvac_action_hot_water_is_idle(self, mock_coordinator, mock_device):
        """Hot water production does not heat the room; report idle."""
        mock_coordinator.data["values"]["hp_status"] = 2
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.hvac_action == "idle"

    def test_hvac_action_unknown(self, mock_coordinator, mock_device):
        """Test HVAC action for unknown status."""
        mock_coordinator.data["values"]["hp_status"] = 99
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.hvac_action == "idle"  # Default to idle

    @pytest.mark.parametrize(
        "sensor_mode",
        [
            SENSOR_MODE_HTTP_BT2,
            SENSOR_MODE_HTTP_EXT_ROOM_SENSOR,
            SensorMode.BT2,
            SensorMode.EXTERNAL,
            SensorMode.BT2.value,
            SensorMode.EXTERNAL.value,
        ],
    )
    def test_supported_features_with_indoor_sensor(
        self, mock_coordinator, mock_device, sensor_mode
    ):
        """Target temperature is offered for BT2 and external room sensor."""
        mock_coordinator.data["values"]["sensor_mode"] = sensor_mode
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.supported_features == 1  # TARGET_TEMPERATURE

    @pytest.mark.parametrize(
        "sensor_mode",
        [
            "other",
            None,
            SensorMode.DISABLED,
            SensorMode.BT3,
            SensorMode.AUX,
            SensorMode.DISABLED.value,
            SensorMode.BT3.value,
            SensorMode.AUX.value,
        ],
    )
    def test_supported_features_without_indoor_sensor(
        self, mock_coordinator, mock_device, sensor_mode
    ):
        """Target temperature is hidden when no indoor room sensor is in use."""
        mock_coordinator.data["values"]["sensor_mode"] = sensor_mode
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        features = entity.supported_features
        # Must be a zero-valued ClimateEntityFeature flag, not an empty dict:
        # HA's climate service does bitwise checks on supported_features.
        assert features == 0
        assert not isinstance(features, dict)

    def test_available_true(self, mock_coordinator, mock_device):
        """Test entity availability when bt2 data exists."""
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.available is True

    def test_available_false_no_bt2(self, mock_coordinator, mock_device):
        """Test entity availability when bt2 key is missing."""
        del mock_coordinator.data["values"]["bt2"]
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.available is False

    def test_available_false_bt2_none(self, mock_coordinator, mock_device):
        """Test entity availability when bt2 is None."""
        mock_coordinator.data["values"]["bt2"] = None
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.available is False

    def test_available_true_external_without_bt2(
        self, mock_coordinator, mock_device
    ):
        """External mode is available from the external reading, even without bt2."""
        mock_coordinator.data["values"]["sensor_mode"] = SensorMode.EXTERNAL
        del mock_coordinator.data["values"]["bt2"]
        mock_coordinator.data["values"]["room_temp_external"] = 19.0
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.available is True

    def test_available_false_external_without_room_temp(
        self, mock_coordinator, mock_device
    ):
        """External mode is unavailable when the external reading is missing."""
        mock_coordinator.data["values"]["sensor_mode"] = (
            SENSOR_MODE_HTTP_EXT_ROOM_SENSOR
        )
        mock_coordinator.data["values"]["bt2"] = 22.5
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.available is False
        assert entity.current_temperature is None

    @pytest.mark.asyncio
    async def test_async_set_temperature(self, mock_coordinator, mock_device):
        """Test setting target temperature."""
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)

        # Mock the API response
        mock_coordinator.client.set_indoor_temperature_target = AsyncMock(
            return_value={"status": "APPLIED"}
        )

        await entity.async_set_temperature(temperature=23.5)

        mock_coordinator.client.set_indoor_temperature_target.assert_called_once_with(
            "test_device_123", 23.5
        )
        # Check that async_set_updated_data was called (indicating successful update)
        mock_coordinator.async_set_updated_data.assert_called_once_with(
            mock_coordinator.data
        )
        # The data should have been updated
        assert mock_coordinator.data["values"]["indoor_temperature_target"] == 23.5

    @pytest.mark.asyncio
    async def test_async_set_hvac_mode(self, mock_coordinator, mock_device):
        """The heat-only mode is accepted as an intentional no-op."""
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)

        await entity.async_set_hvac_mode("heat")


def _spec_coordinator(write_level: int):
    """Coordinator mock that passes the ``isinstance`` write-access check."""
    from custom_components.qvantum.coordinator import QvantumDataUpdateCoordinator

    coordinator = MagicMock(spec=QvantumDataUpdateCoordinator)
    coordinator.data = {
        "device": {"id": "test_device_123"},
        "values": {
            "hpid": "test_device_123",
            "bt2": 22.5,
            "indoor_temperature_target": 21.0,
            "sensor_mode": SENSOR_MODE_HTTP_BT2,
        },
    }
    coordinator.modbus_enabled = False
    coordinator.last_update_success = True
    coordinator.client = MagicMock()
    coordinator.config_entry = MagicMock()
    maintenance = MagicMock()
    maintenance.data = {"access_level": {"writeAccessLevel": write_level}}
    coordinator.config_entry.runtime_data.maintenance_coordinator = maintenance
    return coordinator


class TestWriteAccessBehaviour:
    """Readability is independent of write access; writes enforce it."""

    def test_available_is_false_when_coordinator_failed(self, mock_coordinator, mock_device):
        """A failed poll makes the entity unavailable even with data cached."""
        mock_coordinator.last_update_success = False
        entity = QvantumIndoorClimateEntity(mock_coordinator, mock_device)
        assert entity.available is False

    def test_available_without_write_access(self, mock_device):
        """A read-only account can still see the indoor temperature."""
        coordinator = _spec_coordinator(write_level=10)
        entity = QvantumIndoorClimateEntity(coordinator, mock_device)

        assert entity._has_write_access is False
        assert entity.available is True

    @pytest.mark.asyncio
    async def test_set_temperature_requires_write_access(self, mock_device):
        """set_temperature raises instead of writing for a read-only account."""
        from homeassistant.exceptions import HomeAssistantError

        coordinator = _spec_coordinator(write_level=10)
        entity = QvantumIndoorClimateEntity(coordinator, mock_device)

        with pytest.raises(HomeAssistantError, match="Write access is not enabled"):
            await entity.async_set_temperature(temperature=23.0)

        coordinator.client.set_indoor_temperature_target.assert_not_called()

def _modbus_no_write_coordinator():
    """Modbus coordinator whose entry has Modbus writes disabled."""
    from custom_components.qvantum.coordinator import QvantumDataUpdateCoordinator

    coordinator = MagicMock(spec=QvantumDataUpdateCoordinator)
    coordinator.data = {
        "values": {
            "hpid": "test_device_123",
            "bt2": 22.5,
            "indoor_temperature_target": 21.0,
            "sensor_mode": SENSOR_MODE_HTTP_BT2,
        },
    }
    coordinator.modbus_enabled = True
    coordinator.last_update_success = True
    coordinator.client = MagicMock()
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.options = {}
    coordinator.config_entry.data = {}
    return coordinator


class TestWriteFeatureGating:
    """Controls are hidden without write access; the reading stays available."""

    def test_setpoint_hidden_without_write_access(self, mock_device):
        coordinator = _modbus_no_write_coordinator()
        entity = QvantumIndoorClimateEntity(coordinator, mock_device)

        assert entity._has_write_access is False
        assert entity.available is True
        assert entity.supported_features == 0

    def test_setpoint_shown_with_write_access(self, mock_device):
        coordinator = _modbus_no_write_coordinator()
        coordinator.config_entry.options = {
            "modbus_write": True,
            "modbus_tcp": True,
        }
        entity = QvantumIndoorClimateEntity(coordinator, mock_device)

        assert entity._has_write_access is True
        assert entity.supported_features == 1
