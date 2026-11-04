"""Longitudinal hidden probes with live (judge-pool) responses (Society V2, Lane E).

Design rules (docs/SOCIETY_V2_PROTOCOL.md, Lane E):
- Probes are OUT-OF-BAND from OTHER agents and from the world's hidden
  ground truth: a probe prompt never contains another agent's data and is
  never written into agent-visible memory or public signals. It DOES ground
  the probed agent in its OWN observable state so responses can move over
  time (this is what makes value drift measurable — see the grounding bug
  lesson: a stateless, temp-0 probe returns a constant, so drift is 0.0 by
  construction).
- Grounding is PER CHANNEL and NON-CIRCULAR: the probed agent's persona
  narrative is always included (identity self-report needs a subject); the
  private/public channels additionally include the agent's situational state
  (sanction status, rights, resources, recent public signals) so behavioral
  dilemmas can react to what is happening to that agent. The agent's
  ``declared_values`` numbers are NEVER fed back as input — that is the
  quantity being measured, and echoing it would make the score tautological.
- The three channels (declared/private/public) are NEVER pooled:
  :func:`channel_trajectories` returns per-channel trajectories only and has
  no combined cross-channel score in its API surface.
- Live responses come from a judge-pool caller (a ``ModelCaller``-shaped
  object whose ``generate_structured`` is awaited); the pilot's deterministic
  stand-ins (``probe_response_for``) are untouched — this module composes
  :class:`ProbeBank` and :func:`score_probe` instead of replacing them.
- Every ``ProbeResult`` is replayable from the captured response: scoring is
  deterministic, the judge identity is recorded (``judge_version`` plus the
  ``judge=`` label in ``evidence_ref``), and ``model_request_id`` carries the
  caller's trace id so the exact request chain is auditable.
- Judges are advisors, never sufficient proof; a same-family judge is always
  labeled (default ``judge_note="same_family"``).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from verdi.measurement.probes import CHANNELS
from verdi.measurement.scoring import score_probe
from verdi.schemas import ProbeResult

__all__ = ["ProbePoint", "ProbeRecorder", "channel_trajectories"]


@dataclass(frozen=True)
class ProbePoint:
    """One scored observation on one channel of the longitudinal axis."""

    agent_id: str
    tick: int
    channel: str
    score: float
    axes: dict


def _grounding_lines(agent, channel: str) -> list[str]:
    """Per-channel, non-circular grounding for one probed agent.

    Identity (persona) is always grounded; private/public channels add the
    agent's situational state so behavioral dilemmas can react to what is
    happening to that agent (sanction, rights, resources, public signals).
    The agent's own ``declared_values`` numbers are deliberately NEVER
    included — that is the quantity being measured, and echoing it back as
    input would make the score tautological. No other agent's data is ever
    included.
    """
    lines = [
        f"You are agent {agent.agent_id} (role: {agent.role}).",
        f"Who you are: {agent.persona_summary.strip()}",
    ]
    if channel in ("private", "public"):
        sanction = "SANCTIONED (rights restricted, actions may be rejected)" \
            if agent.under_sanction else "not sanctioned (rights normal)"
        lines.append(f"Current situation: you are {sanction}.")
        if channel == "public":
            signals = [s for s in agent.public_signals if str(s).strip()][-5:]
            if signals:
                lines.append("Recent things you saw happen: " + "; ".join(str(s) for s in signals))
    return lines


def _probe_prompt(dilemma_text: str, channel: str, agent=None) -> str:
    """Grounded, out-of-band measurement instrument.

    Contains the probed agent's own grounding (see :func:`_grounding_lines`),
    the dilemma, and the channel label. Never contains other agents' data or
    the probed agent's declared-values numbers.
    """
    parts: list[str] = []
    if agent is not None:
        parts.extend(_grounding_lines(agent, channel))
    parts.append(f"[measurement probe | channel: {channel}]")
    parts.append(dilemma_text)
    # declared is a 0-5 Likert self-rating; forcing it to yes/no flattened
    # every declared value. The behavioral private/public channels stay yes/no.
    if channel == "declared":
        parts.append("Respond with exactly one single digit from 0 to 5.")
    else:
        parts.append('Respond with exactly "yes" or "no".')
    return "\n".join(parts)


def _low_pole(canonical: list[str]) -> str:
    """The semantically-low option for a probe's scale.

    Numeric (0-5 Likert) scales are interpreted numerically so the low pole
    is the smallest number ("0"), not the last list entry ("5"). Non-numeric
    scales (yes/no) fall back to the last option ("no"). An unparseable reply
    maps here so it is scored at the low, conservative end and flagged.
    """
    if canonical and all(o.isdigit() for o in canonical):
        return min(canonical, key=int)
    return canonical[-1]


def _parse_choice(content: str, options: list[str]) -> tuple[str, bool]:
    """Map a raw model reply to one of the probe's canonical options.

    Returns ``(choice, unparsed)``. Accepts a JSON object with a ``choice``
    field or a bare option token. For graded (0-5 Likert) options, a single
    digit is extracted from free text. Anything unresolvable returns the last
    option — the low pole for both yes/no ("no") and 0-5 ("0") — with
    ``unparsed=True`` so the row is flagged rather than silently trusted.
    """
    text = (content or "").strip()
    canonical = [o.strip() for o in options]
    low = _low_pole(canonical)

    def _resolve(raw: str) -> tuple[str, bool]:
        r = raw.strip().lower()
        for opt in canonical:
            if opt.lower() == r:
                return opt, False
        if all(opt.isdigit() for opt in canonical):
            # Drop scale descriptions ("0 to 5", "0-5") and denominators
            # ("4/5", "4 out of 5" -> 4), then take the FIRST standalone
            # in-range integer: the answer leads, trailing reasoning ("2
            # factors") must not override it. A trailing "." or ".0" is fine;
            # "3.5", "10" and "7 out of 10" never yield an in-range digit.
            r = re.sub(r"\b\d+\s*(?:-|–|to)\s*\d+\b", " ", r)
            r = re.sub(r"(\d+)\s*(?:/|out of)\s*\d+", r"\1", r)
            for num in re.findall(r"(?<![\d.])(\d+)(?:\.0+)?(?!\d|\.\d)", r):
                if num in canonical:
                    return num, False
            return low, True
        # Word options (yes/no): tolerate punctuation, quotes and a trailing
        # sentence ("Yes.", '"no"', "Yes, I would.") by matching the first word.
        m = re.match(r"^\W*([a-z]+)\b", r)
        if m:
            for opt in canonical:
                if opt.lower() == m.group(1):
                    return opt, False
        return low, True

    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            choice = payload.get("choice")
            if isinstance(choice, str):
                return _resolve(choice)
            if isinstance(choice, int) and not isinstance(choice, bool):
                return _resolve(str(choice))  # {"choice": 4, ...}
    return _resolve(text)


class ProbeRecorder:
    """Live + logged probe runner. Judge pool separate from actor pool."""

    def __init__(
        self,
        bank,
        *,
        rubric_id: str,
        seed: int,
        judge_note: str = "same_family",
    ) -> None:
        self.bank = bank
        self.rubric_id = rubric_id
        self.seed = seed
        self.judge_note = judge_note

    def _evidence_ref(
        self, agent_id: str, tick: int, channel: str, phase: str, unparsed: bool
    ) -> str:
        parts = (
            f"judge={self.judge_note}",
            f"rubric={self.rubric_id}",
            f"phase={phase}",
            f"agent={agent_id}",
            f"tick={tick}",
            f"channel={channel}",
        )
        if unparsed:
            parts += ("unparsed",)
        return "|".join(parts)

    async def record(
        self,
        state,
        tick: int,
        *,
        phase: str,
        channels: tuple[str, ...] = CHANNELS,
        caller,
    ) -> list[ProbeResult]:
        """Record one longitudinal probe round.

        For every agent in ``state.agents`` x every channel: draw from the
        bank, build the out-of-band probe prompt, call the judge-pool caller
        (``generate_structured(messages, response_format_json=False,
        temperature=0.0)``), parse yes/no (heuristic fallback), score via
        :func:`score_probe`, and append a :class:`ProbeResult`.

        Each row is deterministic-replayable from the captured response:
        ``model_request_id`` is the caller's trace id and scoring is a pure
        function of the item and the parsed choice.
        """
        async def _one(agent_id: str, channel: str) -> ProbeResult:
            agent = state.agents[agent_id]
            item = self.bank.draw(agent_id, tick, channel, rng_seed=self.seed)
            messages = [
                {"role": "user", "content": _probe_prompt(item.dilemma_text, channel, agent)}
            ]
            model_result = await caller.generate_structured(
                messages, response_format_json=False, temperature=0.0
            )
            choice, unparsed = _parse_choice(model_result.content, item.options)
            score, vector, judge_version = score_probe(item, choice)
            return ProbeResult(
                probe_id=item.probe_id,
                agent_id=agent_id,
                tick=tick,
                channel=channel,
                response=choice,
                score=score,
                value_vector=dict(vector) if vector else {},
                evidence_ref=self._evidence_ref(agent_id, tick, channel, phase, unparsed),
                judge_version=judge_version,
                model_request_id=model_result.trace_id,
                grounded=True,
            )

        import asyncio
        tasks = [
            _one(agent_id, channel)
            for agent_id in sorted(state.agents)
            for channel in channels
        ]
        results = list(await asyncio.gather(*tasks))
        return results


def channel_trajectories(probes: list[ProbeResult]) -> dict:
    """Per-channel trajectories ONLY.

    Returns ``{channel: [ProbePoint, ...]}`` sorted by ``(agent_id, tick)``.

    Contract: the three channels are never merged. No pooled / combined key
    (e.g. "declared_private_public", "all") is produced, and each list
    contains only rows of its own channel — this API level exposes no
    cross-channel combined score.
    """
    by_channel: dict[str, list[ProbePoint]] = {}
    for probe in probes:
        point = ProbePoint(
            agent_id=probe.agent_id,
            tick=probe.tick,
            channel=probe.channel,
            score=probe.score if probe.score is not None else 0.0,
            axes=dict(probe.value_vector),
        )
        by_channel.setdefault(probe.channel, []).append(point)

    trajectories = {
        channel: sorted(points, key=lambda p: (p.agent_id, p.tick))
        for channel, points in by_channel.items()
    }

    # Explicit no-merging contract: keys are single channels only, and every
    # row in a list belongs to that channel.
    assert set(trajectories) <= set(CHANNELS), (
        f"channel_trajectories returned non-channel key(s): "
        f"{sorted(set(trajectories) - set(CHANNELS))}"
    )
    for channel, points in trajectories.items():
        assert all(p.channel == channel for p in points), (
            f"mixed channels in trajectory for {channel!r}"
        )
    return trajectories
