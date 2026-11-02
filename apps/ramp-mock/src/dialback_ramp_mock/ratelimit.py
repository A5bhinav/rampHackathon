"""Ramp's limit: 200 requests per 10 seconds per IP (configurable per mock business).

A sliding-window log per (business, client IP). In memory, which is fine for one mock
process; the core's own token bucket is what keeps Dialback under the real limit.
"""
from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from uuid import UUID

WINDOW_SECONDS = 10.0


class SlidingWindowLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._hits: dict[tuple[UUID, str], deque[float]] = {}

    def allow(self, business_id: UUID, client: str, limit: int) -> tuple[bool, float]:
        """Record a request if allowed. Returns (allowed, seconds until a slot frees)."""
        now = self._clock()
        hits = self._hits.setdefault((business_id, client), deque())
        while hits and now - hits[0] >= WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= limit:
            return False, max(0.0, WINDOW_SECONDS - (now - hits[0]))
        hits.append(now)
        return True, 0.0
