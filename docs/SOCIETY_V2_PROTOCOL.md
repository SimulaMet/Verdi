# Verdi Society V2 Protocol (frozen, Stage A)

Version: 1.0 (frozen before any Society V2 live outcome)
Status: FROZEN — new experimental generation. Owner: integrator.

## 0. Status relative to prior frozen work

This document is a **NEW experimental generation (Society V2)**. It does NOT
supersede, amend, or reinterpret ADR-010 … ADR-014
(`state/DECISIONS.md`), the frozen preregistration
(`docs/PREREGISTRATION.md` v1.2), or the prior frozen campaign protocol
(`docs/LIVE_SOCIETY_CAMPAIGN.md`, protocol v0.1). The prior L1/G5 live runs
remain **archived infrastructure tests**: they are retained as evidence that
the pipeline (endpoint, client, reducer, audit bridge, judge, replay) works,
but they are NOT evidence of an evolving society, because those runs used
non-mutating action kinds, scripted governance votes, and static prompts.
Nothing in this document invalidates prior ADR results or their reporting.

Society V2 exists to fix three specific gaps in the prior runs:

1. **State-mutating proposals.** Agents must actually change world state.
   The required kinds already exist in
   `WorldReducer.MUTATING_KINDS`
   (`src/verdi/world/reducer.py:39`): `transfer`, `deposit`, `withdraw`,
   `produce`, `emit_signal`. A run with zero accepted mutating actions is a
   failed Stage A run, reported as such.
2. **Prompt-observable feedback.** A later agent's prompt must depend on
   actually-committed prior events. If an earlier action's outcome is
   mutated (in a replay/mutation test), later observations must change.
   Static prompts that ignore committed state are a protocol violation.
3. **Real model-voted constitutional assembly.** At t10 the assembly
   deliberates and votes with live model calls. No scripted for/against
   outcome may be counted as an autonomous vote; an honest `no_change`
   outcome (proposals rejected, abstentions, or no proposals) is a valid
   and fully reported result.

Model policy (absolute, per `state/DECISIONS.md` operator policy): ONLY
Qwen3.8-27B on the SimulaChat endpoint (`VERDI_VLLM_BASE_URL` /
`VERDI_VLLM_MODEL` / `VERDI_VLLM_API_KEY`, env-only, `src/verdi/config.py:21-32`)
may be used for any live call. Endpoint smoke (4-way concurrent calls in
~0.7 s, no 429s) is confirmed and recorded in the run manifest; it is an
infrastructure fact, not a throughput guarantee.

## 1. Frozen Stage A temporal protocol (6 agents, 12 ticks, 1 assembly)

Stage A is a smoke society: **6 agents, 12 ticks (t0–t11), one real
constitutional assembly at t10**. Lifecycle ticks are the
scheduling/measurement axis and live in event payloads (`payload.tick`),
per ADR-008; `WorldState.tick` remains the cumulative transition counter.

### 1.1 Exact probe/snapshot schedule

Probes are out-of-band (never written to agent-visible memory; guard
`assert_no_probe_leak`, `src/verdi/pilot/driver.py:185`, run over every
agent-visible surface per
`_assert_no_probe_leak_over_surfaces`, `src/verdi/pilot/driver.py:209`).
The three channels — `declared`, `private`, `public`
(`CHANNELS`, `src/verdi/measurement/probes.py:21`) — are drawn from the
versioned `ProbeBank` (`ProbeBank.draw`,
`src/verdi/measurement/probes.py:118`) and are **recorded, scored, and
reported as separate channels. They are NEVER pooled into a single
"value drift" score** (per `docs/PREREGISTRATION.md` secondary estimands).

| Tick | Phase | Model calls | Probes | Snapshot / checkpoint |
|------|-------|-------------|--------|----------------------|
| t0 | Initialize 6-agent society (roles, personas, resource endowments, rights all-full). Hidden **baseline probes before any social exposure**: 1 item per agent per channel (declared/private/public). | probe only | t0 baseline, all 3 channels | `snapshot: t0` (genesis), checkpoint **CP0** |
| t1 | State-mutating jobs: cross-agent and self tasks; proposals must use mutating kinds. | 1 task per eligible agent (cap 6) | — | snapshot |
| t2 | State-mutating jobs + first social feedback (committed t1 events enter later prompts). | cap 6 | — | snapshot |
| t3 | State-mutating jobs; `emit_signal` expected to appear. | cap 6 | — | snapshot |
| t4 | State-mutating jobs; recurrent memory summaries active (see §2, lane C). | cap 6 | snapshot | — |
| t5 | **Pre-reflection probe (BEFORE self-reflection)**, all 3 channels; then each agent performs a live-model self-reflection (reflection is a model call, recorded in `model_calls.jsonl`, written to private memory only); optional re-probe (declared channel only) recorded as a separate post-reflection draw. | probe + reflection (+ optional re-probe) | t5 pre-reflection, all 3 channels; optional t5 post | `snapshot: t5`, checkpoint **CP1** |
| t6 | Interactions: memory + social feedback on committed t1–t5 events. | cap 6 | — | snapshot |
| t7 | Interactions; adversarial opportunity exists in the task tape (see §6). | cap 6 | — | snapshot |
| t8 | Interactions. | cap 6 | — | snapshot |
| t9 | Interactions + **pre-meeting measurement**: all 3 channels for all agents (the pre-amendment measurement baseline). | cap 6 + probe | t9 pre-meeting, all 3 channels | `snapshot: t9`, checkpoint **CP2** |
| t10 | **Real constitutional assembly** (below). | deliberation + votes + adjudication | — | `snapshot: t10`, checkpoint **CP3** (assembly) |
| t11 | Post-assembly tick: **immediate post-t10 probe**, all 3 channels (post-amendment baseline); 1 round of tasks under the new (or unchanged) constitution version. | cap 6 + probe | t11 post, all 3 channels | `snapshot: t11` terminal, checkpoint **CP4** (completion) |

Probe totals for Stage A: t0 (6×3), t5 pre (6×3) + optional t5 re-probe
(6×1), t9 (6×3), t11 (6×3) = 54 mandatory + 6 optional probe items.
Probe bank version, instrument version, and rotation seed are frozen in
`manifest.json` before t0 and never changed mid-run.

### 1.2 The t10 assembly (real, model-voted)

1. **Call to assembly**: deterministic — every agent with `rights.voting`
   true is eligible (`ConstitutionStore.set_eligible_voters`,
   `src/verdi/governance/constitution.py:91`). No model decides who may
   vote.
2. **Deliberation**: each eligible agent, given its own observation
   channel summary plus the public event log of t1–t9 (committed events
   only), makes a live-model call producing either
   (a) a structured `Proposal` submission (rules payload, justification)
   via `GovernanceEngine.propose`
   (`src/verdi/governance/engine.py:104`), or
   (b) an explicit `no_proposal` statement. Both are recorded.
3. **Voting**: for each proposal, each eligible agent casts a live-model
   `VoteRecord` (for/against/abstain) via
   `GovernanceEngine.cast_vote` (`src/verdi/governance/engine.py:117`);
   duplicate votes are rejected deterministically
   (`src/verdi/governance/constitution.py:14`).
4. **Enactment**: `GovernanceEngine.enact`
   (`src/verdi/governance/engine.py:133`) →
   `ConstitutionStore.enact_if_valid`
   (`src/verdi/governance/constitution.py:161`) applies deterministic
   quorum/threshold/consistency checks. Outcomes, all fully valid:
   amendment enacted (new constitution version), amendment rejected,
   or **no-change** (no valid proposal passed). A scripted or
   human-forced vote is a protocol violation and voids the run.
5. **Adjudication**: if committed t1–t10 evidence supports it, the
   assembly may also pass sanction/appeal agenda items through
   `SanctionEngine` (`src/verdi/governance/sanctions.py:85`) and
   `AppealEngine` (`src/verdi/governance/appeals.py:36`); every
   governance transition is written to `constitution_history.jsonl` and
   the world ledger (`GOVERNANCE_EVENT_KINDS`,
   `src/verdi/governance/engine.py:37`).

**Assembly lifecycle — one Assembly instance per meeting.** An
`Assembly` object is designed for *one* deliberation+voting run: its
`_proposal_order`, `_vote_attempt`, and `quarantined` bookkeeping must
not leak across meetings. The campaign therefore constructs a **fresh
`Assembly(governance)` for each constitutional meeting**
(`src/verdi/society/campaign.py`). Reusing a single instance causes each
assembly to re-enact every prior proposal, which then fails as
`retroactive_amendment` and pollutes the ledger with spurious
rejections. The shared `GovernanceEngine` (and its constitution
state) is what persists across meetings — not the Assembly wrapper.

**Mixed assemblies must ledger every proposal.** An assembly may enact
one proposal while rejecting another (mixed outcome). The campaign must
record the ledger + SimpleAudit tape step for **every** proposal whose
`enact` returned a rejection, regardless of whether a sibling was
enacted; only genuinely-accepted proposals skip the rejection branch
(duplication guard). Silently dropping rejected siblings makes the
audit tape incomplete and the assembly's own commentary claim a
"clean enactment" that the world does not reflect.

### 1.3 Deterministic commit-order rule (frozen)

Stage A uses **concurrent-generation / sequential-commit** mode. This mode
MUST be declared in `manifest.json` (field `commit_mode:
"concurrent-generation-sequential-commit"`). The alternative
(`sequential-epochs`) is declared but not used in Stage A.

Per lifecycle tick `t`, exactly:

1. **Freeze**: take the start-of-tick world snapshot `S_t` (immutable
   `WorldState`, `src/verdi/schemas/world.py:39`) and record
   `canonical_state_hash(S_t)`
   (`src/verdi/world/reducer.py:95`). All agents perceive ONLY `S_t` plus
   their own private memory version as of the tick start. No agent sees
   another agent's concurrent proposal.
2. **Concurrent generation**: all eligible agents' `AgentPolicy.propose`
   calls (`src/verdi/agents/policy.py:136`) run concurrently under the
   bounded actor pool, each producing a `ProposedAction`
   (`src/verdi/schemas/actions.py:21`) or a quarantine reason. Generation
   order is nondeterministic and is recorded (start/end timestamps,
   attempt IDs) but has NO semantic meaning.
3. **Fixed deterministic commit order**: proposals are sorted by
   `(task.task_id, actor_id)` — both strings, lexically ascending, UTF-8
   — and committed one at a time. For each proposal in order:
   deterministic validation (rights, law, resources, consent, quotas,
   conflicts; outcome as `ValidationResult`,
   `src/verdi/schemas/actions.py:36`) against the *current* committed
   state, then `WorldReducer.apply` (`src/verdi/world/reducer.py:49`),
   then `EventLog.append` (`src/verdi/world/log.py:75`). A later proposal
   in the same tick therefore sees the state effects of earlier ones in
   the sort order — this is intended and is the source of deterministic
   conflict outcomes (e.g., double-spend rejected against the first
   spender).
4. **Rejected attempts are events**: every proposal, accepted or
   rejected, produces a ledger event
   (`WorldEvent`, `src/verdi/schemas/events.py:13`); rejection carries
   `attempted_violation_codes` and is never silently discarded.
5. **Replay determinism**: given the recorded tape
   (`model_calls.jsonl`), the tick reproduces byte-identical events and
   state hashes, because the tape carries the full commit-ordered
   sequence. Commit order is a function of `(task_id, actor_id)` only;
   it never depends on wall-clock completion order.

Invariants: no concurrent writes reach the reducer (single-writer per
tick); no agent reads state after its own tick-start snapshot
(no intra-tick future leakage); `S_t` never contains an event with
`event_id` committed after the tick start.

### 1.4 Sanction and appeal world-state semantics

* An accepted sanction deterministically flips the subject's
  `under_sanction` flag in the world state (the same tick as the
  sanction event) and is recorded on the tape as a `kind="rights_change"`
  step so replay reproduces the flag change. While flagged, the agent is
  suspended from the tape (no task generation) and the deterministic
  validator blocks `transfer`/`deposit` actions with code
  `under_sanction`.
* A sanction **does not** file and adjudicate its appeal in the same
  tick. The appeal is filed at `violation_tick + 2` and adjudicated
  against a pre-registered deadline (`violation_tick + 4`); an upheld
  appeal flips the flag back off and is likewise recorded as a
  `rights_change` step. Rights restored by duration expiry
  (`GovernanceEngine.advance_tick`) are flipped off at the top of the
  affected tick.
* Commit-loop validation runs against the **current** committed state,
  not the tick-start snapshot, so a later proposal in the same tick sees
  the resource effects of earlier ones (double-spend rejected as
  `insufficient_world` without ever corrupting state).
* A `deposit` is validated against the **agent's held balance** (not just
  the world pool): an over-deposit — depositing more of a resource than the
  agent currently holds — is rejected with `law:no_double_spend` and code
  `overdraft`, mirroring the `transfer` held-balance check. Without this the
  reducer's final non-negative assertion would trip (`InvariantViolationError`),
  which is what crashed the live Stage B seed-1 (fix1) run at tick 230 when
  agent `b01` was accepted into a 2.0-credit over-deposit while holding 0.0.
  The same code is substantive, so an over-deposit can be sanctioned.
* Sanctions fire **only on substantive violation codes**
  (`overdraft`, `insufficient_world`, `unknown_recipient`). A proposal
  that fails for format or argument reasons (malformed JSON, unknown or
  missing argument keys, quarantined parse) is rejected by the
  validator and logged, but is NOT attributed as a violation and
  generates no sanction. This separates world-law breaches from
  communication-format failures.
* The sanctioned scope recorded by the governance engine is
  `["speaking"]` — the engine's audit record of the restricted right —
  while the deterministic world-state effect is tape suspension plus
  validator blocking of `transfer`/`deposit`. The scope string and the
  flag are mirrored, not equivalent: the former is the engine's
  audit-trail label, the latter is what actually constrains the agent.
* A deterministically REJECTED constitutional proposal (invalid law
  invariant, quorum/threshold not met, stale base version) is recorded
  as a governance tape step and ledger event with `decision="rejected"`
  and the engine's `invalid_reasons`, per rejected proposal. The
  reducer's governance branch applies rule-version bookkeeping only on
  acceptance, so replay from the tape reproduces the live ledger
  exactly, including rejections.

## 2. Interface-contract appendix (6 fleet lanes)

Contract only: signatures + invariants, no implementation. `[TO BUILD —
lane X]` marks components that do not yet exist. Owner boundaries follow
`docs/LIVE_SOCIETY_CAMPAIGN.md` fleet-lane section and
`docs/ARCHITECTURE.md` (parallel ownership). **Disjoint path ownership —
a lane never edits a path outside its own list.**

### Lane W — World (state, mutation, observation feedback)

Owns: `src/verdi/world/**`, `src/verdi/schemas/world.py` (read-only
bumps only via integrator), `tests/world/**`.

Consumes: `WorldState`, `ProposedAction`, `ValidationResult`
(`src/verdi/schemas/`).

Produces (existing):

```python
class WorldReducer:
    MUTATING_KINDS: frozenset[str]  # transfer, deposit, withdraw, produce, emit_signal
    def apply(self, state: WorldState, action: ProposedAction,
              validation: ValidationResult) -> WorldState: ...
    def build_event(self, before: WorldState, after: WorldState,
                    action: ProposedAction, validation: ValidationResult,
                    prev_hash: str) -> WorldEvent: ...
    @staticmethod
    def canonical_state_hash(state: WorldState) -> str: ...

class EventLog:
    def append(self, event: WorldEvent) -> WorldEvent: ...
    def verify(self) -> None: ...
    def load_jsonl(cls, path) -> EventLog: ...
```

New (to be built by lane W):

```python
# [TO BUILD — lane W] src/verdi/world/observation.py
def agent_observation(state: WorldState, agent_id: str,
                      visible_event_ids: frozenset[int]) -> AgentObservation: ...
class AgentObservation(FrozenModel):
    agent_id: str
    tick: int
    resources: dict[str, float]          # own holdings, never others'
    public_events: list[dict]            # committed, in-world-visible, tick-ordered
    public_signals: list[str]            # others' emit_signal payloads
    rights: RightsState
```

Invariants: only the reducer mutates state; observation is a pure
function of committed state (this is what makes the §3 feedback test
possible); `emit_signal` payloads are the only cross-agent information
channel; rejected attempts change no substantive content
(`src/verdi/world/reducer.py:14` docstring).

### Lane C1 — Model client / concurrency

Owns: `src/verdi/agents/**`, `tests/agents/**`.

Consumes: `ModelEndpointConfig` (`src/verdi/config.py:21-32`), messages.

Produces (existing):

```python
class ModelClient:  # src/verdi/agents/client.py:54
    async def generate_structured(self, messages: list[dict[str, str]], *,
                                  response_format_json: bool = False,
                                  temperature: float = 0.0,
                                  max_tokens: int | None = None) -> ModelResult: ...
class ModelResult:  # src/verdi/agents/client.py:37
    content: str; request_id: str; model: str; trace_id: str
    usage: dict[str, Any]; reasoning_content: str
class TaskPool:     # src/verdi/agents/pool.py:18
    def __init__(self, max_concurrency: int) -> None: ...
    async def run_all(self, worker: Callable[[Task], Awaitable[PolicyResult]],
                      tasks: list[Task]) -> dict[str, PolicyResult]: ...
```

New (to be built by lane C1):

```python
# [TO BUILD — lane C1] src/verdi/agents/throttle.py
class AdaptiveThrottle:
    """Bootstrap 4; step 8/16/32 only when stable; ceiling = VERDI_MAX_CONCURRENCY."""
    def __init__(self, bootstrap: int = 4, ceiling: int,
                 step_rule: StepRule) -> None: ...
    async def acquire(self, pool: PoolKind) -> Token: ...   # PoolKind = ACTOR | JUDGE
    def report(self, attempt: AttemptRecord) -> ThrottleDecision: ...
    def snapshot(self) -> ThrottleState: ...   # -> metrics.json / health.json
class AttemptRecord(FrozenModel):
    attempt_id: str        # idempotent: sha256(run_id|agent|task|tick|retry_n)
    pool: PoolKind; status_code: int | None; latency_ms: float
    input_tokens: int; output_tokens: int; error_class: str | None
```

Invariants: `VERDI_MAX_CONCURRENCY` is a ceiling, never a permission to
saturate the endpoint; retries for 429/502/503 only
(`RETRYABLE_STATUS`, `src/verdi/agents/client.py:22`) with counted HTTP
attempts and capped backoff; one persistent async loop and pooled client
per process; judge pool is separate from the actor pool with its own
capacity (see §4); every call appends one row to `model_calls.jsonl`
before the process continues (durable log).

### Lane C2 — Scheduler / society / memory

Owns: `src/verdi/scheduler/**`, new `src/verdi/society/**`,
`tests/scheduler/**`, `tests/society/**`.

Consumes: `Task`, `WorldState`, `AgentObservation` (lane W),
`ProbeResult` only via public channel (never probe internals).

Produces (existing):

```python
class SeededScheduler:   # src/verdi/scheduler/seeded.py:75
    def schedule(self, tasks: list[Task], world: WorldState) -> Schedule: ...
def is_eligible(task: Task, world: WorldState) -> tuple[bool, str]: ...
def fairness_stats(assignments: list[TaskAssignment]) -> dict[str, int]: ...
```

New (to be built by lane C2):

```python
# [TO BUILD — lane C2] src/verdi/society/tasks.py
def generate_tick_tape(tick: int, state: WorldState, rng: random.Random,
                       tape_seed: int) -> list[Task]: ...
"""Seeded task tape: ~1 mutating task per eligible agent; resource/transfer
targets, produce jobs, emit_signal opportunities; adversary (one agent,
Stage A) gets an objectively worse-opportunity-matched persona objective,
not an audit label."""

# [TO BUILD — lane C2] src/verdi/society/memory.py
class AgentMemory:
    """Private, per-agent, versioned; compaction + contamination guards."""
    def append_observations(self, agent_id: str, obs: AgentObservation) -> int: ...
    def append_reflection(self, agent_id: str, reflection: str) -> int: ...
    def render_history_summary(self, agent_id: str) -> str: ...
    def version(self, agent_id: str) -> int: ...
    def to_checkpoint(self, agent_id: str) -> bytes: ...
    @classmethod
    def from_checkpoint(cls, blob: bytes) -> AgentMemory: ...

# [TO BUILD — lane C2] src/verdi/society/prompting.py
def build_social_prompt(agent_id: str, task: Task, state: WorldState,
                        memory: AgentMemory, prompt_version: str) -> str: ...
"""FROZEN REQUIREMENT: render_history_summary() output (which is derived
from committed events) MUST appear in the prompt. Static prompts that
ignore committed state are a protocol violation (§0, gap 2)."""
```

Invariants: scheduler is seeded (baseline control per
`docs/LIVE_SOCIETY_CAMPAIGN.md`); the in-world orchestrator has NO
privileged access to hidden-value probes or outcomes and cannot schedule
diagnostic opportunities using hidden labels; memory is private (never
rendered into another agent's prompt or public signal); compaction may
lose detail but never rewrites committed history.

**Prompt-observable feedback test (acceptance-critical, [TO BUILD —
lane C2 + W] as `tests/society/test_feedback.py`):** drive a two-tick
mini-world; record agent B's tick-2 prompt after agent A's tick-1 action
`transfer`; replay with A's action mutated (rejected instead of
accepted, or different amount); assert B's tick-2 prompt string differs
in the committed-state portion. A run failing this test has no social
feedback and Stage A is failed.

### Lane D — Governance

Owns: `src/verdi/governance/**`, `tests/governance/**`.

Consumes: `LawVersion`/`ConstitutionVersion`
(`src/verdi/schemas/governance.py:13,23`), `VoteRecord`
(`src/verdi/schemas/governance.py:47`), committed events (evidence refs
only).

Produces (existing):

```python
class GovernanceEngine:  # src/verdi/governance/engine.py:55
    def propose(self, proposal_id: str, submitted_by: str, tick: int, ...) -> dict: ...
    def cast_vote(self, vote: VoteRecord) -> dict: ...
    def enact(self, votes: Sequence[VoteRecord], tick: int, ...) -> dict: ...
    def impose_sanction(self, sanction: Sanction) -> dict: ...
    def file_appeal(self, appeal: Appeal) -> dict: ...
    def adjudicate_appeal(self, appeal_id: str, upheld: bool,
                          grounds: str, ...) -> dict: ...
class ConstitutionStore:  # src/verdi/governance/constitution.py:42
    def set_eligible_voters(self, agent_ids: Sequence[str]) -> None: ...
    def record_vote(self, vote: VoteRecord) -> VoteRecord: ...
    def enact_if_valid(self, votes, tick: int, ...) -> ...: ...
class SanctionEngine:  # src/verdi/governance/sanctions.py:85
class AppealEngine:     # src/verdi/governance/appeals.py:36
```

New (to be built by lane D):

```python
# [TO BUILD — lane D] src/verdi/governance/assembled.py
class Assembly:
    """The t10 real-assembly driver. Live model calls for deliberation and
    votes; every vote must trace to a model_calls.jsonl attempt_id."""
    def run_deliberation(self, state: WorldState, caller: ModelCaller,
                         context: list[dict[str, Any]]) -> list[ProposalDraft]: ...
    def run_voting(self, proposals: list[ProposalDraft], caller: ModelCaller,
                   voters: frozenset[str]) -> list[VoteRecord]: ...
    def finalize(self, engine: GovernanceEngine, votes: list[VoteRecord],
                 tick: int) -> AssemblyOutcome: ...
class AssemblyOutcome(FrozenModel):
    outcome: Literal["amendment_enacted", "amendment_rejected", "no_change"]
    enacted_version: str | None
    proposals: list[str]; votes: list[str]   # attempt_id trace refs
    abstentions: int
```

Invariants: model cannot execute governance directly — it produces
structured proposals/votes that the deterministic engine validates
(`docs/ARCHITECTURE.md` nonnegotiable invariants); quorum/threshold
checks are deterministic and immutable-law-outranks-constitution; no
scripted outcomes (a scripted vote voids the run); honest
preservation of failures, abstentions, and no-change.

### Lane E — Measurement / audit

Owns: `src/verdi/measurement/**`, `src/verdi/audit/**`,
`src/verdi/analysis/**`, `tests/measurement/**`, `tests/audit/**`.

Consumes: `ProbeItem`/`ProbeResult` (`src/verdi/schemas/probes.py:18,31`),
`WorldEvent`, `model_calls.jsonl` rows, judge pool (separate from actor
pool, §4).

Produces (existing):

```python
class ProbeBank:     # src/verdi/measurement/probes.py:53
    def draw(self, agent_id: str, tick: int, channel: str, *,
             rng_seed: int) -> ProbeItem: ...
    def leak_check(self, agent_visible_text: str) -> list[str]: ...
def score_probe(item: ProbeItem, response: str, ...) -> tuple: ...  # src/verdi/measurement/scoring.py:83
class SimpleAuditBridge / live judge:  # src/verdi/audit/simpleaudit_bridge.py, judge_live.py
```

New (to be built by lane E):

```python
# [TO BUILD — lane E] src/verdi/measurement/longitudinal.py
class ProbeRecorder:
    """Writes probes.jsonl; one row per (agent, tick, channel, draw_n)."""
    def record(self, state: WorldState, tick: int, phase: str,
               channels: tuple[str, ...], caller: ModelCaller) -> list[ProbeResult]: ...
    """Live probe responses are real model calls (judge pool), out-of-band:
    probe prompts are never in agent memory, task observations, or
    public_signals; leak_check runs over every agent-visible surface
    (src/verdi/pilot/driver.py:209) before each tick."""
def channel_trajectories(probes: list[ProbeResult]) -> dict[str, list[ProbePoint]]: ...
"""Returns per-channel trajectories. There is deliberately NO pooled
cross-channel score in the Stage A API surface."""
```

Invariants: judges are advisors, never sole proof; the same-family
Qwen judge is labeled `same_family: true` on every judgment; no
cross-family independence is claimed (cross-family pass remains
deferred per ADR-012 A1 deferral); audit labels are never visible to
agents; replay from recorded responses reproduces scores exactly.

### Lane F — Driver / reporting

Owns: `src/verdi/pilot/**` (extension), new
`src/verdi/society/campaign.py`, `experiments/**`, integration tests,
run-directory reporting, `docs/SOCIETY_V2_PROTOCOL.md` (this file,
amendments only via new version + ADR addendum).

Consumes: everything above; `ExperimentManifest`
(`src/verdi/schemas/manifest.py:11`).

Produces (existing): `PilotDriver`/`run_pilot`
(`src/verdi/pilot/driver.py:281,887`), `run_live_pilot` /
`TapeReplayCaller` (`src/verdi/pilot/live.py:203,114`).

New (to be built by lane F):

```python
# [TO BUILD — lane F] src/verdi/society/campaign.py
class SocietyCampaign:
    def __init__(self, manifest: ExperimentManifest, run_dir: Path) -> None: ...
    async def run_stage_a(self) -> CampaignRun: ...
    def checkpoint(self, label: str) -> None: ...   # atomic, resumable
    @classmethod
    def resume(cls, run_dir: Path) -> SocietyCampaign: ...
class CampaignRun(FrozenModel):
    status: Literal["completed", "blocked_endpoint", "aborted", "failed"]
    gates: dict[str, Literal["pass", "fail", "blocked", "pending"]]
    evidence: dict[str, str]   # artifact path per gate
"""Manifest MUST include: commit_mode (§1.3), probe_bank_version,
instrument_version, prompt_version (supersedes
PROMPT_VERSION "verdi.agent-policy-prompt/1" for society prompts,
src/verdi/agents/policy.py:20 — the new social prompt gets its own
version constant), model id + sampling, run_seed, tape seed,
git_sha, throttle history summary."""
```

Invariants: manifest is sealed at t0 (write-once); no raw private tapes,
API keys, or unpublished individual traces are ever committed to Git
(run dirs are local/gitignored); `state/STATUS.md` is updated by the
integrator only after real completed-stage evidence.

## 3. Prompt-observable feedback (acceptance-critical)

- Every social prompt (lane C2 `build_social_prompt`) embeds the agent's
  `render_history_summary()`, which is derived from committed events
  only.
- Mandatory test (§2, lane C2+W): mutate an earlier committed action in a
  replay and assert a later agent's rendered prompt changes. Run before
  any live Stage A; rerun after any lane C2/W change.
- Runtime guard: if two agents' tick-t prompts are identical in their
  committed-state section despite different preceding committed events,
  the run FAILS and is reported as a feedback failure (not silently
  continued).

## 4. Concurrency plan (live endpoint)

Ground truth: SimulaChat default Qwen3.8-27B is live and free-tier
unlimited in quota but NOT infinite in throughput; smoke confirmed 4-way
concurrent calls in ~0.7 s with no 429s.

1. **Bootstrap**: concurrency 4 for the actor pool; judge pool starts
   separately at 2 (tunable, own semaphore) so judge work never steals
   actor capacity.
2. **Adaptive stepping to 8 / 16 / 32 only when stable**: after a full
   tick of successful calls at the current level, the throttle may step
   up iff in the measurement window: zero new 429s, zero 5xx, p95
   latency not degraded > 50% vs the prior level, tokens/s trending
   flat-or-up, and process memory within budget. One step per tick;
   step down immediately on any 429/5xx spike or timeout cluster.
3. **`VERDI_MAX_CONCURRENCY` (`src/verdi/config.py:30`) is a CEILING**:
   the effective concurrency is `min(adaptive_level, ceiling)`; the
   environment variable never grants permission to saturate the server.
4. **Mechanism**: one persistent async event loop per process; bounded
   `asyncio.Semaphore` per pool (actor, judge) with backpressure —
   proposal generation awaits a token rather than enqueueing unbounded
   work; retries only for 429/502/503
   (`src/verdi/agents/client.py:22`) with capped exponential backoff and
   every HTTP attempt counted.
5. **Idempotent attempt IDs**: `attempt_id = sha256(run_id|agent_id|
   task_id|tick|retry_n)`; the run tolerates redelivery — a repeated
   `attempt_id` is a no-op for world commits (the reducer is only ever
   fed once per task in commit order) and the duplicate is logged.
6. **Durable logs**: `model_calls.jsonl` and `events.jsonl` are
   append-only, flushed per row; a crash mid-tick resumes from the last
   checkpoint (§5) and never replays a committed task.
7. **Isolation**: independent replicates/branches run as separate
   processes with their own pools; no shared reducer writes across
   processes; no intra-tick future-state leakage (§1.3).
8. **Metrics**: per stage, record token usage, throughput (tokens/s),
   429/5xx/timeout counts, latency distribution, and wall clock in
   `metrics.json` and `health.json`; the throttle history
   (`ThrottleState.snapshot`) is part of `health.json`.

## 5. Observable progress (run directory, local + gitignored)

Required artifacts in each run directory:

| Artifact | Content |
|----------|---------|
| `manifest.json` | Sealed `ExperimentManifest` + Stage A additions (`commit_mode`, seeds, prompt/probe/instrument versions, throttle bootstrap config). Write-once at t0. |
| `events.jsonl` | Full ledger: every accepted AND rejected attempt, governance events. Hash-chained (`EventLog`, `src/verdi/world/log.py:30`). |
| `model_calls.jsonl` | One row per live call: attempt_id, pool, agent/task, tick, model, temperature, in/out tokens, latency, status, trace_id, response. Sole replay source. |
| `snapshots/` | `t0.json` … `t11.json` frozen `WorldState` + `state_hash` per tick. |
| `probes.jsonl` | All probe draws/responses/scores with channel, bank/instrument version, draw_n; channels never pooled. |
| `constitution_history.jsonl` | Proposals, votes (with attempt_id trace), enactment/rejection/no-change, sanctions, appeals, restorations, version chain. |
| `metrics.json` | Per-tick and per-stage: accepted/rejected counts, per-kind mutation counts, fairness stats, throughput, token totals, latency, wall clock. |
| `health.json` | Throttle history, 429/5xx/timeout counters, memory, pool occupancy, last successful tick. |
| `timeline.md` | Human-readable per-tick narrative: tasks, accepted mutations, signal emissions, probe summary (per channel), governance outcome, errors. |
| `checkpoint.json` | See below. Plus an error ledger section (or `errors.jsonl`): every failure with class, attempt_id, retry_n, root-cause note. |

**Checkpoint points (atomic + resumable): CP0 t0 (post-probes),
CP1 t5, CP2 t9 (pre-meeting), CP3 t10 (assembly complete), CP4
completion (t11 post-probe). Every future assembly (Stages B/C) is an
additional checkpoint point.** `checkpoint.json` carries: code SHA,
world hash (`canonical_state_hash`), event count, per-agent last
activity, model id + config (sampling, endpoint alias — no secret),
throughput, gate status, active stage, next actions, and the last
verified commit mode. Resume restores reducer, event log, memory, and
throttle state from the checkpoint; never from wall-clock state.

Console: one human-readable status line per tick (agents acted,
mutations, rejections, probe phase, throttle level, errors).

## 6. Gates, acceptance, recovery, abort

### 6.1 Stage A gates (G0–G3)

- **G0 (pre-flight, before any live call)**: contracts frozen (this
  file + manifest schema); 6 agents × 12 ticks × 1 assembly specified;
  lane ownership disjoint; reuse/licensing assessed (reuse-first,
  `docs/ARCHITECTURE.md`); CI green on Linux + local pytest/ruff (the
  prior CI failure on scenario paths in `docs/LIVE_SOCIETY_CAMPAIGN.md`
  is fixed); endpoint smoke re-verified live (recorded in manifest).
- **G1**: `python -m pytest` + `ruff check .` pass, including:
  malformed/invalid mutating actions quarantined
  (`parse_and_validate_action`, `src/verdi/agents/policy.py:75`);
  zero-resource and double-spend rejections raise
  `InvariantViolationError` instead of corrupting state
  (`src/verdi/world/reducer.py:46`); the §3 prompt-observable feedback
  test passes; probe leak check passes over all agent-visible surfaces
  (`src/verdi/pilot/driver.py:209`).
- **G2 (offline mocked)**: full Stage A offline run produces all
  artifacts in §5 with a mocked caller; ≥ 1 accepted state-changing
  action; a visibly changed future task/decision caused by that action;
  1 human-readable interaction transcript; a governance outcome
  (enacted, rejected, or honest no-change) — offline vote may be mocked,
  but the LIVE run requires real votes.
- **G2-live**: G2 with the real endpoint at concurrency 4; all model
  calls traced in `model_calls.jsonl`; `TapeReplayCaller`
  (`src/verdi/pilot/live.py:114`) replay of the recorded tape
  reproduces events and state hashes byte-identically.
- **G3**: attempted violations logged even when rejected; no
  unconstitutional world mutation; vote/quorum edge cases (duplicate
  vote, abstention majority, empty proposal set → no-change); appeal +
  restoration path exercised; replay produces equal state hashes and
  events; no probe leakage; no intra-tick future leakage (commit-order
  test); **≥ 1 accepted action from `MUTATING_KINDS`** and ≥ 1
  `emit_signal` visible to another agent's later prompt.

### 6.2 Stages B/C gates (G5 + performance/validity)

Stage B (12 agents, 30 ticks, 3 assemblies) and Stage C scale-ups
proceed ONLY on recorded passing Stage A gates. G5 requirements carry
over: preregistered estimands per `docs/PREREGISTRATION.md` (any new
Society V2 estimands require a new PREREG version BEFORE live outcomes);
matched-pair comparisons with placebo inertness check; uncertainty
intervals or explicit `insufficient_sample` flag; rights/fairness and
welfare costs reported; no causal wording without randomization.
Additional performance/validity gates before each scale jump:
concurrency soak (no unexplained 429/5xx trend at the chosen level),
memory/compaction boundedness, no unexplained failed/incomplete
`model_calls.jsonl` rows, no event-log inconsistencies, no silent
denominator change, substantive state divergence when actor choices
change (a null is reported, not hidden).

### 6.3 Acceptance / recovery / abort rules

- **No fabricated success**: a gate is PASS only with linked evidence
  (artifact path + command output) in `state/STATUS.md`. Inconclusive
  is a legal state; "should work" is not a PASS.
- **Retry cap**: any fix strategy is capped at 3 retries with the same
  approach; then the approach changes or a concrete blocker is surfaced.
  No infinite loops, no retry-storms of the endpoint (client retries
  themselves are capped, `VERDI_MAX_RETRIES`, `src/verdi/config.py:32`).
- **Recovery**: on failure — preserve trace, minimize, root-cause, add
  a regression test, and restart from the last verified checkpoint
  (§5). Never restart the whole campaign discarding the trace; never
  "heal" the event log.
- **Endpoint inaccessible → live stages BLOCKED**: if the endpoint or
  credential is unavailable to the acting agent, all offline-executable
  work (offline suites, tests, artifacts, docs) continues, the live
  stage is marked `BLOCKED` in `checkpoint.json`/`STATUS.md`, and the
  work is NOT claimed to have run. `CampaignRun.status` must be
  `blocked_endpoint`, not `completed`.
- **Abort**: a protocol violation (scripted vote, pooled probe channel,
  probe leak, intra-tick leakage, log mutation) aborts the run; the run
  dir is preserved as evidence; the violation is recorded in
  `state/LESSONS.md`.
- **Branch discipline**: no commit/merge to `main` at any point; all
  Society V2 work on `dev` lineage; run artifacts stay out of Git.

## 7. Pre-outcome honesty (boundaries before any Stage A result)

- **LLM nondeterminism**: even at temperature 0, live Qwen outputs are
  not guaranteed reproducible across time; the deterministic guarantee
  is about the reducer, commit order, probes, and replay of RECORDED
  outputs — not about the model re-choosing the same action. Replay
  claims always say "on the recorded tape".
- **Same-family judge**: the judge is Qwen3.8-27B, the same family as
  the actors. It is an **advisor only**, never sufficient proof, and
  every judgment carries `same_family: true`. No cross-family
  independence is claimed; the cross-family pass remains deferred per
  ADR-012 A1 (operator to supply an endpoint later).
- **No "value drift" claims from a demo**: Stage A is a smoke society.
  Its outputs demonstrate pipeline functionality (mutation, feedback,
  real voting, measurement). Per-agent per-channel probe deltas from a
  12-tick, 6-agent run are descriptive only; they are not evidence of
  value drift, social contagion, or institutional effectiveness. Any
  such claim requires Stages B/C with preregistered estimands,
  matched comparisons, and uncertainty intervals — reviewed.
- **Observable behavior only**: probes measure declared/private/public
  value EXPRESSION, not internal beliefs. Reports distinguish task
  effects, incentive effects, and behavioral change, and never conflate
  them.
- **Simulator ≠ evidence**: passing code, tests, or gates proves the
  infrastructure works; it does not establish any experimental finding.
- **Honest nulls**: no-change assemblies, abstentions, rejected
  amendments, failed probes, and 429-limited throughput are results to
  report, not failures to hide.

## 8. Open questions (do not block Stage A; resolve via ADR addenda)

1. Post-t5 optional re-probe: keep (declared channel only) or drop for
   protocol simplicity — decide before sealing the Stage A manifest.
2. Stage A adversary: one agent with a persona-injected objective is in
   the frozen design; whether Stage A needs the appeal/restoration path
   exercised by a real (non-scripted) sanction, or by a scripted-but-
   evidence-referenced one, is left to the integrator's G3 checklist.
3. Judge pool bootstrap level (2) is a default, not frozen evidence;
   re-tune from Stage A `health.json` before Stage B.
4. Whether Stage B assemblies reuse the t10 Assembly API unchanged or
   gain multi-round deliberation — contract change would require a new
   protocol version + ADR.
