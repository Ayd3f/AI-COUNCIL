"""Strict wire schemas.

Every model reply is validated against one of these Pydantic models before any
application logic touches it.  Free-text parsing is *not* used for control
flow: the only text we consume verbatim is the human-readable prose inside
already-validated fields.

The schemas are intentionally free of JSON-Schema constraints (`minimum`,
`maxLength`, ...) because several providers reject those in strict structured
output mode.  Range enforcement happens in validators instead.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from .enums import AgentName, AgentStatus, DebateStatus, RoundKind

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _clamp01(value: Any) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.5
    if v != v:  # NaN
        return 0.5
    # Models sometimes answer "85" when asked for 0..1.
    if v > 1.0:
        v = v / 100.0 if v <= 100.0 else 1.0
    return max(0.0, min(1.0, v))


class StrictBase(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


# --------------------------------------------------------------------------- #
# ROUND 0 — independent answers
# --------------------------------------------------------------------------- #


class InitialAnswer(StrictBase):
    answer: str
    key_points: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    assumptions: list[str] = Field(default_factory=list)

    @field_validator("confidence", mode="before")
    @classmethod
    def _v_conf(cls, v: Any) -> float:
        return _clamp01(v)

    @field_validator("key_points", "assumptions", mode="before")
    @classmethod
    def _v_list(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        return [str(x) for x in v]


# --------------------------------------------------------------------------- #
# ROUND 1+ — debate
# --------------------------------------------------------------------------- #


class ArgumentRef(StrictBase):
    """A specific argument attributed to a specific other agent."""

    agent: str
    argument: str
    reason: str

    @field_validator("agent", mode="before")
    @classmethod
    def _v_agent(cls, v: Any) -> str:
        return str(v).strip().upper()


class PersuasionAttempt(StrictBase):
    """Адресная попытка переубедить конкретного участника."""

    agent: str
    their_objection: str
    my_counter: str
    concession: str = ""

    @field_validator("agent", mode="before")
    @classmethod
    def _v_agent(cls, v: Any) -> str:
        return str(v).strip().upper()

    @field_validator("concession", mode="before")
    @classmethod
    def _v_concession(cls, v: Any) -> str:
        return "" if v is None else str(v)

    @property
    def substantive(self) -> bool:
        return (
            len(self.their_objection.strip()) >= 15
            and len(self.my_counter.strip()) >= 15
        )


class DebateResponse(StrictBase):
    position: str
    accepted_arguments: list[ArgumentRef] = Field(default_factory=list)
    rejected_arguments: list[ArgumentRef] = Field(default_factory=list)
    uncertain_arguments: list[ArgumentRef] = Field(default_factory=list)
    persuasion: list[PersuasionAttempt] = Field(default_factory=list)
    persuaded_by: list[str] = Field(default_factory=list)
    what_would_change_my_mind: str = ""
    proposed_common_answer: str = ""
    changed_my_position: bool = False
    why_changed: str = ""
    confidence: float = 0.5

    @field_validator("confidence", mode="before")
    @classmethod
    def _v_conf(cls, v: Any) -> float:
        return _clamp01(v)

    @field_validator(
        "why_changed", "what_would_change_my_mind", "proposed_common_answer",
        mode="before",
    )
    @classmethod
    def _v_why(cls, v: Any) -> str:
        return "" if v is None else str(v)

    @field_validator("persuaded_by", mode="before")
    @classmethod
    def _v_persuaded_by(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            v = [v]
        return [str(x).strip().upper() for x in v if str(x).strip()]

    @field_validator("changed_my_position", mode="before")
    @classmethod
    def _v_changed(cls, v: Any) -> bool:
        if isinstance(v, str):
            return v.strip().lower() in {"true", "yes", "1"}
        return bool(v)

    @property
    def engagement_score(self) -> int:
        """How much *specific* engagement with other agents this reply shows.

        Used by the consensus engine to reject reflexive "I agree with
        everyone" replies (requirement: no automatic agreement).
        """
        specific = 0
        for ref in (
            self.accepted_arguments
            + self.rejected_arguments
            + self.uncertain_arguments
        ):
            if len(ref.argument.strip()) >= 15 and len(ref.reason.strip()) >= 15:
                specific += 1
        return specific + self.persuasion_score

    @property
    def persuasion_score(self) -> int:
        """Сколько адресных попыток переубедить сделано в этом раунде."""
        return sum(1 for p in self.persuasion if p.substantive)

    @property
    def is_capitulation(self) -> bool:
        """Смена позиции без указания, кто и чем переубедил.

        Именно это отличает настоящую сходимость от продавленной: агент,
        который «передумал» и не может назвать аргумент, просто уступил
        давлению большинства.
        """
        if not self.changed_my_position:
            return False
        named = [a for a in self.persuaded_by if a.strip()]
        return not named or len(self.why_changed.strip()) < 20


# --------------------------------------------------------------------------- #
# Consensus voting
# --------------------------------------------------------------------------- #


class ConsensusVote(StrictBase):
    """Each agent's own read of where the council currently stands."""

    main_conclusion: str = ""
    agrees_with_main_conclusion: bool = False
    agreement_level: float = 0.5
    has_material_objection: bool = False
    objection: str = ""
    evidence_for_objection: str = ""

    @field_validator("agreement_level", mode="before")
    @classmethod
    def _v_level(cls, v: Any) -> float:
        return _clamp01(v)

    @field_validator("agrees_with_main_conclusion", "has_material_objection", mode="before")
    @classmethod
    def _v_bool(cls, v: Any) -> bool:
        if isinstance(v, str):
            return v.strip().lower() in {"true", "yes", "1"}
        return bool(v)

    @field_validator("main_conclusion", "objection", "evidence_for_objection", mode="before")
    @classmethod
    def _v_str(cls, v: Any) -> str:
        return "" if v is None else str(v)


# --------------------------------------------------------------------------- #
# Final synthesis
# --------------------------------------------------------------------------- #


class MinorityPosition(StrictBase):
    agent: str
    position: str
    evidence: str = ""
    assessment: str = ""


class RejectedArgumentSummary(StrictBase):
    argument: str
    rejected_by: list[str] = Field(default_factory=list)
    reason: str = ""

    @field_validator("rejected_by", mode="before")
    @classmethod
    def _v_list(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        return [str(x) for x in v]


class IndividualPosition(StrictBase):
    agent: str
    position: str


class SynthesisResult(StrictBase):
    consensus: str
    main_reasoning: str = ""
    key_agreements: list[str] = Field(default_factory=list)
    disagreements: list[str] = Field(default_factory=list)
    strongest_arguments: list[str] = Field(default_factory=list)
    rejected_arguments: list[RejectedArgumentSummary] = Field(default_factory=list)
    minority_positions: list[MinorityPosition] = Field(default_factory=list)
    individual_positions: list[IndividualPosition] = Field(default_factory=list)
    confidence: float = 50.0

    @field_validator("confidence", mode="before")
    @classmethod
    def _v_conf(cls, v: Any) -> float:
        """Synthesis confidence is reported 0..100."""
        try:
            f = float(v)
        except (TypeError, ValueError):
            return 50.0
        if f <= 1.0:
            f *= 100.0
        return max(0.0, min(100.0, f))

    @field_validator(
        "key_agreements", "disagreements", "strongest_arguments", mode="before"
    )
    @classmethod
    def _v_list(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        return [str(x) for x in v]


# --------------------------------------------------------------------------- #
# Runtime / transport objects (not model outputs)
# --------------------------------------------------------------------------- #


class TokenUsage(StrictBase):
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


class AgentOutcome(StrictBase):
    """Result of one agent call in one round."""

    agent: AgentName
    status: AgentStatus
    model: str = ""
    payload: dict[str, Any] | None = None
    error: str | None = None
    error_kind: str | None = None
    attempts: int = 1
    latency_ms: int = 0
    usage: TokenUsage = Field(default_factory=TokenUsage)

    @property
    def ok(self) -> bool:
        return self.status == AgentStatus.OK

    # Derived at read time rather than stored, so old databases keep working.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def diagnosis(self) -> dict[str, Any] | None:
        """Plain-language reading of `error`: what happened and what to do."""
        if self.ok or (self.error is None and self.error_kind is None):
            return None
        from ..services.diagnostics import explain

        return explain(self.agent.value, self.error_kind, self.error).as_dict()


class ConsensusReport(StrictBase):
    """Deterministic output of the Consensus Engine (computed in code)."""

    round_index: int
    reached: bool
    score: float                       # 0..1, mean weighted agreement
    threshold: float
    agree_count: int
    participant_count: int
    rule: str                          # which rule fired / why it did not
    reason: str
    agreement_by_agent: dict[str, float] = Field(default_factory=dict)
    agreeing_agents: list[str] = Field(default_factory=list)
    dissenting_agents: list[str] = Field(default_factory=list)
    unsubstantiated_agents: list[str] = Field(default_factory=list)
    #: сменили позицию, но не назвали, кто их переубедил
    capitulated_agents: list[str] = Field(default_factory=list)
    #: кто кого переубедил в этом раунде: [{"agent": .., "persuaded_by": ..}]
    persuasion_edges: list[dict[str, str]] = Field(default_factory=list)
    material_objections: list[dict[str, str]] = Field(default_factory=list)
    main_conclusion: str = ""


class RoundResult(StrictBase):
    index: int
    kind: RoundKind
    outcomes: dict[str, AgentOutcome] = Field(default_factory=dict)
    consensus: ConsensusReport | None = None
    started_at: str = ""
    finished_at: str = ""


class DebateRequest(StrictBase):
    question: str
    agents: list[AgentName] | None = None
    min_rounds: int | None = None
    max_rounds: int | None = None
    consensus_threshold: float | None = None
    timeout: int | None = None
    temperature: float | None = None
    max_retries: int | None = None
    models: dict[str, str] | None = None


class DebateConfig(StrictBase):
    """Effective, validated configuration for a single debate run."""

    agents: list[AgentName]
    min_rounds: int
    max_rounds: int
    consensus_threshold: float
    timeout: int
    temperature: float
    max_retries: int
    models: dict[str, str] = Field(default_factory=dict)
    #: агент -> роль за круглым столом (ADVOCATE, SKEPTIC, ...)
    roles: dict[str, str] = Field(default_factory=dict)


class CostLine(StrictBase):
    agent: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_usd: float | None = None
    pricing_known: bool = False


class CostReport(StrictBase):
    lines: list[CostLine] = Field(default_factory=list)
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    complete: bool = True
    models_without_pricing: list[str] = Field(default_factory=list)
    currency: str = "USD"


class DebateSummary(StrictBase):
    id: str
    question: str
    status: DebateStatus
    created_at: str
    finished_at: str | None = None
    rounds_used: int = 0
    max_rounds: int = 0
    consensus_reached: bool = False
    consensus_score: float = 0.0


class DebateDetail(DebateSummary):
    config: DebateConfig | None = None
    rounds: list[RoundResult] = Field(default_factory=list)
    synthesis: SynthesisResult | None = None
    synthesis_agent: str | None = None
    cost: CostReport | None = None
    error: str | None = None
