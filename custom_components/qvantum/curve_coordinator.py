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
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
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
    TOTAL_MAX_C,
    TRIM_MAX_C,
    DayPhase,
    compute_curve,
    corrected_baseline,
    effective_supply_bounds,
    interpolate_supply,
    normalize_baseline,
    trim_residuals,
)
from .open_meteo import (
    OpenMeteoError,
    WeatherForecast,
    fetch_forecast,
    fetch_ghi_history,
)
from .solar_gain import SolarModel, SolarSample, fit_solar_model

_LOGGER = logging.getLogger(__name__)

CURVE_KEYS: tuple[str, ...] = tuple(HEATING_CURVE_OUTDOOR_TEMPS)

UPDATE_INTERVAL = timedelta(minutes=15)
CALIBRATION_DAYS = 60
CALIBRATION_REFRESH_HOURS = 24.0
MIN_CALIBRATION_SAMPLES = 72
MIN_SAMPLE_DELTA_T_K = 5.0
MIN_SAMPLE_Q_W = 50.0
#: Days of observed ``(BT1, cal_heat_temp)`` used to correct the baseline.
BASELINE_LEARN_DAYS = 7
#: Minimum time between baseline-correction statistics fetches.
BASELINE_LEARN_REFRESH_HOURS = 6.0
#: Rolling window of indoor-error hours behind the active trims.
TRIM_WINDOW_DAYS = 7
#: Minimum time between trim updates.
TRIM_REFRESH_HOURS = 24.0
#: Fraction of the indicated residual applied per update (damped loop).
TRIM_UPDATE_GAIN = 0.3
INDOOR_MARGIN_CYCLES = 96  # 24 h at 15 min
POWER_CYCLES = 4  # 1 h at 15 min
READY_WINDOW_DAYS = 3
READY_MIN_COVERAGE = 0.9
READY_MEDIAN_MAX_C = 1.0
READY_MAX_ABS_C = 3.0
STORAGE_VERSION = 1

#: Pause between single-point Modbus writes so one cycle is not a burst.
WRITE_PAUSE_SECONDS = 1.0
#: Minimum change in °C before a point is rewritten.
MIN_WRITE_DELTA_C = 1.0
CONTROL_MODES = ("shadow", "active")


@dataclass(frozen=True)
class CurveReadiness:
    """Whether shadow mode has looked good enough to suggest activation."""

    ready: bool
    blocker: str | None
    median_abs_c: float | None
    max_abs_c: float | None
    window_hours: float


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
    baseline_auto: bool = False
    trims: Mapping[str, float] = field(default_factory=dict)
    clamped: bool = False
    baseline_learned_hours: int | None = None
    baseline_outdoor_min_c: float | None = None
    baseline_outdoor_max_c: float | None = None


def freeze_baseline(settings: Mapping[str, Any]) -> dict[str, float] | None:
    """Copy the seven pump points into a baseline, or ``None`` if incomplete."""
    baseline: dict[str, float] = {}
    for key in CURVE_KEYS:
        value = settings.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        baseline[key] = float(value)
    return baseline


def build_samples(
    q_by_hour: Mapping[int, float],
    indoor_by_hour: Mapping[int, float],
    outdoor_by_hour: Mapping[int, float],
    ghi_by_hour: Mapping[int, float],
    *,
    min_delta_t_k: float = MIN_SAMPLE_DELTA_T_K,
    min_q_w: float = MIN_SAMPLE_Q_W,
) -> list[SolarSample]:
    """Join hourly maps into heating samples for the solar fit.

    Hours without heating demand are dropped: the model needs hours where
    the heating circuit actually delivered heat.
    """
    samples: list[SolarSample] = []
    for ts in sorted(set(q_by_hour) & set(indoor_by_hour) & set(outdoor_by_hour) & set(ghi_by_hour)):
        delta_t = indoor_by_hour[ts] - outdoor_by_hour[ts]
        if delta_t < min_delta_t_k:
            continue
        q_heat = q_by_hour[ts]
        if q_heat < min_q_w:
            continue
        samples.append(
            SolarSample(
                hour_ts=int(ts),
                delta_t_k=delta_t,
                ghi_wm2=max(0.0, ghi_by_hour[ts]),
                q_heat_w=q_heat,
            )
        )
    return samples


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


def assess_readiness(
    hourly_deviations: Mapping[int, float],
    now_ts: int,
    tz: ZoneInfo,
    *,
    signal: str | None = None,
) -> CurveReadiness:
    """Assess the shadow window over at least three full local days.

    ``signal`` carries the missing-signal blocker (baseline/forecast/history)
    computed by the caller; window/value blockers are added here.
    """
    if signal is not None:
        return CurveReadiness(False, signal, None, None, 0.0)

    local_now = datetime.fromtimestamp(now_ts, tz)
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = (day_start - timedelta(days=READY_WINDOW_DAYS)).timestamp()
    window_hours = (now_ts - start) / 3600.0
    expected = max(1, int((now_ts - start) // 3600))
    values = [
        float(value)
        for ts, value in hourly_deviations.items()
        if start <= ts < now_ts
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    ]
    if len(values) / expected < READY_MIN_COVERAGE:
        return CurveReadiness(False, "window", None, None, round(window_hours, 1))

    absolute = sorted(abs(value) for value in values)
    median_abs = _median_sorted(absolute)
    max_abs = absolute[-1]
    if median_abs > READY_MEDIAN_MAX_C:
        return CurveReadiness(False, "median", round(median_abs, 2), round(max_abs, 2), round(window_hours, 1))
    if max_abs > READY_MAX_ABS_C:
        return CurveReadiness(False, "max", round(median_abs, 2), round(max_abs, 2), round(window_hours, 1))
    return CurveReadiness(True, None, round(median_abs, 2), round(max_abs, 2), round(window_hours, 1))


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
            store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.curve.{config_entry.entry_id}")
        self._store = store
        self._baseline: dict[str, float] | None = None
        self._baseline_auto: bool | None = None
        self._curve_23: int | None = None
        self._model: SolarModel | None = None
        self._calibrated_at: str | None = None
        self._last_calibration_ts: float | None = None
        self._last_baseline_learn_ts: float | None = None
        self._last_active_ts: float | None = None
        self._trims: dict[str, float] = {}
        self._last_trim_ts: float | None = None
        self._baseline_stats: dict[str, Any] | None = None
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
    def active(self) -> bool:
        """True when the curve writes the pump (holding 22 is ours)."""
        return self._mode == "active"

    @property
    def writable(self) -> bool:
        """True when the Modbus client accepts holding writes."""
        client = getattr(self._main, "client", None)
        return bool(getattr(client, "writable", False))

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
            "calibrated_at": self._calibrated_at,
            "calibrated_ts": self._last_calibration_ts,
            "last_active_ts": self._last_active_ts,
            "trims": dict(self._trims),
            "baseline_stats": self._baseline_stats,
            "indoor_margins": [[ts, margin] for ts, margin in self._indoor_margins],
            "mode": self._mode,
            "revert_pending": self._revert_pending,
        }

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
        calibrated_at = data.get("calibrated_at")
        self._calibrated_at = calibrated_at if isinstance(calibrated_at, str) else None
        calibrated_ts = data.get("calibrated_ts")
        if isinstance(calibrated_ts, (int, float)) and not isinstance(calibrated_ts, bool):
            self._last_calibration_ts = float(calibrated_ts)
        last_active_ts = data.get("last_active_ts")
        if isinstance(last_active_ts, (int, float)) and not isinstance(
            last_active_ts, bool
        ):
            self._last_active_ts = float(last_active_ts)
        trims = data.get("trims")
        if isinstance(trims, dict):
            for key, value in trims.items():
                if (
                    key in CURVE_KEYS
                    and isinstance(value, (int, float))
                    and not isinstance(value, bool)
                ):
                    self._trims[key] = float(value)
        stats = data.get("baseline_stats")
        if isinstance(stats, dict):
            self._baseline_stats = {
                key: stats.get(key) for key in ("hours", "min_c", "max_c", "corrected")
            }
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
            window_hours=0.0,
            model=self._model,
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
        """Throttle calibration attempts, not only successful ones."""
        if self._last_calibration_ts is None:
            return True
        return now_ts - self._last_calibration_ts >= CALIBRATION_REFRESH_HOURS * 3600

    async def _async_calibrate(self, now_ts: float) -> None:
        # Record the attempt before any IO: a failed or skipped calibration
        # (fresh install, no history, invalid fit) must not re-fetch 60 days
        # of archive plus statistics on every 15-minute cycle.
        self._last_calibration_ts = now_ts
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
            return

        samples = build_samples(
            maps.get("heatingpower", {}),
            maps.get("indoor", {}),
            maps.get("outdoor", {}),
            ghi,
        )
        if len(samples) < MIN_CALIBRATION_SAMPLES:
            _LOGGER.debug(
                "Solar calibration skipped: %s samples < %s",
                len(samples),
                MIN_CALIBRATION_SAMPLES,
            )
            return
        model = fit_solar_model(samples, now_ts=int(now_ts))
        if not model.valid:
            _LOGGER.debug("Solar calibration rejected: %s", model.notes)
            return
        self._model = model
        self._calibrated_at = dt_util.utcnow().isoformat()
        _LOGGER.info(
            "Solar model calibrated: a=%.1f W/K b=%.3f m² trust=%.2f (%s samples)",
            model.a_w_per_k,
            model.b_m2,
            model.trust,
            len(samples),
        )
        await self._async_persist()

    def _resolve_statistic_ids(self) -> dict[str, str]:
        device_id = getattr(self._main, "device_id", None)
        if not device_id:
            return {}
        registry = er.async_get(self.hass)
        resolved: dict[str, str] = {}
        for key in (
            "heatingpower",
            "bt1",
            "bt2",
            "cal_heat_temp",
            "room_temp_external",
            "room_temp_ext",
        ):
            entity_id = registry.async_get_entity_id(
                "sensor", DOMAIN, f"qvantum_{key}_{device_id}"
            )
            if entity_id:
                resolved[key] = entity_id
        return resolved

    def _resolve_curve_entity_id(self, metric_key: str) -> str | None:
        device_id = getattr(self._main, "device_id", None)
        if not device_id:
            return None
        registry = er.async_get(self.hass)
        return registry.async_get_entity_id(
            "sensor", DOMAIN, f"qvantum_{metric_key}_{device_id}"
        )

    async def _async_statistics(
        self, statistic_ids: set[str], start: datetime
    ) -> dict[str, list[Any]]:
        if not statistic_ids:
            return {}
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import statistics_during_period

        try:
            get_instance(self.hass)
        except (KeyError, RuntimeError):
            return {}
        try:
            return await self.hass.async_add_executor_job(
                statistics_during_period,
                self.hass,
                start,
                None,
                statistic_ids,
                "hour",
                None,
                {"mean"},
            )
        except Exception as err:  # noqa: BLE001 — recorder may be unavailable
            _LOGGER.debug("Recorder statistics unavailable: %s", err)
            return {}

    def _indoor_metric_key(self, resolved: Mapping[str, str]) -> str | None:
        values = self._main_values()
        mode = values.get("sensor_mode")
        if mode is None:
            mode = values.get("use_operation_sensor")
        for key in (*SensorMode.current_temperature_keys(mode), "bt2"):
            if key in resolved:
                return key
        return None

    async def _async_hourly_maps(self) -> dict[str, dict[int, float]]:
        resolved = self._resolve_statistic_ids()
        statistic_ids = set(resolved.values())
        rows = await self._async_statistics(
            statistic_ids, dt_util.utcnow() - timedelta(days=CALIBRATION_DAYS)
        )
        maps: dict[str, dict[int, float]] = {}
        for key, entity_id in resolved.items():
            series: dict[int, float] = {}
            for row in rows.get(entity_id, []):
                mean = row.get("mean")
                start = row.get("start")
                if mean is None or start is None:
                    continue
                series[int(start)] = float(mean)
            maps[key] = series
        indoor_key = self._indoor_metric_key(resolved)
        if indoor_key:
            maps["indoor"] = maps[indoor_key]
        if "bt1" in maps:
            maps["outdoor"] = maps["bt1"]
        return maps

    async def _async_deviation_hours(self) -> dict[int, float]:
        entity_id = self._resolve_curve_entity_id("adaptive_curve_deviation")
        if not entity_id:
            return {}
        rows = await self._async_statistics(
            {entity_id},
            dt_util.utcnow() - timedelta(days=READY_WINDOW_DAYS + 1),
        )
        series: dict[int, float] = {}
        for row in rows.get(entity_id, []):
            mean = row.get("mean")
            start = row.get("start")
            if mean is None or start is None:
                continue
            series[int(start)] = float(mean)
        return series

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

    async def _async_trim_observations(self) -> list[tuple[int, float, float]]:
        """Hourly ``(ts, BT1, indoor − target)`` pairs from recorder statistics.

        The target is not a long-term statistic, so the current target is
        applied to the whole window; it changes rarely and a target change only
        slows convergence for a day.
        """
        resolved = self._resolve_statistic_ids()
        outdoor_id = resolved.get("bt1")
        indoor_key = self._indoor_metric_key(resolved)
        indoor_id = resolved.get(indoor_key) if indoor_key else None
        if not outdoor_id or not indoor_id:
            return []
        target = self._main_values().get("indoor_temperature_target")
        if not isinstance(target, (int, float)) or isinstance(target, bool):
            return []
        rows = await self._async_statistics(
            {outdoor_id, indoor_id},
            dt_util.utcnow() - timedelta(days=TRIM_WINDOW_DAYS),
        )

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
        indoor = _series(indoor_id)
        target_c = float(target)
        return [
            (ts, outdoor[ts], indoor[ts] - target_c)
            for ts in sorted(set(outdoor) & set(indoor))
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
        if (
            self._last_trim_ts is not None
            and now_ts - self._last_trim_ts < TRIM_REFRESH_HOURS * 3600.0
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
        residual = trim_residuals(self._baseline, observations, int(now_ts))
        if residual is None:
            return
        changed = False
        for key, value in residual.items():
            old = self._trims.get(key, 0.0)
            new = max(-TRIM_MAX_C, min(TRIM_MAX_C, old + TRIM_UPDATE_GAIN * value))
            if abs(new - old) > 1e-9:
                self._trims[key] = new
                changed = True
        if not changed:
            return
        _LOGGER.info(
            "Adaptive-curve trims updated from %s indoor-error hours",
            len(observations),
        )
        await self._async_persist()

    def _indoor_value(self, values: Mapping[str, Any]) -> float | None:
        """Indoor temperature from the configured sensor, else BT2."""
        mode = values.get("sensor_mode")
        if mode is None:
            mode = values.get("use_operation_sensor")
        for key in (*SensorMode.current_temperature_keys(mode), "bt2"):
            value = values.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
        return None

    def _push_indoor_margin(self, values: Mapping[str, Any], now_ts: float) -> float | None:
        indoor = self._indoor_value(values)
        target = values.get("indoor_temperature_target")
        if indoor is not None and isinstance(target, (int, float)) and not isinstance(target, bool):
            self._indoor_margins.append((now_ts, indoor - float(target)))
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
        if self._baseline is not None:
            result = compute_curve(
                baseline=self._baseline,
                now_ts=int(now_ts),
                forecast_temperature=forecast_temperature,
                ghi_by_hour=ghi_by_hour,
                model=self._model,
                daylight=self._daylight(now),
                indoor_margin_c=indoor_margin,
                indoor_target_c=indoor_target,
                q_actual_w=q_actual,
                point_trims=self._trims if self.active else None,
                min_supply_c=low,
                max_supply_c=high,
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

        signal = signal_blocker(
            has_baseline=self._baseline is not None,
            forecast_ok=self._forecast_ok,
            history_ok=self._model is not None and self._model.valid,
        )
        readiness = assess_readiness(
            await self._async_deviation_hours(),
            int(now_ts),
            dt_util.get_default_time_zone(),
            signal=signal,
        )

        points = result.as_dict() if result is not None else {}
        baseline = dict(self._baseline) if self._baseline else {}
        learned_hours, learned_min, learned_max = self._baseline_stats_values()
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
            window_hours=readiness.window_hours,
            model=self._model,
            calibrated_at=self._calibrated_at,
            baseline_auto=bool(self._baseline_auto),
            trims=active_trims,
            clamped=bool(result is not None and result.clamped),
            baseline_learned_hours=learned_hours,
            baseline_outdoor_min_c=learned_min,
            baseline_outdoor_max_c=learned_max,
        )
