"""Tests for the Open-Meteo forecast and history client."""

from __future__ import annotations

import asyncio
from datetime import date
from urllib.parse import parse_qs, urlparse

import aiohttp
import pytest

from custom_components.qvantum.open_meteo import (
    ARCHIVE_URL,
    FORECAST_URL,
    HISTORICAL_FORECAST_URL,
    HourlyWeather,
    OpenMeteoError,
    fetch_forecast,
    fetch_ghi_history,
    forecast_url,
    history_url,
    hour_ts,
    parse_forecast,
    parse_ghi_history,
)

BASE = 1_760_000_000 - (1_760_000_000 % 3600)


class FakeResponse:
    def __init__(self, payload=None, *, error=None, json_error=None):
        self.payload = payload
        self.error = error
        self.json_error = json_error

    def raise_for_status(self):
        if self.error is not None:
            raise self.error

    async def json(self):
        if self.json_error is not None:
            raise self.json_error
        return self.payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls: list[str] = []

    def get(self, url, timeout=None):
        self.urls.append(url)
        return self.responses.pop(0)


def _forecast_payload(times, temperatures, irradiances, current=None):
    payload = {
        "hourly": {
            "time": list(times),
            "temperature_2m": list(temperatures),
            "shortwave_radiation": list(irradiances),
        }
    }
    if current is not None:
        payload["current"] = current
    return payload


def test_hour_ts_floors_to_hour() -> None:
    assert hour_ts(BASE + 59) == BASE
    assert hour_ts(BASE + 3600) == BASE + 3600
    assert hour_ts(BASE + 3599.9) == BASE


def test_forecast_url_contains_expected_params() -> None:
    url = forecast_url(59.3, 18.1, past_hours=6, future_hours=12)
    query = parse_qs(urlparse(url).query)

    assert url.startswith(FORECAST_URL)
    assert query["latitude"] == ["59.300000"]
    assert query["longitude"] == ["18.100000"]
    assert query["timezone"] == ["UTC"]
    assert query["timeformat"] == ["unixtime"]
    assert query["hourly"] == ["temperature_2m,shortwave_radiation"]
    assert query["past_hours"] == ["6"]
    assert query["forecast_hours"] == ["12"]


def test_history_url_contains_dates() -> None:
    url = history_url(
        HISTORICAL_FORECAST_URL, 59.3, 18.1, date(2026, 1, 1), date(2026, 2, 1)
    )
    query = parse_qs(urlparse(url).query)

    assert url.startswith(HISTORICAL_FORECAST_URL)
    assert query["start_date"] == ["2026-01-01"]
    assert query["end_date"] == ["2026-02-01"]
    assert query["hourly"] == ["shortwave_radiation"]


def test_parse_forecast_sorts_and_overrides_current_hour() -> None:
    payload = _forecast_payload(
        [BASE + 3600, BASE, BASE + 7200],
        [3.0, 2.0, None],
        [120.0, 100.0, 130.0],
        current={
            "time": BASE + 120,
            "temperature_2m": 2.5,
            "shortwave_radiation": 150.0,
        },
    )

    forecast = parse_forecast(payload)

    assert [point.hour_ts for point in forecast.points] == [BASE, BASE + 3600]
    assert forecast.at(BASE) == HourlyWeather(BASE, 2.5, 150.0)
    assert forecast.at(BASE + 7200) is None
    assert forecast.ghi_by_hour() == {BASE: 150.0, BASE + 3600: 120.0}
    assert forecast.temperature_by_hour() == {BASE: 2.5, BASE + 3600: 3.0}


def test_parse_forecast_partial_current_keeps_existing_values() -> None:
    payload = _forecast_payload(
        [BASE], [1.0], [90.0], current={"time": BASE, "shortwave_radiation": 500.0}
    )

    forecast = parse_forecast(payload)

    assert forecast.points == (HourlyWeather(BASE, 1.0, 500.0),)


def test_parse_forecast_ignores_current_hour_outside_hourly() -> None:
    payload = _forecast_payload(
        [BASE],
        [1.0],
        [90.0],
        current={"time": BASE + 3600, "shortwave_radiation": 500.0},
    )

    assert parse_forecast(payload).points == (HourlyWeather(BASE, 1.0, 90.0),)


def test_parse_forecast_clamps_ghi_and_skips_none() -> None:
    payload = _forecast_payload([BASE, BASE + 3600], [1.0, None], [-5.0, 100.0])

    assert parse_forecast(payload).points == (HourlyWeather(BASE, 1.0, 0.0),)


def test_parse_forecast_skips_non_finite_hours() -> None:
    payload = _forecast_payload(
        [BASE, BASE + 3600, BASE + 7200, BASE + 10800],
        [1.0, 2.0, 3.0, 4.0],
        [100.0, float("nan"), float("inf"), 130.0],
    )

    forecast = parse_forecast(payload)

    assert [point.hour_ts for point in forecast.points] == [BASE, BASE + 10800]

    only_corrupt = _forecast_payload([BASE], [float("nan")], [100.0])
    with pytest.raises(OpenMeteoError, match="no hourly forecast"):
        parse_forecast(only_corrupt)


def test_parse_forecast_non_finite_current_keeps_existing() -> None:
    payload = _forecast_payload(
        [BASE],
        [1.0],
        [90.0],
        current={
            "time": BASE,
            "temperature_2m": float("nan"),
            "shortwave_radiation": float("inf"),
        },
    )

    assert parse_forecast(payload).at(BASE) == HourlyWeather(BASE, 1.0, 90.0)


def test_parse_forecast_skips_non_numeric_scalars() -> None:
    payload = _forecast_payload(["abc", BASE], [1.0, 2.0], [100.0, 120.0])

    assert [point.hour_ts for point in parse_forecast(payload).points] == [BASE]

    only_corrupt = _forecast_payload([BASE], ["not-a-number"], [100.0])
    with pytest.raises(OpenMeteoError, match="no hourly forecast"):
        parse_forecast(only_corrupt)


def test_parse_forecast_non_numeric_current_is_ignored() -> None:
    payload = _forecast_payload(
        [BASE],
        [1.0],
        [90.0],
        current={"time": "nope", "shortwave_radiation": 500.0},
    )

    assert parse_forecast(payload).at(BASE) == HourlyWeather(BASE, 1.0, 90.0)


def test_parse_forecast_rejects_unusable_payloads() -> None:
    with pytest.raises(OpenMeteoError, match="missing hourly"):
        parse_forecast(["not", "a", "dict"])
    with pytest.raises(OpenMeteoError, match="missing hourly"):
        parse_forecast({"hourly": None})
    with pytest.raises(OpenMeteoError, match="no hourly forecast"):
        parse_forecast(
            {"hourly": {"time": [], "temperature_2m": [], "shortwave_radiation": []}}
        )


def test_parse_ghi_history_skips_none_and_clamps() -> None:
    payload = {
        "hourly": {
            "time": [BASE, BASE + 3600, None, BASE + 10800],
            "shortwave_radiation": [10.0, None, 20.0, -1.0],
        }
    }

    assert parse_ghi_history(payload) == {BASE: 10.0, BASE + 10800: 0.0}


def test_parse_ghi_history_skips_non_finite() -> None:
    payload = {
        "hourly": {
            "time": [BASE, BASE + 3600, BASE + 7200],
            "shortwave_radiation": [10.0, float("nan"), 30.0],
        }
    }

    assert parse_ghi_history(payload) == {BASE: 10.0, BASE + 7200: 30.0}


def test_parse_ghi_history_skips_non_numeric() -> None:
    payload = {
        "hourly": {
            "time": [BASE, "bad", BASE + 7200],
            "shortwave_radiation": [10.0, 20.0, "inf"],
        }
    }

    assert parse_ghi_history(payload) == {BASE: 10.0}


def test_parse_ghi_history_rejects_unusable_payloads() -> None:
    with pytest.raises(OpenMeteoError, match="missing hourly"):
        parse_ghi_history("nope")


async def test_fetch_forecast_uses_session_and_parses() -> None:
    session = FakeSession([FakeResponse(_forecast_payload([BASE], [1.5], [200.0]))])

    forecast = await fetch_forecast(session, 59.3, 18.1)

    assert forecast.at(BASE) == HourlyWeather(BASE, 1.5, 200.0)
    assert session.urls[0].startswith(FORECAST_URL)


async def test_fetch_ghi_history_returns_primary_series() -> None:
    session = FakeSession(
        [
            FakeResponse(
                {
                    "hourly": {
                        "time": [BASE],
                        "shortwave_radiation": [50.0],
                    }
                }
            )
        ]
    )

    series = await fetch_ghi_history(
        session, 59.3, 18.1, date(2026, 1, 1), date(2026, 1, 2)
    )

    assert series == {BASE: 50.0}
    assert session.urls[0].startswith(HISTORICAL_FORECAST_URL)


async def test_fetch_ghi_history_falls_back_on_error() -> None:
    session = FakeSession(
        [
            FakeResponse(error=aiohttp.ClientConnectionError("down")),
            FakeResponse(
                {"hourly": {"time": [BASE], "shortwave_radiation": [70.0]}}
            ),
        ]
    )

    series = await fetch_ghi_history(
        session, 59.3, 18.1, date(2026, 1, 1), date(2026, 1, 2)
    )

    assert series == {BASE: 70.0}
    assert session.urls[0].startswith(HISTORICAL_FORECAST_URL)
    assert session.urls[1].startswith(ARCHIVE_URL)


async def test_fetch_ghi_history_falls_back_on_empty_series() -> None:
    session = FakeSession(
        [
            FakeResponse({"hourly": {"time": [], "shortwave_radiation": []}}),
            FakeResponse(
                {"hourly": {"time": [BASE], "shortwave_radiation": [80.0]}}
            ),
        ]
    )

    series = await fetch_ghi_history(
        session, 59.3, 18.1, date(2026, 1, 1), date(2026, 1, 2)
    )

    assert series == {BASE: 80.0}


async def test_fetch_ghi_history_raises_when_both_fail() -> None:
    session = FakeSession(
        [
            FakeResponse(error=aiohttp.ClientConnectionError("a")),
            FakeResponse(error=aiohttp.ClientConnectionError("b")),
        ]
    )

    with pytest.raises(OpenMeteoError, match="unavailable"):
        await fetch_ghi_history(
            session, 59.3, 18.1, date(2026, 1, 1), date(2026, 1, 2)
        )


async def test_get_json_maps_transport_and_payload_errors() -> None:
    session = FakeSession([FakeResponse(error=aiohttp.ClientConnectionError("down"))])
    with pytest.raises(OpenMeteoError, match="ClientConnectionError"):
        await fetch_forecast(session, 0.0, 0.0)

    session = FakeSession([FakeResponse(error=asyncio.TimeoutError())])
    with pytest.raises(OpenMeteoError, match="request failed"):
        await fetch_forecast(session, 0.0, 0.0)

    session = FakeSession([FakeResponse(json_error=ValueError("bad json"))])
    with pytest.raises(OpenMeteoError, match="bad json"):
        await fetch_forecast(session, 0.0, 0.0)

    session = FakeSession([FakeResponse({"error": True, "reason": "rate limited"})])
    with pytest.raises(OpenMeteoError, match="rate limited"):
        await fetch_forecast(session, 0.0, 0.0)

    session = FakeSession([FakeResponse({"error": True})])
    with pytest.raises(OpenMeteoError, match="Open-Meteo error"):
        await fetch_forecast(session, 0.0, 0.0)

    session = FakeSession([FakeResponse(["not", "a", "dict"])])
    with pytest.raises(OpenMeteoError, match="unexpected payload"):
        await fetch_forecast(session, 0.0, 0.0)
