"""SmartControl entities are cloud-only and omitted in Modbus mode."""

from unittest.mock import MagicMock, patch

import pytest
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.qvantum import RuntimeData
from custom_components.qvantum.select import async_setup_entry as setup_select
from custom_components.qvantum.switch import async_setup_entry as setup_switch


@pytest.fixture
def mock_device():
    return DeviceInfo(
        identifiers={("qvantum", "qvantum-test_device_123")},
        manufacturer="Qvantum",
        model="QE-6",
    )


def _coordinator(*, modbus: bool):
    coordinator = MagicMock()
    coordinator.modbus_enabled = modbus
    coordinator.data = {
        "device": {"id": "test_device_123"},
        "values": {
            "hpid": "test_device_123",
            "use_adaptive": True,
            "enable_sc_sh": True,
            "enable_sc_dhw": True,
            "use_operation_sensor": 1,
            "extra_tap_water": "off",
            "op_mode": 1,
            "op_man_dhw": 1,
            "op_man_addition": 0,
            "man_mode": 0,
            "vacation_mode": False,
        },
    }
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.runtime_data.maintenance_coordinator = MagicMock()
    coordinator.config_entry.runtime_data.maintenance_coordinator.data = {
        "access_level": {"writeAccessLevel": 20}
    }
    return coordinator


@pytest.mark.asyncio
async def test_cloud_setup_creates_smartcontrol_entities(
    hass, mock_config_entry, mock_device
):
    coordinator = _coordinator(modbus=False)
    mock_config_entry.runtime_data = RuntimeData(
        coordinator=coordinator, device=mock_device
    )

    added = MagicMock()
    with patch("custom_components.qvantum.entity.cleanup_disabled_entities") as cleanup:
        await setup_select(hass, mock_config_entry, added)
    assert {entity._metric_key for entity in added.call_args.args[0]} == {
        "use_adaptive",
        "use_operation_sensor",
    }
    assert cleanup.call_args.args[2] == {"use_adaptive", "use_operation_sensor"}
    assert cleanup.call_args.args[3] == "select"

    added = MagicMock()
    with patch("custom_components.qvantum.entity.cleanup_disabled_entities") as cleanup:
        await setup_switch(hass, mock_config_entry, added)
    keys = {entity._metric_key for entity in added.call_args.args[0]}
    assert {"enable_sc_sh", "enable_sc_dhw"}.issubset(keys)
    assert "enable_sc_sh" in cleanup.call_args.args[2]
    assert "enable_sc_dhw" in cleanup.call_args.args[2]
    assert cleanup.call_args.args[3] == "switch"


@pytest.mark.asyncio
async def test_modbus_setup_omits_smartcontrol_entities(
    hass, mock_config_entry, mock_device
):
    coordinator = _coordinator(modbus=True)
    mock_config_entry.runtime_data = RuntimeData(
        coordinator=coordinator, device=mock_device
    )

    added = MagicMock()
    with patch("custom_components.qvantum.entity.cleanup_disabled_entities") as cleanup:
        await setup_select(hass, mock_config_entry, added)
    assert {entity._metric_key for entity in added.call_args.args[0]} == {
        "use_operation_sensor"
    }
    assert "use_adaptive" not in cleanup.call_args.args[2]
    assert cleanup.call_args.args[3] == "select"

    added = MagicMock()
    with patch("custom_components.qvantum.entity.cleanup_disabled_entities") as cleanup:
        await setup_switch(hass, mock_config_entry, added)
    keys = {entity._metric_key for entity in added.call_args.args[0]}
    assert "enable_sc_sh" not in keys
    assert "enable_sc_dhw" not in keys
    assert "enable_sc_sh" not in cleanup.call_args.args[2]
    assert "enable_sc_dhw" not in cleanup.call_args.args[2]
    assert cleanup.call_args.args[3] == "switch"
