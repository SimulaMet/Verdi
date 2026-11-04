"""Tests for verdi_agents.analysis.statistics (P2 WS-3, G5 statistical sanity).

All tests are deterministic: fixed seeds and fixed synthetic data only.
No LLM, no network, stdlib only.
"""

import math
import random
import statistics

import pytest

from verdi_agents.analysis.statistics import (
    PairedResult,
    bootstrap_ci,
    null_control_passes,
    paired_effect,
)

# ---------------------------------------------------------------------------
# bootstrap_ci
# ---------------------------------------------------------------------------


def test_bootstrap_ci_is_deterministic_for_same_seed():
    values = [1.0, 2.0, 2.5, 3.0, 4.0, 4.5, 5.0]
    first = bootstrap_ci(values, seed=7)
    second = bootstrap_ci(values, seed=7)
    assert first == second


def test_bootstrap_ci_different_seed_may_differ():
    values = [1.0, 2.0, 2.5, 3.0, 4.0, 4.5, 5.0, 5.5, 6.0, 7.0]
    a = bootstrap_ci(values, seed=1, n_boot=500)
    b = bootstrap_ci(values, seed=2, n_boot=500)
    # Not a hard guarantee, but with this sample and seed pair they differ.
    assert a != b


def test_bootstrap_ci_percentiles_match_statistics_quantiles():
    """P2 review F4: the CI endpoints must match the stdlib
    ``statistics.quantiles(means, n=200, method="inclusive")`` convention
    to 1 ulp (abs diff <= 1e-12 * max(1, |value|)).

    The bootstrap means are reproduced here by re-running the documented
    resampling (same ``random.Random(seed)`` stream, same inputs) and are
    passed to ``statistics.quantiles`` with ``n=200``; the endpoints sit
    at indices ``round(alpha/2 * 200) - 1`` and
    ``round((1 - alpha/2) * 200) - 1``.
    """
    values = [1.0, 2.0, 2.5, 3.0, 4.0, 4.5, 5.0, 5.5, 6.0, 7.0]
    for seed, ci in ((0, 0.95), (7, 0.90), (42, 0.99)):
        data = [float(v) for v in values]
        n = len(data)
        n_boot = 500
        rng = random.Random(seed)
        means = sorted(
            statistics.fmean(rng.choices(data, k=n)) for _ in range(n_boot)
        )
        low, high = bootstrap_ci(values, ci=ci, n_boot=n_boot, seed=seed)
        qs = statistics.quantiles(means, n=200, method="inclusive")
        alpha = 1.0 - ci
        expected_low = qs[round(alpha / 2.0 * 200) - 1]
        expected_high = qs[round((1.0 - alpha / 2.0) * 200) - 1]
        for name, got, want in (
            ("low", low, expected_low),
            ("high", high, expected_high),
        ):
            assert abs(got - want) <= 1e-12 * max(1.0, abs(want)), (
                f"bootstrap_ci({ci=}, {seed=}) {name} endpoint {got} "
                f"differs from statistics.quantiles {want}"
            )


def test_bootstrap_ci_symmetric_sample_contains_center():
    # Symmetric around 5.0; the bootstrap mean CI must contain 5.0.
    values = [2.0, 3.0, 4.0, 5.0, 5.0, 6.0, 7.0, 8.0]
    lo, hi = bootstrap_ci(values, seed=0)
    assert lo <= 5.0 <= hi
    assert lo < hi


# ---------------------------------------------------------------------------
# paired_effect
# ---------------------------------------------------------------------------


def test_paired_effect_equal_lists_gives_zero_estimate_and_no_ci():
    vals = [10.0, 20.0, 30.0, 40.0, 50.0]
    result = paired_effect(vals, vals, seed=0)
    assert isinstance(result, PairedResult)
    assert result.estimate == 0.0
    assert result.n_pairs == 5
    assert result.sufficient is False
    assert result.ci_low is None
    assert result.ci_high is None


def test_paired_effect_12_pairs_is_sufficient_with_ci():
    rng = random.Random(42)
    base = [rng.random() * 10.0 for _ in range(12)]
    result = paired_effect(base, base, seed=0)
    assert result.estimate == 0.0
    assert result.n_pairs == 12
    assert result.sufficient is True
    assert result.ci_low is not None
    assert result.ci_high is not None
    assert result.ci_low <= result.estimate <= result.ci_high


def test_paired_effect_constant_shift():
    rng = random.Random(11)
    base = [rng.random() * 5.0 for _ in range(15)]
    treatment = [v + 3.0 for v in base]
    result = paired_effect(treatment, base, seed=0)
    assert result.estimate == pytest.approx(3.0)
    assert result.sufficient is True
    assert result.ci_low is not None and result.ci_high is not None
    assert result.ci_low <= 3.0 <= result.ci_high


# ---------------------------------------------------------------------------
# null_control_passes
# ---------------------------------------------------------------------------


def test_null_control_passes_on_zero_differences():
    zeros = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    assert null_control_passes(zeros, seed=0) is True


def test_null_control_fails_on_strong_shift():
    shifted = [10.0 + i * 0.1 for i in range(10)]
    assert null_control_passes(shifted, seed=0) is False


# ---------------------------------------------------------------------------
# error cases
# ---------------------------------------------------------------------------


def test_paired_effect_length_mismatch_raises():
    with pytest.raises(ValueError, match="length mismatch"):
        paired_effect([1.0, 2.0, 3.0], [1.0, 2.0])


def test_bootstrap_ci_too_few_values_raises():
    with pytest.raises(ValueError):
        bootstrap_ci([1.0])
    with pytest.raises(ValueError):
        bootstrap_ci([])


def test_paired_effect_too_few_pairs_raises():
    with pytest.raises(ValueError):
        paired_effect([1.0], [1.0])


def test_bad_ci_raises():
    values = [1.0, 2.0, 3.0]
    for bad in (0.0, 1.0, -0.5, 1.5):
        with pytest.raises(ValueError):
            bootstrap_ci(values, ci=bad)
        with pytest.raises(ValueError):
            paired_effect([1.0, 2.0], [1.0, 2.0], ci=bad)
        with pytest.raises(ValueError):
            null_control_passes(values, ci=bad)


# ---------------------------------------------------------------------------
# coverage sanity (documented as a sanity check, NOT a coverage guarantee)
# ---------------------------------------------------------------------------


def test_bootstrap_coverage_sanity_true_shift_one():
    """Sanity check, not a guarantee: with seed=0 and a finite bootstrap
    (n_boot=2000), the fraction of 200-replication paired-effect CIs that
    contain the true shift of 1.0 should be >= 0.85.

    Data generator: one deterministic stream (random.Random(20261009));
    replication i draws 15 base values and reuses the same 15 noise values
    on both arms, so each pair's difference is exactly 1.0 + noise and the
    mean paired difference has true value 1.0. This is a design sanity
    check on the resampling code path; it does not certify nominal 95%
    coverage.
    """
    gen = random.Random(20261009)
    contained = 0
    reps = 200
    n_pairs = 15
    true_shift = 1.0
    for _ in range(reps):
        base = [gen.random() * 10.0 for _ in range(n_pairs)]
        noise = [gen.gauss(0.0, 1.0) for _ in range(n_pairs)]
        placebo = [b + e for b, e in zip(base, noise, strict=True)]
        treatment = [b + e + true_shift for b, e in zip(base, noise, strict=True)]
        result = paired_effect(treatment, placebo, seed=0)
        assert result.sufficient is True
        assert result.ci_low is not None and result.ci_high is not None
        if result.ci_low <= true_shift <= result.ci_high:
            contained += 1
    frac = contained / reps
    assert frac >= 0.85, f"bootstrap CI coverage sanity failed: {frac:.2f} < 0.85"


def test_module_imports_are_stdlib_only():
    """Guard ADR-009: WS-3 must not import numpy/scipy or verdi internals."""
    import verdi_agents.analysis.statistics as mod

    for value in vars(mod).values():
        module = str(getattr(value, "__module__", ""))
        assert not module.startswith("numpy"), "numpy referenced"
        assert not module.startswith("scipy"), "scipy referenced"
    # The module's own import statements are stdlib-only (ADR-009).
    import inspect

    source = inspect.getsource(mod)
    assert "import numpy" not in source
    assert "import scipy" not in source
    assert "verdi_agents.experiments" not in source
    assert math.isfinite(bootstrap_ci([1.0, 2.0], seed=0)[0])
