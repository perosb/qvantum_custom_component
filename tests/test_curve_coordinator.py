"""Tests for the custom heating-curve coordinator (shadow mode)."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, call, patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.qvantum import curve_coordinator as cc
from custom_components.qvantum.const import HP_STATUS_HEATING
from custom_components.qvantum.heating_curve import CurveResult
from custom_components.qvantum.curve_coordinator import (
    INDOOR_MARGIN_CYCLES,
    POWER_CYCLES,
    CurveSnapshot,
    QvantumCurveCoordinator,
    _median_sorted,
    _model_from_state,
    _model_to_state,
    assess_readiness,
    build_samples,
    curve_deviation_c,
    freeze_baseline,
    points_within_limits,
    signal_blocker,
)
from custom_components.qvantum.open_meteo import (
    HourlyWeather,
    OpenMeteoError,
    WeatherForecast,
)
from custom_components.qvantum.solar_gain import SolarModel, SolarSample

CURVE_KEYS = (
    "curve_30",
    "curve_20",
    "curve_10",
    "curve_0",
    "curve_minus_10",
    "curve_minus_20",
    "curve_minus_30",
)
BASELINE = dict(zip(CURVE_KEYS, (25.0, 30.0, 36.0, 42.0, 48.0, 54.0, 60.0)))
SETTINGS = {
    **BASELINE,
    "curve_type_heating": 0,
    "temp_compensation_curve": 20,
}
VALUES = {
    "bt1": 10.0,
    "bt2": 21.0,
    "indoor_temperature_target": 21.0,
    "cal_heat_temp": 37.0,
    "heatingpower": 1500.0,
    "hp_status": HP_STATUS_HEATING,
}


class FakeStore:
    def __init__(self, data=None):
        self.data = data
        self.saved = None

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.saved = data


def make_forecast(*, temperature: float = 10.0, ghi: float = 0.0, hours: int = 7):
    now_hour = int(time.time()) - (int(time.time()) % 3600)
    points = tuple(
        HourlyWeather(now_hour + index * 3600, temperature, ghi)
        for index in range(hours)
    )
    return WeatherForecast(points=points)


def make_coordinator(*, values=None, settings=None, store=None):
    with patch.object(
        QvantumCurveCoordinator, "__init__", lambda self, *args, **kwargs: None
    ):
        coordinator = QvantumCurveCoordinator.__new__(QvantumCurveCoordinator)
    main = MagicMock()
    # Production merges holding-register settings into the values section.
    main.data = {"values": {**(values or {}), **(settings or {})}}
    main.device_id = "test_device_123"
    coordinator._main = main
    coordinator._session = MagicMock()
    coordinator._store = store
    coordinator._baseline = None
    coordinator._baseline_auto = None
    coordinator._curve_23 = None
    coordinator._model = None
    coordinator._calibrated_at = None
    coordinator._last_calibration_ts = None
    coordinator._forecast = None
    coordinator._forecast_ok = False
    coordinator._indoor_margins = deque(maxlen=INDOOR_MARGIN_CYCLES)
    coordinator._powers = deque(maxlen=POWER_CYCLES)
    coordinator._mode = "shadow"
    coordinator._revert_pending = False
    coordinator._control_lock = asyncio.Lock()
    coordinator._listeners = {}
    coordinator.data = None
    coordinator.hass = MagicMock()
    coordinator.hass.config.latitude = 59.3
    coordinator.hass.config.longitude = 18.1
    return coordinator


def make_model(**overrides) -> SolarModel:
    values = dict(
        a_w_per_k=161.0,
        b_m2=0.10,
        c_w=300.0,
        trust=0.2,
        r2_opaque=0.9,
        r2_solar=0.3,
        b_std_err=0.05,
        n_opaque=200,
        n_solar=100,
        valid=True,
    )
    values.update(overrides)
    return SolarModel(**values)


def test_freeze_baseline_complete_and_rejects_bad_values() -> None:
    assert freeze_baseline(SETTINGS) == BASELINE

    missing = {key: value for key, value in BASELINE.items() if key != "curve_0"}
    assert freeze_baseline(missing) is None
    assert freeze_baseline({**BASELINE, "curve_0": "42"}) is None
    assert freeze_baseline({**BASELINE, "curve_0": True}) is None
    assert freeze_baseline({}) is None


def test_build_samples_joins_and_filters() -> None:
    q = {100: 500.0, 200: 30.0, 300: 900.0, 400: 800.0}
    indoor = {100: 20.0, 200: 21.0, 300: 22.0, 400: 21.0}
    outdoor = {100: 10.0, 200: 10.0, 300: 10.0, 400: 17.0}
    ghi = {100: 5.0, 200: 100.0, 300: -2.0, 400: 50.0}

    samples = build_samples(q, indoor, outdoor, ghi)

    # 200 drops for low q, 400 drops for delta_t 4 K < 5 K, 100 kept opaque.
    assert samples == [
        SolarSample(hour_ts=100, delta_t_k=10.0, ghi_wm2=5.0, q_heat_w=500.0),
        SolarSample(hour_ts=300, delta_t_k=12.0, ghi_wm2=0.0, q_heat_w=900.0),
    ]


def test_signal_blocker_priority() -> None:
    assert signal_blocker(has_baseline=False, forecast_ok=True, history_ok=True) == "baseline"
    assert signal_blocker(has_baseline=True, forecast_ok=False, history_ok=True) == "forecast"
    assert signal_blocker(has_baseline=True, forecast_ok=True, history_ok=False) == "history"
    assert signal_blocker(has_baseline=True, forecast_ok=True, history_ok=True) is None


def _hourly_buckets(start_ts: int, now_ts: int, value) -> dict[int, float]:
    return {
        ts: value(ts) if callable(value) else value
        for ts in range(start_ts, now_ts, 3600)
    }


def test_assess_readiness_passes_signal_through() -> None:
    result = assess_readiness({}, 1000, ZoneInfo("UTC"), signal="forecast")

    assert not result.ready
    assert result.blocker == "forecast"


def test_assess_readiness_requires_window_coverage() -> None:
    now = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc).timestamp()
    midnight = datetime(2026, 3, 7, 0, 0, tzinfo=timezone.utc).timestamp()
    deviations = _hourly_buckets(int(midnight), int(now), 0.2)
    # Drop enough of the window to fail coverage.
    for ts in list(deviations)[:20]:
        deviations.pop(ts)

    result = assess_readiness(deviations, int(now), ZoneInfo("UTC"))

    assert not result.ready
    assert result.blocker == "window"
    assert result.window_hours == pytest.approx(84.0)


def test_assess_readiness_ready_and_value_blockers() -> None:
    now = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc).timestamp()
    midnight = datetime(2026, 3, 7, 0, 0, tzinfo=timezone.utc).timestamp()
    window = range(int(midnight), int(now), 3600)

    good = {ts: 0.5 for ts in window}
    result = assess_readiness(good, int(now), ZoneInfo("UTC"))
    assert result.ready
    assert result.blocker is None
    assert result.median_abs_c == pytest.approx(0.5)
    assert result.max_abs_c == pytest.approx(0.5)

    median_fail = {ts: 1.5 for ts in window}
    result = assess_readiness(median_fail, int(now), ZoneInfo("UTC"))
    assert not result.ready
    assert result.blocker == "median"

    max_fail = {ts: 0.5 for ts in window}
    max_fail[int(midnight) + 10 * 3600] = 5.0
    result = assess_readiness(max_fail, int(now), ZoneInfo("UTC"))
    assert not result.ready
    assert result.blocker == "max"


def test_curve_deviation_uses_interpolated_supply() -> None:
    points = {**BASELINE, "curve_10": 39.0}

    assert curve_deviation_c(points, 10.0, 37.5) == pytest.approx(1.5)
    assert curve_deviation_c(points, None, 37.5) is None
    assert curve_deviation_c(points, 10.0, None) is None
    assert curve_deviation_c({"curve_0": 42.0}, 10.0, 37.5) is None


def test_model_state_roundtrip() -> None:
    model = make_model()

    state = _model_to_state(model)
    restored = _model_from_state(state)

    assert restored == model
    assert _model_to_state(None) is None
    assert _model_from_state(None) is None
    assert _model_from_state({"a": 1.0}) is None
    assert _model_from_state({**state, "a": "abc"}) is None


async def test_snapshot_shadow_freezes_baseline_and_reports_blocker() -> None:
    store = FakeStore()
    coordinator = make_coordinator(values=VALUES, settings=SETTINGS, store=store)
    coordinator._async_calibrate = AsyncMock()
    coordinator._async_fetch_forecast = AsyncMock(return_value=make_forecast())
    coordinator._async_deviation_hours = AsyncMock(return_value={})
    coordinator._daylight = MagicMock(return_value=None)

    snapshot = await coordinator._async_compute_snapshot()

    assert coordinator.baseline == BASELINE
    assert store.saved["baseline"] == BASELINE
    assert snapshot.shadow
    assert snapshot.points == BASELINE
    assert snapshot.adjustment_c == 0.0
    assert snapshot.deviation_c == pytest.approx(36.0 - 37.0)
    assert not snapshot.ready
    assert snapshot.blocker == "history"
    assert snapshot.model is None


async def test_snapshot_ready_with_model_and_window() -> None:
    coordinator = make_coordinator(values=VALUES, settings=SETTINGS)
    coordinator._baseline = dict(BASELINE)
    coordinator._model = make_model()
    coordinator._calibrated_at = "2026-03-01T00:00:00+00:00"
    coordinator._async_calibrate = AsyncMock()
    coordinator._async_fetch_forecast = AsyncMock(return_value=make_forecast())
    coordinator._daylight = MagicMock(return_value=None)
    now = time.time()
    midnight = datetime.fromtimestamp(now, timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    start = int((midnight - timedelta(days=3)).timestamp())
    coordinator._async_deviation_hours = AsyncMock(
        return_value={ts: 0.25 for ts in range(start, int(now), 3600)}
    )

    snapshot = await coordinator._async_compute_snapshot()

    assert snapshot.ready
    assert snapshot.blocker is None
    assert snapshot.median_abs_c == pytest.approx(0.25)
    assert snapshot.model is not None


async def test_snapshot_deviates_when_user_defined() -> None:
    coordinator = make_coordinator(
        values=VALUES, settings={**SETTINGS, "curve_type_heating": 1}
    )
    coordinator._async_calibrate = AsyncMock()
    coordinator._async_fetch_forecast = AsyncMock(return_value=make_forecast())
    coordinator._async_deviation_hours = AsyncMock(return_value={})
    coordinator._daylight = MagicMock(return_value=None)

    snapshot = await coordinator._async_compute_snapshot()

    assert not snapshot.shadow


async def test_snapshot_without_baseline_does_not_fetch_forecast() -> None:
    coordinator = make_coordinator(values=VALUES, settings={})
    coordinator._async_calibrate = AsyncMock()
    coordinator._async_fetch_forecast = AsyncMock(return_value=make_forecast())
    coordinator._async_deviation_hours = AsyncMock(return_value={})
    coordinator._daylight = MagicMock(return_value=None)

    snapshot = await coordinator._async_compute_snapshot()

    coordinator._async_fetch_forecast.assert_not_awaited()
    assert snapshot.blocker == "baseline"
    assert snapshot.points == {}


async def test_update_data_degrades_on_failure() -> None:
    coordinator = make_coordinator()

    async def boom():
        raise RuntimeError("boom")

    coordinator._async_compute_snapshot = boom

    snapshot = await coordinator._async_update_data()

    assert snapshot.blocker == "baseline"
    assert snapshot.points == {}


async def test_update_data_returns_last_good_snapshot_on_failure() -> None:
    coordinator = make_coordinator()

    async def good():
        return coordinator._empty_snapshot()

    coordinator._async_compute_snapshot = good
    coordinator.data = await coordinator._async_update_data()

    async def boom():
        raise RuntimeError("boom")

    coordinator._async_compute_snapshot = boom
    snapshot = await coordinator._async_update_data()

    assert snapshot is coordinator.data


async def test_async_restore_loads_state() -> None:
    model = make_model()
    store = FakeStore(
        {
            "baseline": BASELINE,
            "baseline_auto": True,
            "temp_compensation_curve": 20,
            "model": _model_to_state(model),
            "calibrated_at": "2026-03-01T00:00:00+00:00",
            "calibrated_ts": 1_770_000_000.0,
            "indoor_margins": [[1_770_000_000.0, 0.4], ["bad"], [1_770_000_100.0, -0.2]],
        }
    )
    coordinator = make_coordinator(store=store)

    await coordinator.async_restore()

    assert coordinator.baseline == BASELINE
    assert coordinator.model == model
    assert coordinator._calibrated_at == "2026-03-01T00:00:00+00:00"
    assert coordinator._last_calibration_ts == 1_770_000_000.0
    assert list(coordinator._indoor_margins) == [
        (1_770_000_000.0, 0.4),
        (1_770_000_100.0, -0.2),
    ]


async def test_async_restore_ignores_bad_payloads() -> None:
    for payload in (None, "nope", {"baseline": {"curve_0": 1.0}}, {"model": {"a": "x"}}):
        coordinator = make_coordinator(store=FakeStore(payload))
        await coordinator.async_restore()
        assert coordinator.baseline is None
        assert coordinator.model is None


async def test_async_persist_swallows_store_errors() -> None:
    coordinator = make_coordinator(store=FakeStore())

    await coordinator._async_persist()
    assert coordinator._store.saved is not None

    class BrokenStore:
        async def async_save(self, data):
            raise OSError("disk full")

    coordinator._store = BrokenStore()
    await coordinator._async_persist()


async def test_push_power_only_while_heating() -> None:
    coordinator = make_coordinator()

    assert coordinator._push_power({"hp_status": HP_STATUS_HEATING, "heatingpower": 1000.0}) == 1000.0
    assert coordinator._push_power({"hp_status": HP_STATUS_HEATING, "heatingpower": 2000.0}) == 1500.0
    assert coordinator._push_power({"hp_status": 0, "heatingpower": 2000.0}) is None
    assert coordinator._push_power({"hp_status": 0}) is None


async def test_push_indoor_margin_averages_window() -> None:
    coordinator = make_coordinator()
    now = time.time()

    assert coordinator._push_indoor_margin(
        {"bt2": 21.0, "indoor_temperature_target": 20.0}, now
    ) == pytest.approx(1.0)
    assert coordinator._push_indoor_margin(
        {"bt2": 23.0, "indoor_temperature_target": 20.0}, now + 1
    ) == pytest.approx(2.0)
    assert coordinator._push_indoor_margin({}, now + 2) == pytest.approx(2.0)

    # Only the margin actually pushed counts; expired entries drop out.
    assert coordinator._push_indoor_margin(
        {"bt2": 19.0, "indoor_temperature_target": 20.0}, now + 25 * 3600
    ) == pytest.approx(-1.0)


async def test_indoor_metric_key_prefers_sensor_mode() -> None:
    coordinator = make_coordinator(values={"sensor_mode": 4})

    assert (
        coordinator._indoor_metric_key({"room_temp_external": "sensor.x", "bt2": "sensor.y"})
        == "room_temp_external"
    )
    assert coordinator._indoor_metric_key({"bt2": "sensor.y"}) == "bt2"
    assert coordinator._indoor_metric_key({}) is None

    fallback = make_coordinator(values={"use_operation_sensor": 1})
    assert fallback._indoor_metric_key({"bt2": "sensor.y"}) == "bt2"


def test_constructor_wires_dependencies() -> None:
    calls: dict = {}

    def fake_init(self, hass, logger, **kwargs):
        calls.update(kwargs)
        self.data = None

    hass = MagicMock()
    hass.config = MagicMock()
    hass.config.config_dir = None
    config_entry = MagicMock()
    config_entry.unique_id = "entry-1"
    main = MagicMock()
    session = MagicMock()

    with patch.object(cc.DataUpdateCoordinator, "__init__", fake_init):
        coordinator = QvantumCurveCoordinator(
            hass, config_entry, main, session=session
        )

    assert coordinator._main is main
    assert coordinator._session is session
    assert coordinator._store is None
    assert coordinator.baseline is None
    assert calls["update_interval"] == cc.UPDATE_INTERVAL


def test_constructor_builds_store_when_config_dir_present() -> None:
    hass = MagicMock()
    hass.config.config_dir = "/config"
    config_entry = MagicMock()
    config_entry.unique_id = "entry-1"
    config_entry.entry_id = "entry-1"

    with patch.object(cc.DataUpdateCoordinator, "__init__", lambda *a, **k: None):
        coordinator = QvantumCurveCoordinator(hass, config_entry, MagicMock())

    assert coordinator._store is not None


def test_session_property_lazily_creates_client_session() -> None:
    coordinator = make_coordinator()
    coordinator._session = None
    sentinel = object()

    with patch.object(cc, "async_get_clientsession", return_value=sentinel):
        assert coordinator.session is sentinel
        assert coordinator.session is sentinel


async def test_restore_without_store_returns_early() -> None:
    coordinator = make_coordinator(store=None)

    await coordinator.async_restore()

    assert coordinator.baseline is None


async def test_restore_swallows_load_errors() -> None:
    class BrokenStore:
        async def async_load(self):
            raise OSError("no store")

    coordinator = make_coordinator(store=BrokenStore())

    await coordinator.async_restore()

    assert coordinator.baseline is None


async def test_update_data_reraises_cancelled() -> None:
    coordinator = make_coordinator()

    async def cancel():
        raise asyncio.CancelledError()

    coordinator._async_compute_snapshot = cancel

    with pytest.raises(asyncio.CancelledError):
        await coordinator._async_update_data()


async def test_fetch_forecast_uses_location_and_session() -> None:
    coordinator = make_coordinator()
    coordinator.hass.config.latitude = 59.3
    coordinator.hass.config.longitude = 18.1
    forecast = make_forecast()

    with patch.object(cc, "fetch_forecast", AsyncMock(return_value=forecast)) as fetch:
        assert await coordinator._async_fetch_forecast() is forecast

    fetch.assert_awaited_once_with(coordinator.session, 59.3, 18.1)


async def test_fetch_forecast_requires_location() -> None:
    coordinator = make_coordinator()
    coordinator.hass.config.latitude = None
    coordinator.hass.config.longitude = None

    with pytest.raises(OpenMeteoError):
        await coordinator._async_fetch_forecast()


def _solar_maps(now_ts: float, hours: int = 100):
    q: dict[int, float] = {}
    indoor: dict[int, float] = {}
    outdoor: dict[int, float] = {}
    ghi: dict[int, float] = {}
    for index in range(hours):
        ts = int(now_ts) - (hours - index) * 3600
        outdoor[ts] = 0.0
        indoor[ts] = 20.0 + (index % 5)
        ghi[ts] = 0.0 if index % 2 == 0 else 300.0
        q[ts] = 161.0 * indoor[ts] - 0.1 * ghi[ts] + 300.0
    return q, indoor, outdoor, ghi


async def test_calibrate_fits_and_persists_model() -> None:
    now_ts = time.time()
    coordinator = make_coordinator(store=FakeStore())
    q, indoor, outdoor, ghi = _solar_maps(now_ts)
    coordinator._async_hourly_maps = AsyncMock(
        return_value={"heatingpower": q, "indoor": indoor, "outdoor": outdoor}
    )
    with patch.object(cc, "fetch_ghi_history", AsyncMock(return_value=ghi)):
        await coordinator._async_calibrate(now_ts)

    assert coordinator.model is not None
    assert coordinator.model.valid
    assert coordinator._calibrated_at is not None
    assert coordinator._store.saved is not None


async def test_calibrate_skips_when_location_missing() -> None:
    coordinator = make_coordinator()
    coordinator.hass.config.latitude = None
    coordinator.hass.config.longitude = None

    await coordinator._async_calibrate(time.time())

    assert coordinator.model is None


async def test_calibrate_skips_when_history_unavailable() -> None:
    coordinator = make_coordinator()
    with patch.object(
        cc, "fetch_ghi_history", AsyncMock(side_effect=OpenMeteoError("no history"))
    ):
        await coordinator._async_calibrate(time.time())

    assert coordinator.model is None


async def test_calibrate_skips_when_too_few_samples() -> None:
    now_ts = time.time()
    coordinator = make_coordinator()
    q, indoor, outdoor, ghi = _solar_maps(now_ts, hours=5)
    coordinator._async_hourly_maps = AsyncMock(
        return_value={"heatingpower": q, "indoor": indoor, "outdoor": outdoor}
    )
    with patch.object(cc, "fetch_ghi_history", AsyncMock(return_value=ghi)):
        await coordinator._async_calibrate(now_ts)

    assert coordinator.model is None


async def test_calibrate_keeps_previous_model_on_invalid_fit() -> None:
    now_ts = time.time()
    coordinator = make_coordinator()
    previous = make_model()
    coordinator._model = previous
    q = {int(now_ts) - index * 3600: 1000.0 for index in range(100)}
    coordinator._async_hourly_maps = AsyncMock(
        return_value={
            "heatingpower": q,
            "indoor": {ts: 20.0 for ts in q},
            "outdoor": {ts: 5.0 for ts in q},
        }
    )
    with patch.object(cc, "fetch_ghi_history", AsyncMock(return_value={ts: 0.0 for ts in q})):
        await coordinator._async_calibrate(now_ts)

    assert coordinator.model is previous


async def test_calibration_attempt_is_throttled_after_failure() -> None:
    coordinator = make_coordinator(store=FakeStore())
    with patch.object(
        cc, "fetch_ghi_history", AsyncMock(side_effect=OpenMeteoError("down"))
    ):
        await coordinator._async_calibrate(2000.0)

    assert coordinator._last_calibration_ts == 2000.0
    assert not coordinator._calibration_due(2000.0 + 3600.0)
    assert coordinator._calibration_due(
        2000.0 + cc.CALIBRATION_REFRESH_HOURS * 3600.0
    )
    assert coordinator._store.saved is not None


def test_calibration_due_rules() -> None:
    coordinator = make_coordinator()

    assert coordinator._calibration_due(1000.0)
    coordinator._model = make_model()
    assert coordinator._calibration_due(1000.0)
    coordinator._last_calibration_ts = 1000.0
    assert not coordinator._calibration_due(1000.0 + 3600.0)
    assert coordinator._calibration_due(1000.0 + cc.CALIBRATION_REFRESH_HOURS * 3600.0)


def test_resolve_statistic_ids_uses_registry() -> None:
    coordinator = make_coordinator()
    registry = MagicMock()
    registry.async_get_entity_id.side_effect = (
        lambda domain, integration, unique_id: (
            "sensor.qvantum_heatingpower"
            if unique_id.endswith("heatingpower_test_device_123")
            else None
        )
    )

    with patch.object(cc.er, "async_get", return_value=registry):
        resolved = coordinator._resolve_statistic_ids()
        entity_id = coordinator._resolve_curve_entity_id("custom_curve_deviation")

    assert resolved == {"heatingpower": "sensor.qvantum_heatingpower"}
    assert entity_id is None


def test_resolve_without_device_id_returns_empty() -> None:
    coordinator = make_coordinator()
    coordinator._main.device_id = None

    assert coordinator._resolve_statistic_ids() == {}
    assert coordinator._resolve_curve_entity_id("custom_curve_deviation") is None


async def test_statistics_returns_empty_without_ids_or_recorder() -> None:
    coordinator = make_coordinator()
    start = datetime.now(timezone.utc)

    assert await coordinator._async_statistics(set(), start) == {}

    with patch("homeassistant.components.recorder.get_instance", side_effect=KeyError):
        assert await coordinator._async_statistics({"sensor.x"}, start) == {}


async def test_statistics_uses_executor_and_maps_errors() -> None:
    coordinator = make_coordinator()
    start = datetime.now(timezone.utc)
    rows = {"sensor.x": [{"start": 100, "mean": 1.0}]}
    coordinator.hass.async_add_executor_job = AsyncMock(return_value=rows)

    with patch("homeassistant.components.recorder.get_instance", return_value=None):
        assert await coordinator._async_statistics({"sensor.x"}, start) == rows

    coordinator.hass.async_add_executor_job = AsyncMock(side_effect=OSError("db"))
    with patch("homeassistant.components.recorder.get_instance", return_value=None):
        assert await coordinator._async_statistics({"sensor.x"}, start) == {}


async def test_hourly_maps_builds_series_and_indoor() -> None:
    coordinator = make_coordinator(values={"sensor_mode": 1})
    coordinator._resolve_statistic_ids = MagicMock(
        return_value={"heatingpower": "sensor.q", "bt1": "sensor.t", "bt2": "sensor.i"}
    )
    coordinator._async_statistics = AsyncMock(
        return_value={
            "sensor.q": [
                {"start": 100, "mean": 1200.0},
                {"start": 200, "mean": None},
                {"start": None, "mean": 1.0},
            ],
            "sensor.t": [{"start": 100, "mean": 5.0}],
            "sensor.i": [{"start": 100, "mean": 19.0}],
        }
    )

    maps = await coordinator._async_hourly_maps()

    assert maps["heatingpower"] == {100: 1200.0}
    assert maps["bt1"] == {100: 5.0}
    assert maps["outdoor"] == {100: 5.0}
    assert maps["indoor"] == {100: 19.0}


async def test_deviation_hours_reads_sensor_statistics() -> None:
    coordinator = make_coordinator()
    coordinator._resolve_curve_entity_id = MagicMock(return_value="sensor.dev")
    coordinator._async_statistics = AsyncMock(
        return_value={
            "sensor.dev": [
                {"start": 100, "mean": 0.5},
                {"start": None, "mean": 1.0},
                {"start": 200, "mean": None},
            ]
        }
    )

    assert await coordinator._async_deviation_hours() == {100: 0.5}

    coordinator._resolve_curve_entity_id = MagicMock(return_value=None)
    assert await coordinator._async_deviation_hours() == {}


def test_daylight_builds_phase_and_degrades() -> None:
    coordinator = make_coordinator()
    now = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)

    def astral_date(hass, event, day):
        from homeassistant.helpers.sun import SUN_EVENT_SUNRISE

        hour = 6 if event == SUN_EVENT_SUNRISE else 18
        base = datetime(2026, 3, 10, hour, 0, tzinfo=timezone.utc)
        return base + timedelta(days=(day - now.date()).days)

    with patch("homeassistant.helpers.sun.get_astral_event_date", side_effect=astral_date):
        phase = coordinator._daylight(now)

    assert phase is not None
    assert phase.sunrise_ts < phase.sunset_ts

    with patch("homeassistant.helpers.sun.get_astral_event_date", return_value=None):
        assert coordinator._daylight(now) is None

    with patch(
        "homeassistant.helpers.sun.get_astral_event_date",
        side_effect=RuntimeError("no location"),
    ):
        assert coordinator._daylight(now) is None


def test_median_sorted_odd_and_even() -> None:
    assert _median_sorted([1.0, 2.0, 3.0]) == 2.0
    assert _median_sorted([1.0, 2.0, 3.0, 4.0]) == 2.5


def test_push_helpers_return_none_for_empty_inputs() -> None:
    coordinator = make_coordinator()

    assert coordinator._push_indoor_margin({}, time.time()) is None
    assert (
        coordinator._push_power({"hp_status": HP_STATUS_HEATING, "heatingpower": None})
        is None
    )


async def test_snapshot_forecast_failure_blocks_and_falls_back() -> None:
    coordinator = make_coordinator(values=VALUES, settings=SETTINGS)
    coordinator._async_calibrate = AsyncMock()
    coordinator._async_fetch_forecast = AsyncMock(side_effect=OpenMeteoError("down"))
    coordinator._async_deviation_hours = AsyncMock(return_value={})
    coordinator._daylight = MagicMock(return_value=None)

    snapshot = await coordinator._async_compute_snapshot()

    assert snapshot.blocker == "forecast"
    assert snapshot.points == BASELINE


async def test_snapshot_deviation_falls_back_to_forecast_outdoor() -> None:
    values = {key: value for key, value in VALUES.items() if key != "bt1"}
    coordinator = make_coordinator(values=values, settings=SETTINGS)
    coordinator._async_calibrate = AsyncMock()
    coordinator._async_fetch_forecast = AsyncMock(
        return_value=make_forecast(temperature=10.0)
    )
    coordinator._async_deviation_hours = AsyncMock(return_value={})
    coordinator._daylight = MagicMock(return_value=None)

    snapshot = await coordinator._async_compute_snapshot()

    assert snapshot.deviation_c == pytest.approx(36.0 - 37.0)

    coordinator = make_coordinator(
        values={**values, "cal_heat_temp": True}, settings=SETTINGS
    )
    coordinator._async_calibrate = AsyncMock()
    coordinator._async_fetch_forecast = AsyncMock(
        return_value=make_forecast(temperature=10.0)
    )
    coordinator._async_deviation_hours = AsyncMock(return_value={})
    coordinator._daylight = MagicMock(return_value=None)

    snapshot = await coordinator._async_compute_snapshot()

    assert snapshot.deviation_c is None



def make_result(points=None) -> CurveResult:
    chosen = dict(points) if points is not None else dict(BASELINE)
    return CurveResult(
        points=tuple(chosen.items()),
        adjustment_c=0.0,
        outdoor_c=0.0,
        night_day_c=0.0,
        solar_c=0.0,
        load_c=0.0,
        capped_by_indoor=False,
    )

def make_snapshot(points=None, **overrides) -> CurveSnapshot:
    values = dict(
        shadow=True,
        points=dict(points) if points is not None else dict(BASELINE),
        baseline=dict(BASELINE),
        adjustment_c=0.0,
        outdoor_c=0.0,
        night_day_c=0.0,
        solar_c=0.0,
        load_c=0.0,
        capped_by_indoor=False,
        deviation_c=None,
        ready=False,
        blocker=None,
        median_abs_c=None,
        max_abs_c=None,
        window_hours=0.0,
        model=None,
        calibrated_at=None,
    )
    values.update(overrides)
    return CurveSnapshot(**values)


def make_writable_coordinator(*, points=None, settings=None, snapshot=None):
    coordinator = make_coordinator(
        values=VALUES, settings={**SETTINGS, **(settings or {})}
    )
    expected = points if points is not None else BASELINE
    client = MagicMock()
    client.writable = True
    client.set_heating_curve_point = AsyncMock(return_value={"status": "APPLIED"})
    client.set_curve_type_heating = AsyncMock(return_value={"status": "APPLIED"})
    client.set_indoor_temperature_offset = AsyncMock(
        return_value={"status": "APPLIED"}
    )
    client.get_settings = AsyncMock(
        return_value={
            "settings": [
                {"name": key, "value": value} for key, value in expected.items()
            ]
        }
    )
    coordinator._main.client = client
    coordinator._main._process_settings_data.side_effect = lambda payload: {
        item["name"]: item["value"] for item in payload["settings"]
    }
    coordinator._baseline = dict(BASELINE)
    coordinator.data = snapshot or make_snapshot(points)
    return coordinator, client


def test_points_within_limits_validation() -> None:
    assert points_within_limits(BASELINE)
    assert not points_within_limits({**BASELINE, "curve_30": 9})
    assert not points_within_limits({**BASELINE, "curve_minus_30": 81})
    assert not points_within_limits({**BASELINE, "curve_30": 50})
    assert not points_within_limits(
        {key: value for key, value in BASELINE.items() if key != "curve_0"}
    )
    assert not points_within_limits({**BASELINE, "curve_0": True})


async def test_set_control_mode_rejects_unknown() -> None:
    coordinator = make_coordinator()

    with pytest.raises(ValueError):
        await coordinator.async_set_control_mode("on")


async def test_activate_requires_baseline_and_write_access() -> None:
    coordinator, _client = make_writable_coordinator()
    coordinator._baseline = None

    with pytest.raises(HomeAssistantError):
        await coordinator.async_set_control_mode("active")
    assert not coordinator.active

    coordinator, _client = make_writable_coordinator()
    coordinator._main.client.writable = False

    with pytest.raises(HomeAssistantError):
        await coordinator.async_set_control_mode("active")


async def test_shadow_mode_never_touches_pump_settings() -> None:
    coordinator, client = make_writable_coordinator()

    await coordinator.async_set_control_mode("shadow")

    assert not coordinator.active
    assert client.set_curve_type_heating.await_args_list == []
    assert client.set_heating_curve_point.await_args_list == []


async def test_activate_updates_main_values_optimistically() -> None:
    coordinator, _client = make_writable_coordinator()

    with patch.object(cc.asyncio, "sleep", AsyncMock()):
        await coordinator.async_set_control_mode("active")

    values = coordinator._main.data["values"]
    assert values["curve_30"] == 25
    assert values["curve_minus_30"] == 60
    assert values["indoor_temperature_offset"] == 0
    assert values["curve_type_heating"] == 1


async def test_activate_writes_points_offset_then_curve_type() -> None:
    coordinator, client = make_writable_coordinator()

    with patch.object(cc.asyncio, "sleep", AsyncMock()):
        await coordinator.async_set_control_mode("active")

    assert coordinator.active
    point_calls = client.set_heating_curve_point.call_args_list
    assert [call.args[1] for call in point_calls] == list(BASELINE)
    assert [call.args[2] for call in point_calls] == [int(v) for v in BASELINE.values()]
    client.set_indoor_temperature_offset.assert_awaited_once_with(
        "test_device_123", 0
    )
    assert client.set_curve_type_heating.await_args_list[-1].args == (
        "test_device_123",
        1,
    )
    assert len(client.set_curve_type_heating.await_args_list) == 1
    assert coordinator.active



async def test_activate_switches_to_auto_first_when_pump_is_user_defined() -> None:
    coordinator, client = make_writable_coordinator(settings={"curve_type_heating": 1})

    with patch.object(cc.asyncio, "sleep", AsyncMock()):
        await coordinator.async_set_control_mode("active")

    assert client.set_curve_type_heating.await_args_list[0].args == (
        "test_device_123",
        0,
    )
    assert client.set_curve_type_heating.await_args_list[-1].args == (
        "test_device_123",
        1,
    )


async def test_activate_reverts_and_raises_on_write_failure() -> None:
    coordinator, client = make_writable_coordinator()
    client.set_heating_curve_point.side_effect = RuntimeError("transport down")

    with patch.object(cc.asyncio, "sleep", AsyncMock()):
        with pytest.raises(HomeAssistantError):
            await coordinator.async_set_control_mode("active")

    assert not coordinator.active
    assert client.set_curve_type_heating.await_args_list[-1].args == (
        "test_device_123",
        0,
    )


async def test_activate_reverts_on_readback_mismatch() -> None:
    coordinator, client = make_writable_coordinator()
    client.get_settings = AsyncMock(
        return_value={
            "settings": [
                {"name": "curve_30", "value": 99},
                {"name": "curve_20", "value": 30},
            ]
        }
    )

    with patch.object(cc.asyncio, "sleep", AsyncMock()):
        with pytest.raises(HomeAssistantError):
            await coordinator.async_set_control_mode("active")

    assert not coordinator.active
    assert client.set_curve_type_heating.await_args_list[-1].args == (
        "test_device_123",
        0,
    )


async def test_deactivate_writes_auto() -> None:
    coordinator, client = make_writable_coordinator()
    coordinator._mode = "active"

    await coordinator.async_set_control_mode("shadow")

    assert not coordinator.active
    assert client.set_curve_type_heating.await_args_list == [
        call("test_device_123", 0)
    ]


async def test_revert_pending_retries_until_reachable() -> None:
    coordinator, client = make_writable_coordinator()
    coordinator._mode = "active"
    client.writable = False

    await coordinator.async_set_control_mode("shadow")

    assert not coordinator.active
    assert coordinator._revert_pending
    assert client.set_curve_type_heating.await_args_list == []

    client.writable = True
    await coordinator._async_retry_revert()

    assert not coordinator._revert_pending
    assert client.set_curve_type_heating.await_args_list[-1].args == (
        "test_device_123",
        0,
    )


async def test_apply_active_writes_only_changed_points() -> None:
    target = {key: value + 1.0 for key, value in BASELINE.items()}
    current = {**BASELINE, "curve_30": 26.0}
    coordinator, client = make_writable_coordinator(
        points=target, settings={"curve_type_heating": 1, **current}
    )
    coordinator._mode = "active"

    with patch.object(cc.asyncio, "sleep", AsyncMock()):
        await coordinator._async_apply_active(make_result(target))

    written = [call.args[1] for call in client.set_heating_curve_point.call_args_list]
    assert written == list(BASELINE)[1:]
    assert coordinator._main.data["values"]["curve_minus_30"] == 61


async def test_apply_active_without_user_defined_goes_shadow() -> None:
    coordinator, client = make_writable_coordinator(
        settings={"curve_type_heating": 0}
    )
    coordinator._mode = "active"

    await coordinator._async_apply_active(make_result())

    assert not coordinator.active
    assert client.set_heating_curve_point.await_args_list == []


async def test_apply_active_reverts_on_failure() -> None:
    coordinator, client = make_writable_coordinator(settings={"curve_type_heating": 1})
    coordinator._mode = "active"
    client.set_heating_curve_point.side_effect = RuntimeError("gone")
    target = {key: value + 1.0 for key, value in BASELINE.items()}

    with patch.object(cc.asyncio, "sleep", AsyncMock()):
        await coordinator._async_apply_active(make_result(target))

    assert not coordinator.active
    assert client.set_curve_type_heating.await_args_list[-1].args == (
        "test_device_123",
        0,
    )


async def test_apply_active_reverts_on_invalid_points() -> None:
    coordinator, client = make_writable_coordinator(settings={"curve_type_heating": 1})
    coordinator._mode = "active"

    with patch.object(cc.asyncio, "sleep", AsyncMock()):
        await coordinator._async_apply_active(
            make_result(points={**BASELINE, "curve_30": 5})
        )

    assert not coordinator.active
    assert client.set_heating_curve_point.await_args_list == []


async def test_restore_loads_control_mode() -> None:
    store = FakeStore(
        {"baseline": BASELINE, "mode": "active", "revert_pending": True}
    )
    coordinator = make_coordinator(store=store)

    await coordinator.async_restore()

    assert coordinator.active
    assert coordinator._revert_pending

    # Active mode without a restored baseline must fall back to shadow.
    coordinator = make_coordinator(store=FakeStore({"mode": "active"}))
    await coordinator.async_restore()
    assert not coordinator.active


async def test_update_data_retries_pending_revert_on_cycle() -> None:
    coordinator, client = make_writable_coordinator()
    coordinator._revert_pending = True
    coordinator._async_calibrate = AsyncMock()
    coordinator._async_fetch_forecast = AsyncMock(return_value=make_forecast())
    coordinator._async_deviation_hours = AsyncMock(return_value={})
    coordinator._daylight = MagicMock(return_value=None)

    await coordinator._async_compute_snapshot()

    assert not coordinator._revert_pending
    assert client.set_curve_type_heating.await_args_list[-1].args == (
        "test_device_123",
        0,
    )

