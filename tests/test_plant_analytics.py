"""Tests for pure plant analytics: cycling rate and health grade."""

from __future__ import annotations

import math

import pytest

from custom_components.qvantum.plant_analytics import (
    DEFAULT_HEALTH_WEIGHTS,
    HealthGrade,
    health_grade,
    score_aux_share,
    score_cycling,
    score_scop,
    starts_per_hour,
)


class TestStartsPerHour:
    def test_rate(self):
        assert starts_per_hour(6.0, 3.0) == pytest.approx(2.0)

    def test_too_little_run_time_is_none(self):
        assert starts_per_hour(1.0, 0.5) is None

    def test_missing_or_invalid_is_none(self):
        assert starts_per_hour(None, 3.0) is None
        assert starts_per_hour(3.0, None) is None
        assert starts_per_hour(-1.0, 3.0) is None
        assert starts_per_hour(3.0, -1.0) is None
        assert starts_per_hour("x", 3.0) is None
        assert starts_per_hour(3.0, math.nan) is None


class TestComponentScores:
    def test_scop(self):
        assert score_scop(2.0) == 0.0
        assert score_scop(5.0) == 1.0
        assert score_scop(3.5) == pytest.approx(0.5)

    def test_aux_share(self):
        assert score_aux_share(0.0) == 1.0
        assert score_aux_share(0.2) == 0.0
        assert score_aux_share(0.5) == 0.0

    def test_cycling(self):
        assert score_cycling(1.0) == 1.0
        assert score_cycling(4.0) == 0.0
        assert score_cycling(2.5) == pytest.approx(0.5)


class TestHealthGrade:
    def test_all_components(self):
        grade = health_grade({"scop": 5.0, "aux_share": 0.0, "cycling": 1.0})
        assert grade.letter == "A"
        assert grade.score == pytest.approx(1.0)
        assert grade.coverage == pytest.approx(1.0)
        assert set(grade.components) == {"scop", "aux_share", "cycling"}

    def test_missing_components_renormalise_and_reduce_coverage(self):
        grade = health_grade({"scop": 5.0})
        assert grade.letter == "A"
        assert grade.coverage == pytest.approx(DEFAULT_HEALTH_WEIGHTS["scop"])

    def test_no_components_yields_no_letter(self):
        assert health_grade({}) == HealthGrade(None, None, 0.0, {})

    def test_invalid_values_are_ignored(self):
        grade = health_grade({"scop": True, "aux_share": math.nan, "cycling": "x"})
        assert grade.letter is None
        assert grade.score is None
        assert grade.coverage == 0.0

    def test_letter_boundaries(self):
        assert health_grade({"scop": 5.0}).letter == "A"
        assert health_grade({"scop": 4.25}).letter == "B"
        assert health_grade({"scop": 3.8}).letter == "C"
        assert health_grade({"scop": 3.35}).letter == "D"
        assert health_grade({"scop": 2.0}).letter == "F"

    def test_custom_weights(self):
        grade = health_grade(
            {"scop": 2.0, "aux_share": 0.0},
            weights={"scop": 0.5, "aux_share": 0.5},
        )
        assert grade.score == pytest.approx(0.5)
        assert grade.letter == "D"
        assert grade.coverage == pytest.approx(1.0)
