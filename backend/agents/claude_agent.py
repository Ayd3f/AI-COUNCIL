"""Anthropic Claude agent — official `anthropic` SDK (`AsyncAnthropic`).

Structured output and the thinking configuration are sent through
`extra_body`, which the SDK forwards verbatim into the request body.  That
keeps this agent working across SDK versions and across models with different
parameter support: anything the configured model rejects is detected from the
400 response and dropped for the rest of the run.
"""

from __future__ import annotations

from typing import Any, ClassVar

from ..config import get_settings
from ..models.enums import AgentName
from ..models.schemas import TokenUsage
from ..services.logging import get_logger
from ..services.retry import ProviderError
from .base import BaseAgent, RawCompletion

log = get_logger(__name__)

_MAX_ADAPTATIONS = 6


class ClaudeAgent(BaseAgent):
    name: ClassVar[AgentName] = AgentName.CLAUDE
    provider: ClassVar[str] = "anthropic"
    structured_output_mode: ClassVar[str] = "json_schema"

    def __init__(self, *, thinking: str = "disabled", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # "disabled" keeps the whole max_tokens budget available for the JSON
        # payload. "adaptive" lets the model think first (better reasoning,
        # more tokens); "default" sends nothing and uses the model's default.
        self.thinking_mode = thinking
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            from anthropic import AsyncAnthropic  # lazy: tests need no key

            self._client = AsyncAnthropic(
                api_key=self.api_key,
                max_retries=0,
                timeout=float(self.timeout),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:  # noqa: BLE001
                pass
            self._client = None

    async def list_models(self) -> list[str]:
        client = self._get_client()
        page = await client.models.list(limit=100)
        return sorted(m.id for m in page.data)

    # ------------------------------------------------------------------ build
    def _build_kwargs(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict[str, Any] | None,
    ) -> dict[str, Any]:
        # Thinking tokens count against max_tokens, so give the model room when
        # thinking is left on.
        max_tokens = self.max_tokens
        if self.thinking_mode == "adaptive" and "thinking" not in self._unsupported:
            max_tokens = max(max_tokens, self.max_tokens * 2)

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }

        if "temperature" not in self._unsupported:
            kwargs["temperature"] = self.temperature

        extra: dict[str, Any] = {}

        if json_schema and "json_schema" not in self._unsupported:
            extra["output_config"] = {
                "format": {"type": "json_schema", "schema": json_schema}
            }

        if "thinking" not in self._unsupported:
            if self.thinking_mode == "disabled":
                extra["thinking"] = {"type": "disabled"}
            elif self.thinking_mode == "adaptive":
                extra["thinking"] = {"type": "adaptive"}

        if extra:
            kwargs["extra_body"] = extra
        return kwargs

    # ------------------------------------------------------------------ adapt
    def _adapt(self, exc: BaseException) -> bool:
        name = type(exc).__name__
        if name not in {"BadRequestError", "UnprocessableEntityError", "APIStatusError"}:
            return False
        msg = str(exc).lower()

        if ("output_config" in msg or "json_schema" in msg or "structured" in msg) and (
            "json_schema" not in self._unsupported
        ):
            self._unsupported.add("json_schema")
            log.info("CLAUDE: model rejects structured outputs — using prompt-only JSON")
            return True

        if "thinking" in msg and "thinking" not in self._unsupported:
            self._unsupported.add("thinking")
            log.info("CLAUDE: model rejects this thinking configuration — omitting it")
            return True

        if "temperature" in msg and "temperature" not in self._unsupported:
            self._unsupported.add("temperature")
            log.info("CLAUDE: model rejects 'temperature' — dropping it")
            return True

        return False

    # ------------------------------------------------------------------- call
    async def _complete(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict[str, Any] | None = None,
        schema_name: str | None = None,
    ) -> RawCompletion:
        client = self._get_client()
        last_exc: BaseException | None = None

        for _ in range(_MAX_ADAPTATIONS):
            kwargs = self._build_kwargs(system=system, user=user, json_schema=json_schema)
            try:
                message = await client.messages.create(**kwargs)
            except BaseException as exc:  # noqa: BLE001 - re-raised below
                last_exc = exc
                if self._adapt(exc):
                    continue
                raise

            # Only visible text blocks are consumed. Thinking blocks are
            # deliberately dropped and never surfaced to the UI.
            parts: list[str] = []
            for block in getattr(message, "content", []) or []:
                if getattr(block, "type", None) == "text":
                    parts.append(getattr(block, "text", "") or "")
            text = "".join(parts)

            usage = TokenUsage()
            if getattr(message, "usage", None):
                usage = TokenUsage(
                    input_tokens=int(getattr(message.usage, "input_tokens", 0) or 0),
                    output_tokens=int(getattr(message.usage, "output_tokens", 0) or 0),
                )
            return RawCompletion(text=text, usage=usage)

        raise ProviderError(
            f"Exhausted request adaptations: {last_exc}", kind="bad_request", retryable=False
        )

    @classmethod
    def from_settings(
        cls, *, model: str | None = None, settings: Any = None, **overrides: Any
    ) -> "ClaudeAgent":
        s = settings or get_settings()
        return cls(
            api_key=s.anthropic_api_key,
            model=model or s.anthropic_model,
            thinking=s.anthropic_thinking,
            temperature=overrides.get("temperature", s.temperature),
            timeout=overrides.get("timeout", s.request_timeout),
            max_retries=overrides.get("max_retries", s.max_retries),
            max_tokens=overrides.get("max_tokens", s.max_tokens),
        )
