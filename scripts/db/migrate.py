"""Apply migrations to a long-lived database ($DATABASE_URL), each file exactly once.

`apply.sh` builds a fresh database from scratch (tests, CI). A hosted database (Supabase) keeps
its data, so this records each applied file and its SHA-256 in `dialback_meta.migrations`,
applies only new files, and refuses to run if an applied file has changed since: a migration
is never edited after it ships, only followed by a new one. Railway runs it before every core
deploy; an advisory lock keeps two deploys from migrating at once.

    DATABASE_URL=... python scripts/db/migrate.py            apply what's new
    DATABASE_URL=... python scripts/db/migrate.py --status   list applied and pending files
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "supabase" / "migrations"
ORDER = ("_bootstrap", "audit", "app", "ledger", "voice", "lab", "mock_ramp")  # same order as apply.sh
LOCK = 7_302_018  # pg_advisory_lock key: "dialback migrations"

SETUP = """
create schema if not exists dialback_meta;
create table if not exists dialback_meta.migrations (
  file       text primary key,
  sha256     text not null,
  applied_at timestamptz not null default now()
);
revoke all on schema dialback_meta from public;
"""


def files() -> list[tuple[str, Path]]:
    """Every migration, in owner order, then by name. Folders that don't exist yet are skipped."""
    found = []
    for schema in ORDER:
        folder = MIGRATIONS / schema
        if folder.is_dir():
            found += [(f"{schema}/{f.name}", f) for f in sorted(folder.glob("*.sql"))]
    return found


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def migrate(url: str, status_only: bool = False) -> int:
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("select pg_advisory_lock(%s)", (LOCK,))
        try:
            conn.execute(SETUP)
            applied = dict(conn.execute("select file, sha256 from dialback_meta.migrations").fetchall())
            pending = []
            for name, path in files():
                if name in applied:
                    if applied[name] != digest(path):
                        print(f"refuse {name}: changed after it was applied; add a new migration instead", file=sys.stderr)
                        return 1
                    print(f"done   {name}")
                else:
                    pending.append((name, path))
            for name, path in pending:
                print(f"{'pending' if status_only else 'apply  '} {name}")
                if status_only:
                    continue
                with conn.transaction():  # the file and its record commit together, or neither does
                    conn.execute(path.read_text(encoding="utf-8"))
                    conn.execute("insert into dialback_meta.migrations (file, sha256) values (%s, %s)", (name, digest(path)))
            print(f"{len(applied)} applied before, {0 if status_only else len(pending)} applied now")
            return 0
        finally:
            conn.execute("select pg_advisory_unlock(%s)", (LOCK,))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--status", action="store_true", help="list applied and pending files; change nothing")
    args = parser.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("set DATABASE_URL", file=sys.stderr)
        return 2
    return migrate(url, status_only=args.status)


if __name__ == "__main__":
    sys.exit(main())
