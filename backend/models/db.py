"""SQLAlchemy 2.0 async ORM models.

SQLite is the MVP target (`sqlite+aiosqlite:///./ai_council.db`), but every
column type used here (String / Integer / Float / Boolean / JSON / DateTime)
maps cleanly onto PostgreSQL, so switching is a matter of changing
`DATABASE_URL` to `postgresql+asyncpg://...` and installing `asyncpg`.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    """A user question. Kept separate from Debate so a single question can be
    re-debated later (different settings, different models)."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    debates: Mapped[list["Debate"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Debate(Base):
    __tablename__ = "debates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    rounds_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consensus_reached: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    consensus_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    synthesis: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    synthesis_agent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    conversation: Mapped[Conversation] = relationship(back_populates="debates")
    rounds: Mapped[list["Round"]] = relationship(
        back_populates="debate",
        cascade="all, delete-orphan",
        order_by="Round.index",
    )
    events: Mapped[list["DebateEvent"]] = relationship(
        back_populates="debate",
        cascade="all, delete-orphan",
        order_by="DebateEvent.seq",
    )
    token_usage: Mapped[list["TokenUsageRow"]] = relationship(
        back_populates="debate", cascade="all, delete-orphan"
    )


class Round(Base):
    __tablename__ = "rounds"
    __table_args__ = (UniqueConstraint("debate_id", "index", name="uq_round_debate_index"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    debate_id: Mapped[str] = mapped_column(
        ForeignKey("debates.id", ondelete="CASCADE"), index=True, nullable=False
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    debate: Mapped[Debate] = relationship(back_populates="rounds")
    responses: Mapped[list["AgentResponse"]] = relationship(
        back_populates="round", cascade="all, delete-orphan"
    )
    consensus: Mapped["ConsensusResult | None"] = relationship(
        back_populates="round", cascade="all, delete-orphan", uselist=False
    )


class AgentResponse(Base):
    __tablename__ = "agent_responses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    round_id: Mapped[int] = mapped_column(
        ForeignKey("rounds.id", ondelete="CASCADE"), index=True, nullable=False
    )
    debate_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    agent: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    round: Mapped[Round] = relationship(back_populates="responses")


class ConsensusResult(Base):
    __tablename__ = "consensus_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    round_id: Mapped[int] = mapped_column(
        ForeignKey("rounds.id", ondelete="CASCADE"), index=True, nullable=False
    )
    debate_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    reached: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    threshold: Mapped[float] = mapped_column(Float, default=0.8, nullable=False)
    rule: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    round: Mapped[Round] = relationship(back_populates="consensus")


class TokenUsageRow(Base):
    __tablename__ = "token_usage"
    __table_args__ = (
        UniqueConstraint("debate_id", "agent", name="uq_usage_debate_agent"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    debate_id: Mapped[str] = mapped_column(
        ForeignKey("debates.id", ondelete="CASCADE"), index=True, nullable=False
    )
    agent: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    debate: Mapped[Debate] = relationship(back_populates="token_usage")


class DebateEvent(Base):
    """Persisted live-update stream, so a reconnecting client can replay."""

    __tablename__ = "debate_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    debate_id: Mapped[str] = mapped_column(
        ForeignKey("debates.id", ondelete="CASCADE"), index=True, nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(48), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    debate: Mapped[Debate] = relationship(back_populates="events")


# --------------------------------------------------------------------------- #
# Engine / session factory
# --------------------------------------------------------------------------- #

_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def init_engine(database_url: str) -> async_sessionmaker[AsyncSession]:
    global _engine, _sessionmaker
    is_sqlite = database_url.startswith("sqlite")

    connect_args: dict[str, Any] = {}
    if is_sqlite:
        connect_args["check_same_thread"] = False
        # A debate writes from five concurrent agent tasks. Without a generous
        # busy timeout SQLite raises "database is locked" instead of waiting.
        connect_args["timeout"] = 30

    _engine = create_async_engine(database_url, future=True, connect_args=connect_args)

    if is_sqlite:

        @event.listens_for(_engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            try:
                # WAL lets readers work while a write is in flight — needed
                # because the web backend and the desktop app can have the
                # same file open at the same time.
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()

    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    return _sessionmaker


async def create_all() -> None:
    assert _engine is not None, "init_engine() must be called first"
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        raise RuntimeError("Database not initialised; call init_engine() first")
    return _sessionmaker
