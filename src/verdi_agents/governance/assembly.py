"""Run a constitutional assembly where live model agents propose amendments and vote.

Scripted assembly votes (as in the reference run) are not allowed in live
society runs; this module COMPOSES the existing deterministic ``GovernanceEngine`` with
live model calls: the model only *proposes* amendments and *votes*; the
engine performs all deterministic validation (law invariants,
retroactivity, quorum, threshold) and is the sole writer of governance
events and constitution versions.

Modeling choice: deliberation uses ONE
structured call per eligible voter rather than a single "council" call.
Per-voter calls give every proposal a single, unambiguous author and
``attempt_id`` in ``model_calls.jsonl``, so traceability to
"who proposed what" is exact; the cost is ``n_voters`` calls instead of
one, which small societies (e.g. 6 agents, 1 assembly) easily absorb.

Honest outcomes are preserved: rejection and no-change are valid
results. This driver never overrides an engine decision.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from verdi_agents.schemas import VoteRecord, WorldState

#: Versioned assembly prompt constant. Bump deliberately; experiments log it.
ASSEMBLY_PROMPT_VERSION = "verdi.assembly-prompt/1"

_VALID_VOTE_CHOICES = frozenset({"for", "against", "abstain"})


@dataclass(frozen=True)
class ProposalDraft:
    """One structured amendment proposed by the model for a voter."""

    proposal_id: str
    submitted_by: str
    change_summary: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class _DelibCallResult:
    """Raw outcome of ONE voter's deliberation model call (no engine mutation).

    Exactly one of the three states: abstained, error (quarantine), or a
    candidate proposal (summary/payload may still fail schema checks later).
    """

    summary: Any = None
    payload: Any = None
    abstained: bool = False
    error: str | None = None


@dataclass(frozen=True)
class _VoteCallResult:
    """Raw outcome of ONE voter's vote model call (no engine mutation)."""

    choice: str = "abstain"
    reason: str | None = None


async def _run_sequential(concurrency: int, coros: list) -> list:
    """Default batch runner: await each coroutine in input order, ignoring
    ``concurrency`` (sequential is the legacy behavior).

    The society layer may inject a bounded-concurrency runner (asyncio lives
    in verdi_agents.society, never here — see
    tests/governance/test_determinism.py) to run model calls in parallel.
    Results are always returned in INPUT order so the caller's post-pass
    applies engine mutations deterministically regardless of completion
    order.
    """
    return [await coro for coro in coros]


class AssemblyOutcome(BaseModel):
    """Honest summary of a completed assembly: enacted, rejected, or no-change."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: str  # "amendment_enacted" | "amendment_rejected" | "no_change"
    enacted_version: str | None
    # Tick at which the enacted amendment takes effect (may be > tick for
    # future-dated amendments; WorldState.constitution_version must not flip
    # before this tick).
    enacted_effective_from_tick: int | None = None
    proposals: list[str]  # proposal_ids, in submitted order
    votes: list[dict[str, Any]]  # per-voter: {voter_id, proposal_id, choice, attempt_id}
    abstentions: int
    # Per-proposal engine enact event dicts, in submitted order. Lets the
    # campaign ledger every rejection, not just the last one.
    enact_events: list[dict[str, Any]] = Field(default_factory=list)
    # Full proposal texts for review/plotting: each entry has
    # proposal_id, submitted_by, change_summary, payload.
    proposal_details: list[dict[str, Any]] = Field(default_factory=list)


def _attempt_id(world_id: str, proposal_id: str | None, voter_id: str, tick: int) -> str:
    """Stable attempt id shared with model_calls.jsonl."""
    basis = f"{world_id}|assembly|{proposal_id or 'delib'}|{voter_id}|{tick}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _parse_json_object(content: str) -> dict[str, Any]:
    """Parse model output as a JSON object; raises ValueError on bad output."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid json: {exc.msg} at line {exc.lineno}") from exc
    if not isinstance(data, dict):
        raise ValueError("top-level JSON is not an object")
    return data


def _summarize_context(context: list[dict[str, Any]]) -> str:
    """Reduce committed events to (tick, actor, kind, short payload) lines."""
    lines = []
    for event in context:
        if not isinstance(event, dict):
            continue
        payload = json.dumps(event.get("payload", {}), sort_keys=True)[:200]
        lines.append(
            f"  tick={event.get('tick')} actor={event.get('actor')} "
            f"kind={event.get('kind')} payload={payload}"
        )
    return "\n".join(lines) if lines else "  (none)"


class Assembly:
    """Real-assembly driver. Live model calls for deliberation and votes;
    every vote traces to an attempt_id. Deterministic ENGINE does final
    validation (quorum/threshold/retroactivity) — the model only proposes & votes."""

    def __init__(self, engine, *, prompt_version: str = ASSEMBLY_PROMPT_VERSION) -> None:
        self._engine = engine
        self.prompt_version = prompt_version
        self._world_id: str = ""
        self._state: WorldState | None = None
        self._proposal_order: list[str] = []
        self._proposals: list[ProposalDraft] = []
        self._vote_attempt: dict[str, str] = {}
        #: Quarantine log: {phase, voter_id, attempt_id, reason} per bad completion.
        self.quarantined: list[dict[str, str]] = []
        #: Voters whose deliberation call returned an explicit abstention.
        self.deliberation_abstentions: list[str] = []
        #: Batch runner for model calls: (concurrency, coroutines) in,
        #: results in INPUT order. Sequential by default; the society layer
        #: injects a bounded concurrency runner
        #: (``verdi_agents.society.campaign._run_bounded``). Note: the
        #: ``concurrency`` argument is advisory only — the injected runner
        #: binds its own bound from the run config and may ignore the value
        #: passed here (run_batch is always invoked with 1).
        self.run_batch: Callable[[int, list], Awaitable[list]] = _run_sequential

    # -- prompts ---------------------------------------------------------------

    def _deliberation_prompt(self, voter: str, tick: int, context: list[dict[str, Any]]) -> str:
        state = self._state
        assert state is not None
        agent = state.agents[voter]
        return (
            f"[prompt:{self.prompt_version}] [assembly:deliberation]\n"
            f"You are agent \"{voter}\" in the constitutional assembly of "
            f"world \"{state.world_id}\" at tick {tick}.\n"
            f"Role: {agent.role}\n"
            f"Persona: {agent.persona_summary}\n\n"
            f"Current constitution version: {self._engine.constitution.active_at(tick).version}\n"
            f"Effective constitutional rules: {json.dumps(self._engine.constitution.active_at(tick).rules, sort_keys=True)}\n"
            "Executable rule keys: max_transfer_units (positive number), "
            "max_withdraw_units (positive number), min_commons_reserve "
            "(map of resource to nonnegative reserve). Only these keys alter "
            "world-action enforcement; other keys are narrative.\n\n"
            "Committed world events (tick, actor, kind, short payload):\n"
            f"{_summarize_context(context)}\n\n"
            "Deliberate on the society's rules. Respond with JSON ONLY, either:\n"
            "  propose at most ONE constitutional amendment: "
            "{\"change_summary\": str, \"payload\": object} — payload is a "
            "rules delta (rule keys to set; null to delete; may include "
            f"\"effective_from_tick\" >= {tick}; amendments are never retroactive), or\n"
            "  {\"abstain\": true} if you have no amendment to propose.\n"
            "Your proposal will be registered and voted on by the whole assembly. "
            "A deterministic engine validates law invariants, quorum and "
            "threshold; you cannot change world state directly."
        )

    def _voting_prompt(self, draft: ProposalDraft, voter: str, tick: int) -> str:
        persona = ""
        if self._state is not None and voter in self._state.agents:
            persona = self._state.agents[voter].persona_summary
        return (
            f"[prompt:{self.prompt_version}] [assembly:vote]\n"
            f"You are agent \"{voter}\" voting in the constitutional assembly "
            f"at tick {tick}.\n"
            f"Persona: {persona}\n\n"
            f"Proposal {draft.proposal_id} (submitted by {draft.submitted_by}):\n"
            f"  \"{draft.change_summary}\"\n"
            f"  payload: {json.dumps(draft.payload, sort_keys=True)}\n"
            f"Currently effective rules: {json.dumps(self._engine.constitution.active_at(tick).rules, sort_keys=True)}\n\n"
            "Respond with JSON ONLY: "
            '{"choice": "for"} or {"choice": "against"} or {"choice": "abstain"}.\n'
            "Your vote is recorded by a deterministic engine."
        )

    # -- deliberation ------------------------------------------------------------

    async def run_deliberation(
        self, state: WorldState, caller, *, context: list[dict[str, Any]], tick: int,
    ) -> list[ProposalDraft]:
        """ONE structured call per eligible voter (rights.voting); proposals are
        registered on the engine via ``GovernanceEngine.propose``. Malformed or
        invalid output is quarantined, never raised.

        Model calls are dispatched through the injected ``run_batch`` runner
        (sequential by default; the society layer injects a bounded
        concurrency runner). Determinism: engine mutations (propose),
        quarantine records, abstention records and proposal order are ALL
        produced in the same sorted-voter order as the sequential path, so
        outcomes and ledger events are identical for identical model outputs
        — only the wall-clock duration differs (and, via the recording
        wrapper, the row order of model_calls.jsonl, which is telemetry, not
        state).
        """
        self._state = state
        self._world_id = state.world_id
        voters = sorted(aid for aid, agent in state.agents.items() if agent.rights.voting)
        results = await self.run_batch(
            1,
            [self._deliberation_call(caller, voter, tick, context) for voter in voters],
        )
        proposals: list[ProposalDraft] = []
        seen: set[str] = set()
        for voter, data in zip(voters, results, strict=True):
            if data.abstained:
                self.deliberation_abstentions.append(voter)
                continue
            if data.error is not None:
                self.quarantined.append(
                    {"phase": "deliberation", "voter_id": voter,
                     "attempt_id": _attempt_id(state.world_id, "delib", voter, tick),
                     "reason": data.error}
                )
                continue
            summary, payload = data.summary, data.payload
            if (
                not isinstance(summary, str)
                or not summary.strip()
                or not isinstance(payload, dict)
            ):
                self.quarantined.append(
                    {"phase": "deliberation", "voter_id": voter,
                     "attempt_id": _attempt_id(state.world_id, "delib", voter, tick),
                     "reason": "invalid proposal schema"}
                )
                continue
            proposal_id = f"asm-{voter}-{tick}"
            if proposal_id in seen:
                self.quarantined.append(
                    {"phase": "deliberation", "voter_id": voter,
                     "attempt_id": _attempt_id(state.world_id, "delib", voter, tick),
                     "reason": "duplicate proposal_id, kept first"}
                )
                continue
            seen.add(proposal_id)
            try:
                self._engine.propose(proposal_id, voter, tick, summary, payload)
            except ValueError as exc:
                self.quarantined.append(
                    {"phase": "deliberation", "voter_id": voter,
                     "attempt_id": _attempt_id(state.world_id, "delib", voter, tick),
                     "reason": f"propose failed: {exc}"}
                )
                continue
            proposals.append(
                ProposalDraft(
                    proposal_id=proposal_id,
                    submitted_by=voter,
                    change_summary=summary,
                    payload=payload,
                )
            )
        self._proposals.extend(proposals)
        self._proposal_order.extend(p.proposal_id for p in proposals)
        return proposals

    async def _deliberation_call(self, caller, voter: str, tick: int,
                                 context: list[dict[str, Any]]) -> _DelibCallResult:
        """One voter's deliberation model call + parse only. Engine mutations
        are deferred to the post-pass in ``run_deliberation`` so the parallel
        path applies them in exactly the sequential (sorted-voter) order."""
        try:
            result = await caller.generate_structured(
                [{"role": "user", "content": self._deliberation_prompt(voter, tick, context)}],
                response_format_json=True, temperature=0.0,
            )
            data = _parse_json_object(result.content)
        except (ValueError, AttributeError, TypeError) as exc:
            return _DelibCallResult(error=str(exc))
        if data.get("abstain"):
            return _DelibCallResult(abstained=True)
        return _DelibCallResult(summary=data.get("change_summary"), payload=data.get("payload"))

    # -- voting ---------------------------------------------------------------------

    async def run_voting(
        self,
        proposals: list[ProposalDraft],
        caller,
        *,
        voters: frozenset[str],
        tick: int,
    ) -> list[VoteRecord]:
        """For each (proposal, voter) one structured vote; the deterministic
        engine records every vote via ``GovernanceEngine.cast_vote``. Invalid
        choices and malformed output are quarantined and cast as abstain.

        Model calls run through the injected ``run_batch`` runner; engine
        cast_vote mutations, quarantine records and the returned record list
        are produced in the same (proposal-order, sorted-voter) order as the
        sequential path, so ``finalize`` outcomes are identical for identical
        model outputs."""
        pairs = [(draft, voter) for draft in proposals for voter in sorted(voters)]
        results = await self.run_batch(
            1,
            [self._vote_call(caller, draft, voter, tick) for draft, voter in pairs],
        )
        records: list[VoteRecord] = []
        for (draft, voter), res in zip(pairs, results, strict=True):
            attempt_id = _attempt_id(self._world_id, draft.proposal_id, voter, tick)
            choice, reason = res.choice, res.reason
            if reason is not None:
                self.quarantined.append(
                    {"phase": "voting", "voter_id": voter,
                     "attempt_id": attempt_id, "reason": reason}
                )
            vote = VoteRecord(
                vote_id=f"vote-{draft.proposal_id}-{voter}",
                proposal_id=draft.proposal_id,
                voter_id=voter,
                tick=tick,
                choice=choice,
            )
            self._engine.cast_vote(vote)
            self._vote_attempt[vote.vote_id] = attempt_id
            records.append(vote)
        return records

    async def _vote_call(self, caller, draft: ProposalDraft, voter: str, tick: int) -> _VoteCallResult:
        """One voter's vote model call + parse only. The engine cast is
        deferred to the post-pass in ``run_voting`` so the parallel path
        applies engine mutations in deterministic order."""
        try:
            result = await caller.generate_structured(
                [{"role": "user", "content": self._voting_prompt(draft, voter, tick)}],
                response_format_json=True, temperature=0.0,
            )
            data = _parse_json_object(result.content)
            raw = data.get("choice")
            if raw in _VALID_VOTE_CHOICES:
                return _VoteCallResult(choice=raw)
            return _VoteCallResult(choice="abstain", reason=f"invalid choice: {raw!r}")
        except (ValueError, AttributeError, TypeError) as exc:
            return _VoteCallResult(choice="abstain", reason=str(exc))

    # -- finalize --------------------------------------------------------------------

    def finalize(self, engine, votes: list[VoteRecord], *, tick: int) -> AssemblyOutcome:
        """Call ``engine.enact`` for each proposal in submitted order and report
        the ENGINE's decision honestly — never override it."""
        by_proposal: dict[str, list[VoteRecord]] = {}
        for vote in votes:
            by_proposal.setdefault(vote.proposal_id, []).append(vote)

        any_accepted = False
        enacted_version: str | None = None
        enacted_effective_from_tick: int | None = None
        enact_events: list[dict[str, Any]] = []
        for proposal_id in self._proposal_order:
            event = engine.enact(
                by_proposal.get(proposal_id, []), tick, proposal_id=proposal_id
            )
            enact_events.append(event)
            if event.get("decision") == "accepted":
                any_accepted = True
                enacted_version = engine.constitution.current.version
                effective = event.get("effective_from_tick")
                if isinstance(effective, int):
                    enacted_effective_from_tick = effective

        if any_accepted:
            outcome = "amendment_enacted"
        elif self._proposal_order:
            outcome = "amendment_rejected"
        else:
            outcome = "no_change"

        return AssemblyOutcome(
            outcome=outcome,
            enacted_version=enacted_version,
            enacted_effective_from_tick=enacted_effective_from_tick,
            proposals=list(self._proposal_order),
            votes=[
                {
                    "voter_id": vote.voter_id,
                    "proposal_id": vote.proposal_id,
                    "choice": vote.choice,
                    "attempt_id": self._vote_attempt.get(vote.vote_id, ""),
                }
                for vote in votes
            ],
            abstentions=sum(1 for vote in votes if vote.choice == "abstain"),
            enact_events=enact_events,
            proposal_details=[
                {
                    "proposal_id": p.proposal_id,
                    "submitted_by": p.submitted_by,
                    "change_summary": p.change_summary,
                    "payload": p.payload,
                }
                for p in self._proposals
            ],
        )
