"""Direct tests for QvantumCloudClient."""

import json
import os
from datetime import datetime, timedelta

import pytest

from custom_components.qvantum.client.cloud import QvantumCloudClient
from custom_components.qvantum.client.exceptions import AuthError, TransportError


def load_test_data(filename):
    test_data_dir = os.path.join(
        os.path.dirname(__file__), "..", "custom_components", "qvantum", "test_data"
    )
    with open(os.path.join(test_data_dir, filename), encoding="utf-8") as handle:
        return json.load(handle)


@pytest.mark.asyncio
async def test_authenticate_stores_token(mock_session):
    auth_data = load_test_data("auth_signin.json")
    cm, _ = mock_session.make_cm_response(status=200, json_data=auth_data)
    mock_session.post.return_value = cm

    client = QvantumCloudClient(
        "test@example.com", "password", "test-agent", session=mock_session
    )
    assert await client.authenticate() is True
    assert client._token == auth_data["idToken"]


@pytest.mark.asyncio
async def test_authenticate_failure_raises(mock_session):
    cm, _ = mock_session.make_cm_response(status=400, json_data={})
    mock_session.post.return_value = cm
    client = QvantumCloudClient(
        "test@example.com", "password", "test-agent", session=mock_session
    )
    with pytest.raises(AuthError):
        await client.authenticate()


@pytest.mark.asyncio
async def test_get_metrics_maps_values(mock_session):
    metrics_data = load_test_data("metrics_test_device.json")
    cm, resp = mock_session.make_cm_response(
        status=200, json_data=metrics_data, headers={"ETag": "etag123"}
    )
    resp.headers = {"ETag": "etag123"}
    mock_session.get.return_value = cm

    client = QvantumCloudClient(
        "test@example.com", "password", "test-agent", session=mock_session
    )
    client._token = "test_token"
    client._token_expiry = datetime.now() + timedelta(hours=1)

    result = await client.get_metrics("test_device", ["bt1", "bt2"])
    assert result["metrics"]["bt1"] == metrics_data["values"]["bt1"]
    assert result["metrics"]["hpid"] == "test_device"


@pytest.mark.asyncio
async def test_get_metrics_after_close_raises(mock_session):
    client = QvantumCloudClient(
        "test@example.com", "password", "test-agent", session=mock_session
    )
    await client.close()
    with pytest.raises(TransportError, match="Cloud client is closed"):
        await client.get_metrics("dev1", ["bt1"])


@pytest.mark.asyncio
async def test_set_indoor_temperature_target_patches(mock_session):
    update_data = load_test_data("settings_update_test_device.json")
    cm, _ = mock_session.make_cm_response(status=200, json_data=update_data)
    mock_session.patch.return_value = cm
    client = QvantumCloudClient(
        "test@example.com", "password", "test-agent", session=mock_session
    )
    client._token = "test_token"
    client._token_expiry = datetime.now() + timedelta(hours=1)

    result = await client.set_indoor_temperature_target("test_device", 22.5)
    assert result == update_data
    mock_session.patch.assert_called_once()
