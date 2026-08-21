"""Logging setup.

Deliberately never logs prompt bodies or API keys — only structural metadata.
"""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def setup_logging(level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)-7s | %(name)-28s | %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Third-party SDKs are chatty at DEBUG.
    for noisy in ("httpx", "httpcore", "openai", "anthropic", "google_genai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def redact(text: str, *secrets: str) -> str:
    """Remove any configured secret from a string before logging it."""
    out = text
    for s in secrets:
        if s and len(s) > 6:
            out = out.replace(s, "***redacted***")
    return out
