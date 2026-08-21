"""Agent registry — the single place that knows the full council roster.

To add another provider: implement `BaseAgent` (or subclass
`OpenAICompatibleAgent` if the provider speaks the OpenAI protocol), then add
one entry to `AGENT_CLASSES` and one to `ENV_HINTS`. Nothing in the debate
engine needs to change.
"""

from __future__ import annotations

from typing import Callable, Iterable, Type

from ..config import Settings, get_settings
from ..models.enums import AgentName
from .base import BaseAgent
from .claude_agent import ClaudeAgent
from .deepseek_agent import DeepSeekAgent
from .free_agents import CerebrasAgent, GroqCloudAgent, LocalAgent, MistralAgent
from .gemini_agent import GeminiAgent
from .grok_agent import GrokAgent
from .openai_agent import OpenAIAgent

#: Canonical order used everywhere (UI columns, registry iteration, synthesiser
#: election). Keep it stable — users learn the layout. Paid providers first,
#: then the ones with a free tier.
AGENT_CLASSES: dict[AgentName, Type[BaseAgent]] = {
    AgentName.OPENAI: OpenAIAgent,
    AgentName.CLAUDE: ClaudeAgent,
    AgentName.GEMINI: GeminiAgent,
    AgentName.GROK: GrokAgent,
    AgentName.DEEPSEEK: DeepSeekAgent,
    AgentName.GROQCLOUD: GroqCloudAgent,
    AgentName.CEREBRAS: CerebrasAgent,
    AgentName.MISTRAL: MistralAgent,
    AgentName.LOCAL: LocalAgent,
}

ROSTER: list[AgentName] = list(AGENT_CLASSES.keys())

#: Providers that offer a usable free tier (shown as such in the UI).
FREE_TIER: set[AgentName] = {
    AgentName.GEMINI,
    AgentName.GROQCLOUD,
    AgentName.CEREBRAS,
    AgentName.MISTRAL,
    AgentName.LOCAL,
}

#: agent -> (env var holding the key, env var holding the model)
ENV_HINTS: dict[AgentName, tuple[str, str]] = {
    AgentName.OPENAI: ("OPENAI_API_KEY", "OPENAI_MODEL"),
    AgentName.CLAUDE: ("ANTHROPIC_API_KEY", "ANTHROPIC_MODEL"),
    AgentName.GEMINI: ("GOOGLE_API_KEY", "GEMINI_MODEL"),
    AgentName.GROK: ("XAI_API_KEY", "XAI_MODEL"),
    AgentName.DEEPSEEK: ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL"),
    AgentName.GROQCLOUD: ("GROQ_API_KEY", "GROQ_MODEL"),
    AgentName.CEREBRAS: ("CEREBRAS_API_KEY", "CEREBRAS_MODEL"),
    AgentName.MISTRAL: ("MISTRAL_API_KEY", "MISTRAL_MODEL"),
    # У локальной модели вместо ключа — адрес сервера.
    AgentName.LOCAL: ("OLLAMA_BASE_URL", "OLLAMA_MODEL"),
}

#: Где взять ключ (или как поднять сервер) — показывается в окне настроек.
SIGNUP_URLS: dict[AgentName, str] = {
    AgentName.OPENAI: "https://platform.openai.com/api-keys",
    AgentName.CLAUDE: "https://console.anthropic.com/settings/keys",
    AgentName.GEMINI: "https://aistudio.google.com/apikey",
    AgentName.GROK: "https://console.x.ai/",
    AgentName.DEEPSEEK: "https://platform.deepseek.com/api_keys",
    AgentName.GROQCLOUD: "https://console.groq.com/keys",
    AgentName.CEREBRAS: "https://cloud.cerebras.ai/",
    AgentName.MISTRAL: "https://console.mistral.ai/api-keys/",
    AgentName.LOCAL: "https://ollama.com/download",
}


def _key_for(agent: AgentName, s: Settings) -> str:
    return {
        AgentName.OPENAI: s.openai_api_key,
        AgentName.CLAUDE: s.anthropic_api_key,
        AgentName.GEMINI: s.google_api_key,
        AgentName.GROK: s.xai_api_key,
        AgentName.DEEPSEEK: s.deepseek_api_key,
        AgentName.GROQCLOUD: s.groq_api_key,
        AgentName.CEREBRAS: s.cerebras_api_key,
        AgentName.MISTRAL: s.mistral_api_key,
        # Локальный участник включается адресом, а не ключом.
        AgentName.LOCAL: s.ollama_base_url,
    }[agent]


def default_model_for(agent: AgentName, s: Settings | None = None) -> str:
    s = s or get_settings()
    return {
        AgentName.OPENAI: s.openai_model,
        AgentName.CLAUDE: s.anthropic_model,
        AgentName.GEMINI: s.gemini_model,
        AgentName.GROK: s.xai_model,
        AgentName.DEEPSEEK: s.deepseek_model,
        AgentName.GROQCLOUD: s.groq_model,
        AgentName.CEREBRAS: s.cerebras_model,
        AgentName.MISTRAL: s.mistral_model,
        AgentName.LOCAL: s.ollama_model,
    }[agent]


def is_configured(agent: AgentName, s: Settings | None = None) -> bool:
    return bool(_key_for(agent, s or get_settings()).strip())


def configured_agents(s: Settings | None = None) -> list[AgentName]:
    s = s or get_settings()
    return [a for a in ROSTER if is_configured(a, s)]


def build_agent(
    agent: AgentName,
    *,
    model: str | None = None,
    temperature: float | None = None,
    timeout: int | None = None,
    max_retries: int | None = None,
    settings: Settings | None = None,
) -> BaseAgent:
    s = settings or get_settings()
    cls = AGENT_CLASSES[agent]
    factory: Callable[..., BaseAgent] = getattr(cls, "from_settings")
    return factory(
        model=model,
        # Пробрасываем явно: иначе агент читал бы глобальные настройки и
        # молча игнорировал переданные — расхождение всплывало бы неожиданно.
        settings=s,
        temperature=s.temperature if temperature is None else temperature,
        timeout=s.request_timeout if timeout is None else timeout,
        max_retries=s.max_retries if max_retries is None else max_retries,
        max_tokens=s.max_tokens,
    )


def build_council(
    agents: Iterable[AgentName],
    *,
    models: dict[str, str] | None = None,
    temperature: float | None = None,
    timeout: int | None = None,
    max_retries: int | None = None,
    settings: Settings | None = None,
) -> dict[AgentName, BaseAgent]:
    models = models or {}
    council: dict[AgentName, BaseAgent] = {}
    for agent in agents:
        council[agent] = build_agent(
            agent,
            model=models.get(agent.value) or models.get(agent.value.lower()),
            temperature=temperature,
            timeout=timeout,
            max_retries=max_retries,
            settings=settings,
        )
    return council


def roster_status(s: Settings | None = None) -> list[dict[str, object]]:
    s = s or get_settings()
    out: list[dict[str, object]] = []
    for agent in ROSTER:
        key_env, model_env = ENV_HINTS[agent]
        out.append(
            {
                "agent": agent.value,
                "provider": AGENT_CLASSES[agent].provider,
                "model": default_model_for(agent, s),
                "configured": is_configured(agent, s),
                "key_env": key_env,
                "model_env": model_env,
                "free_tier": agent in FREE_TIER,
                "signup_url": SIGNUP_URLS.get(agent, ""),
            }
        )
    return out


async def list_models_for(agent: AgentName, settings: Settings | None = None) -> list[str]:
    """Какие модели реально доступны ключу. Пустой список — если не удалось."""
    built = build_agent(agent, settings=settings)
    try:
        return await built.list_models()
    finally:
        await built.aclose()
