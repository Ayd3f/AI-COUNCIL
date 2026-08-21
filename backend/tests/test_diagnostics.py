"""Разбор реальных ошибок провайдеров.

Все строки ниже — дословные ответы API, полученные на живых ключах.
"""

from __future__ import annotations

from ..models.enums import AgentName, AgentStatus
from ..models.schemas import AgentOutcome
from ..services.diagnostics import explain, looks_like_billing
from ..services.retry import classify_exception

# --------------------------------------------------------------------------- #
# Настоящие ответы провайдеров
# --------------------------------------------------------------------------- #

OPENAI_NO_CREDITS = (
    "Error code: 429 - {'error': {'message': 'You have no credits remaining. "
    "Add credits to continue using the API at "
    "https://platform.openai.com/settings/organization/billing.', "
    "'type': 'insufficient_quota', 'param': None, "
    "'code': 'credit_balance_exhausted'}}"
)
DEEPSEEK_NO_BALANCE = (
    "Error code: 402 - {'error': {'message': 'Insufficient Balance', "
    "'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}"
)
XAI_NO_CREDITS = (
    "Error code: 403 - {'code': 'permission-denied', 'error': \"Your newly "
    "created team doesn't have any credits or licenses yet. You can purchase "
    'those on https://console.x.ai/team/abc."}'
)
GEMINI_MODEL_GONE = (
    "404 NOT_FOUND. {'error': {'code': 404, 'message': 'This model "
    "models/gemini-2.5-flash is no longer available to new users. Please "
    "update your code to use a newer model.', 'status': 'NOT_FOUND'}}"
)


# --------------------------------------------------------------------------- #
# Классификация: постоянные ошибки нельзя повторять
# --------------------------------------------------------------------------- #


class RateLimitError(Exception):
    pass


class APIStatusError(Exception):
    pass


class PermissionDeniedError(Exception):
    pass


def test_exhausted_balance_is_never_retried_even_inside_a_429():
    """Раньше 429 считался временным, и программа тратила три попытки."""
    err = classify_exception(RateLimitError(OPENAI_NO_CREDITS))
    assert err.retryable is False
    assert err.kind == "no_credits"


def test_deepseek_402_is_not_retried():
    err = classify_exception(APIStatusError(DEEPSEEK_NO_BALANCE))
    assert err.retryable is False
    assert err.kind == "no_credits"


def test_xai_403_without_credits_is_not_retried():
    err = classify_exception(PermissionDeniedError(XAI_NO_CREDITS))
    assert err.retryable is False
    assert err.kind == "no_credits"


def test_a_genuine_rate_limit_is_still_retried():
    err = classify_exception(RateLimitError("Error code: 429 - rate limit exceeded, slow down"))
    assert err.retryable is True


def test_server_errors_are_still_retried():
    class InternalServerError(Exception):
        pass

    assert classify_exception(InternalServerError("503 upstream unavailable")).retryable is True


# --------------------------------------------------------------------------- #
# Человеческие объяснения
# --------------------------------------------------------------------------- #


def test_billing_detector_covers_every_provider_wording():
    assert looks_like_billing(OPENAI_NO_CREDITS.lower())
    assert looks_like_billing(DEEPSEEK_NO_BALANCE.lower())
    assert looks_like_billing(XAI_NO_CREDITS.lower())
    assert not looks_like_billing(GEMINI_MODEL_GONE.lower())


def test_no_credits_explains_what_to_do():
    d = explain("OPENAI", "ratelimiterror", OPENAI_NO_CREDITS)
    assert d.code == "no_credits"
    assert "баланс" in d.action.lower() or "пополните" in d.action.lower()
    assert d.url.startswith("https://platform.openai.com")
    assert d.permanent is True


def test_retired_model_is_named_in_the_advice():
    d = explain("GEMINI", "provider_error", GEMINI_MODEL_GONE)
    assert d.code == "model_not_found"
    assert "gemini-2.5-flash" in d.action
    assert "Настройки" in d.action


def test_missing_key_is_not_reported_as_a_failure():
    d = explain("CLAUDE", "not_configured", "No API key configured for this provider")
    assert d.code == "not_configured"
    assert "CLAUDE" in d.action


def test_timeout_is_marked_temporary():
    d = explain("GROK", "timeout", "API timeout after 60s")
    assert d.code == "timeout"
    assert d.permanent is False


def test_unknown_errors_do_not_invent_an_explanation():
    d = explain("OPENAI", "provider_error", "Something nobody has seen before")
    assert d.code == "unknown"
    assert "провайдера" in d.action


def test_outcome_exposes_the_diagnosis_without_a_database_column():
    outcome = AgentOutcome(
        agent=AgentName.DEEPSEEK,
        status=AgentStatus.ERROR,
        model="deepseek-v4-flash",
        error=DEEPSEEK_NO_BALANCE,
        error_kind="no_credits",
    )
    data = outcome.model_dump(mode="json")
    assert data["diagnosis"]["code"] == "no_credits"
    assert data["diagnosis"]["url"]


def test_successful_outcome_has_no_diagnosis():
    outcome = AgentOutcome(
        agent=AgentName.OPENAI,
        status=AgentStatus.OK,
        model="gpt-4o",
        payload={"answer": "x", "key_points": [], "confidence": 0.5, "assumptions": []},
    )
    assert outcome.model_dump(mode="json")["diagnosis"] is None
