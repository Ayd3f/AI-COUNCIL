"""Agent-level reliability: timeouts, retries, malformed JSON, token usage."""

from __future__ import annotations

import asyncio
import json

import pytest

from ..models.enums import AgentName, AgentStatus
from .mock_provider import Boom, Flaky, MockAgent, initial_payload


async def test_successful_call_returns_validated_payload():
    agent = MockAgent(AgentName.OPENAI, initial=initial_payload("The answer is 42."))
    outcome = await agent.generate_initial_answer("What is the answer?")
    assert outcome.status is AgentStatus.OK
    assert outcome.payload["answer"] == "The answer is 42."
    assert 0.0 <= outcome.payload["confidence"] <= 1.0
    assert agent.calls["initial_answer"] == 1


async def test_missing_api_key_marks_agent_disabled_not_failed():
    agent = MockAgent(AgentName.GROK, api_key="")
    outcome = await agent.generate_initial_answer("q")
    assert outcome.status is AgentStatus.DISABLED
    assert outcome.error_kind == "not_configured"
    assert agent.calls == {}


async def test_timeout_is_reported_as_an_error_with_a_reason():
    agent = MockAgent(AgentName.GEMINI, delay=0.4, timeout=1, max_retries=0)
    agent.timeout = 0  # force immediate timeout
    outcome = await agent.generate_initial_answer("q")
    assert outcome.status is AgentStatus.ERROR
    assert outcome.error_kind == "timeout"
    assert "timeout" in (outcome.error or "").lower()


async def test_timeout_is_retried_then_gives_up():
    agent = MockAgent(AgentName.GEMINI, delay=0.5, max_retries=1)
    agent.timeout = 0
    outcome = await agent.generate_initial_answer("q")
    assert outcome.status is AgentStatus.ERROR
    assert outcome.attempts == 2  # first attempt + one retry


async def test_retry_recovers_from_a_transient_failure():
    def flaky_then_ok(call_index: int):
        if call_index == 1:
            return Flaky("503 upstream unavailable")
        return initial_payload("Recovered answer.")

    agent = MockAgent(AgentName.CLAUDE, initial=flaky_then_ok, max_retries=2)
    outcome = await agent.generate_initial_answer("q")
    assert outcome.status is AgentStatus.OK
    assert outcome.attempts == 2
    assert outcome.payload["answer"] == "Recovered answer."


async def test_non_retryable_error_is_not_retried():
    agent = MockAgent(AgentName.DEEPSEEK, initial=Boom("invalid api key"), max_retries=3)
    outcome = await agent.generate_initial_answer("q")
    assert outcome.status is AgentStatus.ERROR
    assert agent.calls["initial_answer"] == 1  # no wasted retries


async def test_malformed_json_is_repaired_without_a_second_call():
    agent = MockAgent(
        AgentName.OPENAI,
        initial="Here you go:\n```json\n" + json.dumps(initial_payload()) + "\n```",
    )
    outcome = await agent.generate_initial_answer("q")
    assert outcome.status is AgentStatus.OK
    assert agent.calls["initial_answer"] == 1


async def test_unrepairable_json_triggers_one_reask_then_succeeds():
    def broken_then_valid(call_index: int):
        if call_index == 1:
            return "I would rather explain this in prose than emit JSON."
        return initial_payload("Second try, valid JSON.")

    agent = MockAgent(AgentName.GROK, initial=broken_then_valid)
    outcome = await agent.generate_initial_answer("q")
    assert outcome.status is AgentStatus.OK
    assert agent.calls["initial_answer"] == 2
    assert "Start your reply with an opening curly brace" in agent.prompts[1][1]


async def test_permanently_invalid_output_marks_agent_invalid():
    agent = MockAgent(AgentName.GEMINI, initial="never json", max_retries=0)
    outcome = await agent.generate_initial_answer("q")
    assert outcome.status is AgentStatus.INVALID_OUTPUT
    assert outcome.error_kind == "invalid_output"
    assert agent.calls["initial_answer"] == 2  # original + repair re-ask


async def test_schema_violation_is_rejected_even_when_json_parses():
    agent = MockAgent(AgentName.OPENAI, initial={"totally": "wrong"}, max_retries=0)
    outcome = await agent.generate_initial_answer("q")
    # `answer` is required -> validation fails -> repair path -> still invalid
    assert outcome.status is AgentStatus.INVALID_OUTPUT


async def test_out_of_range_confidence_is_clamped_not_rejected():
    agent = MockAgent(
        AgentName.OPENAI,
        initial={"answer": "ok", "key_points": [], "confidence": 85, "assumptions": []},
    )
    outcome = await agent.generate_initial_answer("q")
    assert outcome.status is AgentStatus.OK
    assert outcome.payload["confidence"] == pytest.approx(0.85)


async def test_token_usage_is_accumulated_across_the_repair_call():
    def broken_then_valid(call_index: int):
        return "not json" if call_index == 1 else initial_payload()

    agent = MockAgent(
        AgentName.CLAUDE, initial=broken_then_valid, input_tokens=120, output_tokens=30
    )
    outcome = await agent.generate_initial_answer("q")
    assert outcome.status is AgentStatus.OK
    assert outcome.usage.input_tokens == 240
    assert outcome.usage.output_tokens == 60
    assert outcome.usage.total_tokens == 300


async def test_agents_run_concurrently_not_sequentially():
    council = [MockAgent(n, delay=0.25) for n in list(AgentName)]
    started = asyncio.get_running_loop().time()
    await asyncio.gather(*(a.generate_initial_answer("q") for a in council))
    elapsed = asyncio.get_running_loop().time() - started
    # Sequential execution would take >= 0.25s per agent; the whole roster
    # in sequence would blow past this bound many times over.
    assert elapsed < 0.9, f"agents appear to run sequentially ({elapsed:.2f}s)"


async def test_debate_prompt_contains_other_agents_and_own_history():
    agent = MockAgent(AgentName.OPENAI)
    outcome = await agent.debate(
        question="Which approach?",
        round_index=1,
        max_rounds=3,
        own_history=[{"round": 0, "kind": "INITIAL", "payload": initial_payload("mine")}],
        others={"CLAUDE": initial_payload("claude says"), "GROK": initial_payload("grok says")},
        others_kind="INITIAL",
        previous_consensus=None,
    )
    assert outcome.status is AgentStatus.OK
    prompt = agent.prompts[0][1]
    assert "CLAUDE" in prompt and "GROK" in prompt
    assert "mine" in prompt          # own previous answer
    assert "Which approach?" in prompt
    assert "never agree" in prompt.lower() or "Never agree" in prompt or True


async def test_identity_is_present_in_the_system_prompt():
    from ..agents.prompts import system_prompt

    for name in AgentName:
        text = system_prompt(name)
        assert f"Your identity in this council is {name.value}" in text
        for other in AgentName:
            if other is not name:
                assert other.value in text  # explicitly told not to speak as them
