"""Statistics used by metrics and release gates. Pure functions, standard library only."""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass


def zero_failure_upper_bound(n: int, confidence: float = 0.95) -> float:
    """Exact one-sided upper bound on a failure rate after n clean independent episodes.

    With n = 300 this is about 0.994%; about 2995 episodes are needed for 0.1%.
    It assumes independent, representative episodes and does not apply to
    correlated or selected cases.
    """
    if n <= 0:
        return 1.0
    return 1 - (1 - confidence) ** (1 / n)


def episodes_for_bound(target_rate: float, confidence: float = 0.95) -> int:
    return math.ceil(math.log(1 - confidence) / math.log(1 - target_rate))


def wilson(successes: float, n: float, z: float = 1.959964) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 1.0
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def percentile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return float(ordered[lo])
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


@dataclass(frozen=True)
class PairedComparison:
    families: int
    candidate_mean: float
    baseline_mean: float
    difference: float
    ci_low: float
    ci_high: float
    one_sided_lower_95: float
    method: str
    resamples: int
    seed: int


def paired_cluster_bootstrap(
    candidate: dict[str, list[float]], baseline: dict[str, list[float]], resamples: int = 4000, seed: int = 20261028
) -> PairedComparison:
    """Compare per-family means with equal family weights, resampling families.

    Trials within a family are averaged first, so repeated copies of one
    scenario never count as independent coverage.
    """
    families = sorted(set(candidate) & set(baseline))
    if not families:
        raise ValueError("no matched scenario families")
    cand = [sum(candidate[f]) / len(candidate[f]) for f in families]
    base = [sum(baseline[f]) / len(baseline[f]) for f in families]
    diffs = [c - b for c, b in zip(cand, base, strict=True)]
    n = len(diffs)
    rng = random.Random(seed)
    stats = []
    for _ in range(resamples):
        total = 0.0
        for _ in range(n):
            total += diffs[rng.randrange(n)]
        stats.append(total / n)
    stats.sort()
    mean_diff = sum(diffs) / n
    return PairedComparison(
        families=n,
        candidate_mean=sum(cand) / n,
        baseline_mean=sum(base) / n,
        difference=mean_diff,
        ci_low=percentile(stats, 0.025) or 0.0,
        ci_high=percentile(stats, 0.975) or 0.0,
        one_sided_lower_95=percentile(stats, 0.05) or 0.0,
        method="paired percentile bootstrap over scenario families, equal family weights, trials averaged within family",
        resamples=resamples,
        seed=seed,
    )


def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> float | None:
    if len(a) != len(b) or not a:
        return None
    n = len(a)
    labels = set(a) | set(b)
    observed = sum(1 for x, y in zip(a, b, strict=True) if x == y) / n
    expected = sum((a.count(lab) / n) * (b.count(lab) / n) for lab in labels)
    if expected == 1:
        return 1.0
    return (observed - expected) / (1 - expected)
