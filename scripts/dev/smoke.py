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
     "Hold not confirmed" alert (R7).
  6. The evidence chain is intact.
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
    def __init__(self, settings, dataset_id, refs: dict[str, str], users: dict[str, tuple]) -> None:
        self.settings, self.dataset_id, self.refs, self.users = settings, str(dataset_id), refs, users
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
            chain = (await approver.get(f"/api/datasets/{self.dataset_id}/chain")).json()
            self.check(chain["ok"], f"the evidence chain is intact ({chain['checked']} events)")
        async with self.mock() as m:
            await m.patch("/_mock/settings", json={"fail_holds": False})

        failed = [what for ok, what in self.results if not ok]
        print(f"\n{len(self.results) - len(failed)} of {len(self.results)} checks passed.")
        return 1 if failed else 0


async def run_smoke(pool, settings, dataset_id, refs, users) -> int:
    return await Smoke(settings, dataset_id, refs, users).run()
