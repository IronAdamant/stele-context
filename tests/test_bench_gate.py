"""D13 statistics: median intervals and the one-sided Mann-Whitney gate."""

from __future__ import annotations

import math

import pytest

from bench.ci_gate import require_runs
from bench.gate import (
    MIN_RUNS,
    _pairwise_frequencies,
    beats_floor,
    mannwhitney_greater,
    median,
    median_ci,
    median_interval_rank,
    regression,
)


def test_median_odd_and_even() -> None:
    assert median([3, 1, 2]) == 2
    assert median([1, 2, 3, 4]) == 2.5


def test_median_interval_for_fifteen_runs() -> None:
    assert median_interval_rank(15) == 4
    low, high = median_ci(list(range(15)))
    assert (low, high) == (3, 11)
    covered = sum(math.comb(15, k) for k in range(4, 12)) / (2**15)
    assert covered >= 0.95


def test_mannwhitney_frequencies_match_enumeration() -> None:
    import itertools

    n, m = 3, 3
    ways = _pairwise_frequencies(n, m)
    counts = [0] * (n * m + 1)
    for chosen in itertools.combinations(range(n + m), n):
        chosen_set = set(chosen)
        other = [value for value in range(n + m) if value not in chosen_set]
        wins = sum(1 for left in chosen for right in other if left > right)
        counts[wins] += 1
    assert counts == ways
    assert sum(ways) == math.comb(n + m, n)


def test_complete_separation_is_the_boundary() -> None:
    assert mannwhitney_greater([4, 5, 6], [1, 2, 3]) == pytest.approx(0.05)


def test_identical_samples_are_not_a_regression() -> None:
    sample = [float(value) for value in range(MIN_RUNS)]
    verdict = regression(sample, list(sample))
    assert verdict["reject"] is False


def test_large_shift_is_a_regression() -> None:
    verdict = regression([1.0] * MIN_RUNS, [2.0] * MIN_RUNS)
    assert verdict["ci_slower"] is True
    assert verdict["mw_slower"] is True
    assert verdict["reject"] is True


def test_mannwhitney_inside_overlapping_intervals_is_not_a_regression() -> None:
    # Shifted by 3, but each 95% median interval still covers the other median.
    baseline = [float(value) for value in range(MIN_RUNS)]
    candidate = [float(value) + 3 for value in range(MIN_RUNS)]
    verdict = regression(baseline, candidate)
    assert verdict["mw_slower"] is True
    assert verdict["ci_slower"] is False
    assert verdict["reject"] is False


def test_gate_refuses_short_samples() -> None:
    with pytest.raises(ValueError):
        regression([1.0] * 14, [1.0] * MIN_RUNS)


def test_floor_is_strict() -> None:
    assert beats_floor(10.0, 11.0) is True
    assert beats_floor(11.0, 11.0) is False


def test_runner_refuses_fewer_than_fifteen_runs() -> None:
    with pytest.raises(SystemExit):
        require_runs(14, allow_short=False)
    require_runs(2, allow_short=True)
    require_runs(15, allow_short=False)
