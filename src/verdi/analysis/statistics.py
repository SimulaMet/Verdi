"""G5 descriptive estimation helpers (P2 WS-3, frozen per docs/P2_CONTRACT.md).

These are **descriptive** estimation helpers for pre-registered estimands
(docs/PREREGISTRATION.md v1.0):

* :func:`bootstrap_ci` — nonparametric percentile bootstrap confidence
  interval of the MEAN, deterministic for a given ``seed``.
* :func:`paired_effect` — paired-difference estimate with sufficiency flag
  (``n_pairs >= 10`` per preregistration; below that the CI is withheld).
* :func:`null_control_passes` — negative-control check: does the bootstrap
  CI of the mean contain zero?

Statistical honesty rules (binding):

* Per PREREGISTRATION.md ("No causal label without randomization"), these
  helpers NEVER label effects causal. They report a point estimate and an
  uncertainty interval for whatever differences/quantities are passed in;
  interpretation of the design belongs to the caller.
* Single-run numbers produced here must never be presented as population
  estimates.
* The bootstrap is seeded (``random.Random(seed)``) so results are
  reproducible; it is a finite-resampling estimate, not a guarantee of true
  coverage.

Implementation is stdlib only (``random``, ``math``, ``statistics``) — no
numpy/scipy, no third-party imports, per ADR-009.
"""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = ["PairedResult", "bootstrap_ci", "paired_effect", "null_control_passes"]


def _validate_ci(ci: float) -> None:
    """Raise ``ValueError`` unless ``ci`` is a float in the open interval (0, 1)."""
    # bool is an int subclass and is rejected too.
    if isinstance(ci, bool) or not (isinstance(ci, (float, int))):
        raise ValueError(f"ci must be a number in (0, 1), got {ci!r}")
    if not 0.0 < float(ci) < 1.0:
        raise ValueError(f"ci must be in the open interval (0, 1), got {ci!r}")


def bootstrap_ci(
    values: Sequence[float],
    *,
    ci: float = 0.95,
    n_boot: int = 2000,
    seed: int = 0,
) -> tuple[float, float]:
    """Nonparametric percentile bootstrap CI of the sample mean.

    Resamples ``len(values)`` observations with replacement ``n_boot`` times
    using ``random.Random(seed)`` and returns the percentile interval
    ``((1 - ci) / 2, 1 - (1 - ci) / 2)`` over the bootstrap means.

    Percentile convention (P2 review F4): the bounds are the *inclusive*
    linear-interpolation percentiles of the sorted bootstrap means, i.e.
    exactly the values ``statistics.quantiles(means, n=200,
    method="inclusive")`` would return at indices
    ``round(alpha/2 * 200) - 1`` and ``round((1 - alpha/2) * 200) - 1``
    (``alpha = 1 - ci``). For the usual levels (ci = 0.90/0.95/0.99) the
    product ``alpha/2 * 200`` is an exact integer, so the endpoints match
    the stdlib ``quantiles`` values to within floating-point rounding.
    The equivalent local linear-interpolation implementation is kept
    (rather than switching to ``statistics.quantiles``) because it is
    stdlib-only and handles any ``n_boot`` / ``ci`` without re-sorting
    into 200 buckets; an equivalence test pins it to
    ``statistics.quantiles`` to 1 ulp in
    ``tests/analysis/test_statistics.py``.

    Args:
        values: Observed values; must contain at least two values.
        ci: Confidence level in the open interval (0, 1).
        n_boot: Number of bootstrap replicates (>= 2).
        seed: RNG seed; identical seed and inputs give identical output.

    Returns:
        ``(ci_low, ci_high)`` floats (inclusive bounds).

    Raises:
        ValueError: If ``len(values) < 2``, ``ci`` is outside (0, 1), or
            ``n_boot < 2``.
    """
    _validate_ci(ci)
    if len(values) < 2:
        raise ValueError(f"bootstrap_ci requires at least 2 values, got {len(values)}")
    if not isinstance(n_boot, int) or isinstance(n_boot, bool) or n_boot < 2:
        raise ValueError(f"n_boot must be an int >= 2, got {n_boot!r}")
    data = [float(v) for v in values]
    n = len(data)
    rng = random.Random(seed)
    alpha = 1.0 - ci
    means: list[float] = []
    means_append = means.append
    for _ in range(n_boot):
        means_append(statistics.fmean(rng.choices(data, k=n)))
    means.sort()
    low_q = alpha / 2.0
    high_q = 1.0 - low_q

    def percentile(sorted_vals: list[float], q: float) -> float:
        # Linear-interpolation percentile (same convention as statistics
        # quantiles with method='inclusive'), stdlib-only.
        if len(sorted_vals) == 1:
            return sorted_vals[0]
        pos = q * (len(sorted_vals) - 1)
        lo = math.floor(pos)
        hi = math.ceil(pos)
        if lo == hi:
            return sorted_vals[int(pos)]
        frac = pos - lo
        return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac

    return (percentile(means, low_q), percentile(means, high_q))


@dataclass(frozen=True)
class PairedResult:
    """Paired-difference estimate with sufficiency flag.

    ``ci_low``/``ci_high`` are ``None`` when ``sufficient`` is False
    (``n_pairs < 10`` per preregistration) — the point estimate is still
    reported, flagged as an insufficient sample.
    """

    estimate: float
    ci_low: float | None
    ci_high: float | None
    n_pairs: int
    sufficient: bool


def paired_effect(
    treatment: Sequence[float],
    placebo: Sequence[float],
    *,
    ci: float = 0.95,
    n_boot: int = 2000,
    seed: int = 0,
) -> PairedResult:
    """Estimate a paired treatment-vs-placebo effect (descriptive, not causal).

    The paired differences are ``treatment[i] - placebo[i]`` for matched
    pairs (matched snapshots + matched task tape per PREREGISTRATION.md).
    ``estimate`` is the mean paired difference. When at least 10 pairs are
    available (``sufficient``), a percentile bootstrap CI of the mean
    difference is reported using the same ``seed``; otherwise the CI is
    withheld (``None``) and the result must be flagged
    ``insufficient_sample`` by any consumer.

    Args:
        treatment: Treatment-arm observed values, in pair order.
        placebo: Placebo-arm observed values, in pair order.
        ci: Confidence level in the open interval (0, 1).
        n_boot: Bootstrap replicates (>= 2).
        seed: RNG seed for the bootstrap.

    Returns:
        :class:`PairedResult`.

    Raises:
        ValueError: If arm lengths mismatch, there are fewer than 2 pairs,
            or ``ci``/``n_boot`` are invalid.
    """
    _validate_ci(ci)
    n_t = len(treatment)
    n_p = len(placebo)
    if n_t != n_p:
        raise ValueError(f"length mismatch: {n_t} treatment vs {n_p} placebo values")
    if n_t < 2:
        raise ValueError(f"paired_effect requires at least 2 pairs, got {n_t}")
    differences = [float(t) - float(p) for t, p in zip(treatment, placebo, strict=True)]
    estimate = statistics.fmean(differences)
    sufficient = n_t >= 10
    if sufficient:
        ci_low, ci_high = bootstrap_ci(
            differences, ci=ci, n_boot=n_boot, seed=seed
        )
    else:
        ci_low = ci_high = None
    return PairedResult(
        estimate=estimate,
        ci_low=ci_low,
        ci_high=ci_high,
        n_pairs=n_t,
        sufficient=sufficient,
    )


def null_control_passes(
    values: Sequence[float],
    *,
    ci: float = 0.95,
    n_boot: int = 2000,
    seed: int = 0,
) -> bool:
    """Negative-control check: True iff the bootstrap CI of the mean contains 0.

    Used for null-drift / no-intervention arms per PREREGISTRATION.md
    ("Negative controls: null drift ... probe deltas CI contains 0 on a
    no-intervention arm").
    """
    _validate_ci(ci)
    ci_low, ci_high = bootstrap_ci(values, ci=ci, n_boot=n_boot, seed=seed)
    return ci_low <= 0.0 <= ci_high
