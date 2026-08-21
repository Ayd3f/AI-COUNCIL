"""DebateOrchestrator — the central controller.

    DebateOrchestrator
            │
            ├── Round 0   (independent answers, all agents in parallel)
            ├── Round 1   (debate, all agents in parallel)
            │     └── Consensus votes  →  Consensus Engine (deterministic)
            ├── Round 2...
            └── Final Synthesis

Guarantees:
  * every phase fans out in parallel;
  * a failing agent never aborts a round, and may rejoin in a later round;
  * the loop is hard-bounded by `max_rounds` — it can never run forever;
  * every event is persisted so a client can join late and replay.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from typing import Any

from ..agents.base import BaseAgent
from ..config import Settings, get_settings
from ..models.enums import AgentName, AgentStatus, DebateStatus, EventType, RoundKind
from ..models.schemas import (
    AgentOutcome,
    ConsensusReport,
    ConsensusVote,
    CostReport,
    DebateConfig,
    DebateDetail,
    DebateResponse,
    RoundResult,
    TokenUsage,
)
from ..services.events import EventBus
from ..services.logging import get_logger
from ..services.pricing import PricingTable, build_cost_report
from ..services.storage import DebateRepository
from .consensus import ConsensusEngine
from .round_manager import RoundManager
from .synthesis import run_synthesis

log = get_logger(__name__)

MAX_HISTORY_ENTRIES = 3


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class DebateOrchestrator:
    def __init__(
        self,
        *,
        debate_id: str,
        question: str,
        config: DebateConfig,
        council: dict[AgentName, BaseAgent],
        repo: DebateRepository | None = None,
        bus: EventBus | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.debate_id = debate_id
        self.question = question
        self.config = config
        self.council = council
        self.repo = repo
        self.bus = bus
        self.settings = settings or get_settings()

        self.rounds: list[RoundResult] = []
        self.usage: dict[str, TokenUsage] = {}
        self.models: dict[str, str] = {a.value: c.model for a, c in council.items()}
        self.engine = ConsensusEngine(
            threshold=config.consensus_threshold, min_rounds=config.min_rounds
        )
        self.round_manager = RoundManager(
            council=council,
            emit=self._emit,
            question=question,
            max_rounds=config.max_rounds,
        )
        self.error: str | None = None

    # ------------------------------------------------------------------ events
    async def _emit(self, event_type: EventType, data: dict[str, Any]) -> None:
        if self.bus is None:
            return
        event = self.bus.emit(self.debate_id, event_type, data)
        if self.repo is not None:
            try:
                await self.repo.save_event(event)
            except Exception:  # noqa: BLE001 - telemetry must never break a debate
                log.exception("Failed to persist event %s", event.type)

    # ------------------------------------------------------------------- usage
    def _account(self, outcome: AgentOutcome) -> None:
        key = outcome.agent.value
        self.usage[key] = self.usage.get(key, TokenUsage()) + outcome.usage

    async def _persist_outcome(self, round_id: int | None, outcome: AgentOutcome) -> None:
        self._account(outcome)
        if self.repo is None or round_id is None:
            return
        await self.repo.save_agent_outcome(self.debate_id, round_id, outcome)
        if outcome.usage.total_tokens:
            await self.repo.add_usage(
                self.debate_id, outcome.agent.value, outcome.model, outcome.usage
            )

    # ------------------------------------------------------------------ helpers
    def _valid_payloads(self, rnd: RoundResult) -> dict[str, dict[str, Any]]:
        return {
            agent: outcome.payload
            for agent, outcome in rnd.outcomes.items()
            if outcome.ok and outcome.payload
        }

    def _histories(self) -> dict[AgentName, list[dict[str, Any]]]:
        out: dict[AgentName, list[dict[str, Any]]] = {a: [] for a in self.council}
        for rnd in self.rounds:
            for agent_name, outcome in rnd.outcomes.items():
                if not outcome.ok or not outcome.payload:
                    continue
                try:
                    agent = AgentName(agent_name)
                except ValueError:
                    continue
                out.setdefault(agent, []).append(
                    {
                        "round": rnd.index,
                        "kind": rnd.kind.value,
                        "payload": outcome.payload,
                    }
                )
        # Keep round 0 (the agent's own independent answer) plus the most
        # recent entries, so prompts stay bounded on long debates.
        for agent, items in out.items():
            if len(items) > MAX_HISTORY_ENTRIES:
                out[agent] = [items[0]] + items[-(MAX_HISTORY_ENTRIES - 1) :]
        return out

    def _failed_agents(self) -> list[str]:
        if not self.rounds:
            return []
        last = self.rounds[-1]
        return sorted(
            agent
            for agent, outcome in last.outcomes.items()
            if not outcome.ok and outcome.status != AgentStatus.DISABLED
        )

    def _cost(self) -> CostReport:
        table = PricingTable(self.settings.load_pricing())
        per_agent = {
            agent: (self.models.get(agent, ""), usage)
            for agent, usage in self.usage.items()
        }
        return build_cost_report(per_agent, table)

    # --------------------------------------------------------------------- run
    async def run(self) -> DebateDetail:
        started = _now()
        try:
            await self._emit(
                EventType.DEBATE_STARTED,
                {
                    "debate_id": self.debate_id,
                    "question": self.question,
                    "agents": [a.value for a in self.council],
                    "models": self.models,
                    "roles": self.config.roles,
                    "config": self.config.model_dump(mode="json"),
                },
            )
            if self.repo is not None:
                await self.repo.set_status(self.debate_id, DebateStatus.RUNNING)

            consensus: ConsensusReport | None = None

            # ------------------------------------------------- ROUND 0
            await self._emit(
                EventType.ROUND_STARTED,
                {"round": 0, "kind": RoundKind.INITIAL.value,
                 "agents": [a.value for a in self.council]},
            )
            round_id = (
                await self.repo.start_round(self.debate_id, 0, RoundKind.INITIAL.value)
                if self.repo
                else None
            )
            outcomes = await self.round_manager.run_initial(list(self.council.keys()))
            rnd0 = RoundResult(
                index=0, kind=RoundKind.INITIAL, started_at=started, finished_at=_now()
            )
            for agent, outcome in outcomes.items():
                rnd0.outcomes[agent.value] = outcome
                await self._persist_outcome(round_id, outcome)
            self.rounds.append(rnd0)
            if self.repo and round_id is not None:
                await self.repo.finish_round(round_id)
            await self._emit(
                EventType.ROUND_FINISHED,
                {
                    "round": 0,
                    "kind": RoundKind.INITIAL.value,
                    "ok": sorted(self._valid_payloads(rnd0).keys()),
                    "failed": sorted(
                        a for a, o in rnd0.outcomes.items() if not o.ok
                    ),
                },
            )

            initial_ok = self._valid_payloads(rnd0)
            if not initial_ok:
                raise RuntimeError(
                    "No agent produced a valid initial answer — check API keys, "
                    "model names and network access."
                )

            # A debate needs an opponent. With a single survivor the loop would
            # spend MAX_ROUNDS rounds arguing with nobody, at full token cost,
            # and the consensus engine would reject every one of them anyway.
            solo = len(initial_ok) < 2
            if solo:
                log.warning(
                    "Only %s produced a valid answer; skipping the debate rounds.",
                    ", ".join(sorted(initial_ok)),
                )
                consensus = ConsensusReport(
                    round_index=0,
                    reached=False,
                    score=0.0,
                    threshold=self.config.consensus_threshold,
                    agree_count=0,
                    participant_count=len(initial_ok),
                    rule="insufficient_participants",
                    reason=(
                        f"Only {', '.join(sorted(initial_ok))} answered. A debate "
                        "needs at least two working providers, so no debate round "
                        "was run and no tokens were spent on one."
                    ),
                )
                if self.repo is not None and round_id is not None:
                    await self.repo.save_consensus(self.debate_id, round_id, consensus)
                rnd0.consensus = consensus
                await self._emit(
                    EventType.CONSENSUS_CHECK,
                    {"round": 0, **consensus.model_dump(mode="json")},
                )

            # ------------------------------------------------- ROUNDS 1..N
            for round_index in range(1, 0 if solo else self.config.max_rounds + 1):
                previous = self.rounds[-1]
                previous_payloads = self._valid_payloads(previous)

                await self._emit(
                    EventType.ROUND_STARTED,
                    {
                        "round": round_index,
                        "kind": RoundKind.DEBATE.value,
                        "agents": [a.value for a in self.council],
                    },
                )
                round_id = (
                    await self.repo.start_round(
                        self.debate_id, round_index, RoundKind.DEBATE.value
                    )
                    if self.repo
                    else None
                )
                round_started = _now()

                debate_outcomes = await self.round_manager.run_debate(
                    list(self.council.keys()),
                    round_index=round_index,
                    histories=self._histories(),
                    previous_payloads=previous_payloads,
                    previous_kind=previous.kind.value,
                    previous_consensus=(
                        consensus.model_dump(mode="json") if consensus else None
                    ),
                    roles=self.config.roles,
                )

                rnd = RoundResult(
                    index=round_index,
                    kind=RoundKind.DEBATE,
                    started_at=round_started,
                    finished_at=_now(),
                )
                for agent, outcome in debate_outcomes.items():
                    rnd.outcomes[agent.value] = outcome
                    await self._persist_outcome(round_id, outcome)

                positions = self._valid_payloads(rnd)

                # --------------------------------------- consensus voting
                consensus = await self._consensus_phase(
                    round_index=round_index,
                    positions=positions,
                    debate_outcomes=debate_outcomes,
                    round_id=round_id,
                )
                rnd.consensus = consensus
                self.rounds.append(rnd)

                if self.repo and round_id is not None:
                    await self.repo.finish_round(round_id)

                await self._emit(
                    EventType.ROUND_FINISHED,
                    {
                        "round": round_index,
                        "kind": RoundKind.DEBATE.value,
                        "ok": sorted(positions.keys()),
                        "failed": sorted(a for a, o in rnd.outcomes.items() if not o.ok),
                        "consensus_reached": consensus.reached if consensus else False,
                        "consensus_score": consensus.score if consensus else 0.0,
                    },
                )

                if consensus and consensus.reached and round_index >= self.config.min_rounds:
                    log.info(
                        "Consensus reached after round %d (rule=%s, score=%.2f)",
                        round_index,
                        consensus.rule,
                        consensus.score,
                    )
                    break

                if not positions:
                    log.warning(
                        "No agent produced a valid debate response in round %d; "
                        "stopping the debate loop.",
                        round_index,
                    )
                    break
            else:  # pragma: no cover - defensive; loop is already bounded
                if not solo:
                    log.info("MAX_ROUNDS reached without consensus")

            if consensus is not None and not consensus.reached and not solo:
                consensus.rule = (
                    "max_rounds_reached"
                    if consensus.rule == "not_reached"
                    else consensus.rule
                )
                consensus.reason = (
                    consensus.reason
                    + " Final round completed; the last consensus vote is treated "
                    "as the final vote."
                )

            # ------------------------------------------------- SYNTHESIS
            await self._emit(EventType.SYNTHESIS_STARTED, {"round": len(self.rounds) - 1})
            synthesis, synth_agent, attempts = await run_synthesis(
                council=self.council,
                rounds=self.rounds,
                consensus=consensus,
                question=self.question,
                failed_agents=self._failed_agents(),
                preferred=self.settings.synthesis_agent,
                roles=self.config.roles,
            )
            for agent, outcome in attempts:
                self._account(outcome)
                if self.repo is not None and outcome.usage.total_tokens:
                    await self.repo.add_usage(
                        self.debate_id, agent.value, outcome.model, outcome.usage
                    )

            rounds_used = max(0, len(self.rounds) - 1)
            if self.repo is not None:
                await self.repo.finalize(
                    self.debate_id,
                    rounds_used=rounds_used,
                    consensus_reached=bool(consensus and consensus.reached),
                    consensus_score=float(consensus.score if consensus else 0.0),
                    synthesis=synthesis.model_dump(mode="json"),
                    synthesis_agent=synth_agent,
                )

            detail = DebateDetail(
                id=self.debate_id,
                question=self.question,
                status=DebateStatus.COMPLETED,
                created_at=started,
                finished_at=_now(),
                rounds_used=rounds_used,
                max_rounds=self.config.max_rounds,
                consensus_reached=bool(consensus and consensus.reached),
                consensus_score=float(consensus.score if consensus else 0.0),
                config=self.config,
                rounds=self.rounds,
                synthesis=synthesis,
                synthesis_agent=synth_agent,
                cost=self._cost(),
            )

            await self._emit(
                EventType.DEBATE_FINISHED,
                {
                    "debate_id": self.debate_id,
                    "rounds_used": rounds_used,
                    "max_rounds": self.config.max_rounds,
                    "consensus_reached": detail.consensus_reached,
                    "consensus_score": detail.consensus_score,
                    "synthesis_agent": synth_agent,
                    "cost": detail.cost.model_dump(mode="json") if detail.cost else None,
                },
            )
            return detail

        except asyncio.CancelledError:
            self.error = "Debate cancelled"
            if self.repo is not None:
                await self.repo.set_status(
                    self.debate_id, DebateStatus.FAILED, error=self.error, finished=True
                )
            await self._emit(EventType.DEBATE_ERROR, {"error": self.error})
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced to the client
            log.exception("Debate %s failed", self.debate_id)
            self.error = f"{type(exc).__name__}: {exc}"
            if self.repo is not None:
                await self.repo.set_status(
                    self.debate_id, DebateStatus.FAILED, error=self.error, finished=True
                )
            await self._emit(EventType.DEBATE_ERROR, {"error": self.error})
            return DebateDetail(
                id=self.debate_id,
                question=self.question,
                status=DebateStatus.FAILED,
                created_at=started,
                finished_at=_now(),
                rounds_used=max(0, len(self.rounds) - 1),
                max_rounds=self.config.max_rounds,
                config=self.config,
                rounds=self.rounds,
                cost=self._cost(),
                error=self.error,
            )
        finally:
            if self.bus is not None:
                self.bus.close(self.debate_id)

    # ------------------------------------------------------------- consensus
    async def _consensus_phase(
        self,
        *,
        round_index: int,
        positions: dict[str, dict[str, Any]],
        debate_outcomes: dict[AgentName, AgentOutcome],
        round_id: int | None,
    ) -> ConsensusReport:
        voters = [
            AgentName(a) for a in positions.keys() if a in AgentName.__members__
        ]
        vote_outcomes = await self.round_manager.run_votes(
            voters, round_index=round_index, positions=positions
        )

        votes: dict[str, ConsensusVote] = {}
        for agent, outcome in vote_outcomes.items():
            self._account(outcome)
            if self.repo is not None and outcome.usage.total_tokens:
                await self.repo.add_usage(
                    self.debate_id, agent.value, outcome.model, outcome.usage
                )
            if outcome.ok and outcome.payload:
                try:
                    votes[agent.value] = ConsensusVote.model_validate(outcome.payload)
                except Exception:  # noqa: BLE001
                    log.warning("Discarding malformed consensus vote from %s", agent.value)

        debates: dict[str, DebateResponse] = {}
        for agent, outcome in debate_outcomes.items():
            if outcome.ok and outcome.payload:
                try:
                    debates[agent.value] = DebateResponse.model_validate(outcome.payload)
                except Exception:  # noqa: BLE001
                    pass

        report = self.engine.evaluate(
            round_index=round_index, votes=votes, debates=debates
        )

        if self.repo is not None and round_id is not None:
            await self.repo.save_consensus(self.debate_id, round_id, report)

        await self._emit(
            EventType.CONSENSUS_CHECK,
            {
                "round": round_index,
                **report.model_dump(mode="json"),
            },
        )
        return report

    # ------------------------------------------------------------------ close
    async def aclose(self) -> None:
        await asyncio.gather(
            *(agent.aclose() for agent in self.council.values()), return_exceptions=True
        )
