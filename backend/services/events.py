"""In-process event bus for live debate updates (SSE).

Every event is (a) persisted to the DB so a late/reconnecting client can
replay the whole run, and (b) fanned out to any currently-subscribed queues.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from ..models.enums import EventType
from .logging import get_logger

log = get_logger(__name__)

_MAX_QUEUE = 1000


@dataclass
class Event:
    seq: int
    type: str
    debate_id: str
    data: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "type": self.type,
            "debate_id": self.debate_id,
            "data": self.data,
            "created_at": self.created_at,
        }


class DebateChannel:
    """Fan-out channel for one debate."""

    def __init__(self, debate_id: str) -> None:
        self.debate_id = debate_id
        self._subscribers: set[asyncio.Queue[Event | None]] = set()
        self._seq = 0
        self.closed = False

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def subscribe(self) -> asyncio.Queue[Event | None]:
        q: asyncio.Queue[Event | None] = asyncio.Queue(maxsize=_MAX_QUEUE)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[Event | None]) -> None:
        self._subscribers.discard(q)

    def publish(self, event: Event) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:  # pragma: no cover - slow consumer
                log.warning("Dropping event for slow subscriber on %s", self.debate_id)

    def close(self) -> None:
        self.closed = True
        for q in list(self._subscribers):
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:  # pragma: no cover
                pass

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


class EventBus:
    """Registry of per-debate channels."""

    def __init__(self) -> None:
        self._channels: dict[str, DebateChannel] = {}

    def channel(self, debate_id: str) -> DebateChannel:
        ch = self._channels.get(debate_id)
        if ch is None:
            ch = DebateChannel(debate_id)
            self._channels[debate_id] = ch
        return ch

    def get(self, debate_id: str) -> DebateChannel | None:
        return self._channels.get(debate_id)

    def emit(
        self, debate_id: str, event_type: EventType | str, data: dict[str, Any] | None = None
    ) -> Event:
        ch = self.channel(debate_id)
        etype = event_type.value if isinstance(event_type, EventType) else str(event_type)
        event = Event(
            seq=ch.next_seq(),
            type=etype,
            debate_id=debate_id,
            data=data or {},
            created_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        )
        ch.publish(event)
        return event

    def close(self, debate_id: str) -> None:
        ch = self._channels.get(debate_id)
        if ch:
            ch.close()

    def drop(self, debate_id: str) -> None:
        ch = self._channels.pop(debate_id, None)
        if ch:
            ch.close()

    async def stream(
        self, debate_id: str, replay: list[Event] | None = None
    ) -> AsyncIterator[Event]:
        """Replay history, then follow live events until the channel closes."""
        ch = self.channel(debate_id)
        q = ch.subscribe()
        seen = 0
        try:
            for ev in replay or []:
                seen = max(seen, ev.seq)
                yield ev
            if ch.closed:
                return
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=20.0)
                except asyncio.TimeoutError:
                    # heartbeat marker; the route turns this into an SSE comment
                    yield Event(seq=-1, type="ping", debate_id=debate_id)
                    continue
                if ev is None:
                    return
                if ev.seq <= seen:
                    continue
                seen = ev.seq
                yield ev
        finally:
            ch.unsubscribe(q)


bus = EventBus()
