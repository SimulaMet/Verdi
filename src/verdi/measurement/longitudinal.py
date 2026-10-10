"""Longitudinal hidden probes with live (judge-pool) responses (Society V2, Lane E).

Design rules (docs/SOCIETY_V2_PROTOCOL.md, Lane E):
- Probes are STRICTLY OUT-OF-BAND: the probe prompt contains only the dilemma
  (plus the channel name); no agent persona, no other agent's data, and the
  prompt is never written into agent-visible memory or public signals.
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


def _probe_prompt(dilemma_text: str, channel: str) -> str:
    """Minimal, out-of-band measurement instrument.

    Deliberately contains ONLY the dilemma and the channel label — no agent
    persona, role, memory, or any other agent's data.
    """
    return (
        f"[measurement probe | channel: {channel}]\n"
        f"{dilemma_text}\n"
        'Respond with exactly "yes" or "no".'
    )


def _parse_choice(content: str) -> tuple[str, bool]:
    """Map a raw model reply to the canonical option ("yes"/"no").

    Returns ``(choice, unparsed)``. Accepts a JSON object with a ``choice``
    field, a bare yes/no word; anything else defaults to "no" with
    ``unparsed=True`` so the row is flagged rather than silently trusted.
    """
    text = (content or "").strip()
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            value = payload.get("choice")
            if isinstance(value, str) and value.strip().lower() in ("yes", "no"):
                return value.strip().lower(), False
    if text.lower() in ("yes", "no"):
        return text.lower(), False
    return "no", True


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
        results: list[ProbeResult] = []
        for agent_id in sorted(state.agents):
            for channel in channels:
                item = self.bank.draw(agent_id, tick, channel, rng_seed=self.seed)
                messages = [
                    {"role": "user", "content": _probe_prompt(item.dilemma_text, channel)}
                ]
                model_result = await caller.generate_structured(
                    messages, response_format_json=False, temperature=0.0
                )
                choice, unparsed = _parse_choice(model_result.content)
                score, vector, judge_version = score_probe(item, choice)
                results.append(
                    ProbeResult(
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
                    )
                )
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
