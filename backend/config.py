"""Central configuration.

Everything is read from environment variables / `.env`.  API keys never leave
the backend process — the frontend receives only the *presence* flag for each
provider (see `api/routes.py::get_settings`).
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = Path(__file__).resolve().parent


class Settings(BaseSettings):
    """Application settings, overridable via environment or `.env`."""

    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------------------------------------------------------- API keys
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    google_api_key: str = ""
    xai_api_key: str = ""
    deepseek_api_key: str = ""

    # Провайдеры с бесплатным тарифом.
    groq_api_key: str = ""
    cerebras_api_key: str = ""
    mistral_api_key: str = ""
    # Локальная модель ключа не требует: участник включается, если задан адрес.
    ollama_base_url: str = ""

    # ------------------------------------------------------------------ Models
    # Every model is configurable; nothing in the architecture is tied to a
    # specific model name.
    # Имена моделей у провайдеров живут недолго. Если агент отвечает
    # «model_not_found» — поменяйте строку здесь или в окне «Настройки».
    openai_model: str = "gpt-4o"
    anthropic_model: str = "claude-opus-5"
    gemini_model: str = "gemini-3.5-flash"
    xai_model: str = "grok-3"
    deepseek_model: str = "deepseek-v4-flash"
    groq_model: str = "llama-3.3-70b-versatile"
    cerebras_model: str = "llama-3.3-70b"
    mistral_model: str = "mistral-large-latest"
    ollama_model: str = "llama3.1"

    # Anthropic thinking mode for structured replies:
    # "disabled" (whole token budget goes to the JSON payload — default),
    # "adaptive" (model reasons first; costs more tokens),
    # "default"  (send nothing; use whatever the model does by default).
    anthropic_thinking: str = "disabled"

    # --------------------------------------------------------------- Endpoints
    openai_base_url: str = "https://api.openai.com/v1"
    xai_base_url: str = "https://api.x.ai/v1"
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    cerebras_base_url: str = "https://api.cerebras.ai/v1"
    mistral_base_url: str = "https://api.mistral.ai/v1"

    # ------------------------------------------------------------ Debate rules
    min_rounds: int = 1
    max_rounds: int = 5
    consensus_threshold: float = 0.8

    # Круглый стол с ролями: каждый участник получает свою роль в споре и
    # активно переубеждает остальных. False — прежнее поведение, когда все
    # просто высказывают позиции параллельно.
    debate_roles: bool = True
    # Кто играет Адвоката — того, кто тянет совет к одному ответу.
    # "auto" — первый участник в порядке реестра.
    advocate_agent: str = "auto"

    # -------------------------------------------------------- Request handling
    temperature: float = 0.7
    request_timeout: int = 60          # seconds, per model call
    max_retries: int = 2               # additional attempts after the first
    max_tokens: int = 4000             # per model call
    max_question_length: int = 4000

    # ---------------------------------------------------------------- Synthesis
    # Which agent plays the neutral synthesiser role in the final stage.
    # "auto" -> first healthy agent in registry order.
    synthesis_agent: str = "auto"

    # ------------------------------------------------------------ Infra / misc
    database_url: str = "sqlite+aiosqlite:///./ai_council.db"
    cors_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:4173,http://127.0.0.1:4173,"
        "http://localhost:8080,http://127.0.0.1:8080"
    )
    rate_limit_requests: int = 20
    rate_limit_window_seconds: int = 60
    pricing_file: str = "pricing.json"
    log_level: str = "INFO"

    # ------------------------------------------------------------------ Helpers
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def pricing_path(self) -> Path:
        p = Path(self.pricing_file)
        return p if p.is_absolute() else BACKEND_ROOT / p

    def load_pricing(self) -> dict[str, Any]:
        """Load the (user-editable) pricing table.

        Returns an empty table when the file is missing or malformed — cost
        estimation is best-effort and must never break a debate.
        """
        try:
            with self.pricing_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Used by tests after mutating the environment."""
    get_settings.cache_clear()
    os.environ.pop("_AI_COUNCIL_SETTINGS_CACHE", None)


settings = get_settings()
