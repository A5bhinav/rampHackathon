"""Database access for the core.

Two ways in, matching ADR-1 and ADR-9:

- `system_tx`: work the system does (webhooks, detection, the worker). Runs as the
  service role with no JWT claims, so `auth.uid()` is null and the grants in the
  migrations are exercised, not bypassed by the connecting superuser.
- `person_tx` (used by the dashboard API): runs as `authenticated` with the person's JWT
  claims, so `auth.uid()` is that person and row-level security applies.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


async def open_pool(url: str, max_size: int = 10) -> AsyncConnectionPool:
    pool = AsyncConnectionPool(url, min_size=1, max_size=max_size, kwargs={"row_factory": dict_row}, open=False)
    await pool.open(wait=True)
    return pool


@asynccontextmanager
async def system_tx(pool: AsyncConnectionPool) -> AsyncIterator[AsyncConnection]:
    async with pool.connection() as conn, conn.transaction():
        await conn.execute("set local role service_role")
        yield conn


@asynccontextmanager
async def person_tx(pool: AsyncConnectionPool, user_id: UUID, claims: dict) -> AsyncIterator[AsyncConnection]:
    """Act as the signed-in person, exactly as Supabase's own API would (spec 0002, D9)."""
    async with pool.connection() as conn, conn.transaction():
        await conn.execute("set local role authenticated")
        await conn.execute("select set_config('request.jwt.claims', %s, true)",
                           (json.dumps({**claims, "sub": str(user_id), "role": "authenticated"}),))
        yield conn


async def dataset_now(conn: AsyncConnection, dataset_id: UUID):
    """The dataset's clock (spec 0002, D2): simulated for a lab dataset, real otherwise."""
    return (await (await conn.execute("select app.now(%s) as now", (dataset_id,))).fetchone())["now"]
