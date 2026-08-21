"""Google Gemini agent — official `google-genai` SDK.

Uses `client.aio.models.generate_content` (the async surface) with
`response_mime_type="application/json"`, which is Gemini's native JSON mode.
A full `response_schema` is intentionally not sent: Gemini's schema dialect is
a restricted subset and rejects several keywords our Pydantic schemas emit, so
the schema is carried in the prompt and enforced by Pydantic on our side.
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

_MAX_ADAPTATIONS = 5


class GeminiAgent(BaseAgent):
    name: ClassVar[AgentName] = AgentName.GEMINI
    provider: ClassVar[str] = "google"
    structured_output_mode: ClassVar[str] = "json_object"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._client: Any = None
        self._types: Any = None

    def _get_client(self) -> tuple[Any, Any]:
        if self._client is None:
            from google import genai  # lazy: tests need no key
            from google.genai import types

            self._client = genai.Client(api_key=self.api_key)
            self._types = types
        return self._client, self._types

    async def list_models(self) -> list[str]:
        client, _types = self._get_client()
        names: list[str] = []
        async for model in await client.aio.models.list():
            actions = getattr(model, "supported_actions", None) or []
            if actions and "generateContent" not in actions:
                continue
            names.append(str(model.name).replace("models/", ""))
        return sorted(names)

    # ------------------------------------------------------------------ build
    def _build_config(self, types: Any, system: str) -> Any:
        cfg: dict[str, Any] = {
            "system_instruction": system,
            "response_mime_type": "application/json",
            "max_output_tokens": self.max_tokens,
        }
        if "temperature" not in self._unsupported:
            cfg["temperature"] = self.temperature

        # Gemini 2.5 "thinking" consumes the output budget. Turning it off
        # keeps the whole budget available for the JSON payload; models that
        # do not accept the field make us drop it on the first 400.
        if "thinking" not in self._unsupported and hasattr(types, "ThinkingConfig"):
            try:
                cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
            except Exception:  # noqa: BLE001 - older SDKs
                self._unsupported.add("thinking")

        if "system_instruction" in self._unsupported:
            cfg.pop("system_instruction", None)

        return types.GenerateContentConfig(**cfg)

    def _adapt(self, exc: BaseException) -> bool:
        msg = str(exc).lower()
        if not any(k in msg for k in ("400", "invalid", "unsupported", "not supported")):
            return False

        if "thinking" in msg and "thinking" not in self._unsupported:
            self._unsupported.add("thinking")
            log.info("GEMINI: model rejects thinking_config — omitting it")
            return True
        if "system_instruction" in msg and "system_instruction" not in self._unsupported:
            self._unsupported.add("system_instruction")
            log.info("GEMINI: model rejects system_instruction — merging into the prompt")
            return True
        if "temperature" in msg and "temperature" not in self._unsupported:
            self._unsupported.add("temperature")
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
        client, types = self._get_client()
        last_exc: BaseException | None = None

        for _ in range(_MAX_ADAPTATIONS):
            contents = user
            if "system_instruction" in self._unsupported:
                contents = f"{system}\n\n---\n\n{user}"
            try:
                response = await client.aio.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=self._build_config(types, system),
                )
            except BaseException as exc:  # noqa: BLE001 - re-raised below
                last_exc = exc
                if self._adapt(exc):
                    continue
                raise

            text = self._extract_text(response)
            usage = TokenUsage()
            meta = getattr(response, "usage_metadata", None)
            if meta is not None:
                usage = TokenUsage(
                    input_tokens=int(getattr(meta, "prompt_token_count", 0) or 0),
                    output_tokens=int(
                        (getattr(meta, "candidates_token_count", 0) or 0)
                        + (getattr(meta, "thoughts_token_count", 0) or 0)
                    ),
                )
            return RawCompletion(text=text, usage=usage)

        raise ProviderError(
            f"Exhausted request adaptations: {last_exc}", kind="bad_request", retryable=False
        )

    @staticmethod
    def _extract_text(response: Any) -> str:
        """`response.text` raises on some responses; walk the parts instead."""
        chunks: list[str] = []
        for candidate in getattr(response, "candidates", None) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", None) or []:
                # Skip thought parts: internal reasoning is never surfaced.
                if getattr(part, "thought", False):
                    continue
                piece = getattr(part, "text", None)
                if piece:
                    chunks.append(piece)
        if chunks:
            return "".join(chunks)
        try:
            return response.text or ""
        except Exception:  # noqa: BLE001
            return ""

    @classmethod
    def from_settings(
        cls, *, model: str | None = None, settings: Any = None, **overrides: Any
    ) -> "GeminiAgent":
        s = settings or get_settings()
        return cls(
            api_key=s.google_api_key,
            model=model or s.gemini_model,
            temperature=overrides.get("temperature", s.temperature),
            timeout=overrides.get("timeout", s.request_timeout),
            max_retries=overrides.get("max_retries", s.max_retries),
            max_tokens=overrides.get("max_tokens", s.max_tokens),
        )
