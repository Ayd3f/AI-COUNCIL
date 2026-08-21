"""Timeout + retry helper used by every agent call.

Contract:
  * a single attempt is bounded by `timeout` seconds (asyncio.wait_for);
  * transport-ish failures (timeout, connection, 429, 5xx) are retried up to
    `max_retries` extra times with exponential backoff + jitter;
  * deterministic failures (auth, bad request, missing model) are *not*
    retried — retrying them only wastes the user's time.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar

from .logging import get_logger

log = get_logger(__name__)

T = TypeVar("T")


class ProviderError(Exception):
    """Normalised provider failure."""

    def __init__(self, message: str, kind: str = "provider_error", retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.kind = kind
        self.retryable = retryable

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


class TimeoutError_(ProviderError):
    def __init__(self, timeout: int):
        super().__init__(f"API timeout after {timeout}s", kind="timeout", retryable=True)


class SchemaError(ProviderError):
    def __init__(self, message: str):
        super().__init__(message, kind="invalid_output", retryable=False)


@dataclass
class RetryOutcome:
    attempts: int


def classify_exception(exc: BaseException) -> ProviderError:
    """Map an arbitrary SDK exception onto our normalised error model."""
    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return ProviderError("API timeout", kind="timeout", retryable=True)

    name = type(exc).__name__
    text = str(exc) or name
    lowered = text.lower()

    # An exhausted balance is permanent, but providers dress it up as a
    # transient status: OpenAI returns 429 `credit_balance_exhausted`, xAI
    # returns 403, DeepSeek returns 402. Retrying any of them only makes the
    # user wait longer for the same failure, so this check comes first.
    from .diagnostics import looks_like_billing

    if looks_like_billing(lowered):
        return ProviderError(f"{name}: {text}", kind="no_credits", retryable=False)

    # Deterministic, do not retry.
    if name in {
        "AuthenticationError",
        "PermissionDeniedError",
        "NotFoundError",
        "BadRequestError",
        "UnprocessableEntityError",
    }:
        return ProviderError(f"{name}: {text}", kind=name.lower(), retryable=False)
    if any(k in lowered for k in ("api key", "unauthorized", "invalid_api_key", "401")):
        return ProviderError(text, kind="authentication_error", retryable=False)
    if "model" in lowered and ("not found" in lowered or "does not exist" in lowered):
        return ProviderError(text, kind="model_not_found", retryable=False)

    # Transient, retry.
    if name in {
        "RateLimitError",
        "InternalServerError",
        "OverloadedError",
        "RetryableError",
        "ConflictError",
        "APIConnectionError",
        "APITimeoutError",
        "APIConnectionTimeoutError",
        "ServiceUnavailable",
        "ServerError",
        "ServerUnavailable",
        "DeadlineExceeded",
        "ResourceExhausted",
    }:
        return ProviderError(f"{name}: {text}", kind=name.lower(), retryable=True)
    if any(k in lowered for k in ("429", "rate limit", "overloaded", "timeout", "connection")):
        return ProviderError(text, kind="transient_error", retryable=True)
    if any(k in lowered for k in ("500", "502", "503", "504")):
        return ProviderError(text, kind="server_error", retryable=True)

    return ProviderError(f"{name}: {text}", kind="provider_error", retryable=False)


async def call_with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    timeout: int,
    max_retries: int,
    label: str = "call",
    base_delay: float = 0.8,
    max_delay: float = 8.0,
    on_attempt: Callable[[int], None] | None = None,
) -> tuple[T, RetryOutcome]:
    """Run `fn` with a per-attempt timeout and bounded retries.

    Raises `ProviderError` when every attempt fails.
    """
    attempts = 0
    last: ProviderError | None = None

    for attempt in range(max_retries + 1):
        attempts = attempt + 1
        if on_attempt:
            on_attempt(attempts)
        try:
            result = await asyncio.wait_for(fn(), timeout=timeout)
            return result, RetryOutcome(attempts=attempts)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            last = TimeoutError_(timeout)
        except BaseException as exc:  # noqa: BLE001 - normalised below
            last = classify_exception(exc)

        if not last.retryable or attempt >= max_retries:
            break

        delay = min(max_delay, base_delay * (2**attempt)) + random.uniform(0, 0.3)
        log.warning(
            "%s failed (%s): %s — retrying in %.1fs (attempt %d/%d)",
            label,
            last.kind,
            last.message[:200],
            delay,
            attempts,
            max_retries + 1,
        )
        await asyncio.sleep(delay)

    assert last is not None
    last.attempts = attempts  # type: ignore[attr-defined]
    raise last
