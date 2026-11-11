"""The admin's API (spec 0095): people, the organisation chain, and the demo commands.

Every person call goes through one of the app.admin_* doors as the signed-in admin (ADR-9): the
database checks the role, refuses what it must, and writes the organisation chain (R6). This
module only maps its refusals to the contract's errors.

The demo commands are the exception spec 0095 D9 names: the core runs them with its own database
connection, as `dialback-core demo` does from the shell, after app.admin_record_demo has checked
the admin and put the intent on the chain. They act on the current demo dataset only.
"""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from psycopg import AsyncConnection, errors
from psycopg.types.json import Jsonb
from pydantic import Field

from . import demo
from .api import Strict, UserDep, problem
from .db import person_tx, system_tx

router = APIRouter(prefix="/api")
Role = Literal["clerk", "approver", "auditor", "admin"]
TAMPER_CONFIRMATION = "break the chain"


class RoleChange(Strict):
    role: Role
    grant: bool
    reason: str = Field(min_length=1, max_length=2000)


class RampLinkChange(Strict):
    ramp_user_id: str | None = Field(min_length=1, max_length=200, pattern=r"\S")  # null clears; blank is refused
    reason: str = Field(min_length=1, max_length=2000)


class AccessChange(Strict):
    enabled: bool
    reason: str = Field(min_length=1, max_length=2000)


class DemoResetRequest(Strict):
    link_clerk_user_id: UUID | None = None
    reason: str = Field(min_length=1, max_length=2000)


class DemoChaosRequest(Strict):
    enabled: bool
    reason: str = Field(min_length=1, max_length=2000)


class DemoTamperRequest(Strict):
    confirm: Literal["break the chain"]
    reason: str = Field(min_length=1, max_length=2000)


def _sentence(exc: errors.Error) -> str:
    return (exc.diag.message_primary or "Refused.").removeprefix("admin: ")


def _refused(exc: errors.Error):
    """The database's refusal, as the contract's error (spec 0095, A13)."""
    if isinstance(exc, errors.InsufficientPrivilege):
        return problem(403, "forbidden", _sentence(exc))
    if isinstance(exc, errors.NoDataFound):
        return problem(404, "not_found", "No such person.")
    return problem(409, "transition_refused", _sentence(exc))


REFUSALS = (errors.InsufficientPrivilege, errors.NoDataFound, errors.CheckViolation)


async def _door(request: Request, user: UserDep, sql: str, params: tuple) -> Any:
    async with person_tx(request.app.state.pool, user.user_id, user.claims) as conn:
        try:
            async with conn.transaction():
                row = await (await conn.execute(sql, params)).fetchone()
        except REFUSALS as exc:
            return _refused(exc)
    return row["r"]


# People ----------------------------------------------------------------------------------------

@router.get("/people")
async def list_people(request: Request, user: UserDep):
    people = await _door(request, user, "select app.admin_people() as r", ())
    return people if not isinstance(people, list) else {"people": people}


@router.post("/people/{user_id}/roles")
async def change_role(user_id: UUID, body: RoleChange, request: Request, user: UserDep):
    return await _door(request, user, "select app.admin_set_role(%s, %s::app.user_role, %s, %s) as r",
                       (user_id, body.role, body.grant, body.reason))


@router.post("/people/{user_id}/ramp-link")
async def change_ramp_link(user_id: UUID, body: RampLinkChange, request: Request, user: UserDep):
    return await _door(request, user, "select app.admin_set_ramp_link(%s, %s, %s) as r", (user_id, body.ramp_user_id, body.reason))


@router.post("/people/{user_id}/access")
async def change_access_of(user_id: UUID, body: AccessChange, request: Request, user: UserDep):
    return await _door(request, user, "select app.admin_set_access(%s, %s, %s) as r", (user_id, body.enabled, body.reason))


@router.get("/admin/log")
async def admin_log(request: Request, user: UserDep, limit: int = Query(100, ge=1, le=500)):
    return await _door(request, user, "select app.admin_log(%s) as r", (limit,))


# Demo commands ----------------------------------------------------------------------------------

def _env(request: Request) -> demo.DemoEnv:
    state = request.app.state
    return demo.DemoEnv(pool=state.pool, settings=state.settings, mock=state.demo_mock, connector=state.connector,
                        owner_url=state.owner_url)


async def _record(conn: AsyncConnection, kind: str, detail: dict[str, Any], reason: str) -> None:
    """The admin check and the intent on the organisation chain, committed before the command runs."""
    await conn.execute("select app.admin_record_demo(%s, %s, %s)", (kind, Jsonb(detail), reason))


async def _current_demo(request: Request) -> dict[str, Any] | None:
    async with system_tx(request.app.state.pool) as conn:
        return await (await conn.execute("select id from app.datasets where kind = 'demo' and retired_at is null "
                                          "order by created_at desc limit 1")).fetchone()


async def _state(request: Request) -> dict[str, Any]:
    s = await demo.status(_env(request))
    return {"dataset": {"id": s["dataset"]["id"], "label": s["dataset"]["label"],
                        "created_at": s["dataset"]["created_at"].replace("+00:00", "Z")},
            "cases": [{"id": c["id"], "vendor": c["vendor"], "state": c["state"], "outcome": c["outcome"]} for c in s["cases"]],
            "chain": {k: s["chain"][k] for k in ("ok", "checked", "first_bad_seq", "detail")},
            "alerts": [{"kind": a["kind"], "severity": a["severity"], "message": a["message"]} for a in s["alerts"]],
            "chaos": s["chaos"], "warnings": list(getattr(request.app.state, "demo_warnings", []))}


async def _demo_call(request: Request, user: UserDep, kind: str, detail: dict[str, Any], reason: str, run):
    """Check and record as the admin, then run. A DemoError is a 409 with its sentence."""
    async with person_tx(request.app.state.pool, user.user_id, user.claims) as conn:
        try:
            async with conn.transaction():
                await _record(conn, kind, detail, reason)
        except REFUSALS as exc:
            return _refused(exc)
    try:
        await run()
        return await _state(request)
    except demo.DemoError as exc:
        return problem(409, "transition_refused", str(exc))


@router.get("/admin/demo")
async def demo_state(request: Request, user: UserDep):
    refused = await _door(request, user, "select app.admin_caller() as r", ())
    if isinstance(refused, JSONResponse):
        return refused
    try:
        return await _state(request)
    except demo.DemoError as exc:
        return problem(409, "transition_refused", str(exc))


@router.post("/admin/demo/reset")
async def demo_reset(body: DemoResetRequest, request: Request, user: UserDep):
    clerk = body.link_clerk_user_id
    if clerk is not None and clerk == user.user_id:
        return problem(409, "transition_refused", "You can't link yourself as the demo clerk: it decides who entered a change (R5).")
    env = _env(request)

    async def run() -> None:
        e164 = demo.check_ledger({}, ["acme"]) if clerk else {}
        seed = demo.load_seed(getattr(request.app.state, "demo_seed", demo.DEFAULT_SEED))  # /app/scripts/demo in the image
        result = await demo.reset(env, seed, {})
        warnings = [w for w in result.warnings if not (clerk and "'clerk'" in w)]
        if clerk:
            async with person_tx(request.app.state.pool, user.user_id, user.claims) as conn:
                try:
                    async with conn.transaction():
                        await conn.execute("select app.admin_set_ramp_link(%s, %s, %s)", (clerk, result.refs["clerk"], body.reason))
                except REFUSALS as exc:
                    raise demo.DemoError(f"the demo was reset but the clerk wasn't linked: {_sentence(exc)}") from None
            await demo.load_ledger(env, result.dataset.id, e164, ["acme"])
        request.app.state.demo_warnings = warnings

    return await _demo_call(request, user, "demo_reset", {"linked_clerk_user_id": str(clerk) if clerk else None},
                            body.reason, run)


@router.post("/admin/demo/chaos")
async def demo_chaos(body: DemoChaosRequest, request: Request, user: UserDep):
    current = await _current_demo(request)
    if current is None:
        return problem(409, "transition_refused", "There is no current demo: reset one first.")
    return await _demo_call(request, user, "demo_chaos", {"dataset_id": str(current["id"]), "enabled": body.enabled},
                            body.reason, lambda: demo.chaos(_env(request), body.enabled))


@router.post("/admin/demo/tamper")
async def demo_tamper(body: DemoTamperRequest, request: Request, user: UserDep):
    current = await _current_demo(request)
    if current is None:
        return problem(409, "transition_refused", "There is no current demo: reset one first.")
    return await _demo_call(request, user, "demo_tamper", {"dataset_id": str(current["id"])}, body.reason,
                            lambda: demo.tamper(_env(request), confirmed=body.confirm == TAMPER_CONFIRMATION,
                                                dataset_id=current["id"]))
