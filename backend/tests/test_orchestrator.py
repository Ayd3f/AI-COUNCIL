"""End-to-end debate runs against mock providers."""

from __future__ import annotations

from typing import Any

from ..agents.registry import ROSTER
from ..debate.orchestrator import DebateOrchestrator
from ..models.enums import AgentName, DebateStatus, RoundKind
from ..models.schemas import DebateConfig
from ..services.events import EventBus
from ..services.storage import DebateRepository
from .mock_provider import (
    Boom,
    MockAgent,
    debate_payload,
    initial_payload,
    synthesis_payload,
    vote_payload,
)


def make_config(**overrides: Any) -> DebateConfig:
    base = dict(
        agents=list(ROSTER),
        min_rounds=1,
        max_rounds=3,
        consensus_threshold=0.8,
        timeout=10,
        temperature=0.7,
        max_retries=1,
        models={a.value: "mock-model-1" for a in ROSTER},
    )
    base.update(overrides)
    return DebateConfig(**base)  # type: ignore[arg-type]


async def run_debate(council, config=None, repo=None, bus=None, settings=None):
    config = config or make_config(agents=list(council.keys()))
    orch = DebateOrchestrator(
        debate_id="dbt_test",
        question="Which approach should we use, X or Y?",
        config=config,
        council=council,
        repo=repo,
        bus=bus,
        settings=settings,
    )
    return await orch.run(), orch


# --------------------------------------------------------------------------- #
# 1. Happy path — all five providers succeed
# --------------------------------------------------------------------------- #


async def test_every_agent_succeeds_and_debate_completes(settings):
    council = {n: MockAgent(n) for n in ROSTER}
    detail, _ = await run_debate(council, settings=settings)

    assert detail.status is DebateStatus.COMPLETED
    assert detail.rounds[0].kind is RoundKind.INITIAL
    assert len(detail.rounds[0].outcomes) == len(ROSTER)
    assert all(o.ok for o in detail.rounds[0].outcomes.values())
    assert detail.synthesis is not None
    assert detail.synthesis.consensus
    assert detail.consensus_reached is True


async def test_round_zero_answers_are_independent(settings):
    """No agent may see another agent's answer during round 0."""
    council = {n: MockAgent(n) for n in ROSTER}
    await run_debate(council, settings=settings)
    for name, agent in council.items():
        initial_prompt = next(p for k, p in agent.prompts if k == "initial_answer")
        for other in ROSTER:
            if other is name:
                continue
            assert f"### {other.value}" not in initial_prompt


async def test_debate_round_shows_every_other_agent(settings):
    council = {n: MockAgent(n) for n in ROSTER}
    await run_debate(council, settings=settings)
    for name, agent in council.items():
        debate_prompt = next(p for k, p in agent.prompts if k == "debate_response")
        for other in ROSTER:
            if other is name:
                continue
            assert f"### {other.value}" in debate_prompt
        assert f"### {name.value}" not in debate_prompt


# --------------------------------------------------------------------------- #
# 2. One provider down — the round must continue
# --------------------------------------------------------------------------- #


async def test_one_failing_provider_does_not_break_the_round(settings):
    council = {n: MockAgent(n) for n in ROSTER}
    council[AgentName.GEMINI] = MockAgent(
        AgentName.GEMINI, initial=Boom("API timeout"), debate=Boom("API timeout")
    )
    detail, _ = await run_debate(council, settings=settings)

    assert detail.status is DebateStatus.COMPLETED
    gemini = detail.rounds[0].outcomes["GEMINI"]
    assert not gemini.ok
    assert gemini.error and "API timeout" in gemini.error
    ok_agents = [a for a, o in detail.rounds[0].outcomes.items() if o.ok]
    assert len(ok_agents) == len(ROSTER) - 1
    # Оставшиеся здоровые участники всё равно пришли к выводу.
    assert detail.synthesis is not None


async def test_failed_agent_can_rejoin_in_a_later_round(settings):
    def fail_first_round(call_index: int):
        return Boom("transient outage") if call_index == 1 else debate_payload()

    council = {n: MockAgent(n) for n in ROSTER}
    council[AgentName.GROK] = MockAgent(
        AgentName.GROK,
        debate=fail_first_round,
        vote=vote_payload(agrees=False, level=0.3, objection=True),
    )
    config = make_config(min_rounds=2, max_rounds=3)
    detail, _ = await run_debate(council, config=config, settings=settings)

    assert detail.rounds[1].outcomes["GROK"].ok is False
    assert detail.rounds[2].outcomes["GROK"].ok is True


async def test_a_single_survivor_does_not_debate_alone(settings):
    """Спорить не с кем — раунды не запускаются и токены не тратятся."""
    council = {n: MockAgent(n, initial=Boom("no credits")) for n in ROSTER}
    council[AgentName.GEMINI] = MockAgent(AgentName.GEMINI)
    config = make_config(min_rounds=1, max_rounds=5)
    detail, orch = await run_debate(council, config=config, settings=settings)

    assert detail.status is DebateStatus.COMPLETED
    assert len(detail.rounds) == 1                 # только раунд 0
    assert detail.rounds_used == 0
    assert detail.consensus_reached is False
    assert detail.rounds[0].consensus.rule == "insufficient_participants"
    assert "GEMINI" in detail.rounds[0].consensus.reason
    # ни одного вызова debate/vote у выжившего
    assert "debate_response" not in council[AgentName.GEMINI].calls
    assert "consensus_vote" not in council[AgentName.GEMINI].calls
    assert detail.synthesis is not None


async def test_debate_fails_cleanly_when_every_provider_is_down(settings):
    council = {n: MockAgent(n, initial=Boom("no network")) for n in ROSTER}
    detail, _ = await run_debate(council, settings=settings)
    assert detail.status is DebateStatus.FAILED
    assert detail.error and "valid initial answer" in detail.error


# --------------------------------------------------------------------------- #
# 3. Consensus behaviour
# --------------------------------------------------------------------------- #


async def test_consensus_stops_the_debate_early(settings):
    council = {n: MockAgent(n) for n in ROSTER}
    config = make_config(min_rounds=1, max_rounds=5)
    detail, _ = await run_debate(council, config=config, settings=settings)

    assert detail.consensus_reached is True
    assert detail.rounds_used == 1  # stopped after the first debate round
    assert detail.rounds[-1].consensus.rule == "unanimous_conclusion"


async def test_min_rounds_is_respected_even_with_instant_agreement(settings):
    council = {n: MockAgent(n) for n in ROSTER}
    config = make_config(min_rounds=2, max_rounds=5)
    detail, _ = await run_debate(council, config=config, settings=settings)
    assert detail.rounds_used == 2
    assert detail.rounds[1].consensus.reached is False
    assert detail.rounds[1].consensus.rule == "min_rounds_not_met"
    assert detail.rounds[2].consensus.reached is True


async def test_no_consensus_runs_to_max_rounds_and_stops(settings):
    """Deadlock must terminate at MAX_ROUNDS — never loop forever."""
    council = {
        n: MockAgent(n, vote=vote_payload(agrees=False, level=0.2, objection=True))
        for n in ROSTER
    }
    config = make_config(min_rounds=1, max_rounds=3)
    detail, _ = await run_debate(council, config=config, settings=settings)

    assert detail.consensus_reached is False
    assert detail.rounds_used == 3
    assert len(detail.rounds) == 4  # round 0 + three debate rounds
    assert detail.rounds[-1].consensus.rule == "max_rounds_reached"
    assert detail.synthesis is not None  # final synthesis still produced


async def test_supermajority_with_one_soft_dissenter(settings):
    council = {n: MockAgent(n) for n in ROSTER}
    council[AgentName.DEEPSEEK] = MockAgent(
        AgentName.DEEPSEEK,
        vote=vote_payload(agrees=False, level=0.6, objection=False),
    )
    detail, _ = await run_debate(council, settings=settings)
    report = detail.rounds[-1].consensus
    assert report.reached is True
    assert report.rule == "supermajority"
    assert report.dissenting_agents == ["DEEPSEEK"]


# --------------------------------------------------------------------------- #
# 4. Minority protection (requirement #23)
# --------------------------------------------------------------------------- #


async def test_minority_position_survives_a_synthesis_that_omits_it(settings):
    council = {n: MockAgent(n) for n in ROSTER}
    council[AgentName.DEEPSEEK] = MockAgent(
        AgentName.DEEPSEEK,
        debate=debate_payload(position="Y is correct because of the cold-start path."),
        vote=vote_payload(agrees=False, level=0.25, objection=True),
    )
    # Every synthesiser returns a synthesis with an empty minority list.
    for name in ROSTER:
        council[name]._behaviours["synthesis_result"] = synthesis_payload()

    config = make_config(min_rounds=1, max_rounds=1)
    detail, _ = await run_debate(council, config=config, settings=settings)

    assert detail.synthesis is not None
    minority_agents = {m.agent for m in detail.synthesis.minority_positions}
    assert "DEEPSEEK" in minority_agents
    entry = next(m for m in detail.synthesis.minority_positions if m.agent == "DEEPSEEK")
    assert "cold-start" in entry.position
    assert entry.evidence


async def test_individual_positions_cover_every_agent(settings):
    council = {n: MockAgent(n) for n in ROSTER}
    detail, _ = await run_debate(council, settings=settings)
    covered = {p.agent for p in detail.synthesis.individual_positions}
    assert covered == {a.value for a in ROSTER}


# --------------------------------------------------------------------------- #
# 5. Synthesis resilience
# --------------------------------------------------------------------------- #


async def test_synthesis_falls_back_to_the_next_agent(settings):
    council = {n: MockAgent(n) for n in ROSTER}
    council[ROSTER[0]] = MockAgent(ROSTER[0], synthesis=Boom("synthesis unavailable"))
    detail, _ = await run_debate(council, settings=settings)
    assert detail.synthesis_agent is not None
    assert detail.synthesis_agent != ROSTER[0].value


async def test_deterministic_synthesis_when_every_model_fails(settings):
    council = {n: MockAgent(n, synthesis=Boom("down")) for n in ROSTER}
    detail, _ = await run_debate(council, settings=settings)
    assert detail.synthesis_agent is None
    assert detail.synthesis is not None
    assert "deterministically" in detail.synthesis.consensus
    assert detail.synthesis.individual_positions


# --------------------------------------------------------------------------- #
# 6. Token accounting (requirement #21)
# --------------------------------------------------------------------------- #


async def test_token_usage_is_tracked_per_agent(settings):
    council = {n: MockAgent(n, input_tokens=100, output_tokens=40) for n in ROSTER}
    config = make_config(min_rounds=1, max_rounds=1)
    detail, orch = await run_debate(council, config=config, settings=settings)

    assert set(orch.usage) == {a.value for a in ROSTER}
    # round 0 + debate round + consensus vote = 3 calls per agent
    for name, usage in orch.usage.items():
        assert usage.input_tokens >= 300
        assert usage.output_tokens >= 120

    assert detail.cost is not None
    assert detail.cost.total_tokens == sum(u.total_tokens for u in orch.usage.values())
    # mock-model-1 has no pricing entry -> reported as unknown, never as $0
    assert detail.cost.complete is False
    assert "mock-model-1" in detail.cost.models_without_pricing
    assert all(line.pricing_known is False for line in detail.cost.lines)


async def test_cost_is_computed_when_pricing_is_configured(settings):
    council = {
        n: MockAgent(n, model="claude-opus-5", input_tokens=1_000_000, output_tokens=0)
        for n in ROSTER
    }
    config = make_config(min_rounds=1, max_rounds=1)
    detail, _ = await run_debate(council, config=config, settings=settings)
    line = detail.cost.lines[0]
    assert line.pricing_known is True
    assert line.estimated_cost_usd and line.estimated_cost_usd > 0


# --------------------------------------------------------------------------- #
# 7. Persistence + events
# --------------------------------------------------------------------------- #


async def test_rounds_and_events_are_persisted(settings, repo: DebateRepository, bus: EventBus):
    council = {n: MockAgent(n) for n in ROSTER}
    config = make_config(min_rounds=1, max_rounds=1)
    await repo.create_debate("dbt_test", "cnv_test", "Which approach?", config)

    detail, _ = await run_debate(council, config=config, repo=repo, bus=bus, settings=settings)
    assert detail.status is DebateStatus.COMPLETED

    stored_rounds = await repo.get_rounds("dbt_test")
    assert len(stored_rounds) == 2
    assert set(stored_rounds[0].outcomes) == {a.value for a in ROSTER}
    assert stored_rounds[1].consensus is not None

    events = await repo.get_events("dbt_test")
    types = [e.type for e in events]
    for expected in (
        "debate_started",
        "round_started",
        "agent_started",
        "agent_finished",
        "consensus_check",
        "round_finished",
        "synthesis_started",
        "debate_finished",
    ):
        assert expected in types, f"missing event {expected}"

    usage = await repo.get_usage("dbt_test")
    assert set(usage) == {a.value for a in ROSTER}


async def test_agent_failed_event_carries_a_reason(settings, repo, bus):
    council = {n: MockAgent(n) for n in ROSTER}
    council[AgentName.GEMINI] = MockAgent(AgentName.GEMINI, initial=Boom("API timeout"))
    config = make_config(min_rounds=1, max_rounds=1)
    await repo.create_debate("dbt_test", "cnv_test", "q", config)
    await run_debate(council, config=config, repo=repo, bus=bus, settings=settings)

    events = await repo.get_events("dbt_test")
    failed = [e for e in events if e.type == "agent_failed"]
    assert failed
    assert failed[0].data["agent"] == "GEMINI"
    assert "API timeout" in failed[0].data["reason"]
    assert failed[0].data["status"] in {"ERROR", "INVALID_OUTPUT"}
