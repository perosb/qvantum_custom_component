"""The window arithmetic behind `tools/tank_balance.py`.

The tool is stdlib-only and reads Home Assistant over HTTP, so these pin the
pure helpers instead: a window that opens or closes at the wrong sample turns a
measurement into a plausible-looking wrong number.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "tank_balance",
    Path(__file__).resolve().parents[1] / "tools" / "tank_balance.py",
)
assert _SPEC is not None and _SPEC.loader is not None
tank_balance = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tank_balance)

T0 = datetime(2026, 11, 24, 19, 0, tzinfo=UTC)


def _series(*values: float, step_s: int = 60) -> list[tuple[datetime, float]]:
    return [(T0 + timedelta(seconds=index * step_s), value)
            for index, value in enumerate(values)]


def test_delta_reads_the_value_held_at_each_edge():
    series = _series(10.0, 10.0, 12.5, 12.5)

    assert tank_balance._delta(series, series[0][0], series[3][0]) == pytest.approx(2.5)
    # A window that starts between samples takes the value in force then.
    assert tank_balance._delta(
        series, series[1][0] + timedelta(seconds=30), series[3][0]
    ) == pytest.approx(2.5)


def test_delta_rejects_a_counter_that_went_backwards():
    series = _series(10.0, 4.0)

    assert tank_balance._delta(series, series[0][0], series[1][0]) is None


def test_steps_finds_counter_increases_inside_the_window_only():
    series = _series(10.0, 10.0, 10.1, 10.1, 10.3)

    inside = tank_balance._steps(series, series[0][0], series[4][0])
    assert inside == [series[2][0], series[4][0]]

    later = tank_balance._steps(series, series[3][0], series[4][0])
    assert later == [series[4][0]]


def test_time_weighted_mean_weights_each_sample_by_how_long_it_held():
    series = _series(20.0, 24.0, 24.0)

    # 20 °C holds for one minute, 24 °C for the next; the last sample has no
    # successor and therefore covers nothing.
    assert tank_balance._time_weighted(
        series, series[0][0], series[2][0]
    ) == pytest.approx(22.0)


def test_time_weighted_mean_is_none_without_data_inside_the_window():
    """A sample older than the window cannot be held forward across the gap."""
    series = _series(20.0)

    assert tank_balance._time_weighted(
        series, T0 + timedelta(hours=1), T0 + timedelta(hours=2)
    ) is None


def test_a_clock_argument_is_todays_time():
    reference = datetime(2026, 11, 24, 21, 30, tzinfo=UTC)

    parsed = tank_balance._parse_clock("19:05", reference)

    assert parsed == datetime(2026, 11, 24, 19, 5, tzinfo=UTC)


def test_a_bad_clock_argument_stops_the_run():
    with pytest.raises(SystemExit):
        tank_balance._parse_clock("half past seven", datetime.now(UTC))
