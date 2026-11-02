"""The webhook sender: delivers due rows of mock_ramp.webhook_deliveries.

Each delivery's raw body is signed with its subscription's secret, as Ramp does:
`X-Ramp-Signature: hex(HMAC-SHA256(secret, raw_body))` (encoding to confirm, spec 0002 Q3).
Duplicates and reordering are scheduled by mock_ramp.queue_webhook(); this sender only
delivers in scheduled order and retries failures with backoff.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from uuid import UUID

import httpx
from psycopg_pool import AsyncConnectionPool

from .chaos import drops_delivery
from .db import transaction

CLAIM = """
select d.id, d.event_id, d.event_type, d.payload, d.attempt, s.url, s.secret,
       st.chaos_seed, coalesce(st.chaos_drop_permille, 0) as chaos_drop_permille
  from mock_ramp.webhook_deliveries d
  join mock_ramp.webhook_subscriptions s on s.id = d.subscription_id
  join mock_ramp.businesses b on b.id = s.business_id
  left join mock_ramp.settings st on st.business_id = b.id
 where d.status = 'scheduled' and (%(everything)s or d.scheduled_at <= now())
   and (%(dataset)s::uuid is null or b.dataset_id = %(dataset)s::uuid)
 order by d.scheduled_at, d.id
 for update of d skip locked
 limit 1
"""


def raw_body(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()


def signature(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def backoff_seconds(attempt: int) -> int:
    return min(60, 2 ** attempt)


async def deliver_due(pool: AsyncConnectionPool, http: httpx.AsyncClient, max_attempts: int = 5,
                      dataset_id: UUID | None = None, limit: int = 50, everything: bool = False) -> int:
    """Attempt every due delivery (optionally for one dataset). Returns how many were attempted.

    `everything` also sends deliveries scheduled in the future (a delayed duplicate, a retry),
    still in scheduled order, so tests and the demo don't wait. One delivery per transaction;
    the row stays locked while it is sent, so two senders never send the same delivery twice.
    A failure pushes the row into the future, so it is attempted at most once per call.
    """
    attempted_ids: set = set()
    attempted = 0
    for _ in range(limit):
        async with transaction(pool) as conn:
            row = await (await conn.execute(CLAIM, {"dataset": dataset_id, "everything": everything})).fetchone()
            if row is None or row["id"] in attempted_ids:
                break
            attempted_ids.add(row["id"])
            if drops_delivery(row["chaos_seed"], row["chaos_drop_permille"], row["id"], row["attempt"]):
                # Chaos mode (spec 0008): this delivery is lost and never retried.
                await conn.execute("update mock_ramp.webhook_deliveries set status = 'dropped', last_error = 'chaos' "
                                   "where id = %s", (row["id"],))
                attempted += 1
                continue
            body = raw_body(row["payload"])
            headers = {
                "Content-Type": "application/json",
                "X-Ramp-Signature": signature(row["secret"], body),
                "X-Ramp-Event-Id": str(row["event_id"]),
            }
            status_code, failure = None, None
            try:
                response = await http.post(row["url"], content=body, headers=headers)
                status_code = response.status_code
                if not 200 <= status_code < 300:
                    failure = f"HTTP {status_code}"
            except httpx.HTTPError as exc:
                failure = f"{type(exc).__name__}: {exc}"
            if failure is None:
                await conn.execute(
                    "update mock_ramp.webhook_deliveries set status = 'delivered', delivered_at = now(), "
                    "response_status = %s, last_error = null where id = %s", (status_code, row["id"]))
            else:
                exhausted = row["attempt"] >= max_attempts
                await conn.execute(
                    "update mock_ramp.webhook_deliveries set attempt = attempt + 1, response_status = %s, last_error = %s, "
                    "status = case when %s then 'failed' else 'scheduled' end, "
                    "scheduled_at = now() + make_interval(secs => %s) where id = %s",
                    (status_code, failure, exhausted, backoff_seconds(row["attempt"]), row["id"]))
            attempted += 1
    return attempted
