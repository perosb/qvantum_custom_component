"""Tests for the pure heating-curve computation."""

from __future__ import annotations

import pytest

from custom_components.qvantum.heating_curve import (
    room_gain_from_active_hours,
    ActiveRoomGain,
    BASELINE_MAX_CORRECTION_C,
    BASELINE_MIN_CORRECTION_C,
    COP_FEEDBACK_MAX_C,
    INDOOR_CAP_C,
    LOAD_MAX_C,
    MAX_SUPPLY_C,
    MIN_SUPPLY_C,
    NIGHT_DAY_MAX_C,
    OUTDOOR_MAX_C,
    PRECHARGE_MAX_C,
    ROOM_GAIN_MAX_C,
    ROOM_GAIN_MIN_SAMPLES,
    ROOM_GAIN_MIN_SPAN_C,
    SOLAR_MAX_C,
    TOTAL_MAX_C,
    TRIM_MAX_C,
    CurveResult,
    DayPhase,
    compute_curve,
    cop_feedback_adjustment_c,
    corrected_baseline,
    curve_slope,
    diurnal_swing_c,
    effective_supply_bounds,
    interpolate_supply,
    load_adjustment_c,
    lift_baseline_floor,
    night_day_adjustment_c,
    normalize_baseline,
    outdoor_adjustment_c,
    precharge_adjustment_c,
    room_compensation_gain,
    room_corrected_deviation,
    solar_adjustment_c,
    trim_evidence_hours,
    trim_residuals,
)
from custom_components.qvantum.solar_gain import SolarDailyModel, SolarModel

BASE_HOUR = (1_760_000_000 // 3600) * 3600
DAY0 = (BASE_HOUR // 86400) * 86400

BASELINE = {
    "curve_30": 25.0,
    "curve_20": 30.0,
    "curve_10": 36.0,
    "curve_0": 42.0,
    "curve_minus_10": 48.0,
    "curve_minus_20": 54.0,
    "curve_minus_30": 60.0,
}

DAYLIGHT = DayPhase(
    sunrise_ts=DAY0 + 6 * 3600,
    sunset_ts=DAY0 + 18 * 3600,
    previous_sunset_ts=DAY0 - 6 * 3600,
    next_sunrise_ts=DAY0 + 30 * 3600,
)


def make_model(
    *,
    a: float = 161.0,
    b: float = 2.0,
    c: float = 300.0,
    trust: float = 1.0,
    valid: bool = True,
) -> SolarModel:
    return SolarModel(
        a_w_per_k=a,
        b_m2=b,
        c_w=c,
        trust=trust,
        r2_opaque=0.9,
        r2_solar=0.9,
        b_std_err=0.01,
        n_opaque=100,
        n_solar=100,
        valid=valid,
    )


def forecast_map(
    now_c: float | None,
    ahead: tuple[float, ...] = (),
    now_ts: int = BASE_HOUR,
) -> dict[int, float]:
    mapping: dict[int, float] = {}
    if now_c is not None:
        mapping[now_ts] = now_c
    for index, temperature in enumerate(ahead, start=1):
        mapping[now_ts + index * 3600] = temperature
    return mapping


def ghi_flat(value: float = 500.0, span: int = 6, now_ts: int = BASE_HOUR) -> dict[int, float]:
    return {now_ts + step * 3600: value for step in range(-span, span + 1)}


def test_normalize_baseline_clamps_and_enforces_monotone() -> None:
    points = normalize_baseline(BASELINE)

    assert points == (
        (30.0, 25.0),
        (20.0, 30.0),
        (10.0, 36.0),
        (0.0, 42.0),
        (-10.0, 48.0),
        (-20.0, 54.0),
        (-30.0, 60.0),
    )

    clamped = normalize_baseline({"curve_30": 5, "curve_20": 90, **{
        key: 50 for key in BASELINE if key not in {"curve_30", "curve_20"}
    }})
    assert clamped[0] == (30.0, MIN_SUPPLY_C)
    assert all(supply == MAX_SUPPLY_C for _, supply in clamped[1:])

    repaired = normalize_baseline({**BASELINE, "curve_20": 20.0})
    assert repaired[1] == (20.0, 25.0)
    assert repaired[2] == (10.0, 36.0)


def test_normalize_baseline_requires_all_points() -> None:
    incomplete = {key: value for key, value in BASELINE.items() if key != "curve_0"}

    with pytest.raises(ValueError, match="curve_0"):
        normalize_baseline(incomplete)


def test_interpolate_supply_between_and_outside_points() -> None:
    points = normalize_baseline(BASELINE)

    assert interpolate_supply(points, 30.0) == 25.0
    assert interpolate_supply(points, -30.0) == 60.0
    assert interpolate_supply(points, 35.0) == 25.0
    assert interpolate_supply(points, -35.0) == 60.0
    assert interpolate_supply(points, 5.0) == pytest.approx(39.0)
    assert interpolate_supply(points, -25.0) == pytest.approx(57.0)


def test_curve_slope_is_negative() -> None:
    points = normalize_baseline(BASELINE)

    assert curve_slope(points, 5.0) == pytest.approx(-0.6)
    assert curve_slope(points, 0.0) == pytest.approx(-0.6)
    assert curve_slope(points, 25.0) == pytest.approx(-0.5)
    assert curve_slope(points, 100.0) == 0.0


def test_outdoor_adjustment_anticipates_forecast_trend() -> None:
    points = normalize_baseline(BASELINE)

    colder = outdoor_adjustment_c(points, forecast_map(10.0, (5.0,) * 6), BASE_HOUR)
    assert colder == pytest.approx(OUTDOOR_MAX_C)

    warmer = outdoor_adjustment_c(points, forecast_map(10.0, (13.0, 13.0)), BASE_HOUR)
    assert warmer == pytest.approx(-0.9)

    small = outdoor_adjustment_c(points, forecast_map(10.0, (11.0,)), BASE_HOUR)
    assert small == pytest.approx(-0.3)

    assert outdoor_adjustment_c(points, forecast_map(None), BASE_HOUR) == 0.0
    assert outdoor_adjustment_c(points, forecast_map(10.0), BASE_HOUR) == 0.0
    assert outdoor_adjustment_c(points, {}, BASE_HOUR) == 0.0


def test_diurnal_swing_c_measures_range() -> None:
    assert diurnal_swing_c(forecast_map(10.0, (5.0, 15.0)), BASE_HOUR) == 10.0
    assert diurnal_swing_c(forecast_map(10.0, (10.0,)), BASE_HOUR) == 0.0
    assert diurnal_swing_c(forecast_map(10.0), BASE_HOUR) is None
    assert diurnal_swing_c({}, BASE_HOUR) is None
    assert diurnal_swing_c(forecast_map(None), BASE_HOUR) is None
    assert diurnal_swing_c({BASE_HOUR: float("nan"), BASE_HOUR + 3600: 5.0}, BASE_HOUR) is None


def test_night_day_adjustment_follows_sun() -> None:
    amplitude = 0.75

    assert night_day_adjustment_c(
        DAY0 + 12 * 3600, DAYLIGHT, amplitude_c=amplitude
    ) == pytest.approx(amplitude)
    assert night_day_adjustment_c(
        DAY0, DAYLIGHT, amplitude_c=amplitude
    ) == pytest.approx(-amplitude)
    assert night_day_adjustment_c(
        DAY0 + 6 * 3600, DAYLIGHT, amplitude_c=amplitude
    ) == 0.0
    assert night_day_adjustment_c(
        DAY0 + 18 * 3600, DAYLIGHT, amplitude_c=amplitude
    ) == pytest.approx(0.0, abs=1e-12)
    assert night_day_adjustment_c(
        DAY0 + 21 * 3600, DAYLIGHT, amplitude_c=amplitude
    ) == pytest.approx(-amplitude * 0.7071, abs=1e-4)
    assert night_day_adjustment_c(DAY0 + 12 * 3600, None, amplitude_c=amplitude) == 0.0
    assert night_day_adjustment_c(DAY0 + 12 * 3600, DAYLIGHT, amplitude_c=0.0) == 0.0


def test_night_day_adjustment_degenerate_spans() -> None:
    flat = DayPhase(
        sunrise_ts=DAY0,
        sunset_ts=DAY0,
        previous_sunset_ts=DAY0,
        next_sunrise_ts=DAY0,
    )

    assert night_day_adjustment_c(DAY0, flat, amplitude_c=0.5) == 0.0
    assert night_day_adjustment_c(DAY0 + 3600, flat, amplitude_c=0.5) == 0.0
    assert night_day_adjustment_c(DAY0 - 3600, flat, amplitude_c=0.5) == 0.0


def test_solar_adjustment_scales_with_trust_and_caps() -> None:
    points = normalize_baseline(BASELINE)

    strong = solar_adjustment_c(make_model(), ghi_flat(), BASE_HOUR, points, 0.0)
    assert strong == pytest.approx(-SOLAR_MAX_C)

    weak = solar_adjustment_c(
        make_model(b=0.1, trust=0.5), ghi_flat(), BASE_HOUR, points, 0.0
    )
    assert weak == pytest.approx(-25.0 / 161.0 * 0.6, abs=1e-3)

    assert solar_adjustment_c(None, ghi_flat(), BASE_HOUR, points, 0.0) == 0.0
    assert (
        solar_adjustment_c(
            make_model(valid=False), ghi_flat(), BASE_HOUR, points, 0.0
        )
        == 0.0
    )
    assert solar_adjustment_c(make_model(a=0.0), ghi_flat(), BASE_HOUR, points, 0.0) == 0.0
    assert solar_adjustment_c(make_model(), {}, BASE_HOUR, points, 0.0) == 0.0
    assert (
        solar_adjustment_c(
            make_model(), {BASE_HOUR: 0.0}, BASE_HOUR, points, 0.0
        )
        == 0.0
    )

    no_forecast = solar_adjustment_c(make_model(), ghi_flat(), BASE_HOUR, points, None)
    assert no_forecast == pytest.approx(-SOLAR_MAX_C)


def test_load_adjustment_reduces_when_pump_coasts() -> None:
    model = make_model()

    assert load_adjustment_c(model, None, 21.0, 0.0, {}, BASE_HOUR) == 0.0
    assert load_adjustment_c(model, 1000.0, None, 0.0, {}, BASE_HOUR) == 0.0
    assert load_adjustment_c(model, 1000.0, 21.0, None, {}, BASE_HOUR) == 0.0

    expected = 161.0 * 21.0 + 300.0
    assert load_adjustment_c(model, expected, 21.0, 0.0, {}, BASE_HOUR) == 0.0
    assert load_adjustment_c(model, expected * 1.1, 21.0, 0.0, {}, BASE_HOUR) == 0.0
    assert load_adjustment_c(
        model, expected * 0.5, 21.0, 0.0, {}, BASE_HOUR
    ) == pytest.approx(-LOAD_MAX_C)
    assert load_adjustment_c(
        model, expected * 0.8, 21.0, 0.0, {}, BASE_HOUR
    ) == pytest.approx(-0.2)

    # Predicted demand already includes the solar term.
    expected_with_sun = expected - 1000.0
    assert (
        load_adjustment_c(
            model, expected_with_sun, 21.0, 0.0, ghi_flat(), BASE_HOUR
        )
        == 0.0
    )

    # Too little predicted demand to define a ratio.
    tiny = make_model(a=10.0, c=0.0)
    assert load_adjustment_c(tiny, 0.0, 21.0, 20.0, {}, BASE_HOUR) == 0.0


def test_cop_feedback_only_reduces_below_trigger() -> None:
    reference = 4.0

    # At or above 90 % of the reference there is nothing to correct.
    assert cop_feedback_adjustment_c(4.0, reference) == 0.0
    assert cop_feedback_adjustment_c(3.6, reference) == 0.0
    # Full reduction at and below 70 % of the reference.
    assert cop_feedback_adjustment_c(2.8, reference) == pytest.approx(
        -COP_FEEDBACK_MAX_C
    )
    assert cop_feedback_adjustment_c(1.0, reference) == pytest.approx(
        -COP_FEEDBACK_MAX_C
    )
    # Linear in between.
    assert cop_feedback_adjustment_c(3.2, reference) == pytest.approx(
        -COP_FEEDBACK_MAX_C / 2.0
    )


def test_cop_feedback_requires_a_trusted_reference() -> None:
    assert cop_feedback_adjustment_c(None, 4.0) == 0.0
    assert cop_feedback_adjustment_c(2.0, None) == 0.0
    assert cop_feedback_adjustment_c(2.0, 0.0) == 0.0
    assert cop_feedback_adjustment_c(0.0, 4.0) == 0.0
    assert cop_feedback_adjustment_c(float("nan"), 4.0) == 0.0
    assert cop_feedback_adjustment_c(2.0, float("inf")) == 0.0
    assert cop_feedback_adjustment_c("x", 4.0) == 0.0


def test_compute_curve_applies_cop_feedback_before_caps() -> None:
    base = compute_curve(
        baseline=BASELINE,
        now_ts=BASE_HOUR,
        forecast_temperature={},
        ghi_by_hour={},
    )
    reduced = compute_curve(
        baseline=BASELINE,
        now_ts=BASE_HOUR,
        forecast_temperature={},
        ghi_by_hour={},
        cop_c=-1.0,
    )

    assert reduced.cop_c == -1.0
    assert reduced.adjustment_c == pytest.approx(-1.0)
    assert all(
        reduced_point[1] == pytest.approx(base_point[1] - 1.0)
        for reduced_point, base_point in zip(reduced.points, base.points)
    )


def test_precharge_raises_only_on_a_forecast_drop() -> None:
    now = 1_760_000_000 // 3600 * 3600

    # No forecast, no future hours or a flat/small drop: no term.
    assert precharge_adjustment_c({}, now) == 0.0
    assert precharge_adjustment_c({now: 2.0}, now) == 0.0
    assert precharge_adjustment_c({now: 2.0, now + 3600: 1.0}, now) == 0.0
    # At the trigger the raise just starts.
    assert precharge_adjustment_c({now: 8.0, now + 3600: 4.0}, now) == 0.0
    # Half a trigger further down gives half the raise.
    assert precharge_adjustment_c(
        {now: 8.0, now + 3600: 2.0}, now
    ) == pytest.approx(PRECHARGE_MAX_C / 2.0)
    # A deeper drop caps at the maximum raise.
    assert precharge_adjustment_c(
        {now: 8.0, now + 3600: -4.0}, now
    ) == pytest.approx(PRECHARGE_MAX_C)
    # The minimum over the horizon drives the term, not the last hour.
    assert precharge_adjustment_c(
        {now: 8.0, now + 3600: 7.0, now + 2 * 3600: -1.0}, now
    ) == pytest.approx(PRECHARGE_MAX_C)


def test_compute_curve_applies_precharge_before_caps() -> None:
    base = compute_curve(
        baseline=BASELINE,
        now_ts=BASE_HOUR,
        forecast_temperature={},
        ghi_by_hour={},
    )
    raised = compute_curve(
        baseline=BASELINE,
        now_ts=BASE_HOUR,
        forecast_temperature={},
        ghi_by_hour={},
        precharge_c=1.0,
    )

    assert raised.precharge_c == 1.0
    assert raised.adjustment_c == pytest.approx(1.0)
    assert all(
        raised_point[1] == pytest.approx(base_point[1] + 1.0)
        for raised_point, base_point in zip(raised.points, base.points)
    )


def _observed_pairs(offset: float, *, span: int = 20) -> list[tuple[float, float]]:
    base = normalize_baseline(BASELINE)
    return [
        (float(outdoor), interpolate_supply(base, float(outdoor)) + offset)
        for outdoor in range(span + 1)
        for _ in range(2)
    ]


def test_corrected_baseline_shifts_and_keeps_shape() -> None:
    learned = corrected_baseline(BASELINE, _observed_pairs(-3.0))

    assert learned is not None
    for key, value in BASELINE.items():
        assert learned[key] == pytest.approx(value - 3.0)
    # Outside the observed range the cached shape is kept, shifted by the fit.
    assert learned["curve_30"] == pytest.approx(22.0)
    assert learned["curve_minus_30"] == pytest.approx(57.0)


LOW_WARM_END = {
    "curve_30": 16.8,
    "curve_20": 18.6,
    "curve_10": 27.5,
    "curve_0": 35.9,
    "curve_minus_10": 45.7,
    "curve_minus_20": 57.5,
    "curve_minus_30": 59.4,
}


def test_lift_baseline_floor_raises_only_the_pinned_warm_end() -> None:
    """A knot under the pump's minimum is clamped every hour: lift it.

    The warm end of a learned baseline can land below the floor the firmware
    accepts, which pins those points, hides their trim feedback and makes the
    clamping structural instead of weather-driven.
    """
    lifted = lift_baseline_floor(LOW_WARM_END, 20.0)

    assert lifted["curve_30"] == 20.0
    assert lifted["curve_20"] == 20.0
    # Everything already inside the bounds is untouched.
    for key in ("curve_10", "curve_0", "curve_minus_10", "curve_minus_20"):
        assert lifted[key] == LOW_WARM_END[key]
    # Still a valid, non-decreasing table toward colder outdoor.
    supplies = list(lifted.values())
    assert supplies == sorted(supplies)


def test_lift_baseline_floor_without_a_floor_is_a_noop() -> None:
    # The register default is 10 °C, so a warm end of 16.8 stays as it is.
    assert lift_baseline_floor(LOW_WARM_END) == LOW_WARM_END


def test_lift_baseline_floor_never_lowers_an_upper_point() -> None:
    """Only the floor is applied: a demand above the ceiling stays visible."""
    lifted = lift_baseline_floor({**LOW_WARM_END, "curve_minus_30": 90.0}, 20.0)

    assert lifted["curve_minus_30"] == MAX_SUPPLY_C


def test_corrected_baseline_rejects_small_or_thin_data() -> None:
    assert corrected_baseline(BASELINE, _observed_pairs(-3.0)[:10]) is None
    assert corrected_baseline(BASELINE, [(0.0, 30.0)] * 30) is None
    assert corrected_baseline(BASELINE, _observed_pairs(-3.0, span=2)) is None
    assert corrected_baseline({"curve_0": 42.0}, _observed_pairs(-3.0)) is None
    # Below the noise threshold: keep the cached baseline.
    assert (
        corrected_baseline(BASELINE, _observed_pairs(-BASELINE_MIN_CORRECTION_C / 2))
        is None
    )
    # Non-finite rows are dropped, so the correction falls back to "too thin".
    noisy = _observed_pairs(-3.0)[:10] + [(float("nan"), 30.0)] * 30
    assert corrected_baseline(BASELINE, noisy) is None


def test_corrected_baseline_clamps_extreme_correction() -> None:
    learned = corrected_baseline(BASELINE, _observed_pairs(-40.0))

    assert learned is not None
    assert learned["curve_30"] == pytest.approx(
        BASELINE["curve_30"] - BASELINE_MAX_CORRECTION_C
    )
    assert all(MIN_SUPPLY_C <= value <= MAX_SUPPLY_C for value in learned.values())


def test_corrected_baseline_fits_a_tilt() -> None:
    base = normalize_baseline(BASELINE)
    observations = [
        (float(outdoor), interpolate_supply(base, float(outdoor)) - 0.2 * outdoor)
        for outdoor in range(21)
        for _ in range(2)
    ]

    learned = corrected_baseline(BASELINE, observations)

    assert learned is not None
    assert learned["curve_30"] < learned["curve_minus_30"]
    assert learned["curve_minus_30"] == pytest.approx(60.0 + 0.2 * 30.0, abs=0.5)


def _room_observations(
    gain: float,
    *,
    offset: float = 0.0,
    tilt: float = 0.0,
    span: int = 20,
) -> list[tuple[float, float, float]]:
    base = normalize_baseline(BASELINE)
    return [
        (
            float(outdoor),
            interpolate_supply(base, float(outdoor))
            + offset
            + tilt * outdoor
            - gain * error,
            error,
        )
        for outdoor in range(span + 1)
        for error in (-0.5, 0.0, 0.5)
    ]


def test_room_compensation_gain_recovers_the_room_slope() -> None:
    assert room_compensation_gain(
        _room_observations(9.0), BASELINE
    ) == pytest.approx(9.0)
    # The baseline offset and tilt are controlled for, not attributed to the room.
    assert room_compensation_gain(
        _room_observations(4.0, offset=3.0, tilt=0.2), BASELINE
    ) == pytest.approx(4.0)


def test_room_compensation_gain_rejects_thin_or_flat_data() -> None:
    thin = _room_observations(9.0)[: ROOM_GAIN_MIN_SAMPLES - 1]
    assert room_compensation_gain(thin, BASELINE) is None
    # No room-error spread: the slope is not identifiable.
    flat = [(float(outdoor), 40.0, 0.1) for outdoor in range(30)]
    assert room_compensation_gain(flat, BASELINE) is None
    assert room_compensation_gain(_room_observations(9.0), {"curve_0": 42.0}) is None
    # Perfectly collinear outdoor/room error: the room slope is not separable.
    collinear = [(float(outdoor), 40.0, 2.0 * outdoor) for outdoor in range(30)]
    assert room_compensation_gain(collinear, BASELINE) is None
    # A negative fit is noise: clamp to no correction.
    assert room_compensation_gain(_room_observations(-5.0), BASELINE) == 0.0
    # And an absurd slope is capped.
    assert room_compensation_gain(
        _room_observations(200.0), BASELINE
    ) == pytest.approx(ROOM_GAIN_MAX_C)


def test_room_compensation_gain_needs_span_above_threshold() -> None:
    errors = [ROOM_GAIN_MIN_SPAN_C / 4, 0.0, -ROOM_GAIN_MIN_SPAN_C / 4]
    rows = [
        (float(outdoor), 40.0 - 9.0 * error, error)
        for outdoor in range(20)
        for error in errors
    ]
    assert room_compensation_gain(rows, BASELINE) is None


def test_room_corrected_deviation_subtracts_the_room_term() -> None:
    assert room_corrected_deviation(2.0, 9.0, 0.5) == pytest.approx(-2.5)
    assert room_corrected_deviation(2.0, None, 0.5) == 2.0
    assert room_corrected_deviation(2.0, 9.0, None) == 2.0
    assert room_corrected_deviation(None, 9.0, 0.5) is None


def test_trim_evidence_hours_counts_only_qualifying_buckets() -> None:
    # Six hours at 5 °C qualify; two at 9 °C do not.
    observations = [
        (index, outdoor, 0.5)
        for index, outdoor in enumerate([5.0] * 6 + [9.0] * 2)
    ]

    evidence = trim_evidence_hours(observations)

    assert evidence["curve_10"] == 6  # within 7.5 °C of the 5 °C bucket
    assert evidence["curve_0"] == 6
    assert evidence["curve_20"] == 0  # 11 °C away from the nearest
    assert evidence["curve_minus_10"] == 0

    # Non-finite outdoor readings cannot support anything.
    assert set(trim_evidence_hours([(0, float("nan"), 0.1)]).values()) == {0}


def test_effective_supply_bounds_sanitizes_and_falls_back() -> None:
    assert effective_supply_bounds(None, None) == (MIN_SUPPLY_C, MAX_SUPPLY_C)
    assert effective_supply_bounds(20, 60) == (20.0, 60.0)
    assert effective_supply_bounds(5, 90) == (MIN_SUPPLY_C, MAX_SUPPLY_C)
    assert effective_supply_bounds(70, 60) == (MIN_SUPPLY_C, MAX_SUPPLY_C)
    assert effective_supply_bounds(True, "60") == (MIN_SUPPLY_C, MAX_SUPPLY_C)
    assert effective_supply_bounds(None, 55) == (MIN_SUPPLY_C, 55.0)


def _trim_observations(
    error: float,
    *,
    hour: int = BASE_HOUR,
    count: int = 8,
) -> list[tuple[int, float, float]]:
    outlets = (-5.0, 5.0)
    return [
        (hour - index * 3600, outdoor, error)
        for outdoor in outlets
        for index in range(count)
    ]


def test_trim_residuals_maps_indoor_error_to_supply() -> None:
    trims = trim_residuals(BASELINE, _trim_observations(-1.0), BASE_HOUR)

    assert trims is not None
    # slope ≈ −0.6; a 1 °C indoor shortfall needs ≈ +0.6 °C supply where the
    # house has been observed (+/-10 °C), nothing at the extrapolated ends.
    assert trims["curve_10"] == pytest.approx(0.6)
    assert trims["curve_minus_10"] == pytest.approx(0.6)
    assert trims["curve_30"] == 0.0
    assert trims["curve_minus_30"] == 0.0

    warm = trim_residuals(BASELINE, _trim_observations(1.0), BASE_HOUR)
    assert warm is not None
    assert warm["curve_10"] == pytest.approx(-0.6)


def test_trim_residuals_needs_evidence_and_clamps() -> None:
    assert trim_residuals(BASELINE, _trim_observations(-1.0, count=3), BASE_HOUR) is None
    one_bucket = [
        (BASE_HOUR - index * 3600, -5.0, -1.0) for index in range(8)
    ]
    assert trim_residuals(BASELINE, one_bucket, BASE_HOUR) is None
    assert trim_residuals(BASELINE, _trim_observations(0.0), BASE_HOUR) is None

    strong = trim_residuals(BASELINE, _trim_observations(-10.0), BASE_HOUR)
    assert strong is not None
    assert strong["curve_10"] == pytest.approx(TRIM_MAX_C)


def test_trim_residuals_filters_corrupt_input() -> None:
    assert trim_residuals({"curve_0": 42.0}, _trim_observations(-1.0), BASE_HOUR) is None

    noisy = [(BASE_HOUR, float("nan"), -1.0)] + _trim_observations(-1.0)
    trims = trim_residuals(BASELINE, noisy, BASE_HOUR)

    assert trims is not None
    assert trims["curve_10"] == pytest.approx(0.6)


def test_compute_curve_applies_trims_and_pump_bounds() -> None:
    result = compute_curve(
        baseline=BASELINE,
        now_ts=BASE_HOUR,
        forecast_temperature=forecast_map(10.0),
        ghi_by_hour={},
        point_trims={"curve_minus_30": 5.0},
        min_supply_c=20.0,
        max_supply_c=60.0,
    )

    assert result.trims["curve_minus_30"] == 5.0
    assert result.points[-1] == ("curve_minus_30", 60.0)
    assert result.clamped
    # Which point the limits bit: the boolean alone cannot name the baseline
    # point that has to move.
    assert result.clamped_points == ("curve_minus_30",)

    unclamped = compute_curve(
        baseline=BASELINE,
        now_ts=BASE_HOUR,
        forecast_temperature=forecast_map(10.0),
        ghi_by_hour={},
        min_supply_c=20.0,
        max_supply_c=70.0,
    )
    assert not unclamped.clamped
    assert unclamped.clamped_points == ()
    assert unclamped.trims == {key: 0.0 for key in BASELINE}


def test_compute_curve_cold_trend_raises_all_points() -> None:
    result = compute_curve(
        baseline=BASELINE,
        now_ts=BASE_HOUR,
        forecast_temperature=forecast_map(10.0, (5.0,) * 6),
        ghi_by_hour={},
    )

    assert result.adjustment_c == pytest.approx(OUTDOOR_MAX_C)
    assert result.outdoor_c == pytest.approx(OUTDOOR_MAX_C)
    assert result.solar_c == 0.0
    assert result.night_day_c == 0.0
    assert result.load_c == 0.0
    assert not result.capped_by_indoor
    assert result.as_dict() == {
        "curve_30": 26.0,
        "curve_20": 31.0,
        "curve_10": 37.0,
        "curve_0": 43.0,
        "curve_minus_10": 49.0,
        "curve_minus_20": 55.0,
        "curve_minus_30": 61.0,
    }
    assert result.integer_points() == (
        ("curve_30", 26),
        ("curve_20", 31),
        ("curve_10", 37),
        ("curve_0", 43),
        ("curve_minus_10", 49),
        ("curve_minus_20", 55),
        ("curve_minus_30", 61),
    )


def test_compute_curve_solar_lowers_points() -> None:
    result = compute_curve(
        baseline=BASELINE,
        now_ts=BASE_HOUR,
        forecast_temperature=forecast_map(10.0),
        ghi_by_hour=ghi_flat(),
        model=make_model(),
    )

    assert result.adjustment_c == pytest.approx(-SOLAR_MAX_C)
    assert result.solar_c == pytest.approx(-SOLAR_MAX_C)
    assert result.as_dict()["curve_30"] == 23.0
    assert result.as_dict()["curve_minus_30"] == 58.0


def test_compute_curve_night_setback() -> None:
    result = compute_curve(
        baseline=BASELINE,
        now_ts=DAY0,
        forecast_temperature=forecast_map(0.0, (20.0,) * 6, now_ts=DAY0),
        ghi_by_hour={},
        daylight=DAYLIGHT,
    )

    assert result.night_day_c == pytest.approx(-NIGHT_DAY_MAX_C)
    assert result.adjustment_c == pytest.approx(
        result.outdoor_c - NIGHT_DAY_MAX_C
    )


def test_compute_curve_night_setback_needs_a_swing() -> None:
    # A usable daylight phase but a flat forecast: no invented rhythm offset.
    result = compute_curve(
        baseline=BASELINE,
        now_ts=DAY0,
        forecast_temperature=forecast_map(10.0, (10.0,) * 6, now_ts=DAY0),
        ghi_by_hour={},
        daylight=DAYLIGHT,
    )

    assert result.night_day_c == 0.0


def test_compute_curve_combines_terms_and_clamps_total() -> None:
    result = compute_curve(
        baseline=BASELINE,
        now_ts=DAY0,
        forecast_temperature=forecast_map(5.0, (25.0,) * 6, now_ts=DAY0),
        ghi_by_hour=ghi_flat(now_ts=DAY0),
        model=make_model(),
        daylight=DAYLIGHT,
        indoor_target_c=21.0,
        q_actual_w=0.0,
    )

    assert result.outdoor_c == pytest.approx(-OUTDOOR_MAX_C)
    assert result.night_day_c == pytest.approx(-NIGHT_DAY_MAX_C)
    assert result.solar_c == pytest.approx(-SOLAR_MAX_C)
    assert result.load_c == pytest.approx(-LOAD_MAX_C)
    assert result.adjustment_c == pytest.approx(-TOTAL_MAX_C)
    assert result.as_dict()["curve_minus_30"] == 57.0


def test_compute_curve_indoor_margin_caps_adjustment() -> None:
    warm = compute_curve(
        baseline=BASELINE,
        now_ts=BASE_HOUR,
        forecast_temperature=forecast_map(10.0, (5.0,) * 6),
        ghi_by_hour={},
        indoor_margin_c=1.5,
    )
    assert warm.adjustment_c == 0.0
    assert warm.capped_by_indoor

    partial = compute_curve(
        baseline=BASELINE,
        now_ts=BASE_HOUR,
        forecast_temperature=forecast_map(10.0, (5.0,) * 6),
        ghi_by_hour={},
        indoor_margin_c=0.5,
    )
    assert partial.adjustment_c == pytest.approx(INDOOR_CAP_C - 0.5)
    assert partial.capped_by_indoor

    cold = compute_curve(
        baseline=BASELINE,
        now_ts=DAY0,
        forecast_temperature=forecast_map(5.0, (15.0,) * 6, now_ts=DAY0),
        ghi_by_hour=ghi_flat(now_ts=DAY0),
        model=make_model(),
        daylight=DAYLIGHT,
        indoor_target_c=21.0,
        q_actual_w=0.0,
        indoor_margin_c=-1.5,
    )
    assert cold.adjustment_c == 0.0
    assert cold.capped_by_indoor

    eased = compute_curve(
        baseline=BASELINE,
        now_ts=DAY0,
        forecast_temperature=forecast_map(5.0, (15.0,) * 6, now_ts=DAY0),
        ghi_by_hour=ghi_flat(now_ts=DAY0),
        model=make_model(),
        daylight=DAYLIGHT,
        indoor_target_c=21.0,
        q_actual_w=0.0,
        indoor_margin_c=-0.75,
    )
    assert eased.adjustment_c == pytest.approx(-(INDOOR_CAP_C - 0.75))

    uncapped = compute_curve(
        baseline=BASELINE,
        now_ts=BASE_HOUR,
        forecast_temperature=forecast_map(10.0, (5.0,) * 6),
        ghi_by_hour={},
        indoor_margin_c=None,
    )
    assert not uncapped.capped_by_indoor


def test_compute_curve_clamps_points_to_supply_range() -> None:
    low = compute_curve(
        baseline={**BASELINE, "curve_30": 11.0},
        now_ts=BASE_HOUR,
        forecast_temperature=forecast_map(10.0),
        ghi_by_hour=ghi_flat(),
        model=make_model(),
    )
    assert low.points[0] == ("curve_30", MIN_SUPPLY_C)
    assert all(supply > MIN_SUPPLY_C for _, supply in low.points[1:])

    high = compute_curve(
        baseline={**BASELINE, "curve_minus_30": 79.0},
        now_ts=BASE_HOUR,
        forecast_temperature=forecast_map(10.0, (5.0,) * 6),
        ghi_by_hour={},
    )
    assert high.points[-1] == ("curve_minus_30", MAX_SUPPLY_C)
    assert all(supply < MAX_SUPPLY_C for _, supply in high.points[:-1])


def test_integer_points_round_half_up() -> None:
    result = CurveResult(
        points=(("curve_30", 42.5), ("curve_20", 42.4)),
        adjustment_c=0.0,
        outdoor_c=0.0,
        night_day_c=0.0,
        solar_c=0.0,
        load_c=0.0,
        capped_by_indoor=False,
    )

    assert result.integer_points() == (("curve_30", 43), ("curve_20", 42))


def make_daily_model(
    *,
    a: float = 152.0,
    b: float = 1.9,
    trust: float = 0.7,
    valid: bool = True,
) -> SolarDailyModel:
    return SolarDailyModel(
        a_w_per_k=a,
        b_m2=b,
        c_w=-600.0,
        trust=trust,
        r2=0.92,
        n_days=25,
        b_std_err=0.6,
        t_stat=3.0,
        b_first_half_m2=1.7,
        b_second_half_m2=2.1,
        b_jackknife_min_m2=1.5,
        b_jackknife_max_m2=2.2,
        valid=valid,
        notes="days=25",
    )


def test_solar_adjustment_prefers_the_gated_day_block_pair() -> None:
    points = normalize_baseline(BASELINE)
    daily = make_daily_model()
    hourly = make_model(b=0.0, trust=0.0)

    # The censored hourly model alone emits nothing at all.
    assert solar_adjustment_c(hourly, ghi_flat(), BASE_HOUR, points, 0.0) == 0.0

    term = solar_adjustment_c(hourly, ghi_flat(), BASE_HOUR, points, 0.0, daily)

    gain_w = daily.effective_b_m2 * 500.0
    expected = curve_slope(points, 0.0) * (gain_w / daily.a_w_per_k)
    assert term == pytest.approx(max(-SOLAR_MAX_C, expected), abs=1e-6)
    assert term < 0.0

    # An ungated day-block model must not be used, however good it looks.
    assert (
        solar_adjustment_c(
            hourly, ghi_flat(), BASE_HOUR, points, 0.0, make_daily_model(valid=False)
        )
        == 0.0
    )
    # ... and a zero-aperture day block leaves the hourly fallback in charge.
    assert solar_adjustment_c(
        make_model(), ghi_flat(), BASE_HOUR, points, 0.0, make_daily_model(b=0.0)
    ) == pytest.approx(-SOLAR_MAX_C)


def test_load_adjustment_subtracts_the_same_day_block_gain() -> None:
    model = make_model(b=0.0, trust=0.0)
    daily = make_daily_model()
    gain_w = daily.effective_b_m2 * 500.0
    expected = 161.0 * 21.0 + 300.0

    # Demand already covered by the applied solar term: no load reduction.
    assert (
        load_adjustment_c(
            model, expected - gain_w, 21.0, 0.0, ghi_flat(), BASE_HOUR, daily
        )
        == 0.0
    )
    # Without the daily model the same actual power looks like a shortfall.
    assert load_adjustment_c(
        model, expected - gain_w, 21.0, 0.0, ghi_flat(), BASE_HOUR
    ) < 0.0


def test_compute_curve_accepts_a_daily_model() -> None:
    result = compute_curve(
        baseline=BASELINE,
        now_ts=BASE_HOUR,
        forecast_temperature=forecast_map(0.0),
        ghi_by_hour=ghi_flat(),
        model=make_model(b=0.0, trust=0.0),
        daily_model=make_daily_model(),
    )

    assert result.solar_c < 0.0
    assert result.adjustment_c <= 0.0


# --- active-hour room gain ---------------------------------------------------

#: The table the pump was given, one °C of supply per °C of outdoor.
ACTIVE_TABLE = {6: 30.0, 8: 28.0, 10: 26.0, 12: 24.0}


def active_hours(term: float = 5.0, *, noise: float = 0.0, seed: int = 3):
    """Synthetic active hours: ``cal = table(outdoor) − term · room error``."""
    import random

    rng = random.Random(seed)
    rows = []
    for outdoor, supply in ACTIVE_TABLE.items():
        for error in (-0.4, -0.2, 0.0, 0.2, 0.4):
            wobble = rng.uniform(-noise, noise) if noise else 0.0
            rows.append((float(outdoor), supply - term * error + wobble, error))
    return rows


def test_active_room_gain_recovers_a_known_slope() -> None:
    estimate = room_gain_from_active_hours(active_hours(5.0))

    assert isinstance(estimate, ActiveRoomGain)
    assert estimate.valid
    assert estimate.gain_c_per_c == pytest.approx(5.0)
    assert estimate.hours == 20
    assert estimate.buckets == 4
    assert estimate.span_c == pytest.approx(0.8)
    assert estimate.r2 == pytest.approx(1.0)
    assert estimate.t_stat > 4.0
    assert estimate.notes == ""


def test_active_room_gain_drops_a_lone_outdoor_bucket() -> None:
    """The leverage immunity: an hour alone in its bucket cannot decide it."""
    rows = active_hours(5.0)
    rows.append((14.0, 22.0 - 5.0 * 1.2, 1.2))

    estimate = room_gain_from_active_hours(rows)

    assert estimate.gain_c_per_c == pytest.approx(5.0)
    assert estimate.hours == 20  # the lone hour is not counted
    assert estimate.valid


def test_active_room_gain_needs_enough_hours_and_buckets() -> None:
    estimate = room_gain_from_active_hours(active_hours(5.0)[:6])

    assert not estimate.valid
    assert "buckets" in estimate.notes


def test_active_room_gain_rejects_a_slope_that_is_not_a_room_term() -> None:
    """No term at all: the number is reported, but nothing is published."""
    estimate = room_gain_from_active_hours(active_hours(0.0, noise=0.4))

    assert not estimate.valid
    assert "not a room term" in estimate.notes
    assert estimate.gain_c_per_c < 1.0


def test_active_room_gain_refuses_a_window_it_cannot_test() -> None:
    """Two hours per bucket: dropping one breaks the bucket, so no verdict."""
    rows = [
        row for row in active_hours(5.0) if row[2] in (-0.4, 0.4) and row[0] <= 10.0
    ]

    estimate = room_gain_from_active_hours(rows, min_hours=6, min_buckets=3)

    assert not estimate.valid
    assert estimate.leave_one_out_c_per_c is None
    assert "carries it" in estimate.notes


def test_active_room_gain_needs_the_room_error_to_move() -> None:
    rows = [(float(outdoor), supply, 0.3) for outdoor, supply in ACTIVE_TABLE.items()]

    estimate = room_gain_from_active_hours(rows)

    assert not estimate.valid
    assert "barely moved" in estimate.notes


def test_active_room_gain_ignores_unusable_rows() -> None:
    rows = active_hours(5.0) + [
        (float("nan"), 20.0, 0.2),
        (8.0, float("inf"), 0.2),
        (10.0, 25.0, float("nan")),
    ]

    estimate = room_gain_from_active_hours(rows)

    assert estimate.valid
    assert estimate.hours == 20
    assert estimate.gain_c_per_c == pytest.approx(5.0)
