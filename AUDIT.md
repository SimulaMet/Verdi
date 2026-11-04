# System Audit and Remediation — verdi

Started 2026-11-03 on branch `ci/pypi-release` (base `4664c96`). All changes are
uncommitted working-tree edits; nothing was pushed or published.

## Verification commands

| Check | Command | Baseline | Final |
|---|---|---|---|
| Lint (CI gate) | `ruff check .` | clean | clean |
| Tests (CI gate) | `python -m pytest` | 672 passed, 1 skipped | 769 passed, 1 skipped |
| Types (not a CI gate) | `mypy src/verdi_agents --ignore-missing-imports` | 73 errors | 66 errors (all triaged, see R7) |
| Packaging | `pip wheel . --no-deps` + fresh venv, `verdi --help`, `verdi-agents init`, `verdi-agents sweep … --dry-run` | worked | not re-run (no packaging config changed) |
| Formatting | `ruff format --check .` | 156 files would change | unchanged — not enforced by CI; mass reformat deliberately skipped |

Secret scan of tracked files and history (`sk-…`, `AKIA…`, `Bearer …`): only
obvious test fixtures (`tests/audit/test_judge_live.py`, `tests/experiments/test_g5_close.py`).
`.env` is git-ignored and was never committed.

## Method

Pass 1: four independent read-only reviewers (world/governance/schemas/scheduler;
society/agents/pilot/config; analysis/measurement/audit; CLI/experiments/packaging/docs)
plus mypy triage. Every finding was re-checked against the code before fixing.
Pass 2: an independent reviewer over the diff (regressions) and a fresh reviewer over
areas pass 1 only skimmed (pilot, g5_*, paired_run, measurement, adapters, recorded).
Each fix has a regression test that was confirmed to FAIL on the pre-fix source
(`git stash push -- src/…`) and pass after.

New tests live in `tests/*/test_audit_regressions.py` (agents, world, governance,
society, analysis, experiments). `tests/test_cli.py` was updated (see F22).

## Fixed findings (most severe first)

| # | Sev | Where | Defect | Fix |
|---|---|---|---|---|
| F1 | Critical | `world/reducer.py` `_transfer`, `world/validate.py` | Transfer to self minted resources (recipient read before sender debit written back): `food=10`, `transfer{to:self,units:10}` → 20. Validator accepted it, so an LLM could do it every tick. | Validator rejects with new non-sanctionable code `self_transfer`; reducer raises `InvariantViolationError` as defence in depth. |
| F2 | High | `governance/constitution.py` | Two amendments enacted in one assembly: second was built from the shared submission base, silently dropping the first's rules. | New version = delta applied onto `active_at(effective_tick)`; vote rules still from the submission base. |
| F3 | High | `governance/constitution.py` | Caller-supplied votes counted raw: no dedup per voter, wrong-proposal votes counted, ballots `cast_vote` had rejected still counted; ineligible voters decided the threshold. | Recorded ballot is the single source of truth; caller votes go through `record_vote`; tally uses the same eligibility filter as quorum. |
| F4 | High | `governance/constitution.py` | Out-of-range `quorum`/`amendment_threshold` in a payload: amendment recorded as valid, then `ConstitutionVersion` raised, leaving store inconsistent. | Validated as fractions in (0, 1] → `invalid_quorum` / `invalid_amendment_threshold`. |
| F5 | High | `governance/sanctions.py`, `appeals.py`, `society/campaign.py` | One revocation per (agent, right): expiring/overturning one of two overlapping sanctions restored the right while the other was active; campaign cleared `under_sanction` on any expiry. | `RightsView` keeps all intervals; `SanctionEngine.end()` re-revokes from remaining live sanctions; campaign keeps the flag while `has_live_sanction`. |
| F6 | High | `agents/client.py` | 200 response with non-JSON body raised raw `JSONDecodeError` (not `ModelError`), aborting whole campaign batches; `"content": null` propagated `None` → `TypeError`/`AttributeError` in policy parsing, reflection, consolidation. | Parse inside the guarded block; `None` → `""`; non-string content → `ModelError`. |
| F7 | High | `analysis/comparative_report.py` | Enactment row used `f"…".format()` after inserting rule values: dict values crashed the report (`KeyError`), `{v}` in values was substituted, values double-escaped. | Single f-string, each value escaped once. |
| F8 | High | `analysis/outcome_metrics.py` `recovery_time` | Declared due ticks zipped with outcome events in YAML order, but the campaign evaluates in due-tick order; shipped `examples/full.yaml` declares `[12, 30, 46, 30]` → outcomes mislabelled. Manifest + resolved YAML ticks were also appended twice, forcing the raw-tick fallback; YAML errors crashed. | Resolved YAML replaces manifest ticks; ticks sorted (stable) before zip; YAML errors handled. |
| F9 | High | `experiments/sweep.py` | Ctrl-C: `with ThreadPoolExecutor` drained the queue, *starting* every queued run (spending model calls), then marked finished runs "interrupted". | Manual executor; `shutdown(cancel_futures=True)` on interrupt; finished-but-unrecorded results kept. |
| F10 | High | `experiments/sweep.py` | Resume reused "complete" runs by `run_id` only (a changed `--ticks` was ignored); runs from earlier invocations leaked into results, summary and exit code. | Per-run `scenario_sha256`; mismatch reruns; `current_runs` filters `SweepResult`, `collect()` and the exit code. Legacy entries without a hash are trusted as before. |
| F11 | High | `experiments/interview_experiment.py` | Post-pass audit always crashed: `asyncio.run()` given the dict returned by synchronous `audit_from_file`. | Call it directly. |
| F12 | Medium | `interview_experiment.py` | Post-pass audit ignored the scenario `model:` block (wrong/missing endpoint). | Uses `resolve_endpoint_config(load_scenario(...))`. |
| F13 | Medium | `config.py`, `g5_pairs.py`, `society/live.py` | Leak guards only checked `MODEL_API_KEY`/`VERDI_VLLM_API_KEY`; a key read via `model.api_key_env` was never scanned for. `ModelEndpointConfig.__repr__` printed the key. | Every resolved key is tracked for `api_key_values()`; G5 artifact audit scans all keys; `api_key` is `repr=False`. |
| F14 | Medium | `config.py` | `max_retries < 0` made every call raise `AssertionError("unreachable")`; zero timeout / concurrency accepted. | Range-checked with readable `ValueError`. |
| F15 | Medium | `society/campaign.py`, `memory.py` | Consolidation prompt's "existing summary" contained every recent entry (string split on the rendered history). | `AgentMemory.consolidated_summary()`. Note: changes consolidation prompt text (see R4). |
| F16 | Medium | `society/tasks.py`, `campaign.py` | One bad field (`"amount": null`) discarded the whole orchestrated tape; invalid transfer targets produced tasks the validator must reject; failed-parse calls were never logged, so token accounting undercounted. | Per-field fallback; invalid transfer → produce; meta returned with `parse_error` and recorded. |
| F17 | Medium | `society/live.py`, `agents/client.py` | Stall watchdog: reflection/consolidation write `model_calls.jsonl` only after the whole batch, so a long healthy batch could trip `os._exit(3)`; retry window ignored pool/connect timeouts and backoff. | In-process `completed_calls` counter feeds the watchdog; window includes pool + connect + max backoff per attempt. |
| F18 | Medium | `world/branch.py` | Branching after a prefix ending in a rejected event always failed (`after_hash=None`). | Terminal hash = `after_hash or before_hash`. |
| F19 | Medium | `world/policy.py`, `validate.py` | `max_produce_units_per_tick` was a per-action cap without an economy block. | Cumulative using `produced_this_tick` when supplied. |
| F20 | Medium | `measurement/longitudinal.py` `_parse_choice` | `"Yes."`, `'"yes"'`, `"Yes, I would."` scored as the low pole; Likert took the first digit anywhere (`"0 to 5 … 4"` → 0, `"10"` → 1, `"7 out of 10"` → crash of the whole probe round). | First-word match for word options; last standalone in-range integer for Likert; anything else flagged unparsed instead of raising. |
| F21 | Medium | `experiments/paired_run.py` | Missing `output_root` → `TypeError`; `except BaseException` swallowed Ctrl-C and continued; resume ignored scenario changes; `--concurrency` help promised `VERDI_MAX_CONCURRENCY` but code hard-coded 4. | Readable error; `except Exception`; per-arm `scenario_sha256`; default honours the env var. |
| F22 | Medium | `cli.py` | `verdi-agents run x.yaml --run-dir=out` ignored the `=` form; bare `verdi` printed a pointer to a non-existent `docs/IMPLEMENTATION_PLAN.md`. | Both forms detected; bare `verdi` prints help (exit 0 kept for compatibility). Test updated to assert the help text instead of the stale string. |
| F23 | Medium | `pilot/driver.py` | Sanction evidence could cite a quarantine rejection of the subject's proposal (false sanction); default live capture dir had 1-second resolution (two runs could share one `live_tape.jsonl`). | Evidence only from the scripted violation; dir stamp has microseconds + pid. Offline canonical path unchanged. |
| F24 | Low | `world/validate.py` | Non-string `resource` (list/dict/null from model JSON) crashed with `TypeError`. | Rejected as `missing_argument`. |
| F25 | Low | `world/snapshot.py` | `load` never re-hashed content; digest used unvalidated as a filename (`../`). | Digest must be 64 hex; content hash verified on load. |
| F26 | Low | `governance/constitution.py` | A proposal could be enacted twice; version names could collide with an existing one and be silently skipped. | `ProposalNotInVoteError` on re-enact; unique version names. |
| F27 | Low | `world/economy.py` | `role_consumption` for a resource not in `consumption_per_tick` was silently ignored. | Rejected with a clear config error (shipped examples unaffected). |
| F28 | Low | `audit/live_bridge.py` | Scorer `ValueError` recorded as score 0.0 (worst), indistinguishable from judge disagreement. | `None` + `rubric_score_error`. |
| F29 | Low | `experiments/sweep.py` | Dot-only labels (`..`) escaped the sweep folder; bad sweep YAML / `vary: null` gave tracebacks; setup I/O errors aborted the sweep. | Labels sanitised; readable `SweepError`s; setup inside the per-run guard. |
| F30 | Low | `analysis/cross_run_compare.py` | `"tick": null` in provenance crashed (`int(None)`). | Non-numeric ticks skipped. |
| F31 | Medium | `experiments/sweep.py` (regression from F10, found by pass-2 diff review; also pre-existing for failed-run retries) | Relaunching a run into its old directory appended a second ledger to `events.jsonl` / `model_calls.jsonl` (two `event_id: 0`, broken hash chain). | Previous non-empty attempt is renamed to `<run>.prev-<UTC stamp>` (never deleted) before relaunch. |
| F32 | Low | `analysis/outcome_metrics.py` (regression from F8) | Malformed `due_tick` (e.g. `soon`) raised instead of degrading, in both the resolved-YAML and manifest paths. | Falls back to raw ledger ticks as before. |
| F33 | High | `society/campaign.py` (found pass 3) | Production cap is documented per agent per tick (`economy.py`, `examples/full.yaml`, `institutional_recovery_constrained.yaml`), but the campaign summed production across ALL agents: once one agent hit the cap, every later producer that tick was rejected with `law:production_limit`. Also completes F19 (the cumulative constitutional cap was never fed in runs without an economy block). | `produced_this_tick` keyed by actor, tracked with or without an economy block. Campaign-level test in `tests/society/test_economy_campaign.py`. **Changes outcomes of economy runs vs earlier results.** |
| F34 | High | `audit/judge_live.py` `KEY_MATERIAL_PATTERNS` (found pass 3) | Leak guard matched ordinary speech (`Bearer of bad news`, `Authorization: the assembly approved…`): one such interview answer aborted `_write_artifacts` mid-way (truncated `interviews.jsonl`, all later artifacts lost) and judge tape writes. The guard's exception also echoed the matched secret into logs/findings. | Bearer / api_key / authorization patterns require a token-shaped value (all real key shapes still flagged, see tests); messages show only a 6-char prefix + length. `tests/experiments/test_g5_close.py::test_b2_planted_key_material_fails_leakage` updated to assert the finding locates the leak and does **not** contain the key (stricter, not weaker). |
| F35 | Medium | `measurement/interview_values.py` `_nearest_event` (pass 3) | Returned the agent's EARLIEST event, and compared lifecycle ticks (0–29) with ledger transition ticks (1–373): every trajectory row linked to the agent's first action (checked on `runs/interview-12agent-seed42`). | Lifecycle tick from the event's task id (`soc-tNN-`), carried forward for events without one; latest match wins. |
| F36 | Medium | `experiments/g5_close.py` (pass 3) | One judge rationale containing "causally" made the G5-close report unbuildable (frozen causal-word guard ran over verbatim model text). | Guard runs on the report with model-authored `rationale` / `raw` fields blanked. |
| F37 | Low–Med | `audit/interview_audit.py` (pass 3) | SimpleAudit reports scenario/judge failures as `severity="ERROR"` without raising; rows had `error: None`, so summaries reported 0 errors when the endpoint was down. | `severity == ERROR` sets `error`. |
| F38 | Medium | `measurement/longitudinal.py` (regression in F20, pass 3) | First F20 version rejected `"4."`/`"4.0"` and picked the scale top from `"4/5"`, `"4 out of 5"`, `"(scale 0-5)"`. | Scale ranges and denominators removed before taking the last standalone integer. |
| F39 | Medium | `experiments/paired_run.py` (pass 3) | Re-running a failed/stale arm appended into the old run dir (same root cause as F31). | Shares `_set_aside_previous_attempt`. |
| F40 | High | `analysis/outcome_metrics.py` `exclusion_episodes` (pass 4) | Open episodes ran to the last LEDGER tick (transition counter) while sanction ticks are LIFECYCLE ticks: `runs/interview-6agent-god-orch` grace sanctioned at t19 of 20 got `excluded_ticks: 85`. Overlapping sanctions were summed (dave 7 instead of 6) and flagged as repeat episodes. | Run end from `resources.jsonl` (ledger tick only as fallback); intervals merged per agent; `repeat_episode` = more than one merged interval. Now grace 0, dave 6. |
| F41 | Medium | `society/campaign.py` `_compute_metrics`, `live.py` (pass 4) | `metrics.json` `sanctions` counted world shocks and rejected impositions; `appeals` counted filed + adjudicated rows (6 for 3 appeals). Flows into sweep `summary.csv`. | Count accepted `sanction_imposed` and `phase == filed` only. |
| F42 | Medium | `society/prompting.py` (pass 4) | Action prompt said "It is now tick 104" in a 20-tick run (ledger counter), while durations/deadlines/caps count lifecycle ticks. | Uses `task.tick_created`; `SOCIAL_PROMPT_VERSION` bumped to `/5` (prompt text changes). |
| F43 | Low | `analysis/comparative_report.py` (pass 4) | Undefined baseline (no objective due in the preregistered window) was labelled "CENSORED — never recovered". | Labelled "not evaluable"; preregistered thresholds untouched (see R14). |
| F44 | Medium | `measurement/longitudinal.py` (regression in F38, pass 4) | "Last integer wins" let trailing reasoning override the answer (`"Choice: 4 … 2 factors"` → 2); JSON `{"choice": 4}` (int) fell through to free-text parsing. | First in-range standalone integer; int `choice` accepted. |
| F45 | Low–Med | `audit/judge_live.py` (regression in F34, pass 4) | Narrowed patterns missed `Authorization: Basic …`, digit-free keys, `"api_key": "…"` (JSON) and `x-api-key`. | Optional scheme word, 20+-char digit-free values, quote before separator, `x-api-key`. Zero hits over 398 real run artifacts + the plotly report bundle; ordinary speech still passes. |
| F46 | Low | `experiments/g5_close.py` (pass 4) | Quarantined rows' `error` embeds the model's stance verbatim, still able to trip the causal guard. | `error` added to excluded model-text keys. |
| F47 | Medium | `analysis/outcome_metrics.py` `completion_rate` (pass 5) | `due_tick` was the outcome event's LEDGER tick (`runs/smoke_final`: 56/127/198 for declared 10/24/38); shown in the comparative report and sweep/pair CSVs. | Declared due tick from `scenario_resolved.yaml` by objective id, else lifecycle tick carried from task ids; raw value kept as new `ledger_tick` field (schema test updated for the added field). |
| F48 | Medium | `analysis/outcome_metrics.py` `recovery_time` (exposed by F47) | A 0.0 pre-shock baseline gave threshold 0, so a run where nothing ever completed "recovered". `test_recovery_time_recovers_on_completed_post_delivery` only passed before because it fed the ledger tick as the due tick. | Zero baseline treated like an undefined one (not evaluable → censored). |
| F49 | Medium | `society/inner_life.py` (pass 5; site F42 missed) | Inner-life prompt also said "It is now tick <ledger counter>"; enabled in both shipped full scenarios. | Lifecycle tick passed from the worker; `INNER_LIFE_PROMPT_VERSION` → `/2`. |
| F50 | Low–Med | `society/campaign.py` `_RecordingCaller` (pass 5) | Probe rows in `model_calls.jsonl` got their subject in COMPLETION order (parallel calls): `runs/smoke_final` had 14/24 probe rows attributed to the wrong agent (tokens/latency, HTML agent filter). | Subject taken before the await (send order). |
| F51 | Low | `audit/judge_live.py` (pass 5, regression in F45) | Case-insensitive `Bearer` + 16 hyphenated chars flagged prose ("standard-bearer counter-proposals"). | Case-sensitive `Bearer` followed by a digit-bearing 16+ token. |
| F52 | Medium | `world/validate.py` (pass 6) | Two accepted `produce 1e308` pushed a balance to `inf`; next tick `int(inf)` in `society/tasks.py` raised `OverflowError` and aborted the run (any scenario without an economy, incl. `starter.yaml`). | Any action whose destination balance would become non-finite is rejected (non-sanctionable code). |
| F53 | Low | `world/validate.py` (pass 6) | `"amount": true` accepted as 1 unit. | Booleans are not quantities. |
| F54 | Medium | `world/economy.py` `parse_task_objectives` (pass 6) | Duplicate objective ids (incl. auto `obj-NN` colliding with an explicit id) overwrote another objective's baseline, flipping completed → failed. | Duplicate ids rejected at load. |
| F55 | Medium | `analysis/scenario.py` `build_t0_state` (pass 6) | Values that load fine then crash deep into a live run: `target_pool` typo (crash at the due tick, after 57 calls), negative / NaN t0 resources, non-integer shock ticks (silently skipped), NaN/inf shock deltas (NaN silently zeroed the resource), empty agent list. | Load-time `ScenarioLoadError`s. All shipped scenarios (examples/, experiments/, tests/, src/) still load. |
| F56 | Medium | `analysis/outcome_metrics.py` `completion_rate` (pass 6, refinement of F47) | Task-id carry-forward lagged a tick when every action of the due tick was quarantined. | Under an economy, lifecycle tick = end-of-tick `consumption` events so far − 1 (exact); task ids only as fallback. |
| F57 | Low | `analysis/comparative_report.py` (pass 6, completes F43/F48) | A 0.0 baseline still printed "CENSORED — never recovered". | Labelled "not evaluable — pre-shock baseline rate is 0". |

## Remaining risks / not fixed (with justification)

| # | Sev | Where | Issue | Why not fixed |
|---|---|---|---|---|
| R1 | High | `measurement/probes.py:128-137` | Value axis is `names[i % 5]` but template is `templates[i % 3]`: the "solidarity" item text says "This is about cooperation"; private items 3–4 repeat dilemmas under different axes. | Changes the measurement instrument. Frozen pilot tapes must replay byte-for-byte and existing runs would stop being comparable. Needs an owner decision and an `instrument_version` bump. |
| R2 | High | `measurement/probes.py:125`, `scoring.py:96` | Yes/no items always score yes=1.0, no=0.1 regardless of item polarity: "accept a secret side-deal? yes" scores integrity 1.0. | Same as R1: an instrument redesign (per-item `reverse_scored`) that changes historical scores. |
| R3 | Medium | `analysis/estimates.py:287, 377-389` | Undefined estimands become 0.0 (no actions → safety 0, no sanctions → false-punishment 0, all-zero Gini → 0); a missing pre-sanction level becomes 0.0, so the agent always "recovers". | The 0.0 convention is written into the preregistration addendum. Changing estimand definitions is a research decision; flag to the analysis owner. |
| R4 | Medium | `society/campaign.py` consolidation (F15) | Consolidation prompts now differ from before, so recorded/replayed runs that captured the old prompt text will not reproduce consolidation calls byte-for-byte. | Intended: the old prompt was wrong. Live runs are not byte-reproducible anyway. |
| R5 | Medium | `measurement/longitudinal.py` | Unparsed probe rows still enter drift calculations (they are flagged but not filtered). | Filtering changes the drift estimand; F20 makes unparsed rows much rarer. |
| R6 | Low | `world/replay.py:90-97`, `branch.py:206-220` | Generic `replay`/`branch_from_snapshot` fold every `TapeStep` as an action; campaign-only kinds (`rights_change`, `world_shock`, …) are not understood. The campaign has its own correct replay. | Only matters if someone feeds a campaign tape to the generic replay; no caller does today. A shared dispatcher is a refactor. |
| R7 | Low | mypy (66 errors) | All triaged: none found to be a runtime bug beyond those fixed. Mostly Optional narrowing and loose annotations. | mypy is not a CI gate; fixing annotations is churn with no behaviour change. |
| R8 | Low | `agents/pool.py`, `campaign.py:_run_bounded` | `asyncio.gather` without cancellation: one failing call leaves siblings running detached. | Moving to `TaskGroup` changes error semantics (ExceptionGroup); low impact since callers abort the run anyway. |
| R9 | Low | `society/memory.py` | `max_entries` is stored but never enforced (growth bounded only by consolidation, which the campaign always runs). | No path appends without consolidating today. |
| R10 | Low | `experiments/treatments.py:135-145` | `rights_restoration_treatment` reports `applied=True` with a tape identical to placebo. | Naming/semantics of a treatment definition; owner decision. |
| R11 | Low | `pilot/live.py:236` | ADR-010 call budget checked only after the run. | Pilot path is sequential and short; enforcing mid-run changes pilot semantics. |
| R12 | Low | `.github/workflows/*` | CI and release install with `pip install -e`, never testing the built wheel's `force-include` examples. | Workflow change best done by the maintainer; packaging was verified manually (wheel + fresh venv + `verdi-agents init`). |
| R13 | Low | `ruff format` | 156 files not formatted. | Not enforced; reformatting would bury the substantive diff. |
| R14 | High | `analysis/comparative_report.py:199-205` | Preregistered recovery criterion (baseline window ticks 0–10, post-shock ≥14, sustained 3 ticks) cannot be evaluated for the shipped `institutional_recovery_constrained.yaml` (due ticks 12, 30, 30, 46): no baseline objective, and 3 consecutive objective-bearing ticks never occur. Every run is "not evaluable" (was mislabelled CENSORED, F43). | The thresholds are explicitly preregistered ("defined BEFORE reading results"); changing them is a research decision. Options: amend the preregistration, or change the scenario's due ticks. |
| R15 | Low | `examples/full.yaml:56-57`, `docs/SCENARIOS.md` | `sanction_policy.max_duration_ticks` and `appeal_policy.deadline_ticks` are inert in campaign runs (fixed 3-tick sanctions; deadline always set explicitly). | Either wire them into `campaign.py` (changes run outcomes) or document them as inert; owner decision. |
| R16 | Low | `analysis/outcome_metrics.py` `recovery_time` | Reports `censored=True` when the baseline is undefined (tests pin this). | Report layer now distinguishes it (F43); changing the metric's contract is an analysis decision. |
| R17 | Low | `analysis/scenario.py`, `campaign.py` | Shocks, assemblies and deliveries scheduled at or after `total_ticks` are silently ignored (a late delivery drops out of `completion_rate`'s denominator). | Not made an error: `verdi-agents sweep --ticks N` deliberately shortens runs below declared ticks. A warning (or reporting dropped objectives in metrics) is the right follow-up. |
| R18 | Low | `analysis/outcome_metrics.py` `exclusion_episodes` | An episode still open at run end is counted up to, not including, the last lifecycle tick (an agent sanctioned only on the final tick shows 0). | Convention pinned by the existing `test_exclusion_repeat_episode_flag_synthetic`; owner decision. |
| R19 | Low | `measurement/longitudinal.py` `_parse_choice` | An answer given after reasoning ("Considering 2 factors, I choose 4") parses as 2. | Same as the original first-digit rule; the prompt asks for a single digit. A structured `{"choice": N}` reply is parsed exactly. |
| R20 | Low | `society/memory.py` | `AgentMemory.from_checkpoint` ignores the stored `max_entries`. | No effect today (R9; `from_checkpoint` unused, `checkpoint.json` write-only). |

## Behaviour changes researchers should know about

These fixes intentionally change results compared with runs made before the audit:

- **F33** per-agent production caps: economy runs accept produces that were rejected before.
- **F42 / F49** prompt text: the action prompt is now `verdi.society-prompt/5` and the inner-life prompt is `verdi.inner-life/2`. They show the lifecycle tick.
- **F15** consolidation prompt text.
- **F16** orchestrator fallbacks.
- **F1 / F52 / F53** validator now rejects self-transfers, overflowing amounts and boolean amounts.
- **F3** ineligible ballots are no longer counted.
- **F2** stacked amendments now apply together.
- **F5** overlapping sanctions are kept separate.
- **Metrics:** F40 and F41 (`metrics.json` counts and `excluded_ticks`), and F47 and F48 (`due_tick`, zero-baseline recovery).

Recorded tapes still replay, because replay re-applies the recorded validations. Comparisons with older runs should account for these changes.

## Verification results (final)

- `ruff check .`: clean.
- `python -m pytest`: **769 passed, 1 skipped** (baseline 672 passed, 1 skipped). Of the 97 new tests, almost all live in `tests/*/test_audit_regressions.py`, plus one campaign-level test in `tests/society/test_economy_campaign.py`. Each was confirmed to fail on the pre-fix source.
- **Existing tests changed (3):**
  - `tests/test_cli.py`: the stale-message assertion now checks the help output.
  - `tests/experiments/test_g5_close.py::test_b2_planted_key_material_fails_leakage`: now asserts the finding locates the leak **without** repeating the key. This is stricter.
  - `tests/analysis/test_outcome_metrics.py::test_completion_rate_well_formed`: adds the new `ledger_tick` field to the pinned key set.
  - No test was deleted or skipped, and no assertion was weakened.
- `mypy`: 66 errors (baseline 73). All were triaged as typing-only (R7).
- Every shipped scenario YAML still passes `build_t0_state`.
- The key-material patterns produce 0 hits over 398 real run artifacts plus the bundled plotly report.

## Audit passes and convergence

| Pass | Scope | New actionable findings |
|---|---|---|
| 1 | 4 area reviewers + mypy triage | ~30 (1 critical, 10 high) |
| 2 | Diff review + lightly covered areas | 2 regressions + 9 new (4 high, all measurement-instrument; R1/R2) |
| 3 | Diff review + full read of pattern-scanned files | 3 regressions + 4 new (1 high: F34) |
| 4 | Diff review + end-to-end data-flow sweep | 3 regressions + 6 new (2 high: F40, R14) |
| 5 | Diff review + tick-unit / metrics sweep | 2 low regressions + 3 medium |
| 6 | Diff review + edge-input / scenario-validation probing | 2 low/medium refinements + 5 medium/low (no high) |

**The stopping criterion was not formally met.** That criterion is two consecutive independent passes with no new actionable findings, and pass 6 still found medium-severity input-validation issues. All of them were fixed in this session.

Severity has fallen with each pass. No unresolved critical issue remains. The only unresolved high-severity items are R1, R2 and R14. Each is a measurement-instrument or preregistration decision that needs the research owner and is documented above with evidence.

## Continuation instructions

1. Decide R1, R2 and R14 with the research owner: the probe-bank axis and polarity, and the preregistered recovery window versus the shipped scenario. Bump `instrument_version` if the probe bank changes.
2. Run two more independent passes. Suggested angles that have not been tried yet:
   - The adapters (`adapters/concordia*.py`) under real Concordia objects.
   - `html_report.py` with large runs (size and performance).
   - Concurrency soak (`tests/agents/test_client_soak.py`) at higher fan-out.
   - The g5 pipeline end to end on a real `runs/` root.
3. Optional hygiene, with no behaviour change:
   - Fix the 66 mypy annotations and add mypy to CI.
   - Make the PyPI workflow test the built wheel (R12).
   - Do a separate `ruff format` commit (R13).
4. All work is uncommitted on branch `ci/pypi-release` (37 files changed). Split it into reviewable commits (world/governance, society, analysis/measurement, experiments/CLI, tests) on a fresh branch before opening a PR.
