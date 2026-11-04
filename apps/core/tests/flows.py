"""Shared steps for core tests: drive a case through the money path the way the services do."""
from __future__ import annotations

from conftest import CONTACT_POINT, INTERNAL_TOKEN
from dialback_core.db import person_tx, system_tx

CLEAN_CHECKS = {"disclosure_first": True, "answers_grounded": True, "agent_spoke_account_digits": False,
                "new_number_accepted": False, "policy_violations": 0, "speech_guard_trips": 0}


async def one(pool, sql: str, *args):
    async with system_tx(pool) as conn:
        return await (await conn.execute(sql, args)).fetchone()


async def case_of(world):
    return await one(world.pool, "select * from app.cases where dataset_id = %s order by opened_at desc limit 1", world.dataset.id)


async def change_and_detect(world, account_number="000055556666", actor="clerk"):
    await world.bank_change(account_number=account_number, actor=actor)
    await world.deliver_webhooks()
    await world.worker.drain(world.dataset.id)


def answer(slot, value, turn):
    """One answer as the voice service reports it, captured in turn order."""
    return {"slot": slot, "value": value, "turn_id": turn, "tool_call_id": f"tc_{turn}",
            "captured_at": f"2026-11-03T17:02:{turn:02d}Z", "capture_method": "keypad" if slot == "account_last4" else "speech"}


def transit(call_id, digits="6666", company="Acme Fasteners", change="yes"):
    return {"schema_version": "call-answers/v0", "call_id": str(call_id), "answers": [
        answer("recording_consent", "yes", 1), answer("company_named", company, 3), answer("speaker_name", "Jordan Lee", 5),
        answer("speaker_role", "AR manager", 5), answer("change_requested", change, 7), answer("account_last4", digits, 9)]}


async def report(world, case_id, call_id, attempt=1, result="answers_recorded", answers=None, token=INTERNAL_TOKEN, key=True,
                 contact_point=CONTACT_POINT, retry_at=None):
    body = {"call_id": str(call_id), "case_id": str(case_id), "result": result, "attempt": attempt,
            "agent_version": "agent-test", "checks": CLEAN_CHECKS}
    if result == "answers_recorded" or answers is not None:
        body["answers"] = answers or transit(call_id)
    if contact_point is not None:
        body["contact_point_id"] = str(contact_point)
    if retry_at is not None:
        body["retry_at"] = retry_at
    headers = {"Authorization": f"Bearer {token.decode()}"}
    if key:
        headers["Idempotency-Key"] = f"{case_id}:call:{attempt}:ended"
    return await world.core.post(f"/internal/calls/{call_id}/ended", json=body, headers=headers)


async def release(world, case_id, version, who="approver1"):
    async with person_tx(world.pool, world.users[who], {}) as conn:
        return (await (await conn.execute("select app.release_case(%s, %s, 'approve', %s) as r",
                                          (case_id, version, "Confirmed on the ledger's number; digits match."))).fetchone())["r"]


async def bills(world):
    return {b.id: b for b in await world.ramp.list_bills_for_vendor(world.refs["acme"])}


async def pay(world, as_of: str) -> list[str]:
    r = await world.mock.post("/_mock/payment-run", json={"as_of": as_of}, headers=world.auth)
    return r.json()["paid_bill_ids"]


async def _types(world, case_id):
    async with system_tx(world.pool) as conn:
        return await (await conn.execute("select type from audit.evidence_events where case_id = %s order by seq", (case_id,))).fetchall()
