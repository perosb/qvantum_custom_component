"""Direct tests for QvantumCloudClient."""

import asyncio
import json
import os
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import aiohttp
import pytest

from custom_components.qvantum.client.cloud import QvantumCloudClient
from custom_components.qvantum.client.cloud.endpoints import HTTP_TIMEOUT
from custom_components.qvantum.client.exceptions import (
    AuthError,
    RateLimitError,
    TransportError,
)


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
async def test_close_is_idempotent_and_rejects_new_requests(mock_session):
    """close() is repeatable; a new request never reaches the session."""
    client = QvantumCloudClient(
        "test@example.com", "password", "test-agent", session=mock_session
    )
    await client.close()
    await client.close()

    with pytest.raises(TransportError, match="Cloud client is closed"):
        async with client._track_request(mock_session.get("http://example.com")):
            pass


@pytest.mark.asyncio
async def test_cancelled_close_can_be_retried(mock_session):
    """A close() cancelled while draining leaves the client open for a retry."""
    cm, resp = mock_session.make_cm_response(status=200, json_data={"values": {}})
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_enter():
        entered.set()
        await release.wait()
        return resp

    cm.__aenter__ = AsyncMock(side_effect=slow_enter)
    mock_session.get.return_value = cm
    mock_session.close = AsyncMock()

    client = QvantumCloudClient(
        "test@example.com", "password", "test-agent", session=mock_session
    )
    client._session_owner = True
    client._token = "test_token"
    client._token_expiry = datetime.now() + timedelta(hours=1)

    request = asyncio.create_task(client.get_metrics("dev1", ["bt1"]))
    closer = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)

        closer = asyncio.create_task(client.close())
        for _ in range(10):
            if client._closed:
                break
            await asyncio.sleep(0)
        assert client._closed is True

        closer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closer
        assert client._closed is False

        release.set()
        await asyncio.wait_for(request, timeout=1)

        await client.close()
        assert client._closed is True
        mock_session.close.assert_awaited_once()
    finally:
        release.set()
        for task in (request, closer):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass


@pytest.mark.asyncio
async def test_concurrent_close_waits_for_drain(mock_session):
    """A second close() waits for the drain instead of returning early."""
    cm, resp = mock_session.make_cm_response(status=200, json_data={"values": {}})
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_enter():
        entered.set()
        await release.wait()
        return resp

    cm.__aenter__ = AsyncMock(side_effect=slow_enter)
    mock_session.get.return_value = cm
    mock_session.close = AsyncMock()

    client = QvantumCloudClient(
        "test@example.com", "password", "test-agent", session=mock_session
    )
    client._session_owner = True
    client._token = "test_token"
    client._token_expiry = datetime.now() + timedelta(hours=1)

    request = asyncio.create_task(client.get_metrics("dev1", ["bt1"]))
    closers = []
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)

        first = asyncio.create_task(client.close())
        closers.append(first)
        for _ in range(10):
            if client._closed:
                break
            await asyncio.sleep(0)

        second = asyncio.create_task(client.close())
        closers.append(second)
        done, _pending = await asyncio.wait({second}, timeout=0.05)
        assert second not in done

        release.set()
        await asyncio.wait_for(request, timeout=1)
        await asyncio.wait_for(asyncio.gather(*closers), timeout=1)

        mock_session.close.assert_awaited_once()
    finally:
        release.set()
        for task in (request, *closers):
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass


@pytest.mark.asyncio
async def test_close_waits_for_in_flight_request(mock_session):
    """close() drains active HTTP requests before closing an owned session."""
    cm, resp = mock_session.make_cm_response(status=200, json_data={"values": {}})
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_enter():
        entered.set()
        await release.wait()
        return resp

    cm.__aenter__ = AsyncMock(side_effect=slow_enter)
    mock_session.get.return_value = cm
    mock_session.close = AsyncMock()

    client = QvantumCloudClient(
        "test@example.com", "password", "test-agent", session=mock_session
    )
    client._session_owner = True
    client._token = "test_token"
    client._token_expiry = datetime.now() + timedelta(hours=1)

    request = asyncio.create_task(client.get_metrics("dev1", ["bt1"]))
    closer = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)

        closer = asyncio.create_task(client.close())
        done, _pending = await asyncio.wait({closer}, timeout=0.05)
        assert closer not in done
        assert client._closed is True
        mock_session.close.assert_not_awaited()

        release.set()
        await asyncio.wait_for(request, timeout=1)
        await asyncio.wait_for(closer, timeout=1)

        mock_session.close.assert_awaited_once()
    finally:
        release.set()
        for task in (request, closer):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass


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
    patch_kwargs = mock_session.patch.call_args.kwargs
    assert patch_kwargs["timeout"] is HTTP_TIMEOUT
    assert patch_kwargs["headers"]["User-Agent"] == "test-agent"
    assert patch_kwargs["headers"]["Authorization"] == "Bearer test_token"


@pytest.mark.asyncio
async def test_injected_session_authenticate_uses_timeout_and_user_agent(mock_session):
    """HA-injected sessions must still send User-Agent and HTTP_TIMEOUT."""
    auth_data = load_test_data("auth_signin.json")
    cm, _ = mock_session.make_cm_response(status=200, json_data=auth_data)
    mock_session.post.return_value = cm

    client = QvantumCloudClient(
        "test@example.com", "password", "test-agent", session=mock_session
    )
    assert await client.authenticate() is True
    post_kwargs = mock_session.post.call_args.kwargs
    assert post_kwargs["timeout"] is HTTP_TIMEOUT
    assert post_kwargs["headers"]["User-Agent"] == "test-agent"
    assert "Authorization" not in post_kwargs["headers"]

@pytest.mark.asyncio
async def test_authenticate_server_error_raises_transport_error(mock_session):
    """A 5xx sign-in failure is retryable, not invalid credentials."""
    cm, _ = mock_session.make_cm_response(status=503, json_data={})
    mock_session.post.return_value = cm

    client = QvantumCloudClient(
        "test@example.com", "password", "test-agent", session=mock_session
    )
    with pytest.raises(TransportError, match="Authentication failed"):
        await client.authenticate()


@pytest.mark.asyncio
async def test_authenticate_invalid_credentials_surfaces_server_message(mock_session):
    """Firebase's error.message is surfaced on rejected credentials."""
    cm, _ = mock_session.make_cm_response(
        status=400,
        json_data={"error": {"code": 400, "message": "INVALID_PASSWORD"}},
    )
    mock_session.post.return_value = cm

    client = QvantumCloudClient(
        "test@example.com", "password", "test-agent", session=mock_session
    )
    with pytest.raises(AuthError, match="INVALID_PASSWORD"):
        await client.authenticate()


@pytest.mark.asyncio
async def test_authenticate_400_without_body_keeps_default_message(mock_session):
    """A 400 without a Firebase error body still raises a clear AuthError."""
    cm, _ = mock_session.make_cm_response(status=400, json_data={})
    mock_session.post.return_value = cm

    client = QvantumCloudClient(
        "test@example.com", "password", "test-agent", session=mock_session
    )
    with pytest.raises(AuthError, match="Authentication failed"):
        await client.authenticate()


@pytest.mark.asyncio
async def test_authenticate_400_body_read_error_keeps_default_message(mock_session):
    """A dropped connection while reading the 400 body still raises AuthError."""
    cm, resp = mock_session.make_cm_response(status=400, json_data={})
    resp.json.side_effect = aiohttp.ClientPayloadError("connection closed")
    mock_session.post.return_value = cm

    client = QvantumCloudClient(
        "test@example.com", "password", "test-agent", session=mock_session
    )
    with pytest.raises(AuthError, match="Authentication failed"):
        await client.authenticate()


@pytest.mark.asyncio
async def test_authenticate_400_non_dict_body_keeps_default_message(mock_session):
    """A non-object Firebase error body falls back to the default message."""
    cm, _ = mock_session.make_cm_response(status=400, json_data=["busy"])
    mock_session.post.return_value = cm

    client = QvantumCloudClient(
        "test@example.com", "password", "test-agent", session=mock_session
    )
    with pytest.raises(AuthError, match="Authentication failed"):
        await client.authenticate()


@pytest.mark.asyncio
async def test_authenticate_400_empty_message_keeps_default_message(mock_session):
    """An empty error.message falls back to the default message."""
    cm, _ = mock_session.make_cm_response(
        status=400, json_data={"error": {"message": ""}}
    )
    mock_session.post.return_value = cm

    client = QvantumCloudClient(
        "test@example.com", "password", "test-agent", session=mock_session
    )
    with pytest.raises(AuthError, match="Authentication failed"):
        await client.authenticate()


@pytest.mark.asyncio
async def test_refresh_server_error_does_not_fall_back_to_sign_in(mock_session):
    """A 5xx token refresh raises instead of adding a sign-in request."""
    cm, _ = mock_session.make_cm_response(status=503, json_data={})
    mock_session.post.return_value = cm

    client = QvantumCloudClient(
        "test@example.com", "password", "test-agent", session=mock_session
    )
    client._refreshtoken = "refresh_token"
    client._token = None
    client._token_expiry = None

    with pytest.raises(TransportError, match="Token refresh failed"):
        await client._ensure_valid_token()
    mock_session.post.assert_called_once()


@pytest.mark.asyncio
async def test_concurrent_token_refresh_issues_one_request(mock_session):
    """Concurrent callers share a single refresh request."""
    refresh_data = {
        "access_token": "new_access_token",
        "refresh_token": "new_refresh_token",
        "expires_in": 3600,
    }
    cm, resp = mock_session.make_cm_response(status=200, json_data=refresh_data)

    async def slow_enter():
        # Force a suspension so the second caller reaches the lock while the
        # first is still refreshing.
        await asyncio.sleep(0)
        return resp

    cm.__aenter__ = AsyncMock(side_effect=slow_enter)
    mock_session.post.return_value = cm

    client = QvantumCloudClient(
        "test@example.com", "password", "test-agent", session=mock_session
    )
    client._token = "expired_token"
    client._token_expiry = datetime.now() - timedelta(seconds=1)
    client._refreshtoken = "refresh_token"

    await asyncio.gather(
        client._ensure_valid_token(),
        client._ensure_valid_token(),
    )

    mock_session.post.assert_called_once()
    assert client._token == "new_access_token"


@pytest.mark.asyncio
async def test_failed_sign_in_is_not_retried_by_waiters(mock_session):
    """Concurrent callers share one failed attempt instead of one each."""
    cm, resp = mock_session.make_cm_response(
        status=400, json_data={"error": {"message": "INVALID_PASSWORD"}}
    )

    async def slow_enter():
        await asyncio.sleep(0)
        return resp

    cm.__aenter__ = AsyncMock(side_effect=slow_enter)
    mock_session.post.return_value = cm

    client = QvantumCloudClient(
        "test@example.com", "password", "test-agent", session=mock_session
    )

    results = await asyncio.gather(
        client._ensure_valid_token(),
        client._ensure_valid_token(),
        client._ensure_valid_token(),
        return_exceptions=True,
    )

    assert all(isinstance(err, AuthError) for err in results)
    mock_session.post.assert_called_once()


@pytest.mark.asyncio
async def test_auth_failure_cooldown_expires(mock_session):
    """A stale failure is ignored so a later poll can retry the sign-in."""
    cm, _ = mock_session.make_cm_response(status=400, json_data={})
    mock_session.post.return_value = cm

    client = QvantumCloudClient(
        "test@example.com", "password", "test-agent", session=mock_session
    )

    with pytest.raises(AuthError):
        await client._ensure_valid_token()
    with pytest.raises(AuthError):
        await client._ensure_valid_token()
    assert mock_session.post.call_count == 1

    failed_at, err_type, status, message = client._auth_failure
    client._auth_failure = (failed_at - 10_000, err_type, status, message)

    with pytest.raises(AuthError):
        await client._ensure_valid_token()
    assert mock_session.post.call_count == 2


@pytest.mark.asyncio
async def test_cached_auth_failure_raises_fresh_instances(mock_session):
    """Each caller gets its own exception instance, not a shared traceback."""
    cm, _ = mock_session.make_cm_response(status=400, json_data={})
    mock_session.post.return_value = cm

    client = QvantumCloudClient(
        "test@example.com", "password", "test-agent", session=mock_session
    )

    with pytest.raises(AuthError) as first:
        await client._ensure_valid_token()
    with pytest.raises(AuthError) as second:
        await client._ensure_valid_token()

    assert first.value is not second.value
    assert str(first.value) == str(second.value)


@pytest.mark.asyncio
async def test_authenticate_sign_in_lockout_raises_rate_limit(mock_session):
    """Firebase's 400 lockout must not send Home Assistant into reauth."""
    cm, _ = mock_session.make_cm_response(
        status=400,
        json_data={
            "error": {
                "message": (
                    "TOO_MANY_ATTEMPTS_TRY_LATER : Access to this account has "
                    "been temporarily disabled by a short-term security measure."
                )
            }
        },
    )
    mock_session.post.return_value = cm

    client = QvantumCloudClient(
        "test@example.com", "password", "test-agent", session=mock_session
    )
    with pytest.raises(RateLimitError):
        await client.authenticate()


@pytest.mark.asyncio
async def test_connection_error_during_sign_in_is_cached(mock_session):
    """A connection failure during auth is typed and cached for waiters."""
    cm, _ = mock_session.make_cm_response(status=200, json_data={})
    cm.__aenter__ = AsyncMock(
        side_effect=aiohttp.ClientConnectionError("connection refused")
    )
    mock_session.post.return_value = cm

    client = QvantumCloudClient(
        "test@example.com", "password", "test-agent", session=mock_session
    )

    with pytest.raises(TransportError, match="Authentication request failed"):
        await client._ensure_valid_token()
    with pytest.raises(TransportError, match="Authentication request failed"):
        await client._ensure_valid_token()
    mock_session.post.assert_called_once()


@pytest.mark.asyncio
async def test_set_tap_water_noop_reports_applied(mock_session):
    """A 0/0 tap-water call is a no-op that still reports APPLIED."""
    client = QvantumCloudClient(
        "test@example.com", "password", "test-agent", session=mock_session
    )
    client._token = "test_token"
    client._token_expiry = datetime.now() + timedelta(hours=1)

    result = await client.set_tap_water("test_device", start=0, stop=0)

    assert result == {"status": "APPLIED"}
    mock_session.patch.assert_not_called()


@pytest.mark.parametrize("capacity", [0, 8, -1])
@pytest.mark.asyncio
async def test_set_tap_water_capacity_target_rejects_unknown_level(
    mock_session, capacity
):
    """Capacities outside 1-7 fail clearly on the cloud client too."""
    client = QvantumCloudClient(
        "test@example.com", "password", "test-agent", session=mock_session
    )
    client._token = "test_token"
    client._token_expiry = datetime.now() + timedelta(hours=1)

    with pytest.raises(ValueError, match=f"Unsupported tap water capacity {capacity}"):
        await client.set_tap_water_capacity_target("test_device", capacity)

    mock_session.patch.assert_not_called()
