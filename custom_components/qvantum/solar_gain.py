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
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Sequence

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
        if tau_hours > 0.0:
            weight = math.exp(-abs(step) / tau_hours)
        else:
            weight = float(step == 0)
        total += weight * max(0.0, float(value))
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
