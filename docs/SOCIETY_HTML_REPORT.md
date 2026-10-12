# Society run visualization

Generate a single read-only HTML snapshot:

```sh
source .venv/bin/activate
python -m verdi.analysis.html_report runs/society-v2/my-run
open runs/society-v2/my-run/report.html
```

The default embeds Plotly.js 3.3.0 and daisyUI 5.5.14 (both MIT; distribution
license headers retained). First generation downloads pinned library assets into
`~/.cache/verdi/html-report`; subsequent generations work offline. No Python
packages, server, JavaScript build, or simulation/model invocation needed.
`--asset-dir PATH` uses an alternate cache containing `plotly.min.js` and
`daisyui.css`. `--cdn` produces an internet-dependent preview. `--output PATH`
chooses the destination. Regenerate after the simulation advances.

Views: resources per agent and common pool; declared/private/public probe scores
per value; model latency and prompt tokens by purpose; governance records;
cumulative ledger event kinds. Filters select agent, resource, value and an
arbitrary lifecycle window. Latest-100 navigation has no hard maximum tick.
Lines render at most 2,000 points using min/max buckets (extrema retained;
intermediate shape approximated). Narrow the window for finer detail. Probe and
model-call scatter plots also have bounded display; the inspector retains exact
records, searchable and paginated at 25 records per page. Governance markers
are sampled uniformly to at most 2,000; rare events remain in the inspector.

Ledger ticks are **reducer transition counts**, not lifecycle ticks. Their chart
and inspector are deliberately separate from the lifecycle window. Missing
streams are disclosed; absent measurements do not become zero. A truncated final
JSONL record is skipped with a warning; malformed complete records fail.
The generator reads named JSON/JSONL artifacts, never raw prompt tapes or private
snapshots; the HTML still contains agent probe responses and action reasons.

A finite HTML snapshot necessarily grows with retained evidence: parsing/storage
are O(records), though displayed lines and DOM evidence are bounded. Thousands
of ticks are supported; truly unbounded live history requires rotating reports
or a server-backed query interface. These are descriptive trajectories, not
causal findings or evidence of internal belief change. No new scientific gate
is asserted by a visualization.

Library references: [Plotly license](https://github.com/plotly/plotly.js/blob/master/LICENSE),
[Plotly controls](https://plotly.com/javascript/controls/),
[daisyUI distributions](https://daisyui.com/docs/cdn/).
