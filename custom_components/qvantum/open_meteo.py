"""Open-Meteo access for the custom heating-curve module.

Fetches hourly outdoor temperature plus shortwave radiation (GHI) for the
near forecast window, and historical GHI for solar-gain calibration. No API
key is required. The caller passes the Home Assistant ``aiohttp`` client
session, so this module stays free of Home Assistant imports and never owns
a session.

Failures are raised as :class:`OpenMeteoError`; callers treat a missing
forecast or history as "no solar term / not ready", never as a reason to
write to the heat pump.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlencode

import aiohttp

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HISTORICAL_FORECAST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

REQUEST_TIMEOUT_S = 20.0
HISTORY_TIMEOUT_S = 40.0
DEFAULT_PAST_HOURS = 6
DEFAULT_FUTURE_HOURS = 12


class OpenMeteoError(RuntimeError):
    """Open-Meteo request failed or the payload was unusable."""


@dataclass(frozen=True)
class HourlyWeather:
    """One hour of outdoor temperature and GHI (UTC hour start)."""

    hour_ts: int
    temperature_c: float
    ghi_wm2: float


@dataclass(frozen=True)
class WeatherForecast:
    """Hourly forecast ordered by ``hour_ts`` (past + future hours)."""

    points: tuple[HourlyWeather, ...]

    def ghi_by_hour(self) -> dict[int, float]:
        return {point.hour_ts: point.ghi_wm2 for point in self.points}

    def temperature_by_hour(self) -> dict[int, float]:
        return {point.hour_ts: point.temperature_c for point in self.points}

    def at(self, hour_ts: int) -> HourlyWeather | None:
        for point in self.points:
            if point.hour_ts == hour_ts:
                return point
        return None


def hour_ts(timestamp: float | int) -> int:
    """Floor a UTC timestamp to its hour start."""
    value = int(timestamp)
    return value - (value % 3600)


def _base_params(latitude: float, longitude: float) -> dict[str, str]:
    return {
        "latitude": f"{latitude:.6f}",
        "longitude": f"{longitude:.6f}",
        "timezone": "UTC",
        "timeformat": "unixtime",
    }


def forecast_url(
    latitude: float,
    longitude: float,
    *,
    past_hours: int = DEFAULT_PAST_HOURS,
    future_hours: int = DEFAULT_FUTURE_HOURS,
) -> str:
    params = {
        **_base_params(latitude, longitude),
        "hourly": "temperature_2m,shortwave_radiation",
        "current": "temperature_2m,shortwave_radiation",
        "past_hours": str(past_hours),
        "forecast_hours": str(future_hours),
    }
    return f"{FORECAST_URL}?{urlencode(params)}"


def history_url(
    base_url: str, latitude: float, longitude: float, start: date, end: date
) -> str:
    params = {
        **_base_params(latitude, longitude),
        "hourly": "shortwave_radiation",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    return f"{base_url}?{urlencode(params)}"


def parse_forecast(payload: Any) -> WeatherForecast:
    """Parse a forecast response; ``current`` overrides its hour if present."""
    hourly = payload.get("hourly") if isinstance(payload, dict) else None
    if not isinstance(hourly, dict):
        raise OpenMeteoError("missing hourly block")

    times = hourly.get("time") or []
    temperatures = hourly.get("temperature_2m") or []
    irradiances = hourly.get("shortwave_radiation") or []
    points: dict[int, HourlyWeather] = {}
    for ts_raw, temperature, ghi in zip(times, temperatures, irradiances):
        if ts_raw is None or temperature is None or ghi is None:
            continue
        ts = hour_ts(ts_raw)
        points[ts] = HourlyWeather(
            hour_ts=ts,
            temperature_c=float(temperature),
            ghi_wm2=max(0.0, float(ghi)),
        )

    current = payload.get("current")
    if isinstance(current, dict) and current.get("time") is not None:
        ts = hour_ts(current["time"])
        existing = points.get(ts)
        if existing is not None:
            ghi = current.get("shortwave_radiation")
            temperature = current.get("temperature_2m")
            points[ts] = HourlyWeather(
                hour_ts=ts,
                temperature_c=(
                    existing.temperature_c
                    if temperature is None
                    else float(temperature)
                ),
                ghi_wm2=existing.ghi_wm2 if ghi is None else max(0.0, float(ghi)),
            )

    if not points:
        raise OpenMeteoError("no hourly forecast values")
    return WeatherForecast(points=tuple(points[key] for key in sorted(points)))


def parse_ghi_history(payload: Any) -> dict[int, float]:
    """Parse a historical GHI response into ``{hour_ts: W/m²}``."""
    hourly = payload.get("hourly") if isinstance(payload, dict) else None
    if not isinstance(hourly, dict):
        raise OpenMeteoError("missing hourly block")

    times = hourly.get("time") or []
    values = hourly.get("shortwave_radiation") or []
    series: dict[int, float] = {}
    for ts_raw, value in zip(times, values):
        if ts_raw is None or value is None:
            continue
        series[hour_ts(ts_raw)] = max(0.0, float(value))
    return series


async def _get_json(
    session: aiohttp.ClientSession, url: str, *, timeout_s: float
) -> Any:
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_s)
        async with session.get(url, timeout=timeout) as response:
            response.raise_for_status()
            payload = await response.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
        raise OpenMeteoError(f"request failed: {err.__class__.__name__}: {err}") from err
    if not isinstance(payload, dict):
        raise OpenMeteoError("unexpected payload type")
    if payload.get("error"):
        raise OpenMeteoError(str(payload.get("reason") or "Open-Meteo error"))
    return payload


async def fetch_forecast(
    session: aiohttp.ClientSession,
    latitude: float,
    longitude: float,
    *,
    past_hours: int = DEFAULT_PAST_HOURS,
    future_hours: int = DEFAULT_FUTURE_HOURS,
) -> WeatherForecast:
    """Fetch hourly temperature + GHI around now (current hour included)."""
    url = forecast_url(
        latitude, longitude, past_hours=past_hours, future_hours=future_hours
    )
    payload = await _get_json(session, url, timeout_s=REQUEST_TIMEOUT_S)
    return parse_forecast(payload)


async def fetch_ghi_history(
    session: aiohttp.ClientSession,
    latitude: float,
    longitude: float,
    start: date,
    end: date,
) -> dict[int, float]:
    """Fetch hourly GHI history, historical-forecast first, archive fallback."""
    last_error: OpenMeteoError | None = None
    for base_url in (HISTORICAL_FORECAST_URL, ARCHIVE_URL):
        url = history_url(base_url, latitude, longitude, start, end)
        try:
            payload = await _get_json(session, url, timeout_s=HISTORY_TIMEOUT_S)
            series = parse_ghi_history(payload)
        except OpenMeteoError as err:
            last_error = err
            continue
        if series:
            return series
        last_error = OpenMeteoError("empty GHI history")
    raise OpenMeteoError(f"GHI history unavailable: {last_error}")
