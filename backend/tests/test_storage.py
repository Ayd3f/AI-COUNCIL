"""Persistence under concurrency.

A debate round writes from five agent tasks at once. SQLite serialises
writers, so before the write lock in `DebateRepository` the losers of that
race raised "database is locked" and their rows were silently dropped by the
orchestrator's telemetry guard.
"""

from __future__ import annotations

import asyncio

import pytest

from ..models.enums import AgentName, AgentStatus, DebateStatus, RoundKind
from ..models.schemas import AgentOutcome, ConsensusReport, DebateConfig, TokenUsage
from ..services.events import Event
from ..services.storage import DebateRepository

AGENTS = list(AgentName)


def _config() -> DebateConfig:
    return DebateConfig(
        agents=AGENTS,
        min_rounds=1,
        max_rounds=3,
        consensus_threshold=0.8,
        timeout=30,
        temperature=0.7,
        max_retries=1,
        models={a.value: "m" for a in AGENTS},
    )


async def test_parallel_writes_are_not_lost(repo: DebateRepository):
    await repo.create_debate("dbt_c", "cnv_c", "q", _config())
    round_id = await repo.start_round("dbt_c", 0, RoundKind.INITIAL.value)

    async def write(agent: AgentName, i: int) -> None:
        await repo.save_agent_outcome(
            "dbt_c",
            round_id,
            AgentOutcome(
                agent=agent,
                status=AgentStatus.OK,
                model="m",
                payload={"answer": "a", "key_points": [], "confidence": 0.5, "assumptions": []},
                usage=TokenUsage(input_tokens=10, output_tokens=5),
            ),
        )
        await repo.add_usage("dbt_c", agent.value, "m", TokenUsage(input_tokens=10, output_tokens=5))
        await repo.save_event(
            Event(seq=i, type="agent_finished", debate_id="dbt_c", data={"agent": agent.value})
        )

    # Каждый агент пишет 8 раз одновременно — заметно больше, чем переживает
    # несериализованный пул соединений SQLite.
    bursts = 8
    expected = len(AGENTS) * bursts
    await asyncio.gather(
        *(write(agent, i * 100 + n) for i, agent in enumerate(AGENTS) for n in range(bursts))
    )

    rounds = await repo.get_rounds("dbt_c")
    assert len(rounds) == 1

    events = await repo.get_events("dbt_c")
    assert len(events) == expected, f"события потеряны: {len(events)} из {expected}"

    usage = await repo.get_usage("dbt_c")
    assert set(usage) == {a.value for a in AGENTS}
    for _model, tokens in usage.values():
        # bursts вызовов на агента, каждый 10 вход / 5 выход — ничего не потеряно.
        assert tokens.input_tokens == 10 * bursts
        assert tokens.output_tokens == 5 * bursts


async def test_concurrent_usage_increments_do_not_race(repo: DebateRepository):
    await repo.create_debate("dbt_u", "cnv_u", "q", _config())
    await asyncio.gather(
        *(
            repo.add_usage("dbt_u", "OPENAI", "gpt-4o", TokenUsage(input_tokens=1, output_tokens=1))
            for _ in range(50)
        )
    )
    usage = await repo.get_usage("dbt_u")
    model, tokens = usage["OPENAI"]
    assert model == "gpt-4o"
    assert tokens.input_tokens == 50
    assert tokens.output_tokens == 50


async def test_start_round_returns_a_usable_id(repo: DebateRepository):
    await repo.create_debate("dbt_r", "cnv_r", "q", _config())
    ids = await asyncio.gather(
        *(repo.start_round("dbt_r", i, RoundKind.DEBATE.value) for i in range(5))
    )
    assert len(set(ids)) == 5
    assert all(isinstance(i, int) and i > 0 for i in ids)

    for round_id in ids:
        await repo.finish_round(round_id)
    rounds = await repo.get_rounds("dbt_r")
    assert len(rounds) == 5
    assert all(r.finished_at for r in rounds)


async def test_consensus_round_trips(repo: DebateRepository):
    await repo.create_debate("dbt_k", "cnv_k", "q", _config())
    round_id = await repo.start_round("dbt_k", 1, RoundKind.DEBATE.value)
    report = ConsensusReport(
        round_index=1,
        reached=False,
        score=0.72,
        threshold=0.8,
        agree_count=4,
        participant_count=5,
        rule="not_reached",
        reason="material objection from CLAUDE",
        dissenting_agents=["CLAUDE"],
    )
    await repo.save_consensus("dbt_k", round_id, report)

    rounds = await repo.get_rounds("dbt_k")
    stored = rounds[0].consensus
    assert stored is not None
    assert stored.score == pytest.approx(0.72)
    assert stored.dissenting_agents == ["CLAUDE"]


async def test_delete_and_clear(repo: DebateRepository):
    await repo.create_debate("dbt_a", "cnv_a", "q1", _config())
    await repo.create_debate("dbt_b", "cnv_b", "q2", _config())
    await repo.set_status("dbt_a", DebateStatus.COMPLETED, finished=True)

    assert await repo.delete_debate("dbt_a") is True
    assert await repo.delete_debate("dbt_a") is False
    assert len(await repo.list_debates()) == 1

    assert await repo.clear_all() == 1
    assert await repo.list_debates() == []
