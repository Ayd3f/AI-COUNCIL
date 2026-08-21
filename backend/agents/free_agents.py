"""Провайдеры с бесплатным тарифом.

Все четыре отдают OpenAI-совместимый `/chat/completions`, поэтому переиспользуют
`OpenAICompatibleAgent` — это документированный способ интеграции у каждого из
них, а не самодельная обёртка.

Важно про имена. Участник Groq Cloud называется `GROQCLOUD`, а не `GROQ`:
в совете уже есть `GROK` (модель xAI), и два имени, различающиеся одной буквой,
модели начали бы смешивать, ссылаясь на аргументы друг друга. Требование
«не путать участников» важнее красоты идентификатора.

Ещё одно предупреждение по существу: бесплатные тарифы часто отдают одни и те
же открытые модели. Если поставить на GROQCLOUD и CEREBRAS одну и ту же
Llama-70B, в совете окажется один голос дважды — спорить будет не с кем, а
консенсус получится фальшивым. Выбирайте разные семейства моделей.
"""

from __future__ import annotations

from typing import Any, ClassVar

from ..config import get_settings
from ..models.enums import AgentName
from .openai_agent import OpenAICompatibleAgent


class GroqCloudAgent(OpenAICompatibleAgent):
    """Groq Cloud — быстрый хостинг открытых моделей, бесплатный тариф."""

    name: ClassVar[AgentName] = AgentName.GROQCLOUD
    provider: ClassVar[str] = "groq"
    structured_output_mode: ClassVar[str] = "json_object"
    default_base_url: ClassVar[str] = "https://api.groq.com/openai/v1"

    @classmethod
    def from_settings(
        cls, *, model: str | None = None, settings: Any = None, **overrides: Any
    ) -> "GroqCloudAgent":
        s = settings or get_settings()
        return cls(
            api_key=s.groq_api_key,
            model=model or s.groq_model,
            base_url=s.groq_base_url,
            temperature=overrides.get("temperature", s.temperature),
            timeout=overrides.get("timeout", s.request_timeout),
            max_retries=overrides.get("max_retries", s.max_retries),
            max_tokens=overrides.get("max_tokens", s.max_tokens),
        )


class CerebrasAgent(OpenAICompatibleAgent):
    """Cerebras — бесплатный тариф с большим суточным лимитом токенов."""

    name: ClassVar[AgentName] = AgentName.CEREBRAS
    provider: ClassVar[str] = "cerebras"
    structured_output_mode: ClassVar[str] = "json_object"
    default_base_url: ClassVar[str] = "https://api.cerebras.ai/v1"

    @classmethod
    def from_settings(
        cls, *, model: str | None = None, settings: Any = None, **overrides: Any
    ) -> "CerebrasAgent":
        s = settings or get_settings()
        return cls(
            api_key=s.cerebras_api_key,
            model=model or s.cerebras_model,
            base_url=s.cerebras_base_url,
            temperature=overrides.get("temperature", s.temperature),
            timeout=overrides.get("timeout", s.request_timeout),
            max_retries=overrides.get("max_retries", s.max_retries),
            max_tokens=overrides.get("max_tokens", s.max_tokens),
        )


class MistralAgent(OpenAICompatibleAgent):
    """Mistral — собственные модели, то есть действительно другой «голос»."""

    name: ClassVar[AgentName] = AgentName.MISTRAL
    provider: ClassVar[str] = "mistral"
    structured_output_mode: ClassVar[str] = "json_object"
    default_base_url: ClassVar[str] = "https://api.mistral.ai/v1"

    @classmethod
    def from_settings(
        cls, *, model: str | None = None, settings: Any = None, **overrides: Any
    ) -> "MistralAgent":
        s = settings or get_settings()
        return cls(
            api_key=s.mistral_api_key,
            model=model or s.mistral_model,
            base_url=s.mistral_base_url,
            temperature=overrides.get("temperature", s.temperature),
            timeout=overrides.get("timeout", s.request_timeout),
            max_retries=overrides.get("max_retries", s.max_retries),
            max_tokens=overrides.get("max_tokens", s.max_tokens),
        )


class LocalAgent(OpenAICompatibleAgent):
    """Модель на своей машине (Ollama, LM Studio, llama.cpp).

    Ключа не требует: участник включается, если задан адрес сервера. Это
    единственный по-настоящему бесплатный участник — без лимитов и без
    отправки вопроса наружу.
    """

    name: ClassVar[AgentName] = AgentName.LOCAL
    provider: ClassVar[str] = "local"
    structured_output_mode: ClassVar[str] = "json_object"
    #: показывается как подсказка в настройках; молча не подставляется
    default_base_url: ClassVar[str] = "http://localhost:11434/v1"
    requires_explicit_base_url: ClassVar[bool] = True

    @property
    def enabled(self) -> bool:
        # Локальные серверы не проверяют ключ, поэтому наличие ключа здесь
        # ничего не значит — участник включён, если указан адрес.
        return bool(self.base_url.strip())

    @classmethod
    def from_settings(
        cls, *, model: str | None = None, settings: Any = None, **overrides: Any
    ) -> "LocalAgent":
        s = settings or get_settings()
        return cls(
            # Локальные серверы игнорируют ключ, но SDK требует непустую строку.
            api_key="local",
            model=model or s.ollama_model,
            base_url=s.ollama_base_url,
            temperature=overrides.get("temperature", s.temperature),
            timeout=overrides.get("timeout", s.request_timeout),
            max_retries=overrides.get("max_retries", s.max_retries),
            max_tokens=overrides.get("max_tokens", s.max_tokens),
        )
