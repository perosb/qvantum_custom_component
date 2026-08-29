"""Tests for the typed Modbus model and dict adapter."""

import pytest
from modbus_connection.mock import MockModbusConnection

from custom_components.qvantum.modbus import (
    MODBUS_HOLDING_REGISTER_MAP,
    MODBUS_INPUT_REGISTER_MAP,
    RELAY_BIT_MAP,
)
from custom_components.qvantum.modbus_device import (
    QvantumModbusDevice,
    build_metrics_payload,
    build_settings_payload,
    component_values,
    holding_field_for_metric,
)
from custom_components.qvantum.modbus_model import QvantumInputs, QvantumSettings


def _device():
    connection = MockModbusConnection()
    unit = connection.for_unit(1)
    return connection, unit, QvantumModbusDevice(unit)


class TestRegisterMapAlignment:
    def test_input_fields_match_register_map(self):
        for name, (address, data_type, scale) in MODBUS_INPUT_REGISTER_MAP.items():
            if name == "relays_bitmask":
                continue
            field = QvantumInputs.declared_fields[name]
            assert field.address == address
            assert field.signed is (data_type == "int16")
            assert (field.scale or 1.0) == scale

    def test_relay_bits_use_bitmask_register(self):
        bitmask_address = MODBUS_INPUT_REGISTER_MAP["relays_bitmask"][0]
        for name, index in RELAY_BIT_MAP.items():
            field = QvantumInputs.declared_fields[name]
            assert field.address == bitmask_address
            assert field.start == index

    def test_holding_fields_match_register_map(self):
        for name, (address, data_type, scale) in MODBUS_HOLDING_REGISTER_MAP.items():
            field = QvantumSettings.declared_fields[name]
            assert field.address == address
            assert field.signed is (data_type == "int16")
            assert (field.scale or 1.0) == scale
            assert field.writable is True


class TestComponentDecode:
    @pytest.mark.asyncio
    async def test_decodes_scaled_signed_and_bits(self):
        _, unit, device = _device()
        unit.input[0] = 215  # bt1 21.5
        unit.input[2] = 0xFFD8  # bt2 -4.0 as int16
        unit.input[33] = 0b0101  # L1 and L3 heat relays
        unit.input[93] = 420  # compressor_power
        unit.input[95] = 4
        unit.input[96] = 8010  # compressor_kwh scale 0.1

        await device.async_update_inputs()
        values = component_values(device.inputs)

        assert values["bt1"] == 21.5
        assert values["bt2"] == -4.0
        assert values["picpin_relay_heat_l1"] == 1
        assert values["picpin_relay_heat_l2"] == 0
        assert values["picpin_relay_heat_l3"] == 1
        assert values["compressor_power"] == 420
        assert values["compressor_kwh"] == 801.0

    @pytest.mark.asyncio
    async def test_input_poll_uses_few_block_reads(self):
        _, unit, device = _device()
        await device.async_update_inputs()
        input_reads = [
            event for event in unit.read_events if event.register_type == "input"
        ]
        assert len(input_reads) <= 3
        assert all(event.count <= 125 for event in input_reads)
        covered = {
            addr
            for event in input_reads
            for addr in range(event.address, event.address + event.count)
        }
        assert not set(range(105, 161)) & covered

    @pytest.mark.asyncio
    async def test_settings_poll_uses_one_block_read(self):
        _, unit, device = _device()
        await device.async_update_settings()
        holding_reads = [
            event for event in unit.read_events if event.register_type == "holding"
        ]
        assert len(holding_reads) == 1
        assert holding_reads[0].address == 0
        assert holding_reads[0].count == 89

    @pytest.mark.asyncio
    async def test_settings_write_applies_scale(self):
        _, unit, device = _device()
        await device.write_metric("room_comp_factor", 2.5)
        await device.write_metric("room_temp_external", 21.5)
        await device.write_metric("dhw_stop_extra", 75)

        assert unit.holding[13] == 25
        assert unit.holding[14] == 215
        assert unit.holding[59] == 75

    @pytest.mark.asyncio
    async def test_raw_holding_write(self):
        _, unit, device = _device()
        await device.write_holding_register(9, 4)
        assert unit.holding[9] == 4


class TestPayloadAdapter:
    def test_powertotal_from_relays_and_compressor(self):
        payload = build_metrics_payload(
            "dev",
            {
                "picpin_relay_heat_l1": 1,
                "picpin_relay_heat_l2": 1,
                "picpin_relay_heat_l3": 1,
                "compressor_power": 100,
            },
        )
        assert payload["metrics"]["powertotal"] == 5260.0
        assert "compressor_power" not in payload["metrics"]
        assert payload["metrics"]["hpid"] == "dev"

    def test_compressorenergy_from_mwh_kwh(self):
        payload = build_metrics_payload(
            "dev",
            {"compressor_mwh": 4, "compressor_kwh": 801.0},
        )
        assert payload["metrics"]["compressorenergy"] == 4801.0
        assert "compressor_mwh" not in payload["metrics"]
        assert "compressor_kwh" not in payload["metrics"]

    def test_skips_energy_if_component_missing(self):
        payload = build_metrics_payload("dev", {"compressor_mwh": 4})
        assert "compressorenergy" not in payload["metrics"]

    def test_skips_energy_if_component_non_numeric(self):
        payload = build_metrics_payload(
            "dev",
            {"compressor_mwh": "not-a-number", "compressor_kwh": 1},
        )
        assert "compressorenergy" not in payload["metrics"]

    def test_rounds_to_two_decimals(self):
        payload = build_metrics_payload(
            "dev",
            {
                "picpin_relay_heat_l1": 1,
                "picpin_relay_heat_l2": 0,
                "picpin_relay_heat_l3": 0,
                "compressor_power": 100.12345,
                "compressor_mwh": 1.2345,
                "compressor_kwh": 2.3456,
            },
        )
        assert payload["metrics"]["powertotal"] == 2260.12
        assert payload["metrics"]["compressorenergy"] == 1236.85

    def test_use_adaptive_from_smart_dhw_mode(self):
        payload = build_metrics_payload("dev", {"smart_dhw_mode": 1})
        assert payload["metrics"]["use_adaptive"] is True
        assert payload["metrics"]["smart_sh_mode"] == 1

    def test_filters_to_enabled_metrics_before_derivation(self):
        payload = build_metrics_payload(
            "dev",
            {"bt1": 21.5, "compressor_mwh": 4, "compressor_kwh": 1},
            enabled_metrics=["bt1"],
        )
        assert payload["metrics"]["bt1"] == 21.5
        assert "compressorenergy" not in payload["metrics"]
        assert "latency" not in payload["metrics"]

    def test_settings_aliasing_and_hidden_keys(self):
        payload = build_settings_payload(
            {
                "dhw_start_normal": 52,
                "dhw_stop_normal": 62,
                "operation_mode": 1,
            }
        )
        settings = {item["name"]: item["value"] for item in payload["settings"]}
        assert "dhw_start_normal" not in settings
        assert "dhw_stop_normal" not in settings
        assert settings["tap_water_start"] == 52
        assert settings["tap_water_stop"] == 62
        assert settings["op_mode"] == 1
        assert "use_adaptive" not in settings

    def test_extra_tap_water_and_fan_mapping(self):
        payload = build_settings_payload(
            {"dhw_mode": 2, "ventilation_state": 2}
        )
        settings = {item["name"]: item["value"] for item in payload["settings"]}
        assert settings["extra_tap_water"] == "on"
        assert settings["fanspeedselector"] == "extra"

    def test_unknown_metric_raises(self):
        with pytest.raises(
            ValueError, match="No Modbus holding register mapping found"
        ):
            holding_field_for_metric("not_a_real_metric")

    def test_holding_field_for_http_alias(self):
        assert holding_field_for_metric("room_comp_factor") == "room_compensation"
        assert holding_field_for_metric("dhw_stop_extra") == "dhw_stop_extra"
