"""Direct tests for QvantumModbusClient."""

import asyncio

import pytest
from modbus_connection.mock import MockModbusConnection

from custom_components.qvantum.client.constants import (
    DHW_MODE_EXTRA,
    DHW_MODE_NORMAL,
)
from custom_components.qvantum.client.exceptions import TransportError
from custom_components.qvantum.client.modbus import QvantumModbusClient
from custom_components.qvantum.client.modbus.client import tap_water_write_order


def _client(*, writable: bool = True) -> tuple[MockModbusConnection, QvantumModbusClient]:
    connection = MockModbusConnection()
    unit = connection.for_unit(1)
    return connection, QvantumModbusClient(unit, writable=writable)


@pytest.mark.asyncio
async def test_get_metrics_decodes_scaled_input_without_latency():
    _connection, client = _client()
    client.unit.input[0] = 256  # bt1, scale 0.1

    payload = await client.get_metrics("dev1", ["bt1"])

    assert payload["metrics"]["bt1"] == 25.6
    assert payload["metrics"]["hpid"] == "dev1"
    assert "latency" not in payload["metrics"]


@pytest.mark.asyncio
async def test_write_metric_rejected_when_not_writable():
    _connection, client = _client(writable=False)

    with pytest.raises(TransportError, match="Modbus writing is disabled"):
        await client.write_metric("dev1", "indoor_temperature_target", 21.5)


@pytest.mark.asyncio
async def test_set_extra_tap_water_writes_extra_or_normal():
    _connection, client = _client()

    await client.set_extra_tap_water("dev1", 60)
    assert client.unit.holding[53] == DHW_MODE_EXTRA

    await client.set_extra_tap_water("dev1", 0)
    assert client.unit.holding[53] == DHW_MODE_NORMAL


@pytest.mark.asyncio
async def test_probe_identity_from_serial_words():
    _connection, client = _client()
    client.unit.input[180] = 12
    client.unit.input[181] = 3
    client.unit.input[182] = 45
    client.unit.input[183] = 6
    client.unit.input[184] = 7
    client.unit.input[191] = 1
    client.unit.input[192] = 2
    client.unit.input[193] = 3

    identity = await client.probe_identity()

    assert identity["id"] == "12003045006007"
    assert identity["sw_version"] == "1.2.3"


@pytest.mark.asyncio
async def test_close_drops_device_but_not_unit():
    _connection, client = _client()
    unit = client.unit
    await client.close()
    assert client.device is None
    assert unit is not None


@pytest.mark.asyncio
async def test_attach_unit_reopens_closed_client():
    """attach_unit() after close() makes the client usable again."""
    _connection, client = _client()
    await client.close()

    connection = MockModbusConnection()
    client.attach_unit(connection.for_unit(1))

    payload = await client.get_metrics("dev1", ["bt1"])
    assert payload["metrics"]["hpid"] == "dev1"


@pytest.mark.asyncio
async def test_operation_timeout_maps_to_transport_error():
    """A hung unit operation must not hold the lock forever."""
    connection = MockModbusConnection()
    unit = connection.for_unit(1)
    client = QvantumModbusClient(unit, writable=False, operation_timeout=0.01)

    async def _slow(*_args, **_kwargs):
        await asyncio.sleep(1)
        return [0]

    unit.read_input_registers = _slow

    with pytest.raises(TransportError, match="timed out"):
        await client.get_metrics("dev1", ["bt1"])


def test_tap_water_write_order_raises_start_first_when_stop_drops():
    writes = tap_water_write_order(
        start=40, stop=50, current_start=55, current_stop=60
    )
    assert writes == [("tap_water_start", 40), ("tap_water_stop", 50)]


def test_tap_water_write_order_lowers_stop_first_otherwise():
    writes = tap_water_write_order(
        start=52, stop=62, current_start=50, current_stop=60
    )
    assert writes == [("tap_water_stop", 62), ("tap_water_start", 52)]


def test_tap_water_write_order_raises_start_first_on_equal_boundary():
    """When the new stop equals the current start, raise start first."""
    writes = tap_water_write_order(
        start=40, stop=50, current_start=50, current_stop=60
    )
    assert writes == [("tap_water_start", 40), ("tap_water_stop", 50)]


def test_tap_water_write_order_rejects_inverted_pair():
    with pytest.raises(ValueError, match="must be below tap_water_stop"):
        tap_water_write_order(
            start=62, stop=52, current_start=None, current_stop=None
        )


def test_tap_water_write_order_rejects_single_crossing_current():
    with pytest.raises(ValueError, match="must be above tap_water_start"):
        tap_water_write_order(
            start=0, stop=60, current_start=65, current_stop=None
        )
    with pytest.raises(ValueError, match="must be below tap_water_stop"):
        tap_water_write_order(
            start=70, stop=0, current_start=None, current_stop=65
        )


@pytest.mark.asyncio
async def test_update_setting_coerces_bool_and_writes():
    _connection, client = _client()
    await client.update_setting("dev1", "man_mode", True)
    assert client.unit.holding[2] == 1


@pytest.mark.asyncio
async def test_set_fanspeedselector_and_tap_water():
    _connection, client = _client()
    await client.set_fanspeedselector("dev1", "extra")
    assert client.unit.holding[68] == 2

    await client.set_tap_water("dev1", start=52, stop=62)
    assert client.unit.holding[56] == 52
    assert client.unit.holding[57] == 62

    await client.set_tap_water_capacity_target("dev1", 2)
    assert client.unit.holding[56] == 52
    assert client.unit.holding[57] == 62


@pytest.mark.asyncio
async def test_set_tap_water_uses_fresh_values_between_polls():
    """Stale cached settings must not reject a valid follow-up write."""
    connection = MockModbusConnection()
    unit = connection.for_unit(1)
    client = QvantumModbusClient(unit, writable=True)
    unit.holding[56] = 52  # dhw_start_normal
    unit.holding[57] = 62  # dhw_stop_normal
    await client.get_settings("dev1")  # a poll caches 52/62

    await client.set_tap_water("dev1", stop=80)
    # No poll in between; the cached stop is still 62 but the unit holds 80.
    await client.set_tap_water("dev1", start=75)

    assert unit.holding[56] == 75
    assert unit.holding[57] == 80


@pytest.mark.asyncio
async def test_set_tap_water_noop_reports_applied():
    """A 0/0 tap-water call is a no-op that still reports APPLIED."""
    _connection, client = _client()

    result = await client.set_tap_water("dev1", start=0, stop=0)

    assert result == {"status": "APPLIED"}


@pytest.mark.parametrize("capacity", [0, 8, -1])
@pytest.mark.asyncio
async def test_set_tap_water_capacity_rejects_unknown_level(capacity):
    """Capacities outside 1-7 fail clearly instead of raising KeyError."""
    _connection, client = _client()

    with pytest.raises(ValueError, match=f"Unsupported tap water capacity {capacity}"):
        await client.set_tap_water_capacity_target("dev1", capacity)


@pytest.mark.asyncio
async def test_set_heating_curve_point_writes_holding():
    _connection, client = _client(writable=True)
    result = await client.set_heating_curve_point("dev1", "curve_minus_30", 59)
    assert result == {"status": "APPLIED"}
    assert client.unit.holding[24] == 59

    await client.set_curve_type_heating("dev1", 1)
    assert client.unit.holding[22] == 1


@pytest.mark.asyncio
async def test_get_settings_includes_heating_curve():
    _connection, client = _client()
    client.unit.holding[22] = 1
    client.unit.holding[24] = 45
    client.unit.holding[27] = 32

    payload = await client.get_settings("dev1")
    settings = {item["name"]: item["value"] for item in payload["settings"]}

    assert settings["curve_type_heating"] == 1
    assert settings["curve_minus_30"] == 45
    assert settings["curve_0"] == 32


@pytest.mark.asyncio
async def test_get_metrics_after_close_raises():
    _connection, client = _client()
    await client.close()
    with pytest.raises(TransportError, match="Modbus client is closed"):
        await client.get_metrics("dev1", ["bt1"])
