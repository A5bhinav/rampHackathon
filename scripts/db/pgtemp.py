"""A migrated Postgres for the Python tests of the money path (apps/core, apps/ramp-mock).

Same rules as scripts/db/test.sh, so Python and SQL tests run against the same database:

- DATABASE_URL set: create a fresh database on that server (CI's service container, a dev
  branch), migrate it, drop it afterwards.
- DATABASE_URL unset: start a throwaway Postgres in a temp directory (as the postgres OS user
  when running as root), migrate it, remove it afterwards.

Tests isolate themselves by creating their own dataset (and evidence chain, and mock business),
exactly as the attack lab does, so nothing is ever truncated or deleted between tests.

Use it from a package's tests by loading it as a pytest plugin:

    [tool.pytest.ini_options]
    pythonpath = ["../../scripts/db"]
    addopts = "-p pgtemp"

Then any test or fixture can take `database_url`.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

ROOT = Path(__file__).resolve().parents[2]
APPLY = ROOT / "scripts" / "db" / "apply.sh"
AUTH_STUB = ROOT / "supabase" / "tests" / "_local_auth_stub.sql"


def _psql(url: str, *args: str) -> None:
    subprocess.run(["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", url, *args], check=True, capture_output=True, text=True)


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/" + name, parts.query, parts.fragment))


def _pg_bin() -> Path:
    explicit = os.environ.get("PG_BIN")
    if explicit:
        return Path(explicit)
    candidates = sorted(Path("/usr/lib/postgresql").glob("*/bin"), key=lambda p: int(p.parent.name))
    if not candidates:
        raise RuntimeError("no local Postgres found: set DATABASE_URL or PG_BIN")
    return candidates[-1]


@contextlib.contextmanager
def temp_postgres(port: int = 54340) -> Iterator[str]:
    """Start a throwaway Postgres and yield a URL to its `postgres` database."""
    pg_bin = _pg_bin()
    tmp = Path(tempfile.mkdtemp(prefix="dialback-pg-"))
    run_as: list[str] = []
    if os.geteuid() == 0:
        shutil.chown(tmp, "postgres")
        run_as = ["runuser", "-u", "postgres", "--"]
    data = tmp / "data"
    subprocess.run([*run_as, str(pg_bin / "initdb"), "-D", str(data), "-A", "trust", "-U", "postgres"],
                   check=True, capture_output=True)
    subprocess.run([*run_as, str(pg_bin / "pg_ctl"), "-D", str(data), "-l", str(tmp / "pg.log"), "-w",
                    "-o", f"-p {port} -k {tmp} -c listen_addresses=''", "start"], check=True, capture_output=True)
    try:
        yield f"postgresql://postgres@/postgres?host={tmp}&port={port}"
    finally:
        subprocess.run([*run_as, str(pg_bin / "pg_ctl"), "-D", str(data), "-m", "fast", "stop"], check=False, capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


def create_database(admin_url: str) -> str:
    name = f"dialback_test_{uuid.uuid4().hex[:12]}"
    _psql(admin_url, "-c", f'create database "{name}"')
    return _with_database(admin_url, name)


def drop_database(admin_url: str, url: str) -> None:
    name = urlsplit(url).path.lstrip("/")
    subprocess.run(["psql", "-X", "-q", admin_url, "-c", f'drop database if exists "{name}" with (force)'],
                   check=False, capture_output=True)


def migrate(url: str) -> None:
    """Install the local auth.uid() stand-in (only if missing) and apply every migration."""
    _psql(url, "-f", str(AUTH_STUB))
    subprocess.run([str(APPLY)], check=True, capture_output=True, text=True, env={**os.environ, "DATABASE_URL": url})


@contextlib.contextmanager
def migrated_database() -> Iterator[str]:
    admin = os.environ.get("DATABASE_URL")
    if admin:
        url = create_database(admin)
        try:
            migrate(url)
            yield url
        finally:
            drop_database(admin, url)
        return
    with temp_postgres() as admin_url:
        url = create_database(admin_url)
        migrate(url)
        yield url


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """A URL to a freshly migrated database, shared by every test in the session."""
    with migrated_database() as url:
        yield url
