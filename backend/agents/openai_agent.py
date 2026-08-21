"""OpenAI agent, plus the shared base for OpenAI-compatible HTTP APIs.

Uses the official `openai` Python SDK (`AsyncOpenAI`).  xAI Grok and DeepSeek
both expose an OpenAI-compatible `/chat/completions` endpoint, so they reuse
this class with a different `base_url` — that is the vendors' own documented
integration path, not a shim we invented.

Provider retries are disabled at the SDK level (`max_retries=0`) because the
council's own retry policy in `services/retry.py` owns that concern.
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


class OpenAICompatibleAgent(BaseAgent):
    """Chat-completions client with capability negotiation.

    Providers differ in which request-shaping features they accept.  Rather
    than hardcoding a matrix per model, we downgrade on the specific error the
    provider returns and remember the downgrade for the rest of the run.
    """

    provider: ClassVar[str] = "openai-compatible"
    structured_output_mode: ClassVar[str] = "json_schema"
    default_base_url: ClassVar[str] = "https://api.openai.com/v1"
    #: Когда True, пустой адрес НЕ подменяется значением по умолчанию.
    #: Нужно локальному участнику: там адрес и есть признак «включён», и
    #: молчаливая подстановка localhost делала бы его включённым всегда.
    requires_explicit_base_url: ClassVar[bool] = False

    def __init__(self, *, base_url: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if self.requires_explicit_base_url:
            self.base_url = (base_url or "").strip()
        else:
            self.base_url = base_url or self.default_base_url
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI  # imported lazily so tests need no key

            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                max_retries=0,
                timeout=float(self.timeout),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:  # noqa: BLE001 - close must never raise
                pass
            self._client = None

    async def list_models(self) -> list[str]:
        client = self._get_client()
        page = await client.models.list()
        return sorted(m.id for m in page.data)

    # ------------------------------------------------------------------ build
    def _messages(self, system: str, user: str) -> list[dict[str, str]]:
        if "system_role" in self._unsupported:
            return [{"role": "user", "content": f"{system}\n\n---\n\n{user}"}]
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def _build_kwargs(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict[str, Any] | None,
        schema_name: str | None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self._messages(system, user),
        }

        if "max_tokens" not in self._unsupported:
            kwargs["max_tokens"] = self.max_tokens
        else:
            kwargs["max_completion_tokens"] = self.max_tokens

        if "temperature" not in self._unsupported:
            kwargs["temperature"] = self.temperature

        mode = self.structured_output_mode
        if mode == "json_schema" and "json_schema" not in self._unsupported and json_schema:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name or "response",
                    "strict": True,
                    "schema": json_schema,
                },
            }
        elif mode in {"json_schema", "json_object"} and "json_object" not in self._unsupported:
            kwargs["response_format"] = {"type": "json_object"}

        return kwargs

    # ------------------------------------------------------------------ adapt
    def _adapt(self, exc: BaseException) -> bool:
        """Record an unsupported feature. Returns True if a retry makes sense."""
        name = type(exc).__name__
        if name not in {"BadRequestError", "UnprocessableEntityError", "APIStatusError"}:
            return False
        msg = str(exc).lower()

        if "json_schema" in msg or "response_format" in msg or "structured output" in msg:
            if "json_schema" not in self._unsupported:
                self._unsupported.add("json_schema")
                log.info("%s: falling back to json_object mode", self.name.value)
                return True
            if "json_object" not in self._unsupported:
                self._unsupported.add("json_object")
                log.info("%s: falling back to prompt-only JSON mode", self.name.value)
                return True

        if "temperature" in msg and "temperature" not in self._unsupported:
            self._unsupported.add("temperature")
            log.info("%s: model rejects 'temperature' — dropping it", self.name.value)
            return True

        if "max_tokens" in msg and "max_tokens" not in self._unsupported:
            self._unsupported.add("max_tokens")
            log.info("%s: switching to 'max_completion_tokens'", self.name.value)
            return True

        if "system" in msg and "role" in msg and "system_role" not in self._unsupported:
            self._unsupported.add("system_role")
            log.info("%s: model rejects a system role — merging into user turn", self.name.value)
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
            kwargs = self._build_kwargs(
                system=system, user=user, json_schema=json_schema, schema_name=schema_name
            )
            try:
                response = await client.chat.completions.create(**kwargs)
            except BaseException as exc:  # noqa: BLE001 - re-raised below
                last_exc = exc
                if self._adapt(exc):
                    continue
                raise

            choice = response.choices[0] if response.choices else None
            text = ""
            if choice is not None and choice.message is not None:
                text = choice.message.content or ""

            usage = TokenUsage()
            if getattr(response, "usage", None):
                usage = TokenUsage(
                    input_tokens=int(getattr(response.usage, "prompt_tokens", 0) or 0),
                    output_tokens=int(getattr(response.usage, "completion_tokens", 0) or 0),
                )
            return RawCompletion(text=text, usage=usage)

        raise ProviderError(
            f"Exhausted request adaptations: {last_exc}", kind="bad_request", retryable=False
        )


class OpenAIAgent(OpenAICompatibleAgent):
    name: ClassVar[AgentName] = AgentName.OPENAI
    provider: ClassVar[str] = "openai"
    structured_output_mode: ClassVar[str] = "json_schema"

    @classmethod
    def from_settings(
        cls, *, model: str | None = None, settings: Any = None, **overrides: Any
    ) -> "OpenAIAgent":
        s = settings or get_settings()
        return cls(
            api_key=s.openai_api_key,
            model=model or s.openai_model,
            base_url=s.openai_base_url,
            temperature=overrides.get("temperature", s.temperature),
            timeout=overrides.get("timeout", s.request_timeout),
            max_retries=overrides.get("max_retries", s.max_retries),
            max_tokens=overrides.get("max_tokens", s.max_tokens),
        )
