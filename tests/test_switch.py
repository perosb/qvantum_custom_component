"""Tests for Qvantum switch entities."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest


# Create mock base classes that don't have metaclass conflicts
class MockCoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator

    async def async_added_to_hass(self):
        return None

    def async_on_remove(self, func):
        self._on_remove = func


class MockSwitchEntity:
    def async_write_ha_state(self):
        pass


# Mock SwitchDeviceClass
class MockSwitchDeviceClass:
    SWITCH = "switch"


# Mock STATE_ON and STATE_OFF
STATE_ON = "on"
STATE_OFF = "off"


# Patch the imports before importing the switch module
with patch(
    "homeassistant.helpers.update_coordinator.CoordinatorEntity", MockCoordinatorEntity
):
    with patch("homeassistant.components.switch.SwitchEntity", MockSwitchEntity):
        with patch(
            "homeassistant.components.switch.SwitchDeviceClass", MockSwitchDeviceClass
        ):
            with patch("homeassistant.const.STATE_ON", STATE_ON):
                with patch("homeassistant.const.STATE_OFF", STATE_OFF):
                    with patch(
                        "custom_components.qvantum.const.SETTING_UPDATE_APPLIED",
                        "APPLIED",
                    ):
                        from homeassistant.helpers.device_registry import DeviceInfo

                        from custom_components.qvantum.switch import (
                            QvantumCurveControlSwitch,
                            QvantumDataUpdateCoordinator,
                            QvantumSwitchEntity,
                        )


@pytest.fixture
def mock_coordinator():
    """Create a mock coordinator with test data."""
    coordinator = MagicMock(spec=QvantumDataUpdateCoordinator)
    coordinator.modbus_enabled = False
    coordinator.data = {
        "device": {"id": "test_device_123"},
        "values": {
            "hpid": "test_device_123",
            "extra_tap_water": None,  # Will be set based on test
            "op_mode": 1,
            "op_man_dhw": 1,
            "op_man_addition": 0,
            "man_mode": 0,
            "use_adaptive": True,  # For enable_sc_* availability tests
            "enable_sc_dhw": True,  # For enable_sc_dhw tests
            "enable_sc_sh": True,  # For enable_sc_sh tests
            "smart_price_dhw_enabled": True,
            "smart_price_heating_enabled": True,
        },
        "metrics": {
            "smart_price_dhw_enabled": True,
            "smart_price_heating_enabled": True,
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


class TestQvantumSwitchEntity:
    """Test the QvantumSwitchEntity class."""

    def test_init(self, mock_coordinator, mock_device):
        """Test switch entity initialization."""
        entity = QvantumSwitchEntity(mock_coordinator, "extra_tap_water", mock_device)

        assert entity._hpid == "test_device_123"
        assert entity._metric_key == "extra_tap_water"
        assert entity._attr_unique_id == "qvantum_extra_tap_water_test_device_123"
        assert entity._attr_device_info == mock_device
        assert entity._attr_device_class == "switch"
        assert entity._attr_has_entity_name is True
        assert entity._attr_icon == "mdi:water-boiler"
        assert entity._attr_translation_key == "extra_tap_water"

    def test_init_op_mode_icon(self, mock_coordinator, mock_device):
        """Test switch entity initialization with op_mode icon."""
        entity = QvantumSwitchEntity(mock_coordinator, "op_mode", mock_device)
        assert entity._attr_icon == "mdi:auto-mode"

    def test_init_op_man_dhw_icon(self, mock_coordinator, mock_device):
        """Test switch entity initialization with op_man_dhw icon."""
        entity = QvantumSwitchEntity(mock_coordinator, "op_man_dhw", mock_device)
        assert entity._attr_icon == "mdi:water-outline"

    def test_init_op_man_addition_icon(self, mock_coordinator, mock_device):
        """Test switch entity initialization with op_man_addition icon."""
        entity = QvantumSwitchEntity(mock_coordinator, "op_man_addition", mock_device)
        assert entity._attr_icon == "mdi:transmission-tower-import"

    def test_init_vacation_mode_icon(self, mock_coordinator, mock_device):
        """Test switch entity initialization with vacation_mode icon."""
        entity = QvantumSwitchEntity(mock_coordinator, "vacation_mode", mock_device)
        assert entity._attr_icon == "mdi:palm-tree"

    def test_init_default_icon(self, mock_coordinator, mock_device):
        """Test switch entity initialization with unknown metric has no icon."""
        entity = QvantumSwitchEntity(mock_coordinator, "unknown_metric", mock_device)
        assert entity._attr_icon is None

    def test_is_on_extra_tap_water_off(self, mock_coordinator, mock_device):
        """Test is_on when extra_tap_water is 'off'."""
        mock_coordinator.data["values"]["extra_tap_water"] = "off"
        entity = QvantumSwitchEntity(mock_coordinator, "extra_tap_water", mock_device)
        assert entity.is_on is False

    def test_is_on_extra_tap_water_on(self, mock_coordinator, mock_device):
        """Test is_on when extra_tap_water is 'on'."""
        mock_coordinator.data["values"]["extra_tap_water"] = "on"
        entity = QvantumSwitchEntity(mock_coordinator, "extra_tap_water", mock_device)
        assert entity.is_on is True

    def test_is_on_extra_tap_water_none(self, mock_coordinator, mock_device):
        """Test is_on when extra_tap_water is None."""
        mock_coordinator.data["values"]["extra_tap_water"] = None
        entity = QvantumSwitchEntity(mock_coordinator, "extra_tap_water", mock_device)
        assert entity.is_on is False

    def test_is_on_other_metric_on(self, mock_coordinator, mock_device):
        """Test is_on for other metrics when set to 1."""
        entity = QvantumSwitchEntity(mock_coordinator, "other_switch", mock_device)
        mock_coordinator.data["values"]["other_switch"] = 1
        assert entity.is_on is True

    def test_is_on_other_metric_off(self, mock_coordinator, mock_device):
        """Test is_on for other metrics when set to 0."""
        entity = QvantumSwitchEntity(mock_coordinator, "other_switch", mock_device)
        mock_coordinator.data["values"]["other_switch"] = 0
        assert entity.is_on is False

    def test_available_true(self, mock_coordinator, mock_device):
        """Test entity availability when data exists."""
        mock_coordinator.data["values"]["extra_tap_water"] = "on"
        entity = QvantumSwitchEntity(mock_coordinator, "extra_tap_water", mock_device)
        assert entity.available is True

    def test_available_false_missing_key(self, mock_coordinator, mock_device):
        """Test entity availability when key is missing."""
        entity = QvantumSwitchEntity(mock_coordinator, "missing_switch", mock_device)
        assert entity.available is False

    def test_available_false_none_value(self, mock_coordinator, mock_device):
        """Test entity availability when value is None."""
        mock_coordinator.data["values"]["extra_tap_water"] = None
        entity = QvantumSwitchEntity(mock_coordinator, "extra_tap_water", mock_device)
        assert entity.available is False

    def test_available_extra_tap_water_with_data(self, mock_coordinator, mock_device):
        """Test availability for extra_tap_water when data exists."""
        mock_coordinator.data["values"]["extra_tap_water"] = "on"
        entity = QvantumSwitchEntity(mock_coordinator, "extra_tap_water", mock_device)
        assert entity.available is True

    def test_available_extra_tap_water_without_stop_data(
        self, mock_coordinator, mock_device
    ):
        """Test availability for extra_tap_water when stop data is missing."""
        # Remove extra_tap_water from values
        if "extra_tap_water" in mock_coordinator.data["values"]:
            del mock_coordinator.data["values"]["extra_tap_water"]
        entity = QvantumSwitchEntity(mock_coordinator, "extra_tap_water", mock_device)
        assert entity.available is False

    def test_available_extra_tap_water_none(self, mock_coordinator, mock_device):
        """Test availability for extra_tap_water when data is None."""
        mock_coordinator.data["values"]["extra_tap_water"] = None
        entity = QvantumSwitchEntity(mock_coordinator, "extra_tap_water", mock_device)
        assert entity.available is False

    def test_available_other_switch_with_data(self, mock_coordinator, mock_device):
        """Test availability for other switches when data exists."""
        mock_coordinator.data["values"]["op_mode"] = 1
        entity = QvantumSwitchEntity(mock_coordinator, "op_mode", mock_device)
        assert entity.available is True

    def test_available_other_switch_without_data(self, mock_coordinator, mock_device):
        """Test availability for other switches when data is missing."""
        # Remove op_mode from data to test availability
        mock_coordinator.data["values"].pop("op_mode", None)
        entity = QvantumSwitchEntity(mock_coordinator, "op_mode", mock_device)
        assert entity.available is False

    def test_available_other_switch_none_value(self, mock_coordinator, mock_device):
        """Test availability for other switches when value is None."""
        mock_coordinator.data["values"]["op_mode"] = None
        entity = QvantumSwitchEntity(mock_coordinator, "op_mode", mock_device)
        assert entity.available is False

    def test_available_enable_sc_dhw_available(self, mock_coordinator, mock_device):
        """Test availability for enable_sc_dhw when metric exists and use_adaptive is True."""
        entity = QvantumSwitchEntity(mock_coordinator, "enable_sc_dhw", mock_device)
        assert entity.available is True

    def test_available_enable_sc_dhw_unavailable_use_adaptive_false(
        self, mock_coordinator, mock_device
    ):
        """Test availability for enable_sc_dhw when use_adaptive is False."""
        mock_coordinator.data["values"]["use_adaptive"] = False
        entity = QvantumSwitchEntity(mock_coordinator, "enable_sc_dhw", mock_device)
        assert entity.available is False

    def test_available_enable_sc_sh_available(self, mock_coordinator, mock_device):
        """Test availability for enable_sc_sh when metric exists and use_adaptive is True."""
        entity = QvantumSwitchEntity(mock_coordinator, "enable_sc_sh", mock_device)
        assert entity.available is True

    def test_available_enable_sc_sh_unavailable_use_adaptive_false(
        self, mock_coordinator, mock_device
    ):
        """Test availability for enable_sc_sh when use_adaptive is False."""
        mock_coordinator.data["values"]["use_adaptive"] = False
        entity = QvantumSwitchEntity(mock_coordinator, "enable_sc_sh", mock_device)
        assert entity.available is False

    def test_available_op_man_addition_available(self, mock_coordinator, mock_device):
        """Test availability for op_man_addition when op_mode is 1."""
        mock_coordinator.data["values"]["op_man_addition"] = 0
        mock_coordinator.data["values"]["op_mode"] = 1
        entity = QvantumSwitchEntity(mock_coordinator, "op_man_addition", mock_device)
        assert entity.available is True

    def test_available_op_man_addition_unavailable_wrong_op_mode(
        self, mock_coordinator, mock_device
    ):
        """Test availability for op_man_addition when op_mode is not 1."""
        mock_coordinator.data["values"]["op_man_addition"] = 0
        mock_coordinator.data["values"]["op_mode"] = 0
        entity = QvantumSwitchEntity(mock_coordinator, "op_man_addition", mock_device)
        assert entity.available is False

    def test_available_op_man_addition_unavailable_missing_metric(
        self, mock_coordinator, mock_device
    ):
        """Test availability for op_man_addition when metric is missing."""
        mock_coordinator.data["values"]["op_mode"] = 1
        # Remove op_man_addition from data
        mock_coordinator.data["values"].pop("op_man_addition", None)
        entity = QvantumSwitchEntity(mock_coordinator, "op_man_addition", mock_device)
        assert entity.available is False

    def test_available_op_man_dhw_available(self, mock_coordinator, mock_device):
        """Test availability for op_man_dhw when op_mode is 1."""
        mock_coordinator.data["values"]["op_man_dhw"] = 0
        mock_coordinator.data["values"]["op_mode"] = 1
        entity = QvantumSwitchEntity(mock_coordinator, "op_man_dhw", mock_device)
        assert entity.available is True

    def test_available_op_man_dhw_unavailable_wrong_op_mode(
        self, mock_coordinator, mock_device
    ):
        """Test availability for op_man_dhw when op_mode is not 1."""
        mock_coordinator.data["values"]["op_man_dhw"] = 0
        mock_coordinator.data["values"]["op_mode"] = 0
        entity = QvantumSwitchEntity(mock_coordinator, "op_man_dhw", mock_device)
        assert entity.available is False

    @pytest.mark.asyncio
    async def test_async_turn_on_extra_tap_water(self, mock_coordinator, mock_device):
        """Test turning on extra tap water."""
        entity = QvantumSwitchEntity(mock_coordinator, "extra_tap_water", mock_device)

        # Mock the API response
        mock_coordinator.async_set_extra_tap_water = AsyncMock(
            return_value={"status": "APPLIED"}
        )

        await entity.async_turn_on()

        mock_coordinator.async_set_extra_tap_water.assert_called_once_with(
            "test_device_123", -1
        )
        # Data is updated after successful API response
        mock_coordinator.async_set_updated_data.assert_called_once()
        updated_data = mock_coordinator.async_set_updated_data.call_args[0][0]
        assert updated_data["values"]["extra_tap_water"] == "on"
        # No immediate refresh to avoid overwriting the update

    @pytest.mark.asyncio
    async def test_async_turn_off_extra_tap_water(self, mock_coordinator, mock_device):
        """Test turning off extra tap water."""
        entity = QvantumSwitchEntity(mock_coordinator, "extra_tap_water", mock_device)

        # Mock the API response
        mock_coordinator.async_set_extra_tap_water = AsyncMock(
            return_value={"status": "APPLIED"}
        )
        mock_coordinator.extra_dhw = MagicMock()
        mock_coordinator.extra_dhw.restore_at = 1712232000.0
        mock_coordinator.data["values"]["tap_stop"] = 1712232000

        await entity.async_turn_off()

        mock_coordinator.async_set_extra_tap_water.assert_called_once_with(
            "test_device_123", 0
        )
        # Data is updated after successful API response
        mock_coordinator.async_set_updated_data.assert_called_once()
        updated_data = mock_coordinator.async_set_updated_data.call_args[0][0]
        assert updated_data["values"]["extra_tap_water"] == "off"
        assert "tap_stop" not in updated_data["values"]
        # No immediate refresh to avoid overwriting the update

    @pytest.mark.asyncio
    async def test_async_turn_on_other_metric(self, mock_coordinator, mock_device):
        """Test turning on other metrics."""
        entity = QvantumSwitchEntity(mock_coordinator, "other_switch", mock_device)

        # Mock the API response
        mock_coordinator.client.update_setting = AsyncMock(
            return_value={"status": "APPLIED"}
        )

        await entity.async_turn_on()

        mock_coordinator.client.update_setting.assert_called_once_with(
            "test_device_123", "other_switch", 1
        )
        # The method updates metrics
        assert mock_coordinator.data["values"]["other_switch"] == 1
        mock_coordinator.async_set_updated_data.assert_called_once_with(
            mock_coordinator.data
        )

    @pytest.mark.asyncio
    async def test_async_turn_off_other_metric(self, mock_coordinator, mock_device):
        """Test turning off other metrics."""
        entity = QvantumSwitchEntity(mock_coordinator, "other_switch", mock_device)

        # Mock the API response
        mock_coordinator.client.update_setting = AsyncMock(
            return_value={"status": "APPLIED"}
        )

        await entity.async_turn_off()

        mock_coordinator.client.update_setting.assert_called_once_with(
            "test_device_123", "other_switch", 0
        )
        # The method updates metrics
        assert mock_coordinator.data["values"]["other_switch"] == 0
        mock_coordinator.async_set_updated_data.assert_called_once_with(
            mock_coordinator.data
        )

    @pytest.mark.asyncio
    async def test_async_turn_on_enable_sc_dhw(self, mock_coordinator, mock_device):
        """Test turning on enable_sc_dhw."""
        entity = QvantumSwitchEntity(mock_coordinator, "enable_sc_dhw", mock_device)

        # Mock the API response
        mock_coordinator.client.update_setting = AsyncMock(
            return_value={"status": "APPLIED"}
        )

        await entity.async_turn_on()

        mock_coordinator.client.update_setting.assert_called_once_with(
            "test_device_123", "enable_sc_dhw", True
        )
        # The method updates metrics
        assert mock_coordinator.data["values"]["enable_sc_dhw"] is True
        mock_coordinator.async_set_updated_data.assert_called_once_with(
            mock_coordinator.data
        )

    @pytest.mark.asyncio
    async def test_async_turn_off_enable_sc_dhw(self, mock_coordinator, mock_device):
        """Test turning off enable_sc_dhw."""
        entity = QvantumSwitchEntity(mock_coordinator, "enable_sc_dhw", mock_device)

        # Mock the API response
        mock_coordinator.client.update_setting = AsyncMock(
            return_value={"status": "APPLIED"}
        )

        await entity.async_turn_off()

        mock_coordinator.client.update_setting.assert_called_once_with(
            "test_device_123", "enable_sc_dhw", False
        )
        # The method updates metrics
        assert mock_coordinator.data["values"]["enable_sc_dhw"] is False
        mock_coordinator.async_set_updated_data.assert_called_once_with(
            mock_coordinator.data
        )

    @pytest.mark.asyncio
    async def test_async_turn_on_enable_sc_sh(self, mock_coordinator, mock_device):
        """Test turning on enable_sc_sh."""
        entity = QvantumSwitchEntity(mock_coordinator, "enable_sc_sh", mock_device)

        # Mock the API response
        mock_coordinator.client.update_setting = AsyncMock(
            return_value={"status": "APPLIED"}
        )

        await entity.async_turn_on()

        mock_coordinator.client.update_setting.assert_called_once_with(
            "test_device_123", "enable_sc_sh", True
        )
        # The method updates metrics
        assert mock_coordinator.data["values"]["enable_sc_sh"] is True
        mock_coordinator.async_set_updated_data.assert_called_once_with(
            mock_coordinator.data
        )

    @pytest.mark.asyncio
    async def test_async_turn_off_enable_sc_sh(self, mock_coordinator, mock_device):
        """Test turning off enable_sc_sh."""
        entity = QvantumSwitchEntity(mock_coordinator, "enable_sc_sh", mock_device)

        # Mock the API response
        mock_coordinator.client.update_setting = AsyncMock(
            return_value={"status": "APPLIED"}
        )

        await entity.async_turn_off()

        mock_coordinator.client.update_setting.assert_called_once_with(
            "test_device_123", "enable_sc_sh", False
        )
        # The method updates metrics
        assert mock_coordinator.data["values"]["enable_sc_sh"] is False
        mock_coordinator.async_set_updated_data.assert_called_once_with(
            mock_coordinator.data
        )

    def test_is_on_vacation_mode_off(self, mock_coordinator, mock_device):
        """Test is_on when vacation_mode is 'off'."""
        mock_coordinator.data["values"]["vacation_mode"] = "off"
        entity = QvantumSwitchEntity(mock_coordinator, "vacation_mode", mock_device)
        assert entity.is_on is False

    def test_is_on_vacation_mode_on(self, mock_coordinator, mock_device):
        """Test is_on when vacation_mode is 'on'."""
        mock_coordinator.data["values"]["vacation_mode"] = "on"
        entity = QvantumSwitchEntity(mock_coordinator, "vacation_mode", mock_device)
        assert entity.is_on is True

    def test_is_on_vacation_mode_bool(self, mock_coordinator, mock_device):
        """Test is_on when vacation_mode is boolean."""
        entity = QvantumSwitchEntity(mock_coordinator, "vacation_mode", mock_device)
        mock_coordinator.data["values"]["vacation_mode"] = True
        assert entity.is_on is True
        mock_coordinator.data["values"]["vacation_mode"] = False
        assert entity.is_on is False

    def test_available_vacation_mode_available(self, mock_coordinator, mock_device):
        """Test available for vacation_mode when data is present."""
        mock_coordinator.data["values"]["vacation_mode"] = "off"
        entity = QvantumSwitchEntity(mock_coordinator, "vacation_mode", mock_device)
        assert entity.available is True

    def test_available_vacation_mode_without_elevated_access(
        self, mock_coordinator, mock_device
    ):
        """Test vacation_mode is available even without elevated write access (level 10)."""
        mock_coordinator.data["values"]["vacation_mode"] = "off"
        mock_coordinator.config_entry.runtime_data.maintenance_coordinator.data = {
            "access_level": {"writeAccessLevel": 10}
        }
        entity = QvantumSwitchEntity(mock_coordinator, "vacation_mode", mock_device)
        assert entity._has_write_access is False
        assert entity.available is True

    def test_available_vacation_mode_none(self, mock_coordinator, mock_device):
        """Test available for vacation_mode when value is None."""
        mock_coordinator.data["values"]["vacation_mode"] = None
        entity = QvantumSwitchEntity(mock_coordinator, "vacation_mode", mock_device)
        assert entity.available is False

    def test_available_vacation_mode_modbus_unavailable(self, mock_coordinator, mock_device):
        """Test vacation_mode is unavailable in Modbus mode (cloud-only write)."""
        mock_coordinator.data["values"]["vacation_mode"] = "off"
        mock_coordinator.modbus_enabled = True
        entity = QvantumSwitchEntity(mock_coordinator, "vacation_mode", mock_device)
        assert entity.available is False

    @pytest.mark.asyncio
    async def test_async_turn_on_vacation_mode(self, mock_coordinator, mock_device):
        """Test turning on vacation_mode."""
        entity = QvantumSwitchEntity(mock_coordinator, "vacation_mode", mock_device)

        mock_coordinator.client.update_setting = AsyncMock(
            return_value={"status": "APPLIED"}
        )

        await entity.async_turn_on()

        mock_coordinator.client.update_setting.assert_called_once_with(
            "test_device_123", "vacation_mode", True
        )
        assert mock_coordinator.data["values"]["vacation_mode"] is True
        mock_coordinator.async_set_updated_data.assert_called_once_with(
            mock_coordinator.data
        )

    @pytest.mark.asyncio
    async def test_async_turn_off_vacation_mode(self, mock_coordinator, mock_device):
        """Test turning off vacation_mode."""
        entity = QvantumSwitchEntity(mock_coordinator, "vacation_mode", mock_device)

        mock_coordinator.client.update_setting = AsyncMock(
            return_value={"status": "APPLIED"}
        )

        await entity.async_turn_off()

        mock_coordinator.client.update_setting.assert_called_once_with(
            "test_device_123", "vacation_mode", False
        )
        assert mock_coordinator.data["values"]["vacation_mode"] is False
        mock_coordinator.async_set_updated_data.assert_called_once_with(
            mock_coordinator.data
        )


class TestSwitchAvailabilityAndWriteGuard:
    """H1/H2: base availability and service-bypass write guard."""

    def test_available_is_false_when_coordinator_failed(
        self, mock_coordinator, mock_device
    ):
        """A failed poll makes the switch unavailable even with cached data."""
        mock_coordinator.data["values"]["extra_tap_water"] = "on"
        mock_coordinator.last_update_success = False
        entity = QvantumSwitchEntity(mock_coordinator, "extra_tap_water", mock_device)
        assert entity.available is False

    @pytest.mark.asyncio
    async def test_turn_on_requires_write_access(self, mock_coordinator, mock_device):
        """A service call cannot bypass the write-access check."""
        from homeassistant.exceptions import HomeAssistantError

        mock_coordinator.config_entry.runtime_data.maintenance_coordinator.data = {
            "access_level": {"writeAccessLevel": 10}
        }
        entity = QvantumSwitchEntity(mock_coordinator, "op_mode", mock_device)

        assert entity._has_write_access is False
        with pytest.raises(HomeAssistantError, match="Write access is not enabled"):
            await entity.async_turn_on()

        mock_coordinator.client.update_setting.assert_not_called()

    @pytest.mark.asyncio
    async def test_vacation_mode_is_not_blocked_by_write_guard(
        self, mock_coordinator, mock_device
    ):
        """vacation_mode is intentionally writable without elevated access."""
        mock_coordinator.data["values"]["vacation_mode"] = "off"
        mock_coordinator.config_entry.runtime_data.maintenance_coordinator.data = {
            "access_level": {"writeAccessLevel": 10}
        }
        mock_coordinator.client.update_setting = AsyncMock(
            return_value={"status": "APPLIED"}
        )
        entity = QvantumSwitchEntity(mock_coordinator, "vacation_mode", mock_device)

        assert entity._has_write_access is False
        await entity.async_turn_on()

        mock_coordinator.client.update_setting.assert_called_once_with(
            "test_device_123", "vacation_mode", True
        )


class TestQvantumCurveControlSwitch:
    """Test the custom-curve control switch."""

    def _make(self, mock_coordinator, mock_device, *, active=False, success=True):
        curve = MagicMock()
        curve.active = active
        curve.last_update_success = success
        curve.async_set_control_mode = AsyncMock()
        entity = QvantumCurveControlSwitch(mock_coordinator, curve, mock_device)
        return entity, curve

    def test_init(self, mock_coordinator, mock_device):
        entity, _curve = self._make(mock_coordinator, mock_device)

        assert entity._metric_key == "custom_curve_control"
        assert entity._attr_unique_id == "qvantum_custom_curve_control_test_device_123"
        assert entity._attr_translation_key == "custom_curve_control"
        assert entity.suggested_object_id == "custom_curve_control"

    def test_is_on_follows_curve_mode(self, mock_coordinator, mock_device):
        entity, _curve = self._make(mock_coordinator, mock_device, active=False)
        assert entity.is_on is False

        entity, _curve = self._make(mock_coordinator, mock_device, active=True)
        assert entity.is_on is True

    def test_available_requires_write_access_and_curve(self, mock_coordinator, mock_device):
        entity, _curve = self._make(mock_coordinator, mock_device)
        assert entity.available is True

        entity, _curve = self._make(mock_coordinator, mock_device, success=False)
        assert entity.available is False

        mock_coordinator.config_entry.runtime_data.maintenance_coordinator.data = {
            "access_level": {"writeAccessLevel": 0}
        }
        entity, _curve = self._make(mock_coordinator, mock_device)
        assert entity.available is False

    @pytest.mark.asyncio
    async def test_turn_on_activates(self, mock_coordinator, mock_device):
        entity, curve = self._make(mock_coordinator, mock_device)

        await entity.async_turn_on()

        curve.async_set_control_mode.assert_awaited_once_with("active")

    @pytest.mark.asyncio
    async def test_turn_off_returns_to_shadow(self, mock_coordinator, mock_device):
        entity, curve = self._make(mock_coordinator, mock_device, active=True)

        await entity.async_turn_off()

        curve.async_set_control_mode.assert_awaited_once_with("shadow")

    @pytest.mark.asyncio
    async def test_turn_on_denied_without_write_access(
        self, mock_coordinator, mock_device
    ):
        from homeassistant.exceptions import HomeAssistantError

        mock_coordinator.config_entry.runtime_data.maintenance_coordinator.data = {
            "access_level": {"writeAccessLevel": 0}
        }
        entity, curve = self._make(mock_coordinator, mock_device)

        with pytest.raises(HomeAssistantError):
            await entity.async_turn_on()

        curve.async_set_control_mode.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_async_added_subscribes_to_curve_updates(
        self, mock_coordinator, mock_device
    ):
        entity, curve = self._make(mock_coordinator, mock_device)
        curve.async_add_listener = MagicMock(return_value=MagicMock())

        await entity.async_added_to_hass()

        curve.async_add_listener.assert_called_once()
        assert callable(curve.async_add_listener.call_args.args[0])

        entity.async_write_ha_state = MagicMock()
        entity._handle_curve_update()
        entity.async_write_ha_state.assert_called_once()
