"""Перевод ошибок провайдеров на человеческий язык.

Провайдеры отдают сырой JSON вроде
`{'error': {'message': 'Insufficient Balance', 'code': 'invalid_request_error'}}`.
Пользователю нужно другое: что именно случилось и что с этим делать.

Возвращается код (для интерфейсов, которым нужен свой язык) и готовый текст.
Ничего не выдумываем: если ошибка не опознана, отдаём код `unknown` и
показываем исходное сообщение как есть.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Diagnosis:
    code: str
    title: str
    action: str
    url: str = ""
    #: постоянная ли проблема — повторять запрос бессмысленно
    permanent: bool = True

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "code": self.code,
            "title": self.title,
            "action": self.action,
            "url": self.url,
            "permanent": self.permanent,
        }


BILLING_URLS = {
    "OPENAI": "https://platform.openai.com/settings/organization/billing",
    "CLAUDE": "https://console.anthropic.com/settings/billing",
    "GEMINI": "https://aistudio.google.com/apikey",
    "GROK": "https://console.x.ai/",
    "DEEPSEEK": "https://platform.deepseek.com/top_up",
}

MODEL_URLS = {
    "OPENAI": "https://platform.openai.com/docs/models",
    "CLAUDE": "https://docs.anthropic.com/en/docs/about-claude/models",
    "GEMINI": "https://ai.google.dev/gemini-api/docs/models",
    "GROK": "https://docs.x.ai/docs/models",
    "DEEPSEEK": "https://api-docs.deepseek.com/quick_start/pricing",
}

# Признаки того, что на счёте нет денег. Важно: OpenAI отдаёт это как 429,
# который в остальных случаях означает временное ограничение частоты.
_BILLING_PATTERNS = (
    "insufficient_quota",
    "insufficient quota",
    "credit_balance_exhausted",
    "insufficient balance",
    "no credits remaining",
    "doesn't have any credits",
    "does not have any credits",
    "exceeded your current quota",
    "billing",
    "payment required",
    "top up",
    "purchase those",
)

_MODEL_PATTERNS = (
    "no longer available",
    "does not exist",
    "not found",
    "not_found",
    "unknown model",
    "invalid model",
    "model_not_found",
    "unsupported model",
    "deprecated",
)

_AUTH_PATTERNS = (
    "invalid api key",
    "invalid_api_key",
    "incorrect api key",
    "unauthorized",
    "authentication",
    "invalid x-api-key",
    "api key not valid",
)


def looks_like_billing(text: str) -> bool:
    lowered = (text or "").lower()
    return any(p in lowered for p in _BILLING_PATTERNS)


def _model_name(text: str) -> str:
    m = re.search(r"models?/([\w.\-]+)", text or "")
    if m:
        return m.group(1)
    m = re.search(r"model[`'\" ]+([\w.\-]{3,})", text or "", re.IGNORECASE)
    return m.group(1) if m else ""


def explain(agent: str, error_kind: str | None, error_text: str | None) -> Diagnosis:
    agent = (agent or "").upper()
    kind = (error_kind or "").lower()
    text = error_text or ""
    lowered = text.lower()

    if kind == "not_configured":
        return Diagnosis(
            "not_configured",
            "Ключ не задан",
            f"Откройте «Настройки» → «Ключи» и добавьте ключ для {agent}. "
            "Без ключа этот участник просто не выходит на обсуждение.",
            BILLING_URLS.get(agent, ""),
        )

    # Деньги проверяем ПЕРВЫМ делом: OpenAI прячет исчерпанный баланс
    # внутри 429, который иначе выглядит как обычное ограничение частоты.
    if looks_like_billing(lowered):
        return Diagnosis(
            "no_credits",
            "На счёте провайдера нет средств",
            f"Ключ рабочий, но баланс {agent} исчерпан. Пополните счёт — "
            "или снимите галочку с этого участника, остальные продолжат без него.",
            BILLING_URLS.get(agent, ""),
        )

    if kind in {"invalid_output"}:
        return Diagnosis(
            "invalid_output",
            "Модель не вернула корректный JSON",
            "Программа уже переспросила её отдельным запросом и снова получила "
            "не то. Обычно так ведут себя совсем маленькие модели — выберите "
            "модель посильнее в «Настройки» → «Модели».",
            MODEL_URLS.get(agent, ""),
        )

    if kind == "timeout" or "timeout" in lowered or "timed out" in lowered:
        return Diagnosis(
            "timeout",
            "Провайдер не ответил вовремя",
            "Увеличьте таймаут в «Настройки» → «Обсуждение» или проверьте сеть. "
            "Участник вернётся в следующем раунде сам.",
            permanent=False,
        )

    if kind in {"model_not_found", "notfounderror"} or any(
        p in lowered for p in _MODEL_PATTERNS
    ):
        name = _model_name(text)
        which = f" «{name}»" if name else ""
        return Diagnosis(
            "model_not_found",
            "Такой модели нет",
            f"Модель{which} недоступна этому ключу — её сняли с публикации или "
            "переименовали. Впишите актуальную в «Настройки» → «Модели».",
            MODEL_URLS.get(agent, ""),
        )

    if kind in {"authentication_error", "authenticationerror"} or any(
        p in lowered for p in _AUTH_PATTERNS
    ):
        return Diagnosis(
            "auth",
            "Ключ не принят",
            f"Провайдер отклонил ключ {agent}. Проверьте, что он скопирован "
            "целиком и не отозван.",
            BILLING_URLS.get(agent, ""),
        )

    if kind in {"permissiondeniederror", "permission_denied"} or "permission" in lowered:
        return Diagnosis(
            "permission_denied",
            "Доступ запрещён",
            f"У ключа {agent} нет прав на эту модель. Обычно это значит, что "
            "аккаунт или команда ещё не активированы.",
            BILLING_URLS.get(agent, ""),
        )

    if kind in {"ratelimiterror", "rate_limit"} or "rate limit" in lowered or "429" in lowered:
        return Diagnosis(
            "rate_limit",
            "Слишком часто",
            "Провайдер ограничил частоту запросов. Программа уже повторяла "
            "попытку; если повторяется — уменьшите число раундов.",
            permanent=False,
        )

    if kind in {"overloadederror", "internalservererror", "server_error"} or any(
        c in lowered for c in ("overloaded", "500", "502", "503", "504")
    ):
        return Diagnosis(
            "provider_down",
            "У провайдера сбой",
            "Это на их стороне. Попробуйте позже — остальные участники "
            "обсуждение не остановят.",
            permanent=False,
        )

    return Diagnosis(
        "unknown",
        "Провайдер вернул ошибку",
        "Полный текст ниже — он от провайдера, не от программы.",
        MODEL_URLS.get(agent, ""),
    )


def short_hint(agent: str, error_kind: str | None, error_text: str | None) -> str:
    """Одна строка: заголовок и что делать."""
    d = explain(agent, error_kind, error_text)
    return f"{d.title}. {d.action}"
