"""The agent abstraction.

Adding a sixth AI provider means:
  1. subclass `BaseAgent`,
  2. implement the single `_complete()` coroutine,
  3. add an entry to `agents/registry.py` and the `AgentName` enum.

Everything else — retries, timeouts, JSON repair, schema validation, token
accounting, status reporting — is inherited.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Type, TypeVar

from pydantic import BaseModel, ValidationError

from ..models.enums import AgentName, AgentStatus
from ..models.schemas import (
    AgentOutcome,
    ConsensusVote,
    DebateResponse,
    InitialAnswer,
    SynthesisResult,
    TokenUsage,
)
from ..services.json_repair import extract_json
from ..services.logging import get_logger
from ..services.retry import ProviderError, SchemaError, call_with_retry
from . import prompts

log = get_logger(__name__)

TModel = TypeVar("TModel", bound=BaseModel)


# --------------------------------------------------------------------------- #
# JSON-Schema helpers
# --------------------------------------------------------------------------- #

_STRIP_KEYS = {"default", "examples", "$comment", "minimum", "maximum", "exclusiveMinimum",
               "exclusiveMaximum", "minLength", "maxLength", "minItems", "maxItems", "pattern",
               "format"}


def _harden(node: Any) -> Any:
    """Make a Pydantic-generated schema acceptable to strict structured output.

    Strict mode requires every object to list all of its properties in
    `required` and to set `additionalProperties: false`, and rejects most
    validation keywords.
    """
    if isinstance(node, list):
        return [_harden(n) for n in node]
    if not isinstance(node, dict):
        return node

    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in _STRIP_KEYS:
            continue
        out[key] = _harden(value)

    if out.get("type") == "object" or "properties" in out:
        props = out.get("properties") or {}
        out["properties"] = props
        out.setdefault("type", "object")
        out["required"] = list(props.keys())
        out["additionalProperties"] = False
    return out


def json_schema_for(model_cls: Type[BaseModel]) -> dict[str, Any]:
    return _harden(model_cls.model_json_schema())


# --------------------------------------------------------------------------- #
# Provider return value
# --------------------------------------------------------------------------- #


@dataclass
class RawCompletion:
    """Whatever a provider returned, normalised."""

    text: str
    usage: TokenUsage = field(default_factory=TokenUsage)


@dataclass
class AgentCall:
    """Internal result of one structured call."""

    data: BaseModel
    usage: TokenUsage
    attempts: int
    latency_ms: int


# --------------------------------------------------------------------------- #
# BaseAgent
# --------------------------------------------------------------------------- #


class BaseAgent(ABC):
    name: ClassVar[AgentName]
    provider: ClassVar[str] = "unknown"
    #: "json_schema" | "json_object" | "none"
    structured_output_mode: ClassVar[str] = "none"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        temperature: float = 0.7,
        timeout: int = 60,
        max_retries: int = 2,
        max_tokens: int = 4000,
    ) -> None:
        self.api_key = api_key or ""
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_tokens = max_tokens
        #: request-shaping features this provider/model turned out to reject.
        self._unsupported: set[str] = set()

    # ------------------------------------------------------------------ status
    @property
    def enabled(self) -> bool:
        """An agent without a key is DISABLED, not failed."""
        return bool(self.api_key.strip())

    def describe(self) -> dict[str, Any]:
        return {
            "agent": self.name.value,
            "provider": self.provider,
            "model": self.model,
            "configured": self.enabled,
        }

    # --------------------------------------------------------- provider bridge
    @abstractmethod
    async def _complete(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict[str, Any] | None = None,
        schema_name: str | None = None,
    ) -> RawCompletion:
        """One raw call to the provider. Must not retry — the base class does."""

    async def aclose(self) -> None:  # pragma: no cover - most SDKs need nothing
        return None

    async def list_models(self) -> list[str]:
        """Model ids this key can actually use.

        Model names churn constantly — a configured model that worked last
        month can start returning 404. The UI uses this to show what is
        available right now instead of making the user guess.
        """
        raise NotImplementedError(
            f"{self.name.value} does not support listing models"
        )

    # -------------------------------------------------------------- public API
    async def generate_initial_answer(self, question: str) -> AgentOutcome:
        return await self._run(
            system=prompts.system_prompt(self.name),
            user=prompts.build_initial_prompt(question),
            model_cls=InitialAnswer,
            schema_name="initial_answer",
        )

    async def debate(
        self,
        *,
        question: str,
        round_index: int,
        max_rounds: int,
        own_history: list[dict[str, Any]],
        others: dict[str, dict[str, Any]],
        others_kind: str,
        previous_consensus: dict[str, Any] | None = None,
        roles: dict[str, str] | None = None,
    ) -> AgentOutcome:
        # Роль включается только со спором. В нулевом раунде агент отвечает
        # независимо, и «ты — критик» исказило бы этот базовый ответ.
        role = (roles or {}).get(self.name.value)
        return await self._run(
            system=prompts.system_prompt(self.name, role),
            user=prompts.build_debate_prompt(
                question=question,
                round_index=round_index,
                max_rounds=max_rounds,
                own_history=own_history,
                others=others,
                others_kind=others_kind,
                previous_consensus=previous_consensus,
                roles=roles,
                agent=self.name,
            ),
            model_cls=DebateResponse,
            schema_name="debate_response",
        )

    async def evaluate_consensus(
        self,
        *,
        question: str,
        round_index: int,
        positions: dict[str, dict[str, Any]],
    ) -> AgentOutcome:
        return await self._run(
            system=prompts.system_prompt(self.name),
            user=prompts.build_consensus_prompt(
                question=question, round_index=round_index, positions=positions
            ),
            model_cls=ConsensusVote,
            schema_name="consensus_vote",
        )

    async def synthesize(
        self,
        *,
        question: str,
        transcript: str,
        consensus: dict[str, Any],
        failed_agents: list[str],
    ) -> AgentOutcome:
        """Used only when this agent is elected as the neutral synthesiser."""
        return await self._run(
            system=prompts.SYNTHESIS_SYSTEM,
            user=prompts.build_synthesis_prompt(
                question=question,
                transcript=transcript,
                consensus=consensus,
                failed_agents=failed_agents,
            ),
            model_cls=SynthesisResult,
            schema_name="synthesis_result",
        )

    # ------------------------------------------------------------------ engine
    async def _run(
        self,
        *,
        system: str,
        user: str,
        model_cls: Type[TModel],
        schema_name: str,
    ) -> AgentOutcome:
        started = time.perf_counter()
        usage_total = TokenUsage()
        attempts = 0

        if not self.enabled:
            return AgentOutcome(
                agent=self.name,
                status=AgentStatus.DISABLED,
                model=self.model,
                error="No API key configured for this provider",
                error_kind="not_configured",
                attempts=0,
            )

        schema = json_schema_for(model_cls)

        async def attempt(prompt_text: str) -> RawCompletion:
            return await self._complete(
                system=system,
                user=prompt_text,
                json_schema=schema,
                schema_name=schema_name,
            )

        # ---- transport phase (retried) -----------------------------------
        try:
            completion, retry_info = await call_with_retry(
                lambda: attempt(user),
                timeout=self.timeout,
                max_retries=self.max_retries,
                label=f"{self.name.value}/{schema_name}",
            )
            attempts = retry_info.attempts
            usage_total = usage_total + completion.usage
        except ProviderError as exc:
            return AgentOutcome(
                agent=self.name,
                status=AgentStatus.ERROR,
                model=self.model,
                error=str(exc)[:1000],
                error_kind=exc.kind,
                attempts=getattr(exc, "attempts", self.max_retries + 1),
                latency_ms=int((time.perf_counter() - started) * 1000),
                usage=usage_total,
            )

        # ---- parse phase (repair, then one re-ask) ------------------------
        parsed = self._parse(completion.text, model_cls)

        if parsed is None:
            log.warning(
                "%s returned unparsable output for %s — re-asking with repair instruction",
                self.name.value,
                schema_name,
            )
            repair_user = (
                f"{user}\n\n---\n{prompts.REPAIR_INSTRUCTION}\n\n"
                f"Your previous (invalid) reply started with:\n"
                f"{completion.text[:400]}"
            )
            try:
                completion2, retry_info2 = await call_with_retry(
                    lambda: attempt(repair_user),
                    timeout=self.timeout,
                    max_retries=self.max_retries,
                    label=f"{self.name.value}/{schema_name}/repair",
                )
                attempts += retry_info2.attempts
                usage_total = usage_total + completion2.usage
                parsed = self._parse(completion2.text, model_cls)
            except ProviderError as exc:
                return AgentOutcome(
                    agent=self.name,
                    status=AgentStatus.ERROR,
                    model=self.model,
                    error=str(exc)[:1000],
                    error_kind=exc.kind,
                    attempts=attempts,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    usage=usage_total,
                )

        latency_ms = int((time.perf_counter() - started) * 1000)

        if parsed is None:
            err = SchemaError(
                f"Model did not return valid JSON for schema '{schema_name}' "
                f"after a repair attempt"
            )
            return AgentOutcome(
                agent=self.name,
                status=AgentStatus.INVALID_OUTPUT,
                model=self.model,
                error=str(err),
                error_kind=err.kind,
                attempts=attempts,
                latency_ms=latency_ms,
                usage=usage_total,
            )

        return AgentOutcome(
            agent=self.name,
            status=AgentStatus.OK,
            model=self.model,
            payload=parsed.model_dump(mode="json"),
            attempts=attempts,
            latency_ms=latency_ms,
            usage=usage_total,
        )

    @staticmethod
    def _parse(text: str, model_cls: Type[TModel]) -> TModel | None:
        raw = extract_json(text or "")
        if raw is None:
            return None
        try:
            return model_cls.model_validate(raw)
        except ValidationError as exc:
            log.debug("Schema validation failed: %s", exc)
            return None
