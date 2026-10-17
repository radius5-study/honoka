"""Live updates: a per-project fan-out of events to the connected SSE clients."""

import asyncio
from collections.abc import Generator
from contextlib import contextmanager

from taskboard.models import Event


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[int, set[asyncio.Queue[Event | None]]] = {}

    @contextmanager
    def subscribe(self, project_id: int) -> Generator[asyncio.Queue[Event | None]]:
        # A slow client must not hold memory forever: a full queue drops its oldest events,
        # and the client catches up with `Last-Event-ID` when it reconnects.
        queue: asyncio.Queue[Event | None] = asyncio.Queue(maxsize=100)
        self._subscribers.setdefault(project_id, set()).add(queue)
        try:
            yield queue
        finally:
            subscribers = self._subscribers[project_id]
            subscribers.discard(queue)
            if not subscribers:
                del self._subscribers[project_id]

    def publish(self, event: Event) -> None:
        self._put(event.project_id, event)

    def close(self, project_id: int) -> None:
        """End every stream watching `project_id` (it was deleted)."""
        self._put(project_id, None)

    def _put(self, project_id: int, item: Event | None) -> None:
        for queue in self._subscribers.get(project_id, ()):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(item)
