"""Shared fixtures for the voice-service tests: a call context and a deterministic clock.

CALLBACK is the string packages/policy's guard tests already treat as a valid callback number,
so the agent speaking it in the disclosure is not counted as an account-digit leak."""
from __future__ import annotations

import datetime as dt
import os
import sys
import uuid as _uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
from dialback_policy.script import Script
from dialback_policy.step import CallContext

CALLBACK = "(415) 555-0100"

# scripts/db holds migrate.py, reused to migrate the throwaway test database (spec 0027, decision 6).
_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "scripts" / "db"))


@pytest.fixture
def make_ctx():
    def _make(call_id: str = "6b1e2f0a-4c3d-4e5f-8a9b-0c1d2e3f4a5b",
              customer: str = "Northwind Traders", vendor: str = "Acme Fasteners") -> CallContext:
        return CallContext(call_id=call_id, script=Script(customer, CALLBACK), vendor_names=(vendor,))
    return _make


@pytest.fixture
def clock():
    def _clock(step: dt.timedelta = dt.timedelta(seconds=10),
               start: dt.datetime = dt.datetime(2026, 11, 1, 17, 0, tzinfo=dt.UTC)):
        box = {"t": start}
        def now() -> dt.datetime:
            cur = box["t"]
            box["t"] = cur + step
            return cur
        return now
    return _clock


def _with_db(url: str, name: str) -> str:
    return urlunsplit(urlsplit(url)._replace(path=f"/{name}"))


@pytest.fixture(scope="session")
def database_url():
    """A fresh, migrated Postgres for the DB-backed voice tests (spec 0027, decision 6). Creates a
    throwaway database on the DATABASE_URL server (CI's service container or a local Docker Postgres),
    applies the local auth stub and every migration with psycopg (no psql), and drops it after. Skips
    when DATABASE_URL is unset."""
    base = os.environ.get("DATABASE_URL")
    if not base:
        pytest.skip("DATABASE_URL unset; the voice DB tests need a throwaway Postgres (spec 0027)")
    import migrate  # scripts/db/migrate.py, via sys.path above
    import psycopg

    name = f"voice_t_{_uuid.uuid4().hex[:12]}"
    with psycopg.connect(base, autocommit=True) as c:
        c.execute(f'create database "{name}"')
    url = _with_db(base, name)
    try:
        with psycopg.connect(url, autocommit=True) as c:
            c.execute((_REPO / "supabase" / "tests" / "_local_auth_stub.sql").read_text())
        migrate.migrate(url)
        yield url
    finally:
        with psycopg.connect(base, autocommit=True) as c:
            c.execute(f'drop database if exists "{name}" with (force)')
