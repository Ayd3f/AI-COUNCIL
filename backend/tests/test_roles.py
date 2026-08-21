"""Круглый стол: роли, убеждение и защита от продавленного согласия.

Главное, что здесь проверяется, — что «спорить, пока не придут к одному» не
превратилось в фабрику фальшивого консенсуса. Убеждать можно; уступать без
аргумента — нет.
"""

from __future__ import annotations

import pytest

from ..agents.prompts import build_debate_prompt, system_prompt
from ..agents.roles import DebateRole, assign_roles, spec
from ..debate.consensus import ConsensusEngine
from ..models.enums import AgentName
from ..models.schemas import ConsensusVote, DebateConfig, DebateResponse, InitialAnswer
from .mock_provider import (
    MockAgent,
    capitulation_payload,
    debate_payload,
    initial_payload,
    vote_payload,
)

ENGINE = ConsensusEngine(threshold=0.8, min_rounds=1)
FIVE = [
    AgentName.OPENAI,
    AgentName.CLAUDE,
    AgentName.GEMINI,
    AgentName.GROK,
    AgentName.DEEPSEEK,
]


# --------------------------------------------------------------------------- #
# Раздача ролей
# --------------------------------------------------------------------------- #


def test_every_agent_gets_a_role_and_the_advocate_is_unique():
    roles = assign_roles(FIVE)
    assert set(roles) == {a.value for a in FIVE}
    advocates = [a for a, r in roles.items() if r == DebateRole.ADVOCATE.value]
    assert len(advocates) == 1, "двое тянущих к одному ответу тянули бы в разные стороны"


def test_roles_are_distinct_while_there_are_enough_of_them():
    roles = assign_roles(FIVE)
    assert len(set(roles.values())) == len(FIVE)


def test_roles_repeat_only_after_the_rotation_is_exhausted():
    roles = assign_roles(list(AgentName))          # девять участников, шесть ролей
    assert len(set(roles.values())) == 6
    advocates = [a for a, r in roles.items() if r == DebateRole.ADVOCATE.value]
    assert len(advocates) == 1


def test_assignment_is_deterministic():
    assert assign_roles(FIVE) == assign_roles(FIVE)


def test_advocate_can_be_chosen_explicitly():
    roles = assign_roles(FIVE, AgentName.DEEPSEEK)
    assert roles["DEEPSEEK"] == DebateRole.ADVOCATE.value
    assert roles["OPENAI"] != DebateRole.ADVOCATE.value


@pytest.mark.parametrize("bad", ["AUTO", "", "NOBODY", AgentName.MISTRAL])
def test_unknown_or_absent_advocate_falls_back_to_the_first_agent(bad):
    roles = assign_roles(FIVE, bad)
    assert roles["OPENAI"] == DebateRole.ADVOCATE.value


def test_empty_roster_gives_no_roles():
    assert assign_roles([]) == {}


@pytest.mark.parametrize("role", list(DebateRole))
def test_every_role_has_a_label_and_an_instruction(role: DebateRole):
    s = spec(role)
    assert s.label_ru and s.short_ru
    assert len(s.instruction) > 200
    assert "YOUR ROLE" in s.instruction


# --------------------------------------------------------------------------- #
# Роль попадает в промпт
# --------------------------------------------------------------------------- #


def test_role_instruction_reaches_the_system_prompt():
    text = system_prompt(AgentName.OPENAI, DebateRole.ADVOCATE.value)
    assert "YOUR ROLE IS THE ADVOCATE" in text
    assert "Your identity in this council is OPENAI" in text


def test_unknown_role_does_not_break_the_prompt():
    text = system_prompt(AgentName.OPENAI, "NOT_A_ROLE")
    assert "Your identity in this council is OPENAI" in text


def test_round_zero_has_no_role_so_the_baseline_stays_independent():
    assert "YOUR ROLE" not in system_prompt(AgentName.OPENAI)


def test_debate_prompt_shows_the_table_and_demands_persuasion():
    roles = assign_roles(FIVE)
    prompt = build_debate_prompt(
        question="X or Y?",
        round_index=1,
        max_rounds=3,
        own_history=[],
        others={"CLAUDE": debate_payload()},
        others_kind="DEBATE",
        previous_consensus=None,
        roles=roles,
        agent=AgentName.OPENAI,
    )
    assert "WHO IS AT THE TABLE AND IN WHICH ROLE" in prompt
    assert "OPENAI: ADVOCATE" in prompt
    assert "Your role in this debate is ADVOCATE." in prompt
    assert "persuasion" in prompt
    assert "capitulation" in prompt
    assert "Rounds remaining after this one: 2" in prompt


def test_last_round_says_so_explicitly():
    prompt = build_debate_prompt(
        question="X or Y?",
        round_index=3,
        max_rounds=3,
        own_history=[],
        others={},
        others_kind="DEBATE",
        previous_consensus=None,
    )
    assert "LAST round" in prompt


# --------------------------------------------------------------------------- #
# Убеждение и капитуляция
# --------------------------------------------------------------------------- #


def test_persuasion_attempts_count_as_engagement():
    r = DebateResponse.model_validate(debate_payload())
    assert r.persuasion_score == 1
    assert r.engagement_score >= 3
    assert r.is_capitulation is False


def test_position_change_with_a_named_source_is_honest():
    r = DebateResponse.model_validate(debate_payload(changed=True))
    assert r.changed_my_position is True
    assert r.persuaded_by == ["CLAUDE"]
    assert r.is_capitulation is False


def test_unexplained_switch_is_capitulation():
    r = DebateResponse.model_validate(capitulation_payload())
    assert r.changed_my_position is True
    assert r.is_capitulation is True


def test_switch_with_a_source_but_no_reason_is_still_capitulation():
    payload = dict(capitulation_payload())
    payload["persuaded_by"] = ["OPENAI"]
    payload["why_changed"] = "ок"
    assert DebateResponse.model_validate(payload).is_capitulation is True


def test_capitulation_does_not_count_towards_consensus():
    """Ровно та ситуация, ради которой это ограничение и введено."""
    names = [a.value for a in FIVE]
    debates = {n: DebateResponse.model_validate(debate_payload()) for n in names[:3]}
    for n in names[3:]:
        debates[n] = DebateResponse.model_validate(capitulation_payload())

    report = ENGINE.evaluate(
        round_index=1,
        votes={n: ConsensusVote.model_validate(vote_payload(level=0.95)) for n in names},
        debates=debates,
    )
    assert sorted(report.capitulated_agents) == sorted(names[3:])
    assert report.agree_count == 3
    assert report.reached is False
    assert "capitulation" in report.reason


def test_persuasion_edges_record_who_moved_whom():
    names = [a.value for a in FIVE]
    debates = {n: DebateResponse.model_validate(debate_payload()) for n in names}
    debates["GROK"] = DebateResponse.model_validate(
        debate_payload(changed=True, accepted_from="CLAUDE")
    )
    report = ENGINE.evaluate(
        round_index=1,
        votes={n: ConsensusVote.model_validate(vote_payload(level=0.95)) for n in names},
        debates=debates,
    )
    assert {"agent": "GROK", "persuaded_by": "CLAUDE"} in report.persuasion_edges
    assert report.reached is True   # честная сходимость не пострадала


# --------------------------------------------------------------------------- #
# Сквозной прогон
# --------------------------------------------------------------------------- #


def _config(agents, roles) -> DebateConfig:
    return DebateConfig(
        agents=agents,
        min_rounds=1,
        max_rounds=1,
        consensus_threshold=0.8,
        timeout=10,
        temperature=0.7,
        max_retries=0,
        models={a.value: "mock-model-1" for a in agents},
        roles=roles,
    )


async def test_roles_reach_the_agents_during_a_debate(settings):
    from ..agents.registry import ROSTER
    from ..debate.orchestrator import DebateOrchestrator

    council = {n: MockAgent(n) for n in ROSTER}
    roles = assign_roles(list(ROSTER))
    orch = DebateOrchestrator(
        debate_id="dbt_roles",
        question="Монолит или микросервисы?",
        config=_config(list(ROSTER), roles),
        council=council,
        settings=settings,
    )
    detail = await orch.run()
    assert detail.rounds[1].kind.value == "DEBATE"

    for agent, mock in council.items():
        initial = next(p for k, p in mock.prompts if k == "initial_answer")
        debate = next(p for k, p in mock.prompts if k == "debate_response")
        # нулевой раунд остаётся независимым
        assert "WHO IS AT THE TABLE" not in initial
        # в споре агент знает свою роль и роли остальных
        assert f"{agent.value}: {roles[agent.value]}" in debate
        assert "Your role in this debate is" in debate


async def test_roles_can_be_switched_off(settings):
    """Прежнее поведение остаётся доступным — это настройка, а не догма."""
    from ..debate.orchestrator import DebateOrchestrator

    council = {n: MockAgent(n) for n in FIVE}
    orch = DebateOrchestrator(
        debate_id="dbt_noroles",
        question="q",
        config=_config(FIVE, {}),
        council=council,
        settings=settings,
    )
    await orch.run()
    debate = next(p for k, p in council[AgentName.OPENAI].prompts if k == "debate_response")
    assert "WHO IS AT THE TABLE" not in debate


def test_initial_answer_schema_is_unaffected_by_roles():
    """Роли не должны просачиваться в схему нулевого раунда."""
    answer = InitialAnswer.model_validate(initial_payload())
    assert not hasattr(answer, "persuasion")
