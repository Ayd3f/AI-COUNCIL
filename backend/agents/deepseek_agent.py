"""DeepSeek agent.

DeepSeek's platform API is OpenAI-compatible (https://api.deepseek.com), which
is the vendor's own documented integration route.  DeepSeek supports JSON mode
(`response_format={"type": "json_object"}`) but not full JSON-Schema strict
mode, so we start in `json_object` mode.
"""

from __future__ import annotations

from typing import Any, ClassVar

from ..config import get_settings
from ..models.enums import AgentName
from .openai_agent import OpenAICompatibleAgent


class DeepSeekAgent(OpenAICompatibleAgent):
    name: ClassVar[AgentName] = AgentName.DEEPSEEK
    provider: ClassVar[str] = "deepseek"
    structured_output_mode: ClassVar[str] = "json_object"
    default_base_url: ClassVar[str] = "https://api.deepseek.com/v1"

    @classmethod
    def from_settings(
        cls, *, model: str | None = None, settings: Any = None, **overrides: Any
    ) -> "DeepSeekAgent":
        s = settings or get_settings()
        return cls(
            api_key=s.deepseek_api_key,
            model=model or s.deepseek_model,
            base_url=s.deepseek_base_url,
            temperature=overrides.get("temperature", s.temperature),
            timeout=overrides.get("timeout", s.request_timeout),
            max_retries=overrides.get("max_retries", s.max_retries),
            max_tokens=overrides.get("max_tokens", s.max_tokens),
        )
