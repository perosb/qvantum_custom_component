"""Custom heating-curve coordinator (Modbus only, shadow until activated).

On a slow interval this coordinator fetches the Open-Meteo forecast, reads
long-term recorder statistics for the calibration window, freezes the Auto
baseline, fits the self-calibrating solar model and computes the shadow
curve. In shadow mode the seven points are only logged and compared against
the pump's ``cal_heat_temp``. In active mode (manual switch or service) the
coordinator writes changed points and holds holding 22 = User defined, with
a safety reversion to Auto on any failure.

Failures degrade to the last good snapshot (a ``blocker`` names the missing
signal) instead of failing the update, so a missing forecast can never block
the pump integrations.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .client.models import result_applied
from .client.modbus.maps import HEATING_CURVE_OUTDOOR_TEMPS
from .const import DOMAIN, HP_STATUS_HEATING, HeatingCurveType, SensorMode
from .coordinator import QvantumDataUpdateCoordinator, handle_setting_update_response
from .heating_curve import (
    MAX_SUPPLY_C,
    MIN_SUPPLY_C,
    ROOM_GAIN_MAX_C,
    TOTAL_MAX_C,
    TRIM_MAX_C,
    DayPhase,
    compute_curve,
    cop_feedback_adjustment_c,
    corrected_baseline,
    effective_supply_bounds,
    interpolate_supply,
    normalize_baseline,
    precharge_adjustment_c,
    ActiveRoomGain,
    room_compensation_gain,
    room_corrected_deviation,
    room_gain_from_active_hours,
    trim_evidence_hours,
    trim_residuals,
)
from .open_meteo import (
    OpenMeteoError,
    WeatherForecast,
    fetch_forecast,
    fetch_ghi_history,
)
from .solar_gain import (
    MIN_SAMPLE_Q_W,
    SolarDailyModel,
    SolarModel,
    aggregate_days,
    build_samples,
    fit_daily_solar_model,
    fit_solar_model,
)
from .statistics import (
    INDOOR_METRIC_KEYS,
    INDOOR_METRIC_MIN_HOURS,
    async_statistics_during_period,
    indoor_metric_entity_ids,
    pick_indoor_metric_key,
    resolve_statistic_entity_ids,
)

_LOGGER = logging.getLogger(__name__)

CURVE_KEYS: tuple[str, ...] = tuple(HEATING_CURVE_OUTDOOR_TEMPS)

UPDATE_INTERVAL = timedelta(minutes=15)
CALIBRATION_DAYS = 60
CALIBRATION_REFRESH_HOURS = 24.0
#: Retry delay after an attempt that could not fetch its inputs. A
#: transient failure must not cost a full refresh interval of staleness:
#: the fetch covers 60 days of archive plus statistics, and the inputs are
#: unchanged for the rest of the day.
CALIBRATION_RETRY_HOURS = 1.0
MIN_CALIBRATION_SAMPLES = 72
#: Days of observed ``(BT1, cal_heat_temp)`` used to correct the baseline.
BASELINE_LEARN_DAYS = 7
#: Minimum time between baseline-correction statistics fetches.
BASELINE_LEARN_REFRESH_HOURS = 6.0
#: Rolling window of indoor-error hours behind the active trims.
TRIM_WINDOW_DAYS = 7
#: Minimum time between trim updates.
TRIM_REFRESH_HOURS = 24.0
#: Days of hourly statistics read for the active-hour room-gain measurement.
ROOM_GAIN_ACTIVE_WINDOW_DAYS = 7
#: Faster cadence until the first trims exist: the evidence needed for them is
#: already in the 7-day window, so waiting a full day only delays the first
#: correction. Once any trim is in place the proven daily step takes over.
TRIM_FIRST_REFRESH_HOURS = 3.0
#: Fraction of the indicated residual applied per update (damped loop).
TRIM_UPDATE_GAIN = 0.3
INDOOR_MARGIN_CYCLES = 96  # 24 h at 15 min
POWER_CYCLES = 4  # 1 h at 15 min
READY_WINDOW_DAYS = 3
READY_MIN_COVERAGE = 0.9
#: Heating-hour samples needed before the readiness statistics mean anything.
READY_MIN_HOURS = 24
#: Median gate, calibrated to what a ten-degree seven-point table can
#: represent: even a converged baseline leaves ~1 °C of scatter in the
#: observed outdoor range, so a tighter gate is unreachable by construction.
READY_MEDIAN_MAX_C = 1.5
#: Upper-bound gate on the 95th percentile: single mode-switch hours are not a
#: curve error, and an absolute maximum would fail on them forever.
READY_P95_MAX_C = 3.0
STORAGE_VERSION = 1


def curve_store_key(entry_id: str) -> str:
    """Storage key for one config entry's curve state."""
    return f"{DOMAIN}.curve.{entry_id}"

#: COP-reference learning: per-5 °C outdoor bucket EMA, and the minimum
#: samples before the feedback term may act. Learning always runs while the
#: pump heats, so enabling the term does not start from nothing.
COP_REFERENCE_ALPHA = 0.05
COP_REFERENCE_BUCKET_C = 5.0
COP_REFERENCE_MIN_SAMPLES = 12

#: Pause between single-point Modbus writes so one cycle is not a burst.
WRITE_PAUSE_SECONDS = 1.0
#: Minimum change in °C before a point is rewritten.
MIN_WRITE_DELTA_C = 1.0
CONTROL_MODES = ("shadow", "active")


async def async_stored_curve_mode(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> str | None:
    """Curve control mode from the Store, without a live coordinator.

    Called on config-entry removal, after the coordinator has been shut down
    and the client closed. Returns ``None`` when the state is missing or
    unreadable, which callers treat as "not active" — removal must never fail
    because the curve state could not be read.
    """
    store = Store(hass, STORAGE_VERSION, curve_store_key(config_entry.entry_id))
    try:
        data = await store.async_load()
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — best effort
        return None
    if not isinstance(data, dict):
        return None
    mode = data.get("mode")
    return str(mode) if mode in CONTROL_MODES else None


@dataclass(frozen=True)
class CurveReadiness:
    """Whether shadow mode has looked good enough to suggest activation."""

    ready: bool
    blocker: str | None
    median_abs_c: float | None
    max_abs_c: float | None
    window_hours: float
    p95_abs_c: float | None = None


@dataclass(frozen=True)
class RoomTracking:
    """How far the rooms sit from the target over the readiness window."""

    median_abs_c: float
    median_c: float
    p90_abs_c: float
    samples: int


@dataclass(frozen=True)
class CurveSnapshot:
    """Curve coordinator data consumed by the curve sensors."""

    shadow: bool
    points: Mapping[str, float]
    baseline: Mapping[str, float]
    adjustment_c: float
    outdoor_c: float
    night_day_c: float
    solar_c: float
    load_c: float
    capped_by_indoor: bool
    deviation_c: float | None
    ready: bool
    blocker: str | None
    median_abs_c: float | None
    max_abs_c: float | None
    window_hours: float
    model: SolarModel | None
    calibrated_at: str | None
    #: Day-block identification (no heating-hour censoring). When ``valid``
    #: its aperture is the one the solar term applies.
    daily_model: SolarDailyModel | None = None
    baseline_auto: bool = False
    trims: Mapping[str, float] = field(default_factory=dict)
    clamped: bool = False
    baseline_learned_hours: int | None = None
    baseline_outdoor_min_c: float | None = None
    baseline_outdoor_max_c: float | None = None
    #: Identified pump room-compensation gain (supply °C per °C room error).
    room_gain_c: float | None = None
    #: 95th percentile of the readiness window (the gated upper bound).
    p95_abs_c: float | None = None
    #: Hours behind each point's trim; 0 means the trim is deliberately 0
    #: because the window holds no evidence near that outdoor temperature.
    trim_evidence: Mapping[str, int] = field(default_factory=dict)
    #: Points whose outdoor temperature lies outside the range the baseline
    #: was observed at. Their supply is extrapolated from the cached table, so
    #: readiness says nothing about them until the weather gets there.
    extrapolated_points: tuple[str, ...] = ()

    #: Room tracking over the same window: |indoor − target|, signed median,
    #: p90 and sample count, plus the indoor metric they came from.
    room_error_median_abs_c: float | None = None
    room_error_median_c: float | None = None
    room_error_p90_abs_c: float | None = None
    room_error_samples: int = 0
    room_error_source: str | None = None
    cop_c: float = 0.0
    precharge_c: float = 0.0
    #: The air-side margin the indoor cap acts on (mean room error over the
    #: last 24 h) and whether it exists at all: without a fresh indoor reading
    #: the cap is inert, so a warm house cannot veto a raise and nothing brakes
    #: the curve when the house is warm.
    indoor_margin_c: float | None = None
    indoor_cap_armed: bool = False


def freeze_baseline(settings: Mapping[str, Any]) -> dict[str, float] | None:
    """Copy the seven pump points into a baseline, or ``None`` if incomplete."""
    baseline: dict[str, float] = {}
    for key in CURVE_KEYS:
        value = settings.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        baseline[key] = float(value)
    return baseline


def signal_blocker(
    *,
    has_baseline: bool,
    forecast_ok: bool,
    history_ok: bool,
) -> str | None:
    """Name the first missing signal for shadow readiness."""
    if not has_baseline:
        return "baseline"
    if not forecast_ok:
        return "forecast"
    if not history_ok:
        return "history"
    return None


def _median_sorted(values: list[float]) -> float:
    index = len(values) // 2
    if len(values) % 2:
        return values[index]
    return 0.5 * (values[index - 1] + values[index])


def _hourly_series(rows: Mapping[str, Any], entity_id: str) -> dict[int, float]:
    """``{hour_start: mean}`` for one statistic, skipping incomplete rows."""
    series: dict[int, float] = {}
    for row in rows.get(entity_id, []):
        mean = row.get("mean")
        start = row.get("start")
        if mean is None or start is None:
            continue
        series[int(start)] = float(mean)
    return series


def curve_deviation_c(
    points: Mapping[str, float],
    outdoor_c: float | None,
    cal_heat_temp_c: float | None,
) -> float | None:
    """Computed supply at the measured outdoor minus the pump's value."""
    if outdoor_c is None or cal_heat_temp_c is None:
        return None
    try:
        pairs = normalize_baseline(points)
    except ValueError:
        return None
    return interpolate_supply(pairs, outdoor_c) - cal_heat_temp_c


def points_within_limits(
    points: Mapping[str, float],
    *,
    min_supply_c: float = MIN_SUPPLY_C,
    max_supply_c: float = MAX_SUPPLY_C,
) -> bool:
    """True when all seven points lie within the effective supply limits.

    ``min_supply_c``/``max_supply_c`` are the pump's own limits when known
    (holding 19/20), otherwise the register range. A violation triggers the
    safety reversion instead of writing a table the firmware must not follow.
    """
    if any(key not in points for key in CURVE_KEYS):
        return False
    previous: float | None = None
    for key in CURVE_KEYS:  # +30 … −30: supply must be non-decreasing
        value = points[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        value = float(value)
        if value < min_supply_c - 1e-9 or value > max_supply_c + 1e-9:
            return False
        if previous is not None and value < previous - 1e-9:
            return False
        previous = value
    return True


def readiness_window(now_ts: int, tz: ZoneInfo) -> tuple[float, float]:
    """``(window start, hours covered)`` for the current local day.

    The window is the last ``READY_WINDOW_DAYS`` full local days, so it rolls
    over at local midnight rather than drifting with the poll cadence.
    """
    local_now = datetime.fromtimestamp(now_ts, tz)
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = (day_start - timedelta(days=READY_WINDOW_DAYS)).timestamp()
    return start, (now_ts - start) / 3600.0


def room_error_summary(
    hourly_errors: Mapping[int, float], now_ts: int, tz: ZoneInfo
) -> "RoomTracking | None":
    """Median and p90 of the hourly ``indoor − target`` over the window.

    Readiness asks whether our baseline still matches the pump, which becomes
    self-referential once the curve is active. This is the outcome instead:
    how far the rooms actually sit from the target. ``None`` when the window
    holds fewer than ``READY_MIN_HOURS`` usable hours.
    """
    start, _ = readiness_window(now_ts, tz)
    values = [
        float(value)
        for ts, value in hourly_errors.items()
        if start <= ts < now_ts
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    ]
    if len(values) < READY_MIN_HOURS:
        return None
    signed = sorted(values)
    absolute = sorted(abs(value) for value in signed)
    index = min(len(absolute) - 1, round(0.9 * (len(absolute) - 1)))
    return RoomTracking(
        median_abs_c=round(_median_sorted(absolute), 2),
        median_c=round(_median_sorted(signed), 2),
        p90_abs_c=round(absolute[index], 2),
        samples=len(values),
    )


def assess_readiness(
    hourly_deviations: Mapping[int, float],
    now_ts: int,
    tz: ZoneInfo,
    *,
    signal: str | None = None,
    covered_hours: Iterable[int] | None = None,
) -> CurveReadiness:
    """Assess the readiness window over at least three full local days.

    ``signal`` carries the missing-signal blocker (baseline/forecast/history)
    computed by the caller; window/value blockers are added here.
    ``covered_hours`` are the timestamps the recorder covered inside the
    window, so a sample that is deliberately filtered (heating hours only) is
    not judged against every hour of the window; it defaults to the sample
    count for callers that pass an unfiltered series.
    """
    if signal is not None:
        return CurveReadiness(False, signal, None, None, 0.0)

    start, window_hours = readiness_window(now_ts, tz)
    expected = max(1, int((now_ts - start) // 3600))
    values = [
        float(value)
        for ts, value in hourly_deviations.items()
        if start <= ts < now_ts
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    ]
    if covered_hours is None:
        observed = len(values)
    else:
        observed = sum(1 for ts in covered_hours if start <= ts < now_ts)
    if observed / expected < READY_MIN_COVERAGE or len(values) < READY_MIN_HOURS:
        return CurveReadiness(False, "window", None, None, round(window_hours, 1))

    absolute = sorted(abs(value) for value in values)
    median_abs = _median_sorted(absolute)
    p95_abs = absolute[min(len(absolute) - 1, round(0.95 * (len(absolute) - 1)))]
    max_abs = absolute[-1]
    if median_abs > READY_MEDIAN_MAX_C:
        return CurveReadiness(
            False,
            "median",
            round(median_abs, 2),
            round(max_abs, 2),
            round(window_hours, 1),
            round(p95_abs, 2),
        )
    if p95_abs > READY_P95_MAX_C:
        return CurveReadiness(
            False,
            "max",
            round(median_abs, 2),
            round(max_abs, 2),
            round(window_hours, 1),
            round(p95_abs, 2),
        )
    return CurveReadiness(
        True,
        None,
        round(median_abs, 2),
        round(max_abs, 2),
        round(window_hours, 1),
        round(p95_abs, 2),
    )


def _model_to_state(model: SolarModel | None) -> dict[str, Any] | None:
    if model is None:
        return None
    return {
        "a": model.a_w_per_k,
        "b": model.b_m2,
        "c": model.c_w,
        "trust": model.trust,
        "r2_opaque": model.r2_opaque,
        "r2_solar": model.r2_solar,
        "b_std_err": model.b_std_err,
        "n_opaque": model.n_opaque,
        "n_solar": model.n_solar,
        "valid": model.valid,
        "notes": model.notes,
    }


def _daily_to_state(model: SolarDailyModel | None) -> dict[str, Any] | None:
    if model is None:
        return None
    return {
        "a": model.a_w_per_k,
        "b": model.b_m2,
        "c": model.c_w,
        "trust": model.trust,
        "r2": model.r2,
        "n_days": model.n_days,
        "b_std_err": model.b_std_err,
        "t_stat": model.t_stat,
        "b_first_half": model.b_first_half_m2,
        "b_second_half": model.b_second_half_m2,
        "b_jackknife_min": model.b_jackknife_min_m2,
        "b_jackknife_max": model.b_jackknife_max_m2,
        "valid": model.valid,
        "notes": model.notes,
    }


def _daily_from_state(state: Any) -> SolarDailyModel | None:
    if not isinstance(state, dict):
        return None

    def optional(raw: Any) -> float | None:
        if raw is None or isinstance(raw, bool):
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None

    try:
        return SolarDailyModel(
            a_w_per_k=float(state["a"]),
            b_m2=float(state["b"]),
            c_w=float(state["c"]),
            trust=float(state.get("trust", 0.0)),
            r2=float(state.get("r2", 0.0)),
            n_days=int(state.get("n_days", 0)),
            b_std_err=optional(state.get("b_std_err")),
            t_stat=optional(state.get("t_stat")),
            b_first_half_m2=optional(state.get("b_first_half")),
            b_second_half_m2=optional(state.get("b_second_half")),
            b_jackknife_min_m2=optional(state.get("b_jackknife_min")),
            b_jackknife_max_m2=optional(state.get("b_jackknife_max")),
            valid=bool(state.get("valid", False)),
            notes=str(state.get("notes", "")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _active_gain_to_state(estimate: ActiveRoomGain | None) -> dict[str, Any] | None:
    if estimate is None:
        return None
    return {
        "gain": estimate.gain_c_per_c,
        "source": estimate.source,
        "hours": estimate.hours,
        "buckets": estimate.buckets,
        "span": estimate.span_c,
        "std_err": estimate.std_err_c_per_c,
        "r2": estimate.r2,
        "residual": estimate.residual_std_c,
        "loo": estimate.leave_one_out_c_per_c,
        "valid": estimate.valid,
        "notes": estimate.notes,
    }


def _active_gain_from_state(state: Any) -> ActiveRoomGain | None:
    if not isinstance(state, dict):
        return None

    def optional(raw: Any) -> float | None:
        if raw is None or isinstance(raw, bool):
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None

    try:
        return ActiveRoomGain(
            gain_c_per_c=float(state["gain"]),
            source=str(state["source"]) if state.get("source") else None,
            hours=int(state.get("hours", 0)),
            buckets=int(state.get("buckets", 0)),
            span_c=float(state.get("span", 0.0)),
            std_err_c_per_c=optional(state.get("std_err")),
            r2=optional(state.get("r2")),
            residual_std_c=optional(state.get("residual")),
            leave_one_out_c_per_c=optional(state.get("loo")),
            valid=bool(state.get("valid", False)),
            notes=str(state.get("notes", "")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _model_from_state(state: Any) -> SolarModel | None:
    if not isinstance(state, dict):
        return None
    try:
        return SolarModel(
            a_w_per_k=float(state["a"]),
            b_m2=float(state["b"]),
            c_w=float(state["c"]),
            trust=float(state["trust"]),
            r2_opaque=float(state.get("r2_opaque", 0.0)),
            r2_solar=float(state.get("r2_solar", 0.0)),
            b_std_err=(
                None
                if state.get("b_std_err") is None
                else float(state["b_std_err"])
            ),
            n_opaque=int(state.get("n_opaque", 0)),
            n_solar=int(state.get("n_solar", 0)),
            valid=bool(state.get("valid", False)),
            notes=str(state.get("notes", "")),
        )
    except (KeyError, TypeError, ValueError):
        return None


class QvantumCurveCoordinator(DataUpdateCoordinator[CurveSnapshot]):
    """Shadow curve computation for one Qvantum entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        main_coordinator: QvantumDataUpdateCoordinator,
        *,
        session: Any = None,
        store: Store | None = None,
    ) -> None:
        self.config_entry = config_entry
        self._main = main_coordinator
        self._session = session
        if store is None and isinstance(
            getattr(getattr(hass, "config", None), "config_dir", None), str
        ):
            store = Store(
                hass, STORAGE_VERSION, curve_store_key(config_entry.entry_id)
            )
        self._store = store
        self._baseline: dict[str, float] | None = None
        self._baseline_auto: bool | None = None
        self._curve_23: int | None = None
        self._model: SolarModel | None = None
        self._daily_model: SolarDailyModel | None = None
        self._calibrated_at: str | None = None
        self._last_calibration_ts: float | None = None
        self._last_calibration_ok: bool | None = None
        self._last_baseline_learn_ts: float | None = None
        self._last_active_ts: float | None = None
        self._active_since_ts: float | None = None
        self._trims: dict[str, float] = {}
        self._trim_evidence: dict[str, int] = {}
        self._last_trim_ts: float | None = None
        self._baseline_stats: dict[str, Any] | None = None
        self._room_gain: float | None = None
        self._room_gain_source: str | None = None
        # The pump's own ``room_comp_factor`` as last seen, for diagnostics.
        self._room_gain_factor: float | None = None
        # The last *fitted* estimate, kept beside the applied gain: the applied
        # one may simply be the pump's configured factor, and the pool needs the
        # measured slope to check the identity.
        self._room_gain_fitted: float | None = None
        self._room_gain_fitted_source: str | None = None
        # The active-hour measurement, kept beside the fitted one: it is the
        # only clean slope on an installation that runs active permanently.
        self._room_gain_active: ActiveRoomGain | None = None
        self._room_gain_active_ts: float | None = None
        # Whether ``_room_gain`` came from that factor: a factor that turns
        # unusable must drop the gain it set rather than leave it applied.
        self._room_gain_from_factor = False
        self._clamped_state = False
        self._forecast: WeatherForecast | None = None
        self._forecast_ok = False
        self._indoor_margins: deque[tuple[float, float]] = deque(
            maxlen=INDOOR_MARGIN_CYCLES
        )
        self._powers: deque[float] = deque(maxlen=POWER_CYCLES)
        self._mode = "shadow"
        self._revert_pending = False
        self._control_lock = asyncio.Lock()
        # Optional control-loop terms (off unless explicitly enabled) and the
        # learned COP reference used by the feedback term.
        self._terms: dict[str, bool] = {"cop_feedback": False, "precharge": False}
        self._cop_reference: dict[int, float] = {}
        self._cop_samples: dict[int, int] = {}

        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN} Curve ({config_entry.unique_id})",
            update_method=self._async_update_data,
            update_interval=UPDATE_INTERVAL,
        )

    @property
    def baseline(self) -> dict[str, float] | None:
        return self._baseline

    @property
    def model(self) -> SolarModel | None:
        return self._model

    @property
    def daily_model(self) -> SolarDailyModel | None:
        """Day-block identification; the solar term's source when it is valid."""
        return self._daily_model

    @property
    def active(self) -> bool:
        """True when the curve writes the pump (holding 22 is ours)."""
        return self._mode == "active"

    @property
    def writable(self) -> bool:
        """True when the Modbus client accepts holding writes."""
        client = getattr(self._main, "client", None)
        return bool(getattr(client, "writable", False))

    @property
    def terms(self) -> dict[str, bool]:
        """Optional control-loop term flags (off unless explicitly enabled)."""
        return dict(self._terms)

    @property
    def session(self) -> Any:
        if self._session is None:
            self._session = async_get_clientsession(self.hass)
        return self._session

    def _state_payload(self) -> dict[str, Any]:
        return {
            "baseline": dict(self._baseline) if self._baseline else None,
            "baseline_auto": self._baseline_auto,
            "temp_compensation_curve": self._curve_23,
            "model": _model_to_state(self._model),
            "daily_model": _daily_to_state(self._daily_model),
            "calibrated_at": self._calibrated_at,
            "calibrated_ts": self._last_calibration_ts,
            "calibration_ok": self._last_calibration_ok,
            "last_active_ts": self._last_active_ts,
            "active_since_ts": self._active_since_ts,
            "trims": dict(self._trims),
            "trim_evidence": dict(self._trim_evidence),
            "baseline_stats": self._baseline_stats,
            "room_gain": self._room_gain,
            "room_gain_source": self._room_gain_source,
            "room_gain_from_factor": self._room_gain_from_factor,
            "room_gain_fitted": self._room_gain_fitted,
            "room_gain_fitted_source": self._room_gain_fitted_source,
            "room_gain_active": _active_gain_to_state(self._room_gain_active),
            "room_gain_active_ts": self._room_gain_active_ts,
            "indoor_margins": [[ts, margin] for ts, margin in self._indoor_margins],
            "mode": self._mode,
            "revert_pending": self._revert_pending,
            "cop_reference": {
                str(bucket): value for bucket, value in self._cop_reference.items()
            },
            "cop_samples": {
                str(bucket): count for bucket, count in self._cop_samples.items()
            },
            "terms": dict(self._terms),
        }

    def _mark_active_started(self) -> None:
        """Record when the pump started following our table.

        ``_last_active_ts`` marks the end of an active period; without the
        start the readiness window cannot tell hours where ``cal_heat_temp``
        followed our own table from hours where it followed the pump's, and the
        room correction is only valid on the former.
        """
        self._active_since_ts = dt_util.utcnow().timestamp()

    def _mark_active_ended(self) -> None:
        """Record when the pump last stopped following our table.

        ``cal_heat_temp`` after this instant is Auto behaviour; before it,
        hours may have followed the user-defined table we wrote. Baseline
        learning must not read across the boundary.
        """
        self._last_active_ts = dt_util.utcnow().timestamp()

    async def _async_persist(self) -> None:
        store = self._store
        if store is None:
            return
        try:
            await store.async_save(self._state_payload())
        except Exception:
            _LOGGER.debug("Failed to persist curve state", exc_info=True)

    async def async_restore(self) -> None:
        """Restore baseline, solar model and the indoor-margin window."""
        store = self._store
        if store is None:
            return
        try:
            data = await store.async_load()
        except Exception:
            _LOGGER.debug("Failed to load curve state", exc_info=True)
            return
        if not isinstance(data, dict):
            return

        baseline = data.get("baseline")
        if isinstance(baseline, dict):
            restored = freeze_baseline(baseline)
            if restored is not None:
                self._baseline = restored
                self._baseline_auto = bool(data.get("baseline_auto", True))
                value = data.get("temp_compensation_curve")
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    self._curve_23 = int(value)
        self._model = _model_from_state(data.get("model"))
        self._daily_model = _daily_from_state(data.get("daily_model"))
        calibrated_at = data.get("calibrated_at")
        self._calibrated_at = calibrated_at if isinstance(calibrated_at, str) else None
        calibrated_ts = data.get("calibrated_ts")
        if isinstance(calibrated_ts, (int, float)) and not isinstance(calibrated_ts, bool):
            self._last_calibration_ts = float(calibrated_ts)
        calibration_ok = data.get("calibration_ok")
        if isinstance(calibration_ok, bool):
            self._last_calibration_ok = calibration_ok
        last_active_ts = data.get("last_active_ts")
        if isinstance(last_active_ts, (int, float)) and not isinstance(
            last_active_ts, bool
        ):
            self._last_active_ts = float(last_active_ts)
        active_since_ts = data.get("active_since_ts")
        if isinstance(active_since_ts, (int, float)) and not isinstance(
            active_since_ts, bool
        ):
            self._active_since_ts = float(active_since_ts)
        trims = data.get("trims")
        if isinstance(trims, dict):
            for key, value in trims.items():
                if (
                    key in CURVE_KEYS
                    and isinstance(value, (int, float))
                    and not isinstance(value, bool)
                ):
                    self._trims[key] = float(value)
        evidence = data.get("trim_evidence")
        if isinstance(evidence, dict):
            for key, hours in evidence.items():
                if key in CURVE_KEYS and isinstance(hours, int) and not isinstance(hours, bool):
                    self._trim_evidence[key] = hours
        stats = data.get("baseline_stats")
        if isinstance(stats, dict):
            self._baseline_stats = {
                key: stats.get(key) for key in ("hours", "min_c", "max_c", "corrected")
            }
        room_gain = data.get("room_gain")
        if isinstance(room_gain, (int, float)) and not isinstance(room_gain, bool):
            self._room_gain = float(room_gain)
        room_gain_source = data.get("room_gain_source")
        if room_gain_source in INDOOR_METRIC_KEYS:
            self._room_gain_source = str(room_gain_source)
        self._room_gain_from_factor = bool(data.get("room_gain_from_factor", False))
        room_gain_fitted = data.get("room_gain_fitted")
        if isinstance(room_gain_fitted, (int, float)) and not isinstance(
            room_gain_fitted, bool
        ):
            self._room_gain_fitted = float(room_gain_fitted)
        fitted_source = data.get("room_gain_fitted_source")
        if fitted_source in INDOOR_METRIC_KEYS:
            self._room_gain_fitted_source = str(fitted_source)
        self._room_gain_active = _active_gain_from_state(data.get("room_gain_active"))
        active_ts = data.get("room_gain_active_ts")
        if isinstance(active_ts, (int, float)) and not isinstance(active_ts, bool):
            self._room_gain_active_ts = float(active_ts)
        margins = data.get("indoor_margins")
        if isinstance(margins, list):
            for item in margins:
                if (
                    isinstance(item, (list, tuple))
                    and len(item) == 2
                    and isinstance(item[0], (int, float))
                    and isinstance(item[1], (int, float))
                ):
                    self._indoor_margins.append((float(item[0]), float(item[1])))
        if self._baseline is not None and data.get("mode") in CONTROL_MODES:
            self._mode = str(data.get("mode"))
        elif data.get("mode") == "shadow":
            self._mode = "shadow"
        self._revert_pending = bool(data.get("revert_pending", False))
        cop_reference = data.get("cop_reference")
        if isinstance(cop_reference, dict):
            for raw_bucket, value in cop_reference.items():
                try:
                    bucket = int(raw_bucket)
                except (TypeError, ValueError):
                    continue
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    numeric = float(value)
                    if math.isfinite(numeric) and numeric > 0.0:
                        self._cop_reference[bucket] = numeric
        cop_samples = data.get("cop_samples")
        if isinstance(cop_samples, dict):
            for raw_bucket, value in cop_samples.items():
                try:
                    bucket = int(raw_bucket)
                except (TypeError, ValueError):
                    continue
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    self._cop_samples[bucket] = max(0, int(value))
        terms = data.get("terms")
        if isinstance(terms, dict):
            for key in ("cop_feedback", "precharge"):
                if key in terms:
                    self._terms[key] = bool(terms[key])
        if self.active and self._active_since_ts is None:
            # A store written before this field existed, or an activation this
            # process never saw, leaves the start unknown. Stamp the moment the
            # pump is known to follow our table rather than guess earlier hours:
            # those stay uncorrected, and the readiness window rolls clean in
            # three local days.
            self._mark_active_started()
            await self._async_persist()

    async def async_set_terms(
        self,
        *,
        cop_feedback: bool | None = None,
        precharge: bool | None = None,
    ) -> None:
        """Enable or disable the optional control-loop terms.

        Persisted and off by default. The terms only change the computed
        curve while active, so enabling one in shadow mode has no write
        effect until the curve is activated.
        """
        if cop_feedback is not None:
            self._terms["cop_feedback"] = bool(cop_feedback)
        if precharge is not None:
            self._terms["precharge"] = bool(precharge)
        await self._async_persist()
        self.async_update_listeners()

    async def async_set_control_mode(self, mode: str) -> None:
        """Switch between shadow (no writes) and active curve control."""
        if mode not in CONTROL_MODES:
            raise ValueError(f"unknown curve control mode: {mode!r}")
        async with self._control_lock:
            if mode == "active":
                await self._async_activate()
            elif self.active:
                await self._async_revert()
            else:
                # Already shadow: never touch a table this module does not
                # own; only retry a latched safety reversion.
                await self._async_retry_revert()
        self.async_update_listeners()

    def _integer_points(self) -> dict[str, int]:
        """Whole-degree targets from the last snapshot."""
        snapshot = self.data
        if snapshot is None:
            return {}
        return {
            key: int(math.floor(value + 0.5))
            for key, value in snapshot.points.items()
        }

    async def _async_read_pump_settings(self) -> dict[str, Any]:
        device_id = getattr(self._main, "device_id", None)
        if not device_id:
            return {}
        payload = await self._main.client.get_settings(device_id)
        return self._main._process_settings_data(payload)

    async def _async_write_or_revert(
        self, awaitable: Any, *, label: str
    ) -> Any | None:
        """Await a curve write; on failure revert to Auto and stop active."""
        try:
            response = await awaitable
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 — transport failures revert
            _LOGGER.warning("Curve write %s failed: %s", label, err)
            await self._async_revert()
            return None
        if not result_applied(response):
            _LOGGER.warning("Curve write %s was not applied: %s", label, response)
            await self._async_revert()
            return None
        return response

    async def _async_activate(self) -> None:
        """Manual activation: points first (Auto ignores them), 22 last."""
        if self._mode == "active":
            return
        if self._baseline is None:
            raise HomeAssistantError("No frozen curve baseline yet")
        if not self.writable:
            raise HomeAssistantError("Modbus writes are not enabled")
        device_id = getattr(self._main, "device_id", None)
        if not device_id:
            raise HomeAssistantError("No Qvantum device id available")
        points = self._integer_points()
        if not points or not points_within_limits(points):
            raise HomeAssistantError("No writable curve points yet")

        client = self._main.client
        # The poll cache can be one scan interval stale. Read the live curve
        # type before deciding, so a pump moved to User defined in the app
        # just before activation cannot have its table mutated while live.
        try:
            live = await self._async_read_pump_settings()
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 — fall back to the poll cache
            _LOGGER.debug("Could not read the live curve type: %s", err)
            live = self._main_values()
        if live.get("curve_type_heating") != HeatingCurveType.AUTO:
            response = await self._async_write_or_revert(
                client.set_curve_type_heating(device_id, int(HeatingCurveType.AUTO)),
                label="curve_type_heating=Auto",
            )
            if response is None:
                raise HomeAssistantError("Could not switch the pump to Auto")
            await handle_setting_update_response(
                response,
                self._main,
                "values",
                "curve_type_heating",
                int(HeatingCurveType.AUTO),
            )
        low, high = self._supply_bounds(live)
        if not points_within_limits(points, min_supply_c=low, max_supply_c=high):
            raise HomeAssistantError("Curve points outside the pump's supply limits")
        for key, value in points.items():
            response = await self._async_write_or_revert(
                client.set_heating_curve_point(device_id, key, value),
                label=f"{key}={value}",
            )
            if response is None:
                raise HomeAssistantError(f"Could not write {key}")
            await handle_setting_update_response(
                response, self._main, "values", key, value
            )
            await asyncio.sleep(WRITE_PAUSE_SECONDS)
        response = await self._async_write_or_revert(
            client.set_indoor_temperature_offset(device_id, 0),
            label="offset=0",
        )
        if response is None:
            raise HomeAssistantError("Could not zero the curve offset")
        await handle_setting_update_response(
            response, self._main, "values", "indoor_temperature_offset", 0
        )

        try:
            readback = await self._async_read_pump_settings()
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 — verification is mandatory
            await self._async_revert()
            raise HomeAssistantError(f"Could not verify curve points: {err}") from err
        for key, value in points.items():
            reported = readback.get(key)
            if (
                not isinstance(reported, (int, float))
                or isinstance(reported, bool)
                or int(math.floor(float(reported) + 0.5)) != value
            ):
                await self._async_revert()
                raise HomeAssistantError(f"Curve point {key} did not stick")

        response = await self._async_write_or_revert(
            client.set_curve_type_heating(
                device_id, int(HeatingCurveType.USER_DEFINED)
            ),
            label="curve_type_heating=User defined",
        )
        if response is None:
            raise HomeAssistantError("Could not enable the user-defined curve")
        await handle_setting_update_response(
            response,
            self._main,
            "values",
            "curve_type_heating",
            int(HeatingCurveType.USER_DEFINED),
        )
        self._mode = "active"
        self._mark_active_started()
        # A latch from an earlier failed revert is obsolete once the pump
        # follows our table again; keeping it would write 22 back to Auto on
        # the next cycle and silently undo this activation.
        self._revert_pending = False
        await self._async_persist()
        _LOGGER.info("Adaptive heating curve active (holding 22 = User defined)")

    async def _async_revert(self) -> None:
        """Safety fallback: write holding 22 back to Auto, stop writing."""
        was_active = self._mode == "active"
        self._mode = "shadow"
        if was_active:
            self._mark_active_ended()
            # Active trims belong to the active period; the next activation
            # relearns them against whatever baseline is current then.
            self._trims = {}
            self._last_trim_ts = None
        device_id = getattr(self._main, "device_id", None)
        client = getattr(self._main, "client", None)
        if self.writable and device_id and client is not None:
            try:
                response = await client.set_curve_type_heating(
                    device_id, int(HeatingCurveType.AUTO)
                )
                if result_applied(response):
                    await handle_setting_update_response(
                        response,
                        self._main,
                        "values",
                        "curve_type_heating",
                        int(HeatingCurveType.AUTO),
                    )
                    self._revert_pending = False
                else:
                    self._revert_pending = True
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — connection may be gone
                self._revert_pending = True
        elif was_active:
            self._revert_pending = True
        await self._async_persist()
        self.async_update_listeners()

    async def _async_retry_revert(self) -> None:
        """Retry a failed safety reversion once the pump is reachable."""
        if not self._revert_pending or not self.writable:
            return
        device_id = getattr(self._main, "device_id", None)
        if not device_id:
            return
        try:
            response = await self._main.client.set_curve_type_heating(
                device_id, int(HeatingCurveType.AUTO)
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — retry next cycle
            return
        if result_applied(response):
            self._revert_pending = False
            await self._async_persist()

    async def _async_apply_active(self, result: Any) -> None:
        """Write changed points while active; a failure reverts to Auto."""
        if not self.writable:
            await self._async_revert()
            return
        device_id = getattr(self._main, "device_id", None)
        if not device_id:
            await self._async_revert()
            return
        values = self._main_values()
        if values.get("curve_type_heating") != HeatingCurveType.USER_DEFINED:
            # Someone moved the pump off User defined; stop claiming control.
            self._mode = "shadow"
            self._mark_active_ended()
            self._trims = {}
            self._last_trim_ts = None
            await self._async_persist()
            self.async_update_listeners()
            return
        low, high = self._supply_bounds(values)
        if not points_within_limits(
            dict(result.points), min_supply_c=low, max_supply_c=high
        ):
            await self._async_revert()
            return

        client = self._main.client
        for key, value in result.integer_points():
            current = values.get(key)
            if (
                isinstance(current, (int, float))
                and not isinstance(current, bool)
                and abs(float(value) - float(current)) < MIN_WRITE_DELTA_C
            ):
                continue
            response = await self._async_write_or_revert(
                client.set_heating_curve_point(device_id, key, value),
                label=f"{key}={value}",
            )
            if response is None:
                return
            await handle_setting_update_response(
                response, self._main, "values", key, value
            )
            await asyncio.sleep(WRITE_PAUSE_SECONDS)

    async def _async_update_data(self) -> CurveSnapshot:
        """Never fail the coordinator: degrade to the last good snapshot."""
        try:
            return await self._async_compute_snapshot()
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 — coordinator must stay alive
            _LOGGER.warning("Curve update failed: %s", err)
            return self.data or self._empty_snapshot()

    def _empty_snapshot(self) -> CurveSnapshot:
        return CurveSnapshot(
            shadow=True,
            points=dict(self._baseline) if self._baseline else {},
            baseline=dict(self._baseline) if self._baseline else {},
            adjustment_c=0.0,
            outdoor_c=0.0,
            night_day_c=0.0,
            solar_c=0.0,
            load_c=0.0,
            capped_by_indoor=False,
            deviation_c=None,
            ready=False,
            blocker="baseline" if self._baseline is None else "forecast",
            median_abs_c=None,
            max_abs_c=None,
            p95_abs_c=None,
            window_hours=0.0,
            model=self._model,
            daily_model=self._daily_model,
            calibrated_at=self._calibrated_at,
            baseline_auto=bool(self._baseline_auto),
            trims={},
            clamped=False,
        )

    def _main_values(self) -> dict:
        """Merged main-coordinator values.

        The main coordinator merges the holding-register settings into the
        same ``values`` section as the metrics (``{"device", "values"}``), so
        curve type, targets and the seven points all live there.
        """
        data = self._main.data if isinstance(self._main.data, dict) else {}
        values = data.get("values")
        return values if isinstance(values, dict) else {}

    def _supply_bounds(self, values: Mapping[str, Any]) -> tuple[float, float]:
        """Effective pump supply limits, falling back to the register range."""
        return effective_supply_bounds(
            values.get("min_heating_supply"), values.get("max_heating_supply")
        )

    def _baseline_stats_values(self) -> tuple[int | None, float | None, float | None]:
        """Validated ``(hours, outdoor min, outdoor max)`` from the last fit."""
        stats = self._baseline_stats or {}
        hours = stats.get("hours")
        outdoor_min = stats.get("min_c")
        outdoor_max = stats.get("max_c")
        valid_hours = (
            int(hours)
            if isinstance(hours, (int, float)) and not isinstance(hours, bool)
            else None
        )
        valid_min = (
            float(outdoor_min)
            if isinstance(outdoor_min, (int, float))
            and not isinstance(outdoor_min, bool)
            else None
        )
        valid_max = (
            float(outdoor_max)
            if isinstance(outdoor_max, (int, float))
            and not isinstance(outdoor_max, bool)
            else None
        )
        return valid_hours, valid_min, valid_max

    def _ensure_baseline(self, values: Mapping[str, Any]) -> bool:
        """Freeze the pump's seven points once; ``True`` when newly frozen."""
        if self._baseline is not None:
            return False
        baseline = freeze_baseline(values)
        if baseline is None:
            return False
        self._baseline = baseline
        self._baseline_auto = (
            values.get("curve_type_heating") == HeatingCurveType.AUTO
        )
        value = values.get("temp_compensation_curve")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            self._curve_23 = int(value)
        _LOGGER.info(
            "Frozen heating-curve baseline from %s mode",
            "Auto" if self._baseline_auto else "User defined",
        )
        return True

    async def _async_fetch_forecast(self) -> WeatherForecast:
        latitude = self.hass.config.latitude
        longitude = self.hass.config.longitude
        if latitude is None or longitude is None:
            raise OpenMeteoError("Home Assistant location is not configured")
        return await fetch_forecast(self.session, latitude, longitude)

    def _calibration_due(self, now_ts: float) -> bool:
        """Throttle calibration attempts, not only successful ones.

        A completed attempt (fit accepted or rejected) waits the full refresh
        interval: the inputs cannot change within the hour. An attempt that
        could not fetch them retries after :data:`CALIBRATION_RETRY_HOURS`,
        so one transient network failure does not cost a day of staleness.
        """
        if self._last_calibration_ts is None:
            return True
        interval = (
            CALIBRATION_REFRESH_HOURS
            if self._last_calibration_ok is not False
            else CALIBRATION_RETRY_HOURS
        )
        return now_ts - self._last_calibration_ts >= interval * 3600

    async def _async_calibrate(self, now_ts: float) -> None:
        # Record the attempt before any IO: an attempt that cannot fetch its
        # inputs (fresh install, no history, network) must not re-fetch 60 days
        # of archive plus statistics on every 15-minute cycle.
        self._last_calibration_ts = now_ts
        self._last_calibration_ok = None
        await self._async_persist()
        latitude = self.hass.config.latitude
        longitude = self.hass.config.longitude
        if latitude is None or longitude is None:
            return
        try:
            today = dt_util.utcnow().date()
            ghi = await fetch_ghi_history(
                self.session,
                latitude,
                longitude,
                today - timedelta(days=CALIBRATION_DAYS),
                today,
            )
            maps = await self._async_hourly_maps()
        except Exception as err:  # noqa: BLE001 — calibration is best effort
            _LOGGER.debug("Solar calibration unavailable: %s", err)
            self._last_calibration_ok = False
            await self._async_persist()
            return
        # The inputs arrived, so this attempt is done whatever the fit says: a
        # rejected fit is a statement about the window, not a retryable error.
        self._last_calibration_ok = True

        # Day-block identification: the same balance over whole days, where
        # the sunniest idle hours contribute q = 0 instead of being censored
        # out of the heating-hour sample. Stored on every attempt — an invalid
        # model means "this window has no identifiable aperture", which must
        # switch the term off rather than preserve an older, unvalidated one.
        self._daily_model = fit_daily_solar_model(
            aggregate_days(
                maps.get("heatingpower", {}),
                maps.get("indoor", {}),
                maps.get("outdoor", {}),
                ghi,
            )
        )

        samples = build_samples(
            maps.get("heatingpower", {}),
            maps.get("indoor", {}),
            maps.get("outdoor", {}),
            ghi,
        )
        if len(samples) < MIN_CALIBRATION_SAMPLES:
            _LOGGER.debug(
                "Hourly solar calibration skipped: %s samples < %s",
                len(samples),
                MIN_CALIBRATION_SAMPLES,
            )
        else:
            model = fit_solar_model(samples, now_ts=int(now_ts))
            if not model.valid:
                _LOGGER.debug("Solar calibration rejected: %s", model.notes)
            else:
                self._model = model
                self._calibrated_at = dt_util.utcnow().isoformat()
                _LOGGER.info(
                    "Solar model calibrated: a=%.1f W/K b=%.3f m² trust=%.2f "
                    "(%s samples)",
                    model.a_w_per_k,
                    model.b_m2,
                    model.trust,
                    len(samples),
                )
        daily = self._daily_model
        if daily is not None:
            _LOGGER.info(
                "Daily solar model: a=%.1f W/K b=%.3f m² trust=%.2f valid=%s "
                "(%s days, %s)",
                daily.a_w_per_k,
                daily.b_m2,
                daily.trust,
                daily.valid,
                daily.n_days,
                daily.notes,
            )
        await self._async_persist()

    def _resolve_statistic_ids(self) -> dict[str, str]:
        return resolve_statistic_entity_ids(
            self.hass,
            getattr(self._main, "device_id", None),
            (
                "heatingpower",
                "bt1",
                "bt2",
                "cal_heat_temp",
                "room_temp_external",
                "room_temp_ext",
            ),
        )

    def _resolve_curve_entity_id(self, metric_key: str) -> str | None:
        return resolve_statistic_entity_ids(
            self.hass, getattr(self._main, "device_id", None), (metric_key,)
        ).get(metric_key)

    async def _async_statistics(
        self, statistic_ids: set[str], start: datetime
    ) -> dict[str, list[Any]]:
        return await async_statistics_during_period(self.hass, statistic_ids, start)

    def _indoor_series(
        self,
        resolved: Mapping[str, str],
        rows: Mapping[str, Any],
        *,
        min_hours: int = INDOOR_METRIC_MIN_HOURS,
    ) -> tuple[str | None, dict[int, float]]:
        """Indoor series for the configured sensor, else the fallback.

        A newly configured room sensor has no recorder history for its first
        hours, which would otherwise silence everything that reads the indoor
        error. ``min_hours`` is how much history this caller needs before the
        configured sensor replaces BT2.
        """
        by_key = {
            key: _hourly_series(rows, entity_id)
            for key, entity_id in indoor_metric_entity_ids(resolved).items()
        }
        key = pick_indoor_metric_key(
            resolved, self._main_values(), by_key, min_rows=min_hours
        )
        return key, (dict(by_key.get(key, {})) if key else {})

    async def _async_hourly_maps(self) -> dict[str, dict[int, float]]:
        resolved = self._resolve_statistic_ids()
        statistic_ids = set(resolved.values())
        rows = await self._async_statistics(
            statistic_ids, dt_util.utcnow() - timedelta(days=CALIBRATION_DAYS)
        )
        maps: dict[str, dict[int, float]] = {
            key: _hourly_series(rows, entity_id)
            for key, entity_id in resolved.items()
        }
        indoor_key, indoor = self._indoor_series(
            resolved, rows, min_hours=MIN_CALIBRATION_SAMPLES
        )
        if indoor_key:
            maps["indoor"] = indoor
        if "bt1" in maps:
            maps["outdoor"] = maps["bt1"]
        return maps

    async def _async_readiness_hours(self) -> tuple[dict[int, float], set[int]]:
        """Hourly baseline-vs-pump deviations for the readiness window.

        Readiness asks whether the *learned baseline* reproduces the pump, not
        whether the shadow (baseline plus the module's deliberate outdoor /
        night-day / solar / load terms) does — those terms are the point of
        activation, and judging them against Auto can never settle. Only
        heating hours enter, like the baseline fit itself: when the circuit is
        idle ``cal_heat_temp`` sits at the min-supply clamp and no longer
        describes the curve. Returns the deviations plus the hours the recorder
        covered, which is the coverage guard's denominator.
        """
        if self._baseline is None:
            return {}, set()
        resolved = self._resolve_statistic_ids()
        outdoor_id = resolved.get("bt1")
        supply_id = resolved.get("cal_heat_temp")
        if not outdoor_id or not supply_id:
            return {}, set()
        power_id = resolved.get("heatingpower")
        statistic_ids = {outdoor_id, supply_id}
        if power_id:
            statistic_ids.add(power_id)
        statistic_ids.update(indoor_metric_entity_ids(resolved).values())
        rows = await self._async_statistics(
            statistic_ids,
            dt_util.utcnow() - timedelta(days=READY_WINDOW_DAYS + 1),
        )

        outdoor = _hourly_series(rows, outdoor_id)
        supply = _hourly_series(rows, supply_id)
        power = _hourly_series(rows, power_id) if power_id else {}
        indoor_key, indoor = self._indoor_series(resolved, rows)
        target = self._main_values().get("indoor_temperature_target")
        target_c = (
            float(target)
            if isinstance(target, (int, float)) and not isinstance(target, bool)
            else None
        )
        try:
            points = normalize_baseline(self._baseline)
        except (TypeError, ValueError):
            return {}, set()
        series: dict[int, float] = {}
        for ts in sorted(set(outdoor) & set(supply)):
            if power_id and power.get(ts, 0.0) < MIN_SAMPLE_Q_W:
                continue
            value = interpolate_supply(points, outdoor[ts]) - supply[ts]
            if target_c is not None and ts in indoor and self._in_active_segment(ts):
                value = room_corrected_deviation(
                    value, self._gain_for(indoor_key), indoor[ts] - target_c
                )
            series[ts] = value
        return series, set(outdoor)

    async def _async_room_error_hours(self) -> tuple[dict[int, float], str | None]:
        """Hourly ``indoor − target`` over the readiness window, and its source.

        The outcome figure for the curve: readiness compares our baseline with
        the pump and turns self-referential once the curve is active, while
        this measures whether the rooms actually follow the target. Uses the
        same indoor resolver and fallback as the trims, so it follows whichever
        sensor the pump is being controlled by.
        """
        resolved = self._resolve_statistic_ids()
        indoor_ids = indoor_metric_entity_ids(resolved)
        if not indoor_ids:
            return {}, None
        target = self._main_values().get("indoor_temperature_target")
        if not isinstance(target, (int, float)) or isinstance(target, bool):
            return {}, None
        rows = await self._async_statistics(
            set(indoor_ids.values()),
            dt_util.utcnow() - timedelta(days=READY_WINDOW_DAYS + 1),
        )
        key, indoor = self._indoor_series(resolved, rows)
        if key is None or not indoor:
            return {}, None
        target_c = float(target)
        return {ts: value - target_c for ts, value in indoor.items()}, key

    async def _async_baseline_observations(
        self, *, since_ts: float | None = None
    ) -> list[tuple[float, float]]:
        """Hourly ``(BT1, cal_heat_temp)`` pairs from recorder statistics.

        Only hours with measured heating power are kept: when the circuit is
        idle ``cal_heat_temp`` can sit at the min-supply clamp and no longer
        describe the curve, which would drag the correction. ``since_ts``
        slices the window after the last time active control ended, so hours
        that followed our own table never enter the Auto fit.
        """
        resolved = self._resolve_statistic_ids()
        outdoor_id = resolved.get("bt1")
        supply_id = resolved.get("cal_heat_temp")
        power_id = resolved.get("heatingpower")
        if not outdoor_id or not supply_id:
            return []
        statistic_ids = {outdoor_id, supply_id}
        if power_id:
            statistic_ids.add(power_id)
        start = dt_util.utcnow() - timedelta(days=BASELINE_LEARN_DAYS)
        if since_ts is not None:
            start = max(start, datetime.fromtimestamp(since_ts, tz=timezone.utc))
        rows = await self._async_statistics(statistic_ids, start)

        def _series(entity_id: str) -> dict[int, float]:
            series: dict[int, float] = {}
            for row in rows.get(entity_id, []):
                mean = row.get("mean")
                start = row.get("start")
                if mean is None or start is None:
                    continue
                series[int(start)] = float(mean)
            return series

        outdoor = _series(outdoor_id)
        supply = _series(supply_id)
        power = _series(power_id) if power_id else {}
        observations: list[tuple[float, float]] = []
        for ts in sorted(set(outdoor) & set(supply)):
            if power_id and power.get(ts, 0.0) < MIN_SAMPLE_Q_W:
                continue
            observations.append((outdoor[ts], supply[ts]))
        return observations

    async def _async_sync_room_gain(self, values: Mapping[str, Any]) -> None:
        """Point the room correction at the pump's own factor when it has one.

        The firmware applies ``cal = table − factor · (room − target)``, so its
        ``room_comp_factor`` *is* the gain the deviation correction removes
        (measured 2026-10-15: 5.0 configured, 5.08 fitted on active hours).
        Reading it beats fitting it: no window, no evidence gate, and it
        follows the pump when the setting changes.
        """
        configured = self._configured_room_gain(values)
        if configured is not None:
            await self._async_store_room_gain(
                *configured, label="the pump's configured factor", from_factor=True
            )
            return
        if self._room_gain_from_factor:
            # The factor is gone or unusable, so the pump no longer applies a
            # room term. Keeping last cycle's number would subtract one anyway
            # — and while the curve is active the Auto-hour fit never runs to
            # replace it, so it would stay wrong indefinitely.
            await self._async_clear_room_gain()

    def _configured_room_gain(
        self, values: Mapping[str, Any]
    ) -> tuple[float, str] | None:
        """The pump's configured room-compensation gain and the sensor it uses.

        ``None`` — today's no-correction behaviour — when the factor is
        missing, zero, non-finite or above ``ROOM_GAIN_MAX_C``, and when
        ``sensor_mode`` supplies no indoor reading at all: with no reading
        there is no room term to remove, so a factor must not invent one.
        """
        factor = values.get("room_comp_factor")
        self._room_gain_factor = (
            float(factor)
            if isinstance(factor, (int, float)) and not isinstance(factor, bool)
            else None
        )
        gain = self._room_gain_factor
        if gain is None or not math.isfinite(gain) or not 0.0 < gain <= ROOM_GAIN_MAX_C:
            return None
        key = self._indoor_live_key(values)
        return None if key is None else (gain, key)

    async def _async_store_room_gain(
        self, gain: float, source: str, *, label: str, from_factor: bool = False
    ) -> None:
        if (
            gain == self._room_gain
            and source == self._room_gain_source
            and from_factor == self._room_gain_from_factor
        ):
            return
        self._room_gain = gain
        self._room_gain_source = source
        self._room_gain_from_factor = from_factor
        _LOGGER.info(
            "Room-compensation gain %.2f °C/°C for %s from %s", gain, source, label
        )
        await self._async_persist()

    async def _async_store_fitted_room_gain(self, gain: float, source: str) -> None:
        """Keep the measured slope beside whatever is applied.

        Diagnostics only: the applied gain may be the pump's configured factor,
        and the fitted number is what tells us whether that identity holds.
        """
        if gain == self._room_gain_fitted and source == self._room_gain_fitted_source:
            return
        self._room_gain_fitted = gain
        self._room_gain_fitted_source = source
        _LOGGER.debug(
            "Room-compensation gain fitted from %s: %.2f °C/°C", source, gain
        )
        await self._async_persist()

    async def _async_clear_room_gain(self) -> None:
        """Drop the stored gain: an unusable factor means no correction."""
        self._room_gain = None
        self._room_gain_source = None
        self._room_gain_from_factor = False
        _LOGGER.info("Room-compensation gain dropped: no usable pump factor")
        await self._async_persist()

    async def _async_room_gain_from_active(self) -> ActiveRoomGain | None:
        """Measure the room term on hours the pump followed **our** table.

        On Auto the pump's own control carries the room term, so a fit there
        measures a closed loop; while the curve is active our written table is
        exogenous and the room term is what moves the supply inside an outdoor
        bucket. Diagnostics only — the applied gain stays the pump's factor.

        Needs the start of the active segment (``active_since_ts``): without it
        there is no way to tell our hours from the pump's.
        """
        if self._active_since_ts is None:
            return None

        def cannot(notes: str) -> ActiveRoomGain:
            """An estimate-shaped answer for "not measurable", with the reason.

            Returning ``None`` here would leave a support dump with a bare null:
            the caller stores the object, so the dump says *why* there is no
            measurement.
            """
            return ActiveRoomGain(
                gain_c_per_c=0.0, hours=0, buckets=0, span_c=0.0, valid=False, notes=notes
            )

        resolved = self._resolve_statistic_ids()
        outdoor_id = resolved.get("bt1")
        supply_id = resolved.get("cal_heat_temp")
        if not outdoor_id or not supply_id:
            return cannot("the outdoor or flow-line statistic does not resolve")
        target = self._main_values().get("indoor_temperature_target")
        if not isinstance(target, (int, float)) or isinstance(target, bool):
            return cannot("no indoor target in the payload")
        indoor_ids = indoor_metric_entity_ids(resolved)
        if not indoor_ids:
            return cannot("no indoor metric resolves to an entity")
        power_id = resolved.get("heatingpower")
        statistic_ids = {outdoor_id, supply_id, *indoor_ids.values()}
        if power_id:
            statistic_ids.add(power_id)
        # Fetch the *full* window even though only the active segment is used:
        # the indoor picker needs its own minimum history before it resolves a
        # sensor, and a segment younger than that has fewer rows — measured
        # 2026-10-16: a 24 h segment held 23 hourly rows and the measurement
        # silently produced nothing.
        since = dt_util.utcnow() - timedelta(days=ROOM_GAIN_ACTIVE_WINDOW_DAYS)
        rows = await self._async_statistics(statistic_ids, since)
        outdoor = _hourly_series(rows, outdoor_id)
        supply = _hourly_series(rows, supply_id)
        indoor_key, indoor = self._indoor_series(resolved, rows)
        power = _hourly_series(rows, power_id) if power_id else {}
        if not indoor or indoor_key is None:
            return cannot(
                "no indoor series with enough history in the last "
                f"{ROOM_GAIN_ACTIVE_WINDOW_DAYS} days"
            )
        target_c = float(target)
        observations = [
            (outdoor[ts], supply[ts], indoor[ts] - target_c)
            for ts in sorted(set(outdoor) & set(supply) & set(indoor))
            if ts >= self._active_since_ts
            and (not power_id or power.get(ts, 0.0) >= MIN_SAMPLE_Q_W)
        ]
        estimate = room_gain_from_active_hours(observations)
        # Bind the measurement to the sensor it was taken against.
        return replace(estimate, source=indoor_key)

    async def _async_refresh_active_room_gain(self, now_ts: float) -> None:
        """Run the active-hour measurement at most every few hours."""
        if not self.active or self._active_since_ts is None:
            return
        if (
            self._room_gain_active_ts is not None
            and now_ts - self._room_gain_active_ts
            < BASELINE_LEARN_REFRESH_HOURS * 3600.0
        ):
            return
        self._room_gain_active_ts = now_ts
        try:
            estimate = await self._async_room_gain_from_active()
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 — measurement is best effort
            _LOGGER.debug("Active-hour room-gain measurement unavailable: %s", err)
            return
        if estimate is not None:
            previous = self._room_gain_active
            if estimate.valid or previous is None or not previous.valid:
                # A real measurement survives a transient "not measurable" run;
                # the reason is only kept when there is nothing better.
                self._room_gain_active = estimate
            _LOGGER.debug(
                "Room gain measured on active hours: %.2f °C/°C (valid=%s, %s)",
                estimate.gain_c_per_c,
                estimate.valid,
                estimate.notes or "no notes",
            )
        await self._async_persist()

    async def _async_room_gain(self) -> tuple[float, str] | None:
        """Identify the pump's room-compensation gain from observed Auto hours.

        While the pump runs room compensation its ``cal_heat_temp`` is the
        weather curve plus a room term, so the readiness comparison against it
        can never settle. The residual against the learned baseline is fitted
        against ``(BT1, indoor − target)``; the room slope is the gain the
        deviation correction removes. The target is not a long-term statistic,
        so the current one is applied to the whole window (it changes rarely).

        Returns the gain **and the indoor metric it was fitted against**, so a
        correction can be skipped rather than applied to another sensor's room
        error.
        """
        if self._baseline is None:
            return None
        resolved = self._resolve_statistic_ids()
        outdoor_id = resolved.get("bt1")
        supply_id = resolved.get("cal_heat_temp")
        if not outdoor_id or not supply_id:
            return None
        target = self._main_values().get("indoor_temperature_target")
        if not isinstance(target, (int, float)) or isinstance(target, bool):
            return None
        indoor_ids = indoor_metric_entity_ids(resolved)
        if not indoor_ids:
            return None
        power_id = resolved.get("heatingpower")
        statistic_ids = {outdoor_id, supply_id, *indoor_ids.values()}
        if power_id:
            statistic_ids.add(power_id)
        start = dt_util.utcnow() - timedelta(days=BASELINE_LEARN_DAYS)
        if self._last_active_ts is not None:
            start = max(
                start, datetime.fromtimestamp(self._last_active_ts, tz=timezone.utc)
            )
        rows = await self._async_statistics(statistic_ids, start)

        outdoor = _hourly_series(rows, outdoor_id)
        supply = _hourly_series(rows, supply_id)
        indoor_key, indoor = self._indoor_series(resolved, rows)
        power = _hourly_series(rows, power_id) if power_id else {}
        if not indoor or indoor_key is None:
            return None
        target_c = float(target)
        observations: list[tuple[float, float, float]] = []
        for ts in sorted(set(outdoor) & set(supply) & set(indoor)):
            if power_id and power.get(ts, 0.0) < MIN_SAMPLE_Q_W:
                continue
            observations.append(
                (outdoor[ts], supply[ts], indoor[ts] - target_c)
            )
        gain = room_compensation_gain(observations, self._baseline)
        return None if gain is None else (gain, indoor_key)

    async def _async_learn_baseline(self, values: Mapping[str, Any], now_ts: float) -> None:
        """Correct the frozen baseline from observed Auto behaviour.

        Only meaningful while the pump is on Auto (holding 22 = 0): in User
        defined ``cal_heat_temp`` would follow our own table. Observations are
        also sliced after the last time active control ended, so a recent
        active period cannot bias the fit. The correction is throttled and
        self-stabilises — once it matches the observations the residual falls
        under the noise threshold and nothing is written.
        """
        if self._baseline is None or self.active:
            return
        if values.get("curve_type_heating") != HeatingCurveType.AUTO:
            return
        if (
            self._last_baseline_learn_ts is not None
            and now_ts - self._last_baseline_learn_ts
            < BASELINE_LEARN_REFRESH_HOURS * 3600.0
        ):
            return
        self._last_baseline_learn_ts = now_ts
        await self._async_refresh_room_gain(values)
        try:
            observations = await self._async_baseline_observations(
                since_ts=self._last_active_ts
            )
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 — correction is best effort
            _LOGGER.debug("Baseline learning unavailable: %s", err)
            return
        if not observations:
            return
        outdoors = [float(outdoor) for outdoor, _ in observations]
        candidate = corrected_baseline(self._baseline, observations)
        self._baseline_stats = {
            "hours": len(observations),
            "min_c": min(outdoors),
            "max_c": max(outdoors),
            "corrected": candidate is not None,
        }
        if candidate is None:
            await self._async_persist()
            return
        self._baseline = candidate
        # The shape changed: any active trims learned against the old baseline
        # are stale (they are only recomputed while active, so clear them here).
        self._trims = {}
        _LOGGER.info(
            "Heating-curve baseline corrected from %s observed Auto hours",
            len(observations),
        )
        await self._async_persist()

    async def _async_refresh_room_gain(
        self, values: Mapping[str, Any] | None = None
    ) -> None:
        """Refresh the fitted room-compensation gain (best effort).

        The fit runs even when the pump reports a usable ``room_comp_factor``:
        it no longer decides what is applied (the factor does), but it is the
        measurement that tells us whether the factor *is* the gain — the pair
        the pooled checks compare.
        """
        configured = (
            self._configured_room_gain(values) if values is not None else None
        )
        try:
            identified = await self._async_room_gain()
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 — identification is best effort
            _LOGGER.debug("Room-compensation gain unavailable: %s", err)
            return
        if identified is None:
            return
        await self._async_store_fitted_room_gain(*identified)
        if configured is not None:
            # The pump's own factor stays applied; the fit is the cross-check.
            return
        await self._async_store_room_gain(*identified, label="observed Auto hours")

    async def _async_trim_observations(self) -> list[tuple[int, float, float]]:
        """Hourly ``(ts, BT1, indoor − target)`` pairs from recorder statistics.

        Only hours with measured heating power are kept, like the baseline fit:
        while the pump makes hot water or idles the rooms cool because nothing
        is being heated, not because the curve is wrong, and feeding those
        hours in would push every trim up. Without heating-power statistics the
        filter is skipped rather than the feature disabled.

        The target is not a long-term statistic, so the current target is
        applied to the whole window; it changes rarely and a target change only
        slows convergence for a day.
        """
        resolved = self._resolve_statistic_ids()
        outdoor_id = resolved.get("bt1")
        if not outdoor_id:
            return []
        target = self._main_values().get("indoor_temperature_target")
        if not isinstance(target, (int, float)) or isinstance(target, bool):
            return []
        indoor_ids = indoor_metric_entity_ids(resolved)
        if not indoor_ids:
            return []
        power_id = resolved.get("heatingpower")
        statistic_ids = {outdoor_id, *indoor_ids.values()}
        if power_id:
            statistic_ids.add(power_id)
        rows = await self._async_statistics(
            statistic_ids,
            dt_util.utcnow() - timedelta(days=TRIM_WINDOW_DAYS),
        )

        outdoor = _hourly_series(rows, outdoor_id)
        _, indoor = self._indoor_series(resolved, rows)
        power = _hourly_series(rows, power_id) if power_id else {}
        if not indoor:
            return []
        target_c = float(target)
        return [
            (ts, outdoor[ts], indoor[ts] - target_c)
            for ts in sorted(set(outdoor) & set(indoor))
            if not power_id or power.get(ts, 0.0) >= MIN_SAMPLE_Q_W
        ]

    async def _async_update_trims(self, result: Any, now_ts: float) -> None:
        """Slowly fold indoor-error residuals into per-point trims.

        Runs only while active: in shadow the baseline learning owns the
        shape, and adding trims there would double-correct. The update is a
        damped step toward the indicated residual, so it converges and stops
        when the error is gone; the indoor cap and the shared-adjustment clamp
        are respected rather than fought.
        """
        if self._baseline is None or not self.active or result is None:
            return
        if result.capped_by_indoor or abs(result.adjustment_c) >= TOTAL_MAX_C - 1e-9:
            return
        interval_hours = (
            TRIM_REFRESH_HOURS if self._trims else TRIM_FIRST_REFRESH_HOURS
        )
        if (
            self._last_trim_ts is not None
            and now_ts - self._last_trim_ts < interval_hours * 3600.0
        ):
            return
        self._last_trim_ts = now_ts
        try:
            observations = await self._async_trim_observations()
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 — trims are best effort
            _LOGGER.debug("Trim update unavailable: %s", err)
            return
        if not observations:
            return
        # Recorded before the fit: when there is no residual the evidence is
        # exactly what explains the empty trims.
        evidence = trim_evidence_hours(observations)
        evidence_changed = evidence != self._trim_evidence
        self._trim_evidence = evidence
        residual = trim_residuals(self._baseline, observations, int(now_ts))
        if residual is None:
            if evidence_changed:
                await self._async_persist()
            return
        changed = False
        for key, value in residual.items():
            old = self._trims.get(key, 0.0)
            new = max(-TRIM_MAX_C, min(TRIM_MAX_C, old + TRIM_UPDATE_GAIN * value))
            if abs(new - old) > 1e-9:
                self._trims[key] = new
                changed = True
        if not changed and not evidence_changed:
            return
        if changed:
            _LOGGER.info(
                "Adaptive-curve trims updated from %s indoor-error hours",
                len(observations),
            )
        await self._async_persist()

    def _indoor_live_key(self, values: Mapping[str, Any]) -> str | None:
        """Metric key the live payload supplies the indoor temperature from.

        Same preference order as the statistics resolver, but read from the
        poll payload rather than the recorder.
        """
        mode = values.get("sensor_mode")
        if mode is None:
            mode = values.get("use_operation_sensor")
        for key in (*SensorMode.current_temperature_keys(mode), "bt2"):
            value = values.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return key
        return None

    def _indoor_value(self, values: Mapping[str, Any]) -> float | None:
        """Indoor temperature from the configured sensor, else BT2."""
        key = self._indoor_live_key(values)
        return None if key is None else float(values[key])

    def _gain_for(self, indoor_key: str | None) -> float | None:
        """The room gain, but only for the sensor it was learned from.

        The statistics picker can fall back to BT2 while a newly configured
        sensor has no history, and the live payload can report yet another
        sensor. Subtracting one sensor's room term from another's error would
        be worse than not correcting at all, so an unknown source means no
        correction.
        """
        if indoor_key is not None and indoor_key == self._room_gain_source:
            return self._room_gain
        return None

    def _in_active_segment(self, ts: float) -> bool:
        """True when ``cal_heat_temp`` at ``ts`` followed the table we wrote.

        The room correction is only valid there. On Auto hours the pump's own
        control already carries the room term — and the learned baseline was
        fitted to it — so subtracting the gain again double-counts: measured
        2026-10-15, applying it to every readiness hour moved the median from
        1.17 °C to 2.67 °C (gate failure) while applying it to the active hours
        only moved it to 1.08 °C (pass).

        Only the last active segment is known: the store holds when it started
        and when it ended, not a full history.
        """
        if self._active_since_ts is None or ts < self._active_since_ts:
            return False
        if self.active:
            return True
        return self._last_active_ts is not None and ts < self._last_active_ts

    def _room_error(self, values: Mapping[str, Any]) -> float | None:
        """Indoor temperature minus the target, or ``None`` when unknown."""
        indoor = self._indoor_value(values)
        target = values.get("indoor_temperature_target")
        if indoor is None or not isinstance(target, (int, float)) or isinstance(
            target, bool
        ):
            return None
        return indoor - float(target)

    def _push_indoor_margin(self, values: Mapping[str, Any], now_ts: float) -> float | None:
        error = self._room_error(values)
        if error is not None:
            self._indoor_margins.append((now_ts, error))
        recent = [
            margin
            for ts, margin in self._indoor_margins
            if now_ts - ts <= 24 * 3600.0
        ]
        if not recent:
            return None
        return sum(recent) / len(recent)

    def _push_power(self, values: Mapping[str, Any]) -> float | None:
        if values.get("hp_status") != HP_STATUS_HEATING:
            self._powers.clear()
            return None
        power = values.get("heatingpower")
        if isinstance(power, (int, float)) and not isinstance(power, bool):
            self._powers.append(float(power))
        if not self._powers:
            return None
        return sum(self._powers) / len(self._powers)

    def _cop_feedback_c(
        self,
        values: Mapping[str, Any],
        previous: CurveSnapshot | None,
    ) -> float:
        """COP-feedback term for this cycle, or 0 when it must not act.

        The reference is learned per 5 °C outdoor bucket whenever the term is
        not actively correcting, so enabling it later starts from real
        history instead of from nothing. The term only applies while the curve
        is active, the pump is heating, the previous cycle was not capped by
        the indoor margin, and the shared adjustment still has headroom — it
        must never fight the indoor cap. A correcting cycle freezes the
        reference so the loop has a stable target until COP recovers.
        """
        cop = values.get("cop_heating")
        outdoor = values.get("bt1")
        if isinstance(cop, bool) or not isinstance(cop, (int, float)):
            return 0.0
        if isinstance(outdoor, bool) or not isinstance(outdoor, (int, float)):
            return 0.0
        cop_value = float(cop)
        if not math.isfinite(cop_value) or cop_value <= 0.0:
            return 0.0
        if values.get("hp_status") != HP_STATUS_HEATING:
            return 0.0

        bucket = int(
            round(float(outdoor) / COP_REFERENCE_BUCKET_C) * COP_REFERENCE_BUCKET_C
        )
        reference = self._cop_reference.get(bucket)
        samples = self._cop_samples.get(bucket, 0)
        term = 0.0
        if (
            self._terms.get("cop_feedback")
            and self.active
            and previous is not None
            and not previous.capped_by_indoor
            and abs(previous.adjustment_c) < TOTAL_MAX_C
            and samples >= COP_REFERENCE_MIN_SAMPLES
        ):
            term = cop_feedback_adjustment_c(cop_value, reference)

        if term == 0.0 or reference is None:
            if reference is None:
                self._cop_reference[bucket] = cop_value
            else:
                self._cop_reference[bucket] = (
                    COP_REFERENCE_ALPHA * cop_value
                    + (1.0 - COP_REFERENCE_ALPHA) * reference
                )
            self._cop_samples[bucket] = samples + 1
            if self._store is not None:
                try:
                    self._store.async_delay_save(self._state_payload, 600)
                except Exception:
                    _LOGGER.debug(
                        "Failed to schedule curve COP-reference save", exc_info=True
                    )
        return term

    def _precharge_c(
        self,
        forecast_temperature: Mapping[int, float],
        now_ts: float,
        previous: CurveSnapshot | None,
    ) -> float:
        """Pre-charge term for this cycle, or 0 when it must not act.

        Only raises the curve while active and only when the previous cycle
        was not already capped or at the positive total clamp. The indoor cap
        inside ``compute_curve`` stays the final veto for a warm house, and
        the pump's max supply clamp bounds the points regardless.
        """
        if not self._terms.get("precharge") or not self.active:
            return 0.0
        if previous is not None and (
            previous.capped_by_indoor or previous.adjustment_c >= TOTAL_MAX_C
        ):
            return 0.0
        return precharge_adjustment_c(forecast_temperature, int(now_ts))

    def _daylight(self, now: datetime) -> DayPhase | None:
        try:
            from homeassistant.helpers.sun import (
                SUN_EVENT_SUNRISE,
                SUN_EVENT_SUNSET,
                get_astral_event_date,
            )

            today = dt_util.as_local(now).date()
            sunrise = get_astral_event_date(self.hass, SUN_EVENT_SUNRISE, today)
            sunset = get_astral_event_date(self.hass, SUN_EVENT_SUNSET, today)
            previous_sunset = get_astral_event_date(
                self.hass, SUN_EVENT_SUNSET, today - timedelta(days=1)
            )
            next_sunrise = get_astral_event_date(
                self.hass, SUN_EVENT_SUNRISE, today + timedelta(days=1)
            )
        except Exception:  # noqa: BLE001 — no location/astral data
            return None
        dates = (sunrise, sunset, previous_sunset, next_sunrise)
        if any(value is None for value in dates):
            return None
        return DayPhase(
            sunrise_ts=int(sunrise.timestamp()),
            sunset_ts=int(sunset.timestamp()),
            previous_sunset_ts=int(previous_sunset.timestamp()),
            next_sunrise_ts=int(next_sunrise.timestamp()),
        )

    async def _async_compute_snapshot(self) -> CurveSnapshot:
        values = self._main_values()
        now = dt_util.utcnow()
        now_ts = now.timestamp()

        if self._ensure_baseline(values):
            await self._async_persist()

        # The pump's own factor is the gain whenever it reports one; the fitted
        # fallback runs during baseline learning below.
        await self._async_sync_room_gain(values)
        # …and while the curve is active, measure the term on our own hours.
        await self._async_refresh_active_room_gain(now_ts)

        # Learn what Auto actually delivers while the pump still follows it;
        # once active, cal_heat_temp would mirror our own table.
        await self._async_learn_baseline(values, now_ts)

        self._forecast_ok = False
        if self._baseline is not None:
            try:
                self._forecast = await self._async_fetch_forecast()
                self._forecast_ok = True
            except OpenMeteoError as err:
                _LOGGER.debug("Curve forecast unavailable: %s", err)

        if self._calibration_due(now_ts):
            await self._async_calibrate(now_ts)

        forecast_temperature = (
            self._forecast.temperature_by_hour() if self._forecast else {}
        )
        ghi_by_hour = self._forecast.ghi_by_hour() if self._forecast else {}
        indoor_margin = self._push_indoor_margin(values, now_ts)
        if indoor_margin is not None and self._store is not None:
            try:
                self._store.async_delay_save(self._state_payload, 600)
            except Exception:
                _LOGGER.debug("Failed to schedule curve state save", exc_info=True)
        q_actual = self._push_power(values)
        target = values.get("indoor_temperature_target")
        indoor_target = (
            float(target)
            if isinstance(target, (int, float)) and not isinstance(target, bool)
            else None
        )

        result = None
        low, high = self._supply_bounds(values)
        # Use the previous cycle for the term's guard conditions: the indoor
        # cap and total clamp it must not fight are those already published.
        cop_c = self._cop_feedback_c(values, self.data)
        precharge_c = self._precharge_c(
            forecast_temperature, now_ts, self.data
        )
        if self._baseline is not None:
            result = compute_curve(
                baseline=self._baseline,
                now_ts=int(now_ts),
                forecast_temperature=forecast_temperature,
                ghi_by_hour=ghi_by_hour,
                model=self._model,
                daily_model=self._daily_model,
                daylight=self._daylight(now),
                indoor_margin_c=indoor_margin,
                indoor_target_c=indoor_target,
                q_actual_w=q_actual,
                point_trims=self._trims if self.active else None,
                min_supply_c=low,
                max_supply_c=high,
                cop_c=cop_c,
                precharge_c=precharge_c,
            )
        if result is not None:
            clamped = self.active and result.clamped
            if clamped != self._clamped_state:
                if clamped:
                    _LOGGER.warning(
                        "Adaptive-curve points clamped to the pump's supply "
                        "limits (%.0f–%.0f °C)",
                        low,
                        high,
                    )
                self._clamped_state = clamped

        async with self._control_lock:
            if self._revert_pending:
                await self._async_retry_revert()
            if self.active and result is not None:
                await self._async_apply_active(result)
            # Serialized with deactivation: a revert clears the trims, and an
            # update awaited outside this lock could resurrect them.
            if result is not None:
                await self._async_update_trims(result, now_ts)

        shadow = values.get("curve_type_heating") != HeatingCurveType.USER_DEFINED
        deviation = None
        if result is not None:
            measured_outdoor = values.get("bt1")
            if not isinstance(measured_outdoor, (int, float)) or isinstance(
                measured_outdoor, bool
            ):
                measured_outdoor = forecast_temperature.get(
                    int(now_ts) - (int(now_ts) % 3600)
                )
            cal_heat_temp = values.get("cal_heat_temp")
            if isinstance(cal_heat_temp, bool) or not isinstance(
                cal_heat_temp, (int, float)
            ):
                cal_heat_temp = None
            deviation = curve_deviation_c(
                result.as_dict(),
                None if measured_outdoor is None else float(measured_outdoor),
                None if cal_heat_temp is None else float(cal_heat_temp),
            )
            # The pump adds its room term to cal_heat_temp, so the raw
            # deviation carries a room-dependent part the shadow curve does not
            # emit; remove it while the pump follows our table, which is the
            # only regime where the term means the same thing.
            deviation = room_corrected_deviation(
                deviation,
                self._gain_for(self._indoor_live_key(values))
                if self._in_active_segment(now_ts)
                else None,
                self._room_error(values),
            )

        signal = signal_blocker(
            has_baseline=self._baseline is not None,
            forecast_ok=self._forecast_ok,
            history_ok=self._model is not None and self._model.valid,
        )
        time_zone = dt_util.get_default_time_zone()
        readiness_hours, covered_hours = await self._async_readiness_hours()
        readiness = assess_readiness(
            readiness_hours,
            int(now_ts),
            time_zone,
            signal=signal,
            covered_hours=covered_hours,
        )
        room_errors, room_source = await self._async_room_error_hours()
        room = room_error_summary(room_errors, int(now_ts), time_zone)

        points = result.as_dict() if result is not None else {}
        baseline = dict(self._baseline) if self._baseline else {}
        learned_hours, learned_min, learned_max = self._baseline_stats_values()
        extrapolated: tuple[str, ...] = ()
        if learned_min is not None and learned_max is not None:
            extrapolated = tuple(
                key
                for key, outdoor in HEATING_CURVE_OUTDOOR_TEMPS.items()
                if not learned_min <= outdoor <= learned_max
            )
        active_trims = (
            dict(result.trims) if self.active and result is not None else {}
        )
        return CurveSnapshot(
            shadow=shadow,
            points=points,
            baseline=baseline,
            adjustment_c=0.0 if result is None else result.adjustment_c,
            outdoor_c=0.0 if result is None else result.outdoor_c,
            night_day_c=0.0 if result is None else result.night_day_c,
            solar_c=0.0 if result is None else result.solar_c,
            load_c=0.0 if result is None else result.load_c,
            capped_by_indoor=False if result is None else result.capped_by_indoor,
            deviation_c=deviation,
            ready=readiness.ready,
            blocker=readiness.blocker,
            median_abs_c=readiness.median_abs_c,
            max_abs_c=readiness.max_abs_c,
            p95_abs_c=readiness.p95_abs_c,
            window_hours=readiness.window_hours,
            room_error_median_abs_c=None if room is None else room.median_abs_c,
            room_error_median_c=None if room is None else room.median_c,
            room_error_p90_abs_c=None if room is None else room.p90_abs_c,
            room_error_samples=0 if room is None else room.samples,
            room_error_source=room_source,
            indoor_margin_c=indoor_margin,
            indoor_cap_armed=indoor_margin is not None,
            model=self._model,
            daily_model=self._daily_model,
            calibrated_at=self._calibrated_at,
            baseline_auto=bool(self._baseline_auto),
            trims=active_trims,
            clamped=bool(result is not None and result.clamped),
            baseline_learned_hours=learned_hours,
            baseline_outdoor_min_c=learned_min,
            baseline_outdoor_max_c=learned_max,
            room_gain_c=self._room_gain,
            trim_evidence=dict(self._trim_evidence),
            extrapolated_points=extrapolated,
            cop_c=0.0 if result is None else result.cop_c,
            precharge_c=0.0 if result is None else result.precharge_c,
        )
