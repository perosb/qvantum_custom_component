"""Tests for the self-calibrating solar-gain identification core."""

from __future__ import annotations

import ast
import math
import random
from pathlib import Path

import pytest

from custom_components.qvantum.solar_gain import (
    MIN_FIT_ROWS,
    OPAQUE_GHI_MAX_WM2,
    SolarModel,
    SolarSample,
    _median,
    _recency_weights,
    _robust_weights_line,
    _trust_from_t_stat,
    _weighted_r2,
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
