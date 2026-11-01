"""Database access. Every unit of work is one transaction run as the service role, so the
mock_ramp grants are what the mock actually relies on."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


async def open_pool(url: str, max_size: int = 10) -> AsyncConnectionPool:
    pool = AsyncConnectionPool(url, min_size=1, max_size=max_size, kwargs={"row_factory": dict_row}, open=False)
    await pool.open(wait=True)
    return pool


@asynccontextmanager
async def transaction(pool: AsyncConnectionPool) -> AsyncIterator[AsyncConnection]:
    async with pool.connection() as conn, conn.transaction():
        await conn.execute("set local role service_role")
        yield conn
