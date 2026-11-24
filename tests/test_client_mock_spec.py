"""The transport mock is spec'd, so a wrong method name cannot pass silently.

A bare ``MagicMock`` invents whatever attribute it is asked for, which is how a
review once "proved" a client method was never called when the method did not
exist at all: ``mock.client.update_setting.assert_not_called()`` answered a
question nobody could answer. ``make_client_mock`` binds the mock to the real
transport class, so reading a name the client does not have raises.
"""

from __future__ import annotations

import pytest

from custom_components.qvantum.client.cloud import QvantumCloudClient
from custom_components.qvantum.client.modbus import QvantumModbusClient
from tests.conftest import make_client_mock


def test_the_cloud_mock_knows_the_cloud_methods() -> None:
    client = make_client_mock()
    assert isinstance(client, QvantumCloudClient)
    # A method the client really has stays callable and assertable.
    client.update_setting.assert_not_called()
    client.set_indoor_temperature_target.assert_not_called()


def test_the_modbus_mock_knows_the_modbus_methods() -> None:
    client = make_client_mock(modbus=True)
    assert isinstance(client, QvantumModbusClient)
    assert client.writable is True
    client.write_metric.assert_not_called()


def test_a_made_up_method_name_raises() -> None:
    """This is the failure mode the spec exists to remove."""
    client = make_client_mock()
    with pytest.raises(AttributeError):
        client.update_settingg.assert_not_called()


def test_a_method_of_the_other_transport_raises_on_the_cloud_mock() -> None:
    """A cloud test cannot assert on a Modbus-only write by accident."""
    client = make_client_mock()
    with pytest.raises(AttributeError):
        client.write_metric.assert_not_called()


def test_the_test_suite_hands_the_coordinator_a_specd_client(mock_coordinator) -> None:
    assert isinstance(mock_coordinator.client, QvantumCloudClient)
    mock_coordinator.client.update_setting.assert_not_called()
    with pytest.raises(AttributeError):
        mock_coordinator.client.update_settingg.assert_not_called()
