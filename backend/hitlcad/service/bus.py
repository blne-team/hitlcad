"""In-process fan-out of design events to live subscribers (the WebSocket streams of panels and the extension)."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from hitlcad.core.events import Event

MAX_QUEUE = 1000  # events a subscriber may fall behind before it is dropped
DROPPED = None  # the last item of a dropped subscriber's queue: it resyncs from the log


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[Event | None]]] = defaultdict(set)
        self._all: set[asyncio.Queue[tuple[str, Event] | None]] = set()

    def subscribe(self, design_id: str) -> asyncio.Queue[Event | None]:
        q: asyncio.Queue[Event | None] = asyncio.Queue(MAX_QUEUE)
        self._subs[design_id].add(q)
        return q

    def unsubscribe(self, design_id: str, q: asyncio.Queue[Event | None]) -> None:
        self._subs[design_id].discard(q)

    def subscribe_all(self) -> asyncio.Queue[tuple[str, Event] | None]:
        """Events of every design, as (design id, event)."""
        q: asyncio.Queue[tuple[str, Event] | None] = asyncio.Queue(MAX_QUEUE)
        self._all.add(q)
        return q

    def unsubscribe_all(self, q: asyncio.Queue[tuple[str, Event] | None]) -> None:
        self._all.discard(q)

    def publish(self, design_id: str, event: Event) -> None:
        for q in list(self._subs.get(design_id, ())):
            if not _put(q, event):
                self._subs[design_id].discard(q)
        for qa in list(self._all):
            if not _put(qa, (design_id, event)):
                self._all.discard(qa)


def _put(q: asyncio.Queue[Any], item: Any) -> bool:
    """Queue ``item``; a subscriber that fell too far behind gets DROPPED instead, in place of what it missed."""
    try:
        q.put_nowait(item)
        return True
    except asyncio.QueueFull:
        while not q.empty():
            q.get_nowait()
        q.put_nowait(DROPPED)
        return False
