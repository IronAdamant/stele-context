"""D13 regression gate.

A candidate is slower than the baseline when either check says so:

* the 95% confidence intervals of the two medians do not overlap and the
  candidate interval sits entirely above the baseline interval, or
* a one-sided Mann-Whitney test says the candidate sample is stochastically
  larger at p < 0.05.

Both need at least 15 runs. A tie at exactly p = 0.05 is not a failure.
The hard floor is separate: a Rust median must be strictly below the recorded
Python median for that benchmark.
"""

from __future__ import annotations

import math
from collections import Counter

MIN_RUNS = 15
ALPHA = 0.05


def median(values: list[float]) -> float:
    if not values:
        raise ValueError("median of an empty sample")
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (float(ordered[mid - 1]) + float(ordered[mid])) / 2.0


def median_interval_rank(n: int, alpha: float = ALPHA) -> int:
    """1-based order-statistic index r for a conservative median CI.

    Largest r such that the left binomial tail through r-1 is at most alpha/2.
    The interval is then [X_(r), X_(n-r+1)].
    """
    if n < 1:
        raise ValueError("empty sample")
    if n == 1:
        return 1
    tail = alpha / 2.0
    acc = 0
    rank = 1
    for i in range(n):
        acc += math.comb(n, i)
        # i is d-1 in the binomial tail. d = i + 1 when that tail still fits.
        if acc / (2**n) <= tail:
            rank = i + 1
        else:
            break
    return min(rank, (n + 1) // 2)


def median_ci(values: list[float], alpha: float = ALPHA) -> tuple[float, float]:
    ordered = sorted(values)
    n = len(ordered)
    rank = median_interval_rank(n, alpha)
    return float(ordered[rank - 1]), float(ordered[n - rank])


def _pairwise_frequencies(n: int, m: int) -> list[int]:
    """Ways to obtain each Mann-Whitney U for sample sizes n and m.

    U counts how often a value from the n-sample is strictly larger than a
    value from the m-sample, under the null of random ranks with no ties.
    """
    if n < 0 or m < 0:
        raise ValueError("negative sample size")
    prev = [[0] * 1 for _ in range(m + 1)]
    for j in range(m + 1):
        prev[j][0] = 1
    for i in range(1, n + 1):
        cur = [[0] * (i * m + 1) for _ in range(m + 1)]
        cur[0][0] = 1
        for j in range(1, m + 1):
            add = j
            left = cur[j - 1]
            earlier = prev[j]
            row = cur[j]
            for u in range(i * j + 1):
                from_left = left[u] if u < len(left) else 0
                from_prev = (
                    earlier[u - add] if u >= add and u - add < len(earlier) else 0
                )
                row[u] = from_left + from_prev
        prev = cur
    return prev[m]


def _average_ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        # 1-based average rank of the tied span.
        avg = (i + 1 + j + 1) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def mannwhitney_greater(candidate: list[float], baseline: list[float]) -> float:
    """One-sided p-value for candidate being stochastically larger.

    Exact when there are no ties and both samples have at most 20 values.
    Otherwise a normal approximation with the tie correction and a continuity
    correction of 0.5.
    """
    n = len(candidate)
    m = len(baseline)
    if n == 0 or m == 0:
        raise ValueError("empty sample")
    combined = list(candidate) + list(baseline)
    ranks = _average_ranks(combined)
    rank_sum = sum(ranks[:n])
    u = rank_sum - n * (n + 1) / 2.0
    ties = [count for count in Counter(combined).values() if count > 1]
    if not ties and n <= 20 and m <= 20:
        ways = _pairwise_frequencies(n, m)
        u_obs = int(round(u))
        total = math.comb(n + m, n)
        numer = sum(ways[u_obs:])
        return numer / total
    capital_n = n + m
    tie_sum = sum(t * t * t - t for t in ties)
    var = n * m * (capital_n + 1) / 12.0
    if capital_n > 1:
        var -= n * m * tie_sum / (12.0 * capital_n * (capital_n - 1))
    if var <= 0:
        return 1.0
    z = (u - n * m / 2.0 - 0.5) / math.sqrt(var)
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def regression(baseline: list[float], candidate: list[float]) -> dict:
    """Return the D13 verdict. ``reject`` means the candidate is slower."""
    if len(baseline) < MIN_RUNS or len(candidate) < MIN_RUNS:
        raise ValueError(f"D13 requires at least {MIN_RUNS} runs on each side")
    base_lo, base_hi = median_ci(baseline)
    cand_lo, cand_hi = median_ci(candidate)
    ci_slower = cand_lo > base_hi
    p_value = mannwhitney_greater(candidate, baseline)
    mw_slower = p_value < ALPHA
    return {
        "baseline_median": median(baseline),
        "candidate_median": median(candidate),
        "baseline_ci": [base_lo, base_hi],
        "candidate_ci": [cand_lo, cand_hi],
        "ci_slower": ci_slower,
        "mannwhitney_p": p_value,
        "mw_slower": mw_slower,
        "reject": ci_slower or mw_slower,
    }


def beats_floor(candidate_median: float, python_median: float) -> bool:
    """Hard floor: Rust must be strictly faster than the recorded Python median."""
    return candidate_median < python_median
