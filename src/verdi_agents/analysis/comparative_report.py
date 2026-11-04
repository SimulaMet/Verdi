"""Self-contained comparative HTML report for a paired fixed-vs-revisable run.

Renders :func:`verdi_agents.analysis.outcome_metrics.summarize_pair` output as one
local, dependency-free HTML file (plain CSS tables; no external JS). The
document is deterministic: sections, arms and seeds are sorted, values come
only from the summary dict, and no generation timestamp is embedded. Every
row that corresponds to an aggregate links out to its underlying event-ledger
ids so a reader can audit the number against ``events.jsonl``.
"""
from __future__ import annotations

import html
import os
from pathlib import Path
from typing import Any


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        text = f"{value:.4f}".rstrip("0").rstrip(".")
        return text or "0"
    return _esc(value)


def _evidence(ids: list[int] | None, limit: int = 8) -> str:
    """Render a short, auditable reference to ledger event ids."""
    if not ids:
        return ""
    shown = ", ".join(str(i) for i in ids[:limit])
    more = f" (+{len(ids) - limit} more)" if len(ids) > limit else ""
    return f"<span class='ev'>ledger events: {shown}{more}</span>"


def _completion_rows(seed: int, metrics: dict[str, Any]) -> list[str]:
    comp = metrics.get("completion_rate", {})
    all_ids = [i for o in comp.get("objectives", []) for i in o["evidence_event_ids"]]
    rows = [
        "<tr><td>Overall completion rate "
        f"(verified completed / evaluated = {comp.get('completed', 0)} / "
        f"{comp.get('evaluated', 0)})</td><td>{_fmt(comp.get('overall'))}</td>"
        f"<td>{_evidence(all_ids)}</td></tr>"
    ]
    for obj in comp.get("objectives", []):
        rows.append(
            "<tr><td>Objective <code>{oid}</code> @ tick {tick}: {status} "
            "(delta {delta} of {required} required)</td><td>{status}</td>"
            "<td>{ev}</td></tr>".format(
                oid=_esc(obj["objective_id"]), tick=obj["due_tick"],
                status=_esc(obj["status"]), delta=_fmt(obj["delta"]),
                required=_fmt(obj["required"]),
                ev=_evidence(obj["evidence_event_ids"]),
            )
        )
    return rows


def _sharing_rows(metrics: dict[str, Any]) -> list[str]:
    sharing = metrics.get("resource_sharing", {})
    rows: list[str] = []
    for resource, bucket in sharing.get("committed", {}).items():
        rows.append(
            "<tr><td>Committed flow <code>{res}</code> "
            "(transfer {t} + deposit {d})</td><td>{total}</td>{ev}</tr>".format(
                res=_esc(resource), t=_fmt(bucket["transfer"]),
                d=_fmt(bucket["deposit"]), total=_fmt(bucket["total"]),
                ev=f"<td>{_evidence(bucket['evidence_event_ids'])}</td>",
            )
        )
    attempted = sharing.get("attempted_not_committed", {})
    if attempted:
        detail = ", ".join(f"{r}: {n}" for r, n in sorted(attempted.items()))
        rows.append(
            f"<tr><td>Attempted but NOT committed (rejected) flows</td>"
            f"<td>{sum(attempted.values())}</td>"
            f"<td>{_esc(detail)}</td></tr>"
        )
    fin = sharing.get("final_inequality", {})
    if fin.get("primary_resource") is not None:
        rows.append(
            "<tr><td>Final holdings Gini of <code>{res}</code> "
            f"@ tick {fin.get('tick')} ({_esc(fin.get('holdings'))})</td>"
            f"<td>{_fmt(fin.get('gini'))}</td>"
            f"<td><span class='ev'>resources.jsonl@tick{_esc(fin.get('tick'))}</span></td></tr>"
        )
    reserves = sharing.get("reserve_levels", {})
    if reserves:
        finals = {r: pts[-1][1] for r, pts in sorted(reserves.items()) if pts}
        rows.append(
            "<tr><td>Reserve levels over time (final world-pool balances)</td>"
            f"<td colspan='2'>{_esc(finals)}</td></tr>"
        )
    return rows or ["<tr><td>No transfer/deposit activity recorded</td><td></td><td></td></tr>"]


def _exclusion_rows(metrics: dict[str, Any]) -> list[str]:
    excl = metrics.get("exclusion_episodes", {})
    rows: list[str] = []
    for aid, entry in excl.get("agents", {}).items():
        episodes = "; ".join(
            f"{e['sanction_id']} eff@{e['effective_tick']}"
            + (f"→restored@{e['restored_tick']}" if e["restored_tick"] is not None
               else "→open")
            for e in entry["episodes"]
        ) or "none"
        repeat = " (repeat)" if entry["repeat_episode"] else ""
        rows.append(
            f"<tr><td>{_esc(aid)}{repeat} — excluded from transfer+deposit</td>"
            f"<td>{entry['excluded_ticks']} ticks</td>"
            f"<td>{_esc(episodes)} <span class='ev'>sanctions.jsonl</span></td></tr>"
        )
    totals = excl.get("totals", {})
    if any(totals.values()):
        rows.append(
            "<tr><td>Totals (imposed / appeals filed / upheld / restorations)</td>"
            f"<td>{totals.get('sanctions_imposed', 0)} / "
            f"{totals.get('appeals_filed', 0)} / {totals.get('appeals_upheld', 0)} / "
            f"{totals.get('restorations', 0)}</td>"
            "<td><span class='ev'>sanctions.jsonl</span></td></tr>"
        )
    return rows or ["<tr><td>No sanction episodes recorded</td><td></td><td></td></tr>"]


def _institution_rows(metrics: dict[str, Any]) -> list[str]:
    mech = metrics.get("institutional_mechanism", {})
    rows: list[str] = []
    turnout = mech.get("turnout", {})
    rows.append(
        "<tr><td>Proposals submitted</td><td>{n}</td>"
        "<td>{ev}</td></tr>".format(
            n=len(mech.get("proposals", [])),
            ev="<span class='ev'>meetings.jsonl</span>",
        )
    )
    rows.append(
        "<tr><td>Mean turnout (votes cast / eligible)</td><td>{t} "
        f"({turnout.get('eligible_voters', '?')} eligible, "
        f"{turnout.get('meetings', 0)} meetings)</td>"
        "<td><span class='ev'>meetings.jsonl</span></td></tr>".format(
            t=_fmt(turnout.get("mean_turnout"))
        )
    )
    votes = mech.get("votes", {})
    rows.append(
        "<tr><td>Votes for / against / abstain</td>"
        f"<td>{votes.get('for', 0)} / {votes.get('against', 0)} / "
        f"{votes.get('abstain', 0)}</td>"
        "<td><span class='ev'>meetings.jsonl</span></td></tr>"
    )
    for enactment in mech.get("enactments", []):
        deltas = enactment.get("rule_deltas", {})
        delta_bits = []
        for key, added in (deltas.get("added") or {}).items():
            delta_bits.append(f"+{_esc(key)}={_esc(added)}")
        for key, change in (deltas.get("changed") or {}).items():
            delta_bits.append(f"~{_esc(key)}:{_esc(change['from'])}→{_esc(change['to'])}")
        for key in (deltas.get("removed") or {}):
            delta_bits.append(f"-{_esc(key)}")
        # One f-string, escaped once: a ``.format`` pass over already-inserted
        # rule values would treat their braces (e.g. dict values) as fields.
        rows.append(
            f"<tr><td>Enacted amendment <code>{_esc(enactment['version'])}</code>"
            f" @ tick {_esc(enactment['tick'])}</td>"
            f"<td>{len(delta_bits)} rule change(s)</td>"
            f"<td>{' '.join(delta_bits)} "
            f"<span class='ev'>constitution_history.jsonl</span></td></tr>"
        )
    enacted_ids = mech.get("amendment_enacted_event_ids", [])
    rejected_ids = mech.get("amendment_rejected_event_ids", [])
    if enacted_ids or rejected_ids:
        rows.append(
            "<tr><td>Amendment ledger events (enacted / rejected)</td>"
            f"<td>{len(enacted_ids)} / {len(rejected_ids)}</td>"
            f"<td>{_evidence(enacted_ids)} {_evidence(rejected_ids)}</td></tr>"
        )
    blocked = mech.get("blocked_actions_by_code", {})
    for code, count in sorted(blocked.items()):
        evidence = mech.get("blocked_action_evidence", {}).get(code, [])
        rows.append(
            f"<tr><td>Actions blocked by <code>{_esc(code)}</code></td>"
            f"<td>{count}</td><td>{_evidence(evidence)}</td></tr>"
        )
    return rows


def _recovery_row(summary: dict[str, Any], arm: str, seed: Any) -> str:
    """Recovery section per (arm, seed) pair using the preregistered call."""
    from verdi_agents.analysis.outcome_metrics import recovery_time

    metrics = summary["arms"][arm][str(seed)]
    try:
        rec = recovery_time(
            Path(metrics["run_dir"]),
            pre_shock_window=(0, 10),
            post_shock_start=14,
            target_fraction=0.5,
            sustained_ticks=3,
        )
    except Exception as exc:  # noqa: BLE001 - report, don't crash the page
        return (f"<p><b>Recovery time ({_esc(arm)}-seed{_esc(seed)})</b>: "
                f"unavailable ({_esc(exc)})</p>")
    if rec["recovered"]:
        label = f"recovered at tick {rec['recovery_tick']}"
    elif not rec["pre_shock_rate"]:
        # No objective due in the preregistered baseline window (None), or a
        # 0.0 baseline: the criterion is undefined -- not evidence of
        # non-recovery (recovery_time still reports it censored).
        label = ("not evaluable — no delivery objective is due in the "
                 "preregistered baseline window" if rec["pre_shock_rate"] is None
                 else "not evaluable — pre-shock baseline rate is 0")
    elif rec["censored"]:
        label = "CENSORED — never recovered by run end"
    else:
        label = "not applicable"
    return (
        "<p><b>Recovery time ({a}-seed{s})</b>: {label} "
        "(pre-shock baseline rate {b}, window {w}, criterion ≥{f}×baseline "
        "sustained {k} ticks)</p>{ev}".format(
            a=_esc(arm), s=_esc(seed), label=label,
            b=_fmt(rec["pre_shock_rate"]), w=rec["baseline_window"],
            f="0.5", k=3,
            ev=_evidence(rec["evidence_event_ids"]),
        )
    )


#: Preregistration note shown verbatim in the report (the thresholds used by
#: :func:`_recovery_row`; they must match the function call above).
RECOVERY_PREREGISTRATION_NOTE = (
    "Recovery was defined BEFORE reading results: per-tick delivery-objective "
    "success rate must reach ≥ 50% of the pre-shock baseline (objectives due "
    "ticks 0–10) and hold for 3 consecutive ticks starting on/after tick 14 "
    "(post-shock). Runs that never meet the criterion are reported CENSORED."
)


def write_comparison_html(summary: dict[str, Any], out_path: str | Path) -> Path:
    """Write the self-contained comparison HTML for a pair summary."""
    out_path = Path(out_path)
    arms = sorted(summary.get("arms", {}).items())
    parts: list[str] = [
        "<!doctype html>",
        '<html lang="en"><meta charset="utf-8">',
        "<title>VERDI · Comparative outcome report</title>",
        "<style>",
        "body{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;"
        "margin:2rem auto;max-width:960px;color:#1c1c1e;padding:0 1rem}",
        "h1{font-size:1.5rem} h2{font-size:1.2rem;margin-top:2rem;border-bottom:"
        "1px solid #d1d1d6;padding-bottom:.25rem}",
        "table{border-collapse:collapse;width:100%;margin:.5rem 0 1rem}",
        "th,td{border:1px solid #d1d1d6;padding:.35rem .5rem;text-align:left;"
        "vertical-align:top;font-size:.9rem} th{background:#f2f2f7}",
        ".ev{color:#6b6b70;font-size:.78rem}",
        "code{background:#f2f2f7;padding:0 .25rem;border-radius:3px}",
        ".note{background:#fff8e1;border-left:4px solid #f0b429;padding:.5rem .75rem;"
        "font-size:.88rem}",
        "</style><body>",
        "<h1>VERDI · Comparative outcome report</h1>",
        f"<p>Experiment: <code>{_esc(summary.get('experiment_id'))}</code> · "
        f"Paired seeds (both arms complete): "
        f"{_esc(', '.join(str(s) for s in summary.get('paired_seeds', [])) or 'none')}</p>",
    ]
    if summary.get("skipped"):
        parts.append("<div class='note'><b>Skipped runs</b> (visible, not dropped): "
                     + "; ".join(
                         f"{_esc(s['arm'])}-seed{_esc(s['seed'])}: {_esc(s['reason'])}"
                         for s in summary["skipped"])
                     + "</div>")

    for arm, by_seed in arms:
        for seed in sorted(by_seed, key=int):
            metrics = by_seed[seed]
            header = (f"<h2>Arm <code>{_esc(arm)}</code> · seed {_esc(seed)}</h2>"
                      f"<p>Run dir: <code>{_esc(metrics.get('run_dir'))}</code> · "
                      f"final state hash: <code>{_esc((metrics.get('final_state_hash') or '')[:16])}"
                      f"…</code> · replay matches: {_esc(metrics.get('replay_matches'))}</p>")
            parts.append(header)
            parts.append("<h3>Completion rate</h3><table>"
                         "".join(_completion_rows(int(seed), metrics)) + "</table>")
            parts.append("<h3>Resource sharing</h3><table>"
                         "".join(_sharing_rows(metrics)) + "</table>")
            parts.append("<h3>Exclusion episodes</h3><table>"
                         "".join(_exclusion_rows(metrics)) + "</table>")
            parts.append("<h3>Institutional mechanism</h3><table>"
                         "".join(_institution_rows(metrics)) + "</table>")
            parts.append(f"<h3>Recovery time</h3><div class='note'>{RECOVERY_PREREGISTRATION_NOTE}</div>"
                         + _recovery_row(summary, arm, seed))

    parts.append("</body></html>")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text("\n".join(parts) + "\n", encoding="utf-8")
    os.replace(tmp, out_path)
    return out_path


if __name__ == "__main__":  # pragma: no cover - manual convenience only
    import argparse
    import json as _json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", required=True, type=Path,
                        help="metrics.json produced by outcome_metrics")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    print(write_comparison_html(
        _json.loads(args.summary.read_text(encoding="utf-8")), args.out))
