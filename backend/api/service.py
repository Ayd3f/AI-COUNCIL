"""Application service: owns background debate tasks and DB reads.

Keeps FastAPI route handlers thin and keeps the orchestrator unaware of HTTP.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any

from ..agents import registry
from ..agents.roles import assign_roles
from ..config import Settings, get_settings
from ..debate.orchestrator import DebateOrchestrator, new_id
from ..models.enums import AgentName, DebateStatus
from ..models.schemas import (
    ConsensusReport,
    CostReport,
    DebateConfig,
    DebateDetail,
    DebateRequest,
    DebateSummary,
    RoundResult,
    SynthesisResult,
    TokenUsage,
)
from ..services.events import Event, EventBus
from ..services.logging import get_logger
from ..services.pricing import PricingTable, build_cost_report
from ..services.storage import DebateRepository

log = get_logger(__name__)


class ValidationProblem(ValueError):
    """Raised for user-fixable request problems (mapped to HTTP 400)."""


def _iso(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.isoformat()


class DebateService:
    def __init__(
        self,
        repo: DebateRepository,
        bus: EventBus,
        settings: Settings | None = None,
    ) -> None:
        self.repo = repo
        self.bus = bus
        self.settings = settings or get_settings()
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    # ------------------------------------------------------------- validation
    def build_config(self, request: DebateRequest) -> DebateConfig:
        s = self.settings
        question = (request.question or "").strip()
        if not question:
            raise ValidationProblem("Question must not be empty.")
        if len(question) > s.max_question_length:
            raise ValidationProblem(
                f"Question is too long ({len(question)} chars); the limit is "
                f"{s.max_question_length}."
            )

        requested = request.agents or registry.ROSTER
        selected = [a for a in registry.ROSTER if a in set(requested)]
        available = [a for a in selected if registry.is_configured(a, s)]
        if not available:
            missing = ", ".join(registry.ENV_HINTS[a][0] for a in selected)
            raise ValidationProblem(
                "None of the selected AI providers has an API key configured. "
                f"Set at least one of: {missing}"
            )
        if len(available) < 2:
            raise ValidationProblem(
                "A council debate needs at least two configured providers; only "
                f"{available[0].value} is available."
            )

        min_rounds = request.min_rounds if request.min_rounds is not None else s.min_rounds
        max_rounds = request.max_rounds if request.max_rounds is not None else s.max_rounds
        min_rounds = max(0, min(20, int(min_rounds)))
        max_rounds = max(1, min(20, int(max_rounds)))
        if min_rounds > max_rounds:
            min_rounds = max_rounds

        threshold = (
            request.consensus_threshold
            if request.consensus_threshold is not None
            else s.consensus_threshold
        )
        threshold = float(threshold)
        if threshold > 1.0:
            threshold /= 100.0
        threshold = max(0.0, min(1.0, threshold))

        timeout = int(request.timeout if request.timeout is not None else s.request_timeout)
        timeout = max(5, min(600, timeout))

        temperature = float(
            request.temperature if request.temperature is not None else s.temperature
        )
        temperature = max(0.0, min(2.0, temperature))

        max_retries = int(
            request.max_retries if request.max_retries is not None else s.max_retries
        )
        max_retries = max(0, min(5, max_retries))

        models = {
            a.value: (request.models or {}).get(a.value)
            or registry.default_model_for(a, s)
            for a in available
        }

        roles = (
            assign_roles(available, s.advocate_agent) if s.debate_roles else {}
        )

        return DebateConfig(
            agents=available,
            min_rounds=min_rounds,
            max_rounds=max_rounds,
            consensus_threshold=threshold,
            timeout=timeout,
            temperature=temperature,
            max_retries=max_retries,
            models=models,
            roles=roles,
        )

    # ------------------------------------------------------------------ start
    async def start_debate(self, request: DebateRequest) -> tuple[str, DebateConfig]:
        config = self.build_config(request)
        debate_id = new_id("dbt")
        conversation_id = new_id("cnv")
        question = request.question.strip()

        await self.repo.create_debate(debate_id, conversation_id, question, config)
        self.bus.channel(debate_id)  # create the channel before returning

        council = registry.build_council(
            config.agents,
            models=config.models,
            temperature=config.temperature,
            timeout=config.timeout,
            max_retries=config.max_retries,
            settings=self.settings,
        )
        orchestrator = DebateOrchestrator(
            debate_id=debate_id,
            question=question,
            config=config,
            council=council,
            repo=self.repo,
            bus=self.bus,
            settings=self.settings,
        )

        async def runner() -> None:
            try:
                await orchestrator.run()
            finally:
                await orchestrator.aclose()
                self._tasks.pop(debate_id, None)

        task = asyncio.create_task(runner(), name=f"debate:{debate_id}")
        self._tasks[debate_id] = task
        return debate_id, config

    def is_running(self, debate_id: str) -> bool:
        task = self._tasks.get(debate_id)
        return task is not None and not task.done()

    async def cancel(self, debate_id: str) -> bool:
        task = self._tasks.get(debate_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    # ------------------------------------------------------------------- read
    async def get_detail(self, debate_id: str) -> DebateDetail | None:
        row = await self.repo.get_debate_row(debate_id)
        if row is None:
            return None

        from ..services.storage import rounds_to_schema

        rounds: list[RoundResult] = [rounds_to_schema(r) for r in row.rounds]
        synthesis = None
        if row.synthesis:
            try:
                synthesis = SynthesisResult.model_validate(row.synthesis)
            except Exception:  # noqa: BLE001
                synthesis = None

        config = None
        if row.config:
            try:
                config = DebateConfig.model_validate(row.config)
            except Exception:  # noqa: BLE001
                config = None

        usage = await self.repo.get_usage(debate_id)
        table = PricingTable(self.settings.load_pricing())
        cost: CostReport = build_cost_report(usage, table)

        return DebateDetail(
            id=row.id,
            question=row.question,
            status=DebateStatus(row.status),
            created_at=_iso(row.created_at) or "",
            finished_at=_iso(row.finished_at),
            rounds_used=row.rounds_used,
            max_rounds=config.max_rounds if config else 0,
            consensus_reached=row.consensus_reached,
            consensus_score=row.consensus_score,
            config=config,
            rounds=rounds,
            synthesis=synthesis,
            synthesis_agent=row.synthesis_agent,
            cost=cost,
            error=row.error,
        )

    async def get_rounds(self, debate_id: str) -> list[RoundResult] | None:
        row = await self.repo.get_debate_row(debate_id)
        if row is None:
            return None
        from ..services.storage import rounds_to_schema

        return [rounds_to_schema(r) for r in row.rounds]

    async def list_debates(self, limit: int = 50) -> list[DebateSummary]:
        rows = await self.repo.list_debates(limit=limit)
        out: list[DebateSummary] = []
        for row in rows:
            max_rounds = 0
            if isinstance(row.config, dict):
                max_rounds = int(row.config.get("max_rounds") or 0)
            out.append(
                DebateSummary(
                    id=row.id,
                    question=row.question,
                    status=DebateStatus(row.status),
                    created_at=_iso(row.created_at) or "",
                    finished_at=_iso(row.finished_at),
                    rounds_used=row.rounds_used,
                    max_rounds=max_rounds,
                    consensus_reached=row.consensus_reached,
                    consensus_score=row.consensus_score,
                )
            )
        return out

    async def replay_events(self, debate_id: str) -> list[Event]:
        return await self.repo.get_events(debate_id)

    # ----------------------------------------------------------------- delete
    async def delete(self, debate_id: str) -> bool:
        await self.cancel(debate_id)
        self.bus.drop(debate_id)
        return await self.repo.delete_debate(debate_id)

    async def clear_all(self) -> int:
        await self.shutdown()
        return await self.repo.clear_all()


# --------------------------------------------------------------------------- #
# Markdown export
# --------------------------------------------------------------------------- #


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def _bar(value: float, width: int = 10) -> str:
    filled = int(round(max(0.0, min(1.0, value)) * width))
    return "█" * filled + "░" * (width - filled)


def export_markdown(detail: DebateDetail) -> str:
    lines: list[str] = []
    lines.append("# AI COUNCIL — DEBATE REPORT")
    lines.append("")
    lines.append(f"**Question:** {detail.question}")
    lines.append("")
    lines.append(f"- Debate ID: `{detail.id}`")
    lines.append(f"- Status: **{detail.status.value}**")
    lines.append(f"- Started: {detail.created_at}")
    if detail.finished_at:
        lines.append(f"- Finished: {detail.finished_at}")
    lines.append(f"- Rounds: {detail.rounds_used} / {detail.max_rounds}")
    lines.append(
        f"- Consensus: **{'REACHED' if detail.consensus_reached else 'NOT REACHED'}** "
        f"({_pct(detail.consensus_score)})"
    )
    if detail.config:
        lines.append(
            "- Participants: "
            + ", ".join(
                f"{a.value} (`{detail.config.models.get(a.value, '')}`)"
                for a in detail.config.agents
            )
        )
    if detail.synthesis_agent:
        lines.append(f"- Synthesis compiled by: {detail.synthesis_agent}")
    lines.append("")

    s = detail.synthesis
    if s:
        lines.append("---")
        lines.append("")
        lines.append("## FINAL ANSWER")
        lines.append("")
        lines.append(s.consensus)
        lines.append("")
        lines.append(f"**System confidence: {s.confidence:.0f}%**")
        lines.append("")
        if s.main_reasoning:
            lines.append("### Main reasoning")
            lines.append("")
            lines.append(s.main_reasoning)
            lines.append("")
        if s.key_agreements:
            lines.append("### Key agreements")
            lines.extend(f"- ✓ {x}" for x in s.key_agreements)
            lines.append("")
        if s.disagreements:
            lines.append("### Disagreements")
            lines.extend(f"- ⚠ {x}" for x in s.disagreements)
            lines.append("")
        if s.strongest_arguments:
            lines.append("### Strongest arguments")
            lines.extend(f"- {x}" for x in s.strongest_arguments)
            lines.append("")
        if s.rejected_arguments:
            lines.append("### Rejected arguments")
            for ra in s.rejected_arguments:
                who = ", ".join(ra.rejected_by) or "—"
                lines.append(f"- **{ra.argument}** — rejected by {who}: {ra.reason}")
            lines.append("")
        if s.minority_positions:
            lines.append("### Minority positions")
            for mp in s.minority_positions:
                lines.append(f"- **{mp.agent}**: {mp.position}")
                if mp.evidence:
                    lines.append(f"  - Evidence: {mp.evidence}")
                if mp.assessment:
                    lines.append(f"  - Assessment: {mp.assessment}")
            lines.append("")
        if s.individual_positions:
            lines.append("### Individual positions")
            for ip in s.individual_positions:
                lines.append(f"- **{ip.agent}**: {ip.position}")
            lines.append("")

    last_consensus = None
    for rnd in reversed(detail.rounds):
        if rnd.consensus is not None:
            last_consensus = rnd.consensus
            break
    if last_consensus and last_consensus.agreement_by_agent:
        lines.append("### Agreement")
        lines.append("")
        lines.append("```")
        for agent, level in sorted(last_consensus.agreement_by_agent.items()):
            lines.append(f"{agent:<10} {_bar(level)} {_pct(level)}")
        lines.append("```")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## DEBATE TRANSCRIPT")
    lines.append("")
    for rnd in detail.rounds:
        title = "ROUND 0 — INDEPENDENT ANSWERS" if rnd.index == 0 else f"ROUND {rnd.index} — DEBATE"
        lines.append(f"### {title}")
        lines.append("")
        for agent, outcome in sorted(rnd.outcomes.items()):
            lines.append(f"#### {agent}")
            if not outcome.ok or not outcome.payload:
                lines.append(
                    f"> Status: **{outcome.status.value}** — {outcome.error or 'no data'}"
                )
                lines.append("")
                continue
            p = outcome.payload
            if rnd.index == 0:
                lines.append(str(p.get("answer", "")))
                if p.get("key_points"):
                    lines.append("")
                    lines.append("Key points:")
                    lines.extend(f"- {k}" for k in p["key_points"])
                if p.get("assumptions"):
                    lines.append("")
                    lines.append("Assumptions:")
                    lines.extend(f"- {k}" for k in p["assumptions"])
            else:
                lines.append(str(p.get("position", "")))
                for label, key, icon in (
                    ("Accepted", "accepted_arguments", "✓"),
                    ("Rejected", "rejected_arguments", "✗"),
                    ("Uncertain", "uncertain_arguments", "?"),
                ):
                    items = p.get(key) or []
                    if items:
                        lines.append("")
                        lines.append(f"{label}:")
                        for it in items:
                            lines.append(
                                f"- {icon} [{it.get('agent')}] {it.get('argument')} "
                                f"— {it.get('reason')}"
                            )
                lines.append("")
                lines.append(
                    f"_Changed position: {p.get('changed_my_position')}_ — "
                    f"{p.get('why_changed', '')}"
                )
            lines.append("")
            lines.append(f"_Confidence: {p.get('confidence')}_")
            lines.append("")
        if rnd.consensus:
            c = rnd.consensus
            lines.append(
                f"> **Consensus check:** {'REACHED' if c.reached else 'not reached'} "
                f"— score {_pct(c.score)} (threshold {_pct(c.threshold)}), "
                f"rule `{c.rule}`. {c.reason}"
            )
            lines.append("")

    if detail.cost:
        lines.append("---")
        lines.append("")
        lines.append("## TOKENS & ESTIMATED COST")
        lines.append("")
        lines.append("| Agent | Model | Input | Output | Total | Est. cost |")
        lines.append("|---|---|---:|---:|---:|---:|")
        for line in detail.cost.lines:
            cost = (
                f"${line.estimated_cost_usd:.4f}"
                if line.estimated_cost_usd is not None
                else "n/a"
            )
            lines.append(
                f"| {line.agent} | `{line.model}` | {line.input_tokens:,} | "
                f"{line.output_tokens:,} | {line.total_tokens:,} | {cost} |"
            )
        lines.append(
            f"| **TOTAL** | | | | **{detail.cost.total_tokens:,}** | "
            f"**${detail.cost.estimated_cost_usd:.4f}** |"
        )
        if not detail.cost.complete:
            lines.append("")
            lines.append(
                "> Cost is partial — no price configured in `pricing.json` for: "
                + ", ".join(detail.cost.models_without_pricing)
            )
        lines.append("")

    return "\n".join(lines)
