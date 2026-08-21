"""Consensus Engine.

Deliberately deterministic: no model decides whether consensus was reached.
The engine reads each agent's structured `ConsensusVote` plus its `DebateResponse`
from the same round and applies fixed rules.

Consensus is reached when, and only when, **all** of these hold:

  * at least `min_rounds` debate rounds have happened;
  * at least two agents are still participating;
  * the weighted agreement score is >= `consensus_threshold`;
  * one of three rules fires:

      R1 `unanimous_conclusion`  — every participant agrees with the main
         conclusion and nobody holds a material objection.
      R2 `supermajority`         — at least n-1 participants agree (and at
         least 80% of them), and no dissenter has a material,
         evidence-backed objection.
      R3 `formulation_alignment` — every participant agrees on the *wording*
         of the conclusion (high textual overlap) even though their
         confidence differs; the score bar is relaxed by 10% for this rule
         because the disagreement is about reasoning, not the answer.

Anti-sycophancy (requirement #22): an agreement is only counted when the agent
actually engaged — it must restate the conclusion in its own words *and* have
produced at least one specific accepted/rejected/uncertain argument about a
named other agent in the same round. A bare "I agree" is recorded as
`unsubstantiated`, does not count towards the agreement tally, and has its
contribution to the score clamped to neutral (0.5).
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable

from ..models.schemas import ConsensusReport, ConsensusVote, DebateResponse
from ..services.logging import get_logger

log = get_logger(__name__)

MIN_CONCLUSION_CHARS = 20
MIN_EVIDENCE_CHARS = 20
NEUTRAL = 0.5
FORMULATION_RELAXATION = 0.9
SIMILARITY_FLOOR = 0.45

_WORD_RE = re.compile(r"[\w']+", re.UNICODE)
_STOPWORDS = {
    "the", "a", "an", "is", "are", "of", "to", "and", "or", "that", "this",
    "it", "in", "on", "for", "with", "as", "be", "by", "at", "from", "which",
    "и", "в", "на", "что", "не", "с", "по", "это", "как", "для",
}


def _tokens(text: str) -> set[str]:
    return {
        w.lower()
        for w in _WORD_RE.findall(text or "")
        if len(w) > 2 and w.lower() not in _STOPWORDS
    }


def similarity(a: str, b: str) -> float:
    """Jaccard overlap of content words — cheap, deterministic, dependency-free."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


class ConsensusEngine:
    def __init__(self, threshold: float = 0.8, min_rounds: int = 1) -> None:
        self.threshold = max(0.0, min(1.0, threshold))
        self.min_rounds = max(0, min_rounds)

    # ------------------------------------------------------------------ helper
    @staticmethod
    def _is_substantiated(vote: ConsensusVote, debate: DebateResponse | None) -> bool:
        if len(vote.main_conclusion.strip()) < MIN_CONCLUSION_CHARS:
            return False
        if debate is None:
            # Round 0 style vote (no debate reply yet) — the restated
            # conclusion is the only evidence of engagement we can require.
            return True
        if debate.is_capitulation:
            # Агент сменил позицию и не смог назвать, кто его переубедил.
            # Ровно та «сходимость», ради предотвращения которой всё и затевалось.
            return False
        return debate.engagement_score >= 1

    @staticmethod
    def _pick_main_conclusion(conclusions: dict[str, str]) -> str:
        """The conclusion that best represents the group (highest mean overlap)."""
        items = [(a, c) for a, c in conclusions.items() if c.strip()]
        if not items:
            return ""
        if len(items) == 1:
            return items[0][1]
        best, best_score = items[0][1], -1.0
        for _, candidate in items:
            score = sum(similarity(candidate, other) for _, other in items if other != candidate)
            score /= max(1, len(items) - 1)
            if score > best_score:
                best, best_score = candidate, score
        return best

    # ---------------------------------------------------------------- evaluate
    def evaluate(
        self,
        *,
        round_index: int,
        votes: dict[str, ConsensusVote],
        debates: dict[str, DebateResponse] | None = None,
    ) -> ConsensusReport:
        debates = debates or {}
        participants = sorted(votes.keys())
        n = len(participants)

        if n == 0:
            return ConsensusReport(
                round_index=round_index,
                reached=False,
                score=0.0,
                threshold=self.threshold,
                agree_count=0,
                participant_count=0,
                rule="no_participants",
                reason="No agent produced a valid consensus vote this round.",
            )

        agreement_by_agent: dict[str, float] = {}
        agreeing: list[str] = []
        dissenting: list[str] = []
        unsubstantiated: list[str] = []
        capitulated: list[str] = []
        edges: list[dict[str, str]] = []
        material: list[dict[str, str]] = []
        conclusions: dict[str, str] = {}
        effective: list[float] = []

        for agent in participants:
            vote = votes[agent]
            debate = debates.get(agent)
            conclusions[agent] = vote.main_conclusion
            agreement_by_agent[agent] = round(vote.agreement_level, 4)

            if debate is not None:
                if debate.is_capitulation:
                    capitulated.append(agent)
                elif debate.changed_my_position:
                    for source in debate.persuaded_by:
                        edges.append({"agent": agent, "persuaded_by": source})

            substantiated = self._is_substantiated(vote, debate)
            if vote.agrees_with_main_conclusion and not substantiated:
                unsubstantiated.append(agent)

            level = vote.agreement_level
            if agent in unsubstantiated:
                level = min(level, NEUTRAL)
            effective.append(level)

            if vote.agrees_with_main_conclusion and substantiated:
                agreeing.append(agent)
            else:
                dissenting.append(agent)

            if vote.has_material_objection and len(
                vote.evidence_for_objection.strip()
            ) >= MIN_EVIDENCE_CHARS:
                material.append(
                    {
                        "agent": agent,
                        "objection": vote.objection.strip(),
                        "evidence": vote.evidence_for_objection.strip(),
                    }
                )

        score = sum(effective) / len(effective)
        agree_count = len(agreeing)
        main_conclusion = self._pick_main_conclusion(conclusions)
        objectors = {m["agent"] for m in material}
        dissenting_with_objection = [a for a in dissenting if a in objectors]

        rule = "not_reached"
        reason = ""
        reached = False

        if round_index < self.min_rounds:
            rule = "min_rounds_not_met"
            reason = (
                f"Debate round {round_index} of a required minimum of "
                f"{self.min_rounds}; consensus check is informational only."
            )
        elif n < 2:
            rule = "insufficient_participants"
            reason = "At least two active agents are required to form a consensus."
        else:
            required_supermajority = max(2, math.ceil(0.8 * n), n - 1)

            if agree_count == n and not material and score >= self.threshold:
                reached, rule = True, "unanimous_conclusion"
                reason = (
                    f"All {n} active agents agree with the main conclusion, no "
                    f"material objection was raised, and the agreement score "
                    f"{score:.2f} meets the {self.threshold:.2f} threshold."
                )
            elif (
                agree_count >= required_supermajority
                and not dissenting_with_objection
                and score >= self.threshold
            ):
                reached, rule = True, "supermajority"
                reason = (
                    f"{agree_count}/{n} agents agree; the remaining "
                    f"{n - agree_count} raised no material evidence-backed "
                    f"objection. Agreement score {score:.2f} >= "
                    f"{self.threshold:.2f}."
                )
            elif (
                agree_count == n
                and not material
                and self._formulation_aligned(conclusions)
                and score >= self.threshold * FORMULATION_RELAXATION
            ):
                reached, rule = True, "formulation_alignment"
                reason = (
                    "All agents converged on the same formulation of the answer; "
                    "remaining differences are in argumentation, not in the "
                    f"conclusion (score {score:.2f})."
                )
            else:
                bits: list[str] = []
                if agree_count < required_supermajority:
                    bits.append(
                        f"only {agree_count}/{n} agents agree "
                        f"({required_supermajority} needed)"
                    )
                if dissenting_with_objection:
                    bits.append(
                        "material objection from "
                        + ", ".join(sorted(dissenting_with_objection))
                    )
                if score < self.threshold:
                    bits.append(
                        f"agreement score {score:.2f} below threshold "
                        f"{self.threshold:.2f}"
                    )
                if unsubstantiated:
                    bits.append(
                        "unsubstantiated agreement discarded from "
                        + ", ".join(sorted(unsubstantiated))
                    )
                if capitulated:
                    bits.append(
                        "unexplained position switch (capitulation) discarded from "
                        + ", ".join(sorted(capitulated))
                    )
                reason = "Consensus not reached: " + "; ".join(bits or ["rules not satisfied"])

        return ConsensusReport(
            round_index=round_index,
            reached=reached,
            score=round(score, 4),
            threshold=self.threshold,
            agree_count=agree_count,
            participant_count=n,
            rule=rule,
            reason=reason,
            agreement_by_agent=agreement_by_agent,
            agreeing_agents=agreeing,
            dissenting_agents=dissenting,
            unsubstantiated_agents=unsubstantiated,
            capitulated_agents=capitulated,
            persuasion_edges=edges,
            material_objections=material,
            main_conclusion=main_conclusion,
        )

    @staticmethod
    def _formulation_aligned(conclusions: dict[str, str]) -> bool:
        values = [c for c in conclusions.values() if c.strip()]
        if len(values) < 2:
            return False
        pairs = [
            similarity(a, b)
            for i, a in enumerate(values)
            for b in values[i + 1 :]
        ]
        return bool(pairs) and (sum(pairs) / len(pairs)) >= SIMILARITY_FLOOR


def collect_minority(report: ConsensusReport, debates: dict[str, Any]) -> list[dict[str, str]]:
    """Minority positions worth preserving in the final answer (requirement #23)."""
    out: list[dict[str, str]] = []
    objections = {m["agent"]: m for m in report.material_objections}
    for agent in report.dissenting_agents:
        payload = debates.get(agent) or {}
        position = str(payload.get("position") or payload.get("answer") or "").strip()
        obj = objections.get(agent, {})
        out.append(
            {
                "agent": agent,
                "position": position,
                "objection": obj.get("objection", ""),
                "evidence": obj.get("evidence", ""),
            }
        )
    return out
