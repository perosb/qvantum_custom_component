"""Direct tests for QvantumModbusClient."""

import pytest
from modbus_connection.mock import MockModbusConnection

from custom_components.qvantum.client.constants import (
    DHW_MODE_EXTRA,
    DHW_MODE_NORMAL,
)
from custom_components.qvantum.client.exceptions import ConnectionError
from custom_components.qvantum.client.modbus import QvantumModbusClient


def _client(*, writable: bool = True) -> tuple[MockModbusConnection, QvantumModbusClient]:
    connection = MockModbusConnection()
    unit = connection.for_unit(1)
    return connection, QvantumModbusClient(unit, writable=writable)


@pytest.mark.asyncio
async def test_get_metrics_decodes_scaled_input_and_sets_latency():
    _connection, client = _client()
    client.unit.input[0] = 256  # bt1, scale 0.1

    payload = await client.get_metrics("dev1", ["bt1"])

    assert payload["metrics"]["bt1"] == 25.6
    assert payload["metrics"]["hpid"] == "dev1"
    assert isinstance(payload["metrics"]["latency"], int)


@pytest.mark.asyncio
async def test_write_metric_rejected_when_not_writable():
    _connection, client = _client(writable=False)

    with pytest.raises(ConnectionError, match="Modbus writing is disabled"):
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
async def test_get_metrics_after_close_raises():
    _connection, client = _client()
    await client.close()
    with pytest.raises(ConnectionError, match="API client is closed"):
        await client.get_metrics("dev1", ["bt1"])
