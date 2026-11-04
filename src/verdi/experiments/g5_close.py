"""ADR-014 G5-close (Qwen-only): sealed sample, bias/leakage audits,
identification diagnostics, and the byte-stable G5-close report.

Implements the FROZEN contract in
``docs/ADR-014-G5-CLOSE-QWEN-ONLY-PROPOSAL.md`` (sections 2, 4, 5, 6):

* :func:`build_sealed_sample` / :func:`build_sample_manifest` — the 504-row
  sealed judge sample as a pure, byte-stable function of the run root
  (pair_0 all 81 rows + pairs 1..9 suffix rows only, 47 each).
* :func:`audit_self_report` (B1), :func:`audit_leakage` (B2),
  :func:`audit_quarantine_symmetry` (B3), :func:`audit_prompt_invariance`
  (B4) — offline, pure audits over the sealed tapes; each returns a dict
  with ``passed: bool`` and detail. No model calls.
* :func:`identification_diagnostics` — leave-one-pair-out spread over the
  six PREREG contrasts and the seeded (seed 0) permutation null baseline,
  both offline diagnostics. NO causal/randomization claim (see
  ``NOT_CLAIMED`` and the ADR-014 §5 framing).
* :func:`build_g5_close_report` / :func:`causal_word_guard` /
  :func:`write_g5_close_report` — the single report with frozen sections
  ``judge, bias_audits, identification, context, limitations,
  deviations_from_contract`` and the frozen causal-word guard.

Framing (ADR-014 §3, carried verbatim into the report's ``judge.framing``):
the judge is SHOWN the engine outcome; the measured quantity is
judge-to-engine SELF-CONSISTENCY on G5 paired data, not an independent
compliance verdict. Blinding is satisfied by the deterministic code judge
only. Same-family self-judgment is disclosed. Advisor-only.

Contract deviation note (recorded per worker instructions, ADR-014 is NOT
edited): the task brief asked for ``build_sample_manifest`` to return the
five fields ``{n_rows, pairs, rows, manifest_sha256}``; this module adds a
sixth field ``run_root`` (the run-root path string) because the ADR-014
§2 seal wording requires the manifest to record the run root it is a pure
function of. The four contract-mandated fields are present with the
contract-mandated semantics; ``manifest_sha256`` hashes the canonical
serialization of ``rows`` exactly as specified.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import statistics
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from verdi.audit.judge_live import _assert_no_key_material, wilson_ci_95

#: ADR-014 §2: pair_0 contributes ALL rows; other pairs suffix-only.
FULL_CONTEXT_PAIR = 0
#: ADR-013: R_p spans lifecycle ticks 13..29 (suffix boundary).
SUFFIX_TICK_MIN = 13
#: ADR-014 §2: the sealed sample size (81 + 9*47).
EXPECTED_SAMPLE_ROWS = 504
N_PAIRS = 10

#: ADR-014 §4 B2: post-branch governance ground-truth strings the model was
#: never told (pre-sealing containment check).
ADJUDICATION_GROUND_TRUTH_STRINGS = ("appeal_upheld", "rights_restored", "appeal_dismissed")

#: ADR-014 §5: the deliberate not-claimed block, stated verbatim in the
#: report (frozen).
NOT_CLAIMED: tuple[str, ...] = (
    "causal or average-treatment-effect statement",
    "external validity beyond the pilot scenario",
    "population generalization",
)

#: ADR-014 §5: the frozen limitations block (report carries these verbatim).
ADR5_OVERSTATEMENT_LIMITATION = (
    "ADR-014 §5's 'evidence the contrast is tied to the manipulation' "
    "phrasing is deliberately not inherited by the report; the permutation "
    "null is a diagnostic of the sign-symmetry of the paired-estimate "
    "machinery"
)

FROZEN_LIMITATIONS: tuple[str, ...] = (
    "paraphrase-level leak detection out of scope: the leakage audit is "
    "verbatim containment only",
    "cross-family judge deferred (operator, ADR-012 A1-1); the model judge "
    "is the same Qwen family as the target and its output is advisor-only",
    "no randomization: deterministic single-manipulation design; the "
    "permutation null is a diagnostic, not randomization",
    "single pilot scenario",
    ADR5_OVERSTATEMENT_LIMITATION,
)

#: Frozen, verbatim record of the contract deviations this module makes.
#: The ADR-014 body is NOT edited; these are handled here, in the report's
#: top-level ``deviations_from_contract`` block (reviewer MED-2 / F1).
DEVIATIONS_FROM_CONTRACT: tuple[str, ...] = (
    "run_root manifest field omitted from the report: the sealed manifest "
    "carries an advisory run_root path field, deliberately excluded from "
    "the report so a filesystem path can never interact with the frozen "
    "causal-word guard",
    "no response_format sent: the live smoke (2026-10-10) confirmed the "
    "endpoint returns clean JSON at temperature 0.1 / max_tokens 2048 "
    "without response_format, so the judge uses a JSON-only system prompt "
    "plus a tolerant parser instead",
    "expected_rows parameter added to run_g5_judge (default 504 = "
    "production behavior) so synthetic mini-run tests can run offline "
    "without weakening the frozen 504-row assert",
    "per-arm agreement substituted by row-position agreement: the sealed "
    "sample rows are the SHARED per-pair tape replayed into both arms, so "
    "per-arm judge agreement is not separable from the sample; the report "
    "reports prefix/suffix (row-position) agreement instead",
    "code-judge comparison not possible on the G5 run root: no code-judge "
    "artifact exists in the G5 run root; the deterministic code judge in "
    "this program runs on the L1 all-81 sample (ADR-012)",
    "B1 implemented as structural parse/row consistency: the policy parser "
    "forces task_id/actor_id, so the 810/810 concordance measures "
    "plumbing consistency rather than self-report-vs-behavior; the ADR "
    "'declared values within bounds' limb is unimplemented "
    "(bounds_check_implemented=false)",
)

#: B1 scope string (reviewer F2): states exactly what B1's task_id /
#: action_kind clauses actually measure, so the report cannot be misread
#: as the full ADR B1.
B1_SCOPE = (
    "structural parse/row consistency only: parse_and_validate_action "
    "forces task_id/actor_id, so concordance measures plumbing "
    "consistency, not self-report-vs-behavior; the 'declared values within "
    "bounds' limb of ADR B1 is unimplemented (bounds_check_implemented "
    "is false)"
)

# Frozen causal-word guard (ADR-014 §5): the word "causal" may appear at
# most CAUSAL_WORD_MAX_OCCURRENCES times in json.dumps(report), and every
# occurrence must be part of a NEGATED framing — its surrounding
# CAUSAL_WORD_CONTEXT_WINDOW chars must contain one of these tokens.
CAUSAL_WORD_MAX_OCCURRENCES = 3
CAUSAL_WORD_CONTEXT_WINDOW = 40
CAUSAL_WORD_ALLOWED_CONTEXTS: tuple[str, ...] = (
    "no causal",
    "not causal",
    "causal or",
    "causal-word",
    "causal word",
    "NOT claimed",
    "not claiming",
    "without causal",
)

#: ADR-014 §5: estimand statement carried into the report verbatim.
ESTIMAND_STATEMENT = (
    "within-pair contrast T-P on the six PREREG v1.0 estimands: the effect "
    "of the adjudication manipulation on ledger-derived outcomes holding "
    "the entire model-generated behavior stream fixed"
)

ESTIMAND_ASSUMPTIONS: tuple[str, ...] = (
    "same-code replay of one shared per-pair response tape into both arms "
    "(ADR-013 Addendum A1 same-code invariants a-d)",
    "scaffold invariance: identical folded final state hash across arms "
    "and L1",
    "single manipulation: only the scripted adjudication at the sealed "
    "branch differs by construction",
)

_PERMUTATION_N_PERMS = 200
_PERMUTATION_SEED = 0
_SYMMETRY_TOL = 1e-12
_PROMPT_LEAK_FRAG_LEN = 64
_ACTION_TYPE_RE = re.compile(r"action_type=([a-z_]+)")


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"required file not found: {path}")
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def pair_dirs(run_root: str | Path) -> list[Path]:
    """Sorted ``pair_<k>`` directories of a g5_pairs run root."""
    run_root = Path(run_root)
    dirs = sorted(run_root.glob("pair_*"), key=lambda p: int(p.name.split("_")[1]))
    if not dirs:
        raise FileNotFoundError(f"no pair_* directories under {run_root}")
    return dirs


def pair_index(path: Path) -> int:
    return int(path.name.split("_")[1])


def pair_tape_rows(run_root: Path, pdir: Path) -> list[dict[str, Any]]:
    """One pair's sealed tape rows in seq order."""
    rows = _load_jsonl(pdir / "live_tape.jsonl")
    rows.sort(key=lambda r: int(r["seq"]))
    return rows


# --------------------------------------------------------------------------- #
# Sealed sample (ADR-014 §2): pure, byte-stable function of the run root
# --------------------------------------------------------------------------- #


def build_sealed_sample(run_root: str | Path) -> list[dict[str, Any]]:
    """The frozen sealed judge sample (ADR-014 §2).

    pair_0: ALL rows (81 in the production run); pairs 1..9: suffix rows
    only (``lifecycle_tick >= 13``, 47 each). Selection is
    positional-free: rows are identified by ``(pair_index, seq, actor_id,
    lifecycle_tick, task_id)`` in seq order within the pair tape. Each
    sample row records an ``arm_relevant`` prefix/suffix flag and
    ``row_sha256`` = sha256 of ``json.dumps`` (sort_keys=True) of
    ``{prompt, response_raw, actor_id, lifecycle_tick, task_id}``.

    Pure function: recomputation on the same run root is byte-identical
    (tested).
    """
    sample: list[dict[str, Any]] = []
    for pdir in pair_dirs(run_root):
        pidx = pair_index(pdir)
        for row in pair_tape_rows(run_root, pdir):
            tick = int(row["lifecycle_tick"])
            if pidx != FULL_CONTEXT_PAIR and tick < SUFFIX_TICK_MIN:
                continue
            sealed = {
                "prompt": row.get("prompt") or "",
                "response_raw": row.get("response_raw") or "",
                "actor_id": row.get("actor_id"),
                "lifecycle_tick": tick,
                "task_id": row.get("task_id"),
            }
            sample.append(
                {
                    "pair_index": pidx,
                    "seq": int(row["seq"]),
                    "actor_id": row.get("actor_id"),
                    "lifecycle_tick": tick,
                    "task_id": row.get("task_id"),
                    "arm_relevant": "suffix" if tick >= SUFFIX_TICK_MIN else "prefix",
                    "row_sha256": _sha256_hex(json.dumps(sealed, sort_keys=True)),
                }
            )
    return sample


def build_sample_manifest(run_root: str | Path) -> dict[str, Any]:
    """Sealed sample manifest: pure function of the run root (ADR-014 §2).

    Returns ``{run_root, n_rows, pairs, rows, manifest_sha256}`` where
    ``rows`` is the sealed sample (see :func:`build_sealed_sample`) and
    ``manifest_sha256`` is the sha256 of the canonical serialization
    (``json.dumps(rows, sort_keys=True)``) of the row list. Recomputing on
    the same run root is byte-identical (tested).
    """
    rows = build_sealed_sample(run_root)
    return {
        "run_root": str(run_root),
        "n_rows": len(rows),
        "pairs": [pair_index(d) for d in pair_dirs(run_root)],
        "rows": rows,
        "manifest_sha256": _sha256_hex(json.dumps(rows, sort_keys=True)),
    }


# --------------------------------------------------------------------------- #
# Bias / leakage audits (ADR-014 §4): offline, pure, no model calls
# --------------------------------------------------------------------------- #


def _declared_action_kind(prompt: str) -> str | None:
    m = _ACTION_TYPE_RE.search(prompt or "")
    return m.group(1) if m else None


def audit_self_report(run_root: str | Path) -> dict[str, Any]:
    """B1 — self-report vs observed behavior (ADR-014 §4).

    For every row of every pair tape, cross-check ``parsed_action``
    against the task declared in the prompt: ``parsed_action.task_id``
    must equal the tape row's ``task_id`` and ``parsed_action.action_kind``
    must equal the ``action_type=<kind>`` token declared in the prompt.
    No self-report value is treated as ground truth. Reports per-pair and
    overall concordance counts plus the divergence list.

    SCOPE (reviewer F2): the task_id / action_kind clauses are STRUCTURAL —
    ``parse_and_validate_action`` already forces ``task_id`` / ``actor_id``
    — so concordance measures plumbing consistency, not
    self-report-vs-behavior. The returned dict carries the frozen
    ``scope`` string (``B1_SCOPE``) and ``bounds_check_implemented:
    False`` so the report cannot be misread as the full ADR B1.
    """
    per_pair: dict[str, Any] = {}
    divergences: list[dict[str, Any]] = []
    total = concordant = 0
    for pdir in pair_dirs(run_root):
        rows = pair_tape_rows(run_root, pdir)
        pair_concordant = 0
        for row in rows:
            total += 1
            parsed = row.get("parsed_action") or {}
            declared_kind = _declared_action_kind(row.get("prompt") or "")
            ok = (
                parsed.get("task_id") == row.get("task_id")
                and parsed.get("action_kind") == declared_kind
            )
            if ok:
                concordant += 1
                pair_concordant += 1
            else:
                divergences.append(
                    {
                        "pair_index": pair_index(pdir),
                        "seq": int(row["seq"]),
                        "actor_id": row.get("actor_id"),
                        "task_id": row.get("task_id"),
                        "declared_action_kind": declared_kind,
                        "reported_action_kind": parsed.get("action_kind"),
                        "reported_task_id": parsed.get("task_id"),
                    }
                )
        per_pair[f"pair_{pair_index(pdir)}"] = {
            "n_rows": len(rows),
            "concordant": pair_concordant,
            "divergent": len(rows) - pair_concordant,
        }
    return {
        "audit": "B1_self_report",
        "passed": not divergences,
        "n_rows": total,
        "concordant": concordant,
        "divergent": len(divergences),
        "per_pair": per_pair,
        "divergences": divergences,
        "scope": B1_SCOPE,
        "bounds_check_implemented": False,
    }


def _prompt_body_head(prompt: str, n: int = _PROMPT_LEAK_FRAG_LEN) -> str:
    """First ``n`` chars of the prompt AFTER the leading '[prompt:...]' line."""
    body = prompt.partition("\n")[2] if "\n" in (prompt or "") else (prompt or "")
    return body[:n]


def audit_leakage(run_root: str | Path) -> dict[str, Any]:
    """B2 — leakage audit (ADR-014 §4): verbatim containment only.

    (i) no ``response_raw`` contains any post-prefix adjudication
    ground-truth string (``appeal_upheld`` / ``rights_restored`` /
    ``appeal_dismissed``) — the pre-sealing check, since responses
    predate the folded adjudication;
    (ii) no key material in any row (``prompt`` + ``response_raw``),
    reusing ``judge_live._assert_no_key_material``;
    (iii) within-tape ordering leak: for every pair of rows (i < j)
    within a pair tape, the first 64 chars of row j's prompt body (after
    the leading '[prompt:...]'' token line) are not a substring of row i's
    ``response_raw``.

    Paraphrase-level leakage is OUT OF SCOPE (documented, ADR-014 §8).
    """
    gt_hits: list[dict[str, Any]] = []
    key_findings: list[str] = []
    ordering_leaks: list[dict[str, Any]] = []
    for pdir in pair_dirs(run_root):
        pidx = pair_index(pdir)
        rows = pair_tape_rows(run_root, pdir)
        heads = [
            (int(r["seq"]), _prompt_body_head(r.get("prompt") or "")) for r in rows
        ]
        for j, row in enumerate(rows):
            response = row.get("response_raw") or ""
            for term in ADJUDICATION_GROUND_TRUTH_STRINGS:
                if term in response:
                    gt_hits.append(
                        {"pair_index": pidx, "seq": int(row["seq"]), "term": term}
                    )
            try:
                _assert_no_key_material(
                    (row.get("prompt") or "") + "\n" + response,
                    f"pair_{pidx} row seq={row['seq']}",
                )
            except AssertionError as exc:
                key_findings.append(str(exc))
            # ordering leak: row j's prompt body head vs every earlier row i
            j_seq, j_head = heads[j]
            if len(j_head) >= _PROMPT_LEAK_FRAG_LEN:
                for i in range(j):
                    i_response = rows[i].get("response_raw") or ""
                    if i_response and j_head in i_response:
                        ordering_leaks.append(
                            {
                                "pair_index": pidx,
                                "earlier_seq": int(rows[i]["seq"]),
                                "later_seq": j_seq,
                                "frag_head": j_head[: _PROMPT_LEAK_FRAG_LEN],
                            }
                        )
    return {
        "audit": "B2_leakage",
        "passed": not (gt_hits or key_findings or ordering_leaks),
        "method": (
            "verbatim containment only (paraphrase-level out of scope); "
            "JSON-escaped forms (e.g. leaked text containing quotes appears "
            "as \\\" in response_raw) defeat verbatim containment \u2014 same "
            "sensitivity class as the disclosed paraphrase-only scope"
        ),
        "ground_truth_hits": gt_hits,
        "key_material_findings": key_findings,
        "ordering_leaks": ordering_leaks,
    }


def audit_quarantine_symmetry(run_root: str | Path) -> dict[str, Any]:
    """B3 — quarantine/retry bias (ADR-014 §4).

    Per-pair quarantine counts and reasons; passed iff all pairs are
    identical (all 0 in the production run root).
    """
    per_pair: dict[str, Any] = {}
    for pdir in pair_dirs(run_root):
        rows = pair_tape_rows(run_root, pdir)
        reasons = [str(r.get("quarantine_reason") or "") for r in rows if r.get("quarantined")]
        per_pair[f"pair_{pair_index(pdir)}"] = {
            "n_quarantined": len(reasons),
            "quarantine_reasons": reasons,
        }
    vector = [v["n_quarantined"] for v in per_pair.values()]
    return {
        "audit": "B3_quarantine_symmetry",
        "passed": len(set(vector)) == 1 and vector and vector[0] == 0,
        "quarantine_vector": vector,
        "per_pair": per_pair,
    }


def audit_prompt_invariance(run_root: str | Path) -> dict[str, Any]:
    """B4 — prompt invariance across pairs (ADR-014 §4).

    For suffix rows (``lifecycle_tick >= 13``), the tuple
    ``(actor_id, lifecycle_tick, task_id, prompt)`` must be identical
    across ALL pairs. Reports the number of distinct values per field
    and the passed flag.
    """
    by_key: dict[tuple[Any, Any, Any], str] = {}
    per_field: dict[str, set[Any]] = {"actor_id": set(), "lifecycle_tick": set(), "task_id": set()}
    drifts: list[dict[str, Any]] = []
    n_suffix_rows = 0
    for pdir in pair_dirs(run_root):
        pidx = pair_index(pdir)
        for row in pair_tape_rows(run_root, pdir):
            if int(row["lifecycle_tick"]) < SUFFIX_TICK_MIN:
                continue
            n_suffix_rows += 1
            per_field["actor_id"].add(row.get("actor_id"))
            per_field["lifecycle_tick"].add(int(row["lifecycle_tick"]))
            per_field["task_id"].add(row.get("task_id"))
            key = (
                row.get("actor_id"),
                int(row["lifecycle_tick"]),
                row.get("task_id"),
            )
            prompt = row.get("prompt") or ""
            if key in by_key and by_key[key] != prompt:
                drifts.append(
                    {
                        "pair_index": pidx,
                        "seq": int(row["seq"]),
                        "key": {"actor_id": key[0], "lifecycle_tick": key[1], "task_id": key[2]},
                    }
                )
            else:
                by_key.setdefault(key, prompt)
    return {
        "audit": "B4_prompt_invariance",
        "passed": not drifts,
        # MED-1: actual count of tape rows with tick >= SUFFIX_TICK_MIN
        # across all pairs (470 on the real run root), NOT a distinct-value
        # estimate.
        "n_suffix_rows": n_suffix_rows,
        "n_distinct_per_field": {k: len(v) for k, v in per_field.items()},
        "n_distinct_prompt_tuples": len(by_key),
        "drifts": drifts,
    }


# --------------------------------------------------------------------------- #
# Identification diagnostics (ADR-014 §5): offline, no model calls
# --------------------------------------------------------------------------- #


def _pair_estimates(run_root: Path) -> dict[str, tuple[dict[str, float], dict[str, float]]]:
    """Per-pair ``(treatment, placebo)`` estimate dicts from the run dicts."""
    out: dict[str, tuple[dict[str, float], dict[str, float]]] = {}
    for pdir in pair_dirs(run_root):
        t = json.loads((pdir / "treatment_run_dict.json").read_text())
        p = json.loads((pdir / "placebo_run_dict.json").read_text())
        out[f"pair_{pair_index(pdir)}"] = (
            {k: float(v) for k, v in t["estimates"].items()},
            {k: float(v) for k, v in p["estimates"].items()},
        )
    return out


def _estimand_names(estimates: Mapping[str, float]) -> list[str]:
    return sorted(estimates.keys())


def leave_one_pair_out(run_root: str | Path) -> dict[str, Any]:
    """Recompute the six T-P contrasts with each pair dropped (ADR-014 §5a).

    Per estimand: 10 leave-one-pair-out means (one per dropped pair) and
    their pstdev spread.
    """
    pairs = _pair_estimates(Path(run_root))
    names = _estimand_names(next(iter(pairs.values()))[0])
    deltas: dict[str, list[float]] = {n: [] for n in names}
    for pdir in pair_dirs(Path(run_root)):
        t, p = pairs[f"pair_{pair_index(pdir)}"]
        for n in names:
            deltas[n].append(t[n] - p[n])
    per_pair: dict[str, Any] = {}
    for n in names:
        values: list[float] = []
        for dropped in range(len(deltas[n])):
            kept = [v for i, v in enumerate(deltas[n]) if i != dropped]
            values.append(sum(kept) / len(kept))
        per_pair[n] = {
            "n_values": len(values),
            "leave_one_out_means": values,
            "pstdev_spread": statistics.pstdev(values),
        }
    return {
        "diagnostic": "leave_one_pair_out",
        "n_pairs": len(deltas[names[0]]),
        "per_estimand": per_pair,
    }


def permutation_null(run_root: str | Path) -> dict[str, Any]:
    """Seeded permutation null baseline (ADR-014 §5b; diagnostic, NOT randomization).

    Exact math: for each of the 10 pairs and each of the six estimands the
    within-pair delta d_p is fixed. Each of ``N_PERMS = 200`` permutations
    draws, with a single fixed ``random.Random(SEED=0)`` sequence, an
    independent random sign s_p in {+1, -1} per pair and reports the mean
    of {s_p * d_p}. For any estimand whose per-pair delta MAGNITUDES are
    all equal (true in the production run: e.g. false_punishment_rate
    deltas are all +1.0), any sign assignment with exactly half the signs
    negative yields a mean of EXACTLY 0.0. The fixed seed 0 makes the 200
    draws DETERMINISTIC, so whether 0.0 appears is a property of the
    recorded draws, not of probability: in the recorded sequence 57 of the
    200 draws have exactly 5 negative signs, so 0.0 appears in exactly
    those 57 cases (it is the exactly-half-negative branch, NOT the
    min == -max symmetry branch, that passes; e.g. min = -1.0, max =
    +0.8 for false_punishment_rate, which is not symmetric). The required
    assertion: for every estimand, either (a) all magnitudes are equal
    and at least one permutation has exactly half-negative signs (=> its
    mean is exactly 0.0), or (b) the set of permutation means is
    symmetric around 0 (min == -max within 1e-12); the fallback when
    magnitudes differ is that the distribution contains a 0.0 mean. The
    per-estimand fields record which branch held.
    """
    pairs = _pair_estimates(Path(run_root))
    names = _estimand_names(next(iter(pairs.values()))[0])
    rng = random.Random(_PERMUTATION_SEED)
    per_estimand: dict[str, Any] = {}
    for n in names:
        deltas: list[float] = []
        for pdir in pair_dirs(Path(run_root)):
            t, p = pairs[f"pair_{pair_index(pdir)}"]
            deltas.append(t[n] - p[n])
        n_pairs = len(deltas)
        mags = [abs(d) for d in deltas]
        mags_equal = all(abs(m - mags[0]) <= _SYMMETRY_TOL for m in mags)
        half = n_pairs // 2
        means: list[float] = []
        n_half_negative = 0
        zero_count = 0
        for _ in range(_PERMUTATION_N_PERMS):
            signs = [1 if rng.random() < 0.5 else -1 for _ in range(n_pairs)]
            n_neg = sum(1 for s in signs if s < 0)
            if n_neg == half:
                n_half_negative += 1
            m = sum(s * d for s, d in zip(signs, deltas, strict=True)) / n_pairs
            means.append(m)
            if m == 0.0:
                zero_count += 1
        mn, mx = min(means), max(means)
        symmetric = abs(mn + mx) <= _SYMMETRY_TOL
        assert (
            (mags_equal and n_half_negative >= 1) or symmetric or zero_count >= 1
        ), (
            f"permutation null null-contains-zero assertion failed for {n}: "
            f"mags_equal={mags_equal} half_negative_perms={n_half_negative} "
            f"symmetric={symmetric} zeros={zero_count}"
        )
        per_estimand[n] = {
            "n_perms": _PERMUTATION_N_PERMS,
            "min": mn,
            "max": mx,
            "fraction_of_means_exactly_zero": zero_count / _PERMUTATION_N_PERMS,
            "magnitudes_equal": mags_equal,
            "symmetric_around_zero": symmetric,
            "contains_zero_mean": zero_count >= 1,
        }
    return {
        "diagnostic": "permutation_null",
        "seed": _PERMUTATION_SEED,
        "n_perms": _PERMUTATION_N_PERMS,
        "is_randomization": False,
        "note": (
            "design-robustness null baseline: random T/P sign flips over the "
            "10 pairs MUST produce a contrast distribution containing 0; "
            "this is NOT a randomization claim (ADR-014 §5)"
        ),
        "per_estimand": per_estimand,
    }


def identification_diagnostics(run_root: str | Path) -> dict[str, Any]:
    """The ADR-014 §5 identification block (offline diagnostics only)."""
    return {
        "estimand_statement": ESTIMAND_STATEMENT,
        "assumptions": list(ESTIMAND_ASSUMPTIONS),
        "not_claimed": list(NOT_CLAIMED),
        "diagnostics": {
            "leave_one_pair_out": leave_one_pair_out(run_root),
            "permutation_null": permutation_null(run_root),
        },
    }


# --------------------------------------------------------------------------- #
# Report (ADR-014 §6): frozen sections, causal-word guard, byte-stable write
# --------------------------------------------------------------------------- #


def build_g5_close_report(
    sample_manifest: Mapping[str, Any],
    judge_section: Mapping[str, Any],
    audits: Mapping[str, Any],
    identification: Mapping[str, Any],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """The single G5-close report (pure function of its inputs).

    Sections, exactly (ADR-014 §6): ``judge``, ``bias_audits``,
    ``identification``, ``context``, ``limitations``. Double-build
    byte-stability is asserted by the caller/tests (the report is a pure
    function of its inputs; :func:`write_g5_close_report` asserts it
    before writing).
    """
    id_block = dict(identification)
    # Guard the NOT-claimed block verbatim (frozen).
    assert tuple(id_block.get("not_claimed") or ()) == NOT_CLAIMED, (
        "identification.not_claimed must carry the frozen ADR-014 block verbatim"
    )
    report: dict[str, Any] = {
        "judge": dict(judge_section),
        "bias_audits": dict(audits),
        "identification": id_block,
        "context": dict(context),
        "limitations": list(FROZEN_LIMITATIONS),
        # MED-2 / reviewer F1: frozen, verbatim record of contract gaps.
        "deviations_from_contract": list(DEVIATIONS_FROM_CONTRACT),
    }
    report.setdefault(
        "sample_manifest",
        {
            # NOTE: the manifest's advisory ``run_root`` path string is
            # deliberately OMITTED here (and below in write) so filesystem
            # paths can never interact with the causal-word guard on the
            # report blob. The run root is recovered from the sealed
            # manifest artifact itself.
            "n_rows": sample_manifest.get("n_rows"),
            "manifest_sha256": sample_manifest.get("manifest_sha256"),
        },
    )
    # The guard polices the report's OWN wording; verbatim model-authored
    # text (judge rationales, quarantined raw payloads) is quoted evidence,
    # and one judge saying "causally" must not make the report unbuildable.
    causal_word_guard(_without_model_text(report))
    return report


#: Keys whose values are verbatim model output, excluded from the causal guard.
#: ("error" on quarantined rows embeds the model's invalid stance verbatim.)
_MODEL_TEXT_KEYS = frozenset({"rationale", "raw", "error"})


def _without_model_text(obj: Any) -> Any:
    if isinstance(obj, Mapping):
        return {k: (None if k in _MODEL_TEXT_KEYS else _without_model_text(v))
                for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_without_model_text(v) for v in obj]
    return obj


def causal_word_guard(report: Mapping[str, Any]) -> None:
    """Frozen causal-word guard (ADR-014 §5).

    Raises AssertionError if the number of case-insensitive "causal"
    occurrences in ``json.dumps(report)`` exceeds
    :data:`CAUSAL_WORD_MAX_OCCURRENCES`, or if any occurrence's
    surrounding :data:`CAUSAL_WORD_CONTEXT_WINDOW` chars contain none of
    :data:`CAUSAL_WORD_ALLOWED_CONTEXTS` (i.e. is not a negated framing).
    """
    blob = json.dumps(report)
    matches = list(re.finditer(r"causal", blob, flags=re.IGNORECASE))
    if len(matches) > CAUSAL_WORD_MAX_OCCURRENCES:
        raise AssertionError(
            f"report contains {len(matches)} 'causal' occurrences; frozen cap "
            f"is {CAUSAL_WORD_MAX_OCCURRENCES}"
        )
    for m in matches:
        lo = max(0, m.start() - CAUSAL_WORD_CONTEXT_WINDOW)
        hi = min(len(blob), m.end() + CAUSAL_WORD_CONTEXT_WINDOW)
        window = blob[lo:hi]
        if not any(tok in window for tok in CAUSAL_WORD_ALLOWED_CONTEXTS):
            raise AssertionError(
                f"non-negated 'causal' occurrence at offset {m.start()}: "
                f"{window!r}"
            )


def write_g5_close_report(report: Mapping[str, Any], path: str | Path) -> None:
    """Write the report byte-stably: ``json.dumps(indent=2, sort_keys=True)``
    + trailing newline, after asserting no key material and JSON
    round-trip stability (reviewer LOW-1: the real on-disk property is
    that a reload-and-redump of the written text is byte-identical; the
    former same-dict re-dump assert was a tautology)."""
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    # Round-trip stability: canonical re-serialization of the parsed text
    # must be byte-identical (what a consumer reading the file sees).
    assert json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n" == text
    _assert_no_key_material(text, str(path))
    Path(path).write_text(text)


def _row_provenance(sample_manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Row provenance (reviewer F7): computed from the sample, never hardcoded.

    ``g5_fresh_suffix_rows`` = sample rows with ``lifecycle_tick >= 13``;
    ``l1_replayed_prefix_rows`` = sample rows with
    ``lifecycle_tick <= SUFFIX_TICK_MIN - 1``. The recorded ``prefix_sha256``
    is the sha256 of the canonical serialization of the sample's prefix
    rows (sample-derived, not the L1 event-ledger tape hash — see the
    note and the ``deviations_from_contract`` record).
    """
    prefix_rows = [
        r for r in (sample_manifest.get("rows") or ())
        if int(r["lifecycle_tick"]) < SUFFIX_TICK_MIN
    ]
    suffix_rows = [
        r for r in (sample_manifest.get("rows") or ())
        if int(r["lifecycle_tick"]) >= SUFFIX_TICK_MIN
    ]
    return {
        "g5_fresh_suffix_rows": len(suffix_rows),
        "l1_replayed_prefix_rows": len(prefix_rows),
        "prefix_sha256": _sha256_hex(json.dumps(prefix_rows, sort_keys=True)),
        "note": (
            "prefix rows are replayed verbatim from the L1 run (identical "
            "across all 10 pairs); the sample is a complete census of all "
            "unique G5 model-generated behavior. prefix_sha256 is the "
            "sha256 of the sample's prefix rows (canonical JSON), NOT the "
            "L1 event-ledger tape hash (which requires L1 artifacts this "
            "pure function does not load)"
        ),
    }


def _engine_outcome_breakdown(
    judged: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Per-engine-outcome judged-row counts (reviewer F8).

    Any outcome class with ``n <= 1`` gets the data-driven flag
    ``{"n": <n>, "statistically_meaningful": False, "note": ...}`` (G4
    convention); no class or event is hardcoded.
    """
    counts: dict[str, int] = {}
    for r in judged:
        decision = str((r.get("engine_outcome") or {}).get("decision"))
        counts[decision] = counts.get(decision, 0) + 1
    out: dict[str, Any] = {}
    for decision in sorted(counts):
        n = counts[decision]
        entry: dict[str, Any] = {"n": n}
        if n <= 1:
            entry["statistically_meaningful"] = False
            entry["note"] = (
                "single-row class; G4 convention: not statistically meaningful"
            )
        out[decision] = entry
    return out


def _agreement_ci(n_agree: int, n_judged: int) -> dict[str, Any]:
    ci = wilson_ci_95(n_agree, n_judged) if n_judged else None
    return {
        "n_judged": n_judged,
        "n_agree": n_agree,
        "n_disagree": n_judged - n_agree,
        "rate": (n_agree / n_judged) if n_judged else None,
        "wilson_ci_95": [ci[0], ci[1]] if ci else None,
    }


def build_judge_section(
    judge_tape: Sequence[Mapping[str, Any]],
    sample_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """The report's ``judge`` section from the judge tape (ADR-014 §3/§6).

    ``judge_tape`` is the list of verdict rows produced by
    ``g5_judge_live.run_g5_judge``: one row per sealed sample row with
    ``row_id``, ``pair_index``, ``arm_relevant``, ``quarantined`` and
    ``verdict`` (``{stance, rationale, confidence}`` or None when
    quarantined). Agreement = judge stance agrees with the engine outcome
    (stance "agree" <=> engine accepted the row's action; "disagree" <=>
    engine rejected/quarantined it) — judge-to-engine SELF-CONSISTENCY,
    never an independent compliance verdict.
    """
    judged = [r for r in judge_tape if not r.get("quarantined")]
    quarantined = [r for r in judge_tape if r.get("quarantined")]

    def _agreement(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
        a = sum(
            1
            for r in rows
            if (r.get("verdict") or {}).get("stance") == r.get("expected_stance")
        )
        return _agreement_ci(a, len(rows))

    disagreements = [
        {
            "row_id": r.get("row_id"),
            "pair_index": r.get("pair_index"),
            "judge_stance": (r.get("verdict") or {}).get("stance"),
            "engine_stance": r.get("expected_stance"),
            "rationale": (r.get("verdict") or {}).get("rationale"),
        }
        for r in judged
        if (r.get("verdict") or {}).get("stance") != r.get("expected_stance")
    ]
    return {
        "n_rows": len(judge_tape),
        "n_valid_verdicts": len(judged),
        "agreement_overall": _agreement(judged),
        "agreement_by_row_position": {
            pos: _agreement([r for r in judged if r.get("arm_relevant") == pos])
            for pos in ("prefix", "suffix")
        },
        # Reviewer F1 / MED-2: explicit null-free record that the
        # code-judge stance comparison is not possible on this run root.
        "code_judge": {
            "available": False,
            "reason": (
                "no code-judge artifact in the G5 run root; the "
                "deterministic code judge in this program runs on the "
                "L1 all-81 sample (ADR-012)"
            ),
        },
        # Reviewer F7: row provenance, counts computed from the sample.
        "row_provenance": _row_provenance(sample_manifest),
        # Reviewer F8: per-engine-outcome breakdown, data-driven n<=1 flag.
        "engine_outcome_breakdown": _engine_outcome_breakdown(judged),
        "disagreements": disagreements,
        "quarantined_judge_rows": [
            {
                "row_id": r.get("row_id"),
                "pair_index": r.get("pair_index"),
                "error": r.get("error"),
                "raw": r.get("raw"),
            }
            for r in quarantined
        ],
        "framing": {
            "judge_shown_engine_outcome": True,
            "measured_quantity": (
                "judge-to-engine self-consistency on G5 paired data, not an "
                "independent compliance verdict"
            ),
            "judge_blinding_satisfied_by": "deterministic code judge only",
            "same_family_self_judgment": True,
            "advisor_only": True,
        },
    }


__all__ = [
    "ADJUDICATION_GROUND_TRUTH_STRINGS",
    "ADR5_OVERSTATEMENT_LIMITATION",
    "B1_SCOPE",
    "CAUSAL_WORD_ALLOWED_CONTEXTS",
    "CAUSAL_WORD_CONTEXT_WINDOW",
    "CAUSAL_WORD_MAX_OCCURRENCES",
    "DEVIATIONS_FROM_CONTRACT",
    "EXPECTED_SAMPLE_ROWS",
    "FROZEN_LIMITATIONS",
    "NOT_CLAIMED",
    "audit_leakage",
    "audit_prompt_invariance",
    "audit_quarantine_symmetry",
    "audit_self_report",
    "build_g5_close_report",
    "build_judge_section",
    "build_sample_manifest",
    "build_sealed_sample",
    "causal_word_guard",
    "identification_diagnostics",
    "leave_one_pair_out",
    "permutation_null",
    "write_g5_close_report",
]
