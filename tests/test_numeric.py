"""Tests for the shared numeric helpers."""

from __future__ import annotations

import math

import pytest

from custom_components.qvantum.numeric import finite, median


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (1.5, 1.5),
        (2, 2.0),
        ("3.25", 3.25),
        (0, 0.0),
    ],
)
def test_finite_passes_usable_numbers_through(raw, expected) -> None:
    assert finite(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        True,
        False,
        "not a number",
        "",
        float("nan"),
        float("inf"),
        float("-inf"),
        object(),
        complex(1, 2),
    ],
)
def test_finite_rejects_everything_that_is_not_a_finite_number(raw) -> None:
    assert finite(raw) is None


def test_finite_rejects_bools_even_though_they_are_ints() -> None:
    """``True`` is 1 numerically; a boolean is a flag, not a measurement."""
    assert finite(True) is None
    assert finite(1) == 1.0


def test_finite_survives_a_float_conversion_overflow() -> None:
    assert finite(10**400) is None


def test_median_of_an_odd_sequence() -> None:
    assert median([3.0, 1.0, 2.0]) == 2.0


def test_median_of_an_even_sequence_averages_the_middle_pair() -> None:
    assert median([4.0, 1.0, 3.0, 2.0]) == 2.5


def test_median_of_one_value_is_that_value() -> None:
    assert median([7.5]) == 7.5


def test_median_does_not_reorder_the_input() -> None:
    values = [3.0, 1.0, 2.0]
    median(values)
    assert values == [3.0, 1.0, 2.0]


def test_median_of_an_empty_sequence_is_an_error() -> None:
    with pytest.raises(IndexError):
        median([])


def test_median_is_not_confused_by_infinities() -> None:
    """The helper is a sort, not a coercion — callers filter with ``finite`` first."""
    assert median([math.inf, 1.0]) == math.inf
