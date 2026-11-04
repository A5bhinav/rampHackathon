"""migrate.py on a long-lived database: every file once, nothing twice, never an edited file.

    uv run --with pytest --with "psycopg[binary]" pytest scripts/db/test_migrate.py
"""
from __future__ import annotations

import contextlib
import os
import re
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import migrate as m
import pgtemp


@contextlib.contextmanager
def empty_database() -> Iterator[str]:
    """A fresh, unmigrated database with the local auth.uid() stand-in (as Supabase has)."""
    admin = os.environ.get("DATABASE_URL")
    with contextlib.ExitStack() as stack:
        if not admin:
            admin = stack.enter_context(pgtemp.temp_postgres(port=54341))
        url = pgtemp.create_database(admin)
        stack.callback(pgtemp.drop_database, admin, url)
        pgtemp._psql(url, "-f", str(pgtemp.AUTH_STUB))
        yield url


def applied(url: str) -> list[str]:
    with psycopg.connect(url) as conn:
        return [r[0] for r in conn.execute("select file from dialback_meta.migrations order by applied_at, file")]


def test_every_file_is_applied_once_in_owner_order(capsys):
    with empty_database() as url:
        assert m.migrate(url) == 0
        first = applied(url)
        assert first == [name for name, _ in m.files()] and first[0].startswith("_bootstrap/")
        assert m.migrate(url) == 0, "a second run is a no-op"
        assert applied(url) == first
        assert "0 applied now" in capsys.readouterr().out
        with psycopg.connect(url) as conn:
            assert conn.execute("select count(*) from app.datasets").fetchone()[0] == 0, "the schema is really there"


def test_a_new_file_is_applied_and_an_edited_one_is_refused(tmp_path, monkeypatch):
    shutil.copytree(m.MIGRATIONS, tmp_path / "migrations")
    monkeypatch.setattr(m, "MIGRATIONS", tmp_path / "migrations")
    with empty_database() as url:
        assert m.migrate(url) == 0
        new = tmp_path / "migrations" / "app" / "9999_test_only.sql"
        new.write_text("create table app.test_only (id int);\n")
        assert m.migrate(url, status_only=True) == 0 and "app/9999_test_only.sql" not in applied(url)
        assert m.migrate(url) == 0 and applied(url)[-1] == "app/9999_test_only.sql"

        new.write_text("create table app.test_only (id int, sneaky text);\n")
        assert m.migrate(url) == 1, "an applied file that changed is refused"


def test_a_failing_file_leaves_no_record(tmp_path, monkeypatch):
    shutil.copytree(m.MIGRATIONS, tmp_path / "migrations")
    monkeypatch.setattr(m, "MIGRATIONS", tmp_path / "migrations")
    (tmp_path / "migrations" / "app" / "9999_broken.sql").write_text("create table app.half (id int);\nselect 1/0;\n")
    with empty_database() as url:
        with pytest.raises(psycopg.errors.DivisionByZero):
            m.migrate(url)
        assert "app/9999_broken.sql" not in applied(url)
        with psycopg.connect(url) as conn:
            assert conn.execute("select to_regclass('app.half')").fetchone()[0] is None, "rolled back with its record"


def test_the_order_matches_apply_sh():
    order = re.search(r"ORDER=\(([^)]*)\)", (Path(__file__).parent / "apply.sh").read_text()).group(1).split()
    assert tuple(order) == m.ORDER
