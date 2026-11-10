"""Demo mode (spec 0011): one command per beat of the demo story, run from the core's shell.

    reset   a fresh demo company: a new dataset and chain, seeded, imported and linked; earlier
            demo datasets are hidden, nothing is deleted
    change  beat 2: a vendor's seeded bank change in the Ramp mock, by the linked clerk or nobody
    tamper  beat 6: one evidence row edited by hand, then the verify job names it
    status  a pre-flight check: the current demo, its cases, the chain and open alerts
    chaos   spec 0008's chaos mode on the current demo business, on or off

Every command refuses Ramp's sandbox (it can't be reset) and any dataset that isn't kind `demo`.
The mock is reached over its HTTP API only; nothing here imports another app.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
import psycopg
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from .datasets import Dataset, create_dataset, retire_dataset
from .db import system_tx
from .detection import Keys, import_all
from .flags import RampMode
from .jobs import Jobs
from .ramp import RampConnector
from .ramp.connector import mock_token
from .settings import Settings

DEFAULT_SEED = Path("scripts/demo/seed.json")
CHAOS = {"chaos_error_permille": 300, "chaos_drop_permille": 0}  # seeded, 300‰ errors, no dropped webhooks (D7)
DATE_FIELDS = ("issued_at", "due_at", "payment_date")
RELATIVE = re.compile(r"^([+-])(\d+)d$")
OUTCOME = re.compile(r'"outcome":"[a-z_]+"')


class DemoError(RuntimeError):
    """A demo command refused, with a message for the person at the shell. Nothing was changed."""


@dataclass(frozen=True)
class DemoEnv:
    pool: AsyncConnectionPool
    settings: Settings
    mock: httpx.AsyncClient            # the Ramp mock's base URL
    connector: RampConnector
    owner_url: str                     # DATABASE_URL: the table owner's connection (links, tamper)


@dataclass(frozen=True)
class ResetResult:
    dataset: Dataset
    refs: dict[str, str]
    linked: dict[str, UUID]
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TamperResult:
    dataset_id: UUID
    seq: int
    alert: str | None  # the open chain_broken alert; an earlier one, if this demo was already broken


def load_seed(path: Path | str = DEFAULT_SEED) -> dict[str, Any]:
    try:
        seed = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DemoError(f"can't read the demo seed {path}: {exc}") from exc
    if not isinstance(seed, dict) or not seed.get("vendors"):
        raise DemoError(f"{path} is not a demo seed: it needs vendors (and a changes list for `demo change`)")
    return seed


def resolve_dates(seed: dict[str, Any], today: dt.date) -> dict[str, Any]:
    """The mock's seed body: relative dates ("+3d", "-27d") resolved against today, `changes` left out."""
    body = json.loads(json.dumps({k: v for k, v in seed.items() if k != "changes"}))
    for vendor in body["vendors"]:
        for bill in vendor.get("bills", []):
            for key in DATE_FIELDS:
                m = RELATIVE.match(str(bill.get(key, "")))
                if m:
                    days = int(m[2]) * (1 if m[1] == "+" else -1)
                    bill[key] = (today + dt.timedelta(days=days)).isoformat()
    return body


def _guard(settings: Settings) -> None:
    if settings.ramp_mode is RampMode.SANDBOX:
        raise DemoError("DIALBACK_RAMP=sandbox: Ramp's sandbox can't be reset, so demo commands run only against the mock")


def _auth(settings: Settings, dataset_id: UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {mock_token(settings.mock_ramp_secret or b'', dataset_id)}"}


async def _mock(env: DemoEnv, method: str, path: str, dataset_id: UUID, body: dict | None = None) -> dict[str, Any]:
    r = await env.mock.request(method, path, json=body, headers=_auth(env.settings, dataset_id))
    if r.status_code != 200:
        raise DemoError(f"the Ramp mock refused {method} {path}: {r.status_code} {r.text[:300]}")
    return r.json()


async def _current(pool: AsyncConnectionPool, dataset_id: UUID | None = None) -> dict[str, Any]:
    async with system_tx(pool) as conn:
        if dataset_id is not None:
            row = await (await conn.execute("select * from app.datasets where id = %s", (dataset_id,))).fetchone()
        else:
            row = await (await conn.execute("select * from app.datasets where kind = 'demo' and retired_at is null "
                                            "order by created_at desc limit 1")).fetchone()
    if row is None:
        raise DemoError("there is no current demo dataset: run `dialback-core demo reset` first")
    if row["kind"] != "demo":
        raise DemoError(f"dataset {row['id']} is a {row['kind']} dataset; demo commands act only on demo datasets")
    if row["retired_at"] is not None:
        raise DemoError(f"dataset {row['id']} is a retired demo; demo commands act only on the current one")
    return row


async def _accounts(owner_url: str, links: dict[str, str]) -> dict[str, UUID]:
    """Each linked email's Dialback account: a Supabase user with a profile. Emails are given at run
    time, never committed (D2)."""
    found: dict[str, UUID] = {}
    async with await psycopg.AsyncConnection.connect(owner_url) as conn:
        if (await (await conn.execute("select to_regclass('auth.users') is not null")).fetchone())[0] is False:
            raise DemoError("this database has no auth.users (it isn't a Supabase project), so --link has no accounts to find")
        for ref, email in links.items():
            rows = await (await conn.execute(
                "select u.id, p.user_id is not null from auth.users u left join app.user_profiles p on p.user_id = u.id "
                "where lower(u.email) = lower(%s)", (email,))).fetchall()
            if not rows:
                raise DemoError(f"--link {ref}: no account has the email {email}")
            if len(rows) > 1:
                raise DemoError(f"--link {ref}: {len(rows)} accounts share the email {email}; link by hand")
            if not rows[0][1]:
                raise DemoError(f"--link {ref}: {email} has no Dialback account yet; add it with scripts/db/add_user.py")
            found[ref] = rows[0][0]
    return found


async def reset(env: DemoEnv, seed: dict[str, Any], links: dict[str, str], today: dt.date | None = None) -> ResetResult:
    """Every check runs before anything is written. The new dataset is created hidden and becomes
    current only at the end, in the same transaction that hides the earlier demos and sets the
    links, so a reset that fails partway never replaces the previous demo."""
    _guard(env.settings)
    today = today or dt.datetime.now(ZoneInfo(env.settings.calling_tz)).date()
    users = [u["ref"] for u in seed.get("users", [])]
    for ref in links:
        if ref not in users:
            raise DemoError(f"--link {ref}: the seed has no user with that ref (it has: {', '.join(users) or 'none'})")
    accounts = await _accounts(env.owner_url, links) if links else {}
    warnings = [f"seeded user '{ref}' is not linked to a Dialback account: their changes have no known author, "
                "so each release needs two approvers (ADR-10)" for ref in users if ref not in links]

    body = resolve_dates(seed, today)
    dataset = await create_dataset(env.pool, "demo", f"Demo {dt.datetime.now(dt.UTC):%Y-%m-%d %H:%M} UTC")
    await retire_dataset(env.pool, dataset.id)  # hidden until it's complete
    public = env.settings.core_public_url
    body["webhook_subscriptions"] = ([{"url": f"{public}/webhooks/ramp/{dataset.id}",
                                       "secret": env.settings.ramp_webhook_secret.decode()}] if public else [])
    body["settings"] = {"chaos_seed": None}  # every reset starts with chaos off
    if not public:
        warnings.append("CORE_PUBLIC_URL is not set: no webhooks, so the poller finds each change within its interval")
    refs = (await _mock(env, "POST", "/_mock/seed", dataset.id, body))["refs"]
    await import_all(env.pool, env.connector.for_dataset(dataset.id),
                     Keys(env.settings.fingerprint_key, env.settings.fingerprint_key_version), dataset.id)
    demo_refs = {"users": {r: refs[r] for r in users}, "vendors": {v["ref"]: refs[v["ref"]] for v in seed["vendors"]}}
    async with system_tx(env.pool) as conn:  # one step: the new demo becomes current, the old ones are hidden
        await conn.execute("update app.datasets set retired_at = now() where kind = 'demo' and retired_at is null")
        await conn.execute("update app.datasets set demo_refs = %s, retired_at = null where id = %s", (Jsonb(demo_refs), dataset.id))
        for ref, user_id in accounts.items():
            await conn.execute("update app.user_profiles set ramp_user_id = %s where user_id = %s", (refs[ref], user_id))
    return ResetResult(dataset, refs, accounts, warnings)


@dataclass(frozen=True)
class PrepareResult:
    reset: ResetResult
    ledger: dict[str, Any]          # dialback_ledger.demo.load's summary: per vendor, sightings and "team"/"fictional" AR line
    requests: dict[str, bool]       # per vendor, whether its change-request email went in


async def prepare(env: DemoEnv, seed: dict[str, Any], links: dict[str, str], *, phones: dict[str, str],
                  requests: list[str], today: dt.date | None = None) -> PrepareResult:
    """One command before the dashboard's clerk makes a change (spec 0077): a fresh demo with the clerk
    linked, then the Trust Ledger's history (with any team phones), then each vendor's change-request
    email. The order matters: the selection rule trusts only what was recorded before the case opened,
    so the ledger and the email must be in before the bank change. Every check runs before the reset."""
    from dialback_ledger import demo as ledger_demo  # the ledger's own demo commands

    _guard(env.settings)
    if "clerk" not in links:
        raise DemoError("prepare needs --link clerk=<email>: the dashboard's Ramp (mock) portal sends changes as that account")
    try:
        e164 = ledger_demo.check(phones, requests)
    except ledger_demo.DemoError as exc:
        raise DemoError(str(exc)) from None
    result = await reset(env, seed, links, today=today)
    try:
        loaded = await ledger_demo.load(env.owner_url, phones=e164, dataset_id=result.dataset.id)
        sent = {ref: await ledger_demo.request(env.owner_url, vendor=ref, dataset_id=result.dataset.id) for ref in requests}
    except ledger_demo.DemoError as exc:
        raise DemoError(f"the demo was reset ({result.dataset.id}) but the Trust Ledger refused: {exc}. "
                        "Run `dialback-ledger demo load` and `dialback-ledger demo request` by hand, before any bank change.") from None
    return PrepareResult(result, loaded, sent)


async def change(env: DemoEnv, seed: dict[str, Any], vendor_ref: str, by: str = "clerk", unknown: bool = False) -> str:
    """Beat 2: the vendor's seeded bank change, made in the mock as the seeded user `by` (the linked
    clerk), or with no known author. The mock's webhook follows; the core opens the case."""
    _guard(env.settings)
    ds = await _current(env.pool)
    refs = ds["demo_refs"] or {"users": {}, "vendors": {}}
    vendor = refs["vendors"].get(vendor_ref)
    if vendor is None:
        raise DemoError(f"no vendor '{vendor_ref}' in the current demo (it has: {', '.join(refs['vendors']) or 'none'})")
    new = seed.get("changes", {}).get(vendor_ref)
    if not new:
        raise DemoError(f"the seed has no change for '{vendor_ref}'")
    body = {"routing_number": new["routing_number"], "account_number": new["account_number"]}
    if not unknown:
        if by not in refs["users"]:
            raise DemoError(f"--by {by}: no seeded user with that ref (it has: {', '.join(refs['users']) or 'none'})")
        body["actor_user_id"] = refs["users"][by]
    made = await _mock(env, "POST", f"/_mock/vendors/{vendor}/bank-change", ds["id"], body)
    await _mock(env, "POST", "/_mock/webhooks/deliver", ds["id"])  # send now; the sender loop would within seconds
    return made["account_id"]


async def tamper(env: DemoEnv, *, confirmed: bool, seq: int | None = None, dataset_id: UUID | None = None) -> TamperResult:
    """Beat 6: as the table's owner, in one transaction, turn the update trigger off, edit one row
    of the demo's chain, and turn it back on. Other sessions wait on the lock and never see it off.
    By default the row is the latest decision that isn't `verified`, rewritten to `verified`."""
    _guard(env.settings)
    ds = await _current(env.pool, dataset_id)
    if not confirmed:
        raise DemoError("tamper edits an evidence row by hand and can't be undone: pass --yes-break-the-chain")
    async with await psycopg.AsyncConnection.connect(env.owner_url) as owner:
        if seq is None:
            row = await (await owner.execute(
                "select seq, body_jcs from audit.evidence_events where chain_id = %s and type = 'decision_made' "
                "and body_jcs like '%%\"outcome\":\"%%' and body_jcs not like '%%\"outcome\":\"verified\"%%' "
                "order by seq desc limit 1", (ds["chain_id"],))).fetchone()
            if row is None:
                raise DemoError("this demo has no decision to flip yet: pass --seq N to edit another row")
        else:
            row = await (await owner.execute("select seq, body_jcs from audit.evidence_events where chain_id = %s and seq = %s",
                                             (ds["chain_id"], seq))).fetchone()
            if row is None:
                raise DemoError(f"this demo's chain has no event #{seq}")
        seq, body = row[0], row[1]
        edited = OUTCOME.sub('"outcome":"verified"', body, count=1)
        if edited == body:
            edited = body[:-1] + ',"edited_by_hand":true}'
        await owner.execute("alter table audit.evidence_events disable trigger evidence_events_no_update")
        await owner.execute("update audit.evidence_events set body_jcs = %s where chain_id = %s and seq = %s",
                            (edited, ds["chain_id"], seq))
        await owner.execute("alter table audit.evidence_events enable trigger evidence_events_no_update")
    jobs = Jobs(env.pool, env.settings, env.connector, Keys(env.settings.fingerprint_key, env.settings.fingerprint_key_version),
                "demo-tamper")
    await jobs.run(ds["id"], "verify_chain", force=True)
    async with system_tx(env.pool) as conn:
        alert = await (await conn.execute("select message from app.alerts where dataset_id = %s and kind = 'chain_broken' "
                                          "and resolved_at is null order by raised_at desc limit 1", (ds["id"],))).fetchone()
    # The row is edited by now, so no refusal from here on. With no alert yet (the worker holds the
    # verify job's lease), the scheduled check raises it within DIALBACK_VERIFY_SECONDS.
    return TamperResult(ds["id"], seq, alert["message"] if alert else None)


async def status(env: DemoEnv) -> dict[str, Any]:
    """The pre-flight check before going on stage."""
    _guard(env.settings)
    ds = await _current(env.pool)
    async with system_tx(env.pool) as conn:
        cases = await (await conn.execute(
            "select c.id, v.legal_name as vendor, c.state::text as state, c.outcome::text as outcome from app.cases c "
            "join app.vendors v on v.id = c.vendor_id where c.dataset_id = %s order by c.opened_at", (ds["id"],))).fetchall()
        chain = await (await conn.execute("select ok, checked, first_bad_seq, detail from audit.verify_chain(%s)",
                                          (ds["chain_id"],))).fetchone()
        alerts = await (await conn.execute("select kind, severity::text as severity, message from app.alerts "
                                           "where dataset_id = %s and resolved_at is null order by raised_at", (ds["id"],))).fetchall()
    mock = await _mock(env, "PATCH", "/_mock/settings", ds["id"], {})  # an empty patch reads the settings
    return {"dataset": {"id": str(ds["id"]), "label": ds["label"], "created_at": ds["created_at"].isoformat()},
            "cases": [{**c, "id": str(c["id"])} for c in cases], "chain": dict(chain), "alerts": [dict(a) for a in alerts],
            "chaos": mock.get("chaos_seed") is not None}


async def chaos(env: DemoEnv, on: bool, seed: int = 11) -> dict[str, Any]:
    """Spec 0008's chaos mode on the current demo business only. Returns the mock's settings."""
    _guard(env.settings)
    ds = await _current(env.pool)
    return await _mock(env, "PATCH", "/_mock/settings", ds["id"], {"chaos_seed": seed, **CHAOS} if on else {"chaos_seed": None})
