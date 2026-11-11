#!/usr/bin/env python3
"""Rehearse the demo's money-path beats end to end (spec 0011) against the local stack: the Ramp
mock as its own process, the core over HTTP with its worker, the scripted call doubles from
scripts/dev/stack.py, and the `demo` commands exactly as the CLI runs them.

    uv run python scripts/dev/demo_rehearsal.py

What it proves, in order:
  1. A reset gives a fresh demo company, linked to the clerk's account; a second reset hides the
     first and deletes nothing.
  2. Beat 2: the clerk's bank change opens a case and the hold goes on.
  3. Beat 5: the scripted call confirms, code decides Verified, the clerk is refused (R5) and a
     second person releases.
  4. Beat 6: after a denied change, tamper flips that decision to verified by hand, and the verify
     job's critical alert names the row.
  5. Status reports it all.

Exits non-zero if any check fails. A throwaway Postgres, so the stand-in auth.users and the
team-style accounts below never touch a real project. Everything is fictional.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
import uuid
from pathlib import Path

import httpx
import psycopg
import uvicorn

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(ROOT / "scripts" / "db")]

from dialback_core import demo
from dialback_core.app import create_app
from dialback_core.db import open_pool, system_tx
from dialback_core.ramp import RampConnector
from pgtemp import migrated_database
from smoke import Smoke
from stack import (
    CORE_PORT,
    CORE_URL,
    MOCK_PORT,
    MOCK_URL,
    SECRETS,
    ScriptedChangeContext,
    ScriptedLedger,
    ScriptedVoice,
    core_settings,
    wait_for,
)

SEED = ROOT / "scripts" / "demo" / "seed.json"
ACCOUNTS = (("clerk", "Pat Clerk", "clerk"), ("approver1", "Riley Approver", "approver"), ("approver2", "Sam Approver", "approver"))


async def accounts(url: str) -> dict[str, tuple[uuid.UUID, str]]:
    """Dialback accounts as the team makes them (spec 0010, D6), in a stand-in auth.users."""
    users = {}
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as owner:
        await owner.execute("create schema if not exists auth")
        await owner.execute("create table if not exists auth.users (id uuid primary key, email text)")
        for key, name, role in ACCOUNTS:
            uid = uuid.uuid4()
            await owner.execute("insert into auth.users (id, email) values (%s, %s)", (uid, f"{key}@rehearsal.example"))
            await owner.execute("insert into app.user_profiles (user_id, display_name) values (%s, %s)", (uid, name))
            await owner.execute("insert into app.user_roles (user_id, role) values (%s, %s)", (uid, role))
            users[key] = (uid, name)
    return users


async def rehearse(url: str) -> int:
    env_vars = os.environ | {"DATABASE_URL": url, "MOCK_RAMP_SECRET": SECRETS["MOCK_RAMP_SECRET"], "PORT": str(MOCK_PORT),
                             "HOST": "127.0.0.1"}
    mock_proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "dialback_ramp_mock.main", env=env_vars)
    try:
        await wait_for(f"{MOCK_URL}/healthz")
        settings = dataclasses.replace(core_settings(url, backoff=0.2, jobs_every=2), core_public_url=CORE_URL,
                                       calling_window="09:00-09:01", calling_days="sun")  # the normal window is shut
        pool = await open_pool(url, max_size=10)
        voice = ScriptedVoice(pool, refuse={"Brightline Office Supply Inc"}, deny={"Cobalt Freight LLC"})
        app = create_app(settings, pool=pool, resolver=ScriptedLedger(), caller=voice, change_context=ScriptedChangeContext())
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=CORE_PORT, log_level="warning"))
        serving = asyncio.create_task(server.serve())
        await wait_for(f"{CORE_URL}/healthz")
        users = await accounts(url)
        seed = demo.load_seed(SEED)
        async with httpx.AsyncClient(base_url=MOCK_URL, timeout=15.0) as mock, httpx.AsyncClient(timeout=10.0) as http:
            env = demo.DemoEnv(pool=pool, settings=settings, mock=mock, connector=RampConnector(settings, http, pool=pool), owner_url=url)
            code = await beats(env, seed, users)
        server.should_exit = True
        await serving
        await pool.close()
        return code
    finally:
        mock_proc.terminate()
        await asyncio.wait_for(mock_proc.wait(), timeout=10)


async def beats(env: demo.DemoEnv, seed: dict, users: dict) -> int:
    print("\nDemo rehearsal (spec 0011)\n")
    links = {"clerk": "clerk@rehearsal.example"}
    first = await demo.reset(env, seed, links)
    r = await demo.reset(env, seed, links)
    s = Smoke(env.pool, env.settings, r.dataset.id, r.refs, users)
    async with system_tx(env.pool) as conn:
        rows = await (await conn.execute("select id, retired_at from app.datasets where id = any(%s)",
                                         ([first.dataset.id, r.dataset.id],))).fetchall()
    retired = {row["id"]: row["retired_at"] is not None for row in rows}
    s.check(retired == {first.dataset.id: True, r.dataset.id: False}, "a second reset hides the first demo and deletes nothing")
    s.check(r.linked == {"clerk": users["clerk"][0]} and not r.warnings, "the clerk's account is linked to the seeded clerk")

    acme, cobalt = "Acme Fasteners LLC", "Cobalt Freight LLC"
    await demo.change(env, seed, "acme")

    async def state(vendor: str, *want: str) -> bool:
        return (await s.cases()).get(vendor, {}).get("state") in want
    await s.until("beat 2: the clerk's change opens a case and the hold goes on",
                  lambda: _held(s, acme), seconds=30)
    await s.until("the call is placed outside the normal window, and code decides", lambda: state(acme, "awaiting_release"))
    case = (await s.cases())[acme]
    s.check(case["outcome"] == "verified", "beat 5: Verified")
    async with s.core("clerk") as clerk:
        detail = (await clerk.get(f"/api/cases/{case['id']}")).json()
        s.check(detail["permissions"]["can_approve"] is False and any("(R5)" in x for x in detail["permissions"]["reasons"]),
                "the changer is refused (R5)")
    body = {"expected_version": case["version"], "action": "approve", "reason": "Confirmed on the ledger's number."}
    async with s.core("approver1") as approver:
        resp = await approver.post(f"/api/cases/{case['id']}/release", json=body)
        s.check(resp.status_code == 200 and resp.json()["to_state"] == "releasing", "a second person releases")
    await s.until("the hold comes off and the case is released", lambda: state(acme, "released"))

    await demo.change(env, seed, "cobalt")
    await s.until("a denied change is not verified", lambda: state(cobalt, "blocked"))
    t = await demo.tamper(env, confirmed=True)
    s.check(f"event #{t.seq}" in (t.alert or ""), f"beat 6: tamper flips event #{t.seq} and the verify job names it")
    st = await demo.status(env)
    s.check(st["chain"]["ok"] is False and any(a["kind"] == "chain_broken" and a["severity"] == "critical" for a in st["alerts"]),
            "status shows the broken chain and its critical alert")

    failed = [what for ok, what in s.results if not ok]
    print(f"\n{len(s.results) - len(failed)} of {len(s.results)} checks passed")
    return 1 if failed else 0


async def _held(s: Smoke, vendor: str) -> bool:
    case = (await s.cases()).get(vendor, {})
    return case.get("hold_status") == "confirmed"


def main() -> int:
    with migrated_database() as url:
        return asyncio.run(rehearse(url))


if __name__ == "__main__":
    sys.exit(main())
