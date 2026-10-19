"""Tests for the custom heating-curve coordinator (shadow mode)."""

from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, call, patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.qvantum import curve_coordinator as cc
from custom_components.qvantum.const import HP_STATUS_HEATING
from custom_components.qvantum.heating_curve import (
    ActiveRoomGain,
    COP_FEEDBACK_MAX_C,
    PRECHARGE_MAX_C,
    TOTAL_MAX_C,
    CurveResult,
    interpolate_supply,
    normalize_baseline,
)
from custom_components.qvantum.curve_coordinator import (
    INDOOR_MARGIN_CYCLES,
    POWER_CYCLES,
    READY_MIN_HOURS,
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
    room_error_summary,
    signal_blocker,
)
from custom_components.qvantum.open_meteo import (
    HourlyWeather,
    OpenMeteoError,
    WeatherForecast,
)
from custom_components.qvantum.solar_gain import (
    SolarDailyModel,
    SolarModel,
    SolarSample,
)

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
    coordinator._daily_model = None
    coordinator._calibrated_at = None
    coordinator._last_calibration_ts = None
    coordinator._last_calibration_ok = None
    # Throttle baseline learning off by default; dedicated tests re-enable it.
    coordinator._last_baseline_learn_ts = float("inf")
    coordinator._last_active_ts = None
    coordinator._active_since_ts = None
    coordinator._trims = {}
    coordinator._trim_evidence = {}
    coordinator._last_trim_ts = None
    coordinator._baseline_stats = None
    coordinator._room_gain = None
    coordinator._room_gain_source = None
    coordinator._room_gain_factor = None
    coordinator._room_gain_from_factor = False
    coordinator._room_gain_fitted = None
    coordinator._room_gain_active = None
    coordinator._room_gain_active_ts = None
    coordinator._room_gain_fitted_source = None
    coordinator._clamped_state = False
    coordinator._forecast = None
    coordinator._forecast_ok = False
    coordinator._forecast_ts = None
    coordinator._forecast_reused = False
    coordinator._indoor_margins = deque(maxlen=INDOOR_MARGIN_CYCLES)
    coordinator._powers = deque(maxlen=POWER_CYCLES)
    coordinator._mode = "shadow"
    coordinator._revert_pending = False
    coordinator._control_lock = asyncio.Lock()
    coordinator._terms = {"cop_feedback": False, "precharge": False}
    coordinator._cop_reference = {}
    coordinator._cop_samples = {}
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


def make_daily_model(**overrides) -> SolarDailyModel:
    values = dict(
        a_w_per_k=152.0,
        b_m2=1.9,
        c_w=-600.0,
        trust=0.7,
        r2=0.92,
        n_days=25,
        b_std_err=0.6,
        t_stat=3.0,
        b_first_half_m2=1.7,
        b_second_half_m2=2.1,
        b_jackknife_min_m2=1.5,
        b_jackknife_max_m2=2.2,
        valid=True,
        notes="days=25",
    )
    values.update(overrides)
    return SolarDailyModel(**values)


def test_freeze_baseline_complete_and_rejects_bad_values() -> None:
    assert freeze_baseline(SETTINGS) == BASELINE

    missing = {key: value for key, value in BASELINE.items() if key != "curve_0"}
    assert freeze_baseline(missing) is None
    assert freeze_baseline({**BASELINE, "curve_0": "42"}) is None
    assert freeze_baseline({**BASELINE, "curve_0": True}) is None
    assert freeze_baseline({}) is None


def _curve_observations(offset: float) -> list[tuple[float, float]]:
    base = normalize_baseline(BASELINE)
    return [
        (float(outdoor), interpolate_supply(base, float(outdoor)) + offset)
        for outdoor in range(21)
        for _ in range(2)
    ]


async def test_baseline_observations_joins_bt1_and_supply() -> None:
    coordinator = make_coordinator()
    coordinator._resolve_statistic_ids = MagicMock(
        return_value={"bt1": "sensor.t", "cal_heat_temp": "sensor.s"}
    )
    coordinator._async_statistics = AsyncMock(
        return_value={
            "sensor.t": [{"start": 100, "mean": 5.0}, {"start": 200, "mean": 6.0}],
            "sensor.s": [{"start": 100, "mean": 35.0}, {"start": 300, "mean": 40.0}],
        }
    )

    observations = await coordinator._async_baseline_observations()

    assert observations == [(5.0, 35.0)]
    coordinator._async_statistics.assert_awaited_once()


async def test_baseline_observations_skips_idle_hours() -> None:
    coordinator = make_coordinator()
    coordinator._resolve_statistic_ids = MagicMock(
        return_value={
            "bt1": "sensor.t",
            "cal_heat_temp": "sensor.s",
            "heatingpower": "sensor.q",
        }
    )
    coordinator._async_statistics = AsyncMock(
        return_value={
            "sensor.t": [{"start": 100, "mean": 5.0}, {"start": 200, "mean": 6.0}],
            "sensor.s": [{"start": 100, "mean": 35.0}, {"start": 200, "mean": 36.0}],
            "sensor.q": [{"start": 100, "mean": 900.0}, {"start": 200, "mean": 10.0}],
        }
    )

    observations = await coordinator._async_baseline_observations()

    assert observations == [(5.0, 35.0)]


async def test_baseline_observations_requires_both_series() -> None:
    coordinator = make_coordinator()
    coordinator._resolve_statistic_ids = MagicMock(return_value={"bt1": "sensor.t"})
    coordinator._async_statistics = AsyncMock()

    assert await coordinator._async_baseline_observations() == []
    coordinator._async_statistics.assert_not_awaited()


async def test_baseline_observations_slices_after_active() -> None:
    coordinator = make_coordinator()
    coordinator._resolve_statistic_ids = MagicMock(
        return_value={"bt1": "sensor.t", "cal_heat_temp": "sensor.s"}
    )
    coordinator._async_statistics = AsyncMock(return_value={})
    since = time.time() - 3600.0

    await coordinator._async_baseline_observations(since_ts=since)

    start = coordinator._async_statistics.call_args.args[1]
    assert start.timestamp() == pytest.approx(since, abs=1.0)


async def test_baseline_observations_keeps_full_window_without_active() -> None:
    coordinator = make_coordinator()
    coordinator._resolve_statistic_ids = MagicMock(
        return_value={"bt1": "sensor.t", "cal_heat_temp": "sensor.s"}
    )
    coordinator._async_statistics = AsyncMock(return_value={})

    await coordinator._async_baseline_observations()

    start = coordinator._async_statistics.call_args.args[1]
    assert start.timestamp() == pytest.approx(
        time.time() - cc.BASELINE_LEARN_DAYS * 86400.0, abs=5.0
    )


async def test_learn_baseline_slices_after_active() -> None:
    coordinator = make_coordinator(values=VALUES, settings=SETTINGS)
    coordinator._baseline = dict(BASELINE)
    coordinator._last_active_ts = 1234.5
    coordinator._async_baseline_observations = AsyncMock(return_value=[])
    coordinator._last_baseline_learn_ts = None

    await coordinator._async_learn_baseline({**VALUES, **SETTINGS}, time.time())

    coordinator._async_baseline_observations.assert_awaited_once_with(since_ts=1234.5)


async def test_learn_baseline_corrects_only_while_auto() -> None:
    coordinator = make_coordinator(values=VALUES, settings=SETTINGS)
    coordinator._baseline = dict(BASELINE)
    coordinator._async_baseline_observations = AsyncMock(
        return_value=_curve_observations(-3.0)
    )
    coordinator._last_baseline_learn_ts = None

    await coordinator._async_learn_baseline({**VALUES, **SETTINGS}, 1000.0)

    assert coordinator.baseline["curve_30"] == pytest.approx(22.0)
    assert coordinator.baseline["curve_minus_30"] == pytest.approx(57.0)
    assert coordinator._last_baseline_learn_ts == 1000.0
    coordinator._async_baseline_observations.assert_awaited_once()


async def test_learn_baseline_skips_user_defined_and_active() -> None:
    coordinator = make_coordinator(values=VALUES, settings=SETTINGS)
    coordinator._baseline = dict(BASELINE)
    coordinator._async_baseline_observations = AsyncMock(return_value=[])
    coordinator._last_baseline_learn_ts = None

    await coordinator._async_learn_baseline(
        {**VALUES, **SETTINGS, "curve_type_heating": 1}, 1000.0
    )
    coordinator._async_baseline_observations.assert_not_awaited()

    coordinator._mode = "active"
    await coordinator._async_learn_baseline({**VALUES, **SETTINGS}, 2000.0)
    coordinator._async_baseline_observations.assert_not_awaited()

    coordinator._mode = "shadow"
    coordinator._baseline = None
    await coordinator._async_learn_baseline({**VALUES, **SETTINGS}, 3000.0)
    coordinator._async_baseline_observations.assert_not_awaited()


async def test_learn_baseline_throttles_and_swallows_failures() -> None:
    coordinator = make_coordinator(values=VALUES, settings=SETTINGS)
    coordinator._baseline = dict(BASELINE)
    coordinator._async_baseline_observations = AsyncMock(return_value=[])
    coordinator._last_baseline_learn_ts = None

    await coordinator._async_learn_baseline({**VALUES, **SETTINGS}, 1000.0)
    await coordinator._async_learn_baseline({**VALUES, **SETTINGS}, 1000.0 + 3600.0)
    assert coordinator._async_baseline_observations.await_count == 1

    await coordinator._async_learn_baseline(
        {**VALUES, **SETTINGS}, 1000.0 + cc.BASELINE_LEARN_REFRESH_HOURS * 3600.0
    )
    assert coordinator._async_baseline_observations.await_count == 2

    coordinator._async_baseline_observations = AsyncMock(
        side_effect=RuntimeError("recorder down")
    )
    coordinator._last_baseline_learn_ts = None
    await coordinator._async_learn_baseline({**VALUES, **SETTINGS}, 9000.0)
    assert coordinator.baseline == BASELINE


def _room_statistics(gain: float, *, power: float = 900.0) -> dict:
    base = normalize_baseline(BASELINE)
    rows: dict[str, list] = {
        "sensor.t": [],
        "sensor.s": [],
        "sensor.r": [],
        "sensor.q": [],
    }
    for outdoor in range(21):
        for index, error in enumerate((-0.5, 0.0, 0.5)):
            ts = 1000 + outdoor * 3600 + index
            rows["sensor.t"].append({"start": ts, "mean": float(outdoor)})
            rows["sensor.s"].append(
                {
                    "start": ts,
                    "mean": interpolate_supply(base, float(outdoor)) - gain * error,
                }
            )
            rows["sensor.r"].append({"start": ts, "mean": 20.0 + error})
            rows["sensor.q"].append({"start": ts, "mean": power})
    for series in rows.values():
        series.append({"start": None, "mean": 1.0})
        series.append({"start": 42, "mean": None})
    return rows


async def test_room_gain_identifies_from_statistics() -> None:
    coordinator = make_coordinator(
        values={**VALUES, "sensor_mode": 4, "indoor_temperature_target": 20.0},
        settings=SETTINGS,
    )
    coordinator._baseline = dict(BASELINE)
    coordinator._resolve_statistic_ids = MagicMock(
        return_value={
            "bt1": "sensor.t",
            "cal_heat_temp": "sensor.s",
            "room_temp_external": "sensor.r",
            "heatingpower": "sensor.q",
        }
    )
    coordinator._async_statistics = AsyncMock(return_value=_room_statistics(7.0))

    identified = await coordinator._async_room_gain()

    # The gain travels with the indoor sensor it was fitted against.
    assert identified == (pytest.approx(7.0), "room_temp_external")
    statistic_ids = coordinator._async_statistics.call_args.args[0]
    assert statistic_ids == {"sensor.t", "sensor.s", "sensor.r", "sensor.q"}


async def test_room_gain_skips_idle_hours() -> None:
    coordinator = make_coordinator(
        values={**VALUES, "sensor_mode": 4, "indoor_temperature_target": 20.0},
        settings=SETTINGS,
    )
    coordinator._baseline = dict(BASELINE)
    coordinator._resolve_statistic_ids = MagicMock(
        return_value={
            "bt1": "sensor.t",
            "cal_heat_temp": "sensor.s",
            "room_temp_external": "sensor.r",
            "heatingpower": "sensor.q",
        }
    )
    coordinator._async_statistics = AsyncMock(
        return_value=_room_statistics(7.0, power=1.0)
    )

    assert await coordinator._async_room_gain() is None


async def test_room_gain_requires_baseline_ids_and_target() -> None:
    coordinator = make_coordinator(
        values={**VALUES, "sensor_mode": 4, "indoor_temperature_target": 20.0},
        settings=SETTINGS,
    )
    coordinator._async_statistics = AsyncMock(return_value=_room_statistics(7.0))

    # No baseline yet.
    assert await coordinator._async_room_gain() is None

    coordinator._baseline = dict(BASELINE)
    coordinator._resolve_statistic_ids = MagicMock(return_value={})
    assert await coordinator._async_room_gain() is None
    coordinator._async_statistics.assert_not_awaited()

    coordinator._resolve_statistic_ids = MagicMock(
        return_value={"bt1": "sensor.t", "cal_heat_temp": "sensor.s"}
    )
    assert await coordinator._async_room_gain() is None
    coordinator._async_statistics.assert_not_awaited()

    coordinator._resolve_statistic_ids = MagicMock(
        return_value={
            "bt1": "sensor.t",
            "cal_heat_temp": "sensor.s",
            "room_temp_external": "sensor.r",
        }
    )
    coordinator._main.data["values"].pop("indoor_temperature_target")
    assert await coordinator._async_room_gain() is None
    coordinator._async_statistics.assert_not_awaited()


async def test_refresh_room_gain_persists_and_swallows_errors() -> None:
    store = FakeStore()
    coordinator = make_coordinator(store=store)
    coordinator._async_room_gain = AsyncMock(return_value=(9.0, "bt2"))

    await coordinator._async_refresh_room_gain()

    assert coordinator._room_gain == pytest.approx(9.0)
    assert coordinator._room_gain_source == "bt2"
    assert store.saved["room_gain"] == pytest.approx(9.0)
    assert store.saved["room_gain_source"] == "bt2"

    # An unchanged gain does not write the store again.
    store.saved = None
    await coordinator._async_refresh_room_gain()
    assert store.saved is None

    # A new source for the same gain is worth persisting: it changes which
    # sensor the correction may be applied to.
    coordinator._async_room_gain = AsyncMock(return_value=(9.0, "room_temp_external"))
    await coordinator._async_refresh_room_gain()
    assert coordinator._room_gain_source == "room_temp_external"
    assert store.saved["room_gain_source"] == "room_temp_external"

    # A recorder failure is swallowed and keeps the last gain.
    coordinator._async_room_gain = AsyncMock(side_effect=RuntimeError("recorder down"))
    await coordinator._async_refresh_room_gain()
    assert coordinator._room_gain == pytest.approx(9.0)
    assert coordinator._room_gain_source == "room_temp_external"

    # A missing fit keeps the last gain too.
    coordinator._async_room_gain = AsyncMock(return_value=None)
    await coordinator._async_refresh_room_gain()
    assert coordinator._room_gain == pytest.approx(9.0)


def test_gain_for_requires_the_sensor_it_was_learned_from() -> None:
    coordinator = make_coordinator()
    coordinator._room_gain = 9.0
    coordinator._room_gain_source = "bt2"

    assert coordinator._gain_for("bt2") == pytest.approx(9.0)
    assert coordinator._gain_for("room_temp_external") is None
    assert coordinator._gain_for(None) is None

    coordinator._room_gain_source = None
    assert coordinator._gain_for("bt2") is None


async def test_readiness_room_correction_requires_the_matching_sensor() -> None:
    """A gain from another sensor must not touch this window's deviation."""
    values = {**VALUES, "sensor_mode": 4, "indoor_temperature_target": 20.0}
    hours = [100 + index * 3600 for index in range(cc.INDOOR_METRIC_MIN_HOURS)]

    def build(*, source: str):
        coordinator = make_coordinator(values=values, settings=SETTINGS)
        coordinator._baseline = dict(BASELINE)
        coordinator._room_gain = 9.0
        coordinator._room_gain_source = source
        # Inside an active segment: the correction is only valid there.
        coordinator._mode = "active"
        coordinator._active_since_ts = 0.0
        coordinator._resolve_statistic_ids = MagicMock(
            return_value={
                "bt1": "sensor.t",
                "cal_heat_temp": "sensor.s",
                "heatingpower": "sensor.q",
                "bt2": "sensor.i",
                "room_temp_external": "sensor.r",
            }
        )
        coordinator._async_statistics = AsyncMock(
            return_value={
                "sensor.t": [{"start": ts, "mean": 10.0} for ts in hours],
                "sensor.s": [{"start": ts, "mean": 40.0} for ts in hours],
                "sensor.q": [{"start": ts, "mean": 900.0} for ts in hours],
                # Only BT2 has history, so the picker falls back to it.
                "sensor.i": [{"start": ts, "mean": 21.0} for ts in hours],
            }
        )
        return coordinator

    # The picker falls back to BT2 and the gain came from BT2: corrected.
    series, _ = await build(source="bt2")._async_readiness_hours()
    assert series[100] == pytest.approx(36.0 - 40.0 - 9.0 * 1.0)

    # Learned from the external sensor, which has no history here: the BT2
    # error is left alone rather than scaled by another sensor's gain.
    series, _ = await build(source="room_temp_external")._async_readiness_hours()
    assert series[100] == pytest.approx(36.0 - 40.0)


async def test_readiness_room_correction_only_inside_active_segments() -> None:
    """Only hours the pump followed our table carry the room term.

    On Auto hours the pump's own control already holds it and the learned
    baseline was fitted to that, so subtracting the gain there double-counts:
    measured on the live pump 2026-10-15, applying it to every readiness hour
    moved the median from 1.17 °C to 2.67 °C (gate failure) while the active
    hours alone gave 1.08 °C (pass).
    """
    values = {**VALUES, "sensor_mode": 4, "indoor_temperature_target": 20.0}
    hours = [100 + index * 3600 for index in range(cc.INDOOR_METRIC_MIN_HOURS)]
    raw = pytest.approx(36.0 - 40.0)
    corrected = pytest.approx(36.0 - 40.0 - 9.0 * 1.0)

    def build(*, active_since, last_active):
        coordinator = make_coordinator(values=values, settings=SETTINGS)
        coordinator._baseline = dict(BASELINE)
        coordinator._room_gain = 9.0
        coordinator._room_gain_source = "bt2"
        coordinator._active_since_ts = active_since
        coordinator._last_active_ts = last_active
        coordinator._resolve_statistic_ids = MagicMock(
            return_value={
                "bt1": "sensor.t",
                "cal_heat_temp": "sensor.s",
                "heatingpower": "sensor.q",
                "bt2": "sensor.i",
            }
        )
        coordinator._async_statistics = AsyncMock(
            return_value={
                "sensor.t": [{"start": ts, "mean": 10.0} for ts in hours],
                "sensor.s": [{"start": ts, "mean": 40.0} for ts in hours],
                "sensor.q": [{"start": ts, "mean": 900.0} for ts in hours],
                "sensor.i": [{"start": ts, "mean": 21.0} for ts in hours],
            }
        )
        return coordinator

    async def first(*, active_since, last_active):
        series, _ = await build(
            active_since=active_since, last_active=last_active
        )._async_readiness_hours()
        return series[hours[0]]

    # Never activated: every hour is Auto, exactly like today's behaviour.
    assert await first(active_since=None, last_active=None) == raw
    # Activated after these hours: they are still Auto hours.
    assert await first(active_since=hours[-1] + 3600, last_active=None) == raw
    # Inside the last active segment: the room term is removed.
    assert (
        await first(active_since=hours[0] - 1, last_active=hours[-1] + 1)
        == corrected
    )
    # Deactivated before these hours: Auto again.
    assert (
        await first(active_since=hours[0] - 7200, last_active=hours[0] - 1) == raw
    )


async def test_restore_keeps_a_known_room_gain_source() -> None:
    store = FakeStore({"room_gain": 9.0, "room_gain_source": "bt2"})
    coordinator = make_coordinator(store=store)
    await coordinator.async_restore()
    assert coordinator._room_gain == pytest.approx(9.0)
    assert coordinator._room_gain_source == "bt2"


def configured_values(*, factor=5.0, **overrides):
    values = {
        **VALUES,
        "sensor_mode": 4,
        "room_temp_external": 20.6,
        "indoor_temperature_target": 20.1,
        **overrides,
    }
    if factor is not None:
        values["room_comp_factor"] = factor
    return values


async def test_the_pumps_own_factor_is_the_gain() -> None:
    """The pump applies ``cal = table − factor · (room − target)``."""
    coordinator = make_coordinator(values=configured_values(), settings=SETTINGS)
    coordinator._async_persist = AsyncMock()

    await coordinator._async_sync_room_gain(coordinator._main_values())

    assert coordinator._room_gain == pytest.approx(5.0)
    assert coordinator._room_gain_source == "room_temp_external"
    assert coordinator._room_gain_factor == pytest.approx(5.0)


@pytest.mark.parametrize("factor", [None, 0.0, -2.0, float("nan"), float("inf"), 99.0])
async def test_an_unusable_factor_never_invents_a_correction(factor) -> None:
    """Missing, zero, non-finite or absurd: no correction, as before."""
    coordinator = make_coordinator(
        values=configured_values(factor=factor), settings=SETTINGS
    )
    coordinator._async_persist = AsyncMock()

    await coordinator._async_sync_room_gain(coordinator._main_values())

    assert coordinator._room_gain is None
    assert coordinator._room_gain_source is None


async def test_a_mode_without_an_indoor_reading_has_no_gain() -> None:
    """No reading means no room term, whatever the factor says."""
    coordinator = make_coordinator(
        values=configured_values(sensor_mode=0, bt2=None), settings=SETTINGS
    )
    coordinator._async_persist = AsyncMock()

    await coordinator._async_sync_room_gain(coordinator._main_values())

    # The factor is reported, but nothing is applied.
    assert coordinator._room_gain_factor == pytest.approx(5.0)
    assert coordinator._room_gain is None


async def test_an_unusable_factor_drops_the_gain_it_set() -> None:
    """A factor that turns unusable must not leave its gain applied.

    While the curve is active the Auto-hour fit never runs, so a stale gain
    would stay wrong for as long as the curve runs.
    """
    coordinator = make_coordinator(values=configured_values(), settings=SETTINGS)
    coordinator._async_persist = AsyncMock()
    await coordinator._async_sync_room_gain(coordinator._main_values())
    assert coordinator._room_gain == pytest.approx(5.0)
    assert coordinator._room_gain_from_factor is True

    # The user zeroes the factor in the app.
    coordinator._main.data["values"]["room_comp_factor"] = 0.0
    await coordinator._async_sync_room_gain(coordinator._main_values())

    assert coordinator._room_gain is None
    assert coordinator._room_gain_source is None
    assert coordinator._room_gain_from_factor is False


async def test_an_unusable_factor_keeps_a_fitted_gain() -> None:
    """The fallback fit owns the gain when the pump has no factor at all."""
    coordinator = make_coordinator(
        values=configured_values(factor=None), settings=SETTINGS
    )
    coordinator._async_room_gain = AsyncMock(return_value=(3.0, "bt2"))
    coordinator._async_persist = AsyncMock()
    await coordinator._async_refresh_room_gain(coordinator._main_values())
    assert coordinator._room_gain == pytest.approx(3.0)

    await coordinator._async_sync_room_gain(coordinator._main_values())

    assert coordinator._room_gain == pytest.approx(3.0)
    assert coordinator._room_gain_source == "bt2"


async def test_a_configured_factor_is_not_overwritten_by_the_fit() -> None:
    """The fit runs as a cross-check; the pump's factor stays applied."""
    with_factor = make_coordinator(values=configured_values(), settings=SETTINGS)
    with_factor._async_room_gain = AsyncMock(return_value=(3.0, "bt2"))
    with_factor._async_persist = AsyncMock()

    await with_factor._async_refresh_room_gain(with_factor._main_values())

    # The measured slope is kept beside the applied gain: that pair is what
    # tells us whether the configured factor *is* the gain.
    assert with_factor._room_gain_fitted == pytest.approx(3.0)
    assert with_factor._room_gain_fitted_source == "bt2"
    # Nothing was applied by the fit — the factor owns that.
    assert with_factor._room_gain is None

    without_factor = make_coordinator(
        values=configured_values(factor=None), settings=SETTINGS
    )
    without_factor._async_room_gain = AsyncMock(return_value=(3.0, "bt2"))
    without_factor._async_persist = AsyncMock()

    await without_factor._async_refresh_room_gain(without_factor._main_values())

    assert without_factor._room_gain == pytest.approx(3.0)
    assert without_factor._room_gain_source == "bt2"


async def test_restore_stamps_an_unknown_active_start() -> None:
    """A store from before the field existed must not disable the correction.

    Without a start the segment gate closes for every hour, so the room term
    would never be removed while the curve runs. The stamp is the moment we
    know the pump follows our table; earlier hours stay uncorrected.
    """
    store = FakeStore({"mode": "active", "baseline": BASELINE})
    coordinator = make_coordinator(store=store)

    await coordinator.async_restore()

    assert coordinator.active
    assert isinstance(coordinator._active_since_ts, float)
    assert coordinator._state_payload()["active_since_ts"] == (
        coordinator._active_since_ts
    )
    assert store.saved["active_since_ts"] == coordinator._active_since_ts


async def test_restore_leaves_a_known_active_start_alone() -> None:
    store = FakeStore(
        {"mode": "active", "baseline": BASELINE, "active_since_ts": 1758794400.0}
    )
    coordinator = make_coordinator(store=store)

    await coordinator.async_restore()

    assert coordinator._active_since_ts == pytest.approx(1758794400.0)
    assert store.saved is None


async def test_restore_does_not_stamp_a_shadow_curve() -> None:
    store = FakeStore({"mode": "shadow", "baseline": BASELINE})
    coordinator = make_coordinator(store=store)

    await coordinator.async_restore()

    assert not coordinator.active
    assert coordinator._active_since_ts is None


async def test_active_start_round_trips_through_the_store() -> None:
    store = FakeStore(
        {
            "active_since_ts": 1758794400.0,
            "last_active_ts": None,
            "room_gain": 5.0,
            "room_gain_source": "room_temp_external",
            "room_gain_from_factor": True,
        }
    )
    coordinator = make_coordinator(store=store)

    await coordinator.async_restore()

    assert coordinator._active_since_ts == pytest.approx(1758794400.0)
    assert coordinator._room_gain_from_factor is True
    payload = coordinator._state_payload()
    assert payload["active_since_ts"] == pytest.approx(1758794400.0)
    assert payload["room_gain_from_factor"] is True


async def test_activation_records_when_the_pump_started_following_us() -> None:
    """``_last_active_ts`` alone cannot say which hours were ours."""
    coordinator = make_coordinator()
    assert coordinator._active_since_ts is None

    coordinator._mark_active_started()

    assert isinstance(coordinator._active_since_ts, float)
    assert coordinator._in_active_segment(time.time()) is False
    coordinator._mode = "active"
    assert coordinator._in_active_segment(time.time()) is True
    assert coordinator._in_active_segment(coordinator._active_since_ts - 60) is False

    coordinator._mode = "shadow"
    coordinator._last_active_ts = coordinator._active_since_ts + 3600
    assert coordinator._in_active_segment(coordinator._active_since_ts + 60) is True
    assert coordinator._in_active_segment(coordinator._active_since_ts + 7200) is False

    # An unknown key is ignored rather than carried into the correction.
    for payload in ("nope", 4, None, True):
        coordinator = make_coordinator(
            store=FakeStore({"room_gain": 9.0, "room_gain_source": payload})
        )
        await coordinator.async_restore()
        assert coordinator._room_gain_source is None


def _trim_hours(
    error: float, *, hour: int = 1_000_000, count: int = 8
) -> list[tuple[int, float, float]]:
    return [
        (hour - index * 3600, outdoor, error)
        for outdoor in (-5.0, 5.0)
        for index in range(count)
    ]


LOW_WARM_END = dict(zip(CURVE_KEYS, (16.8, 18.6, 27.5, 35.9, 45.7, 57.5, 59.4)))


async def test_snapshot_lifts_a_stored_baseline_to_the_supply_floor() -> None:
    """A baseline written by an older version must migrate, not stay pinned."""
    values = {**VALUES, "min_heating_supply": 20, "max_heating_supply": 60}
    coordinator = make_coordinator(values=values, settings=SETTINGS)
    coordinator._baseline = dict(LOW_WARM_END)
    coordinator._trims = {"curve_30": 0.4}
    coordinator._async_calibrate = AsyncMock()
    coordinator._async_fetch_forecast = AsyncMock(return_value=make_forecast())
    coordinator._async_readiness_hours = AsyncMock(return_value=({}, set()))
    coordinator._daylight = MagicMock(return_value=None)

    snapshot = await coordinator._async_compute_snapshot()

    assert coordinator.baseline[CURVE_KEYS[0]] == pytest.approx(20.0)
    assert coordinator.baseline[CURVE_KEYS[1]] == pytest.approx(20.0)
    assert coordinator.baseline[CURVE_KEYS[2]] == pytest.approx(27.5)
    assert snapshot.baseline == coordinator.baseline
    # The shape changed, so trims learned against it are stale.
    assert coordinator._trims == {}


async def test_learned_baseline_is_lifted_to_the_supply_floor() -> None:
    coordinator = make_coordinator(
        values={**VALUES, "min_heating_supply": 20},
        settings=SETTINGS,
    )
    coordinator._baseline = dict(BASELINE)
    coordinator._async_baseline_observations = AsyncMock(
        return_value=_curve_observations(-3.0)
    )
    coordinator._last_baseline_learn_ts = None

    with patch.object(cc, "corrected_baseline", return_value=dict(LOW_WARM_END)):
        await coordinator._async_learn_baseline(
            {**VALUES, **SETTINGS, "min_heating_supply": 20}, 1000.0
        )

    assert coordinator.baseline[CURVE_KEYS[0]] == pytest.approx(20.0)
    assert coordinator.baseline[CURVE_KEYS[1]] == pytest.approx(20.0)


def test_lift_baseline_floor_is_idempotent_and_needs_a_baseline() -> None:
    coordinator = make_coordinator(values={"min_heating_supply": 20})
    assert coordinator._lift_baseline_to_floor(coordinator._main_values()) is False

    coordinator._baseline = dict(LOW_WARM_END)
    assert coordinator._lift_baseline_to_floor(coordinator._main_values()) is True
    assert coordinator._lift_baseline_to_floor(coordinator._main_values()) is False
    assert coordinator.baseline[CURVE_KEYS[0]] == pytest.approx(20.0)


def test_supply_bounds_from_values() -> None:
    coordinator = make_coordinator(
        values={"min_heating_supply": 20, "max_heating_supply": 60}
    )
    assert coordinator._supply_bounds(coordinator._main_values()) == (20.0, 60.0)

    fallback = make_coordinator()
    assert fallback._supply_bounds(fallback._main_values()) == (10.0, 80.0)


async def test_trim_observations_join_and_subtract_target() -> None:
    coordinator = make_coordinator(
        values={"bt2": 21.0, "indoor_temperature_target": 20.0}
    )
    coordinator._resolve_statistic_ids = MagicMock(
        return_value={"bt1": "sensor.t", "bt2": "sensor.i"}
    )
    hours = [100 + index * 3600 for index in range(cc.INDOOR_METRIC_MIN_HOURS)]
    coordinator._async_statistics = AsyncMock(
        return_value={
            "sensor.t": [{"start": ts, "mean": 5.0} for ts in hours],
            # One extra indoor hour that has no outdoor partner: dropped.
            "sensor.i": [
                {"start": ts, "mean": 21.0} for ts in [*hours, hours[-1] + 3600]
            ],
        }
    )

    observations = await coordinator._async_trim_observations()

    assert observations == [(ts, 5.0, 1.0) for ts in hours]
    coordinator._async_statistics.assert_awaited_once()


async def test_trim_observations_skip_hours_without_heating() -> None:
    """Hot-water and idle hours cool the rooms without the curve being wrong."""
    coordinator = make_coordinator(
        values={"bt2": 21.0, "indoor_temperature_target": 20.0}
    )
    coordinator._resolve_statistic_ids = MagicMock(
        return_value={
            "bt1": "sensor.t",
            "bt2": "sensor.i",
            "heatingpower": "sensor.q",
        }
    )
    hours = [
        100 + index * 3600 for index in range(cc.INDOOR_METRIC_MIN_HOURS + 1)
    ]
    idle, heating = hours[0], hours[1:]
    coordinator._async_statistics = AsyncMock(
        return_value={
            "sensor.t": [{"start": ts, "mean": 5.0} for ts in hours],
            "sensor.i": [{"start": ts, "mean": 21.0} for ts in hours],
            "sensor.q": [
                {"start": ts, "mean": 0.0 if ts == idle else 900.0}
                for ts in hours
            ],
        }
    )

    observations = await coordinator._async_trim_observations()

    assert [ts for ts, _, _ in observations] == heating
    assert all(error == pytest.approx(1.0) for _, _, error in observations)
    assert coordinator._async_statistics.call_args.args[0] == {
        "sensor.t",
        "sensor.i",
        "sensor.q",
    }


async def test_trim_observations_keep_every_hour_without_power_statistics() -> None:
    """Without heating-power statistics the filter is skipped, not the feature."""
    coordinator = make_coordinator(
        values={"bt2": 21.0, "indoor_temperature_target": 20.0}
    )
    coordinator._resolve_statistic_ids = MagicMock(
        return_value={"bt1": "sensor.t", "bt2": "sensor.i"}
    )
    hours = [
        100 + index * 3600 for index in range(cc.INDOOR_METRIC_MIN_HOURS)
    ]
    coordinator._async_statistics = AsyncMock(
        return_value={
            "sensor.t": [{"start": ts, "mean": 5.0} for ts in hours],
            "sensor.i": [{"start": ts, "mean": 21.0} for ts in hours],
        }
    )

    observations = await coordinator._async_trim_observations()

    assert [ts for ts, _, _ in observations] == hours
    assert coordinator._async_statistics.call_args.args[0] == {
        "sensor.t",
        "sensor.i",
    }


async def test_trims_record_evidence_when_the_fit_is_empty() -> None:
    """One qualifying bucket fits no trims, and the evidence explains why."""
    store = FakeStore()
    coordinator = make_coordinator(values=VALUES, settings=SETTINGS, store=store)
    coordinator._baseline = dict(BASELINE)
    coordinator._mode = "active"
    coordinator._last_trim_ts = None
    coordinator._async_trim_observations = AsyncMock(
        return_value=[(100 + index, 5.0, 0.5) for index in range(6)]
    )

    await coordinator._async_update_trims(make_result(), 1000.0)

    assert coordinator._trims == {}
    assert coordinator._trim_evidence["curve_0"] == 6
    assert coordinator._trim_evidence["curve_10"] == 6
    assert coordinator._trim_evidence["curve_30"] == 0
    # The evidence is persisted so the empty trims stay explainable across a
    # restart.
    assert store.saved["trim_evidence"]["curve_0"] == 6


async def test_trim_evidence_round_trips_through_the_store() -> None:
    store = FakeStore({"trim_evidence": {"curve_0": 9, "nope": 3, "curve_10": True}})
    coordinator = make_coordinator(store=store)

    await coordinator.async_restore()

    assert coordinator._trim_evidence == {"curve_0": 9}


async def test_trim_observations_require_target_and_indoor() -> None:
    coordinator = make_coordinator(values={"bt2": 21.0})
    coordinator._resolve_statistic_ids = MagicMock(
        return_value={"bt1": "sensor.t", "bt2": "sensor.i"}
    )
    assert await coordinator._async_trim_observations() == []

    coordinator = make_coordinator(values={"indoor_temperature_target": 20.0})
    coordinator._resolve_statistic_ids = MagicMock(return_value={"bt1": "sensor.t"})
    assert await coordinator._async_trim_observations() == []


async def test_update_trims_applies_damped_step() -> None:
    coordinator = make_coordinator(values=VALUES, settings=SETTINGS)
    coordinator._baseline = dict(BASELINE)
    coordinator._mode = "active"
    coordinator._async_trim_observations = AsyncMock(return_value=_trim_hours(-1.0))
    coordinator._last_trim_ts = None

    await coordinator._async_update_trims(make_result(), 1_000_000.0)

    assert coordinator._trims["curve_10"] == pytest.approx(
        cc.TRIM_UPDATE_GAIN * 0.6
    )
    assert coordinator._trims["curve_minus_10"] == pytest.approx(
        cc.TRIM_UPDATE_GAIN * 0.6
    )
    assert coordinator._last_trim_ts == 1_000_000.0


async def test_update_trims_skips_inactive_capped_and_throttled() -> None:
    coordinator = make_coordinator(values=VALUES, settings=SETTINGS)
    coordinator._baseline = dict(BASELINE)
    coordinator._async_trim_observations = AsyncMock(return_value=_trim_hours(-1.0))
    coordinator._last_trim_ts = None

    await coordinator._async_update_trims(make_result(), 1000.0)
    coordinator._async_trim_observations.assert_not_awaited()

    coordinator._mode = "active"
    await coordinator._async_update_trims(
        replace(make_result(), capped_by_indoor=True), 2000.0
    )
    coordinator._async_trim_observations.assert_not_awaited()

    await coordinator._async_update_trims(
        replace(make_result(), adjustment_c=TOTAL_MAX_C), 3000.0
    )
    coordinator._async_trim_observations.assert_not_awaited()

    await coordinator._async_update_trims(make_result(), 4000.0)
    assert coordinator._async_trim_observations.await_count == 1
    await coordinator._async_update_trims(make_result(), 4000.0 + 3600.0)
    assert coordinator._async_trim_observations.await_count == 1
    await coordinator._async_update_trims(
        make_result(), 4000.0 + cc.TRIM_REFRESH_HOURS * 3600.0
    )
    assert coordinator._async_trim_observations.await_count == 2


async def test_update_trims_retries_sooner_until_the_first_trims_exist() -> None:
    """The evidence for the first trims is already in the window."""
    coordinator = make_coordinator(values=VALUES, settings=SETTINGS)
    coordinator._baseline = dict(BASELINE)
    coordinator._mode = "active"
    coordinator._async_trim_observations = AsyncMock(return_value=[])
    coordinator._last_trim_ts = None

    await coordinator._async_update_trims(make_result(), 1000.0)
    assert coordinator._async_trim_observations.await_count == 1

    # Still inside the short cadence.
    await coordinator._async_update_trims(make_result(), 1000.0 + 3600.0)
    assert coordinator._async_trim_observations.await_count == 1

    # No trims yet, so the next attempt does not wait a full day.
    await coordinator._async_update_trims(
        make_result(), 1000.0 + cc.TRIM_FIRST_REFRESH_HOURS * 3600.0
    )
    assert coordinator._async_trim_observations.await_count == 2

    # Once a trim exists the proven daily step takes over.
    coordinator._trims = {"curve_0": 0.5}
    last = 1000.0 + cc.TRIM_FIRST_REFRESH_HOURS * 3600.0
    await coordinator._async_update_trims(
        make_result(), last + cc.TRIM_FIRST_REFRESH_HOURS * 3600.0
    )
    assert coordinator._async_trim_observations.await_count == 2
    await coordinator._async_update_trims(
        make_result(), last + cc.TRIM_REFRESH_HOURS * 3600.0
    )
    assert coordinator._async_trim_observations.await_count == 3


async def test_update_trims_swallows_failures_and_empty_residual() -> None:
    coordinator = make_coordinator(values=VALUES, settings=SETTINGS)
    coordinator._baseline = dict(BASELINE)
    coordinator._mode = "active"
    coordinator._last_trim_ts = None
    coordinator._async_trim_observations = AsyncMock(
        side_effect=RuntimeError("recorder down")
    )

    await coordinator._async_update_trims(make_result(), 1000.0)

    assert coordinator._trims == {}
    assert coordinator._last_trim_ts == 1000.0

    coordinator._last_trim_ts = None
    coordinator._async_trim_observations = AsyncMock(return_value=_trim_hours(0.0))
    await coordinator._async_update_trims(make_result(), 2000.0)

    assert coordinator._trims == {}

    # Already at the cap in the same direction: no change, no write.
    coordinator._last_trim_ts = None
    coordinator._trims = {key: cc.TRIM_MAX_C for key in BASELINE}
    coordinator._async_trim_observations = AsyncMock(return_value=_trim_hours(-10.0))
    await coordinator._async_update_trims(make_result(), 3000.0)

    assert all(value == cc.TRIM_MAX_C for value in coordinator._trims.values())
    assert coordinator._last_trim_ts == 3000.0


async def test_learn_baseline_clears_trims_only_when_corrected() -> None:
    coordinator = make_coordinator(values=VALUES, settings=SETTINGS)
    coordinator._baseline = dict(BASELINE)
    coordinator._trims = {"curve_0": 0.5}
    coordinator._async_baseline_observations = AsyncMock(
        return_value=_curve_observations(-3.0)
    )
    coordinator._last_baseline_learn_ts = None

    await coordinator._async_learn_baseline({**VALUES, **SETTINGS}, 1000.0)

    assert coordinator._trims == {}
    assert coordinator._baseline_stats == {
        "hours": 42,
        "min_c": 0.0,
        "max_c": 20.0,
        "corrected": True,
    }

    coordinator._trims = {"curve_0": 0.5}
    coordinator._baseline = dict(BASELINE)
    coordinator._async_baseline_observations = AsyncMock(
        return_value=_curve_observations(-0.2)
    )
    coordinator._last_baseline_learn_ts = None
    await coordinator._async_learn_baseline({**VALUES, **SETTINGS}, 2000.0)

    assert coordinator._trims == {"curve_0": 0.5}
    assert coordinator._baseline_stats["corrected"] is False


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
    assert result.p95_abs_c == pytest.approx(0.5)

    median_fail = {ts: 1.6 for ts in window}
    result = assess_readiness(median_fail, int(now), ZoneInfo("UTC"))
    assert not result.ready
    assert result.blocker == "median"

    # A single mode-switch hour must not fail the upper-bound gate.
    transient = {ts: 0.5 for ts in window}
    transient[int(midnight) + 10 * 3600] = 5.0
    assert assess_readiness(transient, int(now), ZoneInfo("UTC")).ready

    # A systematic tail does.
    tail = {ts: 0.5 for ts in window}
    for ts in list(tail)[:10]:
        tail[ts] = 5.0
    result = assess_readiness(tail, int(now), ZoneInfo("UTC"))
    assert not result.ready
    assert result.blocker == "max"
    assert result.p95_abs_c == pytest.approx(5.0)


def test_assess_readiness_coverage_uses_covered_hours() -> None:
    now = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc).timestamp()
    midnight = datetime(2026, 3, 7, 0, 0, tzinfo=timezone.utc).timestamp()
    window = {ts: 0.5 for ts in range(int(midnight), int(now), 3600)}
    # Heating-only sample: a third of the window, but the recorder covered it.
    heating = dict(list(window.items())[:28])

    result = assess_readiness(
        heating, int(now), ZoneInfo("UTC"), covered_hours=set(window)
    )
    assert result.ready

    # Too few heating samples is still a window blocker.
    result = assess_readiness(
        dict(list(window.items())[:10]),
        int(now),
        ZoneInfo("UTC"),
        covered_hours=set(window),
    )
    assert not result.ready
    assert result.blocker == "window"

    # Coverage counts only hours inside the window.
    before = set(range(int(midnight) - 48 * 3600, int(midnight), 3600))
    result = assess_readiness(
        heating, int(now), ZoneInfo("UTC"), covered_hours=before
    )
    assert not result.ready
    assert result.blocker == "window"


def test_room_error_summary_reports_median_and_p90() -> None:
    now = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)
    midnight = datetime(2026, 3, 7, 0, 0, tzinfo=timezone.utc)
    window = list(range(int(midnight.timestamp()), int(now.timestamp()), 3600))
    tz = ZoneInfo("UTC")

    flat = {ts: 0.5 for ts in window}
    summary = room_error_summary(flat, int(now.timestamp()), tz)
    assert summary is not None
    assert summary.median_abs_c == pytest.approx(0.5)
    assert summary.median_c == pytest.approx(0.5)
    assert summary.p90_abs_c == pytest.approx(0.5)
    assert summary.samples == len(window)

    # The signed median keeps the direction; p90 works on magnitude.
    split = len(window) * 2 // 3
    mixed = {
        ts: (-0.5 if index < split else 1.5) for index, ts in enumerate(window)
    }
    summary = room_error_summary(mixed, int(now.timestamp()), tz)
    assert summary.median_c == pytest.approx(-0.5)
    assert summary.median_abs_c == pytest.approx(0.5)
    assert summary.p90_abs_c == pytest.approx(1.5)


def test_room_error_summary_needs_enough_usable_hours() -> None:
    now = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)
    midnight = datetime(2026, 3, 7, 0, 0, tzinfo=timezone.utc)
    window = list(range(int(midnight.timestamp()), int(now.timestamp()), 3600))
    tz = ZoneInfo("UTC")

    assert room_error_summary({}, int(now.timestamp()), tz) is None
    thin = {ts: 0.1 for ts in window[: READY_MIN_HOURS - 1]}
    assert room_error_summary(thin, int(now.timestamp()), tz) is None

    # Hours outside the window and non-finite values are dropped, not treated
    # as zero error.
    stale = {int(midnight.timestamp()) - index * 3600: 5.0 for index in range(200)}
    stale.update({ts: float("nan") for ts in window})
    assert room_error_summary(stale, int(now.timestamp()), tz) is None


async def test_room_error_hours_subtracts_the_target() -> None:
    coordinator = make_coordinator(
        values={"sensor_mode": 4, "indoor_temperature_target": 20.0}
    )
    coordinator._resolve_statistic_ids = MagicMock(
        return_value={"room_temp_external": "sensor.r", "bt2": "sensor.i"}
    )
    rows = [
        {"start": 100 + index * 3600, "mean": 20.5}
        for index in range(cc.INDOOR_METRIC_MIN_HOURS)
    ]
    coordinator._async_statistics = AsyncMock(return_value={"sensor.r": rows})

    errors, source = await coordinator._async_room_error_hours()

    assert source == "room_temp_external"
    assert len(errors) == cc.INDOOR_METRIC_MIN_HOURS
    assert errors[100] == pytest.approx(0.5)
    coordinator._async_statistics.assert_awaited_once()


async def test_room_error_hours_requires_target_and_sensor() -> None:
    coordinator = make_coordinator(values={"indoor_temperature_target": 20.0})
    coordinator._async_statistics = AsyncMock()

    coordinator._resolve_statistic_ids = MagicMock(return_value={})
    assert await coordinator._async_room_error_hours() == ({}, None)

    coordinator._resolve_statistic_ids = MagicMock(
        return_value={"room_temp_external": "sensor.r"}
    )
    coordinator._main.data["values"].pop("indoor_temperature_target")
    assert await coordinator._async_room_error_hours() == ({}, None)

    coordinator._async_statistics.assert_not_awaited()


async def test_snapshot_reports_room_tracking() -> None:
    coordinator = make_coordinator(values=VALUES, settings=SETTINGS)
    coordinator._baseline = dict(BASELINE)
    coordinator._async_calibrate = AsyncMock()
    coordinator._async_fetch_forecast = AsyncMock(return_value=make_forecast())
    coordinator._async_readiness_hours = AsyncMock(return_value=({}, set()))
    now = time.time()
    midnight = datetime.fromtimestamp(now, timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    window = range(int((midnight - timedelta(days=3)).timestamp()), int(now), 3600)
    coordinator._async_room_error_hours = AsyncMock(
        return_value=({ts: -0.25 for ts in window}, "bt2")
    )
    coordinator._daylight = MagicMock(return_value=None)

    snapshot = await coordinator._async_compute_snapshot()

    assert snapshot.room_error_median_abs_c == pytest.approx(0.25)
    assert snapshot.room_error_median_c == pytest.approx(-0.25)
    assert snapshot.room_error_p90_abs_c == pytest.approx(0.25)
    assert snapshot.room_error_source == "bt2"
    assert snapshot.room_error_samples == len(list(window))


async def test_snapshot_lists_extrapolated_points() -> None:
    """Only the observed outdoor range is validated; the rest is extrapolated."""
    coordinator = make_coordinator(values=VALUES, settings=SETTINGS)
    coordinator._baseline = dict(BASELINE)
    coordinator._baseline_stats = {
        "hours": 148,
        "min_c": 3.4,
        "max_c": 14.1,
        "corrected": False,
    }
    coordinator._async_calibrate = AsyncMock()
    coordinator._async_fetch_forecast = AsyncMock(return_value=make_forecast())
    coordinator._async_readiness_hours = AsyncMock(return_value=({}, set()))
    coordinator._async_room_error_hours = AsyncMock(return_value=({}, None))
    coordinator._daylight = MagicMock(return_value=None)

    snapshot = await coordinator._async_compute_snapshot()

    assert snapshot.baseline_outdoor_min_c == pytest.approx(3.4)
    assert snapshot.baseline_outdoor_max_c == pytest.approx(14.1)
    # Only curve_10 (10 °C) falls inside the observed 3.4–14.1 °C.
    assert snapshot.extrapolated_points == (
        "curve_30",
        "curve_20",
        "curve_0",
        "curve_minus_10",
        "curve_minus_20",
        "curve_minus_30",
    )


async def test_snapshot_without_a_baseline_fit_claims_no_extrapolation() -> None:
    """Nothing has been observed yet, so no point is singled out."""
    coordinator = make_coordinator(values=VALUES, settings=SETTINGS)
    coordinator._baseline = dict(BASELINE)
    coordinator._async_calibrate = AsyncMock()
    coordinator._async_fetch_forecast = AsyncMock(return_value=make_forecast())
    coordinator._async_readiness_hours = AsyncMock(return_value=({}, set()))
    coordinator._async_room_error_hours = AsyncMock(return_value=({}, None))
    coordinator._daylight = MagicMock(return_value=None)

    snapshot = await coordinator._async_compute_snapshot()

    assert snapshot.extrapolated_points == ()


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
    coordinator._async_readiness_hours = AsyncMock(return_value=({}, set()))
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
    assert snapshot.baseline_auto is True
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
    window = {ts: 0.25 for ts in range(start, int(now), 3600)}
    coordinator._async_readiness_hours = AsyncMock(return_value=(window, set(window)))

    snapshot = await coordinator._async_compute_snapshot()

    assert snapshot.ready
    assert snapshot.blocker is None
    assert snapshot.median_abs_c == pytest.approx(0.25)
    assert snapshot.p95_abs_c == pytest.approx(0.25)
    assert snapshot.model is not None


async def test_snapshot_deviates_when_user_defined() -> None:
    coordinator = make_coordinator(
        values=VALUES, settings={**SETTINGS, "curve_type_heating": 1}
    )
    coordinator._async_calibrate = AsyncMock()
    coordinator._async_fetch_forecast = AsyncMock(return_value=make_forecast())
    coordinator._async_readiness_hours = AsyncMock(return_value=({}, set()))
    coordinator._daylight = MagicMock(return_value=None)

    snapshot = await coordinator._async_compute_snapshot()

    assert not snapshot.shadow


async def test_snapshot_without_baseline_does_not_fetch_forecast() -> None:
    coordinator = make_coordinator(values=VALUES, settings={})
    coordinator._async_calibrate = AsyncMock()
    coordinator._async_fetch_forecast = AsyncMock(return_value=make_forecast())
    coordinator._async_readiness_hours = AsyncMock(return_value=({}, set()))
    coordinator._daylight = MagicMock(return_value=None)

    snapshot = await coordinator._async_compute_snapshot()

    coordinator._async_fetch_forecast.assert_not_awaited()
    assert snapshot.blocker == "baseline"
    assert snapshot.points == {}


async def test_snapshot_learns_baseline_while_auto() -> None:
    coordinator = make_coordinator(values=VALUES, settings=SETTINGS)
    coordinator._async_calibrate = AsyncMock()
    coordinator._async_fetch_forecast = AsyncMock(return_value=make_forecast())
    coordinator._async_readiness_hours = AsyncMock(return_value=({}, set()))
    coordinator._async_baseline_observations = AsyncMock(
        return_value=_curve_observations(-3.0)
    )
    coordinator._last_baseline_learn_ts = None
    coordinator._daylight = MagicMock(return_value=None)

    snapshot = await coordinator._async_compute_snapshot()

    assert coordinator.baseline["curve_30"] == pytest.approx(22.0)
    assert snapshot.baseline == coordinator.baseline
    assert snapshot.baseline_auto is True


async def test_snapshot_clamps_to_pump_supply_limits() -> None:
    values = {**VALUES, "min_heating_supply": 20, "max_heating_supply": 45}
    coordinator = make_coordinator(values=values, settings=SETTINGS)
    coordinator._async_calibrate = AsyncMock()
    coordinator._async_fetch_forecast = AsyncMock(return_value=make_forecast())
    coordinator._async_readiness_hours = AsyncMock(return_value=({}, set()))
    coordinator._daylight = MagicMock(return_value=None)

    snapshot = await coordinator._async_compute_snapshot()

    assert snapshot.clamped
    # The snapshot names the points the pump's limits bit, so the adjustment
    # sensor and a support dump can point at the baseline knot to move.
    assert snapshot.clamped_points
    assert set(snapshot.clamped_points) <= set(snapshot.points)
    assert snapshot.trims == {}
    assert all(20.0 <= value <= 45.0 for value in snapshot.points.values())


async def test_snapshot_active_records_clamp() -> None:
    coordinator, _client = make_writable_coordinator(
        settings={"curve_type_heating": 1}, live_curve_type=1
    )
    coordinator._main.data["values"]["max_heating_supply"] = 45
    coordinator._mode = "active"
    coordinator._async_calibrate = AsyncMock()
    coordinator._async_fetch_forecast = AsyncMock(return_value=make_forecast())
    coordinator._async_readiness_hours = AsyncMock(return_value=({}, set()))
    coordinator._async_trim_observations = AsyncMock(return_value=[])
    coordinator._daylight = MagicMock(return_value=None)

    with patch.object(cc.asyncio, "sleep", AsyncMock()):
        snapshot = await coordinator._async_compute_snapshot()

    assert snapshot.clamped
    assert snapshot.clamped_points
    assert coordinator._clamped_state is True


async def test_snapshot_trim_update_serializes_with_deactivation() -> None:
    coordinator, _client = make_writable_coordinator(
        settings={"curve_type_heating": 1}, live_curve_type=1
    )
    coordinator._mode = "active"
    coordinator._last_trim_ts = None
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_observations():
        started.set()
        await release.wait()
        return _trim_hours(-1.0)

    coordinator._async_trim_observations = slow_observations
    coordinator._async_calibrate = AsyncMock()
    coordinator._async_fetch_forecast = AsyncMock(return_value=make_forecast())
    coordinator._async_readiness_hours = AsyncMock(return_value=({}, set()))
    coordinator._daylight = MagicMock(return_value=None)

    with patch.object(cc.asyncio, "sleep", AsyncMock()):
        snapshot_task = asyncio.create_task(coordinator._async_compute_snapshot())
        await started.wait()
        revert_task = asyncio.create_task(
            coordinator.async_set_control_mode("shadow")
        )
        await asyncio.sleep(0)
        release.set()
        await snapshot_task
        await revert_task

    assert not coordinator.active
    assert coordinator._trims == {}


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
            "last_active_ts": 1_770_000_500.0,
            "trims": {"curve_0": 0.5, "not_a_point": 1.0, "curve_10": True},
            "baseline_stats": {
                "hours": 90,
                "min_c": -5.0,
                "max_c": 8.0,
                "corrected": True,
            },
            "indoor_margins": [[1_770_000_000.0, 0.4], ["bad"], [1_770_000_100.0, -0.2]],
            "room_gain": 8.6,
        }
    )
    coordinator = make_coordinator(store=store)

    await coordinator.async_restore()

    assert coordinator.baseline == BASELINE
    assert coordinator.model == model
    assert coordinator._calibrated_at == "2026-03-01T00:00:00+00:00"
    assert coordinator._last_calibration_ts == 1_770_000_000.0
    assert coordinator._last_active_ts == 1_770_000_500.0
    assert coordinator._trims == {"curve_0": 0.5}
    assert coordinator._baseline_stats == {
        "hours": 90,
        "min_c": -5.0,
        "max_c": 8.0,
        "corrected": True,
    }
    assert list(coordinator._indoor_margins) == [
        (1_770_000_000.0, 0.4),
        (1_770_000_100.0, -0.2),
    ]
    assert coordinator._room_gain == pytest.approx(8.6)


async def test_async_restore_ignores_bad_payloads() -> None:
    for payload in (None, "nope", {"baseline": {"curve_0": 1.0}}, {"model": {"a": "x"}}):
        coordinator = make_coordinator(store=FakeStore(payload))
        await coordinator.async_restore()
        assert coordinator.baseline is None
        assert coordinator.model is None
        assert coordinator._room_gain is None

    coordinator = make_coordinator(store=FakeStore({"room_gain": "nope"}))
    await coordinator.async_restore()
    assert coordinator._room_gain is None


async def test_curve_store_key_is_namespaced_per_entry() -> None:
    assert cc.curve_store_key("abc") == "qvantum.curve.abc"


async def test_stored_curve_mode_reads_the_curve_store() -> None:
    entry = MagicMock()
    entry.entry_id = "abc"

    for payload, expected in (
        ({"mode": "active"}, "active"),
        ({"mode": "shadow"}, "shadow"),
        ({"mode": "bogus"}, None),
        ({}, None),
        (None, None),
        ("nope", None),
    ):
        store = MagicMock()
        store.async_load = AsyncMock(return_value=payload)
        with patch.object(cc, "Store", return_value=store):
            assert await cc.async_stored_curve_mode(MagicMock(), entry) == expected

    # An unreadable store must not fail removal.
    store = MagicMock()
    store.async_load = AsyncMock(side_effect=RuntimeError("disk"))
    with patch.object(cc, "Store", return_value=store):
        assert await cc.async_stored_curve_mode(MagicMock(), entry) is None

    store.async_load = AsyncMock(side_effect=asyncio.CancelledError())
    with patch.object(cc, "Store", return_value=store):
        with pytest.raises(asyncio.CancelledError):
            await cc.async_stored_curve_mode(MagicMock(), entry)


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


async def test_indoor_series_prefers_the_configured_sensor() -> None:
    coordinator = make_coordinator(values={"sensor_mode": 4})
    resolved = {"room_temp_external": "sensor.x", "bt2": "sensor.y"}
    rows = lambda mean, count: [{"start": index, "mean": mean} for index in range(count)]
    enough = cc.INDOOR_METRIC_MIN_HOURS

    key, series = coordinator._indoor_series(
        resolved,
        {"sensor.x": rows(19.0, enough), "sensor.y": rows(21.0, enough)},
    )
    assert key == "room_temp_external"
    assert series[0] == pytest.approx(19.0)

    # A freshly added sensor has no history yet: keep the previous reading.
    key, series = coordinator._indoor_series(
        resolved, {"sensor.x": rows(19.0, 3), "sensor.y": rows(21.0, enough)}
    )
    assert key == "bt2"
    assert series[0] == pytest.approx(21.0)

    # Nothing has enough rows: no indoor series at all.
    key, series = coordinator._indoor_series(
        resolved, {"sensor.x": rows(19.0, 3), "sensor.y": rows(21.0, 3)}
    )
    assert key is None
    assert series == {}

    # A missing sensor_mode falls back to BT2.
    fallback = make_coordinator(values={"use_operation_sensor": 1})
    key, _ = fallback._indoor_series(
        {"bt2": "sensor.y"}, {"sensor.y": rows(21.0, enough)}
    )
    assert key == "bt2"

    # No resolvable indoor metric at all.
    key, series = coordinator._indoor_series({}, {})
    assert key is None
    assert series == {}


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


async def test_refresh_forecast_keeps_last_good_payload_on_error() -> None:
    """A sporadic 503 must not discard a forecast that still covers now."""
    coordinator = make_coordinator()
    good = make_forecast()
    now = time.time()
    coordinator._async_fetch_forecast = AsyncMock(
        side_effect=[good, OpenMeteoError("503")]
    )

    await coordinator._async_refresh_forecast(now)
    assert coordinator._forecast_ok is True
    assert coordinator._forecast is good
    assert coordinator._forecast_reused is False

    # 15 minutes later the same payload is still the best available.
    await coordinator._async_refresh_forecast(now + 900)
    assert coordinator._forecast_ok is True
    assert coordinator._forecast is good
    assert coordinator._forecast_reused is True


async def test_refresh_forecast_drops_payload_that_no_longer_covers_now() -> None:
    """Past the covered hours the cached payload describes the wrong time."""
    coordinator = make_coordinator()
    now = time.time()
    stale = make_forecast(hours=1)
    coordinator._async_fetch_forecast = AsyncMock(
        side_effect=[stale, OpenMeteoError("503")]
    )

    await coordinator._async_refresh_forecast(now)
    await coordinator._async_refresh_forecast(now + 3 * 3600)

    assert coordinator._forecast_ok is False
    assert coordinator._forecast is None


async def test_refresh_forecast_drops_payload_older_than_reuse_window() -> None:
    """Age alone disqualifies a payload, even if its hours happen to line up."""
    coordinator = make_coordinator()
    now = time.time()
    coordinator._async_fetch_forecast = AsyncMock(
        side_effect=[make_forecast(), OpenMeteoError("503")]
    )

    await coordinator._async_refresh_forecast(now)
    await coordinator._async_refresh_forecast(
        now + (cc.FORECAST_REUSE_HOURS + 0.5) * 3600
    )

    assert coordinator._forecast_ok is False
    assert coordinator._forecast is None


async def test_refresh_forecast_without_previous_payload_stays_blocked() -> None:
    coordinator = make_coordinator()
    coordinator._async_fetch_forecast = AsyncMock(side_effect=OpenMeteoError("503"))

    await coordinator._async_refresh_forecast(time.time())

    assert coordinator._forecast_ok is False
    assert coordinator._forecast is None
    assert coordinator._forecast_reused is False


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


async def test_failed_fetch_is_retried_within_the_hour() -> None:
    """One transient network failure must not cost a day of staleness."""
    coordinator = make_coordinator(store=FakeStore())
    with patch.object(
        cc, "fetch_ghi_history", AsyncMock(side_effect=OpenMeteoError("down"))
    ):
        await coordinator._async_calibrate(2000.0)

    assert coordinator._last_calibration_ts == 2000.0
    assert coordinator._last_calibration_ok is False
    # Not on the next 15-minute cycle ...
    assert not coordinator._calibration_due(2000.0 + 900.0)
    # ... but an hour later, not a day.
    assert coordinator._calibration_due(
        2000.0 + cc.CALIBRATION_RETRY_HOURS * 3600.0
    )
    assert not coordinator._calibration_due(
        2000.0 + cc.CALIBRATION_RETRY_HOURS * 3600.0 - 60.0
    )
    assert coordinator._store.saved is not None
    assert coordinator._store.saved["calibration_ok"] is False


async def test_completed_attempt_waits_the_full_refresh_interval() -> None:
    now_ts = time.time()
    coordinator = make_coordinator(store=FakeStore())
    q = {int(now_ts) - index * 3600: 1000.0 for index in range(100)}
    coordinator._async_hourly_maps = AsyncMock(
        return_value={
            "heatingpower": q,
            "indoor": {ts: 20.0 for ts in q},
            "outdoor": {ts: 5.0 for ts in q},
        }
    )
    with patch.object(
        cc, "fetch_ghi_history", AsyncMock(return_value={ts: 0.0 for ts in q})
    ):
        await coordinator._async_calibrate(now_ts)

    # A rejected fit is a statement about the window, not a retryable error.
    assert coordinator._last_calibration_ok is True
    assert not coordinator._calibration_due(now_ts + 3600.0)
    assert coordinator._calibration_due(
        now_ts + cc.CALIBRATION_REFRESH_HOURS * 3600.0
    )
    assert coordinator._store.saved["calibration_ok"] is True


async def test_calibration_retry_flag_round_trips_through_the_store() -> None:
    coordinator = make_coordinator(store=FakeStore())
    with patch.object(
        cc, "fetch_ghi_history", AsyncMock(side_effect=OpenMeteoError("down"))
    ):
        await coordinator._async_calibrate(2000.0)
    payload = coordinator._store.saved

    restored = make_coordinator(store=FakeStore(data=payload))
    await restored.async_restore()

    assert restored._last_calibration_ok is False
    assert restored._calibration_due(2000.0 + 900.0) is False
    assert restored._calibration_due(2000.0 + 3600.0) is True


async def test_missing_location_keeps_the_daily_cadence() -> None:
    """An unconfigured location is permanent, so it must not retry hourly."""
    coordinator = make_coordinator(store=FakeStore())
    coordinator.hass.config.latitude = None
    coordinator.hass.config.longitude = None

    await coordinator._async_calibrate(2000.0)

    assert coordinator._last_calibration_ok is None
    assert not coordinator._calibration_due(2000.0 + 3600.0)
    assert coordinator._calibration_due(
        2000.0 + cc.CALIBRATION_REFRESH_HOURS * 3600.0
    )


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

    with patch(
        "custom_components.qvantum.statistics.er.async_get", return_value=registry
    ):
        resolved = coordinator._resolve_statistic_ids()
        entity_id = coordinator._resolve_curve_entity_id("adaptive_curve_deviation")

    assert resolved == {"heatingpower": "sensor.qvantum_heatingpower"}
    assert entity_id is None


def test_resolve_without_device_id_returns_empty() -> None:
    coordinator = make_coordinator()
    coordinator._main.device_id = None

    assert coordinator._resolve_statistic_ids() == {}
    assert coordinator._resolve_curve_entity_id("adaptive_curve_deviation") is None


async def test_statistics_returns_empty_without_ids_or_recorder() -> None:
    coordinator = make_coordinator()
    start = datetime.now(timezone.utc)

    assert await coordinator._async_statistics(set(), start) == {}

    with patch("homeassistant.components.recorder.get_instance", side_effect=KeyError):
        assert await coordinator._async_statistics({"sensor.x"}, start) == {}


async def test_statistics_uses_recorder_executor_and_maps_errors() -> None:
    coordinator = make_coordinator()
    start = datetime.now(timezone.utc)
    rows = {"sensor.x": [{"start": 100, "mean": 1.0}]}
    recorder = MagicMock()
    recorder.async_add_executor_job = AsyncMock(return_value=rows)

    with patch(
        "homeassistant.components.recorder.get_instance", return_value=recorder
    ):
        assert await coordinator._async_statistics({"sensor.x"}, start) == rows

    recorder.async_add_executor_job = AsyncMock(side_effect=OSError("db"))
    with patch(
        "homeassistant.components.recorder.get_instance", return_value=recorder
    ):
        assert await coordinator._async_statistics({"sensor.x"}, start) == {}


async def test_hourly_maps_builds_series_and_indoor_fallback() -> None:
    coordinator = make_coordinator(values={"sensor_mode": 4})
    coordinator._resolve_statistic_ids = MagicMock(
        return_value={
            "heatingpower": "sensor.q",
            "bt1": "sensor.t",
            "room_temp_external": "sensor.x",
            "bt2": "sensor.i",
        }
    )
    old = [
        {"start": 100 + index * 3600, "mean": 19.0}
        for index in range(cc.MIN_CALIBRATION_SAMPLES)
    ]
    coordinator._async_statistics = AsyncMock(
        return_value={
            "sensor.q": [
                {"start": 100, "mean": 1200.0},
                {"start": 200, "mean": None},
                {"start": None, "mean": 1.0},
            ],
            "sensor.t": [{"start": 100, "mean": 5.0}],
            # The configured external sensor is brand new; BT2 carries the fit.
            "sensor.x": [{"start": 100, "mean": 20.5}],
            "sensor.i": list(old),
        }
    )

    maps = await coordinator._async_hourly_maps()

    assert maps["heatingpower"] == {100: 1200.0}
    assert maps["bt1"] == {100: 5.0}
    assert maps["outdoor"] == {100: 5.0}
    assert maps["room_temp_external"] == {100: 20.5}
    assert maps["indoor"] == {row["start"]: 19.0 for row in old}


async def test_readiness_hours_uses_baseline_on_heating_hours() -> None:
    coordinator = make_coordinator(values={**VALUES, "sensor_mode": 4})
    coordinator._baseline = dict(BASELINE)
    coordinator._resolve_statistic_ids = MagicMock(
        return_value={
            "bt1": "sensor.t",
            "cal_heat_temp": "sensor.s",
            "heatingpower": "sensor.q",
            "room_temp_external": "sensor.r",
        }
    )
    coordinator._async_statistics = AsyncMock(
        return_value={
            "sensor.t": [{"start": 100, "mean": 10.0}],
            "sensor.s": [{"start": 100, "mean": 37.5}],
            "sensor.q": [{"start": 100, "mean": 900.0}],
            "sensor.r": [{"start": 100, "mean": 21.1}],
        }
    )

    series, covered = await coordinator._async_readiness_hours()

    # Baseline (not the adjusted shadow) minus the pump: 36.0 − 37.5.
    assert series == {100: pytest.approx(-1.5)}
    assert covered == {100}


async def test_readiness_hours_skips_idle_and_missing_inputs() -> None:
    coordinator = make_coordinator(values={**VALUES, "sensor_mode": 4})
    coordinator._baseline = dict(BASELINE)
    coordinator._resolve_statistic_ids = MagicMock(
        return_value={
            "bt1": "sensor.t",
            "cal_heat_temp": "sensor.s",
            "heatingpower": "sensor.q",
        }
    )
    coordinator._async_statistics = AsyncMock(
        return_value={
            "sensor.t": [{"start": 100, "mean": 10.0}, {"start": 200, "mean": 10.0}],
            "sensor.s": [{"start": 100, "mean": 37.5}, {"start": 200, "mean": 37.5}],
            "sensor.q": [{"start": 100, "mean": 900.0}, {"start": 200, "mean": 1.0}],
        }
    )

    series, covered = await coordinator._async_readiness_hours()

    assert series == {100: pytest.approx(-1.5)}
    assert covered == {100, 200}

    coordinator._baseline = None
    assert await coordinator._async_readiness_hours() == ({}, set())

    coordinator._baseline = dict(BASELINE)
    coordinator._resolve_statistic_ids = MagicMock(return_value={"bt1": "sensor.t"})
    assert await coordinator._async_readiness_hours() == ({}, set())

    # An incomplete baseline is rejected rather than raising into the poll.
    coordinator._baseline = {"curve_0": 42.0}
    coordinator._resolve_statistic_ids = MagicMock(
        return_value={"bt1": "sensor.t", "cal_heat_temp": "sensor.s"}
    )
    assert await coordinator._async_readiness_hours() == ({}, set())


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
    coordinator._async_readiness_hours = AsyncMock(return_value=({}, set()))
    coordinator._daylight = MagicMock(return_value=None)

    snapshot = await coordinator._async_compute_snapshot()

    assert snapshot.blocker == "forecast"
    assert snapshot.points == BASELINE


async def test_snapshot_keeps_readiness_when_forecast_refetch_fails() -> None:
    """One failed refetch must not blank a window built on a usable forecast."""
    coordinator = make_coordinator(values=VALUES, settings=SETTINGS)
    coordinator._baseline = dict(BASELINE)
    coordinator._model = make_model()
    coordinator._async_calibrate = AsyncMock()
    now = time.time()
    midnight = datetime.fromtimestamp(now, timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    window = range(int((midnight - timedelta(days=3)).timestamp()), int(now), 3600)
    coordinator._async_readiness_hours = AsyncMock(
        return_value=({ts: 0.5 for ts in window}, set(window))
    )
    coordinator._async_room_error_hours = AsyncMock(return_value=({}, None))
    coordinator._daylight = MagicMock(return_value=None)
    coordinator._async_fetch_forecast = AsyncMock(
        side_effect=[make_forecast(), OpenMeteoError("503")]
    )

    first = await coordinator._async_compute_snapshot()
    second = await coordinator._async_compute_snapshot()

    assert first.blocker is None
    assert first.ready is True
    # The second cycle's request failed but the payload still covers now, so
    # the readiness window survives instead of degrading to blocker=forecast.
    assert second.blocker is None
    assert second.ready is True
    assert second.median_abs_c == first.median_abs_c
    assert coordinator._forecast_reused is True


async def test_snapshot_deviation_removes_room_term() -> None:
    """Active hours are the only ones where the term means the same thing."""
    coordinator, _client = make_writable_coordinator(
        settings={"curve_type_heating": 1}, live_curve_type=1
    )
    coordinator._main.data["values"].update(
        {
            "sensor_mode": 4,
            "room_temp_external": 20.6,
            "indoor_temperature_target": 20.1,
        }
    )
    coordinator._baseline = dict(BASELINE)
    coordinator._room_gain = 9.0
    coordinator._room_gain_source = "room_temp_external"
    coordinator._mode = "active"
    coordinator._active_since_ts = time.time() - 3600
    coordinator._async_calibrate = AsyncMock()
    coordinator._async_fetch_forecast = AsyncMock(return_value=make_forecast())
    coordinator._async_readiness_hours = AsyncMock(return_value=({}, set()))
    coordinator._async_trim_observations = AsyncMock(return_value=[])
    coordinator._daylight = MagicMock(return_value=None)

    with patch.object(cc.asyncio, "sleep", AsyncMock()):
        snapshot = await coordinator._async_compute_snapshot()

    # Raw deviation is 36.0 − 37.0; the 0.5 °C room error at 9 °C/°C is removed.
    assert snapshot.deviation_c == pytest.approx(36.0 - 37.0 - 9.0 * 0.5)
    assert snapshot.room_gain_c == pytest.approx(9.0)


async def test_snapshot_arms_the_indoor_cap_with_a_room_reading() -> None:
    """The cap can only brake the curve when an indoor reading exists."""
    coordinator = make_coordinator(values=VALUES, settings=SETTINGS)
    coordinator._baseline = dict(BASELINE)
    coordinator._async_calibrate = AsyncMock()
    coordinator._async_fetch_forecast = AsyncMock(return_value=make_forecast())
    coordinator._async_readiness_hours = AsyncMock(return_value=({}, set()))
    coordinator._daylight = MagicMock(return_value=None)

    snapshot = await coordinator._async_compute_snapshot()

    assert snapshot.indoor_cap_armed is True
    # BT2 reads 21.0 and the target is 21.0.
    assert snapshot.indoor_margin_c == pytest.approx(0.0)


async def test_snapshot_leaves_the_indoor_cap_inert_without_a_reading() -> None:
    """No indoor sensor means no warm-house brake at all, and it is reported."""
    values = {**VALUES, "bt2": None, "sensor_mode": 0}
    coordinator = make_coordinator(values=values, settings=SETTINGS)
    coordinator._baseline = dict(BASELINE)
    coordinator._async_calibrate = AsyncMock()
    coordinator._async_fetch_forecast = AsyncMock(return_value=make_forecast())
    coordinator._async_readiness_hours = AsyncMock(return_value=({}, set()))
    coordinator._daylight = MagicMock(return_value=None)

    snapshot = await coordinator._async_compute_snapshot()

    assert snapshot.indoor_cap_armed is False
    assert snapshot.indoor_margin_c is None


async def test_snapshot_deviation_keeps_the_room_term_while_on_auto() -> None:
    """The pump's own curve already holds the term there."""
    values = {
        **VALUES,
        "sensor_mode": 4,
        "room_temp_external": 20.6,
        "indoor_temperature_target": 20.1,
    }
    coordinator = make_coordinator(values=values, settings=SETTINGS)
    coordinator._baseline = dict(BASELINE)
    coordinator._room_gain = 9.0
    coordinator._room_gain_source = "room_temp_external"
    # Activation is on record, but the pump was put back on Auto later.
    coordinator._active_since_ts = time.time() - 7200
    coordinator._last_active_ts = time.time() - 3600
    coordinator._async_calibrate = AsyncMock()
    coordinator._async_fetch_forecast = AsyncMock(return_value=make_forecast())
    coordinator._async_readiness_hours = AsyncMock(return_value=({}, set()))
    coordinator._daylight = MagicMock(return_value=None)

    snapshot = await coordinator._async_compute_snapshot()

    assert snapshot.deviation_c == pytest.approx(36.0 - 37.0)


async def test_snapshot_deviation_skips_a_gain_from_another_sensor() -> None:
    """A gain learned from BT2 must not be applied to the external sensor."""
    values = {
        **VALUES,
        "sensor_mode": 4,
        "room_temp_external": 20.6,
        "indoor_temperature_target": 20.1,
    }
    coordinator = make_coordinator(values=values, settings=SETTINGS)
    coordinator._baseline = dict(BASELINE)
    coordinator._room_gain = 9.0
    coordinator._room_gain_source = "bt2"
    coordinator._async_calibrate = AsyncMock()
    coordinator._async_fetch_forecast = AsyncMock(return_value=make_forecast())
    coordinator._async_readiness_hours = AsyncMock(return_value=({}, set()))
    coordinator._daylight = MagicMock(return_value=None)

    snapshot = await coordinator._async_compute_snapshot()

    # Uncorrected, rather than subtracting BT2's room term from the external
    # sensor's error. The gain is still reported for diagnostics.
    assert snapshot.deviation_c == pytest.approx(36.0 - 37.0)
    assert snapshot.room_gain_c == pytest.approx(9.0)


async def test_snapshot_deviation_uncorrected_without_gain() -> None:
    values = {
        **VALUES,
        "sensor_mode": 4,
        "room_temp_external": 20.6,
        "indoor_temperature_target": 20.1,
    }
    coordinator = make_coordinator(values=values, settings=SETTINGS)
    coordinator._baseline = dict(BASELINE)
    coordinator._async_calibrate = AsyncMock()
    coordinator._async_fetch_forecast = AsyncMock(return_value=make_forecast())
    coordinator._async_readiness_hours = AsyncMock(return_value=({}, set()))
    coordinator._daylight = MagicMock(return_value=None)

    snapshot = await coordinator._async_compute_snapshot()

    assert snapshot.deviation_c == pytest.approx(36.0 - 37.0)
    assert snapshot.room_gain_c is None


async def test_snapshot_deviation_falls_back_to_forecast_outdoor() -> None:
    values = {key: value for key, value in VALUES.items() if key != "bt1"}
    coordinator = make_coordinator(values=values, settings=SETTINGS)
    coordinator._async_calibrate = AsyncMock()
    coordinator._async_fetch_forecast = AsyncMock(
        return_value=make_forecast(temperature=10.0)
    )
    coordinator._async_readiness_hours = AsyncMock(return_value=({}, set()))
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
    coordinator._async_readiness_hours = AsyncMock(return_value=({}, set()))
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
        baseline_auto=False,
    )
    values.update(overrides)
    return CurveSnapshot(**values)


def make_writable_coordinator(
    *, points=None, settings=None, snapshot=None, live_curve_type=None
):
    merged = {**SETTINGS, **(settings or {})}
    coordinator = make_coordinator(values=VALUES, settings=merged)
    expected = points if points is not None else BASELINE
    curve_type = (
        live_curve_type
        if live_curve_type is not None
        else merged["curve_type_heating"]
    )
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
            + [{"name": "curve_type_heating", "value": curve_type}]
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

    # Effective pump limits are tighter than the register range.
    assert not points_within_limits(BASELINE, max_supply_c=45.0)
    assert points_within_limits(BASELINE, min_supply_c=20.0, max_supply_c=60.0)
    assert not points_within_limits(BASELINE, min_supply_c=50.0)


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
    # The room correction may only touch hours from here on.
    assert isinstance(coordinator._active_since_ts, float)
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



async def test_activate_reads_live_curve_type_not_the_cache() -> None:
    coordinator, client = make_writable_coordinator(live_curve_type=1)

    with patch.object(cc.asyncio, "sleep", AsyncMock()):
        await coordinator.async_set_control_mode("active")

    assert client.set_curve_type_heating.await_args_list[0].args == (
        "test_device_123",
        0,
    )
    assert coordinator.active


async def test_activate_falls_back_to_cached_curve_type_on_read_failure() -> None:
    coordinator, client = make_writable_coordinator()
    readback = client.get_settings.return_value
    client.get_settings = AsyncMock(side_effect=[RuntimeError("offline"), readback])

    with patch.object(cc.asyncio, "sleep", AsyncMock()):
        await coordinator.async_set_control_mode("active")

    # Cached Auto skips the Auto-first write; activation still completes.
    assert client.set_curve_type_heating.await_args_list[-1].args == (
        "test_device_123",
        1,
    )
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


async def test_activation_clears_stale_revert_latch() -> None:
    coordinator, client = make_writable_coordinator()
    coordinator._revert_pending = True

    with patch.object(cc.asyncio, "sleep", AsyncMock()):
        await coordinator.async_set_control_mode("active")

    assert coordinator.active
    assert not coordinator._revert_pending

    client.set_curve_type_heating.reset_mock()
    await coordinator._async_retry_revert()
    client.set_curve_type_heating.assert_not_awaited()


async def test_deactivate_writes_auto() -> None:
    coordinator, client = make_writable_coordinator()
    coordinator._mode = "active"

    await coordinator.async_set_control_mode("shadow")

    assert not coordinator.active
    assert client.set_curve_type_heating.await_args_list == [
        call("test_device_123", 0)
    ]


async def test_revert_records_last_active() -> None:
    coordinator, _client = make_writable_coordinator()
    coordinator._mode = "active"
    coordinator._trims = {"curve_0": 0.5}
    coordinator._last_trim_ts = 42.0

    before = time.time()
    await coordinator.async_set_control_mode("shadow")

    assert coordinator._last_active_ts is not None
    assert coordinator._last_active_ts >= before
    assert coordinator._trims == {}
    assert coordinator._last_trim_ts is None


async def test_revert_from_shadow_does_not_record_last_active() -> None:
    coordinator, _client = make_writable_coordinator()
    assert not coordinator.active

    await coordinator._async_revert()

    assert coordinator._last_active_ts is None


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


async def test_apply_active_demotion_records_last_active() -> None:
    coordinator, _client = make_writable_coordinator(
        settings={"curve_type_heating": 0}
    )
    coordinator._mode = "active"
    coordinator._last_active_ts = None
    coordinator._trims = {"curve_0": 0.5}
    coordinator._last_trim_ts = 42.0

    before = time.time()
    await coordinator._async_apply_active(make_result())

    assert not coordinator.active
    assert coordinator._last_active_ts is not None
    assert coordinator._last_active_ts >= before
    assert coordinator._trims == {}
    assert coordinator._last_trim_ts is None


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
    coordinator._async_readiness_hours = AsyncMock(return_value=({}, set()))
    coordinator._daylight = MagicMock(return_value=None)

    await coordinator._async_compute_snapshot()

    assert not coordinator._revert_pending
    assert client.set_curve_type_heating.await_args_list[-1].args == (
        "test_device_123",
        0,
    )


class TestCopFeedback:
    """COP-feedback term: opt-in, active-only, reference-learning."""

    @staticmethod
    def _values(coordinator, *, hp_status=HP_STATUS_HEATING, cop=2.0, outdoor=0.0):
        coordinator._main.data = {
            "values": {
                "hp_status": hp_status,
                "cop_heating": cop,
                "bt1": outdoor,
            }
        }
        return coordinator._main.data["values"]

    @staticmethod
    def _armed(coordinator):
        coordinator._terms["cop_feedback"] = True
        coordinator._mode = "active"
        coordinator._cop_reference = {0: 4.0}
        coordinator._cop_samples = {0: cc.COP_REFERENCE_MIN_SAMPLES}

    def test_learns_reference_without_applying(self):
        coordinator = make_coordinator()
        values = self._values(coordinator, cop=4.0)

        term = coordinator._cop_feedback_c(values, make_snapshot())

        assert term == 0.0
        assert coordinator._cop_reference == {0: 4.0}
        assert coordinator._cop_samples == {0: 1}

    def test_applies_once_enabled_active_and_learned(self):
        coordinator = make_coordinator()
        self._armed(coordinator)
        values = self._values(coordinator, cop=2.8)

        term = coordinator._cop_feedback_c(values, make_snapshot())

        assert term == pytest.approx(-COP_FEEDBACK_MAX_C)
        # A correcting cycle freezes the reference (stable loop target).
        assert coordinator._cop_reference[0] == 4.0
        assert coordinator._cop_samples[0] == cc.COP_REFERENCE_MIN_SAMPLES

    def test_requires_flag_active_mode_and_enough_samples(self):
        for mutate in (
            lambda c: c._terms.__setitem__("cop_feedback", False),
            lambda c: setattr(c, "_mode", "shadow"),
            lambda c: c._cop_samples.__setitem__(0, 1),
        ):
            coordinator = make_coordinator()
            self._armed(coordinator)
            mutate(coordinator)
            values = self._values(coordinator, cop=2.0)
            assert coordinator._cop_feedback_c(values, make_snapshot()) == 0.0

    def test_never_applies_when_not_heating(self):
        coordinator = make_coordinator()
        self._armed(coordinator)
        values = self._values(coordinator, hp_status=0, cop=2.0)

        assert coordinator._cop_feedback_c(values, make_snapshot()) == 0.0

    def test_skips_when_indoor_cap_or_total_clamp_active(self):
        coordinator = make_coordinator()
        self._armed(coordinator)
        values = self._values(coordinator, cop=2.0)

        capped = make_snapshot(capped_by_indoor=True)
        clamped = make_snapshot(adjustment_c=TOTAL_MAX_C)
        ok = make_snapshot()

        assert coordinator._cop_feedback_c(values, capped) == 0.0
        assert coordinator._cop_feedback_c(values, clamped) == 0.0
        assert coordinator._cop_feedback_c(values, ok) < 0.0

    def test_ignores_invalid_inputs(self):
        coordinator = make_coordinator()
        self._armed(coordinator)

        assert (
            coordinator._cop_feedback_c(
                {"hp_status": HP_STATUS_HEATING, "bt1": 0.0}, make_snapshot()
            )
            == 0.0
        )
        assert (
            coordinator._cop_feedback_c(
                {"hp_status": HP_STATUS_HEATING, "cop_heating": 2.0},
                make_snapshot(),
            )
            == 0.0
        )

    @pytest.mark.asyncio
    async def test_set_terms_persists_flags(self):
        store = FakeStore()
        coordinator = make_coordinator(store=store)

        await coordinator.async_set_terms(cop_feedback=True)

        assert coordinator.terms == {"cop_feedback": True, "precharge": False}
        assert store.saved["terms"]["cop_feedback"] is True

    @pytest.mark.asyncio
    async def test_restore_terms_and_reference(self):
        store = FakeStore(
            {
                "terms": {"cop_feedback": True, "precharge": False},
                "cop_reference": {"0": 3.5},
                "cop_samples": {"0": 20},
            }
        )
        coordinator = make_coordinator(store=store)

        await coordinator.async_restore()

        assert coordinator.terms == {"cop_feedback": True, "precharge": False}
        assert coordinator._cop_reference == {0: 3.5}
        assert coordinator._cop_samples == {0: 20}


class TestPrecharge:
    """Pre-charge term: opt-in, active-only, one-sided upward."""

    @staticmethod
    def _forecast() -> tuple[dict[int, float], int]:
        now = int(time.time())
        now_hour = now - (now % 3600)
        return {now_hour: 8.0, now_hour + 3600: -4.0}, now

    def test_requires_flag_and_active_mode(self):
        coordinator = make_coordinator()
        forecast, now = self._forecast()

        assert coordinator._precharge_c(forecast, now, make_snapshot()) == 0.0

        coordinator._terms["precharge"] = True
        assert coordinator._precharge_c(forecast, now, make_snapshot()) == 0.0

        coordinator._mode = "active"
        assert coordinator._precharge_c(forecast, now, make_snapshot()) == pytest.approx(
            PRECHARGE_MAX_C
        )

    def test_skips_when_previous_capped_or_at_total_clamp(self):
        coordinator = make_coordinator()
        coordinator._terms["precharge"] = True
        coordinator._mode = "active"
        forecast, now = self._forecast()

        assert (
            coordinator._precharge_c(
                forecast, now, make_snapshot(capped_by_indoor=True)
            )
            == 0.0
        )
        assert (
            coordinator._precharge_c(
                forecast, now, make_snapshot(adjustment_c=TOTAL_MAX_C)
            )
            == 0.0
        )
        assert coordinator._precharge_c(forecast, now, make_snapshot()) > 0.0



def _daily_maps(days: int = 26, *, now_ts: float | None = None):
    """Hourly maps for a house whose demand the sun visibly covers."""
    now_ts = int(now_ts or time.time())
    start = now_ts - days * 86400
    start -= start % 86400
    q: dict[int, float] = {}
    indoor: dict[int, float] = {}
    outdoor: dict[int, float] = {}
    ghi: dict[int, float] = {}
    for day in range(days):
        outdoor_day = 8.0 + 0.2 * (day % 5)
        for hour in range(24):
            ts = start + (day * 24 + hour) * 3600
            outdoor[ts] = outdoor_day
            indoor[ts] = 21.0
            ghi[ts] = (
                (300.0 + 90.0 * ((day * 7) % 11)) * math.sin(math.pi * hour / 24.0)
                if 6 <= hour < 18
                else 0.0
            )
            q[ts] = max(0.0, 152.0 * (21.0 - outdoor_day) - 500.0 - 1.9 * ghi[ts])
    return q, indoor, outdoor, ghi


async def test_calibrate_fits_and_persists_the_daily_model() -> None:
    now_ts = time.time()
    coordinator = make_coordinator(store=FakeStore())
    q, indoor, outdoor, ghi = _daily_maps(now_ts=now_ts)
    coordinator._async_hourly_maps = AsyncMock(
        return_value={"heatingpower": q, "indoor": indoor, "outdoor": outdoor}
    )
    with patch.object(cc, "fetch_ghi_history", AsyncMock(return_value=ghi)):
        await coordinator._async_calibrate(now_ts)

    daily = coordinator.daily_model
    assert daily is not None
    assert daily.valid
    assert daily.b_m2 > 0.0
    assert daily.n_days >= 20
    assert coordinator._store.saved["daily_model"]["b"] == pytest.approx(daily.b_m2)
    assert coordinator._store.saved["daily_model"]["n_days"] == daily.n_days


async def test_daily_model_round_trips_through_the_store() -> None:
    now_ts = time.time()
    coordinator = make_coordinator(store=FakeStore())
    q, indoor, outdoor, ghi = _daily_maps(now_ts=now_ts)
    coordinator._async_hourly_maps = AsyncMock(
        return_value={"heatingpower": q, "indoor": indoor, "outdoor": outdoor}
    )
    with patch.object(cc, "fetch_ghi_history", AsyncMock(return_value=ghi)):
        await coordinator._async_calibrate(now_ts)
    payload = coordinator._store.saved

    restored = make_coordinator(store=FakeStore(data=payload))
    await restored.async_restore()

    assert restored.daily_model is not None
    assert restored.daily_model.valid
    assert restored.daily_model.b_m2 == pytest.approx(coordinator.daily_model.b_m2)
    assert restored.daily_model.trust == pytest.approx(coordinator.daily_model.trust)
    assert restored.daily_model.b_jackknife_min_m2 == pytest.approx(
        coordinator.daily_model.b_jackknife_min_m2
    )
    assert restored.data is None


async def test_daily_model_replaces_an_unidentifiable_window() -> None:
    """An ungated window must switch the term off, not keep an older model."""
    now_ts = time.time()
    coordinator = make_coordinator(store=FakeStore())
    coordinator._daily_model = make_daily_model()
    q = {int(now_ts) - index * 3600: 1000.0 for index in range(100)}
    coordinator._async_hourly_maps = AsyncMock(
        return_value={
            "heatingpower": q,
            "indoor": {ts: 20.0 for ts in q},
            "outdoor": {ts: 5.0 for ts in q},
        }
    )
    with patch.object(
        cc, "fetch_ghi_history", AsyncMock(return_value={ts: 0.0 for ts in q})
    ):
        await coordinator._async_calibrate(now_ts)

    assert coordinator.daily_model is not None
    assert not coordinator.daily_model.valid
    assert coordinator._store.saved["daily_model"]["valid"] is False


async def test_snapshot_applies_the_day_block_solar_term() -> None:
    coordinator = make_coordinator(values=VALUES, settings=SETTINGS)
    coordinator._baseline = dict(BASELINE)
    coordinator._async_calibrate = AsyncMock()
    coordinator._async_fetch_forecast = AsyncMock(
        return_value=make_forecast(ghi=500.0)
    )
    coordinator._async_readiness_hours = AsyncMock(return_value=({}, set()))
    coordinator._async_room_error_hours = AsyncMock(return_value=({}, None))
    coordinator._daylight = MagicMock(return_value=None)
    coordinator._model = make_model(b_m2=0.0, trust=0.0)
    coordinator._daily_model = make_daily_model()

    snapshot = await coordinator._async_compute_snapshot()

    assert snapshot.daily_model is coordinator._daily_model
    assert snapshot.solar_c < 0.0

    coordinator._daily_model = make_daily_model(valid=False)
    censored = await coordinator._async_compute_snapshot()

    assert censored.solar_c == 0.0


def active_gain_hours():
    """Hourly statistics for the active-hour measurement (seconds, like HA)."""
    # The indoor picker needs its minimum history before it resolves a key.
    hours = [1_760_000_000 + index * 3600 for index in range(30)]
    rows = {}
    for ts in hours:
        rows.setdefault("sensor.t", []).append({"start": ts, "mean": 8.0})
        rows.setdefault("sensor.s", []).append({"start": ts, "mean": 28.0})
        rows.setdefault("sensor.i", []).append({"start": ts, "mean": 20.0})
        rows.setdefault("sensor.q", []).append({"start": ts, "mean": 900.0})
    return hours, rows


async def test_the_active_gain_is_measured_on_our_own_hours() -> None:
    """The slope comes from the active segment, against the resolved sensor."""
    hours, rows = active_gain_hours()
    coordinator = make_coordinator(values={**VALUES, "sensor_mode": 4}, settings=SETTINGS)
    coordinator._active_since_ts = hours[0] - 1
    coordinator._resolve_statistic_ids = MagicMock(
        return_value={
            "bt1": "sensor.t",
            "cal_heat_temp": "sensor.s",
            "heatingpower": "sensor.q",
            "bt2": "sensor.i",
        }
    )
    coordinator._async_statistics = AsyncMock(return_value=rows)

    estimate = await coordinator._async_room_gain_from_active()

    assert estimate is not None
    assert estimate.source == "bt2"
    # Flat statistics: no slope, so nothing is published.
    assert not estimate.valid


async def test_the_active_gain_ignores_hours_before_the_segment() -> None:
    """Only hours after ``active_since_ts`` count as ours."""
    hours, rows = active_gain_hours()
    coordinator = make_coordinator(values={**VALUES, "sensor_mode": 4}, settings=SETTINGS)
    coordinator._active_since_ts = hours[-2]  # only the last two hours
    coordinator._resolve_statistic_ids = MagicMock(
        return_value={
            "bt1": "sensor.t",
            "cal_heat_temp": "sensor.s",
            "heatingpower": "sensor.q",
            "bt2": "sensor.i",
        }
    )
    coordinator._async_statistics = AsyncMock(return_value=rows)

    estimate = await coordinator._async_room_gain_from_active()

    assert estimate is not None
    # Only the two in-segment hours were considered, and they carry no spread.
    assert estimate.hours == 2
    assert not estimate.valid


async def test_the_active_gain_needs_a_segment_start() -> None:
    """Without the stamp there is no way to tell our hours from the pump's."""
    coordinator = make_coordinator(values=VALUES, settings=SETTINGS)

    assert await coordinator._async_room_gain_from_active() is None


async def test_the_active_gain_refresh_is_throttled_and_persisted() -> None:
    coordinator = make_coordinator(values=VALUES, settings=SETTINGS)
    coordinator._mode = "active"
    coordinator._active_since_ts = 1_760_000_000.0
    coordinator._async_persist = AsyncMock()
    coordinator._async_room_gain_from_active = AsyncMock(
        return_value=ActiveRoomGain(
            gain_c_per_c=4.33, hours=18, buckets=3, span_c=0.9, valid=True
        )
    )

    await coordinator._async_refresh_active_room_gain(1_760_100_000.0)
    assert coordinator._async_room_gain_from_active.await_count == 1
    assert coordinator._room_gain_active.gain_c_per_c == pytest.approx(4.33)
    assert coordinator._async_persist.await_count == 1

    # A second call within the refresh window does nothing.
    await coordinator._async_refresh_active_room_gain(1_760_100_100.0)
    assert coordinator._async_room_gain_from_active.await_count == 1


async def test_the_active_gain_round_trips_through_the_store() -> None:
    store = FakeStore(
        {
            "room_gain_active": {
                "gain": 4.33,
                "source": "room_temp_external",
                "hours": 18,
                "buckets": 3,
                "span": 0.96,
                "std_err": 0.4,
                "r2": 0.88,
                "residual": 0.15,
                "loo": 2.47,
                "valid": False,
                "notes": "one hour carries the estimate",
            },
            "room_gain_active_ts": 1_760_100_000.0,
        }
    )
    coordinator = make_coordinator(store=store)

    await coordinator.async_restore()

    estimate = coordinator._room_gain_active
    assert estimate is not None
    assert estimate.gain_c_per_c == pytest.approx(4.33)
    assert estimate.source == "room_temp_external"
    assert estimate.hours == 18
    assert estimate.leave_one_out_c_per_c == pytest.approx(2.47)
    assert estimate.valid is False
    # The attempt stamp is not persisted: a reload measures again at once
    # rather than waiting out the throttle with a stamp from the previous run.
    assert coordinator._room_gain_active_ts is None
    assert coordinator._state_payload()["room_gain_active"]["gain"] == pytest.approx(
        4.33
    )


async def test_the_active_gain_fetches_a_full_window_for_the_picker() -> None:
    """A young segment must not starve the indoor picker of its history.

    Measured on the live instance 2026-10-16: a 24 h segment held 23 hourly
    rows and the picker needs 24, so the measurement silently produced nothing.
    """
    hours, rows = active_gain_hours()
    coordinator = make_coordinator(values={**VALUES, "sensor_mode": 4}, settings=SETTINGS)
    coordinator._active_since_ts = hours[-3]
    coordinator._resolve_statistic_ids = MagicMock(
        return_value={
            "bt1": "sensor.t",
            "cal_heat_temp": "sensor.s",
            "heatingpower": "sensor.q",
            "bt2": "sensor.i",
        }
    )
    coordinator._async_statistics = AsyncMock(return_value=rows)

    await coordinator._async_room_gain_from_active()

    start = coordinator._async_statistics.await_args.args[1]
    assert start < datetime.now(timezone.utc) - timedelta(days=6)


async def test_the_active_gain_explains_why_it_cannot_measure() -> None:
    """No indoor series: a reason in the dump, not a bare null."""
    coordinator = make_coordinator(values={**VALUES, "sensor_mode": 4}, settings=SETTINGS)
    coordinator._active_since_ts = 1_760_000_000.0
    coordinator._resolve_statistic_ids = MagicMock(
        return_value={"bt1": "sensor.t", "cal_heat_temp": "sensor.s", "bt2": "sensor.i"}
    )
    coordinator._async_statistics = AsyncMock(return_value={})

    estimate = await coordinator._async_room_gain_from_active()

    assert estimate is not None
    assert estimate.valid is False
    assert "no indoor series" in estimate.notes


async def test_a_valid_measurement_survives_a_failed_run() -> None:
    """A transient "not measurable" must not overwrite a real number."""
    valid = ActiveRoomGain(
        gain_c_per_c=4.9, hours=20, buckets=4, span_c=0.9, valid=True
    )
    coordinator = make_coordinator(values=VALUES, settings=SETTINGS)
    coordinator._mode = "active"
    coordinator._active_since_ts = 1_760_000_000.0
    coordinator._room_gain_active = valid
    coordinator._async_persist = AsyncMock()
    coordinator._async_room_gain_from_active = AsyncMock(
        return_value=ActiveRoomGain(
            gain_c_per_c=0.0,
            hours=0,
            buckets=0,
            span_c=0.0,
            valid=False,
            notes="no indoor series",
        )
    )

    await coordinator._async_refresh_active_room_gain(1_760_100_000.0)

    assert coordinator._room_gain_active is valid
