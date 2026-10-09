"""Appeal engine: deadline-bound appeals with restoration on upholding."""
from __future__ import annotations

from dataclasses import dataclass

from verdi.schemas import Appeal, Restoration

from .sanctions import SanctionEngine


class AppealError(ValueError):
    """Appeal rejected by deterministic policy."""


@dataclass
class AppealOutcome:
    appeal_id: str
    sanction_id: str
    status: str  # filed | upheld | dismissed
    out_of_time: bool
    restoration: Restoration | None = None
    grounds: str | None = None


@dataclass
class AppealMetrics:
    upheld: int = 0
    dismissed: int = 0
    out_of_time: int = 0

    @property
    def total_adjudicated(self) -> int:
        return self.upheld + self.dismissed


class AppealEngine:
    """Files and adjudicates appeals against sanctions.

    Filing after ``Sanction.appeal_deadline_tick`` is rejected as
    ``out_of_time`` (recorded, not enforced). Upholding a filed appeal
    restores rights immediately and records a ``Restoration``.
    """

    def __init__(self, sanctions: SanctionEngine) -> None:
        self._sanctions = sanctions
        self._appeals: dict[str, Appeal] = {}
        self._status: dict[str, str] = {}
        self._outcomes: list[AppealOutcome] = []
        self._restorations: dict[str, Restoration] = {}
        self.metrics = AppealMetrics()

    # -- filing -------------------------------------------------------------

    def file_appeal(self, appeal: Appeal) -> AppealOutcome:
        """File an appeal. Returns outcome; ``out_of_time`` True when rejected."""
        if appeal.appeal_id in self._appeals:
            raise AppealError(f"appeal already filed: {appeal.appeal_id}")
        sanction = self._sanctions.get(appeal.sanction_id)
        if sanction.subject_id != appeal.appellant_id:
            raise AppealError("appellant is not the sanctioned subject")

        self._appeals[appeal.appeal_id] = appeal

        deadline = self._sanctions.effective_deadline_tick(appeal.sanction_id)
        if deadline is not None and appeal.tick > deadline:
            self._status[appeal.appeal_id] = "dismissed"
            self.metrics.out_of_time += 1
            outcome = AppealOutcome(
                appeal_id=appeal.appeal_id,
                sanction_id=appeal.sanction_id,
                status="dismissed",
                out_of_time=True,
            )
            self._outcomes.append(outcome)
            return outcome

        self._status[appeal.appeal_id] = "filed"
        outcome = AppealOutcome(
            appeal_id=appeal.appeal_id,
            sanction_id=appeal.sanction_id,
            status="filed",
            out_of_time=False,
        )
        self._outcomes.append(outcome)
        return outcome

    # -- adjudication ---------------------------------------------------------

    def adjudicate(self, appeal_id: str, upheld: bool, grounds: str,
                   tick: int) -> AppealOutcome:
        """Adjudicate a filed appeal. Uphold -> Restoration, rights restored now."""
        appeal = self._appeals.get(appeal_id)
        if appeal is None:
            raise AppealError(f"unknown appeal: {appeal_id}")
        if self._status[appeal_id] != "filed":
            raise AppealError(f"appeal {appeal_id} already adjudicated")
        sanction = self._sanctions.get(appeal.sanction_id)

        if upheld:
            rights_restored = sorted(sanction.scope)
            restoration = Restoration(
                restoration_id=f"rst-{appeal.sanction_id}",
                sanction_id=appeal.sanction_id,
                agent_id=sanction.subject_id,
                effective_tick=tick,
                rights_restored=rights_restored,
                restitution={},
                reason=grounds,
            )
            # Immediate restoration: release rights and end the sanction.
            self._sanctions.rights.release(sanction.subject_id, frozenset(rights_restored))
            record = self._sanctions._active.get(appeal.sanction_id)
            if record is not None:
                record.expired = True
            self._status[appeal_id] = "upheld"
            self.metrics.upheld += 1
            self._restorations[appeal.sanction_id] = restoration
            outcome = AppealOutcome(
                appeal_id=appeal_id,
                sanction_id=appeal.sanction_id,
                status="upheld",
                out_of_time=False,
                restoration=restoration,
                grounds=grounds,
            )
        else:
            self._status[appeal_id] = "dismissed"
            self.metrics.dismissed += 1
            outcome = AppealOutcome(
                appeal_id=appeal_id,
                sanction_id=appeal.sanction_id,
                status="dismissed",
                out_of_time=False,
                grounds=grounds,
            )
        self._outcomes.append(outcome)
        return outcome

    # -- reads -----------------------------------------------------------------

    def status_of(self, appeal_id: str) -> str:
        if appeal_id not in self._status:
            raise AppealError(f"unknown appeal: {appeal_id}")
        return self._status[appeal_id]

    def get(self, appeal_id: str) -> Appeal:
        if appeal_id not in self._appeals:
            raise AppealError(f"unknown appeal: {appeal_id}")
        return self._appeals[appeal_id]

    def restoration_for(self, sanction_id: str) -> Restoration | None:
        return self._restorations.get(sanction_id)

    @property
    def outcomes(self) -> list[AppealOutcome]:
        return list(self._outcomes)
