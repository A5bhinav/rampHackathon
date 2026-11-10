#!/usr/bin/env python3
"""Run the walking skeleton locally: Postgres, the Ramp mock and the core, wired together,
with a demo dataset seeded and its vendors imported.

    uv run python scripts/dev/stack.py                 # leave it running; open the dashboard
    uv run python scripts/dev/stack.py --smoke         # run one case end to end, check it, exit

Then, in another terminal, start the dashboard with the environment this prints.

TEST DOUBLES (spec 0002, D10). Until Inesh's select_number stub and Sami's text phone line
land, two clearly labeled doubles stand in for them, behaving as the contracts say:
`ScriptedLedger` picks one fixed contact point, and `ScriptedVoice` accepts the call and
then reports it to the core over HTTP exactly as internal.yaml describes, as a vendor who
knows their new account's last four digits. Replace them as the real pieces land.

Everything is fictional: .example domains, 555-01xx numbers, made-up account numbers.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime as dt
import os
import signal
import sys
import uuid
from pathlib import Path

import httpx
import uvicorn
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "db"))

from dialback_core.app import create_app
from dialback_core.datasets import create_dataset
from dialback_core.db import open_pool, system_tx
from dialback_core.detection import Keys, import_all
from dialback_core.flags import RampMode
from dialback_core.ports import ChangeFacts, PreDialRefused, Selection
from dialback_core.ramp import RampConnector
from dialback_core.ramp.connector import mock_token
from dialback_core.settings import Settings
from pgtemp import migrated_database

CORE_PORT, MOCK_PORT = 8001, 8003
CORE_URL, MOCK_URL = f"http://127.0.0.1:{CORE_PORT}", f"http://127.0.0.1:{MOCK_PORT}"
# Local development secrets only. Never reuse these anywhere real.
SECRETS = {
    "MOCK_RAMP_SECRET": "dev-mock-ramp-secret-0123456789",
    "RAMP_WEBHOOK_SECRET": "dev-webhook-secret-0123456789",
    "FINGERPRINT_KEY": "dev-fingerprint-key-0123456789abcdef0123",
    "DIGITS_KEY": "dev-digits-key-0123456789abcdef0123456789",
    "INTERNAL_API_TOKEN": "dev-internal-token-0123456789ab",
    "DIALBACK_JWT_SECRET": "dev-jwt-secret-0123456789abcdef0123456789",
}
CONTACT_POINT = uuid.UUID("5d4c3b2a-1f0e-4d9c-8b7a-6f5e4d3c2b1a")


class ScriptedLedger:
    """TEST DOUBLE for Inesh's select_number: one fixed, provable contact point."""

    async def select_number(self, *, case_id, dataset_id, vendor_id, cutoff) -> Selection:
        return Selection(uuid.uuid4(), "scripted-double", CONTACT_POINT, source_types=("contract", "listing"))


class ScriptedChangeContext:
    """TEST DOUBLE for Inesh's change context: the speaker isn't in the change thread and is
    known from before the request; an offered number's source stays unknown."""

    async def check(self, **_) -> ChangeFacts:
        return ChangeFacts(speaker_in_change_thread=False, speaker_known_before_request=True)


class ScriptedVoice:
    """TEST DOUBLE for Sami's voice service. Accepts the call, then reports it to the core as
    internal.yaml describes. The vendor says their new account's last four digits, unless the
    vendor's name is in `refuse` (a pre-dial refusal), `deny` (the vendor denies the change) or
    `callback` (the first call asks to be called back in a few seconds; spec 0007)."""

    def __init__(self, pool, refuse: set[str], deny: set[str], callback: set[str] = frozenset()) -> None:
        self.pool, self.refuse, self.deny, self.callback = pool, refuse, deny, callback
        self.tasks: set[asyncio.Task] = set()

    async def place_call(self, *, case_id, attempt, idempotency_key) -> uuid.UUID:
        async with system_tx(self.pool) as conn:
            row = await (await conn.execute(
                "select v.legal_name, n.account_last4 from app.cases c join app.vendors v on v.id = c.vendor_id "
                "join app.bank_change_events ce on ce.id = c.change_event_id "
                "join app.bank_accounts n on n.id = ce.new_account_id where c.id = %s", (case_id,))).fetchone()
        if row["legal_name"] in self.refuse:
            raise PreDialRefused("outside_calling_window", "scripted refusal")
        call_id = uuid.uuid4()
        task = asyncio.create_task(self._report(case_id, attempt, call_id, row["legal_name"], row["account_last4"]))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return call_id

    async def _report(self, case_id, attempt, call_id, vendor: str, last4: str) -> None:
        await asyncio.sleep(0.5)  # the call happens; the core appends call_started meanwhile
        denies = vendor in self.deny

        def a(slot, value, turn):
            return {"slot": slot, "value": value, "turn_id": turn, "tool_call_id": f"tc_{turn}",
                    "captured_at": f"2026-11-01T17:02:{turn:02d}Z", "capture_method": "keypad" if slot == "account_last4" else "speech"}

        if vendor in self.callback and attempt == 1:
            when = (dt.datetime.now(dt.UTC) + dt.timedelta(seconds=2)).isoformat()
            body = {"call_id": str(call_id), "case_id": str(case_id), "result": "callback_requested", "attempt": attempt,
                    "retry_at": when, "contact_point_id": str(CONTACT_POINT), "direction": "outbound",
                    "dialed_number_is_pick": True, "answered_by": "human", "agent_version": "scripted-double",
                    "answers": {"schema_version": "call-answers/v0", "call_id": str(call_id),
                                "answers": [a("recording_consent", "yes", 1)]}}
            await self._send(case_id, attempt, call_id, body)
            return
        answers = [a("recording_consent", "yes", 1), a("company_named", vendor.removesuffix(" LLC").removesuffix(" Inc"), 3),
                   a("speaker_name", "Jordan Lee", 5), a("speaker_role", "AR manager", 5),
                   a("change_requested", "no" if denies else "yes", 7)]
        if not denies:
            answers.append(a("account_last4", last4, 9))
        body = {"call_id": str(call_id), "case_id": str(case_id), "result": "answers_recorded", "attempt": attempt,
                "contact_point_id": str(CONTACT_POINT), "direction": "outbound", "dialed_number_is_pick": True,
                "answered_by": "human",
                "agent_version": "scripted-double", "answers": {"schema_version": "call-answers/v0", "call_id": str(call_id), "answers": answers},
                "checks": {"disclosure_first": True, "answers_grounded": True, "agent_spoke_account_digits": False,
                           "new_number_accepted": False, "policy_violations": 0, "speech_guard_trips": 0}}
        await self._send(case_id, attempt, call_id, body)

    async def _send(self, case_id, attempt, call_id, body: dict) -> None:
        async with httpx.AsyncClient(timeout=10.0) as http:
            for _ in range(10):
                r = await http.post(f"{CORE_URL}/internal/calls/{call_id}/ended", json=body,
                                    headers={"Authorization": f"Bearer {SECRETS['INTERNAL_API_TOKEN']}",
                                             "Idempotency-Key": f"{case_id}:call:{attempt}:ended"})
                if r.status_code != 409:
                    return
                await asyncio.sleep(0.5)  # call_started not appended yet; report again


VENDORS = [
    ("acme", "Acme Fasteners LLC", "000011112222", "000033334444"),
    ("brightline", "Brightline Office Supply Inc", "000044445555", "000066667777"),
    ("cobalt", "Cobalt Freight LLC", "000077778888", "000099990000"),
    ("delta", "Delta Paper Co", "000012121212", "000034343434"),
]


def seed_body(dataset_id: uuid.UUID) -> dict:
    return {
        "business_name": "Fictional Co (demo)",
        "users": [{"ref": "clerk", "email": "pat.clerk@fictional.example", "first_name": "Pat", "last_name": "Clerk"}],
        "vendors": [{
            "ref": ref, "name": name,
            "accounts": [{"routing_number": "021000021", "account_number": old}, {"routing_number": "121000248", "account_number": cur}],
            "contacts": [{"first_name": "Jordan", "last_name": "Lee", "phone_number": "+14155550133", "is_primary": True}],
            "bills": [{"ref": f"{ref}-bill-1", "invoice_number": f"{ref.upper()}-1001", "amount_cents": 125_000, "payment_date": "2026-11-05"},
                      {"ref": f"{ref}-bill-2", "invoice_number": f"{ref.upper()}-1002", "amount_cents": 80_000, "payment_date": "2026-11-12"}],
        } for ref, name, old, cur in VENDORS],
        "webhook_subscriptions": [{"url": f"{CORE_URL}/webhooks/ramp/{dataset_id}", "secret": SECRETS["RAMP_WEBHOOK_SECRET"]}],
    }


async def seed(pool, settings: Settings) -> tuple[uuid.UUID, dict, dict]:
    dataset = await create_dataset(pool, "demo", "Walking skeleton demo")
    token = mock_token(settings.mock_ramp_secret or b"", dataset.id)
    async with httpx.AsyncClient(base_url=MOCK_URL, headers={"Authorization": f"Bearer {token}"}) as mock:
        refs = (await mock.post("/_mock/seed", json=seed_body(dataset.id))).raise_for_status().json()["refs"]
    users = {}
    async with system_tx(pool) as conn:
        for key, name, role, ramp_ref in (("clerk", "Pat Clerk", "clerk", "clerk"), ("approver1", "Riley Approver", "approver", None),
                                          ("approver2", "Sam Approver", "approver", None)):
            uid = uuid.uuid4()
            await conn.execute("insert into app.user_profiles (user_id, display_name, ramp_user_id) values (%s, %s, %s)",
                               (uid, name, refs.get(ramp_ref) if ramp_ref else None))
            await conn.execute("insert into app.user_roles (user_id, role) values (%s, %s)", (uid, role))
            users[key] = (uid, name)
        # Like `demo reset`: record the seeded Ramp users and vendors, so the dashboard's Ramp (mock)
        # portal knows Pat Clerk is this demo's clerk (spec 0077).
        vendors = {v["ref"]: refs[v["ref"]] for v in seed_body(dataset.id)["vendors"]}
        await conn.execute("update app.datasets set demo_refs = %s where id = %s",
                           (Jsonb({"users": {"clerk": refs["clerk"]}, "vendors": vendors}), dataset.id))
    async with httpx.AsyncClient(timeout=10.0) as http:
        await import_all(pool, RampConnector(settings, http, pool=pool).for_dataset(dataset.id),
                         Keys(settings.fingerprint_key, settings.fingerprint_key_version), dataset.id)
    return dataset.id, refs, users


async def bank_change(settings: Settings, dataset_id, vendor_id: str, account: str, actor: str | None) -> None:
    token = mock_token(settings.mock_ramp_secret or b"", dataset_id)
    body = {"routing_number": "026009593", "account_number": account} | ({"actor_user_id": actor} if actor else {})
    async with httpx.AsyncClient(base_url=MOCK_URL, headers={"Authorization": f"Bearer {token}"}) as mock:
        (await mock.post(f"/_mock/vendors/{vendor_id}/bank-change", json=body)).raise_for_status()


def core_settings(url: str, backoff: float = 1.0, jobs_every: int = 30, supabase_url: str | None = None) -> Settings:
    s = SECRETS
    # With a Supabase project (or scripts/dev/fake_supabase_auth.py), the core trusts only its
    # signing keys; otherwise the dashboard's dev sign-in secret (spec 0010).
    return Settings(database_url=url, ramp_mode=RampMode.MOCK, ramp_base_url=MOCK_URL, supabase_url=supabase_url,
                    ramp_webhook_secret=s["RAMP_WEBHOOK_SECRET"].encode(), fingerprint_key=s["FINGERPRINT_KEY"].encode(),
                    mock_ramp_secret=s["MOCK_RAMP_SECRET"].encode(), digits_key=s["DIGITS_KEY"].encode(),
                    internal_api_token=s["INTERNAL_API_TOKEN"].encode(),
                    jwt_secret=b"" if supabase_url else s["DIALBACK_JWT_SECRET"].encode(),
                    calling_window="00:00-23:59", calling_days="all",  # the evening demo (spec 0002, Q7)
                    command_backoff_seconds=backoff, worker_poll_seconds=0.3, run_worker=True,
                    # The poller and reconciliation (spec 0003), much more often than in production.
                    poll_interval_seconds=jobs_every, reconcile_interval_seconds=jobs_every,
                    verify_interval_seconds=jobs_every)


async def wait_for(url: str, seconds: float = 20.0) -> None:
    async with httpx.AsyncClient() as http:
        for _ in range(int(seconds * 10)):
            with contextlib.suppress(httpx.HTTPError):
                if (await http.get(url)).status_code == 200:
                    return
            await asyncio.sleep(0.1)
    raise RuntimeError(f"{url} did not come up")


async def run(url: str, args: argparse.Namespace) -> int:
    env = os.environ | {"DATABASE_URL": url, "MOCK_RAMP_SECRET": SECRETS["MOCK_RAMP_SECRET"], "PORT": str(MOCK_PORT),
                        "HOST": "127.0.0.1"}
    mock = await asyncio.create_subprocess_exec(sys.executable, "-m", "dialback_ramp_mock.main", env=env)
    try:
        await wait_for(f"{MOCK_URL}/healthz")
        settings = core_settings(url, backoff=0.2 if args.smoke else 1.0, jobs_every=2 if args.smoke else 30,
                                 supabase_url=args.supabase_url)
        pool = await open_pool(url, max_size=10)
        voice = ScriptedVoice(pool, refuse={"Brightline Office Supply Inc"}, deny={"Cobalt Freight LLC"},
                              callback={"Delta Paper Co"})
        app = create_app(settings, pool=pool, resolver=ScriptedLedger(), caller=voice, change_context=ScriptedChangeContext())
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=CORE_PORT, log_level="warning"))
        serving = asyncio.create_task(server.serve())
        await wait_for(f"{CORE_URL}/healthz")

        dataset_id, refs, users = await seed(pool, settings)
        dev_users = ",".join(f"{uid}:{name}" for uid, name in users.values())
        print("\nThe walking skeleton is running.\n")
        print(f"  core      {CORE_URL}\n  ramp mock {MOCK_URL}\n  dataset   {dataset_id}\n")
        print("Start the dashboard in another terminal:\n")
        if args.supabase_url:
            print(f"  cd apps/web && CORE_URL={CORE_URL} SUPABASE_URL={args.supabase_url} \\")
            print("    SUPABASE_PUBLISHABLE_KEY=sb_publishable_local pnpm dev\n")
            print(f"  Seeded users (id:name), for the Supabase project or the stand-in: {dev_users}\n")
        else:
            print(f"  cd apps/web && CORE_URL={CORE_URL} DIALBACK_AUTH_MODE=dev DIALBACK_JWT_SECRET={SECRETS['DIALBACK_JWT_SECRET']} \\")
            print(f"    DIALBACK_DEV_USERS='{dev_users}' pnpm dev\n")

        if args.changes or args.smoke:
            # Four bank changes: Acme confirms (verified), Brightline's call is refused before
            # dialing (needs review), Cobalt's contact denies the change (not verified), and
            # Delta asks for a callback, then confirms; no Dialback user made Delta's change, so
            # its release takes two approvers (spec 0007).
            await bank_change(settings, dataset_id, refs["acme"], "000012340001", refs["clerk"])
            await bank_change(settings, dataset_id, refs["brightline"], "000012340002", refs["clerk"])
            await bank_change(settings, dataset_id, refs["cobalt"], "000012340003", None)
            await bank_change(settings, dataset_id, refs["delta"], "000012340004", None)
            print("Made four bank changes in the Ramp mock (Acme, Brightline, Cobalt, Delta).\n")

        code = 0
        if args.smoke:
            from smoke import run_smoke
            code = await run_smoke(pool, settings, dataset_id, refs, users)
        else:
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop.set)
            await stop.wait()
        server.should_exit = True
        await serving
        await pool.close()
        return code
    finally:
        mock.terminate()
        await asyncio.wait_for(mock.wait(), timeout=10)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--changes", action="store_true", help="make three bank changes after seeding")
    parser.add_argument("--smoke", action="store_true", help="run one case end to end, check it, and exit")
    parser.add_argument("--supabase-url", help="trust this Supabase project's signing keys instead of the dev sign-in "
                        "(e.g. http://127.0.0.1:54321 for scripts/dev/fake_supabase_auth.py)")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).parent))
    # A fresh, migrated database: on DATABASE_URL's server if set (CI), else a throwaway Postgres.
    with migrated_database() as url:
        return asyncio.run(run(url, args))


if __name__ == "__main__":
    sys.exit(main())
