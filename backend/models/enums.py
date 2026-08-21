"""Enumerations shared across the backend."""

from __future__ import annotations

from enum import Enum


class AgentName(str, Enum):
    """Stable identity for every council member.

    These strings are used in prompts, in the database, in SSE events and in
    the UI. They must never be renamed casually — an agent's identity is how
    the other agents refer to its arguments.
    """

    OPENAI = "OPENAI"
    CLAUDE = "CLAUDE"
    GEMINI = "GEMINI"
    GROK = "GROK"
    DEEPSEEK = "DEEPSEEK"

    # Providers with a free tier. GROQCLOUD is deliberately not spelled
    # "GROQ": one letter away from "GROK" (xAI) would invite the models to
    # merge two distinct participants when citing each other's arguments.
    GROQCLOUD = "GROQCLOUD"
    CEREBRAS = "CEREBRAS"
    MISTRAL = "MISTRAL"
    LOCAL = "LOCAL"


class AgentStatus(str, Enum):
    IDLE = "IDLE"
    THINKING = "THINKING"
    OK = "OK"
    ERROR = "ERROR"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    DISABLED = "DISABLED"


class DebateStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class RoundKind(str, Enum):
    INITIAL = "INITIAL"     # round 0 — independent answers
    DEBATE = "DEBATE"       # round 1..N


class Stance(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    UNCERTAIN = "UNCERTAIN"


class EventType(str, Enum):
    DEBATE_STARTED = "debate_started"
    ROUND_STARTED = "round_started"
    AGENT_STARTED = "agent_started"
    AGENT_FINISHED = "agent_finished"
    AGENT_FAILED = "agent_failed"
    CONSENSUS_CHECK = "consensus_check"
    ROUND_FINISHED = "round_finished"
    SYNTHESIS_STARTED = "synthesis_started"
    DEBATE_FINISHED = "debate_finished"
    DEBATE_ERROR = "debate_error"
