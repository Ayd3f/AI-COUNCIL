"""xAI Grok agent.

xAI ships an OpenAI-compatible REST API at https://api.x.ai/v1 and documents
the official `openai` SDK with a swapped `base_url` as a supported integration
path, so we reuse the OpenAI-compatible client.
"""

from __future__ import annotations

from typing import Any, ClassVar

from ..config import get_settings
from ..models.enums import AgentName
from .openai_agent import OpenAICompatibleAgent


class GrokAgent(OpenAICompatibleAgent):
    name: ClassVar[AgentName] = AgentName.GROK
    provider: ClassVar[str] = "xai"
    # xAI supports structured outputs; we still negotiate down automatically
    # if the configured model rejects it.
    structured_output_mode: ClassVar[str] = "json_schema"
    default_base_url: ClassVar[str] = "https://api.x.ai/v1"

    @classmethod
    def from_settings(
        cls, *, model: str | None = None, settings: Any = None, **overrides: Any
    ) -> "GrokAgent":
        s = settings or get_settings()
        return cls(
            api_key=s.xai_api_key,
            model=model or s.xai_model,
            base_url=s.xai_base_url,
            temperature=overrides.get("temperature", s.temperature),
            timeout=overrides.get("timeout", s.request_timeout),
            max_retries=overrides.get("max_retries", s.max_retries),
            max_tokens=overrides.get("max_tokens", s.max_tokens),
        )
