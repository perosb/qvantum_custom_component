"""Self-calibrating solar-gain identification core.

Pure Python — no Home Assistant imports. Ported and reworked from the
standalone ``docs/solar_heat_forecast.py`` v3 script: the two-stage
identification is kept, while the binary trust/share policy (soft levels,
gain thresholds, ``FitTrust`` enum) is replaced by a continuous,
uncertainty-weighted solar term.

Model, fitted over heating hours only::

    q_heat_w ≈ a · ΔT − b · GHI + c

Stage 1 fits ``a``/``c`` on opaque (low-irradiance) hours where GHI carries
no signal. Stage 2 fits ``b`` through the origin on the daytime residual
``(a·ΔT + c) − q`` against GHI. Both stages use exponential recency weights
and a robust (studentised-residual) pass, so one bad hour cannot swing the
fit and the model follows the current season.

``trust`` is continuous in [0, 1]: the shrinkage factor from ``b``'s t-stat
(``t² / (t² + T²)``). A weak ``b`` (low t-stat) contributes almost nothing to
the solar offset — no manual calibration and no hard "weak b" gate. The
residual R² is reported for diagnostics but not used for trust: in-sample R²
is upward-biased for small samples and would make a handful of rows look
more trustworthy than the evidence supports. Callers convert the returned
watts to supply-temperature degrees; this module never writes to a heat
pump.

Heating hours are a *selected* sample: they are the hours the pump itself
decided to heat, and on a sunny day its room term has already stopped the
circuit. The sunniest hours are therefore censored away, and at hour
resolution the control response (recovery runs in the low-sun hours)
dominates the fit — measured on a live pump the slope comes out *negative*
(−0.9 m², t = −8.7 at 1 h). :func:`fit_daily_solar_model` is the same
balance identified on UTC day blocks instead, where the idle hours the sun
bought contribute ``q = 0`` rather than being dropped::

    Q_day ≈ a · ΣΔT + 24c − b · ΣGHI     (Σ over all hours of the day)

It is gated on evidence (day count, t-stat, R², half-window agreement) and
returns an invalid model — no solar term at all — whenever a gate fails.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

#: Recency half-life for the exponential weights (effective memory ≈ 60 days).
HALF_LIFE_DAYS: float = 30.0
#: GHI below this is considered opaque (night / heavy overcast) for stage 1.
OPAQUE_GHI_MAX_WM2: float = 30.0
#: Reference t-stat for the shrinkage factor t² / (t² + T²).
T_STAT_REFERENCE: float = 2.0
#: Minimum rows for an identified stage (needed for a t-stat / residual scale).
MIN_FIT_ROWS: int = 3
#: Huber-style cutoff for studentised residuals (one robust pass per stage).
ROBUST_CUTOFF: float = 4.0
#: Median absolute deviation → Gaussian-consistent scale.
MAD_SCALE: float = 1.4826
#: Default exponential decay for the effective-GHI kernel.
DEFAULT_TAU_HOURS: float = 3.0
#: Default one-sided span of the effective-GHI kernel.
DEFAULT_SPAN_HOURS: int = 6
#: Minimum indoor-outdoor spread for a heating hour to carry signal.
MIN_SAMPLE_DELTA_T_K: float = 5.0
#: Minimum measured heating power for a heating hour to carry signal.
MIN_SAMPLE_Q_W: float = 50.0

# --- Day-block identification -------------------------------------------------

#: Day blocks are the smallest window where the fit describes the house rather
#: than the pump's control response: with idle hours counted at hour resolution
#: the fitted slope flips sign (measured −0.90 ± 0.10 m² at 1 h on a live pump).
DAILY_BLOCK_HOURS: int = 24
#: Joined hours a day needs before its balance means anything.
DAILY_MIN_HOURS: int = 20
#: Real heating hours a day needs, else it is a summer day whose zero demand
#: says nothing about the envelope. Measured: including those days collapses
#: the aperture to 0.27 m² (t = 0.6) over 59 days.
DAILY_MIN_HEATING_HOURS: int = 5
#: Evidence gates. ``trust`` already scales the applied offset by ``t²/(t²+4)``,
#: so the t gate only refuses *no* evidence rather than tuning precision
#: (the live pump sits at t ≈ 3.0 with 25 heating days — the ceiling this
#: sample size can reach).
DAILY_MIN_DAYS: int = 20
#: Days a window half needs before its aperture is reported as a diagnostic.
DAILY_MIN_HALF_DAYS: int = 8
DAILY_MIN_T_STAT: float = 2.5
DAILY_MIN_R2: float = 0.85
DAILY_MAX_B_M2: float = 10.0
#: Every leave-one-day-out refit must stay positive and within ±this factor of
#: the full-window aperture: the estimate must not rest on a single day.
#: Measured on a live pump (26 heating days): jackknife 1.49–2.22 around 1.88,
#: i.e. ±21 %. A plain two-half split is *not* the right test — the older half
#: of a shoulder-season window is simply low-information (R² 0.33 with 6 kWh
#: days), which is a fact about the window, not about the aperture.
DAILY_JACKKNIFE_BAND: float = 0.5


@dataclass(frozen=True)
class SolarSample:
    """One heating hour in domain units (not a SQL/wire row)."""

    hour_ts: int
    delta_t_k: float
    ghi_wm2: float
    q_heat_w: float


@dataclass(frozen=True)
class SolarModel:
    """Identified coefficients plus continuous confidence."""

    a_w_per_k: float
    b_m2: float
    c_w: float
    trust: float
    r2_opaque: float
    r2_solar: float
    b_std_err: float | None
    n_opaque: int
    n_solar: int
    valid: bool
    notes: str = ""

    @property
    def effective_b_m2(self) -> float:
        """``b`` shrunk by ``trust``; what actually drives the solar offset."""
        return self.b_m2 * max(0.0, min(1.0, self.trust))

    def solar_gain_w(self, ghi_wm2: float) -> float:
        """Solar gain in watts at the given (effective) irradiance."""
        if not self.valid or ghi_wm2 <= 0.0:
            return 0.0
        return max(0.0, self.effective_b_m2 * float(ghi_wm2))


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

    Hours without heating demand are dropped: the model needs hours where the
    heating circuit actually delivered heat.
    """
    samples: list[SolarSample] = []
    for ts in sorted(
        set(q_by_hour) & set(indoor_by_hour) & set(outdoor_by_hour) & set(ghi_by_hour)
    ):
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


def smooth_ghi(
    hourly_ghi: Mapping[int, float],
    now_ts: int,
    *,
    tau_hours: float = DEFAULT_TAU_HOURS,
    span_hours: int = DEFAULT_SPAN_HOURS,
) -> float:
    """Thermal-mass-weighted effective GHI around ``now_ts``.

    Observed and forecast hours are blended with exponential decay
    ``exp(−|Δh|/τ)``, so a single cloud or spike moves the effective value by
    a fraction of an hour instead of rewriting the solar term. Missing hours
    are skipped and the weights renormalise.
    """
    if not hourly_ghi or span_hours < 0:
        return 0.0
    now_hour = now_ts - (now_ts % 3600)
    total_w = 0.0
    total = 0.0
    for step in range(-span_hours, span_hours + 1):
        value = hourly_ghi.get(now_hour + step * 3600)
        if value is None:
            continue
        numeric = float(value)
        if not math.isfinite(numeric):
            # A corrupt hour must renormalise like a missing one, not clamp
            # NaN to 0 W/m² or drag the average to inf.
            continue
        if tau_hours > 0.0:
            weight = math.exp(-abs(step) / tau_hours)
        else:
            weight = float(step == 0)
        total += weight * max(0.0, numeric)
        total_w += weight
    if total_w <= 0.0:
        return 0.0
    return total / total_w


def _median(values: Sequence[float]) -> float:
    """Median of a non-empty sequence."""
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return 0.5 * (ordered[mid - 1] + ordered[mid])


def _recency_weights(
    samples: Sequence[SolarSample], now_ts: int, half_life_days: float
) -> list[float]:
    if half_life_days <= 0.0:
        return [1.0] * len(samples)
    weights: list[float] = []
    for sample in samples:
        age_days = max(0.0, (now_ts - sample.hour_ts) / 86400.0)
        weights.append(0.5 ** (age_days / half_life_days))
    return weights


def _weighted_line(
    x: Sequence[float], y: Sequence[float], w: Sequence[float]
) -> tuple[float, float]:
    """Weighted least squares ``y ≈ a·x + c``."""
    sw = swx = swy = swxx = swxy = 0.0
    for xi, yi, wi in zip(x, y, w):
        sw += wi
        swx += wi * xi
        swy += wi * yi
        swxx += wi * xi * xi
        swxy += wi * xi * yi
    det = swxx * sw - swx * swx
    # Relative singularity check: an absolute threshold lets constant-x
    # designs slip through floating-point roundoff when sw·swxx is large.
    if det <= 1e-12 * sw * swxx:
        raise ValueError("singular design matrix")
    a = (swxy * sw - swy * swx) / det
    c = (swxx * swy - swx * swxy) / det
    return a, c


def _robust_weights_line(
    x: Sequence[float], y: Sequence[float], w: Sequence[float], a: float, c: float
) -> list[float]:
    """One Huber pass on studentised residuals of ``y ≈ a·x + c``."""
    residuals = [yi - (a * xi + c) for xi, yi in zip(x, y)]
    center = _median(residuals)
    scale = MAD_SCALE * _median([abs(r - center) for r in residuals])
    if scale < 1e-9:
        mean = sum(residuals) / len(residuals)
        scale = math.sqrt(sum((r - mean) ** 2 for r in residuals) / len(residuals))
    if scale < 1e-9:
        return list(w)

    sw = swx = swxx = 0.0
    for xi, wi in zip(x, w):
        sw += wi
        swx += wi * xi
        swxx += wi * xi * xi
    det = swxx * sw - swx * swx

    robust: list[float] = []
    for xi, residual, wi in zip(x, residuals, w):
        leverage = wi * (swxx - 2.0 * swx * xi + sw * xi * xi) / det
        leverage = min(max(leverage, 0.0), 0.999)
        student = residual / (scale * math.sqrt(1.0 - leverage))
        if abs(student) <= ROBUST_CUTOFF:
            robust.append(wi)
        else:
            robust.append(wi * ROBUST_CUTOFF / abs(student))
    return robust


def _robust_weights_solar(
    i_vals: Sequence[float],
    residual: Sequence[float],
    w: Sequence[float],
    b: float,
) -> list[float]:
    """One Huber pass for the through-origin residual fit."""
    e = [ri - b * ii for ii, ri in zip(i_vals, residual)]
    center = _median(e)
    scale = MAD_SCALE * _median([abs(ei - center) for ei in e])
    if scale < 1e-9:
        mean = sum(e) / len(e)
        scale = math.sqrt(sum((ei - mean) ** 2 for ei in e) / len(e))
    if scale < 1e-9:
        return list(w)

    sxx = sum(wi * ii * ii for wi, ii in zip(w, i_vals))
    out: list[float] = []
    for ii, ei, wi in zip(i_vals, e, w):
        leverage = min(max(wi * ii * ii / sxx, 0.0), 0.999)
        student = ei / (scale * math.sqrt(1.0 - leverage))
        if abs(student) <= ROBUST_CUTOFF:
            out.append(wi)
        else:
            out.append(wi * ROBUST_CUTOFF / abs(student))
    return out


def _weighted_r2(
    y: Sequence[float], pred: Sequence[float], w: Sequence[float]
) -> float:
    sw = sum(w)
    y_mean = sum(wi * yi for yi, wi in zip(y, w)) / sw
    ss_tot = sum(wi * (yi - y_mean) ** 2 for yi, wi in zip(y, w))
    ss_res = sum(wi * (yi - pi) ** 2 for yi, pi, wi in zip(y, pred, w))
    if ss_tot < 1e-9:
        return 0.0
    return 1.0 - ss_res / ss_tot


def _trust_from_t_stat(t_stat: float) -> float:
    """Shrinkage factor in [0, 1): 0 with no evidence, → 1 with strong."""
    if t_stat <= 0.0:
        return 0.0
    t2 = t_stat * t_stat
    return t2 / (t2 + T_STAT_REFERENCE * T_STAT_REFERENCE)


def _through_origin_std_err(
    i_vals: Sequence[float],
    residual: Sequence[float],
    w: Sequence[float],
    b: float,
) -> float:
    """Standard error of the through-origin slope (n ≥ 3, Σw·I² > 0)."""
    sxx = sum(wi * ii * ii for wi, ii in zip(w, i_vals))
    ss_res = sum(wi * (ri - b * ii) ** 2 for wi, ii, ri in zip(w, i_vals, residual))
    sigma2 = ss_res / (len(i_vals) - 1)
    return math.sqrt(sigma2 / sxx)


def fit_solar_model(
    samples: Sequence[SolarSample],
    *,
    now_ts: int | None = None,
    half_life_days: float = HALF_LIFE_DAYS,
) -> SolarModel:
    """Two-stage robust, recency-weighted identification of a, b, c, trust.

    Returns an invalid zero model (``valid=False``, ``trust=0``) when fewer
    than :data:`MIN_FIT_ROWS` opaque rows exist, when the design is
    singular, or when the fitted ``a`` is non-positive. Callers then apply
    no solar correction at all.
    """
    # One corrupt (NaN/inf) sensor hour must never poison the fit: a non-finite
    # row would make the LS sums non-finite and slip past the a <= 0 gate (or
    # collapse b to zero while r2 reads as perfect).
    samples = [
        sample
        for sample in samples
        if math.isfinite(sample.delta_t_k)
        and math.isfinite(sample.ghi_wm2)
        and math.isfinite(sample.q_heat_w)
    ]

    if now_ts is None:
        now_ts = max((s.hour_ts for s in samples), default=0)

    opaque = [s for s in samples if s.ghi_wm2 < OPAQUE_GHI_MAX_WM2]
    solar = [s for s in samples if s.ghi_wm2 >= OPAQUE_GHI_MAX_WM2]
    n_opaque, n_solar = len(opaque), len(solar)
    empty = dict(
        a_w_per_k=0.0,
        b_m2=0.0,
        c_w=0.0,
        trust=0.0,
        r2_opaque=0.0,
        r2_solar=0.0,
        b_std_err=None,
        n_opaque=n_opaque,
        n_solar=n_solar,
        valid=False,
    )

    if n_opaque < MIN_FIT_ROWS:
        return SolarModel(**empty, notes=f"opaque rows {n_opaque} < {MIN_FIT_ROWS}")

    x_opaque = [s.delta_t_k for s in opaque]
    y_opaque = [s.q_heat_w for s in opaque]
    w_opaque = _recency_weights(opaque, now_ts, half_life_days)
    try:
        a, c = _weighted_line(x_opaque, y_opaque, w_opaque)
        robust_w = _robust_weights_line(x_opaque, y_opaque, w_opaque, a, c)
        a, c = _weighted_line(x_opaque, y_opaque, robust_w)
    except ValueError as err:
        return SolarModel(**empty, notes=f"opaque fit failed: {err}")
    if a <= 0.0:
        return SolarModel(**{**empty, "a_w_per_k": a, "c_w": c}, notes="a ≤ 0")
    if not (math.isfinite(a) and math.isfinite(c)):
        return SolarModel(**empty, notes="non-finite fit")

    pred_opaque = [a * xi + c for xi in x_opaque]
    r2_opaque = _weighted_r2(y_opaque, pred_opaque, robust_w)

    b = 0.0
    b_std_err: float | None = None
    r2_solar = 0.0
    solar_note = f"solar rows {n_solar} < {MIN_FIT_ROWS}; b = 0"
    if n_solar >= MIN_FIT_ROWS:
        residual = [(a * s.delta_t_k + c) - s.q_heat_w for s in solar]
        i_vals = [s.ghi_wm2 for s in solar]
        w_solar = _recency_weights(solar, now_ts, half_life_days)

        def _fit_b(weights: Sequence[float]) -> float:
            cross = sum(
                wi * ii * ri for wi, ii, ri in zip(weights, i_vals, residual)
            )
            weight_ii = sum(wi * ii * ii for wi, ii in zip(weights, i_vals))
            return max(0.0, cross / weight_ii)

        b = _fit_b(w_solar)
        robust_w = _robust_weights_solar(i_vals, residual, w_solar, b)
        b = _fit_b(robust_w)
        r2_solar = _weighted_r2(residual, [b * ii for ii in i_vals], robust_w)
        b_std_err = _through_origin_std_err(i_vals, residual, robust_w, b)
        solar_note = f"n_solar={n_solar} b={b:.4f} m² r2={r2_solar:.3f}"
        if b_std_err is not None:
            solar_note += f" se={b_std_err:.4f}"

    trust = 0.0
    if b > 0.0:
        if b_std_err is None or b_std_err < 1e-12:
            trust = 1.0
        else:
            trust = max(0.0, min(1.0, _trust_from_t_stat(b / b_std_err)))

    return SolarModel(
        a_w_per_k=a,
        b_m2=b,
        c_w=c,
        trust=trust,
        r2_opaque=max(0.0, min(1.0, r2_opaque)),
        r2_solar=max(0.0, min(1.0, r2_solar)),
        b_std_err=b_std_err,
        n_opaque=n_opaque,
        n_solar=n_solar,
        valid=True,
        notes=f"opaque n={n_opaque} r2={r2_opaque:.3f}; {solar_note}",
    )


@dataclass(frozen=True)
class SolarDay:
    """One UTC day of the site energy balance.

    The input maps are hourly *means*, so the sums are energy-like: ``ΣΔT``
    in K·h, ``ΣGHI`` in Wh/m² and ``Q`` in Wh. Idle hours are included with
    ``q = 0`` — that is the point, the sun's work shows up as an hour the
    circuit did not have to run.
    """

    day_ts: int
    sum_delta_t_k: float
    sum_ghi_wm2: float
    q_heat_wh: float
    hours: int
    heating_hours: int
    #: ``Σ(v·ΔT)`` over the hours that carried a wind reading, in
    #: K·h·(m/s): the wind interaction column of a day balance, since wind
    #: multiplies the envelope loss rather than shifting it by a constant.
    sum_wind_delta_t_k_m_s: float = 0.0
    #: ``Σv`` over those hours, in (m/s)·h — the plain wind column, kept for
    #: the comparison fit that has no physical meaning but shows whether the
    #: interaction is doing the work.
    sum_wind_ms_h: float = 0.0
    #: Hours with a wind reading. Zero means the payload carried no wind, which
    #: is "not measured", not "calm".
    wind_hours: int = 0


@dataclass(frozen=True)
class SolarDailyModel:
    """Day-block energy balance ``Q = a·ΣΔT + 24c − b·ΣGHI``.

    ``valid`` is ``False`` whenever an evidence gate fails; callers then
    apply no solar term at all rather than a guess.
    """

    a_w_per_k: float
    b_m2: float
    c_w: float
    trust: float
    r2: float
    n_days: int
    b_std_err: float | None = None
    t_stat: float | None = None
    b_first_half_m2: float | None = None
    b_second_half_m2: float | None = None
    b_jackknife_min_m2: float | None = None
    b_jackknife_max_m2: float | None = None
    valid: bool = False
    notes: str = ""

    @property
    def effective_b_m2(self) -> float:
        """``b`` shrunk by ``trust``; what actually drives the solar offset."""
        return self.b_m2 * max(0.0, min(1.0, self.trust))


def aggregate_days(
    q_by_hour: Mapping[int, float],
    indoor_by_hour: Mapping[int, float],
    outdoor_by_hour: Mapping[int, float],
    ghi_by_hour: Mapping[int, float],
    wind_by_hour: Mapping[int, float] | None = None,
) -> list[SolarDay]:
    """Sum hourly maps into UTC day blocks.

    Every hour present in all four series contributes; non-finite rows are
    skipped rather than poisoning a day's sums. ``wind_by_hour`` is optional: a
    payload without wind still produces the same days, with ``wind_hours`` 0 —
    the wind columns are evidence, and absent evidence must not look like calm.
    """
    buckets: dict[int, dict[str, float]] = {}
    for ts in sorted(
        set(q_by_hour) & set(indoor_by_hour) & set(outdoor_by_hour) & set(ghi_by_hour)
    ):
        try:
            hour = int(ts)
            delta_t = float(indoor_by_hour[ts]) - float(outdoor_by_hour[ts])
            irradiance = float(ghi_by_hour[ts])
            q_heat = float(q_by_hour[ts])
        except (TypeError, ValueError):
            continue
        if not (
            math.isfinite(delta_t) and math.isfinite(irradiance) and math.isfinite(q_heat)
        ):
            continue
        day_ts = hour - (hour % (DAILY_BLOCK_HOURS * 3600))
        bucket = buckets.setdefault(
            day_ts,
            {
                "sum_delta_t": 0.0,
                "sum_ghi": 0.0,
                "q_heat": 0.0,
                "sum_wind_dt": 0.0,
                "sum_wind": 0.0,
                "hours": 0,
                "heat": 0,
                "wind_hours": 0,
            },
        )
        bucket["sum_delta_t"] += delta_t
        bucket["sum_ghi"] += max(0.0, irradiance)
        bucket["q_heat"] += max(0.0, q_heat)
        bucket["hours"] += 1
        wind_ms = None if wind_by_hour is None else wind_by_hour.get(ts)
        if isinstance(wind_ms, (int, float)) and not isinstance(wind_ms, bool):
            wind = float(wind_ms)
            if math.isfinite(wind):
                bucket["sum_wind_dt"] += max(0.0, wind) * delta_t
                bucket["sum_wind"] += max(0.0, wind)
                bucket["wind_hours"] += 1
        if q_heat >= MIN_SAMPLE_Q_W and delta_t >= MIN_SAMPLE_DELTA_T_K:
            bucket["heat"] += 1
    return [
        SolarDay(
            day_ts=day_ts,
            sum_delta_t_k=bucket["sum_delta_t"],
            sum_ghi_wm2=bucket["sum_ghi"],
            q_heat_wh=bucket["q_heat"],
            hours=int(bucket["hours"]),
            heating_hours=int(bucket["heat"]),
            sum_wind_delta_t_k_m_s=bucket["sum_wind_dt"],
            sum_wind_ms_h=bucket["sum_wind"],
            wind_hours=int(bucket["wind_hours"]),
        )
        for day_ts, bucket in sorted(buckets.items())
    ]


def _invert_3x3(matrix: Sequence[Sequence[float]]) -> list[list[float]]:
    """Gauss-Jordan inverse with partial pivoting; raises on a singular design."""
    scale = max((abs(value) for row in matrix for value in row), default=0.0)
    if scale <= 0.0:
        raise ValueError("singular design")
    aug = [
        [float(matrix[row][col]) for col in range(3)]
        + [1.0 if row == column else 0.0 for column in range(3)]
        for row in range(3)
    ]
    for col in range(3):
        pivot_row = max(range(col, 3), key=lambda row: abs(aug[row][col]))
        if abs(aug[pivot_row][col]) <= 1e-12 * scale:
            raise ValueError("singular design")
        aug[col], aug[pivot_row] = aug[pivot_row], aug[col]
        divisor = aug[col][col]
        aug[col] = [value / divisor for value in aug[col]]
        for row in range(3):
            if row == col:
                continue
            factor = aug[row][col]
            if factor:
                aug[row] = [
                    value - factor * base for value, base in zip(aug[row], aug[col])
                ]
    return [row[3:] for row in aug]


def _ols_three(
    x1: Sequence[float],
    x2: Sequence[float],
    x3: Sequence[float],
    y: Sequence[float],
) -> tuple[tuple[float, float, float], tuple[float, float, float], float]:
    """Plain least squares for three regressors → ``(beta, stderr, r2)``.

    Raises ``ValueError`` when there are fewer rows than coefficients or the
    design matrix is singular.
    """
    rows = list(zip(x1, x2, x3, y))
    n = len(rows)
    if n < 4:
        raise ValueError("too few rows")
    columns = (
        [row[0] for row in rows],
        [row[1] for row in rows],
        [row[2] for row in rows],
    )
    targets = [row[3] for row in rows]
    xtx = [
        [sum(a * b for a, b in zip(columns[i], columns[j])) for j in range(3)]
        for i in range(3)
    ]
    xty = [sum(a * b for a, b in zip(columns[i], targets)) for i in range(3)]
    inverse = _invert_3x3(xtx)
    beta = tuple(
        sum(inverse[i][j] * xty[j] for j in range(3)) for i in range(3)
    )
    residuals = [
        target - sum(beta[j] * columns[j][index] for j in range(3))
        for index, target in enumerate(targets)
    ]
    ss_res = sum(value * value for value in residuals)
    mean_y = sum(targets) / n
    ss_tot = sum((target - mean_y) ** 2 for target in targets)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-9 else 0.0
    sigma2 = ss_res / (n - 3)
    stderr = tuple(
        math.sqrt(max(0.0, sigma2 * inverse[i][i])) for i in range(3)
    )
    if not all(math.isfinite(value) for value in beta + stderr):
        raise ValueError("non-finite fit")
    return beta, stderr, r2


def _fit_daily_rows(
    days: Sequence[SolarDay],
) -> tuple[float, float, float, float, float | None, float]:
    """Day-block OLS → ``(a, b, c, b_std_err, t_stat, r2)``.

    ``c`` is per hour (the intercept is a day's ``24c``), so it keeps the same
    meaning as the hourly model's intercept.
    """
    n = len(days)
    beta, stderr, r2 = _ols_three(
        [day.sum_delta_t_k for day in days],
        [day.sum_ghi_wm2 for day in days],
        [1.0] * n,
        [day.q_heat_wh for day in days],
    )
    a, minus_b, intercept = beta
    se_b = stderr[1]
    b = -minus_b
    t_stat = None if se_b <= 1e-12 else b / se_b
    return a, b, intercept / float(DAILY_BLOCK_HOURS), se_b, t_stat, r2


def fit_daily_solar_model(days: Sequence[SolarDay]) -> SolarDailyModel:
    """Identify ``a``/``c``/``b`` from UTC day blocks, gated on evidence.

    Returns an invalid zero model whenever a gate fails — too few usable
    days, a non-positive loss or aperture, an absurd aperture, a weak t-stat,
    a poor R², or two window halves that disagree. The caller then applies no
    solar term at all: an unidentifiable aperture must degrade to "off", not
    to a guess.
    """
    usable = [
        day
        for day in days
        if day.hours >= DAILY_MIN_HOURS
        and day.heating_hours >= DAILY_MIN_HEATING_HOURS
    ]
    n_days = len(usable)
    empty: dict[str, Any] = {
        "a_w_per_k": 0.0,
        "b_m2": 0.0,
        "c_w": 0.0,
        "trust": 0.0,
        "r2": 0.0,
        "n_days": n_days,
        "valid": False,
    }
    if n_days < DAILY_MIN_DAYS:
        return SolarDailyModel(**empty, notes=f"days {n_days} < {DAILY_MIN_DAYS}")

    ordered = sorted(usable, key=lambda day: day.day_ts)
    try:
        a, b, c, se_b, t_stat, r2 = _fit_daily_rows(ordered)
    except ValueError as err:
        return SolarDailyModel(**empty, notes=f"daily fit failed: {err}")
    measured: dict[str, Any] = {
        **empty,
        "a_w_per_k": a,
        "b_m2": b,
        "c_w": c,
        "r2": max(0.0, min(1.0, r2)),
        "b_std_err": se_b,
        "t_stat": t_stat,
    }
    if a <= 0.0:
        return SolarDailyModel(**measured, notes="a ≤ 0")
    if b <= 0.0:
        return SolarDailyModel(**measured, notes=f"no positive aperture (b={b:.3f} m²)")
    if b > DAILY_MAX_B_M2:
        return SolarDailyModel(
            **measured, notes=f"b {b:.2f} m² > {DAILY_MAX_B_M2:.0f} m²"
        )
    if t_stat is None or t_stat < DAILY_MIN_T_STAT:
        return SolarDailyModel(
            **measured,
            notes=(
                "t-stat unavailable"
                if t_stat is None
                else f"t {t_stat:.2f} < {DAILY_MIN_T_STAT:.1f}"
            ),
        )
    if r2 < DAILY_MIN_R2:
        return SolarDailyModel(**measured, notes=f"r2 {r2:.2f} < {DAILY_MIN_R2:.2f}")

    # Diagnostics: the two halves of the window, reported (never gated) so a
    # support dump shows when the older half carried no information at all.
    half = n_days // 2
    b_halves: list[float | None] = []
    for part in (ordered[:half], ordered[half:]):
        value: float | None = None
        if len(part) >= DAILY_MIN_HALF_DAYS:
            try:
                candidate = _fit_daily_rows(part)[1]
            except ValueError:
                candidate = float("nan")
            if math.isfinite(candidate):
                value = candidate
        b_halves.append(value)

    # Stability: no single day may carry the estimate.
    jackknife: list[float] = []
    for index in range(n_days):
        try:
            candidate = _fit_daily_rows(ordered[:index] + ordered[index + 1 :])[1]
        except ValueError:
            candidate = float("nan")
        jackknife.append(candidate)
    if not all(math.isfinite(value) for value in jackknife):
        return SolarDailyModel(**measured, notes="jackknife fit failed")
    band = DAILY_JACKKNIFE_BAND
    if not all(band * b <= value <= (1.0 + band) * b for value in jackknife):
        return SolarDailyModel(
            **measured,
            notes=(
                f"jackknife {min(jackknife):.2f}-{max(jackknife):.2f} m² "
                f"vs {b:.2f} m²"
            ),
        )

    trust = max(0.0, min(1.0, _trust_from_t_stat(t_stat)))
    return SolarDailyModel(
        a_w_per_k=a,
        b_m2=b,
        c_w=c,
        trust=trust,
        r2=max(0.0, min(1.0, r2)),
        n_days=n_days,
        b_std_err=se_b,
        t_stat=t_stat,
        b_first_half_m2=b_halves[0],
        b_second_half_m2=b_halves[1],
        b_jackknife_min_m2=min(jackknife),
        b_jackknife_max_m2=max(jackknife),
        valid=True,
        notes=(
            f"days={n_days} t={t_stat:.2f} r2={r2:.3f} "
            f"jackknife={min(jackknife):.2f}-{max(jackknife):.2f}"
        ),
    )


def effective_solar_model(
    model: SolarModel | None,
    daily: SolarDailyModel | None,
) -> tuple[float, float]:
    """``(aperture m², loss coefficient W/K)`` the solar term should use.

    The day-block pair wins whenever it passed its gates with a positive
    aperture: it is the same balance identified without the heating-hour
    censoring, so its ``b`` and ``a`` belong together and are converted with
    each other. The hourly model is the fallback (its own ``b`` is 0 on a
    censored window, i.e. no term), never a second opinion to average in.
    """
    if daily is not None and daily.valid and daily.a_w_per_k > 0.0:
        aperture = daily.effective_b_m2
        if aperture > 0.0:
            return aperture, daily.a_w_per_k
    if model is not None and model.valid and model.a_w_per_k > 0.0:
        return model.effective_b_m2, model.a_w_per_k
    return 0.0, 0.0
