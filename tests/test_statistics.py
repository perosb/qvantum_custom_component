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


def test_resolve_indoor_metric_key_follows_the_configured_sensor():
    from custom_components.qvantum.const import SensorMode

    resolved = {
        "bt2": "sensor.qvantum_indoor",
        "room_temp_external": "sensor.qvantum_room_temp_external",
    }

    assert (
        stats.resolve_indoor_metric_key(resolved, {"sensor_mode": SensorMode.EXTERNAL})
        == "room_temp_external"
    )
    assert (
        stats.resolve_indoor_metric_key(resolved, {"sensor_mode": SensorMode.BT2})
        == "bt2"
    )
    # Missing mode falls back to BT2; no candidate at all is None.
    assert stats.resolve_indoor_metric_key({"bt2": "sensor.x"}, {}) == "bt2"
    assert stats.resolve_indoor_metric_key({}, {"sensor_mode": 4}) is None


def test_indoor_metric_candidates_are_ordered_and_resolved() -> None:
    from custom_components.qvantum.const import SensorMode

    resolved = {
        "bt2": "sensor.indoor",
        "room_temp_external": "sensor.external",
        "room_temp_ext": "sensor.http_ext",
    }

    assert stats.indoor_metric_candidates(
        resolved, {"sensor_mode": SensorMode.EXTERNAL}
    ) == ("room_temp_external", "room_temp_ext", "bt2")
    assert stats.indoor_metric_candidates(
        resolved, {"sensor_mode": SensorMode.BT2}
    ) == ("bt2",)
    # Unresolved candidates never appear.
    assert stats.indoor_metric_candidates(
        {"bt2": "sensor.indoor"}, {"sensor_mode": SensorMode.EXTERNAL}
    ) == ("bt2",)


def test_indoor_metric_entity_ids_keeps_every_resolvable_candidate() -> None:
    assert stats.indoor_metric_entity_ids(
        {
            "bt1": "sensor.outdoor",
            "bt2": "sensor.indoor",
            "room_temp_external": "sensor.external",
        }
    ) == {"room_temp_external": "sensor.external", "bt2": "sensor.indoor"}


def test_pick_indoor_metric_key_falls_back_until_the_new_sensor_has_history() -> None:
    from custom_components.qvantum.const import SensorMode

    resolved = {
        "room_temp_external": "sensor.external",
        "bt2": "sensor.indoor",
    }
    values = {"sensor_mode": SensorMode.EXTERNAL}
    fresh = [{"start": index, "mean": 1.0} for index in range(2)]
    historic = [{"start": index, "mean": 1.0} for index in range(24)]

    # The configured sensor wins as soon as it has enough rows of its own.
    assert (
        stats.pick_indoor_metric_key(
            resolved,
            values,
            {
                "room_temp_external": historic,
                "bt2": historic,
            },
        )
        == "room_temp_external"
    )
    # A freshly added sensor has no history: keep the previous one.
    assert (
        stats.pick_indoor_metric_key(
            resolved,
            values,
            {"room_temp_external": fresh, "bt2": historic},
        )
        == "bt2"
    )
    # Nothing has enough rows: no series, rather than a wrong one.
    assert (
        stats.pick_indoor_metric_key(
            resolved,
            values,
            {"room_temp_external": fresh, "bt2": fresh},
        )
        is None
    )
    # Consumers with other minimums can ask for less or more.
    assert (
        stats.pick_indoor_metric_key(
            resolved, values, {"room_temp_external": fresh}, min_rows=2
        )
        == "room_temp_external"
    )
    assert (
        stats.pick_indoor_metric_key(
            resolved,
            values,
            {"room_temp_external": historic, "bt2": historic},
            min_rows=25,
        )
        is None
    )


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


async def test_statistics_uses_recorder_executor_and_maps_errors():
    hass = MagicMock()
    start = datetime.now(timezone.utc)
    rows = {"sensor.x": [{"start": 100, "sum": 1.0}]}
    recorder = MagicMock()
    recorder.async_add_executor_job = AsyncMock(return_value=rows)

    with patch(
        "homeassistant.components.recorder.get_instance", return_value=recorder
    ):
        assert (
            await stats.async_statistics_during_period(
                hass, {"sensor.x"}, start, types={"sum"}
            )
            == rows
        )
    assert recorder.async_add_executor_job.await_args.args[-1] == {"sum"}

    recorder.async_add_executor_job = AsyncMock(side_effect=OSError("db"))
    with patch(
        "homeassistant.components.recorder.get_instance", return_value=recorder
    ):
        assert (
            await stats.async_statistics_during_period(hass, {"sensor.x"}, start)
            == {}
        )
