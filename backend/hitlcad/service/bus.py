"""In-process fan-out of design events to live subscribers (the WebSocket streams of panels and the extension)."""

from __future__ import annotations

import asyncio
from collections import defaultdict

from hitlcad.core.events import Event

MAX_QUEUE = 1000  # events a subscriber may fall behind before it is dropped


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[Event]]] = defaultdict(set)
        self._all: set[asyncio.Queue[tuple[str, Event]]] = set()

    def subscribe(self, design_id: str) -> asyncio.Queue[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue(MAX_QUEUE)
        self._subs[design_id].add(q)
        return q

    def unsubscribe(self, design_id: str, q: asyncio.Queue[Event]) -> None:
        self._subs[design_id].discard(q)

    def subscribe_all(self) -> asyncio.Queue[tuple[str, Event]]:
        """Events of every design, as (design id, event)."""
        q: asyncio.Queue[tuple[str, Event]] = asyncio.Queue(MAX_QUEUE)
        self._all.add(q)
        return q

    def unsubscribe_all(self, q: asyncio.Queue[tuple[str, Event]]) -> None:
        self._all.discard(q)

    def publish(self, design_id: str, event: Event) -> None:
        for q in list(self._subs.get(design_id, ())):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:  # slow consumer: drop it, it will resync from the log
                self._subs[design_id].discard(q)
        for qa in list(self._all):
            try:
                qa.put_nowait((design_id, event))
            except asyncio.QueueFull:
                self._all.discard(qa)
