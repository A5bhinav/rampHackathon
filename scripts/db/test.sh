#!/usr/bin/env bash
# The "db" CI job: apply every migration to a fresh Postgres, run the SQL tests, redraw the
# state-machine doc, and check the shared hash vectors.
#
#   DATABASE_URL set    use that database (CI service container, a dev Supabase branch)
#   DATABASE_URL unset  start a throwaway Postgres 16 in a temp dir and remove it afterwards
#
# The local run installs supabase/tests/_local_auth_stub.sql (a stand-in for auth.uid())
# only when auth.uid() is missing, so it is safe against a real Supabase database too.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"

if [[ -z "${DATABASE_URL:-}" ]]; then
  PG_BIN=${PG_BIN:-$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1 || true)}
  if [[ -z "$PG_BIN" || ! -x "$PG_BIN/initdb" ]]; then
    echo "no local Postgres found: set DATABASE_URL or PG_BIN" >&2
    exit 2
  fi
  TMP=$(mktemp -d)
  PORT=${PGPORT_TEST:-54329}
  RUN_AS=()
  if [[ $EUID -eq 0 ]]; then
    chown postgres "$TMP"
    RUN_AS=(runuser -u postgres --)
  fi
  "${RUN_AS[@]}" "$PG_BIN/initdb" -D "$TMP/data" -A trust -U postgres >"$TMP/initdb.log" 2>&1
  "${RUN_AS[@]}" "$PG_BIN/pg_ctl" -D "$TMP/data" -o "-p $PORT -k $TMP -c listen_addresses=''" -l "$TMP/pg.log" -w start >/dev/null
  trap '"${RUN_AS[@]}" "$PG_BIN/pg_ctl" -D "$TMP/data" -m fast stop >/dev/null 2>&1 || true; rm -rf "$TMP"' EXIT
  psql -X -q -v ON_ERROR_STOP=1 "postgresql://postgres@/postgres?host=$TMP&port=$PORT" -c "create database dialback_test"
  export DATABASE_URL="postgresql://postgres@/dialback_test?host=$TMP&port=$PORT"
  echo "local  Postgres $("$PG_BIN/postgres" --version | awk '{print $3}') at $TMP"
fi

psql -X -q -v ON_ERROR_STOP=1 "$DATABASE_URL" -f supabase/tests/_local_auth_stub.sql
scripts/db/apply.sh

VECTORS=$(cat packages/contracts/vectors/evidence-chain.json)
for t in supabase/tests/0*.sql; do
  echo "test   $(basename "$t")"
  psql -X -q -v ON_ERROR_STOP=1 -v vectors="$VECTORS" "$DATABASE_URL" -f "$t"
done

echo "render docs/state-machine.md"
python3 scripts/db/render_state_machine.py docs/state-machine.md

if command -v uv >/dev/null 2>&1; then
  echo "check  contracts: schema, examples, hash vectors (python == sql)"
  uv run --quiet --with jsonschema --with rfc8785 --with pyyaml --with openapi-spec-validator python packages/contracts/scripts/validate.py
  uv run --quiet --with rfc8785 python packages/contracts/scripts/make_vectors.py --check
else
  echo "skip   contract checks (uv not installed)"
fi
echo "ok"
