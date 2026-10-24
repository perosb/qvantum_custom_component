"""Tests for the self-calibrating solar-gain identification core."""

from __future__ import annotations

import ast
import math
import random
from dataclasses import replace
from pathlib import Path

import pytest

from custom_components.qvantum.solar_gain import (
    DAILY_MIN_DAYS,
    DAILY_MIN_R2,
    DAILY_MIN_T_STAT,
    MIN_FIT_ROWS,
    MIN_SAMPLE_Q_W,
    OPAQUE_GHI_MAX_WM2,
    SolarDay,
    SolarModel,
    SolarSample,
    DAILY_MIN_WIND_HOURS,
    DAILY_MIN_WIND_T_STAT,
    _median,
    _ols_three,
    _recency_weights,
    _robust_weights_line,
    _trust_from_t_stat,
    _weighted_r2,
    aggregate_days,
    build_samples,
    effective_solar_model,
    _fit_wind_coefficient,
    fit_daily_solar_model,
    fit_solar_model,
    smooth_ghi,
)

NOW = 1_760_000_000
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "qvantum"
    / "solar_gain.py"
)


def make_samples(
    *,
    a: float = 161.0,
    b: float = 0.10,
    c: float = 300.0,
    n_opaque: int = 200,
    n_solar: int = 100,
    noise: float = 20.0,
    seed: int = 7,
    now_ts: int = NOW,
) -> list[SolarSample]:
    """Synthetic heating hours: first opaque (dark), then solar rows."""
    rng = random.Random(seed)
    total = n_opaque + n_solar
    rows: list[SolarSample] = []
    for index in range(total):
        ts = now_ts - (total - index) * 3600
        delta_t = 10.0 + 25.0 * rng.random()
        if index < n_opaque:
            ghi = rng.uniform(0.0, OPAQUE_GHI_MAX_WM2 - 10.0)
        else:
            ghi = rng.uniform(50.0, 800.0)
        q_heat = a * delta_t - b * ghi + c + rng.gauss(0.0, noise)
        rows.append(
            SolarSample(hour_ts=ts, delta_t_k=delta_t, ghi_wm2=ghi, q_heat_w=q_heat)
        )
    return rows


def test_fit_recovers_coefficients() -> None:
    model = fit_solar_model(make_samples(), now_ts=NOW)

    assert model.valid
    assert model.a_w_per_k == pytest.approx(161.0, abs=2.0)
    assert model.b_m2 == pytest.approx(0.10, abs=0.02)
    assert model.c_w == pytest.approx(300.0, abs=15.0)
    assert model.n_opaque == 200
    assert model.n_solar == 100
    assert model.trust > 0.5
    assert model.solar_gain_w(500.0) > 0.0


def test_two_histories_yield_different_models_without_config() -> None:
    model_low = fit_solar_model(make_samples(a=80.0, b=0.30, seed=1), now_ts=NOW)
    model_high = fit_solar_model(make_samples(a=200.0, b=2.00, seed=2), now_ts=NOW)

    assert abs(model_low.a_w_per_k - model_high.a_w_per_k) > 80.0
    assert model_high.b_m2 > model_low.b_m2 * 2.0


def test_pers_like_weak_b_gives_small_trust_and_solar_term() -> None:
    """a ≈ 161 W/K, b ≈ 0.10 m² and little evidence → tiny solar offset."""
    model = fit_solar_model(
        make_samples(b=0.10, n_solar=24, noise=60.0, seed=3), now_ts=NOW
    )

    assert model.a_w_per_k == pytest.approx(161.0, abs=4.0)
    assert model.b_m2 == pytest.approx(0.10, abs=0.03)
    assert model.trust < 0.6
    # Equivalent temperature drop: < 1 °C for any curve slope up to 2 °C/°C.
    assert model.solar_gain_w(500.0) / model.a_w_per_k < 0.5


def test_trust_grows_with_more_solar_data() -> None:
    small = fit_solar_model(make_samples(n_solar=MIN_FIT_ROWS + 5, seed=4), now_ts=NOW)
    large = fit_solar_model(make_samples(n_solar=300, seed=4), now_ts=NOW)

    assert small.valid and large.valid
    assert large.trust > small.trust
    assert large.b_std_err < small.b_std_err


def test_trust_grows_with_cleaner_data() -> None:
    noisy = fit_solar_model(make_samples(n_solar=60, noise=80.0, seed=5), now_ts=NOW)
    clean = fit_solar_model(make_samples(n_solar=60, noise=5.0, seed=6), now_ts=NOW)

    assert clean.trust > noisy.trust


def test_no_solar_signal_gives_no_offset() -> None:
    model = fit_solar_model(
        make_samples(b=0.0, n_solar=40, noise=40.0, seed=7), now_ts=NOW
    )

    assert model.valid
    assert model.trust < 0.2
    assert model.solar_gain_w(600.0) < 10.0


def test_recency_weighting_follows_recent_regime() -> None:
    """Old regime a=70/b=0.2, recent regime a=180/b=1.8; recent wins."""
    rng = random.Random(11)
    total = 960  # 40 days hourly
    rows: list[SolarSample] = []
    for index in range(total):
        ts = NOW - (total - index) * 3600
        delta_t = 10.0 + 25.0 * rng.random()
        if index < total // 2:
            a, b = 70.0, 0.2
        else:
            a, b = 180.0, 1.8
        ghi = rng.uniform(0.0, 20.0) if rng.random() < 0.5 else rng.uniform(50.0, 800.0)
        q_heat = a * delta_t - b * ghi + 300.0 + rng.gauss(0.0, 15.0)
        rows.append(
            SolarSample(hour_ts=ts, delta_t_k=delta_t, ghi_wm2=ghi, q_heat_w=q_heat)
        )

    recent = fit_solar_model(rows, now_ts=NOW, half_life_days=5.0)
    uniform = fit_solar_model(rows, now_ts=NOW, half_life_days=1e9)

    assert recent.a_w_per_k > uniform.a_w_per_k
    assert recent.b_m2 > uniform.b_m2
    assert recent.a_w_per_k > 150.0
    assert recent.b_m2 > 1.0


def test_single_outlier_is_damped() -> None:
    clean = make_samples(n_solar=80, noise=15.0, seed=8)
    outlier = SolarSample(
        hour_ts=NOW - 30 * 3600,
        delta_t_k=20.0,
        ghi_wm2=790.0,
        q_heat_w=161.0 * 20.0 - 0.10 * 790.0 + 300.0 - 3000.0,
    )

    base = fit_solar_model(clean, now_ts=NOW)
    dirty = fit_solar_model(clean + [outlier], now_ts=NOW)

    assert dirty.b_m2 == pytest.approx(base.b_m2, abs=0.03)


def test_noiseless_data_yields_full_trust() -> None:
    """Perfect fit → b_std_err ~ 0 → trust capped at 1, never NaN."""
    rows = [
        SolarSample(
            hour_ts=NOW - (60 - i) * 3600,
            delta_t_k=10.0 + (i % 25),
            ghi_wm2=5.0,
            q_heat_w=161.0 * (10.0 + (i % 25)) + 300.0,
        )
        for i in range(30)
    ]
    rows += [
        SolarSample(
            hour_ts=NOW - (30 - i) * 3600,
            delta_t_k=20.0,
            ghi_wm2=100.0 + 20.0 * i,
            q_heat_w=161.0 * 20.0 - 2.0 * (100.0 + 20.0 * i) + 300.0,
        )
        for i in range(30)
    ]

    model = fit_solar_model(rows, now_ts=NOW, half_life_days=0.0)

    assert model.valid
    assert model.b_m2 == pytest.approx(2.0)
    assert model.trust == pytest.approx(1.0)


def test_fit_uses_latest_hour_when_now_ts_omitted() -> None:
    model = fit_solar_model(make_samples(seed=12))

    assert model.valid
    assert model.n_opaque == 200


def test_weighted_r2_constant_target_is_zero() -> None:
    assert _weighted_r2([5.0, 5.0], [5.0, 5.0], [1.0, 1.0]) == 0.0


def test_model_invalid_with_too_few_opaque_rows() -> None:
    samples = [
        SolarSample(hour_ts=NOW - 3600, delta_t_k=20.0, ghi_wm2=5.0, q_heat_w=3500.0),
        SolarSample(hour_ts=NOW, delta_t_k=21.0, ghi_wm2=5.0, q_heat_w=3600.0),
    ]
    model = fit_solar_model(samples, now_ts=NOW)

    assert not model.valid
    assert "opaque rows" in model.notes
    assert model.solar_gain_w(500.0) == 0.0


def test_model_invalid_with_singular_design() -> None:
    samples = [
        SolarSample(hour_ts=NOW - 3 * 3600 + i * 3600, delta_t_k=20.0, ghi_wm2=5.0, q_heat_w=3500.0 + i)
        for i in range(5)
    ]
    model = fit_solar_model(samples, now_ts=NOW)

    assert not model.valid
    assert "singular" in model.notes


def test_model_invalid_for_constant_delta_t_with_varying_weights() -> None:
    """A long constant-x design must not slip through float roundoff."""
    samples = [
        SolarSample(
            hour_ts=NOW - (120 - i) * 3600,
            delta_t_k=20.0,
            ghi_wm2=5.0,
            q_heat_w=3500.0,
        )
        for i in range(120)
    ]
    model = fit_solar_model(samples, now_ts=NOW)

    assert not model.valid
    assert "singular" in model.notes


def test_model_invalid_when_slope_not_positive() -> None:
    rows = [
        SolarSample(
            hour_ts=NOW - (10 - i) * 3600,
            delta_t_k=10.0 + i,
            ghi_wm2=5.0,
            q_heat_w=4000.0 - 50.0 * i,
        )
        for i in range(10)
    ]
    model = fit_solar_model(rows, now_ts=NOW)

    assert not model.valid
    assert model.notes == "a ≤ 0"


def test_model_invalid_without_solar_rows_still_fits_opaque() -> None:
    model = fit_solar_model(make_samples(n_solar=0, seed=9), now_ts=NOW)

    assert model.valid
    assert model.b_m2 == 0.0
    assert model.trust == 0.0
    assert "b = 0" in model.notes


def test_solar_gain_clamps_negative_and_invalid() -> None:
    model = fit_solar_model(make_samples(seed=10), now_ts=NOW)

    assert model.solar_gain_w(-50.0) == 0.0
    invalid = SolarModel(0, 0, 0, 0, 0, 0, None, 0, 0, valid=False)
    assert invalid.solar_gain_w(500.0) == 0.0


def test_smooth_ghi_flat_and_spike() -> None:
    now_hour = NOW - (NOW % 3600)
    flat = {now_hour + step * 3600: 200.0 for step in range(-6, 7)}
    assert smooth_ghi(flat, NOW) == pytest.approx(200.0)

    spiked = dict(flat)
    spiked[now_hour + 3600] = 1000.0
    effective = smooth_ghi(spiked, NOW)

    assert 200.0 < effective < 350.0


def test_smooth_ghi_missing_hours_and_degenerate_options() -> None:
    now_hour = NOW - (NOW % 3600)

    assert smooth_ghi({}, NOW) == 0.0
    assert smooth_ghi({now_hour: 400.0}, NOW) == pytest.approx(400.0)
    assert smooth_ghi({now_hour: 400.0}, NOW, span_hours=-1) == 0.0
    assert smooth_ghi({now_hour: 400.0}, NOW, span_hours=0, tau_hours=0.0) == pytest.approx(400.0)
    assert smooth_ghi({now_hour - 3600: 1000.0}, NOW, tau_hours=0.0) == 0.0


def test_smooth_ghi_skips_non_finite_hours() -> None:
    now_hour = NOW - (NOW % 3600)
    flat = {now_hour + step * 3600: 200.0 for step in range(-6, 7)}

    nan = dict(flat)
    nan[now_hour] = float("nan")
    assert smooth_ghi(nan, NOW) == pytest.approx(200.0)

    infinite = dict(flat)
    infinite[now_hour] = float("inf")
    assert smooth_ghi(infinite, NOW) == pytest.approx(200.0)


def test_trust_from_t_stat_is_bounded_and_monotonic() -> None:
    assert _trust_from_t_stat(0.0) == 0.0
    assert _trust_from_t_stat(-1.0) == 0.0
    assert 0.0 < _trust_from_t_stat(1.0) < _trust_from_t_stat(4.0) < 1.0


def test_median_and_recency_helpers() -> None:
    assert _median([3.0, 1.0, 2.0]) == 2.0
    assert _median([4.0, 1.0, 3.0, 2.0]) == 2.5

    samples = [
        SolarSample(hour_ts=NOW, delta_t_k=20.0, ghi_wm2=0.0, q_heat_w=3500.0),
        SolarSample(
            hour_ts=NOW - 86400, delta_t_k=20.0, ghi_wm2=0.0, q_heat_w=3500.0
        ),
    ]
    assert _recency_weights(samples, NOW, 0.0) == [1.0, 1.0]
    weights = _recency_weights(samples, NOW, 1.0)
    assert weights[0] == pytest.approx(1.0)
    assert weights[1] == pytest.approx(0.5)


def test_robust_pass_exact_data_keeps_weights() -> None:
    x = [1.0, 2.0, 3.0]
    y = [2.0, 4.0, 6.0]
    w = [1.0, 1.0, 1.0]

    assert _robust_weights_line(x, y, w, 2.0, 0.0) == w


def test_module_has_no_home_assistant_imports() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    assert not [name for name in imported if name.startswith("homeassistant")]
    assert not [name for name in imported if name.startswith("custom_components")]
    assert math.isfinite(math.pi)


def test_non_finite_rows_are_ignored() -> None:
    clean = make_samples(n_solar=60, noise=15.0, seed=13)
    base = fit_solar_model(clean, now_ts=NOW)

    dirty = clean + [
        SolarSample(
            hour_ts=NOW - 3600, delta_t_k=20.0, ghi_wm2=200.0, q_heat_w=float("nan")
        ),
        SolarSample(
            hour_ts=NOW - 7200, delta_t_k=float("nan"), ghi_wm2=5.0, q_heat_w=3000.0
        ),
        SolarSample(
            hour_ts=NOW - 10800, delta_t_k=15.0, ghi_wm2=float("inf"), q_heat_w=2500.0
        ),
    ]
    polluted = fit_solar_model(dirty, now_ts=NOW)

    assert polluted.valid
    assert math.isfinite(polluted.a_w_per_k)
    assert math.isfinite(polluted.c_w)
    assert polluted.a_w_per_k == pytest.approx(base.a_w_per_k)
    assert polluted.b_m2 == pytest.approx(base.b_m2)
    assert polluted.trust == pytest.approx(base.trust)


def test_all_opaque_rows_non_finite_is_invalid() -> None:
    samples = [
        SolarSample(
            hour_ts=NOW - index * 3600,
            delta_t_k=float("nan"),
            ghi_wm2=5.0,
            q_heat_w=3000.0,
        )
        for index in range(10)
    ]

    model = fit_solar_model(samples, now_ts=NOW)

    assert not model.valid
    assert "opaque rows" in model.notes


# ---------------------------------------------------------------------------
# Day-block identification
# ---------------------------------------------------------------------------


def make_daily_rows(
    *,
    a: float = 150.0,
    b: float = 1.8,
    c: float = -300.0,
    days: int = 26,
    noise: float = 40.0,
    seed: int = 21,
    start_ts: int = NOW - 26 * 86400,
    mutate=None,
) -> list[SolarDay]:
    """Synthetic day blocks from ``Q = a·ΣΔT + 24c − b·ΣGHI``."""
    rng = random.Random(seed)
    rows: list[SolarDay] = []
    for index in range(days):
        sum_delta_t = 160.0 + 120.0 * rng.random()
        sum_ghi = 300.0 + 3000.0 * rng.random()
        q_heat = a * sum_delta_t + 24.0 * c - b * sum_ghi + rng.gauss(0.0, noise)
        row = SolarDay(
            day_ts=start_ts + index * 86400,
            sum_delta_t_k=sum_delta_t,
            sum_ghi_wm2=sum_ghi,
            q_heat_wh=q_heat,
            hours=24,
            heating_hours=12,
        )
        if mutate is not None:
            row = mutate(row, index)
        rows.append(row)
    return rows


def make_censored_history(
    *,
    a: float = 150.0,
    b: float = 1.8,
    c: float = -700.0,
    days: int = 40,
    noise: float = 40.0,
    seed: int = 23,
    outdoor_low_c: float = 14.0,
    outdoor_high_c: float = 16.0,
    peak_ghi_wm2: float = 600.0,
    now_ts: int = NOW,
) -> tuple[dict[int, float], dict[int, float], dict[int, float], dict[int, float]]:
    """Hourly maps for a house whose circuit stops when demand is covered.

    Every hour's demand is ``a·ΔT + c − b·GHI``; the pump delivers it only when
    it is positive (that is what a room-compensated pump does in the sun),
    which is exactly the censoring the day-block estimator exists to undo.
    """
    rng = random.Random(seed)
    q_heat: dict[int, float] = {}
    indoor: dict[int, float] = {}
    outdoor: dict[int, float] = {}
    ghi_by_hour: dict[int, float] = {}
    # Align to a UTC midnight so the window is exactly ``days`` day blocks.
    start = now_ts - days * 24 * 3600
    start -= start % 86400
    for day in range(days):
        outdoor_day = outdoor_low_c + (outdoor_high_c - outdoor_low_c) * rng.random()
        clearness = rng.uniform(0.15, 1.0)
        for hour in range(24):
            ts = start + (day * 24 + hour) * 3600
            outdoor[ts] = outdoor_day
            indoor[ts] = 21.0
            if 5 <= hour < 18:
                ghi = peak_ghi_wm2 * math.sin(math.pi * (hour - 5) / 13.0) * clearness
            else:
                ghi = 0.0
            ghi_by_hour[ts] = ghi
            demand = a * (21.0 - outdoor_day) + c - b * ghi
            q_heat[ts] = max(0.0, demand + rng.gauss(0.0, noise))
    return q_heat, indoor, outdoor, ghi_by_hour


def test_aggregate_days_sums_hours_and_keeps_idle_hours() -> None:
    q_heat, indoor, outdoor, ghi = make_censored_history(days=3, noise=0.0)
    days = aggregate_days(q_heat, indoor, outdoor, ghi)

    assert len(days) == 3
    day = days[0]
    keys = [ts for ts in q_heat if ts // 86400 == day.day_ts // 86400]
    assert day.hours == len(keys)
    assert day.q_heat_wh == pytest.approx(sum(q_heat[ts] for ts in keys))
    assert day.sum_ghi_wm2 == pytest.approx(sum(ghi[ts] for ts in keys))
    assert day.sum_delta_t_k == pytest.approx(
        sum(indoor[ts] - outdoor[ts] for ts in keys)
    )
    # Idle hours are the evidence: they stay in the sums with q = 0.
    assert day.heating_hours < 24
    idle = [ts for ts in keys if q_heat[ts] < MIN_SAMPLE_Q_W]
    assert idle
    assert day.q_heat_wh <= sum(q_heat[ts] for ts in keys if ts not in idle) + 1e-9


def test_aggregate_days_skips_non_finite_and_short_days() -> None:
    start = NOW - 2 * 86400
    start -= start % 86400
    keys = [start + index * 3600 for index in range(36)]
    q_heat = {ts: 400.0 for ts in keys}
    indoor = {ts: 21.0 for ts in keys}
    outdoor = {ts: 5.0 for ts in keys}
    ghi = {ts: 100.0 for ts in keys}
    q_heat[keys[0]] = float("nan")
    indoor[keys[1]] = float("inf")

    days = aggregate_days(q_heat, indoor, outdoor, ghi)

    assert len(days) == 2
    assert days[0].hours == 22  # 24 hours minus one NaN and one inf row
    assert all(day.hours < 24 for day in days)


def test_daily_fit_recovers_the_aperture() -> None:
    model = fit_daily_solar_model(make_daily_rows())

    assert model.valid
    assert model.a_w_per_k == pytest.approx(150.0, abs=12.0)
    assert model.b_m2 == pytest.approx(1.8, abs=0.4)
    assert model.trust > 0.5
    assert model.effective_b_m2 == pytest.approx(model.b_m2 * model.trust)
    assert model.n_days == 26


def test_day_blocks_keep_the_hours_the_heating_sample_drops() -> None:
    """Why the day-block estimator exists, as a regression test.

    The hourly sample is a heating-hour sample: on a sunny day the room term
    stops the circuit, so the strongest-sun hours never reach it. The day
    blocks keep those same hours with ``q = 0``, which is the only way the
    aperture can be seen at all.

    Note the day-block fit is *attenuated* in this synthetic world: a demand
    truncated at zero is not exactly linear, so the recovered ``b`` is a lower
    bound on the true one. Being biased down is the safe direction.
    """
    a, b = 150.0, 1.8
    q_heat, indoor, outdoor, ghi = make_censored_history(a=a, b=b, days=40)
    samples = build_samples(q_heat, indoor, outdoor, ghi)
    days = aggregate_days(q_heat, indoor, outdoor, ghi)

    peak_ghi = max(ghi.values())
    sunny_idle = [
        ts
        for ts in ghi
        if ghi[ts] >= 100.0 and q_heat[ts] < MIN_SAMPLE_Q_W
    ]
    assert sunny_idle  # the sun really does switch the circuit off
    assert max(sample.ghi_wm2 for sample in samples) < 0.5 * peak_ghi
    assert all(day.hours == 24 for day in days)
    assert max(day.sum_ghi_wm2 for day in days) > 0.0

    daily = fit_daily_solar_model(days)

    assert daily.valid
    assert daily.a_w_per_k > 0.0
    assert 0.0 < daily.b_m2 <= b


def test_daily_fit_rejects_too_few_days() -> None:
    model = fit_daily_solar_model(make_daily_rows(days=DAILY_MIN_DAYS - 1))

    assert not model.valid
    assert "days" in model.notes
    assert model.effective_b_m2 == 0.0


def test_daily_fit_rejects_days_without_heating() -> None:
    model = fit_daily_solar_model(
        make_daily_rows(mutate=lambda row, index: replace(row, heating_hours=1))
    )

    assert not model.valid
    assert "days 0" in model.notes


def test_daily_fit_rejects_non_positive_aperture() -> None:
    model = fit_daily_solar_model(make_daily_rows(b=-1.2))

    assert not model.valid
    assert "aperture" in model.notes


def test_daily_fit_rejects_a_noisy_window() -> None:
    noisy = fit_daily_solar_model(make_daily_rows(noise=6000.0, seed=31))

    assert not noisy.valid
    assert "r2" in noisy.notes or "t" in noisy.notes


def test_daily_fit_rejects_a_single_day_that_carries_the_estimate() -> None:
    # Every day but one shares the same irradiance, so no day but that one
    # carries any information about b at all. One day must never be enough to
    # switch a solar term on.
    rows = make_daily_rows(
        days=26, mutate=lambda row, index: replace(row, sum_ghi_wm2=1000.0)
    )
    rows = [
        replace(row, sum_ghi_wm2=20000.0, q_heat_wh=row.q_heat_wh - 30000.0)
        if index == 5
        else row
        for index, row in enumerate(rows)
    ]

    model = fit_daily_solar_model(rows)

    assert not model.valid
    assert "jackknife" in model.notes


def test_daily_fit_records_halves_and_jackknife() -> None:
    model = fit_daily_solar_model(make_daily_rows())

    assert model.valid
    assert model.b_first_half_m2 is not None
    assert model.b_second_half_m2 is not None
    assert model.b_jackknife_min_m2 is not None
    assert model.b_jackknife_max_m2 is not None
    assert model.b_jackknife_min_m2 <= model.b_m2 <= model.b_jackknife_max_m2
    assert model.b_jackknife_min_m2 > 0.0
    assert "jackknife" in model.notes
    assert model.t_stat is not None and model.t_stat >= DAILY_MIN_T_STAT
    assert model.r2 >= DAILY_MIN_R2


def test_daily_fit_rejects_singular_and_short_designs() -> None:
    flat = make_daily_rows(
        mutate=lambda row, index: replace(
            row, sum_delta_t_k=0.0, sum_ghi_wm2=0.0, q_heat_wh=500.0
        )
    )
    model = fit_daily_solar_model(flat)

    assert not model.valid
    assert "daily fit failed" in model.notes


def test_ols_three_recovers_coefficients_and_guards() -> None:
    rows = [(1.0, 2.0, 1.0), (2.0, 1.0, 1.0), (3.0, 4.0, 1.0), (4.0, 3.0, 1.0)]
    beta, stderr, r2 = _ols_three(
        [row[0] for row in rows],
        [row[1] for row in rows],
        [row[2] for row in rows],
        [3.0 * row[0] - 2.0 * row[1] + 7.0 for row in rows],
    )

    assert beta[0] == pytest.approx(3.0)
    assert beta[1] == pytest.approx(-2.0)
    assert beta[2] == pytest.approx(7.0)
    assert r2 == pytest.approx(1.0)
    assert all(value >= 0.0 for value in stderr)

    with pytest.raises(ValueError):
        _ols_three([1.0, 2.0], [1.0, 2.0], [1.0, 1.0], [1.0, 2.0])


def test_effective_solar_model_prefers_the_gated_day_block() -> None:
    hourly = SolarModel(
        a_w_per_k=100.0,
        b_m2=0.5,
        c_w=0.0,
        trust=1.0,
        r2_opaque=0.0,
        r2_solar=0.0,
        b_std_err=0.1,
        n_opaque=10,
        n_solar=10,
        valid=True,
    )
    daily = fit_daily_solar_model(make_daily_rows())

    aperture, loss = effective_solar_model(hourly, daily)
    assert aperture == pytest.approx(daily.effective_b_m2)
    assert loss == pytest.approx(daily.a_w_per_k)

    # An ungated day-block model must never be used, however good it looks.
    rejected = replace(daily, valid=False, trust=1.0)
    assert effective_solar_model(hourly, rejected) == (
        pytest.approx(hourly.effective_b_m2),
        pytest.approx(hourly.a_w_per_k),
    )
    assert effective_solar_model(None, rejected) == (0.0, 0.0)
    assert effective_solar_model(None, None) == (0.0, 0.0)


def make_gated_rows(
    *,
    a: float = 152.0,
    b: float = 1.8,
    sdt_range: tuple[float, float] = (130.0, 230.0),
    ghi_range: tuple[float, float] = (300.0, 3300.0),
    noise: float = 40.0,
    days: int = 26,
    seed: int = 41,
) -> list[SolarDay]:
    """Day blocks built around the production equation, for gate tests."""
    rng = random.Random(seed)
    rows: list[SolarDay] = []
    for index in range(days):
        sum_delta_t = sdt_range[0] + (sdt_range[1] - sdt_range[0]) * rng.random()
        sum_ghi = ghi_range[0] + (ghi_range[1] - ghi_range[0]) * rng.random()
        q_heat = (
            a * sum_delta_t
            + 24.0 * -600.0
            - b * sum_ghi
            + rng.gauss(0.0, noise)
        )
        rows.append(
            SolarDay(
                day_ts=index * 86400,
                sum_delta_t_k=sum_delta_t,
                sum_ghi_wm2=sum_ghi,
                q_heat_wh=q_heat,
                hours=24,
                heating_hours=12,
            )
        )
    return rows


def test_daily_fit_rejects_a_non_positive_loss_coefficient() -> None:
    rows = [
        replace(row, q_heat_wh=20000.0 - 40.0 * (row.sum_delta_t_k - 200.0))
        for row in make_gated_rows()
    ]

    model = fit_daily_solar_model(rows)

    assert not model.valid
    assert model.notes == "a ≤ 0"
    assert model.a_w_per_k < 0.0


def test_daily_fit_rejects_an_absurd_aperture() -> None:
    rows = [
        replace(
            row,
            sum_ghi_wm2=1.0 + 0.5 * index,
            q_heat_wh=row.q_heat_wh - 15.0 * (1.0 + 0.5 * index),
        )
        for index, row in enumerate(make_gated_rows())
    ]

    model = fit_daily_solar_model(rows)

    assert not model.valid
    assert "m² >" in model.notes


def test_daily_fit_rejects_a_window_that_explains_too_little() -> None:
    rows = make_gated_rows(
        sdt_range=(100.0, 300.0), ghi_range=(0.0, 20000.0), noise=6000.0, seed=5
    )

    model = fit_daily_solar_model(rows)

    assert not model.valid
    assert model.t_stat is not None and model.t_stat >= DAILY_MIN_T_STAT
    assert model.r2 < DAILY_MIN_R2
    assert "r2" in model.notes


def test_daily_fit_rejects_a_day_that_swings_the_estimate() -> None:
    """One wild day must not decide the aperture, even when t looks superb."""
    rows = make_gated_rows(
        ghi_range=(1000.0, 1005.0), noise=300.0, seed=11
    )
    rows = [
        replace(
            row,
            sum_ghi_wm2=30000.0,
            q_heat_wh=152.0 * row.sum_delta_t_k - 14400.0 - 6.0 * 30000.0,
        )
        if index == 7
        else row
        for index, row in enumerate(rows)
    ]

    model = fit_daily_solar_model(rows)

    assert not model.valid
    assert model.t_stat is not None and model.t_stat > DAILY_MIN_T_STAT
    assert "jackknife" in model.notes


def test_aggregate_days_ignores_unparsable_values() -> None:
    start = NOW - 86400
    start -= start % 86400
    keys = [start + index * 3600 for index in range(4)]
    q_heat = {ts: 400.0 for ts in keys}
    indoor = {ts: 21.0 for ts in keys}
    outdoor = {ts: 5.0 for ts in keys}
    ghi = {ts: 100.0 for ts in keys}
    q_heat[keys[0]] = "n/a"  # type: ignore[assignment]

    days = aggregate_days(q_heat, indoor, outdoor, ghi)

    assert len(days) == 1
    assert days[0].hours == 3


def test_aggregate_days_adds_the_wind_interaction_column() -> None:
    """Wind multiplies the envelope loss, so the column is Σ(v·ΔT)."""
    start = NOW - 2 * 86400
    start -= start % 86400
    keys = [start + index * 3600 for index in range(48)]
    q_heat = {ts: 400.0 for ts in keys}
    indoor = {ts: 21.0 for ts in keys}
    outdoor = {ts: 5.0 for ts in keys}
    ghi = {ts: 100.0 for ts in keys}
    wind = {ts: 4.0 for ts in keys}

    days = aggregate_days(q_heat, indoor, outdoor, ghi, wind)

    assert len(days) == 2
    for day in days:
        assert day.wind_hours == 24
        assert day.sum_wind_ms_h == pytest.approx(4.0 * 24)
        # ΔT is 16 K every hour.
        assert day.sum_wind_delta_t_k_m_s == pytest.approx(4.0 * 16.0 * 24)
        # The other columns are untouched by the extra input.
        assert day.sum_delta_t_k == pytest.approx(16.0 * 24)


def test_aggregate_days_without_wind_reports_no_wind_hours() -> None:
    """No wind payload is "not measured", not a calm day."""
    q_heat, indoor, outdoor, ghi = make_censored_history(days=3, noise=0.0)

    days = aggregate_days(q_heat, indoor, outdoor, ghi)

    assert days
    assert all(day.wind_hours == 0 for day in days)
    assert all(day.sum_wind_delta_t_k_m_s == 0.0 for day in days)


def test_aggregate_days_counts_only_hours_with_a_wind_reading() -> None:
    """A partially covered day keeps its other sums and its wind evidence count."""
    start = NOW - 86400
    start -= start % 86400
    keys = [start + index * 3600 for index in range(24)]
    q_heat = {ts: 400.0 for ts in keys}
    indoor = {ts: 21.0 for ts in keys}
    outdoor = {ts: 5.0 for ts in keys}
    ghi = {ts: 0.0 for ts in keys}
    wind = {keys[0]: 6.0, keys[1]: 2.0}

    day = aggregate_days(q_heat, indoor, outdoor, ghi, wind)[0]

    assert day.hours == 24
    assert day.wind_hours == 2
    assert day.sum_wind_ms_h == pytest.approx(8.0)
    assert day.sum_wind_delta_t_k_m_s == pytest.approx((6.0 + 2.0) * 16.0)


def _wind_days(
    n_days: int = 30,
    *,
    a: float = 150.0,
    w: float = 2.5,
    b: float = 2.0,
    c: float = 120.0,
    noise: float = 40.0,
    seed: int = 7,
    wind_of_day=None,
):
    """Synthetic day blocks with a known wind sensitivity.

    ``wind_of_day`` defaults to a pattern deliberately out of phase with the
    outdoor cycle, so the interaction column is not collinear with ΣΔT.
    """
    rng = random.Random(seed)
    base = 1_760_000_000 - (1_760_000_000 % 86400)
    wind_of_day = wind_of_day or (lambda index: 4.0 + 2.0 * math.cos(index / 4.0))
    days = []
    for index in range(n_days):
        delta_t = 21.0 - (6.0 + 4.0 * math.sin(index / 5.0))
        ghi = max(0.0, 120.0 + 400.0 * math.sin(index / 3.0))
        wind = wind_of_day(index)
        sum_dt = delta_t * 24
        sum_ghi = ghi * 24
        sum_wind_dt = wind * delta_t * 24
        q_heat = (
            a * sum_dt + w * sum_wind_dt + 24 * c - b * sum_ghi
            + rng.uniform(-noise, noise)
        )
        days.append(
            SolarDay(
                day_ts=base + index * 86400,
                sum_delta_t_k=sum_dt,
                sum_ghi_wm2=sum_ghi,
                q_heat_wh=q_heat,
                hours=24,
                heating_hours=24,
                sum_wind_delta_t_k_m_s=sum_wind_dt,
                sum_wind_ms_h=wind * 24,
                wind_hours=24,
            )
        )
    return days


def test_wind_coefficient_is_recovered_from_the_interaction_column() -> None:
    """A known wind sensitivity comes back, with its evidence count."""
    model = fit_daily_solar_model(_wind_days(w=2.5))

    assert model.valid
    assert model.wind_valid
    assert model.wind_hours == 30 * 24
    assert model.w_per_ms == pytest.approx(2.5, abs=0.4)
    assert model.w_t_stat is not None and model.w_t_stat >= DAILY_MIN_WIND_T_STAT
    assert "w=" in model.wind_notes


def test_wind_term_is_invalid_without_wind_data() -> None:
    """No wind payload is "not measured": no coefficient, and the solar model
    is untouched."""
    days = _wind_days(w=2.5)
    for day in days:
        day_windless = SolarDay(
            day_ts=day.day_ts,
            sum_delta_t_k=day.sum_delta_t_k,
            sum_ghi_wm2=day.sum_ghi_wm2,
            q_heat_wh=day.q_heat_wh,
            hours=day.hours,
            heating_hours=day.heating_hours,
        )
        assert day_windless.wind_hours == 0
    windless = [
        SolarDay(
            day_ts=day.day_ts,
            sum_delta_t_k=day.sum_delta_t_k,
            sum_ghi_wm2=day.sum_ghi_wm2,
            q_heat_wh=day.q_heat_wh,
            hours=day.hours,
            heating_hours=day.heating_hours,
        )
        for day in days
    ]

    model = fit_daily_solar_model(windless)

    assert model.valid
    assert model.wind_valid is False
    assert model.w_per_ms is None
    assert model.wind_hours == 0
    assert model.wind_notes == "no wind data in the window"


def test_wind_needs_evidence_hours_not_days() -> None:
    """A solar-valid window with sparse wind evidence still gives no term.

    The three-column fit never sees wind, so the window stays valid: exactly
    the case the gate exists for, and the one a day-count regression would
    otherwise pass (30 days, 4 wind hours each).
    """
    days = [replace(day, wind_hours=4) for day in _wind_days(w=2.5)]
    model = fit_daily_solar_model(days)

    assert model.valid
    assert model.wind_valid is False
    assert model.w_per_ms is None
    assert model.wind_hours == 30 * 4 < DAILY_MIN_WIND_HOURS
    assert f"wind hours {30 * 4} < {DAILY_MIN_WIND_HOURS}" in model.wind_notes


def test_wind_fit_is_attempted_only_where_the_balance_is_valid() -> None:
    """Too few days: no solar model, and the dump says why there is no wind."""
    model = fit_daily_solar_model(_wind_days(n_days=5, w=2.5))

    assert model.valid is False
    assert model.wind_valid is False
    assert model.wind_hours == 5 * 24
    assert "solar balance was not valid" in model.wind_notes


def test_negative_wind_sensitivity_is_rejected() -> None:
    """Wind cannot reduce the envelope loss; a negative w is the season."""
    model = fit_daily_solar_model(_wind_days(w=-2.0))

    assert model.wind_valid is False
    assert model.w_per_ms is not None and model.w_per_ms < 0
    assert "w < 0" in model.wind_notes


def test_absurd_wind_sensitivity_is_rejected() -> None:
    """A coefficient that would multiply the loss at 5 m/s is the fit failing.

    Tested through the gate itself: with a large true ``w`` the three-column
    balance misattributes it into ``b`` first, so the reachable path to this
    check is a small stated loss against a large recovered coefficient.
    """
    _solar, wind = _fit_wind_coefficient(
        _wind_days(a=150.0, w=15.0), a_three=50.0, b_three=2.0
    )

    assert wind["wind_valid"] is False
    assert "W/K per m/s" in wind["wind_notes"]


def test_wind_column_collinear_with_delta_t_is_rejected() -> None:
    """A constant wind makes the interaction column a copy of ΣΔT."""
    model = fit_daily_solar_model(_wind_days(w=2.5, wind_of_day=lambda _i: 4.0))

    assert model.wind_valid is False
    assert "wind fit failed" in model.wind_notes
