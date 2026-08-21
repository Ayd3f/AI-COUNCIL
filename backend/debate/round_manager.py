"""Round execution.

Every phase of a round fans out to all active agents **simultaneously**
(`asyncio.gather`), never sequentially.  One agent failing never cancels the
others: each agent returns an `AgentOutcome` describing its own fate and the
round continues with whoever survived.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from ..agents.base import BaseAgent
from ..models.enums import AgentName, AgentStatus, EventType
from ..models.schemas import AgentOutcome, TokenUsage
from ..services.logging import get_logger

log = get_logger(__name__)

EmitFn = Callable[[EventType, dict[str, Any]], Awaitable[None]]


class RoundManager:
    def __init__(
        self,
        *,
        council: dict[AgentName, BaseAgent],
        emit: EmitFn,
        question: str,
        max_rounds: int,
    ) -> None:
        self.council = council
        self.emit = emit
        self.question = question
        self.max_rounds = max_rounds

    # ------------------------------------------------------------------ phases
    async def run_initial(self, agents: list[AgentName]) -> dict[AgentName, AgentOutcome]:
        return await self._fan_out(
            agents,
            phase="initial",
            round_index=0,
            call=lambda agent: self.council[agent].generate_initial_answer(self.question),
        )

    async def run_debate(
        self,
        agents: list[AgentName],
        *,
        round_index: int,
        histories: dict[AgentName, list[dict[str, Any]]],
        previous_payloads: dict[str, dict[str, Any]],
        previous_kind: str,
        previous_consensus: dict[str, Any] | None,
        roles: dict[str, str] | None = None,
    ) -> dict[AgentName, AgentOutcome]:
        def make_call(agent: AgentName) -> Awaitable[AgentOutcome]:
            others = {
                name: payload
                for name, payload in previous_payloads.items()
                if name != agent.value
            }
            return self.council[agent].debate(
                question=self.question,
                round_index=round_index,
                max_rounds=self.max_rounds,
                own_history=histories.get(agent, []),
                others=others,
                others_kind=previous_kind,
                previous_consensus=previous_consensus,
                roles=roles,
            )

        return await self._fan_out(
            agents, phase="debate", round_index=round_index, call=make_call
        )

    async def run_votes(
        self,
        agents: list[AgentName],
        *,
        round_index: int,
        positions: dict[str, dict[str, Any]],
    ) -> dict[AgentName, AgentOutcome]:
        return await self._fan_out(
            agents,
            phase="consensus_vote",
            round_index=round_index,
            call=lambda agent: self.council[agent].evaluate_consensus(
                question=self.question, round_index=round_index, positions=positions
            ),
            announce=False,
        )

    # ------------------------------------------------------------------ engine
    async def _fan_out(
        self,
        agents: list[AgentName],
        *,
        phase: str,
        round_index: int,
        call: Callable[[AgentName], Awaitable[AgentOutcome]],
        announce: bool = True,
    ) -> dict[AgentName, AgentOutcome]:
        if not agents:
            return {}

        async def wrapped(agent: AgentName) -> AgentOutcome:
            if announce:
                await self.emit(
                    EventType.AGENT_STARTED,
                    {"agent": agent.value, "round": round_index, "phase": phase},
                )
            try:
                outcome = await call(agent)
            except asyncio.CancelledError:
                raise
            except BaseException as exc:  # noqa: BLE001 - one agent must not kill the round
                log.exception("Unhandled error from %s during %s", agent.value, phase)
                outcome = AgentOutcome(
                    agent=agent,
                    status=AgentStatus.ERROR,
                    model=self.council[agent].model,
                    error=f"{type(exc).__name__}: {exc}"[:1000],
                    error_kind="unexpected_error",
                    usage=TokenUsage(),
                )

            if announce:
                if outcome.ok:
                    await self.emit(
                        EventType.AGENT_FINISHED,
                        {
                            "agent": agent.value,
                            "round": round_index,
                            "phase": phase,
                            "latency_ms": outcome.latency_ms,
                            "attempts": outcome.attempts,
                            "confidence": (outcome.payload or {}).get("confidence"),
                            "tokens": outcome.usage.total_tokens,
                        },
                    )
                else:
                    await self.emit(
                        EventType.AGENT_FAILED,
                        {
                            "agent": agent.value,
                            "round": round_index,
                            "phase": phase,
                            "status": outcome.status.value,
                            "reason": outcome.error or "unknown error",
                            "error_kind": outcome.error_kind,
                            "attempts": outcome.attempts,
                        },
                    )
            return outcome

        results = await asyncio.gather(
            *(wrapped(a) for a in agents), return_exceptions=False
        )
        return {outcome.agent: outcome for outcome in results}
