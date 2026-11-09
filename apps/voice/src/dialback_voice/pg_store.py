"""The Postgres CallStore (spec 0029, PR2 of Sami's plan): the POST /calls side of the database seam.
`lookup_call`, the Twilio-route side, is `db.py`.

Every query runs as `service_role`, as the core's worker does, which reads the `app` and `ledger`
schemas and writes `voice.calls` (the grants in their migrations). Each pre-dial fact comes from the
database, never from the request, so nothing the core sends can change who gets called (R2). A fact
that can't be found reads as the refusing value, so a gap fails closed (R7).
"""
from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

from .db import CONNECT_TIMEOUT
from .guards import PreDialInputs
from .store import PlacedCall, PreDial

# The normal calling window for a non-demo dataset (design, "How a call runs"). A demo dataset is
# always inside it here: the core already applies the demo window before asking (spec 0011, D8).
WEEKDAYS = range(5)  # Monday to Friday
OPENS, CLOSES = dt.time(9, 0), dt.time(16, 30)

CASE = """
select c.state::text as state, c.version, d.kind::text as kind,
       exists (select 1 from app.holds h
                where h.case_id = c.id and h.status = 'confirmed' and h.released_at is null) as hold_on,
       (select count(*) from voice.calls vc
         where vc.case_id = c.id
           and vc.idempotency_key like c.id::text || ':call:v' || c.version || ':%%') as attempts_used
  from app.cases c join app.datasets d on d.id = c.dataset_id
 where c.id = %s
"""

# The newest selection for the case; only a `picked` one names a number. Contact points are per
# dataset, so the 30-day count starts fresh with each demo reset (spec 0029, Q56).
PICK = """
select s.outcome::text as outcome, cp.id as contact_point_id, cp.e164, cp.status::text as status,
       (select count(*) from voice.calls vc
         where vc.contact_point_id = cp.id and vc.created_at > now() - interval '30 days') as calls_30d
  from ledger.number_selections s
  left join ledger.contact_points cp on cp.id = s.contact_point_id
 where s.case_id = %s
 order by s.recorded_at desc, s.id desc
 limit 1
"""



def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class PgCallStore:
    """`CallStore` over Postgres. One short connection per operation: a call is placed a few times a
    day, so a pool would only add a lifecycle to manage."""

    def __init__(self, conninfo: str, *, dial_allowlist: frozenset[str], calling_tz: str = "America/Los_Angeles",
                 now: Callable[[], dt.datetime] = _utcnow) -> None:
        self._conninfo = conninfo
        self._allowlist = dial_allowlist
        self._tz = ZoneInfo(calling_tz)
        self._now = now

    @asynccontextmanager
    async def _tx(self) -> AsyncIterator[psycopg.AsyncConnection]:
        async with await psycopg.AsyncConnection.connect(self._conninfo, row_factory=dict_row,
                                                         connect_timeout=CONNECT_TIMEOUT) as conn, \
                conn.transaction():
            await conn.execute("set local role service_role")
            yield conn

    async def find_call(self, idempotency_key: str) -> PlacedCall | None:
        async with self._tx() as conn:
            row = await (await conn.execute(
                "select id, case_id, attempt from voice.calls where idempotency_key = %s", (idempotency_key,))).fetchone()
        return PlacedCall(call_id=row["id"], case_id=row["case_id"], attempt=row["attempt"]) if row else None

    async def gather_pre_dial(self, *, case_id: UUID, attempt: int) -> PreDial:
        async with self._tx() as conn:
            case = await (await conn.execute(CASE, (case_id,))).fetchone()
            pick = await (await conn.execute(PICK, (case_id,))).fetchone() if case else None
        if case is None:  # unknown case: refused before anything else is looked at
            return PreDial(inputs=PreDialInputs(case_exists=False, case_finished=False, case_held=False,
                                                has_number_pick=False, number_opted_out=False,
                                                within_calling_window=False, attempts_used=0, number_calls_last_30d=0),
                           contact_point_id=None, number_e164=None)
        picked = pick is not None and pick["outcome"] == "picked" and pick["contact_point_id"] is not None
        demo = case["kind"] == "demo"
        e164 = pick["e164"] if picked else None
        inputs = PreDialInputs(
            case_exists=True,
            case_finished=case["state"] == "released",
            case_held=case["state"] == "calling" and case["hold_on"],
            has_number_pick=picked,
            number_opted_out=picked and pick["status"] != "eligible",   # excluded counts as opted out (Q57)
            within_calling_window=demo or self._in_window(),
            attempts_used=int(case["attempts_used"]),
            number_calls_last_30d=int(pick["calls_30d"]) if picked else 0,
            demo_dataset=demo,
            number_on_allowlist=e164 in self._allowlist if e164 else False,
        )
        return PreDial(inputs=inputs, contact_point_id=pick["contact_point_id"] if picked else None, number_e164=e164)

    def _in_window(self) -> bool:
        local = self._now().astimezone(self._tz)
        return local.weekday() in WEEKDAYS and OPENS <= local.time() < CLOSES

    async def record_call(self, *, case_id: UUID, attempt: int, idempotency_key: str,
                          contact_point_id: UUID | None, number_e164: str | None,
                          agent_version: str) -> tuple[PlacedCall, bool]:
        async with self._tx() as conn:
            row = await (await conn.execute(
                "insert into voice.calls (dataset_id, case_id, attempt, idempotency_key, contact_point_id, agent_version) "
                "select c.dataset_id, c.id, %s, %s, %s, %s from app.cases c where c.id = %s "
                "on conflict (idempotency_key) do nothing returning id, case_id, attempt",
                (attempt, idempotency_key, contact_point_id, agent_version, case_id))).fetchone()
            created = row is not None
            if not created:  # a racing request with the same key got there first: return its call
                row = await (await conn.execute(
                    "select id, case_id, attempt from voice.calls where idempotency_key = %s", (idempotency_key,))).fetchone()
        if row is None:
            raise LookupError(f"no case {case_id} to record a call for")
        return PlacedCall(call_id=row["id"], case_id=row["case_id"], attempt=row["attempt"]), created

    async def mark_dialed(self, call_id: UUID, *, provider_call_id: str) -> None:
        async with self._tx() as conn:
            await conn.execute(
                "update voice.calls set provider = 'twilio', provider_call_id = %s, status = 'ringing', "
                "started_at = coalesce(started_at, now()) where id = %s", (provider_call_id, call_id))

    async def mark_failed(self, call_id: UUID, reason: str) -> None:
        async with self._tx() as conn:
            await conn.execute(
                "update voice.calls set status = 'ended', result = 'failed', reason = %s, ended_at = now() "
                "where id = %s", (reason[:200], call_id))
