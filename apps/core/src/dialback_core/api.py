"""The dashboard API (packages/contracts/openapi/dialback.yaml; spec 0002, D9 and D12).

Every request runs in Postgres as the signed-in person (`person_tx`), so row-level security
applies and `app.release_case()` sees who is releasing. The API never decides who may act:
`permissions` is a hint for the page, and the database refuses the changer, the reviewer
and a stale version whatever the page shows. When the database refuses, the API explains
why with a templated sentence.
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Annotated, Any, Literal
from uuid import UUID

from dialback_decision.engine import TEMPLATES
from dialback_ledger.reasons import NOTES, REASON_TEXT
from dialback_ledger.risk import risk_list
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from psycopg import AsyncConnection, errors
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .auth import Principal, current_user
from .db import person_tx
from .events import NotAllowed, TransitionRefused, VersionConflict, apply_event, person
from .timeline import alert_message, summary

router = APIRouter(prefix="/api")
UserDep = Annotated[Principal, Depends(current_user)]
CaseState = Literal["open", "held", "calling", "retry_wait", "deciding", "awaiting_release", "needs_review",
                    "blocked", "releasing", "released"]
UNFINISHED = ["open", "held", "calling", "retry_wait", "deciding", "awaiting_release", "needs_review", "blocked", "releasing"]


def ts(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
    return value.isoformat()


def problem(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"code": code, "message": message}, status_code=status)


# Requests ---------------------------------------------------------------------------------------

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Reasoned(Strict):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("a written reason is required")
        return v.strip()


class ReleaseRequest(Reasoned):
    action: Literal["approve", "reject"]


class ReviewRequest(Reasoned):
    action: Literal["clear", "block"]


# Queries ----------------------------------------------------------------------------------------

SUMMARY = """
select c.id, c.dataset_id, c.vendor_id, v.legal_name, c.state::text as state, c.outcome::text as outcome, c.version,
       c.opened_at, c.updated_at, c.deadline_at, c.has_unknown_changer, c.entered_by, c.reviewed_by, c.changer_ids,
       c.change_event_id, h.status::text as hold_status, coalesce(jsonb_array_length(h.original_schedule), 0) as bills_held,
       (select min((e ->> 'payment_date')::date) from jsonb_array_elements(h.original_schedule) e
         where e ->> 'payment_date' is not null) as next_payment_date
  from app.cases c
  join app.vendors v on v.id = c.vendor_id
  left join lateral (select * from app.holds h where h.case_id = c.id
                      order by (h.released_at is null) desc, h.created_at desc limit 1) h on true
"""


def case_summary(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(r["id"]), "dataset_id": str(r["dataset_id"]),
        "vendor": {"id": str(r["vendor_id"]), "legal_name": r["legal_name"]},
        "state": r["state"], "outcome": r["outcome"], "version": r["version"],
        "opened_at": ts(r["opened_at"]), "updated_at": ts(r["updated_at"]), "deadline_at": ts(r["deadline_at"]),
        "hold_status": r["hold_status"], "bills_held": r["bills_held"], "next_payment_date": ts(r["next_payment_date"]),
        "unknown_changer": r["has_unknown_changer"],
    }


async def people(conn: AsyncConnection, ids: set[UUID]) -> dict[UUID, str]:
    if not ids:
        return {}
    rows = await (await conn.execute("select user_id, display_name from app.user_profiles where user_id = any(%s)",
                                     (list(ids),))).fetchall()
    return {r["user_id"]: r["display_name"] for r in rows}


def person_json(user_id: UUID | None, names: dict[UUID, str]) -> dict[str, str] | None:
    return None if user_id is None else {"id": str(user_id), "display_name": names.get(user_id, "Unknown user")}


async def chain_status(conn: AsyncConnection, chain_id: UUID) -> dict[str, Any]:
    r = await (await conn.execute(
        "select v.ok, v.checked, v.first_bad_seq, v.detail, t.seq as tip_seq, t.hash as tip_hash "
        "from audit.verify_chain(%s) v left join audit.chain_tip(%s) t on true", (chain_id, chain_id))).fetchone()
    return {"chain_id": str(chain_id), "ok": r["ok"], "checked": r["checked"], "first_bad_seq": r["first_bad_seq"],
            "detail": r["detail"], "tip_seq": r["tip_seq"], "tip_hash": r["tip_hash"]}


async def roles_of(conn: AsyncConnection) -> list[str]:
    rows = await (await conn.execute("select role::text as role from app.user_roles where user_id = auth.uid() order by role")).fetchall()
    return [r["role"] for r in rows]


def permissions(case: dict[str, Any], user: UUID, roles: list[str], approved_this_version: bool) -> dict[str, Any]:
    """A hint for the page. The database enforces every one of these again."""
    reasons: list[str] = []
    is_changer = user in (case["changer_ids"] or [])
    is_reviewer = case["reviewed_by"] == user
    approver = "approver" in roles
    awaiting = case["state"] == "awaiting_release"
    if is_changer:
        reasons.append("You entered this change, so you cannot release, reject or clear it (R5).")
    if awaiting and not approver:
        reasons.append("Releasing needs the approver role.")
    if case["state"] == "needs_review" and not approver:
        reasons.append("Reviewing needs the approver role.")
    if awaiting and is_reviewer:
        reasons.append("You cleared this case's review, so a different approver must release it.")
    if awaiting and approved_this_version:
        reasons.append("This change has no known author, so it needs two approvers; you have already approved.")
    if case["state"] == "released":
        reasons.append("This case is released; the hold is off and nothing is left to decide.")
    elif not awaiting and case["state"] != "needs_review":
        reasons.append(f"This case is {case['state'].replace('_', ' ')}; there is nothing to release or review yet.")
    return {
        "can_approve": awaiting and approver and not is_changer and not is_reviewer and not approved_this_version,
        "can_reject": awaiting and approver and not is_changer,
        "can_review": case["state"] == "needs_review" and approver and not is_changer,  # spec 0028
        "reasons": reasons,
    }


# Routes -----------------------------------------------------------------------------------------

@router.get("/me")
async def me(request: Request, user: UserDep):
    async with person_tx(request.app.state.pool, user.user_id, user.claims) as conn:
        profile = await (await conn.execute("select display_name from app.user_profiles where user_id = auth.uid()")).fetchone()
        roles = await roles_of(conn)
    name = profile["display_name"] if profile else str(user.claims.get("email") or "Unknown user")
    return {"user_id": str(user.user_id), "display_name": name, "roles": roles}


@router.get("/datasets")
async def datasets(request: Request, user: UserDep):
    async with person_tx(request.app.state.pool, user.user_id, user.claims) as conn:
        rows = await (await conn.execute(
            "select id, kind::text as kind, label, chain_id, created_at, sim_now from app.datasets "
            "where retired_at is null order by created_at desc")).fetchall()
    return {"datasets": [{"id": str(r["id"]), "kind": r["kind"], "label": r["label"], "chain_id": str(r["chain_id"]),
                          "created_at": ts(r["created_at"]), "sim_now": ts(r["sim_now"])} for r in rows]}


@router.get("/datasets/{dataset_id}/chain")
async def dataset_chain(dataset_id: UUID, request: Request, user: UserDep):
    async with person_tx(request.app.state.pool, user.user_id, user.claims) as conn:
        d = await (await conn.execute("select chain_id from app.datasets where id = %s", (dataset_id,))).fetchone()
        if d is None:
            return problem(404, "not_found", "No such dataset.")
        return await chain_status(conn, d["chain_id"])


@router.get("/datasets/{dataset_id}/vendor-risk")
async def vendor_risk(dataset_id: UUID, request: Request, user: UserDep):
    """The vendor risk list (spec 0026): the ledger decides each status, read under the person's token."""
    async with person_tx(request.app.state.pool, user.user_id, user.claims) as conn:
        if await (await conn.execute("select 1 from app.datasets where id = %s", (dataset_id,))).fetchone() is None:
            return problem(404, "not_found", "No such dataset.")
        rows = await risk_list(conn, dataset_id)
    return {"vendors": rows}


@router.get("/datasets/{dataset_id}/alerts")
async def dataset_alerts(dataset_id: UUID, request: Request, user: UserDep):
    async with person_tx(request.app.state.pool, user.user_id, user.claims) as conn:
        if await (await conn.execute("select 1 from app.datasets where id = %s", (dataset_id,))).fetchone() is None:
            return problem(404, "not_found", "No such dataset.")
        # A stuck place_call carries the case's latest refusal, so the banner can say why (spec 0031).
        rows = await (await conn.execute(
            "select a.id, a.case_id, a.kind, a.severity::text as severity, a.message, a.raised_at, f.body as failed "
            "from app.alerts a left join lateral ("
            "  select e.body_jcs::jsonb -> 'body' as body from audit.evidence_events e"
            "   where a.kind = 'command_stuck' and a.message like 'Command place\\_call %%'"
            "     and e.case_id = a.case_id and e.type = 'command_failed'"
            "     and e.body_jcs::jsonb -> 'body' ->> 'command' = 'place_call'"
            "   order by e.seq desc limit 1) f on true "
            "where a.dataset_id = %s and a.resolved_at is null "
            "order by (a.severity = 'critical') desc, a.raised_at desc",
            (dataset_id,))).fetchall()
    return {"alerts": [{"id": r["id"], "case_id": str(r["case_id"]) if r["case_id"] else None, "kind": r["kind"],
                        "severity": r["severity"], "message": alert_message(r["message"], r["failed"]),
                        "raised_at": ts(r["raised_at"])} for r in rows]}


@router.get("/cases")
async def list_cases(request: Request, user: UserDep, dataset_id: UUID, state: Annotated[list[CaseState] | None, Query()] = None,
                     limit: Annotated[int, Query(ge=1, le=200)] = 100):
    async with person_tx(request.app.state.pool, user.user_id, user.claims) as conn:
        rows = await (await conn.execute(
            SUMMARY + " where c.dataset_id = %s and c.state::text = any(%s) order by c.opened_at, c.id limit %s",
            (dataset_id, state or UNFINISHED, limit))).fetchall()
    return {"cases": [case_summary(r) for r in rows]}


@router.get("/cases/{case_id}")
async def get_case(case_id: UUID, request: Request, user: UserDep):
    async with person_tx(request.app.state.pool, user.user_id, user.claims) as conn:
        c = await (await conn.execute(SUMMARY + " where c.id = %s", (case_id,))).fetchone()
        if c is None:
            return problem(404, "not_found", "No such case.")
        change = await (await conn.execute(
            """select ce.detected_via::text as detected_via, ce.detected_at, ce.changed_by,
                      n.routing_number as new_routing, n.account_last4 as new_last4,
                      o.routing_number as old_routing, o.account_last4 as old_last4
                 from app.bank_change_events ce join app.bank_accounts n on n.id = ce.new_account_id
                 left join app.bank_accounts o on o.id = ce.old_account_id where ce.id = %s""",
            (c["change_event_id"],))).fetchone()
        hold = await (await conn.execute(
            "select status::text as status, ramp_ref, placed_at, confirmed_at, released_at, original_schedule, last_error "
            "from app.holds where case_id = %s order by (released_at is null) desc, created_at desc limit 1", (case_id,))).fetchone()
        decision = await (await conn.execute(
            "select outcome::text as outcome, rules_fired, engine_version, last_four_result, decided_at from app.decisions "
            "where case_id = %s and case_version = %s and voided_at is null order by decided_at desc limit 1",
            (case_id, c["version"]))).fetchone()
        releases = await (await conn.execute(
            "select actor_id, action::text as action, reason, case_version, created_at from app.releases "
            "where case_id = %s order by created_at, id", (case_id,))).fetchall()
        events = await (await conn.execute(
            "select seq, type, occurred_at, actor_kind, actor_id, hash, body_jcs from audit.evidence_events "
            "where case_id = %s order by seq", (case_id,))).fetchall()
        chain_id = (await (await conn.execute("select chain_id from app.datasets where id = %s", (c["dataset_id"],))).fetchone())["chain_id"]
        chain = await chain_status(conn, chain_id)
        roles = await roles_of(conn)
        ids = {c["entered_by"], c["reviewed_by"], change["changed_by"], *(r["actor_id"] for r in releases),
               *(e["actor_id"] for e in events)} - {None}
        names = await people(conn, ids)

    approved = any(r["actor_id"] == user.user_id and r["action"] == "approve" and r["case_version"] == c["version"] for r in releases)
    timeline = []
    for e in events:
        envelope = json.loads(e["body_jcs"])
        actor_name = "Dialback" if e["actor_kind"] == "system" else names.get(e["actor_id"], "Unknown user")
        timeline.append({
            "seq": e["seq"], "type": e["type"], "occurred_at": ts(e["occurred_at"]),
            "actor": {"kind": e["actor_kind"], "id": str(e["actor_id"]) if e["actor_id"] else None, "display_name": actor_name},
            "from_state": envelope.get("from_state"), "to_state": envelope["to_state"], "version": envelope["version"],
            "summary": summary(e["type"], envelope.get("body") or {}, actor_name), "hash": e["hash"],
        })
    return {
        "case": case_summary(c),
        "entered_by": person_json(c["entered_by"], names),
        "reviewed_by": person_json(c["reviewed_by"], names),
        "change": {
            "detected_via": change["detected_via"], "detected_at": ts(change["detected_at"]),
            "old_account": None if change["old_last4"] is None else {"routing_number": change["old_routing"], "last4": change["old_last4"]},
            "new_account": {"routing_number": change["new_routing"], "last4": change["new_last4"]},
            "changed_by": person_json(change["changed_by"], names),
            "unknown_changer": c["has_unknown_changer"],
        },
        "hold": None if hold is None else {
            "status": hold["status"], "ramp_ref": hold["ramp_ref"], "placed_at": ts(hold["placed_at"]),
            "confirmed_at": ts(hold["confirmed_at"]), "released_at": ts(hold["released_at"]),
            "schedule": hold["original_schedule"] or [], "last_error": hold["last_error"],
        },
        "decision": None if decision is None else {
            "outcome": decision["outcome"], "rules_fired": list(decision["rules_fired"]), "engine_version": decision["engine_version"],
            "last_four_result": decision["last_four_result"], "decided_at": ts(decision["decided_at"]),
            "explanation": [TEMPLATES[r] for r in decision["rules_fired"] if r in TEMPLATES],
        },
        "releases": [{"actor": person_json(r["actor_id"], names), "action": r["action"], "reason": r["reason"],
                      "case_version": r["case_version"], "created_at": ts(r["created_at"])} for r in releases],
        "timeline": timeline,
        "chain": chain,
        "permissions": permissions(c, user.user_id, roles, approved),
    }


def action_result(raw: dict[str, Any]) -> dict[str, Any]:
    return {"status": raw["status"], "case_id": raw["case_id"], "from_state": raw.get("from_state"),
            "to_state": raw["to_state"], "version": raw["version"], "evidence_seq": raw.get("evidence_seq")}


async def _refusal(conn: AsyncConnection, case_id: UUID, user: UUID, message: str) -> JSONResponse:
    """Explain a refusal from the database. The check already happened in SQL."""
    case = await (await conn.execute("select changer_ids, reviewed_by, state::text as state, version from app.cases where id = %s",
                                     (case_id,))).fetchone()
    if "(R5)" in message or user in (case["changer_ids"] or []):
        return problem(403, "forbidden", "You entered this change, so you cannot release, reject or clear it (R5)."
                       if user in (case["changer_ids"] or []) else "The rules refuse this action for you (R5): "
                       "the person who cleared the review cannot also approve the release.")
    if case["state"] == "needs_review" and "approver" not in await roles_of(conn):
        return problem(403, "forbidden", "Reviewing needs the approver role.")  # spec 0028
    return problem(409, "transition_refused", f"This case is {case['state'].replace('_', ' ')}, so that action is not available.")


@router.get("/cases/{case_id}/selection")
async def get_case_selection(case_id: UUID, request: Request, user: UserDep):
    """The number lineage panel (spec 0023): the selection the case's latest selection event names."""
    async with person_tx(request.app.state.pool, user.user_id, user.claims) as conn:
        if await (await conn.execute("select 1 from app.cases where id = %s", (case_id,))).fetchone() is None:
            return problem(404, "not_found", "No such case.")
        event = await (await conn.execute(
            "select body_jcs::jsonb -> 'body' ->> 'number_selection_id' as id from audit.evidence_events "
            "where case_id = %s and type in ('number_resolved', 'no_provable_number') order by seq desc limit 1",
            (case_id,))).fetchone()
        row = None
        if event and event["id"]:
            row = await (await conn.execute(
                "select selection from ledger.number_selections where id::text = %s and case_id = %s",
                (event["id"], case_id))).fetchone()
    return {"selection": row["selection"] if row else None, "reason_text": REASON_TEXT, "notes": NOTES}


@router.post("/cases/{case_id}/release")
async def release(case_id: UUID, body: ReleaseRequest, request: Request, user: UserDep):
    pool = request.app.state.pool
    async with person_tx(pool, user.user_id, user.claims) as conn:
        if await (await conn.execute("select 1 from app.cases where id = %s", (case_id,))).fetchone() is None:
            return problem(404, "not_found", "No such case.")
        try:
            async with conn.transaction():
                row = await (await conn.execute("select app.release_case(%s, %s, %s::app.release_action, %s) as r",
                                                (case_id, body.expected_version, body.action, body.reason))).fetchone()
        except errors.InsufficientPrivilege:
            return problem(403, "forbidden", "Releasing needs the approver role.")
        except errors.SerializationFailure:
            return problem(409, "version_conflict", "This case changed since you loaded it. Reload to see the latest version.")
        except errors.CheckViolation as exc:
            return await _refusal(conn, case_id, user.user_id, exc.diag.message_primary or "")
    return action_result(row["r"])


@router.post("/cases/{case_id}/review")
async def review(case_id: UUID, body: ReviewRequest, request: Request, user: UserDep):
    pool = request.app.state.pool
    event = "review_cleared" if body.action == "clear" else "review_blocked"
    async with person_tx(pool, user.user_id, user.claims) as conn:
        if await (await conn.execute("select 1 from app.cases where id = %s", (case_id,))).fetchone() is None:
            return problem(404, "not_found", "No such case.")
        try:
            result = await apply_event(conn, case_id, event, f"{case_id}:review:v{body.expected_version}:{user.user_id}:{body.action}",
                                       {"reason": body.reason, "expected_version": body.expected_version}, actor=person(user.user_id))
        except NotAllowed:
            return problem(403, "forbidden", "The rules refuse this action for you.")
        except VersionConflict:
            return problem(409, "version_conflict", "This case changed since you loaded it. Reload to see the latest version.")
        except TransitionRefused as exc:
            return await _refusal(conn, case_id, user.user_id, str(exc))
    return action_result(result.raw)
