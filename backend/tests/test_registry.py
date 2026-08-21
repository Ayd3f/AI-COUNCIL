"""Целостность реестра участников.

Добавить провайдера — это правка в нескольких местах сразу. Эти тесты ловят
случай «добавил в enum, забыл в одном из словарей»: без них такая ошибка
всплыла бы только в рантайме, у пользователя.
"""

from __future__ import annotations

import pytest

from ..agents import registry
from ..agents.prompts import identity, system_prompt
from ..config import Settings
from ..models.enums import AgentName


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def test_every_enum_member_is_registered():
    assert set(registry.AGENT_CLASSES) == set(AgentName)
    assert set(registry.ENV_HINTS) == set(AgentName)
    assert set(registry.SIGNUP_URLS) == set(AgentName)
    assert len(registry.ROSTER) == len(AgentName)


@pytest.mark.parametrize("agent", list(AgentName))
def test_every_agent_resolves_a_key_and_a_model(agent: AgentName):
    s = _settings()
    # не бросает KeyError и возвращает строку
    assert isinstance(registry._key_for(agent, s), str)
    assert registry.default_model_for(agent, s)
    key_env, model_env = registry.ENV_HINTS[agent]
    assert key_env and model_env
    assert registry.SIGNUP_URLS[agent].startswith("http")


@pytest.mark.parametrize("agent", list(AgentName))
def test_every_agent_has_a_ui_colour(agent: AgentName):
    pytest.importorskip("PySide6", reason="десктопная тема требует PySide6")
    from desktop.theme import AGENT_COLORS

    assert agent.value in AGENT_COLORS


def test_free_tier_marks_only_providers_that_have_one():
    assert AgentName.GROQCLOUD in registry.FREE_TIER
    assert AgentName.CEREBRAS in registry.FREE_TIER
    assert AgentName.MISTRAL in registry.FREE_TIER
    assert AgentName.LOCAL in registry.FREE_TIER
    assert AgentName.GEMINI in registry.FREE_TIER
    # У этих бесплатного тарифа для API нет.
    assert AgentName.OPENAI not in registry.FREE_TIER
    assert AgentName.CLAUDE not in registry.FREE_TIER


def test_roster_status_exposes_what_the_ui_needs():
    rows = registry.roster_status(_settings())
    assert len(rows) == len(AgentName)
    for row in rows:
        assert set(row) >= {
            "agent", "provider", "model", "configured",
            "key_env", "model_env", "free_tier", "signup_url",
        }


# --------------------------------------------------------------------------- #
# Локальный участник
# --------------------------------------------------------------------------- #


def test_local_agent_is_enabled_by_address_not_by_key():
    s = _settings(ollama_base_url="http://localhost:11434/v1")
    assert registry.is_configured(AgentName.LOCAL, s) is True

    agent = registry.build_agent(AgentName.LOCAL, settings=s)
    assert agent.enabled is True

    off = _settings(ollama_base_url="")
    assert registry.is_configured(AgentName.LOCAL, off) is False


def test_local_agent_stays_disabled_without_an_address():
    from ..agents.free_agents import LocalAgent

    agent = LocalAgent(api_key="local", model="llama3.1", base_url="")
    assert agent.enabled is False


# --------------------------------------------------------------------------- #
# Идентичность: GROK и GROQCLOUD не должны сливаться
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("agent", list(AgentName))
def test_identity_names_the_agent_and_every_other_participant(agent: AgentName):
    text = system_prompt(agent)
    assert f"Your identity in this council is {agent.value}" in text
    for other in AgentName:
        if other is not agent:
            assert other.value in text


def test_grok_and_groqcloud_are_explicitly_separated():
    """Имена различаются одной буквой — модели обязаны их различать."""
    for agent in (AgentName.GROK, AgentName.GROQCLOUD):
        text = identity(agent)
        assert "GROK" in text and "GROQCLOUD" in text
        assert "Never merge them" in text
        assert "one letter" in text


def test_build_agent_honours_the_settings_it_is_given():
    """Регрессия: агенты читали глобальные настройки и игнорировали переданные."""
    s = _settings(
        groq_api_key="explicit-key",
        groq_model="explicit-model",
        groq_base_url="https://example.invalid/v1",
        request_timeout=17,
        max_retries=4,
        temperature=0.11,
    )
    agent = registry.build_agent(AgentName.GROQCLOUD, settings=s)
    assert agent.api_key == "explicit-key"
    assert agent.model == "explicit-model"
    assert agent.base_url == "https://example.invalid/v1"  # type: ignore[attr-defined]
    assert agent.timeout == 17
    assert agent.max_retries == 4
    assert agent.temperature == pytest.approx(0.11)


def test_build_council_passes_per_agent_model_overrides():
    s = _settings(groq_api_key="k", cerebras_api_key="k")
    council = registry.build_council(
        [AgentName.GROQCLOUD, AgentName.CEREBRAS],
        models={"GROQCLOUD": "model-a", "CEREBRAS": "model-b"},
        settings=s,
    )
    assert council[AgentName.GROQCLOUD].model == "model-a"
    assert council[AgentName.CEREBRAS].model == "model-b"


def test_free_agents_do_not_reuse_the_paid_base_urls():
    s = _settings()
    urls = {
        registry.build_agent(a, settings=s).base_url  # type: ignore[attr-defined]
        for a in (
            AgentName.OPENAI,
            AgentName.GROK,
            AgentName.DEEPSEEK,
            AgentName.GROQCLOUD,
            AgentName.CEREBRAS,
            AgentName.MISTRAL,
        )
    }
    assert len(urls) == 6, "у OpenAI-совместимых провайдеров должны быть разные адреса"


async def test_list_models_is_not_implemented_by_default():
    from ..agents.base import BaseAgent
    from ..models.enums import AgentName as N

    class Dummy(BaseAgent):
        name = N.OPENAI

        async def _complete(self, **_kw):  # pragma: no cover - не вызывается
            raise NotImplementedError

    with pytest.raises(NotImplementedError):
        await Dummy(api_key="k", model="m").list_models()
