"""Tests for the shared recorder long-term-statistics helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.qvantum import statistics as stats


def test_resolve_entity_ids_matches_sensor_unique_ids():
    registry = MagicMock()
    registry.async_get_entity_id.side_effect = (
        lambda domain, integration, unique_id: (
            "sensor.qvantum_heatingenergy"
            if unique_id.endswith("heatingenergy_dev")
            else None
        )
    )

    with patch.object(stats.er, "async_get", return_value=registry):
        resolved = stats.resolve_statistic_entity_ids(
            MagicMock(), "dev", ("heatingenergy", "dhwenergy")
        )

    assert resolved == {"heatingenergy": "sensor.qvantum_heatingenergy"}


def test_resolve_entity_ids_without_device_id_returns_empty():
    assert stats.resolve_statistic_entity_ids(MagicMock(), None, ("bt1",)) == {}


async def test_statistics_returns_empty_without_ids():
    assert (
        await stats.async_statistics_during_period(
            MagicMock(), set(), datetime.now(timezone.utc)
        )
        == {}
    )


async def test_statistics_returns_empty_when_recorder_unavailable():
    with patch("homeassistant.components.recorder.get_instance", side_effect=KeyError):
        assert (
            await stats.async_statistics_during_period(
                MagicMock(), {"sensor.x"}, datetime.now(timezone.utc)
            )
            == {}
        )


async def test_statistics_uses_executor_and_maps_errors():
    hass = MagicMock()
    start = datetime.now(timezone.utc)
    rows = {"sensor.x": [{"start": 100, "sum": 1.0}]}
    hass.async_add_executor_job = AsyncMock(return_value=rows)

    with patch("homeassistant.components.recorder.get_instance", return_value=None):
        assert (
            await stats.async_statistics_during_period(
                hass, {"sensor.x"}, start, types={"sum"}
            )
            == rows
        )
    assert hass.async_add_executor_job.await_args.args[-1] == {"sum"}

    hass.async_add_executor_job = AsyncMock(side_effect=OSError("db"))
    with patch("homeassistant.components.recorder.get_instance", return_value=None):
        assert (
            await stats.async_statistics_during_period(hass, {"sensor.x"}, start)
            == {}
        )
