"""Chaos mode (spec 0008): Ramp's API, made unreliable on purpose and repeatably.

While a business's `chaos_seed` is set, each request to Ramp's API (never the mock's own
`/_mock` routes) draws from a stream seeded by the seed and the request's number since the
settings last changed. No database id goes in, so the same seed and the same requests give the
same faults in every run, and patching the settings restarts the sequence:

- it may wait up to `chaos_max_latency_ms` first;
- with probability `chaos_error_permille`/1000 it answers 500, 502 or 503, and half the time it
  does so *after* the work was done, as when Ramp's answer is lost on the way back;
- a webhook delivery is dropped with probability `chaos_drop_permille`/1000.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

SETTINGS = """
select b.id as business_id, s.chaos_seed, s.chaos_error_permille, s.chaos_max_latency_ms, s.updated_at
  from mock_ramp.businesses b join mock_ramp.settings s on s.business_id = b.id
 where b.dataset_id = %s and s.chaos_seed is not null
"""


@dataclass(frozen=True)
class Fault:
    latency_ms: int = 0
    status: int | None = None          # 500, 502 or 503 when this request fails
    after_work: bool = False           # fail after the handler ran (a lost response)


class Chaos:
    """One counter per business and settings version, so a run repeats for the same seed."""

    def __init__(self) -> None:
        self._counters: dict[tuple, int] = {}
        self.injected = 0  # faults answered so far, so a test can prove chaos did something

    def next_fault(self, row: dict) -> Fault:
        key = (row["business_id"], row["chaos_seed"], row["updated_at"])
        n = self._counters.get(key, 0)
        self._counters[key] = n + 1
        rng = random.Random(f"{row['chaos_seed']}:{n}")  # changing settings restarts at n = 0
        latency = rng.randint(0, row["chaos_max_latency_ms"]) if row["chaos_max_latency_ms"] else 0
        if rng.randrange(1000) >= row["chaos_error_permille"]:
            return Fault(latency_ms=latency)
        self.injected += 1
        return Fault(latency_ms=latency, status=rng.choice((500, 502, 503)), after_work=rng.random() < 0.5)


def drops_delivery(seed: int | None, permille: int, ordinal: int, attempt: int) -> bool:
    """`ordinal` is the delivery's number within its subscription, not its row id, so a run repeats."""
    if seed is None or permille <= 0:
        return False
    return random.Random(f"{seed}:delivery:{ordinal}:{attempt}").randrange(1000) < permille

