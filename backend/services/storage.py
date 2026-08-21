"""Persistence layer.

Thin repository over the SQLAlchemy models so the orchestrator never touches
sessions directly and the storage backend stays swappable.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from ..models import db as dbm
from ..models.enums import DebateStatus
from ..models.schemas import (
    AgentOutcome,
    ConsensusReport,
    DebateConfig,
    RoundResult,
    TokenUsage,
)
from .events import Event


def _iso(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.isoformat()


class DebateRepository:
    """All writes go through one lock.

    A debate round writes from five concurrent agent tasks. SQLite allows only
    one writer at a time, so without serialisation the losers of the race get
    `database is locked` and their rows — including live events — are dropped.
    A single in-process lock removes the contention entirely; the WAL mode and
    busy timeout set in `models/db.py` cover the case of a second process
    (web backend + desktop app) holding the same file open.
    """

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sessionmaker
        self._write_lock = asyncio.Lock()

    @asynccontextmanager
    async def _write(self) -> AsyncIterator[AsyncSession]:
        async with self._write_lock:
            async with self._sm() as session:
                yield session
                await session.commit()

    # ------------------------------------------------------------------ create
    async def create_debate(
        self, debate_id: str, conversation_id: str, question: str, config: DebateConfig
    ) -> None:
        async with self._write() as session:
            session.add(dbm.Conversation(id=conversation_id, question=question))
            session.add(
                dbm.Debate(
                    id=debate_id,
                    conversation_id=conversation_id,
                    question=question,
                    status=DebateStatus.PENDING.value,
                    config=config.model_dump(mode="json"),
                )
            )

    async def set_status(
        self,
        debate_id: str,
        status: DebateStatus,
        *,
        error: str | None = None,
        finished: bool = False,
    ) -> None:
        async with self._write() as session:
            debate = await session.get(dbm.Debate, debate_id)
            if debate is None:
                return
            debate.status = status.value
            if error is not None:
                debate.error = error
            if finished:
                debate.finished_at = dbm.utcnow()

    # ------------------------------------------------------------------ rounds
    async def start_round(self, debate_id: str, index: int, kind: str) -> int:
        async with self._write() as session:
            row = dbm.Round(debate_id=debate_id, index=index, kind=kind)
            session.add(row)
            await session.flush()   # assigns the primary key
            round_id = int(row.id)
        return round_id

    async def finish_round(self, round_id: int) -> None:
        async with self._write() as session:
            row = await session.get(dbm.Round, round_id)
            if row is not None:
                row.finished_at = dbm.utcnow()

    async def save_agent_outcome(
        self, debate_id: str, round_id: int, outcome: AgentOutcome
    ) -> None:
        async with self._write() as session:
            session.add(
                dbm.AgentResponse(
                    round_id=round_id,
                    debate_id=debate_id,
                    agent=outcome.agent.value,
                    status=outcome.status.value,
                    model=outcome.model,
                    payload=outcome.payload,
                    error=outcome.error,
                    error_kind=outcome.error_kind,
                    attempts=outcome.attempts,
                    latency_ms=outcome.latency_ms,
                    input_tokens=outcome.usage.input_tokens,
                    output_tokens=outcome.usage.output_tokens,
                )
            )

    async def save_consensus(
        self, debate_id: str, round_id: int, report: ConsensusReport
    ) -> None:
        async with self._write() as session:
            session.add(
                dbm.ConsensusResult(
                    round_id=round_id,
                    debate_id=debate_id,
                    reached=report.reached,
                    score=report.score,
                    threshold=report.threshold,
                    rule=report.rule,
                    detail=report.model_dump(mode="json"),
                )
            )

    # ------------------------------------------------------------------- usage
    async def add_usage(
        self, debate_id: str, agent: str, model: str, usage: TokenUsage
    ) -> None:
        async with self._write() as session:
            stmt = select(dbm.TokenUsageRow).where(
                dbm.TokenUsageRow.debate_id == debate_id,
                dbm.TokenUsageRow.agent == agent,
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                # Column defaults are only applied on flush, so seed the
                # counters explicitly before accumulating into them.
                row = dbm.TokenUsageRow(
                    debate_id=debate_id,
                    agent=agent,
                    model=model,
                    calls=0,
                    input_tokens=0,
                    output_tokens=0,
                )
                session.add(row)
            row.model = model or row.model
            row.input_tokens += usage.input_tokens
            row.output_tokens += usage.output_tokens
            row.calls += 1

    async def get_usage(self, debate_id: str) -> dict[str, tuple[str, TokenUsage]]:
        async with self._sm() as session:
            stmt = select(dbm.TokenUsageRow).where(
                dbm.TokenUsageRow.debate_id == debate_id
            )
            rows = (await session.execute(stmt)).scalars().all()
            return {
                r.agent: (
                    r.model,
                    TokenUsage(input_tokens=r.input_tokens, output_tokens=r.output_tokens),
                )
                for r in rows
            }

    # ------------------------------------------------------------------ finish
    async def finalize(
        self,
        debate_id: str,
        *,
        rounds_used: int,
        consensus_reached: bool,
        consensus_score: float,
        synthesis: dict[str, Any] | None,
        synthesis_agent: str | None,
    ) -> None:
        async with self._write() as session:
            debate = await session.get(dbm.Debate, debate_id)
            if debate is None:
                return
            debate.rounds_used = rounds_used
            debate.consensus_reached = consensus_reached
            debate.consensus_score = consensus_score
            debate.synthesis = synthesis
            debate.synthesis_agent = synthesis_agent
            debate.status = DebateStatus.COMPLETED.value
            debate.finished_at = dbm.utcnow()

    # ------------------------------------------------------------------ events
    async def save_event(self, event: Event) -> None:
        async with self._write() as session:
            session.add(
                dbm.DebateEvent(
                    debate_id=event.debate_id,
                    seq=event.seq,
                    type=event.type,
                    data=event.data,
                )
            )

    async def get_events(self, debate_id: str) -> list[Event]:
        async with self._sm() as session:
            stmt = (
                select(dbm.DebateEvent)
                .where(dbm.DebateEvent.debate_id == debate_id)
                .order_by(dbm.DebateEvent.seq)
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [
                Event(
                    seq=r.seq,
                    type=r.type,
                    debate_id=r.debate_id,
                    data=r.data or {},
                    created_at=_iso(r.created_at) or "",
                )
                for r in rows
            ]

    # -------------------------------------------------------------------- read
    async def get_debate_row(self, debate_id: str) -> dbm.Debate | None:
        async with self._sm() as session:
            stmt = (
                select(dbm.Debate)
                .where(dbm.Debate.id == debate_id)
                .options(
                    selectinload(dbm.Debate.rounds).selectinload(dbm.Round.responses),
                    selectinload(dbm.Debate.rounds).selectinload(dbm.Round.consensus),
                )
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    async def get_rounds(self, debate_id: str) -> list[RoundResult]:
        debate = await self.get_debate_row(debate_id)
        if debate is None:
            return []
        return [rounds_to_schema(r) for r in debate.rounds]

    async def list_debates(self, limit: int = 50) -> Sequence[dbm.Debate]:
        async with self._sm() as session:
            stmt = (
                select(dbm.Debate)
                .order_by(dbm.Debate.created_at.desc())
                .limit(limit)
            )
            return (await session.execute(stmt)).scalars().all()

    async def delete_debate(self, debate_id: str) -> bool:
        deleted = False
        async with self._write() as session:
            debate = await session.get(dbm.Debate, debate_id)
            if debate is None:
                return False
            conv_id = debate.conversation_id
            await session.delete(debate)
            await session.flush()
            remaining = await session.scalar(
                select(func.count())
                .select_from(dbm.Debate)
                .where(dbm.Debate.conversation_id == conv_id)
            )
            if not remaining:
                conv = await session.get(dbm.Conversation, conv_id)
                if conv is not None:
                    await session.delete(conv)
            deleted = True
        return deleted

    async def clear_all(self) -> int:
        async with self._write() as session:
            count = await session.scalar(select(func.count()).select_from(dbm.Debate)) or 0
            await session.execute(delete(dbm.DebateEvent))
            await session.execute(delete(dbm.TokenUsageRow))
            await session.execute(delete(dbm.ConsensusResult))
            await session.execute(delete(dbm.AgentResponse))
            await session.execute(delete(dbm.Round))
            await session.execute(delete(dbm.Debate))
            await session.execute(delete(dbm.Conversation))
        return int(count)


def rounds_to_schema(row: dbm.Round) -> RoundResult:
    outcomes: dict[str, AgentOutcome] = {}
    for resp in row.responses:
        outcomes[resp.agent] = AgentOutcome(
            agent=resp.agent,  # type: ignore[arg-type]
            status=resp.status,  # type: ignore[arg-type]
            model=resp.model,
            payload=resp.payload,
            error=resp.error,
            error_kind=resp.error_kind,
            attempts=resp.attempts,
            latency_ms=resp.latency_ms,
            usage=TokenUsage(
                input_tokens=resp.input_tokens, output_tokens=resp.output_tokens
            ),
        )
    consensus = None
    if row.consensus is not None and row.consensus.detail:
        try:
            consensus = ConsensusReport.model_validate(row.consensus.detail)
        except Exception:  # noqa: BLE001 - never break a read on legacy rows
            consensus = None
    return RoundResult(
        index=row.index,
        kind=row.kind,  # type: ignore[arg-type]
        outcomes=outcomes,
        consensus=consensus,
        started_at=_iso(row.started_at) or "",
        finished_at=_iso(row.finished_at) or "",
    )
