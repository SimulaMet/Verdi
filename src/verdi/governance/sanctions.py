"""Sanction engine: evidence-required sanctions with time-decay restoration."""
from __future__ import annotations

from dataclasses import dataclass, field

from verdi.schemas import Sanction


class SanctionError(ValueError):
    """Sanction rejected by deterministic policy."""


@dataclass
class ActiveSanction:
    """An imposed sanction with its expiry tick (inclusive bound: expires when tick >= expires_at)."""

    sanction: Sanction
    expires_at: int  # first tick at which the sanction no longer applies
    expired: bool = False

    @property
    def sanction_id(self) -> str:
        return self.sanction.sanction_id


@dataclass
class _Revocation:
    from_tick: int  # first tick at which the revocation applies
    until: int  # first tick at which the right is restored


@dataclass
class RightsView:
    """Which agents have which rights revoked, and until when.

    Each right keeps every revocation interval, so overlapping or disjoint
    sanctions on the same right are represented exactly (a single merged
    interval would either restore rights early or revoke them too soon).
    """

    _revocations: dict[str, dict[str, list[_Revocation]]] = field(default_factory=dict)

    def revoke(self, agent_id: str, rights: frozenset[str], from_tick: int,
               until: int) -> None:
        per_agent = self._revocations.setdefault(agent_id, {})
        for right in rights:
            per_agent.setdefault(right, []).append(
                _Revocation(from_tick=from_tick, until=until)
            )

    def release(self, agent_id: str, rights: frozenset[str]) -> None:
        per_agent = self._revocations.get(agent_id)
        if per_agent is None:
            return
        for right in rights:
            per_agent.pop(right, None)
        if not per_agent:
            self._revocations.pop(agent_id, None)

    @staticmethod
    def _active(revs: list[_Revocation], tick: int) -> bool:
        return any(rev.from_tick <= tick < rev.until for rev in revs)

    def revoked_rights(self, agent_id: str, tick: int) -> frozenset[str]:
        per_agent = self._revocations.get(agent_id, {})
        return frozenset(
            right for right, revs in per_agent.items() if self._active(revs, tick)
        )

    def has_active_revocation(self, agent_id: str, tick: int) -> bool:
        return bool(self.revoked_rights(agent_id, tick))

    def expires_at(self, agent_id: str, right: str) -> int | None:
        revs = self._revocations.get(agent_id, {}).get(right)
        return max(rev.until for rev in revs) if revs else None

    def all_active(self, tick: int) -> dict[str, frozenset[str]]:
        out: dict[str, frozenset[str]] = {}
        for agent_id in self._revocations:
            active = self.revoked_rights(agent_id, tick)
            if active:
                out[agent_id] = active
        return out


class SanctionEngine:
    """Imposes sanctions, tracks rights revocations, auto-expires by duration.

    Every sanction stores its full evidence so downstream analysis can flag
    false sanctions. No wall clock, no randomness. ``default_deadline_ticks``
    supplies a constitution appeal-policy default when a sanction does not
    carry an explicit ``appeal_deadline_tick``.
    """

    def __init__(self, default_deadline_ticks: int | None = None) -> None:
        self._active: dict[str, ActiveSanction] = {}
        self._history: list[Sanction] = []
        self.rights = RightsView()
        self.false_sanction_flags: dict[str, str] = {}
        self.default_deadline_ticks = default_deadline_ticks

    def impose(self, sanction: Sanction) -> ActiveSanction:
        """Validate and impose a sanction. Refuses evidence-less sanctions."""
        # Re-validate beyond the schema: at least one reference with event ids.
        if not sanction.evidence:
            raise SanctionError(f"{sanction.sanction_id}: sanction requires evidence")
        if not any(ev.event_ids for ev in sanction.evidence):
            raise SanctionError(
                f"{sanction.sanction_id}: every evidence reference must cite event ids"
            )
        if sanction.sanction_id in self._active:
            raise SanctionError(f"sanction already imposed: {sanction.sanction_id}")

        expires_at = sanction.effective_tick + sanction.duration_ticks
        record = ActiveSanction(sanction=sanction, expires_at=expires_at)
        self._active[sanction.sanction_id] = record
        self._history.append(sanction)
        self.rights.revoke(
            sanction.subject_id,
            frozenset(sanction.scope),
            from_tick=sanction.effective_tick,
            until=expires_at,
        )
        return record

    def register_declaration(self, sanction: Sanction) -> ActiveSanction:
        """Register a scenario-declared sanction (t0 pre-existing condition).

        Unlike :meth:`impose`, this does not require event-based evidence —
        the "evidence" is the scenario file itself. The sanction still
        participates in the normal expiry lifecycle (advance_tick →
        rights release), so the agent is auto-restored by duration.
        """
        if not sanction.evidence:
            raise SanctionError(f"{sanction.sanction_id}: sanction requires evidence")
        if sanction.sanction_id in self._active:
            raise SanctionError(f"sanction already imposed: {sanction.sanction_id}")
        expires_at = sanction.effective_tick + sanction.duration_ticks
        record = ActiveSanction(sanction=sanction, expires_at=expires_at)
        self._active[sanction.sanction_id] = record
        self._history.append(sanction)
        self.rights.revoke(
            sanction.subject_id,
            frozenset(sanction.scope),
            from_tick=sanction.effective_tick,
            until=expires_at,
        )
        return record

    def advance_tick(self, tick: int) -> list[str]:
        """Expire sanctions whose duration has elapsed; restore rights.

        A sanction is active for ``duration_ticks`` ticks starting at
        ``effective_tick`` and expires when ``tick >= effective_tick + duration_ticks``.
        Returns sanction ids that expired at this advance (deterministic order).
        """
        expired = [
            sid
            for sid, record in self._active.items()
            if not record.expired and tick >= record.expires_at
        ]
        for sid in sorted(expired):
            self.end(sid)
        return sorted(expired)

    def end(self, sanction_id: str) -> None:
        """End one sanction and restore only the rights no other live sanction holds.

        Rights are released for the sanction's scope and then re-revoked from
        every other non-expired sanction on the same subject, so expiring (or
        overturning) one of two overlapping sanctions keeps the other in force.
        """
        record = self._active.get(sanction_id)
        if record is None:
            return
        record.expired = True
        subject = record.sanction.subject_id
        scope = frozenset(record.sanction.scope)
        self.rights.release(subject, scope)
        for other in sorted(self._active.values(), key=lambda r: r.sanction_id):
            if other.expired or other.sanction.subject_id != subject:
                continue
            overlap = scope & frozenset(other.sanction.scope)
            if overlap:
                self.rights.revoke(
                    subject, overlap,
                    from_tick=other.sanction.effective_tick, until=other.expires_at,
                )

    def has_live_sanction(self, agent_id: str) -> bool:
        """True while any non-expired sanction on ``agent_id`` remains."""
        return any(
            not r.expired and r.sanction.subject_id == agent_id
            for r in self._active.values()
        )

    def is_active(self, sanction_id: str, tick: int) -> bool:
        record = self._active.get(sanction_id)
        if record is None or record.expired:
            return False
        return record.sanction.effective_tick <= tick < record.expires_at

    def effective_deadline_tick(self, sanction_id: str) -> int | None:
        """Appeal deadline for a sanction (explicit field, else policy default)."""
        sanction = self.get(sanction_id)
        if sanction.appeal_deadline_tick is not None:
            return sanction.appeal_deadline_tick
        if self.default_deadline_ticks is None:
            return None
        return sanction.effective_tick + self.default_deadline_ticks

    def active_for(self, agent_id: str, tick: int) -> list[Sanction]:
        out = [
            r.sanction
            for r in self._active.values()
            if r.sanction.subject_id == agent_id and self.is_active(r.sanction_id, tick)
        ]
        return sorted(out, key=lambda s: s.sanction_id)

    def get(self, sanction_id: str) -> Sanction:
        record = self._active.get(sanction_id)
        if record is None:
            for s in self._history:
                if s.sanction_id == sanction_id:
                    return s
            raise KeyError(f"unknown sanction: {sanction_id}")
        return record.sanction

    @property
    def history(self) -> list[Sanction]:
        return list(self._history)

    def mark_false_sanction(self, sanction_id: str, reason: str) -> None:
        """Downstream (judge/analysis) flags a sanction as false; metrics read this."""
        self.false_sanction_flags[sanction_id] = reason

    def evidence_for(self, sanction_id: str) -> list:
        return list(self.get(sanction_id).evidence)
