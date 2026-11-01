"""The Ramp-shaped mock: Ramp's paths and fields over the mock_ramp schema.

Behavior that must match Ramp (holds clear payment dates, releases don't restore them,
webhooks duplicate or go silent on a switch) lives in SQL functions and is tested in
supabase/tests/004_mock_ramp.sql. This layer authenticates a dataset, rate-limits, maps
routes to queries, and shapes JSON. Field names marked "confirm" in the migration are
guesses until Ramp's OpenAPI spec is committed (spec 0002, Q3 and Q4).

Routes under /developer/v1 copy Ramp. Routes under /_mock exist only here: the lab and the
demo use them to seed a business, change bank details, flip chaos switches and run payments.
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field

from . import tokens
from .db import open_pool, transaction
from .ratelimit import SlidingWindowLimiter
from .settings import Settings
from .webhooks import deliver_due

API = "/developer/v1"
MAX_PAGE = 100


class MockError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.headers = status, code, message, headers or {}


def error(status: int, code: str, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status, headers=headers)


def iso(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        return value.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


# Representations: the JSON Ramp would return ------------------------------------------------

def vendor_json(v: dict, hold: dict | None) -> dict:
    return {
        "id": iso(v["id"]),
        "name": v["name"],
        "status": v["status"],
        "is_active": v["is_active"],
        "type": v["type"],
        "country": v["country"],
        "state": v["state"],
        "remote_id": v["remote_id"],
        "payment_method": v["payment_method"],
        "created_at": iso(v["created_at"]),
        "updated_at": iso(v["updated_at"]),
        # confirm (Q4): how Ramp shows a vendor-level payment hold
        "hold": None if hold is None else {"id": iso(hold["id"]), "placed_at": iso(hold["placed_at"]), "reason": hold["reason"]},
    }


def account_json(a: dict) -> dict:
    # Masked like Ramp: the full number never leaves the mock.
    return {
        "id": iso(a["id"]),
        "vendor_id": iso(a["vendor_id"]),
        "routing_number": a["routing_number"],
        "account_number_last4": a["account_number_last4"],
        "account_type": a["account_type"],
        "bank_name": a["bank_name"],
        "is_active": a["is_active"],
        "created_at": iso(a["created_at"]),
        "updated_at": iso(a["updated_at"]),
    }


def contact_json(c: dict) -> dict:
    return {k: iso(c[k]) for k in ("id", "vendor_id", "first_name", "last_name", "email", "phone_number", "is_primary", "created_at", "updated_at")}


def bill_json(b: dict) -> dict:
    return {
        "id": iso(b["id"]),
        "vendor_id": iso(b["vendor_id"]),
        "invoice_number": b["invoice_number"],
        "amount": {"amount": b["amount_cents"], "currency_code": b["currency_code"]},
        "issued_at": iso(b["issued_at"]),
        "due_at": iso(b["due_at"]),
        "payment_date": iso(b["payment_date"]),
        "status": b["status"],
        "is_on_hold": b["is_on_hold"],  # confirm (Q4)
        "paid_at": iso(b["paid_at"]),
        "created_at": iso(b["created_at"]),
        "updated_at": iso(b["updated_at"]),
    }


def audit_json(e: dict) -> dict:
    return {
        "id": iso(e["id"]),
        "event_type": e["event_type"],
        "actor_id": iso(e["actor_id"]),
        "target_type": e["target_type"],
        "target_id": iso(e["target_id"]),
        "payload": e["payload"],
        "occurred_at": iso(e["occurred_at"]),
    }


# Request bodies -----------------------------------------------------------------------------

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HoldRequest(Strict):
    reason: str | None = None


class BillUpdate(Strict):
    payment_date: dt.date | None


class BankChange(Strict):
    routing_number: str = Field(pattern=r"^[0-9]{9}$")
    account_number: str = Field(pattern=r"^[0-9]{4,17}$")
    actor_user_id: UUID | None = None
    account_type: str = "CHECKING"
    bank_name: str | None = None


class SettingsPatch(Strict):
    duplicate_webhooks: bool | None = None
    reorder_webhooks: bool | None = None
    silent_changes: bool | None = None
    webhook_delay_ms: int | None = Field(default=None, ge=0, le=600_000)
    rate_limit_per_10s: int | None = Field(default=None, ge=1, le=10_000)
    fail_next_hold: bool | None = None
    fail_next_release: bool | None = None
    hold_readback_lag_ms: int | None = Field(default=None, ge=0, le=600_000)


class PaymentRun(Strict):
    as_of: dt.date


class SeedUser(Strict):
    ref: str
    email: str
    first_name: str
    last_name: str
    role: str = "BUSINESS_USER"


class SeedAccount(Strict):
    ref: str | None = None
    routing_number: str = Field(pattern=r"^[0-9]{9}$")
    account_number: str = Field(pattern=r"^[0-9]{4,17}$")
    account_type: str = "CHECKING"
    bank_name: str | None = None
    created_by_ref: str | None = None
    created_at: dt.datetime | None = None


class SeedContact(Strict):
    ref: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone_number: str | None = None
    is_primary: bool = False


class SeedBill(Strict):
    ref: str | None = None
    invoice_number: str | None = None
    amount_cents: int = Field(ge=0)
    currency_code: str = "USD"
    issued_at: dt.date | None = None
    due_at: dt.date | None = None
    payment_date: dt.date | None = None
    status: str = "APPROVED"


class SeedVendor(Strict):
    ref: str
    name: str
    type: str | None = "BUSINESS"
    country: str | None = "US"
    state: str | None = None
    tax_id: str | None = None
    remote_id: str | None = None
    accounts: list[SeedAccount] = []
    contacts: list[SeedContact] = []
    bills: list[SeedBill] = []


class SeedSubscription(Strict):
    url: str
    secret: str = Field(min_length=16)
    event_types: list[str] = ["vendors.updated"]


class SeedRequest(Strict):
    business_name: str = "Mock business"
    users: list[SeedUser] = []
    vendors: list[SeedVendor] = []
    webhook_subscriptions: list[SeedSubscription] = []
    settings: SettingsPatch | None = None


# Auth and scope -----------------------------------------------------------------------------

@dataclass
class Scope:
    conn: AsyncConnection
    business_id: UUID
    dataset_id: UUID


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def dataset_from(request: Request) -> UUID:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    dataset_id = tokens.verify(request.app.state.settings.token_secret, token.strip()) if scheme.lower() == "bearer" else None
    if dataset_id is None:
        raise MockError(401, "unauthorized", "A valid mock bearer token is required.")
    return dataset_id


async def scope(request: Request) -> AsyncIterator[Scope]:
    """One transaction per request, as the service role, scoped to the token's business."""
    dataset_id = dataset_from(request)
    async with transaction(request.app.state.pool) as conn:
        row = await (await conn.execute(
            "select b.id, coalesce(s.rate_limit_per_10s, 200) as rate_limit "
            "from mock_ramp.businesses b left join mock_ramp.settings s on s.business_id = b.id where b.dataset_id = %s",
            (dataset_id,))).fetchone()
        if row is None:
            raise MockError(401, "unauthorized", "No mock business exists for this token's dataset; seed it first.")
        allowed, retry = request.app.state.limiter.allow(row["id"], client_ip(request), row["rate_limit"])
        if not allowed:
            raise MockError(429, "rate_limited", "Too many requests.", {"Retry-After": str(max(1, round(retry)))})
        yield Scope(conn, row["id"], dataset_id)


ScopeDep = Annotated[Scope, Depends(scope)]


async def owned_vendor(s: Scope, vendor_id: UUID, lock: bool = False) -> dict:
    sql = "select * from mock_ramp.vendors where id = %s and business_id = %s" + (" for update" if lock else "")
    v = await (await s.conn.execute(sql, (vendor_id, s.business_id))).fetchone()
    if v is None:
        raise MockError(404, "not_found", f"Vendor {vendor_id} not found.")
    return v


async def readback_lag(s: Scope) -> int:
    row = await (await s.conn.execute(
        "select hold_readback_lag_ms from mock_ramp.settings where business_id = %s", (s.business_id,))).fetchone()
    return row["hold_readback_lag_ms"] if row else 0


async def active_hold(s: Scope, vendor_id: UUID) -> dict | None:
    """The vendor's hold as a read shows it: a hold younger than hold_readback_lag_ms isn't
    visible yet, so the core's read-back has to wait (a chaos switch for the lab)."""
    return await (await s.conn.execute(
        "select * from mock_ramp.vendor_holds where vendor_id = %s and released_at is null "
        "and placed_at <= clock_timestamp() - make_interval(secs => %s / 1000.0)", (vendor_id, await readback_lag(s)))).fetchone()


def page(request: Request, rows: list[dict], size: int, shape) -> dict:
    data = [shape(r) for r in rows[:size]]
    nxt = None
    if len(rows) > size:
        nxt = str(request.url.include_query_params(start=str(rows[size - 1]["id"])))
    return {"data": data, "page": {"next": nxt}}


async def cursor_clause(s: Scope, table: str, start: UUID | None) -> tuple[str, tuple]:
    if start is None:
        return "", ()
    row = await (await s.conn.execute(f"select created_at, id from mock_ramp.{table} where id = %s", (start,))).fetchone()
    if row is None:
        raise MockError(400, "bad_cursor", "Unknown start cursor.")
    return " and (created_at, id) > (%s, %s)", (row["created_at"], row["id"])


# The app ------------------------------------------------------------------------------------

def create_app(settings: Settings, pool: AsyncConnectionPool | None = None,
               http: httpx.AsyncClient | None = None) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        owns_pool = pool is None
        app.state.pool = pool or await open_pool(settings.database_url)
        app.state.http = http or httpx.AsyncClient(timeout=settings.webhook_timeout_seconds)
        sender = None
        if settings.deliver_webhooks:
            sender = asyncio.create_task(_sender_loop(app))
        try:
            yield
        finally:
            if sender:
                sender.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await sender
            if http is None:
                await app.state.http.aclose()
            if owns_pool:
                await app.state.pool.close()

    app = FastAPI(title="Ramp mock", lifespan=lifespan)
    app.state.settings = settings
    app.state.limiter = SlidingWindowLimiter()
    if pool is not None:  # tests drive the app without running the lifespan
        app.state.pool = pool
        app.state.http = http

    @app.exception_handler(MockError)
    async def _mock_error(_: Request, exc: MockError):
        return error(exc.status, exc.code, exc.message, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, exc: RequestValidationError):
        return error(422, "invalid_request", "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()))

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    # Ramp's paths ----------------------------------------------------------------------------

    @app.get(f"{API}/vendors")
    async def list_vendors(request: Request, s: ScopeDep, from_updated_at: dt.datetime | None = None,
                           page_size: Annotated[int, Query(ge=1, le=MAX_PAGE)] = MAX_PAGE, start: UUID | None = None):
        clause, args = await cursor_clause(s, "vendors", start)
        where = "business_id = %s" + clause
        params: tuple = (s.business_id, *args)
        if from_updated_at is not None:
            where += " and updated_at >= %s"
            params += (from_updated_at,)
        rows = await (await s.conn.execute(
            f"select * from mock_ramp.vendors where {where} order by created_at, id limit %s", (*params, page_size + 1))).fetchall()
        holds = {h["vendor_id"]: h for h in await (await s.conn.execute(
            "select * from mock_ramp.vendor_holds where business_id = %s and released_at is null", (s.business_id,))).fetchall()}
        return page(request, rows, page_size, lambda v: vendor_json(v, holds.get(v["id"])))

    @app.get(f"{API}/vendors/{{vendor_id}}")
    async def get_vendor(vendor_id: UUID, s: ScopeDep):
        v = await owned_vendor(s, vendor_id)
        return vendor_json(v, await active_hold(s, vendor_id))

    @app.get(f"{API}/vendors/{{vendor_id}}/accounts")
    async def list_vendor_accounts(vendor_id: UUID, s: ScopeDep):
        await owned_vendor(s, vendor_id)
        rows = await (await s.conn.execute(
            "select * from mock_ramp.vendor_accounts_api where vendor_id = %s order by created_at, id", (vendor_id,))).fetchall()
        return {"data": [account_json(a) for a in rows], "page": {"next": None}}

    @app.get(f"{API}/vendors/{{vendor_id}}/contacts")
    async def list_vendor_contacts(vendor_id: UUID, s: ScopeDep):
        await owned_vendor(s, vendor_id)
        rows = await (await s.conn.execute(
            "select * from mock_ramp.vendor_contacts where vendor_id = %s order by created_at, id", (vendor_id,))).fetchall()
        return {"data": [contact_json(c) for c in rows], "page": {"next": None}}

    @app.post(f"{API}/vendors/{{vendor_id}}/hold")
    async def place_hold(vendor_id: UUID, s: ScopeDep, body: HoldRequest | None = None):
        await owned_vendor(s, vendor_id)
        if await _switch(s, "fail_next_hold"):
            # Return, don't raise: the transaction commits, so the one-shot switch stays spent.
            return error(500, "internal_error", "Simulated failure placing the hold.")
        hold_id = (await (await s.conn.execute(
            "select mock_ramp.place_hold(%s, %s, %s) as id", (vendor_id, body.reason if body else None, "api"))).fetchone())["id"]
        hold = await (await s.conn.execute("select * from mock_ramp.vendor_holds where id = %s", (hold_id,))).fetchone()
        return {"id": iso(hold["id"]), "vendor_id": iso(vendor_id), "placed_at": iso(hold["placed_at"])}

    @app.post(f"{API}/vendors/{{vendor_id}}/release")
    async def release_hold(vendor_id: UUID, s: ScopeDep):
        await owned_vendor(s, vendor_id)
        if await _switch(s, "fail_next_release"):
            return error(500, "internal_error", "Simulated failure releasing the hold.")
        released = (await (await s.conn.execute("select mock_ramp.release_hold(%s) as r", (vendor_id,))).fetchone())["r"]
        return {"vendor_id": iso(vendor_id), "released": released}

    @app.get(f"{API}/bills")
    async def list_bills(request: Request, s: ScopeDep, vendor_id: UUID | None = None,
                         page_size: Annotated[int, Query(ge=1, le=MAX_PAGE)] = MAX_PAGE, start: UUID | None = None):
        clause, args = await cursor_clause(s, "bills", start)
        where, params = "business_id = %s" + clause, (s.business_id, *args)
        if vendor_id is not None:
            where += " and vendor_id = %s"
            params += (vendor_id,)
        rows = await (await s.conn.execute(
            f"select * from mock_ramp.bills where {where} order by created_at, id limit %s", (*params, page_size + 1))).fetchall()
        lag = await readback_lag(s)
        if lag:
            fresh = {r["vendor_id"] for r in await (await s.conn.execute(
                "select vendor_id from mock_ramp.vendor_holds where business_id = %s and released_at is null "
                "and placed_at > clock_timestamp() - make_interval(secs => %s / 1000.0)", (s.business_id, lag))).fetchall()}
            rows = [dict(r, is_on_hold=False) if r["vendor_id"] in fresh else r for r in rows]
        return page(request, rows, page_size, bill_json)

    @app.patch(f"{API}/bills/{{bill_id}}")  # confirm: Ramp's path for updating a bill's payment date
    async def update_bill(bill_id: UUID, body: BillUpdate, s: ScopeDep):
        row = await (await s.conn.execute(
            "update mock_ramp.bills set payment_date = %s where id = %s and business_id = %s and paid_at is null returning *",
            (body.payment_date, bill_id, s.business_id))).fetchone()
        if row is None:
            raise MockError(404, "not_found", f"Unpaid bill {bill_id} not found.")
        return bill_json(row)

    @app.get(f"{API}/audit-logs/events")
    async def audit_events(s: ScopeDep, target_id: UUID | None = None, from_occurred_at: dt.datetime | None = None,
                           event_type: str | None = None, page_size: Annotated[int, Query(ge=1, le=MAX_PAGE)] = MAX_PAGE):
        where, params = "business_id = %s", (s.business_id,)
        for column, value in (("target_id", target_id), ("event_type", event_type)):
            if value is not None:
                where += f" and {column} = %s"
                params += (value,)
        if from_occurred_at is not None:
            where += " and occurred_at >= %s"
            params += (from_occurred_at,)
        rows = await (await s.conn.execute(
            f"select * from mock_ramp.audit_events where {where} order by occurred_at, id limit %s", (*params, page_size))).fetchall()
        return {"data": [audit_json(e) for e in rows], "page": {"next": None}}

    # Mock-only routes --------------------------------------------------------------------------

    @app.post("/_mock/seed")
    async def seed(request: Request, body: SeedRequest):
        """Create the token's business if needed, then add everything in the body. Returns ids by ref."""
        dataset_id = dataset_from(request)
        async with transaction(request.app.state.pool) as conn:
            return await _seed(conn, dataset_id, body)

    @app.post("/_mock/vendors/{vendor_id}/bank-change")
    async def bank_change(vendor_id: UUID, body: BankChange, s: ScopeDep):
        await owned_vendor(s, vendor_id)
        account_id = (await (await s.conn.execute(
            "select mock_ramp.change_bank_account(%s, %s, %s, %s, %s, %s) as id",
            (vendor_id, body.routing_number, body.account_number, body.actor_user_id, body.account_type, body.bank_name))).fetchone())["id"]
        return {"account_id": iso(account_id)}

    @app.patch("/_mock/settings")
    async def patch_settings(body: SettingsPatch, s: ScopeDep):
        return await _apply_settings(s.conn, s.business_id, body)

    @app.post("/_mock/payment-run")
    async def payment_run(body: PaymentRun, s: ScopeDep):
        """Pay every approved, unpaid bill due by as_of, unless it or its vendor is on hold (R1 in the demo)."""
        rows = await (await s.conn.execute(
            """update mock_ramp.bills b set status = 'PAID', paid_at = now()
                where b.business_id = %s and b.paid_at is null and b.status = 'APPROVED'
                  and not b.is_on_hold and b.payment_date is not null and b.payment_date <= %s
                  and not exists (select 1 from mock_ramp.vendor_holds h where h.vendor_id = b.vendor_id and h.released_at is null)
               returning b.id""", (s.business_id, body.as_of))).fetchall()
        return {"paid_bill_ids": [iso(r["id"]) for r in rows]}

    @app.post("/_mock/webhooks/deliver")
    async def deliver_now(request: Request, everything: bool = True):
        """Deliver this business's queued webhooks now, in scheduled order (tests and the demo don't
        wait for the sender loop). By default this includes delayed duplicates and retries."""
        dataset_id = dataset_from(request)
        return {"delivered": await deliver_due(request.app.state.pool, request.app.state.http,
                                               settings.webhook_max_attempts, dataset_id=dataset_id, everything=everything)}

    return app


async def _switch(s: Scope, name: str) -> bool:
    return (await (await s.conn.execute("select mock_ramp.consume_switch(%s, %s) as fired", (s.business_id, name))).fetchone())["fired"]


async def _apply_settings(conn: AsyncConnection, business_id: UUID, body: SettingsPatch) -> dict:
    changes = body.model_dump(exclude_none=True)
    await conn.execute("insert into mock_ramp.settings (business_id) values (%s) on conflict do nothing", (business_id,))
    if changes:
        sets = ", ".join(f"{k} = %s" for k in changes)
        await conn.execute(f"update mock_ramp.settings set {sets}, updated_at = now() where business_id = %s",
                           (*changes.values(), business_id))
    row = await (await conn.execute("select * from mock_ramp.settings where business_id = %s", (business_id,))).fetchone()
    return {k: iso(v) for k, v in row.items()}


async def _seed(conn: AsyncConnection, dataset_id: UUID, body: SeedRequest) -> dict:
    biz = await (await conn.execute(
        "insert into mock_ramp.businesses (dataset_id, name) values (%s, %s) "
        "on conflict (dataset_id) do update set name = mock_ramp.businesses.name returning id",
        (dataset_id, body.business_name))).fetchone()
    business_id = biz["id"]
    await _apply_settings(conn, business_id, body.settings or SettingsPatch())
    refs: dict[str, str] = {}

    async def insert(sql: str, params: tuple) -> UUID:
        return (await (await conn.execute(sql + " returning id", params)).fetchone())["id"]

    for u in body.users:
        refs[u.ref] = iso(await insert(
            "insert into mock_ramp.users (business_id, email, first_name, last_name, role) values (%s, %s, %s, %s, %s)",
            (business_id, u.email, u.first_name, u.last_name, u.role)))
    for v in body.vendors:
        vid = await insert(
            "insert into mock_ramp.vendors (business_id, name, type, country, state, tax_id, remote_id, created_at, updated_at) "
            "values (%s, %s, %s, %s, %s, %s, %s, clock_timestamp(), clock_timestamp())",
            (business_id, v.name, v.type, v.country, v.state, v.tax_id, v.remote_id))
        refs[v.ref] = iso(vid)
        for i, a in enumerate(v.accounts):
            created_by = refs.get(a.created_by_ref) if a.created_by_ref else None
            # Only the last account listed is active, as in Ramp after a change.
            aid = await insert(
                "insert into mock_ramp.vendor_accounts (business_id, vendor_id, routing_number, account_number, account_type, bank_name, "
                "created_by, is_active, created_at) values (%s, %s, %s, %s, %s, %s, %s, %s, coalesce(%s, clock_timestamp()))",
                (business_id, vid, a.routing_number, a.account_number, a.account_type, a.bank_name, created_by,
                 i == len(v.accounts) - 1, a.created_at))
            if a.ref:
                refs[a.ref] = iso(aid)
        for c in v.contacts:
            cid = await insert(
                "insert into mock_ramp.vendor_contacts (business_id, vendor_id, first_name, last_name, email, phone_number, is_primary, created_at) "
                "values (%s, %s, %s, %s, %s, %s, %s, clock_timestamp())",
                (business_id, vid, c.first_name, c.last_name, c.email, c.phone_number, c.is_primary))
            if c.ref:
                refs[c.ref] = iso(cid)
        for b in v.bills:
            bid = await insert(
                "insert into mock_ramp.bills (business_id, vendor_id, invoice_number, amount_cents, currency_code, issued_at, due_at, "
                "payment_date, status, created_at) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, clock_timestamp())",
                (business_id, vid, b.invoice_number, b.amount_cents, b.currency_code, b.issued_at, b.due_at, b.payment_date, b.status))
            if b.ref:
                refs[b.ref] = iso(bid)
    for sub in body.webhook_subscriptions:
        await insert(
            "insert into mock_ramp.webhook_subscriptions (business_id, url, secret, event_types) values (%s, %s, %s, %s)",
            (business_id, sub.url, sub.secret, sub.event_types))
    return {"business_id": iso(business_id), "refs": refs}


async def _sender_loop(app: FastAPI) -> None:
    settings: Settings = app.state.settings
    while True:
        try:
            delivered = await deliver_due(app.state.pool, app.state.http, settings.webhook_max_attempts)
        except Exception:  # noqa: BLE001  (keep the loop alive; deliveries stay scheduled and retry)
            delivered = 0
        if not delivered:
            await asyncio.sleep(settings.webhook_poll_seconds)

