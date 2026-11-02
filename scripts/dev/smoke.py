"""The walking skeleton's end-to-end check (specs/0001-walking-skeleton.md), run by
`scripts/dev/stack.py --smoke` against the real services: the Ramp mock as its own process,
the core over HTTP with its worker running, and the dashboard API as a signed-in approver.

What it proves, in order:
  1. Three bank changes become three cases and settle where the scripted calls send them:
     a confirmed change awaits release, a refused call needs review, a denial is blocked.
  2. R1: while the cases are open, a payment run in Ramp pays nothing.
  3. A different person releases the confirmed case through the API; the hold comes off in
     Ramp, the saved payment dates come back, and only then does the bill get paid.
  4. The changer cannot release (R5), and a stale version is refused.
  5. When Ramp's hold endpoint is down, a new change ends in needs_review with a
     "Hold not confirmed" alert (R7); when Ramp recovers, reconciliation puts the hold on.
  6. A hold lifted by hand in Ramp is put back by reconciliation, with a critical alert (I2).
  7. A bank change Ramp never announces is found by the poller (spec 0003).
  8. The evidence chain is intact.
  9. One evidence row edited by hand is caught by the scheduled chain check, which points
     straight at it (spec 0004; the demo's sixth beat).
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import jwt
from dialback_core.ramp.connector import mock_token

CORE, MOCK = "http://127.0.0.1:8001", "http://127.0.0.1:8003"


class Smoke:
    def __init__(self, pool, settings, dataset_id, refs: dict[str, str], users: dict[str, tuple]) -> None:
        self.pool, self.settings, self.dataset_id, self.refs, self.users = pool, settings, str(dataset_id), refs, users
        self.results: list[tuple[bool, str]] = []

    def check(self, ok: bool, what: str) -> bool:
        self.results.append((bool(ok), what))
        print(f"  {'ok  ' if ok else 'FAIL'}  {what}")
        return bool(ok)

    def token(self, who: str) -> dict[str, str]:
        claims = {"sub": str(self.users[who][0]), "aud": "authenticated", "role": "authenticated", "exp": int(time.time()) + 600}
        return {"Authorization": f"Bearer {jwt.encode(claims, self.settings.jwt_secret, algorithm='HS256')}"}

    def core(self, who: str = "approver1") -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=CORE, headers=self.token(who), timeout=10.0)

    def mock(self) -> httpx.AsyncClient:
        token = mock_token(self.settings.mock_ramp_secret, self.dataset_id)
        return httpx.AsyncClient(base_url=MOCK, headers={"Authorization": f"Bearer {token}"}, timeout=10.0)

    async def cases(self) -> dict[str, dict[str, Any]]:
        """Each vendor's latest case, by vendor name."""
        async with self.core() as c:
            states = ["open", "held", "calling", "retry_wait", "deciding", "awaiting_release", "needs_review", "blocked",
                      "releasing", "released"]
            r = await c.get("/api/cases", params={"dataset_id": self.dataset_id, "state": states})
            latest: dict[str, dict] = {}
            for case in r.json()["cases"]:
                latest[case["vendor"]["legal_name"]] = case  # oldest first, so the last one wins
            return latest

    async def until(self, what: str, predicate: Callable[[], Awaitable[bool]], seconds: float = 60.0) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if await predicate():
                return self.check(True, what)
            await asyncio.sleep(0.25)
        return self.check(False, f"{what} (timed out after {seconds:.0f}s)")

    async def pay(self, as_of: str) -> list[str]:
        async with self.mock() as m:
            return (await m.post("/_mock/payment-run", json={"as_of": as_of})).json()["paid_bill_ids"]

    async def bills(self, vendor_ref: str) -> list[dict]:
        async with self.mock() as m:
            return (await m.get("/developer/v1/bills", params={"vendor_id": self.refs[vendor_ref]})).json()["data"]

    async def run(self) -> int:
        print("\nWalking skeleton smoke run\n")
        acme, bright, cobalt = "Acme Fasteners LLC", "Brightline Office Supply Inc", "Cobalt Freight LLC"

        async def settled() -> bool:
            c = await self.cases()
            return (c.get(acme, {}).get("state"), c.get(bright, {}).get("state"), c.get(cobalt, {}).get("state")) == \
                ("awaiting_release", "needs_review", "blocked")
        await self.until("three changes settle: confirmed awaits release, refused call needs review, denial is blocked", settled)
        c = await self.cases()
        self.check(c[acme]["outcome"] == "verified" and c[cobalt]["outcome"] == "not_verified", "code decided verified and not verified")

        self.check(await self.pay("2026-12-31") == [], "R1: a payment run pays nothing while the cases are open")

        acme_id = c[acme]["id"]
        body = {"expected_version": 1, "action": "approve", "reason": "Confirmed on the ledger's number; last four match."}
        async with self.core("clerk") as clerk:
            r = await clerk.post(f"/api/cases/{acme_id}/release", json=body)
            self.check(r.status_code == 403, "R5: the clerk who entered the change cannot release it")
        async with self.core() as approver:
            r = await approver.post(f"/api/cases/{acme_id}/release", json=body | {"expected_version": 2})
            self.check(r.status_code == 409 and r.json()["code"] == "version_conflict", "a stale version is refused")
            r = await approver.post(f"/api/cases/{acme_id}/release", json=body)
            self.check(r.status_code == 200 and r.json()["to_state"] == "releasing", "a different approver releases through the API")

        async def released() -> bool:
            return (await self.cases()).get(acme, {}).get("state") == "released"
        await self.until("the worker lifts the hold and the case is released", released)
        dates = sorted(b["payment_date"] or "" for b in await self.bills("acme"))
        self.check(dates == ["2026-11-05", "2026-11-12"], "the saved payment dates are back in Ramp")
        self.check(await self.pay("2026-11-05") == [self.refs["acme-bill-1"]], "only the released vendor's due bill is paid")

        async with self.mock() as m:
            await m.patch("/_mock/settings", json={"fail_holds": True})
            await m.post(f"/_mock/vendors/{self.refs['acme']}/bank-change",
                         json={"routing_number": "026009593", "account_number": "000012340009", "actor_user_id": self.refs["clerk"]})

        async def hold_failed() -> bool:
            case = (await self.cases()).get(acme, {})
            return case.get("id") != acme_id and case.get("state") == "needs_review" and case.get("hold_status") == "failed"
        await self.until("with Ramp's hold endpoint down, a new change goes to a person", hold_failed, seconds=90)
        async with self.core() as approver:
            alerts = (await approver.get(f"/api/datasets/{self.dataset_id}/alerts")).json()["alerts"]
            self.check(any(a["kind"] == "hold_not_confirmed" and a["severity"] == "critical" for a in alerts),
                       "R7: a critical 'Hold not confirmed' alert is raised")
        async with self.mock() as m:
            await m.patch("/_mock/settings", json={"fail_holds": False})

        async def held(ref: str) -> bool:
            async with self.mock() as m:
                return (await m.get(f"/developer/v1/vendors/{self.refs[ref]}")).json()["hold"] is not None
        await self.until("when Ramp recovers, reconciliation puts the missing hold on", lambda: held("acme"))

        async with self.mock() as m:
            await m.post(f"/developer/v1/vendors/{self.refs['brightline']}/release")
        await self.until("a hold lifted by hand in Ramp is put back by reconciliation", lambda: held("brightline"))
        async with self.core() as approver:
            alerts = (await approver.get(f"/api/datasets/{self.dataset_id}/alerts")).json()["alerts"]
            self.check(any(a["kind"] == "vendor_not_held" and a["case_id"] == c[bright]["id"] for a in alerts),
                       "I2: the missing hold raised a critical 'vendor not held' alert")

        async with self.mock() as m:
            await m.patch("/_mock/settings", json={"silent_changes": True})
            await m.post(f"/_mock/vendors/{self.refs['cobalt']}/bank-change",
                         json={"routing_number": "026009593", "account_number": "000012340011"})

        async def found_by_poller() -> bool:
            return (await self.cases()).get(cobalt, {}).get("version") == 2
        await self.until("a bank change Ramp never announced is found by the poller", found_by_poller)
        async with self.mock() as m:
            await m.patch("/_mock/settings", json={"silent_changes": False})

        async with self.core() as approver:
            chain = (await approver.get(f"/api/datasets/{self.dataset_id}/chain")).json()
            self.check(chain["ok"], f"the evidence chain is intact ({chain['checked']} events)")

        # The demo's sixth beat: someone with owner rights edits one evidence row by hand, mid-chain.
        # One transaction, so the append-only trigger is back on when it ends, whatever happens.
        async with self.pool.connection() as owner:
            db = (await (await owner.execute("select current_database() as name")).fetchone())["name"]
            if not db.startswith("dialback_test_"):
                raise SystemExit(f"refusing to edit evidence in {db}: the smoke run only edits a throwaway database")
            await owner.execute("alter table audit.evidence_events disable trigger evidence_events_no_update")
            # Every envelope carries "v":1 (followed by "version", keys being sorted), so this edit
            # always lands, whichever door found the change.
            edited = await (await owner.execute(
                "update audit.evidence_events set body_jcs = replace(body_jcs, '\"v\":1,', '\"v\":9,') "
                "where chain_id = %s and seq = %s and body_jcs like '%%\"v\":1,%%' returning seq, case_id",
                (chain["chain_id"], chain["checked"] // 2))).fetchone()
            await owner.execute("alter table audit.evidence_events enable trigger evidence_events_no_update")
        self.check(edited is not None, f"one evidence row is edited by hand, as the demo shows (event #{chain['checked'] // 2})")

        async def caught() -> list[dict]:
            async with self.core() as approver:
                alerts = (await approver.get(f"/api/datasets/{self.dataset_id}/alerts")).json()["alerts"]
            return [a for a in alerts if a["kind"] == "chain_broken"]
        await self.until("the scheduled chain check raises 'Evidence chain broken'", caught)
        [alert] = await caught()
        async with self.core() as approver:
            chain = (await approver.get(f"/api/datasets/{self.dataset_id}/chain")).json()
        self.check(not chain["ok"] and chain["first_bad_seq"] == edited["seq"],
                   f"the chain badge points straight at the edited row (event #{chain['first_bad_seq']})")
        case_id = str(edited["case_id"]) if edited["case_id"] else None
        self.check(f"event #{edited['seq']}:" in alert["message"] and alert["case_id"] == case_id,
                   "the alert names that event and its case")

        failed = [what for ok, what in self.results if not ok]
        print(f"\n{len(self.results) - len(failed)} of {len(self.results)} checks passed.")
        return 1 if failed else 0


async def run_smoke(pool, settings, dataset_id, refs, users) -> int:
    return await Smoke(pool, settings, dataset_id, refs, users).run()
