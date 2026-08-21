"""Mock AI provider used by the whole test suite.

`MockAgent` subclasses the real `BaseAgent`, so every test exercises the real
retry policy, the real JSON repair path, the real schema validation and the
real token accounting — only the network call is replaced.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from ..agents.base import BaseAgent, RawCompletion
from ..models.enums import AgentName
from ..models.schemas import TokenUsage

Behaviour = Any  # dict | str | BaseException | Callable[[int], Any]


class Boom(Exception):
    """Generic non-retryable provider failure."""


class Flaky(Exception):
    """Retryable failure — classified as transient by services.retry."""

    def __init__(self, msg: str = "503 service unavailable") -> None:
        super().__init__(msg)


Flaky.__name__ = "InternalServerError"  # matches the retryable name set


class MockAgent(BaseAgent):
    provider = "mock"
    structured_output_mode = "none"

    def __init__(
        self,
        name: AgentName,
        *,
        initial: Behaviour = None,
        debate: Behaviour = None,
        vote: Behaviour = None,
        synthesis: Behaviour = None,
        delay: float = 0.0,
        input_tokens: int = 100,
        output_tokens: int = 50,
        api_key: str = "test-key",
        model: str = "mock-model-1",
        **kwargs: Any,
    ) -> None:
        super().__init__(api_key=api_key, model=model, **kwargs)
        self.name = name  # type: ignore[misc] - instance-level identity
        self._behaviours: dict[str, Behaviour] = {
            "initial_answer": initial,
            "debate_response": debate,
            "consensus_vote": vote,
            "synthesis_result": synthesis,
        }
        self._delay = delay
        self._in = input_tokens
        self._out = output_tokens
        self.calls: dict[str, int] = {}
        self.prompts: list[tuple[str, str]] = []

    # ------------------------------------------------------------------ helper
    def _resolve(self, schema_name: str, index: int) -> Behaviour:
        behaviour = self._behaviours.get(schema_name)
        if callable(behaviour) and not isinstance(behaviour, BaseException):
            behaviour = behaviour(index)
        return behaviour

    async def _complete(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict[str, Any] | None = None,
        schema_name: str | None = None,
    ) -> RawCompletion:
        key = schema_name or "unknown"
        self.calls[key] = self.calls.get(key, 0) + 1
        self.prompts.append((key, user))

        if self._delay:
            await asyncio.sleep(self._delay)

        behaviour = self._resolve(key, self.calls[key])

        if isinstance(behaviour, BaseException):
            raise behaviour
        if isinstance(behaviour, type) and issubclass(behaviour, BaseException):
            raise behaviour()

        if behaviour is None:
            behaviour = DEFAULTS[key]

        text = (
            behaviour
            if isinstance(behaviour, str)
            else json.dumps(behaviour, ensure_ascii=False)
        )
        return RawCompletion(
            text=text,
            usage=TokenUsage(input_tokens=self._in, output_tokens=self._out),
        )


# --------------------------------------------------------------------------- #
# Canned payloads
# --------------------------------------------------------------------------- #


def initial_payload(answer: str = "Use approach X.", confidence: float = 0.8) -> dict[str, Any]:
    return {
        "answer": answer,
        "key_points": ["X is well supported", "Y is a weaker alternative"],
        "confidence": confidence,
        "assumptions": ["The environment is a standard one"],
    }


def debate_payload(
    position: str = "I keep my position: approach X is correct.",
    *,
    accepted_from: str = "CLAUDE",
    rejected_from: str = "GEMINI",
    changed: bool = False,
    confidence: float = 0.85,
) -> dict[str, Any]:
    return {
        "position": position,
        "accepted_arguments": [
            {
                "agent": accepted_from,
                "argument": "X scales better than Y under sustained load",
                "reason": "This matches the published benchmark methodology",
            }
        ],
        "rejected_arguments": [
            {
                "agent": rejected_from,
                "argument": "Y is always cheaper to operate",
                "reason": "It ignores the cost of the extra coordination layer",
            }
        ],
        "uncertain_arguments": [],
        "persuasion": [
            {
                "agent": rejected_from,
                "their_objection": "Operating cost is the only thing that matters here",
                "my_counter": "Coordination overhead outweighs it below ten engineers",
                "concession": "I accept your point for teams past that size",
            }
        ],
        "persuaded_by": [accepted_from] if changed else [],
        "what_would_change_my_mind": (
            "A published benchmark showing the opposite under sustained load"
        ),
        "proposed_common_answer": "Approach X, with Y kept for the cold-start path.",
        "changed_my_position": changed,
        "why_changed": (
            "The benchmark evidence did not move me."
            if not changed
            else "The compliance-boundary argument is concrete and verifiable."
        ),
        "confidence": confidence,
    }


def capitulation_payload() -> dict[str, Any]:
    """Сменил позицию и не назвал, кто переубедил, — уступка под давлением."""
    return {
        "position": "Ладно, согласен с большинством.",
        "accepted_arguments": [],
        "rejected_arguments": [],
        "uncertain_arguments": [],
        "persuasion": [],
        "persuaded_by": [],
        "what_would_change_my_mind": "",
        "proposed_common_answer": "",
        "changed_my_position": True,
        "why_changed": "",
        "confidence": 0.95,
    }


def vote_payload(
    *,
    agrees: bool = True,
    level: float = 0.9,
    objection: bool = False,
    conclusion: str = "The council converges on approach X for this workload.",
) -> dict[str, Any]:
    return {
        "main_conclusion": conclusion,
        "agrees_with_main_conclusion": agrees,
        "agreement_level": level,
        "has_material_objection": objection,
        "objection": "Approach X fails on the cold-start path" if objection else "",
        "evidence_for_objection": (
            "Measured 4x latency regression on cold start in the reference run"
            if objection
            else ""
        ),
    }


def synthesis_payload(consensus: str = "Approach X is the best answer.") -> dict[str, Any]:
    return {
        "consensus": consensus,
        "main_reasoning": "Four of five agents converged on X with consistent evidence.",
        "key_agreements": ["X scales better under load"],
        "disagreements": ["Cold-start behaviour is unresolved"],
        "strongest_arguments": ["Benchmark methodology supports X"],
        "rejected_arguments": [
            {
                "argument": "Y is always cheaper",
                "rejected_by": ["OPENAI"],
                "reason": "Ignores coordination overhead",
            }
        ],
        "minority_positions": [],
        "individual_positions": [],
        "confidence": 86,
    }


DEFAULTS: dict[str, Any] = {
    "initial_answer": initial_payload(),
    "debate_response": debate_payload(),
    "consensus_vote": vote_payload(),
    "synthesis_result": synthesis_payload(),
    "unknown": {"answer": "n/a", "key_points": [], "confidence": 0.5, "assumptions": []},
}


def build_council(
    names: list[AgentName] | None = None, **kwargs: Any
) -> dict[AgentName, MockAgent]:
    from ..agents.registry import ROSTER

    names = names or list(ROSTER)
    return {n: MockAgent(n, **kwargs) for n in names}
