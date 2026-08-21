"""Final synthesis stage.

This stage is deliberately *not* "let one of the five pick a winner":

  * the quantitative parts (agreement per agent, consensus score, who agreed,
    who dissented, which objections were material) are computed in code by the
    Consensus Engine and passed in as authoritative facts;
  * a model is used only to write the prose compilation, and it is addressed
    in a neutral SYNTHESIS ENGINE role, not as a council member;
  * whichever agent is elected, minority positions are re-injected from the
    deterministic record afterwards, so a synthesiser cannot erase dissent
    even if it wants to;
  * if every model fails, a fully deterministic fallback synthesis is built
    from the stored round data.
"""

from __future__ import annotations

import json
from typing import Any

from ..agents.base import BaseAgent
from ..models.enums import AgentName, RoundKind
from ..models.schemas import (
    AgentOutcome,
    ConsensusReport,
    IndividualPosition,
    MinorityPosition,
    RejectedArgumentSummary,
    RoundResult,
    SynthesisResult,
    TokenUsage,
)
from ..services.logging import get_logger
from .consensus import collect_minority

log = get_logger(__name__)

MAX_TRANSCRIPT_CHARS = 24000


# --------------------------------------------------------------------------- #
# Transcript
# --------------------------------------------------------------------------- #


def build_transcript(rounds: list[RoundResult], roles: dict[str, str] | None = None) -> str:
    blocks: list[str] = []
    if roles:
        blocks.append(
            "===== ROLES AT THE TABLE =====\n"
            + "\n".join(f"- {a}: {r}" for a, r in sorted(roles.items()))
            + "\nRoles shape how an agent argued, not what it was allowed to conclude."
        )
    for rnd in rounds:
        header = (
            f"===== ROUND {rnd.index} "
            f"({'INDEPENDENT ANSWERS' if rnd.kind == RoundKind.INITIAL else 'DEBATE'}) ====="
        )
        blocks.append(header)
        for agent, outcome in sorted(rnd.outcomes.items()):
            if not outcome.ok or not outcome.payload:
                blocks.append(
                    f"--- {agent}: NO VALID RESPONSE "
                    f"({outcome.status.value}: {outcome.error or 'unknown'})"
                )
                continue
            p = outcome.payload
            if rnd.kind == RoundKind.INITIAL:
                blocks.append(
                    f"--- {agent} (confidence {p.get('confidence')})\n"
                    f"{p.get('answer', '')}\n"
                    f"Key points: {json.dumps(p.get('key_points', []), ensure_ascii=False)}\n"
                    f"Assumptions: {json.dumps(p.get('assumptions', []), ensure_ascii=False)}"
                )
            else:
                blocks.append(
                    f"--- {agent} (confidence {p.get('confidence')}, "
                    f"changed_position={p.get('changed_my_position')}, "
                    f"persuaded_by={p.get('persuaded_by') or []})\n"
                    f"{p.get('position', '')}\n"
                    f"Accepted: {json.dumps(p.get('accepted_arguments', []), ensure_ascii=False)}\n"
                    f"Rejected: {json.dumps(p.get('rejected_arguments', []), ensure_ascii=False)}\n"
                    f"Uncertain: {json.dumps(p.get('uncertain_arguments', []), ensure_ascii=False)}\n"
                    f"Persuasion attempts: "
                    f"{json.dumps(p.get('persuasion', []), ensure_ascii=False)}\n"
                    f"Would change my mind: {p.get('what_would_change_my_mind', '')}\n"
                    f"Proposed common answer: {p.get('proposed_common_answer', '')}\n"
                    f"Why: {p.get('why_changed', '')}"
                )
        if rnd.consensus is not None:
            blocks.append(
                f"--- CONSENSUS CHECK: reached={rnd.consensus.reached} "
                f"score={rnd.consensus.score} rule={rnd.consensus.rule}\n"
                f"{rnd.consensus.reason}"
            )

    text = "\n\n".join(blocks)
    if len(text) > MAX_TRANSCRIPT_CHARS:
        # Keep round 0 and the tail: the newest rounds carry the live argument.
        head = text[: MAX_TRANSCRIPT_CHARS // 3]
        tail = text[-(MAX_TRANSCRIPT_CHARS * 2 // 3) :]
        text = f"{head}\n\n[... middle of the transcript truncated for length ...]\n\n{tail}"
    return text


# --------------------------------------------------------------------------- #
# Synthesiser election
# --------------------------------------------------------------------------- #


def elect_synthesizers(
    council: dict[AgentName, BaseAgent],
    last_round: RoundResult | None,
    preferred: str = "auto",
) -> list[AgentName]:
    """Ordered list of candidates for the neutral synthesiser role."""
    healthy = [
        a
        for a in council
        if council[a].enabled
        and (last_round is None or last_round.outcomes.get(a.value, None) is None
             or last_round.outcomes[a.value].ok)
    ]
    if not healthy:
        healthy = [a for a in council if council[a].enabled]

    pref = (preferred or "auto").strip().upper()
    if pref and pref != "AUTO":
        try:
            chosen = AgentName(pref)
        except ValueError:
            chosen = None
        if chosen is not None and chosen in council and council[chosen].enabled:
            return [chosen] + [a for a in healthy if a != chosen]
    return healthy


# --------------------------------------------------------------------------- #
# Deterministic parts
# --------------------------------------------------------------------------- #


def _final_payloads(rounds: list[RoundResult]) -> dict[str, dict[str, Any]]:
    """Each agent's most recent valid payload."""
    latest: dict[str, dict[str, Any]] = {}
    for rnd in rounds:
        for agent, outcome in rnd.outcomes.items():
            if outcome.ok and outcome.payload:
                latest[agent] = outcome.payload
    return latest


def _collect_rejected(rounds: list[RoundResult]) -> list[RejectedArgumentSummary]:
    grouped: dict[str, RejectedArgumentSummary] = {}
    for rnd in rounds:
        if rnd.kind != RoundKind.DEBATE:
            continue
        for agent, outcome in rnd.outcomes.items():
            if not outcome.ok or not outcome.payload:
                continue
            for ref in outcome.payload.get("rejected_arguments") or []:
                if not isinstance(ref, dict):
                    continue
                arg = str(ref.get("argument", "")).strip()
                if not arg:
                    continue
                key = arg.lower()[:160]
                entry = grouped.get(key)
                if entry is None:
                    entry = RejectedArgumentSummary(
                        argument=arg, rejected_by=[], reason=str(ref.get("reason", ""))
                    )
                    grouped[key] = entry
                if agent not in entry.rejected_by:
                    entry.rejected_by.append(agent)
        # newest reason wins
    return list(grouped.values())


def deterministic_synthesis(
    *,
    rounds: list[RoundResult],
    consensus: ConsensusReport | None,
    note: str = "",
) -> SynthesisResult:
    """Fallback used when no model could produce a synthesis."""
    payloads = _final_payloads(rounds)
    positions = [
        IndividualPosition(
            agent=agent,
            position=str(p.get("position") or p.get("answer") or "")[:1500],
        )
        for agent, p in sorted(payloads.items())
    ]
    minority = []
    if consensus is not None:
        for m in collect_minority(consensus, payloads):
            minority.append(
                MinorityPosition(
                    agent=m["agent"],
                    position=m["position"][:1500],
                    evidence=m["evidence"],
                    assessment="Assessed automatically: this agent did not agree "
                    "with the main conclusion at the end of the debate.",
                )
            )

    consensus_text = ""
    if consensus is not None and consensus.main_conclusion:
        consensus_text = consensus.main_conclusion
    elif positions:
        consensus_text = positions[0].position

    prefix = f"{note}\n\n" if note else ""
    return SynthesisResult(
        consensus=prefix + consensus_text,
        main_reasoning=(consensus.reason if consensus else ""),
        key_agreements=[],
        disagreements=[
            f"{m.agent} did not agree with the main conclusion." for m in minority
        ],
        strongest_arguments=[],
        rejected_arguments=_collect_rejected(rounds),
        minority_positions=minority,
        individual_positions=positions,
        confidence=round((consensus.score if consensus else 0.0) * 100, 1),
    )


def enforce_minority(
    result: SynthesisResult,
    consensus: ConsensusReport | None,
    rounds: list[RoundResult],
) -> SynthesisResult:
    """Re-inject dissent the synthesiser may have dropped (requirement #23)."""
    if consensus is None:
        return result
    payloads = _final_payloads(rounds)
    present = {m.agent.upper() for m in result.minority_positions}
    for m in collect_minority(consensus, payloads):
        if m["agent"].upper() in present:
            continue
        result.minority_positions.append(
            MinorityPosition(
                agent=m["agent"],
                position=m["position"][:1500],
                evidence=m["evidence"],
                assessment=(
                    "Preserved automatically by the consensus engine: this agent "
                    "did not agree with the main conclusion and its position was "
                    "not represented in the synthesis."
                ),
            )
        )
    # Individual positions must cover every agent that ever answered.
    covered = {p.agent.upper() for p in result.individual_positions}
    for agent, payload in sorted(payloads.items()):
        if agent.upper() not in covered:
            result.individual_positions.append(
                IndividualPosition(
                    agent=agent,
                    position=str(payload.get("position") or payload.get("answer") or "")[:1500],
                )
            )
    if not result.rejected_arguments:
        result.rejected_arguments = _collect_rejected(rounds)
    return result


# --------------------------------------------------------------------------- #
# Orchestrated synthesis
# --------------------------------------------------------------------------- #


async def run_synthesis(
    *,
    council: dict[AgentName, BaseAgent],
    rounds: list[RoundResult],
    consensus: ConsensusReport | None,
    question: str,
    failed_agents: list[str],
    preferred: str = "auto",
    roles: dict[str, str] | None = None,
) -> tuple[SynthesisResult, str | None, list[tuple[AgentName, AgentOutcome]]]:
    """Returns (result, synthesiser agent name or None, attempts made)."""
    transcript = build_transcript(rounds, roles)
    consensus_payload = consensus.model_dump(mode="json") if consensus else {}
    last_round = rounds[-1] if rounds else None
    attempts: list[tuple[AgentName, AgentOutcome]] = []

    for candidate in elect_synthesizers(council, last_round, preferred):
        agent = council[candidate]
        outcome = await agent.synthesize(
            question=question,
            transcript=transcript,
            consensus=consensus_payload,
            failed_agents=failed_agents,
        )
        attempts.append((candidate, outcome))
        if outcome.ok and outcome.payload:
            result = SynthesisResult.model_validate(outcome.payload)
            result = enforce_minority(result, consensus, rounds)
            return result, candidate.value, attempts
        log.warning(
            "Synthesis via %s failed (%s): %s",
            candidate.value,
            outcome.status.value,
            outcome.error,
        )

    log.error("All synthesis candidates failed — using the deterministic fallback")
    fallback = deterministic_synthesis(
        rounds=rounds,
        consensus=consensus,
        note=(
            "_No model was able to produce the final synthesis, so this summary "
            "was assembled deterministically from the debate record._"
        ),
    )
    return fallback, None, attempts


def total_usage(attempts: list[tuple[AgentName, AgentOutcome]]) -> dict[str, TokenUsage]:
    out: dict[str, TokenUsage] = {}
    for agent, outcome in attempts:
        out[agent.value] = out.get(agent.value, TokenUsage()) + outcome.usage
    return out
