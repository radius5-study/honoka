"""A sliding-window counter of attempts per key, kept in memory.

It is per process: with several workers each one counts for itself, so the real limit is the
configured one times the number of workers. A shared store (Redis, the database) is the next step
once that matters."""

import math
import time
from collections import deque
from collections.abc import Callable


class Throttle:
    def __init__(
        self,
        limit: int,
        window: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = 10_000,
    ) -> None:
        self._limit = limit
        self._window = window
        self._clock = clock
        self._max_keys = max_keys
        self._attempts: dict[str, deque[float]] = {}

    def _recent(self, key: str) -> deque[float]:
        attempts = self._attempts.get(key)
        if attempts is None:
            return deque()
        horizon = self._clock() - self._window
        while attempts and attempts[0] <= horizon:
            attempts.popleft()
        if not attempts:
            del self._attempts[key]
        return attempts

    def retry_after(self, key: str) -> int | None:
        """Whole seconds until `key` may try again, or `None` when it may now."""
        attempts = self._recent(key)
        if len(attempts) < self._limit:
            return None
        return max(1, math.ceil(attempts[0] + self._window - self._clock()))

    def hit(self, key: str) -> None:
        """Count one attempt by `key`."""
        attempts = self._recent(key)
        if not attempts and len(self._attempts) >= self._max_keys:
            # Bounded memory: the oldest-seen key is forgotten first. A flood of new keys can
            # therefore reset someone else's count; the limit slows guessing, it is not a lock.
            del self._attempts[next(iter(self._attempts))]
        self._attempts.setdefault(key, attempts).append(self._clock())

    def reset(self, key: str) -> None:
        self._attempts.pop(key, None)

    def tracked(self) -> int:
        """How many keys currently hold attempts."""
        return len(self._attempts)
