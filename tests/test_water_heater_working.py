"""Tests for Qvantum water_heater entities (avoids metaclass issues)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError


class MockCoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator


class MockWaterHeaterEntity:
    pass


class MockWaterHeaterEntityFeature:
    TARGET_TEMPERATURE = 1
    OPERATION_MODE = 2
    ON_OFF = 8


with patch(
    "homeassistant.helpers.update_coordinator.CoordinatorEntity", MockCoordinatorEntity
):
    with patch(
        "homeassistant.components.water_heater.WaterHeaterEntity", MockWaterHeaterEntity
    ):
        with patch(
            "homeassistant.components.water_heater.WaterHeaterEntityFeature",
            MockWaterHeaterEntityFeature,
        ):
            from homeassistant.helpers.device_registry import DeviceInfo

            from custom_components.qvantum import RuntimeData
            from custom_components.qvantum.const import (
                DHW_MODE_ECO,
                DHW_MODE_EXTRA,
                DHW_MODE_NORMAL,
                DHW_MODE_SMART,
                SETTING_UPDATE_APPLIED,
            )
            from custom_components.qvantum.water_heater import (
                OPERATION_ECO,
                OPERATION_EXTRA,
                OPERATION_NORMAL,
                OPERATION_OFF,
                OPERATION_SMART,
                QvantumWaterHeaterEntity,
                async_setup_entry,
                map_operation_mode,
                operation_modes_for_values,
            )


def _applied():
    return {"status": SETTING_UPDATE_APPLIED}


@pytest.fixture
def mock_coordinator():
    """Cloud-style coordinator with DHW values (no raw dhw_mode)."""
    coordinator = MagicMock()
    coordinator.data = {
        "device": {"id": "test_device_123"},
        "values": {
            "hpid": "test_device_123",
            "bt30": 54.5,
            "tap_water_stop": 62,
            "extra_tap_water": "off",
            "op_man_dhw": 1,
        },
    }
    coordinator.client = MagicMock()
    coordinator.client.set_tap_water = AsyncMock(return_value=_applied())
    coordinator.client.update_setting = AsyncMock(return_value=_applied())
    coordinator.async_set_extra_tap_water = AsyncMock(return_value=_applied())
    coordinator.async_write_metric = AsyncMock(return_value=_applied())
    coordinator.async_set_updated_data = MagicMock()
    coordinator.modbus_enabled = False

    config_entry = MagicMock()
    maintenance_coordinator = MagicMock()
    maintenance_coordinator.data = {"access_level": {"writeAccessLevel": 20}}
    config_entry.runtime_data.maintenance_coordinator = maintenance_coordinator
    coordinator.config_entry = config_entry
    return coordinator


@pytest.fixture
def mock_modbus_coordinator(mock_coordinator):
    """Modbus-style values including raw dhw_mode."""
    mock_coordinator.modbus_enabled = True
    mock_coordinator.data["values"]["dhw_mode"] = DHW_MODE_NORMAL
    mock_coordinator.data["values"]["bt31"] = 52.0
    return mock_coordinator


@pytest.fixture
def mock_device():
    return DeviceInfo(
        identifiers={("qvantum", "qvantum-test_device_123")},
        manufacturer="Qvantum",
        model="QE-6",
    )


class TestMapOperationMode:
    def test_off_via_op_man_dhw(self):
        assert map_operation_mode({"op_man_dhw": 0}) == OPERATION_OFF

    def test_dhw_mode_mapping(self):
        assert map_operation_mode({"dhw_mode": DHW_MODE_ECO}) == OPERATION_ECO
        assert map_operation_mode({"dhw_mode": DHW_MODE_NORMAL}) == OPERATION_NORMAL
        assert map_operation_mode({"dhw_mode": DHW_MODE_EXTRA}) == OPERATION_EXTRA
        assert map_operation_mode({"dhw_mode": DHW_MODE_SMART}) == OPERATION_SMART

    def test_extra_tap_water_fallback(self):
        assert map_operation_mode({"extra_tap_water": "on"}) == OPERATION_EXTRA
        assert map_operation_mode({"extra_tap_water": "off"}) == OPERATION_NORMAL

    def test_dhw_mode_preferred_over_extra_alias(self):
        assert (
            map_operation_mode({"dhw_mode": DHW_MODE_ECO, "extra_tap_water": "off"})
            == OPERATION_ECO
        )


class TestOperationModesForValues:
    def test_cloud_without_dhw_mode(self):
        assert operation_modes_for_values({}, modbus_enabled=False) == [
            OPERATION_NORMAL,
            OPERATION_EXTRA,
        ]

    def test_modbus_includes_eco_smart(self):
        assert operation_modes_for_values({}, modbus_enabled=True) == [
            OPERATION_ECO,
            OPERATION_NORMAL,
            OPERATION_EXTRA,
            OPERATION_SMART,
        ]

    def test_off_when_op_man_present(self):
        modes = operation_modes_for_values({"op_man_dhw": 1}, modbus_enabled=False)
        assert OPERATION_OFF in modes


class TestQvantumWaterHeaterEntity:
    def test_init(self, mock_coordinator, mock_device):
        entity = QvantumWaterHeaterEntity(mock_coordinator, mock_device)
        assert entity._hpid == "test_device_123"
        assert entity._attr_unique_id == "qvantum_water_heater_test_device_123"
        assert entity._attr_translation_key == "dhw"
        assert entity._attr_min_temp == 60
        assert entity._attr_max_temp == 80

    def test_current_temperature_prefers_bt30(self, mock_coordinator, mock_device):
        mock_coordinator.data["values"]["bt31"] = 50.0
        entity = QvantumWaterHeaterEntity(mock_coordinator, mock_device)
        assert entity.current_temperature == 54.5

    def test_current_temperature_fallback(self, mock_coordinator, mock_device):
        del mock_coordinator.data["values"]["bt30"]
        mock_coordinator.data["values"]["bt31"] = 51.2
        entity = QvantumWaterHeaterEntity(mock_coordinator, mock_device)
        assert entity.current_temperature == 51.2

    def test_target_temperature(self, mock_coordinator, mock_device):
        entity = QvantumWaterHeaterEntity(mock_coordinator, mock_device)
        assert entity.target_temperature == 62.0

    def test_current_operation_cloud_normal(self, mock_coordinator, mock_device):
        entity = QvantumWaterHeaterEntity(mock_coordinator, mock_device)
        assert entity.current_operation == OPERATION_NORMAL

    def test_operation_list_cloud(self, mock_coordinator, mock_device):
        entity = QvantumWaterHeaterEntity(mock_coordinator, mock_device)
        assert entity.operation_list == [
            OPERATION_NORMAL,
            OPERATION_EXTRA,
            OPERATION_OFF,
        ]

    def test_operation_list_modbus(self, mock_modbus_coordinator, mock_device):
        entity = QvantumWaterHeaterEntity(mock_modbus_coordinator, mock_device)
        assert entity.operation_list == [
            OPERATION_ECO,
            OPERATION_NORMAL,
            OPERATION_EXTRA,
            OPERATION_SMART,
            OPERATION_OFF,
        ]

    def test_supported_features_include_on_off(self, mock_coordinator, mock_device):
        entity = QvantumWaterHeaterEntity(mock_coordinator, mock_device)
        assert entity.supported_features & MockWaterHeaterEntityFeature.ON_OFF
        assert (
            entity.supported_features
            & MockWaterHeaterEntityFeature.TARGET_TEMPERATURE
        )
        assert entity.supported_features & MockWaterHeaterEntityFeature.OPERATION_MODE

    @pytest.mark.asyncio
    async def test_set_temperature(self, mock_coordinator, mock_device):
        entity = QvantumWaterHeaterEntity(mock_coordinator, mock_device)
        await entity.async_set_temperature(temperature=70)
        mock_coordinator.client.set_tap_water.assert_awaited_once_with(
            "test_device_123", stop=70
        )
        assert mock_coordinator.data["values"]["tap_water_stop"] == 70

    @pytest.mark.asyncio
    async def test_set_operation_extra(self, mock_coordinator, mock_device):
        entity = QvantumWaterHeaterEntity(mock_coordinator, mock_device)
        await entity.async_set_operation_mode(OPERATION_EXTRA)
        mock_coordinator.async_set_extra_tap_water.assert_awaited_once_with(
            "test_device_123", -1
        )
        assert mock_coordinator.data["values"]["extra_tap_water"] == "on"

    @pytest.mark.asyncio
    async def test_set_operation_normal(self, mock_coordinator, mock_device):
        entity = QvantumWaterHeaterEntity(mock_coordinator, mock_device)
        await entity.async_set_operation_mode(OPERATION_NORMAL)
        mock_coordinator.async_set_extra_tap_water.assert_awaited_once_with(
            "test_device_123", 0
        )

    @pytest.mark.asyncio
    async def test_set_operation_off(self, mock_coordinator, mock_device):
        entity = QvantumWaterHeaterEntity(mock_coordinator, mock_device)
        await entity.async_set_operation_mode(OPERATION_OFF)
        mock_coordinator.client.update_setting.assert_awaited_once_with(
            "test_device_123", "op_man_dhw", 0
        )

    @pytest.mark.asyncio
    async def test_set_operation_eco_modbus(self, mock_modbus_coordinator, mock_device):
        entity = QvantumWaterHeaterEntity(mock_modbus_coordinator, mock_device)
        await entity.async_set_operation_mode(OPERATION_ECO)
        mock_modbus_coordinator.async_write_metric.assert_awaited_once_with(
            "test_device_123", "extra_tap_water", DHW_MODE_ECO
        )
        assert mock_modbus_coordinator.data["values"]["dhw_mode"] == DHW_MODE_ECO

    @pytest.mark.asyncio
    async def test_set_operation_eco_cloud_raises(self, mock_coordinator, mock_device):
        entity = QvantumWaterHeaterEntity(mock_coordinator, mock_device)
        with pytest.raises(HomeAssistantError):
            await entity.async_set_operation_mode(OPERATION_ECO)


class TestAsyncSetupEntry:
    @pytest.mark.asyncio
    async def test_setup_cloud_creates_entity(
        self, hass, mock_config_entry, mock_coordinator, mock_device
    ):
        mock_config_entry.runtime_data = RuntimeData(
            coordinator=mock_coordinator,
            client=MagicMock(),
            device=mock_device,
        )
        async_add_entities = MagicMock()
        await async_setup_entry(hass, mock_config_entry, async_add_entities)
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 1
        assert isinstance(entities[0], QvantumWaterHeaterEntity)

    @pytest.mark.asyncio
    async def test_setup_modbus_creates_entity(
        self, hass, mock_config_entry, mock_modbus_coordinator, mock_device
    ):
        mock_config_entry.runtime_data = RuntimeData(
            coordinator=mock_modbus_coordinator,
            client=MagicMock(),
            device=mock_device,
        )
        async_add_entities = MagicMock()
        await async_setup_entry(hass, mock_config_entry, async_add_entities)
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 1
        assert entities[0].current_operation == OPERATION_NORMAL

    @pytest.mark.asyncio
    async def test_setup_skips_without_dhw_metrics(
        self, hass, mock_config_entry, mock_coordinator, mock_device
    ):
        mock_coordinator.data["values"] = {"hpid": "test_device_123"}
        mock_config_entry.runtime_data = RuntimeData(
            coordinator=mock_coordinator,
            client=MagicMock(),
            device=mock_device,
        )
        async_add_entities = MagicMock()
        await async_setup_entry(hass, mock_config_entry, async_add_entities)
        entities = async_add_entities.call_args[0][0]
        assert entities == []
